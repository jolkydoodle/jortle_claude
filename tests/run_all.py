"""Runs every test file in this folder and prints one line per suite.

    python tests/run_all.py

Each file is a standalone script that builds real widgets against a
throwaway data directory (see isolation.py), so they can also be run one at
a time while working on whatever they cover. On Linux they need an offscreen
Qt platform, which this sets for you; on Windows and macOS they just run.

Two things here exist because of Windows specifically:

  * the child processes are decoded as UTF-8. `text=True` alone decodes with
    the locale encoding, which on Windows is cp1252 — and these suites print
    em dashes, arrows and "…", so a passing run could still blow up in the
    RUNNER while reading the output of a test that worked fine;

  * `PYTHONIOENCODING` is set for the same reason in the other direction: a
    child printing "…" to a cp1252 stdout raises UnicodeEncodeError inside
    the test, which looks like a test failure and is not;

  * the runner's OWN stdout and stderr are switched to UTF-8 as well. When
    they are a pipe (CI, or `run_all.py | more`), Python on Windows writes
    them in the locale code page, so printing a failing suite's "▶" raised
    UnicodeEncodeError in the runner and hid the rest of the failure
    details and the final FAILED: line.

A failing suite gets its full output printed. A one-line summary is enough
when everything passes and useless when something does not — particularly in
CI, where nobody can re-run it locally to see what happened.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def summarise(output: str, ok: bool) -> str:
    """The most informative single line: the verdict when a suite passed,
    the failure when it did not.

    Scanning for "PASS or FAIL or Error" from the end is how a PASSING suite
    used to be labelled with an exception string that its own test had
    deliberately provoked and caught.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if ok:
        for line in reversed(lines):
            upper = line.upper()
            if upper.startswith("ALL ") or "ALL PASS" in upper:
                return line[:70]
        return lines[-1][:70] if lines else ""
    for line in reversed(lines):
        if "FAIL" in line.upper() or "Error" in line or "error:" in line:
            return line[:70]
    return lines[-1][:70] if lines else ""


SUITE_TIMEOUT = 900   # seconds per suite


def run_suite(path: Path, env: dict) -> tuple:
    """Runs one suite; returns (exit code, its output).

    A suite that overruns SUITE_TIMEOUT is a FAIL of that suite, and the
    remaining suites still run. (Before, the timeout raised out of the whole
    runner: on 2026-10-01 a computer that went to sleep mid-run woke with a
    suite far past its limit, and the run ended with a traceback after eight
    suites.) Its own process tree is stopped by its PID, never by name:
    other Python programs may be open. The output goes to a temporary file
    rather than a pipe, so a process the suite started that outlives it
    cannot hold the runner waiting on an inherited pipe.
    """
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        process = subprocess.Popen(
            [sys.executable, str(path)], env=env, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        try:
            returncode = process.wait(timeout=SUITE_TIMEOUT)
            note = ""
        except subprocess.TimeoutExpired:
            stop_tree(process)
            returncode = 1
            note = (f"\nFAIL  {path.name} timed out after {SUITE_TIMEOUT} s; "
                    f"its process tree (PID {process.pid}) was stopped\n")
        out.seek(0)
        return returncode, out.read() + note


def stop_tree(process: subprocess.Popen) -> None:
    """Stops a suite and every process it started, by the suite's own PID."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)   # its own session (start_new_session)
        except ProcessLookupError:
            pass
    process.wait()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    env = dict(os.environ)
    if sys.platform.startswith("linux") and not env.get("DISPLAY"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONIOENCODING"] = "utf-8"

    failures = []
    outputs = {}
    for path in sorted(HERE.glob("test_*.py")):
        returncode, output = run_suite(path, env)
        ok = returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'}  {path.name:<34} {summarise(output, ok)}", flush=True)
        if not ok:
            failures.append(path.name)
            outputs[path.name] = output

    print()
    if not failures:
        print("all suites passed")
        return 0

    for name in failures:
        print("=" * 72)
        print(f"== {name}")
        print("=" * 72)
        print(outputs[name].rstrip())
        print()
    print(f"FAILED: {', '.join(failures)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
