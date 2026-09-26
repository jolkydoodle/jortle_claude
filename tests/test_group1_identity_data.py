"""Group 1 — the jortle_claude identity, and the data paths it touches.

* the data folder is jortle_claude's own, and the previous `Jortle` folder
  (and the older `DailyJournal`) are migrated from by COPYING — the source
  is never changed, and an existing jortle_claude folder is never overwritten;
* launcher, build, installer and backup names follow the identity;
* old backups (any file name, old manifest) still restore;
* backup → restore keeps version history and recovery copies;
* migration's "is there a journal here?" counts every user table;
* the static archive uses the one written-vs-blank rule.
"""
import json
import os
import pathlib
import sqlite3
import sys
import zipfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g1-id-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import data_migration as dm  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def make_install(folder: pathlib.Path, marker: str):
    """A realistic older installation: a real database with an entry."""
    folder.mkdir(parents=True)
    (folder / "attachments" / "2026" / "01").mkdir(parents=True)
    (folder / "attachments" / "2026" / "01" / "p.png").write_bytes(b"png")
    from app.database import Database
    db = Database(folder / "journal.db")
    db.upsert_entry("2026-01-05", body_md=f"<p>{marker}</p>", body_format="html", body_text=marker)
    db.close()


def fingerprint(folder: pathlib.Path):
    return sorted((str(p.relative_to(folder)), p.stat().st_size)
                  for p in folder.rglob("*") if p.is_file())


print("\n--- names ---")
from app.paths import APP_NAME, DISPLAY_NAME  # noqa: E402
from app.backup import default_backup_filename  # noqa: E402
check("the application is called jortle_claude", DISPLAY_NAME == "jortle_claude" == APP_NAME)
check("its data folder is jortle_claude", dm.APP_DIR_NAME == "jortle_claude"
      and dm.target_dir() == root / "jortle_claude")
check("Jortle is the first legacy source, DailyJournal still recognised",
      dm.LEGACY_DIR_NAMES[:2] == ("Jortle", "DailyJournal"))
check("backups are named jortle_claude-backup-*",
      default_backup_filename().startswith("jortle_claude-backup-"))
check("the entry point is jortle_claude.py", (REPO / "jortle_claude.py").is_file()
      and not (REPO / "jortle.py").exists())
launcher = (REPO / "run_jortle_claude.bat").read_text(encoding="utf-8", errors="replace")
check("the launcher runs jortle_claude.py", "python jortle_claude.py" in launcher)
build = (REPO / "build_windows.bat").read_text(encoding="utf-8", errors="replace")
check("the build produces jortle_claude.exe", '--name "jortle_claude"' in build and "jortle_claude.py" in build)
iss = (REPO / "installer.iss").read_text(encoding="utf-8", errors="replace")
check("the installer is jortle_claude with its own AppId",
      '#define MyAppName "jortle_claude"' in iss
      and "6F2B6E2B-6A3E-4C2E-9C3A-0F6E2B0F2C1A" not in iss)
shortcut = (REPO / "create_desktop_shortcut.bat").read_text(encoding="utf-8", errors="replace")
check("the shortcut script creates jortle_claude.lnk and never deletes a Jortle shortcut",
      "jortle_claude.lnk" in shortcut and "Jortle.lnk\"; if" not in shortcut)
app_sources = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "app").glob("*.py"))
visible = [line.strip() for line in app_sources.splitlines()
           if '"' in line and "Jortle" in line.split("#")[0]
           and "LEGACY_DIR_NAMES" not in line and "Deliberately NOT" not in line
           and "formerly" not in line]
check("no user-visible string still calls the app Jortle", not visible, "; ".join(visible[:3]))


print("\n--- migrating from the previous Jortle folder ---")
jortle = root / "Jortle"
make_install(jortle, "from the Jortle folder")
before = fingerprint(jortle)
dm.reset_for_tests()
data_dir = dm.resolve_data_dir()
check("the data folder is now jortle_claude", data_dir == root / "jortle_claude")
from app.database import Database  # noqa: E402
db = Database()
check("the journal came across", "from the Jortle folder" in db.get_entry("2026-01-05").body_text)
check("photos came across", (data_dir / "attachments" / "2026" / "01" / "p.png").is_file())
db.close()
check("the Jortle folder is untouched (same files, same sizes)", fingerprint(jortle) == before)
record = json.loads((data_dir / "data_version.json").read_text(encoding="utf-8"))
check("the migration is recorded", str(jortle) in str(record.get("migrated_from")))

print("\n--- rerunning, and a newer Jortle folder later ---")
db = Database()
db.upsert_entry("2026-01-06", body_md="<p>written in jortle_claude</p>", body_format="html",
                body_text="written in jortle_claude")
