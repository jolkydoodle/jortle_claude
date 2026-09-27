"""Group 2 — the backup and encryption features through the real window.

Dialogs that would wait for a person are answered by replacing their exec()
or the module function that shows them; everything else is the real window,
the real database and real files. Screenshots of the new windows are saved
to $JORTLE_SHOTS when that is set.
"""
import json
import os
import pathlib
import sqlite3
import sys
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g2-ui-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import backup, backup_dialog, security  # noqa: E402
import app.main_window as mw  # noqa: E402
from app.database import Database  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

failures = []
PASS = "violet harbour lantern"            # database passphrase
BPASS = "copper kettle meadow"             # backup passphrase (separate)
NO_ACTIONS = {name: (lambda *a: True) for name in (
    "back_up_now", "set_up_database_encryption", "set_up_backup_encryption",
    "change_database_passphrase", "change_backup_passphrase",
    "keep_backups_unencrypted", "set_keep_copies")}
SHOTS = os.environ.get("JORTLE_SHOTS")


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


def type_text(editor_widget, text):
    edit = editor_widget.text_edit
    edit.setFocus()
    QTest.keyClicks(edit, text)
    settle()


def shot(widget, name):
    if SHOTS:
        pathlib.Path(SHOTS).mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(pathlib.Path(SHOTS) / f"{name}.png"))


infos, warnings, criticals = [], [], []
QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[2] if len(a) > 2 else a))
QMessageBox.critical = staticmethod(lambda *a, **k: criticals.append(a[2] if len(a) > 2 else a))
warning_answer = {"value": QMessageBox.Ok}
QMessageBox.warning = staticmethod(
    lambda *a, **k: warnings.append(a[2] if len(a) > 2 else a) or warning_answer["value"])
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

# With autosave off (a new installation's default), leaving unsaved work
# asks Save / Discard / Cancel. This test answers Save.
from app.saving import SAVE  # noqa: E402
unsaved_asked = []
mw.ask_unsaved = lambda _parent, what: unsaved_asked.append(what) or SAVE

data = get_data_dir()
win = MainWindow()
win.show()
settle()

print("\n--- the File menu routes ---")
labels = [a.text() for a in win.menuBar().actions()[0].menu().actions() if a.text()]
check("File has Back Up Now, Export Backup…, Restore…, Backups & Security…",
      all(x in labels for x in ("Back Up Now", "Export Backup…", "Restore from Backup…",
                                 "Backups && Security…")), labels)

print("\n--- Back Up Now, unencrypted ---")
win.selected_date.set("2026-06-01")
settle()
type_text(win.editor, "Written before encryption")
infos.clear()
win.back_up_now_action.trigger()
settle()
records = backup.scan_backups()
check("a checked backup is in the default folder",
      len(records) == 1 and records[0].verified and not records[0].encrypted)
with __import__("zipfile").ZipFile(backup.backup_dir() / records[0].file) as zf:
    mem = security.open_plain_bytes(zf.read("journal.db"))
check("...really in the backup's database",
      "Written before encryption" in mem.execute(
          "SELECT body_text FROM entries WHERE date='2026-06-01'").fetchone()[0])
check("the success message says it was checked and not encrypted",
      infos and "checked" in infos[-1] and "Encrypted: no" in infos[-1])

print("\n--- Export Backup… uses its own remembered folder ---")
export_dir = root / "exports"
export_dir.mkdir()
seen_start = {}


def fake_save(parent, title, start, pattern):
    seen_start["start"] = start
    return str(export_dir / "mine.zip"), ""


QFileDialog.getSaveFileName = staticmethod(fake_save)
win._export_backup()
settle()
check("the first export starts in the backup folder",
      pathlib.Path(seen_start["start"]).parent == backup.backup_dir())
check("the export was written where chosen", (export_dir / "mine.zip").is_file())
win._export_backup()
check("the next export starts where the last one went",
      pathlib.Path(seen_start["start"]).parent == export_dir)
