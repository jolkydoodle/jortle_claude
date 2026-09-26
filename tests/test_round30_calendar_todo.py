"""Round 30 — one untimed concept, a separate ToDo list, double-click to create.

Three changes, and the interesting risk in each is not the feature itself:

* the Day View's untimed events moved from a fixed-height list OUTSIDE the
  calendar into the same strip the Week View uses. The risk is that "same"
  turns out to mean "a copy that will drift", so this asserts both views use
  the one class;
* ToDo is a new concept, and the risk is that it quietly becomes a calendar
  event — or resurrects the retired `tasks` table, whose rows were already
  converted INTO calendar events, which would show a user their old tasks
  twice;
* double-click creates an event, and the risk is that it fires on top of an
  existing one, or races the drag-to-create path that shares the same mouse
  press.

Mouse interactions are driven with real synthesized events, because "is the
double-click wired to empty space only" is not a question you can answer by
calling the handler directly.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r30-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r30-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.all_day_strip import AllDayStrip  # noqa: E402
from app.calendar_prefs import BASE_HOUR_HEIGHT  # noqa: E402
from app.database import Database  # noqa: E402
from app.day_calendar_model import (  # noqa: E402
    DEFAULT_NEW_EVENT_MINUTES, minute_to_y, new_event_span,
)
from app.event_render import gutter_width  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.todo_widget import DEFAULT_MAX_ROWS, TodoPanel  # noqa: E402

failures = []
DATE = "2026-11-04"          # a Wednesday


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=6):
    for _ in range(times):
        app.processEvents()


def paint(widget):
    """Force a synchronous repaint, then settle.

    `_rects` — which is how both the strip and the timeline answer "what is
    under the pointer" — is built during paintEvent. update() only SCHEDULES
    a paint, so reading _rects after it tests whatever the previous paint
    happened to leave behind. grab() actually paints.
    """
    widget.grab()
    settle()


def send(widget, kind, x, y, button=Qt.LeftButton):
    buttons = Qt.LeftButton if kind != QEvent.MouseButtonRelease else Qt.NoButton
    app.sendEvent(widget, QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                                       button, buttons, Qt.NoModifier))


def double_click(widget, x, y):
    """A real double-click: Qt delivers press, release, doubleclick, release."""
    send(widget, QEvent.MouseButtonPress, x, y)
    send(widget, QEvent.MouseButtonRelease, x, y)
    send(widget, QEvent.MouseButtonDblClick, x, y)
    send(widget, QEvent.MouseButtonRelease, x, y)
    settle()


win = MainWindow()
win.show()
win.selected_date.set(DATE)
settle()
db = win.db
day = win.day_calendar
week = win.week_calendar

# ================================================ 1. one untimed concept
print("\n--- the Day View's untimed events use the Week View's strip ---")
check("the Day View has no standalone untimed list any more",
      not hasattr(day, "untimed_list") and not hasattr(day, "untimed_label"))
check("it has an all-day strip", isinstance(day.all_day_strip, AllDayStrip))
check("the Week View has the same kind", isinstance(week.all_day_row, AllDayStrip))
check("literally the same class, not two that look alike",
      type(day.all_day_strip) is type(week.all_day_row))
check("there is no week-specific copy left in the module",
      not hasattr(sys.modules["app.week_calendar"], "_WeekAllDayRow"))

check("the strip is hidden when the day has no untimed events",
      not day.all_day_strip.isVisible())

untimed = db.create_event(date=DATE, start_minute=0, end_minute=0,
                          title="Dentist, sometime", all_day=True)
timed = db.create_event(date=DATE, start_minute=10 * 60, end_minute=11 * 60,
                        title="Standup")
day.refresh()
paint(day.all_day_strip)
paint(day.timeline)
check("it appears once there is one", day.all_day_strip.isVisible())
check("the untimed event is in the strip", untimed.id in day.all_day_strip._rects)
check("and NOT on the timeline",
      timed.id in day.timeline._rects and untimed.id not in day.timeline._rects)

week.visible_week.align_to_week_of(DATE)
week.refresh()
paint(week.all_day_row)
check("the same event is in the Week View's strip",
      untimed.id in week.all_day_row._rects)
check("both views were given the same event row",
      db.get_event(untimed.id).all_day is True)

print("\n--- interacting with an untimed event still works ---")
opened = []
day._open_editor = lambda event: opened.append(event.id)
rect = day.all_day_strip._rects[untimed.id]
double_click(day.all_day_strip, rect.center().x(), rect.center().y())
check("double-clicking it asks to edit it", opened == [untimed.id])

db.update_event(untimed.id, title="Dentist, moved", all_day=True)
day.refresh()
paint(day.all_day_strip)
check("editing it is reflected", untimed.id in day.all_day_strip._rects)

db.update_event(untimed.id, all_day=False, start_minute=14 * 60, end_minute=15 * 60)
day.refresh()
paint(day.all_day_strip)
paint(day.timeline)
check("making it timed moves it to the timeline",
      untimed.id in day.timeline._rects and untimed.id not in day.all_day_strip._rects)

db.update_event(untimed.id, all_day=True)
day.refresh()
paint(day.all_day_strip)
paint(day.timeline)
check("and back again",
      untimed.id in day.all_day_strip._rects and untimed.id not in day.timeline._rects)

db.delete_event(untimed.id)
day.refresh()
settle()
check("deleting the last one hides the strip again", not day.all_day_strip.isVisible())

# ================================================ 2. ToDo is its own thing
print("\n--- ToDo is a separate concept, not an event ---")
check("the Day View has a ToDo panel", isinstance(day.todo_panel, TodoPanel))
# Group 3 (G3-6): with no tasks the panel is just its "Tasks +" heading.
check("with none, only the Tasks heading shows",
      day.todo_panel.isVisible() and not day.todo_panel.list.isVisible())

first = db.add_todo(DATE, "Buy milk")
db.add_todo(DATE, "Call the lab")
day.todo_panel.refresh()
settle()
check("the list appears when there are tasks", day.todo_panel.list.isVisible())
check("with one row each", day.todo_panel.list.count() == 2)

check("a ToDo did not become a calendar event",
      [e.title for e in db.get_events(DATE)] == ["Standup"])
check("and no event became a ToDo",
      [t.text for t in db.get_todos(DATE)] == ["Buy milk", "Call the lab"])
check("the retired tasks table is untouched and still empty",
      db._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0)
check("todos live in their own table",
      db._conn.execute("SELECT COUNT(*) FROM todos WHERE date=?", (DATE,)).fetchone()[0] == 2)

print("\n--- completion: visible, struck through, and persistent ---")
item = day.todo_panel.list.item(0)
item.setCheckState(Qt.Checked)
settle()
check("checking it persists", db.get_todo(first.id).done is True)
check("the task is still visible", day.todo_panel.list.count() == 2)
check("its text is struck through", day.todo_panel.list.item(0).font().strikeOut())
check("the text itself is unchanged", day.todo_panel.list.item(0).text() == "Buy milk")

day.todo_panel.list.item(0).setCheckState(Qt.Unchecked)
settle()
check("unchecking returns it to normal",
      db.get_todo(first.id).done is False
      and not day.todo_panel.list.item(0).font().strikeOut())
day.todo_panel.list.item(0).setCheckState(Qt.Checked)
settle()

print("\n--- tasks belong to their date ---")
win.selected_date.set("2026-11-05")
settle()
check("another day shows its own (none)", day.todo_panel.list.count() == 0)
check("and only the heading remains", not day.todo_panel.list.isVisible())
db.add_todo("2026-11-05", "Thursday only")
day.todo_panel.refresh()
settle()
check("that day's task appears there", day.todo_panel.list.count() == 1)
win.selected_date.set(DATE)
settle()
check("going back shows the original day's tasks", day.todo_panel.list.count() == 2)
check("with the completed one still completed",
      day.todo_panel.list.item(0).font().strikeOut())

print("\n--- the panel sizes itself to its contents ---")
# Since Group 3 the height is the Tasks / calendar splitter's (G3-6): what
# the tasks need until the user drags it, capped at DEFAULT_MAX_ROWS rows.
day.apply_task_split()
settle()
two_rows = day.task_splitter.sizes()[0]
check(f"two tasks make a compact panel ({two_rows}px)",
      day.todo_panel.header_height() < two_rows <= day.todo_panel.content_height() + 2)
for n in range(14):
    db.add_todo(DATE, f"Task number {n}")
day.todo_panel.refresh()
settle()
day.apply_task_split()
settle()
many = day.task_splitter.sizes()[0]
check(f"sixteen tasks stop at the default cap ({many}px)",
      many <= day.todo_panel.content_height(DEFAULT_MAX_ROWS) + 2
      and day.todo_panel.content_height(DEFAULT_MAX_ROWS) < day.todo_panel.content_height(99))
check("and scroll instead",
      day.todo_panel.list.verticalScrollBar().maximum() > 0)
check("it is taller than it was with two", many > two_rows)

for todo in db.get_todos(DATE):
    db.delete_todo(todo.id)
day.todo_panel.refresh()
settle()
day.apply_task_split()
settle()
check("emptying it shrinks the panel to its heading",
      not day.todo_panel.list.isVisible()
      and day.task_splitter.sizes()[0] <= day.todo_panel.header_height() + 2)
check("leaving no placeholder height", day.todo_panel.list.count() == 0)

print("\n--- a day with only ToDos is marked on the month grid ---")
db.add_todo(DATE, "Something to do")
win._refresh_calendar_marks()
settle()
check("the hollow dot covers ToDos too",
      DATE in win.calendar_panel.calendar._other_content_dates)
check("but it is not a journal entry",
      DATE not in win.calendar_panel.calendar._entry_dates)

# ================================================ 3. double-click to create
print("\n--- double-clicking empty timeline creates a one-hour event ---")
check("the default length is one hour", DEFAULT_NEW_EVENT_MINUTES == 60)
for label, minute, expected in [
    ("2:00 PM", 14 * 60, (14 * 60, 15 * 60)),
    ("2:30 PM", 14 * 60 + 30, (14 * 60 + 30, 15 * 60 + 30)),
    ("2:07 PM", 14 * 60 + 7, (14 * 60, 15 * 60)),   # snapped, like a drag
]:
    y = minute_to_y(minute, BASE_HOUR_HEIGHT)
    check(f"{label} -> {expected}", new_event_span(y, BASE_HOUR_HEIGHT) == expected)
late = new_event_span(minute_to_y(23 * 60 + 40, BASE_HOUR_HEIGHT), BASE_HOUR_HEIGHT)
# Group 3 (criterion 20): events can cross midnight, so a late double-click
# still gives exactly one hour, ending on the next day (minute > 1440).
check(f"11:40 PM gives a full hour into the next day {late}", late == (23 * 60 + 45, 24 * 60 + 45))

# Now through the real widget, with real mouse events.
#
# The gesture legitimately ends in the modal event editor — that is the
# requirement ("use the same event-creation workflow/dialog"), and a modal
# dialog would block a headless run for ever. So the DIALOG is stubbed and
# nothing else is: the mouse events, the hit-testing, the y-to-time
# conversion and the signal are all the real ones.
created_drafts = []
opened_for_edit = []
day._create_from_draft = created_drafts.append
week._create_from_draft = created_drafts.append
week._open_editor = lambda event: opened_for_edit.append(event.id)

day.timeline.resize(420, int(24 * BASE_HOUR_HEIGHT))
day.timeline.show()
settle()
requested = []
day.timeline.createRequested.connect(lambda d, s, e: requested.append((s, e)))
edits = []
day.timeline.editRequested.connect(edits.append)

x = gutter_width(day.timeline.font()) + 80
double_click(day.timeline, x, minute_to_y(16 * 60, day.prefs.hour_height))
check(f"double-clicking empty space asks to create one event {requested}",
      requested == [(16 * 60, 17 * 60)])
check("and does not ask to edit anything", not edits)
check("exactly one draft reached the editor, on the selected day",
      len(created_drafts) == 1 and created_drafts[0].date == DATE)
check("the draft is a timed event, one hour long",
      created_drafts[0].all_day is False
      and created_drafts[0].end_minute - created_drafts[0].start_minute == 60)

print("\n--- double-clicking an EXISTING event edits it, and creates nothing ---")
requested.clear()
edits.clear()
created_drafts.clear()
day.refresh()
paint(day.timeline)
block = day.timeline._rects[timed.id]
double_click(day.timeline, block.center().x(), block.center().y())
check("it asks to edit that event", edits == [timed.id])
check("no event was created underneath it", not requested)
check("and no draft reached the editor either", not created_drafts)

print("\n--- and the drag-to-create path is not disturbed ---")
requested.clear()
start_y = minute_to_y(20 * 60, day.prefs.hour_height)
send(day.timeline, QEvent.MouseButtonPress, x, start_y)
for step in range(1, 5):
    send(day.timeline, QEvent.MouseMove, x, start_y + step * 15)
send(day.timeline, QEvent.MouseButtonRelease, x, start_y + 60)
settle()
check(f"a drag still creates from its own span {requested}",
      len(requested) == 1 and requested[0][0] == 20 * 60 and requested[0][1] > 20 * 60)

requested.clear()
send(day.timeline, QEvent.MouseButtonPress, x, minute_to_y(21 * 60, day.prefs.hour_height))
send(day.timeline, QEvent.MouseButtonRelease, x, minute_to_y(21 * 60, day.prefs.hour_height))
settle()
check("a single click still creates nothing", not requested)

print("\n--- the Week View does the same, using the clicked column's date ---")
week.timeline.resize(900, int(24 * BASE_HOUR_HEIGHT))
week.timeline.show()
settle()
week_requests = []
week.timeline.createRequested.connect(
    lambda date, s, e: week_requests.append((date, s, e)))
dates = week.visible_week.dates()
for index in (0, 3, 6):
    week_requests.clear()
    geometry = week.timeline._geometry()
    left, right = geometry.bounds(index)
    created_drafts.clear()
    double_click(week.timeline, (left + right) / 2,
                 minute_to_y(13 * 60, week.prefs.hour_height))
    check(f"column {index} -> {dates[index]} 1:00-2:00 PM {week_requests}",
          week_requests == [(dates[index], 13 * 60, 14 * 60)])
    check(f"the draft carries that column's date",
          len(created_drafts) == 1 and created_drafts[0].date == dates[index])

week_events = db.get_events(dates[2])
if not week_events:
    db.create_event(date=dates[2], start_minute=9 * 60, end_minute=10 * 60,
                    title="Week thing")
week.refresh()
paint(week.timeline)
week_requests.clear()
week_edits = []
week.timeline.editRequested.connect(week_edits.append)
existing = next(iter(week.timeline._rects.items()), None)
if existing is not None:
    event_id, rect = existing
    double_click(week.timeline, rect.center().x(), rect.center().y())
    check("double-clicking an existing week event edits it", week_edits == [event_id])
    check("and creates nothing underneath", not week_requests)

win.editor.mark_clean()
win.projects_widget.editor.mark_clean()
win.close()
settle()

print("\n--- everything survives a restart ---")
fresh = Database()
check("ToDos persisted", [t.text for t in fresh.get_todos(DATE)] == ["Something to do"])
check("Thursday's is still on Thursday",
      [t.text for t in fresh.get_todos("2026-11-05")] == ["Thursday only"])
check("events are untouched", [e.title for e in fresh.get_events(DATE)] == ["Standup"])
fresh.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
