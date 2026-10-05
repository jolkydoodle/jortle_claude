"""Help → Keyboard Shortcuts and Help → Recovery Guide (G4-D4, batch 4A).

Both are read-only windows inside the application. The shortcut list is
generated from the command table (app/commands.py), so it never shows a
shortcut the application does not use; the same table backs Settings →
Hotkeys. The Recovery Guide shows docs/RECOVERY.md, the recovery
documentation shipped with the application, and never hands a link to the
operating system: clicking one starts no program (4A/AM-5).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget
)

from . import commands

RECOVERY_GUIDE = Path(__file__).resolve().parent.parent / "docs" / "RECOVERY.md"


def shortcuts_table(parent: QWidget | None = None) -> QTableWidget:
    """Command | Shortcut | Where, one row per command with a shortcut."""
    rows = commands.shortcut_rows()
    table = QTableWidget(len(rows), 3, parent)
    table.setHorizontalHeaderLabels(["Command", "Shortcut", "Where"])
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(row, column, item)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    for column in range(3):
        header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    table.resizeRowsToContents()
    return table


def _fit_to_screen(dialog: QDialog, width_chars: int, height_lines: int):
    metrics = dialog.fontMetrics()
    width = metrics.averageCharWidth() * width_chars
    height = metrics.lineSpacing() * height_lines
    screen = QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, available.width() - 40)
        height = min(height, available.height() - 80)
    dialog.resize(width, height)


class KeyboardShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.table = shortcuts_table(self)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(buttons)
        _fit_to_screen(self, 90, 30)


class RecoveryGuideDialog(QDialog):
    def __init__(self, parent=None, path: Path = RECOVERY_GUIDE):
        super().__init__(parent)
        self.setWindowTitle("Recovery Guide")
        self.browser = QTextBrowser(self)
        # Links never leave the application: Qt neither follows them itself
        # nor passes them to the system browser (4A/AM-5). A link within the
        # guide scrolls to its place; any other link's address is shown
        # below, so it can be copied by hand.
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._on_link)
        self.link_label = QLabel("")
        self.link_label.setWordWrap(True)
        self.link_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        try:
            self.browser.setMarkdown(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self.browser.setPlainText(
                f"The Recovery Guide could not be read from {path}:\n{exc}")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, stretch=1)
        layout.addWidget(self.link_label)
        layout.addWidget(buttons)
        _fit_to_screen(self, 90, 40)

    def _on_link(self, url: QUrl):
        if url.isRelative() and url.hasFragment() and not url.path():
            self.browser.scrollToAnchor(url.fragment())
            return
        self.link_label.setText(f"Link (not opened): {url.toString()}")
