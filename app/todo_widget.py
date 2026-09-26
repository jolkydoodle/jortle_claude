"""The Tasks list for the selected day (Master Spec §30).

A task is not a calendar event, and this module deliberately shares nothing
with the calendar beyond the selected date. An event has a time, a duration,
a colour and a place on a grid; a task has a line of text and a checkbox.
The app has been here before — the original Daily Tasks feature was folded
into calendar events in round 22, and the cost was that every calendar query
then had to ask "but is this REALLY an event". This keeps the two apart; the
storage is the `todos` table, which is why some names here still say ToDo.

Layout (Group 3):
  * the heading reads "Tasks", with a "+" beside it — the same add command
    as the Add task… button below the calendar;
  * with no tasks the panel is just that heading, so there is always a way
    to add one and the splitter above the calendar never vanishes;
  * tasks flow into as many columns as the pane's width allows (§30.4) —
    the column count is derived from the width, never configured — and the
    list scrolls when they don't all fit, recomputed on every resize;
  * a completed task stays, struck through AND greyed (§30.7).

How tall the panel is belongs to the Tasks / Day Calendar splitter in
DayCalendarWidget (§30.3); `content_height()` is what it asks for when the
user hasn't chosen a height themselves.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListView, QListWidget,
    QListWidgetItem, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget
)

from .database import Database
from .date_state import SelectedDate

# Kept for callers that used the old fixed cap: the content-aware default
# height never exceeds this many rows of tasks (the user can drag for more).
DEFAULT_MAX_ROWS = 6
# A task is given at least this many characters of width before the list
# flows into another column.
MIN_TASK_CHARS = 18


def ask_task_text(parent, title: str, text: str = ""):
    """The one-line text question, as a module function so tests can answer it."""
    return QInputDialog.getText(parent, title, "Task:", text=text)


class TodoPanel(QWidget):
    """A day's tasks: checkbox, text, and nothing it doesn't need."""

    changed = Signal()
    countChanged = Signal(int)
    columnsChanged = Signal(int)   # the width now fits a different number of columns

    def __init__(self, db: Database, selected_date: SelectedDate, parent=None):
        super().__init__(parent)
        self.db = db
        self.selected_date = selected_date
        from .theme import PRESETS
        self.scheme = PRESETS["Light"]

        self.heading = QLabel("Tasks")
        self.heading.setObjectName("SectionHeading")
        self.add_button = QToolButton()
        self.add_button.setText("+")
        self.add_button.setToolTip("Add a task to this day")
        self.add_button.setAutoRaise(True)
        self.add_button.clicked.connect(self.add_todo)

        self.list = QListWidget()
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        # Items flow left to right and wrap into rows, so a wide pane shows
        # several columns; the grid size below sets how many.
        self.list.setViewMode(QListView.ListMode)
        self.list.setFlow(QListView.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(0)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.list.setMinimumHeight(0)
        # Pixel scrolling, so any height — down to the one-row minimum below —
        # can reach every task, including the last one.
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.heading)
        header.addWidget(self.add_button)
        header.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header)
        layout.addWidget(self.list, stretch=1)

        self.selected_date.changed.connect(lambda _date: self.refresh())
        self.refresh()

    # ------------------------------------------------------------- render
    def refresh(self):
        """Re-reads the selected day's tasks."""
        todos = self.db.get_todos(self.selected_date.value)
        self.list.blockSignals(True)
        self.list.clear()
        for todo in todos:
            self.list.addItem(self._item_for(todo))
        self.list.blockSignals(False)
        self.list.setVisible(bool(todos))
        # With tasks, the pane can't be dragged smaller than the heading
        # plus one whole row (§30.5: tasks stay reachable at any size).
        self.list.setMinimumHeight(self._row_height() + 2 * self.list.frameWidth() + 2
                                   if todos else 0)
        self._update_grid()
        self.countChanged.emit(len(todos))

    def set_scheme(self, scheme):
        """The app's colour scheme — the greyed look of a completed task is
        taken from it, not from the widget palette, which a stylesheet-themed
        app never updates (FP-11: dark text would vanish on a dark theme)."""
        self.scheme = scheme
        self.refresh()

    def _muted(self) -> QColor:
        muted = QColor(self.scheme.text)
        muted.setAlpha(120)
        return muted

    def _style_item(self, item: QListWidgetItem, done: bool):
        """Struck through and greyed when done — the visual state only; the
        done flag itself is the database's (update_todo)."""
        font = item.font()
        font.setStrikeOut(done)
        item.setFont(font)
        if done:
            item.setForeground(QBrush(self._muted()))
        else:
            item.setData(Qt.ForegroundRole, None)

    def _item_for(self, todo) -> QListWidgetItem:
        item = QListWidgetItem(todo.text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if todo.done else Qt.Unchecked)
        item.setData(Qt.UserRole, todo.id)
        item.setToolTip(todo.text)
        self._style_item(item, bool(todo.done))
        return item

    # ----------------------------------------------------- layout helpers
    def _row_height(self) -> int:
        height = self.list.sizeHintForRow(0) if self.list.count() else -1
        return max(height, QFontMetrics(self.list.font()).height() + 8)

    def _usable_width(self) -> int:
        """The list's width less its frame and a vertical scrollbar's width,
        whether or not the bar is showing — so the scrollbar appearing does
        not change the column count, which would change the height, which
        would make the bar disappear again."""
        bar = self.list.style().pixelMetric(self.list.style().PixelMetric.PM_ScrollBarExtent)
        return max(1, self.list.width() - 2 * self.list.frameWidth() - bar)

    def columns(self) -> int:
        """How many columns of tasks fit the list's current width (§30.4)."""
        width = self._usable_width()
        metrics = QFontMetrics(self.list.font())
        minimum = metrics.averageCharWidth() * MIN_TASK_CHARS + 32   # + checkbox
        return max(1, width // max(1, minimum))

    def _update_grid(self):
        """Recomputes the column width from the pane width, so the flow
        re-lays itself out (and the scroll range follows) on every resize.

        Every item is given exactly one column's width; the view's own
        eliding then keeps each text inside its column (a grid size alone
        lets long texts paint into the next column)."""
        count = self.list.count()
        columns = min(self.columns(), max(1, count))
        width = max(1, self._usable_width() // columns - 1)
        size = QSize(width, self._row_height())
        # Silently: a size hint change emits itemChanged, which is the
        # checkbox's signal — and would write the task and refresh everything.
        blocked = self.list.blockSignals(True)
        try:
            for index in range(count):
                self.list.item(index).setSizeHint(size)
        finally:
            self.list.blockSignals(blocked)
        self.list.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_grid()
        columns = self.columns()
        if columns != getattr(self, "_last_columns", None):
            self._last_columns = columns
            self.columnsChanged.emit(columns)

    def header_height(self) -> int:
        return max(self.heading.sizeHint().height(), self.add_button.sizeHint().height()) + 2

    def content_height(self, max_rows: int = DEFAULT_MAX_ROWS) -> int:
        """The height the tasks need at the current width, up to `max_rows`
        rows — what the splitter uses until the user drags it."""
        count = self.list.count()
        if count == 0:
            return self.header_height()
        rows = -(-count // self.columns())
        frame = 2 * self.list.frameWidth() + 4
        return self.header_height() + min(rows, max_rows) * self._row_height() + frame

    def minimumSizeHint(self):
        list_min = self.list.minimumHeight() if self.list.isVisibleTo(self) else 0
        return QSize(80, self.header_height() + list_min + (2 if list_min else 0))

    def sizeHint(self):
        return QSize(200, self.content_height())

    # ------------------------------------------------------------ editing
    def add_todo(self) -> bool:
        """Asks for the text and adds it to the selected day. The "+" here
        and the Add task… button both call this."""
        text, accepted = ask_task_text(self, "Add Task")
        if not accepted or not text.strip():
            return False
        self.db.add_todo(self.selected_date.value, text.strip())
        self.refresh()
        self.changed.emit()
        return True

    def _todo_id(self, item: QListWidgetItem) -> int:
        return int(item.data(Qt.UserRole))

    def _on_item_changed(self, item: QListWidgetItem):
        """The checkbox. Completed tasks stay visible, struck through."""
        done = item.checkState() == Qt.Checked
        self.db.update_todo(self._todo_id(item), done=done)
        self.list.blockSignals(True)
        self._style_item(item, done)
        self.list.blockSignals(False)
        self.changed.emit()

    def _on_item_double_clicked(self, item: QListWidgetItem):
        self._rename(item)

    def _on_context_menu(self, point):
        item = self.list.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        rename = menu.addAction("Edit…")
        delete = menu.addAction("Delete")
        chosen = menu.exec(self.list.mapToGlobal(point))
        if chosen is rename:
            self._rename(item)
        elif chosen is delete:
            self._delete(item)

    def _rename(self, item: QListWidgetItem):
        text, accepted = ask_task_text(self, "Edit Task", item.text())
        if not accepted or not text.strip():
            return
        self.db.update_todo(self._todo_id(item), text=text.strip())
        self.refresh()
        self.changed.emit()

    def _delete(self, item: QListWidgetItem):
        self.db.delete_todo(self._todo_id(item))
        self.refresh()
        self.changed.emit()

    def keyPressEvent(self, key_event):
        if key_event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            item = self.list.currentItem()
            if item is not None:
                self._delete(item)
                return
        super().keyPressEvent(key_event)
