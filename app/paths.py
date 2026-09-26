"""Central definitions of where jortle_claude stores its data.

Everything the app owns (database, attachments, settings, backups) lives
under one directory, so backing it up is a matter of zipping
one folder. On Windows that is %APPDATA%\\jortle_claude; on macOS
~/Library/Application Support/jortle_claude; on Linux
~/.local/share/jortle_claude.

The folder used to carry the app's former names (`DailyJournal`, then
`Jortle`). It is renamed here, and the rename is a real migration rather than
a constant edit: `data_migration.py` finds the old folder, copies it,
validates the copy by opening the database and reading every store in it, and
only then promotes it into place — leaving the original exactly where it was
as a fallback. `Jortle` is also the name of a separately developed
application, which is why this one must have a folder of its own (Master
Spec §§2, 50).

That is also why `get_data_dir()` no longer creates the directory itself.
Creating it eagerly is precisely what would break the migration: the app would
make an empty folder, find it, conclude there was nothing to migrate, and
present the user with an empty journal sitting next to their real one. The
only code allowed to create it is the migration module, which decides once
per process what the data directory is.

`APP_NAME` is the Qt organization name. It used to be kept at the legacy
"DailyJournal" on the grounds that QSettings data was stored under it, but no
code in this application has ever read or written QSettings — every
preference lives in the database's `settings` table — so nothing depends on
the old value and it now follows the application identity. Persistent
internal identifiers that DO hold data (settings keys, table names, column
names, migration flags) are deliberately left alone: backward compatibility
beats cosmetic consistency.
"""
from __future__ import annotations

import os
from pathlib import Path

from .data_migration import APP_DIR_NAME, resolve_data_dir

APP_NAME = "jortle_claude"      # Qt organization name — see the module docstring
DISPLAY_NAME = "jortle_claude"  # the user-visible application name
DATA_DIR_NAME = APP_DIR_NAME    # the folder on disk: "jortle_claude"


def get_data_dir() -> Path:
    """The directory holding all of jortle_claude's data.

    Delegates to the migration module, which decides once per process and
    creates the directory itself — see the module docstring for why this
    function must not `mkdir` on its own.
    """
    return resolve_data_dir()


def get_attachments_dir() -> Path:
    d = get_data_dir() / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_db_path() -> Path:
    return get_data_dir() / "journal.db"


# There is no get_backups_dir() any more. Backups used to go to
# `<data dir>/backups`, inside the very folder they exist to protect, and
# calling it created that folder. The backup folder is now configurable and
# outside the data folder by default: see backup.backup_dir(). An existing
# `backups` folder is left where it is and listed (backup.legacy_backup_dirs).


# There is deliberately no get_models_dir() any more. It created
# `<data dir>/models` on demand for the removed AI feature — and because it
# went through get_data_dir(), calling it was one of the ways the data
# directory could come into existence before the migration had decided which
# directory was authoritative. Nothing in jortle_claude creates that folder now.


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"
