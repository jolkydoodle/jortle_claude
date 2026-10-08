"""Optional encryption for jortle_claude: the journal database, backups, keys.

Encryption is optional (Master Spec §46.3, 2026-09-24), and the database and
the backups are encrypted INDEPENDENTLY, each with its own passphrase (the
user may choose the same passphrase for both):

    database encryption   journal.db encrypted at rest (SQLCipher); the app
                          asks for the database passphrase when it starts
    backup encryption     new backups are .jcbackup (age) instead of .zip;
                          making one never needs the backup passphrase

Either, both, or neither may be on. Once on, the encrypted files are the
base copies. "Keep unencrypted copies" adds unencrypted copies beside
whichever of the two is encrypted (journal.unencrypted.db for the database,
a .zip twin beside every .jcbackup); turning it on or off only creates or
deletes those copies and never rewrites, re-encrypts or deletes an encrypted
file.

Files in the data folder
------------------------
    journal.db             the database (SQLCipher 4, raw 256-bit key, once
                           database encryption is set up)
    db-key.age             that raw key as 64 hex characters, encrypted with
                           the DATABASE passphrase (age, scrypt)
    backup-key.age         the backup identity (an age X25519 secret key),
                           encrypted with the BACKUP passphrase. Backups are
                           encrypted TO its public key. A copy is kept in the
                           backup folder and inside every backup.
    security.json          encryption and backup settings. Plain JSON on
                           purpose: it is needed before the journal is
                           unlocked, and it holds no secrets (the public
                           backup key is not a secret).
    journal.unencrypted.db only while keeping unencrypted copies of an
                           encrypted database
    keys.unencrypted.json  only while keeping unencrypted copies: the keys of
                           whatever is encrypted, in plain text, so the app
                           opens without a passphrase. Asking for one would
                           suggest the journal is protected while a readable
                           copy sits beside it (Master Spec §46.6).

Whether journal.db is encrypted is read from the file itself, never from a
flag: every plain SQLite file starts with the 16 bytes "SQLite format 3\\0",
and an encrypted one starts with random salt. A flag can disagree with the
file after a crash; the header cannot.

Established mechanisms only: SQLCipher 4 (AES-256, HMAC-SHA512 page
authentication) for the database, age (X25519 / scrypt, ChaCha20-Poly1305)
for keys and backups. docs/RECOVERY.md explains how to open every file with
the standard `sqlcipher` and `age` tools, without this application.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:                                    # pragma: no cover - import guard
    import sqlcipher3.dbapi2 as sqlcipher
except ImportError:                     # pragma: no cover
    sqlcipher = None

try:                                    # pragma: no cover - import guard
    import pyrage
    from pyrage import passphrase as age_passphrase
    from pyrage import x25519 as age_x25519
except ImportError:                     # pragma: no cover
    pyrage = None

DB_FILENAME = "journal.db"
DB_KEY_FILE = "db-key.age"
BACKUP_KEY_FILE = "backup-key.age"
CONFIG_FILE = "security.json"
MIRROR_DB = "journal.unencrypted.db"
PLAIN_KEYS_FILE = "keys.unencrypted.json"
# Working names used while encryption is being set up. Both are handled by
# finish_interrupted_setup() if the app stops half way.
ENCRYPTING_TMP = "journal.db.encrypting"
BEFORE_ENCRYPTION = "journal.db.before-encryption"

SQLITE_MAGIC = b"SQLite format 3\x00"
_HEX_KEY = re.compile(r"^[0-9a-f]{64}$")

# Anything either SQLite binding can raise. The live database may be opened
# through either one, so code that catches database errors catches both.
DB_ERRORS: tuple = (sqlite3.Error,) + ((sqlcipher.Error,) if sqlcipher else ())

log = logging.getLogger(__name__)

# Opening the journal retries an I/O error for about this long (A2, Windows
# fixes). Windows releases the file locks of a process that was killed a
# moment ago asynchronously — after the process has already been reported
# as ended — so the app restarted straight after being killed can find
# journal.db-shm still locked and get SQLITE_IOERR. It has always opened a
# fraction of a second later. Only I/O errors are retried: a wrong key, or
# a file that is not a database, fails at once.
IO_RETRY_SECONDS = 1.0
IO_RETRY_INTERVAL = 0.1
_SQLITE_IOERR = 10


def is_io_error(exc: BaseException) -> bool:
    """True for SQLITE_IOERR and its extended codes, from either binding."""
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and code & 0xFF == _SQLITE_IOERR


def _retrying_io_errors(path: Path, open_once):
    """Calls open_once() until it stops raising an I/O error, for at most
    IO_RETRY_SECONDS; every other error, and the last I/O error, is raised.
    Each retry is logged."""
    deadline = time.monotonic() + IO_RETRY_SECONDS
    attempt = 0
    while True:
        try:
            return open_once()
        except DB_ERRORS as exc:
            if not is_io_error(exc) or time.monotonic() >= deadline:
                raise
            attempt += 1
            log.warning("Opening %s gave an I/O error (%s); retry %d in %d ms",
                        path, exc, attempt, int(IO_RETRY_INTERVAL * 1000))
            time.sleep(IO_RETRY_INTERVAL)


class SecurityError(Exception):
    """A problem with encryption the user needs to hear about."""


class WrongPassphrase(SecurityError):
    pass


class Locked(SecurityError):
    """The database is encrypted and has not been unlocked in this process."""


def recovery_doc_path() -> Path:
    """Where docs/RECOVERY.md is in this copy of the app: beside the source
    when run from source, or inside the bundle (`_internal/docs`) when built
    with PyInstaller. Shown to the user, so it must be the real location."""
    import sys
    bases = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "_MEIPASS", None):
        bases.insert(0, Path(sys._MEIPASS))
    for base in bases:
        candidate = base / "docs" / "RECOVERY.md"
        if candidate.is_file():
            return candidate
    return Path("docs") / "RECOVERY.md"


def available() -> bool:
    """Whether the encryption libraries are importable in this build."""
    return sqlcipher is not None and pyrage is not None


# ------------------------------------------------------------------ files
def atomic_write(path: Path, data: bytes):
    """Writes `data` so that `path` is either the old file or the new one,
    never a torn mixture — the tmp file is flushed to disk and then renamed
    over the target."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def db_file_state(path: Path) -> str:
    """"missing", "empty", "plain" or "encrypted", from the file's header."""
    path = Path(path)
    try:
        if not path.is_file():
            return "missing"
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return "missing"
    if not head:
        return "empty"
    return "plain" if head == SQLITE_MAGIC else "encrypted"


