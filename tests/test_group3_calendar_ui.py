"""Group 3, stage 3B — the calendar views on the new event model.

Acceptance criteria 14-23 of the Group 3 plan are marked [14]..[23]. Driven
through the real MainWindow with real mouse events (FP-9 point 1); every
result is read back from the database (point 2). Only the two modal
questions are answered by the test: the event editor (event_commands.
run_dialog, which still builds the real dialog) and the "which occurrences?"
question (event_commands.ask_scope).
"""
import os
import pathlib
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g3-cal-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import event_commands, theme  # noqa: E402
from app.database import CalendarEvent, OPACITY_FOLLOWS_DEFAULT  # noqa: E402
from app.day_calendar_model import minute_to_y  # noqa: E402
from app.event_dialog import EventDialog  # noqa: E402
from app.event_render import paint_event_block, resolve_event_opacity  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.recurrence import Occurrence, RecurrenceRule, day_pieces  # noqa: E402
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


def send(widget, kind, x, y):
    button = Qt.LeftButton
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    app.sendEvent(widget, QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button,
                                      buttons, Qt.NoModifier))


def drag(widget, start: QPoint, end: QPoint, steps=6):
    send(widget, QEvent.MouseButtonPress, start.x(), start.y())
    for i in range(1, steps + 1):
        send(widget, QEvent.MouseMove, start.x() + (end.x() - start.x()) * i / steps,
             start.y() + (end.y() - start.y()) * i / steps)
    send(widget, QEvent.MouseButtonRelease, end.x(), end.y())
    settle()


def double_click(widget, x, y):
    send(widget, QEvent.MouseButtonPress, x, y)
    send(widget, QEvent.MouseButtonRelease, x, y)
    send(widget, QEvent.MouseButtonDblClick, x, y)
    send(widget, QEvent.MouseButtonRelease, x, y)
    settle()


# The two modal questions, answered from queues the checks fill.
dialog_actions = []      # callables(dialog) -> bool (accepted?)
scope_answers = []
scope_asked = []


def fake_run_dialog(dialog):
    action = dialog_actions.pop(0) if dialog_actions else (lambda d: True)
    return action(dialog)


def fake_ask_scope(parent, action, allow_this=True):
    scope_asked.append((action, allow_this))
    return scope_answers.pop(0) if scope_answers else None


event_commands.run_dialog = fake_run_dialog
event_commands.ask_scope = fake_ask_scope

win = MainWindow()
win.resize(1400, 900)
win.show()
settle(100)
db = win.db
day = win.day_calendar
week = win.week_calendar
MON, TUE, WED = "2026-11-02", "2026-11-03", "2026-11-04"   # a Sunday-start week: Nov 1-7

win.selected_date.set(MON)
week.visible_week.align_to_week_of(MON)
win.main_tabs.setCurrentWidget(week)
settle(100)
week.timeline.resize(1000, week.timeline.minimumHeight())
settle()
check("both views use one shared command object", day.commands is week.commands is win.event_commands)


def hh():
    return week.prefs.hour_height


def column_x(grid, date):
    left, right = grid._geometry().bounds(grid._dates.index(date))
    return int((left + right) / 2)


def refresh_all():
    day.refresh()
    week.refresh()
    settle()
    # grab() paints a widget even when its tab is not in front, which is
    # what fills the hit-test rectangles the checks read.
    for w in (day.timeline, week.timeline, day.all_day_strip, week.all_day_row):
        w.grab()


# ---------------------------------------------------------------- [14]
print("\n--- [14] new events are 70% opaque, stored as 'follow the default' ---")
dialog_actions.append(lambda d: True)            # the real editor, saved unchanged
day._on_create_requested(MON, 9 * 60, 10 * 60)
new = [e for e in db.get_events(MON) if e.start_minute == 540][0]
check("stored with the follow-the-default sentinel", new.opacity == OPACITY_FOLLOWS_DEFAULT)
check("which resolves to 70% opaque", resolve_event_opacity(new) == 70)
original = theme.DEFAULT_EVENT_TRANSPARENCY
theme.DEFAULT_EVENT_TRANSPARENCY = 50
check("changing the constant changes the drawing...", resolve_event_opacity(db.get_event(new.id)) == 50)
check("...and rewrites no row", db.get_event(new.id).opacity == OPACITY_FOLLOWS_DEFAULT)
theme.DEFAULT_EVENT_TRANSPARENCY = original
refresh_all()


