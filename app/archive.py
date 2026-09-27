"""The static HTML archive: the copy of this journal that outlives the app.

The runtime database and this archive answer two different questions, and
that's why both exist (Part 72):

    journal.db                  -> restore the application exactly
    journal_archive/index.html  -> read the journal with no application at all

Everything written here is plain, static, local files. Opening index.html in
any browser works with no server, no Python, no runtime, and no JavaScript
needed to read ordinary content — which is the entire point: in ten years the
useful guarantee isn't "the app still installs", it's "these are HTML files".

Structure (Part 66):

    journal_archive/
      index.html              year -> month -> day browse tree
      styles.css              one shared stylesheet
      entries/2026-09-15.html one page per day with an entry
      readers_notes/…         one page per day with notes
      projects.html           the project index
      projects/…              one page per project, plus each saved version
      calendar/calendar.ics   open calendar format
      calendar/calendar.csv   spreadsheet-friendly
      settings.json           non-sensitive preferences
      backup_manifest.json    counts, versions, timestamp

The runtime database is deliberately NOT in the archive (Master Spec §46.7):
restoring is what backups are for, and they are checked and, when the user
has set it up, encrypted. An archive is readable pages only.

Journal HTML is Qt's own rich-text HTML — the same canonical representation
the database stores — with its document wrapper swapped for the archive's
page template. That's what makes the archive faithful rather than
approximate: alignment, line spacing, paragraph spacing, indentation, tabs,
blank paragraphs, colors, highlights, super/subscript and lists are all
already expressed as ordinary inline CSS in that markup, so a browser renders
what the editor rendered without any conversion step that could drop
properties.

Internal journal://date/ links are rewritten to relative paths so the archive
keeps its cross-date navigation (Part 68).
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape
from pathlib import Path

from . import date_links
from .database import Database
from .date_state import human, human_long, to_qdate
from .day_calendar_model import format_minute
from .recurrence import MINUTES_PER_DAY, add_days, next_occurrence, span_days
from .saving import document_has_content

ARCHIVE_FORMAT_VERSION = 1
APP_VERSION = "round-22"

# Settings that are safe and useful to carry into a portable archive. An
# allow-list rather than a deny-list: a future setting is excluded until
# someone deliberately adds it, which is the right default when the output
# is an unencrypted file the user may hand to someone else (Part 71).
EXPORTED_SETTINGS = [
    "font_family", "font_size", "ui_font_size", "color_scheme",
    "writing_position",
]

STYLES = """\
:root {
  --bg: #fbfaf7;
  --fg: #23201c;
  --muted: #6b645c;
  --rule: #e2ddd4;
  --accent: #3f6ea5;
  --notes-bg: #f2f5fa;
  --notes-border: #c8d6e8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1917; --fg: #e8e4dd; --muted: #9c948a; --rule: #34312c;
    --accent: #8fb6e0; --notes-bg: #20262e; --notes-border: #35465a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem 5rem;
  background: var(--bg); color: var(--fg);
  font-family: Georgia, "Times New Roman", serif;
  font-size: 17px; line-height: 1.6;
}
.wrap { max-width: 44rem; margin: 0 auto; }
header.page { border-bottom: 1px solid var(--rule); margin-bottom: 2rem; padding-bottom: 1rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
.muted { color: var(--muted); font-size: .85rem; }
a { color: var(--accent); }
nav.crumbs { font-size: .85rem; margin-bottom: 1.5rem; }
nav.crumbs a { text-decoration: none; }
nav.crumbs a:hover { text-decoration: underline; }
article.entry { margin-bottom: 2.5rem; }
/* Qt writes per-paragraph inline styles; these only supply defaults it
   doesn't set, so nothing here can override the document's own formatting. */
article.entry p { margin: 0; }
section.readers-notes {
  background: var(--notes-bg);
  border: 1px solid var(--notes-border);
  border-left: 4px solid var(--accent);
  border-radius: 4px; padding: 1rem 1.25rem; margin-bottom: 2.5rem;
}
section.readers-notes h2, section.calendar h2 {
  font-size: .8rem; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); margin: 0 0 .75rem;
}
section.readers-notes p { margin: 0; }
section.calendar table { border-collapse: collapse; width: 100%; font-size: .95rem; }
section.calendar th, section.calendar td {
  text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--rule);
}
section.calendar th { font-size: .75rem; text-transform: uppercase; color: var(--muted); }
ul.years { list-style: none; padding: 0; }
ul.years > li { margin-bottom: 1.75rem; }
ul.months { list-style: none; padding-left: 0; }
ul.months > li { margin-bottom: .9rem; }
.month-name { font-weight: bold; display: block; margin-bottom: .3rem; }
ul.days { list-style: none; padding-left: 1rem; margin: 0; }
ul.days li { padding: .12rem 0; }
.badge {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); border: 1px solid var(--rule);
  border-radius: 999px; padding: 0 .45rem; margin-left: .4rem;
}
footer.page { border-top: 1px solid var(--rule); margin-top: 3rem; padding-top: 1rem; }
"""

_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)
_HREF_RE = re.compile(r'href="([^"]*)"')
# Qt writes paragraph alignment as the HTML 4 presentational attribute
# (<p align="center">) rather than as CSS. Browsers all still honor it, but
# it was removed from the HTML5 spec, and this archive's whole purpose is to
# still render correctly in a browser years from now — so the export
# additionally writes a real text-align declaration into the paragraph's own
# inline style. The attribute is left in place as well: keeping both costs a
# few bytes and means the file degrades gracefully either way.
_ALIGN_ATTR_RE = re.compile(r'<p align="(left|right|center|justify)"([^>]*?)style="([^"]*)"',
                            re.IGNORECASE)
_ROOT_TABLE_MARGIN_RE = re.compile(
    r'<table[^>]*-qt-table-type:\s*root[^>]*?(margin-bottom:\s*\d+px)[^>]*>', re.IGNORECASE
)


@dataclass
class ArchiveSummary:
    root: Path
    entry_count: int
    notes_count: int
    event_count: int
    project_count: int = 0


def default_archive_dirname() -> str:
    return f"journal_archive_{datetime.now().strftime('%Y-%m-%d')}"


def _inner_html(document_html: str, fmt: str, link_depth: int) -> str:
    """Extracts the renderable body from a stored document and rewrites its
    internal links for the archive.

    A "markdown" row (anything saved before round 21 and not edited since)
    is escaped and wrapped in <pre> rather than being run through a Markdown
    renderer: this app has no Markdown dependency, and inventing one at
    export time risks rendering old entries differently from how the app
    itself shows them. <pre> guarantees the original text is readable and
    intact, which is what the archive is for.
    """
    if fmt != "html":
        return f"<pre>{escape(document_html)}</pre>"

    match = _BODY_RE.search(document_html)
    body = match.group(1) if match else document_html

    def rewrite(match_obj):
        href = match_obj.group(1)
        return f'href="{date_links.to_relative_html_href(href, link_depth)}"'

    body = _HREF_RE.sub(rewrite, body)

    # Defensive: entries saved between the round-21 HTML switch and the
    # round-22 fix have the editor's scroll-past-end padding baked into
    # their root frame as a large margin-bottom. Those rows are only
    # rewritten when they're next edited, so strip it here too — otherwise
    # an archive of older entries shows a big blank gap under each one.
    body = _ROOT_TABLE_MARGIN_RE.sub(
        lambda m: m.group(0).replace(m.group(1), "margin-bottom:0px"), body
    )

    def add_text_align(match_obj):
        alignment, middle, style = match_obj.group(1), match_obj.group(2), match_obj.group(3)
        return f'<p align="{alignment}"{middle}style="text-align:{alignment}; {style}"'

    return _ALIGN_ATTR_RE.sub(add_text_align, body)


def _page(title: str, body: str, link_depth: int, subtitle: str = "") -> str:
    css_path = "../" * link_depth + "styles.css"
    home_path = "../" * link_depth + "index.html"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)}</title>
<link rel="stylesheet" href="{css_path}" />
</head>
<body>
<div class="wrap">
<nav class="crumbs"><a href="{home_path}">← Journal Archive</a></nav>
<header class="page">
<h1>{escape(title)}</h1>
{f'<div class="muted">{escape(subtitle)}</div>' if subtitle else ''}
</header>
{body}
<footer class="page"><div class="muted">Exported from jortle_claude — this page needs no application to read.</div></footer>
</div>
</body>
</html>
"""


