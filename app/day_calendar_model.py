"""The day calendar's pure logic: time <-> pixel conversion, snapping, and
overlap layout.

Deliberately separate from day_calendar.py's widget. None of this imports Qt
widgets or touches the database — it's arithmetic over plain numbers and
CalendarEvent-shaped objects, which means the fiddly parts (does a 15-minute
snap round correctly at 23:52? do three mutually overlapping events each get
a third of the width? does dragging an event past midnight clamp instead of
wrapping?) can be tested directly, without constructing a window or
simulating a mouse.

Time is always integer minutes from midnight, 0..1440, end exclusive — see
database.py's calendar_events comment for why that representation.
"""
from __future__ import annotations

from dataclasses import dataclass

MINUTES_PER_DAY = 24 * 60
DEFAULT_SNAP_MINUTES = 15
MIN_EVENT_MINUTES = 15  # a drag shorter than this still produces a usable event
DEFAULT_NEW_EVENT_MINUTES = 60  # an event created by pointing rather than dragging


def clamp_minute(minute: int) -> int:
    return max(0, min(MINUTES_PER_DAY, int(minute)))


def snap_minute(minute: int, snap: int = DEFAULT_SNAP_MINUTES) -> int:
    """Rounds to the nearest `snap` boundary (not down — rounding down makes
    dragging feel like it lags behind the cursor by up to a whole increment),
    then clamps into the day."""
    if snap <= 0:
        return clamp_minute(minute)
    return clamp_minute(round(minute / snap) * snap)


def minute_to_y(minute: int, hour_height: float, top_padding: float = 0.0) -> float:
    return top_padding + (minute / 60.0) * hour_height


def y_to_minute(y: float, hour_height: float, top_padding: float = 0.0) -> int:
    if hour_height <= 0:
        return 0
    return clamp_minute(round(((y - top_padding) / hour_height) * 60.0))


def format_minute(minute: int, use_24h: bool = False) -> str:
    """0 -> '12:00 AM' / '00:00'. 1440 (the exclusive end of a full day) is
    rendered as midnight rather than as an invalid 24th hour."""
    minute = clamp_minute(minute)
    hours, mins = divmod(minute % MINUTES_PER_DAY, 60)
    if use_24h:
        return f"{hours:02d}:{mins:02d}"
    suffix = "AM" if hours < 12 else "PM"
    display_hour = hours % 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{mins:02d} {suffix}"


def normalize_span(start: int, end: int, snap: int = DEFAULT_SNAP_MINUTES) -> tuple[int, int]:
    """Turns any pair of drag endpoints into a valid, snapped (start, end).

    Handles dragging upward (end before start) by swapping rather than
    rejecting, and guarantees `end > start` by at least MIN_EVENT_MINUTES —
    the "prevent end_time <= start_time" rule enforced here, in one place,
    rather than separately in create/move/resize.

    If enforcing the minimum would push `end` past midnight, the event is
    pulled back from the end of the day instead of being allowed to overflow
    into the next one (this widget shows exactly one day).
    """
    start, end = clamp_minute(start), clamp_minute(end)
    if end < start:
        start, end = end, start
    start, end = snap_minute(start, snap), snap_minute(end, snap)
    if end - start < MIN_EVENT_MINUTES:
        end = start + MIN_EVENT_MINUTES
    if end > MINUTES_PER_DAY:
        end = MINUTES_PER_DAY
        start = min(start, end - MIN_EVENT_MINUTES)
    return start, end


def new_event_span(y: float, hour_height: float, top_padding: float = 0.0,
                    snap: int = DEFAULT_SNAP_MINUTES,
                    length: int = DEFAULT_NEW_EVENT_MINUTES) -> tuple[int, int]:
    """The span for an event created by pointing at one spot, not dragging.

    Used by the double-click gesture in both views. It goes through the same
    y-to-minute conversion and snapping as a drag, so a double-click at
    2:07 PM produces 2:00–3:00 PM exactly as a drag to that pixel would
    start at 2:00.

    Always exactly `length` minutes (Group 3 criterion 20): double-clicking
    at 11:30 PM gives 11:30 PM–12:30 AM, the end running into the next day
    (an end past 1440 means the next day; recurrence.span_from turns it into
    an end date). Before Group 3 an event could not cross midnight, so this
    stopped at midnight instead.
    """
    start = snap_minute(clamp_minute(y_to_minute(y, hour_height, top_padding)), snap)
    start = min(start, MINUTES_PER_DAY - snap)
    return start, start + length


