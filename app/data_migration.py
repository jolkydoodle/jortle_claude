"""Moving the application-data directory to jortle_claude's own name, safely.

The data used to live in folders named after the app's former names
(`DailyJournal`, then `Jortle`). The folder is being renamed; the data
inside it is not being changed in any way.
That makes this a file-copy problem with one hard requirement attached: the
user's journal must survive it, including in the cases where the copy goes
wrong.

The shape of the migration (spec Parts 4-9)
-------------------------------------------

    <legacy dir>
         │ copy (never move)
         ▼
    <target>.migrating-<timestamp>
         │ validate: open the database, read the tables, parse the settings
         ▼
    <target>              ← renamed into place only once validation passed
         │
    <legacy dir> stays exactly where it was, untouched

Three properties are doing the real work here:

* **Copy, never move.** Until the moment the temporary directory is renamed
  into place, the legacy folder is the only copy of anything, and it is never
  opened for writing. A failure at any point leaves it exactly as it was.
* **Validate before promoting.** "The files copied" is not the same claim as
  "the journal survived". The database is actually opened and queried, and
  every settings file is actually parsed, before the temporary directory is
  allowed to become the real one.
* **Promote by rename.** A directory rename within one filesystem is atomic
  on every platform this runs on, so there is no window in which the target
  exists but is half-populated.

Promotion when the target already exists
----------------------------------------
The first release of this module assumed the target could not exist by the
time the copy was ready, and called `staging.rename(target)` unconditionally.
On POSIX that quietly replaces an empty directory; on Windows it raises
`[WinError 183] Cannot create a file when that file already exists`, which is
exactly the failure this module was written to avoid reporting. Worse, the
assumption itself was wrong: the target can appear during the copy, because a
second jortle_claude process can be starting at the same time, and because a
subsystem asking for a subfolder of the data directory used to create it.

So promotion never renames blindly. `classify_target()` decides what the
existing directory actually is, and `_promote()` acts on that (Part 4):

    A  a real installation, already migrated   → use it; discard our copy
    B  nothing but app-generated scaffolding   → set it aside, promote, put
       (empty, or only models/backups/logs,       the scaffolding back
        or a database with no user content)
    C  a real installation we did not make,    → touch nothing; report the
       or one we cannot read                      ambiguity and stop

Case B moves the existing directory aside with a rename rather than deleting
anything, and renames it back if the promotion then fails. Nothing in this
module deletes a directory that has ever held user data.

Why the target directory is not created first
---------------------------------------------
`paths.get_data_dir()` used to `mkdir` on every call. If that ran before this
did, jortle_claude would create an empty target, then find it, then conclude there
was nothing to migrate — and the user would get an empty journal sitting next
to their real one. So the ONLY thing allowed to create the target directory is
this module, and `paths.get_data_dir()` now routes through `resolve_data_dir()`
below.

For the same reason, "has the migration already run?" is not answered by
looking at whether the legacy directory still exists — it deliberately always
does. It is answered by `data_version.json` inside the target (Part 9).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import security
from .process_lock import ProcessLock
from .saving import SQL_CONTENT_FUNCTION, content_sql, register_sql_functions

# The folder jortle_claude uses from now on. Platform-conventional location,
# with the application's own name — see paths.py. Deliberately NOT "Jortle":
# that name belongs to a separately developed application, and jortle_claude
# must never share its active data folder (Master Spec §§2, 50).
APP_DIR_NAME = "jortle_claude"

# Where the data may have been before, newest first. Each is tried in order;
# the first one that exists and looks like a real installation is migrated.
#
# "Jortle" is what the builds immediately before the jortle_claude rename
# used, so it is the newest source and is tried first. It is COPIED, never
# moved or modified (apart from the WAL checkpoint below), so an application
# that still uses that folder keeps working exactly as before.
# "DailyJournal" is what every build before that used.
# ".journal-app" (in the data root and in the home directory) is included
# because it is the name an earlier rename was requested against — if a build
# or a machine has one, it is found rather than ignored.
LEGACY_DIR_NAMES = ("Jortle", "DailyJournal", ".journal-app", "journal-app")
LEGACY_HOME_DIR_NAMES = (".journal-app", ".jortle", ".daily-journal")

DATA_FORMAT_VERSION = 1

# Prefix for the hidden working folders and lock files this module (and
# backup.py's restore) creates beside the data folder. Named after the
# current identity so they are never mistaken for another application's.
STAGING_PREFIX = ".jortle_claude"
VERSION_FILE = "data_version.json"

# Everything the validator knows how to check. A missing file is only a
# failure when the legacy directory HAD one — a legacy install with no
# attachments folder is normal, not corrupt.
DB_FILENAME = "journal.db"


class MigrationError(Exception):
    """Raised when legacy data was found but could not be safely migrated.

    Carries enough detail to tell the user exactly where their data still is
    — which is the point, since the whole contract of this module is that the
    legacy directory is never touched.
    """

    def __init__(self, message: str, legacy_dir: Path, quarantine: Optional[Path] = None):
        super().__init__(message)
        self.message = message
        self.legacy_dir = legacy_dir
        self.quarantine = quarantine

    def user_message(self) -> str:
        lines = [
            "jortle_claude could not migrate your existing data to its new folder.",
            "",
            f"Reason: {self.message}",
            "",
            "Your existing data has NOT been changed, moved, or deleted. It is",
            f"still here:\n    {self.legacy_dir}",
        ]
        if self.quarantine is not None:
            lines += [
                "",
                "The incomplete copy has been left here for diagnosis, and can be",
                f"deleted safely:\n    {self.quarantine}",
            ]
        lines += [
            "",
            "jortle_claude has not started with an empty journal, because that would look",
            "like your writing had disappeared. Nothing has been lost — please send",
            "this message on so the cause can be found.",
        ]
        return "\n".join(lines)


@dataclass
class MigrationResult:
    """What `resolve_data_dir()` did, for logging and for the tests."""

    data_dir: Path
    case: str = ""              # which of the spec's cases A-E applied
    migrated_from: Optional[Path] = None
    validated: list = field(default_factory=list)
    notes: list = field(default_factory=list)


_result: Optional[MigrationResult] = None


# --------------------------------------------------------------- locations
def _data_root() -> Path:
    """The platform's per-user application-data directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base)


