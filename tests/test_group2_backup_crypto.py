"""Group 2 — backups, encryption, twins, retention, restore (no UI).

Every check reads the files that were actually written: backups are opened
as zips and databases as SQLite, and "encrypted" is proved by looking for
known text in the raw bytes, not by trusting a flag.
"""
import hashlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import zipfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g2-core-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import backup, security  # noqa: E402
from app.database import Database  # noqa: E402
from app.paths import get_attachments_dir, get_data_dir  # noqa: E402

failures = []
MARK = "Zebrafinch-marmalade-7731"
DBP = "correct horse battery"               # the database passphrase
BKP = "staple lamp orchard"                 # the backup passphrase (separate)          # text that must never appear in encrypted bytes
PHOTO = b"\x89PNG\r\n\x1a\n" + b"Quokka-photo-bytes" * 50


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def raises(fn, exc_type):
    try:
        fn()
    except exc_type as exc:
        return exc
    except Exception as exc:        # wrong type is a failure, reported
        return f"WRONG {type(exc).__name__}: {exc}"
    return None


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def contains_plaintext(path, needle=MARK):
    return needle.encode() in pathlib.Path(path).read_bytes()


def entry(db, date):
    e = db.get_entry(date)
    return e.body_text if e else None


data = get_data_dir()
print("\n--- no encryption: an ordinary, checked .zip, outside the data folder ---")
db = Database()
db.upsert_entry("2026-03-01", body_md=f"<p>{MARK} one</p>", body_format="html",
                body_text=f"{MARK} one")
(get_attachments_dir() / "photo1.png").write_bytes(PHOTO)
check("the write is still only in the WAL (not checkpointed)",
      (data / "journal.db-wal").stat().st_size > 0)
plain1 = backup.create_backup("manual")
check("backup made in the default folder", plain1.zip_path.parent == backup.default_backup_dir())
check("the default folder is outside the data folder",
      data not in plain1.zip_path.parents and plain1.zip_path.parent != data)
check("not encrypted, reported as such", plain1.zip_path.suffix == ".zip" and not plain1.encrypted)
with zipfile.ZipFile(plain1.zip_path) as zf:
    manifest = json.loads(zf.read("manifest.json"))
    db_in_zip = zf.read("journal.db")
check("the manifest has a checksum for every other file",
      set(manifest["files"]) == {"journal.db", "attachments/photo1.png"})
mem = security.open_plain_bytes(db_in_zip)
check("the WAL-only entry is in the backup",
      mem.execute("SELECT body_text FROM entries WHERE date='2026-03-01'").fetchone()[0].startswith(MARK))
check("the backup's database is a standalone file (not WAL-marked)", db_in_zip[18:20] == b"\x01\x01")
recs = backup.scan_backups()
check("recorded in the index as checked",
      len(recs) == 1 and recs[0].verified and recs[0].kind == "manual")
st = backup.status()
check("status: last success set, not overdue, not encrypted",
      st.last_success and not st.overdue and not st.last_encrypted)

print("\n--- corrupt and invalid backups are refused before anything changes ---")
work = root / "bad"
work.mkdir()


def rebuild(src, dst, mutate):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            data_ = zin.read(item.filename)
            name, data_ = mutate(item.filename, data_)
            if name:
                zout.writestr(name, data_)


truncated = work / "truncated.zip"
truncated.write_bytes(plain1.zip_path.read_bytes()[:400])
check("a truncated backup is refused",
      isinstance(raises(lambda: backup.verify_payload(truncated), backup.RestoreError),
                 backup.RestoreError))
flipped = work / "flipped.zip"
raw = bytearray(plain1.zip_path.read_bytes())
offset = raw.find(b"Quokka")
if offset < 0:          # compressed; flip a byte in the middle instead
    offset = len(raw) // 3
raw[offset] ^= 0xFF
flipped.write_bytes(bytes(raw))
check("a backup with a flipped byte is refused",
      isinstance(raises(lambda: backup.verify_payload(flipped), backup.RestoreError),
                 backup.RestoreError))