def sample_fill(grid, key):
    image = grid.grab().toImage()
    rect = grid._rects[key]
    return image.pixelColor(rect.right() - 3, rect.bottom() - 3)


def expected_fill(accent, surface, opacity=0.7):
    a, s = QColor(accent), QColor(surface)
    return (round(a.red() * opacity + s.red() * (1 - opacity)),
            round(a.green() * opacity + s.green() * (1 - opacity)),
            round(a.blue() * opacity + s.blue() * (1 - opacity)))


def close_to(color, rgb, tol=10):
    return all(abs(c - e) <= tol for c, e in zip((color.red(), color.green(), color.blue()), rgb))


for name, grid in (("Day View", day.timeline), ("Week View", week.timeline)):
    got = sample_fill(grid, new.id)
    want = expected_fill(theme.event_color(grid.scheme), grid.scheme.panel)
    check(f"{name} paints it at 70% over the pane", close_to(got, want),
          f"{got.name()} vs {want}")

# ---------------------------------------------------------------- [15]
print("\n--- [15] a theme change recolours theme events, not custom ones ---")
custom = db.create_event(MON, 11 * 60, 12 * 60, title="Custom", color="#c0392b", opacity=100)
refresh_all()
before_theme = sample_fill(week.timeline, new.id)
before_custom = sample_fill(week.timeline, custom.id)
db.set_setting("color_scheme", scheme_to_json(PRESETS["Dark"]))
win._apply_settings()
refresh_all()
after_theme = sample_fill(week.timeline, new.id)
after_custom = sample_fill(week.timeline, custom.id)
check("the theme-default event changed colour", before_theme.name() != after_theme.name(),
      f"{before_theme.name()} -> {after_theme.name()}")
check("the custom-coloured (opaque) event did not", after_custom.name() == before_custom.name()
      == "#c0392b", f"{before_custom.name()} -> {after_custom.name()}")
check("nothing was written to either row",
      db.get_event(new.id).color is None and db.get_event(custom.id).color == "#c0392b")
db.set_setting("color_scheme", scheme_to_json(PRESETS["Light"]))
win._apply_settings()
refresh_all()

# ---------------------------------------------------------------- [16]
print("\n--- [16] Day and Week draw the same pieces with the same layout ---")
overlap = db.create_event(MON, 9 * 60 + 30, 10 * 60 + 30, title="Overlap")
night = db.create_event(MON, 22 * 60, 60, title="Night", end_date=TUE)
refresh_all()


def layout_of(grid, date):
    return sorted((l.event.id, l.column, l.column_count, l.event.start_minute, l.event.end_minute)
                  for l in grid._layouts_by_date[date])


check("Monday: same pieces, columns and times in both views",
      layout_of(day.timeline, MON) == layout_of(week.timeline, MON), layout_of(day.timeline, MON))
check("overlapping events sit side by side",
      day.timeline._rects[new.id].left() != day.timeline._rects[overlap.id].left())
win.selected_date.set(TUE)
refresh_all()
check("Tuesday: the overnight event's second piece is in both views, 0:00-1:00",
      layout_of(day.timeline, TUE) == layout_of(week.timeline, TUE)
      and (night.id, 0, 1, 0, 60) in layout_of(day.timeline, TUE), layout_of(day.timeline, TUE))
win.selected_date.set(MON)
refresh_all()
x = column_x(week.timeline, MON)
for key in (new.id, overlap.id):
    rect = week.timeline._rects[key]
    send(week.timeline, QEvent.MouseButtonPress, rect.center().x(), rect.center().y())
    send(week.timeline, QEvent.MouseButtonRelease, rect.center().x(), rect.center().y())
    check(f"clicking block {key} selects exactly that event", week.timeline.selected_id == key)

