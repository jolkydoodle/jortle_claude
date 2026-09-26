"""Restoration backups: making them, checking them, keeping them, restoring them.

There is one backup system (Master Spec §46). What a backup file looks like
depends on whether the user has set up BACKUP encryption (security.py; it is
independent of database encryption):

  not set up       jortle_claude-backup-<time>.zip       an ordinary zip
  encrypted        jortle_claude-backup-<time>.jcbackup  encrypted
  encrypted, keeping unencrypted copies
                   both files, same name, same contents ("twins")

If encrypted backups were chosen but no backup passphrase has been set yet,
no backup is made at all (BackupPaused) rather than an unencrypted one.

The unencrypted .zip ("the payload")
------------------------------------
    manifest.json   what is inside, with a SHA-256 for every other member,
                    a backup_id, the kind (manual/automatic) and counts
    journal.db      a consistent, unencrypted snapshot of the database
    attachments/…   every photo, in the same layout as on disk

The encrypted .jcbackup
-----------------------
A zip (stored, not compressed) that anyone can open, holding:

    README-RECOVERY.txt  how to decrypt this file without jortle_claude
    backup-info.json     backup_id, time, kind, and the SHA-256 of payload.zip.age
    backup-key.age       the backup identity, encrypted with the backup passphrase
    payload.zip.age      the .zip above, encrypted with age to that identity

So a .jcbackup carries everything needed to open it except the backup passphrase,
and the standard `age` tool opens it: see README-RECOVERY.txt / docs/RECOVERY.md.

Checking
--------
A backup is not reported as made until it has been read back from disk and
checked (§46.4): every member's SHA-256 against the manifest, the zip's own
CRCs, and the database opened and integrity-checked. An encrypted backup is
checked in full before it is encrypted; then the file on disk is read back
and, when this session holds the backup key, decrypted again for the same
checks ("full"). Without the key, the encrypted payload read back from disk
must match, byte for byte, what was encrypted ("contents and written file").
The level is recorded.

The index (jortle_claude-backups.json in the backup folder) has one record
per FILE, not per backup: a copy of a backup made by hand in the same folder
shares its backup_id but is its own record, never marked as checked or as
made by jortle_claude, and so never touched by retention.

Retention (approved in the Group 2 plan)
----------------------------------------
Default: keep everything. Optionally keep only the newest N automatic
backups. Only files jortle_claude made and checked are ever deleted; manual
backups, the newest checked backup, the backup just made, and any file
jortle_claude did not make or could not check are kept. "Newest" is decided
by (time to the microsecond, then the order backups were recorded), so two
backups made in the same second are still told apart.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from . import __version__, security
from .data_migration import DB_FILENAME, STAGING_PREFIX, _data_root, _unique_path
from .paths import get_attachments_dir, get_data_dir, get_db_path

BACKUP_PREFIX = "jortle_claude-backup-"
PLAIN_SUFFIX = ".zip"
ENCRYPTED_SUFFIX = ".jcbackup"
INDEX_FILE = "jortle_claude-backups.json"
PAYLOAD_FORMAT = "jortle_claude-backup"
PAYLOAD_FORMAT_VERSION = 2
CONTAINER_FORMAT = "jortle_claude-encrypted-backup"
CONTAINER_FORMAT_VERSION = 1
PAYLOAD_NAME = "payload.zip.age"
INFO_NAME = "backup-info.json"
README_NAME = "README-RECOVERY.txt"
DEFAULT_BACKUP_DIR_NAME = "jortle_claude Backups"
AUTOMATIC_INTERVAL = timedelta(hours=24)

README_TEXT = """jortle_claude encrypted backup
==============================

This file is an ordinary zip archive. It holds one encrypted backup of a
jortle_claude journal, and everything needed to decrypt it except the
backup passphrase.

  backup-info.json   when the backup was made; not secret
  backup-key.age     the backup key, encrypted with your backup passphrase
  payload.zip.age    the backup itself, encrypted with that key (age format)