swapped = work / "swapped.zip"
rebuild(plain1.zip_path, swapped,
        lambda n, d: (n, PHOTO + b"x") if n.startswith("attachments/") else (n, d))
err = raises(lambda: backup.verify_payload(swapped), backup.RestoreError)
check("a file that no longer matches the manifest is refused",
      isinstance(err, backup.RestoreError) and "manifest" in str(err), err)
missing = work / "missing.zip"
rebuild(plain1.zip_path, missing,
        lambda n, d: (None, d) if n.startswith("attachments/") else (n, d))
check("a file missing from the backup is refused",
      "missing" in str(raises(lambda: backup.verify_payload(missing), backup.RestoreError)))
unsafe = work / "unsafe.zip"
rebuild(plain1.zip_path, unsafe,
        lambda n, d: ("../evil.txt", d) if n.startswith("attachments/") else (n, d))
check("an unsafe file name is refused",
      "unsafe" in str(raises(lambda: backup.verify_payload(unsafe), backup.RestoreError)))
before_restore_dirs = set(data.parent.glob("jortle_claude.before-restore-*"))
db.close()
for bad in (truncated, flipped, swapped, missing, unsafe):
    raises(lambda: backup.restore_backup(bad), backup.RestoreError)
db = Database()
check("after five refused restores the journal is unchanged",
      entry(db, "2026-03-01") == f"{MARK} one")
check("...no safety folder was needed and none was made",
      set(data.parent.glob("jortle_claude.before-restore-*")) == before_restore_dirs)
check("...and no staging folder was left behind",
      not list(data.parent.glob(".jortle_claude-restoring-*")))

print("\n--- setting up encryption ---")
db.close()
check("the journal starts out as plain SQLite",
      security.db_file_state(data / "journal.db") == "plain")
security.set_up_database_encryption(data, DBP)
recipient = security.set_up_backup_encryption(data, BKP)
check("journal.db is encrypted (no SQLite header)",
      security.db_file_state(data / "journal.db") == "encrypted")
check("the marker text appears in no file in the data folder except photos",
      not [p for p in data.rglob("*") if p.is_file() and "attachments" not in p.parts
           and contains_plaintext(p)],
      [p.name for p in data.rglob("*") if p.is_file() and contains_plaintext(p)])
check("the unencrypted original is gone",
      not (data / security.BEFORE_ENCRYPTION).exists()
      and not (data / security.ENCRYPTING_TMP).exists())
cfg = security.load_config(data)
check("security.json holds the public key and no secret",
      cfg["backup_recipient"] == recipient and "AGE-SECRET" not in
      (data / "security.json").read_text() and security.session.db_key not in
      (data / "security.json").read_text())
db = Database()
check("the journal opens and reads back", entry(db, "2026-03-01") == f"{MARK} one")
db.upsert_entry("2026-03-02", body_md=f"<p>{MARK} two</p>", body_format="html",
                body_text=f"{MARK} two")
db.close()
wal = data / "journal.db-wal"
check("new writes are encrypted too (journal.db and its WAL)",
      not contains_plaintext(data / "journal.db")
      and (not wal.exists() or not contains_plaintext(wal)))

print("\n--- wrong credentials ---")
saved = (security.session.db_key, security.session.identity)
security.session.clear()
check("without the key the database refuses to open (Locked)",
      isinstance(raises(lambda: Database(), security.Locked), security.Locked))
check("a wrong passphrase is refused",
      isinstance(raises(lambda: security.unlock(data, "wrong horse"), security.WrongPassphrase),
                 security.WrongPassphrase))
check("...and leaves the session locked", security.session.db_key is None)
plain_conn = sqlite3.connect(str(data / "journal.db"))
err = raises(lambda: plain_conn.execute("SELECT * FROM entries").fetchall(), sqlite3.DatabaseError)
check("ordinary SQLite cannot read it", isinstance(err, sqlite3.DatabaseError))
plain_conn.close()
security.unlock(data, DBP)
check("the database passphrase unlocks the database with the same key",
      security.session.db_key == saved[0])
