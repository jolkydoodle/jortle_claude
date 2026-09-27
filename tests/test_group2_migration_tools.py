"""Group 2 — encrypted installations meet the migration, the startup
repair, the command-line tools, and the standard `age` tool.

The age-interop section runs only when an `age` binary is available (on the
PATH, or named by the AGE_BIN environment variable); otherwise it prints
NOT RUN rather than passing silently.
"""
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import zipfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g2-mig-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import backup, data_migration, security  # noqa: E402
from app.database import Database  # noqa: E402

failures = []
PASS = "tidal pool anemone 42"


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def fresh(name):
    """A new fake filesystem root for one case."""
    case = root / name
    case.mkdir()
    isolation.point_at(case)
    data_migration.reset_for_tests()
    security.session.clear()
    return case


def make_legacy(case, text="legacy writing"):
    legacy = case / "Jortle"
    legacy.mkdir()
    db = Database(legacy / "journal.db")
    db.upsert_entry("2025-01-01", body_md=f"<p>{text}</p>", body_format="html", body_text=text)
    db.close()
    return legacy


def encrypt_both(target, passphrase):
    """Database and backup encryption, set up with the same passphrase (the
    "use the same passphrase" choice)."""
    security.set_up_database_encryption(target, passphrase)
    security.set_up_backup_encryption(target, passphrase)


def listing(path):
    return sorted((p.relative_to(path).as_posix(), p.stat().st_size)
                  for p in path.rglob("*") if p.is_file())


print("\n--- an encrypted installation next to the old Jortle folder starts ---")
case = fresh("enc-plus-legacy")
legacy = make_legacy(case)
target = data_migration.resolve_data_dir()          # migrates the legacy folder
db = Database()
db.upsert_entry("2026-02-02", body_md="<p>mine</p>", body_format="html", body_text="mine")
db.close()
encrypt_both(target, PASS)
legacy_before = listing(legacy)
security.session.clear()
data_migration.reset_for_tests()
check("classified as a real installation without being unlocked",
      data_migration.classify_target(target) == "installation")
try:
    resolved = data_migration.resolve_data_dir()
    error = None
except data_migration.MigrationError as exc:
    resolved, error = None, exc
check("resolving the data folder does not refuse to start (the Group 1 blocker)",
      error is None and resolved == target, error)
check("the legacy folder was not re-imported or touched", listing(legacy) == legacy_before)
check("the encrypted journal is still encrypted and untouched",
      security.db_file_state(target / "journal.db") == "encrypted")
security.unlock(target, PASS)
db = Database()
check("and it unlocks to the user's writing", db.get_entry("2026-02-02").body_text == "mine")
db.close()

print("\n--- an encrypted-looking journal with no key file is left alone ---")
case = fresh("no-keyfile")
legacy = make_legacy(case)
target = case / "jortle_claude"
target.mkdir()
(target / "journal.db").write_bytes(os.urandom(8192))
before = listing(target)
check("classified as ambiguous, not as an installation",
      data_migration.classify_target(target) == "ambiguous")
try:
    data_migration.resolve_data_dir()
    refused = False
except data_migration.MigrationError:
    refused = True
check("with legacy data present, the app refuses rather than guessing", refused)
check("...and neither folder was changed", listing(target) == before
      and (legacy / "journal.db").is_file())

print("\n--- an interrupted 'set up encryption' is repaired at startup ---")
case = fresh("interrupted")
target = data_migration.resolve_data_dir()
db = Database()
db.upsert_entry("2026-04-04", body_md="<p>keep me</p>", body_format="html", body_text="keep me")
db.close()
# (a) stopped after the encrypted copy was written, before the swap
security.set_up_database_encryption(target, PASS)   # for real, then rebuild each state
enc_bytes = (target / "journal.db").read_bytes()
security.session.clear()
shutil.rmtree(target)
target.mkdir()
db = Database(target / "journal.db")
db.upsert_entry("2026-04-04", body_md="<p>keep me</p>", body_format="html", body_text="keep me")
db.close()
(target / security.ENCRYPTING_TMP).write_bytes(enc_bytes)
(target / security.DB_KEY_FILE).write_bytes(b"stale")
(target / security.BACKUP_KEY_FILE).write_bytes(b"belongs to backup encryption")
notes = security.finish_interrupted_setup(target)
check("(a) the unfinished encrypted copy and its database key are removed",
      not (target / security.ENCRYPTING_TMP).exists()
      and not (target / security.DB_KEY_FILE).exists(), notes)
