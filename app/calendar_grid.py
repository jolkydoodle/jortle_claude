"""The timed calendar grid shared by the Day View and the Weekly Schedule.

One widget, N day columns over one 24-hour axis (Master Spec §§24, 66). The
Day View is the one-column form; the Weekly Schedule has seven. Everything a
user can do with a timed event — create, move, resize, select, edit, delete —
is implemented once here, and both views receive the same requests.

What the grid draws are day pieces (recurrence.DayPiece): the part of one
occurrence that falls on one column's day. An event from 9 PM to 2 AM is one
occurrence and two pieces; both carry the occurrence's key, so selecting or
dragging either one acts on the whole event. The grid never writes to the
database. It reports what the user did, in whole-event terms:

    createRequested(date, start, end)       end may pass 1440 (= next day)
    moveRequested(key, date, start)         the occurrence's NEW start
    resizeRequested(key, edge, date, min)   the new start ('top') or end
    editRequested(key) / deleteRequested(key)

and the workspace hands those to event_commands, the one implementation of
what they mean.

Pixel <-> minute arithmetic, snapping, overlap layout and column geometry
are day_calendar_model's; how a block looks is event_render's.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QDate, QPoint, QRect, QTime, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .calendar_prefs import CalendarPrefs
from .day_calendar_model import (
    ColumnGeometry, format_minute, layout_events, minute_to_y, new_event_span,
    normalize_span, snap_minute, y_to_minute,
)
from .event_render import (
    event_tooltip, gutter_width, hour_font, paint_event_block, readable_text_color,
)
from .recurrence import (
    MINUTES_PER_DAY, Occurrence, add_days, day_pieces, days_between, end_date_of,
    span_from,
)
from .theme import PRESETS, ColorScheme, event_color, mix

ISO = "yyyy-MM-dd"
EDGE_GRAB_PX = 6          # how close to an edge counts as "resize", not "move"
DRAG_THRESHOLD_PX = 3     # movement below this is a click, not a drag


def occurrences_from_rows(events: list) -> list:
    """Plain event rows as occurrences (no repetition): for callers that
    hold rows rather than asking the database for occurrences."""
    return [Occurrence(event=e, date=e.date, end_date=end_date_of(e),
                       series_id=getattr(e, "series_id", None),
                       occurrence_date=getattr(e, "occurrence_date", None))
            for e in events]


class CalendarGrid(QWidget):
    """Timed events for one or more consecutive days."""

    createRequested = Signal(str, int, int)          # date, start, end (end > 1440 = next day)
    moveRequested = Signal(object, str, int)         # key, new start date, new start minute
    resizeRequested = Signal(object, str, str, int)  # key, "top"/"bottom", date, minute
    editRequested = Signal(object)                   # key
    deleteRequested = Signal(object)                 # key
    stepRequested = Signal(int)                      # Left/Right: -1/+1 day, with Shift ±7

    def __init__(self, prefs: CalendarPrefs | None = None, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # One time scale and one work-hours setting for every calendar view,
        # and the app's own colour scheme rather than the widget palette
        # (a stylesheet-themed app's palette is Qt's default; theme.event_color).
        self.prefs = prefs or CalendarPrefs()
        self.prefs.changed.connect(self._on_prefs_changed)
        self.scheme: ColorScheme = PRESETS["Light"]
        self._dates: list[str] = []
        self._occurrences: list = []
        self._pieces_by_date: dict[str, list] = {}
        self._layouts_by_date: dict[str, list] = {}
        self._rects: dict = {}           # key -> QRect of its first visible piece
        self._piece_rects: list = []     # [(QRect, piece)] every drawn piece
        self.selected_id = None

        # Interaction state. `_mode` is None when idle, otherwise one of
        # "create" / "move" / "resize-top" / "resize-bottom".
        self._mode: str | None = None
        self._press_pos: QPoint | None = None
        self._press_minute = 0
        self._press_column = 0
        self._drag_piece = None
        self._drag_key = None
        self._provisional = None          # create: (column, start, end); drags: span tuple
        self._moved_enough = False

        self._apply_scale()

    # ------------------------------------------------------- appearance
    def set_scheme(self, scheme: ColorScheme):
        self.scheme = scheme
        self.update()

    def hour_height(self) -> float:
        return self.prefs.hour_height

    def _apply_scale(self):
        """Exactly 24 hours tall at the current scale — zoom re-lays the grid
        out at the new pixels-per-hour rather than stretching a picture."""
        self.setMinimumHeight(int(24 * self.prefs.hour_height) + 2)
        self.resize(self.width(), self.minimumHeight())

    def _on_prefs_changed(self):
        self._apply_scale()
        self.update()

    def wheelEvent(self, wheel_event):
        """Ctrl+wheel zooms the shared time scale; a plain wheel is left to
        the scroll area around the grid."""
        if wheel_event.modifiers() & Qt.ControlModifier:
            steps = wheel_event.angleDelta().y() / 120.0
            if steps:
                self.prefs.zoom_by(1 if steps > 0 else -1)
            wheel_event.accept()
            return
        wheel_event.ignore()

    # ------------------------------------------------------------- data
    def set_days(self, dates: list, occurrences: list):
        """The columns' dates and every occurrence to draw on them (untimed
        ones are ignored here; the untimed strip draws those)."""
        self._dates = list(dates)
        self._occurrences = [o for o in occurrences if not o.all_day]
        self._pieces_by_date = {d: [] for d in self._dates}
        for occ in self._occurrences:
            for day in self._dates:
                piece = day_pieces(occ, day)
                if piece is not None:
                    self._pieces_by_date[day].append(piece)
        self._layouts_by_date = {d: layout_events(p) for d, p in self._pieces_by_date.items()}
        keys = {o.key for o in self._occurrences}
        if self.selected_id is not None and self.selected_id not in keys:
            self.selected_id = None
        self.update()

    def set_week(self, dates: list, events_by_date: dict):
        """Rows grouped by date, as events_in_range() returns them."""
        rows = [e for d in dates for e in events_by_date.get(d, [])]
        self.set_days(dates, occurrences_from_rows(rows))

    def set_events(self, events: list):
        """Rows for the single column's date."""
        self.set_days(self._dates[:1] or [QDate.currentDate().toString(ISO)],
                      occurrences_from_rows(events))

    @property
    def date(self) -> str:
        return self._dates[0] if self._dates else ""

    @date.setter
    def date(self, value: str):
        self._dates = [value]

    def shows_today(self) -> bool:
        return QDate.currentDate().toString(ISO) in self._dates

    def pieces(self) -> list:
        """Every piece the grid is drawing, column by column."""
        return [p for d in self._dates for p in self._pieces_by_date.get(d, [])]

    def occurrence_by_key(self, key):
        for occ in self._occurrences:
            if occ.key == key:
                return occ
        return None

    def event_by_id(self, key):
        """The first drawn piece of the occurrence with this key."""
        for pieces in self._pieces_by_date.values():
            for piece in pieces:
                if piece.id == key:
                    return piece
        return None

    def _geometry(self) -> ColumnGeometry:
        return ColumnGeometry(self.width(), gutter_width(self.font()),
                              max(1, len(self._dates)))

    def date_at(self, x: float) -> str:
        if not self._dates:
            return ""
        return self._dates[self._geometry().index_at(x)]

    # ---------------------------------------------------------- painting
    def paintEvent(self, _paint_event):
        if not self._dates:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        geometry = self._geometry()
        scale = self.prefs.hour_height

        text_color = palette.text().color()
        faint = QColor(text_color)
        faint.setAlpha(46)
        fainter = QColor(text_color)
        fainter.setAlpha(22)
        label_color = QColor(text_color)
        label_color.setAlpha(150)

        # Work hours first, so the grid and the events sit on top of it.
        self._paint_work_hours(painter, geometry, scale)

        labels_font = hour_font(self.font())
        metrics_h = QFontMetrics(labels_font).height()
        gutter = geometry.gutter
        for hour in range(25):
            y = minute_to_y(hour * 60, scale)
            painter.setPen(QPen(faint, 1))
            painter.drawLine(int(gutter), int(y), self.width(), int(y))
            if hour < 24:
                half_y = minute_to_y(hour * 60 + 30, scale)
                painter.setPen(QPen(fainter, 1, Qt.DotLine))
                painter.drawLine(int(gutter), int(half_y), self.width(), int(half_y))
                painter.setPen(label_color)
                painter.setFont(labels_font)
                painter.drawText(QRect(0, int(y) + 2, int(gutter) - 8, metrics_h),
                                 Qt.AlignRight | Qt.AlignTop, format_minute(hour * 60))

        if len(self._dates) > 1:
            painter.setPen(QPen(faint, 1))
            for index in range(len(self._dates) + 1):
                x = geometry.left(index)
                painter.drawLine(int(x), 0, int(x), self.height())

        self._rects.clear()
        self._piece_rects = []
        accent = QColor(event_color(self.scheme))
        surface = QColor(self.scheme.panel)
        dragging = self._mode in ("move", "resize-top", "resize-bottom") and self._moved_enough
        for index, date_str in enumerate(self._dates):
            left, right = geometry.bounds(index)
            column_width = max(0.0, right - left - 2)
            for layout in self._layouts_by_date.get(date_str, []):
                piece = layout.event
                if dragging and piece.id == self._drag_key:
                    continue  # drawn as the drag ghost below instead
                rect = self._piece_rect(left, column_width, layout, piece, scale)
                self._rects.setdefault(piece.id, rect)   # its first (earliest) piece
                self._piece_rects.append((rect, piece))
                paint_event_block(painter, rect, piece, accent,
                                  selected=(piece.id == self.selected_id),
                                  palette=palette, base_font=self.font(), surface=surface)

        self._paint_provisional(painter, geometry, accent, palette, scale, surface)
        self._paint_now_line(painter, geometry)

    @staticmethod
    def _piece_rect(left, column_width, layout, piece, scale) -> QRect:
        top = minute_to_y(piece.start_minute, scale)
        bottom = minute_to_y(piece.end_minute, scale)
        x = left + 1 + layout.x_fraction * column_width
        # Side by side at any width (Group 3 fixes, B1): each block keeps to
        # its own lane — a fixed minimum width made narrow lanes overlap
        # their neighbours in the Week view. The 2 px gap goes first.
        lane = layout.width_fraction * column_width
        w = max(1.0, lane - 2 if lane >= 6 else lane - 1)
        return QRect(int(x), int(top) + 1, int(w), max(14, int(bottom - top) - 2))

    def _paint_work_hours(self, painter, geometry, scale: float):
        """The work-hours tint, per column, so weekends don't get it."""
        if not self.prefs.work_hours_enabled:
            return
        from .date_state import to_qdate
        start, end = self.prefs.work_span()
        top, bottom = minute_to_y(start, scale), minute_to_y(end, scale)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(mix(self.scheme.panel, self.scheme.accent, 0.12)))
        for index, date_str in enumerate(self._dates):
            if not self.prefs.is_work_day(to_qdate(date_str)):
                continue
            left, right = geometry.bounds(index)
            painter.drawRect(QRect(int(left), int(top), int(right - left), int(bottom - top)))
        painter.restore()

    def _paint_provisional(self, painter, geometry, accent, palette, scale, surface):
        if self._provisional is None:
            return
        if self._mode == "create":
            column, start, end = self._provisional
            left, right = geometry.bounds(column)
            top, bottom = minute_to_y(start, scale), minute_to_y(end, scale)
            rect = QRect(int(left) + 1, int(top) + 1, int(right - left) - 2,
                         max(14, int(bottom - top) - 2))
            ghost_color = QColor(accent)
            ghost_color.setAlpha(90)
            painter.setBrush(ghost_color)
            painter.setPen(QPen(accent, 1))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(readable_text_color(ghost_color, surface))
            painter.drawText(rect.adjusted(6, 2, -6, -2), Qt.AlignLeft | Qt.AlignTop,
                             f"{format_minute(start)} – {format_minute(end)}")
            return
        # A move or resize: the real event at its prospective place — same
        # colour, same text rules — on every visible day it would cover.
        occ = self._ghost_occurrence()
        if occ is None:
            return
        for index, date_str in enumerate(self._dates):
            piece = day_pieces(occ, date_str)
            if piece is None:
                continue
            left, right = geometry.bounds(index)
            top, bottom = minute_to_y(piece.start_minute, scale), minute_to_y(piece.end_minute, scale)
            rect = QRect(int(left) + 1, int(top) + 1, int(right - left) - 2,
                         max(14, int(bottom - top) - 2))
            paint_event_block(painter, rect, piece, accent, selected=True,
                              palette=palette, base_font=self.font(), surface=surface)

    def _ghost_occurrence(self):
        """The dragged occurrence as it would be if dropped now."""
        if self._drag_piece is None or self._provisional is None:
            return None
        start_date, start_minute, end_date, end_minute = self._provisional
        occ = self._drag_piece.occurrence
        event = replace(occ.event, date=start_date, start_minute=start_minute,
                        end_date=None if end_date == start_date else end_date,
                        end_minute=end_minute)
        return Occurrence(event=event, date=start_date, end_date=end_date,
                          series_id=occ.series_id, occurrence_date=occ.occurrence_date,
                          rule=occ.rule)

    def _paint_now_line(self, painter, geometry):
        today = QDate.currentDate().toString(ISO)
        if today not in self._dates:
            return
        index = self._dates.index(today)
        left, right = geometry.bounds(index)
        now = QTime.currentTime()
        y = minute_to_y(now.hour() * 60 + now.minute(), self.prefs.hour_height)
        marker = QColor("#d9534f")
        painter.setPen(QPen(marker, 2))
        painter.drawLine(int(left), int(y), int(right), int(y))
        painter.setBrush(marker)
        painter.drawEllipse(QPoint(int(left), int(y)), 4, 4)

    # ----------------------------------------------------------- hit test
    def _piece_at(self, pos: QPoint):
        # Last drawn wins: it is the visually topmost block.
        for rect, piece in reversed(self._piece_rects):
            if rect.contains(pos):
                return piece, rect
        return None, None

    def _event_at(self, pos: QPoint):
        return self._piece_at(pos)[0]

    @staticmethod
    def _edge_of(piece, rect: QRect, pos: QPoint):
        """'top' / 'bottom' when the pointer is on a REAL edge of the event.
        The top of a piece continuing from the day before, and the bottom of
        one continuing into the next day, are the day's edges, not the
        event's — grabbing them moves the event instead."""
        if abs(pos.y() - rect.top()) <= EDGE_GRAB_PX and not piece.continues_before:
            return "top"
        if abs(pos.y() - rect.bottom()) <= EDGE_GRAB_PX and not piece.continues_after:
            return "bottom"
        return None

    def _edge_at(self, piece, pos: QPoint):
        for rect, candidate in self._piece_rects:
            if candidate is piece:
                return self._edge_of(piece, rect, pos)
        return None

    # -------------------------------------------------------------- mouse
    def mousePressEvent(self, mouse_event):
        if mouse_event.button() != Qt.LeftButton or not self._dates:
            return
        pos = mouse_event.position().toPoint()
        self._press_pos = pos
        self._moved_enough = False
        self._press_minute = y_to_minute(pos.y(), self.prefs.hour_height)
        self._press_column = self._geometry().index_at(pos.x())
        piece, rect = self._piece_at(pos)
        if piece is not None:
            self.selected_id = piece.id
            self._drag_piece = piece
            self._drag_key = piece.id
            edge = self._edge_of(piece, rect, pos)
            self._mode = f"resize-{edge}" if edge else "move"
            self._provisional = None
        else:
            self.selected_id = None
            self._drag_piece = self._drag_key = None
            self._mode = "create"
            self._provisional = None
        self.update()

    def _absolute(self, column: int, minute: int) -> int:
        """Minutes from midnight of the dragged occurrence's start date to
        `minute` on column `column`'s day."""
        occ = self._drag_piece.occurrence
        return days_between(occ.date, self._dates[column]) * MINUTES_PER_DAY + minute

    def _drag_result(self, column: int, minute: int):
        """(start_date, start_minute, end_date, end_minute) for the current
        drag position, or None. Moves keep the duration; resizes keep the
        other end and never let the two cross (minimum 15 minutes)."""
        from .day_calendar_model import MIN_EVENT_MINUTES
        occ = self._drag_piece.occurrence
        start = occ.start_minute
        end = days_between(occ.date, occ.end_date) * MINUTES_PER_DAY + occ.end_minute
        if self._mode == "move":
            delta = self._absolute(column, snap_minute(minute)) - \
                self._absolute(self._press_column, snap_minute(self._press_minute))
            start, end = start + delta, end + delta
        elif self._mode == "resize-top":
            start = min(self._absolute(column, snap_minute(minute)), end - MIN_EVENT_MINUTES)
        elif self._mode == "resize-bottom":
            end = max(self._absolute(column, snap_minute(minute)), start + MIN_EVENT_MINUTES)
        start_days, start_minute = divmod(start, MINUTES_PER_DAY)
        start_date = add_days(occ.date, start_days)
        end_date, end_minute = span_from(start_date, start_minute, end - start)
        return start_date, start_minute, end_date or start_date, end_minute

    def mouseMoveEvent(self, mouse_event):
        pos = mouse_event.position().toPoint()
        if self._mode is None:
            piece, rect = self._piece_at(pos)
            if piece is not None and self._edge_of(piece, rect, pos):
                self.setCursor(Qt.SizeVerCursor)
            elif piece is not None:
                self.setCursor(Qt.OpenHandCursor)
                self.setToolTip(event_tooltip(piece))
            else:
                self.setCursor(Qt.ArrowCursor)
                self.setToolTip("")
            return
        if self._press_pos is not None and not self._moved_enough:
            if abs(pos.y() - self._press_pos.y()) < DRAG_THRESHOLD_PX and \
               abs(pos.x() - self._press_pos.x()) < DRAG_THRESHOLD_PX:
                return
            self._moved_enough = True
        minute = y_to_minute(pos.y(), self.prefs.hour_height)
        column = self._geometry().index_at(pos.x())
        if self._mode == "create":
            # A create drag stays in the column it started in: dragging
            # diagonally is how you make a tall event near a column edge.
            start, end = normalize_span(self._press_minute, minute)
            self._provisional = (self._press_column, start, end)
        elif self._drag_piece is not None:
            self._provisional = self._drag_result(column, minute)
        self.update()

    def mouseReleaseEvent(self, mouse_event):
        if mouse_event.button() != Qt.LeftButton:
            return
        mode, provisional, moved = self._mode, self._provisional, self._moved_enough
        key = self._drag_key
        self._mode = None
        self._provisional = None
        self._press_pos = None
        self._drag_piece = self._drag_key = None
        self.setCursor(Qt.ArrowCursor)
        self.update()
        if mode == "create":
            if moved and provisional:
                column, start, end = provisional
                self.createRequested.emit(self._dates[column], start, end)
            return
        if key is None or not moved or not provisional:
            return  # a plain click selects; it does not open the editor
        start_date, start_minute, end_date, end_minute = provisional
        if mode == "move":
            self.moveRequested.emit(key, start_date, start_minute)
        elif mode == "resize-top":
            self.resizeRequested.emit(key, "top", start_date, start_minute)
        elif mode == "resize-bottom":
            self.resizeRequested.emit(key, "bottom", end_date, end_minute)

    def mouseDoubleClickEvent(self, mouse_event):
        """Edit the event under the pointer, or create a one-hour event on
        the clicked day at the clicked time — running past midnight into the
        next day when it starts late (Group 3 criterion 20)."""
        pos = mouse_event.position().toPoint()
        piece, _rect = self._piece_at(pos)
        if piece is not None:
            self.selected_id = piece.id
            self.editRequested.emit(piece.id)
            return
        date = self.date_at(pos.x())
        if not date:
            return
        start, end = new_event_span(pos.y(), self.prefs.hour_height)
        self.createRequested.emit(date, start, end)

    def keyPressEvent(self, key_event):
        if key_event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_id is not None:
            self.deleteRequested.emit(self.selected_id)
            return
        # Left/Right move the visible days. They are reported, not acted on:
        # the Weekly Schedule steps its window, the Day View ignores them.
        # (Left to propagate, they never arrived: the scroll area around the
        # grid takes Left/Right for itself.)
        if key_event.key() in (Qt.Key_Left, Qt.Key_Right):
            step = 7 if key_event.modifiers() & Qt.ShiftModifier else 1
            self.stepRequested.emit(-step if key_event.key() == Qt.Key_Left else step)
            key_event.accept()
            return
        super().keyPressEvent(key_event)