check("an export elsewhere still counts as the last successful backup",
      backup.status().last_success is not None)

print("\n--- the Backups & Security window, unencrypted ---")
dlg = backup_dialog.BackupsSecurityDialog(dict(NO_ACTIONS), parent=win)
dlg.show()
settle()
status_text = dlg.status_label.text()
check("it shows the folder, the last backup, checked, and 'NOT encrypted'",
      str(backup.backup_dir()) in dlg.folder_label.text() and "checked" in status_text
      and "NOT encrypted" in status_text, status_text)
check("database: 'Not encrypted', with a button to encrypt it",
      "Not encrypted" in dlg.db_label.text() and not dlg.db_setup_button.isHidden()
      and dlg.db_change_button.isHidden())
check("backups: 'Not chosen yet', with Encrypt Backups and Keep Unencrypted",
      "Not chosen yet" in dlg.backup_enc_label.text()
      and not dlg.backup_setup_button.isHidden() and not dlg.backup_plain_button.isHidden())
check("automatic backups are shown as paused until the choice is made",
      "PAUSED" in status_text)
check("same-drive note is shown when the folder shares the drive",
      "same drive" in dlg.same_drive_label.text() and not dlg.same_drive_label.isHidden())
check("every line of the status is laid out at full height (nothing cut off)",
      all(lbl.height() >= lbl.heightForWidth(lbl.width()) for lbl in
          (dlg.status_label, dlg.same_drive_label, dlg.db_label, dlg.backup_enc_label)),
      [(lbl.height(), lbl.heightForWidth(lbl.width())) for lbl in
       (dlg.status_label, dlg.same_drive_label, dlg.db_label, dlg.backup_enc_label)])
shot(dlg, "backups_security_unencrypted")
dlg.retention_combo.setCurrentIndex(1)
dlg.retention_spin.setValue(5)
check("choosing 'keep newest' saves the number",
      security.load_config(data)["keep_automatic"] == 5)
dlg.retention_combo.setCurrentIndex(0)
check("'keep all' saves None", security.load_config(data)["keep_automatic"] is None)
dlg.close()

print("\n--- Settings shows the backup folder and opens the window ---")
opened = []
sd = SettingsDialog(win.db, on_change=lambda: None, parent=win,
                    open_backups=lambda: opened.append(1))
sd.show()
settle()
check("Settings shows the backup folder, whole, in a read-only field",
      sd.backup_folder.text() == str(backup.backup_dir()) and sd.backup_folder.isReadOnly())
sd.backup_folder.selectAll()
sd.backup_folder.copy()
check("...whose path can be selected and copied in full",
      QApplication.clipboard().text() == str(backup.backup_dir()))
check("...with the encryption state beside it",
      "Database: not encrypted" in sd.backup_status.text())
sd._manage_backups()
check("Manage… opens Backups & Security", opened == [1])
shot(sd, "settings_backups_row")
sd.close()

print("\n--- encrypting the database from the window (its own passphrase) ---")
type_text(win.editor, " plus unsaved words")
check("(the editor has unsaved text)", win.editor.is_dirty())
answers = {"text": PASS}
seen_dialogs = []


def fake_passphrase_exec(self):
    seen_dialogs.append((self.subject, self.same is not None))
    self.first.setText(answers["text"])
    self.second.setText(answers["text"])
    self.understand.setChecked(True)
    self._check()
    return self.result() or QDialog.Rejected


backup_dialog.NewPassphraseDialog.exec = fake_passphrase_exec
infos.clear()
old_db = win.db
check("the database set-up succeeds", win._set_up_database_encryption() is True)
settle()
check("the journal database is now encrypted",
      security.db_file_state(data / "journal.db") == "encrypted")
check("...and backups are still not encrypted (independent)",
      not security.backups_encrypted(data))
