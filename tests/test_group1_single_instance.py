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


print("\n--- the lock never raises: an old build's lock, and a release at the same moment ---")
import tempfile  # noqa: E402

lock_dir = pathlib.Path(tempfile.mkdtemp(prefix="jortle-g1-lockraise-"))
OLD_BUILD = (
    "import sys, time\n"
    "from PySide6.QtCore import QCoreApplication, QLockFile\n"
    "app = QCoreApplication([])\n"
    "lock = QLockFile(sys.argv[1])\n"
    "lock.setStaleLockTime(0)\n"
    "print('held' if lock.tryLock(0) else 'not held', flush=True)\n"
    "time.sleep(120)\n"
)
old_path = lock_dir / "old-build.lock"
old_build = subprocess.Popen([sys.executable, "-c", OLD_BUILD, str(old_path)], env=ENV,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
check("(a process holds the lock the way an older build did, with QLockFile)",
      old_build.stdout.readline().strip() == "held")
try:
    newer = SingleInstance(lock_path=old_path)
    got = newer.acquire()
    raised = None
except Exception as exc:                  # noqa: BLE001 — reported by the check
    got, raised = None, repr(exc)
check("while an older build runs, this build refuses the lock instead of raising",
      raised is None and got is False, f"{raised} {got}")
old_build.kill()
old_build.wait(timeout=30)

RACER = (
    "import sys; sys.path.insert(0, {repo!r})\n"
    "from app.process_lock import ProcessLock\n"
    "errors = 0\n"
    "for _ in range(int(sys.argv[2])):\n"
    "    lock = ProcessLock(sys.argv[1])\n"
    "    try:\n"
    "        if lock.acquire():\n"
    "            lock.release()\n"
    "    except Exception:\n"
    "        errors += 1\n"
    "print(errors, flush=True)\n"
).format(repo=str(REPO))
race_path = str(lock_dir / "race.lock")
racers = [subprocess.Popen([sys.executable, "-c", RACER, race_path, "3000"], env=ENV,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
          for _ in range(3)]
counts = []
for racer in racers:
    try:
        out, _err = racer.communicate(timeout=180)
        counts.append(int(out.strip().splitlines()[-1]))
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        racer.kill()
        racer.wait(timeout=30)
        counts.append(None)
check("three processes taking and releasing the lock 3,000 times each never raise",
      counts == [0, 0, 0], f"errors per process: {counts}")


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
if blocked.poll() is None:                # never leave it running at a message box
    blocked.kill()
    blocked.wait(timeout=30)
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

def exit_code(process, timeout=40):
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)
        return None


def nobody_answers(timeout=30):
    """Until no process answers on the activation channel. A killed venv
    python.exe is a launcher; the interpreter under it can still answer for a
    moment, and a check made then proves nothing about crash recovery."""
    probe = SingleInstance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not probe.notify_running_instance():
            return True
        time.sleep(0.3)
    return False


primary.kill()                           # SIGKILL / TerminateProcess: no cleanup at all
primary.wait(timeout=30)
check("(the killed instance no longer answers)", nobody_answers())
print(f"  info  lock file present after the kill: {lock_path.exists()}")

third = launch()
check("a launch after the crash becomes the running instance", wait_for_server())
fourth = exit_code(launch())
# If the relaunch had refused ("already running") it would be sitting at that
# message with no activation channel, and this launch could not reach it.
check("...a later launch reaches it and exits, so the relaunch really is the instance",
      fourth == 0 and third.poll() is None, f"exit {fourth}")

print("\n--- the killed instance's process id now belongs to another Python program ---")
third.kill()
third.wait(timeout=30)
check("(the killed instance no longer answers)", nobody_answers())
sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], env=ENV)
# What Windows can do after a crash: the dead owner's process id is handed to
# the next process that starts, here a live Python program.
lines = (lock_path.read_text(encoding="utf-8", errors="replace").splitlines()
         if lock_path.exists() else [])
if len(lines) >= 2:
    lines[0] = str(sleeper.pid)
else:
    import socket  # noqa: E402
    from PySide6.QtCore import QSysInfo  # noqa: E402
    lines = [str(sleeper.pid), "python", socket.gethostname(),
             bytes(QSysInfo.machineUniqueId()).decode(errors="replace"), ""]
lock_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
fifth = launch()
check("a relaunch starts even though the old owner's process id is a live Python program",
      wait_for_server())
sixth = exit_code(launch())
check("...and it is the running instance (a later launch reaches it)", sixth == 0, f"exit {sixth}")
for process in (fifth, sleeper):
    process.kill()
    process.wait(timeout=30)
check("(it no longer answers after being stopped)", nobody_answers())


print("\n--- a migration lock held by a process that dies ---")
import tempfile  # noqa: E402

