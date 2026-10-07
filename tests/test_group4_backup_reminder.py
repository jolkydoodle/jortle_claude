"""Group 4, batch 4A-43 — the launch message while backups are paused (bug 43).

Master Spec §46.2 (decided 2026-10-04): while backups are paused (encrypted
backups chosen, no backup passphrase), Jortle shows a message at launch, at
most once a day, naming the cause and offering "Set a backup passphrase" or
"Use unencrypted backups"; it can be dismissed and shows again the next day.
Criteria B43-1…B43-12 with amendments 4A-43/AM-1…AM-9
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4A-43 plan — agreed 2026-10-07").

The date comes from backup_reminder.today, set here, never the system clock.
Every dialog is driven in-process (QTest, or its own buttons); nothing is sent
to the desktop (FP-15). The unlock-order check (B43-4) runs the real startup,
jortle_claude.main, in a child process of this file (`--startup-order`),
because it creates its own QApplication; that process is killed by its
timeout if it hangs.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
CHILD = len(sys.argv) > 1 and sys.argv[1] == "--startup-order"
ROOT = isolation.isolate(prefix="jortle-g4-b43-child-" if CHILD else "jortle-g4-b43-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PASS = "correct horse battery"


# ------------------------------------------------------------ the child: real startup order
def child_startup_order():
    """An isolated encrypted install with backups paused, started through
    jortle_claude.main: records the order of the unlock window, the main
    window and the paused message, then quits."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog
    from app import backup_dialog, security
    from app.database import Database
    from app.paths import get_data_dir
    assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"
    data = get_data_dir()
    db = Database()
    db.close()
    security.set_up_database_encryption(data, PASS)
    security.session.clear()
    security.update_config(data, storage_choice="encrypted")
    order = []

    def unlock_exec(self):
        order.append("unlock")
        self.passphrase.setText(PASS)
        self._try()
        return self.result()

    def paused_exec(self):
        order.append("paused message")
        QTimer.singleShot(0, QApplication.quit)
        return QDialog.Rejected

    backup_dialog.UnlockDialog.exec = unlock_exec
    backup_dialog.PausedBackupsDialog.exec = paused_exec
    from app import main_window
    original_start = main_window.MainWindow.start_background_tasks

    def start(self):
        order.append("main window shown" if self.isVisible() else "main window NOT shown")
        QTimer.singleShot(20000, QApplication.quit)      # never hang
        original_start(self)

    main_window.MainWindow.start_background_tasks = start
    import jortle_claude
    code = jortle_claude.main()
    print("ORDER:" + json.dumps(order), flush=True)
    sys.exit(0 if code == 0 else 3)


if CHILD:
    child_startup_order()

# ------------------------------------------------------------ the main test
from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import backup, backup_dialog, backup_reminder, security  # noqa: E402
from app import main_window as mw  # noqa: E402
from app.backup_dialog import PausedBackupsDialog, StorageChoiceDialog  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.theme import PRESETS, build_stylesheet  # noqa: E402

print("IMPORTED app FROM", mw.__file__)
DATA = get_data_dir()
assert str(DATA).startswith(str(ROOT)), "data dir not isolated"

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


# ---- the date seam
TODAY = {"date": "2026-11-01"}
backup_reminder.today = lambda: TODAY["date"]

