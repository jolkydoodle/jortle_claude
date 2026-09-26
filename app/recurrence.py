"""Event time spans, recurrence rules and the occurrences they produce.

Pure functions over event rows: nothing here touches the database or Qt, so
every rule can be tested directly (Master Spec §§22.1, 23, 25).

THE TIME MODEL (Group 3, decision G3-2)

An event row keeps `date` + `start_minute` as its start and gains `end_date`
(NULL = the same day as `date`):

  * timed:   [date + start_minute, end_date + end_minute), end exclusive and
             strictly later than the start. "9 PM to midnight" is
             (d, 1260) -> (d+1, 0); rows written before Group 3 may also
             say (d, 1260) -> (d, 1440), which means the same instant and is
             never rewritten.
  * untimed: every day from `date` to `end_date`, inclusive. The minute
             columns are ignored.

A span that crosses midnight is ONE event. Views draw it as one piece per
day (`day_pieces`), and every piece carries the same event identity.

RECURRENCE (decision G3-3)

A series is an ordinary event row (the "master") plus one rule
(`event_recurrence`). Occurrences are computed for the range being looked at
and never stored. An occurrence changed on its own is its own row, pointing
back with `series_id` + `occurrence_date` (the iCalendar RECURRENCE-ID
pattern), and `event_exceptions` lists the series dates the rule must not
produce — both the cancelled ones and the ones an override replaced. The
identity of an occurrence is (series id, original date), whatever its
current date is.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

MINUTES_PER_DAY = 24 * 60

FREQUENCIES = ("daily", "weekly", "monthly", "yearly")

# Weekday numbers used everywhere in this module: 0 = Sunday … 6 = Saturday,
# the app's week (date_state.week_start_for). Python's date.weekday() is
# 0 = Monday, so every conversion goes through these two helpers.
_ICS_DAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
_DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def sunday_weekday(day: date) -> int:
    return (day.weekday() + 1) % 7


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def iso(day: date) -> str:
    return day.isoformat()


def add_days(value: str, days: int) -> str:
    return iso(parse_date(value) + timedelta(days=days))


def days_between(first: str, second: str) -> int:
    return (parse_date(second) - parse_date(first)).days


def _week_start(day: date, week_start: int = 0) -> date:
    """The first day of the week containing `day`, for weeks starting on
    weekday `week_start` (0 = Sunday, the app's week)."""
    return day - timedelta(days=(sunday_weekday(day) - week_start) % 7)


# ------------------------------------------------------------------- spans
def end_date_of(event) -> str:
    """The event's last date as stored (its `date` when `end_date` is NULL)."""
    return getattr(event, "end_date", None) or event.date


def span_days(event) -> int:
    """How many days after its start date the event ends (0 = same day)."""
    return max(0, days_between(event.date, end_date_of(event)))


def absolute_end(event) -> int:
    """End of a timed event in minutes after midnight of its start date."""
    return span_days(event) * MINUTES_PER_DAY + event.end_minute


def duration_minutes(event) -> int:
    return max(0, absolute_end(event) - event.start_minute)


def validate_span(event_date: str, start_minute: int, end_date: Optional[str],
                  end_minute: int, all_day: bool) -> None:
    """Raises ValueError for a span that must never be stored.

    The rule that makes "9 PM to midnight" impossible to store backwards: a
    timed event ends strictly after it starts, measured across days, so an
    end earlier in the day than the start is only valid on a later date.
    """
    try:
        first = parse_date(event_date)
        last = parse_date(end_date) if end_date else first
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid event date: {exc}") from exc
    if last < first:
        raise ValueError("an event cannot end on a date before it starts")
    if all_day:
        return
    if not (0 <= int(start_minute) < MINUTES_PER_DAY):
        raise ValueError("start time out of range")
    if not (0 <= int(end_minute) <= MINUTES_PER_DAY):
        raise ValueError("end time out of range")
    if (last - first).days * MINUTES_PER_DAY + int(end_minute) <= int(start_minute):
        raise ValueError("an event must end after it starts")


def span_from(start_date: str, start_minute: int, duration: int) -> tuple:
    """(end_date or None, end_minute) for a timed event of `duration` minutes.

    A span that ends exactly at midnight ends at minute 0 of the next date —
    the one canonical form for new rows. None means "same day as the start".
    """
    total = start_minute + max(1, duration)
    days, minute = divmod(total, MINUTES_PER_DAY)
    end_date = add_days(start_date, days) if days else None
    return end_date, minute


# ------------------------------------------------------------ recurrence
@dataclass(frozen=True)
class RecurrenceRule:
    freq: str                       # one of FREQUENCIES
    interval: int = 1               # every N days/weeks/months/years
    weekdays: frozenset = frozenset()   # weekly only; 0 = Sunday … 6 = Saturday
    until: Optional[str] = None     # last date an occurrence may START on (inclusive)
    # Which weekday "every N weeks" counts weeks from (0 = Sunday). Sunday
    # unless a whole series was moved: moving Fri+Sat every 2 weeks by a day
    # gives Sat+Sun, which only stay in the same counted week if the week
    # moves with them (shift_rule).
    week_start: int = 0

    def __post_init__(self):
        if self.freq not in FREQUENCIES:
            raise ValueError(f"unknown frequency {self.freq!r}")
        if int(self.interval) < 1:
            raise ValueError("interval must be at least 1")
        if any(not (0 <= int(d) <= 6) for d in self.weekdays):
            raise ValueError("weekdays are 0 (Sunday) to 6 (Saturday)")
        if not (0 <= int(self.week_start) <= 6):
            raise ValueError("week_start is 0 (Sunday) to 6 (Saturday)")
        if self.until is not None:
            parse_date(self.until)

    # Stored as a comma list, e.g. "1,3" for Monday and Wednesday.
    def weekdays_text(self) -> str:
        return ",".join(str(d) for d in sorted(self.weekdays))

    @staticmethod
    def weekdays_from_text(text: str) -> frozenset:
        return frozenset(int(p) for p in (text or "").split(",") if p.strip() != "")

    def describe(self) -> str:
        unit = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}[self.freq]
        every = f"every {unit}" if self.interval == 1 else f"every {self.interval} {unit}s"
        text = every
        if self.freq == "weekly" and self.weekdays:
            text += " on " + ", ".join(_DAY_NAMES[d] for d in sorted(self.weekdays))
        if self.until:
            text += f" until {self.until}"
        return text

    def to_rrule(self, timed: bool) -> str:
        """The iCalendar RRULE value (RFC 5545) for this rule.

        WKST keeps "every 2 weeks" counted in the same weeks this module
        counts (Sunday-start unless the series was moved, see week_start). Months without the start's day number (the
        31st) and 29 February in common years are skipped, which is also what
        RFC 5545 does with dates that don't exist.
        """
        parts = [f"FREQ={self.freq.upper()}", f"INTERVAL={self.interval}"]
        if self.freq == "weekly":
            parts.append(f"WKST={_ICS_DAYS[self.week_start]}")
            if self.weekdays:
                parts.append("BYDAY=" + ",".join(_ICS_DAYS[d] for d in sorted(self.weekdays)))
        if self.until:
            compact = self.until.replace("-", "")
            parts.append(f"UNTIL={compact}T235959" if timed else f"UNTIL={compact}")
        return ";".join(parts)


