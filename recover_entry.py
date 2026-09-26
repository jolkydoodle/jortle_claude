"""jortle_claude — pull one journal entry out of a backup, without restoring everything.

A full restore replaces your whole journal with the backup's contents, which
is the wrong tool when you only want one day back and have written other
things since. This copies a single entry — its formatting, and any photos in
it — from a backup zip into the journal you are using now, and touches
nothing else.

    python recover_entry.py <backup>                 list what is in it
    python recover_entry.py <backup> 2026-09-15      show that entry
    python recover_entry.py <backup> 2026-09-15 --write    put it back

<backup> is a .zip or an encrypted .jcbackup (you are asked for its
passphrase). If your current journal is encrypted, --write asks for its
passphrase too.

It also accepts an old data folder, or the journal.db inside one, in place
of a zip — which is what you want for a jortle_claude.before-restore-… folder (or a
Jortle.before-restore-… one from the builds before the rename).

Nothing is written without `--write`, and even then, if that date already has
writing in it, the existing version is copied to a dated file beside the
database first so it can never be the thing you lose.

It needs no Qt, so it runs even when the app will not start. (Encrypted
backups and journals need the sqlcipher3 and pyrage packages from
requirements.txt.)
"""
from __future__ import annotations

import getpass
import io
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import data_migration as dm            # noqa: E402
from app import security                        # noqa: E402


class Source:
    """A backup or an old data folder, read into memory.

    Everything is read into memory rather than unpacked into a temporary
    folder, so an encrypted backup's contents never land on disk unencrypted.
    """

    def __init__(self, db_bytes: bytes, photo_reader):
        self.db_bytes = db_bytes
        self.photo = photo_reader           # relative path -> bytes | None

    def connect(self) -> sqlite3.Connection:
        conn = security.open_plain_bytes(self.db_bytes)
        conn.row_factory = sqlite3.Row
        return conn


def _ask(prompt: str) -> str:
    return getpass.getpass(prompt)


def _from_zip_bytes(data: bytes) -> Source:
    zf = zipfile.ZipFile(io.BytesIO(data))
    if "journal.db" not in zf.namelist():
        raise SystemExit("That zip has no journal.db in it — not a jortle_claude backup.")

    def photo(rel):
        name = f"attachments/{rel}"
        return zf.read(name) if name in zf.namelist() else None
    return Source(zf.read("journal.db"), photo)


def _from_folder(folder: Path) -> Source:
    db_path = folder / "journal.db"
    if security.db_file_state(db_path) == "encrypted":
        if not security.is_encrypted_install(folder):
            raise SystemExit(f"{db_path} is encrypted, but its key file "
                             f"({security.DB_KEY_FILE}) is not beside it.")
        if not security.unlock_with_plain_keys(folder):
            try:
                security.unlock(folder, _ask(f"Database passphrase for {folder}: "))
            except security.WrongPassphrase:
                raise SystemExit("That passphrase is not correct.")
        data = security.plain_snapshot_bytes(db_path)
        security.session.clear()
    else:
        data = security.plain_snapshot_bytes(db_path)

    def photo(rel):
        path = folder / "attachments" / rel
        return path.read_bytes() if path.is_file() else None
    return Source(data, photo)


def open_source(source: Path) -> Source:
    """Takes a backup (.zip or encrypted .jcbackup), an old data folder, or
    the journal.db inside one — `jortle_claude.before-restore-…`,
    `DailyJournal`, a quarantined migration copy. Those folders are exactly
    what diagnose_data.py points at when it finds writing that the current
    journal is missing, so they have to be usable here without being zipped
    up first."""
    if not source.exists():
        raise SystemExit(f"No such file or folder: {source}")
    if source.is_dir():
        if not (source / "journal.db").is_file():
            raise SystemExit(f"No journal.db inside {source}")
        return _from_folder(source)
    if source.suffix.lower() == ".db":
        return _from_folder(source.parent)
    from app import backup
    if backup.is_encrypted_backup(source):
        try:
            _info, payload = backup.decrypt_container(
                source, _ask("Backup passphrase for this backup: "))
        except security.WrongPassphrase:
            raise SystemExit("That passphrase does not open this backup.")
        except backup.RestoreError as exc:
            raise SystemExit(str(exc))
        return _from_zip_bytes(payload)
    return _from_zip_bytes(source.read_bytes())


