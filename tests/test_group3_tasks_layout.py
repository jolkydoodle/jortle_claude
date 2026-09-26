"""Group 3, stage 3D — Tasks (label, completion look, date association,
splitter, responsive columns, scrolling) and pane layout persistence (the
shared month-pane width, the D1 restore bug, font changes).

Acceptance criteria 37-45 of the Group 3 plan are marked [37]..[45]. Real
mouse events on real splitter handles and list items; every persisted value
is checked on a NEW MainWindow after the event loop has run past the
deferred sizing timers (FP-5, FP-9).
"""
import os
import pathlib
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g3-tasks-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import todo_widget  # noqa: E402
from app.main_window import MainWindow  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=60):
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def send(widget, kind, pos):
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    app.sendEvent(widget, QMouseEvent(kind, QPointF(pos), widget.mapToGlobal(QPointF(pos)),
                                      Qt.LeftButton, buttons, Qt.NoModifier))


def drag_handle(splitter, index, delta: QPoint):
    """Drags a splitter handle with real mouse events, as a user would."""
    handle = splitter.handle(index)
    start = handle.rect().center()
    send(handle, QEvent.MouseButtonPress, start)
    for step in range(1, 5):
        send(handle, QEvent.MouseMove, start + delta * step / 4)
    send(handle, QEvent.MouseButtonRelease, start + delta)
    settle()


def new_window(width=1400, height=900):
    w = MainWindow()
    w.resize(width, height)
    w.show()
    settle(400)          # past every deferred sizing pass
    return w


answers = []
todo_widget.ask_task_text = lambda parent, title, text="": (answers.pop(0), True) if answers else ("", False)

DATE = "2026-12-31"
win = new_window()
db = win.db
day = win.day_calendar
panel = day.todo_panel
win.selected_date.set(DATE)
settle()

# ---------------------------------------------------------------- [40]
print("\n--- [40] with no tasks, the heading stays and adding works ---")
check("the heading reads Tasks, not To do", panel.heading.text() == "Tasks")
check("the heading and its + are visible", panel.isVisible() and panel.add_button.isVisible())
check("with no list under them", not panel.list.isVisible())
check("the Tasks pane is just the heading",
      day.task_splitter.sizes()[0] <= panel.header_height() + 2, day.task_splitter.sizes())
answers.append("Renew the lab badge")
QTest.mouseClick(panel.add_button, Qt.LeftButton)
settle()
check("+ adds a task to the selected day",
      [t.text for t in db.get_todos(DATE)] == ["Renew the lab badge"])
answers.append("Order sample holders")
day._on_add_task_clicked()                      # the Add task… button's handler
settle()
check("Add task… uses the same command", len(db.get_todos(DATE)) == 2)

