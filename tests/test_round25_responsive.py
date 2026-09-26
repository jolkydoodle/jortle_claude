"""Round 25 — whole-application responsive layout (Parts 42 and 43).

Sweeps realistic window sizes against realistic application font sizes and
checks, on every combination and every tab, the specific failures the spec
lists: clipped numeric controls, calendar headers cut off, event text
escaping its block, panes with an excessive minimum width, and columns that
don't line up with their own headers.

This is the test that would have caught the things screenshots caught by
eye in earlier rounds, so it runs on every size rather than the one the
window happens to open at.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r25r-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QSpinBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.day_calendar_model import ColumnGeometry  # noqa: E402
from app.main_window import MainWindow  # noqa: E402

failures = []


def check(label, cond):
    if not cond:
        print("  FAIL  " + label)
        failures.append(label)


WINDOW_SIZES = [(1000, 700), (1280, 800), (1600, 1000)]
FONT_SIZES = [9, 12, 16, 20]

win = MainWindow()
win.show()
for _ in range(3):
    app.processEvents()

# Something on the calendar in every view, so the checks have real content.
dates = win.week_calendar.visible_week.dates()
win.db.create_event(date=dates[1], start_minute=9 * 60, end_minute=10 * 60,
                    title="Standup with the whole team")
win.db.create_event(date=dates[1], start_minute=9 * 60 + 30, end_minute=11 * 60,
                    title="Overlapping review")
win.db.create_event(date=dates[4], start_minute=13 * 60, end_minute=14 * 60 + 30,
                    title="Quarterly planning session", color="#b5651d", opacity=45)
win.db.create_event(date=dates[3], start_minute=15 * 60, end_minute=15 * 60 + 15,
                    title="Quick sync")
win.db.create_event(date=dates[2], start_minute=0, end_minute=0, all_day=True,
                    title="Dentist appointment")
win.selected_date.set(dates[1])

marker = win.db.create_day_marker("Quarterly deep-work research retreat", "#2e8b57")
win.db.upsert_entry(dates[2], tag=str(marker.id), tag_color=marker.color)
win.calendar_panel.tag_picker.reload_markers()


def settle():
    for _ in range(4):
        app.processEvents()


for width, height in WINDOW_SIZES:
    for point_size in FONT_SIZES:
        where = f"{width}x{height} @{point_size}pt"
        win.resize(width, height)
        win.db.set_setting("ui_font_size", str(point_size))
        win._apply_settings()
        settle()

        # ---------------------------------------------------- Daily Journal
        win.main_tabs.setCurrentWidget(win.daily_splitter)
        settle()
        win._refresh_calendar_marks()
        settle()

        calendar = win.calendar_panel.calendar
        check(f"{where}: month grid keeps all its week rows "
              f"({calendar.height()} >= {calendar.minimumSizeHint().height()})",
              calendar.height() >= calendar.minimumSizeHint().height() - 1)
        check(f"{where}: the calendar pane doesn't demand an excessive minimum "
              f"({win.calendar_panel.minimumSizeHint().width()}px)",
              win.calendar_panel.minimumSizeHint().width() <= width * 0.45)
        check(f"{where}: a long marker name doesn't widen the pane "
              f"({win.calendar_panel.tag_picker.minimumSizeHint().width()}px)",
              win.calendar_panel.tag_picker.minimumSizeHint().width() <= width * 0.35)

        # Numeric controls must show their whole value — they can't elide.
        for spin in win.editor.findChildren(QSpinBox):
            if spin.isVisible():
                check(f"{where}: numeric control not clipped "
                      f"({spin.width()} >= {spin.sizeHint().width()})",
                      spin.width() >= spin.sizeHint().width() - 1)

        # ---------------------------------------------------------- Projects
        win.main_tabs.setCurrentWidget(win.projects_widget)
        settle()
        projects = win.projects_widget
        check(f"{where}: projects panes both have usable width "
              f"{projects.splitter.sizes()}",
              min(projects.splitter.sizes()) > 100)

        # --------------------------------------------------- Weekly Calendar
        win.main_tabs.setCurrentWidget(win.week_calendar)
        settle()
        week = win.week_calendar
        week.refresh()
        settle()

        timeline = week.timeline
        header = week.header
        sizes = week.splitter.sizes()
        check(f"{where}: Week View keeps most of the width {sizes}",
              sizes[week.splitter.indexOf(week.week_panel)]
              > sizes[week.splitter.indexOf(week.nav_panel)])

        # The header must divide the same space into the same seven columns
        # as the timeline, or every label names the column next to it.
        timeline_geometry = timeline._geometry()
        header_geometry = header._geometry()
        for index in range(7):
            drift = abs(timeline_geometry.left(index) - header_geometry.left(index))
            check(f"{where}: header column {index} lines up with the timeline "
                  f"(drift {drift:.1f}px)", drift <= 2.0)

        # Guard against a vacuous pass: if nothing painted, the per-event
        # checks below would all "succeed" by having nothing to check.
        check(f"{where}: the week actually painted its events "
              f"({len(timeline._rects)} blocks)", len(timeline._rects) >= 4)
        check(f"{where}: the untimed strip is showing", week.all_day_row.isVisible())

        # Event blocks must stay inside their own column, at every size.
        for rect, piece in timeline._piece_rects:
            event_id, date = piece.id, piece.day
            column = timeline._dates.index(date)
            left, right = timeline_geometry.bounds(column)
            check(f"{where}: event {event_id} stays inside its day column "
                  f"([{rect.left()}, {rect.right()}] in [{left:.0f}, {right:.0f}])",
                  rect.left() >= left - 1 and rect.right() <= right + 1)
            check(f"{where}: event {event_id} has a usable height",
                  rect.height() >= 12)

        check(f"{where}: no horizontal scrollbar on the week timeline",
              not week.scroll.horizontalScrollBar().isVisible())
        check(f"{where}: the untimed strip stays a strip, not a panel "
              f"({week.all_day_row.height()}px of {height})",
              week.all_day_row.height() <= height * 0.35)

        navigator = week.navigator.calendar
        check(f"{where}: navigator month grid keeps its rows too "
              f"({navigator.height()} >= {navigator.minimumSizeHint().height()})",
              navigator.height() >= navigator.minimumSizeHint().height() - 1)

print(f"\nSwept {len(WINDOW_SIZES)} window sizes x {len(FONT_SIZES)} font sizes "
      f"x 3 workspaces.")

# The whole window has to fit a small laptop screen at a normal font.
win.db.set_setting("ui_font_size", "10")
win._apply_settings()
settle()
check(f"the window's own minimum fits a 1366x768 screen "
      f"({win.minimumSizeHint().width()}x{win.minimumSizeHint().height()})",
      win.minimumSizeHint().width() <= 1300 and win.minimumSizeHint().height() <= 740)

win.close()
print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES (first 10): {failures[:10]}"))
sys.exit(1 if failures else 0)
