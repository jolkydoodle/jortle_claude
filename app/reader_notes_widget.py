"""Reader's Notes: a lightweight companion area for whatever you're writing.

It holds the short supporting material that belongs beside a piece of
writing — definitions of words, explanations of technical or philosophical
concepts, short references, terminology reminders.

It was always a core feature rather than part of the optional AI one, and
imported nothing from that stack; when the AI feature was removed this
widget needed no changes at all, which is what that separation was for. It
now fills the companion area directly, rather than sitting in a tab bar that
only existed so a second, optional panel could share the space.

SCOPES (Part 33)
----------------
Reader's Notes originally existed only for journal days. It now also applies
to Projects, and the requirement was explicit: reuse the same architecture
rather than building a second, incompatible one. So this widget is no longer
date-shaped. It is told a *scope* ("date" or "project") once, at construction,
and a *ref* (the date string, or the project id) whenever the thing being
written about changes:

    ReaderNotesWidget(db, selected_date)              # scope="date", follows
                                                      # the canonical selection
    ReaderNotesWidget(db, scope="project", ...)       # ref set by the host via
                                                      # set_ref(project_id)

Storage is the matching `reader_notes_scoped` row per (scope, ref) — never
appended into the journal entry's or project's canonical document — using the
same canonical/derived split the journal uses: the rich content is
authoritative, `content_text` is a derived plain-text view for search and
export.

It reuses RichEditor because that component already exists and already carries
autosave-friendly load/save, inline photos and Ctrl+F — so reusing it is
genuinely less complexity than writing a second editor, which is the condition
Part 33 sets. It runs in `compact` mode so it doesn't duplicate the journal's
full word-processor toolbar: the main writing surface stays the full one.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .database import Database
from .date_state import SelectedDate, human
from .rich_editor import RichEditor
from .saving import autosave_enabled, document_has_content

AUTOSAVE_INTERVAL_MS = 1200

DEFAULT_HINT = "Definitions, concepts and references to keep beside today's writing."
DEFAULT_PLACEHOLDER = (
    "Instrumental convergence:\n"
    "The idea that different goals can produce similar intermediate "
    "strategies such as acquiring resources.\n\n"
    "Epistemic:\n"
    "Relating to knowledge or the conditions for knowing."
)

EMPTY_HINT = "Select something on the left to keep notes beside it."


class ReaderNotesWidget(QWidget):
    """Reader's Notes for one (scope, ref) at a time.

    For scope="date" a SelectedDate can be passed in and the widget follows
    it automatically — that is the Daily Journal's arrangement, where the
    canonical selection is the single source of truth for "which day".
    For any other scope the host calls set_ref() when its selection changes.
    """

    changed = Signal()

    def __init__(self, db: Database, selected_date: SelectedDate | None = None,
                 parent=None, *, scope: str = "date",
                 hint: str | None = None, placeholder: str | None = None):
        super().__init__(parent)
        self.db = db
        self.scope = scope
        self.selected_date = selected_date

        # The ref whose notes are currently LOADED. Not a second source of
        # truth for "the selected thing" — it exists only so a pending
        # autosave can be written back to what it was actually typed into,
        # even if the selection has since moved. Without it, a keystroke
        # followed quickly by a date or project change could save one
        # subject's notes onto another.
        self._loaded_ref: str | None = None

        # Built before the editor, because wiring textChanged below can fire
        # during construction on some Qt versions — the same init-ordering
        # rule the journal window already follows.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_tick)

        self._hint_text = hint if hint is not None else DEFAULT_HINT
        self.hint = QLabel(self._hint_text)
        self.hint.setWordWrap(True)
        self.hint.setObjectName("SubtleHint")  # sized by the app font setting, see theme.py

        self.editor = RichEditor(compact=True, link_dates=True)
        self.editor.text_edit.setPlaceholderText(
            placeholder if placeholder is not None else DEFAULT_PLACEHOLDER
        )
        self.editor.textChanged.connect(self._schedule_autosave)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.hint)
        layout.addWidget(self.editor, stretch=1)

        if self.selected_date is not None:
            self.selected_date.changed.connect(self._on_selected_date_changed)
            self.load(self.selected_date.value)
        else:
            # No ref yet (e.g. Projects with nothing selected): show the
            # editor disabled rather than letting someone type into notes
            # that have nowhere to be saved.
            self.set_ref(None)

    def hasHeightForWidth(self) -> bool:
        """Not passed up to the column this widget sits in.

        The word-wrapped hint makes the widget height-for-width, and a
        vertical layout that sees one height-for-width child treats that
        child's whole preferred height as its MINIMUM — so under the month
        panel in Daily Jorts, Reader's Notes kept its full height and the
        month grid was squeezed until the Marker row covered its last week
        at larger fonts (Group 3 fixes, C3; Master Spec §54). Reported as
        not height-for-width, it takes the height left over and its own
        layout still wraps the hint inside that."""
        return False

    # --------------------------------------------------------------- data
    def _on_selected_date_changed(self, date: str):
        # Save what's on screen BEFORE loading the new day, so an edit made
        # a fraction of a second before clicking another date isn't lost —
        # but only an actual edit: merely looking at a date writes nothing.
        self.flush(automatic=True)
        self.load(date)

    def _storage_subdir(self, ref: str) -> str:
        """Where inline photos for these notes are filed.

        Dates keep their existing year/month layout so images saved before
        Reader's Notes became scoped are still found at the same path.
        """
        if self.scope == "date":
            return ref[:7].replace("-", "/")
        return f"{self.scope}s/{ref}/notes"

    def set_ref(self, ref: str | int | None):  # noqa: D401
        """Points the widget at a different subject, saving the old one first.

        Passing None clears and disables the editor — the state Projects is
        in before a project is selected.
        """
        # Changing subject unloads the note, and a note is small enough that
        # silently losing an edit would be worse than saving one the user
        # hadn't explicitly asked to save. An untouched note is not written.
        self.flush(automatic=True)
        if ref is None:
            self._loaded_ref = None
            self.editor.text_edit.blockSignals(True)
            self.editor.load("", "html")
            self.editor.text_edit.blockSignals(False)
            self.editor.setEnabled(False)
            self.hint.setText(EMPTY_HINT)
            return
        self.editor.setEnabled(True)
        self.hint.setText(self._hint_text)
        self.load(str(ref))

    def load(self, ref: str):
        ref = str(ref)
        notes = self.db.get_notes(self.scope, ref)
        self.editor.set_storage_subdir(self._storage_subdir(ref))
        # blockSignals: setting the document text emits textChanged, which
        # would otherwise schedule an autosave of freshly-loaded content and
        # mark a clean subject dirty the moment it's opened.
        # A stored note with nothing written in it (the blank rows earlier
        # versions wrote just by visiting a date) opens as a genuinely empty
        # editor rather than as its leftover blank paragraphs.
        written = notes is not None and document_has_content(notes.content, notes.content_text)
        self.editor.text_edit.blockSignals(True)
        self.editor.load(notes.content if written else "",
                          notes.content_format if written else "html")
        self.editor.text_edit.blockSignals(False)
        self._loaded_ref = ref

    def _schedule_autosave(self):
        """Only queues a timed save when the user has asked for one. With
        autosave off the note is still saved by Ctrl+S, by leaving the
        subject, and on the way out — just never by a timer."""
        if autosave_enabled(self.db):
            self._autosave_timer.start()

    def _autosave_tick(self):
        """The timer fired. Checked again here because the preference can
        change while a save is already queued."""
        if autosave_enabled(self.db):
            self.flush(automatic=True)

    def is_dirty(self) -> bool:
        return self._loaded_ref is not None and self.editor.is_dirty()

    def flush(self, force: bool = False, automatic: bool = False) -> bool:
        """Writes the note if it differs from what is stored; returns whether
        anything was written.

        Two kinds of caller, the same rule for both about WHAT is written:

          * explicit (Ctrl+S, and anything that calls this plainly): the
            note is compared with the stored row and written if different —
            including a programmatic change, which Qt does not flag as an
            edit.
          * automatic=True (changing date or project, the autosave timer,
            leaving a tab, closing): only when the user actually edited the
            note. Visiting a date and moving on writes nothing at all.

        Either way a blank note never CREATES a row, and emptying a written
        note stores it as empty (""), which is what "no notes" looks like.
        `force` is accepted for older callers and means the explicit path.
        """
        if self._loaded_ref is None:
            return False
        if automatic and not force and not self.editor.is_dirty():
            return False
        content, plain = self.editor.save()
        written = document_has_content(content, plain)
        if not written:
            content, plain = "", ""
        stored = self.db.get_notes(self.scope, self._loaded_ref)
        if (stored is None and not written) or (stored is not None and stored.content == content):
            self.editor.mark_clean()
            return False
        self.db.save_notes(self.scope, self._loaded_ref, content,
                            content_format="html", content_text=plain)
        self.editor.mark_clean()
        self.changed.emit()
        return True

    def stop_timers(self):
        """Stops the autosave timer on the way out, before the database
        closes — a tick landing after that raises from inside Qt's event
        dispatch and prints a traceback that looks like a crash on exit."""
        self._autosave_timer.stop()

    def has_content(self) -> bool:
        return document_has_content(None, self.editor.plain_text())

    def title_for_date(self) -> str:
        if self.selected_date is None:
            return "Reader's Notes"
        return f"Reader's Notes — {human(self.selected_date.value)}"
