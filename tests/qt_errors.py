"""Fail a suite whose run raised an error inside a Qt callback (D24).

An error raised inside a Qt callback — a slot, a timer, an event handler
reached through Qt — never reaches the code that caused it: Qt hands it to
`sys.excepthook`, prints it, and carries on, so the suite still exits 0 and
the error is noticed only by chance (1R-F9: a timer firing after the window
had closed its database, visible only in CI's stderr).

`install()` replaces `sys.excepthook` with a recorder that keeps each error's
full stack and then passes it on to the original hook, and registers an exit
handler. If anything was recorded, the exit handler prints every stack under
"UNCAUGHT ERROR INSIDE A QT CALLBACK" and ends the process with exit code 1,
whatever exit code the suite itself chose. An exit handler is the one place
that sees every suite's end: some call `sys.exit(...)`, some exit only on
failure, some simply run off the end of the file.

`isolation.py` calls `install()` when it is imported, and every Qt suite
imports it first, so no suite needs its own copy (`test_isolation.py`
checks that last point).

A test that provokes errors in a callback on purpose collects them with
`expected_errors()`; those are not counted.

What this cannot see: errors inside processes a suite starts (the app run
through `jortle_claude.py`, helper scripts), errors in background threads
(`threading.excepthook`), errors Python only reports as "unraisable"
(`sys.unraisablehook`, e.g. in `__del__`), and Qt warnings that never raise
a Python exception.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import sys
import traceback

# Every error recorded outside an expected_errors() block, as a formatted stack.
recorded: list[str] = []

_original_hook = None
_collectors: list[list] = []


def _hook(kind, value, tb):
    stack = "".join(traceback.format_exception(kind, value, tb))
    if _collectors:
        _collectors[-1].append(stack)
        return
    recorded.append(stack)
    _original_hook(kind, value, tb)


def _report_at_exit():
    if not recorded:
        return
    lines = ["", "=" * 72,
             f"UNCAUGHT ERROR INSIDE A QT CALLBACK ({len(recorded)}): "
             "the suite fails whatever its own checks said "
             "(an error that ended the suite itself is listed here too)",
             "=" * 72]
    for number, stack in enumerate(recorded, 1):
        lines.append(f"--- error {number} of {len(recorded)}")
        lines.append(stack.rstrip())
    text = "\n".join(lines) + "\n"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.write(text if stream is sys.stderr else "\nUNCAUGHT ERROR INSIDE A QT CALLBACK — see stderr\n")
            stream.flush()
        except Exception:                       # noqa: BLE001 — a closed stream must not hide the exit code
            pass
    # Not sys.exit(): an exit handler cannot change the exit code that way.
    os._exit(1)


def install():
    """Record errors raised inside Qt callbacks; fail the process at exit if any."""
    global _original_hook
    if _original_hook is not None:
        return
    _original_hook = sys.excepthook
    sys.excepthook = _hook
    atexit.register(_report_at_exit)


@contextlib.contextmanager
def expected_errors():
    """Collect the errors a test provokes in a callback on purpose.

        with qt_errors.expected_errors() as caught:
            ...                   # code whose slot is meant to raise
        check("the slot raised", caught)

    Errors collected here are neither counted nor printed by the original
    hook; the test decides what they mean.
    """
    caught: list[str] = []
    _collectors.append(caught)
    try:
        yield caught
    finally:
        _collectors.remove(caught)