# ---- message boxes answered in-process
infos, questions = [], []
ANSWER = {"question": QMessageBox.No}
QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[1:3]) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: infos.append(("critical",) + tuple(a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: infos.append(("warning",) + tuple(a[1:3])) or QMessageBox.Ok)


def fake_question(_parent, title, text, *a, **k):
    questions.append((title, text))
    return ANSWER["question"]


QMessageBox.question = staticmethod(fake_question)

# ---- the passphrase dialog answered in-process (as test_group2_ui does)
PASSPHRASE = {"accept": True}


def fake_passphrase_exec(self):
    if not PASSPHRASE["accept"]:
        return QDialog.Rejected
    self.first.setText(PASS)
    self.second.setText(PASS)
    self.understand.setChecked(True)
    self._check()
    return self.result() or QDialog.Rejected


backup_dialog.NewPassphraseDialog.exec = fake_passphrase_exec


# ---- calls of the shared commands, counted (the same functions Backups & Security uses)
calls = {"keep_unencrypted": 0, "set_up_backup": 0, "back_up_now": 0}
for name, key in (("_keep_backups_unencrypted", "keep_unencrypted"),
                  ("_set_up_backup_encryption", "set_up_backup"), ("_back_up_now", "back_up_now")):
    original = getattr(MainWindow, name)

    def counted(self, *a, _original=original, _key=key, **k):
        calls[_key] += 1
        return _original(self, *a, **k)

    setattr(MainWindow, name, counted)


class Driver:
    """Answers the startup dialogs as they appear, in order; any paused
    message or storage question not expected is recorded and dismissed, so a
    wrong result fails a check instead of hanging the run."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.seen = []
        self.unexpected = []
        self.dialogs = []
        self.running = True
        QTimer.singleShot(0, self._tick)

    def _tick(self):
        if not self.running:
            return
        w = QApplication.activeModalWidget()
        if isinstance(w, (PausedBackupsDialog, StorageChoiceDialog)) and w.isVisible() \
                and w not in self.dialogs:
            self.dialogs.append(w)
            if self.steps and isinstance(w, self.steps[0][0]):
                cls, act = self.steps.pop(0)
                self.seen.append(cls.__name__)
                act(w)
            else:
                self.unexpected.append(type(w).__name__)
                w.reject()
        QTimer.singleShot(10, self._tick)

    def stop(self):
        self.running = False


def click(label):
    def act(w):
        settle()
        if isinstance(w, PausedBackupsDialog):
            button = {"passphrase": w.buttons[w.PASSPHRASE], "unencrypted": w.buttons[w.UNENCRYPTED],
                      "later": w.buttons[w.LATER]}[label]
        else:
            button = next(b for b in w.findChildren(QPushButton) if b.text().startswith(label))
        QTest.mouseClick(button, Qt.LeftButton)
    return act


def escape(w):
    QTest.keyClick(w, Qt.Key_Escape)


def close(w):
    w.close()


def launch(win, *steps):
    """One launch's startup step (what start_background_tasks runs after
    500 ms), with its dialogs answered."""
    driver = Driver(*steps)
    try:
        win._startup_backup_tasks()
        settle(50)
    finally:
        driver.stop()
    return driver


# ---- state helpers
def config():
    return security.load_config(DATA)


def backups():
    return sorted(r.file for r in backup.scan_backups(backup.backup_dir(DATA)))


def no_backup_key():
    for path in (DATA / security.BACKUP_KEY_FILE, backup.backup_dir(DATA) / security.BACKUP_KEY_FILE):
        if path.exists():
            path.unlink()
    security.session.identity = None


def set_paused(automatic="daily", shown=None):
    no_backup_key()
    security.update_config(DATA, storage_choice="encrypted", backup_encryption=False,
                           backup_recipient=None, automatic_backups=automatic,
                           paused_notice_shown_on=shown)


def paused_messages(driver):
    return driver.seen.count("PausedBackupsDialog") + driver.unexpected.count("PausedBackupsDialog")


def clear_backups():
    folder = backup.backup_dir(DATA)
    if folder.exists():
        shutil.rmtree(folder)
    security.update_config(DATA, last_backup=None, last_error=None)


# ================================================================ B43-10
print("\n--- [B43-10] a window built directly shows no message ---")
set_paused()
win = MainWindow()
win.resize(1400, 900)
win.show()
settle(800)
check("a MainWindow built and shown without start_background_tasks shows no dialog while paused",
      not isinstance(QApplication.activeModalWidget(), (PausedBackupsDialog, StorageChoiceDialog))
      and config().get("paused_notice_shown_on") is None)

# ================================================================ B43-1, B43-2
print("\n--- [B43-1] shown at launch while paused, with the cause and three buttons ---")
clear_backups()
set_paused()
TODAY["date"] = "2026-11-01"
captured = {}


def inspect_then_later(w):
    captured["labels"] = [lbl.text() for lbl in w.findChildren(QLabel)]
    captured["buttons"] = [b.text() for b in w.findChildren(QPushButton)]
    click("later")(w)


before_cfg = {k: v for k, v in config().items() if k != "paused_notice_shown_on"}
before_files = sorted(p.name for p in DATA.iterdir())
d = launch(win, (PausedBackupsDialog, inspect_then_later))
text = " ".join(captured.get("labels", []))
check("paused (automatic backups on): the message is shown once at launch",
      paused_messages(d) == 1 and d.seen == ["PausedBackupsDialog"], (d.seen, d.unexpected))
check("it names the cause (the pause reason)",
      (security.backups_paused_reason(DATA) or "<not paused>") in text, text)
check("it says what 'Use unencrypted backups' means",
      backup_dialog.UNENCRYPTED_BACKUPS_MEANING in text)
check(f"exactly the three buttons {captured.get('buttons')}",
      captured.get("buttons") == ["Set a backup passphrase…", "Use unencrypted backups", "Not now"])
print("\n--- [B43-2] Not now changes nothing but the date ---")
after_cfg = {k: v for k, v in config().items() if k != "paused_notice_shown_on"}
check("Not now: security.json unchanged except paused_notice_shown_on = today",
      after_cfg == before_cfg and config()["paused_notice_shown_on"] == "2026-11-01")
check("...no backup file or key was created",
      backups() == [] and sorted(p.name for p in DATA.iterdir()) == before_files
      and not (DATA / security.BACKUP_KEY_FILE).exists())
d = launch(win)
check("a second launch the same day does not show it", paused_messages(d) == 0, d.unexpected)
TODAY["date"] = "2026-11-02"
d = launch(win, (PausedBackupsDialog, escape))
check("the next day it shows again (dismissed with Escape)",
      d.seen == ["PausedBackupsDialog"] and config()["storage_choice"] == "encrypted"
      and config()["paused_notice_shown_on"] == "2026-11-02")
TODAY["date"] = "2026-11-03"
d = launch(win, (PausedBackupsDialog, close))
check("...and the day after (closed with the window's close)",
      d.seen == ["PausedBackupsDialog"] and config()["storage_choice"] == "encrypted" and backups() == [])

set_paused(automatic="off")
TODAY["date"] = "2026-11-04"
d = launch(win, (PausedBackupsDialog, click("later")))
check("paused with automatic backups off: shown too", d.seen == ["PausedBackupsDialog"])

print("\n--- [B43-1] not shown when backups are not paused ---")
security.update_config(DATA, storage_choice="unencrypted", automatic_backups="off")
TODAY["date"] = "2026-11-05"
d = launch(win)
check("backups unencrypted: not shown", paused_messages(d) == 0, d.unexpected)
security.update_config(DATA, storage_choice=None, paused_notice_shown_on=None)
d = launch(win, (StorageChoiceDialog, close))
check("the rule itself: should_show is false while the storage question is unanswered (waiting)",
      security.automatic_backups_waiting_reason(DATA) and not security.backups_paused_reason(DATA)
      and not backup_reminder.should_show(DATA, TODAY["date"]))
check("storage question unanswered: the question shows, the paused message does not",
      d.seen == ["StorageChoiceDialog"] and paused_messages(d) == 0
      and config()["paused_notice_shown_on"] is None, (d.seen, d.unexpected))

# ================================================================ AM-2: the last-backup line
print("\n--- [AM-2] the last-backup line never says 'never' when backups exist elsewhere ---")
set_paused()
clear_backups()
line = backup_dialog.last_backup_line(DATA)
check(f"empty backup folder, nothing elsewhere: {line!r}",
      "never" not in line.lower() and "no backup in the backup folder" in line.lower()
      and "earlier version" not in line)
legacy = DATA / "backups"
legacy.mkdir(exist_ok=True)
(legacy / "DailyJournal-backup-2026-09-16_173405.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
line = backup_dialog.last_backup_line(DATA)
check(f"only a legacy backup: it is mentioned, not 'never': {line!r}",
      "never" not in line.lower() and str(legacy) in line and "earlier version" in line)
TODAY["date"] = "2026-11-06"
d = launch(win, (PausedBackupsDialog, inspect_then_later))
check("...and the message shows that line",
      any(str(legacy) in t for t in captured.get("labels", [])))
shutil.rmtree(legacy)

# ================================================================ B43-3
print("\n--- [B43-3] 'last shown' lives in security.json, not the settings table ---")
check("security.json holds paused_notice_shown_on",
      json.loads((DATA / security.CONFIG_FILE).read_text(encoding="utf-8")).get("paused_notice_shown_on")
      == "2026-11-06")
rows = win.db._conn.execute("SELECT key FROM settings").fetchall()
check("the settings table has no such key", not [r for r in rows if "paused" in r[0] or "notice" in r[0]], rows)
raw = json.loads((DATA / security.CONFIG_FILE).read_text(encoding="utf-8"))
raw.pop("paused_notice_shown_on", None)
(DATA / security.CONFIG_FILE).write_text(json.dumps(raw), encoding="utf-8")
check("a security.json without the key reads as never shown",
      backup_reminder.should_show(DATA, "2026-11-06"))

# ================================================================ B43-4
print("\n--- [B43-4] the order at launch ---")
security.update_config(DATA, storage_choice=None, paused_notice_shown_on=None, automatic_backups="daily")
no_backup_key()
PASSPHRASE["accept"] = False
TODAY["date"] = "2026-11-07"
d = launch(win, (StorageChoiceDialog, click("Encrypt backups")))
check("'Encrypt backups…' then the passphrase cancelled: paused, but no paused message that launch",
      config()["storage_choice"] == "encrypted" and security.backups_paused_reason(DATA)
      and paused_messages(d) == 0 and config()["paused_notice_shown_on"] is None, (d.seen, d.unexpected))
d = launch(win, (PausedBackupsDialog, click("later")))
check("...and the next launch (the same day) shows it", d.seen == ["PausedBackupsDialog"])
TODAY["date"] = "2026-11-08"
blocker = QDialog(win)
blocker.setModal(True)
blocker.open()
settle(50)
d = launch(win)
blocker.reject()
settle()
check("with another modal window open: not shown, not recorded",
      paused_messages(d) == 0 and config()["paused_notice_shown_on"] == "2026-11-07")
driver = Driver()
win._backup_timer.timeout.emit()
settle(100)
driver.stop()
check("the hourly check never shows it", driver.unexpected == [] and config()["paused_notice_shown_on"] == "2026-11-07")
# The real route: start_background_tasks, 500 ms after the window shows.
driver = Driver((PausedBackupsDialog, click("later")))
win.start_background_tasks()
settle(900)
driver.stop()
win._backup_timer.stop()
check("through start_background_tasks (the entry point's route) it shows",
      driver.seen == ["PausedBackupsDialog"] and config()["paused_notice_shown_on"] == "2026-11-08")

print("\n--- [B43-4] never before the unlock window (the real startup, in a child process) ---")
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
try:
    run = subprocess.run([sys.executable, __file__, "--startup-order"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env, timeout=180)
    out = run.stdout + run.stderr
except subprocess.TimeoutExpired as exc:
    out = f"TIMEOUT {exc}"
order = next((json.loads(l[6:]) for l in out.splitlines() if l.startswith("ORDER:")), None)
check(f"the real startup: unlock window, then the main window, then the message ({order})",
      order == ["unlock", "main window shown", "paused message"], out[-600:])

# ================================================================ B43-5, AM-3 (unencrypted)
print("\n--- [B43-5, AM-3] 'Use unencrypted backups' ---")
for automatic, answer in (("daily", None), ("off", QMessageBox.Yes), ("off", QMessageBox.No)):
    clear_backups()
    set_paused(automatic=automatic)
    TODAY["date"] = f"2026-11-1{0 if automatic == 'daily' else (1 if answer == QMessageBox.Yes else 2)}"
    ANSWER["question"] = answer if answer is not None else QMessageBox.No
    calls.update(keep_unencrypted=0, back_up_now=0)
    questions.clear()
    count_before = len(backups())
    d = launch(win, (PausedBackupsDialog, click("unencrypted")))
    new = len(backups()) - count_before
    st = backup.status(DATA)
    expected = 0 if answer == QMessageBox.No else 1
    label = (f"automatic {automatic}" + ("" if answer is None else
                                         f", 'Make a backup now?' {'Yes' if answer == QMessageBox.Yes else 'No'}"))
    check(f"{label}: storage set unencrypted through _keep_backups_unencrypted, once",
          config()["storage_choice"] == "unencrypted" and calls["keep_unencrypted"] == 1
          and not security.backups_paused_reason(DATA))
    check(f"{label}: exactly {expected} new backup in that startup ({new})"
          + ("" if expected == 0 else ", checked"),
          new == expected and (expected == 0 or (st.last_verified and not st.last_encrypted)))
    check(f"{label}: 'Make a backup now?' asked only with automatic backups off ({len(questions)})",
          len(questions) == (0 if automatic == "daily" else 1)
          and calls["back_up_now"] == (1 if answer == QMessageBox.Yes else 0))
security_dialogs = []
original_security_exec = backup_dialog.BackupsSecurityDialog.exec
backup_dialog.BackupsSecurityDialog.exec = lambda self: security_dialogs.append(self) or 0
win._open_backups_security()
backup_dialog.BackupsSecurityDialog.exec = original_security_exec
calls.update(keep_unencrypted=0)
security_dialogs[0].actions["keep_backups_unencrypted"]()
check("Backups & Security's 'Keep Backups Unencrypted' runs the same command (the same counter)",
      calls["keep_unencrypted"] == 1)
TODAY["date"] = "2026-11-13"
d = launch(win)
check("once backups are no longer paused it never shows", paused_messages(d) == 0)

# ================================================================ B43-9 is below (encrypted database)
# ================================================================ B43-6, AM-3 (passphrase)
print("\n--- [B43-6, AM-3] 'Set a backup passphrase…' ---")
for automatic in ("daily", "off"):
    clear_backups()
    set_paused(automatic=automatic)
    TODAY["date"] = "2026-11-14" if automatic == "daily" else "2026-11-15"
    PASSPHRASE["accept"] = True
    calls.update(set_up_backup=0)
    count_before = len(backups())
    d = launch(win, (PausedBackupsDialog, click("passphrase")))
    st = backup.status(DATA)
    new = len(backups()) - count_before
    check(f"automatic {automatic}: the passphrase is set through _set_up_backup_encryption, backups encrypted",
          calls["set_up_backup"] == 1 and security.backups_encrypted(DATA)
          and not security.backups_paused_reason(DATA))
    check(f"automatic {automatic}: exactly one new backup in that startup ({new}), encrypted and checked",
          new == 1 and st.last_encrypted and st.last_verified)
    TODAY["date"] = "2026-11-16"
    d = launch(win)
    check(f"automatic {automatic}: the next day the message no longer shows", paused_messages(d) == 0)

clear_backups()
set_paused()
TODAY["date"] = "2026-11-17"
PASSPHRASE["accept"] = False
before_cfg = {k: v for k, v in config().items() if k != "paused_notice_shown_on"}
d = launch(win, (PausedBackupsDialog, click("passphrase")))
check("passphrase cancelled: nothing changes",
      {k: v for k, v in config().items() if k != "paused_notice_shown_on"} == before_cfg
      and backups() == [] and not security.backups_encrypted(DATA))
d = launch(win)
check("...and it does not show again that day", paused_messages(d) == 0)

# ================================================================ B43-11
# B43-11 (the paused message; tightened by 4A-43/AM-7 to fit the screen) and
# B43-12 (the first-launch storage question, bug 45; 4A-43/AM-9).
print("\n--- [B43-11, B43-12] both dialogs at 9, 13 and 24 pt, Light and Dark ---")
for name, make, buttons in (("paused message", lambda: PausedBackupsDialog(DATA, win), 3),
                            ("storage question", lambda: StorageChoiceDialog(win), 2)):
    for scheme_name in ("Light", "Dark"):
        for size in (9, 13, 24):
            sheet = build_stylesheet(PRESETS[scheme_name], size)
            font = app.font()
            font.setPointSize(size)
            app.setFont(font)
            app.setStyleSheet(sheet)
            settle()
            dlg = make()
            dlg.show()
            settle(50)
            clipped = []
            for lbl in dlg.findChildren(QLabel):
                if lbl.isVisible() and lbl.heightForWidth(lbl.width()) > lbl.height():
                    clipped.append(lbl.text()[:30])
            for b in dlg.findChildren(QPushButton):
                if not b.isVisible() or not dlg.rect().contains(b.geometry()) \
                        or QFontMetrics(b.font()).horizontalAdvance(b.text()) > b.width():
                    clipped.append(b.text())
            screen = app.primaryScreen().availableGeometry()
            fits = dlg.width() <= screen.width() and dlg.height() <= screen.height()
            # No paragraph gap: the window is as tall as its layout needs at
            # its width (the spare height used to spread between paragraphs).
            spare = dlg.height() - dlg.layout().totalHeightForWidth(dlg.width())
            check(f"{name}, {scheme_name} {size}pt: no clipped text, all {buttons} buttons visible, "
                  f"within the {screen.width()}×{screen.height()} screen ({dlg.width()}×{dlg.height()}), "
                  f"no gap ({spare}px spare)",
                  not clipped and fits and spare <= 2
                  and len(dlg.findChildren(QPushButton)) == buttons, clipped)
            dlg.close()
win._apply_settings()
settle()

# ================================================================ B43-9 (encrypted database)
print("\n--- [B43-9] an encrypted database stays encrypted after 'Use unencrypted backups' ---")
PASSPHRASE["accept"] = True
check("(setup) the database is encrypted", win._set_up_database_encryption() is True
      and security.is_encrypted_install(DATA))
clear_backups()
set_paused()
key_before = (DATA / security.DB_KEY_FILE).read_bytes()
header_before = (DATA / security.DB_FILENAME).read_bytes()[:16]
TODAY["date"] = "2026-11-18"
d = launch(win, (PausedBackupsDialog, click("unencrypted")))
check("'Use unencrypted backups' leaves the database encrypted, db-key.age and the header byte-identical",
      d.seen == ["PausedBackupsDialog"] and security.is_encrypted_install(DATA)
      and (DATA / security.DB_KEY_FILE).read_bytes() == key_before
      and (DATA / security.DB_FILENAME).read_bytes()[:16] == header_before)

win.close()
settle()

# ================================================================ B43-3: a restore keeps "last shown"
print("\n--- [B43-3] a restore on the day it was shown does not bring it back ---")
win2 = MainWindow()
win2.show()
settle(300)
security.update_config(DATA, storage_choice="unencrypted")
summary = backup.create_backup("manual")
set_paused()
TODAY["date"] = "2026-11-20"
d = launch(win2, (PausedBackupsDialog, click("later")))
check("(shown on 2026-11-20)", d.seen == ["PausedBackupsDialog"])
win2.close()
settle()
manifest = backup.restore_backup(summary.zip_path, passphrase=None)
check("(the backup was restored)", bool(manifest))
win3 = MainWindow()
win3.show()
settle(300)
d = launch(win3)
check("after the restore, a launch the same day does not show it again",
      paused_messages(d) == 0 and config()["paused_notice_shown_on"] == "2026-11-20", d.unexpected)
win3.close()
settle()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
