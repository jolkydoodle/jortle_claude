"""Round 21 smoke test: simulates a pre-round-21 database (no body_format/
body_text/content_format/content_text columns) with real legacy data, then
verifies Database() migrates it safely, preserves all existing data exactly,
creates the pre-migration backup file, and that new saves work correctly
alongside old rows."""
import shutil
import sqlite3
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

tmpdir = Path(tempfile.mkdtemp())
db_path = tmpdir / "journal.db"

# ---- build an old-schema database by hand (pre-round-21 shape) ----
OLD_SCHEMA = """
CREATE TABLE entries (
    date TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    body_md TEXT NOT NULL DEFAULT '',
    tag TEXT,
    tag_color TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    text TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    category TEXT
);
CREATE TABLE project_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    first_edit_at TEXT NOT NULL,
    last_edit_at TEXT NOT NULL
);
CREATE TABLE project_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    label TEXT,
    kind TEXT NOT NULL DEFAULT 'manual',
    saved_at TEXT NOT NULL
);
"""

conn = sqlite3.connect(str(db_path))
conn.executescript(OLD_SCHEMA)
conn.execute(
    "INSERT INTO entries (date, title, body_md, tag, tag_color, created_at, updated_at) "
    "VALUES ('2026-01-01', 'New Year', '**Bold** old markdown entry\\n\\nwith a blank line above', "
    "'happy', '#ffcc00', '2026-01-01T09:00:00', '2026-01-01T09:00:00')"
)
conn.execute(
    "INSERT INTO projects (id, title, content_md, created_at, updated_at, archived, category) "
    "VALUES (1, 'My Novel', '# Chapter 1\\n\\nOnce upon a time.', '2026-01-01T09:00:00', "
    "'2026-01-01T09:00:00', 0, 'Fiction')"
)
conn.execute(
    "INSERT INTO project_versions (project_id, content_md, label, kind, saved_at) "
    "VALUES (1, '# Chapter 1\\n\\nDraft.', NULL, 'auto', '2025-12-31T09:00:00')"
)
conn.commit()
conn.close()

print("=== old DB built, now opening with new Database() ===")
from app.database import Database  # noqa: E402

db = Database(db_path)

# 1. migration ran and columns exist
entry = db.get_entry("2026-01-01")
assert entry is not None, "existing entry lost!"
assert entry.title == "New Year", entry.title
assert "Bold" in entry.body_md, "existing body content lost!"
assert entry.body_format == "markdown", f"expected markdown, got {entry.body_format!r}"
assert entry.body_text == entry.body_md, "body_text should backfill == body_md"
# Day markers became named records: entries.tag now holds the marker's id
# rather than a built-in key, and the marker carries the name and colour the
# date used to display under. The colour stays on the row either way, so the
# check is that nothing was LOST, not that the representation is unchanged.
assert entry.tag_color == "#ffcc00", "tag colour lost!"
_markers = {m.id: m for m in db.list_day_markers()}
assert entry.tag.isdigit() and int(entry.tag) in _markers, f"tag not migrated: {entry.tag!r}"
_marker = _markers[int(entry.tag)]
assert _marker.color == "#ffcc00", f"marker colour lost: {_marker.color}"
assert _marker.name.lower() == "happy", f"marker name lost: {_marker.name}"
print("OK: legacy entry preserved with body_format='markdown', body_text backfilled")

project = db.get_project(1)
assert project is not None
assert project.title == "My Novel"
assert "Chapter 1" in project.content_md
assert project.content_format == "markdown"
assert project.content_text == project.content_md
assert project.category == "Fiction"
print("OK: legacy project preserved with content_format='markdown'")

versions = db.get_project_versions(1)
assert len(versions) == 1
assert versions[0].content_format == "markdown"
print("OK: legacy project version preserved with content_format='markdown'")

# 2. backup file was created
backup_path = db_path.with_name(f"{db_path.stem}.pre-round21-richtext.db")
assert backup_path.exists(), "pre-migration backup was not created!"
print(f"OK: pre-migration backup created at {backup_path}")

# backup contains the OLD schema (no body_format column) -- i.e. it's a
# real snapshot from before migration, not a copy taken after.
backup_conn = sqlite3.connect(str(backup_path))
backup_cols = {r[1] for r in backup_conn.execute("PRAGMA table_info(entries)").fetchall()}
assert "body_format" not in backup_cols, "backup should reflect PRE-migration schema"
backup_conn.close()
print("OK: backup reflects pre-migration schema (taken before ALTER TABLE ran)")

# 3. new-format save works and coexists with the migrated old row
db.upsert_entry("2026-01-02", body_md="<p>New html entry</p>", body_format="html",
                body_text="New html entry")
entry2 = db.get_entry("2026-01-02")
assert entry2.body_format == "html"
assert entry2.body_text == "New html entry"
print("OK: new html-format entry saves and loads alongside the migrated markdown one")

# 4. re-opening the (already-migrated) database doesn't re-run the migration
# or duplicate/overwrite the backup with a newer state
backup_mtime_before = backup_path.stat().st_mtime
db.close()
db2 = Database(db_path)
backup_mtime_after = backup_path.stat().st_mtime
assert backup_mtime_before == backup_mtime_after, "backup was overwritten on a second open!"
entry_again = db2.get_entry("2026-01-01")
assert entry_again.body_format == "markdown"
print("OK: re-opening an already-migrated database is a no-op (no duplicate backup, no re-migration)")

# 5. save_project_content / add_version / restore_project_version round trip
db2.save_project_content(1, "<p>New HTML chapter 1</p>", content_format="html",
                          content_text="New HTML chapter 1")
proj_after = db2.get_project(1)
assert proj_after.content_format == "html"
assert proj_after.content_text == "New HTML chapter 1"
# saving should have auto-snapshotted the PRE-edit (markdown) version, preserving its format
versions_after = db2.get_project_versions(1)
formats = {v.content_format for v in versions_after}
assert "markdown" in formats, f"expected an auto-snapshotted markdown version, got formats={formats}"
print("OK: save_project_content upgrades to html and auto-snapshots the prior markdown version correctly")

restored = db2.restore_project_version(1, versions_after[0].id)
assert restored is not None
restored_content, restored_format = restored
print(f"OK: restore_project_version returns (content, format) = (..., {restored_format!r})")

db2.close()
print("\nALL DATABASE MIGRATION TESTS PASSED")
shutil.rmtree(tmpdir)
