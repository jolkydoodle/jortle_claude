"""A QCalendarWidget subclass that paints a colored background for tagged
days (holidays, trips, milestones, custom) and a small dot under any day
that has journal content, so the month view doubles as an overview of your
year. Colors for the "has content" dot and the today-outline are drawn
from the active ColorScheme so they always read clearly against whatever
background the user has chosen.

Two things Qt does NOT pick up from the app's stylesheet, so they're set
explicitly here instead:

- QCalendarWidget defaults Saturday/Sunday day numbers to a hardcoded red,
  via a per-weekday QTextCharFormat that lives outside the normal
  palette/stylesheet system entirely. set_scheme() overrides every
  weekday's format (and the Mon/Tue/... header row) to use the scheme's
  text color, so weekends stop looking like an error state in every theme.
- Custom painting in paintCell() has to happen *after* calling
  super().paintCell(), not before — Qt's default implementation paints its
  own cell background (for selection/today/etc.) which would otherwise
  paint over and erase anything drawn first.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget

from .theme import ColorScheme, PRESETS


def month_marks(db, start: str, end: str) -> tuple:
    """(entry_dates, tag_colors, lengths, other_content_dates) for a range —
    what set_month_data() takes — from Database.day_metadata(), which reads
    no document bodies. The one place every month grid gets its marks
    (Daily Jorts, the Weekly Schedule navigator, the Yearly Calendar), so
    they cannot come to disagree about what a mark means.
    """
    meta = db.day_metadata(start, end)
    entry_dates = {d for d, m in meta.items() if m.has_entry}
    tag_colors = {d: m.tag_color for d, m in meta.items() if m.tag_color}
    lengths = {d: m.length for d, m in meta.items() if m.has_entry}
    other = {d for d, m in meta.items() if m.other_content and not m.has_entry}
    return entry_dates, tag_colors, lengths, other


def today_circle_diameter(font) -> int:
    """The diameter today's circle needs so that ANY day number drawn in
    `font` sits entirely inside it: the diagonal of the widest two-digit
    number's ink, plus a margin for the ring (Group 3 fixes, B4)."""
    import math
    from PySide6.QtGui import QFontMetrics
    metrics = QFontMetrics(font)
    widest = max((metrics.tightBoundingRect(t) for t in ("88", "30", "28", "20")),
                 key=lambda r: r.width() * r.height())
    return int(math.ceil(math.hypot(widest.width(), widest.height()))) + 6


def _fit_font(font, text: str, diameter: int):
    """`font`, scaled down only if `text` would not fit a circle of
    `diameter` (a cell too small for the full-size circle)."""
    import math
    from PySide6.QtGui import QFont, QFontMetrics
    fitted = QFont(font)
    for _attempt in range(12):
        ink = QFontMetrics(fitted).tightBoundingRect(text)
        if math.hypot(ink.width(), ink.height()) + 4 <= diameter:
            break
        if fitted.pointSizeF() > 0:
            fitted.setPointSizeF(max(4.0, fitted.pointSizeF() * 0.9))
        else:
            fitted.setPixelSize(max(5, int(fitted.pixelSize() * 0.9)))
    return fitted


