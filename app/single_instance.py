"""One running jortle_claude per data folder (Master Spec §4.1).

Two processes editing the same live `journal.db` is the one way to lose
writing that no save path can prevent: each holds its own editor state and
each overwrites the other's saves. So the second launch does not open a
second window. It asks the running one to come to the front, and exits.

Mechanism
---------
* A `QLockFile` next to the data folder — in the per-user application-data
  root, the same place the migration lock lives — decides who is first. Qt
  records the owner's process id and host in it, and treats a lock whose
  owner is no longer running as stale and takes it over. A crash, a kill, or
  a power cut therefore never locks the user out: the next launch simply
  proceeds. (A time-based expiry is deliberately NOT used — a long-running
  first instance must not have its lock declared stale.)
* A `QLocalServer`, named from the same path, lets the second launch reach
  the first and say "activate". If the first instance cannot be reached (it
  is hung, say), the second launch still refuses to open a second window and
  tells the user why.

The lock is a guard for the application lifecycle, not a substitute for
backups or SQLite's own transaction safety.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .data_migration import APP_DIR_NAME, STAGING_PREFIX, _data_root

ACTIVATE_MESSAGE = b"activate"
CONNECT_TIMEOUT_MS = 1500


def default_lock_path() -> Path:
    """`<app-data root>/.jortle_claude-instance.lock` — beside, never inside,
    the data folder, so taking the lock can't create the data folder before
    the migration has decided what it is."""
    return _data_root() / f"{STAGING_PREFIX}-instance.lock"


def server_name_for(lock_path: Path) -> str:
    """A local-socket name unique to this user's data location."""
    digest = hashlib.sha1(str(Path(lock_path).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"{APP_DIR_NAME}-{digest}"


class SingleInstance(QObject):
    """Owns the instance lock and the activation channel for this process."""

    activationRequested = Signal()

    def __init__(self, lock_path: Path | None = None, parent=None):
        super().__init__(parent)
        self.lock_path = Path(lock_path) if lock_path else default_lock_path()
        self.server_name = server_name_for(self.lock_path)
        self._lock: QLockFile | None = None
        self._server: QLocalServer | None = None

    # ------------------------------------------------------------ primary
    def acquire(self) -> bool:
        """True if this process is now THE instance. Never blocks."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.lock_path))
        lock.setStaleLockTime(0)          # stale only when the owner is gone
        if not lock.tryLock(0):
            return False
        self._lock = lock
        self._listen()
        return True

    def _listen(self):
        # A server left behind by a crashed instance (a socket file on
        # Linux/macOS) would make listen() fail; we hold the lock, so any
        # server under this name is necessarily stale.
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if server.listen(self.server_name):
            server.newConnection.connect(self._on_connection)
            self._server = server

    def _on_connection(self):
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket):
        data = bytes(socket.readAll())
        if ACTIVATE_MESSAGE in data:
            self.activationRequested.emit()

    def release(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    # ---------------------------------------------------------- secondary
    def notify_running_instance(self) -> bool:
        """Asks the running instance to come to the front. True if it was
        reached."""
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(ACTIVATE_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True


def bring_to_front(window):
    """What the running instance does when asked. On Windows the system may
    only flash the taskbar button instead of raising the window: a process
    can't always take focus from the one the user is typing in."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