HOLDER = (
    "import sys, time; sys.path.insert(0, {repo!r})\n"
    "from app import data_migration as dm\n"
    "target = dm.target_dir()\n"
    "with dm._migration_lock(target):\n"
    "    staging = target.parent / (dm.STAGING_PREFIX + '-migrating-19990101-000000')\n"
    "    staging.mkdir(parents=True, exist_ok=True)\n"
    "    (staging / 'partial.bin').write_bytes(b'x' * 1000)\n"
    "    print('held', flush=True)\n"
    "    time.sleep(300)\n"
).format(repo=str(REPO))


def migration_root(name):
    """A fresh per-user root holding a legacy Jortle folder with one entry."""
    new_root = pathlib.Path(tempfile.mkdtemp(prefix=f"jortle-g1-miglock-{name}-"))
    isolation.point_at(new_root)
    from app.database import Database  # noqa: E402
    legacy_dir = new_root / "Jortle"
    legacy_dir.mkdir()
    old = Database(str(legacy_dir / "journal.db"))
    old.upsert_entry("2026-01-05", body_md="<p>from the old app</p>", body_format="html",
                     body_text="from the old app")
    old.close()
    failed = new_root / ".jortle_claude-migration-failed-19990101-000000"
    failed.mkdir()
    (failed / "journal.db").write_bytes(b"kept for diagnosis")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    return new_root, env


