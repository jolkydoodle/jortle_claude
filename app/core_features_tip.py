"""The one-time tooltip pointing at View → Core Features (Master Spec §51.2;
batch 4A2).

Shown while due (a first launch makes it due, core_features.first_launch;
4A2/AM-6), anchored under the menu bar's "View" item. How the window is made
(4A2/AM-7): a QFrame whose parent is the main window, with the flags
Qt.Tool | Qt.FramelessWindowHint — a frameless tool window owned by the main
window, kept above it, with no taskbar entry — and WA_ShowWithoutActivating,
so it never takes the keyboard focus from the window when it appears.

It stays attached to the main window: when the window moves or is resized it
follows, re-placed under "View"; when the window is minimized it hides, and
when the window is restored it shows again unless it was dismissed meanwhile.
The tip does this itself (an event filter on the main window), so it does not
depend on any platform's handling of owned windows.

Only "Cool!" acknowledges it, for good (the main window records that in
security.json). The × button, Escape and closing hide it for this launch
only; it is still due, so it shows again at the next launch.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton,
                               QVBoxLayout)

from .paths import DISPLAY_NAME
from .ui_util import font_scaled


def feature_names(features) -> str:
    labels = [f.label for f in features]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


class CoreFeaturesTip(QFrame):
    acknowledged = Signal()

    def __init__(self, parent, features):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("CoreFeaturesTip")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFrameShape(QFrame.Box)
        self.setLineWidth(1)
        self._menu_bar = None
        self._action = None
        self._dismissed = False
        self._hidden_with_window = False
        layout = QVBoxLayout(self)
        # The features named here come from the one list (FP-1).
        self.label = QLabel(
            f"Some parts of {DISPLAY_NAME} are optional: {feature_names(features)}. "
            "View → Core Features shows or hides them, so you can start with what "
            "you need and turn more on as you get to know the application.")
        self.label.setWordWrap(True)
        self.close_button = QToolButton()
        self.close_button.setText("×")
        self.close_button.setAutoRaise(True)
        self.close_button.setToolTip("Close (it shows again next time)")
        self.close_button.clicked.connect(self.close)
        top = QHBoxLayout()
        top.addWidget(self.label, 1)
        top.addWidget(self.close_button, 0, Qt.AlignTop)
        self.button = QPushButton("Cool!")
        self.button.clicked.connect(self._cool)
        layout.addLayout(top)
        layout.addWidget(self.button, 0, Qt.AlignRight)

    def _cool(self):
        self.acknowledged.emit()
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()        # not an acknowledgement: it shows again next launch
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # Closed (×, Escape, Cool!): it stays closed for the rest of this
        # launch, whatever the main window does.
        self._dismissed = True
        if self.parent() is not None:
            self.parent().removeEventFilter(self)
        super().closeEvent(event)

    def show_under(self, menu_bar, action):
        """Under the menu bar item `action`, inside the screen, as tall as
        its text needs at its width; from then on it follows the window."""
        self._menu_bar, self._action = menu_bar, action
        self._place()
        if self.parent() is not None:
            self.parent().installEventFilter(self)
        self.show()

    def _place(self):
        """Under "View", kept inside the screen that holds the "View"
        anchor point — so a window on a second monitor, or straddling two,
        keeps its tooltip beside it — falling back to the main window's
        screen, then the primary (4A2/AM-8)."""
        bar = self._menu_bar
        pos = bar.mapToGlobal(bar.actionGeometry(self._action).bottomLeft())
        screen = (QGuiApplication.screenAt(pos)
                  or (self.parent().screen() if self.parent() is not None else None)
                  or QApplication.primaryScreen())
        available = screen.availableGeometry() if screen is not None else None
        width = font_scaled(340)
        if available is not None:
            width = min(width, available.width() - 40)
        width = max(240, width)
        self.setFixedWidth(width)
        self.resize(width, self.layout().totalHeightForWidth(width))
        if available is not None:
            pos = QPoint(min(max(pos.x(), available.left()), available.right() - width + 1),
                         min(max(pos.y(), available.top()), available.bottom() - self.height() + 1))
        self.move(pos)

    def _close_if_window_gone(self):
        parent = self.parent()
        if parent is not None and not parent.isVisible() and not parent.isMinimized():
            self.close()

    def eventFilter(self, watched, event):
        if watched is self.parent() and not self._dismissed:
            kind = event.type()
            if kind in (QEvent.Move, QEvent.Resize) and self.isVisible():
                self._place()
            elif kind == QEvent.Hide:
                # Hidden for good (closed), not just minimized: the tooltip
                # goes with it (4A2/AM-8). Checked once the close has
                # settled, since a close can still be cancelled.
                QTimer.singleShot(0, self._close_if_window_gone)
            elif kind == QEvent.WindowStateChange:
                if watched.isMinimized():
                    if self.isVisible():
                        self._hidden_with_window = True
                        self.hide()
                elif self._hidden_with_window:
                    self._hidden_with_window = False
                    self._place()
                    self.show()
        return False