check("no 'same passphrase' option was offered (nothing else is encrypted yet)",
      seen_dialogs[-1] == (backup_dialog.DATABASE, False))
check("the window has a new, working database", win.db is not old_db and win.db.encrypted)
stale = [type(o).__name__ for o in win._database_holders()
         if getattr(o, "db", None) is not None and o.db is not win.db]
check("nothing holds the closed database", not stale, stale)
check("unsaved work was asked about first (autosave is off)", unsaved_asked)
check("the unsaved words were saved into the encrypted journal",
      "plus unsaved words" in win.db.get_entry("2026-06-01").body_text)
check("the editor still shows them", "plus unsaved words" in win.editor.plain_text())
check("the user was told backups are still not encrypted, and older copies are kept",
      infos and "NOT encrypted" in infos[-1] and "not deleted" in infos[-1], infos[-1:])

print("\n--- encrypting backups from the window (a separate passphrase) ---")
answers["text"] = BPASS
infos.clear()
check("the backup set-up succeeds", win._set_up_backup_encryption() is True)
check("'use the same passphrase as the database' was offered",
      seen_dialogs[-1] == (backup_dialog.BACKUPS, True))
check("backups are now encrypted, with their own passphrase",
      security.backups_encrypted(data)
      and security.passphrase_opens(data, "backup", BPASS)
      and not security.passphrase_opens(data, "backup", PASS))
check("an encrypted backup was made straight away, and fully checked",
      any(r.encrypted and r.verified and r.verification == backup.VERIFIED_FULL
          for r in backup.scan_backups()))
check("the user was told, and told older backups are not changed",
      infos and "encrypted" in infos[-1] and "not changed" in infos[-1], infos[-1:])
type_text(win.editor, " after")
win._save_active_workspace()
settle()
from PySide6.QtGui import QTextCursor  # noqa: E402
win.editor.text_edit.moveCursor(QTextCursor.End)
type_text(win.editor, " end")
win._save_active_workspace()
settle()
check("typing and saving keep working", win.db.get_entry("2026-06-01").body_text.endswith("end"))

print("\n--- autosave stays fast with an encrypted journal ---")
import random  # noqa: E402
random.seed(7)
words = "the quick brown fox jumps over a lazy dog while writing about spectroscopy".split()
huge = " ".join(random.choice(words) for _ in range(1500))
win._request_date("2026-03-20")
settle()
win.editor.text_edit.setPlainText(huge)
win._save_current_entry()
win._request_date("2026-03-21")
win._request_date("2026-03-20")
settle()
timings = []
for _ in range(4):
    type_text(win.editor, " a few more words")
    win._autosave_timer.stop()
    started = time.perf_counter()
    win._save_current_entry(automatic=True)
    timings.append((time.perf_counter() - started) * 1000)
check("an autosave in a 1,500-word paragraph is well under 250 ms (SQLCipher)",
      max(timings) < 250, f"{[round(t) for t in timings]} ms")
print(f"  info  autosave times: {[round(t, 1) for t in timings]} ms")

print("\n--- the Backups & Security window, encrypted ---")
dlg = backup_dialog.BackupsSecurityDialog(dict(NO_ACTIONS), parent=win)
dlg.show()
settle()
check("it says the database is encrypted with the database passphrase",
      "database passphrase" in dlg.db_label.text())
check("it says backups are encrypted with the backup passphrase",
      "backup passphrase" in dlg.backup_enc_label.text())
check("it warns that photos in the data folder are not encrypted",
      "not" in dlg.db_label.text() and "Photos" in dlg.db_label.text())
check("it offers the two Change Passphrase buttons and Keep unencrypted copies",
      dlg.db_setup_button.isHidden() and dlg.backup_setup_button.isHidden()
      and not dlg.db_change_button.isHidden() and not dlg.backup_change_button.isHidden()
      and not dlg.keep_copies.isHidden() and not dlg.keep_copies.isChecked())