check("(a) the backup key, which belongs to backup encryption, is left alone",
      (target / security.BACKUP_KEY_FILE).read_bytes() == b"belongs to backup encryption")
(target / security.BACKUP_KEY_FILE).unlink()
# Closed before (b) renames the file: Windows refuses to rename an open file,
# and an unclosed sqlite3 connection stays open until the cyclic GC runs.
db = Database(target / "journal.db")
check("(a) the unencrypted journal is untouched and opens",
      db.get_entry("2026-04-04").body_text == "keep me")
db.close()
# (b) stopped between the two renames: no journal.db at all
os.replace(target / "journal.db", target / security.BEFORE_ENCRYPTION)
notes = security.finish_interrupted_setup(target)
check("(b) with journal.db missing, the original is put back",
      security.db_file_state(target / "journal.db") == "plain"
      and not (target / security.BEFORE_ENCRYPTION).exists(), notes)
# (c) stopped after the swap, before the original was deleted
encrypt_both(target, PASS)
shutil.copy2(target / "journal.db", root / "enc-copy.db")
(target / security.BEFORE_ENCRYPTION).write_bytes(b"SQLite format 3\x00 left over")
security.session.clear()
security.finish_interrupted_setup(target)
check("(c) before unlocking, the leftover original is NOT deleted",
      (target / security.BEFORE_ENCRYPTION).exists())
security.unlock(target, PASS)
security.finish_after_unlock(target)
check("(c) once the encrypted journal has opened, the leftover is removed",
      not (target / security.BEFORE_ENCRYPTION).exists())

print("\n--- the command-line tools understand encryption ---")
case = fresh("tools")
target = data_migration.resolve_data_dir()
db = Database()
db.upsert_entry("2026-05-05", body_md="<p>tool text</p>", body_format="html",
                body_text="tool text")
db.close()
encrypt_both(target, PASS)
db = Database()
enc = backup.create_backup("manual")
db.close()
env = dict(os.environ, PYTHONIOENCODING="utf-8")
out = subprocess.run([sys.executable, str(REPO / "diagnose_data.py")], capture_output=True,
                     text=True, env=env, timeout=120)
check("diagnose_data runs on an encrypted install", out.returncode == 0, out.stderr[-300:])
check("...and says it is encrypted and locked",
      "encrypted (SQLCipher)" in out.stdout and "--unlock" in out.stdout, out.stdout[-600:])
check("...and does not claim the legacy data is missing from it",
      "ABSENT from the current folder" not in out.stdout)
out = subprocess.run([sys.executable, str(REPO / "diagnose_data.py"), "--unlock"],
                     input=PASS + "\n", capture_output=True, text=True, env=env, timeout=120)
check("diagnose_data --unlock counts the encrypted journal",
      "journal entries 1" in out.stdout and "with writing    1" in out.stdout, out.stdout[-900:])
out = subprocess.run([sys.executable, str(REPO / "recover_entry.py"), str(enc.zip_path)],
                     input=PASS + "\n", capture_output=True, text=True, env=env, timeout=120)
check("recover_entry lists an encrypted backup after the passphrase",
      "2026-05-05" in out.stdout, out.stdout[-400:] + out.stderr[-400:])
out = subprocess.run([sys.executable, str(REPO / "recover_entry.py"), str(enc.zip_path)],
                     input="wrong passphrase\n", capture_output=True, text=True, env=env,
                     timeout=120)
