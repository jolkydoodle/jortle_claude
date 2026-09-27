"""When a daily entry gets a historical version, and when a large deletion
gets a recovery checkpoint (Master Spec §§44.1, 44.5).

Three things that are easy to blur, kept apart on purpose:

    working state        the `entries` row. Autosave and Ctrl+S keep it
                         current; it is overwritten all the time.
    entry revisions      `entry_revisions`: immutable past states of one
                         entry, made at meaningful EDITING BOUNDARIES only.
    recovery checkpoints `recovery_checkpoints`: the content just before a
                         save that removed a lot of it (entries AND projects).
    full backups         backup.py — the whole data folder. Not touched here.

Editing boundaries and what each one records
--------------------------------------------
A "session" is the time one entry is open in the editor. It begins when the
date is loaded (`begin_session`) and ends when the user leaves the entry,
closes the app, or restores an older version (`end_session`). Every
revision is a snapshot of what is STORED at that moment — never of unsaved
editor text:

  * before the first overwrite of a session — the entry as it stood before
    this session touched it, unless that exact state is already recorded.
    This is what protects entries written before version history existed,
    and it is the first line of defence against a bad first save.
  * end of session — the state the session left behind, if the session
    actually changed the stored entry.
  * a long session — after LONG_SESSION_SECONDS of editing, if at least
    LONG_SESSION_MIN_CHANGE characters changed since the last revision.
  * emptying — when a save deletes all of a written entry's text (which is
    how an entry is deleted), the state it had is kept first.

Never: on merely opening a date; for a blank state (a blank entry is "no
entry", and the state before it was blanked is what gets recorded); or for a
state already in this entry's history (compared by hash, without reading any
bodies).

Recovery checkpoints
--------------------
On every write, the new text is compared paragraph-by-paragraph with the
state being overwritten AND with the state the session started from — the
second catches a large deletion made a little at a time across several
autosaves. If either comparison shows a substantial removal
(`is_substantial_removal`), the larger of the two states is kept as a
checkpoint, once per session.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject

from .saving import document_has_content

# ---------------------------------------------------------------- policy
LONG_SESSION_SECONDS = 30 * 60
LONG_SESSION_MIN_CHANGE = 200

# A deletion is "substantial" when it removes at least this much outright…
RECOVERY_MIN_ABSOLUTE = 1000
# …or at least this much AND at least this fraction of what was there, so
# that deleting most of a short entry is protected too.
RECOVERY_MIN_CHARS = 200
RECOVERY_MIN_FRACTION = 0.5

# Human-readable reasons, stored with each revision.
REASON_BEFORE_EDITING = "before editing"
REASON_LEFT = "left the entry"
REASON_CLOSED = "closed the app"
REASON_LONG_SESSION = "long editing session"
REASON_BEFORE_RESTORE = "before restoring an older version"
REASON_BEFORE_EMPTIED = "before the entry was emptied"


def content_hash(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _paragraphs(plain: str) -> list:
    return (plain or "").split("\n")


def text_chars(plain: str) -> int:
    """Characters of text, counted the way `removal` counts them: line breaks
    are not characters. The 50% rule and a checkpoint's recorded totals use
    this, so a removal is always compared with a prior size in the same unit."""
    return sum(len(p) for p in _paragraphs(plain))


def paragraph_count(plain: str) -> int:
    """Paragraphs that hold something — blank lines are not counted."""
    return sum(1 for p in _paragraphs(plain) if p.strip())


_WORD_RE = re.compile(r"\S+\s*")   # a word with the space after it


def _edge_overlap(before: str, after: str) -> int:
    """Characters shared at the start plus at the end of two strings."""
    limit = min(len(before), len(after))
    prefix = 0
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < limit - prefix
           and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]):
        suffix += 1
    return prefix + suffix


def _removed_words(before: str, after: str) -> int:
    """Characters of `before` gone from `after`, by a word-level diff."""
    old_words = _WORD_RE.findall(before)
    new_words = _WORD_RE.findall(after)
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)
    removed = 0
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed += sum(len(w) for w in old_words[i1:i2])
    return removed


def removal(old_plain: str, new_plain: str, exact: bool = True) -> tuple:
    """(characters, paragraphs) of `old_plain` that are gone from `new_plain`.

    Paragraph-level diff first. Whole paragraphs that disappeared are counted
    in full (and are the only paragraph count reported — the reliable one).
    Inside a paragraph that was edited rather than deleted:

      * exact=False — a linear UPPER BOUND: everything between the common
        start and the common end of the paragraph. Ordinary typing (text
        inserted, a word fixed) scores ~0 at almost no cost. This is what
        every save runs.
      * exact=True — a word-level diff, run only when the upper bound says a
        large deletion is possible, so rewording is not mistaken for
        deleting.

    Line breaks are not counted as characters. (The first version diffed
    every edited paragraph character by character on every autosave; on a
    1,500-word paragraph that took ~1.8 s and made typing lag — measured.)
    """
    old_paras, new_paras = _paragraphs(old_plain), _paragraphs(new_plain)
    matcher = difflib.SequenceMatcher(None, old_paras, new_paras, autojunk=False)
    removed_chars = removed_paras = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            gone = old_paras[i1:i2]
            removed_chars += sum(len(p) for p in gone)
            removed_paras += sum(1 for p in gone if p.strip())
        elif tag == "replace":
            before = "".join(old_paras[i1:i2])
            after = "".join(new_paras[j1:j2])
            upper = max(0, len(before) - _edge_overlap(before, after))
            if exact and upper:
                removed_chars += min(upper, _removed_words(before, after))
            else:
                removed_chars += upper
            removed_paras += max(0, sum(1 for p in old_paras[i1:i2] if p.strip())
                                 - sum(1 for p in new_paras[j1:j2] if p.strip()))
    return removed_chars, removed_paras


def changed_chars(old_plain: str, new_plain: str) -> int:
    """Characters removed plus characters added — a size for 'how much changed'.
    An upper bound (see `removal`); used only to decide whether a long session
    has changed enough to deserve a version."""
    removed, _ = removal(old_plain, new_plain, exact=False)
    added, _ = removal(new_plain, old_plain, exact=False)
    return removed + added


def is_substantial_removal(prior_chars: int, removed_chars: int) -> bool:
    if removed_chars >= RECOVERY_MIN_ABSOLUTE:
        return True
    return (removed_chars >= RECOVERY_MIN_CHARS
            and removed_chars >= RECOVERY_MIN_FRACTION * max(1, prior_chars))


@dataclass
class _State:
    """A stored document: what is on disk, not what is in the editor."""
    html: str
    fmt: str
    plain: str
    title: str = ""

    @property
    def written(self) -> bool:
        return document_has_content(self.html, self.plain)


@dataclass
class _Session:
    scope: str
    ref: str
    baseline: Optional[_State]
    started_at: float
    last_revision_at: float
    wrote: bool = False
    checkpointed: bool = False


class EntryHistory(QObject):
    """Owns the editing sessions and applies the policy above.

    A QObject child of the main window on purpose: restoring a backup swaps
    the Database, and the window re-points every object that holds one by
    walking its QObject children (`MainWindow._database_holders`). A plain
    Python object here would silently keep writing to the closed database.
    """

    def __init__(self, db, parent=None, clock=time.time):
        super().__init__(parent)
        self.db = db
        self.clock = clock
        self._sessions: dict = {}

    # ------------------------------------------------------------ sessions
    def begin_session(self, scope: str, ref, state: Optional[_State] = None):
        """Starts tracking one open document. Re-opening the document that is
        already open (e.g. Discard reloading it) keeps the existing session."""
        key = (scope, str(ref))
        if key in self._sessions:
            return
        if state is None and scope == "date":
            state = self._stored_entry(str(ref))
        now = self.clock()
        self._sessions[key] = _Session(scope, str(ref), state, now, now)

    def reset(self):
        """Forgets every session without recording anything — used when the
        database under the app is replaced by a restore."""
        self._sessions.clear()

    def end_session(self, scope: str, ref, reason: str) -> bool:
        """Closes a session; for a daily entry that changed, records the
        state it left behind. Returns whether a revision was made."""
        session = self._sessions.pop((scope, str(ref)), None)
        if session is None or not session.wrote or scope != "date":
            return False
        return self._record_revision(session.ref, self._stored_entry(session.ref), reason)

    def end_all_sessions(self, reason: str):
        for scope, ref in list(self._sessions):
            self.end_session(scope, ref, reason)

    # -------------------------------------------------------------- writes
    def before_write(self, scope: str, ref, stored: Optional[_State],
                     new_html: str, new_plain: str, title: str = "",
                     detect_removal: bool = True):
        """Called by a save path just before it overwrites `stored` (None when
        no row exists yet). Records the pre-session state if it is not yet
        recorded, and a recovery checkpoint if this save removes a lot."""
        ref = str(ref)
        self.begin_session(scope, ref, stored)
        session = self._sessions[(scope, ref)]

        if scope == "date" and stored is not None:
            if not session.wrote:
                self._record_revision(ref, stored, REASON_BEFORE_EDITING)
            elif stored.written and not document_has_content(new_html, new_plain):
                # Deleting all of an entry's text deletes the entry. That is
                # a legitimate way to remove it — and exactly the kind of
                # destructive change history exists for, however short the
                # entry was, so what it held is kept.
                self._record_revision(ref, stored, REASON_BEFORE_EMPTIED)

        # A deliberate restore replaces text on purpose, and the state it
        # replaces was just kept as a version — not a deletion to recover.
        if detect_removal and not session.checkpointed:
            self._maybe_checkpoint(session, stored, new_plain, title)

    def after_write(self, scope: str, ref, new_html: str, new_fmt: str,
                    new_plain: str, title: str = ""):
        """Called once the new state is stored."""
        ref = str(ref)
        self.begin_session(scope, ref)
        session = self._sessions[(scope, ref)]
        session.wrote = True
        if scope != "date":
            return
        now = self.clock()
        if now - session.last_revision_at < LONG_SESSION_SECONDS:
            return
        state = _State(new_html, new_fmt, new_plain, title)
        latest = self.db.latest_entry_revision(ref)
        reference = latest.body_text if latest else (
            session.baseline.plain if session.baseline else "")
        if changed_chars(reference or "", new_plain) >= LONG_SESSION_MIN_CHANGE:
            if self._record_revision(ref, state, REASON_LONG_SESSION):
                session.last_revision_at = now

    def record_before_restore(self, date: str) -> bool:
        """Keeps the current stored state before a restore replaces it."""
        return self._record_revision(date, self._stored_entry(date), REASON_BEFORE_RESTORE)

    # ------------------------------------------------------------ helpers
    def _stored_entry(self, date: str) -> Optional[_State]:
        entry = self.db.get_entry(date)
        if entry is None:
            return None
        return _State(entry.body_md or "", entry.body_format or "html",
                      entry.body_text or "", entry.title or "")

    def _record_revision(self, date: str, state: Optional[_State], reason: str) -> bool:
        if state is None or not state.written:
            return False
        digest = content_hash(state.html)
        if self.db.entry_revision_exists(date, digest):
            return False
        self.db.add_entry_revision(date, state.html, state.fmt, state.plain, reason,
                                   title=state.title, content_hash=digest)
        return True

    def _maybe_checkpoint(self, session: _Session, stored: Optional[_State],
                          new_plain: str, title: str):
        candidates = [s for s in (stored, session.baseline) if s is not None and s.written]
        best = None
        for state in candidates:
            prior = text_chars(state.plain)
            # Cheap upper bound first: ordinary typing stops here.
            upper, _ = removal(state.plain, new_plain, exact=False)
            if not is_substantial_removal(prior, upper):
                continue
            removed_chars, removed_paras = removal(state.plain, new_plain, exact=True)
            if is_substantial_removal(prior, removed_chars):
                if best is None or removed_chars > best[1]:
                    best = (state, removed_chars, removed_paras)
        if best is None:
            return
        state, removed_chars, removed_paras = best
        if self.db.recovery_checkpoint_exists(session.scope, session.ref,
                                              content_hash(state.html)):
            session.checkpointed = True
            return
        self.db.add_recovery_checkpoint(
            session.scope, session.ref, state.html, state.fmt, state.plain,
            title=title or state.title, prior_chars=text_chars(state.plain),
            prior_paragraphs=paragraph_count(state.plain),
            removed_chars=removed_chars, removed_paragraphs=removed_paras,
            remaining_chars=text_chars(new_plain))
        session.checkpointed = True


def stored_state(html: str, fmt: str, plain: str, title: str = "") -> _State:
    """Public constructor for callers outside this module."""
    return _State(html or "", fmt or "html", plain or "", title or "")