def start_holder(env, new_root):
    holder = subprocess.Popen([sys.executable, "-c", HOLDER], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    staging = new_root / ".jortle_claude-migrating-19990101-000000"
    end = time.monotonic() + 30
    while not staging.is_dir() and time.monotonic() < end:
        time.sleep(0.2)
    time.sleep(0.5)
    return holder, staging


def migrated_entry(new_root):
    db_file = new_root / "jortle_claude" / "journal.db"
    if not db_file.is_file():
        return None
    connection = sqlite3.connect(str(db_file))
    try:
        row = connection.execute("SELECT body_text FROM entries WHERE date='2026-01-05'").fetchone()
    except sqlite3.Error:
        row = None
    finally:
        connection.close()
    return row[0] if row else None


def wait_migrated(new_root, timeout=60):
    """Until the migrated journal holds the old entry. (The app answers on its
    activation channel before it migrates, so answering proves nothing here.)"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if migrated_entry(new_root) == "from the old app":
            return True
        time.sleep(0.3)
    return False


def after_migration(new_root, name):
    check(f"{name}: the migrated journal holds the old entry",
          migrated_entry(new_root) == "from the old app")
    leftovers = [p.name for p in new_root.glob(".jortle_claude-migrating-*")]
    check(f"{name}: the dead migration's partial copy is gone", not leftovers, f"{leftovers}")
    check(f"{name}: no migration lock file is left behind",
          not (new_root / ".jortle_claude-migration.lock").exists())
    check(f"{name}: the kept copy of a failed migration is untouched",
          (new_root / ".jortle_claude-migration-failed-19990101-000000" / "journal.db").read_bytes()
          == b"kept for diagnosis")
    check(f"{name}: the legacy folder is still there", (new_root / "Jortle" / "journal.db").is_file())


def launch_in(env):
    return subprocess.Popen([sys.executable, str(REPO / "jortle_claude.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


# (a) The holder is killed first; then the app starts.
dead_root, dead_env = migration_root("dead")
holder, staging = start_holder(dead_env, dead_root)
check("(a migration holder took the lock and began a copy)", staging.is_dir())
holder.kill()
holder.wait(timeout=30)
time.sleep(1.0)                           # the interpreter under a venv launcher goes too
started = time.monotonic()
app_process = launch_in(dead_env)
ready = wait_migrated(dead_root) and wait_for_server()
check("a launch after the migration holder was killed migrates and starts without the long wait",
      ready and time.monotonic() - started < 60, f"{time.monotonic() - started:.1f} s")
after_migration(dead_root, "killed before the launch")
app_process.kill()
app_process.wait(timeout=30)
check("(stopped)", nobody_answers())

# (b) The holder is alive when the app starts, and killed while the app waits.
live_root, live_env = migration_root("live")
holder, staging = start_holder(live_env, live_root)
app_process = launch_in(live_env)
time.sleep(8.0)
check("while a live process holds the migration lock, a launch does not migrate",
      not (live_root / "jortle_claude").exists() and app_process.poll() is None)
holder.kill()
holder.wait(timeout=30)
started = time.monotonic()
ready = wait_migrated(live_root) and wait_for_server()
check("once that process dies, the waiting launch migrates and starts promptly",
      ready and time.monotonic() - started < 60, f"{time.monotonic() - started:.1f} s")
after_migration(live_root, "killed while the launch waited")
app_process.kill()
app_process.wait(timeout=30)
check("(stopped)", nobody_answers())

print("\n--- a second launch brings the startup window forward (unlock, error boxes) ---")
# The first instance runs the real jortle_claude.py under a thin wrapper that
# records every bring_to_front() call (offscreen there is no other way to see
# inside it). JORTLE_TEST_PASSPHRASE makes the wrapper unlock the unlock
# window through its own button handler once it has been brought forward.
RECORDER = (
    "import os, runpy, sys\n"
    "sys.path.insert(0, {repo!r})\n"
    "log, script = sys.argv[1], sys.argv[2]\n"
    "sys.argv = [script]\n"
    "import app.single_instance as si\n"
    "from PySide6.QtCore import QTimer\n"
    "real = si.bring_to_front\n"
    "def recording(window):\n"
    "    with open(log, 'a', encoding='utf-8') as f:\n"
    "        f.write(type(window).__name__ + '\\n')\n"
    "    real(window)\n"
    "    phrase = os.environ.get('JORTLE_TEST_PASSPHRASE')\n"
    "    if phrase and type(window).__name__ == 'UnlockDialog':\n"
    "        def unlock():\n"
    "            window.passphrase.setText(phrase)\n"
    "            window._try()\n"
    "        QTimer.singleShot(3000, unlock)\n"
    "si.bring_to_front = recording\n"
    "runpy.run_path(script, run_name='__main__')\n"
).format(repo=str(REPO))
PASSPHRASE = "correct horse battery staple"

from app import data_migration as dm  # noqa: E402
from app import security  # noqa: E402


def recorded(log, name, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if log.exists() and name in log.read_text(encoding="utf-8").split():
            return True
        time.sleep(0.2)
    return False


def start_recorded(env, log):
    return subprocess.Popen([sys.executable, "-c", RECORDER, str(log), str(REPO / "jortle_claude.py")],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def startup_root(name):
    new_root = pathlib.Path(tempfile.mkdtemp(prefix=f"jortle-g1-front-{name}-"))
    isolation.point_at(new_root)
    dm.reset_for_tests()
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    return new_root, env


def encrypted_install():
    data_dir = dm.resolve_data_dir()
    from app.database import Database  # noqa: E402
    Database().close()
    security.set_up_database_encryption(data_dir, PASSPHRASE)
    security.session.clear()
    return data_dir


def fingerprint_dir(folder):
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in folder.rglob("*") if p.is_file())


# The unlock window, then the main window after unlocking.
unlock_root, unlock_env = startup_root("unlock")
unlock_data = encrypted_install()
log = unlock_root / "front.log"
first = start_recorded(dict(unlock_env, JORTLE_TEST_PASSPHRASE=PASSPHRASE), log)
check("(the first instance is up, at its unlock window)", wait_for_server() and first.poll() is None)
time.sleep(2.0)
before = fingerprint_dir(unlock_data)                 # as the first instance left it at the window
second = exit_code(launch_in(unlock_env))
after_second = fingerprint_dir(unlock_data)
check("a second launch while the first is at its unlock window exits", second == 0, f"exit {second}")
check("...and the unlock window is brought forward", recorded(log, "UnlockDialog"),
      log.read_text(encoding="utf-8") if log.exists() else "(nothing recorded)")
check("...without the second launch touching the journal (checked before the first unlocks)",
      after_second == before)
opened = False
end = time.monotonic() + 40
while time.monotonic() < end and not opened:
    third = exit_code(launch_in(unlock_env))
    opened = third == 0 and recorded(log, "MainWindow", timeout=2)
check("after unlocking, the first instance opens its journal, and a later launch brings "
      "the main window forward", opened and first.poll() is None,
      log.read_text(encoding="utf-8") if log.exists() else "")
first.kill()
first.wait(timeout=30)
check("(stopped)", nobody_answers())

# The "key file missing" box.
key_root, key_env = startup_root("missing-key")
key_data = encrypted_install()
(key_data / security.DB_KEY_FILE).rename(key_data / "moved-away.age")
log = key_root / "front.log"
first = start_recorded(key_env, log)
check("(the first instance is up, at its 'key file missing' message)",
      wait_for_server() and first.poll() is None)
time.sleep(2.0)
second = exit_code(launch_in(key_env))
check("a second launch while the first shows 'key file missing' exits, and brings that message "
      "forward", second == 0 and recorded(log, "QMessageBox"),
      f"exit {second}; {log.read_text(encoding='utf-8') if log.exists() else '(nothing recorded)'}")
first.kill()
first.wait(timeout=30)
check("(stopped)", nobody_answers())

# The "migration failed" box: a legacy folder whose database is not one.
failed_root, failed_env = startup_root("migration-failed")
broken = failed_root / "Jortle"
broken.mkdir()
(broken / "journal.db").write_bytes(b"this is not a database" * 100)
log = failed_root / "front.log"
first = start_recorded(failed_env, log)
check("(the first instance is up, at its 'migration failed' message)",
      wait_for_server() and first.poll() is None)
time.sleep(3.0)
second = exit_code(launch_in(failed_env))
check("a second launch while the first shows 'migration failed' exits, and brings that message "
      "forward", second == 0 and recorded(log, "QMessageBox"),
      f"exit {second}; {log.read_text(encoding='utf-8') if log.exists() else '(nothing recorded)'}")
first.kill()
first.wait(timeout=30)
check("(stopped)", nobody_answers())

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