check("...and refuses a wrong one", out.returncode != 0 and "does not open" in
      (out.stdout + out.stderr))
# Delete the entry, then put it back from the encrypted backup.
security.unlock(target, PASS)
db = Database()
db.upsert_entry("2026-05-05", body_md="", body_format="html", body_text="")
db.close()
security.session.clear()
out = subprocess.run([sys.executable, str(REPO / "recover_entry.py"), str(enc.zip_path),
                      "2026-05-05", "--write"], input=PASS + "\n" + PASS + "\n",
                     capture_output=True, text=True, env=env, timeout=120)
check("recover_entry --write puts an entry back into an encrypted journal",
      out.returncode == 0, out.stdout[-400:] + out.stderr[-400:])
security.unlock(target, PASS)
db = Database()
check("...which is really there, and the journal is still encrypted",
      db.get_entry("2026-05-05").body_text == "tool text" and db.encrypted)
db.close()
check("recover_entry left no decrypted files behind",
      not [p for p in pathlib.Path(os.environ.get("TMPDIR", "/tmp")).glob("jortle-recover-*")])

print("\n--- the standard age tool opens the keys and backups (no jortle_claude) ---")
age = os.environ.get("AGE_BIN") or shutil.which("age")
if not age or not pathlib.Path(age).is_file():
    print("  NOT RUN  no `age` binary (set AGE_BIN to include this section)")
else:
    import pty  # noqa: E402
    import select  # noqa: E402

    def age_with_passphrase(args, passphrase):
        """Runs age with a terminal, because age reads passphrases from the
        terminal and not from stdin."""
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(age, [age] + args)
        buffer, sent = b"", False
        while True:
            ready, _, _ = select.select([fd], [], [], 30)
            if not ready:
                break
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            if not sent and b"passphrase" in buffer.lower():
                os.write(fd, passphrase.encode() + b"\n")
                sent = True
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), buffer.decode(errors="replace")

    work = root / "age-work"
    work.mkdir()
    with zipfile.ZipFile(enc.zip_path) as zf:
        zf.extractall(work)
    code, text = age_with_passphrase(
        ["--decrypt", "-i", str(work / "backup-key.age"), "-o", str(work / "payload.zip"),
         str(work / "payload.zip.age")], PASS)
    check("age decrypts a .jcbackup's payload with the passphrase", code == 0, text[-300:])
    if code == 0:
        manifest, has = backup.verify_payload(work / "payload.zip")
        check("...into an ordinary backup zip whose checksums all match",
              has and manifest["backup_id"] == enc.backup_id)
        with zipfile.ZipFile(work / "payload.zip") as zf:
            (work / "journal.db").write_bytes(zf.read("journal.db"))
        con = sqlite3.connect(str(work / "journal.db"))
        check("...whose journal.db opens with plain sqlite3",
              con.execute("SELECT body_text FROM entries WHERE date='2026-05-05'").fetchone()[0]
              == "tool text")
        con.close()
    code, text = age_with_passphrase(
        ["--decrypt", "-o", str(work / "key.txt"), str(target / security.DB_KEY_FILE)], PASS)
    check("age decrypts db-key.age with the passphrase", code == 0, text[-300:])
    if code == 0:
        key = (work / "key.txt").read_text().strip()
        import sqlcipher3.dbapi2 as sqlcipher  # noqa: E402  (SQLCipher, as the docs describe)
        con = sqlcipher.connect(str(target / "journal.db"))
        con.execute(f"PRAGMA key = \"x'{key}'\"")
        check("...and that key opens journal.db with SQLCipher, as RECOVERY.md says",
              con.execute("SELECT count(*) FROM entries").fetchone()[0] >= 1)
        con.close()
    code, text = age_with_passphrase(
        ["--decrypt", "-o", str(work / "wrong.txt"), str(target / security.DB_KEY_FILE)],
        "not the passphrase")
    check("age refuses a wrong passphrase", code != 0)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