def is_encrypted_install(data_dir: Path) -> bool:
    """Database encryption is on: the database is encrypted and has the key
    file that goes with it. (Backup encryption is separate: backups_encrypted.) A random-looking journal.db WITHOUT db-key.age is not
    an encrypted installation — it is a damaged or foreign file, and callers
    treat it as unreadable rather than asking for a passphrase that cannot
    open it."""
    data_dir = Path(data_dir)
    return (db_file_state(data_dir / DB_FILENAME) == "encrypted"
            and (data_dir / DB_KEY_FILE).is_file())


# ----------------------------------------------------------------- config
DEFAULT_CONFIG = {
    # How backups are stored, as chosen at first launch: "encrypted",
    # "unencrypted", or None (not chosen yet — the question is asked again).
    "storage_choice": None,
    "keep_unencrypted_copies": False,
    # Backup encryption, independent of database encryption. True once a
    # backup passphrase has been set; backup_recipient is its public key.
    "backup_encryption": False,
    "backup_recipient": None,       # age public key; not a secret
    "backup_dir": None,             # None = the default folder
    "automatic_backups": "daily",   # "daily" or "off"
    "keep_automatic": None,         # None = keep all; N = newest N automatic
    "restore_warning": True,
    "last_automatic_attempt": None,
    "last_error": None,
    # The date (YYYY-MM-DD) the "backups are paused" message was last shown
    # at launch; it is shown at most once a day (backup_reminder.py).
    "paused_notice_shown_on": None,
    # The date the Core Features tooltip was acknowledged with "Cool!" (4A2);
    # here so a restore never brings it back.
    "core_features_tip_acknowledged": None,
    # The date a first launch made that tooltip due (4A2/AM-6).
    "core_features_tip_due": None,
}


