"""The one canonical `selectedDate`, and the navigation history built on it.

Before round 22 the "currently selected day" existed as four independent
variables that happened to be kept in sync by hand: `MainWindow.current_date`
(an ISO string), `JournalCalendar`'s own Qt selection, `TaskListWidget.date`,
and the companion panel's own copy. Nothing enforced that they agreed — they
agreed because every code path that changed one remembered to change the
others, which is exactly the kind of invariant that holds right up until a
new feature forgets it. Adding two more date-aware features (the Day Calendar
and Reader's Notes) plus internal date links to that arrangement would have
meant six hand-synchronized copies of one value.

So there is now exactly one:

    Monthly Calendar ──writes──> SelectedDate ──notifies──> Journal
                                                            Day Calendar
                                                            Reader's Notes

`SelectedDate` owns the value and emits `changed` when it actually changes.
Consumers never store a date of their own that means "the day being shown" —
they receive it, render it, and read `.value` if they need it again later.
(A widget may of course still hold the date of something it has *loaded*, for
staleness checks; what it must not do is independently decide what the
selected date is.)

The monthly calendar's *visible month* deliberately stays separate and lives
where it always has, on the calendar widget itself: paging to November to look
around does not change which day is selected, and conflating the two would
make browsing the calendar silently reassign the journal.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QObject, Signal

ISO = "yyyy-MM-dd"


def today_iso() -> str:
    return QDate.currentDate().toString(ISO)


def to_qdate(date_str: str) -> QDate:
    return QDate.fromString(date_str, ISO)


def human(date_str: str) -> str:
    """'2026-09-15' -> 'September 15, 2026' — used in headings, link
    tooltips and the Return-to button, so they all phrase dates the same."""
    qdate = to_qdate(date_str)
    return qdate.toString("MMMM d, yyyy") if qdate.isValid() else date_str


def human_long(date_str: str) -> str:
    """As `human`, with the weekday — for the journal's own date header."""
    qdate = to_qdate(date_str)
    return qdate.toString("dddd, MMMM d, yyyy") if qdate.isValid() else date_str


class SelectedDate(QObject):
    """The single source of truth for which day the app is showing.

    Deliberately tiny: it holds an ISO date string, validates it, and tells
    everyone when it changes. It does NOT know about the journal, the
    calendar, or notes — everything that cares subscribes to `changed`.
    That direction matters: it keeps this module dependency-free, so every
    date-aware feature can depend on it without depending on each other.
    """

    changed = Signal(str)  # the new ISO date

    def __init__(self, initial: str | None = None, parent=None):
        super().__init__(parent)
        self._value = initial or today_iso()

    @property
    def value(self) -> str:
        return self._value

    def qdate(self) -> QDate:
        return to_qdate(self._value)

    def set(self, date_str: str) -> bool:
        """Sets the selected date, emitting `changed` only on a real change.

        Returns True if the value actually changed. Re-selecting the day
        already shown is a deliberate no-op rather than a reload: the
        monthly calendar emits `clicked` on every click, including on the
        already-selected day, and reloading there would throw away the
        caret position (and, worse, race the autosave of the entry being
        typed into) for no benefit.

        An unparseable date is rejected outright rather than stored — every
        consumer downstream assumes a valid ISO day, and letting a bad one
        through would corrupt whichever entry got saved under it.
        """
        if not date_str or not to_qdate(date_str).isValid():
            return False
        if date_str == self._value:
            return False
        self._value = date_str
        self.changed.emit(date_str)
        return True