check("...but not the backup key, which has its own passphrase",
      security.session.identity is None and not security.passphrase_opens(data, "backup", DBP))
check("the database passphrase is refused for the backup key",
      isinstance(raises(lambda: security.unlock_backup_key(data, DBP), security.WrongPassphrase),
                 security.WrongPassphrase))
security.unlock_backup_key(data, BKP)
check("the backup passphrase unlocks the same backup key", security.session.identity == saved[1])

print("\n--- encrypted backups ---")
db = Database()
enc1 = backup.create_backup("manual")
check("an encrypted backup is a .jcbackup", enc1.zip_path.suffix == ".jcbackup" and enc1.encrypted)
check("no marker text and no photo bytes in it",
      not contains_plaintext(enc1.zip_path) and b"Quokka" not in enc1.zip_path.read_bytes())
check("no unencrypted twin while copies are not kept",
      enc1.plain_path is None and not enc1.zip_path.with_suffix(".zip").exists())
with zipfile.ZipFile(enc1.zip_path) as zf:
    names = set(zf.namelist())
check("it carries its README, info, key and payload",
      names == {"README-RECOVERY.txt", "backup-info.json", "backup-key.age", "payload.zip.age"})
backup.copy_key_to_backup_folder()
check("copy_key_to_backup_folder puts both key files there",
      (backup.backup_dir() / "backup-key.age").is_file()
      and (backup.backup_dir() / "db-key.age").is_file())
