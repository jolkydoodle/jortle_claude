"""Recognizing written dates in journal text and turning them into internal links.

A date written in an entry ("I should revisit this on September 15, 2026")
becomes a clickable link that moves the canonical SelectedDate. The link
target is normalized:

    journal://date/2026-09-15

while the VISIBLE text is left exactly as the user typed it (Part 41) —
linking should never silently rewrite someone's prose into a house date
format.

Deliberately conservative about what counts as a date. Every pattern here
requires an unambiguous, complete date with a four-digit year, because the
cost of a false positive is high and asymmetric: a wrongly-linked "3/4"
turns ordinary text (a fraction, a score, a ratio) into a blue clickable
thing that jumps the user's journal to some unrelated day. Bare
day/month pairs are therefore not recognized at all, exactly as Part 41
asks. A date that fails to parse into a real calendar day (February 30,
month 13, day 0) is likewise left as plain text rather than linked to
something invalid.

This module is pure text processing — no Qt, no database — so the patterns
can be tested directly.
"""
from __future__ import annotations

import re
from datetime import date as date_cls

LINK_SCHEME = "journal://date/"

MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_ALTERNATION = "|".join(sorted(MONTHS, key=len, reverse=True))

# ISO: 2026-09-15
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# US numeric: 09/15/2026 or 9/15/2026 (four-digit year required — see docstring)
_US_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

# Long form: September 15, 2026 / Sep 15 2026 / September 15th, 2026
_LONG_RE = re.compile(
    rf"\b({_MONTH_ALTERNATION})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)


def _safe_iso(year: int, month: int, day: int) -> str | None:
    """Real calendar days only — `date()` rejects February 30 and friends,
    which is exactly the validation wanted here."""
    try:
        return date_cls(year, month, day).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, iso_date) for every recognizable date in `text`,
    sorted by position and with overlaps removed.

    Overlap removal matters because the ISO and US patterns can both fire
    inside a longer run of digits in pathological input; keeping the first
    match and skipping anything that starts before the previous one ended
    means a given stretch of text is only ever linked once.
    """
    found: list[tuple[int, int, str]] = []

    for match in _ISO_RE.finditer(text):
        iso = _safe_iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if iso:
            found.append((match.start(), match.end(), iso))

    for match in _US_RE.finditer(text):
        iso = _safe_iso(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        if iso:
            found.append((match.start(), match.end(), iso))

    for match in _LONG_RE.finditer(text):
        month = MONTHS.get(match.group(1).lower())
        if month is None:
            continue
        iso = _safe_iso(int(match.group(3)), month, int(match.group(2)))
        if iso:
            found.append((match.start(), match.end(), iso))

    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    deduped: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, iso in found:
        if start < last_end:
            continue
        deduped.append((start, end, iso))
        last_end = end
    return deduped


def link_href(iso_date: str) -> str:
    return f"{LINK_SCHEME}{iso_date}"


def parse_link(href: str) -> str | None:
    """The inverse: 'journal://date/2026-09-15' -> '2026-09-15', or None for
    an ordinary external URL."""
    if not href or not href.startswith(LINK_SCHEME):
        return None
    candidate = href[len(LINK_SCHEME):].strip()
    parts = candidate.split("-")
    if len(parts) != 3:
        return None
    try:
        return _safe_iso(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def to_relative_html_href(href: str, from_dir_depth: int = 1) -> str:
    """Converts an internal link into a relative path for the static HTML
    archive (Part 68), so exported pages still navigate between days with no
    application present. `from_dir_depth` is how many directories deep the
    page being written sits below the archive root — entries/ and
    readers_notes/ are both 1."""
    iso = parse_link(href)
    if iso is None:
        return href
    prefix = "../" * from_dir_depth
    return f"{prefix}entries/{iso}.html"