class VisibleWeek(QObject):
    """The Weekly Schedule's own date state: `visible_start_date` (Part 32).

    This is deliberately NOT a second copy of SelectedDate, and the two have
    different jobs:

        SelectedDate      "which single day am I writing about"
                          — owned by the Daily Journal, written by the
                            monthly calendar and by internal date links.

        VisibleWeek       "which seven consecutive days is Week View showing"
                          — owned by the Weekly Calendar, written by its own
                            navigator and by Left/Right navigation.

    They stay two values, but since Group 3 (decision G3-5, Master Spec
    §6.2) they are connected in ONE direction: when the selected date
    changes — from any source — the week aligns to the Sunday–Saturday week
    containing it (the main window connects SelectedDate.changed to
    WeekCalendarWidget.show_week_of). The other direction never happens:
    browsing, sliding or stepping the week does not select a day; only
    clicking a day name hands a day to Daily Jorts. This replaced the old
    View → "Show This Day's Week" command (removed, §34).

    The window is seven consecutive days starting at `start` — NOT a calendar
    week. `shift()` slides it a day (or steps it seven), so Sunday–Saturday
    can become Monday–Sunday and stay there until something aligns it again
    (Part 28/31; §32 as decided 2026-09-24).
    """

    changed = Signal(str)  # the new visible start date, ISO

    DAYS = 7

    def __init__(self, start: str | None = None, parent=None):
        super().__init__(parent)
        self._start = start if start and to_qdate(start).isValid() else week_start_for(today_iso())

    @property
    def start(self) -> str:
        return self._start

    def dates(self) -> list[str]:
        """The seven ISO dates currently visible, in display order."""
        first = to_qdate(self._start)
        return [first.addDays(offset).toString(ISO) for offset in range(self.DAYS)]

    def qdates(self) -> list[QDate]:
        first = to_qdate(self._start)
        return [first.addDays(offset) for offset in range(self.DAYS)]

    def end(self) -> str:
        return to_qdate(self._start).addDays(self.DAYS - 1).toString(ISO)

    def contains(self, date_str: str) -> bool:
        return self._start <= date_str <= self.end()

    def set_start(self, date_str: str) -> bool:
        """Starts the window at this exact day — no snapping to a Sunday."""
        if not date_str or not to_qdate(date_str).isValid():
            return False
        if date_str == self._start:
            return False
        self._start = date_str
        self.changed.emit(self._start)
        return True

    def shift(self, days: int) -> bool:
        """Slides the window by whole days; ±1 is what the arrow keys use."""
        return self.set_start(to_qdate(self._start).addDays(days).toString(ISO))

    def align_to_week_of(self, date_str: str) -> bool:
        """Snaps to the conventional Sunday–Saturday week containing a day.

        Only called when the user explicitly picks a week row in the
        navigator — never automatically to "correct" a shifted window.
        """
        return self.set_start(week_start_for(date_str))


class VisibleYear(QObject):
    """The year the Yearly Calendar is showing (Master Spec §6.1).

    A period being VIEWED, not a selection: browsing years never changes
    SelectedDate. It follows the selected date when that changes, the way
    the Weekly Schedule's week does (decision G3-5).
    """

    changed = Signal(int)

    def __init__(self, year: int | None = None, parent=None):
        super().__init__(parent)
        self._year = year or QDate.currentDate().year()

    @property
    def value(self) -> int:
        return self._year

    def set(self, year: int) -> bool:
        year = max(1900, min(2100, int(year)))
        if year == self._year:
            return False
        self._year = year
        self.changed.emit(year)
        return True

    def shift(self, years: int) -> bool:
        return self.set(self._year + years)


def week_start_for(date_str: str) -> str:
    """The Sunday on or before this date.

    Qt numbers weekdays 1=Monday..7=Sunday, so `dayOfWeek() % 7` is the
    number of days back to Sunday (Sunday itself gives 0).
    """
    qdate = to_qdate(date_str)
    if not qdate.isValid():
        qdate = QDate.currentDate()
    return qdate.addDays(-(qdate.dayOfWeek() % 7)).toString(ISO)


class NavigationHistory(QObject):
    """Where the user came from, so an internal date link can offer a way back.

    Only *link* navigation is recorded. Picking a day on the monthly calendar
    is ordinary browsing and deliberately does not push history — otherwise
    the Return button would point at whatever day happened to be selected
    before, which is noise rather than a trail. Following a link from
    September 15 to June 4 to March 12 leaves a real trail
    (15 -> 4 -> 12) that Back walks in reverse.

    Bounded: a long session of link-following shouldn't grow this without
    limit, and nobody needs to walk back more than a few dozen hops.
    """

    changed = Signal()  # history depth changed; re-read can_go_back()/back_target()

    MAX_DEPTH = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stack: list[str] = []

    def push(self, from_date: str):
        self._stack.append(from_date)
        if len(self._stack) > self.MAX_DEPTH:
            del self._stack[0]
        self.changed.emit()

    def pop(self) -> str | None:
        if not self._stack:
            return None
        value = self._stack.pop()
        self.changed.emit()
        return value

    def clear(self):
        if self._stack:
            self._stack.clear()
            self.changed.emit()

    def can_go_back(self) -> bool:
        return bool(self._stack)

    def back_target(self) -> str | None:
        return self._stack[-1] if self._stack else None

    def depth(self) -> int:
        return len(self._stack)