def _short(date_str: str) -> str:
    qdate = to_qdate(date_str)
    return qdate.toString("MMM d") if qdate.isValid() else date_str


def _when(piece) -> str:
    """The Time column for one day's piece of an occurrence.

    An event on a single day reads exactly as it always has ("Untimed",
    "9:00 AM – 10:00 AM"); one that crosses midnight or spans days names
    both ends, so the same event on each of its days says the same thing.
    """
    occ = piece.occurrence
    days = occ.days()
    if occ.all_day:
        text = "Untimed" if len(days) == 1 else f"Untimed ({_short(days[0])} – {_short(days[-1])})"
    elif len(days) == 1:
        text = f"{format_minute(occ.start_minute)} – {format_minute(occ.end_minute)}"
    else:
        text = (f"{format_minute(occ.start_minute)} {_short(occ.date)} – "
                f"{format_minute(occ.end_minute % MINUTES_PER_DAY)} {_short(occ.end_date)}")
    if occ.is_recurring and occ.rule is not None:
        text += f" · repeats {occ.rule.describe()}"
    return text


def _events_table(pieces: list) -> str:
    """One day's calendar: a piece of each occurrence visible that day
    (recurrence.DayPiece), so an overnight event appears on both days and a
    repeating one on every day of the page set."""
    if not pieces:
        return ""
    rows = []
    for piece in pieces:
        when = _when(piece)
        note = escape(piece.notes).replace("\n", "<br />") if piece.notes else ""
        rows.append(
            f"<tr><td>{escape(when)}</td><td>{escape(piece.title or '(untitled)')}"
            f"{'  ✓' if piece.done else ''}</td><td>{note}</td></tr>"
        )
    return (
        '<section class="calendar"><h2>Calendar</h2><table>'
        "<tr><th>Time</th><th>Event</th><th>Notes</th></tr>"
        + "".join(rows) + "</table></section>"
    )


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", r"\;")
                .replace(",", r"\,").replace("\n", r"\n"))


