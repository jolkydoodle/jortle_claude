"""Point every data-directory lookup at a throwaway folder, on every OS.

WHY THIS EXISTS. Every test used to isolate itself with one line:

    os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(...)

which is correct on Linux and does **nothing at all on Windows**.
`data_migration._data_root()` branches on `sys.platform`: Windows reads
`APPDATA`, macOS builds a path under `Path.home()`, and only Linux looks at
`XDG_DATA_HOME`. So on Windows the whole suite ran against the real
`%APPDATA%\\jortle_claude` — sharing one database between suites (which is why
settings leaked across files and the migration tests found a folder they had
not built), and, on a developer's own machine rather than a clean CI runner,
reading and writing their actual journal.

Setting every variable the platform branches on closes that. The two
migration suites build a fake filesystem root per case and call
`point_at(root)` to move the whole environment there between cases.

`HOME` and `USERPROFILE` are set as well because `Path.home()` is the
fallback on every branch — an unset `APPDATA` still resolves to
`~/AppData/Roaming`, and leaving the real home directory reachable is how a
"temporary" folder ends up somewhere permanent.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def point_at(root) -> Path:
    """Send every per-user data path the app can resolve to `root`."""
    root = Path(root)
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_DATA_HOME"] = str(root)     # Linux
    os.environ["APPDATA"] = str(root)           # Windows: %APPDATA%\jortle_claude
    os.environ["LOCALAPPDATA"] = str(root)
    os.environ["HOME"] = str(home)              # macOS, and Path.home() fallbacks
    os.environ["USERPROFILE"] = str(home)       # Path.home() on Windows
    return root


def isolate(prefix: str = "jortle-test-") -> Path:
    """A fresh throwaway root, with the environment already pointed at it.

    Call this at the top of a test module, BEFORE importing anything from
    `app` — the data directory is resolved once per process.
    """
    return point_at(tempfile.mkdtemp(prefix=prefix))
