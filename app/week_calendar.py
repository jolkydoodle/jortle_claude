"""The Weekly Schedule workspace: seven days side by side on the shared
calendar grid, plus a monthly calendar navigator (Parts 24–32).

What is shared with the Day View, and why
-----------------------------------------
Everything except the column geometry:

    day_calendar_model.py   time <-> pixel, snapping, span validity, overlap
                            layout, column geometry — pure arithmetic
    event_render.py         how one event block is painted (colour, theme
                            colour, transparency, responsive text)
    day_calendar.EventDialog  the event editor — one editor, two views
    database.py             ONE calendar_events table (Part 26)

So a change to how events look, snap, overlap or validate lands in both
views at once, and an event edited in either view is the same row: there is
no weekly_events table and nothing to synchronize (Part 26). The only thing
this file adds is "which of seven columns is this pixel in", and even that
is `ColumnGeometry` in the model module, where the Day View's single column
is just the count=1 case.

Date state
----------
This workspace owns a `VisibleWeek` (see date_state.py), not a copy of the
journal's `SelectedDate`. It follows the selected date — whenever that
changes, the Sunday–Saturday week containing it is shown (decision G3-5) —
and otherwise moves only when asked: ◀ Day / Day ▶ (and Left/Right) slide
it a day, ◀ Week / Week ▶ (and Shift+Left/Right) step it seven days keeping
its alignment, the navigator picks a Sunday–Saturday row. Nothing here ever
changes the selected date except clicking a day name, which opens that day
in Daily Jorts.

Interaction (Part 27) matches the Day View, with one addition:
  * drag through empty space          -> create an event on that day
  * drag an event's middle            -> move it in time, AND across day
                                         columns, duration preserved
  * drag within 6px of its top/bottom -> resize that edge
  * double-click an event             -> edit it in the shared editor
  * select an event and press Delete  -> delete it
  * Left / Right                      -> slide the seven-day window a day
  * Shift+Left / Shift+Right          -> step it a week
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractScrollArea, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .all_day_strip import AllDayStrip
from .calendar_grid import CalendarGrid
from .calendar_panel import CalendarPanel
from .calendar_widget import month_marks
from .calendar_prefs import CalendarPrefs
from .database import Database
from .date_state import ISO, VisibleWeek, to_qdate
from .day_calendar_model import ColumnGeometry
from .event_commands import EventCommands
from .event_render import gutter_width
from .theme import PRESETS, ColorScheme
from .ui_util import MONTHLY_PANE_WIDTH_SETTING, YieldingHintLabel, saved_width, side_pane_width

CURRENT_TIME_TICK_MS = 60_000


class _WeekHeader(QWidget):
    """The day-name row above the scrolling timeline.

    Outside the scroll area on purpose: scrolling to 9pm should not scroll
    the labels that say which column is which. It shares the timeline's
    ColumnGeometry so the two never disagree about where a column starts.
    """

    dayClicked = Signal(str)  # ISO date of the column that was clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dates: list[str] = []
        self.scheme: ColorScheme = PRESETS["Light"]
        # How much of this widget's width the timeline below does NOT have —
        # the scroll area's frame and its vertical scrollbar. Without it the
        # header divides a slightly wider space into seven, and every column
        # label drifts a little further right than the column it names.
        self._right_inset = 0.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_dates(self, dates: list[str]):
        self._dates = list(dates)
        self.updateGeometry()
        self.update()

    def set_scheme(self, scheme: ColorScheme):
        self.scheme = scheme
        self.update()

    def set_right_inset(self, inset: float):
        if abs(inset - self._right_inset) > 0.5:
            self._right_inset = max(0.0, inset)
            self.update()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        metrics = QFontMetrics(self.font())
        return QSize(200, metrics.height() * 2 + 10)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _geometry(self) -> ColumnGeometry:
        return ColumnGeometry(self.width(), gutter_width(self.font()),
                               max(1, len(self._dates)),
                               right_margin=6.0 + self._right_inset)

    def paintEvent(self, _event):
        if not self._dates:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        geometry = self._geometry()
        metrics = QFontMetrics(self.font())
        today = QDate.currentDate().toString(ISO)

        text_color = palette.text().color()
        faint = QColor(text_color)
        faint.setAlpha(46)

        for index, date_str in enumerate(self._dates):
            left, right = geometry.bounds(index)
            qdate = to_qdate(date_str)
            rect = QRect(int(left), 0, int(right - left), self.height())

            if date_str == today:
                wash = QColor(self.scheme.accent)
                wash.setAlpha(38)
                painter.fillRect(rect.adjusted(1, 1, -1, -1), wash)

            painter.setPen(QPen(faint, 1))
            painter.drawLine(int(left), 2, int(left), self.height() - 2)

            # Two lines — weekday above, date below — because the weekday is
            # what you scan for and the date is what you verify. Both
            # abbreviate as the column narrows rather than clipping.
            name = qdate.toString("ddd")
            if metrics.horizontalAdvance(name) > rect.width() - 6:
                name = qdate.toString("ddd")[:1]
            day = qdate.toString("MMM d")
            if metrics.horizontalAdvance(day) > rect.width() - 6:
                day = qdate.toString("d")

            font = QFont(self.font())
            font.setBold(date_str == today)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(rect.adjusted(2, 2, -2, 0), Qt.AlignHCenter | Qt.AlignTop, name)
            hint = QColor(text_color)
            hint.setAlpha(170)
            painter.setPen(hint)
            painter.drawText(
                QRect(rect.x() + 2, metrics.height() + 2, rect.width() - 4, metrics.height()),
                Qt.AlignHCenter | Qt.AlignTop, day,
            )

        painter.setPen(QPen(faint, 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mouseReleaseEvent(self, mouse_event):
        if not self._dates or mouse_event.button() != Qt.LeftButton:
            return
        index = self._geometry().index_at(mouse_event.position().x())
        self.dayClicked.emit(self._dates[index])


# The seven-column grid is the same widget the Day View uses (Group 3).
_WeekTimeline = CalendarGrid


class WeekCalendarWidget(QWidget):
    """The Weekly Calendar workspace: Week View plus its month navigator."""

    changed = Signal()      # an event was created/edited/deleted
    openDayRequested = Signal(str)  # "show this day in the Daily Journal"

    def __init__(self, db: Database, visible_week: VisibleWeek | None = None,
                 prefs: CalendarPrefs | None = None, parent=None,
                 commands: EventCommands | None = None, titles=None):
        super().__init__(parent)
        self.db = db
        self.prefs = prefs or CalendarPrefs(db)
        # The one implementation of every event change, shared with the
        # Day View (event_commands.py).
        self.commands = commands or EventCommands(db)
        self.visible_week = visible_week or VisibleWeek()
        self.visible_week.changed.connect(lambda _start: self.refresh())

        self.header = _WeekHeader()
        self.header.dayClicked.connect(self.openDayRequested.emit)

        self.all_day_row = AllDayStrip()
        self.all_day_row.editRequested.connect(self._on_edit_requested)
        self.all_day_row.createRequested.connect(self._on_create_untimed)
        self.all_day_row.rangeChanged.connect(self._on_untimed_range_changed)
        self.all_day_row.setVisible(False)

        self.timeline = CalendarGrid(self.prefs)
        self.timeline.createRequested.connect(self._on_create_requested)
        self.timeline.moveRequested.connect(self._on_move_requested)
        self.timeline.resizeRequested.connect(self._on_resize_requested)
        self.timeline.editRequested.connect(self._on_edit_requested)
        self.timeline.deleteRequested.connect(self._delete_event)
        self.timeline.stepRequested.connect(self.shift_days)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.timeline)
        self.scroll.setWidgetResizable(True)
        self.scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        # The header must line up with the timeline's columns, and the
        # timeline is inset by the scroll area's vertical scrollbar. Keeping
        # the bar always on means the two never drift by its width.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.range_label = QLabel()
        self.range_label.setObjectName("SectionHeading")
        self.range_label.setWordWrap(True)

        # Two ways to move the seven-day window (Master Spec §32 as decided
        # 2026-09-24): slide it a day at a time, or step it a whole week.
        # A week step keeps whatever alignment the window has.
        week_back_btn = QPushButton("◀ Week")
        week_back_btn.setToolTip("Show the seven days before these (Shift+Left)")
        week_back_btn.clicked.connect(lambda: self.shift_days(-7))
        earlier_btn = QPushButton("◀ Day")
        earlier_btn.setToolTip("Slide the seven-day window one day earlier (Left arrow)")
        earlier_btn.clicked.connect(lambda: self.shift_days(-1))
        later_btn = QPushButton("Day ▶")
        later_btn.setToolTip("Slide the seven-day window one day later (Right arrow)")
        later_btn.clicked.connect(lambda: self.shift_days(1))
        week_forward_btn = QPushButton("Week ▶")
        week_forward_btn.setToolTip("Show the seven days after these (Shift+Right)")
        week_forward_btn.clicked.connect(lambda: self.shift_days(7))
        self.week_back_btn, self.week_forward_btn = week_back_btn, week_forward_btn
        # Clicking these must not take keyboard focus: a focused button eats
        # Left/Right (Qt moves focus between buttons with them), so the arrow
        # keys would stop working the moment you had clicked "Day ▶".
        for button in (week_back_btn, earlier_btn, later_btn, week_forward_btn):
            button.setFocusPolicy(Qt.NoFocus)
        this_week_btn = QPushButton("This week")
        this_week_btn.setToolTip("Jump to the Sunday–Saturday week containing today")
        this_week_btn.clicked.connect(self.go_to_current_week)
        this_week_btn.setFocusPolicy(Qt.NoFocus)

        nav_row = QHBoxLayout()
        nav_row.addWidget(week_back_btn)
        nav_row.addWidget(earlier_btn)
        nav_row.addWidget(later_btn)
        nav_row.addWidget(week_forward_btn)
        nav_row.addStretch(1)
        nav_row.addWidget(this_week_btn)

        add_btn = QPushButton("Add event…")
        add_btn.clicked.connect(self._on_add_clicked)

        hint = QLabel(
            "Drag on a day to create · double-click empty space for an hour · "
            "drag an event to move it in time or to another day · drag its "
            "edge to resize · double-click an event to edit · Left/Right "
            "slides the week by a day, Shift+Left/Right by a week · click a day "
            "name to open that day in Daily Jorts"
        )
        hint.setWordWrap(True)
        hint.setObjectName("SubtleHint")

        week_panel = self.week_panel = QWidget()
        week_layout = QVBoxLayout(week_panel)
        week_layout.setContentsMargins(0, 0, 0, 0)
        week_layout.addWidget(self.range_label)
        week_layout.addLayout(nav_row)
        week_layout.addWidget(self.header)
        week_layout.addWidget(self.all_day_row)
        week_layout.addWidget(self.scroll, stretch=1)
        week_layout.addWidget(add_btn)
        week_layout.addWidget(hint)

        # The navigator: the SAME month grid the Daily Journal uses, minus
        # the day-marker control (seven days are on screen; "set the marker"
        # would have no unambiguous subject). Part 30.
        self.navigator = CalendarPanel(show_tag_picker=False, titles=titles)
        self.navigator.dateClicked.connect(self._on_navigator_date_clicked)
        self.navigator.pageChanged.connect(lambda *_: self.refresh_marks())

        nav_hint = YieldingHintLabel(
            "Click any day to show its Sunday–Saturday week. The shaded days "
            "are the ones Week View is showing now."
        )
        nav_hint.setObjectName("SubtleHint")

        nav_panel = self.nav_panel = QWidget()
        nav_panel_layout = QVBoxLayout(nav_panel)
        nav_panel_layout.setContentsMargins(0, 0, 0, 0)
        nav_panel_layout.addWidget(self.navigator)
        nav_panel_layout.addWidget(nav_hint)
        nav_panel_layout.addStretch(1)

        # ------------------------------------------------------------------
        # PANE ORDER. These two lines, and only these two lines, decide which
        # side each pane is on. Swap them to put Week View back on the left;
        # nothing else needs touching, because the stretch factors and the
        # starting widths below are both looked up by indexOf() rather than
        # written as literal 0 and 1.
        splitter = QSplitter()
        splitter.addWidget(nav_panel)     # month navigator — left
        splitter.addWidget(week_panel)    # Week View — right
        # ------------------------------------------------------------------

        # Week View takes the slack when the window resizes; the navigator
        # keeps the width it was given.
        splitter.setStretchFactor(splitter.indexOf(week_panel), 1)
        splitter.setStretchFactor(splitter.indexOf(nav_panel), 0)
        self.splitter = splitter
        self.rebalance_panes()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self.setFocusPolicy(Qt.StrongFocus)

        self._clock = QTimer(self)
        self._clock.setInterval(CURRENT_TIME_TICK_MS)
        self._clock.timeout.connect(self.timeline.update)
        self._clock.start()

        self.refresh()
        QTimer.singleShot(0, self._scroll_to_working_hours)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_header_inset()

    def _sync_header_inset(self):
        """Keeps the header's seven columns over the timeline's seven."""
        inset = max(0, self.scroll.width() - self.scroll.viewport().width())
        self.header.set_right_inset(inset)
        self.all_day_row.set_right_inset(inset)

    # ------------------------------------------------------------ render
    def refresh(self):
        dates = self.visible_week.dates()
        occurrences = self.db.occurrences_between(dates[0], dates[-1])
        self.header.set_dates(dates)
        self.all_day_row.set_days(dates, occurrences)
        self.timeline.set_days(dates, occurrences)
        self._sync_header_inset()
        first, last = to_qdate(dates[0]), to_qdate(dates[-1])
        if first.year() == last.year() and first.month() == last.month():
            span = f"{first.toString('MMMM d')} – {last.toString('d, yyyy')}"
        else:
            span = f"{first.toString('MMM d, yyyy')} – {last.toString('MMM d, yyyy')}"
        self.range_label.setText(span)
        self.refresh_marks()

    def refresh_marks(self):
        """Re-reads the month the navigator is showing.

        Deliberately a separate method from refresh(): the main window calls
        this when journal content changes elsewhere, without disturbing which
        week is visible.
        """
        calendar = self.navigator.calendar
        year, month = calendar.yearShown(), calendar.monthShown()
        start = QDate(year, month, 1)
        end = QDate(year, month, start.daysInMonth())
        start_str, end_str = start.toString(ISO), end.toString(ISO)

        # The same marks as every other month grid (calendar_widget.month_marks).
        entry_dates, tag_colors, lengths, other_dates = month_marks(self.db, start_str, end_str)
        self.navigator.reload_month_title()
        calendar.set_month_data(entry_dates, tag_colors, lengths,
                                 other_content_dates=other_dates)
        # All seven visible days, which is what makes a shifted window
        # legible on a grid whose rows are Sunday–Saturday (Part 31).
        calendar.set_highlight_dates(self.visible_week.dates())

    def _scroll_to_working_hours(self):
        self.scroll.verticalScrollBar().setValue(int(7 * self.prefs.hour_height))

    def apply_theme(self, scheme):
        """One theme change reaches every part of this workspace.

        The navigator is a QCalendarWidget and takes the scheme the way it
        always did; the timeline, the day-name header and the untimed strip
        take it because their event colours come from the scheme rather than
        from the widget palette (see theme.event_color)."""
        self.navigator.calendar.set_scheme(scheme)
        self.timeline.set_scheme(scheme)
        self.header.set_scheme(scheme)
        self.all_day_row.set_scheme(scheme)

    def apply_compact_headers(self):
        self.navigator.apply_compact_headers()

    def showEvent(self, event):
        """Sizes the panes once this is really on screen (the splitter knows
        its width only then). Repeatable: it reads the remembered width."""
        super().showEvent(event)
        self._apply_pane_sizes()

    def rebalance_panes(self):
        """The navigator needs room for seven date columns at the current
        application font; Week View takes everything else. Without this the
        month grid loses its last column as the font grows (Parts 13/42) —
        the pane isn't too small, it's too small FOR THE TEXT IN IT."""
        QTimer.singleShot(0, self._apply_pane_sizes)

    def _apply_pane_sizes(self):
        """The month navigator at the width shared with Daily Jorts' month
        pane (ui_util.MONTHLY_PANE_WIDTH_SETTING; Master Spec §53.1), or the
        default when it was never dragged. Assigned by pane, not position."""
        total = self.splitter.width() or self.width() or 1180
        navigator = saved_width(self.db, MONTHLY_PANE_WIDTH_SETTING)
        if navigator is None:                  # 0 = dragged shut and kept shut (GF-4)
            navigator = side_pane_width(280, total, max_fraction=0.30)
        sizes = [0, 0]
        sizes[self.splitter.indexOf(self.nav_panel)] = navigator
        sizes[self.splitter.indexOf(self.week_panel)] = max(320, total - navigator)
        self.splitter.setSizes(sizes)

    # -------------------------------------------------------- navigation
    def shift_days(self, days: int):
        """Slides the seven-day window by a day (±1) or steps it by a week
        (±7). Never re-aligns to a Sunday: once the user has shifted to
        Monday–Sunday, that is what they asked for (Part 31). The month
        navigator turns its page when the window leaves it (B2)."""
        self.visible_week.shift(days)
        self._keep_window_on_page()

    @staticmethod
    def _page_range(year: int, month: int) -> tuple:
        """First and last date a Sunday-first QCalendarWidget shows for a
        month: six rows, starting a whole extra week early when the 1st is
        a Sunday (Qt keeps at least one day of the previous month)."""
        first = QDate(year, month, 1)
        offset = first.dayOfWeek() % 7            # Sunday = 0 … Saturday = 6
        if offset < 1:
            offset += 7
        start = first.addDays(-offset)
        return start.toString(ISO), start.addDays(41).toString(ISO)

    def _keep_window_on_page(self):
        """Turns the navigator to a month whose page holds all seven days on
        screen whenever any of them is off its page, so the shaded days are
        always the days on screen (Group 3 fixes, B2).

        The month of the middle day is tried first, then the months of the
        first and last days: a page starts on the Sunday on or before the
        1st, so stepping back into a month that begins on a Monday or
        Tuesday puts the window's first days on the PREVIOUS month's page."""
        dates = self.visible_week.dates()
        calendar = self.navigator.calendar

        def holds_all(year, month):
            lo, hi = self._page_range(year, month)
            return lo <= dates[0] and dates[-1] <= hi

        if holds_all(calendar.yearShown(), calendar.monthShown()):
            return
        for day in (dates[3], dates[0], dates[-1]):
            q = to_qdate(day)
            if holds_all(q.year(), q.month()):
                calendar.setCurrentPage(q.year(), q.month())
                break
        else:
            middle = to_qdate(dates[3])
            calendar.setCurrentPage(middle.year(), middle.month())
        self.refresh_marks()

    def go_to_current_week(self):
        from .date_state import today_iso
        self.visible_week.align_to_week_of(today_iso())
        self.navigator.calendar.setCurrentPage(
            QDate.currentDate().year(), QDate.currentDate().month())
        self.refresh_marks()

    def show_week_of(self, date_str: str):
        """Shows the Sunday–Saturday week containing `date_str`, with the
        navigator on its month. Connected to SelectedDate.changed by the main
        window: the week follows the selected date (decision G3-5)."""
        self.visible_week.align_to_week_of(date_str)
        qdate = to_qdate(date_str)
        self.navigator.calendar.setCurrentPage(qdate.year(), qdate.month())
        self.refresh_marks()

    def _on_navigator_date_clicked(self, qdate: QDate):
        # Clicking a day selects that day's conventional Sunday–Saturday
        # ROW (Part 30) — the navigator picks weeks, not single days.
        self.visible_week.align_to_week_of(qdate.toString(ISO))
        self.refresh_marks()

    def keyPressEvent(self, key_event):
        """Left/Right slide the window — but only when the keystroke has
        actually reached this workspace.

        This is a widget-level handler, not an application event filter
        (Part 29). A text field, a spin box, the event editor or a rich-text
        editor consumes its own arrow keys long before they could get here,
        so typing is never hijacked; these only fire when the focused widget
        had no use for them.
        """
        step = 7 if key_event.modifiers() & Qt.ShiftModifier else 1
        if key_event.key() == Qt.Key_Left:
            self.shift_days(-step)
            return
        if key_event.key() == Qt.Key_Right:
            self.shift_days(step)
            return
        super().keyPressEvent(key_event)

    # ------------------------------------------------------------ editing
    # Every route ends in the shared commands (event_commands.py) — the same
    # object and methods the Day View calls.
    def _done(self, written: bool):
        self.refresh()                  # a cancelled drag snaps back too
        if written:
            self.changed.emit()

    def _on_move_requested(self, key, date: str, start_minute: int):
        """One drop = one change to the one event (Part 26): a move across
        columns changes its dates, not a separate 'week position'."""
        self._done(self.commands.move(self, key, date, start_minute))

    def _on_resize_requested(self, key, edge: str, date: str, minute: int):
        self._done(self.commands.resize(self, key, edge, date, minute))

    def _on_untimed_range_changed(self, key, first: str, last: str):
        self._done(self.commands.set_untimed_range(self, key, first, last))

    def _on_create_untimed(self, date: str):
        """Double-click on empty space in the untimed strip."""
        self._create_from_draft(self.commands.draft(date, 0, 0, all_day=True))

    def _on_create_requested(self, date: str, start_minute: int, end_minute: int):
        """Dragging the grid, double-clicking it, and Add event…: one draft,
        one editor, one insert."""
        self._create_from_draft(self.commands.draft(date, start_minute, end_minute))

    def _create_from_draft(self, draft):
        self._done(self.commands.create_from_draft(self, draft))


    def _on_add_clicked(self):
        self._on_create_requested(self.visible_week.dates()[0], 9 * 60, 10 * 60)

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
