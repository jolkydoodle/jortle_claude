"""Round 31 — pane order, draggable tabs, and the Daily Notes rename.

Driven with REAL mouse events where the requirement is an interaction. A
test that calls `bar.moveTab()` proves the reorder function works; it proves
nothing about whether a tab can be *dragged*, which is what was asked for.
The same goes for the splitter handle.

The seven acceptance points are marked [1]..[7] against the checks that
cover them.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r31-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(ms=50):
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def drag(widget, start: QPoint, end: QPoint, steps=6):
    """Press, cross the drag threshold in several moves, release."""
    QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, start)
    app.processEvents()
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, QPoint(
            start.x() + (end.x() - start.x()) * i // steps,
            start.y() + (end.y() - start.y()) * i // steps))
        app.processEvents()
    QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, end)
    app.processEvents()


win = MainWindow()
win.resize(1280, 820)
win.show()
settle()

# ============================================================ [5] the rename
print("\n--- [5] the tabs read as Master Spec §4 names them (Group 3) ---")
labels = [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
check(f"the default tab order is unchanged {labels}",
      labels == ["Daily Jorts", "Weekly Schedule", "Yearly Calendar", "Projects"])

# Every string the user can actually see, swept at once — a rename that
# leaves the Week View hint or the View-menu tooltip behind is the failure
# mode this catches, and neither is reachable from tabText().
visible = list(labels)
for action in win.menuBar().findChildren(type(win.menuBar().actions()[0])):
    visible += [action.text(), action.toolTip()]
from PySide6.QtWidgets import QLabel  # noqa: E402
visible += [w.text() for w in win.findChildren(QLabel)]
visible += [w.toolTip() for w in win.findChildren(QLabel)]
visible.append(win.windowTitle())
stragglers = [t for t in visible if t and "Daily Journal" in t]
check(f"no user-facing string still says 'Daily Journal' {stragglers}",
      not stragglers)
check("the Weekly Schedule hint points at Daily Jorts",
      any("Daily Jorts" in (w.text() or "")
          for w in win.week_calendar.findChildren(QLabel)))

# The internal names it was NOT supposed to touch.
print("\n--- [6] identity: jortle_claude, with legacy names still recognised ---")
from app.paths import APP_NAME  # noqa: E402
from app.data_migration import LEGACY_DIR_NAMES  # noqa: E402
from app.backup import default_backup_filename  # noqa: E402
check("the Qt organization name follows the jortle_claude identity", APP_NAME == "jortle_claude")
check("the legacy folder names are still recognised",
      "DailyJournal" in LEGACY_DIR_NAMES)
check("backup filenames use the jortle_claude identity",
      default_backup_filename().startswith("jortle_claude-backup-"))
check("the previous Jortle folder is recognised as a legacy source, first",
      LEGACY_DIR_NAMES[0] == "Jortle")
check("the entries table is untouched",
      [r[0] for r in win.db._conn.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='entries'")]
      == ["entries"])

# ====================================================== [1] Monthly on left
print("\n--- [1] the month navigator is the left pane ---")
week = win.week_calendar
win.main_tabs.setCurrentWidget(week)
settle()
splitter = week.splitter
nav_x = week.nav_panel.mapTo(week, week.nav_panel.rect().topLeft()).x()
week_x = week.week_panel.mapTo(week, week.week_panel.rect().topLeft()).x()
check(f"on screen the navigator starts left of Week View "
      f"(navigator x={nav_x}, Week View x={week_x})", nav_x < week_x)
check("the left pane really holds the month grid",
      week.nav_panel.isAncestorOf(week.navigator))
check("the right pane really holds the week timeline",
      week.week_panel.isAncestorOf(week.timeline))
sizes = splitter.sizes()
check(f"Week View is still the wider pane {sizes}",
      sizes[splitter.indexOf(week.week_panel)]
      > sizes[splitter.indexOf(week.nav_panel)])
check("the month grid is not squeezed below seven columns",
      week.navigator.calendar.width() >= 7 * 24)

print("\n--- the widths are assigned by pane, not by position ---")
splitter.insertWidget(0, week.week_panel)          # pretend they were swapped
settle(20)
week._apply_pane_sizes()
settle(20)
swapped = splitter.sizes()
check(f"Week View would still get the width if the panes were reversed "
      f"{swapped}",
      swapped[splitter.indexOf(week.week_panel)]
      > swapped[splitter.indexOf(week.nav_panel)])
splitter.insertWidget(0, week.nav_panel)           # put it back
settle(20)
week._apply_pane_sizes()
settle(20)
check("navigator restored to the left", splitter.indexOf(week.nav_panel) == 0)

# ================================================= [2] the divider still works
print("\n--- [2] the divider still drags ---")
handle = splitter.handle(1)
check("there is a draggable handle between the two panes",
      handle is not None and splitter.handleWidth() > 0)
before = splitter.sizes()
drag(handle,
     QPoint(handle.width() // 2, handle.height() // 2),
     QPoint(handle.width() // 2 + 160, handle.height() // 2))
settle()
after = splitter.sizes()
check(f"dragging it right widens the left pane ({before} -> {after})",
      after[0] > before[0] + 100)
check("the total width is conserved",
      abs(sum(after) - sum(before)) <= 2)
check("dragging resizes, it does not reorder",
      splitter.indexOf(week.nav_panel) == 0)
check("both panes are still visible", min(after) > 0)
# and back
drag(handle,
     QPoint(handle.width() // 2, handle.height() // 2),
     QPoint(handle.width() // 2 - 160, handle.height() // 2))
settle()
check(f"and dragging it back narrows it again {splitter.sizes()}",
      splitter.sizes()[0] < after[0] - 100)

# ==================================================== [3] tabs drag to reorder
print("\n--- [3] the tabs reorder by dragging ---")
bar = win.main_tabs.tabBar()
check("the tab bar accepts moves", win.main_tabs.isMovable())
start_order = win._current_tab_order()
check(f"default key order {start_order}",
      start_order == ["daily", "week", "year", "projects"])

# Drag the first tab past the second. Qt reorders mid-drag once the pointer
# passes the neighbour's midpoint, so the release position is what matters.
drag(bar, bar.tabRect(0).center(), bar.tabRect(2).center())
settle()
after_drag = win._current_tab_order()
check(f"dragging the first tab to the right moved it {after_drag}",
      after_drag != start_order and after_drag.index("daily") > 0)
check("the labels travelled with the pages",
      win.main_tabs.tabText(win.main_tabs.indexOf(win.week_calendar))
      == "Weekly Schedule"
      and win.main_tabs.tabText(win.main_tabs.indexOf(win.projects_widget))
      == "Projects")
check("the page widgets are the same objects, not rebuilt",
      win.main_tabs.indexOf(win.week_calendar) >= 0
      and win.main_tabs.indexOf(win.projects_widget) >= 0)

# ===================================== [7] reordering changed nothing but order
print("\n--- [7] reordering is presentation only ---")
DATE = "2026-09-15"
win.selected_date.set(DATE)
settle()
entry_event = win.db.create_event(date=DATE, start_minute=600, end_minute=660,
                                  title="Standup")
todo = win.db.add_todo(DATE, "Write the report")
win.db.set_setting("probe", "value")
win.editor.text_edit.setHtml("<p>a line of writing</p>")
win._save_current_entry()
settle()
before_ids = (entry_event.id, todo.id)
drag(bar, bar.tabRect(0).center(), bar.tabRect(2).center())
settle()
check("the calendar event survived a reorder",
      win.db.get_event(before_ids[0]) is not None)
check("the ToDo survived a reorder",
      win.db.get_todo(before_ids[1]) is not None)
check("the journal entry survived a reorder",
      "a line of writing" in (win.db.get_entry(DATE).body_text or ""))
check("the selected date did not move", win.selected_date.value == DATE)
check("switching to a moved tab still works",
      (win.main_tabs.setCurrentWidget(win.week_calendar) or True)
      and win.main_tabs.currentWidget() is win.week_calendar)

# The handoffs that used to say setCurrentIndex(0). With Daily Notes dragged
# away from position 0, an index-based switch lands on the wrong workspace —
# and it does so silently, because the date really was selected underneath.
print("\n--- the handoffs follow the tab, not position 0 ---")
win._apply_tab_order(["week", "projects", "daily"])
settle()
check("Daily Notes is no longer the leftmost tab",
      win._current_tab_order().index("daily") == 2)
win.main_tabs.setCurrentWidget(win.week_calendar)
settle()
win._on_open_day_from_week("2026-09-14")
settle()
check("clicking a day name in Week View still opens Daily Notes",
      win.main_tabs.currentWidget() is win.daily_splitter)
check("...on the day that was clicked", win.selected_date.value == "2026-09-14")
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
win._on_date_link_activated(DATE)
settle()
check("following an internal date link still opens Daily Notes",
      win.main_tabs.currentWidget() is win.daily_splitter)
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
win._go_back()
settle()
check("Back still opens Daily Notes",
      win.main_tabs.currentWidget() is win.daily_splitter)
win._apply_tab_order(["daily", "projects", "week"])
settle()

# ============================================== [4] the order survives a restart
print("\n--- [4] the tab order persists ---")
win._apply_tab_order(["week", "daily", "projects"])
settle()
win._on_tab_moved(0, 0)                      # what a real drop triggers
saved = win.db.get_setting(win.TAB_ORDER_SETTING)
check(f"stored as keys, not indices ({saved!r})", saved == "week,daily,projects,year")
check("stored in the settings table, not in journal/project/calendar data",
      win.db.get_setting(win.TAB_ORDER_SETTING) is not None)
win._save_window_state()
win.close()
del win

win2 = MainWindow()
win2.resize(1280, 820)
win2.show()
settle()
check(f"the order came back {win2._current_tab_order()}",
      win2._current_tab_order() == ["week", "daily", "projects", "year"])
check("the labels came back with it",
      [win2.main_tabs.tabText(i) for i in range(win2.main_tabs.count())]
      == ["Weekly Schedule", "Daily Jorts", "Projects", "Yearly Calendar"])
check("[6] and the data is still there",
      win2.db.get_entry(DATE) is not None
      and "a line of writing" in (win2.db.get_entry(DATE).body_text or "")
      and win2.db.get_setting("probe") == "value")
check("[1] the navigator is still the left pane after a restart",
      win2.week_calendar.splitter.indexOf(win2.week_calendar.nav_panel) == 0)

# ------------------------------------------- forward compatibility of the list
print("\n--- a saved order written by another version ---")
check("an unknown key is ignored, the rest still apply",
      (win2._apply_tab_order(["week", "search", "daily", "projects"]) or True)
      and win2._current_tab_order() == ["week", "daily", "projects", "year"])
check("a missing key leaves that tab after the ones that were named",
      (win2._apply_tab_order(["week"]) or True)
      and win2._current_tab_order() == ["week", "daily", "projects", "year"])
check("a later tab would land after the user's own arrangement",
      (win2._apply_tab_order(["projects", "daily"]) or True)
      and win2._current_tab_order() == ["projects", "daily", "week", "year"])
check("a repeated key does not duplicate or drop a tab",
      (win2._apply_tab_order(["daily", "daily", "week", "projects"]) or True)
      and sorted(win2._current_tab_order()) == ["daily", "projects", "week", "year"]
      and win2._current_tab_order() == ["daily", "week", "projects", "year"])
check("an empty saved value leaves the order alone",
      (win2._apply_tab_order([]) or True)
      and win2._current_tab_order() == ["daily", "week", "projects", "year"])
check("applying a saved order does not write it back over itself",
      win2.db.get_setting(win2.TAB_ORDER_SETTING) == "week,daily,projects,year")

# ------------------------------------ a database written before this change
print("\n--- [6] a pre-change database opens normally ---")
db = win2.db
db.set_setting("splitter_week", "b2d1ZQ==")      # the retired key, as it was
for key in (win2.TAB_ORDER_SETTING,):
    db._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    db._conn.commit()
win2.close()
del win2

win3 = MainWindow()
win3.resize(1280, 820)
win3.show()
settle()
check("no saved tab order means the normal default order",
      win3._current_tab_order() == ["daily", "week", "year", "projects"])
check("the retired splitter key is not read",
      "week" not in [k for k, _s in win3._named_splitters()])
check("the retired row is simply left alone",
      win3.db.get_setting("splitter_week") == "b2d1ZQ==")
check("the entry written before the change is unchanged",
      "a line of writing" in (win3.db.get_entry(DATE).body_text or ""))
check("the event written before the change is unchanged",
      any(e.title == "Standup" for e in win3.db.get_events(DATE)))
check("the ToDo written before the change is unchanged",
      any(t.text == "Write the report" for t in win3.db.get_todos(DATE)))
win3.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL ROUND-31 UI LAYOUT CHECKS PASSED")
