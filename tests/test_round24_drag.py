"""Round 24 — drive the day calendar with REAL synthesized mouse events.

Round 22 tested the drag maths directly and the create/move/resize/delete
paths through the database, but never actually pressed, moved and released a
mouse button on the widget. That left the interaction handlers themselves —
hit testing, the click-vs-drag threshold, edge detection, which mode a press
selects — covered only by inference. This closes that gap by sending genuine
QMouseEvents to the real _Timeline so mousePressEvent / mouseMoveEvent /
mouseReleaseEvent run exactly as they do under a hand.
"""
import os, pathlib, sys, tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
isolation.isolate()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

app = QApplication.instance() or QApplication([])

from app.database import Database
from app.date_state import SelectedDate
from app.calendar_prefs import BASE_HOUR_HEIGHT as HOUR_HEIGHT
from app.day_calendar import _Timeline, EDGE_GRAB_PX
from app.event_render import gutter_width
from app.day_calendar_model import minute_to_y
from app.event_commands import EventCommands

fails = []
def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"   {detail}" if not cond and detail else ""))
    if not cond: fails.append(label)


def send(widget, kind, x, y, button=Qt.LeftButton, buttons=None):
    """Synthesize one real mouse event and deliver it to the widget's own
    handler — not a helper method, the actual event path."""
    if buttons is None:
        buttons = button if kind != QEvent.MouseButtonRelease else Qt.NoButton
    ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                     button, buttons, Qt.NoModifier)
    app.sendEvent(widget, ev)


def y_for(minute):
    return minute_to_y(minute, HOUR_HEIGHT)


X = 0                   # set below, once the timeline exists and has a font


