"""Round 26 — the data-directory rename and its migration (Parts 3-11, 33).

Runs the spec's own migration script against realistic legacy data built by
the app's own Database class, plus the failure case: a copy that doesn't
validate must leave the legacy directory untouched and must not start Jortle
on an empty journal.

Each case gets its own fake filesystem root (XDG_DATA_HOME + HOME), so a
case can't see another case's leftovers.
"""
import hashlib
import json
import os
import pathlib
import shutil
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
    """A private HOME + data root, with the migration's cached decision cleared."""
    from app import data_migration
    root = pathlib.Path(tempfile.mkdtemp(prefix="jortle-r26m-"))
    (root / "home").mkdir()
    isolation.point_at(root)
    data_migration.reset_for_tests()
    return root


def build_legacy(path: pathlib.Path):
    """A populated installation in the OLD folder, made by the real app."""
    from app.database import Database
    path.mkdir(parents=True, exist_ok=True)
    db = Database(str(path / "journal.db"))
    db.upsert_entry("2026-01-05", title="A legacy day",
                    body_md='<p><span style=" font-weight:700;">bold</span> legacy writing</p>',
                    body_format="html", body_text="bold legacy writing")
    db.upsert_entry("2026-01-06", body_md="<p>second day</p>",
                    body_format="html", body_text="second day")
    project = db.create_project("Legacy novel", category="Fiction")
    db.save_project_content(project.id, "<p>chapter one</p>", content_text="chapter one")
    db.save_notes("date", "2026-01-05", "<p>a reader's note</p>", content_text="a reader's note")
    db.save_notes("project", str(project.id), "<p>project note</p>", content_text="project note")
    db.create_event(date="2026-01-05", start_minute=540, end_minute=600, title="Legacy meeting")
    marker = db.create_day_marker("Legacy marker", "#123456")
    db.upsert_entry("2026-01-06", tag=str(marker.id), tag_color=marker.color)
    db.set_setting("color_scheme", json.dumps({
        "name": "Dark", "background": "#1e1f22", "panel": "#26272b",
        "text": "#e6e6e6", "accent": "#5aa0f0", "border": "#3a3b40"}))
    db.set_setting("ui_font_size", "13")
    db.set_setting("ai_reflection_enabled", "1")
    db.set_setting("ai_reflection_model", "qwen3.5-9b")
    db.set_setting("window_geometry", "AdnQywADAAAAAAAAAAAAAA==")
    db.close()
    (path / "attachments").mkdir(exist_ok=True)
    (path / "attachments" / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    (path / "reflection_prompts.yaml").write_text("stage1_analysis: hello\n", encoding="utf-8")
    return path


# ===================================================== Case B: the migration
print("\n--- Part 33: a populated legacy folder, no Jortle folder yet ---")
root = fresh_root()
from app import data_migration                                    # noqa: E402
from app.database import Database                                 # noqa: E402

legacy = build_legacy(root / "DailyJournal")


def fingerprint(path: pathlib.Path, skip_db=False):
    """Every file's path and contents.

    The migration checkpoints the legacy WAL before copying (so a copy can't
    miss committed data sitting in journal.db-wal), and that is a write to
    journal.db / -wal / -shm. It is the ONLY write the migration is allowed
    to make to the legacy folder, so `skip_db` excludes exactly those three
    and everything else must still match byte for byte. The legacy database's
    *contents* are checked separately, by reopening it and counting rows.
    """
    out = {}
    for p in sorted(legacy.rglob("*")):
        if p.is_dir():
            continue
        if skip_db and p.name.startswith("journal.db"):
            continue
        out[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def legacy_row_counts():
    con = sqlite3.connect(f"file:{legacy / 'journal.db'}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables}
    finally:
        con.close()


legacy_names_before = sorted(p.name for p in legacy.rglob("*"))
legacy_files_before = fingerprint(legacy, skip_db=True)
legacy_rows_before = legacy_row_counts()
target = data_migration.target_dir()
check("the Jortle folder does not exist yet", not target.exists())

data_dir = data_migration.resolve_data_dir()
result = data_migration.migration_result()
check(f"migration ran (case {result.case})", result.case == "B")
check(f"the data folder is now Jortle's ({data_dir.name})", data_dir == target)
check("it was created", target.is_dir())
check("the legacy folder still exists", legacy.is_dir())
check("nothing was added to or removed from the legacy folder",
      sorted(p.name for p in legacy.rglob("*")) == legacy_names_before)
check("every legacy file except the database is byte-for-byte identical",
      fingerprint(legacy, skip_db=True) == legacy_files_before)
check("the legacy database still holds exactly the same rows",
      legacy_row_counts() == legacy_rows_before)
check("no temporary migration folder was left behind",
      not any(p.name.startswith((".jortle-migrating", ".jortle_claude-migrating"))
              for p in target.parent.iterdir()))

db = Database()
entry = db.get_entry("2026-01-05")
check("journal entries loaded", entry is not None and entry.body_text == "bold legacy writing")
check("rich-text formatting survived",
      "font-weight" in entry.body_md and "bold" in entry.body_md)
check("Projects loaded",
      [p.title for p in db.list_projects()] == ["Legacy novel"])
check("project categories survived", db.list_projects()[0].category == "Fiction")
check("Reader's Notes loaded (date scope)",
      db.get_notes("date", "2026-01-05").content_text == "a reader's note")
check("Reader's Notes loaded (project scope)",
      db.get_notes("project", str(db.list_projects()[0].id)).content_text == "project note")
check("calendar events loaded",
      [e.title for e in db.get_events("2026-01-05")] == ["Legacy meeting"])
check("Day Markers loaded",
      "Legacy marker" in [m.name for m in db.list_day_markers()])
check("the marked day is still marked",
      db.get_entry("2026-01-06").tag_color == "#123456")
check("theme survived", json.loads(db.get_setting("color_scheme"))["name"] == "Dark")
check("UI font size survived", db.get_setting("ui_font_size") == "13")
check("AI Suggestion settings survived",
      db.get_setting("ai_reflection_enabled") == "1"
      and db.get_setting("ai_reflection_model") == "qwen3.5-9b")
check("window geometry survived", db.get_setting("window_geometry") is not None)
check("attachments came across", (data_dir / "attachments" / "photo.png").is_file())
check("the AI prompts file came across", (data_dir / "reflection_prompts.yaml").is_file())
db.close()

print("\n--- Part 9: the migration is idempotent ---")
entries_before = len(Database().all_entries())
data_migration.reset_for_tests()
second = data_migration.resolve_data_dir()
result2 = data_migration.migration_result()
check(f"a second launch does not migrate again (case {result2.case})", result2.case == "D")
check("...even though the legacy folder is still there", legacy.is_dir())
db = Database()
check("no data was duplicated", len(db.all_entries()) == entries_before)
check("Jortle's folder is authoritative", second == target)
db.close()
record = data_migration.read_version_file(target)
check("the migration is recorded in the data folder",
      record.get("legacy_migration_completed") is True
      and record.get("data_format_version") == data_migration.DATA_FORMAT_VERSION)
check("and it records where the data came from",
      record.get("migrated_from") == str(legacy))

# ===================================================== Case C: new install
print("\n--- Part 8, Case C: neither folder exists ---")
fresh_root()
result = None
data_dir = data_migration.resolve_data_dir()
result = data_migration.migration_result()
check("a brand-new installation is created", result.case == "C")
check("with no migration recorded",
      data_migration.read_version_file(data_dir).get("legacy_migration_completed") is False)

# ============================== the empty-target trap the spec calls out
print("\n--- Part 4: an empty Jortle folder must not block the migration ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
data_migration.target_dir().mkdir(parents=True)          # as a careless startup would
result = None
data_migration.resolve_data_dir()
result = data_migration.migration_result()
check("migration still runs (the empty folder is not evidence of anything)",
      result.case == "B")
db = Database()
check("and the legacy entries are there", db.get_entry("2026-01-05") is not None)
db.close()

# =================================== Part 7: validation failure is safe
print("\n--- Part 7: a copy that doesn't validate ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
legacy_bytes = (legacy / "journal.db").read_bytes()

real_validate = data_migration.validate_data_dir
data_migration.validate_data_dir = lambda path, expect_attachments=False: (_ for _ in ()).throw(
    ValueError("simulated: the journal entries table could not be read"))
try:
    data_migration.resolve_data_dir()
    check("migration failure is reported, not swallowed", False)
except data_migration.MigrationError as exc:
    check("migration failure raises rather than starting empty", True)
    check("the message names the real cause",
          "journal entries table" in exc.message)
    check("the message tells the user where their data still is",
          str(legacy) in exc.user_message())
    check("the legacy database is untouched",
          (legacy / "journal.db").read_bytes() == legacy_bytes)
    check("the Jortle folder was NOT created",
          not data_migration.target_dir().exists())
    check("the failed copy is quarantined for diagnosis, not deleted",
          exc.quarantine is not None and exc.quarantine.exists())
finally:
    data_migration.validate_data_dir = real_validate

print("\n--- Part 7: validation actually reads the data ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
checks = data_migration.validate_data_dir(legacy, expect_attachments=True)
check(f"it reports what it verified ({len(checks)} checks)", len(checks) >= 6)
check("including the journal entries", any("journal entries" in c for c in checks))
check("including rich text", any("rich text" in c for c in checks))
corrupt = root / "corrupt"
corrupt.mkdir()
(corrupt / "journal.db").write_bytes(b"this is not a database")
try:
    data_migration.validate_data_dir(corrupt)
    check("a corrupt database fails validation", False)
except ValueError as exc:
    check(f"a corrupt database fails validation ({exc})", True)

# ================================ Part 8 Case E: an unusable target
print("\n--- Part 8, Case E: the Jortle folder exists but is not usable ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
target = data_migration.target_dir()
target.mkdir(parents=True)
(target / "journal.db").write_bytes(b"not a database at all")
try:
    data_migration.resolve_data_dir()
    check("an unusable target with legacy data alongside is refused", False)
except data_migration.MigrationError:
    check("an unusable target with legacy data alongside is refused", True)
    check("neither location is modified",
          (target / "journal.db").read_bytes() == b"not a database at all"
          and (legacy / "journal.db").is_file())

# ============================= Part 11: only app-managed paths are rewritten
print("\n--- Part 11: stored paths ---")
root = fresh_root()
legacy = build_legacy(root / "DailyJournal")
from app.database import Database as DB                            # noqa: E402
db = DB(str(legacy / "journal.db"))
db.set_setting("some_app_file", str(legacy / "models" / "model.gguf"))
db.set_setting("user_backup_folder", "D:\\\\Archive\\\\Backups")
db.close()
data_migration.resolve_data_dir()
db = DB()
check("a path inside the data folder was repointed at Jortle's folder",
      db.get_setting("some_app_file", "").startswith(str(data_migration.target_dir())))
check("a path the user chose elsewhere is left exactly alone",
      db.get_setting("user_backup_folder") == "D:\\\\Archive\\\\Backups")
db.close()

# ============================= Part 1: launchers point at real files
print("\n--- Part 1: renamed launchers ---")
repo = pathlib.Path(__file__).resolve().parent.parent
check("the entry point is jortle_claude.py", (repo / "jortle_claude.py").is_file())
check("the old entry point is gone", not (repo / "main.py").exists())
check("run_jortle_claude.bat exists", (repo / "run_jortle_claude.bat").is_file())
for bat in repo.glob("*.bat"):
    text = bat.read_text(encoding="utf-8", errors="replace")
    # The shortcut script mentions the old name twice on purpose: it deletes
    # the pre-rename Desktop shortcut, which would otherwise be left pointing
    # at an .exe that no longer exists. Those two lines are the exception.
    stripped = "\n".join(
        line for line in text.splitlines()
        if "Remove-Item" not in line and "used to build as" not in line)
    check(f"{bat.name} does not reference the old app name",
          "DailyJournal" not in stripped and "Daily Journal" not in stripped)
    for referenced in ("main.py", "jortle.py", "jortle_claude.py"):
        if referenced in text:
            check(f"{bat.name} references {referenced}, which exists",
                  (repo / referenced).is_file())
check("the build script produces jortle_claude.exe",
      '--name "jortle_claude"' in (repo / "build_windows.bat").read_text(encoding="utf-8", errors="replace"))
check("run_jortle_claude.bat launches jortle_claude.py",
      "python jortle_claude.py" in (repo / "run_jortle_claude.bat").read_text(encoding="utf-8", errors="replace"))

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