def _ics_time(date_str: str, minute: int) -> str:
    """A floating local date-time; minute 1440 is 00:00 of the next day."""
    days, minute = divmod(int(minute), MINUTES_PER_DAY)
    compact = add_days(date_str, days).replace("-", "")
    hours, minutes = divmod(minute, 60)
    return f"{compact}T{hours:02d}{minutes:02d}00"


def _write_calendar(root: Path, events: list, db: Database):
    """calendar.ics and calendar.csv: every stored event row.

    A repeating event is one VEVENT with its RRULE and an EXDATE for each
    cancelled date; an occurrence edited on its own is a VEVENT with the
    series' UID and a RECURRENCE-ID (RFC 5545 §3.8.4.4), which is how other
    calendar programs expect to receive it. An event that ends on a later
    date gets a DTEND on that date. A single-day event is written exactly as
    before Group 3.
    """
    calendar_dir = root / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//jortle_claude//Archive Export//EN", "CALSCALE:GREGORIAN",
    ]
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    rules = {}
    first_shown = {}
    for event in events:
        rule = db.get_recurrence(event.id)
        if rule is not None:
            rules[event.id] = rule
            first_shown[event.id] = db.first_real_occurrence(event.id)
    # A series that no longer shows anything (every date cancelled — only
    # possible in databases from before such series were removed) is left
    # out altogether rather than written as an event with no instances.
    events = [e for e in events if not (e.id in rules and first_shown[e.id] is None)]
    masters = {e.id: e for e in events}
    overridden = {(e.series_id, e.occurrence_date) for e in events if e.series_id is not None}
    for event in events:
        if event.id in rules:
            # RFC 5545 counts DTSTART itself as the first occurrence, but a
            # series' own date need not be one (a Mon/Wed series that starts
            # on a Tuesday). Write the first date the rule really produces.
            first = db.first_occurrence_date(event.id) or event.date
            if first != event.date:
                span = (to_qdate(event.date).daysTo(to_qdate(event.end_date))
                        if event.end_date else 0)
                event = replace(event, date=first,
                                end_date=add_days(first, span) if span else None)
        compact_date = event.date.replace("-", "")
        lines.append("BEGIN:VEVENT")
        uid_id = event.series_id if event.series_id in masters else event.id
        lines.append(f"UID:journal-event-{uid_id}@daily-journal")
        lines.append(f"DTSTAMP:{stamp}")
        if event.series_id in masters and event.occurrence_date:
            master = masters[event.series_id]
            if master.all_day:
                lines.append(f"RECURRENCE-ID;VALUE=DATE:{event.occurrence_date.replace('-', '')}")
            else:
                lines.append(f"RECURRENCE-ID:{_ics_time(event.occurrence_date, master.start_minute)}")
        if event.all_day:
            # An untimed event is a whole-day VEVENT: DTSTART as a DATE
            # value with DTEND on the day after its last day, which is how
            # iCalendar represents "these days" and what every calendar app
            # expects.
            qdate = to_qdate(event.end_date or event.date)
            next_day = qdate.addDays(1).toString("yyyyMMdd")
            lines.append(f"DTSTART;VALUE=DATE:{compact_date}")
            lines.append(f"DTEND;VALUE=DATE:{next_day}")
        elif event.end_date and event.end_date != event.date:
            lines.append(f"DTSTART:{_ics_time(event.date, event.start_minute)}")
            lines.append(f"DTEND:{_ics_time(event.end_date, event.end_minute)}")
        else:
            start_h, start_m = divmod(event.start_minute, 60)
            end_h, end_m = divmod(min(event.end_minute, 24 * 60 - 1), 60)
            lines.append(f"DTSTART:{compact_date}T{start_h:02d}{start_m:02d}00")
            lines.append(f"DTEND:{compact_date}T{end_h:02d}{end_m:02d}00")
        rule = rules.get(event.id)
        if rule is not None:
            lines.append(f"RRULE:{rule.to_rrule(timed=not event.all_day)}")
            for cancelled in sorted(db._exception_dates(event.id)):
                if (event.id, cancelled) in overridden:
                    continue
                if event.all_day:
                    lines.append(f"EXDATE;VALUE=DATE:{cancelled.replace('-', '')}")
                else:
                    lines.append(f"EXDATE:{_ics_time(cancelled, event.start_minute)}")
        lines.append(f"SUMMARY:{_ics_escape(event.title or '(untitled)')}")
        if event.notes:
            lines.append(f"DESCRIPTION:{_ics_escape(event.notes)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    # newline="": the lines already end in CRLF (RFC 5545), and text mode on
    # Windows would turn each "\n" into "\r\n" again, giving "\r\r\n".
    (calendar_dir / "calendar.ics").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8",
                                               newline="")

    import csv
    with (calendar_dir / "calendar.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        # The first eight columns are unchanged from before Group 3; the
        # new ones are appended so a spreadsheet built on the old layout
        # still lines up.
        writer.writerow(["id", "date", "start", "end", "all_day", "done", "title", "notes",
                         "end_date", "repeats", "series_id", "occurrence_date"])
        for event in events:
            rule = rules.get(event.id)
            if rule is not None and first_shown.get(event.id):
                # A series' date in the spreadsheet is the first day it is
                # really shown, not its stored start (which need not be an
                # occurrence: a Mon/Wed series starting on a Tuesday).
                span = span_days(event)
                first = first_shown[event.id]
                event = replace(event, date=first,
                                end_date=add_days(first, span) if span else None)
            writer.writerow([
                event.id, event.date,
                "" if event.all_day else format_minute(event.start_minute, use_24h=True),
                "" if event.all_day else format_minute(event.end_minute, use_24h=True),
                "yes" if event.all_day else "no",
                "yes" if event.done else "no",
                event.title, event.notes,
                event.end_date or "", rule.describe() if rule else "",
                event.series_id or "", event.occurrence_date or "",
            ])


def _write_index(root: Path, dates: dict, project_count: int = 0):
    """The browse tree: year -> month -> day, newest first."""
    by_year: dict = {}
    for date_str, flags in dates.items():
        year, month, _day = date_str.split("-")
        by_year.setdefault(year, {}).setdefault(month, []).append((date_str, flags))

    year_blocks = []
    for year in sorted(by_year, reverse=True):
        month_blocks = []
        for month in sorted(by_year[year], reverse=True):
            day_items = []
            for date_str, flags in sorted(by_year[year][month], reverse=True):
                badges = ""
                if flags.get("notes"):
                    badges += '<span class="badge">notes</span>'
                if flags.get("events"):
                    badges += '<span class="badge">calendar</span>'
                day_items.append(
                    f'<li><a href="entries/{date_str}.html">{escape(human(date_str))}</a>{badges}</li>'
                )
            month_name = to_qdate(f"{year}-{month}-01").toString("MMMM")
            month_blocks.append(
                f'<li><span class="month-name">{escape(month_name)}</span>'
                f'<ul class="days">{"".join(day_items)}</ul></li>'
            )
        year_blocks.append(
            f'<li><h2>{escape(year)}</h2><ul class="months">{"".join(month_blocks)}</ul></li>'
        )

    projects_link = (
        f'<p><a href="projects.html">Projects ({project_count}) →</a></p>'
        if project_count else ""
    )
    body = projects_link + (
        f'<p class="muted">{len(dates)} day(s) archived.</p>'
        f'<ul class="years">{"".join(year_blocks)}</ul>'
        if dates else '<p class="muted">This archive contains no entries yet.</p>'
    )
    (root / "index.html").write_text(
        _page("Journal Archive", body, link_depth=0,
              subtitle=f"Exported {datetime.now().strftime('%B %d, %Y at %H:%M')}"),
        encoding="utf-8",
    )


def _has_content(document_html: str, plain_text: str) -> bool:
    """Whether a stored document is worth putting in the archive.

    The application's one rule for a written vs a blank document
    (saving.document_has_content): visible text, or an image. A Reader's
    Note that is a single photo with no caption has no plain text at all,
    and is still content.
    """
    return document_has_content(document_html, plain_text)


def _write_projects(root: Path, db: Database) -> int:
    """One page per project, plus its Reader's Notes and version history.

    Projects were missing from this archive entirely. The readable archive is
    the copy that is supposed to still be readable when there is no jortle_claude to
    open it with, and it silently contained only half the writing in the app
    — the journal, but not the long-form work or its history. Nothing was
    lost (it was all in the database), but "readable without this
    application" was only true of one workspace.
    """
    projects = list(db.list_projects(archived=False)) + list(db.list_projects(archived=True))
    if not projects:
        return 0

    (root / "projects").mkdir(parents=True, exist_ok=True)
    listed = []
    for project in projects:
        sections = []
        # The project's folder path ("Research / GaN / EES"). The nested
        # archive navigation is the archive group's; the path keeps the
        # hierarchy visible meanwhile. Folders converted from the old flat
        # categories read exactly as the category did.
        folder_path = " / ".join(db.folder_path(project.folder_id))
        if folder_path:
            sections.append(f'<p class="muted">{escape(folder_path)}</p>')
        sections.append(
            '<article class="entry">'
            + _inner_html(project.content_md, project.content_format, link_depth=1)
            + "</article>"
        )

        note = db.get_notes("project", str(project.id))
        if note is not None and _has_content(note.content, note.content_text):
            sections.append(
                '<section class="readers-notes"><h2>Reader\'s Notes</h2>'
                + _inner_html(note.content, note.content_format, link_depth=1)
                + "</section>"
            )

        versions = db.get_project_versions(project.id)
        if versions:
            rows = []
            for version in versions:
                label = escape(version.label or version.kind or "checkpoint")
                rows.append(
                    f'<li><a href="{project.id}-v{version.id}.html">'
                    f'{escape(version.saved_at)}</a> — {label}</li>'
                )
                (root / "projects" / f"{project.id}-v{version.id}.html").write_text(
                    _page(f"{project.title} — version from {version.saved_at}",
                          '<article class="entry">'
                          + _inner_html(version.content_md, version.content_format, 1)
                          + "</article>"
                          f'<p><a href="{project.id}.html">← back to {escape(project.title)}</a></p>',
                          link_depth=1),
                    encoding="utf-8",
                )
            sections.append(
                '<section class="calendar"><h2>Version history</h2>'
                f'<ul>{"".join(rows)}</ul></section>'
            )

        title = project.title or "Untitled project"
        (root / "projects" / f"{project.id}.html").write_text(
            _page(title + (" (archived)" if project.archived else ""),
                  "".join(sections)
                  + '<p><a href="../projects.html">← all projects</a></p>',
                  link_depth=1),
            encoding="utf-8",
        )
        listed.append(
            f'<li><a href="projects/{project.id}.html">{escape(title)}</a>'
            + (' <span class="badge">archived</span>' if project.archived else "")
            + (f' <span class="badge">{escape(folder_path)}</span>'
               if folder_path else "")
            + "</li>"
        )

    (root / "projects.html").write_text(
        _page("Projects",
              f'<p class="muted">{len(projects)} project(s) archived.</p>'
              f'<ul class="years">{"".join(listed)}</ul>'
              '<p><a href="index.html">← journal index</a></p>',
              link_depth=0),
        encoding="utf-8",
    )
    return len(projects)


def export_archive(db: Database, root: Path, include_database: bool = False) -> ArchiveSummary:
    """Writes the complete static archive to `root`.

    Deliberately an explicit, whole-archive operation rather than anything
    incremental or autosave-driven (Part 74): it runs when the user asks for
    it, reads everything once, and writes a self-consistent snapshot.
    """
    root = Path(root)
    (root / "entries").mkdir(parents=True, exist_ok=True)
    (root / "readers_notes").mkdir(parents=True, exist_ok=True)
    (root / "styles.css").write_text(STYLES, encoding="utf-8")

    entries = [e for e in db.all_entries()
               if _has_content(e.body_md, e.body_text) or e.title.strip()]
    notes = [n for n in db.all_reader_notes()
             if _has_content(n.content, n.content_text)]
    events = db.all_events()

    notes_by_date = {n.date: n for n in notes}

    # Which days get a page: every date with writing or notes, and every
    # date an event row is stored on or runs into. A repeating event gets a
    # page for its first date, like any other row; its other occurrences
    # are listed on the pages that exist rather than each creating a page of
    # its own (a daily series would otherwise add a page for every day). The
    # whole series is in calendar.ics.
    event_row_dates: set = set()
    for event in events:
        if db.get_recurrence(event.id) is not None:
            # A series' stored date need not be an occurrence (a Mon/Wed
            # series starting on a Tuesday, a cancelled first date): its
            # page is the first date the RULE really shows, with the
            # series' own length. Separately edited occurrences (which may
            # be shorter, or elsewhere) are rows of their own and get their
            # pages from their own dates.
            first = next_occurrence(event.date, db.get_recurrence(event.id), event.date,
                                    db._exception_dates(event.id))
            if first is None:
                continue
            span = span_days(event)
            event = replace(event, date=first, end_date=add_days(first, span) if span else None)
        if event.all_day or not event.end_date:
            first, last = event.date, event.end_date or event.date
        else:
            first = event.date
            last = event.end_date if event.end_minute > 0 else add_days(event.end_date, -1)
        day = first
        while day <= last:
            event_row_dates.add(day)
            day = add_days(day, 1)
    page_dates = {e.date for e in entries} | set(notes_by_date) | event_row_dates

    events_by_date: dict = {}
    if page_dates:
        from .recurrence import day_pieces
        for occ in db.occurrences_between(min(page_dates), max(page_dates)):
            for day in occ.days():
                if day in page_dates:
                    events_by_date.setdefault(day, []).append(day_pieces(occ, day))
        # Untimed first, then by start time, then in the order the rows
        # were made — the order day pages listed events in before Group 3.
        for pieces in events_by_date.values():
            pieces.sort(key=lambda p: (0 if p.all_day else 1, p.start_minute, p.event.id,
                                       p.occurrence.date))

    all_dates = sorted(page_dates, reverse=True)
    entries_by_date = {e.date: e for e in entries}

    for date_str in all_dates:
        entry = entries_by_date.get(date_str)
        note = notes_by_date.get(date_str)
        day_events = events_by_date.get(date_str, [])

        sections = []
        if entry is not None:
            sections.append(
                '<article class="entry">'
                + _inner_html(entry.body_md, entry.body_format, link_depth=1)
                + "</article>"
            )
        else:
            sections.append('<p class="muted">No journal entry for this day.</p>')

        # Reader's Notes appear on the day page AND get their own file
        # (Part 39 accepts either; doing both costs nothing and means the
        # notes are reachable whichever way someone browses the archive).
        if note is not None:
            notes_html = _inner_html(note.content, note.content_format, link_depth=1)
            sections.append(
                '<section class="readers-notes"><h2>Reader\'s Notes</h2>'
                + notes_html + "</section>"
            )
            (root / "readers_notes" / f"{date_str}.html").write_text(
                _page(f"Reader's Notes — {human(date_str)}",
                      f'<section class="readers-notes">{_inner_html(note.content, note.content_format, 1)}</section>'
                      f'<p><a href="../entries/{date_str}.html">Journal entry for this day →</a></p>',
                      link_depth=1),
                encoding="utf-8",
            )

        sections.append(_events_table(day_events))

        (root / "entries" / f"{date_str}.html").write_text(
            _page(human_long(date_str), "".join(sections), link_depth=1),
            encoding="utf-8",
        )

    _write_calendar(root, events, db)
    project_count = _write_projects(root, db)

    index_dates = {
        date_str: {
            "notes": date_str in notes_by_date,
            "events": bool(events_by_date.get(date_str)),
        }
        for date_str in all_dates
    }
    _write_index(root, index_dates, project_count)

    settings = {key: db.get_setting(key) for key in EXPORTED_SETTINGS}
    settings = {k: v for k, v in settings.items() if v is not None}
    (root / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    manifest = {
        "backup_format_version": ARCHIVE_FORMAT_VERSION,
        "application_version": APP_VERSION,
        "database_schema_version": db.schema_version(),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "journal_entry_count": len(entries),
        "readers_notes_count": len(notes),
        "calendar_event_count": len(events),
        "archived_day_count": len(all_dates),
        "note": (
            "Every .html file here is readable on its own in any browser. "
            "These files are not encrypted. To restore jortle_claude itself, use "
            "a backup (File > Back Up Now), not this archive."
        ),
    }
    (root / "backup_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if include_database:
        try:
            # Checkpoint WAL first so the copied file is self-contained
            # rather than missing recent writes still sitting in the -wal.
            db.checkpoint()
            shutil.copy2(db.db_path, root / "journal.db")
        except OSError as exc:
            print(f"Warning: could not copy database into archive: {exc}")

    return ArchiveSummary(root=root, entry_count=len(entries),
                           notes_count=len(notes), event_count=len(events),
                           project_count=project_count)