Opening it WITHOUT jortle_claude, using the free `age` tool
(https://age-encryption.org, version 1.1 or later):

  1. Unzip this file into an empty folder.
  2. In that folder, run:
         age --decrypt -i backup-key.age -o payload.zip payload.zip.age
     age asks for the backup passphrase that was in use when this backup
     was made. (backup-key.age from any later backup of the same journal, or
     from the backup folder, works too, with the passphrase it was saved with.)
  3. payload.zip is an ordinary zip. Inside it:
         journal.db     an ordinary, unencrypted SQLite database
         attachments/   your photos
         manifest.json  a SHA-256 checksum for every other file

journal.db opens in any SQLite tool (for example "DB Browser for SQLite").
Entries are in the `entries` table: `date`, `title`, and `body_md`, which
holds the formatted text as HTML (plain text is in `body_text`).

Without the backup passphrase this backup cannot be decrypted by anyone,
including by jortle_claude.
"""


class RestoreError(Exception):
    pass


class BackupError(Exception):
    pass


class NeedsPassphrase(RestoreError):
    """An encrypted backup this session holds no key for."""


class BackupPaused(BackupError):
    """Encrypted backups were chosen but no backup passphrase is set yet."""


# ------------------------------------------------------------------ places
def default_backup_dir() -> Path:
    """Outside the data folder, so that losing or restoring the data folder
    never takes the backups with it (deferred item D8)."""
    return _data_root() / DEFAULT_BACKUP_DIR_NAME


def backup_dir(data_dir: Optional[Path] = None) -> Path:
    cfg = security.load_config(data_dir or get_data_dir())
    chosen = cfg.get("backup_dir")
    return Path(chosen) if chosen else default_backup_dir()


def legacy_backup_dirs(data_dir: Optional[Path] = None) -> list:
    """Where earlier versions put backups (inside the data folder). Never
    moved or deleted — listed so the user knows they are there."""
    old = Path(data_dir or get_data_dir()) / "backups"
    try:
        if old.is_dir() and any(old.iterdir()):
            return [old]
    except OSError:
        pass
    return []


def default_backup_filename() -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    # Restore never relies on the name — it identifies a backup by its
    # contents — so older `DailyJournal-backup-*.zip` files still restore.
    return f"{BACKUP_PREFIX}{stamp}{PLAIN_SUFFIX}"


def _unique_stem(folder: Path) -> str:
    base = Path(default_backup_filename()).stem
    stem, n = base, 2
    while any((folder / (stem + s)).exists() for s in (PLAIN_SUFFIX, ENCRYPTED_SUFFIX)):
        stem, n = f"{base}-{n}", n + 1
    return stem


# ----------------------------------------------------------------- checks
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(conn) -> tuple:
    def count(table):
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except security.DB_ERRORS:
            return 0
    notes = count("reader_notes_scoped") or count("reader_notes")
    return count("entries"), count("calendar_events"), notes


def check_database_bytes(data: bytes) -> tuple:
    """Opens a plain database image and proves it is a readable journal.
    Returns (entries, events, notes). Raises RestoreError saying what is wrong."""
    if not data:
        raise RestoreError("The backup's database file is missing or empty.")
    try:
        conn = security.open_plain_bytes(data)
    except Exception as exc:
        raise RestoreError(f"The backup's database could not be opened ({exc}).") from exc
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise RestoreError("The backup's database failed SQLite's integrity check.")
        tables = {row[0] for row in
                  conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "entries" not in tables:
            raise RestoreError(
                "The backup contains no journal entries table. This can happen with "
                "backups made by an earlier version, which copied the database file "
                "without its write-ahead log. Your current data has not been touched."
            )
        return _counts(conn)
    except security.DB_ERRORS as exc:
        raise RestoreError(f"The backup's database could not be read ({exc}).") from exc
    finally:
        conn.close()


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name


def verify_payload(source) -> tuple:
    """Checks an unencrypted backup zip (a path or a file object).

    Returns (manifest, has_checksums). has_checksums is False for backups
    made before checksums existed; those are still checked for readable zip
    data and a readable journal, and restore warns that less could be checked.
    """
    try:
        zf = zipfile.ZipFile(source, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise RestoreError(f"This file is not a readable backup ({exc}).") from exc
    with zf:
        names = zf.namelist()
        if "manifest.json" not in names or DB_FILENAME not in names:
            raise RestoreError("This file doesn't look like a jortle_claude backup.")
        bad = [n for n in names if not _safe_member(n)]
        if bad:
            raise RestoreError(f"The backup contains an unsafe file name ({bad[0]}).")
        try:
            broken = zf.testzip()
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise RestoreError(f"The backup is damaged ({exc}).") from exc
        if broken is not None:
            raise RestoreError(f"The backup is damaged: {broken} does not match its checksum.")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RestoreError("The backup's manifest could not be read.") from exc
        if not isinstance(manifest, dict):
            raise RestoreError("The backup's manifest could not be read.")
        files = manifest.get("files")
        has_checksums = isinstance(files, dict)
        if has_checksums:
            listed = set(files)
            present = set(names) - {"manifest.json"}
            if listed - present:
                raise RestoreError(
                    f"The backup is incomplete: {sorted(listed - present)[0]} is missing.")
            if present - listed:
                raise RestoreError(
                    f"The backup contains a file its manifest does not list "
                    f"({sorted(present - listed)[0]}).")
            for name, meta in files.items():
                h = hashlib.sha256()
                with zf.open(name) as member:
                    for chunk in iter(lambda: member.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != meta.get("sha256"):
                    raise RestoreError(f"The backup is damaged: {name} does not match the manifest.")
        check_database_bytes(zf.read(DB_FILENAME))
    return manifest, has_checksums


# -------------------------------------------------------------- building
@dataclass
class BackupSummary:
    entry_count: int
    event_count: int
    notes_count: int
    photo_count: int
    zip_path: Path                  # the main file: .jcbackup, or .zip
    size_bytes: int
    backup_id: str = ""
    kind: str = "manual"
    encrypted: bool = False
    plain_path: Optional[Path] = None   # the unencrypted twin, if one was made
    verified: bool = False
    verification: str = ""              # VERIFIED_FULL or VERIFIED_WRITTEN
    pruned: list = field(default_factory=list)


# How thoroughly a backup was checked when it was made.
VERIFIED_FULL = "full"
VERIFIED_WRITTEN = "contents and written file"


def _write_payload(fileobj, db_bytes: bytes, data_dir: Path, backup_id: str, kind: str,
                   progress_cb=None) -> dict:
    attachments_dir = get_attachments_dir()
    photos = sorted(p for p in attachments_dir.rglob("*") if p.is_file())
    entry_count, event_count, notes_count = check_database_bytes(db_bytes)
    total = len(photos) + 2
    files = {}
    done = 0
    with zipfile.ZipFile(fileobj, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(DB_FILENAME, db_bytes)
        files[DB_FILENAME] = {"sha256": hashlib.sha256(db_bytes).hexdigest(),
                              "size": len(db_bytes)}
        done += 1
        if progress_cb:
            progress_cb(done, total)
        for photo in photos:
            arcname = photo.relative_to(data_dir).as_posix()
            h = hashlib.sha256()
            size = 0
            with open(photo, "rb") as src, zf.open(arcname, "w", force_zip64=True) as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
                    dst.write(chunk)
                    size += len(chunk)
            files[arcname] = {"sha256": h.hexdigest(), "size": size}
            done += 1
            if progress_cb:
                progress_cb(done, total)
        manifest = {
            "app": "jortle_claude",
            "app_version": __version__,
            "format": PAYLOAD_FORMAT,
            "format_version": PAYLOAD_FORMAT_VERSION,
            "backup_id": backup_id,
            "kind": kind,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "entry_count": entry_count,
            "calendar_event_count": event_count,
            "readers_notes_count": notes_count,
            "photo_count": len(photos),
            "files": files,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        done += 1
        if progress_cb:
            progress_cb(done, total)
    return manifest


def _identity():
    if not security.session.identity:
        return None
    return security.age_x25519.Identity.from_str(security.session.identity)


def _write_container(path: Path, payload: bytes, info: dict, data_dir: Path):
    recipient = security.age_x25519.Recipient.from_str(info["recipient"])
    encrypted = security.pyrage.encrypt(payload, [recipient])
    info = dict(info, payload=PAYLOAD_NAME,
                payload_sha256=hashlib.sha256(encrypted).hexdigest(),
                payload_size=len(encrypted))
    key_file = (data_dir / security.BACKUP_KEY_FILE).read_bytes()
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(README_NAME, README_TEXT)
        zf.writestr(INFO_NAME, json.dumps(info, indent=2))
        zf.writestr(security.BACKUP_KEY_FILE, key_file)
        zf.writestr(PAYLOAD_NAME, encrypted)
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    return tmp, encrypted


def read_container_info(path: Path) -> dict:
    with zipfile.ZipFile(path, "r") as zf:
        return json.loads(zf.read(INFO_NAME).decode("utf-8"))


def is_encrypted_backup(path: Path) -> bool:
    """By content, not by name: a .jcbackup renamed to .zip is still one."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return INFO_NAME in names and PAYLOAD_NAME in names


def decrypt_container(path: Path, passphrase: Optional[str] = None) -> tuple:
    """Returns (info, payload bytes) for an encrypted backup.

    Uses this session's backup key when it fits. A backup from a different
    installation (or made before a new key) needs its own passphrase, which
    unlocks the backup-key.age stored inside it. Raises NeedsPassphrase,
    security.WrongPassphrase or RestoreError.
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            info = json.loads(zf.read(INFO_NAME).decode("utf-8"))
            blob = zf.read(PAYLOAD_NAME)
            wrapped_key = zf.read(security.BACKUP_KEY_FILE) \
                if security.BACKUP_KEY_FILE in zf.namelist() else None
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, UnicodeDecodeError) as exc:
        raise RestoreError(f"The encrypted backup could not be read ({exc}).") from exc
    if info.get("format") != CONTAINER_FORMAT:
        raise RestoreError("This is not a jortle_claude encrypted backup.")
    if hashlib.sha256(blob).hexdigest() != info.get("payload_sha256"):
        raise RestoreError("The encrypted backup is damaged: its checksum does not match.")
    if not security.available():
        raise RestoreError("Encryption support is not available in this build.")
    identities = []
    if passphrase is None:
        ident = _identity()
        if ident is None:
            raise NeedsPassphrase("This backup is encrypted.")
        identities.append(ident)
    else:
        if wrapped_key is None:
            raise RestoreError("The encrypted backup does not contain its key file.")
        try:
            secret = security.age_passphrase.decrypt(wrapped_key, passphrase)
        except security.pyrage.DecryptError as exc:
            raise security.WrongPassphrase("That passphrase does not open this backup.") from exc
        identities.append(security.age_x25519.Identity.from_str(secret.decode("ascii").strip()))
    try:
        payload = security.pyrage.decrypt(blob, identities)
    except security.pyrage.DecryptError as exc:
        if "No matching keys" in str(exc) and passphrase is None:
            raise NeedsPassphrase("This backup was made with a different key.") from exc
        raise RestoreError(f"The encrypted backup could not be decrypted ({exc}).") from exc
    return info, payload


def verify_container(path: Path, passphrase: Optional[str] = None) -> tuple:
    info, payload = decrypt_container(path, passphrase)
    manifest, _ = verify_payload(io.BytesIO(payload))
    if manifest.get("backup_id") != info.get("backup_id"):
        raise RestoreError("The encrypted backup's contents do not match its label.")
    return info, payload, manifest


def create_backup(kind: str = "manual", destination: Optional[Path] = None,
                  progress_cb=None) -> BackupSummary:
    """Makes one backup, checks it, and records it.

    `destination` is a file the user picked (Export Backup…); otherwise the
    backup goes into the configured backup folder (Back Up Now, automatic
    backups). Every route goes through here. Raises BackupPaused when
    encrypted backups were chosen but cannot be made yet.
    """
    data_dir = get_data_dir()
    cfg = security.load_config(data_dir)
    paused = security.backups_paused_reason(data_dir)
    if paused:
        raise BackupPaused(paused)
    encrypted = security.backups_encrypted(data_dir)
    if encrypted and not security.available():
        raise BackupError("Encryption support is not available in this build.")
    keep_plain = encrypted and bool(cfg.get("keep_unencrypted_copies"))

    if destination is not None:
        destination = Path(destination)
        folder = destination.parent
        stem = destination.stem if destination.suffix in (PLAIN_SUFFIX, ENCRYPTED_SUFFIX) \
            else destination.name
    else:
        folder = backup_dir(data_dir)
        stem = None
    folder.mkdir(parents=True, exist_ok=True)
    if stem is None:
        stem = _unique_stem(folder)
    main_path = folder / (stem + (ENCRYPTED_SUFFIX if encrypted else PLAIN_SUFFIX))
    plain_path = folder / (stem + PLAIN_SUFFIX) if (keep_plain or not encrypted) else None

    backup_id = uuid.uuid4().hex
    created = _now()
    db_bytes = security.plain_snapshot_bytes(get_db_path())
    temps = []
    verification = VERIFIED_FULL
    try:
        if encrypted:
            buffer = io.BytesIO()
            manifest = _write_payload(buffer, db_bytes, data_dir, backup_id, kind, progress_cb)
            payload = buffer.getvalue()
            verify_payload(io.BytesIO(payload))
            info = {"format": CONTAINER_FORMAT, "format_version": CONTAINER_FORMAT_VERSION,
                    "backup_id": backup_id, "created_at": created, "kind": kind,
                    "recipient": cfg["backup_recipient"]}
            tmp, ciphertext = _write_container(main_path, payload, info, data_dir)
            temps.append(tmp)
            # Read back from disk — what is checked is the file that will be
            # restored, not the bytes still in memory.
            if _identity() is not None:
                _, again, _ = verify_container(tmp)
                if again != payload:
                    raise BackupError("The encrypted backup did not read back identically.")
            else:
                _check_written_container(tmp, ciphertext, backup_id)
                verification = VERIFIED_WRITTEN
            if plain_path is not None:
                ptmp = plain_path.with_name(plain_path.name + ".tmp")
                temps.append(ptmp)
                security.atomic_write(ptmp, payload)
                verify_payload(ptmp)
        else:
            tmp = main_path.with_name(main_path.name + ".tmp")
            temps.append(tmp)
            with open(tmp, "wb") as fh:
                manifest = _write_payload(fh, db_bytes, data_dir, backup_id, kind, progress_cb)
                fh.flush()
                os.fsync(fh.fileno())
            verify_payload(tmp)
    except RestoreError as exc:
        for t in temps:
            Path(t).unlink(missing_ok=True)
        raise BackupError(f"The backup failed its check and was not kept: {exc}") from exc
    except Exception:
        for t in temps:
            Path(t).unlink(missing_ok=True)
        raise

    os.replace(temps[0], main_path)
    if encrypted and plain_path is not None:
        os.replace(temps[1], plain_path)
    summary = BackupSummary(
        manifest["entry_count"], manifest["calendar_event_count"],
        manifest["readers_notes_count"], manifest["photo_count"],
        main_path, main_path.stat().st_size, backup_id=backup_id, kind=kind,
        encrypted=encrypted, plain_path=plain_path if encrypted else None, verified=True,
        verification=verification)

    if folder.resolve() == backup_dir(data_dir).resolve():
        made = [(main_path.name, encrypted, verification)]
        if encrypted and plain_path is not None:
            made.append((plain_path.name, False, VERIFIED_FULL))
        for name, enc, level in made:
            record_backup(folder, BackupRecord(
                file=name, backup_id=backup_id, created_at=created, kind=kind,
                encrypted=enc, made_by_app=True, verified=True, verified_at=created,
                verification=level))
        if kind == "automatic" and cfg.get("keep_automatic"):
            summary.pruned = prune(folder, int(cfg["keep_automatic"]), protect=backup_id)
    security.update_config(data_dir, last_backup={
        "created_at": created, "path": str(main_path), "encrypted": encrypted,
        "verified": True, "verification": verification, "kind": kind}, last_error=None)
    return summary


def _check_written_container(path: Path, ciphertext: bytes, backup_id: str):
    """The check for an encrypted backup when this session does not hold the
    backup key: the payload was already checked in full before encryption,
    so the file on disk must open as a container, carry the right label, and
    hold exactly the ciphertext that was produced."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            info = json.loads(zf.read(INFO_NAME).decode("utf-8"))
            blob = zf.read(PAYLOAD_NAME)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise RestoreError(f"The encrypted backup could not be read back ({exc}).") from exc
    if bad is not None or blob != ciphertext or info.get("backup_id") != backup_id \
            or hashlib.sha256(blob).hexdigest() != info.get("payload_sha256"):
        raise RestoreError("The encrypted backup did not read back identically.")


def _now() -> str:
    # Microseconds: two backups in the same second must still sort correctly.
    return datetime.now().isoformat(timespec="microseconds")


def export_backup(dest_zip_path: Path, progress_cb=None) -> BackupSummary:
    """The earlier name, kept for callers and tests: a manual backup to a
    chosen file."""
    return create_backup("manual", Path(dest_zip_path), progress_cb)


# ------------------------------------------------------------- the index
INDEX_VERSION = 2


@dataclass
class BackupRecord:
    """One backup FILE in the backup folder."""
    file: str
    backup_id: str = ""
    created_at: str = ""
    kind: str = "unknown"           # manual | automatic | unknown
    encrypted: bool = False
    made_by_app: bool = False       # written (and checked) by create_backup / twins
    verified: bool = False
    verified_at: Optional[str] = None
    verification: str = ""
    seq: int = 0                    # order recorded: the tie-breaker for equal times

    def sort_key(self):
        return (self.created_at or "", self.seq)


def _load_index(folder: Path) -> dict:
    """{file name: BackupRecord}. Reads the version-1 index (one record per
    backup_id, with encrypted_file / plain_file) and converts it: a checked
    version-1 record was written by the app; an unchecked one was found by a
    scan, so it is not treated as the app's."""
    try:
        data = json.loads((folder / INDEX_FILE).read_text(encoding="utf-8"))
        raw = data.get("backups", {})
        out = {}
        if data.get("version", 1) < 2:
            for n, (bid, rec) in enumerate(sorted(raw.items(),
                                                  key=lambda kv: kv[1].get("created_at", ""))):
                for attr, enc in (("encrypted_file", True), ("plain_file", False)):
                    name = rec.get(attr)
                    if name:
                        out[name] = BackupRecord(
                            file=name, backup_id=bid, created_at=rec.get("created_at", ""),
                            kind=rec.get("kind", "unknown"), encrypted=enc,
                            made_by_app=bool(rec.get("verified")),
                            verified=bool(rec.get("verified")),
                            verified_at=rec.get("verified_at"),
                            verification=VERIFIED_FULL if rec.get("verified") else "", seq=n)
            return out
        fields = BackupRecord.__dataclass_fields__
        for name, rec in raw.items():
            out[name] = BackupRecord(file=name, **{k: v for k, v in rec.items()
                                                  if k in fields and k != "file"})
        return out
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_index(folder: Path, records: dict):
    data = {"version": INDEX_VERSION, "backups": {
        name: {k: v for k, v in vars(r).items() if k != "file"}
        for name, r in sorted(records.items(), key=lambda kv: kv[1].sort_key())}}
    security.atomic_write(folder / INDEX_FILE, json.dumps(data, indent=2).encode("utf-8"))


def _next_seq(records: dict) -> int:
    return max((r.seq for r in records.values()), default=0) + 1


def record_backup(folder: Path, record: BackupRecord):
    records = _load_index(folder)
    old = records.get(record.file)
    record.seq = old.seq if old is not None and old.seq else _next_seq(records)
    records[record.file] = record
    _save_index(folder, records)


def _identify_file(path: Path) -> Optional[tuple]:
    """(backup_id, created_at, kind, encrypted) read from a backup's own
    plaintext metadata, without decrypting anything."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if INFO_NAME in names:
                info = json.loads(zf.read(INFO_NAME).decode("utf-8"))
                return (info.get("backup_id") or f"file:{path.name}",
                        info.get("created_at", ""), info.get("kind", "unknown"), True)
            if "manifest.json" in names:
                m = json.loads(zf.read("manifest.json").decode("utf-8"))
                return (m.get("backup_id") or f"file:{path.name}",
                        m.get("exported_at", ""), m.get("kind", "unknown"), False)
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError, KeyError):
        return None
    return None


def scan_backups(folder: Optional[Path] = None) -> list:
    """Every backup file in the folder, newest first, reconciled with the
    index: records whose files are gone are dropped, and files the index does
    not know (copied in by hand, or made before the index existed) are added
    as unchecked and not made by the app."""
    folder = Path(folder or backup_dir())
    if not folder.is_dir():
        return []
    records = _load_index(folder)
    changed = False
    for name in list(records):
        if not (folder / name).is_file():
            del records[name]
            changed = True
    for path in sorted(folder.iterdir()):
        if path.suffix not in (PLAIN_SUFFIX, ENCRYPTED_SUFFIX) or path.name in records \
                or not path.is_file():
            continue
        found = _identify_file(path)
        if found is None:
            continue
        bid, created, kind, enc = found
        records[path.name] = BackupRecord(file=path.name, backup_id=bid, created_at=created,
                                          kind=kind, encrypted=enc, seq=_next_seq(records))
        changed = True
    if changed:
        try:
            _save_index(folder, records)
        except OSError:
            pass
    return sorted(records.values(), key=BackupRecord.sort_key, reverse=True)


def _deletable(rec: BackupRecord) -> bool:
    return rec.made_by_app and rec.verified


def prune(folder: Path, keep_automatic: int, protect: Optional[str] = None) -> list:
    """Deletes checked automatic backups beyond the newest `keep_automatic`.

    Counts backups (backup_ids), not files, and deletes only files the app
    made and checked. Never a manual backup, never the newest checked backup
    of any kind, never `protect` (the backup just made), and never a file the
    app did not make or could not check — such as a copy made by hand.
    Returns the file names deleted."""
    if keep_automatic is None or keep_automatic < 1:
        return []
    records = scan_backups(folder)                      # newest first
    checked = [r for r in records if _deletable(r)]
    newest = checked[0].backup_id if checked else None
    order = []
    for r in checked:
        if r.kind == "automatic" and r.backup_id not in order:
            order.append(r.backup_id)
    doomed = [bid for bid in order[keep_automatic:] if bid not in (newest, protect)]
    deleted = []
    index = _load_index(folder)
    for r in checked:
        if r.backup_id in doomed:
            try:
                (folder / r.file).unlink()
                deleted.append(r.file)
                index.pop(r.file, None)
            except OSError:
                pass
    if deleted:
        _save_index(folder, index)
    return deleted


@dataclass
class BackupStatus:
    folder: Path
    last_success: Optional[str]
    last_verified: bool
    last_encrypted: bool
    overdue: bool
    automatic: bool
    same_device: Optional[bool]
    last_error: Optional[str]
    last_verification: str = ""
    paused_reason: Optional[str] = None     # automatic backups are not running


def status(data_dir: Optional[Path] = None) -> BackupStatus:
    data_dir = Path(data_dir or get_data_dir())
    cfg = security.load_config(data_dir)
    folder = backup_dir(data_dir)
    candidates = []
    for rec in scan_backups(folder):
        if rec.verified and rec.made_by_app:
            candidates.append((rec.created_at, rec.seq, rec.encrypted, rec.verification))
    last = cfg.get("last_backup") or {}
    if last.get("created_at") and last.get("verified"):
        candidates.append((last["created_at"], 0, bool(last.get("encrypted")),
                           last.get("verification", VERIFIED_FULL)))
    candidates.sort(reverse=True)
    if candidates:
        last_success, _seq, enc, level = candidates[0]
        verified = True
    else:
        last_success, enc, level, verified = None, False, "", False
    automatic = cfg.get("automatic_backups", "daily") != "off"
    overdue = False
    if automatic:
        if last_success is None:
            overdue = True
        else:
            try:
                overdue = datetime.now() - datetime.fromisoformat(last_success) > AUTOMATIC_INTERVAL
            except ValueError:
                overdue = True
    same = None
    try:
        probe = folder if folder.exists() else folder.parent
        same = os.stat(probe).st_dev == os.stat(data_dir).st_dev
    except OSError:
        pass
    paused = security.automatic_backups_waiting_reason(data_dir) if automatic else None
    return BackupStatus(folder, last_success, verified, enc, overdue, automatic, same,
                        cfg.get("last_error"), level, paused)


def automatic_backup_due(data_dir: Optional[Path] = None) -> bool:
    st = status(data_dir)
    return st.automatic and st.overdue and not st.paused_reason


# ------------------------------------------------- unencrypted copies (twins)
def _twins_by_id(records: list) -> dict:
    """{backup_id: {"encrypted": [records], "plain": [records]}}"""
    groups = {}
    for r in records:
        g = groups.setdefault(r.backup_id, {"encrypted": [], "plain": []})
        g["encrypted" if r.encrypted else "plain"].append(r)
    return groups


def create_missing_twins(folder: Optional[Path] = None, progress_cb=None) -> tuple:
    """"Keep unencrypted copies" turned on: an unencrypted .zip beside every
    encrypted backup that has none. Files that are copies of the same backup
    get one twin between them; a damaged copy is reported and the next copy
    of that backup is tried. The encrypted files are only read.
    Returns (created, failed) lists of (name, reason)."""
    folder = Path(folder or backup_dir())
    created, failed = [], []
    groups = [g for g in _twins_by_id(scan_backups(folder)).values()
              if g["encrypted"] and not g["plain"]]
    for i, g in enumerate(groups):
        # The app's own, checked file first; hand-made copies after.
        candidates = sorted(g["encrypted"], key=lambda r: (not r.made_by_app, not r.verified))
        done = False
        for rec in candidates:
            if done:
                # The backup has its twin; other copies of it are only
                # checked, so a damaged file in the folder is still reported.
                try:
                    verify_container(folder / rec.file)
                except NeedsPassphrase:
                    failed.append((rec.file, "made with a different backup key"))
                except (RestoreError, OSError) as exc:
                    failed.append((rec.file, f"damaged copy (another copy of this backup is "
                                             f"fine): {exc}"))
                continue
            try:
                _info, payload, _manifest = verify_container(folder / rec.file)
                plain = folder / (Path(rec.file).stem + PLAIN_SUFFIX)
                if plain.exists():
                    raise BackupError(f"{plain.name} already exists")
                tmp = plain.with_name(plain.name + ".tmp")
                security.atomic_write(tmp, payload)
                verify_payload(tmp)
                os.replace(tmp, plain)
                now = _now()
                record_backup(folder, BackupRecord(
                    file=plain.name, backup_id=rec.backup_id, created_at=rec.created_at,
                    kind=rec.kind, encrypted=False, made_by_app=True, verified=True,
                    verified_at=now, verification=VERIFIED_FULL))
                if not rec.verified or rec.verification != VERIFIED_FULL:
                    rec.verified, rec.verified_at, rec.verification = True, now, VERIFIED_FULL
                    record_backup(folder, rec)
                created.append((plain.name, ""))
                done = True
            except NeedsPassphrase:
                failed.append((rec.file, "made with a different backup key"))
            except (RestoreError, BackupError, OSError) as exc:
                failed.append((rec.file, str(exc)))
        if progress_cb:
            progress_cb(i + 1, len(groups))
    return created, failed


def delete_twins(folder: Optional[Path] = None) -> tuple:
    """"Keep unencrypted copies" turned off: deletes each unencrypted .zip the
    app made as a twin, when an encrypted file of the same backup is present
    and checks out. Unencrypted backups with no encrypted counterpart, and
    unencrypted files the app did not make, are kept and returned so the user
    can decide about them. Returns (deleted, kept)."""
    folder = Path(folder or backup_dir())
    deleted, kept = [], []
    for g in _twins_by_id(scan_backups(folder)).values():
        if not g["plain"]:
            continue
        good_encrypted = False
        for rec in g["encrypted"]:
            if rec.verified and rec.verification == VERIFIED_FULL:
                good_encrypted = True
                break
            try:
                verify_container(folder / rec.file)
                rec.verified, rec.verified_at, rec.verification = True, _now(), VERIFIED_FULL
                record_backup(folder, rec)
                good_encrypted = True
                break
            except (RestoreError, OSError):
                continue
        for plain in g["plain"]:
            if not good_encrypted or not plain.made_by_app:
                kept.append(plain.file)             # never delete the only good copy
                continue
            try:
                (folder / plain.file).unlink()
                deleted.append(plain.file)
            except OSError:
                kept.append(plain.file)
    if deleted:
        scan_backups(folder)                        # drops the deleted files' records
    return deleted, kept


class NeedsBackupKey(BackupError):
    """Unencrypted twins of encrypted backups need the backup passphrase."""


def set_keep_unencrypted_copies(db, enabled: bool, progress_cb=None) -> dict:
    """Turns "Keep unencrypted copies" on or off (Master Spec §46.3).

    On: an unencrypted copy of the database (if the database is encrypted)
    and an unencrypted twin for each encrypted backup (if backups are
    encrypted), plus keys.unencrypted.json. Off: deletes exactly those.
    Encrypted files are never modified in either direction.
    """
    data_dir = get_data_dir()
    db_encrypted = security.is_encrypted_install(data_dir)
    backups_enc = security.backups_encrypted(data_dir)
    if not db_encrypted and not backups_enc:
        raise BackupError("Nothing is encrypted, so there is nothing to keep copies of.")
    if db_encrypted and security.session.db_key is None:
        raise BackupError("The journal is locked.")
    result = {"created": [], "failed": [], "deleted": [], "kept": []}
    if enabled:
        if backups_enc and not security.session.identity:
            raise NeedsBackupKey("The backup key is not unlocked.")
        security.update_config(data_dir, keep_unencrypted_copies=True)
        security.write_plain_keys(data_dir)
        if db_encrypted:
            db.export_plain_copy(data_dir / security.MIRROR_DB)
            result["created"].append((security.MIRROR_DB, ""))
        if backups_enc:
            made, failed = create_missing_twins(progress_cb=progress_cb)
            result["created"] += made
            result["failed"] = failed
    else:
        security.update_config(data_dir, keep_unencrypted_copies=False)
        for name in (security.MIRROR_DB, security.PLAIN_KEYS_FILE,
                     security.MIRROR_DB + ".tmp"):
            path = data_dir / name
            if path.exists():
                path.unlink()
                if not name.endswith(".tmp"):
                    result["deleted"].append(name)
        deleted, kept = delete_twins()
        result["deleted"] += deleted
        result["kept"] = kept
    return result


def copy_key_to_backup_folder(data_dir: Optional[Path] = None):
    """backup-key.age and db-key.age beside the backups too (each protected
    by its passphrase), so losing the data folder's copies is not the end of
    the journal. Each .jcbackup also carries its own backup-key.age."""
    data_dir = Path(data_dir or get_data_dir())
    folder = backup_dir(data_dir)
    for name in (security.BACKUP_KEY_FILE, security.DB_KEY_FILE):
        src = data_dir / name
        if src.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            security.atomic_write(folder / name, src.read_bytes())


# ---------------------------------------------------------------- restore
# Files that describe THIS installation rather than the journal in a backup.
# A restore brings in the backup's journal and keeps these, so restoring
# never switches encryption, keys or backup settings on or off by surprise.
INSTALLATION_FILES = (security.CONFIG_FILE, security.DB_KEY_FILE, security.BACKUP_KEY_FILE,
                      security.PLAIN_KEYS_FILE, "data_version.json")
# Folders carried from the old data folder: not part of any backup, and not
# the restore's to throw away (an earlier version kept backups in `backups`;
# `models` is several GB left by a removed feature).
CARRIED_FOLDERS = ("models", "backups")


def read_backup_payload(path: Path, passphrase: Optional[str] = None) -> tuple:
    """(payload source, manifest, has_checksums, encrypted) for any backup.
    The payload source is the file itself for a .zip, or bytes in memory for
    a decrypted .jcbackup (the plaintext is never written to disk here)."""
    path = Path(path)
    if is_encrypted_backup(path):
        _info, payload, manifest = verify_container(path, passphrase)
        return io.BytesIO(payload), manifest, True, True
    manifest, has_checksums = verify_payload(path)
    return path, manifest, has_checksums, False


def restore_backup(zip_path: Path, passphrase: Optional[str] = None) -> dict:
    """Replaces the current journal with a backup's. Returns its manifest,
    with "_has_checksums" added.

    Checked completely before anything is touched (verify_payload). The new
    data folder is built beside the old one, then swapped in by rename; the
    old one is moved aside to `<data>.before-restore-<time>`, never deleted —
    that is the unconditional pre-restore snapshot (Master Spec §47). If the
    installation is encrypted, the restored journal is encrypted with this
    installation's key before it is put in place, so no unencrypted database
    lands on disk.
    """
    data_dir = get_data_dir()
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    source, manifest, has_checksums, _was_encrypted = read_backup_payload(zip_path, passphrase)
    encrypted_install = security.is_encrypted_install(data_dir)
    cfg = security.load_config(data_dir)

    staging = _unique_path(data_dir.parent / f"{STAGING_PREFIX}-restoring-{stamp}")
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(source, "r") as zf:
            for name in zf.namelist():
                if name in ("manifest.json", DB_FILENAME) or name.endswith("/"):
                    continue
                zf.extract(name, path=staging)
            db_bytes = security.rollback_journal_image(zf.read(DB_FILENAME))
        if encrypted_install:
            security.write_encrypted_from_bytes(db_bytes, staging / DB_FILENAME,
                                                security.session.db_key)
            if cfg.get("keep_unencrypted_copies"):
                security.atomic_write(staging / security.MIRROR_DB, db_bytes)
        else:
            security.atomic_write(staging / DB_FILENAME, db_bytes)
        _check_restored(staging / DB_FILENAME)
        for name in INSTALLATION_FILES:
            if (data_dir / name).is_file():
                shutil.copy2(data_dir / name, staging / name)
    except RestoreError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise RestoreError(f"The backup could not be unpacked ({exc}).") from exc

    # Unique even for two restores in the same second: moving onto an
    # existing folder would nest the journal inside the earlier safety copy.
    safety_dir = _unique_path(data_dir.parent / f"{data_dir.name}.before-restore-{stamp}")
    if data_dir.exists():
        shutil.move(str(data_dir), str(safety_dir))
    try:
        staging.rename(data_dir)
    except OSError as exc:
        # Put the user's data back before reporting. Losing the restore is an
        # inconvenience; losing what they already had is not.
        if safety_dir.exists() and not data_dir.exists():
            shutil.move(str(safety_dir), str(data_dir))
        shutil.rmtree(staging, ignore_errors=True)
        raise RestoreError(
            f"The restored copy could not be moved into place ({exc}). "
            f"Your data has been left exactly as it was."
        ) from exc

    for folder_name in CARRIED_FOLDERS:
        old = safety_dir / folder_name
        if old.exists() and not (data_dir / folder_name).exists():
            shutil.move(str(old), str(data_dir / folder_name))

    manifest = dict(manifest)
    manifest["_has_checksums"] = has_checksums
    manifest["_safety_dir"] = str(safety_dir)
    return manifest


def _check_restored(db_path: Path):
    """The restored database, opened the way the app will open it."""
    try:
        conn = security.connect(db_path, readonly=True)
    except (security.SecurityError, *security.DB_ERRORS) as exc:
        raise RestoreError(f"The restored database could not be opened ({exc}).") from exc
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise RestoreError("The restored database failed SQLite's integrity check.")
        conn.execute("SELECT COUNT(*) FROM entries").fetchone()
    except security.DB_ERRORS as exc:
        raise RestoreError(f"The restored database could not be read ({exc}).") from exc
    finally:
        conn.close()


# Kept for the tests and tools that snapshot a database file directly.
def _snapshot_database(db_path: Path, destination: Path):
    """A complete, consistent, unencrypted copy of the database in a file.
    (The backup itself no longer writes one — see security.plain_snapshot_bytes.)"""
    data = security.plain_snapshot_bytes(Path(db_path))
    security.atomic_write(Path(destination), data)