# ---------------------------------------------------------------- [17]
print("\n--- [17] dragging moves the whole event, across midnight and days ---")
late = db.create_event(WED, 22 * 60, 23 * 60 + 30, title="Late")
refresh_all()
rect = week.timeline._rects[late.id]
drag(week.timeline, rect.center(), QPoint(rect.center().x(), int(rect.center().y() + hh())))
row = db.get_event(late.id)
check("Week: moved an hour later, now running past midnight",
      (row.date, row.start_minute, row.end_date, row.end_minute) == (WED, 23 * 60, "2026-11-05", 30),
      (row.date, row.start_minute, row.end_date, row.end_minute))
check("its length is unchanged (90 minutes)", row.duration == 90)
refresh_all()
rect = week.timeline._rects[late.id]
drag(week.timeline, rect.center(), QPoint(column_x(week.timeline, MON), rect.center().y()))
row = db.get_event(late.id)
check("Week: dragged sideways to Monday, same time and length",
      (row.date, row.start_minute, row.duration) == (MON, 23 * 60, 90),
      (row.date, row.start_minute, row.duration))
win.selected_date.set(TUE)
refresh_all()
piece = [p for p in day.timeline.pieces() if p.id == night.id][0]
rect = day.timeline._rects[night.id]
check("Day: the continuation piece has no top edge to resize (it is the day's edge)",
      piece.continues_before)
drag(day.timeline, QPoint(rect.center().x(), rect.center().y()),
     QPoint(rect.center().x(), int(rect.center().y() + hh())))
row = db.get_event(night.id)
check("Day: dragging the Tuesday piece moves the whole event an hour",
      (row.date, row.start_minute, row.end_date, row.end_minute) == (MON, 23 * 60, TUE, 120),
      (row.date, row.start_minute, row.end_date, row.end_minute))
win.selected_date.set(MON)
refresh_all()

# ---------------------------------------------------------------- [18]
print("\n--- [18] resizing the bottom edge past midnight in Week ---")
evening = db.create_event(TUE, 20 * 60, 21 * 60, title="Evening")
refresh_all()
rect = week.timeline._rects[evening.id]
target = QPoint(column_x(week.timeline, WED), int(minute_to_y(2 * 60, hh())))
drag(week.timeline, QPoint(rect.center().x(), rect.bottom() - 1), target)
row = db.get_event(evening.id)
check("the event now ends at 2:00 AM the next day",
      (row.date, row.start_minute, row.end_date, row.end_minute) == (TUE, 20 * 60, WED, 120),
      (row.date, row.start_minute, row.end_date, row.end_minute))
refresh_all()
check("it is drawn on both days, as one selectable event",
      any(p.id == evening.id for p in week.timeline._pieces_by_date[TUE])
      and any(p.id == evening.id for p in week.timeline._pieces_by_date[WED]))

# ---------------------------------------------------------------- [19]
print("\n--- [19] dragging a repeating occurrence asks which ones; Cancel reverts ---")
series = db.create_event("2026-10-26", 14 * 60, 15 * 60, title="Weekly review",
                         recurrence=RecurrenceRule("weekly", 1, frozenset({1})))
refresh_all()
key = (series.id, MON)
rect = week.timeline._rects[key]
scope_asked.clear()
scope_answers.append(None)                       # Cancel
drag(week.timeline, rect.center(), QPoint(rect.center().x(), int(rect.center().y() + hh())))
check("the scope question was asked, for a move", scope_asked == [("move", True)], scope_asked)
check("Cancel changed nothing in the database",
      not db._overrides(series.id) and db.get_event(series.id).start_minute == 14 * 60)
check("and the block is back where it was", week.timeline._rects[key].top() == rect.top())
scope_answers.append(event_commands.SCOPE_THIS)
drag(week.timeline, rect.center(), QPoint(rect.center().x(), int(rect.center().y() + hh())))
over = db._overrides(series.id)
check("'This occurrence only' moved just Monday's occurrence",
      len(over) == 1 and over[0].occurrence_date == MON and over[0].start_minute == 15 * 60
      and db.get_event(series.id).start_minute == 14 * 60)
