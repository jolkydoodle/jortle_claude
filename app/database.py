"""SQLite persistence layer for the journal app.

Schema
------
entries(date PK, title, body_md, body_format, body_text, tag, tag_color,
created_at, updated_at)
reader_notes_scoped(scope, ref, content, content_format, content_text,
created_at, updated_at, PK (scope, ref)) — Reader's Notes for either a
journal date (scope 'date') or a project (scope 'project').
reader_notes(date PK, content, content_format, content_text, created_at,
updated_at) — SUPERSEDED by reader_notes_scoped, whose migration copied every
row across as scope='date'. Kept, like `tasks`, so the pre-migration rows stay
recoverable; nothing writes to it any more.
calendar_events(id PK, date, start_minute, end_minute, title, notes, color,
all_day, done, opacity, created_at, updated_at, end_date, series_id,
occurrence_date) — end_date lets an event cross midnight or span days;
series_id/occurrence_date mark a row that replaces one occurrence of a
repeating event. event_recurrence(event_id PK, freq, interval, weekdays,
until_date) and event_exceptions(series_id, occurrence_date) hold repeating
events' rules and skipped dates; recurrence.py explains the model.
period_titles(kind, period, title, updated_at, PK (kind, period)) — optional
month ('YYYY-MM') and year ('YYYY') titles.
tasks(id PK, date, text, checked, position, created_at) — RETIRED as of round
22: the Daily Tasks feature it backed was replaced by the Day Calendar, and
every row was converted into a calendar_events row by
_migrate_tasks_to_calendar_events(). The table is deliberately left in place
rather than dropped so the original rows stay recoverable; nothing writes to
it any more. It is NOT the ToDo feature's table; see `todos` below.
todos(id PK, date, text, done, position, created_at, updated_at) — the ToDo
list shown beside the Day View. Its own concept: no time, no duration, not a
calendar event, and deliberately not the retired `tasks` table, whose rows
already became calendar events.
settings(key PK, value)

A day is the join key across the three user-authored stores — one journal
entry, one set of Reader's Notes, and any number of calendar events all hang
off the same ISO date, but each is its own row in its own table. They are
never merged into one another's canonical content.
projects(id PK, title, content_md, content_format, content_text, created_at,
updated_at, archived, category, folder_id) / project_sessions /
project_versions (id PK, project_id, content_md, content_format, content_text,
label, kind, saved_at) / project_folders(id PK, parent_id, name, position,
created_at, updated_at) — see the CREATE statements below. Projects live in
nested user-created folders (folder_id NULL = the root, "Uncategorized").
`category` is the old flat label: converted once into top-level folders and
not written after that.

Dates are stored as ISO strings 'YYYY-MM-DD' so they sort lexicographically.

Photos are no longer tracked in their own table (see the old `attachments`
table in earlier versions of this app) — they're embedded directly inline in
`body_md` / `content_md`, resolved against the attachments folder at render
time. `_migrate_legacy_attachments` converts any rows left over from that
older schema into inline references the first time this runs against an old
database, then drops the table.

body_md / content_md — canonical representation (round 21+)
--------------------------------------------------------------
Despite the "_md" name (kept from before round 21 — see below for why),
these columns hold whichever format `body_format` / `content_format` names
for that specific row:

  - "html": the editor's own Qt rich-text HTML (`QTextEdit.toHtml()`),
    which is the canonical, lossless representation as of round 21 — every
    formatting property the rich_editor.py toolbar can apply (alignment,
    line spacing, paragraph spacing before/after, superscript, subscript,
    strikethrough, color, lists, indentation, blank paragraphs, etc.)
    round-trips exactly through toHtml()/setHtml(), because both the writer
    and the reader are the same Qt engine reconstructing its own document
    model — see rich_editor.py's module docstring for the full diagnosis of
    why the *previous* representation (CommonMark Markdown, via
    toMarkdown()/setMarkdown()) could not do this.
  - "markdown": the pre-round-21 representation, for any row saved before
    this change. Existing data is never rewritten in place just to change
    its format — that would be a needless, riskier migration for no benefit
    to already-saved content. Instead, a "markdown" row loads exactly as it
    always did (through the old setMarkdown() path) and is transparently
    upgraded to "html" the NEXT time it's saved (RichEditor.save() always
    returns "html" — see that method). This is a one-time, per-row,
    read-triggers-eventual-upgrade migration, following the rule this
    codebase applies to historical data generally — never rewrite it
    speculatively, only migrate forward from an explicit user action
    (here: editing and saving).

  The columns keep their original "_md" names rather than being renamed to
  something like "body_content" — a rename would touch every query and
  every call site in the app for a purely cosmetic reason, which cuts
  against "minimal disruption to unrelated existing behavior." The comment
  above is the source of truth for what's actually inside them.

body_text / content_text — derived plain-text view (round 21+)
--------------------------------------------------------------
A plain-text-only copy of the same content, written alongside body_md/
content_md on every save, used by anything that needs to search or reason
about the WORDS in an entry without caring about formatting: full-text
search (search_entries), the "how long is this entry" heuristic used by the
calendar's day-length shading, and the "does this day have a real entry"
test the month grid paints from (see saving.MEANINGFUL_TEXT_SQL) — asking
that question of the markup would count `<p></p>` as writing. This is a
derived view, computed once by the widget
that already has the live QTextDocument in hand (RichEditor.plain_text()/
save()) and stored alongside the canonical column — nothing downstream ever
re-derives it independently or writes back into body_md/content_md from it,
per the "one canonical representation, others read from it" rule.

For rows saved before round 21 (format="markdown"), body_text/content_text
is backfilled to the same value as body_md/content_md at migration time —
Markdown source is already close enough to plain text that this preserves
existing search behaviour for old entries exactly as it was, with no
separate backfill pass needed.
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import security
from .paths import get_db_path
from .recurrence import (
    MINUTES_PER_DAY, Occurrence, RecurrenceRule, absolute_end, add_days, days_between,
    duration_minutes, end_date_of, next_occurrence, nth_occurrences, occurrence_dates,
    occurrences_for_series,
    remap_dates, remap_for_rule, shift_rule, sort_key, span_days, span_from, validate_span
)
from .saving import (
    MEANINGFUL_TEXT_SQL, content_sql, document_has_content, register_sql_functions
)

# calendar_events.opacity sentinel: "this event has no explicitly chosen
# transparency, so follow the application default" (theme.DEFAULT_EVENT_
# TRANSPARENCY). A sentinel rather than NULL because the column is NOT NULL
# and rebuilding the table to relax that would be real migration risk for a
# cosmetic gain — exactly the trade-off Part 2 asks to weigh. -1 is
# unambiguous: a real percentage is never negative.
OPACITY_FOLLOWS_DEFAULT = -1

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    date TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL DEFAULT '',
    body_format TEXT NOT NULL DEFAULT 'html',
    body_text TEXT NOT NULL DEFAULT '',
    tag TEXT,
    tag_color TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    text TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Reader's Notes: short supporting material (definitions, concepts, context)
-- kept alongside a day's journal entry or a project, but stored as its own
-- row, never appended into that subject's canonical document. Same
-- canonical/derived split as entries: `content` is authoritative in whichever
-- format `content_format` names, `content_text` is the derived plain-text
-- view. Always 'html' in practice (this table postdates the round-21 move to
-- HTML, so it has no Markdown rows), but the column exists so this table
-- reads the same way as entries/projects rather than being a special case.
--
-- Scoped: notes attach either to a journal DATE or to a PROJECT, so the key
-- is (scope, ref) rather than a bare date — scope is 'date' or 'project',
-- ref is the ISO date or the project id as text. One notes system serving
-- both contexts, which is the point: a second, parallel notes table for
-- projects is exactly the thing to avoid.
--
-- The original date-only table below is kept and left populated rather than
-- dropped: SQLite cannot change a primary key in place, so this is a new
-- table with the rows copied across, and keeping the original means the
-- pre-migration data stays recoverable from the file itself.
CREATE TABLE IF NOT EXISTS reader_notes_scoped (
    scope TEXT NOT NULL,
    ref TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    content_format TEXT NOT NULL DEFAULT 'html',
    content_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, ref)
);

CREATE TABLE IF NOT EXISTS reader_notes (
    date TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    content_format TEXT NOT NULL DEFAULT 'html',
    content_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Interactive day-calendar events: structured entities in their own table,
-- not text embedded in a journal entry. Associated with the journal only
-- through `date`.
--
-- Times are stored as integer minutes from midnight (0..1440) rather than
-- 'HH:MM' strings or full timestamps. Three reasons: the day view's
-- time<->pixel maths is integer arithmetic with no parsing in the hot
-- repaint path; snapping to a 15-minute grid is a modulo; and ordering,
-- overlap detection and duration are plain integer comparisons. Converting
-- to wall-clock for display or to ICS is a single divmod at the edge.
-- `end_minute` is exclusive and always strictly greater than `start_minute`
-- (enforced in day_calendar.py's clamping, so an invalid event can't be
-- constructed by dragging).
--
-- `all_day` exists specifically so the round-22 migration of the old Daily
-- Tasks feature can preserve those tasks WITHOUT inventing start/end times
-- they never had — an untimed task becomes an untimed event, which is the
-- honest representation. It doubles as the natural hook if all-day events
-- are wanted later. `done` likewise preserves each migrated task's
-- checked/unchecked state rather than discarding it.
--
-- Group 3 (recurrence.py explains the model): `end_date` NULL = the event
-- ends on its start date; a timed event runs from date+start_minute to
-- end_date+end_minute, so an overnight event is one row. `series_id` and
-- `occurrence_date` are set only on a row that replaces one occurrence of a
-- repeating event: the series' own row id, and the date that occurrence
-- originally had. Added by _migrate_event_spans() on older databases, which
-- also creates their indexes. (Comments stay outside the column list:
-- SQLite's ALTER TABLE … DROP COLUMN rewrites that text and trips over them.)
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    start_minute INTEGER NOT NULL DEFAULT 0,
    end_minute INTEGER NOT NULL DEFAULT 60,
    title TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    color TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    -- Background transparency, 0-100, where 100 is fully opaque. Stored as a
    -- percentage rather than an alpha byte purely so the value is readable
    -- if anyone ever inspects the database by hand. Defaulting to 100 means
    -- every event that predates the feature stays exactly as it looked.
    opacity INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    end_date TEXT,
    series_id INTEGER,
    occurrence_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_date ON calendar_events(date);

-- A repeating event's rule. One row per series, keyed by the series' own
-- event row (its "master"), whose date is the series' first date. `weekdays`
-- is a comma list, 0 = Sunday … 6 = Saturday, used by 'weekly' only.
-- `until_date` is the last date an occurrence may start on; NULL = no end.
-- `series_root`: the part a "this and following" split came from (NULL = the
-- series is its own root), so "entire series" reaches every part.
CREATE TABLE IF NOT EXISTS event_recurrence (
    event_id INTEGER PRIMARY KEY REFERENCES calendar_events(id) ON DELETE CASCADE,
    freq TEXT NOT NULL,
    interval INTEGER NOT NULL DEFAULT 1,
    weekdays TEXT NOT NULL DEFAULT '',
    until_date TEXT,
    week_start INTEGER NOT NULL DEFAULT 0,
    series_root INTEGER
);

-- Series dates the rule must NOT produce: cancelled occurrences, and
-- occurrences that were replaced by their own row (calendar_events with
-- series_id/occurrence_date). Occurrences themselves are never stored.
CREATE TABLE IF NOT EXISTS event_exceptions (
    series_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    occurrence_date TEXT NOT NULL,
    PRIMARY KEY (series_id, occurrence_date)
);

-- Optional one-line titles for a month ('month', 'YYYY-MM') or a year
-- ('year', 'YYYY') — one canonical value each, shown wherever that period
-- is (Master Spec §§11-12). Created empty: a fresh database must not look
-- like it holds user content (data_migration.has_user_content).
CREATE TABLE IF NOT EXISTS period_titles (
    kind TEXT NOT NULL,
    period TEXT NOT NULL,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (kind, period)
);

-- User-created project folders, nested through parent_id (NULL = top
-- level). Projects point at one with projects.folder_id (NULL = the root,
-- shown as "Uncategorized"). Everything refers to folders and projects by
-- id, so renaming or moving either never breaks a reference (§40). The
-- foreign keys make SQLite itself refuse to delete a folder that still
-- holds something.
CREATE TABLE IF NOT EXISTS project_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES project_folders(id),
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Named Day Markers. Before this, a marker was just a colour written onto
-- the entry row itself; markers now have user-chosen names and live in their
-- own table so a name can be edited once rather than on every date that uses
-- it. `entries.tag` holds this marker's id (or, for rows that predate the
-- migration, one of the old built-in tag keys) — see _migrate_named_markers.
CREATE TABLE IF NOT EXISTS day_markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Long-term writing projects: not tied to any calendar date. `category` is
-- the legacy flat grouping label (NULL/'' = Uncategorized): converted once
-- into project_folders by _migrate_project_folders() and never written after
-- that (decision G3-8), but kept so its values stay readable in the file.
-- `folder_id` is the project's folder (NULL = the root).
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL DEFAULT '',
    content_format TEXT NOT NULL DEFAULT 'html',
    content_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    folder_id INTEGER REFERENCES project_folders(id)
);

-- One row per (project, calendar day) it was touched on, so "when was this
-- worked on" is a cheap query rather than something reconstructed from
-- version snapshots.
CREATE TABLE IF NOT EXISTS project_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    work_date TEXT NOT NULL,
    first_edit_at TEXT NOT NULL,
    last_edit_at TEXT NOT NULL,
    UNIQUE(project_id, work_date)
);
CREATE INDEX IF NOT EXISTS idx_project_sessions_project ON project_sessions(project_id);

-- Full-content snapshots: one automatic snapshot at the start of each new
-- work day (capturing the content as it stood before that day's edits),
-- plus any number of manual, user-labeled checkpoints.
CREATE TABLE IF NOT EXISTS project_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content_md TEXT NOT NULL,
    content_format TEXT NOT NULL DEFAULT 'html',
    content_text TEXT NOT NULL DEFAULT '',
    label TEXT,
    kind TEXT NOT NULL DEFAULT 'manual',
    saved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_versions_project ON project_versions(project_id);

-- ToDo items for a day. Deliberately NOT the `tasks` table above, and
-- deliberately not calendar events either.
--
-- `tasks` is the retired Daily Tasks feature, and every one of its rows was
-- converted into a calendar event by _migrate_tasks_to_calendar_events().
-- Reading it here would show a user their old tasks a second time, beside
-- the untimed events those same rows already became — the exact duplication
-- this feature is supposed to avoid. So this is its own table, starting
-- empty, and the two never meet.
--
-- Not calendar events either: a ToDo has no time, no duration, no colour and
-- no place on a grid. Modelling it as an all-day event would make every
-- calendar query have to ask "but is this REALLY an event", which is how the
-- old tasks feature became untimed events in the first place.
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(date);

-- Daily-entry version history (Master Spec §44). One row per HISTORICAL
-- state of an entry, created at meaningful editing boundaries by
-- entry_history.py — never per autosave or keystroke. Distinct from the
-- working state (the `entries` row, which autosave keeps current) and from
-- full backups (backup.py). Keyed by the entry's date, NOT a foreign key to
-- `entries`, so history survives the entry itself being emptied.
--
-- Immutable: the trigger below refuses every UPDATE. Rows can be deleted
-- (the user's "delete previous versions" commands), never rewritten.
-- `content_hash` lets "is this state already recorded?" be answered without
-- reading any bodies; listing reads only metadata, and a body is loaded
-- when one revision is actually opened.
CREATE TABLE IF NOT EXISTS entry_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    body_format TEXT NOT NULL DEFAULT 'html',
    body_text TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_revisions_date ON entry_revisions(date, created_at);
CREATE INDEX IF NOT EXISTS idx_entry_revisions_hash ON entry_revisions(date, content_hash);
CREATE TRIGGER IF NOT EXISTS entry_revisions_immutable
BEFORE UPDATE ON entry_revisions
BEGIN
    SELECT RAISE(ABORT, 'entry revisions are immutable');
END;

-- Recovery checkpoints for substantial destructive edits (Master Spec
-- §44.5): the content as it stood just before a save that removed a large
-- amount of it. Separate from entry_revisions on purpose: it covers Projects
-- as well as daily entries (scope/ref, the same addressing Reader's Notes
-- uses), and deleting an entry's version history must not delete these.
-- Same immutability rule. No automatic pruning (§46.2) — the user deletes
-- them from File -> Recovery.
CREATE TABLE IF NOT EXISTS recovery_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    ref TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    body_format TEXT NOT NULL DEFAULT 'html',
    body_text TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    prior_chars INTEGER NOT NULL DEFAULT 0,
    prior_paragraphs INTEGER NOT NULL DEFAULT 0,
    removed_chars INTEGER NOT NULL DEFAULT 0,
    removed_paragraphs INTEGER NOT NULL DEFAULT 0,
    remaining_chars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recovery_created ON recovery_checkpoints(created_at);
CREATE TRIGGER IF NOT EXISTS recovery_checkpoints_immutable
BEFORE UPDATE ON recovery_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'recovery checkpoints are immutable');
END;
"""


