"""The Day View: the selected day's Tasks, untimed events and timed grid.

Since Group 3 the Day View is the one-column form of the Weekly Schedule
(Master Spec §24, §66):

    calendar_grid.py     the timed grid (painting, hit-testing, dragging),
                         shared with the Weekly Schedule
    all_day_strip.py     untimed and multi-day untimed events, shared
    event_commands.py    what creating, editing, moving, resizing and
                         deleting an event MEAN — one implementation for
                         both views, including repeating-event scopes
    event_dialog.py      the event editor
    database.py          persistence (calendar_events and its companions)

The view never owns a date of its own. It renders whatever the canonical
SelectedDate says — every occurrence visible that day, including the part of
an event that started the day before — and re-reads when that changes. There
is no "< Previous Day / Next Day >" control in here (Part 23): the monthly
calendar is the app's date selector.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter,
    QVBoxLayout, QWidget,
)

from .all_day_strip import AllDayStrip
from .calendar_grid import DRAG_THRESHOLD_PX, EDGE_GRAB_PX, CalendarGrid  # noqa: F401
from .calendar_prefs import CalendarPrefs
from .database import Database
from .date_state import SelectedDate, human_long
from .event_commands import EventCommands
from .event_dialog import EventDialog as _EventDialog
from .todo_widget import TodoPanel

# Pixels per hour at 100% zoom lives in calendar_prefs.BASE_HOUR_HEIGHT.
# A second copy of it used to sit here, which is how the zoom and the grid
# could have ended up disagreeing about what "100%" meant.
CURRENT_TIME_TICK_MS = 60_000
# The Tasks pane's height once the user has dragged the Tasks / calendar
# boundary (pixels). Absent = content-aware default.
TASKS_HEIGHT_SETTING = "layout_tasks_height"


# The editor and the timed grid are shared with the Weekly Schedule (Group 3):
# EventDialog lives in event_dialog.py and the grid in calendar_grid.py.
# The names stay importable from here, where they used to be defined.
EventDialog = _EventDialog
_Timeline = CalendarGrid


class DayCalendarWidget(QWidget):
    """The whole right-hand day view: heading, untimed strip, timeline."""

    changed = Signal()  # something was created/edited/deleted (calendar marks refresh)

    def __init__(self, db: Database, selected_date: SelectedDate,
                 prefs: CalendarPrefs | None = None, parent=None,
                 commands: EventCommands | None = None):
        super().__init__(parent)
        self.db = db
        self.selected_date = selected_date
        self.prefs = prefs or CalendarPrefs(db)
        # The one implementation of every event change, shared with the
        # Weekly Schedule (event_commands.py).
        self.commands = commands or EventCommands(db)

        # No date heading here (spec Part 15). The Daily Journal already
        # shows the selected date immediately to the left, and repeating it
        # in the adjacent pane was pure duplication costing vertical space.
        # The date STATE is untouched — this view still renders whatever
        # SelectedDate says; it just doesn't restate it. Week View does label
        # its columns, because there the labels are what distinguish them.
        self.heading = QLabel()
        self.heading.setVisible(False)

        # Untimed events sit in a strip above the timeline, in the same
        # component the Week View uses (see all_day_strip.py). This used to
        # be a fixed-height list widget outside the calendar with checkboxes
        # on it — a different presentation of the same concept, which meant
        # "untimed event" looked like one thing here and another over there.
        self.all_day_strip = AllDayStrip()
        self.all_day_strip.editRequested.connect(self._on_edit_requested)
        self.all_day_strip.createRequested.connect(self._on_create_untimed)
        self.all_day_strip.rangeChanged.connect(self._on_untimed_range_changed)
        self.all_day_strip.setVisible(False)

        # A ToDo list is NOT a calendar event (todo_widget.py says why), so
        # it is a separate widget over separate storage that happens to sit
        # in the space the untimed list used to occupy.
        self.todo_panel = TodoPanel(self.db, self.selected_date)
        self.todo_panel.changed.connect(self.changed.emit)

        # The one-column form of the Weekly Schedule's grid (Master Spec §24).
        self.timeline = CalendarGrid(self.prefs)
        self.timeline.date = self.selected_date.value
        self.timeline.createRequested.connect(self._on_create_requested)
        self.timeline.moveRequested.connect(self._on_move_requested)
        self.timeline.resizeRequested.connect(self._on_resize_requested)
        self.timeline.editRequested.connect(self._on_edit_requested)
        self.timeline.deleteRequested.connect(self._delete_event)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.timeline)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)

        add_event_btn = QPushButton("Add event…")
        add_event_btn.clicked.connect(self._on_add_clicked)
        add_task_btn = QPushButton("Add task…")
        add_task_btn.clicked.connect(self._on_add_task_clicked)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(add_event_btn, stretch=1)
        buttons.addWidget(add_task_btn, stretch=1)

        hint = QLabel("Drag to create · double-click empty space for an hour · "
                       "drag to move · drag an edge to resize")
        hint.setWordWrap(True)
        hint.setObjectName("SubtleHint")  # sized by the app font setting, see theme.py

        # Tasks above the calendar, with a draggable boundary between them
        # (Master Spec §30.3, §53.2). The calendar part is the untimed strip
        # plus the timed grid — untimed events are part of the calendar.
        calendar_part = QWidget()
        calendar_layout = QVBoxLayout(calendar_part)
        calendar_layout.setContentsMargins(0, 0, 0, 0)
        calendar_layout.addWidget(self.all_day_strip)
        calendar_layout.addWidget(self.scroll, stretch=1)
        self.task_splitter = QSplitter(Qt.Vertical)
        self.task_splitter.addWidget(self.todo_panel)
        self.task_splitter.addWidget(calendar_part)
        self.task_splitter.setChildrenCollapsible(False)
        self.task_splitter.setStretchFactor(0, 0)
        self.task_splitter.setStretchFactor(1, 1)
        self.task_splitter.splitterMoved.connect(self._on_task_splitter_moved)
        self.todo_panel.countChanged.connect(lambda _n: self.apply_task_split())
        # A wider pane fits more columns, so the tasks need fewer rows.
        self.todo_panel.columnsChanged.connect(lambda _n: self.apply_task_split())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.task_splitter, stretch=1)
        layout.addLayout(buttons)
        layout.addWidget(hint)

        # One canonical date: render whatever it says, whenever it changes.
        self.selected_date.changed.connect(lambda _date: self.refresh())

        self._clock = QTimer(self)
        self._clock.setInterval(CURRENT_TIME_TICK_MS)
        self._clock.timeout.connect(self._tick_current_time)
        self._clock.start()

        self.refresh()
        QTimer.singleShot(0, self._scroll_to_working_hours)

    # ------------------------------------------------ Tasks / calendar split
    def saved_task_height(self):
        value = self.db.get_setting(TASKS_HEIGHT_SETTING)
        try:
            return int(value) if value else None
        except ValueError:
            return None

    def apply_task_split(self):
        """Sizes the Tasks pane: the height the user dragged it to, if they
        have; otherwise just what the day's tasks need, up to a few rows
        (§30.3). Never less than the Tasks heading, never so much that the
        calendar disappears."""
        total = self.task_splitter.height()
        if total <= 0 or getattr(self, "_sizing_tasks", False):
            return
        floor = self.todo_panel.minimumSizeHint().height()
        saved = self.saved_task_height()
        wanted = saved if saved is not None else self.todo_panel.content_height()
        ceiling = max(floor, total - max(120, total // 4))
        tasks = max(floor, min(wanted, ceiling))
        self._sizing_tasks = True       # setSizes resizes the panel, which can call back here
        try:
            self.task_splitter.setSizes([tasks, max(1, total - tasks)])
        finally:
            self._sizing_tasks = False

    def _on_task_splitter_moved(self, _pos: int, _index: int):
        """A drag of the boundary (only the user's own drags emit this): the
        Tasks pane keeps this height from now on, across days and restarts."""
        self.db.set_setting(TASKS_HEIGHT_SETTING, str(self.task_splitter.sizes()[0]))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_task_split()

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_task_split()

    # --------------------------------------------------------- appearance
    def apply_theme(self, scheme):
        # (the strip resolves the same accent, see below)
        """Hands the view the application's colour scheme.

        Events that follow the theme resolve their colour from this, not from
        the widget palette — which is what makes a theme change actually
        change them (see theme.event_color)."""
        self.timeline.set_scheme(scheme)
        self.all_day_strip.set_scheme(scheme)
        self.todo_panel.set_scheme(scheme)

    # ------------------------------------------------------------- render
    def refresh(self):
        """Everything visible on the selected day: its own events, the
        parts of events that started earlier or end later, and the
        occurrences of repeating events — one query, both widgets."""
        date = self.selected_date.value
        self.heading.setText(human_long(date))
        occurrences = self.db.occurrences_between(date, date)
        self.timeline.set_days([date], occurrences)
        # The strip takes the untimed ones, the grid the rest; neither can
        # be handed the wrong half, because neither is handed a half.
        self.all_day_strip.set_days([date], occurrences)
        self.todo_panel.refresh()

    def _scroll_to_working_hours(self):
        """Open on the morning rather than at midnight — 24 hours don't fit
        on screen (Part 22), and the top of the scroll range is the least
        useful part of the day to land on."""
        self.scroll.verticalScrollBar().setValue(int(7 * self.prefs.hour_height))

    def _tick_current_time(self):
        if self.timeline.shows_today():
            self.timeline.update()

    # ------------------------------------------------------------ editing
    # Every route ends in the shared commands; this view only says where
    # (the selected date) and refreshes when something was written.
    def _done(self, written: bool):
        self.refresh()                  # a cancelled drag snaps back too
        if written:
            self.changed.emit()
    def _on_create_requested(self, date: str, start_minute: int, end_minute: int):
        """Dragging the grid, double-clicking it, and Add event…: one draft,
        one editor, one insert."""
        self._create_from_draft(self.commands.draft(date, start_minute, end_minute))

    def _create_from_draft(self, draft):
        self._done(self.commands.create_from_draft(self, draft))

    def _on_move_requested(self, key, date: str, start_minute: int):
        self._done(self.commands.move(self, key, date, start_minute))

    def _on_resize_requested(self, key, edge: str, date: str, minute: int):
        self._done(self.commands.resize(self, key, edge, date, minute))

    def _on_untimed_range_changed(self, key, first: str, last: str):
        self._done(self.commands.set_untimed_range(self, key, first, last))

    def _on_add_clicked(self):
        """Keyboard/button route to the same editor, for anyone who'd rather
        not drag (and so the feature is reachable without a pointer)."""
        self._on_create_requested(self.selected_date.value, 9 * 60, 10 * 60)

    def _on_add_task_clicked(self):
        """Add Task sits beside Add Event and does something else entirely —
        the panel owns its own storage (todo_widget.py)."""
        self.todo_panel.add_todo()

    def _on_create_untimed(self, date: str):
        """Double-click on empty space in the untimed strip."""
        self._create_from_draft(self.commands.draft(date, 0, 0, all_day=True))

    def _on_edit_requested(self, key):
        if isinstance(key, int) and key < 0:     # older "negative id = delete" form
            self._delete_event(-key)
            return
        occurrence = self.commands.occurrence(key)
        if occurrence is not None:
            # An ordinary event is handed over as its row (the older shape of
            # this seam); an occurrence of a series by its key, since the row
            # behind it may be the whole series'.
            self._open_editor(key if occurrence.is_recurring else occurrence.event)

    def _open_editor(self, event_or_key):
        """The shared editor, through the shared commands."""
        key = event_or_key if not hasattr(event_or_key, "id") else self.commands.key_of(event_or_key)
        self._done(self.commands.edit(self, key))

    def _delete_event(self, key, confirm: bool = False):
        self._done(self.commands.delete(self, key, confirm=confirm))
