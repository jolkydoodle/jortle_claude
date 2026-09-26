"""Group 2 fixes — the five failures found by the Group 2 audit.

  F1  separate database and backup passphrases, with "use the same" as an option
  F2  database encryption and backup encryption are independent switches
  F3  choosing encrypted backups without a passphrase pauses backups (never
      falls back to unencrypted); automatic backups wait for the choice
  F4  the backup index has one record per FILE: a copy made by hand is never
      pruned, and a damaged copy does not hide the good original from twins
  F5  two backups made in the same second: retention never deletes the newest
"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time
import zipfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g2-fix-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import backup, backup_dialog, data_migration, security  # noqa: E402
import app.main_window as mw  # noqa: E402
from app.database import Database  # noqa: E402
from app.saving import SAVE  # noqa: E402

failures = []
DBP = "database words here"
BKP = "backup words there"


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def fresh(name):
    case = root / name
    case.mkdir()
    isolation.point_at(case)
    data_migration.reset_for_tests()
    security.session.clear()
    return data_migration.resolve_data_dir()


def write_entry(db, date="2026-05-01", text="some writing"):
    db.upsert_entry(date, body_md=f"<p>{text}</p>", body_format="html", body_text=text)


def raises(fn, exc_type):
    try:
        fn()
    except exc_type as exc:
        return exc
    except Exception as exc:
        return f"WRONG {type(exc).__name__}: {exc}"
    return None


infos, criticals, questions = [], [], []
QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[2] if len(a) > 2 else a))
QMessageBox.critical = staticmethod(lambda *a, **k: criticals.append(a[2] if len(a) > 2 else a))
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
question_answer = {"value": QMessageBox.No}
QMessageBox.question = staticmethod(
    lambda *a, **k: questions.append(a[2] if len(a) > 2 else a) or question_answer["value"])
mw.ask_unsaved = lambda *_a: SAVE

# ============================================================== F1 + F2
print("\n--- F2: backups encrypted, database not ---")
d = fresh("backups-only")
db = Database()
write_entry(db)
security.set_up_backup_encryption(d, BKP)
check("backup encryption on, database still plain",
      security.backups_encrypted(d) and security.db_file_state(d / "journal.db") == "plain"
      and not (d / security.DB_KEY_FILE).exists())
s1 = backup.create_backup("manual")
check("a backup is a .jcbackup", s1.encrypted and s1.zip_path.suffix == ".jcbackup")
check("...without the plaintext in it",
      b"some writing" not in s1.zip_path.read_bytes())
res = backup.set_keep_unencrypted_copies(db, True)
check("keep copies ON makes a twin but no database copy (the database isn't encrypted)",
      s1.zip_path.with_suffix(".zip").is_file() and not (d / security.MIRROR_DB).exists()
      and security.MIRROR_DB not in [n for n, _ in res["created"]])
keys = json.loads((d / security.PLAIN_KEYS_FILE).read_text())
check("the plain keyfile holds only the backup key", keys["db_key"] is None and keys["backup_identity"])
backup.set_keep_unencrypted_copies(db, False)
check("keep copies OFF removes the twin and the keyfile",
      not s1.zip_path.with_suffix(".zip").exists() and not (d / security.PLAIN_KEYS_FILE).exists())
db.close()

print("\n--- F2: database encrypted, backups not ---")
d = fresh("database-only")
db = Database()
write_entry(db)
db.close()
security.set_up_database_encryption(d, DBP)
check("database encrypted, backups still unencrypted",
      security.is_encrypted_install(d) and not security.backups_encrypted(d)
      and not (d / security.BACKUP_KEY_FILE).exists())
security.update_config(d, storage_choice="unencrypted")
db = Database()
s2 = backup.create_backup("manual")
check("a backup is an ordinary .zip", not s2.encrypted and s2.zip_path.suffix == ".zip")
with zipfile.ZipFile(s2.zip_path) as zf:
    mem = security.open_plain_bytes(zf.read("journal.db"))
check("...holding the (decrypted) journal",
      mem.execute("SELECT body_text FROM entries").fetchone()[0] == "some writing")
res = backup.set_keep_unencrypted_copies(db, True)
check("keep copies ON makes only the database copy",
      (d / security.MIRROR_DB).is_file() and [n for n, _ in res["created"]] == [security.MIRROR_DB])
backup.set_keep_unencrypted_copies(db, False)
check("keep copies OFF removes it", not (d / security.MIRROR_DB).exists())
db.close()

print("\n--- F1: separate passphrases by default; 'the same' only when chosen ---")
security.set_up_backup_encryption(d, BKP)
check("each key file opens only with its own passphrase",
      security.passphrase_opens(d, "database", DBP) and not security.passphrase_opens(d, "database", BKP)
      and security.passphrase_opens(d, "backup", BKP) and not security.passphrase_opens(d, "backup", DBP))
security.session.clear()
security.unlock(d, DBP)
check("unlocking the database does not unlock the backup key", security.session.identity is None)
db = Database()
s3 = backup.create_backup("manual")
check("without the backup key, an encrypted backup is still made and checked "
      "(contents before encryption, file read back)",
      s3.encrypted and s3.verified and s3.verification == backup.VERIFIED_WRITTEN)
rec = [r for r in backup.scan_backups() if r.file == s3.zip_path.name][0]
check("...and the index records that level", rec.verification == backup.VERIFIED_WRITTEN)
check("that backup really is intact (decrypts with the backup passphrase)",
      backup.verify_container(s3.zip_path, BKP)[2]["backup_id"] == s3.backup_id)
security.unlock_backup_key(d, BKP)
s4 = backup.create_backup("manual")
check("with the backup key unlocked, the check is full", s4.verification == backup.VERIFIED_FULL)
db.close()

# The written-copy check really compares: a different ciphertext is refused.
tmp_container, cipher = backup._write_container(
    root / "probe.jcbackup", b"payload bytes", {
        "format": backup.CONTAINER_FORMAT, "format_version": 1, "backup_id": "x",
        "created_at": "", "kind": "manual",
        "recipient": security.load_config(d)["backup_recipient"]}, d)
err = raises(lambda: backup._check_written_container(tmp_container, cipher[:-1] + b"!", "x"),
             backup.RestoreError)
check("the read-back check refuses a file whose ciphertext differs",
      isinstance(err, backup.RestoreError))
check("...and accepts the identical one",
      backup._check_written_container(tmp_container, cipher, "x") is None)

print("\n--- F1: 'use the same passphrase' in the set-up window ---")
d = fresh("same-passphrase")
Database().close()
security.set_up_database_encryption(d, DBP)
dlg = backup_dialog.NewPassphraseDialog(
    "Encrypt Backups", "intro", subject=backup_dialog.BACKUPS,
    same_as=backup_dialog.DATABASE,
    verify_same=lambda text: security.passphrase_opens(d, "database", text))
check("the option is offered and off by default", dlg.same is not None and not dlg.same.isChecked())
dlg.same.setChecked(True)
dlg.show()
check("with it ticked, the second field is hidden", dlg.second.isHidden())
dlg.first.setText("not the database one")
dlg.understand.setChecked(True)
dlg._check()
check("a passphrase that isn't the database's is refused",
      dlg.result() != QDialog.Accepted and "not the passphrase of" in dlg.error.text())
dlg.first.setText(DBP)
dlg._check()
check("the database passphrase is accepted", dlg.result() == QDialog.Accepted and dlg.uses_same())
security.set_up_backup_encryption(d, dlg.value())
security.session.clear()
security.unlock(d, DBP)
check("afterwards, one unlock opens both keys", security.session.identity is not None)
plain = backup_dialog.NewPassphraseDialog("Encrypt the Journal Database", "intro",
                                          subject=backup_dialog.DATABASE)
check("with nothing else encrypted, no 'same' option is shown", plain.same is None)

# ============================================================== F4
print("\n--- F4: a hand-made copy of a backup ---")
d = fresh("copies")
db = Database()
write_entry(db)
security.update_config(d, storage_choice="unencrypted", keep_automatic=1)
a = backup.create_backup("automatic")
folder = a.zip_path.parent
copy = folder / "my own copy.zip"
shutil.copy2(a.zip_path, copy)
records = {r.file: r for r in backup.scan_backups()}
check("the copy gets its own record: same backup, not made or checked by the app",
      copy.name in records and a.zip_path.name in records
      and records[copy.name].backup_id == records[a.zip_path.name].backup_id
      and not records[copy.name].made_by_app and not records[copy.name].verified
      and records[a.zip_path.name].made_by_app)
records2 = {r.file: r for r in backup.scan_backups()}
check("a second scan changes nothing (no flip-flopping)",
      {k: vars(v) for k, v in records.items()} == {k: vars(v) for k, v in records2.items()})
time.sleep(1.1)
b = backup.create_backup("automatic")
left = {p.name for p in folder.iterdir()}
check("retention (keep 1) deletes the app's older automatic backup",
      a.zip_path.name not in left and a.zip_path.name in b.pruned, b.pruned)
check("...and keeps the copy you made yourself", copy.name in left)
check("...and the new backup", b.zip_path.name in left)
db.close()

print("\n--- F4: a damaged copy does not hide the good original ---")
d = fresh("damaged-copy")
db = Database()
write_entry(db)
security.set_up_backup_encryption(d, BKP)
good = backup.create_backup("manual")
folder = good.zip_path.parent
bad = folder / "aaa-damaged copy.jcbackup"            # sorts first
raw = bytearray(good.zip_path.read_bytes())
raw[len(raw) // 2] ^= 0xFF
bad.write_bytes(bytes(raw))
bad_hash = hashlib.sha256(bad.read_bytes()).hexdigest()
res = backup.set_keep_unencrypted_copies(db, True)
check("the good backup gets its twin", good.zip_path.with_suffix(".zip").is_file(), res)
check("the damaged copy gets no twin, is reported as damaged, and is untouched",
      not bad.with_suffix(".zip").exists() and [n for n, _ in res["failed"]] == [bad.name]
      and hashlib.sha256(bad.read_bytes()).hexdigest() == bad_hash, res["failed"])
res = backup.set_keep_unencrypted_copies(db, False)
check("turning copies off deletes the twin",
      not good.zip_path.with_suffix(".zip").exists() and res["deleted"])
good.zip_path.unlink()
(folder / backup.INDEX_FILE).unlink()
res = backup.set_keep_unencrypted_copies(db, True)
check("with only the damaged copy left, it is reported and left untouched",
      [n for n, _ in res["failed"]] == [bad.name]
      and hashlib.sha256(bad.read_bytes()).hexdigest() == bad_hash
      and not bad.with_suffix(".zip").exists(), res["failed"])
backup.set_keep_unencrypted_copies(db, False)
db.close()

print("\n--- F4: an index written by the first Group 2 build is read correctly ---")
d = fresh("index-v1")
folder = backup.default_backup_dir()
folder.mkdir(parents=True)
db = Database()
write_entry(db)
security.update_config(d, storage_choice="unencrypted")
made = backup.create_backup("manual")
db.close()
(folder / "by hand.zip").write_bytes(made.zip_path.read_bytes())
v1 = {"version": 1, "backups": {
    made.backup_id: {"created_at": "2026-09-23T10:00:00", "kind": "automatic",
                     "encrypted_file": None, "plain_file": made.zip_path.name,
                     "verified": True, "verified_at": "2026-09-23T10:00:00"},
    "file:by hand.zip": {"created_at": "", "kind": "unknown", "encrypted_file": None,
                         "plain_file": "by hand.zip", "verified": False}}}
(folder / backup.INDEX_FILE).write_text(json.dumps(v1))
recs = {r.file: r for r in backup.scan_backups(folder)}
check("the app's checked backup keeps its state",
      recs[made.zip_path.name].made_by_app and recs[made.zip_path.name].verified
      and recs[made.zip_path.name].kind == "automatic")
check("the unchecked file is not treated as the app's",
      not recs["by hand.zip"].made_by_app and not recs["by hand.zip"].verified)

# ============================================================== F5
print("\n--- F5: two automatic backups in the same second ---")
d = fresh("same-second")
db = Database()
write_entry(db)
security.update_config(d, storage_choice="unencrypted", keep_automatic=1)
lost = 0
same_second = 0
for _ in range(10):
    for p in backup.backup_dir().glob("*.zip"):
        p.unlink()
    (backup.backup_dir() / backup.INDEX_FILE).unlink(missing_ok=True)
    first = backup.create_backup("automatic")
    second = backup.create_backup("automatic")
    if first.zip_path.stem[:len(backup.BACKUP_PREFIX) + 17] == \
            second.zip_path.stem[:len(backup.BACKUP_PREFIX) + 17]:
        same_second += 1
    if not second.zip_path.exists():
        lost += 1
    if first.zip_path.exists():
        lost += 100             # the older one should have gone
check(f"the newest backup is kept and the older pruned in every pair "
      f"({same_second}/10 pairs fell in the same second)", lost == 0 and same_second > 0,
      f"lost={lost}")
recs = backup.scan_backups()
check("records carry microsecond times",
      all("." in r.created_at for r in recs if r.made_by_app))
db.close()

# ============================================================== F3
print("\n--- F3: encrypted backups chosen, no passphrase set ---")
d = fresh("paused")
db = Database()
write_entry(db)
security.update_config(d, storage_choice="encrypted")
err = raises(lambda: backup.create_backup("manual"), backup.BackupPaused)
folder = backup.backup_dir()
check("no backup of any kind is made", isinstance(err, backup.BackupPaused)
      and not (folder.exists() and list(folder.glob("*.*"))))
st = backup.status(d)
check("status says automatic backups are paused, and why",
      st.paused_reason and "passphrase" in st.paused_reason)
check("an automatic backup is never due while paused", not backup.automatic_backup_due(d))
db.close()

print("\n--- F3: the same, through the first-launch question ---")
d = fresh("first-launch")
win = mw.MainWindow()
win.show()
QTest.qWait(50)


def choose(choice):
    def exec_(self):
        self.choice = choice
        return QDialog.Accepted
    return exec_


mw.StorageChoiceDialog.exec = choose(backup_dialog.StorageChoiceDialog.LATER)
win._startup_backup_tasks()
QTest.qWait(50)
check("closing the question records nothing and makes no backup",
      security.load_config(d)["storage_choice"] is None and not backup.scan_backups())
check("...the status bar says backups are paused, and why",
      not win._backup_indicator.isHidden() and win._backup_indicator.text() == "Backups paused"
      and "choose" in win._backup_indicator.toolTip())
summary = win._run_backup("manual")
check("a backup you ask for is still made while undecided, and is not encrypted",
      summary is not None and not summary.encrypted)

d = fresh("first-launch-2")
win.editor.mark_clean()
win.close()
win = mw.MainWindow()
win.show()
QTest.qWait(50)
mw.StorageChoiceDialog.exec = choose(backup_dialog.StorageChoiceDialog.ENCRYPTED)
backup_dialog.NewPassphraseDialog.exec = lambda self: QDialog.Rejected
win._startup_backup_tasks()
QTest.qWait(50)
check("choosing encryption and cancelling the passphrase records the choice",
      security.load_config(d)["storage_choice"] == "encrypted" and not security.backups_encrypted(d))
check("...no automatic backup was made", not backup.scan_backups())
check("...the status bar says 'Backups paused', with the reason",
      win._backup_indicator.text() == "Backups paused" and not win._backup_indicator.isHidden()
      and "passphrase" in win._backup_indicator.toolTip())
check("...and the automatic check keeps waiting", not backup.automatic_backup_due(d))
questions.clear()
question_answer["value"] = QMessageBox.No
check("Back Up Now while paused asks to set a passphrase; No makes nothing",
      win._run_backup("manual") is None and questions and not backup.scan_backups())
question_answer["value"] = QMessageBox.Yes


def set_passphrase(self):
    self.first.setText(BKP)
    self.second.setText(BKP)
    self.understand.setChecked(True)
    self._check()
    return self.result() or QDialog.Rejected


backup_dialog.NewPassphraseDialog.exec = set_passphrase
summary = win._run_backup("manual")
check("Yes, then a passphrase: the backup is made, encrypted",
      summary is not None and summary.encrypted and security.backups_encrypted(d))
check("...and the paused indicator is gone", win._backup_indicator.text() != "Backups paused"
      or win._backup_indicator.isHidden())

d = fresh("first-launch-3")
win.editor.mark_clean()
win.close()
win = mw.MainWindow()
win.show()
QTest.qWait(50)
security.update_config(d, storage_choice="encrypted")
dlg = backup_dialog.BackupsSecurityDialog({
    "back_up_now": win._back_up_now,
    "set_up_database_encryption": win._set_up_database_encryption,
    "set_up_backup_encryption": win._set_up_backup_encryption,
    "change_database_passphrase": win._change_database_passphrase,
    "change_backup_passphrase": win._change_backup_passphrase,
    "keep_backups_unencrypted": win._keep_backups_unencrypted,
    "set_keep_copies": win._set_keep_unencrypted_copies}, parent=win)
dlg.show()
QTest.qWait(50)
check("Backups & Security shows the pause and offers both ways out",
      "PAUSED" in dlg.status_label.text() and not dlg.backup_setup_button.isHidden()
      and not dlg.backup_plain_button.isHidden())
dlg.backup_plain_button.click()
QTest.qWait(50)
check("'Keep Backups Unencrypted' resolves it: automatic backups are due again",
      security.load_config(d)["storage_choice"] == "unencrypted"
      and backup.automatic_backup_due(d) and "PAUSED" not in dlg.status_label.text())
dlg.close()
win.editor.mark_clean()
win.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
