"""Group 3, stage 3C — workspace names and order, Weekly Schedule navigation,
the week following the selected date, the Yearly Calendar, month and year
titles, the today circle, and Day Markers vs entry indicators.

Acceptance criteria 24-36 of the Group 3 plan are marked [24]..[36]. Real
mouse and key events on the real window; results read back from the
database and, for restart criteria, from a new MainWindow (FP-9, FP-5).
"""
import os
import pathlib
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g3-nav-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.event_render import readable_text_color  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.theme import PRESETS, scheme_to_json  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=40):
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


def click(widget, pos: QPoint, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else button
        app.sendEvent(widget, QMouseEvent(kind, QPointF(pos), QPointF(pos), button, buttons, modifiers))
    settle()


def key(widget, k, modifiers=Qt.NoModifier):
    app.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, modifiers))
    settle()


TODAY = QDate.currentDate().toString("yyyy-MM-dd")
win = MainWindow()
win.resize(1400, 900)
win.show()
settle(100)
db = win.db
week = win.week_calendar
year = win.year_calendar

# ---------------------------------------------------------------- [24]
print("\n--- [24] the four workspaces, named and ordered as Master Spec §4 says ---")
labels = [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
check(f"Daily Jorts · Weekly Schedule · Yearly Calendar · Projects {labels}",
      labels == ["Daily Jorts", "Weekly Schedule", "Yearly Calendar", "Projects"])
check("tabs are addressed by key", win._current_tab_order() == ["daily", "week", "year", "projects"])

# ---------------------------------------------------------------- [29]
print("\n--- [29] no 'Show This Day's Week' ---")
texts = [a.text().replace("&", "") for a in win.menuBar().actions()]
all_actions = [a.text() for m in win.menuBar().findChildren(type(win.menuBar().actions()[0]))
               for a in [m]]
# Since batch 4A the View menu exists (Master Spec §51.2); what [29] checks
# is that it holds no "Show This Day's Week".
view_menu = next((a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "View"), None)
check(f"the View menu does not hold it {texts}",
      view_menu is not None and not any("Show This Day" in a.text() for a in view_menu.actions()))
check("no action anywhere with that name",
      not any("Show This Day" in t for t in all_actions + texts))

# ------------------------------------------------------------ [26] [27] [28]
print("\n--- [26] [27] [28] Weekly Schedule navigation ---")
win.main_tabs.setCurrentWidget(week)
settle()
win.selected_date.set("2026-11-04")                    # a Wednesday
settle()
check("[27] the selected date's Sunday–Saturday week is shown",
      week.visible_week.dates() == [f"2026-11-{d:02d}" for d in range(1, 8)])
selected_before = win.selected_date.value
buttons = {b.text(): b for b in week.findChildren(type(week.week_back_btn))}
QTest.mouseClick(buttons["Day ▶"], Qt.LeftButton)
settle()
check("[26] Day ▶ slides one day (Mon–Sun)", week.visible_week.start == "2026-11-02")
QTest.mouseClick(buttons["Week ▶"], Qt.LeftButton)
settle()
check("[26] Week ▶ steps exactly seven days and keeps the alignment",
      week.visible_week.start == "2026-11-09")
week.setFocus(Qt.OtherFocusReason)
key(week, Qt.Key_Left, Qt.ShiftModifier)
check("[26] Shift+Left steps back a week", week.visible_week.start == "2026-11-02")
key(week, Qt.Key_Left)
check("[26] Left slides back a day", week.visible_week.start == "2026-11-01")
key(week, Qt.Key_Right, Qt.ShiftModifier)
check("[26] Shift+Right steps forward a week", week.visible_week.start == "2026-11-08")
QTest.mouseClick(buttons["◀ Week"], Qt.LeftButton)
QTest.mouseClick(buttons["◀ Day"], Qt.LeftButton)
settle()
check("[26] ◀ Week and ◀ Day together: back 8 days", week.visible_week.start == "2026-10-31")

# The way a user actually does it: click in the week grid (or a button),
# then press the keys — delivered to whatever has keyboard focus.
week.timeline.grab()
QTest.mouseClick(week.timeline, Qt.LeftButton, Qt.NoModifier,
                 QPoint(week.timeline.width() // 2, int(week.prefs.hour_height * 3)))
settle()
QTest.keyClick(app.focusWidget() or week, Qt.Key_Right)
settle()
check("[26] after clicking in the grid, Right still slides a day",
      week.visible_week.start == "2026-11-01", week.visible_week.start)
QTest.keyClick(app.focusWidget() or week, Qt.Key_Right, Qt.ShiftModifier)
settle()
check("[26] ...and Shift+Right steps a week", week.visible_week.start == "2026-11-08")
QTest.mouseClick(buttons["◀ Day"], Qt.LeftButton)
settle()
QTest.keyClick(app.focusWidget() or week, Qt.Key_Left, Qt.ShiftModifier)
settle()
check("[26] after clicking a navigation button, the keys still work",
      week.visible_week.start == "2026-10-31", week.visible_week.start)
check("[27] none of that changed the selected date", win.selected_date.value == selected_before)
nav = week.navigator.calendar
check("[28] the navigator highlights exactly the seven days shown",
      nav._highlight_dates == set(week.visible_week.dates()))
QTest.mouseClick(buttons["This week"], Qt.LeftButton)
settle()
today_q = QDate.currentDate()
sunday = today_q.addDays(-(today_q.dayOfWeek() % 7)).toString("yyyy-MM-dd")
check("[26] This week gives the Sunday–Saturday week of today", week.visible_week.start == sunday)
week._on_navigator_date_clicked(QDate(2026, 12, 16))
check("[26] a navigator click gives that day's Sunday–Saturday week",
      week.visible_week.start == "2026-12-13")
check("[27] ...and does not select the day", win.selected_date.value == selected_before)
win._on_date_link_activated("2027-02-10")
settle()
check("[27] a date link elsewhere moves the week to that date's week",
      week.visible_week.start == "2027-02-07")
win.main_tabs.setCurrentWidget(week)
settle()
check("[28] and the navigator follows to its month", (nav.yearShown(), nav.monthShown()) == (2027, 2))

# ---------------------------------------------------------------- [34]
print("\n--- [34] one month title, shared by every view ---")
win.selected_date.set("2026-09-10")
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
field = win.calendar_panel.month_title
field.setFocus()
field.selectAll()
QTest.keyClicks(field, "The move to Santa Barbara")
QTest.keyClick(field, Qt.Key_Return)
settle()
check("stored once, for September 2026",
      db.period_titles("month") == {"2026-09": "The move to Santa Barbara"})
check("the Weekly Schedule navigator shows the same title at once",
      week.navigator.month_title.text() == "The move to Santa Barbara")
week.navigator.month_title.setText("Moving month")
week.navigator.month_title.editingFinished.emit()
settle()
check("edited there, Daily Jorts shows the new value",
      field.text() == "Moving month" and db.get_period_title("month", "2026-09") == "Moving month")
check("one row, not one per view",
      db._conn.execute("SELECT COUNT(*) AS n FROM period_titles").fetchone()["n"] == 1)
field.setText("x" * 120)
check("the field stops at 80 characters", len(field.text()) == 80)
field.editingFinished.emit()
check("and so does storage", len(db.get_period_title("month", "2026-09")) == 80)
win.calendar_panel.calendar.showNextMonth()
settle()
check("paging to October shows October's (empty) title", field.text() == "")
win.calendar_panel.calendar.showPreviousMonth()
settle()
field.setText("")
field.editingFinished.emit()
check("clearing removes the row", db.period_titles("month") == {})
field.setText("The move to Santa Barbara")
field.editingFinished.emit()

# ---------------------------------------------------------------- [36]
print("\n--- [36] markers and events are never an entry dot ---")
db.upsert_entry("2026-09-10", body_md="<p>Written</p>", body_format="html", body_text="Written")
marker = db._conn.execute("SELECT id, color FROM day_markers LIMIT 1").fetchone()
db.upsert_entry("2026-09-11", tag=str(marker["id"]), tag_color=marker["color"])
db.create_event("2026-09-12", 600, 660, title="Only an event")
win._refresh_calendar_marks()
settle()
cal = win.calendar_panel.calendar
check("month grid: the written day has the entry dot", "2026-09-10" in cal._entry_dates)
check("month grid: the marker-only day has its marker and no entry dot",
      cal._tag_colors.get("2026-09-11") == marker["color"] and "2026-09-11" not in cal._entry_dates)
check("month grid: the events-only day has the hollow mark, not the entry dot",
      "2026-09-12" in cal._other_content_dates and "2026-09-12" not in cal._entry_dates)

# ------------------------------------------------------------ [30] [31] [36]
print("\n--- [30] [31] the Yearly Calendar ---")
db.set_period_title("year", "2026", "A year of trap states")
win.main_tabs.setCurrentWidget(year)
settle(80)
check("[30] twelve months, January to December",
      [g.month for g in year.months] == list(range(1, 13)) and all(g.year == 2026 for g in year.months))
sept = year.months[8]
check("[30] September shows its entry dot and marker, from the same query",
      "2026-09-10" in sept.entry_dates and sept.tag_colors.get("2026-09-11") == marker["color"])
check("[36] ...and no entry dot on the marker-only or events-only day",
      "2026-09-11" not in sept.entry_dates and "2026-09-12" not in sept.entry_dates
      and "2026-09-12" in sept.other_dates)
check("[30] September shows its month title", sept.title == "The move to Santa Barbara")
check("[31] the year title is in the header", year.year_title.text() == "A year of trap states")
next_btn = [b for b in year.findChildren(type(week.week_back_btn)) if b.text() == "▶"][0]
prev_btn = [b for b in year.findChildren(type(week.week_back_btn)) if b.text() == "◀"][0]
QTest.mouseClick(next_btn, Qt.LeftButton)
settle()
check("[31] ▶ goes from 2026 to 2027: December 2026 → January 2027",
      year.visible_year.value == 2027 and year.months[0].year == 2027 and year.months[0].month == 1)
check("[31] the other year has its own (empty) title", year.year_title.text() == "")
QTest.mouseClick(prev_btn, Qt.LeftButton)
QTest.mouseClick(prev_btn, Qt.LeftButton)
settle()
check("[31] ◀ twice lands on 2025", year.visible_year.value == 2025 and year.months[11].year == 2025)
check("[27] browsing years does not change the selected date", win.selected_date.value == "2026-09-10")
year.year_title.setText("Before")
year.year_title.editingFinished.emit()
QTest.mouseClick(next_btn, Qt.LeftButton)
settle()

# ---------------------------------------------------------------- [32]
print("\n--- [32] navigating from the Yearly Calendar ---")


def cell_center(grid, date):
    grid.grab()     # paints, which records the cell rectangles
    for rect, key_ in grid._cells:
        if key_ == date:
            return rect.center()
    raise AssertionError(date)


oct_grid = year.months[9]
click(oct_grid, cell_center(oct_grid, "2026-10-15"))
check("left click: Daily Jorts in front, on that date",
      win.main_tabs.currentWidget() is win.daily_splitter and win.selected_date.value == "2026-10-15")
win.main_tabs.setCurrentWidget(year)
settle()
click(year.months[10], cell_center(year.months[10], "2026-11-18"), button=Qt.RightButton)
check("right click: Weekly Schedule in front, on the week containing the day",
      win.main_tabs.currentWidget() is week and week.visible_week.dates()[0] == "2026-11-15"
      and "2026-11-18" in week.visible_week.dates())
check("...and the selected date is that specific day", win.selected_date.value == "2026-11-18")
win.main_tabs.setCurrentWidget(year)
settle()
click(year.months[11], cell_center(year.months[11], "2026-12-02"), modifiers=Qt.ShiftModifier)
check("Shift-click does the same",
      win.main_tabs.currentWidget() is week and win.selected_date.value == "2026-12-02"
      and week.visible_week.start == "2026-11-29")

# ------------------------------------------------------------ [35] today
print("\n--- [35] today's filled circle ---")
win.selected_date.set(TODAY)
win.main_tabs.setCurrentWidget(year)
settle(80)
for scheme_name in ("Light", "Dark", "Sepia") if "Sepia" in PRESETS else ("Light", "Dark"):
    scheme = PRESETS[scheme_name]
    fill = QColor(scheme.accent)
    ink = readable_text_color(fill)
    lum = lambda c: 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()  # noqa: E731
    check(f"{scheme_name}: the number on the circle has contrast ({ink.name()} on {fill.name()})",
          abs(lum(ink) - lum(fill)) > 0.35)
for scheme_name in ("Light", "Dark"):
    db.set_setting("color_scheme", scheme_to_json(PRESETS[scheme_name]))
    win._apply_settings()
    win._refresh_calendar_marks()
    win.main_tabs.setCurrentWidget(year)
    settle(60)
    grid = year.months[QDate.currentDate().month() - 1]
    image = grid.grab().toImage()
    center = cell_center(grid, TODAY)
    ring = image.pixelColor(center.x() - QFontMetrics(grid.font()).horizontalAdvance("30") // 2 - 1,
                            center.y())
    accent = QColor(PRESETS[scheme_name].accent)
    check(f"{scheme_name}: the Yearly Calendar paints today's cell with the accent circle",
          abs(ring.red() - accent.red()) < 30 and abs(ring.green() - accent.green()) < 30
          and abs(ring.blue() - accent.blue()) < 30, f"{ring.name()} vs {accent.name()}")
    other = [k for _r, k in grid._cells if k != TODAY][0]
    other_center = cell_center(grid, other)
    plain = image.pixelColor(other_center.x() - QFontMetrics(grid.font()).horizontalAdvance("30") // 2 - 1,
                             other_center.y())
    check(f"{scheme_name}: other days have no circle", plain.name() != ring.name())

print("\n--- [35] today's circle stays visible on the selected (highlighted) cell ---")
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont, QImage, QPainter  # noqa: E402
from app.calendar_widget import paint_day_marks  # noqa: E402
for scheme_name in ("Light", "Dark"):
    scheme = PRESETS[scheme_name]
    image = QImage(60, 40, QImage.Format_ARGB32)
    image.fill(QColor("#308cc6"))          # Qt's selection highlight, close to the accent
    painter = QPainter(image)
    paint_day_marks(painter, QRect(0, 0, 60, 40), scheme, day_text="24", font=QFont(),
                    is_today=True)
    painter.end()
    from app.calendar_widget import today_circle_diameter  # noqa: E402  (the one size rule)
    diameter = max(8, min(today_circle_diameter(QFont()), 58, 38))
    left_edge = QRect(0, 0, 60, 40).center().x() - diameter // 2
    # Only the circle's own edge — well away from the day number's pixels.
    row = [image.pixelColor(x, 20) for x in range(max(0, left_edge - 1), left_edge + 3)]
    panel = QColor(scheme.panel)
    ring = [c for c in row if abs(c.red() - panel.red()) < 40 and abs(c.green() - panel.green()) < 40
            and abs(c.blue() - panel.blue()) < 40]
    check(f"{scheme_name}: a ring in the pane colour separates the circle from the selection",
          len(ring) >= 2, f"{len(ring)} ring pixels")

# ---------------------------------------------------------------- [33]
print("\n--- [33] the year stays readable at any font and window size ---")
win.selected_date.set("2026-06-15")
for point in (9, 14, 20):
    for width in (900, 1600):
        db.set_setting("ui_font_size", str(point))
        win._apply_settings()
        win.resize(width, 900)
        win.main_tabs.setCurrentWidget(year)
        settle(120)
        where = f"{point}pt @ {width}px"
        grids = year.months
        painted = [g.grab() for g in grids]
        fm = QFontMetrics(grids[0].font())
        cells_ok = all(fm.horizontalAdvance(k[-2:].lstrip("0")) <= r.width()
                       and fm.height() <= r.height()
                       and 0 <= r.left() and r.right() <= g.width() and r.bottom() <= g.height()
                       for g in grids for r, k in g._cells)
        check(f"{where}: every day fits inside its cell and its month", cells_ok)
        check(f"{where}: no month is squeezed below its size",
              all(g.width() >= g.sizeHint().width() and g.height() >= g.sizeHint().height()
                  for g in grids))
        rects = [g.geometry() for g in grids]
        overlap = any(rects[i].intersects(rects[j]) for i in range(12) for j in range(i + 1, 12))
        check(f"{where}: months don't overlap ({year._columns} per row)", not overlap)
        check(f"{where}: no sideways scrolling needed",
              year.grid_host.width() <= year.scroll.viewport().width() + 1
              and not year.scroll.horizontalScrollBar().isVisible())
db.set_setting("ui_font_size", "10")
win._apply_settings()

# ---------------------------------------------------------------- restart
print("\n--- [25] [31] [34] after a restart ---")
win._apply_tab_order(["projects", "daily", "week"])
win._on_tab_moved(0, 0)
win.close()
settle()
win2 = MainWindow()
win2.show()
settle(100)
check("[25] a saved custom order keeps the user's arrangement, Yearly after it",
      win2._current_tab_order() == ["projects", "daily", "week", "year"],
      win2._current_tab_order())
check("[31] year titles survive", win2.db.get_period_title("year", "2026") == "A year of trap states"
      and win2.db.get_period_title("year", "2025") == "Before")
win2.selected_date.set("2026-09-03")
settle()
check("[34] the month title survives and is shown",
      win2.calendar_panel.month_title.text() == "The move to Santa Barbara")
win2.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