check("it lists the unencrypted backup made before encryption",
      "Unencrypted backup" in dlg.leftovers_label.text())
shot(dlg, "backups_security_encrypted")
dlg.close()

print("\n--- keep unencrypted copies, through the window's command ---")
warnings.clear()
warning_answer["value"] = QMessageBox.Cancel
check("Cancel at the warning changes nothing",
      win._set_keep_unencrypted_copies(True) is False
      and not (data / security.MIRROR_DB).exists())
check("the warning says the copies are readable without the passphrase",
      warnings and "not protected" in warnings[-1].replace("\n", " "))
warning_answer["value"] = QMessageBox.Ok
check("OK turns it on", win._set_keep_unencrypted_copies(True) is True)
check("the unencrypted copy exists", security.db_file_state(data / security.MIRROR_DB) == "plain")
win._request_date("2026-06-02")
settle()
type_text(win.editor, "Mirror me please")
win._save_active_workspace()
settle()
win._refresh_unencrypted_copy()
con = sqlite3.connect(str(data / security.MIRROR_DB))
row = con.execute("SELECT body_text FROM entries WHERE date='2026-06-02'").fetchone()
con.close()
check("a save reaches the unencrypted copy on the next refresh",
      row is not None and row[0] == "Mirror me please")
QTest.qWait(4500)                           # the real timer, not a direct call
type_text(win.editor, " and again")
win._save_active_workspace()
QTest.qWait(4500)
settle()
con = sqlite3.connect(str(data / security.MIRROR_DB))
row = con.execute("SELECT body_text FROM entries WHERE date='2026-06-02'").fetchone()
con.close()
check("...and the timer does it by itself within a few seconds",
      row is not None and row[0].endswith("and again"), row)
summary = win._run_backup("manual")
check("a backup now makes both an encrypted file and an unencrypted twin",
      summary and summary.encrypted and summary.plain_path and summary.plain_path.is_file())

print("\n--- restore: warning, suppression, and what suppression does NOT skip ---")
confirm_calls = []


def fake_confirm(parent, text):
    confirm_calls.append(text)
    return True, True                    # Yes, and "don't show again"


backup_dialog.confirm_restore = fake_confirm
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(summary.zip_path), ""))
win._request_date("2026-06-02")
settle()
type_text(win.editor, " typed after the backup")
win._save_active_workspace()
settle()
safety_before = set(data.parent.glob("jortle_claude.before-restore-*"))
criticals.clear()
win._import_backup()
settle()
check("the warning was shown once", len(confirm_calls) == 1)
check("'don't show again' was remembered", security.load_config(data)["restore_warning"] is False)
check("the restore worked", not criticals and
      not win.db.get_entry("2026-06-02").body_text.endswith("typed after the backup"), criticals)
check("the restored journal is encrypted and the copy is kept up to date",
      win.db.encrypted and security.db_file_state(data / security.MIRROR_DB) == "plain")
safety_after = set(data.parent.glob("jortle_claude.before-restore-*"))
check("a safety copy of the previous journal was made", len(safety_after) == len(safety_before) + 1)

bad = root / "bad.jcbackup"
raw = bytearray(summary.zip_path.read_bytes())
raw[len(raw) // 2] ^= 0xFF
bad.write_bytes(bytes(raw))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(bad), ""))
confirm_calls.clear()
criticals.clear()
win._import_backup()
settle()
check("with the warning off, a damaged backup is still checked and refused",
      criticals and not confirm_calls)
check("...and no safety copy or change happened",
      set(data.parent.glob("jortle_claude.before-restore-*")) == safety_after)

QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(summary.zip_path), ""))
confirm_calls.clear()
win._import_backup()
settle()
check("with the warning off, the next restore does not ask", not confirm_calls)
check("...but the safety copy is still made (unconditional)",
      len(set(data.parent.glob("jortle_claude.before-restore-*"))) == len(safety_after) + 1)
security.update_config(data, restore_warning=True)