def target_dir() -> Path:
    """Where jortle_claude's data lives from now on. Not created by this function."""
    return _data_root() / APP_DIR_NAME


def legacy_candidates() -> list:
    """Every place an older installation's data could be, in priority order."""
    root = _data_root()
    home = Path.home()
    candidates = [root / name for name in LEGACY_DIR_NAMES]
    candidates += [home / name for name in LEGACY_HOME_DIR_NAMES]
    target = target_dir()
    # Never treat the target as its own legacy source, however it is spelled
    # on a case-insensitive filesystem.
    return [c for c in candidates if c.resolve() != target.resolve()]


def find_legacy_dir() -> Optional[Path]:
    """The first candidate that exists and actually holds an installation.

    "Holds an installation" means a database file is present. An empty folder
    left behind by something else is not data, and migrating it would give
    the user a confusing empty install with a migration record attached.
    """
    for candidate in legacy_candidates():
        try:
            if (candidate / DB_FILENAME).is_file():
                return candidate
        except OSError:
            continue
    return None


# -------------------------------------------------------------- validation
def validate_data_dir(path: Path, expect_attachments: bool = False) -> list:
    """Opens and reads a copied installation. Returns the checks that passed.

    Raises ValueError with a specific reason on the first real problem — the
    caller turns that into a MigrationError, so the failure the user sees
    names what actually went wrong rather than "migration failed".
    """
    checks = []

    db_path = path / DB_FILENAME
    if not db_path.is_file():
        raise ValueError(f"the copied database file is missing ({db_path.name})")
    if db_path.stat().st_size == 0:
        raise ValueError("the copied database file is empty")
    checks.append("database file present")

    # Opened read-only: validation must never be able to modify the copy it
    # is checking, and must never run a schema migration as a side effect.
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        register_sql_functions(conn)
    except sqlite3.Error as exc:
        raise ValueError(f"the copied database could not be opened ({exc})") from exc

    # Everything from here on is wrapped: sqlite3 opens lazily, so a file
    # that isn't a database at all only fails on the first real query, and
    # that failure must arrive as a validation result rather than as a raw
    # sqlite3 error escaping the migration.
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise ValueError("the copied database failed SQLite's integrity check")
        checks.append("database opens and passes an integrity check")

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "entries" not in tables:
            raise ValueError("the copied database has no journal entries table")

        # Walk EVERY table, not a list of the ones someone remembered.
        # Counting rows forces SQLite to actually read each table through
        # rather than just confirm it is declared, so a page that will not
        # decode is found here instead of when the user opens that day.
        #
        # This used to name six tables. `project_versions` was not among
        # them, so a copy with damaged version history would have validated
        # and been promoted — and a table added later would have been
        # unchecked until someone thought to come back. The friendly names
        # below are only for the report; the set of tables comes from the
        # database itself.
        FRIENDLY = {
            "entries": "journal entries",
            "projects": "Projects",
            "project_versions": "project version history",
            "project_sessions": "project sessions",
            "reader_notes_scoped": "Reader's Notes",
            "reader_notes": "Reader's Notes (superseded table)",
            "calendar_events": "calendar events",
            "day_markers": "Day Markers",
            "settings": "settings",
            "tasks": "tasks (retired)",
        }
        for table in sorted(tables):
            label = FRIENDLY.get(table, table)
            count = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
            checks.append(f"{label}: {count} row(s) readable")
        # Rich text specifically: read one real entry body end to end, so a
        # truncated copy is caught here rather than when the user opens it.
        # "Real" is the app's one written-vs-blank rule: a blank row left by
        # an earlier version has no formatted content to lose. The copy is
        # checked as it was copied, before any schema upgrade, so a database
        # from before round 21 has no body_text: its stored Markdown is the
        # text (the upgrade that adds the column copies it across the same way).
        entry_cols = {r["name"] for r in conn.execute('PRAGMA table_info("entries")')}
        text = "body_text" if "body_text" in entry_cols else "body_md"
        row = conn.execute(
            f"SELECT date, body_md FROM entries WHERE {content_sql(text, 'body_md')} "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            if not row["body_md"]:
                raise ValueError(
                    f"the journal entry for {row['date']} lost its formatted content"
                )
            checks.append(f"rich text readable (checked {row['date']})")
    except sqlite3.Error as exc:
        raise ValueError(f"the copied database could not be read ({exc})") from exc
    finally:
        conn.close()

    # Any JSON the installation carries must still parse — a half-copied
    # settings file is exactly the kind of damage that otherwise surfaces
    # days later.
    for json_path in sorted(path.rglob("*.json")):
        try:
            json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{json_path.name} could not be parsed ({exc})") from exc
    checks.append("configuration files parse")

    if expect_attachments:
        if not (path / "attachments").is_dir():
            raise ValueError("the attachments folder did not copy across")
        checks.append("attachments folder present")

    return checks


def _looks_usable(path: Path) -> bool:
    """A cheap check for 'this directory is a working installation'.

    Used to decide between the spec's Case A (use it) and Case E (something
    is wrong — do not overwrite it, do not merge into it).
    """
    if security.is_encrypted_install(path):
        # Cannot be read before it is unlocked, and it is not this module's
        # business to ask for the passphrase. It is opened, and so validated,
        # by the unlock that follows; failing there reports the problem
        # without anything having been moved or merged.
        return True
    try:
        validate_data_dir(path)
        return True
    except (ValueError, OSError, sqlite3.Error):
        return False


# Subfolders and files the application creates for itself, which carry no
# journal of their own. A target directory containing only these was made by
# startup scaffolding, not by a user writing in it, so it must not be allowed
# to block a migration (Part 4B). `models` is on the list because the AI
# feature that created it is exactly how the target came to exist early; it
# stays listed so an installation that still has the folder is handled.
APP_GENERATED_NAMES = {
    "models", "backups", "logs", "cache", "tmp", "temp",
    VERSION_FILE, "reflection_prompts.yaml",
}


# Tables a brand-new database fills by itself (settings bookkeeping and the
# four seeded Day Markers) — the only ones that are NOT evidence of a user.
NON_USER_TABLES = frozenset({"settings", "day_markers"})


def _user_row_condition(conn, table: str) -> str:
    """Which rows of `table` are evidence of a user, as a WHERE condition.

    Journal entries and Reader's Notes hold documents, and a document is only
    something a person wrote when the one written-vs-blank rule says so
    (`saving.document_has_content`, Invariant 6): earlier versions stored
    blank rows just for viewing a date, and those must not make a folder look
    like a journal. An entry row also carries the day's marker and title,
    which a person sets without writing, so those count on their own. Every
    other table: any row.
    """
    if table == "entries":
        cols = {row[1] for row in conn.execute('PRAGMA table_info("entries")')}
        # Before round 21 there was no body_text: the stored Markdown is the text.
        text = "body_text" if "body_text" in cols else "body_md"
        parts = [content_sql(text, "body_md")]
        if "tag" in cols:
            parts.append("COALESCE(tag, '') != ''")
        if "title" in cols:
            parts.append(f"{SQL_CONTENT_FUNCTION}(title, NULL) = 1")
        return " OR ".join(parts)
    if table == "reader_notes_scoped":
        return content_sql("content_text", "content")
    return "1"


def has_user_content(path: Path) -> Optional[bool]:
    """Whether this installation's database holds anything a user made.

    True / False when the database can be read, None when there is no
    readable database at all.

    Every store a person authors into counts. Two tables deliberately do not,
    and that exclusion is the whole point of this function: merely creating a
    database populates `settings` with bookkeeping rows (schema flags,
    migration markers) and `day_markers` with the four seeded defaults, all
    before anybody has typed a word. Counting either would make an untouched
    installation look like a journal — and an untouched installation at the
    destination is exactly what must not be allowed to block a migration.

    Measured rather than assumed: a database straight out of `Database()`
    has 4 settings rows and 4 day_markers rows and nothing else. A marker is
    only evidence of a user when a day is using it, and that day is an entry,
    which is counted. Getting this wrong the other way would let a real
    journal be treated as scaffolding, so everything a person writes counts.
    """
    db_path = path / DB_FILENAME
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    if security.is_encrypted_install(path):
        # An encrypted journal with its key file. Its tables cannot be read
        # without the passphrase, and they do not need to be: encryption is
        # only ever set up by a person, on purpose, so this is a real
        # installation and must never be treated as scaffolding or be
        # overwritten by a legacy copy. (Before this check, an encrypted
        # journal read as "unreadable", and with the old Jortle folder still
        # present the app refused to start.)
        return True
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        register_sql_functions(conn)
        tables = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # Every table counts EXCEPT the two a fresh database fills by itself.
        # This used to be a hand-written list of the tables that counted, and
        # it went stale: ToDos (`todos`) were never on it, so a database
        # holding only ToDos looked empty — and so would one holding only
        # version history or recovery copies. Naming the exceptions instead
        # means a store added later counts automatically, which is the safe
        # direction for this question to fail in. Entries and Reader's Notes
        # count only when written (see _user_row_condition).
        for table in sorted(tables):
            if table in NON_USER_TABLES or table.startswith("sqlite_"):
                continue
            where = _user_row_condition(conn, table)
            if conn.execute(f'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1').fetchone() is not None:
                return True
        return False
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def classify_target(target: Path) -> str:
    """What an existing target directory is, in the spec's Part 4 terms.

    Returns one of:
      "absent"       it isn't there
      "installation" a readable installation with user content in it
      "scaffolding"  empty, or only app-generated files, or a readable but
                     content-free database — nothing a user would miss
      "ambiguous"    a database that is present but unreadable, or unknown
                     files alongside one — never touched automatically
    """
    if not target.exists():
        return "absent"
    if not target.is_dir():
        return "ambiguous"

    try:
        entries = list(target.iterdir())
    except OSError:
        return "ambiguous"

    if not entries:
        return "scaffolding"

    content = has_user_content(target)
    if content is True:
        # There is a journal in here. Whether it is ours is decided by the
        # caller from the version record; either way it is never overwritten.
        return "installation"

    if content is None:
        # No readable database. If everything present is app scaffolding, it
        # is safe to set aside; anything else is unknown and stays put.
        db_present = (target / DB_FILENAME).is_file()
        if db_present:
            return "ambiguous"    # a database that would not open
        unknown = [e.name for e in entries if e.name not in APP_GENERATED_NAMES]
        return "scaffolding" if not unknown else "ambiguous"

    # A database that reads fine and contains nothing at all: a store that
    # was initialised and never written to. Part 4B — must not block.
    return "scaffolding"


def _promote(staging: Path, target: Path, legacy_dir: Path, stamp: str) -> list:
    """Puts the validated copy in place, whatever is already at `target`.

    Returns notes describing what it had to do. Raises MigrationError without
    modifying either the legacy directory or an existing installation.
    """
    notes = []
    state = classify_target(target)

    if state == "absent":
        try:
            staging.rename(target)
        except OSError as exc:
            raise MigrationError(
                f"the migrated copy could not be moved into place ({exc})",
                legacy_dir, _quarantine(staging, target, stamp),
            ) from exc
        return notes

    if state == "installation":
        # Case A. Someone — almost certainly a second jortle_claude process that
        # started at the same time — got there first. Its data is already
        # the migrated data, so ours is redundant. Do not overwrite, do not
        # merge, do not import again.
        keep = _quarantine(staging, target, stamp, kind="superseded")
        notes.append(
            f"the jortle_claude folder already held an installation, so it was kept and "
            f"this copy was set aside at {keep}"
        )
        return notes

    if state == "ambiguous":
        # Case C. Something is there that we did not put there and cannot
        # read. Both copies are preserved and the user is told.
        raise MigrationError(
            "the jortle_claude folder already exists and contains data that could not be "
            "read as a jortle_claude installation. Nothing has been merged, overwritten "
            "or deleted — the folder is still exactly as it was",
            legacy_dir, _quarantine(staging, target, stamp),
        )

    # Case B: scaffolding. Set it aside by rename (never delete), promote,
    # then put the scaffolding back beside the real data.
    sidelined = _unique_path(target.parent / f"{target.name}.replaced-{stamp}")
    try:
        target.rename(sidelined)
    except OSError as exc:
        raise MigrationError(
            f"the empty jortle_claude folder could not be moved out of the way ({exc})",
            legacy_dir, _quarantine(staging, target, stamp),
        ) from exc

    try:
        staging.rename(target)
    except OSError as exc:
        # Put it back exactly as it was before failing.
        try:
            sidelined.rename(target)
        except OSError:
            pass
        raise MigrationError(
            f"the migrated copy could not be moved into place ({exc})",
            legacy_dir, _quarantine(staging, target, stamp),
        ) from exc

    notes.append("an empty jortle_claude folder created before migration was set aside")
    notes.extend(_restore_scaffolding(sidelined, target))
    return notes


def _restore_scaffolding(sidelined: Path, target: Path) -> list:
    """Moves app-generated files from the sidelined folder into the new one.

    Only names that aren't already present are moved, so nothing the migrated
    data brought with it is overwritten. Whatever can't be moved is left in
    the sidelined folder rather than being deleted, and the folder itself is
    removed only when it ends up genuinely empty.
    """
    notes = []
    moved = []
    try:
        entries = list(sidelined.iterdir())
    except OSError:
        return notes

    for item in entries:
        if item.name == VERSION_FILE:
            continue            # the migrated copy writes its own
        destination = target / item.name
        if destination.exists():
            continue
        try:
            item.rename(destination)
            moved.append(item.name)
        except OSError:
            pass

    if moved:
        notes.append("kept from the pre-migration folder: " + ", ".join(sorted(moved)))
    try:
        next(sidelined.iterdir())
    except StopIteration:
        try:
            sidelined.rmdir()
        except OSError:
            pass
    except OSError:
        pass
    else:
        notes.append(f"leftover files from before migration are at {sidelined}")
    return notes


def _unique_path(path: Path) -> Path:
    """`path`, or the first numbered variant of it that doesn't exist."""
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.name}-{n}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.name}-{os.getpid()}")


