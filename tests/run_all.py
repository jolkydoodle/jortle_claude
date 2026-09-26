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
    the test, which looks like a test failure and is not.

A failing suite gets its full output printed. A one-line summary is enough
when everything passes and useless when something does not — particularly in
CI, where nobody can re-run it locally to see what happened.
"""
from __future__ import annotations

import os
import subprocess
import sys
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


def main() -> int:
    env = dict(os.environ)
    if sys.platform.startswith("linux") and not env.get("DISPLAY"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONIOENCODING"] = "utf-8"

    failures = []
    outputs = {}
    for path in sorted(HERE.glob("test_*.py")):
        result = subprocess.run(
            [sys.executable, str(path)], env=env, capture_output=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        ok = result.returncode == 0
        output = (result.stdout or "") + (result.stderr or "")
        print(f"{'ok  ' if ok else 'FAIL'}  {path.name:<34} {summarise(output, ok)}")
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