def drag(widget, x1, y1, x2, y2, steps=4):
    send(widget, QEvent.MouseButtonPress, x1, y1)
    for i in range(1, steps + 1):
        send(widget, QEvent.MouseMove,
             x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
    send(widget, QEvent.MouseButtonRelease, x2, y2)


db = Database()
date = "2026-09-15"
selected = SelectedDate(date)

timeline = _Timeline()
timeline.date = date
timeline.resize(420, int(24 * HOUR_HEIGHT))
timeline.show()

# Since Group 3 the grid reports a finished drag as a request (move / resize
# the occurrence to here) and the shared event commands write it — the same
# wiring as DayCalendarWidget. So these drags are checked in the database.
commands = EventCommands(db)
moved = []


def _apply(written):
    moved.append(written)
    timeline.set_events(db.get_events(date))
    timeline.repaint()


timeline.moveRequested.connect(lambda k, d, m: _apply(commands.move(None, k, d, m)))
timeline.resizeRequested.connect(lambda k, e, d, m: _apply(commands.resize(None, k, e, d, m)))

# The hour-label gutter is measured from the font rather than fixed, so the
# x to click at has to be measured too (see event_render.gutter_width).
X = gutter_width(timeline.font()) + 80

# ------------------------------------------------------------------
print("\n=== drag on empty space creates an event ===")
created = []
timeline.createRequested.connect(lambda d, s, e: created.append((s, e)))

drag(timeline, X, y_for(9 * 60), X, y_for(10 * 60 + 30))
check("drag from 9:00 to 10:30 requests a create", len(created) == 1, str(created))
check("created span snaps to the dragged times",
      created and created[0] == (9 * 60, 10 * 60 + 30), str(created[:1]))

# ------------------------------------------------------------------
print("\n=== a click is not a drag ===")
created.clear()
send(timeline, QEvent.MouseButtonPress, X, y_for(14 * 60))
send(timeline, QEvent.MouseButtonRelease, X, y_for(14 * 60))
check("a plain click on empty space creates nothing", created == [], str(created))

created.clear()
drag(timeline, X, y_for(14 * 60), X, y_for(14 * 60) + 2, steps=1)
check("a 2px twitch is treated as a click, not a drag", created == [], str(created))

# ------------------------------------------------------------------
print("\n=== move an existing event, preserving duration ===")
event = db.create_event(date, 9 * 60, 10 * 60, title="Move me")
timeline.set_events(db.get_events(date))
timeline.update()
app.processEvents()
timeline.repaint()          # populates the hit-test rectangles

mid_y = (y_for(9 * 60) + y_for(10 * 60)) / 2
drag(timeline, X, mid_y, X, mid_y + HOUR_HEIGHT * 2)   # down two hours
live = timeline.event_by_id(event.id)
check("dragging the body moved the event", moved == [True] and live.start_minute == 11 * 60
      and db.get_event(event.id).start_minute == 11 * 60,
      f"start={live.start_minute}")
check("duration preserved across the move", live.end_minute - live.start_minute == 60,
      f"{live.start_minute}-{live.end_minute}")

# ------------------------------------------------------------------
print("\n=== resize by dragging an edge ===")
timeline.set_events([live])
timeline.repaint()
rect = timeline._rects[live.id]

moved.clear()
drag(timeline, X, rect.bottom() - 1, X, rect.bottom() + HOUR_HEIGHT)  # bottom edge down 1h
after = timeline.event_by_id(live.id)
check("dragging the bottom edge extended the end time",
      after.end_minute == 13 * 60 and db.get_event(live.id).end_minute == 13 * 60,
      f"end={after.end_minute}")
check("the start time did not move while resizing the bottom",
      after.start_minute == 11 * 60, f"start={after.start_minute}")

timeline.set_events([after])
timeline.repaint()
rect = timeline._rects[after.id]
moved.clear()
drag(timeline, X, rect.top() + 1, X, rect.top() - HOUR_HEIGHT)        # top edge up 1h
after2 = timeline.event_by_id(after.id)
check("dragging the top edge moved the start time",
      after2.start_minute == 10 * 60, f"start={after2.start_minute}")
check("the end time did not move while resizing the top",
      after2.end_minute == 13 * 60, f"end={after2.end_minute}")

# ------------------------------------------------------------------
print("\n=== an edge drag is distinguished from a body drag ===")
timeline.set_events([after2])
timeline.repaint()
rect = timeline._rects[after2.id]
send(timeline, QEvent.MouseButtonPress, X, rect.top() + 1)
check("pressing within the grab zone selects resize mode",
      timeline._mode == "resize-top", str(timeline._mode))
send(timeline, QEvent.MouseButtonRelease, X, rect.top() + 1)

send(timeline, QEvent.MouseButtonPress, X, rect.top() + EDGE_GRAB_PX + 12)
check("pressing well inside the block selects move mode",
      timeline._mode == "move", str(timeline._mode))
send(timeline, QEvent.MouseButtonRelease, X, rect.top() + EDGE_GRAB_PX + 12)

# ------------------------------------------------------------------
print("\n=== select, edit, delete ===")
timeline.set_events([after2])
timeline.repaint()
rect = timeline._rects[after2.id]
cy = rect.center().y()

edits = []
timeline.editRequested.connect(edits.append)

send(timeline, QEvent.MouseButtonPress, X, cy)
send(timeline, QEvent.MouseButtonRelease, X, cy)
check("a single click selects the event", timeline.selected_id == after2.id)
check("a single click does NOT open the editor", edits == [], str(edits))

send(timeline, QEvent.MouseButtonDblClick, X, cy)
check("double-click requests the editor", edits == [after2.id], str(edits))

edits.clear()
from PySide6.QtGui import QKeyEvent
deletes = []
timeline.deleteRequested.connect(deletes.append)
app.sendEvent(timeline, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
check("Delete requests removal of the selected event",
      deletes == [after2.id] and edits == [], str((deletes, edits)))

# clicking empty space clears the selection
send(timeline, QEvent.MouseButtonPress, X, y_for(20 * 60))
send(timeline, QEvent.MouseButtonRelease, X, y_for(20 * 60))
check("clicking empty space deselects", timeline.selected_id is None)

# ------------------------------------------------------------------
print("\n=== dragging cannot create an invalid event ===")
created.clear()
drag(timeline, X, y_for(16 * 60), X, y_for(14 * 60))      # dragged upward
check("an upward drag still yields start < end",
      created and created[0][0] < created[0][1], str(created[:1]))
check("an upward drag produces the span actually covered",
      created and created[0] == (14 * 60, 16 * 60), str(created[:1]))

created.clear()
drag(timeline, X, y_for(23 * 60 + 50), X, y_for(23 * 60 + 59) + 400)  # past midnight
check("a drag past the end of the day is clamped inside it",
      created and created[0][1] <= 24 * 60, str(created[:1]))
check("and still has a usable duration",
      created and created[0][1] > created[0][0], str(created[:1]))

# ------------------------------------------------------------------
print("\n=== overlapping events remain individually clickable ===")
db.delete_event(after2.id)
a = db.create_event(date, 10 * 60, 12 * 60, title="A")
b = db.create_event(date, 10 * 60 + 30, 11 * 60 + 30, title="B")
timeline.set_events(db.get_events(date))
timeline.repaint()
check("both overlapping events have their own rectangle",
      a.id in timeline._rects and b.id in timeline._rects)
rect_a, rect_b = timeline._rects[a.id], timeline._rects[b.id]
check("overlapping events are laid out side by side, not stacked",
      rect_a.left() != rect_b.left(), f"{rect_a.left()} vs {rect_b.left()}")

send(timeline, QEvent.MouseButtonPress, rect_b.center().x(), rect_b.center().y())
send(timeline, QEvent.MouseButtonRelease, rect_b.center().x(), rect_b.center().y())
check("clicking the right-hand block selects that one", timeline.selected_id == b.id,
      f"selected={timeline.selected_id} expected={b.id}")

send(timeline, QEvent.MouseButtonPress, rect_a.center().x(), rect_a.center().y())
send(timeline, QEvent.MouseButtonRelease, rect_a.center().x(), rect_a.center().y())
check("clicking the left-hand block selects that one", timeline.selected_id == a.id,
      f"selected={timeline.selected_id} expected={a.id}")

db.close()
print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED:")
    for f in fails: print("  -", f)
    sys.exit(1)
print("ALL DRAG-INTERACTION CHECKS PASSED (real mouse events)")
