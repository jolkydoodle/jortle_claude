"""Settings → Hotkeys: the editor of the assigned keys (Master Spec §51.1;
batch 4B, 4B-D4).

One row per command. A configurable command has a key editor and a Default
button; the six standard edit keys (Undo, Redo, Cut, Copy, Paste, Select
All) are the platform's: shown, not editable (4B-D3). Edits are only
proposed: every change re-checks the whole set (commands.validate), an
invalid row is highlighted with its reason in the row, every problem is
listed under the table, and Apply stays disabled while any row is invalid.
Apply stores the set and applies it at once to every route; closing the
window without Apply, or Discard Changes, drops the edits. Restore All
Defaults and a row's Default button are edits like any other, taking
effect on Apply.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QKeySequenceEdit, QLabel, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from . import commands
from .ui_util import FlowLayout

COLUMNS = ["Command", "Shortcut", "", "Where", "Note"]
COL_COMMAND, COL_KEY, COL_DEFAULT, COL_WHERE, COL_NOTE = range(5)
ERROR_COLOUR = QColor(220, 60, 60, 90)
WARNING_COLOUR = QColor(230, 170, 40, 90)
SYSTEM_NOTE = "Set by the system"


def _keys_text(keys) -> list[str]:
    return [commands.portable(k) for k in keys]


class HotkeysPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.pending: dict[str, list[QKeySequence]] = {}
        self.problems: list[commands.Problem] = []
        self.editors: dict[str, QKeySequenceEdit] = {}
        self.default_buttons: dict[str, QPushButton] = {}
        self.rows: dict[str, int] = {}

        self.table = QTableWidget(len(commands.COMMANDS), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(False)
        for row, spec in enumerate(commands.COMMANDS):
            self.rows[spec.id] = row
            self.table.setItem(row, COL_COMMAND, self._item(commands.plain_label(spec.id)))
            self.table.setItem(row, COL_WHERE, self._item(spec.where))
            self.table.setItem(row, COL_NOTE, self._item(""))
            self.table.setItem(row, COL_DEFAULT, self._item(""))
            if spec.assignable:
                editor = QKeySequenceEdit()
                editor.setMaximumSequenceLength(1)
                editor.setClearButtonEnabled(True)
                editor.setObjectName(f"hotkey_{spec.id}")
                editor.keySequenceChanged.connect(
                    lambda seq, c=spec.id: self._on_key_edited(c, seq))
                self.editors[spec.id] = editor
                self.table.setItem(row, COL_KEY, self._item(""))
                self.table.setCellWidget(row, COL_KEY, editor)
                button = QPushButton("Default")
                button.setToolTip("Put back this command's default key (takes effect on Apply)")
                button.clicked.connect(lambda _checked=False, c=spec.id: self.set_default(c))
                self.default_buttons[spec.id] = button
                self.table.setCellWidget(row, COL_DEFAULT, button)
            else:
                self.table.setItem(row, COL_KEY, self._item(commands.shortcut_text(spec.id)))
                self.table.item(row, COL_NOTE).setText(SYSTEM_NOTE)

        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setObjectName("HotkeyLoadNotes")
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setObjectName("HotkeyProblems")
        self.problems_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.restore_all_button = QPushButton("Restore All Defaults")
        self.restore_all_button.clicked.connect(self.restore_all_defaults)
        self.discard_button = QPushButton("Discard Changes")
        self.discard_button.clicked.connect(self.reload)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        # A flow, not a row: at a large interface font one row of the three
        # was wider than a small screen, and a page sets the window's floor
        # (Part 36; as the colour swatches, 4A-F1).
        buttons = FlowLayout()
        buttons.addWidget(self.restore_all_button)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.apply_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.notes_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.problems_label)
        layout.addLayout(buttons)

        self.reload()
        self.fit_table()

    @staticmethod
    def _item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    # ------------------------------------------------------------ sizing (bug 44)
    def fit_table(self):
        """One row height for every row, from the tallest control, and the
        columns as wide as their contents. The table scrolls sideways within
        itself when the window is narrower, so the page never forces the
        Settings window wider (Part 36); the window opens at
        `preferred_width()` as far as the screen allows
        (SettingsDialog._starting_size, 4B/AM-5)."""
        tallest = max([self.fontMetrics().height() + 8]
                      + [w.sizeHint().height() for w in self.editors.values()]
                      + [b.sizeHint().height() for b in self.default_buttons.values()])
        header = self.table.verticalHeader()
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setDefaultSectionSize(tallest)
        # No stretched column: one would be squeezed below its text, with no
        # scrollbar, in a narrow window (bug 44); the table scrolls instead.
        columns = self.table.horizontalHeader()
        columns.setStretchLastSection(False)
        for column in range(len(COLUMNS)):
            columns.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.resizeColumnsToContents()
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def preferred_width(self) -> int:
        """The width that shows every column whole, with a vertical scrollbar."""
        return (sum(self.table.columnWidth(c) for c in range(len(COLUMNS)))
                + 2 * self.table.frameWidth()
                + self.table.verticalScrollBar().sizeHint().width())

    # ------------------------------------------------------------ editing
    def reload(self):
        """The rows as now assigned; drops every unapplied edit."""
        self.pending = commands.assignments()
        self.notes_label.setText("\n".join(commands.load_notes))
        self.notes_label.setVisible(bool(commands.load_notes))
        self._show_pending()

    def _show_pending(self):
        for command_id, editor in self.editors.items():
            keys = self.pending.get(command_id, [])
            blocked = editor.blockSignals(True)
            editor.setKeySequence(keys[0] if keys else QKeySequence())
            editor.blockSignals(blocked)
        self.revalidate()

    def _on_key_edited(self, command_id: str, seq: QKeySequence):
        self.set_key(command_id, seq)

    def set_key(self, command_id: str, seq: QKeySequence):
        """A row's new key (what its key editor does); an empty key means
        none. The shifted =/+ key is shown and kept as Shift+= (4B-D6)."""
        seq = commands.canonical(seq) if not seq.isEmpty() else seq
        self.pending[command_id] = [] if seq.isEmpty() else [seq]
        editor = self.editors[command_id]
        if editor.keySequence() != seq:
            blocked = editor.blockSignals(True)
            editor.setKeySequence(seq)
            editor.blockSignals(blocked)
        self.revalidate()

    def set_default(self, command_id: str):
        self.pending[command_id] = commands.default_keys(command_id)
        self._show_pending()

    def restore_all_defaults(self):
        self.pending = {c: commands.default_keys(c) for c in self.pending}
        self._show_pending()

    def revalidate(self):
        self.problems = commands.validate(self.pending)
        by_command: dict[str, list[commands.Problem]] = {}
        for problem in self.problems:
            by_command.setdefault(problem.command_id, []).append(problem)
        for command_id, row in self.rows.items():
            if not commands.command(command_id).assignable:
                continue
            found = by_command.get(command_id, [])
            colour = (ERROR_COLOUR if any(p.error for p in found)
                      else WARNING_COLOUR if found else None)
            for column in range(len(COLUMNS)):
                item = self.table.item(row, column)
                item.setData(Qt.BackgroundRole, colour)
            note = "; ".join(p.reason for p in found)
            self.table.item(row, COL_NOTE).setText(note)
            self.table.item(row, COL_NOTE).setToolTip(note)
        lines = [f"{'Problem' if p.error else 'Warning'} — {commands.plain_label(p.command_id)}, "
                 f"{p.key}: {p.reason}" for p in self.problems]
        self.problems_label.setText("\n".join(lines))
        self.problems_label.setVisible(bool(lines))
        self.apply_button.setEnabled(self.can_apply())
        self.discard_button.setEnabled(self.has_changes())

    def has_errors(self) -> bool:
        return bool(commands.errors(self.problems))

    def has_changes(self) -> bool:
        current = commands.assignments()
        return any(_keys_text(self.pending[c]) != _keys_text(current[c]) for c in current)

    def can_apply(self) -> bool:
        return not self.has_errors() and self.has_changes()

    def apply(self):
        """Stores and applies the set; refused (nothing stored) while any
        row is invalid."""
        if not self.can_apply():
            return False
        commands.save(self.db, self.pending)
        commands.load_notes.clear()
        self.reload()
        return True
