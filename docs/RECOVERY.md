# Recovering a jortle_claude journal without jortle_claude

Your journal must stay readable even if this application stops working. This
page lists every file jortle_claude keeps and how to open each one with free,
standard tools.

Encryption is optional, and the database and the backups are encrypted
separately, each with its own passphrase (you may have chosen the same one
for both):

- the **database passphrase** protects `db-key.age`, the key to `journal.db`;
- the **backup passphrase** protects `backup-key.age`, the key to `.jcbackup`
  backups.

If you never set encryption up, everything is an ordinary SQLite database or
zip file, and only the section "Unencrypted files" applies.

## Where the files are

| What | Windows | macOS | Linux |
|---|---|---|---|
| Data folder | `%APPDATA%\jortle_claude` | `~/Library/Application Support/jortle_claude` | `~/.local/share/jortle_claude` |
| Default backup folder | `%APPDATA%\jortle_claude Backups` | `~/Library/Application Support/jortle_claude Backups` | `~/.local/share/jortle_claude Backups` |

You can choose a different backup folder in File → Backups & Security. It is
recorded in `security.json` in the data folder.

## What is in the data folder

| File | What it is | Encrypted? |
|---|---|---|
| `journal.db` | The database: entries, projects, Reader's Notes, calendar, tasks, version history, settings | Only if encryption is set up |
| `attachments/` | Photos used in your writing | No |
| `db-key.age` | The database key, encrypted with your database passphrase | Yes (age, passphrase) |
| `backup-key.age` | The backup key, encrypted with your backup passphrase | Yes (age, passphrase) |
| `security.json` | Encryption and backup settings. Contains no secrets | No |
| `journal.unencrypted.db` | Only while "Keep unencrypted copies" is on | No |
| `keys.unencrypted.json` | Only while "Keep unencrypted copies" is on: the keys of whatever is encrypted, readable | No |
| `data_version.json` | Records the data-folder migration | No |

## Unencrypted files

- **`journal.db`** (not encrypted), **`journal.unencrypted.db`**, and the
  `journal.db` inside any `.zip` backup are ordinary SQLite 3 databases. Open
  them with the `sqlite3` command-line tool or with
  [DB Browser for SQLite](https://sqlitebrowser.org).
- Entries are in the `entries` table. `date` is the day (YYYY-MM-DD),
  `title` is the title, `body_md` holds the formatted text as HTML, and
  `body_text` holds the same text without formatting. Projects are in
  `projects` (`content_md` / `content_text`). Reader's Notes are in
  `reader_notes_scoped`. Earlier versions of an entry are in
  `entry_revisions`.
- **`.zip` backups** open with any zip tool. Inside:
  - `journal.db`, the database as above;
  - `attachments/`, the photos;
  - `manifest.json`, with a SHA-256 checksum for every other file, so you
    can check nothing is damaged.

## Encrypted backups (`.jcbackup`)

A `.jcbackup` file is an ordinary zip archive (rename it to `.zip` if your
tool needs that). It contains its own `README-RECOVERY.txt` with these steps.

You need the free [`age`](https://age-encryption.org) tool, version 1.1 or
later, and your backup passphrase.

1. Unzip the `.jcbackup` into an empty folder. You get `backup-info.json`,
   `backup-key.age`, `payload.zip.age` and `README-RECOVERY.txt`.
2. Decrypt the backup:

   ```
   age --decrypt -i backup-key.age -o payload.zip payload.zip.age
   ```

   `age` asks for the passphrase that protects `backup-key.age`.
3. `payload.zip` is an ordinary `.zip` backup, described above.

Which passphrase? The `backup-key.age` inside a backup is protected by the
passphrase that was in use when that backup was made. The backup key itself
never changes, so if you have since changed your backup passphrase, you can use
`backup-key.age` from your backup folder (or your data folder) instead: it is
protected by your current backup passphrase and opens every backup of that journal.

## The encrypted database (`journal.db` with encryption set up)

The database is encrypted with [SQLCipher](https://www.zetetic.net/sqlcipher/)
4, using its default settings and a raw 256-bit key. The key is stored in
`db-key.age`, encrypted with your database passphrase.

1. Get the key (64 hexadecimal characters):

   ```
   age --decrypt -o key.txt db-key.age
   ```

   `age` asks for your database passphrase. **`key.txt` is the key to your
   journal. Delete it when you are done.**
2. Open the database with the `sqlcipher` command-line tool:

   ```
   sqlcipher journal.db
   sqlite> PRAGMA key = "x'PASTE-THE-64-CHARACTERS-HERE'";
   sqlite> SELECT date, title FROM entries;
   ```

   In DB Browser for SQLite (the SQLCipher edition), choose "Raw key",
   paste `0x` followed by the 64 characters, and "SQLCipher 4 defaults".
3. To make an unencrypted copy:

   ```
   sqlite> ATTACH DATABASE 'plain.db' AS plain KEY '';
   sqlite> SELECT sqlcipher_export('plain');
   sqlite> DETACH DATABASE plain;
   ```

If jortle_claude still runs, File → Backups & Security → "Keep unencrypted
copies" does the same thing for you.

## If you lose a passphrase

Whatever that passphrase protects cannot be decrypted without it — by you,
by jortle_claude, or by anyone else. There is no reset.

- **Database passphrase lost, backup passphrase known:** restore your newest
  backup in a new installation (File → Restore from Backup…). It needs only
  the backup passphrase.
- **Backup passphrase lost, database passphrase known:** the journal itself
  still opens, but no `.jcbackup` can be opened — including new ones, which
  use the same backup key. Until that is resolved, File → Export Readable
  Archive gives you an unencrypted, readable copy of the journal.

What can still be recovered in any case:

- anything in an unencrypted copy: `journal.unencrypted.db` and `.zip`
  backups, if "Keep unencrypted copies" was on; `.zip` backups made before
  encryption was set up;
- the folders of the application's earlier names (`Jortle`, `DailyJournal`)
  and `journal.pre-*.db` files, which jortle_claude never deletes;
- the photos in `attachments/`, which are not encrypted in the data folder.

## If a key file is missing

`db-key.age` and `backup-key.age` are also kept in the backup folder, and
every encrypted backup carries a `backup-key.age`. jortle_claude refuses to
start without `db-key.age` rather than starting with an empty journal. Put a
copy back into the data folder:

- `db-key.age`: from the backup folder;
- `backup-key.age`: from the backup folder, or from inside any `.jcbackup`.

If no copy of `db-key.age` survives, restore the newest backup instead
(File → Restore from Backup… in a new installation), which needs only your
backup passphrase.

## Tools included with jortle_claude

Both run with Python, without starting the application, and change nothing
unless asked:

- `python diagnose_data.py` reports on every data folder: size, whether it
  is encrypted, what it contains, and what jortle_claude would do with it.
  Add `--unlock` to include the contents of an encrypted journal.
- `python recover_entry.py <backup or folder> [date] [--write]` lists, shows,
  or puts back a single entry from a `.zip` backup, a `.jcbackup`, or an old
  data folder. It asks for a passphrase when one is needed. Everything it
  reads from an encrypted backup stays in memory.
