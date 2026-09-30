"""An exclusive lock held by a running process, released by the OS when it dies.

Both of jortle_claude's locks — one running instance per data folder
(`single_instance.py`) and one migration at a time (`data_migration.py`) —
need the same answer to one question: is the process that took this lock
still running? Guessing from a process id written into the file is not good
enough: after a crash, Windows hands that id to the next process that
starts, and when jortle_claude runs from source every such process is called
"python". A time limit is no better: a long-running first instance must
never have its lock declared stale.

So the lock is the operating system's own byte-range/file lock on an open
file (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere). The OS drops it
the moment its process ends — a normal exit, a kill, a crash, a power cut —
and nothing about it can be confused with another process. The owner's
process id is still written into the file, for a person reading it; nothing
decides anything from it.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Windows locks a byte range, and a locked range cannot be read by anyone
# else. Lock a byte far past the text, so the owner line stays readable.
_WINDOWS_LOCK_OFFSET = 1 << 20

# How long acquire() keeps retrying a file Windows will not open (see there).
_OPEN_RETRY_SECONDS = 0.5


class ProcessLock:
    """`acquire()` never blocks; `release()` is safe to call twice."""

    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> bool:
        if self._file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        open_deadline = time.monotonic() + _OPEN_RETRY_SECONDS
        replaced = 0
        while True:
            try:
                handle = open(self.path, "a+b")
            except PermissionError:
                # Windows refuses to open a file that another process has open
                # without sharing (an older build's QLockFile) or is deleting
                # right now (another process's release()). The first means
                # someone holds the lock; the second is over in moments.
                if time.monotonic() > open_deadline:
                    return False
                time.sleep(0.02)
                continue
            try:
                if sys.platform == "win32":
                    import msvcrt
                    handle.seek(_WINDOWS_LOCK_OFFSET)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
            # release() deletes the file. On Linux/macOS a process can lock a
            # file that was deleted meanwhile, while another creates a new one
            # under the same name: only a lock on the file the path names
            # counts. (Windows cannot delete a file someone has open.)
            try:
                same = os.path.samestat(os.fstat(handle.fileno()), os.stat(self.path))
            except OSError:
                same = False
            if same:
                break
            handle.close()
            replaced += 1
            if replaced >= 5:
                return False
        self._file = handle
        try:                               # for a person reading the file only
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode())
            handle.flush()
        except OSError:
            pass
        return True

    def release(self):
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(_WINDOWS_LOCK_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()
        try:
            # Tidy, not required: a file nobody holds is free whatever it says.
            self.path.unlink()
        except OSError:
            pass