def paint_day_marks(painter: QPainter, rect: QRect, scheme: ColorScheme, *,
                    day_text: str, font, tag_color=None, has_entry=False,
                    other_content=False, highlighted=False, is_today=False):
    """Everything a month grid draws on one day cell besides the plain day
    number: the "shown elsewhere" wash, the Day Marker bar and border, the
    journal-entry dot (filled) or other-content dot (hollow), and today's
    filled circle behind the day number (Master Spec §37).

    Shared by the month grids (JournalCalendar) and the Yearly Calendar's
    small months, so a mark means the same thing everywhere. The marks stay
    distinct from each other and from the selection: the selection is Qt's
    full-cell highlight, today is a circle, a marker is a coloured bar and
    border, an entry is a dot.
    """
    from .event_render import readable_text_color

    if highlighted:
        painter.save()
        wash = QColor(scheme.accent)
        wash.setAlpha(48)
        painter.fillRect(rect.adjusted(1, 1, -1, -1), wash)
        pen = QPen(QColor(scheme.accent))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.restore()

    if tag_color:
        painter.save()
        color = QColor(tag_color)
        bar_height = max(3, rect.height() // 6)
        painter.fillRect(rect.x() + 1, rect.y() + 1, rect.width() - 2, bar_height, color)
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.restore()

    today_dot_area = None
    if is_today:
        # A filled circle around the day number, its text in whichever of
        # black/white reads on the accent — in every theme.
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFontMetrics
        # Big enough for the number (month grids make their cells tall
        # enough for this — JournalCalendar.apply_cell_minimums); in a cell
        # that is still smaller, the number shrinks rather than spilling
        # out of the circle.
        diameter = max(8, min(today_circle_diameter(font), rect.width() - 2, rect.height() - 2))
        number_font = _fit_font(font, day_text, diameter)
        center = rect.center()
        circle = QRect(center.x() - diameter // 2, center.y() - diameter // 2, diameter, diameter)
        fill = QColor(scheme.accent)
        # A ring in the pane colour around the fill: on an ordinary cell it
        # is invisible, but on the SELECTED cell (Qt fills that with the
        # highlight colour, often close to the accent) it is what keeps
        # today's circle visible and distinct from the selection (§37).
        ring = QPen(QColor(scheme.panel))
        ring.setWidth(2)
        painter.setPen(ring)
        painter.setBrush(fill)
        painter.drawEllipse(circle)
        painter.setPen(readable_text_color(fill))
        painter.setFont(number_font)
        # Centred by its ink, not its line box, so the digits sit in the
        # middle of the circle whatever the font's ascent and descent.
        ink = QFontMetrics(number_font).tightBoundingRect(day_text)
        middle = QPointF(circle.x() + circle.width() / 2.0, circle.y() + circle.height() / 2.0)
        painter.drawText(QPointF(middle.x() - ink.x() - ink.width() / 2.0,
                                 middle.y() - ink.y() - ink.height() / 2.0), day_text)
        painter.restore()
        # Where today's entry dot goes: inside the circle, under the number,
        # in the number's colour — so it reads against the fill in every
        # theme instead of sitting on the circle's rim (Group 3 fixes, B4).
        today_dot_area = (middle.x(), middle.y() + ink.height() / 2.0,
                          circle.y() + circle.height() - 2, readable_text_color(fill))

    if has_entry or other_content:
        painter.save()
        diameter = max(3, rect.height() // 10)
        cx = rect.center().x()
        cy = rect.bottom() - diameter - 1
        colour = QColor(scheme.text)
        if today_dot_area is not None:
            painter.setRenderHint(QPainter.Antialiasing)
            ink_bottom, inner_bottom = today_dot_area[1], today_dot_area[2]
            diameter = max(3, min(diameter, int(inner_bottom - ink_bottom) - 1))
            cx = int(round(today_dot_area[0]))
            cy = int(round((ink_bottom + inner_bottom - diameter) / 2.0))
            colour = QColor(today_dot_area[3])
        if has_entry:
            # A solid dot: this day has journal writing saved on it.
            painter.setBrush(QBrush(colour))
            painter.setPen(Qt.NoPen)
        else:
            # A hollow dot: something is on this day (an event, a note, a
            # task) but no journal entry — a different mark on purpose.
            outline = QColor(colour)
            outline.setAlpha(120 if today_dot_area is None else 200)
            pen = QPen(outline)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(cx - diameter // 2, cy, diameter, diameter)
        painter.restore()

_ALL_WEEKDAYS = [Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday, Qt.Saturday, Qt.Sunday]


class JournalCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setGridVisible(True)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        # Sunday-first, explicitly, rather than whatever the system locale
        # happens to say. Week View shows Sunday–Saturday, and its navigator
        # is this same widget: clicking a ROW there has to select exactly the
        # seven days that row displays (Part 30), which is only true if a row
        # IS a Sunday–Saturday week. Setting it here rather than on just the
        # navigator keeps the app's two month grids identical — two calendars
        # side by side starting their weeks on different days would read as a
        # bug even though each was internally consistent.
        self.setFirstDayOfWeek(Qt.Sunday)
        self._entry_dates: set[str] = set()
        self._tag_colors: dict[str, str] = {}  # date_str -> hex color
        self._entry_lengths: dict[str, int] = {}  # date_str -> body length (for tooltip)
        # The days Week View is currently showing, when this calendar is
        # acting as its navigator (Part 30). Empty in the Daily Journal,
        # where nothing has a range — one calendar implementation serving
        # both, rather than a second month grid written for the week view.
        self._highlight_dates: set[str] = set()
        # Days with an event or a note but no journal writing — marked, but
        # distinctly from a real journal entry. See set_month_data().
        self._other_content_dates: set[str] = set()
        self._scheme: ColorScheme = PRESETS["Light"]
        self.set_scheme(self._scheme)

    def _hide_qt_number(self, date: QDate):
        if getattr(self, "_hidden_number_date", None) == date:
            return
        previous = getattr(self, "_hidden_number_date", None)
        if previous is not None:
            self.setDateTextFormat(previous, QTextCharFormat())
        invisible = QTextCharFormat()
        invisible.setForeground(QColor(0, 0, 0, 0))
        self.setDateTextFormat(date, invisible)
        self._hidden_number_date = date

    def set_scheme(self, scheme: ColorScheme):
        self._scheme = scheme

        # Override Qt's built-in weekend-red and give every day (and the
        # Mon/Tue/... header) the scheme's own text color.
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(scheme.text))
        for day in _ALL_WEEKDAYS:
            self.setWeekdayTextFormat(day, text_format)
        header_format = QTextCharFormat()
        header_format.setForeground(QColor(scheme.text))
        # And the header's background: left to Qt it is white in every theme,
        # which made the day names unreadable (light on white) in dark ones.
        header_format.setBackground(QColor(scheme.panel))
        header_format.setFontWeight(600)
        self.setHeaderTextFormat(header_format)

        self.updateCells()

    def set_month_data(self, entry_dates: set, tag_colors: dict,
                        entry_lengths: dict | None = None,
                        other_content_dates: set | None = None):
        """What to mark on the grid.

        `entry_dates` means exactly one thing: these dates have a JOURNAL
        ENTRY — a saved document with visible text in it. It is not "these
        dates have a database row" and not "something happened on this day";
        conflating those is what made the calendar claim an entry existed on
        a day that had only been opened (Part 19).

        `other_content_dates` are days that hold a calendar event or
        Reader's Notes but no journal writing. They still deserve to be
        visible on a year's overview, so they get their own quieter mark
        rather than being folded into the journal indicator.
        """
        self._entry_dates = set(entry_dates)
        self._tag_colors = tag_colors
        self._entry_lengths = entry_lengths or {}
        self._other_content_dates = set(other_content_dates or ()) - self._entry_dates
        self.updateCells()

    def set_highlight_dates(self, dates):
        """Marks a set of ISO dates as "currently shown elsewhere".

        Used by the Weekly Calendar's navigator to indicate all seven visible
        days, including when the window has been shifted off a conventional
        Sunday–Saturday row and spans two rows of the grid (Part 31) — which
        is exactly why this takes a set of days rather than a start date and
        a length.
        """
        dates = set(dates or ())
        if dates != self._highlight_dates:
            self._highlight_dates = dates
            self.updateCells()

    @staticmethod
    def _key(date: QDate) -> str:
        return date.toString("yyyy-MM-dd")

    def apply_cell_minimums(self):
        """The grid's minimum size at its current font (Group 3 fixes, B4 and
        C3): wide enough that Qt lays out all seven columns (its own
        minimumSizeHint — narrower than that and the grid is cut off at
        Friday), and with cells big enough for today's circle around a
        two-digit number. Recomputed on every font change."""
        cell = today_circle_diameter(self.font()) + 2
        # Qt's hint depends on the weekday-header format the panel picked
        # for the CURRENT width ("Mon" needs wider columns than "M"). The
        # floor must not: measured with the single-letter headers every
        # narrow pane falls back to, so it is the same in a session and
        # after a restart (Group 3 fixes, C3).
        shown_format = self.horizontalHeaderFormat()
        if shown_format != QCalendarWidget.SingleLetterDayNames:
            self.setUpdatesEnabled(False)
            self.setHorizontalHeaderFormat(QCalendarWidget.SingleLetterDayNames)
            hint = self.minimumSizeHint()
            self.setHorizontalHeaderFormat(shown_format)
            self.setUpdatesEnabled(True)
        else:
            hint = self.minimumSizeHint()
        self.setMinimumWidth(max(hint.width(), 7 * cell + 4))
        self.setMinimumHeight(max(hint.height(), 7 * cell + 4))

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate):
        # Draw Qt's own cell contents (background, selection, day number)
        # FIRST — anything painted before this gets wiped out by it. Today's
        # number is drawn only inside its circle (paint_day_marks), so Qt's
        # copy of it is painted invisibly.
        if date == QDate.currentDate():
            self._hide_qt_number(date)
        super().paintCell(painter, rect, date)

        key = self._key(date)
        paint_day_marks(
            painter, rect, self._scheme, day_text=str(date.day()), font=self.font(),
            tag_color=self._tag_colors.get(key),
            has_entry=key in self._entry_dates,
            other_content=key in self._other_content_dates,
            highlighted=key in self._highlight_dates,
            is_today=date == QDate.currentDate(),
        )
