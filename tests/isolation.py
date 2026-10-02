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

FONTS ON WINDOWS. Importing this module also gives Qt's `offscreen` platform
real fonts on Windows (`use_system_fonts_offscreen`). Without them it finds no
font families at all, draws every glyph as a box and measures text about
twice as wide as the real `windows` platform does ("Wednesday" at 9pt: 108 px
against 61 px), so layout and pixel checks fail for widths no user ever
sees. It has to happen before the QApplication exists, which is why it runs
at import: every Qt suite imports this module first.

ERRORS INSIDE QT CALLBACKS. For the same reason — every Qt suite imports this
module first — importing it also installs `qt_errors`' recorder, which fails
a suite whose run raised an error inside a Qt slot or timer, even if every
check passed (D24; see qt_errors.py).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def use_system_fonts_offscreen() -> None:
    """On Windows under the offscreen platform (the tests' default), point
    Qt at the system font folder and resolve the generic "Sans Serif" family
    it asks for to Segoe UI, the family the native platform uses. Test
    environment only; does nothing on other platforms or on the native one.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("QT_QPA_PLATFORM", "offscreen") != "offscreen":
        return
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(windir, "Fonts"))
    from PySide6.QtGui import QFont
    QFont.insertSubstitution("Sans Serif", "Segoe UI")
    # Offscreen loads fonts with FreeType, which keeps each font file open
    # through the C runtime, and the C runtime allows 512 open files by
    # default. At large interface fonts Qt opens enough fallback fonts (for
    # glyphs like ▶ and …) to reach that; the next font then fails to load
    # and Qt draws the box font instead, twice as wide — at a random point in
    # a run (measured: Segoe UI Semibold lost in the responsive sweep's 20pt
    # passes). 8192 is the C runtime's own maximum. The native platform
    # loads fonts through DirectWrite and never meets this limit.
    import ctypes
    ctypes.cdll.ucrtbase._setmaxstdio(8192)


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


use_system_fonts_offscreen()


import qt_errors  # noqa: E402

qt_errors.install()