db.close()
make_install(root / "Jortle-other", "x")                      # irrelevant sibling
dm.reset_for_tests()
check("a second launch uses the existing jortle_claude folder", dm.resolve_data_dir() == data_dir)
db = Database()
check("and does not re-import or overwrite it",
      "written in jortle_claude" in db.get_entry("2026-01-06").body_text)
db.close()
check("the Jortle folder is still untouched", fingerprint(jortle) == before)

print("\n--- DailyJournal only (older installs) ---")
import shutil  # noqa: E402
shutil.rmtree(data_dir)
shutil.rmtree(jortle)
dj = root / "DailyJournal"
make_install(dj, "from DailyJournal")
dm.reset_for_tests()
data_dir = dm.resolve_data_dir()
db = Database()
check("DailyJournal still migrates into jortle_claude",
      data_dir == root / "jortle_claude" and "from DailyJournal" in db.get_entry("2026-01-05").body_text)


print("\n--- backups: old ones restore, new ones keep history ---")
from app.backup import export_backup, restore_backup  # noqa: E402
db.add_entry_revision("2026-01-05", "<p>older</p>", "html", "older", "test")
db.add_recovery_checkpoint("date", "2026-01-05", "<p>big</p>", "html", "big", removed_chars=1500)
db.close()
backup = export_backup(root / "b" / default_backup_filename())
manifest = json.loads(zipfile.ZipFile(backup.zip_path).read("manifest.json"))
check("a new backup says it is jortle_claude's", manifest.get("app") == "jortle_claude")
renamed = backup.zip_path.with_name("DailyJournal-backup-2025-12-01_120000.zip")
backup.zip_path.rename(renamed)
db = Database()
db.delete_entry_revisions("2026-01-05")
db._conn.execute("DELETE FROM recovery_checkpoints")
db._conn.commit()
db.close()
restore_backup(renamed)
db = Database()
check("a backup with an old-style name restores (identified by contents)",
      "from DailyJournal" in db.get_entry("2026-01-05").body_text)
check("version history came back with it", len(db.list_entry_revisions("2026-01-05")) == 1)
check("recovery copies came back with it", len(db.list_recovery_checkpoints()) == 1)
db.close()
old_style = root / "b" / "old.zip"
with zipfile.ZipFile(old_style, "w") as zf:
    zf.writestr("manifest.json", '{"app": "DailyJournal"}')
    legacy_db = root / "b" / "legacy.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute("CREATE TABLE entries (date TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', "
                 "body_md TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                 "tag TEXT, tag_color TEXT)")
    conn.execute("INSERT INTO entries VALUES ('2025-05-05', '', 'an old markdown entry', 'x', 'x', NULL, NULL)")
    conn.commit()
    conn.close()
    zf.write(legacy_db, "journal.db")
restore_backup(old_style)
db = Database()
check("a pre-history backup restores and gains the new tables empty",
      db.get_entry("2025-05-05") is not None and db.list_entry_revisions("2025-05-05") == []
      and db.schema_version() == 4)
db.close()


print("\n--- migration: every user table counts as a journal ---")
for table, insert in (
    ("todos", "INSERT INTO todos (date, text, done, position, created_at, updated_at) "
              "VALUES ('2026-01-01', 't', 0, 0, 'x', 'x')"),
    ("entry_revisions", "INSERT INTO entry_revisions (date, body, body_format, body_text, "
                        "content_hash, reason, created_at) VALUES ('2026-01-01', 'b', 'html', 'b', "
                        "'h', 'r', 'x')"),
):
    folder = root / f"only-{table}"
    folder.mkdir()
    fresh = Database(folder / "journal.db")
    fresh._conn.execute(insert)
    fresh._conn.commit()
    fresh.close()
    check(f"a database holding only {table} is real user data", dm.has_user_content(folder) is True)
folder = root / "fresh-only"
folder.mkdir()
Database(folder / "journal.db").close()
check("a freshly created database is still scaffolding", dm.has_user_content(folder) is False)


print("\n--- the archive uses the same rule ---")
from app.archive import export_archive  # noqa: E402
db = Database()
db.upsert_entry("2026-08-01", body_md="<p><br /></p><p><br /></p>", body_format="html", body_text="\n")
db.upsert_entry("2026-08-02", body_md="<p>​</p>", body_format="html", body_text="​")
db.upsert_entry("2026-08-03", body_md='<p><img src="2026/01/p.png" /></p>', body_format="html",
                body_text="￼")
summary = export_archive(db, root / "archive")
pages = {p.stem for p in (root / "archive" / "entries").glob("*.html")}
check("blank entries are not archived as entries", not {"2026-08-01", "2026-08-02"} & pages)
check("an image-only entry is archived", "2026-08-03" in pages)
db.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
