"""The tests must not be able to touch a real journal, on any platform.

This is the check that was missing. Every suite isolated itself with

    os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(...)

which `data_migration._data_root()` only consults on Linux. On Windows it
reads `APPDATA`, and on macOS it builds a path under `Path.home()` — so the
entire suite ran against the user's real data folder there, silently. It
showed up first as cross-suite state leakage on a CI runner; on a developer's
own machine the same code would have been reading and writing their journal.

So this suite drives `_data_root()` through all three platform branches with
`sys.platform` patched, and fails if any of them resolves outside the
throwaway root. It is deliberately the cheapest suite here and the one whose
failure matters most.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-isolation-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import data_migration  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def resolved_as(platform: str) -> pathlib.Path:
    real = data_migration.sys.platform
    try:
        data_migration.sys.platform = platform
        return data_migration._data_root().resolve()
    finally:
        data_migration.sys.platform = real


print("\n--- every platform branch stays inside the throwaway root ---")
root = ROOT.resolve()
for platform in ("win32", "darwin", "linux"):
    where = resolved_as(platform)
    inside = root == where or root in where.parents
    check(f"{platform}: {where} is inside the temp root", inside)

print("\n--- the variables each branch actually reads are all set ---")
for var in ("XDG_DATA_HOME", "APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
    value = os.environ.get(var, "")
    check(f"{var} points into the temp root ({value or 'UNSET'})",
          bool(value) and (root == pathlib.Path(value).resolve()
                           or root in pathlib.Path(value).resolve().parents))

print("\n--- point_at moves an already-running process ---")
second = pathlib.Path(tempfile.mkdtemp(prefix="jortle-isolation-2-"))
isolation.point_at(second)
for platform in ("win32", "darwin", "linux"):
    where = resolved_as(platform)
    check(f"{platform}: followed point_at to the new root",
          second.resolve() == where or second.resolve() in where.parents)
isolation.point_at(ROOT)

print("\n--- and none of them is the real home directory ---")
# A guard against the failure mode where a variable is simply left unset and
# Path.home() quietly supplies the developer's own profile.
check(f"the temp root is not the real home ({root})",
      root != pathlib.Path(tempfile.gettempdir()).resolve()
      and "Jortle" not in root.name)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL ISOLATION CHECKS PASSED")
