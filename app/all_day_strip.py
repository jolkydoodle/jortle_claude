"""The untimed-event strip, shared by the Day View and the Week View.

An untimed event has no position on a time axis, so drawing it on one would
be a lie. Both views therefore show untimed events in a strip above the
timeline, aligned to the same columns as the grid below it.

This used to be two different things. The Week View drew this strip; the Day
View had a fixed-height `QListWidget` sitting outside its calendar, with
checkboxes left over from the retired Daily Tasks feature. They looked
different, behaved differently, and were 150 lines apart in two files — so
"untimed event" meant one thing on Monday and another when you looked at the
whole week.

The week's version turned out to be the general one: a strip over N columns
is a strip over one column when N is 1. The only thing that had to change to
share it was taking the list of days as an argument instead of assuming
seven. There is no Day-View-specific subclass and no `if len(dates) == 1`
anywhere below; one column is not a special case.

The strip hides itself entirely when the days it covers have no untimed
events, so it costs nothing on an ordinary day or week.

Multi-day untimed events (Group 3, Master Spec §25) are one chip spanning
their columns, stacked in rows so two overlapping ranges never cover each
other. Dragging a chip's left or right end to another column changes the
event's date range (`rangeChanged`); it stays one event throughout. In the
Day View (one column) the same event simply appears once.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .day_calendar_model import ColumnGeometry
from .event_render import event_tooltip, gutter_width, paint_event_block
from .recurrence import Occurrence, end_date_of, sortable_key
from .theme import ColorScheme, PRESETS, event_color

EDGE_GRAB_PX = 6


def _as_occurrences(dates: list, items) -> list:
    """Accepts occurrences, or rows grouped by date (the older call shape)."""
    if isinstance(items, dict):
        rows, seen = [], set()
        for date in dates:
            for e in items.get(date, []):
                if e.id not in seen:
                    seen.add(e.id)
                    rows.append(e)
        return [Occurrence(event=e, date=e.date, end_date=end_date_of(e),
                           series_id=getattr(e, "series_id", None),
                           occurrence_date=getattr(e, "occurrence_date", None)) for e in rows]
    return list(items)


class AllDayStrip(QWidget):
    """Untimed events for one or more days, in columns matching the grid."""

    editRequested = Signal(object)            # occurrence key
    createRequested = Signal(str)             # a date — double-clicked empty space
    rangeChanged = Signal(object, str, str)   # key, new first date, new last date

    MAX_VISIBLE = 3  # deeper than this and the strip would crowd out the day

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dates: list[str] = []
        self._chips: list = []            # [(occurrence, first_index, last_index, row)]
        self._hidden: dict[int, int] = {}  # column index -> chips not shown there
        self._rects: dict = {}             # key -> QRect
        self._right_inset = 0.0
        self.scheme: ColorScheme = PRESETS["Light"]
        self._drag = None                  # (occurrence, edge, first, last)
        self._drag_to: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # --------------------------------------------------------- appearance
    def set_scheme(self, scheme: ColorScheme):
        self.scheme = scheme
        self.update()

    def set_right_inset(self, inset: float):
        """Keeps the strip's columns aligned with the timeline's when the
        timeline is inside a scroll area whose scrollbar takes width."""
        if abs(inset - self._right_inset) > 0.5:
            self._right_inset = max(0.0, inset)
            self.update()

    # -------------------------------------------------------------- state
    def set_days(self, dates: list, occurrences):
        """Shows the untimed occurrences among `occurrences` for these days.

        Takes everything and filters here, so a caller never has to remember
        which half belongs to which widget — the strip takes the untimed
        ones, the timed grid the rest.
        """
        self._dates = list(dates)
        index = {d: i for i, d in enumerate(self._dates)}
        spans = []
        for occ in _as_occurrences(self._dates, occurrences):
            if not occ.all_day:
                continue
            shown = [index[d] for d in occ.days() if d in index]
            if shown:
                spans.append((occ, min(shown), max(shown)))
        # Longer ranges first, so they take the top rows and the short ones
        # fill in underneath — the usual calendar arrangement.
        spans.sort(key=lambda s: (s[1], -(s[2] - s[1]), sortable_key(s[0].key)))
        rows_end: list = []      # per row: last column used
        self._chips = []
        for occ, first, last in spans:
            for row, used in enumerate(rows_end):
                if first > used:
                    rows_end[row] = last
                    break
            else:
                rows_end.append(last)
                row = len(rows_end) - 1
            self._chips.append((occ, first, last, row))
        self._hidden = {}
        for occ, first, last, row in self._chips:
            if row >= self.MAX_VISIBLE - (1 if len(rows_end) > self.MAX_VISIBLE else 0):
                for col in range(first, last + 1):
                    self._hidden[col] = self._hidden.get(col, 0) + 1
        self.setVisible(bool(self._chips))
        self.updateGeometry()
        self.update()

    def set_day(self, date: str, events):
        """The one-day case, for the Day View. Same strip, one column."""
        if isinstance(events, list) and events and not isinstance(events[0], Occurrence):
            events = {date: events}
        self.set_days([date], events)

    # ------------------------------------------------------------ metrics
    def _chip_height(self) -> int:
        return QFontMetrics(self.font()).height() + 6

    def _rows(self) -> int:
        deepest = max((row + 1 for *_x, row in self._chips), default=0)
        return min(self.MAX_VISIBLE, max(1, deepest))

    def sizeHint(self):
        return QSize(200, self._rows() * self._chip_height() + 6)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _geometry(self) -> ColumnGeometry:
        return ColumnGeometry(self.width(), gutter_width(self.font()),
                               max(1, len(self._dates)),
                               right_margin=6.0 + self._right_inset)

    def _column_at(self, x: float):
        if not self._dates:
            return None
        geometry = self._geometry()
        if x < geometry.gutter:
            return None
        return geometry.index_at(x)

    def _date_at(self, x: float) -> str | None:
        column = self._column_at(x)
        return None if column is None else self._dates[column]

    # ----------------------------------------------------------- painting
    def paintEvent(self, _event):
        if not self._dates:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        geometry = self._geometry()
        accent = QColor(event_color(self.scheme))
        chip_height = self._chip_height()
        self._rects.clear()

        label = QColor(palette.text().color())
        label.setAlpha(150)
        painter.setPen(label)
        painter.drawText(QRect(0, 2, int(geometry.gutter) - 8, chip_height),
                          Qt.AlignRight | Qt.AlignVCenter, "Untimed")

        overflow = len({row for *_x, row in self._chips}) > self.MAX_VISIBLE
        visible_rows = self.MAX_VISIBLE - 1 if overflow else self.MAX_VISIBLE
        for occ, first, last, row in self._chips:
            if row >= visible_rows:
                continue
            if self._drag is not None and occ.key == self._drag[0].key:
                first, last = self._dragged_range()
            left = geometry.bounds(first)[0]
            right = geometry.bounds(last)[1]
            rect = QRect(int(left) + 2, 2 + row * chip_height,
                         int(right - left) - 4, chip_height - 3)
            self._rects[occ.key] = rect
            # show_time=False: "Untimed" is what the strip already says.
            paint_event_block(painter, rect, occ.event, accent,
                               palette=palette, base_font=self.font(),
                               show_time=False, surface=QColor(self.scheme.panel))
        for column, count in self._hidden.items():
            left, right = geometry.bounds(column)
            more = QRect(int(left) + 2, 2 + visible_rows * chip_height,
                         int(right - left) - 4, chip_height - 3)
            painter.setPen(label)
            painter.drawText(more, Qt.AlignRight | Qt.AlignVCenter, f"+{count}")

    # -------------------------------------------------------------- mouse
    def _event_at(self, pos):
        for key, rect in self._rects.items():
            if rect.contains(pos):
                return key
        return None

    def _chip(self, key):
        for chip in self._chips:
            if chip[0].key == key:
                return chip
        return None

    def _edge_at(self, key, pos):
        """'first'/'last' when the pointer is on an end of the chip that is
        the event's real first/last day (not cut off by the visible range)."""
        rect, chip = self._rects.get(key), self._chip(key)
        if rect is None or chip is None:
            return None
        occ = chip[0]
        days = occ.days()
        if abs(pos.x() - rect.right()) <= EDGE_GRAB_PX and days[-1] in self._dates:
            return "last"
        if abs(pos.x() - rect.left()) <= EDGE_GRAB_PX and days[0] in self._dates:
            return "first"
        return None

    def _dragged_range(self):
        occ, edge, first, last = self._drag
        if self._drag_to is None:
            return first, last
        if edge == "last":
            return first, max(first, self._drag_to)
        return min(last, self._drag_to), last

    def mousePressEvent(self, mouse_event):
        if mouse_event.button() != Qt.LeftButton:
            return
        pos = mouse_event.position().toPoint()
        key = self._event_at(pos)
        edge = self._edge_at(key, pos) if key is not None else None
        if edge is None:
            return
        occ, first, last, _row = self._chip(key)
        self._drag = (occ, edge, first, last)
        self._drag_to = None

    def mouseMoveEvent(self, mouse_event):
        pos = mouse_event.position().toPoint()
        if self._drag is not None:
            column = self._column_at(pos.x())
            if column is not None and column != self._drag_to:
                self._drag_to = column
                self.update()
            return
        key = self._event_at(pos)
        if key is None:
            self.setToolTip("")
            self.setCursor(Qt.ArrowCursor)
            return
        self.setCursor(Qt.SizeHorCursor if self._edge_at(key, pos) else Qt.ArrowCursor)
        chip = self._chip(key)
        if chip is not None:
            self.setToolTip(event_tooltip(chip[0].event))

    def mouseReleaseEvent(self, mouse_event):
        if self._drag is None or mouse_event.button() != Qt.LeftButton:
            return
        occ, edge, first, last = self._drag
        new_first, new_last = self._dragged_range()
        self._drag, self._drag_to = None, None
        self.update()
        if (new_first, new_last) == (first, last):
            return
        # The event's real first/last dates move by the same number of
        # columns as the dragged end (an end outside the view stays put).
        from .recurrence import add_days
        days = occ.days()
        start = add_days(days[0], new_first - first) if edge == "first" else days[0]
        end = add_days(days[-1], new_last - last) if edge == "last" else days[-1]
        self.rangeChanged.emit(occ.key, start, end)

    def mouseDoubleClickEvent(self, mouse_event):
        pos = mouse_event.position().toPoint()
        key = self._event_at(pos)
        if key is not None:
            self.editRequested.emit(key)
            return
        # Empty space in the strip creates an untimed event on that day —
        # the same gesture the timeline uses for a timed one, so "double-
        # click where you want it" is one rule rather than two.
        date = self._date_at(pos.x())
        if date is not None:
            self.createRequested.emit(date)