def list_entries(src: Source):
    conn = src.connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "entries" not in tables:
            raise SystemExit(
                "That backup's database has no entries table. It was made by a version "
                "that copied the database without its write-ahead log, and the writing "
                "is not in the file. Nothing to recover from this one."
            )
        print(f"\n{'date':<12} {'characters':>11}   last saved")
        print("-" * 50)
        found = 0
        for row in conn.execute(
            "SELECT date, LENGTH(TRIM(COALESCE(body_text, ''))) AS n, updated_at "
            "FROM entries ORDER BY date"
        ):
            if row["n"]:
                found += 1
            marker = "  " if row["n"] else "  (empty)"
            print(f"{row['date']:<12} {row['n']:>11}   {row['updated_at'] or ''}{marker}")
        print(f"\n{found} entr{'y' if found == 1 else 'ies'} with writing in them.")
    finally:
        conn.close()


def show(src: Source, date: str):
    conn = src.connect()
    try:
        row = conn.execute("SELECT * FROM entries WHERE date = ?", (date,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"{date} is not in this backup at all.")
    text = (row["body_text"] or "").strip()
    if not text:
        raise SystemExit(f"{date} is in this backup, but it is empty.")
    photos = re.findall(r'src="([^"]+)"', row["body_md"] or "")
    print(f"\n{date} — {len(text)} characters, last saved {row['updated_at']}")
    if photos:
        print(f"photos: {', '.join(photos)}")
    print("-" * 70)
    print(text[:1500] + ("\n…" if len(text) > 1500 else ""))
    print("-" * 70)
    return row


def _open_live(data_dir: Path):
    live_db = data_dir / dm.DB_FILENAME
    if security.is_encrypted_install(data_dir) and security.session.db_key is None:
        if not security.unlock_with_plain_keys(data_dir):
            try:
                security.unlock(data_dir, _ask("Database passphrase for your current journal: "))
            except security.WrongPassphrase:
                raise SystemExit("That passphrase is not correct. Nothing was changed.")
    return security.connect(live_db)


def write_back(src: Source, date: str):
    row = show(src, date)
    data_dir = dm.resolve_data_dir()
    print(f"\nWriting into: {data_dir / dm.DB_FILENAME}")

    conn = _open_live(data_dir)
    try:
        existing = conn.execute(
            "SELECT body_md, body_text FROM entries WHERE date = ?", (date,)).fetchone()
        if existing and (existing["body_text"] or "").strip():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            keep = data_dir / f"replaced-{date}-{stamp}.html"
            keep.write_text(existing["body_md"] or "", encoding="utf-8")
            print(f"The version already there ({len(existing['body_text'])} chars) was "
                  f"saved to:\n  {keep}")
            if security.is_encrypted_install(data_dir):
                print("  (That file is NOT encrypted. Delete it once you no longer need it.)")

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO entries (date, title, body_md, body_format, body_text, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET body_md=excluded.body_md, "
            "body_format=excluded.body_format, body_text=excluded.body_text, "
            "updated_at=excluded.updated_at",
            (date, row["title"] or "", row["body_md"], row["body_format"] or "html",
             row["body_text"], row["created_at"] or now, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Any photo the entry points at has to exist where the entry expects it,
    # or the writing comes back with holes in it.
    copied = 0
    for rel in re.findall(r'src="([^"]+)"', row["body_md"] or ""):
        if rel.startswith("data:") or "://" in rel:
            continue
        data = src.photo(rel)
        if data is None:
            print(f"  ! the photo {rel} is not in this backup")
            continue
        destination = data_dir / "attachments" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(data)
            copied += 1
    if copied:
        print(f"Copied {copied} photo(s) back into your attachments folder.")

    print(f"\nDone. Open jortle_claude and go to {date}.")


def main():
    args = [a for a in sys.argv[1:] if a != "--write"]
    do_write = "--write" in sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(0)

    src = open_source(Path(args[0]).expanduser())

    if len(args) == 1:
        list_entries(src)
        print("\nRun again with a date to see that entry, and --write to put it back.")
        return

    date = args[1]
    if do_write:
        write_back(src, date)
    else:
        show(src, date)
        print("\nNothing has been changed. Add --write to put this back into jortle_claude.")


if __name__ == "__main__":
    main()
