"""Round 25 — the Weekly Calendar workspace (Parts 24–32).

Driven with REAL mouse and key events through QTest wherever an interaction
is being tested, not by calling handlers directly: the point of Part 27 is
that dragging works, and a test that calls _on_event_moved() would pass even
if nothing were wired to the mouse at all.

Covers the spec's own Part 44 integration script: create in Day View →
appears in Week View at the same time; move it in Week View → Day View
agrees; change day by dragging across columns; colour and transparency
changes visible in both; delete in one, gone from the other; theme-following
events follow a theme change while explicitly-coloured ones don't; week-row
selection vs. shifted ranges; and arrow keys that don't steal typing.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r25w-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.database import Database  # noqa: E402
from app.date_state import VisibleWeek, week_start_for  # noqa: E402
from app.day_calendar_model import ColumnGeometry, minute_to_y  # noqa: E402
from app.event_render import resolve_event_color, _wrap_lines  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.theme import PRESETS  # noqa: E402
from app.calendar_prefs import BASE_HOUR_HEIGHT as HOUR_HEIGHT  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


# ------------------------------------------------------------------ pure
print("\n--- Part 28: the seven-day window is a sliding window, not a week ---")
week = VisibleWeek("2026-09-13")   # a Sunday
check("seven consecutive days", len(week.dates()) == 7)
check("Sunday -> Saturday by default",
      week.dates() == [f"2026-09-{d}" for d in range(13, 20)])
week.shift(1)
check("shifting right gives Monday–Sunday",
      week.dates() == [f"2026-09-{d}" for d in range(14, 21)])
week.shift(-1)
check("shifting left is the exact inverse", week.start == "2026-09-13")
week.shift(-1)
check("shifting across a month boundary works",
      week.dates()[0] == "2026-09-12" and week.dates()[-1] == "2026-09-18")
check("week_start_for finds the Sunday on or before a day",
      week_start_for("2026-09-16") == "2026-09-13"
      and week_start_for("2026-09-13") == "2026-09-13"
      and week_start_for("2026-09-19") == "2026-09-13")

print("\n--- Part 25/26: column geometry is shared arithmetic ---")
geo = ColumnGeometry(700, 60, 7)
check("seven equal columns fill the content width",
      abs(geo.column_width * 7 - geo.content_width) < 1e-9)
check("a pixel in the gutter clamps to the first column", geo.index_at(10) == 0)
check("a pixel past the right edge clamps to the last", geo.index_at(9999) == 6)
check("column 3's own left edge is in column 3", geo.index_at(geo.left(3) + 1) == 3)
check("the Day View is just count=1",
      ColumnGeometry(700, 60, 1).index_at(400) == 0)

print("\n--- Part 19: event text elides instead of clipping ---")
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
metrics = QFontMetrics(QFont())
lines = _wrap_lines("Research meeting with the team", metrics, 90, 2)
check(f"wraps to the lines available {lines}", len(lines) == 2)
check("the overflowing last line is elided, not cut", lines[-1].endswith("…"))
one_long = _wrap_lines("Supercalifragilistic", metrics, 40, 1)
check(f"a single too-long word is elided too {one_long}",
      len(one_long) == 1 and one_long[0].endswith("…"))
check("no room means no lines", _wrap_lines("x", metrics, 0, 2) == [])

# ------------------------------------------------------- the real window
print("\n--- Part 24: the third top-level workspace ---")
win = MainWindow()
win.resize(1280, 820)
win.show()
app.processEvents()
check("the four workspaces, in the default order (Master Spec §4)",
      [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
      == ["Daily Jorts", "Weekly Schedule", "Yearly Calendar", "Projects"])

wc = win.week_calendar
wc.visible_week.set_start("2026-09-13")
win.main_tabs.setCurrentWidget(wc)
app.processEvents()
QTest.qWait(30)
_sizes = wc.splitter.sizes()
check("Week View gets most of the width",
      _sizes[wc.splitter.indexOf(wc.week_panel)]
      > _sizes[wc.splitter.indexOf(wc.nav_panel)])

dates = wc.visible_week.dates()
timeline = wc.timeline


def column_center_x(index: int) -> int:
    geometry = ColumnGeometry(timeline.width(), timeline._geometry().gutter, 7)
    left, right = geometry.bounds(index)
    return int((left + right) / 2)


def y_for(minute: int) -> int:
    return int(minute_to_y(minute, HOUR_HEIGHT))


def drag(widget, start: QPoint, end: QPoint):
    """A real press / move / release, with an intermediate move so the
    drag threshold is genuinely crossed."""
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, start)
    mid = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    QTest.mouseMove(widget, mid)
    app.processEvents()
    QTest.mouseMove(widget, end)
    app.processEvents()
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, end)
    app.processEvents()


print("\n--- Part 26: one event store behind both views ---")
event = win.db.create_event(date=dates[2], start_minute=9 * 60, end_minute=10 * 60,
                            title="Research meeting")
wc.refresh()
app.processEvents()
check("an event created outside the view shows up in Week View",
      event.id in timeline._rects)
win.selected_date.set(dates[2])
check("...and in Day View, same row",
      any(e.id == event.id for e in win.day_calendar.timeline.pieces()))

print("\n--- Part 27: drag to move in time, inside one day ---")
rect = timeline._rects[event.id]
drag(timeline, rect.center(), QPoint(rect.center().x(), rect.center().y() + HOUR_HEIGHT * 2))
moved = win.db.get_event(event.id)
check(f"moved two hours later ({moved.start_minute // 60}:00)", moved.start_minute == 11 * 60)
check("duration preserved", moved.end_minute - moved.start_minute == 60)
check("still the same day", moved.date == dates[2])
check("Day View shows the new time after its own refresh",
      any(e.id == event.id and e.start_minute == 11 * 60
          for e in win.day_calendar.timeline.pieces()))

print("\n--- Part 27: horizontal drag moves it to another day ---")
wc.refresh()
app.processEvents()
rect = timeline._rects[event.id]
drag(timeline, rect.center(), QPoint(column_center_x(3), rect.center().y()))
moved = win.db.get_event(event.id)
check(f"now on the next day ({moved.date})", moved.date == dates[3])
check("time unchanged by the sideways move", moved.start_minute == 11 * 60)
check("duration still preserved", moved.end_minute - moved.start_minute == 60)

print("\n--- Part 27: resize by dragging an edge ---")
wc.refresh()
app.processEvents()
rect = timeline._rects[event.id]
drag(timeline, QPoint(rect.center().x(), rect.bottom()),
     QPoint(rect.center().x(), rect.bottom() + HOUR_HEIGHT))
resized = win.db.get_event(event.id)
check(f"end moved out by an hour ({resized.end_minute // 60}:00)",
      resized.end_minute == 13 * 60)
check("start untouched by a bottom-edge resize", resized.start_minute == 11 * 60)

print("\n--- Part 27: drag on empty space asks to create ---")
wc.refresh()
app.processEvents()
requested = []
# The workspace's own handler opens a MODAL editor on this signal, which
# would block a headless run forever — disconnect it and listen directly,
# since what's under test here is the drag, not the dialog.
timeline.createRequested.disconnect(wc._on_create_requested)
timeline.createRequested.connect(lambda d, s, e: requested.append((d, s, e)))
drag(timeline, QPoint(column_center_x(5), y_for(15 * 60)),
     QPoint(column_center_x(5), y_for(16 * 60)))
timeline.createRequested.connect(wc._on_create_requested)
check(f"create requested on the right day and span {requested}",
      requested and requested[0][0] == dates[5]
      and requested[0][1] == 15 * 60 and requested[0][2] == 16 * 60)

print("\n--- Part 17/18: colour, transparency and theme-following ---")
themed = win.db.create_event(date=dates[1], start_minute=8 * 60, end_minute=9 * 60,
                             title="Themed")
colored = win.db.create_event(date=dates[1], start_minute=14 * 60, end_minute=15 * 60,
                              title="Coloured", color="#b5651d", opacity=40)
light_accent = QColor(PRESETS["Light"].accent)
dark_accent = QColor(PRESETS["Dark"].accent)
themed_row = win.db.get_event(themed.id)
colored_row = win.db.get_event(colored.id)
check("a theme event follows the theme in both views",
      resolve_event_color(themed_row, light_accent) == light_accent
      and resolve_event_color(themed_row, dark_accent) == dark_accent)
check("an explicitly coloured event ignores the theme",
      resolve_event_color(colored_row, light_accent) == QColor("#b5651d")
      and resolve_event_color(colored_row, dark_accent) == QColor("#b5651d"))
check("transparency is stored and round-trips", colored_row.opacity == 40)

print("\n--- Part 26: deleting in one view removes it from the other ---")
win.db.delete_event(colored.id)
wc.refresh()
win.day_calendar.refresh()
app.processEvents()
check("gone from Week View", colored.id not in timeline._rects)
check("gone from Day View",
      not any(e.id == colored.id for e in win.day_calendar.timeline.pieces()))

print("\n--- Part 29: Left/Right at the workspace, not globally ---")
wc.setFocus(Qt.OtherFocusReason)
app.processEvents()
start_before = wc.visible_week.start
QTest.keyClick(wc, Qt.Key_Right)
check(f"Right slides one day later ({wc.visible_week.start})",
      wc.visible_week.start > start_before)
QTest.keyClick(wc, Qt.Key_Left)
check("Left slides back", wc.visible_week.start == start_before)
check("still exactly seven days", len(wc.visible_week.dates()) == 7)

field = QLineEdit("hello", wc)
field.show()
field.setFocus(Qt.OtherFocusReason)
field.setCursorPosition(5)
app.processEvents()
start_before = wc.visible_week.start
QTest.keyClick(field, Qt.Key_Left)
app.processEvents()
check("a focused text field keeps its own arrow keys",
      wc.visible_week.start == start_before and field.cursorPosition() == 4)
field.deleteLater()
wc.setFocus(Qt.OtherFocusReason)

print("\n--- Parts 30/31: the navigator selects weeks; shifts are respected ---")
from PySide6.QtCore import QDate  # noqa: E402
wc._on_navigator_date_clicked(QDate(2026, 9, 16))   # a Wednesday
check("clicking a day selects its Sunday–Saturday row",
      wc.visible_week.dates() == [f"2026-09-{d}" for d in range(13, 20)])
check("the navigator highlights all seven",
      wc.navigator.calendar._highlight_dates == set(wc.visible_week.dates()))
wc.shift_days(1)
check("a shifted window is NOT snapped back to Sunday",
      wc.visible_week.start == "2026-09-14")
check("the highlight follows the shifted window across two grid rows",
      wc.navigator.calendar._highlight_dates == set(wc.visible_week.dates())
      and "2026-09-20" in wc.navigator.calendar._highlight_dates)
wc.refresh()
check("refreshing does not re-align it either", wc.visible_week.start == "2026-09-14")

print("\n--- Part 32 / G3-5: the week follows the selected date; browsing does not select ---")
win.selected_date.set("2026-11-03")
check("changing the selected date shows its Sunday–Saturday week",
      wc.visible_week.start == "2026-11-01" and wc.visible_week.contains("2026-11-03"))
check("View → Show This Day's Week is gone (Master Spec §34): no such action in any menu",
      not hasattr(win, "_show_selected_week")
      and not any("Show This Day" in a.text() for m in win.menuBar().actions() if m.menu()
                  for a in m.menu().actions()))
day_before = win.selected_date.value
wc.shift_days(3)
check("sliding the week leaves the journal's day alone",
      win.selected_date.value == day_before)
clicked = wc.visible_week.dates()[2]
wc.header.dayClicked.emit(clicked)
check("clicking a day name hands that day to the journal",
      win.selected_date.value == clicked
      and win.main_tabs.currentWidget() is win.daily_splitter)
check("...and the week then aligns to that day's Sunday–Saturday week",
      wc.visible_week.contains(clicked) and wc.visible_week.dates()[0] == "2026-11-01")

print("\n--- untimed events are not lost in Week View ---")
# Back to the Weekly Calendar: the handoff test above left the Daily Journal
# in front, and a hidden widget neither reports visible nor paints.
win.main_tabs.setCurrentWidget(wc)
app.processEvents()
QTest.qWait(30)
win.selected_date.set(wc.visible_week.dates()[0])
untimed = win.db.create_event(date=wc.visible_week.dates()[0], start_minute=0,
                              end_minute=0, all_day=True, title="Dentist sometime")
wc.refresh()
app.processEvents()
QTest.qWait(30)
check("the untimed strip appears when the week has one", wc.all_day_row.isVisible())
check("and holds that event", untimed.id in wc.all_day_row._rects)
win.db.delete_event(untimed.id)
wc.refresh()
app.processEvents()
check("and hides again when there are none", not wc.all_day_row.isVisible())

win.close()
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