def shift_span(start: int, end: int, delta: int, snap: int = DEFAULT_SNAP_MINUTES) -> tuple[int, int]:
    """Moves an event while preserving its duration (the Part 25 rule).

    The duration is preserved even when the move would run off either end of
    the day: the event stops against the boundary rather than being squashed
    against it, which is what every calendar app does and what a user
    dragging quickly expects.
    """
    duration = end - start
    new_start = snap_minute(start + delta, snap)
    if new_start < 0:
        new_start = 0
    if new_start + duration > MINUTES_PER_DAY:
        new_start = MINUTES_PER_DAY - duration
    return new_start, new_start + duration


def resize_span(start: int, end: int, edge: str, new_minute: int,
                 snap: int = DEFAULT_SNAP_MINUTES) -> tuple[int, int]:
    """Drags one edge of an event, keeping the other fixed and never letting
    the two cross (dragging the top edge below the bottom pins it at the
    minimum duration rather than inverting the event)."""
    new_minute = snap_minute(new_minute, snap)
    if edge == "top":
        start = min(new_minute, end - MIN_EVENT_MINUTES)
        start = max(0, start)
    else:
        end = max(new_minute, start + MIN_EVENT_MINUTES)
        end = min(MINUTES_PER_DAY, end)
    return start, end


@dataclass
class ColumnGeometry:
    """Where the day columns sit across a timeline's width.

    Shared by the Day View (one column) and the Week View (seven), so the
    two cannot drift into different ideas of where the grid lines are. Like
    everything else in this module it's plain arithmetic — the widget passes
    in its current width and the measured gutter, and gets back pixel
    positions it can paint with or hit-test against.
    """

    total_width: float
    gutter: float
    count: int = 1
    right_margin: float = 6.0

    @property
    def content_width(self) -> float:
        return max(0.0, self.total_width - self.gutter - self.right_margin)

    @property
    def column_width(self) -> float:
        return self.content_width / max(1, self.count)

    def left(self, index: int) -> float:
        return self.gutter + index * self.column_width

    def bounds(self, index: int) -> tuple[float, float]:
        left = self.left(index)
        return left, left + self.column_width

    def index_at(self, x: float) -> int:
        """Which column a pixel x falls in, clamped to a real column.

        Clamping rather than returning None is deliberate: a drag that runs
        off the left edge into the hour gutter, or off the right edge of the
        window, should land in the nearest day rather than be discarded
        halfway through the gesture.
        """
        if self.column_width <= 0:
            return 0
        index = int((x - self.gutter) // self.column_width)
        return max(0, min(self.count - 1, index))


@dataclass
class EventLayout:
    """Where one event is drawn horizontally, as fractions of the column
    width, so the widget can scale it to whatever width it happens to have."""
    event: object
    column: int
    column_count: int

    @property
    def x_fraction(self) -> float:
        return self.column / self.column_count

    @property
    def width_fraction(self) -> float:
        return 1.0 / self.column_count


def layout_events(events: list) -> list:
    """General side-by-side layout for overlapping events (Part 27).

    The algorithm is the standard one, not a set of special cases:

      1. Sort by start time (then by longest-first, so a long event tends to
         take the leftmost column and shorter ones stack to its right, which
         reads better than the reverse).
      2. Walk the list accumulating a "cluster" of events that transitively
         overlap — a new event joins the current cluster if it starts before
         the cluster's running maximum end time.
      3. Within a cluster, assign each event the lowest column index not
         already occupied by an event it actually overlaps.
      4. Every event in the cluster is drawn at 1/N width where N is the
         number of columns that cluster needed.

    Step 4 uses the cluster-wide column count rather than a per-event one so
    that events in the same visual group line up on a shared grid instead of
    each picking its own width — which is what makes overlapping blocks look
    like a calendar rather than a pile.

    Untimed (all-day) events are excluded here; they're drawn in their own
    strip above the timeline and have no position on it.
    """
    timed = [e for e in events if not getattr(e, "all_day", False)]
    if not timed:
        return []

    from .recurrence import sortable_key
    ordered = sorted(timed, key=lambda e: (e.start_minute, -(e.end_minute - e.start_minute),
                                           sortable_key(e.id)))

    layouts: list = []
    cluster: list = []
    cluster_end = -1

    def flush(cluster_events: list):
        if not cluster_events:
            return
        columns: list = []  # columns[i] = end_minute of the last event placed in column i
        assignments = []
        for event in cluster_events:
            placed = False
            for index, occupied_until in enumerate(columns):
                if event.start_minute >= occupied_until:
                    columns[index] = event.end_minute
                    assignments.append((event, index))
                    placed = True
                    break
            if not placed:
                columns.append(event.end_minute)
                assignments.append((event, len(columns) - 1))
        count = len(columns)
        for event, index in assignments:
            layouts.append(EventLayout(event=event, column=index, column_count=count))

    for event in ordered:
        if cluster and event.start_minute >= cluster_end:
            flush(cluster)
            cluster = []
            cluster_end = -1
        cluster.append(event)
        cluster_end = max(cluster_end, event.end_minute)
    flush(cluster)

    return layouts