info, payload, manifest = backup.verify_container(enc1.zip_path)
check("it decrypts and its contents check out", manifest["backup_id"] == enc1.backup_id)
tampered = work / "tampered.jcbackup"
raw = bytearray(enc1.zip_path.read_bytes())
raw[len(raw) // 2] ^= 0x01
tampered.write_bytes(bytes(raw))
err = raises(lambda: backup.verify_container(tampered), backup.RestoreError)
check("a tampered encrypted backup is refused", isinstance(err, backup.RestoreError), err)
forged = work / "forged.jcbackup"


def forge(name, content):
    if name == "payload.zip.age":
        return name, content[:-40] + bytes(40)
    return name, content


with zipfile.ZipFile(enc1.zip_path) as zin, zipfile.ZipFile(forged, "w") as zout:
    items = {n: zin.read(n) for n in zin.namelist()}
    items["payload.zip.age"] = items["payload.zip.age"][:-40] + bytes(40)
    meta = json.loads(items["backup-info.json"])
    meta["payload_sha256"] = hashlib.sha256(items["payload.zip.age"]).hexdigest()
    items["backup-info.json"] = json.dumps(meta).encode()
    for n, d in items.items():
        zout.writestr(n, d)
err = raises(lambda: backup.verify_container(forged), backup.RestoreError)
check("...even with its checksum forged to match (age authentication catches it)",
      isinstance(err, backup.RestoreError), err)
identity_saved = security.session.identity
security.session.identity = None
check("without the session key an encrypted backup asks for a passphrase",
      isinstance(raises(lambda: backup.decrypt_container(enc1.zip_path), backup.NeedsPassphrase),
                 backup.NeedsPassphrase))
check("a wrong passphrase for the backup is refused",
      isinstance(raises(lambda: backup.decrypt_container(enc1.zip_path, "nope nope"),
                        security.WrongPassphrase), security.WrongPassphrase))
_, again = backup.decrypt_container(enc1.zip_path, BKP)
check("the passphrase opens it through the key inside the backup", again == payload)
security.session.identity = identity_saved

print("\n--- keep unencrypted copies: ON creates copies only ---")
enc_hashes = {p.name: sha(p) for p in backup.backup_dir().glob("*.jcbackup")}
db_hash = sha(data / "journal.db")
result = backup.set_keep_unencrypted_copies(db, True)
check("the unencrypted database copy exists and is plain SQLite",
      security.db_file_state(data / security.MIRROR_DB) == "plain")
mirror = sqlite3.connect(str(data / security.MIRROR_DB))
check("...with the journal in it",
      mirror.execute("SELECT count(*) FROM entries").fetchone()[0] == 2)
mirror.close()
check("keys.unencrypted.json written", (data / security.PLAIN_KEYS_FILE).is_file())
twin = enc1.zip_path.with_suffix(".zip")
check("an unencrypted twin was made for the existing encrypted backup", twin.is_file())
check("the twin is exactly the encrypted backup's contents", twin.read_bytes() == payload)
check("every encrypted backup is byte-for-byte unchanged",
      {p.name: sha(p) for p in backup.backup_dir().glob("*.jcbackup")} == enc_hashes)
check("the encrypted journal file itself was not rewritten", sha(data / "journal.db") == db_hash)
enc2 = backup.create_backup("manual")
check("a new backup now makes both files", enc2.plain_path and enc2.plain_path.is_file()
      and enc2.zip_path.suffix == ".jcbackup")
_, p2, _ = backup.verify_container(enc2.zip_path)
check("...with identical contents", enc2.plain_path.read_bytes() == p2)
security.session.clear()
check("with copies kept, the app unlocks without a passphrase",
      security.unlock_with_plain_keys(data) and security.session.identity == identity_saved)

print("\n--- keep unencrypted copies: OFF deletes copies only ---")
enc_hashes = {p.name: sha(p) for p in backup.backup_dir().glob("*.jcbackup")}
db_hash = sha(data / "journal.db")
result = backup.set_keep_unencrypted_copies(db, False)
check("the unencrypted database copy is deleted", not (data / security.MIRROR_DB).exists())
check("keys.unencrypted.json is deleted", not (data / security.PLAIN_KEYS_FILE).exists())
check("the twins with encrypted counterparts are deleted",
      not twin.exists() and not enc2.plain_path.exists())
check("the backup made before encryption is kept (no encrypted counterpart)",
      plain1.zip_path.is_file() and plain1.zip_path.name in result["kept"])
check("every encrypted backup is byte-for-byte unchanged",
      {p.name: sha(p) for p in backup.backup_dir().glob("*.jcbackup")} == enc_hashes)
check("the encrypted journal file was not rewritten", sha(data / "journal.db") == db_hash)
check("no marker text in any remaining file of the data folder",
      not [p for p in data.rglob("*") if p.is_file() and "attachments" not in p.parts
           and contains_plaintext(p)])
security.session.clear()
check("with copies off, a passphrase is needed again", not security.unlock_with_plain_keys(data))
security.unlock(data, DBP)
security.unlock_backup_key(data, BKP)

print("\n--- restore into an encrypted installation ---")
db.upsert_entry("2026-03-01", body_md="<p>overwritten</p>", body_format="html",
                body_text="overwritten")
db.close()
manifest = backup.restore_backup(enc1.zip_path)
check("restore reports the backup it restored", manifest["backup_id"] == enc1.backup_id)
check("the restored journal is encrypted with this installation's key",
      security.db_file_state(data / "journal.db") == "encrypted")
db = Database()
check("the restored entry is back", entry(db, "2026-03-01") == f"{MARK} one")
check("the photo is back", (get_attachments_dir() / "photo1.png").read_bytes() == PHOTO)
check("key files and settings were kept",
      (data / security.DB_KEY_FILE).is_file() and (data / "security.json").is_file())
safety = pathlib.Path(manifest["_safety_dir"])
check("the previous journal was set aside, not deleted", (safety / "journal.db").is_file())
check("...still encrypted", security.db_file_state(safety / "journal.db") == "encrypted")
check("no unencrypted database landed in the data folder",
      not [p for p in data.rglob("*") if p.is_file() and "attachments" not in p.parts
           and contains_plaintext(p)])
db.close()

print("\n--- an old-format backup (no checksums) restores, encrypted ---")
old_zip = work / "DailyJournal-backup-old.zip"
old_db = work / "old.db"
old_db.write_bytes(db_in_zip)                # a real journal database of this schema
con = sqlite3.connect(str(old_db))
con.execute("PRAGMA journal_mode=WAL")      # an old backup copied a WAL-mode file
con.execute("INSERT INTO entries (date, body_md, body_format, body_text, created_at, updated_at) "
            "VALUES ('2020-01-01', 'old writing', 'markdown', 'old writing', 'x', 'x')")
con.commit()
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.close()
with zipfile.ZipFile(old_zip, "w") as zf:
    zf.writestr("manifest.json", json.dumps({"app": "DailyJournal", "exported_at": "2020-01-02"}))
    zf.write(old_db, arcname="journal.db")
manifest = backup.restore_backup(old_zip)
check("restored, with no checksums reported", manifest["_has_checksums"] is False)
db = Database()
check("the old entry is readable and upgraded", "old writing" in (db.get_entry("2020-01-01").body_md))
check("and the journal is encrypted", db.encrypted)
db.close()

print("\n--- restoring a backup from another installation, with its passphrase ---")
other = root / "other-install"
other.mkdir()
other_db = Database(other / "journal.db")
other_db.upsert_entry("2027-05-05", body_md="<p>from elsewhere</p>", body_format="html",
                      body_text="from elsewhere")
other_db.close()
keep_session = (security.session.db_key, security.session.identity)
security.session.clear()
security.set_up_backup_encryption(other, "another passphrase")
# Build a real .jcbackup for the other installation by hand, with its keys.
other_bytes = security.plain_snapshot_bytes(other / "journal.db")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("journal.db", other_bytes)
    zf.writestr("manifest.json", json.dumps({
        "app": "jortle_claude", "backup_id": "foreign", "exported_at": "2027-05-06",
        "files": {"journal.db": {"sha256": hashlib.sha256(other_bytes).hexdigest(),
                                 "size": len(other_bytes)}}}))
foreign, _cipher = backup._write_container(
    work / "foreign.jcbackup", buf.getvalue(),
    {"format": backup.CONTAINER_FORMAT, "format_version": 1, "backup_id": "foreign",
     "created_at": "2027-05-06", "kind": "manual",
     "recipient": security.load_config(other)["backup_recipient"]}, other)
os.replace(foreign, work / "foreign.jcbackup")
security.session.db_key, security.session.identity = keep_session
check("with this installation's key it needs the other passphrase",
      isinstance(raises(lambda: backup.restore_backup(work / "foreign.jcbackup"),
                        backup.NeedsPassphrase), backup.NeedsPassphrase))
check("the wrong passphrase is refused and nothing moves",
      isinstance(raises(lambda: backup.restore_backup(work / "foreign.jcbackup", "wrong!!!"),
                        security.WrongPassphrase), security.WrongPassphrase)
      and Database().get_entry("2020-01-01") is not None)
backup.restore_backup(work / "foreign.jcbackup", "another passphrase")
db = Database()
check("the other passphrase restores it, re-encrypted with THIS installation's key",
      entry(db, "2027-05-05") == "from elsewhere" and db.encrypted
      and security.session.db_key == keep_session[0])
db.close()

print("\n--- retention: only checked automatic backups beyond N go ---")
folder = backup.backup_dir()
db = Database()
autos = [backup.create_backup("automatic") for _ in range(4)]
manual = backup.create_backup("manual")
stray = folder / "copied-in-by-hand.zip"
stray.write_bytes(plain1.zip_path.read_bytes().replace(b"backup_id", b"backup_iX"))
index = json.loads((folder / backup.INDEX_FILE).read_text())
# Give the automatic backups distinct, ordered times.
for i, s in enumerate(autos):
    index["backups"][s.zip_path.name]["created_at"] = f"2026-01-0{i + 1}T00:00:00"
(folder / backup.INDEX_FILE).write_text(json.dumps(index))
deleted = backup.prune(folder, 2)
left = {p.name for p in folder.iterdir()}
check("the two oldest automatic backups were deleted",
      autos[0].zip_path.name not in left and autos[1].zip_path.name not in left, deleted)
check("the two newest automatic backups were kept",
      autos[2].zip_path.name in left and autos[3].zip_path.name in left)
check("manual backups are never pruned", manual.zip_path.name in left and enc1.zip_path.name in left)
check("unchecked files are never pruned", stray.name in left and plain1.zip_path.name in left)
check("keep-all (None) deletes nothing", backup.prune(folder, None) == [])
security.update_config(data, keep_automatic=1)
extra = backup.create_backup("automatic")
left = {p.name for p in folder.iterdir()}
check("an automatic backup applies the setting itself",
      extra.zip_path.name in left and autos[2].zip_path.name not in left)
security.update_config(data, keep_automatic=None)

print("\n--- automatic backups: due, overdue ---")
cfg = security.load_config(data)
check("not due right after a backup", not backup.automatic_backup_due(data))
index = json.loads((folder / backup.INDEX_FILE).read_text())
for rec in index["backups"].values():
    rec["created_at"] = "2020-01-01T00:00:00"
(folder / backup.INDEX_FILE).write_text(json.dumps(index))
security.update_config(data, last_backup=dict(cfg["last_backup"], created_at="2020-01-01T00:00:00"))
check("due and overdue once the last one is over a day old",
      backup.automatic_backup_due(data) and backup.status(data).overdue)
security.update_config(data, automatic_backups="off")
check("never due when automatic backups are off", not backup.automatic_backup_due(data))
security.update_config(data, automatic_backups="daily")

print("\n--- a custom backup folder ---")
custom = root / "elsewhere" / "My Backups"
security.update_config(data, backup_dir=str(custom))
s = backup.create_backup("manual")
check("backups go to the chosen folder", s.zip_path.parent == custom)
check("status reports the chosen folder", backup.status(data).folder == custom)
security.update_config(data, backup_dir=None)
db.close()

print("\n--- changing the passphrase ---")
security.change_database_passphrase(data, DBP, "a brand new passphrase")
security.change_backup_passphrase(data, BKP, "a new backup phrase")
security.session.clear()
check("the old database passphrase no longer unlocks",
      isinstance(raises(lambda: security.unlock(data, DBP),
                        security.WrongPassphrase), security.WrongPassphrase))
security.unlock(data, "a brand new passphrase")
check("the new one does, with the same key", security.session.db_key == keep_session[0])
check("the old backup passphrase no longer opens backup-key.age",
      isinstance(raises(lambda: security.unlock_backup_key(data, BKP),
                        security.WrongPassphrase), security.WrongPassphrase))
security.unlock_backup_key(data, "a new backup phrase")
check("the new backup passphrase does, with the same key",
      security.session.identity == keep_session[1])
check("old backups still open with the session's backup key",
      backup.verify_container(enc1.zip_path)[2]["backup_id"] == enc1.backup_id)
check("an old backup's own key file still wants the passphrase it was made with",
      backup.decrypt_container(enc1.zip_path, BKP)[1] == payload)

print("\n--- a restart: a fresh process unlocks and reads ---")
import subprocess  # noqa: E402
script = (
    "import sys; sys.path.insert(0, %r)\n"
    "from app import security\nfrom app.paths import get_data_dir\n"
    "from app.database import Database\n"
    "security.unlock(get_data_dir(), 'a brand new passphrase')\n"
    "print(Database().get_entry('2027-05-05').body_text)\n"
) % str(pathlib.Path(__file__).resolve().parent.parent)
out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                     env=dict(os.environ), timeout=120)
check("a new process opens the encrypted journal with the passphrase",
      out.stdout.strip() == "from elsewhere", out.stderr[-300:])

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