def occurrence_dates(first: str, rule: RecurrenceRule, lo: str, hi: str,
                     excluded: Iterable[str] = ()) -> list:
    """Start dates of the series' occurrences that fall within [lo, hi].

    `first` is the series' own start date (the master row's `date`), which
    is always an occurrence unless excluded; nothing before it is produced,
    and nothing after `rule.until`.
    """
    start = parse_date(first)
    lo_d, hi_d = max(parse_date(lo), start), parse_date(hi)
    if rule.until:
        hi_d = min(hi_d, parse_date(rule.until))
    if hi_d < lo_d:
        return []
    skip = set(excluded)
    found: list = []
    interval = int(rule.interval)

    if rule.freq == "daily":
        offset = (lo_d - start).days
        k = -(-offset // interval)          # ceiling division: first step >= lo
        day = start + timedelta(days=k * interval)
        while day <= hi_d:
            found.append(day)
            day += timedelta(days=interval)
    elif rule.freq == "weekly":
        weekdays = set(rule.weekdays) or {sunday_weekday(start)}
        base_week = _week_start(start, rule.week_start)
        day = lo_d
        while day <= hi_d:
            weeks = (_week_start(day, rule.week_start) - base_week).days // 7
            if weeks % interval == 0 and sunday_weekday(day) in weekdays:
                found.append(day)
            day += timedelta(days=1)
    elif rule.freq == "monthly":
        months_to_lo = (lo_d.year - start.year) * 12 + (lo_d.month - start.month)
        k = max(0, -(-months_to_lo // interval) - 1)
        while True:
            total = start.month - 1 + k * interval
            year, month = start.year + total // 12, total % 12 + 1
            if date(year, month, 1) > hi_d:
                break
            if start.day <= monthrange(year, month)[1]:
                day = date(year, month, start.day)
                if lo_d <= day <= hi_d:
                    found.append(day)
            k += 1
    else:  # yearly
        k = max(0, (lo_d.year - start.year) // interval - 1)
        while True:
            year = start.year + k * interval
            if year > hi_d.year:
                break
            try:
                day = date(year, start.month, start.day)
            except ValueError:       # 29 February in a common year: skipped
                day = None
            if day is not None and lo_d <= day <= hi_d:
                found.append(day)
            k += 1

    return [iso(d) for d in found if iso(d) not in skip]


def nth_occurrences(first: str, rule: RecurrenceRule, count: int, horizon_years: int = 400) -> list:
    """The first `count` occurrence dates (ignoring exceptions), in order."""
    if count <= 0:
        return []
    out: list = []
    lo = first
    step_days = 366 * 4
    limit = add_days(first, 366 * horizon_years)
    while len(out) < count and lo <= limit:
        hi = add_days(lo, step_days)
        out.extend(occurrence_dates(first, rule, lo, hi))
        lo = add_days(hi, 1)
        if rule.until and lo > rule.until:
            break
    return out[:count]


def remap_dates(old_first: str, old_rule: RecurrenceRule, new_first: str,
                new_rule: RecurrenceRule, dates: Iterable[str], shift: int) -> dict:
    """Where each occurrence date of a series lands when the whole series
    moves by `shift` days: the same occurrence, `shift` days later — as long
    as the moved series really has an occurrence there. Otherwise None.

    Used to keep cancelled dates and separately edited occurrences attached
    to their occurrence when a series moves. None happens only when a move
    changes which dates a rule can produce (a monthly series on the 30th
    moved to the 1st, say): the caller then drops a cancelled date, and
    keeps an edited occurrence as an ordinary event, so nothing is lost.
    """
    result = {}
    for d in sorted(set(dates)):
        moved = add_days(d, shift)
        result[d] = moved if occurrence_dates(new_first, new_rule, moved, moved) else None
    return result


def _period_bounds(day: date, rule: RecurrenceRule) -> Optional[tuple]:
    """The week / month / year containing `day` for a rule of that
    frequency (None for daily rules, whose period is the day itself)."""
    if rule.freq == "weekly":
        lo = _week_start(day, rule.week_start)
        return lo, lo + timedelta(days=6)
    if rule.freq == "monthly":
        return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])
    if rule.freq == "yearly":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    return None


def remap_for_rule(first: str, rule: RecurrenceRule, dates: Iterable[str]) -> dict:
    """Where each occurrence date of a series lands when its RULE changes
    (Group 3 fixes, A5): a date the new rule still produces stays; otherwise
    it moves to the new rule's only occurrence in the same week / month /
    year — Monday 14 Sep of a Monday series becomes Tuesday 15 Sep when the
    series changes to Tuesdays. With no such single date (or when another
    occurrence already has it) the result is None: the caller drops a
    cancelled date and keeps an edited occurrence as an ordinary event, so
    nothing the user wrote is lost."""
    ordered = sorted(set(dates))
    result: dict = {}
    taken: set = set()
    for d in ordered:                      # dates that stay put come first
        if occurrence_dates(first, rule, d, d):
            result[d] = d
            taken.add(d)
    for d in ordered:
        if d in result:
            continue
        result[d] = None
        bounds = _period_bounds(parse_date(d), rule)
        if bounds is None:
            continue
        candidates = [c for c in occurrence_dates(first, rule, iso(bounds[0]), iso(bounds[1]))
                      if c not in taken]
        if len(candidates) == 1:
            result[d] = candidates[0]
            taken.add(candidates[0])
    return result


def next_occurrence(first: str, rule: RecurrenceRule, lo: str,
                    excluded: Iterable[str] = (), horizon_years: int = 400) -> Optional[str]:
    """The first occurrence date on or after `lo` (skipping `excluded`), or
    None if the series has none. Searched a few years at a time, so a daily
    series never expands further than it must."""
    lo = max(lo, first)
    limit = rule.until or add_days(first, 366 * horizon_years)
    while lo <= limit:
        hi = min(add_days(lo, 366 * 4), limit)
        dates = occurrence_dates(first, rule, lo, hi, excluded)
        if dates:
            return dates[0]
        lo = add_days(hi, 1)
    return None


def shift_rule(rule: RecurrenceRule, days: int) -> RecurrenceRule:
    """The same rule for a series whose start moves by `days` days: its
    weekdays, its week start and its end date all move with it."""
    weekdays = frozenset((d + days) % 7 for d in rule.weekdays)
    until = add_days(rule.until, days) if rule.until else None
    return RecurrenceRule(rule.freq, rule.interval, weekdays, until,
                          (rule.week_start + days) % 7)


# ------------------------------------------------------------ occurrences
@dataclass
class Occurrence:
    """One concrete appearance of an event.

    `event` is the row whose values it shows: the event itself, a series'
    master, or an occurrence's own override row. `series_id` and
    `occurrence_date` identify it within a series; both are None for an
    ordinary event.
    """
    event: object
    date: str                     # start date of this occurrence
    end_date: str                 # last date as stored (see end_date_of)
    series_id: Optional[int] = None
    occurrence_date: Optional[str] = None
    rule: Optional[RecurrenceRule] = None

    @property
    def key(self):
        """Stable identity. An ordinary event is its row id (an int, as it
        always was); an occurrence of a repeating event is (series id,
        original series date), whether or not it has been edited on its own."""
        if self.series_id is not None:
            return (self.series_id, self.occurrence_date or "")
        return self.event.id

    @property
    def is_recurring(self) -> bool:
        return self.series_id is not None

    @property
    def all_day(self) -> bool:
        return bool(getattr(self.event, "all_day", False))

    @property
    def start_minute(self) -> int:
        return self.event.start_minute

    @property
    def end_minute(self) -> int:
        return self.event.end_minute

    def days(self) -> list:
        """Every date this occurrence is visible on, in order."""
        first, last = parse_date(self.date), parse_date(self.end_date)
        if not self.all_day and last > first and self.event.end_minute == 0:
            last -= timedelta(days=1)      # ends exactly at midnight
        out = []
        day = first
        while day <= last:
            out.append(iso(day))
            day += timedelta(days=1)
        return out

    def touches(self, lo: str, hi: str) -> bool:
        return any(lo <= d <= hi for d in self.days())


@dataclass
class DayPiece:
    """The part of one occurrence that is drawn on one day.

    Carries the attributes the one-day layout and painting code already read
    (`id`, `start_minute`, `end_minute`, `all_day`, `title`, …), so
    `day_calendar_model.layout_events` and `event_render.paint_event_block`
    work on pieces unchanged. `id` is the occurrence key: two pieces of one
    overnight event share it, which is what makes them one event on screen.
    """
    occurrence: Occurrence
    day: str
    start_minute: int
    end_minute: int
    continues_before: bool = False
    continues_after: bool = False

    @property
    def id(self):
        return self.occurrence.key

    @property
    def event(self):
        return self.occurrence.event

    def __getattr__(self, name):
        # title, notes, color, opacity, done, all_day … come from the row.
        if name in ("occurrence",):
            raise AttributeError(name)
        return getattr(self.occurrence.event, name)


def day_pieces(occurrence: Occurrence, day: str) -> Optional[DayPiece]:
    """The piece of `occurrence` drawn on `day`, or None if it isn't there."""
    days = occurrence.days()
    if day not in days:
        return None
    if occurrence.all_day:
        return DayPiece(occurrence, day, 0, MINUTES_PER_DAY,
                        continues_before=day != days[0], continues_after=day != days[-1])
    start = occurrence.event.start_minute if day == occurrence.date else 0
    if day == occurrence.end_date:
        end = occurrence.event.end_minute
    else:
        end = MINUTES_PER_DAY
    return DayPiece(occurrence, day, start, end,
                    continues_before=day != occurrence.date,
                    continues_after=day != days[-1])


def occurrences_for_series(master, rule: RecurrenceRule, lo: str, hi: str,
                           excluded: Iterable[str]) -> list:
    """The generated (not overridden) occurrences of one series touching
    [lo, hi]. An occurrence that starts before `lo` but runs into it counts."""
    span = span_days(master)
    starts = occurrence_dates(master.date, rule, add_days(lo, -span), hi, excluded)
    out = []
    for start in starts:
        occ = Occurrence(event=master, date=start, end_date=add_days(start, span),
                         series_id=master.id, occurrence_date=start, rule=rule)
        if occ.touches(lo, hi):
            out.append(occ)
    return out


def sort_key(occurrence: Occurrence):
    return (occurrence.date, 0 if occurrence.all_day else 1,
            occurrence.event.start_minute, absolute_end(occurrence.event),
            sortable_key(occurrence.key))


def sortable_key(key):
    """Occurrence keys mix ints (ordinary events) and (id, date) tuples
    (occurrences of a series); this orders them without comparing the two."""
    if isinstance(key, tuple):
        return (1, key[0], key[1])
    return (0, key, "")
