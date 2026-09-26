"""Group 1 — one running jortle_claude per data folder (acceptance 14).

Uses real processes, because the thing under test is what happens between
processes: a first launch, a second launch while it runs, and a launch after
the first one was killed without any chance to clean up (the "crash" case).
"""
import os
import pathlib
import subprocess
import sys
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g1-inst-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])

from app.single_instance import SingleInstance, default_lock_path  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


ENV = dict(os.environ)
ENV["QT_QPA_PLATFORM"] = "offscreen"
ENV["PYTHONIOENCODING"] = "utf-8"

SECONDARY = (
    "import sys; sys.path.insert(0, {repo!r})\n"
    "from PySide6.QtCore import QCoreApplication\n"
    "app = QCoreApplication([])\n"
    "from app.single_instance import SingleInstance\n"
    "s = SingleInstance()\n"
    "acquired = s.acquire()\n"
    "print('acquired' if acquired else ('notified' if s.notify_running_instance() else 'unreached'))\n"
).format(repo=str(REPO))


def run_secondary():
    out = subprocess.run([sys.executable, "-c", SECONDARY], env=ENV, capture_output=True,
                         text=True, timeout=60)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-300:]


print("\n--- the lock and the activation channel ---")
lock_path = default_lock_path()
check("the lock lives beside the data folder, not inside it",
      lock_path.parent == root and "jortle_claude" in lock_path.name)
first = SingleInstance()
check("the first process becomes THE instance", first.acquire())
check("taking the lock did not create the data folder", not (root / "jortle_claude").exists())
activations = []
first.activationRequested.connect(lambda: activations.append(1))

result = {}
proc = subprocess.Popen([sys.executable, "-c", SECONDARY], env=ENV,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
deadline = time.monotonic() + 30
while proc.poll() is None and time.monotonic() < deadline:
    QTest.qWait(50)                      # keep this instance's event loop running
QTest.qWait(200)
stdout = proc.stdout.read().strip()
check("a second process does not get the lock, and reaches the first",
      stdout.endswith("notified"), stdout)
check("the first instance was asked to come to the front", activations == [1])
first.release()
check("after the first instance exits normally, a new launch gets the lock",
      run_secondary() == "acquired")


print("\n--- a second launch never joins a first launch's migration ---")
import shutil  # noqa: E402
import sqlite3  # noqa: E402
legacy = root / "Jortle"
legacy.mkdir()
sqlite3.connect(str(legacy / "journal.db")).execute("CREATE TABLE entries (date TEXT)").connection.close()
holder = SingleInstance()
check("(a first instance holds the lock)", holder.acquire())
blocked = subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=ENV,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
deadline = time.monotonic() + 40
while blocked.poll() is None and time.monotonic() < deadline:
    QTest.qWait(50)
check("the second launch exits", blocked.poll() == 0, f"{blocked.poll()}")
check("...without migrating or creating the data folder", not (root / "jortle_claude").exists())
check("...and without touching the legacy folder", sorted(p.name for p in legacy.iterdir()) == ["journal.db"])
holder.release()
shutil.rmtree(legacy)


print("\n--- the real application ---")
def launch():
    return subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for_server(timeout=40):
    """Until the running app answers on its activation channel."""
    probe = SingleInstance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if probe.notify_running_instance():
            return True
        time.sleep(0.3)
    return False


primary = launch()
check("the first launch starts and answers", wait_for_server())
check("the data folder was created by the running app", (root / "jortle_claude" / "journal.db").is_file())

started = time.monotonic()
second = launch()
try:
    code = second.wait(timeout=40)
except subprocess.TimeoutExpired:
    second.kill()
    code = None
check("a second launch exits by itself (no second window)", code == 0, f"exit {code}")
check("...promptly", time.monotonic() - started < 40)
check("the first instance is still running", primary.poll() is None)

primary.kill()                           # SIGKILL / TerminateProcess: no cleanup at all
primary.wait(timeout=30)
# On Linux/macOS the killed process's lock file is left behind (a stale
# lock). Whether Windows leaves the file is up to the OS; either way the
# next launch below must succeed, which is what matters.
print(f"  info  stale lock file present after the kill: {lock_path.exists()}")

third = launch()
check("a launch after the crash starts normally despite the stale lock", wait_for_server())
time.sleep(1.0)
check("...and keeps running", third.poll() is None)
third.kill()
third.wait(timeout=30)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