# ---------------------------------------------------------------- [37]
print("\n--- [37] a completed task is struck through and greyed ---")
item = panel.list.item(0)
todo_id = item.data(Qt.UserRole)
rect = panel.list.visualItemRect(item)
check_box = QPoint(rect.left() + panel.list.style().pixelMetric(
    panel.list.style().PixelMetric.PM_IndicatorWidth) // 2 + 4, rect.center().y())
send(panel.list.viewport(), QEvent.MouseButtonPress, check_box)
send(panel.list.viewport(), QEvent.MouseButtonRelease, check_box)
settle()
check("clicking its checkbox marks it done in the database", db.get_todo(todo_id).done is True)
item = panel.list.item(0)
check("struck through", item.font().strikeOut())
check("and greyed", item.foreground().color().alpha() < 200, str(item.foreground().color().alpha()))
other = panel.list.item(1)
check("an open task is neither", not other.font().strikeOut()
      and other.foreground().style() == Qt.NoBrush)

# ---------------------------------------------------------------- [38]
print("\n--- [38] tasks stay with their date across month and year ends ---")
db.add_todo("2027-01-01", "New year task")
db.add_todo("2026-11-30", "November task")
for date, expected in (("2027-01-01", ["New year task"]), ("2026-11-30", ["November task"]),
                       ("2026-12-01", []), (DATE, ["Renew the lab badge", "Order sample holders"])):
    win.selected_date.set(date)
    settle()
    shown = [panel.list.item(i).text() for i in range(panel.list.count())]
    check(f"{date} shows exactly its own tasks {shown}", shown == expected)

# ---------------------------------------------------------------- [39]
print("\n--- [39] the Tasks / Day Calendar splitter ---")
before = day.task_splitter.sizes()[0]
drag_handle(day.task_splitter, 1, QPoint(0, 120))
after = day.task_splitter.sizes()[0]
check(f"dragging the boundary down gives Tasks more room ({before} -> {after})", after > before + 80)
check("the height is remembered", db.get_setting("layout_tasks_height") == str(after))
win.selected_date.set("2026-12-01")      # a day without tasks
settle()
check("a chosen height is kept on other days too", abs(day.task_splitter.sizes()[0] - after) <= 2)
win.selected_date.set(DATE)
settle()

# ---------------------------------------------------------------- [41] [42]
print("\n--- [41] [42] tasks reflow into columns and stay reachable ---")
for n in range(14):
    db.add_todo(DATE, f"Measurement batch {n + 1}")
panel.refresh()
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
narrow_total = win.daily_splitter.width()
win.daily_splitter.setSizes([300, narrow_total - 300 - 260, 260])
settle(120)
narrow_cols = panel.columns()
xs_narrow = {panel.list.visualItemRect(panel.list.item(i)).left() for i in range(4)}
win.daily_splitter.setSizes([300, 300, narrow_total - 600])
settle(120)
wide_cols = panel.columns()
xs_wide = {panel.list.visualItemRect(panel.list.item(i)).left() for i in range(4)}
check(f"narrow pane: one column ({narrow_cols})", narrow_cols == 1 and len(xs_narrow) == 1)
check(f"wide pane: several columns ({wide_cols})", wide_cols >= 2 and len(xs_wide) >= 2)
win.daily_splitter.setSizes([300, narrow_total - 300 - 260, 260])
settle(120)
check("narrowing again flows back to one column",
      panel.columns() == 1
      and len({panel.list.visualItemRect(panel.list.item(i)).left() for i in range(4)}) == 1)
# The smallest the pane can be: drag the boundary right up to the top.
drag_handle(day.task_splitter, 1, QPoint(0, -2000))
settle(120)
check(f"at its smallest the pane still shows one whole task ({panel.list.height()}px)",
      panel.list.viewport().height() >= panel._row_height())
bar = panel.list.verticalScrollBar()
check("a short pane has a scroll range", bar.maximum() > 0)
last = panel.list.item(panel.list.count() - 1)
panel.list.scrollToItem(last)
settle()
check("the last task can be scrolled fully into view",
      panel.list.viewport().rect().contains(panel.list.visualItemRect(last)))
bar.setValue(bar.maximum())
settle()
check("scrolled to the end, the last task is on screen",
      panel.list.viewport().rect().intersects(panel.list.visualItemRect(last))
      and panel.list.visualItemRect(last).bottom() <= panel.list.viewport().rect().bottom() + 1)
win.daily_splitter.setSizes([300, 300, narrow_total - 600])
settle(120)
panel.list.scrollToItem(last)
settle()
check("after widening, the range is recomputed and the last task is still reachable",
      panel.list.viewport().rect().contains(panel.list.visualItemRect(last).center()))

# ---------------------------------------------------------------- [43]
print("\n--- [43] the month pane width is shared by Daily Jorts and the Weekly Schedule ---")
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
win._apply_pane_sizes()
settle()
start_left = win.daily_splitter.sizes()[0]
drag_handle(win.daily_splitter, 1, QPoint(90, 0))
daily_left = win.daily_splitter.sizes()[0]
check(f"dragging it in Daily Jorts widens it ({start_left} -> {daily_left})", daily_left > start_left + 60)
check("remembered once", db.get_setting("layout_monthly_pane_width") == str(daily_left))
win.main_tabs.setCurrentWidget(win.week_calendar)
settle()
week = win.week_calendar
nav_width = week.splitter.sizes()[week.splitter.indexOf(week.nav_panel)]
check(f"the Weekly Schedule's month pane has the same width ({nav_width})",
      abs(nav_width - daily_left) <= 2)
drag_handle(week.splitter, 1, QPoint(-50, 0))
nav_width = week.splitter.sizes()[week.splitter.indexOf(week.nav_panel)]
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
check(f"dragging it there changes Daily Jorts too ({nav_width} / {win.daily_splitter.sizes()[0]})",
      abs(win.daily_splitter.sizes()[0] - nav_width) <= 2)
drag_handle(win.daily_splitter, 2, QPoint(-40, 0))
day_width = win.daily_splitter.sizes()[2]
check("the Day Calendar pane width is remembered separately",
      db.get_setting("layout_day_pane_width") == str(day_width))
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
drag_handle(win.projects_widget.splitter, 1, QPoint(70, 0))
projects_sizes = win.projects_widget.splitter.sizes()
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
# What the user dragged to — read from what was stored, not from the panes,
# so a restore that silently falls back to the defaults cannot match it.
expected = {"monthly": int(db.get_setting("layout_monthly_pane_width")),
            "day": int(db.get_setting("layout_day_pane_width")),
            "tasks": int(db.get_setting("layout_tasks_height")), "projects_left": projects_sizes[0]}
from app.ui_util import side_pane_width  # noqa: E402
default_left = side_pane_width(340, win.daily_splitter.width(), max_fraction=0.30)
check(f"guard: the dragged widths differ from the defaults ({expected['monthly']} vs {default_left})",
      abs(expected["monthly"] - default_left) > 20)

# ---------------------------------------------------------------- [44]
print("\n--- [44] every pane comes back after a restart (D1) ---")
win.close()
settle()
win = new_window()
db = win.db
day = win.day_calendar
win.selected_date.set(DATE)
settle(200)
got = win.daily_splitter.sizes()
check(f"Daily Jorts month pane {got[0]} == {expected['monthly']}", abs(got[0] - expected["monthly"]) <= 2)
check(f"Daily Jorts Day Calendar pane {got[2]} == {expected['day']}", abs(got[2] - expected["day"]) <= 2)
check(f"Tasks height {day.task_splitter.sizes()[0]} == {expected['tasks']}",
      abs(day.task_splitter.sizes()[0] - expected["tasks"]) <= 2)
win.main_tabs.setCurrentWidget(win.week_calendar)
settle(300)
week = win.week_calendar
nav = week.splitter.sizes()[week.splitter.indexOf(week.nav_panel)]
check(f"Weekly Schedule month pane, first time shown {nav} == {expected['monthly']}",
      abs(nav - expected["monthly"]) <= 2)
win.main_tabs.setCurrentWidget(win.projects_widget)
settle(300)
left = win.projects_widget.splitter.sizes()[0]
check(f"Projects list pane, first time shown {left} == {expected['projects_left']}",
      abs(left - expected["projects_left"]) <= 2)
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle(300)
check("still the same after the deferred sizing passes and a tab round trip",
      abs(win.daily_splitter.sizes()[0] - expected["monthly"]) <= 2)

# ---------------------------------------------------------------- [45]
print("\n--- [45] a font change keeps the arrangement ---")
for size in (14, 11):
    db.set_setting("ui_font_size", str(size))
    win._apply_settings()
    settle(300)
    check(f"at {size}pt the month pane keeps its width ({win.daily_splitter.sizes()[0]})",
          abs(win.daily_splitter.sizes()[0] - expected["monthly"]) <= 2)
db.set_setting("layout_monthly_pane_width", "60")      # narrower than the month grid can be
win._apply_pane_sizes()
settle(200)
minimum = win.daily_splitter.widget(0).minimumSizeHint().width()
check(f"a width below the pane's minimum grows to it ({win.daily_splitter.sizes()[0]} >= {minimum})",
      win.daily_splitter.sizes()[0] >= minimum - 1)
db.set_setting("ui_font_size", "22")
win._apply_settings()
settle(300)
minimum = win.daily_splitter.widget(0).minimumSizeHint().width()
check(f"at 22pt the month pane is at least its (larger) minimum ({win.daily_splitter.sizes()[0]} >= {minimum})",
      win.daily_splitter.sizes()[0] >= minimum - 1)
check("and the remembered value is not rewritten by that",
      db.get_setting("layout_monthly_pane_width") == "60")
win.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
