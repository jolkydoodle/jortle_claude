"""Round 27 — migrating when the destination already exists (Parts 1-7, 22).

This is the failure actually reported:

    [WinError 183] Cannot create a file when that file already exists:
    '...\\.jortle-migrating-20260916-145043' -> '...\\Jortle'

The copy was made and validated; the promotion assumed it could rename onto
a name nothing else had taken. So these cases all set the destination up
deliberately before migrating, and check that each one is resolved the way
Part 4 says rather than by a rename that throws:

    A  destination holds a real migrated installation  -> use it, keep ours aside
    B  destination holds only scaffolding              -> set aside, migrate, restore
    C  destination holds something unreadable          -> touch nothing, report

Each case gets its own fake filesystem root, and every case re-checks that
the legacy directory came through untouched, because that is the promise the
whole module exists to keep.
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def fresh_root():
    from app import data_migration
    root = pathlib.Path(tempfile.mkdtemp(prefix="jortle-r27-"))
    (root / "home").mkdir()
    isolation.point_at(root)
    data_migration.reset_for_tests()
    return root


def build_legacy(path: pathlib.Path, marker="legacy writing"):
    """A populated installation in the OLD folder, made by the real app."""
    from app.database import Database
    path.mkdir(parents=True, exist_ok=True)
    db = Database(str(path / "journal.db"))
    db.upsert_entry("2026-01-05", title="A legacy day",
                    body_md=f'<p><span style=" font-weight:700;">bold</span> {marker}</p>',
                    body_format="html", body_text=f"bold {marker}")
    project = db.create_project("Legacy novel", category="Fiction")
    db.save_project_content(project.id, "<p>chapter one</p>", content_text="chapter one")
    db.save_notes("date", "2026-01-05", "<p>a reader's note</p>", content_text="a reader's note")
    db.create_event(date="2026-01-05", start_minute=540, end_minute=600, title="Legacy meeting")
    marker_row = db.create_day_marker("Legacy marker", "#123456")
    db.upsert_entry("2026-01-06", body_md="<p>second</p>", body_format="html",
                    body_text="second", tag=str(marker_row.id), tag_color=marker_row.color)
    db.set_setting("color_scheme", json.dumps({
        "name": "Dark", "background": "#1e1f22", "panel": "#26272b",
        "text": "#e6e6e6", "accent": "#5aa0f0", "border": "#3a3b40"}))
    db.set_setting("ui_font_size", "13")
    db.set_setting("window_geometry", "AdnQywADAAAAAAAAAAAAAA==")
    db.close()
    (path / "attachments").mkdir(exist_ok=True)
    (path / "attachments" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return path


def fingerprint(path):
    """Every file except the database, by content."""
    out = {}
    for f in sorted(path.rglob("*")):
        if f.is_dir() or f.name.startswith("journal.db"):
            continue
        out[str(f.relative_to(path))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def row_counts(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables}
    finally:
        con.close()


# ============================== Part 4B: the destination is scaffolding
print("\n--- Part 4B: a 'models' folder created before migration (the reported case) ---")
root = fresh_root()
from app import data_migration                                    # noqa: E402
from app.database import Database                                 # noqa: E402

legacy = build_legacy(root / "DailyJournal")
legacy_files = fingerprint(legacy)
legacy_rows = row_counts(legacy / "journal.db")

# Exactly what the removed AI feature used to do: ask for a subfolder of the
# data directory, which created the data directory on the way.
target = data_migration.target_dir()
(target / "models").mkdir(parents=True)
(target / "models" / "Qwen_Qwen3.5-9B-Q4_K_S.gguf").write_bytes(b"pretend weights")
check("the destination exists before migration, as it did on the affected machine",
      target.is_dir())
check("classified as scaffolding, not as an installation",
      data_migration.classify_target(target) == "scaffolding")

data_dir = data_migration.resolve_data_dir()
result = data_migration.migration_result()
check(f"migration ran anyway (case {result.case})", result.case == "B")
check("the destination did not block it", data_dir == target)
check("the journal is there", Database().get_entry("2026-01-05") is not None)

db = Database()
check("rich text survived",
      "font-weight" in (db.get_entry("2026-01-05").body_md or ""))
check("Projects survived", [p.title for p in db.list_projects()] == ["Legacy novel"])
check("Reader's Notes survived",
      db.get_notes("date", "2026-01-05").content_text == "a reader's note")
check("calendar events survived",
      [e.title for e in db.get_events("2026-01-05")] == ["Legacy meeting"])
check("Day Markers survived",
      "Legacy marker" in [m.name for m in db.list_day_markers()])
check("theme survived", json.loads(db.get_setting("color_scheme"))["name"] == "Dark")
check("window geometry survived", db.get_setting("window_geometry") is not None)
check("attachments survived", (data_dir / "attachments" / "photo.png").is_file())
db.close()

check("the scaffolding was preserved, not deleted",
      (target / "models" / "Qwen_Qwen3.5-9B-Q4_K_S.gguf").is_file())
check("no leftover .replaced- folder with anything in it",
      not [p for p in target.parent.iterdir()
           if p.name.startswith(f"{target.name}.replaced-") and any(p.iterdir())])
check("the legacy folder is untouched (files)", fingerprint(legacy) == legacy_files)
check("the legacy folder is untouched (rows)",
      row_counts(legacy / "journal.db") == legacy_rows)

print("\n--- ...and a second launch does not migrate again ---")
before = len(Database().all_entries())
data_migration.reset_for_tests()
data_migration.resolve_data_dir()
second = data_migration.migration_result()
check(f"second launch takes case D, not B (got {second.case})", second.case == "D")
check("no entries were duplicated", len(Database().all_entries()) == before)

# ============================== Part 4B: an entirely empty destination
print("\n--- Part 4B: an empty destination must not block migration ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
data_migration.target_dir().mkdir(parents=True)
data_dir = data_migration.resolve_data_dir()
check(f"migrated (case {data_migration.migration_result().case})",
      data_migration.migration_result().case == "B")
check("the journal is there", Database().get_entry("2026-01-05") is not None)

# ============================== Part 4B: an initialised but empty database
print("\n--- Part 4B: a destination database with nothing in it is scaffolding ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
target = data_migration.target_dir()
target.mkdir(parents=True)
Database(str(target / "journal.db")).close()      # schema, no content
check("an empty-but-valid database reads as scaffolding",
      data_migration.classify_target(target) == "scaffolding")
data_migration.resolve_data_dir()
check(f"so migration still runs (case {data_migration.migration_result().case})",
      data_migration.migration_result().case == "B")
check("and the legacy journal is what ended up there",
      Database().get_entry("2026-01-05") is not None)

# ============================== Part 4A: the destination is already migrated
print("\n--- Part 4A: a real installation at the destination is used, not replaced ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal", marker="the old copy")
target = data_migration.target_dir()
build_legacy(target, marker="the already-migrated copy")
data_migration.write_version_file(target, migrated_from=legacy)
legacy_files = fingerprint(legacy)
legacy_rows = row_counts(legacy / "journal.db")

data_dir = data_migration.resolve_data_dir()
result = data_migration.migration_result()
check(f"case D — both exist, the destination wins (got {result.case})", result.case == "D")
check("the destination's own data is what loads, not a re-import",
      "the already-migrated copy" in (Database().get_entry("2026-01-05").body_text or ""))
check("the legacy folder was not re-imported over it",
      "the old copy" not in (Database().get_entry("2026-01-05").body_text or ""))
check("entries were not duplicated", len(Database().all_entries()) == 2)
check("the legacy folder is untouched (files)", fingerprint(legacy) == legacy_files)
check("the legacy folder is untouched (rows)",
      row_counts(legacy / "journal.db") == legacy_rows)

print("\n--- Part 4A: losing a race mid-migration is not an error ---")
# The destination appears WHILE the copy is in flight — which is what two
# Jortle processes starting together does, and what the WinError was.
root = fresh_root()
legacy = build_legacy(root / "DailyJournal", marker="mine")
target = data_migration.target_dir()

real_copy_tree = data_migration._copy_tree


def copy_then_lose_the_race(source, destination):
    real_copy_tree(source, destination)
    build_legacy(target, marker="the other process")
    data_migration.write_version_file(target, migrated_from=legacy)


data_migration._copy_tree = copy_then_lose_the_race
try:
    data_dir = data_migration.resolve_data_dir()
finally:
    data_migration._copy_tree = real_copy_tree

check("it did not raise", data_dir == target)
check("the winner's data is what is in place",
      "the other process" in (Database().get_entry("2026-01-05").body_text or ""))
check("our copy was set aside rather than thrown away",
      any(p.name.startswith(".jortle_claude-migration-superseded-")
          for p in target.parent.iterdir()))
check("the legacy folder still exists", legacy.is_dir())

# ============================== Part 4C: ambiguous
print("\n--- Part 4C: an unreadable destination is a recovery condition ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
target = data_migration.target_dir()
target.mkdir(parents=True)
(target / "journal.db").write_bytes(b"this is not a database, but it is named like one")
legacy_files = fingerprint(legacy)
legacy_rows = row_counts(legacy / "journal.db")

check("classified as ambiguous", data_migration.classify_target(target) == "ambiguous")
raised = None
try:
    data_migration.resolve_data_dir()
except data_migration.MigrationError as exc:
    raised = exc
check("migration refuses rather than guessing", raised is not None)
check("the message says where the data still is",
      raised is not None and str(legacy) in raised.user_message())
check("the destination was not overwritten",
      (target / "journal.db").read_bytes().startswith(b"this is not a database"))
check("the legacy folder is untouched (files)", fingerprint(legacy) == legacy_files)
check("the legacy folder is untouched (rows)",
      row_counts(legacy / "journal.db") == legacy_rows)
check("nothing was merged into the destination",
      not (target / "attachments").exists())

# ============================== Part 17 / 22: nothing creates the target early
print("\n--- Parts 17 and 22: no subsystem creates the destination first ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
target = data_migration.target_dir()
check("nothing has created it yet", not target.exists())

from app import paths                                             # noqa: E402
check("paths has no get_models_dir() to create one", not hasattr(paths, "get_models_dir"))
callers = []
for module in (pathlib.Path(__file__).resolve().parent.parent / "app").glob("*.py"):
    for line in module.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue          # the note in paths.py explaining its removal
        if "get_models_dir" in stripped:
            callers.append(f"{module.name}: {stripped}")
check("no module in the package asks for a models directory: " + "; ".join(callers),
      not callers)

import importlib                                                  # noqa: E402
for name in ("app.theme", "app.saving", "app.calendar_prefs", "app.date_state",
             "app.event_render", "app.ui_util", "app.date_links"):
    importlib.import_module(name)
check("importing the app's modules does not create the data directory",
      not target.exists())

data_migration.resolve_data_dir()
check("only the migration creates it", target.is_dir())
check("and it holds the migrated journal",
      Database().get_entry("2026-01-05") is not None)

print("\n--- the model file is not dragged along ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
(legacy / "models").mkdir()
(legacy / "models" / "big.gguf").write_bytes(b"x" * 4096)
data_dir = data_migration.resolve_data_dir()
check("the migrated folder has no models directory",
      not (data_dir / "models").exists())
check("...and the original is still where it was",
      (legacy / "models" / "big.gguf").is_file())
check("the journal itself came across",
      Database().get_entry("2026-01-05") is not None)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
