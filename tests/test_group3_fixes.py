"""Group 3 fixes batch — the acceptance criteria written into the handoff on
2026-09-24 ("Group 3 fixes batch"), marked [A1]..[D3].

Drives the real MainWindow: the shared EventCommands with the REAL event
editor (only its exec() is answered, after the check has set its controls
the way a user would), real mouse drags on the Week grid, the archive
export, restarts and the Projects tree. The only stand-ins are the modal
questions (the repeat scope, the editor's exec, names and notices).
"""
import csv
import os
import pathlib
import sqlite3
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g3-fixes-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import math  # noqa: E402

from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
BASE_POINTS = app.font().pointSize()     # the application font is process-wide

from app import archive, event_commands  # noqa: E402
from app.database import Database  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.calendar_widget import paint_day_marks, today_circle_diameter  # noqa: E402
from app.event_render import readable_text_color  # noqa: E402
from app.recurrence import RecurrenceRule  # noqa: E402
from app.theme import PRESETS, scheme_to_json  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=30):
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def send(widget, kind, x, y, modifiers=Qt.NoModifier):
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    app.sendEvent(widget, QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton,
                                      buttons, modifiers))


def drag(widget, start: QPoint, end: QPoint, steps=6):
    send(widget, QEvent.MouseButtonPress, start.x(), start.y())
    for i in range(1, steps + 1):
        send(widget, QEvent.MouseMove, start.x() + (end.x() - start.x()) * i / steps,
             start.y() + (end.y() - start.y()) * i / steps)
    send(widget, QEvent.MouseButtonRelease, end.x(), end.y())
    settle()


# The modal questions, answered from queues each check fills. The editor is
# the real EventDialog: `editor_actions` receive it and set its controls.
editor_actions = []
scope_answers = []
scope_asked = []


def fake_run_dialog(dialog):
    action = editor_actions.pop(0) if editor_actions else (lambda d: True)
    return bool(action(dialog))


def fake_ask_scope(parent, action, allow_this=True):
    scope_asked.append((action, allow_this))
    return scope_answers.pop(0) if scope_answers else None


event_commands.run_dialog = fake_run_dialog
event_commands.ask_scope = fake_ask_scope
refusals = []           # "Event not changed" notices (a modal box in the app)
event_commands.QMessageBox.warning = staticmethod(lambda parent, title, text, *a: refusals.append(text))

win = MainWindow()
win.resize(1500, 950)
win.show()
settle(100)
db = win.db
cmd = win.event_commands
week = win.week_calendar


