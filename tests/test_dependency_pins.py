"""The installed dependencies are exactly the ones in constraints.txt (FP-16).

Local runs and CI only count as the same evidence when they run the same
library versions. An unpinned PySide6 once let CI install a new release
(6.12.0) that crashed every job, while the local runs used 6.11.2
(4A2-F1, deferred item D31). This suite fails whenever an installed package
differs from its pin, or a package the app needs is not installed at all.

Packages pinned for one platform only (pefile, pywin32-ctypes come with
PyInstaller on Windows) are checked where they are installed.
"""
import platform
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# What the app and the tests cannot run without; each must be installed.
REQUIRED = ["PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "sqlcipher3", "pyrage",
            "python-dateutil"]

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def normal(name: str) -> str:
    """PEP 503: names compare case-insensitively, with - _ . all alike."""
    return re.sub(r"[-_.]+", "-", name).lower()


pins = {}
for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    name, sep, version = line.partition("==")
    check(f"constraints.txt pins {line!r} exactly (name==version)", bool(sep and version.strip()))
    pins[normal(name.strip())] = (name.strip(), version.strip())

print(f"Python {platform.python_version()} ({sys.platform}); {len(pins)} pins")

installed = {normal(d.metadata["Name"]): d.version for d in metadata.distributions()}
for key, (name, version) in sorted(pins.items()):
    if key in installed:
        check(f"{name} is {version}", installed[key] == version, f"installed {installed[key]}")
    else:
        print(f"  (not installed here: {name})")

for name in REQUIRED:
    key = normal(name)
    check(f"{name} is installed and pinned", key in installed and key in pins,
          f"installed {key in installed}, pinned {key in pins}")

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