@dataclass
class Entry:
    date: str
    title: str = ""
    body_md: str = ""
    body_format: str = "html"
    body_text: str = ""
    tag: Optional[str] = None
    tag_color: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Todo:
    """One ToDo item, belonging to a date and to nothing else."""

    id: int
    date: str
    text: str
    done: bool = False
    position: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ReaderNotes:
    date: str
    content: str = ""
    content_format: str = "html"
    content_text: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class DayMarker:
    """A user-defined day marker: a name plus a colour. Assigned to dates
    through entries.tag (which holds the marker's id)."""
    id: int
    name: str
    color: str
    position: int = 0


@dataclass
class CalendarEvent:
    """One day-calendar event. `start_minute`/`end_minute` are minutes from
    midnight, end exclusive — see the CREATE TABLE comment for why."""
    id: int
    date: str
    start_minute: int
    end_minute: int
    title: str = ""
    notes: str = ""
    color: Optional[str] = None
    # None means "follow the current theme" and keeps following it when the
    # theme changes; a value means the user chose it explicitly. The theme's
    # colour is deliberately never written here — see event_render.py.
    #
    # Opacity works the same way, and this default is load-bearing. A draft
    # event handed to the editor is built by calling this constructor, so a
    # default of 100 here meant every NEW event opened the editor at "0%
    # transparent" and then stored 100 as if the user had chosen it — the
    # sentinel exists precisely so that "the user has not chosen" is a state
    # the row can hold, and the dataclass has to agree with it.
    opacity: int = OPACITY_FOLLOWS_DEFAULT
    all_day: bool = False
    done: bool = False
    created_at: str = ""
    updated_at: str = ""
    # Group 3 — see recurrence.py. None = ends on `date`.
    end_date: Optional[str] = None
    # Set only on a row that replaces one occurrence of a repeating event.
    series_id: Optional[int] = None
    occurrence_date: Optional[str] = None

    @property
    def duration(self) -> int:
        """Minutes from start to end, across days."""
        from .recurrence import duration_minutes
        return duration_minutes(self)


@dataclass
class Project:
    id: int
    title: str
    content_md: str
    created_at: str
    updated_at: str
    archived: bool
    category: Optional[str] = None
    content_format: str = "html"
    content_text: str = ""
    folder_id: Optional[int] = None


@dataclass
class ProjectFolder:
    id: int
    parent_id: Optional[int]
    name: str
    position: int = 0


@dataclass
class DayMeta:
    """What the month and year grids need to know about one date, without
    loading any document body (Master Spec §7.4)."""
    date: str
    has_entry: bool = False
    title: str = ""
    length: int = 0
    tag_color: Optional[str] = None
    other_content: bool = False


class FolderNotEmpty(Exception):
    """A folder that still holds folders or projects cannot be deleted
    (decision G3-7)."""


class InvalidMove(Exception):
    """A folder cannot be moved into itself or one of its descendants."""


@dataclass
class ProjectSession:
    work_date: str
    first_edit_at: str
    last_edit_at: str


@dataclass
class ProjectVersion:
    id: int
    project_id: int
    content_md: str
    label: Optional[str]
    kind: str
    saved_at: str
    content_format: str = "html"
    content_text: str = ""


@dataclass
class EntryRevision:
    """One historical state of a daily entry. `body`/`body_text` are None
    when only the metadata was loaded (list_entry_revisions)."""
    id: int
    date: str
    title: str
    reason: str
    created_at: str
    content_hash: str
    char_count: int
    body: Optional[str] = None
    body_format: str = "html"
    body_text: Optional[str] = None


@dataclass
class RecoveryCheckpoint:
    """Content saved just before a large deletion. `body`/`body_text` are
    None when only the metadata was loaded."""
    id: int
    scope: str
    ref: str
    title: str
    prior_chars: int
    prior_paragraphs: int
    removed_chars: int
    removed_paragraphs: int
    remaining_chars: int
    created_at: str
    content_hash: str
    body: Optional[str] = None
    body_format: str = "html"
    body_text: Optional[str] = None


