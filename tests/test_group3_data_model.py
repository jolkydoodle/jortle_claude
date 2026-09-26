"""Group 3, stage 3A — the data model: overnight and multi-day events,
repeating events and their three edit/delete scopes, month/year titles,
project folders, day metadata, migrations, and the archive.

Acceptance criteria 1-13 of the Group 3 plan (JORTLE_IMPLEMENTATION_HANDOFF.md)
are marked [1]..[13]. Everything is read back from the database (and, for the
migrations, from a database written in the pre-Group 3 shape and restored
from a backup); FP-9 points 2 and 3.
"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = isolation.isolate(prefix="jortle-g3-data-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import archive, backup, data_migration, security  # noqa: E402
from app.database import Database, FolderNotEmpty, InvalidMove  # noqa: E402
from app.recurrence import (  # noqa: E402
    RecurrenceRule, day_pieces, occurrence_dates, remap_dates
)

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def raises(fn, exc_type):
    try:
        fn()
    except exc_type as exc:
        return exc
    except Exception as exc:  # noqa: BLE001
        return f"WRONG {type(exc).__name__}: {exc}"
    return None


def pieces_on(db, day):
    """What a one-day view would draw on `day`: (key, start, end, title)."""
    out = []
    for occ in db.occurrences_between(day, day):
        piece = day_pieces(occ, day)
        if piece is not None:
            out.append((piece.id, piece.start_minute, piece.end_minute, piece.title))
    return out


def starts(db, lo, hi, title=None):
    return [o.date for o in db.occurrences_between(lo, hi)
            if title is None or o.event.title == title]


def reopen(db):
    """Close and open the database again: every check after this reads what
    was actually stored."""
    db.close()
    return Database()


db = Database()

# ---------------------------------------------------------------- [1]
print("\n--- [1] 9 PM to 12 AM the next day is one event, never reversed ---")
ev = db.create_event("2026-09-21", 21 * 60, 0, title="Late shift", end_date="2026-09-22")
db = reopen(db)
row = db.get_event(ev.id)
check("stored as one row running into the next date",
      (row.date, row.start_minute, row.end_date, row.end_minute)
      == ("2026-09-21", 1260, "2026-09-22", 0))
check("duration is 180 minutes", row.duration == 180, row.duration)
check("drawn on the 21st from 9 PM to midnight",
      [(s, e) for _k, s, e, t in pieces_on(db, "2026-09-21") if t == "Late shift"] == [(1260, 1440)])
check("nothing drawn on the 22nd (it ends exactly at midnight)",
      not [p for p in pieces_on(db, "2026-09-22") if p[3] == "Late shift"])
err = raises(lambda: db.create_event("2026-09-21", 21 * 60, 0, title="Backwards"), ValueError)
check("a same-day end before the start is refused, not stored reversed", isinstance(err, ValueError), err)
check("...and nothing was written",
      not [e for e in db.get_events("2026-09-21") if e.title == "Backwards"])
err = raises(lambda: db.update_event(ev.id, end_date="2026-09-21"), ValueError)
check("an update that would reverse it is refused", isinstance(err, ValueError), err)
check("...and the row is unchanged", db.get_event(ev.id).end_date == "2026-09-22")
legacy = db.create_event("2026-09-24", 21 * 60, 1440, title="Legacy to midnight")
check("the older 'same day, minute 1440' form still means 9 PM - midnight",
      [(s, e) for _k, s, e, t in pieces_on(db, "2026-09-24") if t == "Legacy to midnight"]
      == [(1260, 1440)] and db.get_event(legacy.id).duration == 180)

# ---------------------------------------------------------------- [2]
print("\n--- [2] Monday 9 PM to Wednesday 2 AM: three pieces, one event ---")
long_ev = db.create_event("2026-09-28", 21 * 60, 120, title="Long run", end_date="2026-09-30")
got = {day: [(k, s, e) for k, s, e, t in pieces_on(db, day) if t == "Long run"]
       for day in ("2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01")}
key = long_ev.id    # an ordinary event's key is its row id
check("Monday 21:00-24:00", got["2026-09-28"] == [(key, 1260, 1440)], got["2026-09-28"])
check("Tuesday 00:00-24:00", got["2026-09-29"] == [(key, 0, 1440)], got["2026-09-29"])
check("Wednesday 00:00-02:00", got["2026-09-30"] == [(key, 0, 120)], got["2026-09-30"])
check("not on Sunday or Thursday", not got["2026-09-27"] and not got["2026-10-01"])
week = [o for o in db.occurrences_between("2026-09-27", "2026-10-03") if o.event.title == "Long run"]
check("a week query returns it once, with one identity", len(week) == 1 and week[0].key == key)
check("the pieces say which sides continue",
      [(p.continues_before, p.continues_after) for p in
       (day_pieces(week[0], d) for d in ("2026-09-28", "2026-09-29", "2026-09-30"))]
      == [(False, True), (True, True), (True, False)])

# ---------------------------------------------------------------- [3]
print("\n--- [3] an untimed event from the 3rd to the 5th ---")
trip = db.create_event("2026-09-03", 0, 0, title="Trip", all_day=True, end_date="2026-09-05")
per_day = {d: [t for _k, _s, _e, t in pieces_on(db, d) if t == "Trip"]
           for d in ("2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06")}
check("on the 3rd, 4th and 5th", all(per_day[d] == ["Trip"] for d in ("2026-09-03", "2026-09-04", "2026-09-05")))
check("not on the 2nd or 6th", not per_day["2026-09-02"] and not per_day["2026-09-06"])
check("one row, one identity", len(db.get_events("2026-09-03")) >= 1
      and len({o.key for o in db.occurrences_between("2026-09-01", "2026-09-30")
               if o.event.title == "Trip"}) == 1)
check("the month's event dates include each of its days",
      {"2026-09-03", "2026-09-04", "2026-09-05"} <= db.event_dates("2026-09-01", "2026-09-30"))
err = raises(lambda: db.create_event("2026-09-05", 0, 0, all_day=True, end_date="2026-09-03"), ValueError)
check("an untimed range cannot end before it starts", isinstance(err, ValueError), err)

# ---------------------------------------------------------------- [4]
print("\n--- [4] weekly on Monday and Wednesday, 1 to 30 September ---")
mw = db.create_event("2026-09-01", 9 * 60, 10 * 60, title="Seminar",
                     recurrence=RecurrenceRule("weekly", 1, frozenset({1, 3}), "2026-09-30"))
db = reopen(db)
expected = ["2026-09-02", "2026-09-07", "2026-09-09", "2026-09-14", "2026-09-16",
            "2026-09-21", "2026-09-23", "2026-09-28", "2026-09-30"]
check("exactly the Mondays and Wednesdays in the range",
      starts(db, "2026-08-01", "2026-10-31", "Seminar") == expected,
      starts(db, "2026-08-01", "2026-10-31", "Seminar"))
check("nothing before the start date (Tue 1 Sep is not produced)",
      "2026-09-01" not in starts(db, "2026-09-01", "2026-09-01", "Seminar"))
check("nothing after the end date", not starts(db, "2026-10-01", "2026-12-31", "Seminar"))
check("a range inside the series gives just its part",
      starts(db, "2026-09-10", "2026-09-20", "Seminar") == ["2026-09-14", "2026-09-16"])
check("every 2 weeks counts Sunday-start weeks",
      occurrence_dates("2026-09-07", RecurrenceRule("weekly", 2, frozenset({1})),
                       "2026-09-01", "2026-10-31")
      == ["2026-09-07", "2026-09-21", "2026-10-05", "2026-10-19"])
check("every 3 days", occurrence_dates("2026-09-01", RecurrenceRule("daily", 3), "2026-09-05",
                                       "2026-09-14") == ["2026-09-07", "2026-09-10", "2026-09-13"])
check("the until date is inclusive",
      occurrence_dates("2026-09-01", RecurrenceRule("daily", 1, until="2026-09-03"),
                       "2026-09-01", "2026-09-30") == ["2026-09-01", "2026-09-02", "2026-09-03"])

# ---------------------------------------------------------------- [5]
print("\n--- [5] month ends and 29 February ---")
check("monthly on the 31st skips months without one",
      occurrence_dates("2026-01-31", RecurrenceRule("monthly"), "2026-01-01", "2026-12-31")
      == ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31", "2026-08-31",
          "2026-10-31", "2026-12-31"])
check("yearly on 29 February only in leap years",
      occurrence_dates("2024-02-29", RecurrenceRule("yearly"), "2024-01-01", "2033-12-31")
      == ["2024-02-29", "2028-02-29", "2032-02-29"])
check("every 2 months from the 30th skips February",
      occurrence_dates("2026-12-30", RecurrenceRule("monthly", 2), "2026-12-01", "2027-06-30")
      == ["2026-12-30", "2027-04-30", "2027-06-30"])

# ---------------------------------------------------------------- [6]
print("\n--- [6] this occurrence only ---")
mon = db.create_event("2026-09-07", 9 * 60, 10 * 60, title="Standup",
                      recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
sid = mon.id
db.edit_occurrence(sid, "2026-09-14", title="Standup (late)", start_minute=600, end_minute=660)
db.delete_occurrence(sid, "2026-09-21")
db.edit_occurrence(sid, "2026-09-28", date="2026-09-29")
db = reopen(db)
occ = {o.occurrence_date: o for o in db.occurrences_between("2026-09-01", "2026-10-06")
       if o.series_id == sid}
check("the edited occurrence shows its own title and time",
      occ["2026-09-14"].event.title == "Standup (late)" and occ["2026-09-14"].start_minute == 600)
check("the other occurrences are unchanged",
      occ["2026-09-07"].event.title == "Standup" and occ["2026-10-05"].start_minute == 540)
check("the deleted occurrence is gone, the next one is there",
      "2026-09-21" not in occ and "2026-10-05" in occ)
check("an occurrence moved to Tuesday is on Tuesday and keeps its identity",
      occ["2026-09-28"].date == "2026-09-29" and occ["2026-09-28"].key == (sid, "2026-09-28")
      and "2026-09-28" not in starts(db, "2026-09-28", "2026-09-28", "Standup"))
check("editing the same occurrence again updates its row, not a second one",
      (db.edit_occurrence(sid, "2026-09-14", title="Standup (later)") or True)
      and len([r for r in db._overrides(sid) if r.occurrence_date == "2026-09-14"]) == 1)
check("the series' own row still says Standup", db.get_event(sid).title == "Standup")

# ---------------------------------------------------------------- [7]
print("\n--- [7] this and following ---")
w = db.create_event("2026-10-05", 9 * 60, 10 * 60, title="Lab",
                    recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-12-28"))
wid = w.id
db.edit_occurrence(wid, "2026-11-09", title="Lab (guest)")      # after the split point
db.delete_occurrence(wid, "2026-11-16")                          # after the split point
db.edit_occurrence(wid, "2026-10-12", notes="bring samples")     # before the split point
new = db.edit_following(wid, "2026-10-26", title="Lab B", start_minute=13 * 60, end_minute=14 * 60)
db = reopen(db)
labs = {o.date: o for o in db.occurrences_between("2026-10-01", "2026-12-31")
        if o.series_id in (wid, new.id)}
check("earlier occurrences are unchanged",
      labs["2026-10-05"].event.title == "Lab" and labs["2026-10-19"].start_minute == 540
      and labs["2026-10-12"].event.notes == "bring samples")
check("this and later occurrences take the edit",
      all(labs[d].event.title == "Lab B" and labs[d].start_minute == 780
          for d in ("2026-10-26", "2026-11-02", "2026-11-23", "2026-12-28")))
check("a separately edited later occurrence is kept (and reached by the edit's changes)",
      "2026-11-09" in labs and labs["2026-11-09"].series_id == new.id
      and labs["2026-11-09"].start_minute == 780)
check("a cancelled later occurrence stays cancelled", "2026-11-16" not in labs)
check("the first part ends the day before, the second keeps the end date",
      db.get_recurrence(wid).until == "2026-10-25" and db.get_recurrence(new.id).until == "2026-12-28")
count_before = len(db.all_events())
db.edit_following(wid, "2026-10-05", notes="from the first")
check("from the first occurrence it is the whole series: no new series row",
      len(db.all_events()) == count_before and db.get_event(wid).notes == "from the first")
tue = db.edit_following(new.id, "2026-11-23", shift_days=1)
check("a following-edit that moves a day moves every later occurrence",
      starts(db, "2026-11-20", "2026-12-31", "Lab B") == ["2026-11-24", "2026-12-01", "2026-12-08",
                                                         "2026-12-15", "2026-12-22", "2026-12-29"],
      starts(db, "2026-11-20", "2026-12-31", "Lab B"))
check("...and its end date moved with it", db.get_recurrence(tue.id).until == "2026-12-29")

# ---------------------------------------------------------------- [8]
print("\n--- [8] entire series ---")
s8 = db.create_event("2026-09-01", 8 * 60, 9 * 60, title="Gym",
                     recurrence=RecurrenceRule("daily", 2))
db.edit_occurrence(s8.id, "2026-09-05", title="Gym (pool)")
db.delete_occurrence(s8.id, "2026-09-07")
db.edit_series(s8.id, color="#aa0000", start_minute=7 * 60, end_minute=8 * 60)
db = reopen(db)
gym = [o for o in db.occurrences_between("2026-09-01", "2026-09-30") if o.series_id == s8.id]
check("every occurrence takes the new time and colour, the edited one too",
      gym and all(o.start_minute == 420 and o.event.color == "#aa0000" for o in gym))
check("the edited occurrence keeps its own title", any(o.event.title == "Gym (pool)" for o in gym))
check("the cancelled one stays cancelled", "2026-09-07" not in [o.occurrence_date for o in gym])
db.edit_series(s8.id, shift_days=1)
moved = [o.occurrence_date for o in db.occurrences_between("2026-09-01", "2026-09-30")
         if o.series_id == s8.id]
check("moving the whole series a day moves every occurrence, the cancelled one stays cancelled",
      moved[:4] == ["2026-09-02", "2026-09-04", "2026-09-06", "2026-09-10"], moved[:5])
db.delete_series(s8.id)
leftovers = db._conn.execute(
    "SELECT (SELECT COUNT(*) FROM calendar_events WHERE id=? OR series_id=?) + "
    "(SELECT COUNT(*) FROM event_exceptions WHERE series_id=?) + "
    "(SELECT COUNT(*) FROM event_recurrence WHERE event_id=?) AS n",
    (s8.id, s8.id, s8.id, s8.id)).fetchone()["n"]
check("deleting the series leaves no rows behind", leftovers == 0, leftovers)
d8 = db.create_event("2026-09-01", 60, 120, title="Tmp", recurrence=RecurrenceRule("daily"))
db.edit_occurrence(d8.id, "2026-09-02", title="Tmp 2")
db.delete_event(d8.id)
check("deleting a series' row the ordinary way also takes everything with it",
      not db._overrides(d8.id) and db.get_recurrence(d8.id) is None
      and not starts(db, "2026-09-01", "2026-09-30", "Tmp"))
f8 = db.create_event("2026-09-01", 60, 120, title="Tail", recurrence=RecurrenceRule("daily"))
db.edit_occurrence(f8.id, "2026-09-10", title="Tail 10")
db.delete_following(f8.id, "2026-09-05")
check("delete this-and-following ends it the day before and drops later edits",
      starts(db, "2026-09-01", "2026-09-30", "Tail") == ["2026-09-01", "2026-09-02",
                                                         "2026-09-03", "2026-09-04"]
      and not db._overrides(f8.id))
check("an edited occurrence cannot be given its own rule",
      isinstance(raises(lambda: db.set_recurrence(db._overrides(mon.id)[0].id,
                                                  RecurrenceRule("daily")), ValueError), ValueError))
check("a moved series keeps an occurrence's date moved by the same days, when it exists",
      remap_dates("2026-01-31", RecurrenceRule("monthly"), "2026-02-01", RecurrenceRule("monthly"),
                  ["2026-05-31"], 1) == {"2026-05-31": "2026-06-01"}
      and remap_dates("2026-01-30", RecurrenceRule("monthly"), "2026-02-01",
                      RecurrenceRule("monthly"), ["2026-04-30"], 2) == {"2026-04-30": None})

# Found by the independent audit (2026-09-24): two moves that went wrong.
fs = db.create_event("2026-09-04", 600, 660, title="FriSat",
                     recurrence=RecurrenceRule("weekly", 2, frozenset({5, 6})))
db.edit_series(fs.id, shift_days=1)
check("every 2 weeks on Fri+Sat moved a day is every 2 weeks on Sat+Sun (week moves with it)",
      starts(db, "2026-09-01", "2026-10-04", "FriSat")
      == ["2026-09-05", "2026-09-06", "2026-09-19", "2026-09-20", "2026-10-03", "2026-10-04"],
      starts(db, "2026-09-01", "2026-10-04", "FriSat"))
m31 = db.create_event("2026-01-31", 600, 660, title="Month end",
                      recurrence=RecurrenceRule("monthly"))
db.delete_occurrence(m31.id, "2026-05-31")
db.edit_occurrence(m31.id, "2026-07-31", title="Month end (edited)")
db.edit_series(m31.id, shift_days=1)
moved = [(o.date, o.event.title) for o in db.occurrences_between("2026-01-01", "2026-09-30")
         if o.series_id == m31.id]
check("monthly on the 31st moved a day: the cancelled one stays cancelled, the edited one stays "
      "edited, no date twice",
      ("2026-06-01", "Month end") not in moved
      and ("2026-08-01", "Month end (edited)") in moved
      and len([d for d, _t in moved if d == "2026-08-01"]) == 1
      and ("2026-04-01", "Month end") in moved and ("2026-05-01", "Month end") in moved, moved)

# ---------------------------------------------------------------- [9]
print("\n--- [9] events written before Group 3 look and export as before ---")
now = datetime.now().isoformat(timespec="seconds")
for title, start, end, opacity, color, all_day in (
        ("Old default", 540, 600, -1, None, 0),
        ("Old opaque", 600, 660, 100, "#336699", 0),
        ("Old to midnight", 1320, 1440, -1, None, 0),
        ("Old untimed", 0, 0, -1, None, 1)):
    db._conn.execute(
        "INSERT INTO calendar_events (date, start_minute, end_minute, title, notes, color, all_day, "
        "done, opacity, created_at, updated_at) VALUES ('2026-08-10', ?, ?, ?, '', ?, ?, 0, ?, ?, ?)",
        (start, end, title, color, all_day, opacity, now, now))
db._conn.commit()
db = reopen(db)
old = {t: (s, e) for _k, s, e, t in pieces_on(db, "2026-08-10")}
check("each draws with its own start and end",
      old == {"Old default": (540, 600), "Old opaque": (600, 660),
              "Old to midnight": (1320, 1440), "Old untimed": (0, 1440)}, old)
rows = {e.title: e for e in db.get_events("2026-08-10")}
check("opacity and colour are exactly as stored (-1 still follows the default)",
      rows["Old default"].opacity == -1 and rows["Old opaque"].opacity == 100
      and rows["Old opaque"].color == "#336699" and rows["Old default"].end_date is None)
check("nothing appears on the next day", not pieces_on(db, "2026-08-11"))

out = pathlib.Path(tempfile.mkdtemp(prefix="g3-archive-"))
archive.export_archive(db, out)
# Bytes, not read_text(): universal-newline reading would turn the CRLF line
# ends RFC 5545 requires into "\n" and hide whether they are there.
ics = (out / "calendar" / "calendar.ics").read_bytes().decode("utf-8")
vevents = ics.split("BEGIN:VEVENT")[1:]


def vevent(summary):
    for block in vevents:
        if f"SUMMARY:{summary}\r\n" in block:
            return block
    return ""


check("a single-day event exports exactly as before",
      "DTSTART:20260810T090000\r\nDTEND:20260810T100000\r\n" in vevent("Old default"))
check("the old 'until midnight' export is unchanged (23:59, as before)",
      "DTSTART:20260810T220000\r\nDTEND:20260810T235900\r\n" in vevent("Old to midnight"))
check("an old untimed event exports as one DATE day",
      "DTSTART;VALUE=DATE:20260810\r\nDTEND;VALUE=DATE:20260811\r\n" in vevent("Old untimed"))
page = (out / "entries" / "2026-08-10.html").read_text(encoding="utf-8")
check("the day page reads as before", "9:00 AM – 10:00 AM" in page and "Untimed" in page)
with (out / "calendar" / "calendar.csv").open(encoding="utf-8") as handle:
    header = handle.readline().strip().split(",")
check("the CSV keeps its first eight columns and appends the new ones",
      header[:8] == ["id", "date", "start", "end", "all_day", "done", "title", "notes"]
      and header[8:] == ["end_date", "repeats", "series_id", "occurrence_date"])

print("\n--- the archive shows new events correctly ---")
check("an overnight event exports with its end on the next date",
      "DTSTART:20260928T210000\r\nDTEND:20260930T020000\r\n" in vevent("Long run"))
check("a multi-day untimed event ends the day after its last day",
      "DTSTART;VALUE=DATE:20260903\r\nDTEND;VALUE=DATE:20260906\r\n" in vevent("Trip"))
tue_page = (out / "entries" / "2026-09-29.html").read_text(encoding="utf-8")
check("the overnight event is listed on each day it covers",
      "Long run" in tue_page and "Long run" in (out / "entries" / "2026-09-30.html").read_text("utf-8"))
check("...naming both ends", "9:00 PM Sep 28 – 2:00 AM Sep 30" in tue_page)
check("its DTSTART is its first real occurrence (Wed 2 Sep), not the Tuesday it was dated",
      "DTSTART:20260902T090000" in vevent("Seminar"))
check("a repeating event is one VEVENT with its rule",
      "RRULE:FREQ=WEEKLY;INTERVAL=1;WKST=SU;BYDAY=MO,WE;UNTIL=20260930T235959" in vevent("Seminar"))
standup = vevent("Standup")
check("a cancelled occurrence is an EXDATE; replaced ones are not",
      "EXDATE:20260921T090000" in standup and "EXDATE:20260914T090000" not in standup
      and "EXDATE:20260928T090000" not in standup)
late = vevent("Standup (later)")
check("an edited occurrence carries the series' UID and a RECURRENCE-ID",
      f"UID:journal-event-{sid}@daily-journal" in late and "RECURRENCE-ID:20260914T090000" in late)

# ---------------------------------------------------------------- [13]
print("\n--- [13] the calendar file reads back to the same occurrences ---")
try:
    from dateutil.rrule import rrulestr
except ImportError:
    rrulestr = None
    print("  NOT RUN  python-dateutil is not installed")
if rrulestr is not None:
    def parse_ics(text):
        events = []
        for block in text.split("BEGIN:VEVENT")[1:]:
            props = {}
            exdates = []
            for line in block.split("\r\n"):
                if ":" not in line:
                    continue
                name, value = line.split(":", 1)
                if name.startswith("EXDATE"):
                    exdates.append(value)
                else:
                    props[name.split(";")[0]] = value
            props["EXDATES"] = exdates
            events.append(props)
        return events

    def when(value):
        return datetime.strptime(value, "%Y%m%dT%H%M%S") if "T" in value \
            else datetime.strptime(value, "%Y%m%d")

    parsed = parse_ics(ics)
    lo, hi = datetime(2026, 9, 1), datetime(2026, 10, 6, 23, 59)
    by_uid = {}
    for p in parsed:
        by_uid.setdefault(p["UID"], []).append(p)
    got = []
    for uid, group in by_uid.items():
        master = next((p for p in group if "RECURRENCE-ID" not in p), None)
        replaced = {p["RECURRENCE-ID"] for p in group if "RECURRENCE-ID" in p}
        for p in group:
            if "RECURRENCE-ID" in p and lo <= when(p["DTSTART"]) <= hi:
                got.append((p["SUMMARY"], when(p["DTSTART"])))
        if master is None:
            continue
        if "RRULE" in master:
            rule = rrulestr(master["RRULE"], dtstart=when(master["DTSTART"]))
            skip = set(master["EXDATES"]) | replaced
            for moment in rule.between(lo, hi, inc=True):
                stamp = moment.strftime("%Y%m%dT%H%M%S") if "T" in master["DTSTART"] \
                    else moment.strftime("%Y%m%d")
                if stamp not in skip:
                    got.append((master["SUMMARY"], moment))
        elif lo <= when(master["DTSTART"]) <= hi:
            got.append((master["SUMMARY"], when(master["DTSTART"])))
    ours = [(o.event.title, when(o.date.replace("-", "") + (
        "" if o.all_day else f"T{o.start_minute // 60:02d}{o.start_minute % 60:02d}00")))
        for o in db.occurrences_between("2026-09-01", "2026-10-06")
        if o.date >= "2026-09-01"]
    focus = {"Standup", "Standup (late)", "Standup (later)", "Seminar", "Long run", "Late shift"}
    check("same occurrences, same start times (overnight, skipped date, edited occurrence)",
          sorted(x for x in got if x[0] in focus) == sorted(x for x in ours if x[0] in focus),
          f"ics-only={sorted(set(got) - set(ours))[:4]} db-only={sorted(set(ours) - set(got))[:4]}")

# ------------------------------------------------ titles, metadata, folders
print("\n--- month and year titles (one canonical row) ---")
check("no title is ''", db.get_period_title("month", "2026-09") == "")
db.set_period_title("month", "2026-09", "  The   move\nto Santa Barbara  ")
db.set_period_title("year", "2026", "x" * 120)
db = reopen(db)
check("stored as one clean line", db.get_period_title("month", "2026-09") == "The move to Santa Barbara")
check("limited to 80 characters", len(db.get_period_title("year", "2026")) == 80)
check("month titles of a year in one call", db.period_titles("month", "2026-") == {"2026-09": "The move to Santa Barbara"})
db.set_period_title("month", "2026-09", "   ")
check("clearing a title removes its row",
      db._conn.execute("SELECT COUNT(*) AS n FROM period_titles WHERE kind='month'").fetchone()["n"] == 0)

print("\n--- day metadata without document bodies ---")
db.upsert_entry("2026-09-10", body_md="<p>Written</p>", body_format="html", body_text="Written",
                title="A title")
marker = db._conn.execute("SELECT id, color FROM day_markers LIMIT 1").fetchone()
db.upsert_entry("2026-09-11", tag=str(marker["id"]), tag_color=marker["color"])
meta = db.day_metadata("2026-09-01", "2026-09-30")
check("an entry: has_entry, title and length",
      meta["2026-09-10"].has_entry and meta["2026-09-10"].title == "A title"
      and meta["2026-09-10"].length == 7)
check("a marker-only day has the marker colour and no entry",
      meta["2026-09-11"].tag_color == marker["color"] and not meta["2026-09-11"].has_entry)
check("an events-only day is 'other content', not an entry",
      meta["2026-09-04"].other_content and not meta["2026-09-04"].has_entry)
check("a day with nothing is absent", "2026-09-12" not in meta or not (
    meta["2026-09-12"].has_entry or meta["2026-09-12"].tag_color))

print("\n--- project folders ---")
research = db.create_folder("Research")
gan = db.create_folder("GaN", research.id)
ees = db.create_folder("EES", gan.id)
p = db.create_project("Trap study", folder_id=ees.id)
db.save_notes("project", str(p.id), "<p>note</p>", "html", "note")
check("a nested path", db.folder_path(db.get_project(p.id).folder_id) == ["Research", "GaN", "EES"])
db.rename_folder(gan.id, "Gallium nitride")
writing = db.create_folder("Writing")
db.move_folder(ees.id, writing.id)
db = reopen(db)
check("rename and move keep the project's id, folder and notes",
      db.get_project(p.id).folder_id == ees.id
      and db.folder_path(ees.id) == ["Writing", "EES"]
      and db.get_notes("project", str(p.id)) is not None)
check("moving a folder into its own subfolder is refused",
      isinstance(raises(lambda: db.move_folder(writing.id, ees.id), InvalidMove), InvalidMove)
      and db.get_folder(writing.id).parent_id is None)
check("moving a folder into itself is refused",
      isinstance(raises(lambda: db.move_folder(writing.id, writing.id), InvalidMove), InvalidMove))
check("a folder holding a project cannot be deleted",
      isinstance(raises(lambda: db.delete_folder(ees.id), FolderNotEmpty), FolderNotEmpty))
check("a folder holding a folder cannot be deleted",
      isinstance(raises(lambda: db.delete_folder(writing.id), FolderNotEmpty), FolderNotEmpty))
db.move_project(p.id, None)
db.delete_folder(ees.id)
check("an empty folder can be deleted; the project is at the root",
      db.get_folder(ees.id) is None and db.get_project(p.id).folder_id is None)

# ---------------------------------------------------------------- [11]
print("\n--- [11] a fresh database still holds no user content ---")
fresh_dir = pathlib.Path(tempfile.mkdtemp(prefix="g3-fresh-"))
fresh = Database(fresh_dir / "journal.db")
fresh.close()
check("has_user_content is False", data_migration.has_user_content(fresh_dir) is False)
check("the new tables are empty",
      all(sqlite3.connect(str(fresh_dir / "journal.db")).execute(
          f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
          for t in ("event_recurrence", "event_exceptions", "period_titles", "project_folders")))


# ------------------------------------------------ a pre-Group 3 database
def make_pre_group3_db(path: pathlib.Path):
    """A database in exactly the shape the app wrote before Group 3: built
    with today's code, then with every Group 3 table, column, index and flag
    removed, and pre-Group 3 content added."""
    d = Database(path)
    d.create_event("2026-05-01", 540, 600, title="Old meeting", color="#123456")
    d.create_event("2026-05-02", 0, 0, title="Old untimed", all_day=True)
    for title, category, archived in (("Novel draft", "Novel", 0), ("Novel notes", " Novel ", 0),
                                      ("Chapter 3", "Thesis", 0), ("Loose", None, 0),
                                      ("Old thesis", "Thesis", 1), ("Blank", "", 0)):
        d._conn.execute(
            "INSERT INTO projects (title, content_md, content_format, content_text, created_at, "
            "updated_at, archived, category) VALUES (?, '<p>x</p>', 'html', 'x', 'a', 'a', ?, ?)",
            (title, archived, category))
    d._conn.commit()
    d.close()
    con = sqlite3.connect(str(path))
    for index in ("idx_calendar_events_end_date", "idx_calendar_events_series"):
        con.execute(f"DROP INDEX IF EXISTS {index}")
    for table in ("event_recurrence", "event_exceptions", "period_titles"):
        con.execute(f"DROP TABLE {table}")
    for column in ("end_date", "series_id", "occurrence_date"):
        con.execute(f"ALTER TABLE calendar_events DROP COLUMN {column}")
    con.execute("ALTER TABLE projects DROP COLUMN folder_id")
    con.execute("DROP TABLE project_folders")
    con.execute("DELETE FROM settings WHERE key = ?", (Database.FOLDERS_MIGRATION_SETTING,))
    con.commit()
    con.close()


def check_migrated(d: Database, label: str):
    folders = {f.name: f for f in d.list_folders()}
    projects = {p.title: p for p in d.list_projects() + d.list_projects(archived=True)}
    check(f"{label}: one top-level folder per category (spaces ignored)",
          sorted(folders) == ["Novel", "Thesis"]
          and all(f.parent_id is None for f in folders.values()), sorted(folders))
    check(f"{label}: projects are in their category's folder, archived ones too",
          projects["Novel draft"].folder_id == folders["Novel"].id
          and projects["Novel notes"].folder_id == folders["Novel"].id
          and projects["Chapter 3"].folder_id == folders["Thesis"].id
          and projects["Old thesis"].folder_id == folders["Thesis"].id)
    check(f"{label}: uncategorised projects stay at the root",
          projects["Loose"].folder_id is None and projects["Blank"].folder_id is None)
    check(f"{label}: the old category values are unchanged",
          projects["Novel notes"].category == " Novel " and projects["Chapter 3"].category == "Thesis")
    old_list = d.get_events("2026-05-01") + d.get_events("2026-05-02")
    old_events = {e.title: e for e in old_list}
    check(f"{label}: exactly the two old events", len(old_list) == 2, len(old_list))
    check(f"{label}: old events are intact and end on their own day",
          old_events["Old meeting"].color == "#123456" and old_events["Old meeting"].end_date is None
          and [(s, e) for _k, s, e, t in pieces_on(d, "2026-05-01")] == [(540, 600)])
    check(f"{label}: events work in the new model straight away",
          d.create_event("2026-05-03", 1380, 60, end_date="2026-05-04", title="New").duration == 120)
    n_folders = len(d.list_folders())
    d._migrate_project_folders()
    check(f"{label}: running the migration again changes nothing", len(d.list_folders()) == n_folders)


print("\n--- [10] a pre-Group 3 backup restores and is migrated ---")
db.close()
work = pathlib.Path(tempfile.mkdtemp(prefix="g3-old-"))
old_db = work / "journal.db"
make_pre_group3_db(old_db)
cols = {r[1] for r in sqlite3.connect(str(old_db)).execute("PRAGMA table_info(calendar_events)")}
check("the fixture really is in the old shape", "end_date" not in cols)
old_zip = work / "jortle_claude-backup-pre-group3.zip"
with zipfile.ZipFile(old_zip, "w") as zf:
    zf.writestr("manifest.json", json.dumps({"app": "jortle_claude", "exported_at": "2026-09-01"}))
    zf.write(old_db, arcname="journal.db")
manifest = backup.restore_backup(old_zip)
check("the restore succeeded", manifest is not None)
db = Database()
check_migrated(db, "restored")
db.close()

# ---------------------------------------------------------------- [12]
print("\n--- [12] the migrations on an encrypted database ---")
# A separate folder of its own (the data folder is resolved once per
# process, so it is named explicitly rather than through get_data_dir).
enc_data = pathlib.Path(tempfile.mkdtemp(prefix="g3-enc-"))
make_pre_group3_db(enc_data / "journal.db")
security.set_up_database_encryption(enc_data, "correct horse battery")
check("the pre-Group 3 journal is now encrypted",
      security.db_file_state(enc_data / "journal.db") == "encrypted")
security.session.clear()
security.unlock(enc_data, "correct horse battery")
db = Database(enc_data / "journal.db")
check("it opened through SQLCipher", db.encrypted)
check_migrated(db, "encrypted")
db.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