print("\n--- turning unencrypted copies off ---")
check("Off is accepted", win._set_keep_unencrypted_copies(False) is True)
check("the unencrypted database copy is gone", not (data / security.MIRROR_DB).exists())
check("the twin of the backup is gone, the encrypted backup stays",
      not summary.plain_path.exists() and summary.zip_path.exists())
check("the kept unencrypted backups were listed for the user",
      infos and "no encrypted twin" in infos[-1])

print("\n--- the readable archive: warning first, no database, its own folder ---")
warnings.clear()
warning_answer["value"] = QMessageBox.Cancel
picked = {}
QFileDialog.getExistingDirectory = staticmethod(
    lambda *a, **k: (picked.setdefault("start", a[2]), str(root / "archive-here"))[1])
win._export_archive()
check("the warning comes first, and Cancel stops before the folder picker",
      warnings and "NOT encrypted" in warnings[-1] and "start" not in picked)
warning_answer["value"] = QMessageBox.Ok
(root / "archive-here").mkdir()
win._export_archive()
settle()
check("the folder picker starts outside the backup folder",
      pathlib.Path(picked["start"]) != backup.backup_dir())
archive_root = root / "archive-here" / __import__("app.archive", fromlist=["x"]).default_archive_dirname()
check("the archive was written", (archive_root / "index.html").is_file())
check("it contains no journal database", not list(archive_root.rglob("*.db")))
check("nothing was written into the backup folder by the archive",
      not [p for p in backup.backup_dir().iterdir() if p.is_dir()])

print("\n--- first launch: the storage question, then the automatic backup ---")
win.editor.mark_clean()
win.projects_widget.editor.mark_clean()
win.close()
settle()
security.session.clear()
# A new, unencrypted installation.
case = root / "second"
case.mkdir()
isolation.point_at(case)
from app import data_migration  # noqa: E402
data_migration.reset_for_tests()
win2 = MainWindow()
win2.show()
settle()
asked = []


def fake_choice_exec(self):
    asked.append(1)
    self.choice = backup_dialog.StorageChoiceDialog.UNENCRYPTED
    return QDialog.Accepted


backup_dialog.StorageChoiceDialog.exec = fake_choice_exec
mw.StorageChoiceDialog.exec = fake_choice_exec
check("a window built directly asks nothing and backs up nothing",
      not asked and not backup.backup_dir().exists())
win2.start_background_tasks()
settle(900)
check("start_background_tasks asks how backups should be stored", asked == [1])
check("the answer is remembered", security.load_config(get_data_dir())["storage_choice"] == "unencrypted")
recs = backup.scan_backups()
check("an automatic backup was made because none existed",
      len(recs) == 1 and recs[0].kind == "automatic" and recs[0].verified)
check("the hourly check is running", win2._backup_timer.isActive())
win2._startup_backup_tasks()
check("a second check does not ask again or back up again",
      asked == [1] and len(backup.scan_backups()) == 1)
check("no 'overdue' indicator after a fresh backup", win2._backup_indicator.isHidden())
# Make the next automatic backup due, into a folder that cannot be created on
# any OS: its parent is an ordinary file inside the isolated root. (The old
# "/proc/definitely-not-writable/x" is C:\proc\… on Windows, which any user can
# create, so the backup succeeded — outside the isolated root.)
blocker = root / "an-ordinary-file"
blocker.write_bytes(b"not a folder")
unwritable = blocker / "x"
security.update_config(get_data_dir(), last_backup=None, backup_dir=str(unwritable))
criticals_before = len(criticals)
win2._automatic_backup_if_due()
settle()
check("an automatic backup that fails is reported without a modal error",
      "failed" in win2.statusBar().currentMessage().lower()
      and len(criticals) == criticals_before)
check("the failure is kept for the Backups & Security window",
      security.load_config(get_data_dir())["last_error"])