def _whole_days(minutes: int) -> int:
    """Whole days in a signed number of minutes, rounded toward zero."""
    days = abs(int(minutes)) // MINUTES_PER_DAY
    return days if minutes >= 0 else -days


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_db_path()
        # Plain SQLite, or SQLCipher when the journal is encrypted — decided
        # from the file itself (security.connect). Raises security.Locked if
        # it is encrypted and this process has not been given the key.
        self._conn = security.connect(self.db_path)
        # "Is this a written entry?" is one Python function, callable from SQL
        # on this connection (see saving.register_sql_functions).
        register_sql_functions(self._conn)
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL + NORMAL synchronous: noticeably snappier for the frequent
        # small autosave writes this app does, with negligible durability
        # trade-off for a single-user desktop app.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        # Runs before _migrate_legacy_attachments()/_migrate_add_project_category()
        # deliberately: both of those call upsert_entry()/get_entry(), whose SQL
        # now references body_format/body_text unconditionally — those columns
        # must already exist on THIS connection before either migration can
        # safely touch the entries table, even on a very old database that
        # needs every migration to run in the same __init__ call.
        self._migrate_add_rich_text_columns()
        self._migrate_legacy_attachments()
        self._migrate_add_project_category()
        self._migrate_tasks_to_calendar_events()
        self._migrate_event_opacity()
        self._migrate_default_event_transparency()
        self._migrate_named_markers()
        self._migrate_scoped_reader_notes()
        self._migrate_event_spans()
        self._migrate_project_folders()

    def close(self):
        self._conn.close()

    @property
    def encrypted(self) -> bool:
        return security.db_file_state(self.db_path) == "encrypted"

    def total_changes(self) -> int:
        """Rows changed through this connection since it opened — a cheap way
        to ask "has anything been saved since I last looked?"."""
        return self._conn.total_changes

    def export_plain_copy(self, destination):
        """The unencrypted copy kept beside an encrypted journal while the user
        has chosen to keep unencrypted copies (security.export_plain_copy)."""
        security.export_plain_copy(self._conn, destination)

    # The schema version is derived from what's actually present rather than
    # stored as a number someone has to remember to bump. It's reported in
    # the archive manifest, where its job is to tell a future reader which
    # shape of database they're looking at.
    def schema_version(self) -> int:
        tables = {
            r["name"] for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        entry_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "entry_revisions" in tables:
            return 4   # entry version history + recovery checkpoints
        if "calendar_events" in tables and "reader_notes" in tables:
            return 3   # round 22: Reader's Notes + day calendar
        if "body_format" in entry_cols:
            return 2   # round 21: rich-text HTML persistence
        return 1

    def checkpoint(self):
        """Flushes the write-ahead log into the main database file, so a
        plain file copy of it is complete. Best-effort: a failure here means
        a copy might miss the most recent writes, which is worth a warning
        but never worth aborting an export over."""
        try:
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except security.DB_ERRORS as exc:
            print(f"Warning: WAL checkpoint failed before copy: {exc}")

    def _migrate_legacy_attachments(self):
        """One-time upgrade path from the earlier schema version, which
        tracked photos in a separate `attachments` table instead of inline
        in the entry's own text. Converts each row into an inline Markdown
        image reference on the corresponding date, then drops the table."""
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='attachments'"
        ).fetchone()
        if not exists:
            return
        rows = self._conn.execute("SELECT date, filename, original_name FROM attachments").fetchall()
        for r in rows:
            date, filename, original_name = r["date"], r["filename"], r["original_name"]
            entry = self.get_entry(date)
            image_md = f"![{original_name}]({filename})"
            # Explicitly "markdown": this migration only ever runs once, at
            # startup, before anything in this session could have saved an
            # entry as "html" yet — so every entry it touches here is still
            # genuinely in the old Markdown representation, and writing
            # Markdown image syntax into it under any other format label
            # would make it render as literal "![...](...)" text instead of
            # an image once re-opened.
            if entry is None:
                self.upsert_entry(date, body_md=image_md, body_format="markdown", body_text=image_md)
            elif filename not in entry.body_md:
                separator = "\n\n" if entry.body_md.strip() else ""
                new_body = entry.body_md + separator + image_md
                self.upsert_entry(date, body_md=new_body, body_format="markdown", body_text=new_body)
        self._conn.execute("DROP TABLE IF EXISTS attachments")
        self._conn.commit()

    def _migrate_add_project_category(self):
        """One-time upgrade for databases created before Projects
        had categories: SCHEMA's CREATE TABLE IF NOT EXISTS only applies to
        a brand-new database, so an already-existing `projects` table needs
        the column added explicitly. All existing projects end up with
        category = NULL, i.e. "Uncategorized" — nothing is reassigned."""
        cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(projects)").fetchall()]
        if "category" not in cols:
            self._conn.execute("ALTER TABLE projects ADD COLUMN category TEXT")
            self._conn.commit()

    def _migrate_add_rich_text_columns(self):
        """Round 21: adds body_format/body_text (entries) and
        content_format/content_text (projects, project_versions) to a
        database created before the rich-text persistence overhaul — see
        this module's docstring for what these columns mean and why.

        Non-destructive by construction: every statement here is either
        `ALTER TABLE ... ADD COLUMN` (which only ever adds a new column with
        a default value — it cannot remove or overwrite any existing data)
        or an `UPDATE` that backfills those new columns from data that's
        already there. Still, per an explicit requirement to back up
        before any destructive-*adjacent* schema change, this makes one
        safety copy of the whole database file before touching anything —
        see _backup_db_file_once() — the first time (and only the first
        time) it finds this migration hasn't run yet on THIS database file.
        A database created fresh (by the CREATE TABLE statements above,
        already including these columns) never hits this path at all: the
        columns are already there, so there's nothing to migrate or back up.
        """
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "body_format" in cols:
            return  # already migrated (or a fresh database that never needed it)

        self._backup_db_file_once("pre-round21-richtext")

        # Existing rows in a pre-round-21 database were always saved through
        # the old toMarkdown()/setMarkdown() path — so BOTH the new format
        # column and the new derived-plain-text column backfill to that
        # reality explicitly, rather than to this module's "html" default
        # (which only describes what a FUTURE save into these columns means).
        self._conn.execute("ALTER TABLE entries ADD COLUMN body_format TEXT NOT NULL DEFAULT 'markdown'")
        self._conn.execute("ALTER TABLE entries ADD COLUMN body_text TEXT NOT NULL DEFAULT ''")
        self._conn.execute("UPDATE entries SET body_text = body_md")

        proj_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "content_format" not in proj_cols:
            self._conn.execute("ALTER TABLE projects ADD COLUMN content_format TEXT NOT NULL DEFAULT 'markdown'")
            self._conn.execute("ALTER TABLE projects ADD COLUMN content_text TEXT NOT NULL DEFAULT ''")
            self._conn.execute("UPDATE projects SET content_text = content_md")

        version_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(project_versions)").fetchall()}
        if "content_format" not in version_cols:
            self._conn.execute("ALTER TABLE project_versions ADD COLUMN content_format TEXT NOT NULL DEFAULT 'markdown'")
            self._conn.execute("ALTER TABLE project_versions ADD COLUMN content_text TEXT NOT NULL DEFAULT ''")
            self._conn.execute("UPDATE project_versions SET content_text = content_md")

        self._conn.commit()

    TASKS_MIGRATION_SETTING = "migrated_tasks_to_calendar_events"

    def _migrate_tasks_to_calendar_events(self):
        """Round 22: the Daily Tasks feature was replaced by the interactive
        Day Calendar, so every existing task becomes a calendar event on the
        same date.

        Three deliberate choices here, all in service of "preserve all
        existing user data":

        1. Migrated tasks become UNTIMED (all_day=1) events, not events at
           some invented default hour. A task never had a start or end time;
           manufacturing 9:00-9:30 for it would be fabricating data the user
           never entered, and would scatter their old checklist across a
           timeline as though they'd scheduled it. Untimed events render in
           the strip above the timeline, which is the honest equivalent of
           the list they used to be.
        2. `done` carries each task's checked state across, so a completed
           task still reads as completed.
        3. The `tasks` table itself is NOT dropped. This migration only ever
           reads it. Keeping it costs a few kilobytes and means the original
           rows remain recoverable from the database file (and from every
           existing backup zip) if anything about the conversion is ever
           questioned — the opposite of the `attachments` migration above,
           which did drop its source table, and which is the precedent this
           deliberately departs from given how much more user-authored
           content is at stake here.

        Guarded by a settings flag rather than by "are there any events yet",
        so a user who migrates, deletes the migrated events because they
        didn't want them, and restarts doesn't get them all resurrected.
        """
        if self.get_setting(self.TASKS_MIGRATION_SETTING) == "1":
            return

        rows = self._conn.execute(
            "SELECT date, text, checked FROM tasks ORDER BY date ASC, position ASC, id ASC"
        ).fetchall()
        if not rows:
            # Nothing to convert (fresh install, or tasks never used). Still
            # record the flag so this query doesn't run on every launch.
            self.set_setting(self.TASKS_MIGRATION_SETTING, "1")
            return

        self._backup_db_file_once("pre-round22-daycalendar")

        now = datetime.now().isoformat(timespec="seconds")
        self._conn.executemany(
            "INSERT INTO calendar_events (date, start_minute, end_minute, title, notes, "
            "color, all_day, done, created_at, updated_at) "
            "VALUES (?, 0, 0, ?, '', NULL, 1, ?, ?, ?)",
            [(r["date"], r["text"], 1 if r["checked"] else 0, now, now) for r in rows],
        )
        self._conn.commit()
        self.set_setting(self.TASKS_MIGRATION_SETTING, "1")

    def _migrate_event_opacity(self):
        """Adds calendar_events.opacity to a database created before event
        transparency existed. Additive only: every existing event gets 100
        (fully opaque), so nothing looks different until the user changes it."""
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(calendar_events)").fetchall()}
        if cols and "opacity" not in cols:
            self._conn.execute(
                "ALTER TABLE calendar_events ADD COLUMN opacity INTEGER NOT NULL DEFAULT 100")
            self._conn.commit()

    NOTES_SCOPE_MIGRATION_SETTING = "migrated_scoped_reader_notes"

    def _migrate_scoped_reader_notes(self):
        """Copies date-scoped Reader's Notes into the scoped table.

        Additive: every existing row becomes ('date', <date>) and the
        original table is left exactly as it was. Runs once.
        """
        if self.get_setting(self.NOTES_SCOPE_MIGRATION_SETTING) == "1":
            return
        rows = self._conn.execute("SELECT * FROM reader_notes").fetchall()
        for r in rows:
            self._conn.execute(
                "INSERT OR IGNORE INTO reader_notes_scoped "
                "(scope, ref, content, content_format, content_text, created_at, updated_at) "
                "VALUES ('date', ?, ?, ?, ?, ?, ?)",
                (r["date"], r["content"], r["content_format"], r["content_text"],
                 r["created_at"], r["updated_at"]),
            )
        self._conn.commit()
        self.set_setting(self.NOTES_SCOPE_MIGRATION_SETTING, "1")

    def _migrate_event_spans(self):
        """Group 3: the columns that let an event cross midnight or belong to
        a repeating series (recurrence.py).

        Additive only: existing rows get NULL in every new column, which
        means exactly what they meant before — an event that ends on its own
        date and repeats nothing. No row is rewritten. Safe to run on every
        open (each step checks first).
        """
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(calendar_events)").fetchall()}
        for column in ("end_date", "series_id", "occurrence_date"):
            if column not in cols:
                kind = "INTEGER" if column == "series_id" else "TEXT"
                self._conn.execute(f"ALTER TABLE calendar_events ADD COLUMN {column} {kind}")
        rule_cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(event_recurrence)").fetchall()}
        if "week_start" not in rule_cols:
            self._conn.execute(
                "ALTER TABLE event_recurrence ADD COLUMN week_start INTEGER NOT NULL DEFAULT 0")
        # Group 3 fixes (GF-3): the parts of a series split by "this and
        # following" point at the part they came from, so "entire series"
        # can still reach all of them. NULL = the series is its own root
        # (every series made before this column existed).
        if "series_root" not in rule_cols:
            self._conn.execute("ALTER TABLE event_recurrence ADD COLUMN series_root INTEGER")
        # end_date: finding the events that START before a range but run
        # into it, without scanning every event ever made (occurrences_between).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calendar_events_end_date ON calendar_events(end_date)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calendar_events_series ON calendar_events(series_id)")
        self._conn.commit()

    FOLDERS_MIGRATION_SETTING = "migrated_project_categories_to_folders"

    def _migrate_project_folders(self):
        """Group 3: flat project categories become top-level folders (G3-8).

        Each distinct category (surrounding spaces ignored, since two labels
        differing only by them looked identical in the list) becomes one
        top-level folder with that name, and its projects are placed in it.
        Projects without a category stay at the root ("Uncategorized").
        `projects.category` is left exactly as it was and is never written
        again. Runs once, guarded by a settings flag, in one transaction.
        """
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(projects)").fetchall()}
        if "folder_id" not in cols:
            self._conn.execute(
                "ALTER TABLE projects ADD COLUMN folder_id INTEGER REFERENCES project_folders(id)")
            self._conn.commit()
        if self.get_setting(self.FOLDERS_MIGRATION_SETTING) == "1":
            return
        # Blank means blank by Python's rule — spaces, tabs, newlines and
        # non-breaking spaces alike — so a whitespace-only category goes to
        # Uncategorized instead of becoming a folder with no name (Group 3
        # fixes, D2). SQL's TRIM only removes spaces, so it isn't used here.
        rows = [r for r in self._conn.execute(
            "SELECT id, category FROM projects WHERE category IS NOT NULL "
            "AND folder_id IS NULL").fetchall() if r["category"].strip()]
        now = datetime.now().isoformat(timespec="seconds")
        folder_for: dict = {}
        try:
            # Sorted by name ignoring case, then exactly, so "Work" and
            # "work" come out in the same order on every run.
            for name in sorted({r["category"].strip() for r in rows},
                               key=lambda n: (n.lower(), n)):
                cur = self._conn.execute(
                    "INSERT INTO project_folders (parent_id, name, position, created_at, updated_at) "
                    "VALUES (NULL, ?, ?, ?, ?)", (name, len(folder_for), now, now))
                folder_for[name] = cur.lastrowid
            for r in rows:
                self._conn.execute("UPDATE projects SET folder_id=? WHERE id=?",
                                   (folder_for[r["category"].strip()], r["id"]))
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
                (self.FOLDERS_MIGRATION_SETTING,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    MARKER_MIGRATION_SETTING = "migrated_named_day_markers"

    # Bumped once. The first pass converted events that predated
    # transparency; this second pass catches events created BETWEEN that pass
    # and the fix to CalendarEvent.opacity's default, which stored an explicit
    # 100 for every new event because the draft handed to the editor carried
    # one. Those 100s are the bug's output, not anybody's choice.
    TRANSPARENCY_DEFAULT_SETTING = "migrated_event_transparency_default_2"

    def _migrate_default_event_transparency(self):
        """Lets events that never had a chosen transparency follow the default.

        Background: when transparency was added, every existing event was
        given opacity 100 by the column default — not by the user. Now that
        the application default is no longer fully opaque, those events would stay
        stubbornly opaque forever, because 100 is indistinguishable from
        "the user chose fully opaque".

        So rows sitting at exactly 100 are converted once to the
        follow-the-default sentinel. Any other value is a number the user
        actually moved a slider to, and is left exactly as it is — which is
        the distinction Part 13 asks for. This is cosmetic and reversible
        either way: the event editor can set any transparency back.

        Guarded by a settings flag, so a user who deliberately sets an event
        to fully opaque AFTER this runs keeps that choice.
        """
        if self.get_setting(self.TRANSPARENCY_DEFAULT_SETTING) == "1":
            return
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(calendar_events)").fetchall()}
        if "opacity" in cols:
            self._conn.execute(
                "UPDATE calendar_events SET opacity = ? WHERE opacity = 100",
                (OPACITY_FOLLOWS_DEFAULT,))
            self._conn.commit()
        self.set_setting(self.TRANSPARENCY_DEFAULT_SETTING, "1")

    def _migrate_named_markers(self):
        """Turns the old colour-only day markers into named ones.

        Before this, a marked day stored a built-in tag key and a colour
        directly on its own entry row, and the set of markers was a hard-coded
        list. Markers are now user-defined records with a name the user picks,
        which means the existing data has to be carried across rather than
        replaced: every distinct (tag, colour) pair already present in the
        database becomes a day_markers row, seeded with the name that pair
        used to display under, and each entry's `tag` is repointed at the new
        marker's id.

        Nothing is deleted. `entries.tag_color` keeps its value, so even if
        this migration were somehow wrong the original colour is still on the
        row. Guarded by a settings flag so it runs exactly once.
        """
        if self.get_setting(self.MARKER_MIGRATION_SETTING) == "1":
            return

        from .theme import DEFAULT_TAGS
        known = {key: label for key, label, _color in DEFAULT_TAGS}
        now = datetime.now().isoformat(timespec="seconds")

        rows = self._conn.execute(
            "SELECT DISTINCT tag, tag_color FROM entries "
            "WHERE tag IS NOT NULL AND tag != ''"
        ).fetchall()

        for position, row in enumerate(rows):
            tag, color = row["tag"], row["tag_color"] or "#3f8ede"
            if tag.isdigit():
                continue  # already a marker id
            name = known.get(tag, "Custom" if tag == "custom" else tag.title())
            existing = self._conn.execute(
                "SELECT id FROM day_markers WHERE name = ? AND color = ?", (name, color)
            ).fetchone()
            if existing:
                marker_id = existing["id"]
            else:
                cur = self._conn.execute(
                    "INSERT INTO day_markers (name, color, position, created_at) "
                    "VALUES (?, ?, ?, ?)", (name, color, position, now))
                marker_id = cur.lastrowid
            self._conn.execute(
                "UPDATE entries SET tag = ? WHERE tag = ? AND COALESCE(tag_color,'') = ?",
                (str(marker_id), tag, row["tag_color"] or ""))

        self._conn.commit()

        # A database with no marked days at all (a fresh install) gets the
        # four markers this app has always offered, as ORDINARY ROWS — the
        # user can rename, recolour or delete any of them, and add their own.
        # That is the difference the spec draws: the names must not be
        # hard-coded *behaviour*, but starting from a blank list would make a
        # feature that used to work out of the box need setup first.
        if not self._conn.execute("SELECT 1 FROM day_markers LIMIT 1").fetchone():
            for position, (_key, label, color) in enumerate(DEFAULT_TAGS):
                self._conn.execute(
                    "INSERT INTO day_markers (name, color, position, created_at) "
                    "VALUES (?, ?, ?, ?)", (label, color, position, now))
            self._conn.commit()

        self.set_setting(self.MARKER_MIGRATION_SETTING, "1")

    # ------------------------------------------------------------ markers
    def list_day_markers(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM day_markers ORDER BY position ASC, id ASC").fetchall()
        return [DayMarker(id=r["id"], name=r["name"], color=r["color"],
                           position=r["position"]) for r in rows]

    def create_day_marker(self, name: str, color: str) -> "DayMarker":
        now = datetime.now().isoformat(timespec="seconds")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM day_markers").fetchone()
        cur = self._conn.execute(
            "INSERT INTO day_markers (name, color, position, created_at) VALUES (?, ?, ?, ?)",
            (name, color, row["p"], now))
        self._conn.commit()
        return DayMarker(id=cur.lastrowid, name=name, color=color, position=row["p"])

    def update_day_marker(self, marker_id: int, name: str = None, color: str = None):
        updates = {}
        if name is not None:
            updates["name"] = name
        if color is not None:
            updates["color"] = color
        if not updates:
            return
        assignments = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(f"UPDATE day_markers SET {assignments} WHERE id=?",
                            (*updates.values(), marker_id))
        # Dates carry the marker's colour too (it predates this table and is
        # what the calendar grid paints from), so recolouring a marker has to
        # update the days using it or the grid would keep the old colour.
        if color is not None:
            self._conn.execute("UPDATE entries SET tag_color=? WHERE tag=?",
                                (color, str(marker_id)))
        self._conn.commit()

    def delete_day_marker(self, marker_id: int):
        """Removes the marker and unmarks the days that used it. The days
        themselves — entries, notes, events — are untouched."""
        self._conn.execute("DELETE FROM day_markers WHERE id=?", (marker_id,))
        self._conn.execute(
            "UPDATE entries SET tag=NULL, tag_color=NULL WHERE tag=?", (str(marker_id),))
        self._conn.commit()

    def _backup_db_file_once(self, tag: str):
        """Copies the current database file to a sibling
        `journal.<tag>.db` before a schema migration touches it, unless
        that exact backup already exists (so re-running this on an
        already-migrated database, or across repeated app launches, never
        overwrites the one safety copy with a later, already-migrated
        state). Best-effort: a failure to copy is logged, not raised — a
        missing safety copy should never be the reason a migration (and
        therefore the app) refuses to start."""
        backup_path = self.db_path.with_name(f"{self.db_path.stem}.{tag}.db")
        if backup_path.exists():
            return
        try:
            # checkpoint(), not commit(). Committing writes the transaction
            # INTO the write-ahead log; it does not move it into the database
            # file, so a plain copy taken straight afterwards can still be
            # missing everything since SQLite's last internal checkpoint.
            # This is the copy someone would reach for if a schema migration
            # went wrong, so it being quietly incomplete would be the worst
            # possible time to find that out. checkpoint() folds the log in
            # first. (backup.py hit the same trap and fixes it a stronger
            # way, with SQLite's backup API.)
            self.checkpoint()
            shutil.copy2(self.db_path, backup_path)
        except OSError as exc:
            print(f"Warning: could not create pre-migration backup at {backup_path}: {exc}")

    # ---------------------------------------------------------------- entries
    def get_entry(self, date: str) -> Optional[Entry]:
        row = self._conn.execute("SELECT * FROM entries WHERE date = ?", (date,)).fetchone()
        if row is None:
            return None
        return Entry(**dict(row))

    def upsert_entry(self, date: str, title: str = None, body_md: str = None,
                      body_format: str = None, body_text: str = None,
                      tag: str = None, tag_color: str = None, _clear_tag: bool = False) -> Entry:
        """body_format/body_text: see this module's docstring. Every call
        site that saves real editor content (RichEditor.save()) always
        passes both explicitly — body_format defaults to "html" here only
        as a safety net for the rare call that updates just the title/tag
        and never touches body_md at all, so it never accidentally
        overwrites an existing row's real format with a wrong guess (the
        `existing.body_format if body_format is None` branch below is what
        actually protects that case; this default only matters for a
        brand-new row)."""
        now = datetime.now().isoformat(timespec="seconds")
        existing = self.get_entry(date)
        if existing is None:
            entry = Entry(
                date=date,
                title=title or "",
                body_md=body_md or "",
                body_format=body_format or "html",
                body_text=body_text if body_text is not None else (body_md or ""),
                tag=None if _clear_tag else tag,
                tag_color=None if _clear_tag else tag_color,
                created_at=now,
                updated_at=now,
            )
            self._conn.execute(
                "INSERT INTO entries (date, title, body_md, body_format, body_text, tag, tag_color, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry.date, entry.title, entry.body_md, entry.body_format, entry.body_text,
                 entry.tag, entry.tag_color, entry.created_at, entry.updated_at),
            )
        else:
            entry = Entry(
                date=date,
                title=existing.title if title is None else title,
                body_md=existing.body_md if body_md is None else body_md,
                body_format=existing.body_format if body_format is None else body_format,
                body_text=existing.body_text if body_text is None else body_text,
                tag=(None if _clear_tag else (existing.tag if tag is None else tag)),
                tag_color=(None if _clear_tag else (existing.tag_color if tag_color is None else tag_color)),
                created_at=existing.created_at,
                updated_at=now,
            )
            self._conn.execute(
                "UPDATE entries SET title=?, body_md=?, body_format=?, body_text=?, tag=?, tag_color=?, "
                "updated_at=? WHERE date=?",
                (entry.title, entry.body_md, entry.body_format, entry.body_text, entry.tag,
                 entry.tag_color, entry.updated_at, entry.date),
            )
        self._conn.commit()
        return entry

    def clear_tag(self, date: str):
        self.upsert_entry(date, _clear_tag=True)

    def entries_in_range(self, start: str, end: str) -> dict:
        """Return {date: Entry} for entries with date in [start, end]."""
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE date >= ? AND date <= ?", (start, end)
        ).fetchall()
        return {r["date"]: Entry(**dict(r)) for r in rows}

    def all_dates_with_content(self) -> set:
        """Every date whose journal entry actually contains writing.

        Not "every date that has a row" — those are different questions, and
        conflating them is what made the calendar claim an entry existed on a
        day the user had only opened (Part 19). A row is created as soon as a
        date is saved, empty or not; an ENTRY exists only when the saved
        document has visible text in it.
        """
        rows = self._conn.execute(
            f"SELECT date FROM entries WHERE {MEANINGFUL_TEXT_SQL}").fetchall()
        return {r["date"] for r in rows}

    def dates_with_entries(self, start: str, end: str) -> set:
        """The same content-based question, for one month at a time.

        The calendar asks this rather than reading every entry and testing
        the text itself, so there is one definition of "this date has a
        journal entry" and it lives next to the data.
        """
        rows = self._conn.execute(
            f"SELECT date FROM entries WHERE date >= ? AND date <= ? "
            f"AND {MEANINGFUL_TEXT_SQL}", (start, end)).fetchall()
        return {r["date"] for r in rows}

    def other_content_dates(self, start: str, end: str) -> set:
        """Dates in [start, end] that hold something other than writing.

        Events, Reader's Notes, ToDos. The month grid marks these with a
        hollow dot — worth seeing, but not a journal entry. Both calendars
        ask this one question rather than each assembling the set from three
        queries of their own, which is how the two grids would eventually
        have come to disagree about what a hollow dot means.

        Journal entries are NOT subtracted here; that is the caller's
        business, since it already knows which dates have writing.
        """
        # Every date an event is visible on — including the later days of an
        # overnight or multi-day event and the occurrences of repeating ones.
        dates = self.event_dates(start, end)
        dates.update(self.reader_notes_dates(start, end))
        dates.update(self.todo_dates(start, end))
        return dates

    def day_metadata(self, start: str, end: str) -> dict:
        """{date: DayMeta} for [start, end], without loading document bodies
        into Python (Master Spec §7.4).

        What both month grids and the Yearly Calendar draw: whether the date
        has a journal entry (the one content rule, saving.document_has_content,
        evaluated in SQL), its title, its text length, its Day Marker colour,
        and whether something other than writing is on it. Only dates with
        something to show are returned.
        """
        meta: dict = {}
        rows = self._conn.execute(
            f"SELECT date, title, tag, tag_color, length(body_text) AS length, "
            f"({MEANINGFUL_TEXT_SQL}) AS has_entry "
            f"FROM entries WHERE date >= ? AND date <= ?", (start, end)).fetchall()
        for r in rows:
            has_entry = bool(r["has_entry"])
            color = r["tag_color"] if r["tag"] and r["tag_color"] else None
            if not has_entry and not color:
                continue
            meta[r["date"]] = DayMeta(date=r["date"], has_entry=has_entry,
                                      title=r["title"] or "", length=r["length"] or 0,
                                      tag_color=color)
        for date in self.other_content_dates(start, end):
            item = meta.setdefault(date, DayMeta(date=date))
            item.other_content = not item.has_entry
        return meta

    # ------------------------------------------------ month and year titles
    TITLE_MAX = 80

    def get_period_title(self, kind: str, period: str) -> str:
        """The title of a month ('month', 'YYYY-MM') or year ('year', 'YYYY'),
        or '' when it has none. '' is never stored."""
        row = self._conn.execute(
            "SELECT title FROM period_titles WHERE kind=? AND period=?", (kind, period)).fetchone()
        return row["title"] if row else ""

    def set_period_title(self, kind: str, period: str, title: str) -> str:
        """Stores one line of at most TITLE_MAX characters; an empty title
        removes the row. Returns what was stored."""
        if kind not in ("month", "year"):
            raise ValueError(f"unknown period kind {kind!r}")
        title = " ".join((title or "").split())[:self.TITLE_MAX].strip()
        if title:
            self._conn.execute(
                "INSERT INTO period_titles (kind, period, title, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, period) DO UPDATE SET title=excluded.title, "
                "updated_at=excluded.updated_at",
                (kind, period, title, datetime.now().isoformat(timespec="seconds")))
        else:
            self._conn.execute("DELETE FROM period_titles WHERE kind=? AND period=?",
                               (kind, period))
        self._conn.commit()
        return title

    def period_titles(self, kind: str, prefix: str = "") -> dict:
        """{period: title} for one kind, optionally only periods starting
        with `prefix` (e.g. every month title of 2026: ('month', '2026-'))."""
        rows = self._conn.execute(
            "SELECT period, title FROM period_titles WHERE kind=? AND period LIKE ?",
            (kind, prefix.replace("%", "") + "%")).fetchall()
        return {r["period"]: r["title"] for r in rows}

    def has_journal_entry(self, date: str) -> bool:
        """Whether one date counts as having a journal entry."""
        entry = self.get_entry(date)
        return entry is not None and document_has_content(entry.body_md, entry.body_text)

    def all_entries(self) -> list:
        rows = self._conn.execute("SELECT * FROM entries").fetchall()
        return [Entry(**dict(r)) for r in rows]

    def search_entries(self, query: str) -> list:
        # Matches against body_text (the derived plain-text view — see this
        # module's docstring), not just body_md: for an "html"-format row,
        # a phrase split across two formatted spans (e.g. "hello <b>world</b>")
        # is a substring of the plain text but NOT of the raw HTML, so
        # searching body_md alone would silently miss it. body_md is still
        # checked too, mainly so a "markdown" (pre-round-21) row — where
        # body_text was backfilled as an exact copy of body_md anyway —
        # keeps matching exactly as it always did, belt-and-suspenders.
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM entries WHERE title LIKE ? OR body_md LIKE ? OR body_text LIKE ? "
            "ORDER BY date DESC",
            (like, like, like),
        ).fetchall()
        return [Entry(**dict(r)) for r in rows]

    # ------------------------------------------------------------------ todos
    #
    # The Python API for the retired `tasks` table used to live here:
    # get_tasks, add_task, set_task_checked, update_task_text, delete_task,
    # copy_unfinished_tasks, tasks_in_range. Nothing had called any of it
    # since round 22 replaced Daily Tasks with the Day Calendar, and every
    # row it read had already been converted into a calendar event. The
    # TABLE and its migration stay (an old database restored from a backup
    # still needs both); the dead accessors are gone.
    #
    # What follows is a different feature with its own table — see the
    # `todos` CREATE above for why it is not the old one and not an event.
    def get_todos(self, date: str) -> list:
        rows = self._conn.execute(
            "SELECT * FROM todos WHERE date = ? ORDER BY position ASC, id ASC", (date,)
        ).fetchall()
        return [self._todo_from_row(r) for r in rows]

    @staticmethod
    def _todo_from_row(row) -> Todo:
        return Todo(id=row["id"], date=row["date"], text=row["text"],
                     done=bool(row["done"]), position=row["position"],
                     created_at=row["created_at"], updated_at=row["updated_at"])

    def add_todo(self, date: str, text: str) -> Todo:
        position = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM todos WHERE date = ?", (date,)
        ).fetchone()["p"]
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO todos (date, text, done, position, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (date, text, position, now, now),
        )
        self._conn.commit()
        return Todo(id=cur.lastrowid, date=date, text=text, position=position,
                     created_at=now, updated_at=now)

    def update_todo(self, todo_id: int, **fields) -> Optional[Todo]:
        """Partial update, same shape as update_event. `date` is updatable so
        a ToDo can be moved to another day like any other edit."""
        allowed = {"date", "text", "done", "position"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            values.append(int(value) if key == "done" else value)
        if not sets:
            return self.get_todo(todo_id)
        sets.append("updated_at=?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(todo_id)
        self._conn.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id=?", values)
        self._conn.commit()
        return self.get_todo(todo_id)

    def get_todo(self, todo_id: int) -> Optional[Todo]:
        row = self._conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return self._todo_from_row(row) if row is not None else None

    def delete_todo(self, todo_id: int):
        self._conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        self._conn.commit()

    def todo_dates(self, start: str, end: str) -> set:
        """Dates in [start, end] that have at least one ToDo — for the month
        grid's "something is on this day" mark, alongside events and notes."""
        rows = self._conn.execute(
            "SELECT DISTINCT date FROM todos WHERE date >= ? AND date <= ?",
            (start, end),
        ).fetchall()
        return {r["date"] for r in rows}

    def all_todos(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM todos ORDER BY date ASC, position ASC, id ASC").fetchall()
        return [self._todo_from_row(r) for r in rows]

    # --------------------------------------------------------- reader's notes
    #
    # Notes are keyed by (scope, ref): scope is "date" or "project", ref is
    # the ISO date or the project id. The date-shaped helpers below are thin
    # wrappers so existing journal call sites read naturally, but there is
    # only one storage path and one widget behind them.
    def get_notes(self, scope: str, ref: str) -> Optional[ReaderNotes]:
        row = self._conn.execute(
            "SELECT * FROM reader_notes_scoped WHERE scope = ? AND ref = ?",
            (scope, str(ref)),
        ).fetchone()
        if row is None:
            return None
        return ReaderNotes(date=row["ref"], content=row["content"],
                            content_format=row["content_format"],
                            content_text=row["content_text"],
                            created_at=row["created_at"], updated_at=row["updated_at"])

    def save_notes(self, scope: str, ref: str, content: str,
                    content_format: str = "html", content_text: str = "") -> ReaderNotes:
        now = datetime.now().isoformat(timespec="seconds")
        ref = str(ref)
        existing = self.get_notes(scope, ref)
        if existing is None:
            self._conn.execute(
                "INSERT INTO reader_notes_scoped (scope, ref, content, content_format, "
                "content_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scope, ref, content, content_format, content_text, now, now))
            created_at = now
        else:
            self._conn.execute(
                "UPDATE reader_notes_scoped SET content=?, content_format=?, "
                "content_text=?, updated_at=? WHERE scope=? AND ref=?",
                (content, content_format, content_text, now, scope, ref))
            created_at = existing.created_at
        self._conn.commit()
        return ReaderNotes(date=ref, content=content, content_format=content_format,
                            content_text=content_text, created_at=created_at,
                            updated_at=now)

    def get_reader_notes(self, date: str) -> Optional[ReaderNotes]:
        return self.get_notes("date", date)

    def save_reader_notes(self, date: str, content: str,
                           content_format: str = "html", content_text: str = "") -> ReaderNotes:
        return self.save_notes("date", date, content, content_format, content_text)

    def reader_notes_dates(self, start: Optional[str] = None, end: Optional[str] = None) -> set:
        """Dates whose Reader's Notes are written rather than blank — used by
        the calendar's hollow "something else is on this day" marker.

        Same rule as a journal entry (saving.document_has_content). It used to
        be SQLite's TRIM, which strips spaces only, so a note holding nothing
        but a newline — which merely visiting a date used to write — put a
        marker on a day the user had only looked at."""
        sql = ("SELECT ref FROM reader_notes_scoped "
               f"WHERE scope='date' AND {content_sql('content_text', 'content')}")
        params: tuple = ()
        if start is not None and end is not None:
            # Bounded to the dates being drawn, so a month or year grid does
            # not evaluate every note ever written (Group 3, §7).
            sql += " AND ref >= ? AND ref <= ?"
            params = (start, end)
        return {r["ref"] for r in self._conn.execute(sql, params).fetchall()}

    def all_reader_notes(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM reader_notes_scoped WHERE scope='date' ORDER BY ref ASC"
        ).fetchall()
        return [ReaderNotes(date=r["ref"], content=r["content"],
                             content_format=r["content_format"],
                             content_text=r["content_text"],
                             created_at=r["created_at"], updated_at=r["updated_at"])
                for r in rows]

    # ------------------------------------------------------- calendar events
    def _event_from_row(self, row) -> CalendarEvent:
        keys = row.keys()
        return CalendarEvent(
            id=row["id"], date=row["date"],
            start_minute=row["start_minute"], end_minute=row["end_minute"],
            title=row["title"], notes=row["notes"], color=row["color"],
            opacity=row["opacity"] if "opacity" in keys else 100,
            all_day=bool(row["all_day"]), done=bool(row["done"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
            end_date=row["end_date"] if "end_date" in keys else None,
            series_id=row["series_id"] if "series_id" in keys else None,
            occurrence_date=row["occurrence_date"] if "occurrence_date" in keys else None,
        )

    def get_events(self, date: str) -> list:
        """Rows STORED on `date` (their start date). The calendar views use
        occurrences_between() instead, which also finds events that started
        earlier and run into the day, and the occurrences of repeating ones."""
        rows = self._conn.execute(
            "SELECT * FROM calendar_events WHERE date = ? "
            "ORDER BY all_day DESC, start_minute ASC, end_minute ASC, id ASC",
            (date,),
        ).fetchall()
        return [self._event_from_row(r) for r in rows]

    def get_event(self, event_id: int) -> Optional[CalendarEvent]:
        row = self._conn.execute(
            "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._event_from_row(row) if row is not None else None

    def create_event(self, date: str, start_minute: int, end_minute: int,
                      title: str = "", notes: str = "", color: Optional[str] = None,
                      all_day: bool = False, done: bool = False,
                      opacity: int = OPACITY_FOLLOWS_DEFAULT,
                      end_date: Optional[str] = None,
                      recurrence=None) -> CalendarEvent:
        """Inserts one event. `end_date` None = ends on `date`.

        Refuses (ValueError) a span that ends before it starts — the rule
        that makes "9 PM to 12 AM" impossible to store backwards; the caller
        must say which day the end is on. `recurrence` (a
        recurrence.RecurrenceRule) makes it the first date of a series.
        """
        end_date = None if end_date in (None, "", date) else end_date
        validate_span(date, start_minute, end_date, end_minute, all_day)
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO calendar_events (date, start_minute, end_minute, title, notes, "
            "color, opacity, all_day, done, created_at, updated_at, end_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date, start_minute, end_minute, title, notes, color, opacity,
             1 if all_day else 0, 1 if done else 0, now, now, end_date),
        )
        event_id = cur.lastrowid
        if recurrence is not None:
            self._write_rule(event_id, recurrence)
        self._conn.commit()
        return self.get_event(event_id)

    _EVENT_FIELDS = {"date", "start_minute", "end_minute", "title", "notes",
                     "color", "opacity", "all_day", "done", "end_date"}

    def update_event(self, event_id: int, **fields) -> Optional[CalendarEvent]:
        """Partial update: only the named columns change. `date` is updatable
        like any other field — moving an event to another day is an ordinary
        edit, and the day view simply stops showing it once it no longer
        matches the selected date.

        The resulting span is checked before anything is written (see
        create_event). Updating a series' own row changes the whole series.
        """
        updates = {k: v for k, v in fields.items() if k in self._EVENT_FIELDS}
        if not updates:
            return self.get_event(event_id)
        current = self.get_event(event_id)
        if current is None:
            return None
        merged = {f: getattr(current, f) for f in self._EVENT_FIELDS}
        merged.update(updates)
        if merged["end_date"] in ("", merged["date"]):
            merged["end_date"] = None
            if "end_date" in updates or "date" in updates:
                updates["end_date"] = None
        validate_span(merged["date"], merged["start_minute"], merged["end_date"],
                      merged["end_minute"], bool(merged["all_day"]))
        self._write_event_fields(event_id, updates)
        self._conn.commit()
        return self.get_event(event_id)

    def _write_event_fields(self, event_id: int, updates: dict):
        updates = dict(updates)
        for bool_field in ("all_day", "done"):
            if bool_field in updates:
                updates[bool_field] = 1 if updates[bool_field] else 0
        updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
        assignments = ", ".join(f"{k}=?" for k in updates)
        self._conn.execute(
            f"UPDATE calendar_events SET {assignments} WHERE id=?",
            (*updates.values(), event_id),
        )

    def delete_event(self, event_id: int):
        """Deletes one event row.

        A series' own row takes the whole series with it (rule, cancelled
        dates and separately edited occurrences), so nothing is orphaned.
        Deleting a separately edited occurrence's row cancels that one
        occurrence: its series date stays in event_exceptions, so the rule
        does not bring it back.
        """
        event = self.get_event(event_id)
        if event is None:
            return
        if self.get_recurrence(event_id) is not None:
            self.delete_series(event_id)
            return
        self._conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        if event.series_id is not None:
            self._drop_if_empty(event.series_id)
        self._conn.commit()

    def events_in_range(self, start: str, end: str) -> dict:
        """{date: [CalendarEvent, ...]} of rows STORED with a start date in
        [start, end], in one query. Kept for callers that want rows rather
        than occurrences; see occurrences_between()."""
        rows = self._conn.execute(
            "SELECT * FROM calendar_events WHERE date >= ? AND date <= ? "
            "ORDER BY date ASC, all_day DESC, start_minute ASC, id ASC",
            (start, end),
        ).fetchall()
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(r["date"], []).append(self._event_from_row(r))
        return grouped

    def all_events(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM calendar_events ORDER BY date ASC, all_day DESC, start_minute ASC, id ASC"
        ).fetchall()
        return [self._event_from_row(r) for r in rows]

    # ------------------------------------------------ occurrences (Group 3)
    def occurrences_between(self, start: str, end: str) -> list:
        """Every occurrence visible on any date in [start, end], sorted.

        Ordinary events (including separately edited occurrences of a
        series) that overlap the range, plus the occurrences each series'
        rule produces there. Bounded: rows are found through the `date` and
        `end_date` indexes, and a series is only expanded across the range
        asked for (recurrence.occurrence_dates).
        """
        rows = self._conn.execute(
            "SELECT e.* FROM calendar_events e "
            "WHERE NOT EXISTS (SELECT 1 FROM event_recurrence r WHERE r.event_id = e.id) "
            "AND ((e.date >= ? AND e.date <= ?) "
            "     OR (e.end_date IS NOT NULL AND e.end_date >= ? AND e.date < ?))",
            (start, end, start, start),
        ).fetchall()
        found = []
        for row in rows:
            event = self._event_from_row(row)
            occ = Occurrence(event=event, date=event.date, end_date=end_date_of(event),
                             series_id=event.series_id,
                             occurrence_date=event.occurrence_date,
                             rule=None)
            if occ.touches(start, end):
                found.append(occ)
        for master, rule in self._series_starting_by(end):
            if rule.until and add_days(rule.until, span_days(master)) < start:
                continue
            found.extend(occurrences_for_series(
                master, rule, start, end, self._exception_dates(master.id)))
        for occ in found:
            if occ.series_id is not None and occ.rule is None:
                occ.rule = self.get_recurrence(occ.series_id)
        found.sort(key=sort_key)
        return found

    def occurrence_by_key(self, key):
        """The occurrence an Occurrence.key names: an ordinary event's id,
        or (series id, original date) for an occurrence of a series — its
        own row if it was edited on its own, otherwise the series' values on
        that date. None if it no longer exists."""
        if isinstance(key, tuple):
            series_id, occurrence_date = key
            rule = self.get_recurrence(series_id)
            own = self._override_row(series_id, occurrence_date)
            if own is not None:
                return Occurrence(event=own, date=own.date, end_date=end_date_of(own),
                                  series_id=series_id, occurrence_date=occurrence_date, rule=rule)
            master = self.get_event(series_id)
            if master is None or rule is None or \
                    occurrence_date in self._exception_dates(series_id):
                return None
            return Occurrence(event=master, date=occurrence_date,
                              end_date=add_days(occurrence_date, span_days(master)),
                              series_id=series_id, occurrence_date=occurrence_date, rule=rule)
        event = self.get_event(key)
        if event is None:
            return None
        if event.series_id is not None and event.occurrence_date:
            return self.occurrence_by_key((event.series_id, event.occurrence_date))
        rule = self.get_recurrence(event.id)
        if rule is not None:
            first = self.first_occurrence_date(event.id) or event.date
            return self.occurrence_by_key((event.id, first))
        return Occurrence(event=event, date=event.date, end_date=end_date_of(event))

    def event_dates(self, start: str, end: str) -> set:
        """Dates in [start, end] on which any occurrence is visible."""
        dates = set()
        for occ in self.occurrences_between(start, end):
            dates.update(d for d in occ.days() if start <= d <= end)
        return dates

    def _series_starting_by(self, end: str) -> list:
        rows = self._conn.execute(
            "SELECT e.*, r.freq AS r_freq, r.interval AS r_interval, "
            "r.weekdays AS r_weekdays, r.until_date AS r_until, r.week_start AS r_week_start "
            "FROM calendar_events e JOIN event_recurrence r ON r.event_id = e.id "
            "WHERE e.date <= ?", (end,)).fetchall()
        return [(self._event_from_row(r), self._rule_from_values(
            r["r_freq"], r["r_interval"], r["r_weekdays"], r["r_until"], r["r_week_start"]))
            for r in rows]

    @staticmethod
    def _rule_from_values(freq, interval, weekdays, until, week_start=0) -> RecurrenceRule:
        return RecurrenceRule(freq=freq, interval=int(interval or 1),
                              weekdays=RecurrenceRule.weekdays_from_text(weekdays),
                              until=until or None, week_start=int(week_start or 0))

    def _exception_dates(self, series_id: int) -> set:
        return {r["occurrence_date"] for r in self._conn.execute(
            "SELECT occurrence_date FROM event_exceptions WHERE series_id=?",
            (series_id,)).fetchall()}

    def get_recurrence(self, event_id: int) -> Optional[RecurrenceRule]:
        row = self._conn.execute(
            "SELECT * FROM event_recurrence WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        return self._rule_from_values(row["freq"], row["interval"], row["weekdays"],
                                      row["until_date"], row["week_start"])

    def _write_rule(self, event_id: int, rule: RecurrenceRule):
        """Stores the rule, keeping the series' place in its family
        (series_root), which the rule itself doesn't carry."""
        event = self.get_event(event_id)
        if event is not None and rule.until and rule.until < event.date:
            raise ValueError("a series cannot end before its first date")
        values = (rule.freq, int(rule.interval), rule.weekdays_text(), rule.until,
                  int(rule.week_start), event_id)
        if self._conn.execute("SELECT 1 FROM event_recurrence WHERE event_id=?",
                              (event_id,)).fetchone():
            self._conn.execute(
                "UPDATE event_recurrence SET freq=?, interval=?, weekdays=?, until_date=?, "
                "week_start=? WHERE event_id=?", values)
        else:
            self._conn.execute(
                "INSERT INTO event_recurrence (freq, interval, weekdays, until_date, week_start, "
                "event_id) VALUES (?, ?, ?, ?, ?, ?)", values)

    def set_recurrence(self, event_id: int, rule: Optional[RecurrenceRule]):
        """Makes an ordinary event repeat, or with None stops a series
        repeating: the series' own row stays as a single event, and its
        cancelled dates and separately edited occurrences are removed.
        Changing the rule of an existing series goes through
        change_series_rule, which keeps its occurrences attached."""
        event = self.get_event(event_id)
        if event is None:
            return
        if event.series_id is not None:
            raise ValueError("an edited occurrence cannot have its own rule")
        if rule is None:
            self._conn.execute("DELETE FROM calendar_events WHERE series_id=?", (event_id,))
            self._conn.execute("DELETE FROM event_exceptions WHERE series_id=?", (event_id,))
            self._conn.execute("DELETE FROM event_recurrence WHERE event_id=?", (event_id,))
        else:
            self._write_rule(event_id, rule)
        self._conn.commit()

    def _override_row(self, series_id: int, occurrence_date: str) -> Optional[CalendarEvent]:
        row = self._conn.execute(
            "SELECT * FROM calendar_events WHERE series_id=? AND occurrence_date=?",
            (series_id, occurrence_date)).fetchone()
        return self._event_from_row(row) if row is not None else None

    # ------------------------------------------------- series families
    #
    # "This and following" ends a series and continues it as a new one
    # (_split_series). The parts stay one series for the user (GF-3): each
    # later part's rule row names the first part in `series_root`, so
    # "entire series" reaches every part and "this and following" reaches
    # the later parts too. Event ids are AUTOINCREMENT, so a root id stays a
    # safe grouping key even after that part itself has been deleted.
    def _series_root(self, series_id: int) -> int:
        row = self._conn.execute("SELECT series_root FROM event_recurrence WHERE event_id=?",
                                 (series_id,)).fetchone()
        return (row["series_root"] if row is not None and row["series_root"] else series_id)

    def series_family(self, series_id: int) -> list:
        """The ids of every part of the series `series_id` belongs to, in
        date order (just [series_id] for a series never split)."""
        root = self._series_root(series_id)
        rows = self._conn.execute(
            "SELECT e.id FROM calendar_events e JOIN event_recurrence r ON r.event_id = e.id "
            "WHERE COALESCE(r.series_root, r.event_id) = ? ORDER BY e.date, e.id", (root,)).fetchall()
        ids = [r["id"] for r in rows]
        return ids if series_id in ids else [series_id]

    def _parts_from(self, series_id: int) -> list:
        """`series_id` and the parts after it."""
        family = self.series_family(series_id)
        return family[family.index(series_id):]

    def _delete_part(self, series_id: int):
        """One part's rule, cancelled dates, edited occurrences and row
        (no commit)."""
        self._conn.execute("DELETE FROM calendar_events WHERE series_id=?", (series_id,))
        self._conn.execute("DELETE FROM event_exceptions WHERE series_id=?", (series_id,))
        self._conn.execute("DELETE FROM event_recurrence WHERE event_id=?", (series_id,))
        self._conn.execute("DELETE FROM calendar_events WHERE id=?", (series_id,))

    def _series_is_empty(self, series_id: int) -> bool:
        """True when a series shows nothing any more: no edited occurrence
        and, for a series with an end, no date the rule still produces."""
        master = self.get_event(series_id)
        rule = self.get_recurrence(series_id)
        if master is None or rule is None:
            return False
        if self._conn.execute("SELECT 1 FROM calendar_events WHERE series_id=? LIMIT 1",
                              (series_id,)).fetchone():
            return False
        if rule.until is None:
            return False
        return not occurrence_dates(master.date, rule, master.date, rule.until,
                                    self._exception_dates(series_id))

    def _drop_if_empty(self, series_id: int):
        """A series left with no occurrences is removed entirely (A7), so no
        invisible row, rule or cancelled date is left behind."""
        if self._series_is_empty(series_id):
            self._delete_part(series_id)

    def first_real_occurrence(self, series_id: int) -> Optional[str]:
        """The first date the series really shows (cancelled dates skipped;
        an edited occurrence counts at its own date). None if there is none."""
        master = self.get_event(series_id)
        rule = self.get_recurrence(series_id)
        if master is None or rule is None:
            return None
        own = self._conn.execute("SELECT MIN(date) AS d FROM calendar_events WHERE series_id=?",
                                 (series_id,)).fetchone()["d"]
        found = next_occurrence(master.date, rule, master.date, self._exception_dates(series_id))
        candidates = [d for d in (found, own) if d]
        return min(candidates) if candidates else None

    # The three scopes of Master Spec §22.1. `fields` are event columns for
    # the edited occurrence's new values (date, end_date, minutes, title, …).
    def edit_occurrence(self, series_id: int, occurrence_date: str, **fields) -> CalendarEvent:
        """"This occurrence only": the occurrence gets (or updates) its own
        row; the series is unchanged."""
        existing = self._override_row(series_id, occurrence_date)
        if existing is not None:
            return self.update_event(existing.id, **fields)
        master = self.get_event(series_id)
        if master is None or self.get_recurrence(series_id) is None:
            raise ValueError("not a repeating event")
        values = {f: getattr(master, f) for f in self._EVENT_FIELDS}
        values["date"] = occurrence_date
        values["end_date"] = add_days(occurrence_date, span_days(master)) if span_days(master) else None
        values.update({k: v for k, v in fields.items() if k in self._EVENT_FIELDS})
        if values["end_date"] in ("", values["date"]):
            values["end_date"] = None
        validate_span(values["date"], values["start_minute"], values["end_date"],
                      values["end_minute"], bool(values["all_day"]))
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO calendar_events (date, start_minute, end_minute, title, notes, color, "
            "opacity, all_day, done, created_at, updated_at, end_date, series_id, occurrence_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (values["date"], values["start_minute"], values["end_minute"], values["title"],
             values["notes"], values["color"], values["opacity"],
             1 if values["all_day"] else 0, 1 if values["done"] else 0, now, now,
             values["end_date"], series_id, occurrence_date))
        self._conn.execute(
            "INSERT OR IGNORE INTO event_exceptions (series_id, occurrence_date) VALUES (?, ?)",
            (series_id, occurrence_date))
        self._conn.commit()
        return self.get_event(cur.lastrowid)

    def delete_occurrence(self, series_id: int, occurrence_date: str):
        """"This occurrence only" delete: the series skips that date. A
        series left with nothing to show is removed."""
        self._conn.execute(
            "DELETE FROM calendar_events WHERE series_id=? AND occurrence_date=?",
            (series_id, occurrence_date))
        self._conn.execute(
            "INSERT OR IGNORE INTO event_exceptions (series_id, occurrence_date) VALUES (?, ?)",
            (series_id, occurrence_date))
        self._drop_if_empty(series_id)
        self._conn.commit()

    def _new_values(self, event: CalendarEvent, shift_days: int, span: Optional[int],
                    start_delta: Optional[int], end_delta: Optional[int], fields: dict,
                    start_days: Optional[int] = None, end_days: Optional[int] = None) -> dict:
        """The columns that change when a series-wide edit reaches `event`
        (the series' own row or one separately edited occurrence).

        Two kinds of change (GF-2):
          * editor changes — `fields` hold exactly the values the user
            changed and are applied as typed; `shift_days` moves the date
            and `span` (if given) sets how many days after its start each
            occurrence ends;
          * drags — `start_delta` / `end_delta` (minutes, days included)
            move each occurrence's own start / end by the same amount, so a
            9:00 series with one occurrence at 12:00 dragged 30 minutes
            later becomes 9:30 and 12:30.
        """
        current = {f: getattr(event, f) for f in self._EVENT_FIELDS}
        new = dict(current)
        new.update(fields)
        if start_delta is not None or end_delta is not None:
            start_delta, end_delta = start_delta or 0, end_delta or 0
            if new["all_day"]:
                # By days: the calendar days the dragged occurrence's start
                # and end moved by (so an untimed occurrence in a timed
                # series follows a drag onto the next day, however few hours
                # it moved), or whole days of the change when not given.
                first = add_days(event.date, _whole_days(start_delta)
                                 if start_days is None else start_days)
                last = add_days(end_date_of(event), _whole_days(end_delta)
                                if end_days is None else end_days)
                new["date"], new["end_date"] = first, (last if last > first else None)
            else:
                start = event.start_minute + start_delta
                end = absolute_end(event) + end_delta
                if end - start < 15:          # never reversed, never shorter than a step
                    end = start + 15
                carry = start // MINUTES_PER_DAY
                new["date"] = add_days(event.date, carry)
                new["start_minute"] = start - carry * MINUTES_PER_DAY
                new["end_date"], new["end_minute"] = span_from(
                    new["date"], new["start_minute"], end - start)
        elif shift_days or span is not None:
            new["date"] = add_days(event.date, shift_days)
            own_span = span_days(event) if span is None else int(span)
            new["end_date"] = add_days(new["date"], own_span) if own_span else None
        if new["end_date"] in ("", new["date"]):
            new["end_date"] = None
        try:
            validate_span(new["date"], new["start_minute"], new["end_date"],
                          new["end_minute"], bool(new["all_day"]))
        except ValueError:
            # A change that can't apply as-is to this occurrence (a
            # separately edited one at another time of day, or of another
            # length) leaves it consistent instead of refusing the whole
            # edit (Group 3 fixes, A3):
            #   * typed start AND end: the typed range, running past midnight
            #     when the end is not after the start (as the editor reads it);
            #   * a typed end it would start after: not applied to it;
            #   * otherwise (a typed start, a new date): it keeps its length.
            if new["all_day"]:
                raise
            typed_start, typed_end = "start_minute" in fields, "end_minute" in fields
            if typed_start and typed_end:
                length = (new["end_minute"] - new["start_minute"]) % MINUTES_PER_DAY or MINUTES_PER_DAY
                new["end_date"], new["end_minute"] = span_from(new["date"], new["start_minute"], length)
            else:
                if typed_end:
                    new["end_minute"] = event.end_minute
                    new["end_date"] = (add_days(new["date"], span_days(event))
                                       if span_days(event) else None)
                try:
                    validate_span(new["date"], new["start_minute"], new["end_date"],
                                  new["end_minute"], bool(new["all_day"]))
                except ValueError:
                    new["end_date"], new["end_minute"] = span_from(
                        new["date"], new["start_minute"], max(15, duration_minutes(event)))
        return {k: v for k, v in new.items() if current.get(k) != v}

    def _edit_part(self, series_id: int, shift_days: int, span: Optional[int],
                   start_delta: Optional[int], end_delta: Optional[int], fields: dict,
                   start_days: Optional[int] = None, end_days: Optional[int] = None):
        """Applies one series-wide change to one part: its own row, every
        separately edited occurrence, and — if its start date moved — its
        rule, cancelled dates and occurrence identities (no commit)."""
        master = self.get_event(series_id)
        rule = self.get_recurrence(series_id)
        if master is None or rule is None:
            raise ValueError("not a repeating event")
        changes = self._new_values(master, shift_days, span, start_delta, end_delta, fields,
                                   start_days, end_days)
        moved = days_between(master.date, changes.get("date", master.date))
        if changes:
            self._write_event_fields(series_id, changes)
        for override in self._overrides(series_id):
            values = self._new_values(override, shift_days, span, start_delta, end_delta, fields,
                                      start_days, end_days)
            if values:
                self._write_event_fields(override.id, values)
        if moved:
            new_rule = shift_rule(rule, moved)
            self._remap_series(series_id, master.date, rule, add_days(master.date, moved),
                               new_rule, moved)
            self._write_rule(series_id, new_rule)

    def _edit_parts(self, parts: list, shift_days: int, span: Optional[int],
                    start_delta: Optional[int], end_delta: Optional[int], fields: dict,
                    start_days: Optional[int] = None, end_days: Optional[int] = None):
        fields = {k: v for k, v in fields.items() if k in self._EVENT_FIELDS - {"date", "end_date"}}
        try:
            for part in parts:
                self._edit_part(part, shift_days, span, start_delta, end_delta, fields,
                                start_days, end_days)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def edit_series(self, series_id: int, shift_days: int = 0, span: Optional[int] = None,
                    start_delta: Optional[int] = None, end_delta: Optional[int] = None,
                    start_days: Optional[int] = None, end_days: Optional[int] = None,
                    **fields) -> CalendarEvent:
        """"Entire series": every part of the series (GF-3).

        `fields` are the values the user changed, applied to every
        occurrence including separately edited ones (so pass ONLY changed
        fields). `shift_days` moves every occurrence by that many days (its
        rule, cancelled dates and edited occurrences move with it —
        recurrence.remap_dates); `span` sets how many days after its start
        each occurrence ends. `start_delta` / `end_delta` are a drag's
        change in minutes, applied to each occurrence's own times.
        """
        if self.get_event(series_id) is None or self.get_recurrence(series_id) is None:
            raise ValueError("not a repeating event")
        self._edit_parts(self.series_family(series_id), shift_days, span,
                         start_delta, end_delta, fields, start_days, end_days)
        return self.get_event(series_id)

    def _overrides(self, series_id: int) -> list:
        return [self._event_from_row(r) for r in self._conn.execute(
            "SELECT * FROM calendar_events WHERE series_id=?", (series_id,)).fetchall()]

    def first_occurrence_date(self, series_id: int) -> Optional[str]:
        """The first date the rule produces (cancelled or not) — which need
        not be the series' own start date, e.g. a Mon/Wed series starting on
        a Tuesday."""
        master = self.get_event(series_id)
        rule = self.get_recurrence(series_id)
        if master is None or rule is None:
            return None
        first = nth_occurrences(master.date, rule, 1)
        return first[0] if first else None

    def _apply_identity_map(self, series_id: int, mapping: dict, move_dates: bool):
        """Re-points cancelled dates and edited occurrences of one series
        to their new occurrence dates. None = that occurrence no longer
        exists: a cancelled date is dropped; an edited occurrence is kept as
        an ordinary event rather than deleted. With `move_dates`, an edited
        occurrence's own date moves by as many days as its identity."""
        exceptions = self._exception_dates(series_id)
        overrides = self._conn.execute(
            "SELECT id, date, end_date, occurrence_date FROM calendar_events WHERE series_id=?",
            (series_id,)).fetchall()
        self._conn.execute("DELETE FROM event_exceptions WHERE series_id=?", (series_id,))
        for old in exceptions:
            if mapping.get(old):
                self._conn.execute(
                    "INSERT OR IGNORE INTO event_exceptions (series_id, occurrence_date) VALUES (?, ?)",
                    (series_id, mapping[old]))
        for r in overrides:
            new = mapping.get(r["occurrence_date"])
            if new:
                self._conn.execute("UPDATE calendar_events SET occurrence_date=? WHERE id=?",
                                   (new, r["id"]))
                moved = days_between(r["occurrence_date"], new)
                if move_dates and moved:
                    self._conn.execute(
                        "UPDATE calendar_events SET date=?, end_date=? WHERE id=?",
                        (add_days(r["date"], moved),
                         add_days(r["end_date"], moved) if r["end_date"] else None, r["id"]))
                self._conn.execute(
                    "INSERT OR IGNORE INTO event_exceptions (series_id, occurrence_date) VALUES (?, ?)",
                    (series_id, new))
            else:
                self._conn.execute(
                    "UPDATE calendar_events SET series_id=NULL, occurrence_date=NULL WHERE id=?",
                    (r["id"],))

    def _remap_series(self, series_id: int, old_first: str, old_rule: RecurrenceRule,
                      new_first: str, new_rule: RecurrenceRule, shift: int):
        """Re-points cancelled dates and edited occurrences after a move
        (their own dates were already moved by the edit)."""
        dates = self._exception_dates(series_id) | {r["occurrence_date"] for r in self._conn.execute(
            "SELECT occurrence_date FROM calendar_events WHERE series_id=?", (series_id,)).fetchall()}
        mapping = remap_dates(old_first, old_rule, new_first, new_rule, dates, shift)
        self._apply_identity_map(series_id, mapping, move_dates=False)

    def _split_series(self, series_id: int, occurrence_date: str) -> int:
        """Ends the series the day before `occurrence_date` and continues it
        as a new series starting there, with the later cancelled dates and
        edited occurrences moved across. The new part joins the series'
        family (series_root). Returns the new series' id."""
        master = self.get_event(series_id)
        rule = self.get_recurrence(series_id)
        now = datetime.now().isoformat(timespec="seconds")
        span = span_days(master)
        cur = self._conn.execute(
            "INSERT INTO calendar_events (date, start_minute, end_minute, title, notes, color, "
            "opacity, all_day, done, created_at, updated_at, end_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (occurrence_date, master.start_minute, master.end_minute, master.title,
             master.notes, master.color, master.opacity, 1 if master.all_day else 0,
             1 if master.done else 0, now, now,
             add_days(occurrence_date, span) if span else None))
        new_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO event_recurrence (event_id, freq, interval, weekdays, until_date, "
            "week_start, series_root) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, rule.freq, rule.interval, rule.weekdays_text(), rule.until, rule.week_start,
             self._series_root(series_id)))
        self._conn.execute("UPDATE event_recurrence SET until_date=? WHERE event_id=?",
                           (add_days(occurrence_date, -1), series_id))
        self._conn.execute(
            "UPDATE event_exceptions SET series_id=? WHERE series_id=? AND occurrence_date>=?",
            (new_id, series_id, occurrence_date))
        self._conn.execute(
            "UPDATE calendar_events SET series_id=? WHERE series_id=? AND occurrence_date>=?",
            (new_id, series_id, occurrence_date))
        return new_id

    def _parts_following(self, series_id: int, occurrence_date: str) -> list:
        """The parts "this and following" from `occurrence_date` covers:
        the series from that occurrence on (split off unless it is the
        series' first occurrence) and every later part (no commit)."""
        later = self._parts_from(series_id)[1:]
        master = self.get_event(series_id)
        if occurrence_date <= (self.first_occurrence_date(series_id) or master.date):
            return [series_id] + later
        new_part = self._split_series(series_id, occurrence_date)
        # Everything before the split may have been cancelled already: the
        # earlier part then shows nothing and is removed (A7).
        self._drop_if_empty(series_id)
        return [new_part] + later

    def edit_following(self, series_id: int, occurrence_date: str, shift_days: int = 0,
                       span: Optional[int] = None, start_delta: Optional[int] = None,
                       end_delta: Optional[int] = None, start_days: Optional[int] = None,
                       end_days: Optional[int] = None, **fields) -> CalendarEvent:
        """"This and following": the series is split at this occurrence and
        the edit applies to the new part and every later part. From the
        series' first occurrence it covers that whole part. Returns the
        part that starts at this occurrence."""
        master = self.get_event(series_id)
        if master is None or self.get_recurrence(series_id) is None:
            raise ValueError("not a repeating event")
        try:
            parts = self._parts_following(series_id, occurrence_date)
        except Exception:
            self._conn.rollback()
            raise
        self._edit_parts(parts, shift_days, span, start_delta, end_delta, fields,
                         start_days, end_days)
        return self.get_event(parts[0])

    _PART_VALUES = ("title", "notes", "color", "opacity", "done", "all_day",
                    "start_minute", "end_minute")

    def _same_part_values(self, a: CalendarEvent, b: CalendarEvent) -> bool:
        """Two parts of a series show the same event (only their dates and
        rules differ), so they can become one series without losing
        anything."""
        return all(getattr(a, f) == getattr(b, f) for f in self._PART_VALUES) and \
            span_days(a) == span_days(b)

    def series_end(self, series_id: int) -> Optional[str]:
        """The last date the whole series may repeat on: the end of its
        last part (None = no end). What the editor shows as "Ends on"."""
        rule = self.get_recurrence(self.series_family(series_id)[-1])
        return rule.until if rule is not None else None

    def _detach_part(self, series_id: int):
        """Removes a part that no longer has any date, keeping its
        separately edited occurrences as ordinary events (no commit)."""
        self._conn.execute("UPDATE calendar_events SET series_id=NULL, occurrence_date=NULL "
                           "WHERE series_id=?", (series_id,))
        self._delete_part(series_id)

    def change_series_rule(self, series_id: int, rule: Optional[RecurrenceRule],
                           keep_until: bool = False) -> Optional[CalendarEvent]:
        """A new rule for `series_id` and every later part of its series
        (GF-3). Call it on the first part for "entire series", or on the part
        edit_following returned for "this and following".

        The parts become one series again: consecutive parts that show the
        same event (same title, notes, times, colour …) are joined into one
        series row. A part that "this and following" gave different values
        (a new title, say) stays its own row so nothing it holds is lost,
        but it follows the same rule counted from the first part — one
        cadence ("every 2 weeks", "monthly on the 31st" run on across it)
        and one end: `rule.until` ends the whole series, and parts after it
        are removed (their edited occurrences kept as ordinary events).
        `keep_until` keeps the series' own end (series_end) — the user
        didn't change "Ends on".

        Cancelled dates and edited occurrences stay attached
        (recurrence.remap_for_rule): a date the new rule still produces
        stays, otherwise it moves to the new rule's only occurrence in the
        same week / month / year; with none, a cancelled date is dropped and
        an edited occurrence becomes an ordinary event. None stops the
        series repeating: the first part stays as a single event and later
        parts are removed.
        """
        parts = self._parts_from(series_id)
        target = parts[0]
        try:
            if rule is None:
                for part in parts[1:]:
                    self._delete_part(part)
                self._conn.commit()
                self.set_recurrence(target, None)
                return self.get_event(target)
            end = self.get_recurrence(parts[-1]).until if keep_until else rule.until
            anchor = self.get_event(target).date
            counting = RecurrenceRule(rule.freq, rule.interval, rule.weekdays, None, rule.week_start)
            # 1. Each part's range under the series' end; join equal neighbours.
            groups: list = []                 # [head id, last date or None]
            for index, part in enumerate(parts):
                master = self.get_event(part)
                own_until = None if index == len(parts) - 1 else self.get_recurrence(part).until
                until = own_until if (end is None or (own_until is not None and own_until < end)) else end
                if index and end is not None and master.date > end:
                    self._detach_part(part)
                    continue
                if groups and self._same_part_values(self.get_event(groups[-1][0]), master):
                    head = groups[-1][0]
                    # Two edited occurrences must never share one identity
                    # (a click on one would open the other, deleting one
                    # would delete both): one that clashes with the head's
                    # own is kept as an ordinary event.
                    self._conn.execute(
                        "UPDATE calendar_events SET series_id=NULL, occurrence_date=NULL "
                        "WHERE series_id=? AND occurrence_date IN "
                        "(SELECT occurrence_date FROM calendar_events WHERE series_id=?)",
                        (part, head))
                    self._conn.execute(
                        "INSERT OR IGNORE INTO event_exceptions (series_id, occurrence_date) "
                        "SELECT ?, occurrence_date FROM event_exceptions WHERE series_id=?",
                        (head, part))
                    self._conn.execute("DELETE FROM event_exceptions WHERE series_id=?", (part,))
                    self._conn.execute("UPDATE calendar_events SET series_id=? WHERE series_id=?",
                                       (head, part))
                    self._conn.execute("DELETE FROM event_recurrence WHERE event_id=?", (part,))
                    self._conn.execute("DELETE FROM calendar_events WHERE id=?", (part,))
                    groups[-1][1] = until
                    continue
                groups.append([part, until])
            # 2. The new rule on every remaining part, one cadence from the anchor.
            for index, (part, until) in enumerate(groups):
                master = self.get_event(part)
                start = master.date
                if index:
                    start = next_occurrence(anchor, counting, master.date)
                    if start is None or (until is not None and start > until):
                        self._detach_part(part)
                        continue
                    if start != master.date:
                        span = span_days(master)
                        self._write_event_fields(part, {
                            "date": start, "end_date": add_days(start, span) if span else None})
                part_rule = RecurrenceRule(rule.freq, rule.interval, rule.weekdays, until,
                                           rule.week_start)
                self._write_rule(part, part_rule)
                dates = self._exception_dates(part) | {r["occurrence_date"] for r in self._conn.execute(
                    "SELECT occurrence_date FROM calendar_events WHERE series_id=?", (part,)).fetchall()}
                self._apply_identity_map(part, remap_for_rule(start, part_rule, dates),
                                         move_dates=True)
                self._drop_if_empty(part)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return self.get_event(target)

    def delete_following(self, series_id: int, occurrence_date: str):
        """"This and following" delete: the series ends the day before, and
        every later part of it is deleted (GF-3). From the first occurrence
        the whole part goes."""
        master = self.get_event(series_id)
        if master is None:
            return
        later = self._parts_from(series_id)[1:]
        if occurrence_date <= (self.first_occurrence_date(series_id) or master.date):
            self._delete_part(series_id)
        else:
            self._conn.execute("UPDATE event_recurrence SET until_date=? WHERE event_id=?",
                               (add_days(occurrence_date, -1), series_id))
            self._conn.execute(
                "DELETE FROM calendar_events WHERE series_id=? AND occurrence_date>=?",
                (series_id, occurrence_date))
            self._conn.execute(
                "DELETE FROM event_exceptions WHERE series_id=? AND occurrence_date>=?",
                (series_id, occurrence_date))
            self._drop_if_empty(series_id)
        for part in later:
            self._delete_part(part)
        self._conn.commit()

    def delete_series(self, series_id: int):
        """"Entire series" delete: every part of the series (GF-3), each
        with its rule, cancelled dates and separately edited occurrences."""
        for part in self.series_family(series_id):
            self._delete_part(part)
        self._conn.commit()

    # -------------------------------------------------------------- projects
    def create_project(self, title: str, category: Optional[str] = None,
                       folder_id: Optional[int] = None) -> Project:
        now = datetime.now().isoformat(timespec="seconds")
        category = category.strip() if category and category.strip() else None
        cur = self._conn.execute(
            "INSERT INTO projects (title, content_md, content_format, content_text, created_at, "
            "updated_at, archived, category, folder_id) VALUES (?, '', 'html', '', ?, ?, 0, ?, ?)",
            (title, now, now, category, folder_id),
        )
        self._conn.commit()
        return Project(id=cur.lastrowid, title=title, content_md="", created_at=now,
                        updated_at=now, archived=False, category=category,
                        content_format="html", content_text="", folder_id=folder_id)

    def _project_from_row(self, row: sqlite3.Row) -> Project:
        return Project(id=row["id"], title=row["title"], content_md=row["content_md"],
                        created_at=row["created_at"], updated_at=row["updated_at"],
                        archived=bool(row["archived"]), category=row["category"],
                        content_format=row["content_format"], content_text=row["content_text"],
                        folder_id=row["folder_id"] if "folder_id" in row.keys() else None)

    # ------------------------------------------------------ project folders
    def list_folders(self) -> list:
        """Every folder, ordered by position then name within each parent."""
        rows = self._conn.execute(
            "SELECT * FROM project_folders ORDER BY position ASC, name COLLATE NOCASE ASC, id ASC"
        ).fetchall()
        return [ProjectFolder(id=r["id"], parent_id=r["parent_id"], name=r["name"],
                              position=r["position"]) for r in rows]

    def get_folder(self, folder_id: int) -> Optional[ProjectFolder]:
        r = self._conn.execute("SELECT * FROM project_folders WHERE id=?", (folder_id,)).fetchone()
        return ProjectFolder(id=r["id"], parent_id=r["parent_id"], name=r["name"],
                             position=r["position"]) if r else None

    @staticmethod
    def _clean_folder_name(name: str) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("a folder needs a name")
        return name

    def create_folder(self, name: str, parent_id: Optional[int] = None) -> ProjectFolder:
        name = self._clean_folder_name(name)
        if parent_id is not None and self.get_folder(parent_id) is None:
            raise ValueError("no such parent folder")
        now = datetime.now().isoformat(timespec="seconds")
        position = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM project_folders WHERE parent_id IS ?",
            (parent_id,)).fetchone()["p"]
        cur = self._conn.execute(
            "INSERT INTO project_folders (parent_id, name, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)", (parent_id, name, position, now, now))
        self._conn.commit()
        return self.get_folder(cur.lastrowid)

    def rename_folder(self, folder_id: int, name: str):
        self._conn.execute("UPDATE project_folders SET name=?, updated_at=? WHERE id=?",
                           (self._clean_folder_name(name),
                            datetime.now().isoformat(timespec="seconds"), folder_id))
        self._conn.commit()

    def folder_ancestors(self, folder_id: Optional[int]) -> list:
        """[root-most, …, folder] — the folder's path, as ProjectFolders."""
        path: list = []
        seen = set()
        while folder_id is not None and folder_id not in seen:
            seen.add(folder_id)
            folder = self.get_folder(folder_id)
            if folder is None:
                break
            path.append(folder)
            folder_id = folder.parent_id
        return list(reversed(path))

    UNNAMED_FOLDER = "(unnamed folder)"

    @classmethod
    def folder_label(cls, name: str) -> str:
        """A folder's name for display. A folder whose stored name is blank
        (made from a whitespace-only category by an earlier version) shows
        as "(unnamed folder)" and can be renamed; the name isn't rewritten."""
        return name if (name or "").strip() else cls.UNNAMED_FOLDER

    def folder_path(self, folder_id: Optional[int]) -> list:
        """The folder's path as names, e.g. ['Research', 'GaN', 'EES']."""
        return [self.folder_label(f.name) for f in self.folder_ancestors(folder_id)]

    def move_folder(self, folder_id: int, new_parent_id: Optional[int]):
        """Moves a folder (and everything in it) under another folder, or to
        the top level with None. Refuses a move into itself or one of its own
        descendants, which would detach the branch from the tree."""
        if self.get_folder(folder_id) is None:
            raise ValueError("no such folder")
        if new_parent_id is not None:
            if self.get_folder(new_parent_id) is None:
                raise ValueError("no such parent folder")
            if folder_id in {f.id for f in self.folder_ancestors(new_parent_id)}:
                raise InvalidMove("a folder cannot be moved into itself or its own subfolder")
        self._conn.execute("UPDATE project_folders SET parent_id=?, updated_at=? WHERE id=?",
                           (new_parent_id, datetime.now().isoformat(timespec="seconds"), folder_id))
        self._conn.commit()

    def delete_folder(self, folder_id: int):
        """Deletes an EMPTY folder. A folder holding folders or projects
        (archived ones included) is refused with FolderNotEmpty (G3-7)."""
        holds = self._conn.execute(
            "SELECT (SELECT COUNT(*) FROM project_folders WHERE parent_id=?) + "
            "(SELECT COUNT(*) FROM projects WHERE folder_id=?) AS n",
            (folder_id, folder_id)).fetchone()["n"]
        if holds:
            raise FolderNotEmpty("the folder still holds folders or projects")
        self._conn.execute("DELETE FROM project_folders WHERE id=?", (folder_id,))
        self._conn.commit()

    def move_project(self, project_id: int, folder_id: Optional[int]):
        """Puts a project in a folder (None = the root). Only folder_id
        changes; the project keeps its id and everything keyed by it."""
        if folder_id is not None and self.get_folder(folder_id) is None:
            raise ValueError("no such folder")
        self._conn.execute("UPDATE projects SET folder_id=? WHERE id=?", (folder_id, project_id))
        self._conn.commit()

    def get_project(self, project_id: int) -> Optional[Project]:
        row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            return None
        return self._project_from_row(row)

    def list_projects(self, archived: bool = False) -> list:
        rows = self._conn.execute(
            "SELECT * FROM projects WHERE archived=? ORDER BY updated_at DESC",
            (1 if archived else 0,),
        ).fetchall()
        return [self._project_from_row(r) for r in rows]

    def list_project_categories(self) -> list:
        """Distinct category names currently in use by any project (archived
        or not), for populating the category picker — a category that no
        project currently uses simply doesn't appear here, since there's no
        separate table for it to be "created" in ahead of time."""
        rows = self._conn.execute(
            "SELECT DISTINCT category FROM projects "
            "WHERE category IS NOT NULL AND category != '' "
            "ORDER BY category COLLATE NOCASE"
        ).fetchall()
        return [r["category"] for r in rows]

    def set_project_category(self, project_id: int, category: Optional[str]):
        category = category.strip() if category and category.strip() else None
        self._conn.execute("UPDATE projects SET category=? WHERE id=?", (category, project_id))
        self._conn.commit()

    def rename_project(self, project_id: int, title: str):
        self._conn.execute("UPDATE projects SET title=? WHERE id=?", (title, project_id))
        self._conn.commit()

    def set_project_archived(self, project_id: int, archived: bool):
        self._conn.execute("UPDATE projects SET archived=? WHERE id=?", (1 if archived else 0, project_id))
        self._conn.commit()

    def delete_project(self, project_id: int):
        """Permanently deletes a project and its sessions/versions. Only meant
        to be offered for already-archived projects, as a deliberate last step."""
        self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        self._conn.commit()

    def save_project_content(self, project_id: int, new_content_md: str,
                              content_format: str = "html", content_text: str = ""):
        """Save new content for a project. If this is the first edit of the
        current calendar day for this project, automatically snapshot the
        content as it stood *before* this edit (so version history reads as
        one checkpoint per day worked, not one per keystroke), and record
        today as a work day. content_format/content_text: see this module's
        docstring — every real call site (ProjectsWidget._save_content) uses
        RichEditor.save() to get both, alongside new_content_md itself.
        """
        now = datetime.now().isoformat(timespec="seconds")
        today = now[:10]
        existing = self.get_project(project_id)
        if existing is None:
            return

        session_exists = self._conn.execute(
            "SELECT 1 FROM project_sessions WHERE project_id=? AND work_date=?",
            (project_id, today),
        ).fetchone()

        if session_exists is None:
            if existing.content_md.strip():
                self.add_version(project_id, existing.content_md, label=None, kind="auto",
                                  content_format=existing.content_format, content_text=existing.content_text)
            self._conn.execute(
                "INSERT INTO project_sessions (project_id, work_date, first_edit_at, last_edit_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, today, now, now),
            )
        else:
            self._conn.execute(
                "UPDATE project_sessions SET last_edit_at=? WHERE project_id=? AND work_date=?",
                (now, project_id, today),
            )

        self._conn.execute(
            "UPDATE projects SET content_md=?, content_format=?, content_text=?, updated_at=? WHERE id=?",
            (new_content_md, content_format, content_text, now, project_id),
        )
        self._conn.commit()

    def get_project_sessions(self, project_id: int) -> list:
        rows = self._conn.execute(
            "SELECT * FROM project_sessions WHERE project_id=? ORDER BY work_date ASC",
            (project_id,),
        ).fetchall()
        return [ProjectSession(work_date=r["work_date"], first_edit_at=r["first_edit_at"],
                                last_edit_at=r["last_edit_at"]) for r in rows]

    def add_version(self, project_id: int, content_md: str, label: Optional[str] = None,
                     kind: str = "manual", content_format: str = "html",
                     content_text: str = "") -> ProjectVersion:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO project_versions (project_id, content_md, content_format, content_text, "
            "label, kind, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, content_md, content_format, content_text or content_md, label, kind, now),
        )
        self._conn.commit()
        return ProjectVersion(id=cur.lastrowid, project_id=project_id, content_md=content_md,
                               label=label, kind=kind, saved_at=now,
                               content_format=content_format, content_text=content_text or content_md)

    def _version_from_row(self, row: sqlite3.Row) -> ProjectVersion:
        return ProjectVersion(id=row["id"], project_id=row["project_id"], content_md=row["content_md"],
                               label=row["label"], kind=row["kind"], saved_at=row["saved_at"],
                               content_format=row["content_format"], content_text=row["content_text"])

    def get_project_versions(self, project_id: int) -> list:
        rows = self._conn.execute(
            "SELECT * FROM project_versions WHERE project_id=? ORDER BY saved_at DESC",
            (project_id,),
        ).fetchall()
        return [self._version_from_row(r) for r in rows]

    def get_version(self, version_id: int) -> Optional[ProjectVersion]:
        row = self._conn.execute("SELECT * FROM project_versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            return None
        return self._version_from_row(row)

    def restore_project_version(self, project_id: int, version_id: int) -> Optional[tuple[str, str]]:
        """Restore a project's content to an earlier version. The content in
        place right before the restore is itself saved as a manual version
        first, so restoring never silently discards current work. Returns
        (content_md, content_format) — the caller (ProjectsWidget) needs the
        format to load the restored content back into the editor correctly
        (RichEditor.load()), since an old version may be in either format."""
        version = self.get_version(version_id)
        if version is None or version.project_id != project_id:
            return None
        current = self.get_project(project_id)
        if current and current.content_md.strip():
            self.add_version(project_id, current.content_md, label="Before restore", kind="manual",
                              content_format=current.content_format, content_text=current.content_text)
        self.save_project_content(project_id, version.content_md,
                                   content_format=version.content_format, content_text=version.content_text)
        return version.content_md, version.content_format

    # -------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str):
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    # ------------------------------------------------------ entry revisions
    #
    # The storage half of daily-entry version history. WHEN a revision is
    # made is decided by entry_history.py; this only stores, lists, loads and
    # deletes. There is no update method, and the table's trigger would
    # refuse one: a revision, once made, is what the entry WAS.
    _REVISION_META = ("id, date, title, reason, created_at, content_hash, "
                      "LENGTH(body_text) AS char_count")

    def add_entry_revision(self, date: str, body: str, body_format: str,
                           body_text: str, reason: str, title: str = "",
                           content_hash: Optional[str] = None) -> EntryRevision:
        from .entry_history import content_hash as _hash
        digest = content_hash or _hash(body)
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO entry_revisions (date, title, body, body_format, body_text, "
            "content_hash, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, title or "", body, body_format or "html", body_text or "",
             digest, reason, now))
        self._conn.commit()
        return EntryRevision(id=cur.lastrowid, date=date, title=title or "",
                             reason=reason, created_at=now, content_hash=digest,
                             char_count=len(body_text or ""), body=body,
                             body_format=body_format or "html", body_text=body_text or "")

    def entry_revision_exists(self, date: str, content_hash: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM entry_revisions WHERE date=? AND content_hash=? LIMIT 1",
            (date, content_hash)).fetchone() is not None

    def list_entry_revisions(self, date: str) -> list:
        """Newest first, metadata only — no bodies are read."""
        rows = self._conn.execute(
            f"SELECT {self._REVISION_META} FROM entry_revisions WHERE date=? "
            "ORDER BY created_at DESC, id DESC", (date,)).fetchall()
        return [EntryRevision(**dict(r)) for r in rows]

    def latest_entry_revision(self, date: str) -> Optional[EntryRevision]:
        """The newest revision, WITH its body (used to measure change)."""
        row = self._conn.execute(
            "SELECT * FROM entry_revisions WHERE date=? ORDER BY created_at DESC, id DESC "
            "LIMIT 1", (date,)).fetchone()
        return self._revision_from_row(row) if row else None

    def get_entry_revision(self, revision_id: int) -> Optional[EntryRevision]:
        row = self._conn.execute(
            "SELECT * FROM entry_revisions WHERE id=?", (revision_id,)).fetchone()
        return self._revision_from_row(row) if row else None

    @staticmethod
    def _revision_from_row(row) -> EntryRevision:
        return EntryRevision(
            id=row["id"], date=row["date"], title=row["title"], reason=row["reason"],
            created_at=row["created_at"], content_hash=row["content_hash"],
            char_count=len(row["body_text"] or ""), body=row["body"],
            body_format=row["body_format"], body_text=row["body_text"])

    def delete_entry_revisions(self, date: str) -> int:
        """Deletes every previous version of one entry. The entry itself (its
        current state) and any recovery checkpoints are untouched."""
        cur = self._conn.execute("DELETE FROM entry_revisions WHERE date=?", (date,))
        self._conn.commit()
        return cur.rowcount

    def delete_entry_revisions_before(self, date: str, cutoff_date: str) -> int:
        """Deletes one entry's versions created before `cutoff_date`
        (YYYY-MM-DD, local time — the same clock created_at is written in)."""
        cur = self._conn.execute(
            "DELETE FROM entry_revisions WHERE date=? AND created_at < ?",
            (date, cutoff_date))
        self._conn.commit()
        return cur.rowcount

    # --------------------------------------------------- recovery checkpoints
    _CHECKPOINT_META = ("id, scope, ref, title, prior_chars, prior_paragraphs, "
                        "removed_chars, removed_paragraphs, remaining_chars, "
                        "created_at, content_hash")

    def add_recovery_checkpoint(self, scope: str, ref: str, body: str, body_format: str,
                                body_text: str, *, title: str = "", prior_chars: int = 0,
                                prior_paragraphs: int = 0, removed_chars: int = 0,
                                removed_paragraphs: int = 0,
                                remaining_chars: int = 0) -> RecoveryCheckpoint:
        from .entry_history import content_hash as _hash
        digest = _hash(body)
        now = datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "INSERT INTO recovery_checkpoints (scope, ref, title, body, body_format, "
            "body_text, content_hash, prior_chars, prior_paragraphs, removed_chars, "
            "removed_paragraphs, remaining_chars, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scope, str(ref), title or "", body, body_format or "html", body_text or "",
             digest, prior_chars, prior_paragraphs, removed_chars, removed_paragraphs,
             remaining_chars, now))
        self._conn.commit()
        return RecoveryCheckpoint(
            id=cur.lastrowid, scope=scope, ref=str(ref), title=title or "",
            prior_chars=prior_chars, prior_paragraphs=prior_paragraphs,
            removed_chars=removed_chars, removed_paragraphs=removed_paragraphs,
            remaining_chars=remaining_chars, created_at=now, content_hash=digest,
            body=body, body_format=body_format or "html", body_text=body_text or "")

    def recovery_checkpoint_exists(self, scope: str, ref: str, content_hash: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM recovery_checkpoints WHERE scope=? AND ref=? AND content_hash=? "
            "LIMIT 1", (scope, str(ref), content_hash)).fetchone() is not None

    def list_recovery_checkpoints(self) -> list:
        """Newest first, metadata only."""
        rows = self._conn.execute(
            f"SELECT {self._CHECKPOINT_META} FROM recovery_checkpoints "
            "ORDER BY created_at DESC, id DESC").fetchall()
        return [RecoveryCheckpoint(**dict(r)) for r in rows]

    def get_recovery_checkpoint(self, checkpoint_id: int) -> Optional[RecoveryCheckpoint]:
        row = self._conn.execute(
            "SELECT * FROM recovery_checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        return RecoveryCheckpoint(**data)

    def delete_recovery_checkpoint(self, checkpoint_id: int):
        self._conn.execute("DELETE FROM recovery_checkpoints WHERE id=?", (checkpoint_id,))
        self._conn.commit()