refresh_all()
key2 = (series.id, "2026-11-09")
week.visible_week.align_to_week_of("2026-11-09")
refresh_all()
rect = week.timeline._rects[key2]
scope_answers.append(event_commands.SCOPE_ALL)
drag(week.timeline, rect.center(), QPoint(column_x(week.timeline, "2026-11-10"), rect.center().y()))
check("'The entire series' moved every occurrence to Tuesdays",
      [o.date for o in db.occurrences_between("2026-11-09", "2026-11-22") if o.series_id == series.id]
      == ["2026-11-10", "2026-11-17"])
refresh_all()
rect = week.timeline._rects[(series.id, "2026-11-10")]
send(week.timeline, QEvent.MouseButtonPress, rect.center().x(), rect.center().y())
send(week.timeline, QEvent.MouseButtonRelease, rect.center().x(), rect.center().y())
scope_answers.append(event_commands.SCOPE_THIS)
app.sendEvent(week.timeline, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
settle()
check("Delete on an occurrence asks, then skips just that one",
      "2026-11-10" not in [o.occurrence_date for o in db.occurrences_between(
          "2026-11-09", "2026-11-22") if o.series_id == series.id]
      and "2026-11-17" in [o.occurrence_date for o in db.occurrences_between(
          "2026-11-09", "2026-11-22") if o.series_id == series.id])
week.visible_week.align_to_week_of(MON)
refresh_all()

print("\n--- the editor on an occurrence, 'this and following' ---")
key3 = (series.id, "2026-11-17")


def rename(dialog):
    dialog.title_input.setText("Review (new format)")
    return True


dialog_actions.append(rename)
scope_answers.append(event_commands.SCOPE_FOLLOWING)
week._on_edit_requested(key3)
titles = {o.date: o.event.title for o in db.occurrences_between("2026-10-26", "2026-12-01")
          if o.event.title.startswith("Review") or o.event.title.startswith("Weekly")}
check("earlier occurrences keep the old title, this and later ones get the new one",
      titles.get("2026-10-27") == "Weekly review" and titles.get("2026-11-17") == "Review (new format)"
      and titles.get("2026-11-24") == "Review (new format)", titles)


def change_rule(dialog):
    dialog.interval_spin.setValue(2)
    return True


dialog_actions.append(change_rule)
scope_asked.clear()
scope_answers.append(event_commands.SCOPE_ALL)
week._on_edit_requested((series.id, "2026-10-27"))
check("changing the repetition does not offer 'this occurrence only'",
      scope_asked == [("edit", False)], scope_asked)
check("and the series now repeats every 2 weeks", db.get_recurrence(series.id).interval == 2)

# ---------------------------------------------------------------- [20]
print("\n--- [20] double-click creates exactly one hour, across midnight ---")
win.selected_date.set("2026-11-06")
refresh_all()
dialog_actions.append(lambda d: True)
x = column_x(day.timeline, "2026-11-06")
double_click(day.timeline, x, int(minute_to_y(23 * 60 + 30, hh())) + 1)
made = [e for e in db.get_events("2026-11-06") if e.start_minute == 23 * 60 + 30]
check("11:30 PM -> 12:30 AM the next day, one row",
      len(made) == 1 and (made[0].end_date, made[0].end_minute) == ("2026-11-07", 30),
      [(e.start_minute, e.end_date, e.end_minute) for e in made])
dialog_actions.append(lambda d: True)
double_click(day.timeline, x, int(minute_to_y(10 * 60, hh())) + 1)
made = [e for e in db.get_events("2026-11-06") if e.start_minute == 10 * 60]
check("10:00 AM -> 11:00 AM", len(made) == 1 and made[0].end_minute == 11 * 60 and made[0].end_date is None)

print("\n--- the editor reads an end before the start as the next day ---")
dialog = EventDialog(CalendarEvent(id=0, date="2026-11-06", start_minute=21 * 60, end_minute=22 * 60))
from PySide6.QtCore import QTime  # noqa: E402
dialog.end_input.setTime(QTime(0, 0))
values = dialog.values()
check("9 PM -> 12 AM is saved as ending at midnight tomorrow",
      (values["date"], values["start_minute"], values["end_date"], values["end_minute"])
      == ("2026-11-06", 21 * 60, "2026-11-07", 0), values)
check("and the editor shows that date", dialog.end_date_input.date().toString("yyyy-MM-dd") == "2026-11-07")

# ---------------------------------------------------------------- [21]
print("\n--- [21] multi-day untimed events span their columns ---")
win.selected_date.set(MON)
trip = db.create_event(TUE, 0, 0, title="Conference", all_day=True, end_date=WED)
refresh_all()
strip = week.all_day_row
check("one chip, spanning Tuesday and Wednesday",
      trip.id in strip._rects and strip._rects[trip.id].width() > 1.5 * strip._geometry().column_width)
win.selected_date.set(WED)
refresh_all()
check("the Day View shows it once on Wednesday",
      list(day.all_day_strip._rects) == [trip.id] or trip.id in day.all_day_strip._rects)
win.selected_date.set(MON)
refresh_all()
rect = strip._rects[trip.id]
target_x = int(strip._geometry().bounds(week.timeline._dates.index("2026-11-05"))[0] + 20)
drag(strip, QPoint(rect.right() - 1, rect.center().y()), QPoint(target_x, rect.center().y()))
row = db.get_event(trip.id)
check("dragging its right end onto Thursday extends it to Thursday",
      (row.date, row.end_date, row.all_day) == (TUE, "2026-11-05", True), (row.date, row.end_date))
refresh_all()
check("still one chip, now three columns wide",
      strip._rects[trip.id].width() > 2.5 * strip._geometry().column_width)

# ---------------------------------------------------------------- [22]
print("\n--- [22] event text stays inside its block, continuation pieces too ---")
occ = Occurrence(event=db.get_event(night.id), date=MON, end_date=TUE)
pieces = [day_pieces(occ, MON), day_pieces(occ, TUE)]
bad = []
cases = 0
for scheme_name in ("Light", "Dark"):
    scheme = PRESETS[scheme_name]
    for pt in (8, 11, 14, 20):
        for w, h in ((24, 10), (60, 20), (120, 40), (200, 90)):
            for piece in pieces:
                image = QImage(400, 300, QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                font = QFont()
                font.setPointSize(pt)
                block = QRect(100, 50, w, h)
                paint_event_block(painter, block, piece, QColor(scheme.accent),
                                  base_font=font, surface=QColor(scheme.panel))
                painter.end()
                cases += 1
                outside = sum(1 for yy in range(0, 300, 2) for xx in range(0, 400, 2)
                              if not (block.left() - 1 <= xx <= block.right() + 1
                                      and block.top() - 1 <= yy <= block.bottom() + 1)
                              and image.pixelColor(xx, yy).alpha() > 0)
                if outside:
                    bad.append((scheme_name, pt, w, h, piece.day))
check(f"{cases - len(bad)}/{cases} cases draw nothing outside the block", not bad, bad[:3])
from app.event_render import _format_span  # noqa: E402
check("both pieces show the whole event's span",
      _format_span(pieces[0]) == _format_span(pieces[1]) == "Mon 11:00 PM – Tue 2:00 AM",
      _format_span(pieces[0]))

# ---------------------------------------------------------------- [23]
print("\n--- [23] zoom and work hours: both views, immediately, and after restart ---")
day_h, week_h = day.timeline.minimumHeight(), week.timeline.minimumHeight()
win.calendar_prefs.set_zoom_percent(150)
settle()
check("zoom grows both grids at once",
      day.timeline.minimumHeight() > day_h * 1.4 and week.timeline.minimumHeight() > week_h * 1.4)
win.calendar_prefs.set_work_hours_enabled(True)
settle()
check("work hours on in both (one shared setting)",
      day.timeline.prefs is week.timeline.prefs and day.timeline.prefs.work_hours_enabled)
win.close()
settle()
win2 = MainWindow()
settle(100)
check("after a restart: zoom 150% and work hours on",
      win2.calendar_prefs.zoom_percent == 150 and win2.calendar_prefs.work_hours_enabled)
check("after a restart: the overnight and multi-day events are intact",
      win2.db.get_event(evening.id).end_date == WED and win2.db.get_event(trip.id).end_date == "2026-11-05")
win2.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