def load_config(data_dir: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        stored = json.loads((Path(data_dir) / CONFIG_FILE).read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            cfg.update(stored)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return cfg


def save_config(data_dir: Path, cfg: dict):
    atomic_write(Path(data_dir) / CONFIG_FILE,
                 json.dumps(cfg, indent=2, sort_keys=True).encode("utf-8"))


def update_config(data_dir: Path, **changes) -> dict:
    cfg = load_config(data_dir)
    cfg.update(changes)
    save_config(data_dir, cfg)
    return cfg


def backups_encrypted(data_dir: Path) -> bool:
    """Backup encryption is on: a backup passphrase has been set, so new
    backups are .jcbackup files."""
    cfg = load_config(data_dir)
    return bool(cfg.get("backup_encryption") and cfg.get("backup_recipient")
                and (Path(data_dir) / BACKUP_KEY_FILE).is_file())


def backups_paused_reason(data_dir: Path) -> Optional[str]:
    """Why backups must not be made right now, or None.

    Encrypted backups were chosen but no backup passphrase exists yet: making
    an unencrypted backup instead would quietly go against that choice, so
    backups wait (Master Spec §46.2) and the status says why."""
    cfg = load_config(data_dir)
    if cfg.get("storage_choice") == "encrypted" and not backups_encrypted(data_dir):
        return ("Encrypted backups were chosen, but no backup passphrase has been set "
                "yet. Set one in File → Backups & Security, or choose unencrypted backups.")
    return None


def automatic_backups_waiting_reason(data_dir: Path) -> Optional[str]:
    """Why AUTOMATIC backups are not running, or None. Adds one case to
    backups_paused_reason: nobody has said yet how backups should be stored,
    and automatic backups do not decide that on the user's behalf. (A backup
    the user asks for — Back Up Now, Export Backup… — is still made, and says
    it is not encrypted.)"""
    reason = backups_paused_reason(data_dir)
    if reason:
        return reason
    cfg = load_config(data_dir)
    if cfg.get("storage_choice") is None and not backups_encrypted(data_dir):
        return ("Automatic backups are waiting for you to choose whether backups are "
                "encrypted (File → Backups & Security).")
    return None


# ---------------------------------------------------------------- session
@dataclass
class Session:
    """The keys this process holds once the journal is unlocked. In memory
    only; never written anywhere except keys.unencrypted.json, and that only
    while the user has chosen to keep unencrypted copies."""
    db_key: Optional[str] = None        # 64 hex characters
    # The backup identity, when this process has been given it: the backup
    # passphrase was entered (or it is the same as the database passphrase),
    # backup encryption was just set up, or unencrypted copies are kept.
    # Without it, encrypted backups are still made and checked, but not
    # decrypted again (see backup.create_backup).
    identity: Optional[str] = None      # "AGE-SECRET-KEY-1…"
    source: str = ""                    # "passphrase" | "keyfile" | "setup"

    def clear(self):
        self.db_key = None
        self.identity = None
        self.source = ""


session = Session()


def startup_state(data_dir: Path) -> str:
    """What the entry point must do before opening the journal:
    "plain" (just open it), "encrypted" (unlock first), or "missing-key"
    (encrypted, but db-key.age is gone: stop, do not start empty)."""
    state = db_file_state(Path(data_dir) / DB_FILENAME)
    if state != "encrypted":
        return "plain"
    return "encrypted" if is_encrypted_install(data_dir) else "missing-key"


def needs_unlock(data_dir: Path) -> bool:
    return is_encrypted_install(data_dir) and session.db_key is None


def _check_key(key: str) -> str:
    key = key.strip().lower()
    if not _HEX_KEY.match(key):
        raise SecurityError("The database key is not in the expected format.")
    return key


def _open_encrypted(path: Path, key: str, readonly: bool = False):
    if sqlcipher is None:
        raise SecurityError(
            "This copy of jortle_claude was built without encryption support, "
            "so it cannot open an encrypted journal.")
    key = _check_key(key)
    target = f"file:{path}?mode=ro" if readonly else str(path)

    def open_once():
        conn = sqlcipher.connect(target, uri=readonly)
        # The key must be the first statement on the connection. A raw key
        # (x'…') skips SQLCipher's own key derivation: the passphrase has
        # already been stretched by age's scrypt, once, when db-key.age was
        # unlocked — doing it again on every open would only slow things down.
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchall()
        except sqlcipher.Error:
            conn.close()
            raise
        return conn

    try:
        return _retrying_io_errors(path, open_once)
    except sqlcipher.Error as exc:
        if is_io_error(exc):
            # A read or lock failure, not a key or decryption failure: saying
            # "damaged" or "wrong key file" here would send the user after
            # the wrong problem (A1).
            raise SecurityError(
                f"The journal could not be read ({exc}). Another program may be "
                "holding the file; close it, or wait a moment, and try again."
            ) from exc
        raise SecurityError(
            "The database could not be opened with its key "
            f"({exc}). It may be damaged, or the key file may not belong to it."
        ) from exc


def connect(db_path: Path, readonly: bool = False):
    """Opens a jortle_claude database whichever way it is stored.

    A plain (or not yet existing) database opens with Python's own sqlite3,
    exactly as before encryption existed. An encrypted one opens with
    SQLCipher and the key held by this session, and raises Locked when there
    is none. The returned connection has `row_factory` set to that binding's
    Row class.
    """
    db_path = Path(db_path)
    state = db_file_state(db_path)
    if state == "encrypted":
        if session.db_key is None:
            raise Locked("The journal is encrypted and has not been unlocked.")
        conn = _open_encrypted(db_path, session.db_key, readonly=readonly)
        conn.row_factory = sqlcipher.Row
        return conn

    def open_once():
        if readonly:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(str(db_path))
        # sqlite3 opens the file lazily, so an I/O error would otherwise
        # surface at the caller's first statement, past the retry. Only an
        # I/O error is acted on here; anything else is left to that first
        # statement, exactly as before.
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchall()
        except sqlite3.Error as exc:
            if is_io_error(exc):
                conn.close()
                raise
        return conn

    conn = _retrying_io_errors(db_path, open_once)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- unlocking
def _decrypt_with_passphrase(blob: bytes, passphrase: str) -> bytes:
    try:
        return age_passphrase.decrypt(blob, passphrase)
    except pyrage.DecryptError as exc:
        raise WrongPassphrase("That passphrase is not correct.") from exc


def _unwrap_both(data_dir: Path, passphrase: str) -> tuple:
    """Decrypts db-key.age with the database passphrase and, at the same
    time, tries the same passphrase on backup-key.age — it opens only when the
    user chose the same passphrase for both. Each unwrap takes a second or two
    of deliberate scrypt work; running them side by side keeps the unlock
    quick. Returns (database key, backup identity or None)."""
    data_dir = Path(data_dir)
    try:
        db_blob = (data_dir / DB_KEY_FILE).read_bytes()
    except OSError as exc:
        raise SecurityError(f"The database key file could not be read ({exc}).") from exc
    try:
        id_blob = (data_dir / BACKUP_KEY_FILE).read_bytes()
    except OSError:
        id_blob = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        db_future = pool.submit(_decrypt_with_passphrase, db_blob, passphrase)
        id_future = pool.submit(_decrypt_with_passphrase, id_blob, passphrase) if id_blob else None
        key = db_future.result().decode("ascii", "replace")
        identity = None
        if id_future is not None:
            try:
                identity = id_future.result().decode("ascii").strip()
            except (WrongPassphrase, UnicodeDecodeError):
                identity = None
    return _check_key(key), identity


def _unwrap_identity(data_dir: Path, passphrase: str) -> str:
    try:
        blob = (Path(data_dir) / BACKUP_KEY_FILE).read_bytes()
    except OSError as exc:
        raise SecurityError(f"The backup key file could not be read ({exc}).") from exc
    identity = _decrypt_with_passphrase(blob, passphrase).decode("ascii", "replace").strip()
    age_x25519.Identity.from_str(identity)          # raises if it isn't one
    return identity


def unlock(data_dir: Path, passphrase: str):
    """Unlocks the journal database for this process with the DATABASE
    passphrase. Raises WrongPassphrase. If the backup passphrase is the same,
    the backup key is unlocked too."""
    data_dir = Path(data_dir)
    key, identity = _unwrap_both(data_dir, passphrase)
    _open_encrypted(data_dir / DB_FILENAME, key, readonly=True).close()
    session.db_key, session.source = key, "passphrase"
    if identity:
        session.identity = identity


def ask_passphrase_on_console(prompt: str) -> str:
    """The command-line tools' passphrase prompt (diagnose_data.py,
    recover_entry.py): without echo at a terminal, otherwise one line from
    standard input.

    `getpass` alone is not enough: on POSIX it falls back to stdin when there
    is no terminal, but on Windows it reads only the console and ignores a
    piped stdin, so `echo … | python recover_entry.py …` waited forever."""
    import sys
    if sys.stdin is None or sys.stdin.isatty():
        import getpass
        return getpass.getpass(prompt)
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().rstrip("\r\n")


def unlock_backup_key(data_dir: Path, passphrase: str):
    """Unlocks the backup key for this process with the BACKUP passphrase.
    Raises WrongPassphrase."""
    session.identity = _unwrap_identity(data_dir, passphrase)


def unlock_with_plain_keys(data_dir: Path) -> bool:
    """Opens without a passphrase when unencrypted copies are being kept
    (keys.unencrypted.json exists). Returns False if there is no such file
    or it does not open the database."""
    path = Path(data_dir) / PLAIN_KEYS_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        key = _check_key(record["db_key"])
        identity = record.get("backup_identity")
        _open_encrypted(Path(data_dir) / DB_FILENAME, key, readonly=True).close()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SecurityError):
        return False
    session.db_key, session.source = key, "keyfile"
    if identity:
        session.identity = identity
    return True


def passphrase_opens(data_dir: Path, which: str, passphrase: str) -> bool:
    """Whether `passphrase` opens the database key ("database") or the
    backup key ("backup"). Used for "use the same passphrase as …"."""
    name = DB_KEY_FILE if which == "database" else BACKUP_KEY_FILE
    try:
        _decrypt_with_passphrase((Path(data_dir) / name).read_bytes(), passphrase)
        return True
    except (OSError, WrongPassphrase):
        return False


# --------------------------------------------------------------- copying
def copy_database(src, dst, skip_tables: tuple = ()):
    """Copies every table, row, index, trigger and view from one open
    database connection into another, empty one.

    Used where SQLite's own backup API cannot go: between an encrypted and a
    plain database (SQLCipher's page format differs), in both directions,
    without a plaintext file ever touching disk. The source is read inside
    one transaction, so the copy is a consistent snapshot even in WAL mode.
    Rows keep their rowids, and AUTOINCREMENT counters are carried over, so
    the copy is the same database, not merely similar data.
    """
    src.execute("BEGIN")
    try:
        objects = src.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
        tables = [(o[1], o[2]) for o in objects
                  if o[0] == "table" and not o[1].startswith("sqlite_")
                  and o[1] not in skip_tables]
        for _name, sql in tables:
            dst.execute(sql)
        for name, _sql in tables:
            columns = [r[1] for r in src.execute(f'PRAGMA table_info("{name}")')]
            quoted = ", ".join(f'"{c}"' for c in columns)
            try:
                cursor = src.execute(f'SELECT rowid, {quoted} FROM "{name}" ORDER BY rowid')
                insert = (f'INSERT INTO "{name}" (rowid, {quoted}) '
                          f'VALUES ({", ".join("?" * (len(columns) + 1))})')
            except DB_ERRORS:           # WITHOUT ROWID
                cursor = src.execute(f'SELECT {quoted} FROM "{name}"')
                insert = (f'INSERT INTO "{name}" ({quoted}) '
                          f'VALUES ({", ".join("?" * len(columns))})')
            while True:
                rows = cursor.fetchmany(500)
                if not rows:
                    break
                dst.executemany(insert, [tuple(r) for r in rows])
        has_sequence = src.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
        if has_sequence:
            seq = [tuple(r) for r in src.execute("SELECT name, seq FROM sqlite_sequence")
                   if r[0] not in skip_tables]
            if seq:
                dst.execute("DELETE FROM sqlite_sequence")
                dst.executemany("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", seq)
        for kind, name, sql in objects:
            if kind in ("index", "trigger", "view") and not name.startswith("sqlite_"):
                table = src.execute(
                    "SELECT tbl_name FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
                if table in skip_tables:
                    continue
                dst.execute(sql)
        version = src.execute("PRAGMA user_version").fetchone()[0]
        dst.execute(f"PRAGMA user_version = {int(version)}")
        dst.commit()
    finally:
        src.rollback()


def table_fingerprint(conn) -> dict:
    """{table: (row count, sha256 of every row)} — a content comparison
    that works across both bindings, used to prove a copy is exact."""
    import hashlib
    out = {}
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid'):
            digest.update(repr(tuple(row)).encode("utf-8"))
            count += 1
        out[name] = (count, digest.hexdigest())
    return out


def plain_snapshot_bytes(db_path: Path) -> bytes:
    """A complete, consistent, unencrypted image of the database, in memory.

    Plain database: SQLite's backup API into a memory database (it sees
    committed WAL content, which a file copy does not). Encrypted database:
    copy_database() out of SQLCipher into a plain memory database. Either
    way the plaintext exists only in this process's memory; it is written to
    disk only where the caller has decided an unencrypted file belongs.
    """
    db_path = Path(db_path)
    memory = sqlite3.connect(":memory:")
    try:
        if db_file_state(db_path) == "encrypted":
            source = connect(db_path, readonly=True)
            try:
                copy_database(source, memory)
            finally:
                source.close()
        else:
            source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                source.backup(memory)
            finally:
                source.close()
        # The backup API copies the source's header, WAL flag included; a
        # snapshot is a standalone file and must not claim a -wal it lacks.
        return rollback_journal_image(memory.serialize())
    finally:
        memory.close()


def rollback_journal_image(data: bytes) -> bytes:
    """The same database image, marked as using a rollback journal.

    Bytes 18-19 of the header say "WAL mode" (2, 2) for a database copied
    from a live WAL-mode file, as older backups were. SQLite refuses to use
    a WAL-marked image in memory, and without its -wal file the flag means
    nothing anyway; switching it to (1, 1) is exactly what
    `PRAGMA journal_mode = DELETE` does. Nothing else changes.
    """
    if len(data) >= 20 and data[:16] == SQLITE_MAGIC and data[18:20] == b"\x02\x02":
        return data[:18] + b"\x01\x01" + data[20:]
    return data


def open_plain_bytes(data: bytes) -> sqlite3.Connection:
    """A read-write memory database holding `data` (a plain SQLite image)."""
    conn = sqlite3.connect(":memory:")
    conn.deserialize(rollback_journal_image(data))
    return conn


def write_encrypted_from_bytes(data: bytes, destination: Path, key: str):
    """Creates an encrypted database file at `destination` whose content is
    the plain image `data`, without writing the plaintext anywhere."""
    destination = Path(destination)
    if destination.exists():
        destination.unlink()
    source = open_plain_bytes(data)
    try:
        target = _open_encrypted_new(destination, key)
        try:
            copy_database(source, target)
            _verify_same(source, target)
        finally:
            target.close()
    finally:
        source.close()


def _open_encrypted_new(path: Path, key: str):
    if sqlcipher is None:
        raise SecurityError("Encryption support is not available in this build.")
    conn = sqlcipher.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{_check_key(key)}'\"")
    return conn


def _verify_same(a, b):
    check = b.execute("PRAGMA quick_check").fetchone()
    if check is None or str(check[0]).lower() != "ok":
        raise SecurityError("The copied database failed SQLite's integrity check.")
    if table_fingerprint(a) != table_fingerprint(b):
        raise SecurityError("The copied database does not match the original.")


# ------------------------------------------------------ setting encryption up
def _cleanup_wal(path: Path):
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass


def _wrap(secret: bytes, passphrase: str) -> bytes:
    return age_passphrase.encrypt(secret, passphrase)


def _write_key_file(path: Path, secret: str, passphrase: str):
    """Writes one passphrase-protected key file and proves it opens before
    anything depends on it. A key file that does not decrypt is the one
    mistake that cannot be recovered from later."""
    atomic_write(path, _wrap(secret.encode("ascii"), passphrase))
    back = _decrypt_with_passphrase(path.read_bytes(), passphrase).decode("ascii", "replace")
    if back.strip() != secret:
        raise SecurityError(f"{path.name} did not read back correctly.")


def write_plain_keys(data_dir: Path):
    """keys.unencrypted.json, while unencrypted copies are kept: the keys of
    whatever is encrypted (the database key when the database is, the backup
    identity when backups are)."""
    record = {
        "note": ("These are the keys to this jortle_claude journal, stored "
                 "unencrypted because 'Keep unencrypted copies' is on. Turning "
                 "that option off deletes this file."),
        "db_key": session.db_key if is_encrypted_install(data_dir) else None,
        "backup_identity": session.identity if backups_encrypted(data_dir) else None,
    }
    atomic_write(Path(data_dir) / PLAIN_KEYS_FILE,
                 json.dumps(record, indent=2).encode("utf-8"))


def set_up_database_encryption(data_dir: Path, passphrase: str,
                               progress: Optional[Callable[[str], None]] = None):
    """Encrypts the journal database with a new random key, protected by the
    DATABASE passphrase. Backups are not affected (set_up_backup_encryption).

    The database must be closed by the caller first. The steps are ordered
    so that stopping at any point leaves a journal that opens:

      1. new random database key
      2. sqlcipher_export into journal.db.encrypting, and verify it: SQLite's
         integrity check plus a row-by-row fingerprint of every table
         against the original
      3. write and re-read db-key.age
      4. journal.db → journal.db.before-encryption; journal.db.encrypting →
         journal.db. (A crash between these two renames leaves no journal.db;
         finish_interrupted_setup() puts the original back.)
      5. delete journal.db.before-encryption — the unencrypted original.
    """
    if not available():
        raise SecurityError("Encryption support is not available in this build.")
    data_dir = Path(data_dir)
    db_path = data_dir / DB_FILENAME
    if db_file_state(db_path) != "plain":
        raise SecurityError("The journal is already encrypted, or missing.")
    say = progress or (lambda _msg: None)

    key = secrets.token_hex(32)

    say("Encrypting the journal…")
    tmp = data_dir / ENCRYPTING_TMP
    for leftover in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if leftover.exists():
            leftover.unlink()
    plain = sqlcipher.connect(str(db_path))
    try:
        plain.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        plain.execute("ATTACH DATABASE ? AS enc KEY ?", (str(tmp), f"x'{key}'"))
        plain.execute("SELECT sqlcipher_export('enc')")
        plain.execute("DETACH DATABASE enc")
    finally:
        plain.close()

    say("Checking the encrypted copy…")
    original = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        encrypted = _open_encrypted(tmp, key, readonly=True)
        try:
            _verify_same(original, encrypted)
        finally:
            encrypted.close()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        original.close()

    say("Writing the key file…")
    try:
        _write_key_file(data_dir / DB_KEY_FILE, key, passphrase)
    except Exception:
        tmp.unlink(missing_ok=True)
        (data_dir / DB_KEY_FILE).unlink(missing_ok=True)
        raise

    say("Replacing the unencrypted journal…")
    before = data_dir / BEFORE_ENCRYPTION
    _cleanup_wal(db_path)
    os.replace(db_path, before)
    os.replace(tmp, db_path)
    session.db_key, session.source = key, "setup"
    # Proven to open before the original goes.
    _open_encrypted(db_path, key, readonly=True).close()
    before.unlink()
    if load_config(data_dir).get("keep_unencrypted_copies"):
        # Copies are being kept (for encrypted backups); the database's
        # unencrypted copy is now one of them.
        write_plain_keys(data_dir)


def set_up_backup_encryption(data_dir: Path, passphrase: str) -> str:
    """Turns backup encryption on: a new backup identity, protected by the
    BACKUP passphrase. Every backup from now on is a .jcbackup encrypted to
    its public key. Existing backups and the database are not touched.
    Returns the public key."""
    if not available():
        raise SecurityError("Encryption support is not available in this build.")
    data_dir = Path(data_dir)
    if backups_encrypted(data_dir):
        raise SecurityError("Backup encryption is already set up.")
    identity = age_x25519.Identity.generate()
    identity_text = str(identity)
    recipient = str(identity.to_public())
    _write_key_file(data_dir / BACKUP_KEY_FILE, identity_text, passphrase)
    session.identity = identity_text
    update_config(data_dir, backup_encryption=True, backup_recipient=recipient,
                  storage_choice="encrypted")
    if load_config(data_dir).get("keep_unencrypted_copies"):
        write_plain_keys(data_dir)
    return recipient


def finish_interrupted_setup(data_dir: Path) -> list:
    """Repairs what an interrupted set_up_database_encryption() can leave behind.

    Runs at every startup, before the database is opened. Never deletes the
    only copy of anything: the unencrypted original is removed only when the
    encrypted journal beside it is present and its key file exists (it is
    checked properly once unlocked, by finish_after_unlock()).
    """
    data_dir = Path(data_dir)
    notes = []
    db_path = data_dir / DB_FILENAME
    tmp = data_dir / ENCRYPTING_TMP
    before = data_dir / BEFORE_ENCRYPTION
    state = db_file_state(db_path)
    if state == "missing" and before.is_file():
        os.replace(before, db_path)
        notes.append("an interrupted encryption was undone; the journal is unencrypted")
        state = "plain"
    if state == "plain" and tmp.exists():
        tmp.unlink()
        # The database key written for an encryption that never took effect.
        # (backup-key.age belongs to backup encryption and is left alone.)
        (data_dir / DB_KEY_FILE).unlink(missing_ok=True)
        notes.append("an unfinished encrypted copy was removed")
    return notes


def finish_after_unlock(data_dir: Path) -> list:
    """Once the encrypted journal has been opened successfully, an unencrypted
    original left by an interrupted setup is no longer the only copy."""
    before = Path(data_dir) / BEFORE_ENCRYPTION
    if before.exists() and is_encrypted_install(data_dir) and session.db_key:
        before.unlink()
        return ["an unencrypted original left by an interrupted setup was removed"]
    return []


def change_database_passphrase(data_dir: Path, old: str, new: str):
    """Re-wraps the same database key with a new passphrase. The database
    itself is untouched — its key does not change."""
    blob = (Path(data_dir) / DB_KEY_FILE).read_bytes()
    key = _check_key(_decrypt_with_passphrase(blob, old).decode("ascii", "replace"))
    _write_key_file(Path(data_dir) / DB_KEY_FILE, key, new)


def change_backup_passphrase(data_dir: Path, old: str, new: str):
    """Re-wraps the same backup identity with a new passphrase. Existing
    backups are untouched; the key inside each one keeps the passphrase it
    was made with, and this new key file opens all of them."""
    identity = _unwrap_identity(data_dir, old)
    _write_key_file(Path(data_dir) / BACKUP_KEY_FILE, identity, new)
    session.identity = identity


# ------------------------------------------------------ the unencrypted copy
def export_plain_copy(conn, destination: Path):
    """Writes an unencrypted copy of the live encrypted database, through the
    live connection (sqlcipher_export), atomically replacing any previous
    copy. Raises on failure; the previous copy is left as it was."""
    destination = Path(destination)
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn.commit()
    conn.execute("ATTACH DATABASE ? AS plain KEY ''", (str(tmp),))
    try:
        conn.execute("SELECT sqlcipher_export('plain')")
    finally:
        conn.execute("DETACH DATABASE plain")
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, destination)


def unencrypted_leftovers(data_dir: Path) -> list:
    """Unencrypted copies of the journal that encryption does not cover and
    that jortle_claude will not delete by itself: the folders of the app's
    earlier names, schema-migration safety copies, and the folders a restore
    sets aside. Listed so the user can decide (Master Spec §46.3)."""
    from .data_migration import legacy_candidates
    data_dir = Path(data_dir)
    found = []
    for legacy in legacy_candidates():
        if (legacy / DB_FILENAME).is_file():
            found.append(("Data folder of an earlier version", legacy))
    for path in sorted(data_dir.glob("journal.pre-*.db")):
        found.append(("Copy made before a database upgrade", path))
    for path in sorted(data_dir.parent.glob(f"{data_dir.name}.before-restore-*")):
        if db_file_state(path / DB_FILENAME) == "plain":
            found.append(("Journal set aside by a restore", path))
    return found
