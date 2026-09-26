"""The Yearly Calendar workspace (Master Spec §§35-38).

All twelve months of one year at a glance, each showing its name, its
optional month title, the journal-entry dots, Day Markers and today's circle
— drawn by the same `paint_day_marks` the month grids use, from the same
`month_marks`/day_metadata query, so a mark means the same thing here as in
Daily Jorts and the Weekly Schedule. The year's own optional title sits in
the header.

Navigation (§38): clicking a day selects it and opens Daily Jorts on it;
right-click or Shift-click selects it and opens the Weekly Schedule on the
week containing it. Browsing years never changes the selected date; the
visible year follows the selected date when that changes (VisibleYear).

Milestones and the Milestone pane (§36) are not part of this group; Day
Markers are what the grid shows.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .calendar_widget import month_marks, paint_day_marks
from .date_state import SelectedDate, VisibleYear, human, to_qdate
from .period_titles import month_key
from .theme import PRESETS, ColorScheme

ISO = "yyyy-MM-dd"
WEEKDAY_LETTERS = ("S", "M", "T", "W", "T", "F", "S")


class MonthGrid(QWidget):
    """One small month: name, title, weekday letters, and six week rows."""

    dayActivated = Signal(str, bool)     # ISO date, "open the week instead"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.year, self.month = 2000, 1
        self.title = ""
        self.scheme: ColorScheme = PRESETS["Light"]
        self.entry_dates: set = set()
        self.tag_colors: dict = {}
        self.other_dates: set = set()
        self.titles_by_date: dict = {}
        self.selected = ""
        self._cells: list = []          # [(QRect, iso date)]
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_month(self, year: int, month: int, title: str, marks: tuple, titles_by_date: dict,
                  selected: str, scheme: ColorScheme):
        self.year, self.month, self.title = year, month, title
        self.entry_dates, self.tag_colors, _lengths, self.other_dates = marks
        self.titles_by_date = titles_by_date
        self.selected = selected
        self.scheme = scheme
        self.updateGeometry()
        self.update()

    # ------------------------------------------------------------ metrics
    def _cell_size(self) -> tuple:
        metrics = QFontMetrics(self.font())
        # Room around the number for today's circle and, below it, the
        # entry dot — both measured from the font, so they scale with it.
        width = max(metrics.horizontalAdvance("30"), metrics.horizontalAdvance("W")) + 14
        height = metrics.height() + 14
        return width, height

    def _header_height(self) -> int:
        metrics = QFontMetrics(self.font())
        return metrics.height() * 3 + 10     # name, title, weekday letters

    def sizeHint(self):
        cw, ch = self._cell_size()
        return QSize(7 * cw + 8, self._header_height() + 6 * ch + 6)

    def minimumSizeHint(self):
        return self.sizeHint()

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        text = QColor(self.scheme.text)
        muted = QColor(self.scheme.text)
        muted.setAlpha(150)
        metrics = QFontMetrics(self.font())
        cw, ch = self._cell_size()
        left = 4
        line = metrics.height()

        bold = QFont(self.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(text)
        painter.drawText(QRect(left, 2, self.width() - 8, line), Qt.AlignLeft | Qt.AlignVCenter,
                         QDate(self.year, self.month, 1).toString("MMMM"))
        painter.setFont(self.font())
        painter.setPen(muted)
        painter.drawText(QRect(left, 2 + line, self.width() - 8, line),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(self.title, Qt.ElideRight, self.width() - 8))
        top = 6 + 2 * line
        for col, letter in enumerate(WEEKDAY_LETTERS):
            painter.drawText(QRect(left + col * cw, top, cw, line), Qt.AlignCenter, letter)

        first = QDate(self.year, self.month, 1)
        offset = first.dayOfWeek() % 7            # Qt: Monday=1 … Sunday=7 -> Sunday column 0
        grid_top = self._header_height()
        today = QDate.currentDate()
        self._cells = []
        for day in range(1, first.daysInMonth() + 1):
            index = offset + day - 1
            row, col = divmod(index, 7)
            rect = QRect(left + col * cw, grid_top + row * ch, cw, ch)
            date = QDate(self.year, self.month, day)
            key = date.toString(ISO)
            self._cells.append((rect, key))
            if key == self.selected:
                painter.save()
                pen = QPen(QColor(self.scheme.accent))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 3, 3)
                painter.restore()
            painter.setPen(text)
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignCenter, str(day))
            paint_day_marks(painter, rect, self.scheme, day_text=str(day), font=self.font(),
                            tag_color=self.tag_colors.get(key),
                            has_entry=key in self.entry_dates,
                            other_content=key in self.other_dates,
                            is_today=date == today)

    # ------------------------------------------------------------- mouse
    def date_at(self, pos) -> str | None:
        for rect, key in self._cells:
            if rect.contains(pos):
                return key
        return None

    def mouseMoveEvent(self, event):
        key = self.date_at(event.position().toPoint())
        if key is None:
            self.setToolTip("")
            return
        title = self.titles_by_date.get(key, "")
        self.setToolTip(human(key) + (f" — {title}" if title else ""))

    def mouseReleaseEvent(self, event):
        key = self.date_at(event.position().toPoint())
        if key is None:
            return
        if event.button() == Qt.RightButton:
            self.dayActivated.emit(key, True)
        elif event.button() == Qt.LeftButton:
            self.dayActivated.emit(key, bool(event.modifiers() & Qt.ShiftModifier))

    def contextMenuEvent(self, event):
        event.accept()      # right-click is a navigation gesture here, not a menu


class YearCalendarWidget(QWidget):
    """The workspace: header with year navigation and title, then the months."""

    openDayRequested = Signal(str)      # show this date in Daily Jorts
    openWeekRequested = Signal(str)     # show the week containing this date

    def __init__(self, db, selected_date: SelectedDate, titles, visible_year: VisibleYear | None = None,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.selected_date = selected_date
        self.titles = titles
        self.visible_year = visible_year or VisibleYear(to_qdate(selected_date.value).year())
        self.scheme: ColorScheme = PRESETS["Light"]
        self._dirty = True
        self._reveal_selected = True     # scroll the selected month into view on next refresh

        prev_btn = QPushButton("◀")
        prev_btn.setToolTip("Previous year")
        prev_btn.clicked.connect(lambda: self.visible_year.shift(-1))
        next_btn = QPushButton("▶")
        next_btn.setToolTip("Next year")
        next_btn.clicked.connect(lambda: self.visible_year.shift(1))
        this_btn = QPushButton("This year")
        this_btn.clicked.connect(lambda: self.visible_year.set(QDate.currentDate().year()))
        self.year_label = QLabel()
        self.year_label.setObjectName("SectionHeading")
        self.year_title = QLineEdit()
        self.year_title.setMaxLength(80)
        self.year_title.setPlaceholderText("Title for this year (optional)")
        self.year_title.editingFinished.connect(self._on_year_title_edited)
        header = QHBoxLayout()
        header.addWidget(prev_btn)
        header.addWidget(self.year_label)
        header.addWidget(next_btn)
        header.addWidget(self.year_title, stretch=1)
        header.addWidget(this_btn)

        hint = QLabel("Click a day to open it in Daily Jorts · right-click or Shift-click "
                      "to open its week in the Weekly Schedule")
        hint.setObjectName("SubtleHint")
        hint.setWordWrap(True)

        self.months = [MonthGrid() for _ in range(12)]
        for grid in self.months:
            grid.dayActivated.connect(self._on_day_activated)
        self.grid_host = QWidget()
        self.grid_layout = QGridLayout(self.grid_host)
        self.grid_layout.setHorizontalSpacing(18)
        self.grid_layout.setVerticalSpacing(12)
        self._columns = 0
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_host)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self.scroll, stretch=1)
        layout.addWidget(hint)

        self.visible_year.changed.connect(self._on_year_changed)
        self.selected_date.changed.connect(self._on_selected_date_changed)
        if titles is not None:
            titles.changed.connect(self._on_titles_changed)
        self._reflow(force=True)
        self.refresh()

    # ---------------------------------------------------------- layout
    def _reflow(self, force: bool = False):
        """As many months per row as fit (4, 3, 2 or 1), so the year stays
        readable at any window width and font size (§36, §54)."""
        month_width = self.months[0].sizeHint().width() + self.grid_layout.horizontalSpacing()
        available = max(1, self.scroll.viewport().width() - 8)
        columns = 1
        for candidate in (4, 3, 2):
            if candidate * month_width <= available:
                columns = candidate
                break
        if columns == self._columns and not force:
            return
        self._columns = columns
        for grid in self.months:
            self.grid_layout.removeWidget(grid)
        for index, grid in enumerate(self.months):
            self.grid_layout.addWidget(grid, index // columns, index % columns,
                                       Qt.AlignLeft | Qt.AlignTop)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.FontChange:
            self._reflow(force=True)

    # ------------------------------------------------------------ data
    def apply_theme(self, scheme: ColorScheme):
        self.scheme = scheme
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._reflow()
        if self._dirty:
            self.refresh()

    def refresh(self):
        """Re-reads the visible year — one metadata query for all twelve
        months. Deferred while the workspace is not on screen."""
        if not self.isVisible():
            self._dirty = True
            return
        self._dirty = False
        year = self.visible_year.value
        self.year_label.setText(str(year))
        text = self.titles.get("year", str(year)) if self.titles is not None else ""
        if self.year_title.text() != text:
            self.year_title.blockSignals(True)
            self.year_title.setText(text)
            self.year_title.blockSignals(False)
        entry, tags, lengths, other = month_marks(self.db, f"{year}-01-01", f"{year}-12-31")
        meta_titles = {d: m.title for d, m in self.db.day_metadata(f"{year}-01-01", f"{year}-12-31").items()
                       if m.title}
        month_titles = self.titles.month_titles(year) if self.titles is not None else {}
        for index, grid in enumerate(self.months):
            month = index + 1
            prefix = f"{year:04d}-{month:02d}-"
            grid.set_month(
                year, month, month_titles.get(month_key(year, month), ""),
                ({d for d in entry if d.startswith(prefix)},
                 {d: c for d, c in tags.items() if d.startswith(prefix)},
                 {}, {d for d in other if d.startswith(prefix)}),
                meta_titles, self.selected_date.value, self.scheme)
        self._reflow()
        if self._reveal_selected:
            self._reveal_selected = False
            selected = to_qdate(self.selected_date.value)
            if selected.year() == year:
                grid = self.months[selected.month() - 1]
                QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(grid, 0, 12))

    def mark_dirty(self):
        """Something the year shows changed elsewhere; re-read when seen."""
        self.refresh()

    # ------------------------------------------------------ interaction
    def _on_year_changed(self, _year: int):
        self._reveal_selected = True
        self.refresh()

    def _on_selected_date_changed(self, date_str: str):
        self._reveal_selected = True
        if not self.visible_year.set(to_qdate(date_str).year()):
            self.refresh()

    def _on_titles_changed(self, _kind: str, _period: str):
        self.refresh()

    def _on_year_title_edited(self):
        if self.titles is not None:
            self.titles.set("year", str(self.visible_year.value), self.year_title.text())

    def _on_day_activated(self, date_str: str, open_week: bool):
        if open_week:
            self.openWeekRequested.emit(date_str)
        else:
            self.openDayRequested.emit(date_str)
