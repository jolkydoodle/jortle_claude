"""The whole left-hand side of the Daily Journal view: month/year
navigation, the calendar grid, and the day-marker (tag) control, as one
cohesive unit.

Qt's own QCalendarWidget navigation bar has two rough edges that show up
under a custom stylesheet: the month dropdown's arrow can sit on top of its
own text, and the year field's up/down spinner only appears once you click
into it. Rather than fight Qt's internal (and undocumented/unstable)
widget names to patch that with more stylesheet rules, this hides the
built-in nav bar entirely and replaces it with a small, plain nav row we
fully control: a month combo box, an always-visible year spinner, and
prev/next/today buttons.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
)

from .calendar_widget import JournalCalendar
from .period_titles import month_key
from .tag_picker import TagPicker

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class CalendarPanel(QWidget):
    """The month grid plus its navigation row.

    Used in two places on purpose (Part 30: reuse the monthly calendar
    architecture, theme rendering, Day Markers and marker colours rather
    than writing a second month grid for Week View):

      * the Daily Journal, where it picks the selected day and carries the
        Day Marker control for that day;
      * the Weekly Calendar, where it acts as the navigator for Week View
        and has no marker control — assigning a marker there would be
        ambiguous, since seven days are on screen at once.

    `show_tag_picker` is what distinguishes them. Everything else — the
    painting, the marks, the theming, the compact-header logic — is shared.
    """

    dateClicked = Signal(QDate)
    pageChanged = Signal(int, int)  # year, month

    def __init__(self, parent=None, show_tag_picker: bool = True, db=None, titles=None):
        super().__init__(parent)
        # The shared month-title store (period_titles.PeriodTitles), or None
        # for a panel without a title line.
        self.titles = titles

        self.calendar = JournalCalendar()
        self.calendar.setNavigationBarVisible(False)
        # QCalendarWidget computes a minimumSizeHint from its own header and
        # column metrics — about 266px here — and that hint was the entire
        # reason this pane could never be dragged narrow, not any explicit
        # minimum we had set. An explicit minimumWidth overrides the hint, so
        # the grid is free to compress its cells instead of holding the whole
        # left column open. The floor is still wide enough for two-digit
        # dates at a normal font; date text is never shrunk to compensate.
        self.calendar.setMinimumWidth(150)       # raised to what the grid needs: apply_cell_minimums
        self.calendar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.calendar.clicked.connect(self.dateClicked.emit)
        self.calendar.currentPageChanged.connect(self._on_page_changed)

        prev_btn = QPushButton("‹")
        prev_btn.setFixedWidth(32)
        prev_btn.setToolTip("Previous month")
        prev_btn.clicked.connect(self._go_previous_month)

        next_btn = QPushButton("›")
        next_btn.setFixedWidth(32)
        next_btn.setToolTip("Next month")
        next_btn.clicked.connect(self._go_next_month)

        self.month_combo = QComboBox()
        # Let the month name elide rather than hold the pane open: the month
        # is also visible from the dates themselves, so its full spelling is
        # not worth a permanent ~100px of minimum width.
        self.month_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.month_combo.setMinimumContentsLength(3)
        self.month_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.month_combo.addItems(MONTH_NAMES)
        self.month_combo.currentIndexChanged.connect(self._on_month_combo_changed)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1900, 2100)
        self.year_spin.setButtonSymbols(QSpinBox.UpDownArrows)  # always visible, not just on hover/click
        self.year_spin.valueChanged.connect(self._on_year_spin_changed)

        today_btn = QPushButton("Today")
        today_btn.clicked.connect(self.go_to_today)

        nav_row = QHBoxLayout()
        nav_row.addWidget(prev_btn)
        nav_row.addWidget(self.month_combo, stretch=1)
        nav_row.addWidget(self.year_spin)
        nav_row.addWidget(next_btn)

        # Always constructed, so callers can connect to tag_picker.tagChanged
        # without caring which configuration they're in; only ADDED to the
        # layout when this panel is the one that owns a single selected day.
        self.tag_picker = TagPicker(db=db)
        self.tag_picker.setVisible(show_tag_picker)

        # The month's optional title (Master Spec §11): its own line under
        # the month name, so it is plainly about the month and never squeezed
        # into the navigation controls. One stored value, shared through
        # `titles` with every other view of the same month.
        self.month_title = QLineEdit()
        self.month_title.setMaxLength(80)
        self.month_title.setPlaceholderText("Title for this month (optional)")
        self.month_title.setToolTip("An optional one-line title for the month shown. "
                                    "It appears wherever this month does.")
        self.month_title.setObjectName("MonthTitle")
        self.month_title.editingFinished.connect(self._on_month_title_edited)
        self.month_title.setVisible(titles is not None)
        if titles is not None:
            titles.changed.connect(self._on_titles_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(nav_row)
        layout.addWidget(self.month_title)
        layout.addWidget(self.calendar)
        if show_tag_picker:
            layout.addWidget(self.tag_picker)
        layout.addWidget(today_btn)
        layout.addStretch(1)

        today = QDate.currentDate()
        self._set_nav_controls(today.year(), today.month())
        self._load_month_title()

    def apply_compact_headers(self):
        """Chooses a weekday-header format that actually fits.

        "Mon/Tue/Wed" clip to "M.../W..." once the column is narrow or the
        application font is large, which is worse than useless — single
        letters are unambiguous in a calendar grid and always fit. Measured
        against the real column width rather than guessed, and re-evaluated
        on resize and on font changes.
        """
        from PySide6.QtWidgets import QCalendarWidget
        from PySide6.QtGui import QFontMetrics
        metrics = QFontMetrics(self.calendar.font())
        # The grid reserves frame and cell padding beyond the raw width, so
        # width//7 overstates what a header cell actually gets. Measured
        # against a deliberately conservative estimate: being one step too
        # abbreviated is harmless, whereas being one step too wide clips the
        # names to "M..." which is strictly worse than a single letter.
        usable = max(0, self.calendar.width() - 12)
        column = max(1, usable // 7)
        widest_short = max(metrics.horizontalAdvance(d) for d in
                           ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        self.calendar.setHorizontalHeaderFormat(
            QCalendarWidget.ShortDayNames if widest_short + 14 <= column
            else QCalendarWidget.SingleLetterDayNames
        )

        # Make the grid's own minimum height explicit, recomputed here
        # because it depends on the font. Without this the surrounding
        # column can squeeze the calendar below what its six week-rows
        # need and the last row is clipped mid-digit — a widget's
        # minimumSizeHint is advisory to a layout under pressure, but an
        # explicit minimum is not. Height only: the grid is still free to
        # get NARROW (see setMinimumWidth above), it just can't lose a week.
        # Width too (Group 3 fixes, C3): below Qt's own minimum the grid is
        # cut off at Friday, so that minimum — and room for today's circle —
        # is the floor a pane can be dragged to.
        self.calendar.apply_cell_minimums()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_compact_headers()

    # ---------------------------------------------------------------- sync
    def _set_nav_controls(self, year: int, month: int):
        self.month_combo.blockSignals(True)
        self.month_combo.setCurrentIndex(month - 1)
        self.month_combo.blockSignals(False)
        self.year_spin.blockSignals(True)
        self.year_spin.setValue(year)
        self.year_spin.blockSignals(False)

    def _on_page_changed(self, year: int, month: int):
        self._set_nav_controls(year, month)
        self._load_month_title()
        self.pageChanged.emit(year, month)

    # --------------------------------------------------------- month title
    def shown_month_key(self) -> str:
        return month_key(self.calendar.yearShown(), self.calendar.monthShown())

    def reload_month_title(self):
        """Re-reads the shown month's title (after anything that may have
        changed it outside this panel, such as a restore)."""
        self._load_month_title()

    def _load_month_title(self):
        if self.titles is None:
            return
        text = self.titles.get("month", self.shown_month_key())
        if self.month_title.text() != text:
            self.month_title.blockSignals(True)
            self.month_title.setText(text)
            self.month_title.blockSignals(False)

    def _on_month_title_edited(self):
        if self.titles is not None:
            stored = self.titles.set("month", self.shown_month_key(), self.month_title.text())
            if stored != self.month_title.text():
                self._load_month_title()

    def _on_titles_changed(self, kind: str, period: str):
        if kind == "month" and period == self.shown_month_key():
            self._load_month_title()

    def _on_month_combo_changed(self, index: int):
        self.calendar.setCurrentPage(self.year_spin.value(), index + 1)

    def _on_year_spin_changed(self, year: int):
        self.calendar.setCurrentPage(year, self.month_combo.currentIndex() + 1)

    def _go_previous_month(self):
        self.calendar.showPreviousMonth()

    def _go_next_month(self):
        self.calendar.showNextMonth()

    # ------------------------------------------------------------- public
    def go_to_today(self):
        today = QDate.currentDate()
        self.calendar.setSelectedDate(today)
        self.calendar.setCurrentPage(today.year(), today.month())
        self.dateClicked.emit(today)

    def selected_date(self) -> QDate:
        return self.calendar.selectedDate()

    def set_selected_date(self, date: QDate):
        self.calendar.setSelectedDate(date)
        self.calendar.setCurrentPage(date.year(), date.month())