def table_rows():
    """Every row of the event tables — to prove "nothing was written"."""
    return tuple(tuple(tuple(r) for r in db._conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2"))
                 for name in ("calendar_events", "event_recurrence", "event_exceptions"))


def shown(title_prefix, lo, hi):
    """(date, start, end_date, end, title, notes, colour) of every occurrence
    whose title starts with `title_prefix`, in order."""
    out = []
    for o in db.occurrences_between(lo, hi):
        if (o.event.title or "").startswith(title_prefix):
            out.append((o.date, o.event.start_minute, o.end_date, o.event.end_minute,
                        o.event.title, o.event.notes, o.event.color))
    return out


def edit(key, action, answer=None):
    """The editor on `key`, with `action` setting its controls, then Save."""
    editor_actions.append(lambda d: (action(d), True)[1])
    if answer is not None:
        scope_answers.append(answer)
    scope_asked.clear()
    return cmd.edit(None, key)


def key_on(title_prefix, day):
    return next(o.key for o in db.occurrences_between(day, day)
                if (o.event.title or "").startswith(title_prefix))


def nothing(_dialog):
    pass


# =================================================================== A1
print("\n--- [A1] Save with nothing changed after a whole-series move ---")
standup = db.create_event("2026-09-07", 540, 600, title="Standup",
                          recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
scope_answers.append("all")
cmd.move(None, (standup.id, "2026-09-14"), "2026-09-15", 540)
check("the whole series moved to Tuesdays",
      [d for d, *_ in shown("Standup", "2026-09-01", "2026-09-30")]
      == ["2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29"])
before = table_rows()
edit((standup.id, "2026-09-22"), nothing)
check("unchanged Save asks nothing", scope_asked == [], scope_asked)
check("...and writes nothing", table_rows() == before)

bi = db.create_event("2026-09-04", 600, 660, title="Bi",
                     recurrence=RecurrenceRule("weekly", 2, frozenset({5, 6})))
scope_answers.append("all")
cmd.move(None, (bi.id, "2026-09-04"), "2026-09-05", 600)
bi_dates = [d for d, *_ in shown("Bi", "2026-09-01", "2026-10-31")]
check("every-2-weeks Fri+Sat moved to Sat+Sun",
      bi_dates == ["2026-09-05", "2026-09-06", "2026-09-19", "2026-09-20", "2026-10-03",
                   "2026-10-04", "2026-10-17", "2026-10-18", "2026-10-31"], bi_dates)
before = table_rows()
edit((bi.id, "2026-09-19"), nothing)
check("unchanged Save on the moved fortnightly series writes nothing", table_rows() == before)
check("...and its dates are unchanged",
      [d for d, *_ in shown("Bi", "2026-09-01", "2026-10-31")] == bi_dates)

# =================================================================== A2
print("\n--- [A2] untouched repeat settings are not a change ---")
plain = db.create_event("2026-09-02", 480, 540, title="NoDays",
                        recurrence=RecurrenceRule("weekly", 1, frozenset()))
ends = db.create_event("2026-09-03", 480, 540, title="Ends",
                       recurrence=RecurrenceRule("daily", 1, frozenset(), "2026-09-20"))
for label, key in (("weekly with no stored weekdays", (plain.id, "2026-09-16")),
                   ("daily with an end date", (ends.id, "2026-09-10"))):
    before = table_rows()
    edit(key, nothing)
    check(f"{label}: unchanged Save writes nothing and asks nothing",
          table_rows() == before and scope_asked == [], scope_asked)
edit((plain.id, "2026-09-16"), lambda d: d.interval_spin.setValue(2), answer=None)
check("changing the repeat settings offers only 'following' / 'entire series'",
      scope_asked == [("edit", False)], scope_asked)

# The weekday boxes of a rule with no stored weekdays are only a guide
# (they show the opened occurrence's weekday): untouched, they must not
# become the rule when another repeat control changes (re-audit, A2).
guide = db.create_event("2027-03-03", 540, 600, title="Guide",
                        recurrence=RecurrenceRule("weekly", 1, frozenset()))
db.edit_occurrence(guide.id, "2027-03-10", date="2027-03-12")          # moved to a Friday


def end_in_april(dialog):
    dialog.until_check.setChecked(True)
    dialog.until_input.setDate(QDate(2027, 4, 30))


edit((guide.id, "2027-03-10"), end_in_april, answer="all")
got = shown("Guide", "2027-03-01", "2027-05-31")
check("changing only 'Ends on' from an occurrence moved to a Friday keeps the series on Wednesdays",
      db.get_recurrence(guide.id).weekdays == frozenset() and db.get_recurrence(guide.id).until == "2027-04-30"
      and [g[0] for g in got] == ["2027-03-03", "2027-03-12", "2027-03-17", "2027-03-24", "2027-03-31",
                                  "2027-04-07", "2027-04-14", "2027-04-21", "2027-04-28"], got)

# =================================================================== A3
print("\n--- [A3] the editor applies only what was changed ---")
klass = db.create_event("2026-09-07", 540, 600, title="Class",
                        recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
db.edit_occurrence(klass.id, "2026-09-21", title="Class: guest lecture",
                   start_minute=720, end_minute=780)
edit((klass.id, "2026-09-21"), lambda d: d.notes_input.setPlainText("bring laptop"),
     answer="following")
got = shown("Class", "2026-09-01", "2026-10-12")
check("earlier occurrences untouched",
      [g[:6] for g in got[:2]] == [("2026-09-07", 540, "2026-09-07", 600, "Class", ""),
                                   ("2026-09-14", 540, "2026-09-14", 600, "Class", "")], got[:2])
check("the edited occurrence keeps its own title and time, gains the notes",
      got[2][:6] == ("2026-09-21", 720, "2026-09-21", 780, "Class: guest lecture", "bring laptop"),
      got[2])
check("later occurrences get the notes and keep the series' title and time",
      all(g[1] == 540 and g[3] == 600 and g[4] == "Class" and g[5] == "bring laptop"
          for g in got[3:]) and len(got) == 6, got[3:])

gym = db.create_event("2026-09-07", 540, 600, title="Gym",
                      recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
db.edit_occurrence(gym.id, "2026-09-14", title="Gym (pool)", notes="own")


def recolour(dialog):
    dialog.theme_color_check.setChecked(False)
    dialog._custom_color = "#c0392b"


edit((gym.id, "2026-09-14"), recolour, answer="all")
got = shown("Gym", "2026-09-01", "2026-10-06")
check("colour-only 'entire series': every occurrence gets the colour",
      [g[6] for g in got] == ["#c0392b"] * 5, got)
check("...and keeps its own title and notes",
      [(g[4], g[5]) for g in got] == [("Gym", ""), ("Gym (pool)", "own"), ("Gym", ""),
                                     ("Gym", ""), ("Gym", "")], got)
edit((gym.id, "2026-09-14"), lambda d: d.title_input.setText("Gym"), answer="all")
check("a typed title equal to the series' own still reaches the edited occurrence",
      [g[4] for g in shown("Gym", "2026-09-01", "2026-10-06")] == ["Gym"] * 5)

# A typed END time that one separately edited occurrence starts after is not
# applied to that occurrence, instead of refusing the whole edit (re-audit).
typed = db.create_event("2027-01-04", 540, 600, title="Typed",
                        recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2027-01-25"))
db.edit_occurrence(typed.id, "2027-01-11", start_minute=720, end_minute=780)
editor_actions.clear()


def end_at_1030(dialog):
    from PySide6.QtCore import QTime
    dialog.end_input.setTime(QTime(10, 30))


edit((typed.id, "2027-01-04"), end_at_1030, answer="all")
got = shown("Typed", "2027-01-01", "2027-01-31")
check("a typed end time for the entire series is not refused", not refusals, refusals)
check("a typed end time for the entire series is saved (9:00-10:30)",
      [(g[0], g[1], g[3]) for g in got] == [("2027-01-04", 540, 630), ("2027-01-11", 720, 780),
                                            ("2027-01-18", 540, 630), ("2027-01-25", 540, 630)], got)

# A new date keeps every occurrence's own length; typed start AND end apply as
# a range (second re-audit, A3).
over = db.create_event("2027-01-05", 540, 600, title="Over",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({2}), "2027-01-26"))
db.edit_occurrence(over.id, "2027-01-12", start_minute=23 * 60, end_minute=30, end_date="2027-01-13")
refusals.clear()


def two_days_later(dialog):
    dialog.date_input.setDate(dialog.date_input.date().addDays(2))


edit((over.id, "2027-01-19"), two_days_later, answer="all")
got = shown("Over", "2027-01-01", "2027-02-05")
check("a new date for the entire series with an overnight occurrence is not refused", not refusals, refusals)
check("...every occurrence moves 2 days and keeps its own length",
      [g[:4] for g in got] == [("2027-01-07", 540, "2027-01-07", 600), ("2027-01-14", 1380, "2027-01-15", 30),
                               ("2027-01-21", 540, "2027-01-21", 600), ("2027-01-28", 540, "2027-01-28", 600)], got)
late = db.create_event("2027-06-01", 22 * 60, 60, title="Late", end_date="2027-06-02",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({2}), "2027-06-22"))
db.edit_occurrence(late.id, "2027-06-08", start_minute=600, end_minute=660, end_date=None)


def times_23_to_2(dialog):
    from PySide6.QtCore import QTime
    dialog.start_input.setTime(QTime(23, 0))
    dialog.end_input.setTime(QTime(2, 0))


edit((late.id, "2027-06-01"), times_23_to_2, answer="all")
got = shown("Late", "2027-06-01", "2027-06-30")
check("typed start AND end for an overnight series apply as that range to every occurrence",
      not refusals and all((g[1], g[3]) == (1380, 120) and g[2] > g[0] for g in got) and len(got) == 4,
      (refusals, got))
same_day = db.create_event("2027-04-06", 540, 600, title="SameDay",
                           recurrence=RecurrenceRule("weekly", 1, frozenset({2}), "2027-04-20"))
db.edit_occurrence(same_day.id, "2027-04-13", start_minute=23 * 60, end_minute=30, end_date="2027-04-14")


def times_14_to_15(dialog):
    from PySide6.QtCore import QTime
    dialog.start_input.setTime(QTime(14, 0))
    dialog.end_input.setTime(QTime(15, 0))


edit((same_day.id, "2027-04-06"), times_14_to_15, answer="all")
got = shown("SameDay", "2027-04-01", "2027-04-30")
check("typed 14:00-15:00 makes every occurrence 14:00-15:00 the same day (no 25-hour occurrence)",
      [g[:4] for g in got] == [(d, 840, d, 900) for d in ("2027-04-06", "2027-04-13", "2027-04-20")], got)

# =================================================================== A4
print("\n--- [A4] drags move every occurrence by the same amount ---")
run = db.create_event("2026-10-05", 540, 600, title="Run",
                      recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-10-26"))
db.edit_occurrence(run.id, "2026-10-12", start_minute=720, end_minute=780)
db.edit_occurrence(run.id, "2026-10-19", start_minute=23 * 60, end_minute=23 * 60 + 30)
scope_answers.append("all")
cmd.move(None, (run.id, "2026-10-05"), "2026-10-06", 570)        # +1 day +30 min
got = shown("Run", "2026-10-01", "2026-10-31")
check("9:00 occurrences -> 9:30 the next day",
      [(g[0], g[1], g[3]) for g in got if g[0] in ("2026-10-06", "2026-10-27")]
      == [("2026-10-06", 570, 630), ("2026-10-27", 570, 630)], got)
check("the 12:00 one -> 12:30 the next day (its own time kept)",
      ("2026-10-13", 750, "2026-10-13", 810) in [g[:4] for g in got], got)
check("the 23:00 one -> 23:30 the next day, still 30 minutes",
      ("2026-10-20", 1410, "2026-10-21", 0) in [g[:4] for g in got], got)
scope_answers.append("all")
cmd.move(None, (run.id, "2026-10-06"), "2026-10-06", 690)        # +2 hours
got = shown("Run", "2026-10-01", "2026-10-31")
check("+2 hours pushes the 23:30 occurrence past midnight as one event",
      ("2026-10-21", 90, "2026-10-21", 120) in [g[:4] for g in got]
      and ("2026-10-13", 870, "2026-10-13", 930) in [g[:4] for g in got], got)
scope_answers.append("all")
cmd.resize(None, (run.id, "2026-10-06"), "bottom", "2026-10-06", 13 * 60)   # 11:30-12:30 -> 13:00
got = shown("Run", "2026-10-01", "2026-10-31")
check("resizing the end +30 minutes lengthens each occurrence by 30 minutes",
      [g[3] - g[1] for g in got] == [90, 90, 60, 90], got)     # the 23:00 one was 30 min
scope_answers.append("all")
cmd.resize(None, (run.id, "2026-10-06"), "top", "2026-10-06", 12 * 60 + 45)  # start +75 min
got = shown("Run", "2026-10-01", "2026-10-31")
check("a resize that would reverse an occurrence leaves it 15 minutes long, never reversed",
      [g[3] - g[1] for g in got] == [15, 15, 15, 15], got)
week_rows = db._conn.execute("SELECT COUNT(*) FROM calendar_events WHERE title='Run' "
                             "OR series_id=?", (run.id,)).fetchone()[0]
check("still one series with its two edited occurrences", week_rows == 3, week_rows)

trip = db.create_event("2026-11-02", 0, 0, title="Trip", all_day=True, end_date="2026-11-03",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-11-30"))
db.edit_occurrence(trip.id, "2026-11-16", end_date="2026-11-18")
scope_answers.append("all")
cmd.set_untimed_range(None, (trip.id, "2026-11-09"), "2026-11-09", "2026-11-11")   # end +1 day
got = shown("Trip", "2026-11-01", "2026-12-06")
check("an untimed series' end dragged a day later: each occurrence one day longer",
      [(g[0], g[2]) for g in got] == [("2026-11-02", "2026-11-04"), ("2026-11-09", "2026-11-11"),
                                      ("2026-11-16", "2026-11-19"), ("2026-11-23", "2026-11-25"),
                                      ("2026-11-30", "2026-12-02")], got)

# A real mouse drag in the Week grid, "this and following".
walk = db.create_event("2026-11-30", 600, 660, title="Walk",
                       recurrence=RecurrenceRule("daily", 1, frozenset(), "2026-12-06"))
win.selected_date.set("2026-12-02")
win.main_tabs.setCurrentWidget(week)
settle(80)
week.refresh()
grid = week.timeline
grid.grab()
rect = grid._rects[(walk.id, "2026-12-02")]
hour = week.prefs.hour_height
scope_answers.append("following")
start = rect.center()
drag(grid, start, QPoint(start.x(), start.y() + hour))
got = shown("Walk", "2026-11-29", "2026-12-06")
check("a real drag, 'this and following': earlier days unchanged, later ones +1 hour",
      [(g[0], g[1]) for g in got] == [("2026-11-30", 600), ("2026-12-01", 600),
                                      ("2026-12-02", 660), ("2026-12-03", 660),
                                      ("2026-12-04", 660), ("2026-12-05", 660),
                                      ("2026-12-06", 660)], got)
check("the drag asked the scope once", scope_asked[-1:] == [("move", True)], scope_asked)

# An untimed occurrence inside a timed series moves only by whole days of a
# drag, rounded toward zero (re-audit, A4).
mixed = db.create_event("2027-02-01", 600, 660, title="Mixed",
                        recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2027-02-22"))
db.edit_occurrence(mixed.id, "2027-02-15", all_day=True)
for minutes in (-30, 30):
    scope_answers.append("all")
    start_of = next(g for g in shown("Mixed", "2027-01-25", "2027-03-01") if g[0] <= "2027-02-02")
    cmd.move(None, (mixed.id, start_of[0]), start_of[0], start_of[1] + minutes)
    untimed = [o.date for o in db.occurrences_between("2027-02-01", "2027-02-28")
               if o.event.title == "Mixed" and o.all_day]
    check(f"a {minutes:+d}-minute drag of the series leaves its untimed occurrence on 15 Feb",
          untimed == ["2027-02-15"], untimed)

# ...and follows a drag onto the next or previous day however few hours it
# moved (second re-audit, A4).
mixed2 = db.create_event("2027-05-03", 540, 600, title="Mixed2",
                         recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2027-05-24"))
db.edit_occurrence(mixed2.id, "2027-05-17", all_day=True)
scope_answers.append("all")
cmd.move(None, (mixed2.id, "2027-05-03"), "2027-05-04", 510)       # Mon 9:00 -> Tue 8:30 (+23 h 30)
untimed = [o.date for o in db.occurrences_between("2027-05-01", "2027-05-31")
           if o.event.title == "Mixed2" and o.all_day]
check("dragged onto the next day (+23 h 30): the untimed occurrence moves a day too",
      untimed == ["2027-05-18"], untimed)
scope_answers.append("all")
cmd.move(None, (mixed2.id, "2027-05-04"), "2027-05-03", 570)       # Tue 8:30 -> Mon 9:30 (-23 h)
untimed = [o.date for o in db.occurrences_between("2027-05-01", "2027-05-31")
           if o.event.title == "Mixed2" and o.all_day]
check("dragged back onto the previous day (-23 h): it moves back a day", untimed == ["2027-05-17"], untimed)

# =================================================================== A5
print("\n--- [A5] a rule change keeps edited and cancelled occurrences ---")
lab = db.create_event("2026-09-07", 540, 600, title="Lab",
                      recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
db.edit_occurrence(lab.id, "2026-09-14", title="Lab (other room)")
db.delete_occurrence(lab.id, "2026-09-21")


def to_tuesday(dialog):
    dialog.weekday_checks[1].setChecked(False)
    dialog.weekday_checks[2].setChecked(True)


edit((lab.id, "2026-09-07"), to_tuesday, answer="all")
got = shown("Lab", "2026-09-01", "2026-10-06")
check("Mon -> Tue: the edited 14 Sep is now 15 Sep with its edits, 22 Sep stays cancelled",
      [(g[0], g[4]) for g in got] == [("2026-09-08", "Lab"), ("2026-09-15", "Lab (other room)"),
                                      ("2026-09-29", "Lab"), ("2026-10-06", "Lab")], got)
sem = db.create_event("2026-09-01", 600, 660, title="Sem",
                      recurrence=RecurrenceRule("weekly", 1, frozenset({2})))
db.edit_occurrence(sem.id, "2026-09-08", notes="kept")
edit((sem.id, "2026-09-01"), lambda d: d.interval_spin.setValue(2), answer="all")
got = shown("Sem", "2026-09-01", "2026-09-30")
kept = db._conn.execute("SELECT date, notes, series_id FROM calendar_events WHERE notes='kept'").fetchall()
check("every week -> every 2 weeks: the edited occurrence in a dropped week is kept as an "
      "ordinary event", [tuple(r) for r in kept] == [("2026-09-08", "kept", None)], kept)
check("...and the series shows every other Tuesday",
      [g[0] for g in got] == ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-29"], got)

# =================================================================== A6
print("\n--- [A6] a split series stays one series ---")
yoga = db.create_event("2026-10-05", 420, 480, title="Yoga",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-11-30"))
edit((yoga.id, "2026-10-19"), lambda d: d.title_input.setText("Yoga II"), answer="following")
family = db.series_family(yoga.id)
check("'this and following' made a second part in the same family", len(family) == 2, family)
edit((yoga.id, "2026-10-05"), recolour, answer="all")
check("'entire series' from the first part recolours both parts",
      {g[6] for g in shown("Yoga", "2026-10-01", "2026-11-30")} == {"#c0392b"})
scope_answers.append("all")
cmd.move(None, (family[1], "2026-10-26"), "2026-10-27", 420)
check("dragging a later occurrence, 'entire series', moves both parts a day",
      [g[0] for g in shown("Yoga", "2026-10-01", "2026-12-01")]
      == ["2026-10-06", "2026-10-13", "2026-10-20", "2026-10-27", "2026-11-03", "2026-11-10",
          "2026-11-17", "2026-11-24", "2026-12-01"])
edit((yoga.id, "2026-10-13"), lambda d: d.notes_input.setPlainText("mat"), answer="following")
got = shown("Yoga", "2026-10-01", "2026-12-01")
check("'this and following' from the first part reaches the later part too",
      [g[5] for g in got] == [""] + ["mat"] * 8, got)
scope_answers.append("following")
cmd.delete(None, key_on("Yoga", "2026-11-03"))
check("'delete this and following' in a middle part removes every later part",
      [g[0] for g in shown("Yoga", "2026-10-01", "2026-12-01")]
      == ["2026-10-06", "2026-10-13", "2026-10-20", "2026-10-27"])
edit((yoga.id, "2026-10-06"), lambda d: d.weekday_checks[3].setChecked(True), answer="all")
fam = db.series_family(yoga.id)
got = shown("Yoga", "2026-10-01", "2026-12-01")
check("a rule change on the entire series applies to every part", len(fam) >= 2 and all(
    db.get_recurrence(p).weekdays == frozenset({2, 3}) for p in fam), fam)
check("...which now repeats Tue+Wed over the whole range",
      [g[0] for g in got] == ["2026-10-06", "2026-10-07", "2026-10-13", "2026-10-14",
                              "2026-10-20", "2026-10-21", "2026-10-27", "2026-10-28"], got)
check("...each part keeping its own title and notes",
      [(g[4], g[5]) for g in got] == [("Yoga", ""), ("Yoga", ""), ("Yoga", "mat"), ("Yoga", "mat"),
                                      ("Yoga II", "mat"), ("Yoga II", "mat"), ("Yoga II", "mat"),
                                      ("Yoga II", "mat")], got)
cadence = db.create_event("2026-10-02", 600, 660, title="Cadence",
                          recurrence=RecurrenceRule("weekly", 1, frozenset({5}), "2026-12-31"))
edit((cadence.id, "2026-10-16"), lambda d: d.title_input.setText("Cadence B"), answer="following")
edit((cadence.id, "2026-10-02"), lambda d: d.interval_spin.setValue(2), answer="all")
check("'every 2 weeks' on a split series keeps one cadence across the parts",
      [g[0] for g in shown("Cadence", "2026-10-01", "2026-11-30")]
      == ["2026-10-02", "2026-10-16", "2026-10-30", "2026-11-13", "2026-11-27"])
edit((fam[0], "2026-10-20"), lambda d: d.title_input.setText("Yoga III"), answer="following")
scope_answers.append("all")
cmd.delete(None, (fam[0], "2026-10-06"))
left = db._conn.execute(
    "SELECT COUNT(*) FROM calendar_events WHERE title LIKE 'Yoga%' OR series_id IN "
    "(SELECT event_id FROM event_recurrence)").fetchone()[0]
orphans = db._conn.execute(
    "SELECT (SELECT COUNT(*) FROM event_exceptions WHERE series_id NOT IN "
    "(SELECT event_id FROM event_recurrence)) + (SELECT COUNT(*) FROM event_recurrence "
    "WHERE event_id NOT IN (SELECT id FROM calendar_events))").fetchone()[0]
check("'delete entire series' after another split removes every part", not shown("Yoga", "2026-10-01", "2026-12-31"))
check("...leaving no orphan rule, cancellation or edited-occurrence rows", orphans == 0, orphans)

# Parts that show the same event become one series again on a rule change;
# the editor shows the whole series' end; "Ends on" ends the whole series.
same = db.create_event("2027-06-07", 480, 540, title="Same",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2027-08-30"))
edit((same.id, "2027-07-05"), lambda d: d.weekday_checks[2].setChecked(True), answer="following")
check("a repeat change 'this and following' splits the series",
      len(db.series_family(same.id)) == 2, db.series_family(same.id))
seen_end = []
edit((same.id, "2027-06-14"), lambda d: seen_end.append(d.until_input.date().toString("yyyy-MM-dd")))
check("the editor on the first part shows the whole series' end (30 Aug)", seen_end == ["2027-08-30"], seen_end)


def wednesdays(dialog):
    for n, box in enumerate(dialog.weekday_checks):
        box.setChecked(n == 3)


edit((same.id, "2027-06-14"), wednesdays, answer="all")
fam = db.series_family(same.id)
got = shown("Same", "2027-06-01", "2027-09-30")
check("a rule change on the entire series rejoins parts that show the same event into one",
      len(fam) == 1, fam)
check("...repeating on Wednesdays to the series' end",
      got[0][0] == "2027-06-09" and got[-1][0] == "2027-08-25" and len(got) == 12
      and all(g[0] >= "2027-06-09" for g in got), [g[0] for g in got])
ends = db.create_event("2027-09-06", 480, 540, title="Ends",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
edit((ends.id, "2027-10-04"), lambda d: d.title_input.setText("Ends later"), answer="following")


def end_sept_20(dialog):
    dialog.until_check.setChecked(True)
    dialog.until_input.setDate(QDate(2027, 9, 20))


edit((ends.id, "2027-09-13"), end_sept_20, answer="all")
check("'Ends on' for the entire series of a split series ends the whole series there",
      [g[0] for g in shown("Ends", "2027-09-01", "2027-12-31")]
      == ["2027-09-06", "2027-09-13", "2027-09-20"], shown("Ends", "2027-09-01", "2027-12-31"))

# Joining parts never gives two edited occurrences one identity (second
# re-audit, A6). "This and following" moved a day earlier overlaps the
# earlier part on 9 Oct (as before Group 3); both 9 Oct occurrences get
# their own edits, then the rule changes for the entire series.
daily = db.create_event("2027-10-01", 480, 510, title="Daily",
                        recurrence=RecurrenceRule("daily", 1, frozenset(), "2027-10-20"))
scope_answers.append("following")
cmd.move(None, (daily.id, "2027-10-10"), "2027-10-09", 480)
fam = db.series_family(daily.id)
nine = [o.key for o in db.occurrences_between("2027-10-09", "2027-10-09") if o.event.title == "Daily"]
for number, key in enumerate(nine):
    scope_answers.append("this")
    edit(key, lambda d, n=number: d.notes_input.setPlainText(f"own {n}"), answer=None)
edit((fam[0], "2027-10-01"), lambda d: d.interval_spin.setValue(2), answer="all")
identities = db._conn.execute(
    "SELECT series_id, occurrence_date, COUNT(*) FROM calendar_events WHERE series_id IS NOT NULL "
    "GROUP BY series_id, occurrence_date HAVING COUNT(*) > 1").fetchall()
check("after a rule change no two edited occurrences share an identity", not identities,
      [tuple(r) for r in identities])

# =================================================================== A7
print("\n--- [A7] a series with nothing left is removed ---")
pair = db.create_event("2026-12-07", 600, 660, title="Pair",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-12-14"))
scope_answers.append("this")
cmd.delete(None, (pair.id, "2026-12-07"))
scope_answers.append("this")
cmd.delete(None, (pair.id, "2026-12-14"))
check("deleting both occurrences one by one leaves no rows",
      db.get_event(pair.id) is None and db.get_recurrence(pair.id) is None
      and not db._exception_dates(pair.id))
trio = db.create_event("2026-12-07", 600, 660, title="Trio",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-12-21"))
db.delete_occurrence(trio.id, "2026-12-07")
scope_answers.append("following")
cmd.delete(None, (trio.id, "2026-12-14"))
check("cancel the first, then 'delete this and following' from the second: no rows left",
      db.get_event(trio.id) is None and not db._exception_dates(trio.id))
solo = db.create_event("2026-12-08", 600, 660, title="Solo",
                       recurrence=RecurrenceRule("weekly", 1, frozenset({2}), "2026-12-08"))
own = db.edit_occurrence(solo.id, "2026-12-08", title="Solo edited")
db.delete_event(own.id)
check("deleting the only (edited) occurrence removes the series", db.get_event(solo.id) is None)

# Cancelling the first occurrence and then editing, dragging or changing the
# repeat "this and following" from the second leaves no empty part behind.
for how in ("edit", "drag", "rule"):
    first_gone = db.create_event("2027-11-01", 600, 660, title=f"Emptied {how}",
                                 recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2027-11-15"))
    db.delete_occurrence(first_gone.id, "2027-11-01")
    if how == "edit":
        edit((first_gone.id, "2027-11-08"), lambda d: d.notes_input.setPlainText("x"), answer="following")
    elif how == "drag":
        scope_answers.append("following")
        cmd.move(None, (first_gone.id, "2027-11-08"), "2027-11-08", 630)
    else:
        edit((first_gone.id, "2027-11-08"), lambda d: d.interval_spin.setValue(2), answer="following")
    check(f"cancelled first occurrence, then 'this and following' {how}: no empty part is left",
          db.get_event(first_gone.id) is None and not db._exception_dates(first_gone.id)
          and len(shown(f"Emptied {how}", "2027-10-01", "2027-12-31")) >= 1)

# =================================================================== A8
print("\n--- [A8] archive pages and spreadsheet dates follow real occurrences ---")
monwed = db.create_event("2027-03-02", 600, 660, title="MonWed",
                         recurrence=RecurrenceRule("weekly", 1, frozenset({1, 3}), "2027-03-31"))
gone = db.create_event("2027-04-01", 60, 120, title="Gone",
                       recurrence=RecurrenceRule("daily", 1, frozenset(), "2027-04-01"))
db._conn.execute("INSERT INTO event_exceptions (series_id, occurrence_date) VALUES (?, ?)",
                 (gone.id, "2027-04-01"))       # an empty series as older versions could leave
db._conn.commit()
first_made = db.create_event("2027-05-11", 0, 1440, title="Whole day timed")
second_made = db.create_event("2027-05-11", 0, 60, title="Short")
out = pathlib.Path(tempfile.mkdtemp(prefix="g3-fixes-archive-"))
archive.export_archive(db, out)
pages = {p.stem for p in (out / "entries").glob("*.html")}
check("no page for the series' stored start (Tue 2 Mar is not an occurrence)",
      "2027-03-02" not in pages and "2027-03-03" in pages)
check("no page for a series with nothing left", "2027-04-01" not in pages)
rows = list(csv.reader((out / "calendar" / "calendar.csv").open(encoding="utf-8")))
row = next(r for r in rows if r[6] == "MonWed")
check("its spreadsheet date is its first real occurrence (Wed 3 Mar)", row[1] == "2027-03-03", row)
check("the empty series is not in the spreadsheet or the calendar file",
      not any(r[6] == "Gone" for r in rows)
      and "SUMMARY:Gone" not in (out / "calendar" / "calendar.ics").read_text(encoding="utf-8"))
page = (out / "entries" / "2027-05-11.html").read_text(encoding="utf-8")
check("events starting at the same time are listed in creation order",
      0 < page.index("Whole day timed") < page.index("Short"))

# A series whose first occurrence was edited to a shorter span gets no page
# for a day that occurrence no longer reaches (re-audit, A8).
span2 = db.create_event("2028-01-03", 0, 0, title="Span2", all_day=True, end_date="2028-01-04",
                        recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2028-01-10"))
db.edit_occurrence(span2.id, "2028-01-03", end_date=None)
night2 = db.create_event("2028-02-07", 22 * 60, 120, title="Night2", end_date="2028-02-08",
                         recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2028-02-14"))
db.edit_occurrence(night2.id, "2028-02-07", start_minute=600, end_minute=660, end_date=None)
out2 = pathlib.Path(tempfile.mkdtemp(prefix="g3-fixes-archive2-"))
archive.export_archive(db, out2)
pages2 = {p.stem for p in (out2 / "entries").glob("*.html")}
check("an untimed series whose first occurrence was cut to one day: no page for the day it lost",
      "2028-01-04" not in pages2 and "2028-01-03" in pages2 and "2028-01-10" in pages2, sorted(
          d for d in pages2 if d.startswith("2028-01")))
check("an overnight series whose first occurrence moved to the daytime: no page for the next day",
      "2028-02-08" not in pages2 and "2028-02-07" in pages2, sorted(
          d for d in pages2 if d.startswith("2028-02")))

# =================================================================== A9
print("\n--- [A9] older databases migrate once and keep working ---")
path = pathlib.Path(tempfile.mkdtemp(prefix="g3-fixes-old-")) / "journal.db"
old = Database(path)
first = old.create_event("2026-09-07", 540, 600, title="OldA",
                         recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-09-20"))
second = old.create_event("2026-09-21", 540, 600, title="OldB",
                          recurrence=RecurrenceRule("weekly", 1, frozenset({1}), "2026-10-19"))
old.close()
con = sqlite3.connect(str(path))
con.execute("ALTER TABLE event_recurrence DROP COLUMN series_root")
con.commit()
con.close()
reopened = Database(path)
cols = [r[1] for r in reopened._conn.execute("PRAGMA table_info(event_recurrence)")]
check("the family column is added to an older database", "series_root" in cols)
reopened.delete_series(first.id)
check("two series split before families existed stay independent",
      reopened.get_event(second.id) is not None and reopened.get_event(first.id) is None)
reopened.close()
Database(path).close()


# =================================================================== B1
print("\n--- [B1] overlapping blocks sit side by side at any width ---")
day = win.day_calendar
CLUSTER = "2027-01-12"
for n in range(6):
    db.create_event(CLUSTER, 600 + 5 * n, 660 + 5 * n, title=f"Busy {n}")


def lane_rects(grid, date):
    grid.grab()
    left, right = grid._geometry().bounds(grid._dates.index(date))
    return left, right, [(r, piece) for r, piece in grid._piece_rects
                         if piece.day == date and piece.title.startswith("Busy")]


for width in (900, 1500):
    win.resize(width, 950)
    win.selected_date.set(CLUSTER)
    win.main_tabs.setCurrentWidget(week)
    settle(120)
    week.refresh()
    day.refresh()
    settle()
    wl, wr, wrects = lane_rects(week.timeline, CLUSTER)
    dl, dr, drects = lane_rects(day.timeline, CLUSTER)
    overlaps = [(a[1].title, b[1].title) for i, a in enumerate(wrects) for b in wrects[i + 1:]
                if a[0].intersects(b[0])]
    check(f"{width} px: six Week blocks, none overlapping another", len(wrects) == 6 and not overlaps,
          (len(wrects), overlaps))
    wfrac = sorted(((r.x() - wl) / (wr - wl), p.title) for r, p in wrects)
    dfrac = sorted(((r.x() - dl) / (dr - dl), p.title) for r, p in drects)
    check(f"{width} px: Week and Day place them at the same fractions of the column",
          [t for _f, t in wfrac] == [t for _f, t in dfrac]
          and all(abs(a[0] - b[0]) < 0.05 for a, b in zip(wfrac, dfrac)), (wfrac, dfrac))
    picked = []
    for rect, piece in wrects:
        c = rect.center()
        send(week.timeline, QEvent.MouseButtonPress, c.x(), c.y())
        send(week.timeline, QEvent.MouseButtonRelease, c.x(), c.y())
        settle(10)
        picked.append(week.timeline.selected_id == piece.id)
    check(f"{width} px: a click at each block's centre selects that block", all(picked), picked)
win.resize(1500, 950)
settle(80)

# =================================================================== B2
print("\n--- [B2] the Weekly navigator follows the window ---")
nav = week.navigator.calendar
buttons = {b.text(): b for b in week.findChildren(type(week.week_back_btn))}


def wash_pixels():
    """Pixels of the navigator painted with the 'shown in Week View' wash."""
    image = nav.grab().toImage()
    wash = QColor(nav._scheme.accent)
    panel = QColor(nav._scheme.panel)
    want = [round(wash.red() * 48 / 255 + panel.red() * (1 - 48 / 255)),
            round(wash.green() * 48 / 255 + panel.green() * (1 - 48 / 255)),
            round(wash.blue() * 48 / 255 + panel.blue() * (1 - 48 / 255))]
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            if all(abs(a - b) <= 6 for a, b in zip((c.red(), c.green(), c.blue()), want)):
                count += 1
    return count


def window_on_page():
    """All seven days on screen are cells of the navigator's current page
    (the page's first and last cells computed independently: Sunday on or
    before the 1st, a whole week earlier when the 1st is a Sunday; 42 days)."""
    from datetime import date as _date, timedelta as _td
    dates = week.visible_week.dates()
    first = _date(nav.yearShown(), nav.monthShown(), 1)
    back = (first.weekday() + 1) % 7 or 7
    lo = first - _td(days=back)
    hi = lo + _td(days=41)
    return all(lo.isoformat() <= d <= hi.isoformat() for d in dates), dates


win.selected_date.set("2026-11-04")
win.main_tabs.setCurrentWidget(week)
settle(80)
base_wash = wash_pixels()
for _ in range(6):
    QTest.mouseClick(buttons["Week ▶"], Qt.LeftButton)
settle()
on_page, dates = window_on_page()
check("Week ▶ ×6 from November: the navigator shows the window's month",
      on_page and dates[0] == "2026-12-13", (nav.yearShown(), nav.monthShown(), dates))
check("...and the seven days on screen are shaded", wash_pixels() >= base_wash * 0.8 > 0,
      (wash_pixels(), base_wash))
for _ in range(10):
    app.sendEvent(week, QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.ShiftModifier))
    settle(5)
on_page, dates = window_on_page()
check("Shift+Left ×10: still on the window's month, days shaded",
      on_page and wash_pixels() > 0, (nav.monthShown(), dates))
win.selected_date.set("2026-09-24")
settle()
for _ in range(9):
    QTest.mouseClick(buttons["Day ▶"], Qt.LeftButton)
settle()
on_page, dates = window_on_page()
check("Day ▶ across the month end keeps the window's days on the page",
      on_page and wash_pixels() > 0, (nav.monthShown(), dates))
for start, label in (("2026-09-02", "into a month starting on a Tuesday"),
                     ("2027-03-03", "into a month starting on a Monday"),
                     ("2027-02-03", "into February 2027")):
    win.selected_date.set(start)
    settle()
    for _ in range(4):
        QTest.mouseClick(buttons["◀ Day"], Qt.LeftButton)
        settle(5)
        on_page, dates = window_on_page()
        if not on_page:
            break
    check(f"◀ Day {label}: every day on screen stays on the navigator's page",
          on_page, (nav.yearShown(), nav.monthShown(), dates))

# =================================================================== B3
print("\n--- [B3] Yearly right-click / Shift-click on the selected day ---")
year = win.year_calendar


def cell_center(grid, date):
    grid.grab()
    for rect, key_ in grid._cells:
        if key_ == date:
            return rect.center()
    raise AssertionError(date)


for label, button, modifiers in (("right-click", Qt.RightButton, Qt.NoModifier),
                                 ("Shift-click", Qt.LeftButton, Qt.ShiftModifier)):
    win.selected_date.set("2026-11-18")
    win.main_tabs.setCurrentWidget(week)
    settle()
    QTest.mouseClick(buttons["Day ▶"], Qt.LeftButton)
    settle()
    slid = week.visible_week.dates()[0]
    win.main_tabs.setCurrentWidget(year)
    settle(80)
    november = year.months[10]
    QTest.mouseClick(november, button, modifiers, cell_center(november, "2026-11-18"))
    settle()
    check(f"{label} on the already-selected day (window slid to {slid}) shows Sun 15 – Sat 21 Nov",
          win.main_tabs.currentWidget() is week and week.visible_week.dates()[0] == "2026-11-15"
          and win.selected_date.value == "2026-11-18", week.visible_week.dates())

# =================================================================== B4
print("\n--- [B4] today's number fits inside its circle ---")


def cell_size_of(calendar):
    from PySide6.QtWidgets import QTableView
    view = calendar.findChild(QTableView)
    return view.columnWidth(0), view.rowHeight(1)


def stray_ink(width, height, scheme, font, text, entry=False):
    """Paints today's cell as the grids do and counts pixels outside the
    circle that differ from the empty cell (0 = the number, and with
    `entry` the entry dot, are inside)."""
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(scheme.panel))
    painter = QPainter(image)
    paint_day_marks(painter, QRect(0, 0, width, height), scheme, day_text=text, font=font,
                    is_today=True, has_entry=entry)
    painter.end()
    diameter = max(8, min(today_circle_diameter(font), width - 2, height - 2))
    cx, cy = QRect(0, 0, width, height).center().x(), QRect(0, 0, width, height).center().y()
    panel = QColor(scheme.panel)
    stray = 0
    for y in range(height):
        for x in range(width):
            if math.hypot(x + 0.5 - (cx - diameter // 2 + diameter / 2.0),
                          y + 0.5 - (cy - diameter // 2 + diameter / 2.0)) <= diameter / 2.0 + 2:
                continue
            c = image.pixelColor(x, y)
            if max(abs(c.red() - panel.red()), abs(c.green() - panel.green()),
                   abs(c.blue() - panel.blue())) > 24:
                stray += 1
    return stray


def dot_outside_circle(width, height, scheme, font):
    """Pixels the entry dot changes that are NOT well inside today's circle
    (-1 if the dot changed nothing, i.e. it was not drawn at all)."""
    images = []
    for entry in (False, True):
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor(scheme.panel))
        painter = QPainter(image)
        paint_day_marks(painter, QRect(0, 0, width, height), scheme, day_text="24", font=font,
                        is_today=True, has_entry=entry)
        painter.end()
        images.append(image)
    diameter = max(8, min(today_circle_diameter(font), width - 2, height - 2))
    rect = QRect(0, 0, width, height)
    cx = rect.center().x() - diameter // 2 + diameter / 2.0
    cy = rect.center().y() - diameter // 2 + diameter / 2.0
    changed = outside = 0
    for y in range(height):
        for x in range(width):
            if images[0].pixelColor(x, y) != images[1].pixelColor(x, y):
                changed += 1
                if math.hypot(x + 0.5 - cx, y + 0.5 - cy) > diameter / 2.0 - 2:
                    outside += 1
    return outside if changed else -1


original_size = win.db.get_setting("ui_font_size")
results = []
for points in (9, 14, 20):
    win.db.set_setting("ui_font_size", str(points))
    win._apply_settings()
    settle(150)
    grids = (("Daily", win.calendar_panel.calendar, win.daily_splitter),
             ("Weekly", week.navigator.calendar, week))
    for name, calendar, tab in grids:
        win.main_tabs.setCurrentWidget(tab)       # measured as the user sees it
        settle(120)
        width, height = cell_size_of(calendar)
        circle = today_circle_diameter(calendar.font())
        results.append((points, name, width, height, circle))
        check(f"{points} pt {name}: the cells are big enough for the full-size circle",
              min(width, height) - 2 >= circle, (width, height, circle))
        worst = max(stray_ink(width, height, scheme, calendar.font(), text)
                    for scheme in PRESETS.values() for text in ("24", "28", "30", "8"))
        check(f"{points} pt {name}: in every preset theme no ink of the number falls outside the circle",
              worst == 0, worst)
    win.main_tabs.setCurrentWidget(year)
    settle(150)
    grid = year.months[0]
    grid.grab()
    cell = grid._cells[0][0]
    worst = max(stray_ink(cell.width(), cell.height(), scheme, grid.font(), text)
                for scheme in PRESETS.values() for text in ("24", "28", "30"))
    check(f"{points} pt Yearly: no ink of the number falls outside the circle", worst == 0,
          (cell.width(), cell.height(), worst))
    dot_outside = [dot_outside_circle(cell.width(), cell.height(), scheme, grid.font())
                   for scheme in PRESETS.values()]
    check(f"{points} pt Yearly: today's entry dot is drawn inside the circle, in every theme",
          all(d == 0 for d in dot_outside), dot_outside)
    small = QFont(win.font())
    small.setPointSize(points)
    worst = max(stray_ink(18, 14, scheme, small, "28") for scheme in PRESETS.values())
    check(f"{points} pt: in a cell too small for the circle the number shrinks to fit", worst == 0, worst)
win.db.set_setting("ui_font_size", original_size or str(BASE_POINTS))
win._apply_settings()
settle(100)

win.close()
settle()



# =================================================================== C
def new_window(tab=None):
    w = MainWindow()
    w.resize(1500, 950)
    w.show()
    settle(150)
    if tab is not None:
        w.main_tabs.setCurrentWidget(tab(w))
        settle(120)
    return w


def drag_handle(handle, dx):
    """A real press / move / release on a splitter handle; the global
    position is computed from the press point once, as a hand would move."""
    local = QPointF(handle.rect().center())
    origin = QPointF(handle.mapToGlobal(handle.rect().center()))
    app.sendEvent(handle, QMouseEvent(QEvent.MouseButtonPress, local, origin, Qt.LeftButton,
                                      Qt.LeftButton, Qt.NoModifier))
    for i in range(1, 9):
        step = QPointF(dx * i / 8, 0)
        app.sendEvent(handle, QMouseEvent(QEvent.MouseMove, local + step, origin + step,
                                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    app.sendEvent(handle, QMouseEvent(QEvent.MouseButtonRelease, local + QPointF(dx, 0),
                                      origin + QPointF(dx, 0), Qt.LeftButton, Qt.NoButton,
                                      Qt.NoModifier))
    settle(60)


def nav_width(w):
    s = w.week_calendar.splitter
    return s.sizes()[s.indexOf(w.week_calendar.nav_panel)]


print("\n--- [C1] a pane dragged shut stays shut ---")
w = new_window(lambda x: x.daily_splitter)
drag_handle(w.daily_splitter.handle(1), -900)
check("dragging the month divider all the way left shuts the month pane",
      w.daily_splitter.sizes()[0] == 0, w.daily_splitter.sizes())
drag_handle(w.daily_splitter.handle(2), 900)
check("dragging the Day divider all the way right shuts the Day pane",
      w.daily_splitter.sizes()[2] == 0, w.daily_splitter.sizes())
w.main_tabs.setCurrentWidget(w.week_calendar)
settle(120)
check("the Weekly month pane is shut too (the width is shared)", nav_width(w) == 0, nav_width(w))
w.main_tabs.setCurrentWidget(w.daily_splitter)
settle(120)
check("back on Daily Jorts both panes are still shut",
      w.daily_splitter.sizes()[0] == 0 and w.daily_splitter.sizes()[2] == 0, w.daily_splitter.sizes())
w.close()
settle()
w = new_window(lambda x: x.daily_splitter)
check("after a restart both stay shut",
      w.daily_splitter.sizes()[0] == 0 and w.daily_splitter.sizes()[2] == 0, w.daily_splitter.sizes())
w.main_tabs.setCurrentWidget(w.week_calendar)
settle(120)
check("...and the Weekly month pane too", nav_width(w) == 0, nav_width(w))
w.main_tabs.setCurrentWidget(w.daily_splitter)
settle(120)
drag_handle(w.daily_splitter.handle(1), 320)
opened = w.daily_splitter.sizes()[0]
check("dragging it open gives it a normal width", opened >= 200, w.daily_splitter.sizes())
w.close()
settle()
w = new_window(lambda x: x.daily_splitter)
check("which is remembered after a restart",
      abs(w.daily_splitter.sizes()[0] - opened) <= 2, (w.daily_splitter.sizes(), opened))
drag_handle(w.daily_splitter.handle(2), -300)
check("the Day pane can be dragged open again", w.daily_splitter.sizes()[2] >= 150,
      w.daily_splitter.sizes())

print("\n--- [C2] the Projects list keeps the user's width through font changes ---")
w.main_tabs.setCurrentWidget(w.projects_widget)
settle(120)
ps = w.projects_widget.splitter
drag_handle(ps.handle(1), 340 - ps.sizes()[0])
user = ps.sizes()[0]
check("the Projects list was dragged to about 340", abs(user - 340) <= 4, ps.sizes())
start_size = w.font().pointSize()
for current in (w.daily_splitter, w.projects_widget):
    w.main_tabs.setCurrentWidget(current)
    settle(60)
    for points in (24, start_size):
        w.db.set_setting("ui_font_size", str(points))
        w._apply_settings()
        settle(120)
    w.main_tabs.setCurrentWidget(w.projects_widget)
    settle(150)
    name = "hidden" if current is w.daily_splitter else "showing"
    check(f"font 24 and back while Projects was {name}: the list is the user's width again",
          abs(ps.sizes()[0] - user) <= 2, (ps.sizes(), user))
w.close()
settle()
w = new_window(lambda x: x.projects_widget)
check("...and after a restart", abs(w.projects_widget.splitter.sizes()[0] - user) <= 2,
      w.projects_widget.splitter.sizes())

print("\n--- [C3] month panes never narrower than the month grid needs ---")
from PySide6.QtWidgets import QTableView, QWidget  # noqa: E402


def grid_fits(calendar):
    view = calendar.findChild(QTableView)
    return view.horizontalHeader().length() <= view.viewport().width() + 1


w.main_tabs.setCurrentWidget(w.daily_splitter)
settle(120)
drag_handle(w.daily_splitter.handle(1), 300 - w.daily_splitter.sizes()[0])
stored = w.db.get_setting("layout_monthly_pane_width")
check("the month pane dragged to about 300 at the normal font",
      abs(int(stored) - 300) <= 4 and grid_fits(w.calendar_panel.calendar), stored)
for points in (14, 20, 24):
    w.db.set_setting("ui_font_size", str(points))
    w._apply_settings()
    settle(150)
    daily_ok = grid_fits(w.calendar_panel.calendar)
    w.main_tabs.setCurrentWidget(w.week_calendar)
    settle(150)
    weekly_ok = grid_fits(w.week_calendar.navigator.calendar)
    w.main_tabs.setCurrentWidget(w.daily_splitter)
    settle(150)
    check(f"{points} pt: all seven columns fit in Daily and in Weekly", daily_ok and weekly_ok,
          (daily_ok, weekly_ok, w.daily_splitter.sizes()))
w.db.set_setting("ui_font_size", str(start_size))
w._apply_settings()
settle(150)
check("back at the normal font the stored width returns",
      w.db.get_setting("layout_monthly_pane_width") == stored
      and abs(w.daily_splitter.sizes()[0] - int(stored)) <= 2, (w.daily_splitter.sizes(), stored))
for points in (24, 14):
    w.db.set_setting("ui_font_size", str(points))
    w._apply_settings()
    settle(150)
    live_min = w.calendar_panel.calendar.minimumWidth()
    w.close()
    settle()
    w = new_window(lambda x: x.daily_splitter)
    check(f"{points} pt: the month grid's narrowest width is the same after a restart",
          w.calendar_panel.calendar.minimumWidth() == live_min,
          (live_min, w.calendar_panel.calendar.minimumWidth()))
    w.resize(1366, 740)
    settle(200)
    panel = w.calendar_panel
    grid = panel.calendar
    covered = [type(c).__name__ for c in panel.findChildren(QWidget)
               if c.parentWidget() is panel and c is not grid and c.isVisible()
               and c.geometry().intersects(grid.geometry())]
    check(f"{points} pt in a 1366x740 window: nothing covers the month grid and it has its full height",
          not covered and grid.height() >= grid.minimumHeight(), (covered, grid.height(), grid.minimumHeight())) \
        if points == 14 else None
w.db.set_setting("ui_font_size", str(start_size))
w.close()
settle()
from PySide6.QtWidgets import QLabel  # noqa: E402
from app.ui_util import YieldingHintLabel  # noqa: E402
w = new_window(lambda x: x.week_calendar)
week_hint = w.week_calendar.findChild(YieldingHintLabel)
wrapped = QLabel.heightForWidth(week_hint, week_hint.width())
check("at the normal font the Weekly navigator's hint shows in full",
      week_hint.isVisible() and week_hint.height() >= wrapped > 0, (week_hint.height(), wrapped))
w.main_tabs.setCurrentWidget(w.daily_splitter)
settle(120)
notes_hint = w.reader_notes.hint
check("...and so does Reader's Notes' hint, with its editor below it",
      notes_hint.height() >= notes_hint.heightForWidth(notes_hint.width()) > 0
      and w.reader_notes.editor.height() > 3 * notes_hint.fontMetrics().height(),
      (notes_hint.height(), notes_hint.heightForWidth(notes_hint.width()), w.reader_notes.editor.height()))
w.db.set_setting("ui_font_size", "20")
w.close()
settle()

print("\n--- [C4] a saved interface font applies at startup ---")
import subprocess  # noqa: E402
probe = r"""
import os, sys
sys.path.insert(0, %r)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
app = QApplication([])
from app.main_window import MainWindow
w = MainWindow(); w.resize(1500, 950); w.show(); QTest.qWait(200)
w.main_tabs.setCurrentWidget(w.year_calendar); QTest.qWait(150)
sizes = {
    "tab bar": w.main_tabs.tabBar().font().pointSize(),
    "Daily month grid": w.calendar_panel.calendar.font().pointSize(),
    "Weekly navigator": w.week_calendar.navigator.calendar.font().pointSize(),
    "Weekly header": w.week_calendar.header.font().pointSize(),
    "Yearly grid": w.year_calendar.months[0].font().pointSize(),
}
print(repr(sizes))
w.close()
""" % str(pathlib.Path(__file__).resolve().parent.parent)
result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
line = [l for l in result.stdout.splitlines() if l.startswith("{")]
fonts = eval(line[-1]) if line else {}
check("a new process with 20 pt saved: every widget starts at 20 pt",
      fonts and set(fonts.values()) == {20}, (fonts, result.stderr[-300:]))



# =================================================================== D1
print("\n--- [D1] Projects folders stay open or closed as the user left them ---")
from app import projects_widget  # noqa: E402
names, destinations = [], []
projects_widget.ask_name = lambda parent, title, label, text="": (names.pop(0), True) if names else ("", False)
projects_widget.choose_folder = lambda parent, db, title, top, exclude=None, current=None: \
    (True, destinations.pop(0)) if destinations else (False, None)
projects_widget.notify = lambda parent, title, text: None

w = new_window(lambda x: x.projects_widget)
pw = w.projects_widget
tree = pw.project_list
fa = w.db.create_folder("Alpha")
fa1 = w.db.create_folder("Alpha inner", fa.id)
fb = w.db.create_folder("Beta")
proj = w.db.create_project("Deep project")
w.db.move_project(proj.id, fa1.id)
loose_proj = w.db.create_project("Loose project")
pw.refresh_project_list()
settle()


def is_open(kind, ident):
    item = pw.item_for(kind, ident)
    return item is not None and item.isExpanded()


tree.setFocus()
tree.setCurrentItem(pw.item_for("folder", fa.id))
QTest.keyClick(tree, Qt.Key_Left)             # the keyboard's "close this folder"
settle()
check("Left arrow on a folder closes it", not is_open("folder", fa.id))
tree.setCurrentItem(pw.item_for("folder", fb.id))
check("the other folders stay open", is_open("folder", fb.id) and is_open("folder", fa1.id))
pw.move_to("project", loose_proj.id, fb.id)
settle()
check("after a move rebuilds the tree, Alpha is still closed and Beta open",
      not is_open("folder", fa.id) and is_open("folder", fb.id))
names.append("Beta renamed")
pw.rename_folder(fb.id)
pw.archive_toggle.setChecked(True)
settle()
pw.archive_toggle.setChecked(False)
settle()
check("...and after a rename and a trip to the archived view",
      not is_open("folder", fa.id) and is_open("folder", fb.id))
names.append("Gamma")
gamma = pw.new_folder(None)
check("a new folder starts open", is_open("folder", gamma.id) if gamma else False)
w.close()
settle()
w = new_window(lambda x: x.projects_widget)
pw = w.projects_widget
check("after a restart Alpha is closed, the others open",
      not is_open("folder", fa.id) and is_open("folder", fb.id) and is_open("folder", gamma.id))
pw.project_list.setCurrentItem(pw.item_for("folder", fa.id))
QTest.keyClick(pw.project_list, Qt.Key_Right)
settle()
w.close()
settle()
w = new_window(lambda x: x.projects_widget)
pw = w.projects_widget
check("opening it again is remembered too", is_open("folder", fa.id) and is_open("folder", fa1.id))
pw.project_list.setCurrentItem(pw.item_for("project", proj.id))     # open the project
settle()
check("the project inside Alpha / Alpha inner is the open one", pw.current_project_id == proj.id)
pw.project_list.setCurrentItem(pw.item_for("folder", fa1.id))
QTest.keyClick(pw.project_list, Qt.Key_Left)                       # close its folder
settle()
names.append("Gamma renamed")
pw.rename_folder(gamma.id)                                         # an unrelated rebuild
settle()
check("closing the open project's folder survives an unrelated rebuild",
      not is_open("folder", fa1.id) and pw.current_project_id == proj.id)
check("...and is stored as closed",
      str(fa1.id) in (w.db.get_setting("projects_collapsed_folders") or "").split(","),
      w.db.get_setting("projects_collapsed_folders"))
pw.move_to("project", proj.id, fb.id)
pw.move_to("project", proj.id, fa1.id)                             # moved back into the closed folder
settle()
check("moving the open project into a closed folder opens it to show the project",
      is_open("folder", fa1.id))
check("...and remembers it as open",
      str(fa1.id) not in (w.db.get_setting("projects_collapsed_folders") or "").split(","),
      w.db.get_setting("projects_collapsed_folders"))
pw.project_list.setCurrentItem(pw.item_for("folder", fa1.id))
QTest.keyClick(pw.project_list, Qt.Key_Left)
settle()
w.close()
settle()
w = new_window(lambda x: x.projects_widget)
check("a folder the user closed stays closed after a restart", not is_open("folder", fa1.id))
w.close()
settle()

# =================================================================== D2
print("\n--- [D2] whitespace-only categories and blank folder names ---")
legacy_dir = pathlib.Path(tempfile.mkdtemp(prefix="g3-fixes-cats-"))
legacy = legacy_dir / "journal.db"
seed = Database(legacy)
for title, category in (("Tabbed", "\t"), ("Newline", "\n"), ("Nbsp", " "),
                        ("Spaces", "   "), ("Upper", "Work"), ("Lower", "work"), ("None", None)):
    seed._conn.execute(
        "INSERT INTO projects (title, content_md, content_format, content_text, created_at, "
        "updated_at, archived, category) VALUES (?, '<p>x</p>', 'html', 'x', 'a', 'a', 0, ?)",
        (title, category))
seed._conn.commit()
seed.close()
con = sqlite3.connect(str(legacy))
con.execute("ALTER TABLE projects DROP COLUMN folder_id")
con.execute("DROP TABLE project_folders")
con.execute("DELETE FROM settings WHERE key = ?", (Database.FOLDERS_MIGRATION_SETTING,))
con.commit()
con.close()
import shutil  # noqa: E402
import subprocess  # noqa: E402
layouts = []
for seed_value in ("1", "2", "3"):
    copy = legacy_dir / f"copy{seed_value}.db"
    shutil.copy(legacy, copy)
    script = (f"import sys; sys.path.insert(0, {str(pathlib.Path(__file__).resolve().parent.parent)!r});"
              f"from app.database import Database; d = Database({str(copy)!r});"
              "print(sorted((f.id, f.name) for f in d.list_folders()));"
              "print(sorted((p.title, p.folder_id) for p in d.list_projects()))")
    env = dict(os.environ, PYTHONHASHSEED=seed_value)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, timeout=60).stdout.splitlines()
    layouts.append(out)
folders_made, placed = eval(layouts[0][0]), dict(eval(layouts[0][1]))
check("whitespace-only categories (tab, newline, no-break space, spaces) go to Uncategorized",
      all(placed[t] is None for t in ("Tabbed", "Newline", "Nbsp", "Spaces", "None")), placed)
check("no folder without a name is made", all(name.strip() for _i, name in folders_made), folders_made)
check("'Work' and 'work' get the same folders in the same order on every run",
      layouts[0] == layouts[1] == layouts[2] and [n for _i, n in folders_made] == ["Work", "work"],
      layouts)
w = new_window(lambda x: x.projects_widget)
pw = w.projects_widget
blank = w.db._conn.execute(
    "INSERT INTO project_folders (parent_id, name, position, created_at, updated_at) "
    "VALUES (NULL, ' ', 99, 'a', 'a')").lastrowid
w.db._conn.commit()
pw.refresh_project_list()
settle()
check("a blank-named folder from an earlier run shows as '(unnamed folder)'",
      pw.item_for("folder", blank).text(0) == "(unnamed folder)")
names.append("Named at last")
pw.rename_folder(blank)
check("...and can be renamed", w.db.get_folder(blank).name == "Named at last"
      and pw.item_for("folder", blank).text(0) == "Named at last")
w.close()
settle()

# =================================================================== D3
print("\n--- [D3] no toolbar traceback from read-only previews ---")
if os.environ.get("G3_FIXES_SKIP_D3"):        # mutation runs of other criteria only
    print("  (D3 skipped)")
suite = pathlib.Path(__file__).resolve().parent / "test_group1_history_recovery.py"
run = subprocess.run([sys.executable, str(suite)] if not os.environ.get("G3_FIXES_SKIP_D3")
                     else [sys.executable, "-c", "pass"], capture_output=True, text=True,
                     timeout=600, cwd=str(suite.parent))
check("test_group1_history_recovery passes and prints no bold_btn traceback",
      run.returncode == 0 and "bold_btn" not in run.stdout + run.stderr
      and "Traceback" not in run.stdout + run.stderr, (run.returncode, run.stderr[-400:]))

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
