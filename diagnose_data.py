"""jortle_claude — report on the data folders, and change nothing.

Run this when a migration has gone wrong, or before letting one run, to see
what is actually on disk:

    python diagnose_data.py

It opens every candidate folder READ-ONLY, so it cannot alter, move or
delete anything. It needs no Qt and no dependencies beyond the standard
library, so it runs even if the app won't start.

It reports, for each folder it finds:

    where it is, and how big
    whether its database opens, and passes SQLite's own integrity check
    how many journal entries, projects, notes, events and markers are in it
    whether any of the entries actually have writing in them
    when it was last written to
    whether jortle_claude's migration would treat it as real data or as scaffolding

and then says which folder jortle_claude would use on the next launch, and why.

An encrypted journal is reported as encrypted, with its key files; its
contents are only counted if you run it with --unlock (you are asked for the
passphrase, and nothing is written):

    python diagnose_data.py --unlock
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import re                                       # noqa: E402

from app import data_migration as dm            # noqa: E402
from app import security                        # noqa: E402
from app.saving import MEANINGFUL_TEXT_SQL, register_sql_functions  # noqa: E402

TABLES = [
    ("entries", "journal entries"),
    ("projects", "Projects"),
    ("project_versions", "project versions"),
    ("reader_notes_scoped", "Reader's Notes"),
    ("calendar_events", "calendar events"),
    ("todos", "ToDos"),
    ("entry_revisions", "entry versions"),
    ("recovery_checkpoints", "recovery copies"),
    ("day_markers", "Day Markers"),
    ("settings", "settings"),
]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def tree_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def newest_mtime(path: Path):
    newest = None
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                stamp = (Path(root) / name).stat().st_mtime
            except OSError:
                continue
            if newest is None or stamp > newest:
                newest = stamp
    return newest


# Before the 2026-09 fix, an entry saved while its FIRST paragraph was empty
# was stored inside Qt's root-frame table, and every load + save added one
# more blank line at the top (see rich_editor.unwrap_root_frame). Entries
# like that may have gained blank lines the user never typed. This lists
# them; it changes nothing — only the user can tell which blank lines were
# deliberate.
_ROOT_WRAPPER_RE = re.compile(
    r"<body[^>]*>\s*<table[^>]*-qt-table-type:\s*root[^>]*>\s*<tr>\s*<td[^>]*>(.*)", re.S)
_PARAGRAPH_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.S)


def leading_blank_paragraphs(html: str) -> int:
    """How many empty paragraphs open a root-wrapped stored entry (0 if the
    entry is not in that shape)."""
    match = _ROOT_WRAPPER_RE.search(html or "")
    if not match:
        return 0
    count = 0
    for paragraph in _PARAGRAPH_RE.finditer(match.group(1)):
        attributes, inner = paragraph.group(1), paragraph.group(2)
        if "-qt-paragraph-type:empty" in attributes or re.fullmatch(r"\s*(<br\s*/?>)?\s*", inner):
            count += 1
        else:
            break
    return count


def report_leading_blank_lines(conn):
    rows = conn.execute(
        f"SELECT date, body_md FROM entries WHERE body_format='html' AND {MEANINGFUL_TEXT_SQL} "
        "ORDER BY date").fetchall()
    affected = [(date, n) for date, html in rows if (n := leading_blank_paragraphs(html))]
    if not affected:
        print(f"  {'blank-line check':<15} no written entry starts with blank lines in the old stored shape")
        return
    one = len(affected) == 1
    print(f"  {'blank-line check':<15} {len(affected)} written entr{'y starts' if one else 'ies start'} "
          "with blank lines stored in the old shape;")
    print("                  earlier versions may have added some of those lines. Nothing")
    print("                  has been changed — open each date and remove any you didn't type:")
    for date, n in affected:
        print(f"                    {date}  ({n} blank line{'s' if n != 1 else ''} at the top)")


def describe(path: Path, label: str):
    print(f"\n{label}")
    print(f"  path            {path}")
    if not path.exists():
        print("  status          does not exist")
        return
    print(f"  size on disk    {human_size(tree_size(path))}")
    stamp = newest_mtime(path)
    if stamp:
        print(f"  last written    {datetime.fromtimestamp(stamp):%Y-%m-%d %H:%M:%S}")

    try:
        contents = sorted(p.name for p in path.iterdir())
    except OSError as exc:
        print(f"  contents        could not be listed ({exc})")
        return
    print(f"  contents        {', '.join(contents) if contents else '(empty)'}")

    version = dm.read_version_file(path)
    if version:
        print(f"  migration record data_format_version="
              f"{version.get('data_format_version')}, "
              f"legacy_migration_completed={version.get('legacy_migration_completed')}")
        if version.get("migrated_from"):
            print(f"                  migrated from {version['migrated_from']}")
            print(f"                  at {version.get('migrated_at', 'unknown time')}")
    else:
        print("  migration record none")

    db_path = path / dm.DB_FILENAME
    encrypted = security.db_file_state(db_path) == "encrypted"
    locked = False
    cfg = security.load_config(path)
    if (path / security.CONFIG_FILE).is_file():
        backups = ("encrypted" if security.backups_encrypted(path) else
                   "PAUSED (encrypted chosen, no backup passphrase)"
                   if security.backups_paused_reason(path) else "not encrypted")
        print(f"  backups         {backups}; backup key "
              f"{'present' if (path / security.BACKUP_KEY_FILE).is_file() else 'absent'}")
    if encrypted:
        print(f"  encryption      database encrypted (SQLCipher); key file "
              f"{'present' if (path / security.DB_KEY_FILE).is_file() else 'MISSING'}")
        if cfg.get("keep_unencrypted_copies") or (path / security.MIRROR_DB).exists():
            print(f"  unencrypted copy {security.MIRROR_DB} "
                  f"{'present' if (path / security.MIRROR_DB).exists() else 'missing'} "
                  f"(keep unencrypted copies is "
                  f"{'on' if cfg.get('keep_unencrypted_copies') else 'off'})")
        locked = security.session.db_key is None or path != _unlocked_folder[0]
    elif db_path.is_file():
        print("  encryption      database not encrypted")
    if not db_path.is_file():
        print("  database        none")
    elif locked:
        print(f"  database        {human_size(db_path.stat().st_size)}, encrypted — "
              "run with --unlock to count what is in it")
    else:
        print(f"  database        {human_size(db_path.stat().st_size)}")
        try:
            conn = security.connect(db_path, readonly=True)
            conn.row_factory = None
            register_sql_functions(conn)     # the app's written-vs-blank rule
        except (security.SecurityError, *security.DB_ERRORS) as exc:
            print(f"  opens           NO ({exc})")
            conn = None
        if conn is not None:
            try:
                integrity = conn.execute("PRAGMA quick_check").fetchone()
                ok = integrity and str(integrity[0]).lower() == "ok"
                print(f"  integrity       {'ok' if ok else integrity}")
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                for table, pretty in TABLES:
                    if table not in names:
                        print(f"  {pretty:<15} table not present")
                        continue
                    count = conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
                    print(f"  {pretty:<15} {count}")
                if "entries" in names:
                    real = conn.execute(
                        f"SELECT COUNT(*) FROM entries WHERE {MEANINGFUL_TEXT_SQL}").fetchone()[0]
                    print(f"  {'with writing':<15} {real}")
                    row = conn.execute(
                        f"SELECT date FROM entries WHERE {MEANINGFUL_TEXT_SQL} "
                        "ORDER BY date DESC LIMIT 1").fetchone()
                    if row:
                        print(f"  most recent     {row[0]}")
                    report_leading_blank_lines(conn)
            except security.DB_ERRORS as exc:
                print(f"  database        could not be read ({exc})")
            finally:
                conn.close()

    if encrypted:
        print("  validates       checked by opening it (see above)" if not locked
              else "  validates       not checked — encrypted")
    else:
        try:
            dm.validate_data_dir(path)
            print("  validates       yes")
        except Exception as exc:                          # noqa: BLE001 - reporting
            print(f"  validates       NO — {exc}")

    verdict = {
        "installation": "real user data — jortle_claude would use it and never overwrite it",
        "scaffolding": "no user data — jortle_claude may set it aside to complete a migration",
        "ambiguous": "unreadable — jortle_claude will refuse to touch it and ask for help",
        "absent": "not there",
    }
    state = dm.classify_target(path)
    print(f"  jortle_claude sees it  {state}: {verdict[state]}")


def entry_lengths(path: Path) -> dict:
    """{date: characters of writing} for a folder, or {} if unreadable."""
    db_path = path / dm.DB_FILENAME
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return {}
    if security.db_file_state(db_path) == "encrypted" and path != _unlocked_folder[0]:
        return {}
    try:
        conn = security.connect(db_path, readonly=True)
    except (security.SecurityError, *security.DB_ERRORS):
        return {}
    try:
        return {
            row[0]: row[1] for row in conn.execute(
                "SELECT date, LENGTH(TRIM(COALESCE(body_text, ''))) FROM entries")
            if row[1]
        }
    except security.DB_ERRORS:
        return {}
    finally:
        conn.close()


def compare(current: Path, others: list):
    """Is there writing in any other folder that the current one lacks?

    This is the question worth answering before deleting anything: not "is
    this folder old", but "does it hold a single character I would miss".
    Anything reported here should be recovered — recover_entry.py takes a
    folder's journal.db as readily as a backup zip — before that folder goes.
    """
    if not others:
        return
    print("\n" + "=" * 72)
    print("Is anything in those folders missing from the one jortle_claude uses?")
    print("=" * 72)

    mine = entry_lengths(current)
    if security.db_file_state(current / dm.DB_FILENAME) == "encrypted" \
            and current != _unlocked_folder[0]:
        print("  The current journal is encrypted. Run with --unlock to compare it")
        print("  with these folders.")
        return
    if not mine and not current.exists():
        print("  The current folder does not exist, so everything else is worth keeping.")
        return

    anything = False
    for other in others:
        theirs = entry_lengths(other)
        if not theirs:
            print(f"\n{other.name}\n  no readable journal in it — nothing to lose")
            continue
        missing = []
        for date, length in sorted(theirs.items()):
            here = mine.get(date, 0)
            if length > here:
                missing.append((date, length, here))
        if not missing:
            print(f"\n{other.name}\n  nothing here that the current folder does not "
                  f"already have. Safe to delete.")
        else:
            anything = True
            print(f"\n{other.name}\n  *** HAS WRITING THE CURRENT FOLDER DOES NOT ***")
            for date, length, here in missing:
                if here:
                    print(f"    {date}  {length} chars here vs {here} in the current folder")
                else:
                    print(f"    {date}  {length} chars here, ABSENT from the current folder")
            print("  Recover these before deleting this folder:")
            print(f"    python recover_entry.py \"{other / dm.DB_FILENAME}\" <date> --write")

    if not anything:
        print("\n  Nothing is missing. Every folder above is safe to delete once you")
        print("  are happy with how the app is running.")


# The one folder this run was given the passphrase for (--unlock).
_unlocked_folder = [None]


def main():
    print("=" * 72)
    print("jortle_claude data folder diagnostic — read-only, changes nothing")
    print("=" * 72)
    print(f"\nApplication data root: {dm._data_root()}")

    target = dm.target_dir()
    if "--unlock" in sys.argv[1:] and security.is_encrypted_install(target):
        if not security.unlock_with_plain_keys(target):
            import getpass
            try:
                security.unlock(target, getpass.getpass("Database passphrase: "))
            except security.WrongPassphrase:
                print("That passphrase is not correct; continuing without it.")
        if security.session.db_key:
            _unlocked_folder[0] = target
    describe(target, "CURRENT (what jortle_claude uses now)")

    for candidate in dm.legacy_candidates():
        if candidate.exists():
            describe(candidate, "LEGACY (an older installation)")

    root = target.parent
    leftovers = []
    try:
        leftovers = sorted(
            p for p in root.iterdir()
            if p.is_dir() and (p.name.startswith((".jortle-migrat", f"{dm.STAGING_PREFIX}-migrat"))
                               or p.name.startswith((".jortle-restoring-", f"{dm.STAGING_PREFIX}-restoring-"))
                               or p.name.startswith(f"{target.name}.replaced-")
                               or p.name.startswith(f"{target.name}.before-restore-")
                               # safety copies made by restores in the builds
                               # that still used a legacy folder name
                               or any(p.name.startswith(f"{legacy}.before-restore-")
                                      for legacy in dm.LEGACY_DIR_NAMES))
        )
    except OSError:
        pass
    for leftover in leftovers:
        describe(leftover, "LEFTOVER (from an earlier migration or restore)")

    compare(target, [c for c in dm.legacy_candidates() if c.exists()] + leftovers)

    print("\n" + "=" * 72)
    print("What jortle_claude would do on the next launch")
    print("=" * 72)
    legacy = dm.find_legacy_dir()
    state = dm.classify_target(target)
    if state == "installation":
        print(f"  Use {target}.")
        if legacy:
            print(f"  Keep {legacy} untouched as a backup, and not re-import it.")
    elif state == "ambiguous":
        print(f"  Refuse to start, and report that {target} could not be read.")
        print("  Nothing would be moved, merged or deleted.")
    elif legacy is not None:
        print(f"  Copy {legacy}")
        print(f"    to a temporary folder, validate it, and rename it to {target}.")
        if state == "scaffolding" and target.exists():
            print(f"  {target} holds no user data, so it would be set aside first")
            print("    (renamed, never deleted) and its files put back afterwards.")
        print(f"  {legacy} would be left exactly as it is.")
    else:
        print(f"  Start a new installation at {target}.")

    if leftovers:
        print("\n  Leftover folders listed above are not used by jortle_claude. Once you")
        print("  have confirmed your journal is intact, they are safe to delete.")
    print()


if __name__ == "__main__":
    main()