def _quarantine(staging: Path, target: Path, stamp: str, kind: str = "failed") -> Path:
    """Renames the staging copy out of the way, keeping it for diagnosis."""
    destination = _unique_path(target.parent / f"{STAGING_PREFIX}-migration-{kind}-{stamp}")
    try:
        staging.rename(destination)
        return destination
    except OSError:
        return staging


# --------------------------------------------------------------- versioning
def read_version_file(path: Path) -> dict:
    try:
        return json.loads((path / VERSION_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def write_version_file(path: Path, migrated_from: Optional[Path] = None):
    record = read_version_file(path)
    record["data_format_version"] = DATA_FORMAT_VERSION
    record["app_name"] = APP_DIR_NAME
    if migrated_from is not None:
        record["legacy_migration_completed"] = True
        record["migrated_from"] = str(migrated_from)
        record["migrated_at"] = datetime.now().isoformat(timespec="seconds")
    record.setdefault("legacy_migration_completed", False)
    (path / VERSION_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")


def migration_completed(path: Path) -> bool:
    """Whether this installation has already absorbed a legacy directory.

    Read from the record inside the target, NOT from whether the legacy
    directory still exists — it is deliberately kept forever, so its presence
    says nothing about whether the migration ran (Part 9).
    """
    return bool(read_version_file(path).get("legacy_migration_completed"))


# --------------------------------------------------------------- migration
def _copy_tree(source: Path, destination: Path):
    """Copies an installation, skipping what must not be copied.

    Journal/WAL sidecars are skipped deliberately: copying a `-wal` file
    without the exact matching database is how a copy ends up subtly stale.
    The database is checkpointed into a single file below before the copy, so
    everything the sidecars held is already inside `journal.db`.
    """
    def ignore(directory, names):
        skip = [
            name for name in names
            if name.endswith(("-wal", "-shm", ".tmp"))
            or name.startswith((".jortle-migrating", f"{STAGING_PREFIX}-migrating"))
        ]
        # `models` held downloaded language-model files for a feature this
        # version no longer has. It is several gigabytes of cache, not user
        # data, and copying it would make every migration slow for something
        # nothing will ever read. It stays in the legacy folder, where the
        # user can delete it when they like.
        if Path(directory) == Path(source) and "models" in names:
            skip.append("models")
        return skip

    shutil.copytree(source, destination, ignore=ignore, symlinks=False)


def _checkpoint_legacy_db(source: Path):
    """Folds any write-ahead log back into the legacy database file.

    The app runs SQLite in WAL mode, so a database closed uncleanly can have
    committed data sitting in `journal.db-wal` rather than in `journal.db`.
    Copying only the main file would silently lose the most recent writes —
    exactly the entries the user is most likely to notice.

    This opens the legacy database read-write for a moment, which is the one
    and only exception to "never touch the legacy directory". It is a
    checkpoint, not a change: it moves the user's own already-committed data
    from one of their files into another. It is also best-effort — if it
    can't be done, the copy proceeds and the sidecars come along too.
    """
    db_path = source / DB_FILENAME
    if not db_path.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def migrate(legacy_dir: Path, target: Path) -> MigrationResult:
    """Copy → validate → promote. Raises MigrationError without touching the
    legacy directory if anything goes wrong."""
    checkpointed = _checkpoint_legacy_db(legacy_dir)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = target.parent / f"{STAGING_PREFIX}-migrating-{stamp}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        _copy_tree(legacy_dir, staging)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise MigrationError(f"the data could not be copied ({exc})", legacy_dir) from exc

    if not checkpointed:
        # The checkpoint didn't run, so the sidecars may still matter. Bring
        # them across rather than leaving the copy potentially behind.
        for suffix in ("-wal", "-shm"):
            sidecar = legacy_dir / (DB_FILENAME + suffix)
            if sidecar.is_file():
                try:
                    shutil.copy2(sidecar, staging / sidecar.name)
                except OSError:
                    pass

    try:
        checks = validate_data_dir(
            staging, expect_attachments=(legacy_dir / "attachments").is_dir()
        )
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise MigrationError(
            str(exc), legacy_dir, _quarantine(staging, target, stamp)
        ) from exc

    _rewrite_app_managed_paths(staging, legacy_dir, target)
    write_version_file(staging, migrated_from=legacy_dir)

    notes = _promote(staging, target, legacy_dir, stamp)

    return MigrationResult(
        data_dir=target, case="B", migrated_from=legacy_dir, validated=checks,
        notes=[f"legacy data left untouched at {legacy_dir}"] + notes,
    )


def _rewrite_app_managed_paths(copied: Path, legacy_dir: Path, target: Path):
    """Repoints stored settings that name a file INSIDE the data directory.

    Deliberately not a text substitution over the settings table (Part 11): a
    stored value is rewritten only when it is an absolute path that lives
    inside the legacy data directory, i.e. a file the application manages and
    has just moved. A backup folder the user chose on another drive, or any
    other path of theirs, contains no such prefix and is left alone.

    As of this version the app stores no such paths at all — every location
    it uses is derived from the data directory at runtime. This exists so
    that a path stored LATER migrates correctly instead of quietly breaking,
    and it records what it changed rather than doing it silently.
    """
    db_path = copied / DB_FILENAME
    if not db_path.is_file():
        return []
    prefix = str(legacy_dir)
    rewritten = []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "settings" not in tables:
                return []
            for row in conn.execute("SELECT key, value FROM settings").fetchall():
                value = row["value"]
                if not isinstance(value, str) or not value.startswith(prefix):
                    continue
                moved = str(target) + value[len(prefix):]
                conn.execute("UPDATE settings SET value=? WHERE key=?", (moved, row["key"]))
                rewritten.append(row["key"])
            if rewritten:
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return rewritten


# ------------------------------------------------------------------ entry
def resolve_data_dir() -> Path:
    """The data directory to use, migrating legacy data first if needed.

    Runs its decision once per process; `paths.get_data_dir()` calls this on
    every access, so the result is cached rather than re-deciding (and
    re-scanning the filesystem) constantly.
    """
    global _result
    if _result is None:
        _result = _resolve()
    return _result.data_dir


def migration_result() -> Optional[MigrationResult]:
    return _result


def reset_for_tests():
    """Clears the cached decision. Only the tests call this — a real process
    resolves its data directory exactly once."""
    global _result
    _result = None


def _resolve() -> MigrationResult:
    target = target_dir()
    legacy = find_legacy_dir()
    state = classify_target(target)

    if state == "installation":
        # Case A / D. There is a journal in the target. A legacy directory
        # alongside it is expected — it is deliberately kept forever — so it
        # is never re-imported on the strength of merely existing (Part 9).
        if not _looks_usable(target):
            # Content is there but the installation does not validate. Do not
            # overwrite it and do not merge legacy data into it.
            raise MigrationError(
                "the jortle_claude data folder contains a journal that could not be read as a "
                "working installation. Nothing has been merged, overwritten or deleted",
                legacy if legacy is not None else target,
                target,
            )
        write_version_file(target)
        case = "D" if legacy is not None else "A"
        notes = []
        if legacy is not None:
            notes.append(f"legacy data retained at {legacy}; not re-imported")
        if not migration_completed(target):
            notes.append("this installation was not created by a migration")
        return MigrationResult(data_dir=target, case=case, notes=notes)

    if state == "ambiguous":
        # Case E. Unreadable data we did not put there. Preserve everything.
        if legacy is not None:
            raise MigrationError(
                "the jortle_claude data folder already exists but could not be read as a "
                "working installation, and legacy data was found alongside it. "
                "Neither has been modified",
                legacy, target,
            )
        return MigrationResult(
            data_dir=target, case="E",
            notes=["target exists but did not validate; no legacy data to fall back on"],
        )

    # state is "absent" or "scaffolding": nothing here that a user would miss,
    # so an existing folder must not block the migration (Part 4B). `migrate`
    # handles setting the scaffolding aside — it is not removed here, because
    # between this check and the promotion another process may have filled it.
    if legacy is not None:
        with _migration_lock(target):
            # Re-check inside the lock: if another process migrated while we
            # waited, adopt its result instead of migrating a second time.
            if classify_target(target) == "installation" and _looks_usable(target):
                write_version_file(target)
                return MigrationResult(
                    data_dir=target, case="D",
                    notes=[f"another jortle_claude process completed the migration; "
                           f"legacy data retained at {legacy}"],
                )
            return migrate(legacy, target)      # Case B

    if state == "absent":
        target.mkdir(parents=True, exist_ok=True)
    write_version_file(target)                  # Case C
    return MigrationResult(data_dir=target, case="C", notes=["new installation"])


# A directory rename is atomic, but a copy that takes a minute is not, and two
# jortle_claude processes starting together would otherwise both copy and then race
# to promote. The lock makes the second one wait for the first rather than
# duplicating the work; `_promote` still handles losing the race, because a
# lock is an optimisation and the classification is the guarantee.
LOCK_NAME = f"{STAGING_PREFIX}-migration.lock"
LOCK_WAIT_SECONDS = 120


@contextmanager
def _migration_lock(target: Path):
    """Held by one process while it migrates. An operating-system lock
    (`process_lock`): a process that dies — killed, crashed — frees it at once,
    so the next launch never waits for a migration nobody is running. A live
    holder (another launch, or `recover_entry.py`) is waited for, as before, and
    after LOCK_WAIT_SECONDS the migration proceeds anyway: promotion is still
    safe."""
    lock = ProcessLock(target.parent / LOCK_NAME)
    held = False
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            held = lock.acquire()
        except OSError:
            break           # can't lock here; promotion is still safe
        if held or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    if held:
        _remove_abandoned_staging(target.parent)
    try:
        yield
    finally:
        if held:
            lock.release()


def _remove_abandoned_staging(parent: Path):
    """Partial copies left by a migration whose process died mid-copy.

    Called only while holding the migration lock, so no live process is
    copying into any of them. Each is an unfinished copy of a legacy folder
    that was never touched, so nothing in it exists only there. The copies a
    failed migration keeps on purpose (`…-migration-failed-…`) are not staging
    folders and are left alone."""
    try:
        leftovers = [p for p in parent.iterdir()
                     if p.is_dir() and p.name.startswith(f"{STAGING_PREFIX}-migrating-")]
    except OSError:
        return
    for leftover in leftovers:
        shutil.rmtree(leftover, ignore_errors=True)