check("and the status bar shows a backup warning", not win2._backup_indicator.isHidden())
check("the failed backup created nothing (its parent is still a file)",
      blocker.is_file() and not unwritable.exists())
shot(win2, "main_window_backup_failed_indicator")
win2.editor.mark_clean()
win2.projects_widget.editor.mark_clean()
win2.close()
settle()

print("\n--- the unlock window ---")
case3 = root / "third"
case3.mkdir()
isolation.point_at(case3)
data_migration.reset_for_tests()
d3 = data_migration.resolve_data_dir()
Database().close()
# This time the "same passphrase for both" choice.
security.set_up_database_encryption(d3, PASS)
security.set_up_backup_encryption(d3, PASS)
security.session.clear()
dlg = backup_dialog.UnlockDialog(d3)
dlg.show()
dlg.passphrase.setText("wrong wrong wrong")
dlg._try()
settle()
check("a wrong passphrase keeps the window open with a message",
      dlg.result() != QDialog.Accepted and "not correct" in dlg.error.text())
shot(dlg, "unlock_wrong_passphrase")
dlg.passphrase.setText(PASS)
dlg._try()
check("the right one accepts and unlocks", dlg.result() == QDialog.Accepted
      and security.session.db_key is not None)
check("with the same passphrase chosen for both, the backup key is unlocked too",
      security.session.identity is not None)
win3 = MainWindow()
check("the main window opens the encrypted journal", win3.db.encrypted)
win3.editor.mark_clean()
win3.close()
settle()

print("\n--- the real application, started as a user would ---")
import subprocess  # noqa: E402
from app.single_instance import SingleInstance  # noqa: E402
security.session.clear()
security.unlock(d3, PASS)
db3 = Database()
backup.set_keep_unencrypted_copies(db3, True)
db3.close()
security.update_config(d3, storage_choice="encrypted", automatic_backups="off")
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
probe = SingleInstance()
started = False
deadline = time.monotonic() + 40
while time.monotonic() < deadline and proc.poll() is None:
    if probe.notify_running_instance():
        started = True
        break
    time.sleep(0.3)
check("with unencrypted copies kept, it starts without asking for the passphrase", started,
      proc.stderr.read()[-400:] if proc.poll() is not None else "")
proc.kill()
proc.wait(timeout=30)


def reopen_after_kill():
    """Windows releases a killed process's file locks asynchronously, after
    wait() has returned, so the first reopen can meet an I/O error (bug 22).
    Waits for it for at most 5 s. JORTLE_TEST_NO_REOPEN_WAIT=1 switches the
    wait off, to show that the app's own retry is enough (WF-14)."""
    if os.environ.get("JORTLE_TEST_NO_REOPEN_WAIT") == "1":
        return Database()
    deadline = time.monotonic() + 5
    while True:
        try:
            return Database()
        except (security.SecurityError, *security.DB_ERRORS):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


reopened = reopen_after_kill()
check("the journal reopens straight after the app was killed", reopened.encrypted)
backup.set_keep_unencrypted_copies(reopened, False)
reopened.close()
security.session.clear()
proc = subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(6)
check("without them, it waits at the unlock window rather than starting or failing",
      proc.poll() is None)
proc.kill()
proc.wait(timeout=30)
(d3 / security.DB_KEY_FILE).rename(d3 / "moved-away.age")
check("with the key file missing, startup refuses rather than starting empty",
      security.startup_state(d3) == "missing-key")
before = sorted((p.name, p.stat().st_size) for p in d3.iterdir())
proc = subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(6)
alive = proc.poll() is None          # waiting on its error message, not running
proc.kill()
proc.wait(timeout=30)
check("...the real app stops at its error message and changes nothing",
      alive and sorted((p.name, p.stat().st_size) for p in d3.iterdir()) == before)
(d3 / "moved-away.age").rename(d3 / security.DB_KEY_FILE)
check("with the key file back, startup unlocks normally", security.startup_state(d3) == "encrypted")

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
