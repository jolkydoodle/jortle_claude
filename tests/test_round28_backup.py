"""Round 28 — backup and restore actually work (a data-loss report).

A backup was exported, a Tuesday journal entry deleted, and the backup
restored — and the entry did not come back. Two independent bugs, each of
which alone is enough to lose writing.

1. **The export copied a live WAL database as a plain file.** The database
   runs in WAL mode, so a committed write sits in `journal.db-wal` until
   SQLite checkpoints it into the main file on its own schedule. Zipping
   `journal.db` alone captures the state as of the last checkpoint. On a
   young database nothing has been checkpointed at all, and the exported
   file has no tables in it.

2. **The restore aborted silently partway through.** It built a new
   Database and repointed every widget at it from a hand-written list —
   which had gone stale and missed `calendar_prefs`. The first call into
   that object hit a closed connection and raised, before the editors were
   reloaded. No error was shown, the pre-restore (emptied) document stayed
   in the editor, and the next save put it back over the restored entry.

So this suite drives the real menu actions in the real window, in his
order, and then checks the database after a close — because "it looked
right on screen" is exactly what was true while the data was being lost.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

import isolation
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r28-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r28-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.backup import RestoreError, export_backup, restore_backup  # noqa: E402
from app.database import Database  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_data_dir  # noqa: E402

failures = []
DATE = "2026-09-15"
TEXT = ("Today I'm exploring cyber security on my second sick-day. "
        "The IMP is very similar to a modern router. ") * 12


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=6):
    for _ in range(times):
        app.processEvents()


def entries_in(zip_path, date=DATE):
    """What the zip's database ACTUALLY contains — read as SQLite, not as a
    file that exists. A zip with a journal.db in it is not a backup."""
    with zipfile.ZipFile(zip_path) as zf:
        target = pathlib.Path(tempfile.mkdtemp()) / "journal.db"
        target.write_bytes(zf.read("journal.db"))
    con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "entries" not in tables:
            return None
        row = con.execute("SELECT body_text FROM entries WHERE date=?", (date,)).fetchone()
        return row[0] if row else ""
    finally:
        con.close()


# Silence the dialogs; the file pickers answer with paths this test chooses.
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
_critical_messages = []
QMessageBox.critical = staticmethod(
    lambda *a, **k: _critical_messages.append(str(a[2]) if len(a) > 2 else str(a)))

# The restore warning has a "don't show again" box, so it is its own dialog.
from app import backup_dialog  # noqa: E402
backup_dialog.confirm_restore = lambda *a, **k: (True, False)

win = MainWindow()
win.show()
settle()

# ======================================================= the export
print("\n--- a backup contains what was written, not what was checkpointed ---")
win.selected_date.set(DATE)
settle()
win.editor.text_edit.textCursor().insertText(TEXT)
win._save_active_workspace()
settle()
written = len(win.db.get_entry(DATE).body_text or "")
check(f"the entry is saved ({written} chars)", written > 500)

zip_path = pathlib.Path(tempfile.mkdtemp()) / "backup.zip"
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(zip_path), ""))
win._export_backup()
settle()
check("the backup file was written", zip_path.is_file())

in_zip = entries_in(zip_path)
check("the zip's database has an entries table at all", in_zip is not None)
check(f"the entry is IN the backup ({len(in_zip or '')} chars)",
      in_zip is not None and len(in_zip) == written)

# This is the specific shape of the original bug: a database young enough
# that nothing has been checkpointed yet.
print("\n--- ...including on a brand-new database, where nothing is checkpointed ---")
fresh_dir = pathlib.Path(tempfile.mkdtemp())
fresh_db = Database(str(fresh_dir / "journal.db"))
fresh_db.upsert_entry("2026-01-01", body_md="<p>brand new</p>", body_format="html",
                      body_text="brand new")
wal = fresh_dir / "journal.db-wal"
check("SQLite really is holding it in the write-ahead log",
      wal.is_file() and wal.stat().st_size > 0)
from app.backup import _snapshot_database  # noqa: E402

snap = fresh_dir / "snapshot.db"
_snapshot_database(fresh_dir / "journal.db", snap)
con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
row = con.execute("SELECT body_text FROM entries WHERE date='2026-01-01'").fetchone()
check("the snapshot still has the entry", row is not None and row[0] == "brand new")
check("and the snapshot needs no sidecar files to be read",
      con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal")
con.close()
fresh_db.close()

# ======================================================= the restore
print("\n--- the reported sequence: delete the entry, then restore the backup ---")
cursor = win.editor.text_edit.textCursor()
cursor.select(QTextCursor.Document)
cursor.removeSelectedText()
settle()
win._save_active_workspace()
settle()
check("the entry is now empty", not win.db.has_journal_entry(DATE))
check("and the calendar indicator is gone",
      DATE not in win.calendar_panel.calendar._entry_dates)

_critical_messages.clear()
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(zip_path), ""))
win._import_backup()
settle()

check("the restore reported no error: " + "; ".join(_critical_messages),
      not _critical_messages)
check(f"the editor shows the entry again ({len(win.editor.plain_text())} chars)",
      len(win.editor.plain_text()) >= written - 1)
check("the database has it back",
      len(win.db.get_entry(DATE).body_text or "") == written)
check("the calendar indicator is back",
      DATE in win.calendar_panel.calendar._entry_dates)
check("the editor is not left dirty, so nothing will overwrite it",
      not win.editor.is_dirty())

print("\n--- the object the stale list forgot ---")
check("calendar_prefs was repointed at the live database",
      win.calendar_prefs.db is win.db)
stale = [type(o).__name__ for o in win._database_holders()
         if getattr(o, "db", None) is not None and o.db is not win.db]
check("nothing at all is still holding an old database: " + ", ".join(sorted(set(stale))),
      not stale)
# The call that actually blew up, made directly.
win._apply_settings()
settle()
check("a settings change after a restore does not raise", True)

print("\n--- and it survives being closed, which is where it was lost ---")
win.editor.mark_clean()
win.projects_widget.editor.mark_clean()
win.close()
settle()
after = Database()
check(f"the entry is still there after closing "
      f"({len(after.get_entry(DATE).body_text or '')} chars)",
      len(after.get_entry(DATE).body_text or "") == written)
after.close()

# ======================================================= failure paths
print("\n--- a backup that is not one is refused, and changes nothing ---")
win2 = MainWindow()
win2.show()
settle()
win2.selected_date.set("2026-10-01")
settle()
win2.editor.text_edit.textCursor().insertText("Writing that must not be lost.")
win2._save_active_workspace()
settle()
before = win2.db.get_entry("2026-10-01").body_text

bad_zip = pathlib.Path(tempfile.mkdtemp()) / "bad.zip"
with zipfile.ZipFile(bad_zip, "w") as zf:
    zf.writestr("manifest.json", '{"app": "DailyJournal"}')
    zf.writestr("journal.db", "this is not a database")

_critical_messages.clear()
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(bad_zip), ""))
win2._import_backup()
settle()
check("the user is told it failed: " + "; ".join(m[:60] for m in _critical_messages),
      bool(_critical_messages))
check("the existing journal is untouched",
      win2.db.get_entry("2026-10-01").body_text == before)
check("the app is still usable — the database is open",
      win2.db.get_setting("color_scheme", "unset") is not None)
check("no half-restored data directory was left",
      (get_data_dir() / "journal.db").is_file())
check("no staging folder was left behind",
      not [p for p in get_data_dir().parent.iterdir()
           if p.name.startswith((".jortle-restoring-", ".jortle_claude-restoring-"))])

print("\n--- a backup with no journal in it is refused, either shape ---")
# Shape 1: the file the OLD export produced from a young WAL database — a
# real database, readable, with no `entries` table because everything was
# still in the write-ahead log that never got copied.
tableless = pathlib.Path(tempfile.mkdtemp()) / "journal.db"
con = sqlite3.connect(str(tableless))
con.execute("CREATE TABLE something_else (x INTEGER)")
con.commit()
con.close()
check("it is a real database, just not a journal one", tableless.stat().st_size > 0)

tableless_zip = pathlib.Path(tempfile.mkdtemp()) / "tableless.zip"
with zipfile.ZipFile(tableless_zip, "w") as zf:
    zf.writestr("manifest.json", '{"app": "DailyJournal"}')
    zf.write(tableless, arcname="journal.db")
raised = None
try:
    restore_backup(tableless_zip)
except RestoreError as exc:
    raised = exc
check("restore_backup refuses it rather than swapping it in", raised is not None)
check(f"and the message says why ({raised})",
      raised is not None and "entries" in str(raised).lower())

# Shape 2: a database file that was created and never written to at all.
empty_zip = pathlib.Path(tempfile.mkdtemp()) / "empty.zip"
empty_db = pathlib.Path(tempfile.mkdtemp()) / "journal.db"
sqlite3.connect(str(empty_db)).close()
with zipfile.ZipFile(empty_zip, "w") as zf:
    zf.writestr("manifest.json", '{"app": "DailyJournal"}')
    zf.write(empty_db, arcname="journal.db")
raised2 = None
try:
    restore_backup(empty_zip)
except RestoreError as exc:
    raised2 = exc
check(f"an empty database file is refused too ({raised2})", raised2 is not None)

check("the real data is still in place after both refusals",
      win2.db.get_entry("2026-10-01") is not None
      and Database().get_entry("2026-10-01") is not None)

win2.editor.mark_clean()
win2.projects_widget.editor.mark_clean()
win2.close()
settle()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
