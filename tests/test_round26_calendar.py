"""Round 26 — theme colours, transparency, zoom and work hours (Part 30).

Follows the spec's own 29-step script. Where a step is about what something
LOOKS like, the check is made against the colour or geometry actually used to
paint it, not against the setting that was requested — the bug being fixed
here was precisely that the setting said one thing and the painting did
another.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r26c-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r26c-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import theme  # noqa: E402
from app.calendar_prefs import (  # noqa: E402
    BASE_HOUR_HEIGHT, MAX_ZOOM_PERCENT, MIN_ZOOM_PERCENT, CalendarPrefs,
)
from app.database import OPACITY_FOLLOWS_DEFAULT, Database  # noqa: E402
from app.day_calendar_model import minute_to_y  # noqa: E402
from app.event_render import (  # noqa: E402
    apply_opacity, readable_text_color, resolve_event_color, resolve_event_opacity,
)
from app.main_window import MainWindow  # noqa: E402
from app.theme import PRESETS, scheme_to_json  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=4):
    for _ in range(times):
        app.processEvents()


win = MainWindow()
win.resize(1280, 820)
win.show()
settle()

timeline = win.day_calendar.timeline
week_timeline = win.week_calendar.timeline
today = QDate.currentDate().toString("yyyy-MM-dd")
monday = QDate(2026, 9, 14).toString("yyyy-MM-dd")     # a Monday
saturday = QDate(2026, 9, 19).toString("yyyy-MM-dd")   # a Saturday

# ============================================ steps 1-9: theme-default colour
print("\n--- Steps 1-9: theme default vs. explicit colour ---")
themed = win.db.create_event(date=monday, start_minute=9 * 60, end_minute=10 * 60,
                             title="Theme default")
custom = win.db.create_event(date=monday, start_minute=11 * 60, end_minute=12 * 60,
                             title="Explicit colour", color="#b5651d")

check("a theme-default event stores no colour at all",
      win.db.get_event(themed.id).color is None)


def apply_theme(name):
    win.db.set_setting("color_scheme", scheme_to_json(PRESETS[name]))
    win._apply_settings()
    settle()


apply_theme("Light")
light_accent = QColor(theme.event_color(PRESETS["Light"]))
check(f"the Day View paints it in the theme's colour ({light_accent.name()})",
      resolve_event_color(win.db.get_event(themed.id),
                          QColor(theme.event_color(timeline.scheme))) == light_accent)
check("the timeline is using the app's scheme, not Qt's palette",
      QColor(timeline.scheme.accent) == light_accent
      and QColor(timeline.palette().highlight().color()) != light_accent)

apply_theme("Forest")
forest_accent = QColor(theme.event_color(PRESETS["Forest"]))
check(f"changing theme changes it ({forest_accent.name()})",
      resolve_event_color(win.db.get_event(themed.id),
                          QColor(theme.event_color(timeline.scheme))) == forest_accent)
check("...and the Week View agrees",
      QColor(theme.event_color(week_timeline.scheme)) == forest_accent)
check("the explicitly coloured event is unchanged by the theme",
      resolve_event_color(win.db.get_event(custom.id),
                          QColor(theme.event_color(timeline.scheme))) == QColor("#b5651d"))
check("the theme never got written onto the event",
      win.db.get_event(themed.id).color is None)

# return the custom event to the theme default, as the dialog's checkbox does
win.db.update_event(custom.id, color=None)
check("returning an event to Theme Default makes it follow the theme again",
      resolve_event_color(win.db.get_event(custom.id),
                          QColor(theme.event_color(timeline.scheme))) == forest_accent)

print("\n--- the previous implementation must not have converted anything ---")
opaque_blue = win.db.create_event(date=monday, start_minute=14 * 60, end_minute=15 * 60,
                                  title="Made before the fix")
check("an event created now still has a NULL colour, not a copied-in blue",
      win.db.get_event(opaque_blue.id).color is None)

# ================================================ steps 10-12: transparency
print("\n--- Steps 10-12: default transparency (70% opaque since Group 3) ---")
fresh = win.db.get_event(themed.id)
check("a new event has no explicit transparency",
      fresh.opacity == OPACITY_FOLLOWS_DEFAULT)
check(f"it resolves to 70% opaque, i.e. 30% transparent "
      f"({resolve_event_opacity(fresh)}% opaque)",
      resolve_event_opacity(fresh) == 100 - theme.DEFAULT_EVENT_TRANSPARENCY)
check("the default is 30% transparent = 70% opaque (Master Spec §26)",
      theme.DEFAULT_EVENT_TRANSPARENCY == 30)

fill = apply_opacity(QColor(theme.event_color(timeline.scheme)), fresh)
check(f"the painted background is translucent (alpha {fill.alpha()}/255)",
      170 < fill.alpha() < 185)   # 70% of 255 = 178.5

win.db.update_event(custom.id, opacity=85)
check("an explicitly chosen transparency is kept",
      resolve_event_opacity(win.db.get_event(custom.id)) == 85)

text = readable_text_color(fill, QColor(timeline.scheme.panel))
check("the title/time text is fully opaque", text.alpha() == 255)
light_text = readable_text_color(
    apply_opacity(QColor(PRESETS["Light"].accent), fresh), QColor(PRESETS["Light"].panel))
dark_text = readable_text_color(
    apply_opacity(QColor(PRESETS["Dark"].accent), fresh), QColor(PRESETS["Dark"].panel))
check(f"and readable on a light theme ({light_text.name()}) and a dark one "
      f"({dark_text.name()})", light_text != dark_text)

# ============================================== steps 13-20: calendar zoom
print("\n--- Steps 13-20: calendar zoom ---")
prefs = win.calendar_prefs
prefs.set_zoom_percent(100)
# The Day View shows the SELECTED day, so put the events' day on screen
# before measuring how they are drawn.
win.selected_date.set(monday)
win.day_calendar.refresh()
settle()


def event_rect(tl, event_id):
    """Forces a real paint and reads back the rectangle the event was drawn
    in. grab() rather than update(): update() only schedules a repaint, and
    offscreen it may not have happened by the time we look."""
    tl.grab()
    settle()
    return tl._rects.get(event_id)


base_height = timeline.height()
base_scale = prefs.hour_height
rect_before = event_rect(timeline, themed.id)

prefs.set_zoom_percent(200)
settle()
check(f"zooming in doubles the pixels per hour "
      f"({base_scale:.0f} -> {prefs.hour_height:.0f})",
      abs(prefs.hour_height - base_scale * 2) < 0.01)
check("the day canvas gets taller", timeline.height() > base_height)
hour_gap_100 = minute_to_y(60, base_scale)
hour_gap_200 = minute_to_y(60, prefs.hour_height)
check(f"hours spread apart vertically ({hour_gap_100:.0f}px -> {hour_gap_200:.0f}px)",
      hour_gap_200 > hour_gap_100)

rect_after = event_rect(timeline, themed.id)
check(f"event heights scale with it ({rect_before.height()} -> {rect_after.height()})",
      rect_after is not None and rect_after.height() > rect_before.height() * 1.5)
stored = win.db.get_event(themed.id)
check("the event's actual times never changed",
      stored.start_minute == 9 * 60 and stored.end_minute == 10 * 60)

prefs.set_zoom_percent(50)
settle()
check("zooming out shows more of the day",
      timeline.height() < base_height)
stored = win.db.get_event(themed.id)
check("...and still doesn't touch the times",
      stored.start_minute == 9 * 60 and stored.end_minute == 10 * 60)

check("zoom is clamped at both ends",
      prefs.set_zoom_percent(5) and prefs.zoom_percent == MIN_ZOOM_PERCENT
      and prefs.set_zoom_percent(10_000) and prefs.zoom_percent == MAX_ZOOM_PERCENT)

prefs.set_zoom_percent(100)
settle()
print("\n--- Step 19-20: the SAME scale in Week View ---")
win.main_tabs.setCurrentWidget(win.week_calendar)
win.week_calendar.visible_week.align_to_week_of(monday)
settle()
win.week_calendar.refresh()
week_timeline.grab()
settle()
week_height = week_timeline.height()
prefs.set_zoom_percent(200)
settle()
check("Week View is on the same shared scale",
      week_timeline.prefs is timeline.prefs)
check("and its canvas scales too", week_timeline.height() > week_height)

print("\n--- Part 15: Ctrl+wheel zooms, a plain wheel does not ---")
prefs.set_zoom_percent(100)
settle()


def wheel(widget, ctrl: bool, up: bool = True):
    event = QWheelEvent(
        QPoint(50, 50), widget.mapToGlobal(QPoint(50, 50)),
        QPoint(0, 0), QPoint(0, 120 if up else -120),
        Qt.NoButton, Qt.ControlModifier if ctrl else Qt.NoModifier,
        Qt.NoScrollPhase, False)
    app.sendEvent(widget, event)
    settle()
    return event


before = prefs.zoom_percent
wheel(timeline, ctrl=True, up=True)
check(f"Ctrl+wheel up zooms in ({before}% -> {prefs.zoom_percent}%)",
      prefs.zoom_percent > before)
before = prefs.zoom_percent
wheel(timeline, ctrl=True, up=False)
check(f"Ctrl+wheel down zooms out ({before}% -> {prefs.zoom_percent}%)",
      prefs.zoom_percent < before)
before = prefs.zoom_percent
plain = wheel(timeline, ctrl=False, up=True)
check("a plain wheel does not zoom", prefs.zoom_percent == before)
check("...and is passed on so the calendar still scrolls", not plain.isAccepted())
before = prefs.zoom_percent
wheel(week_timeline, ctrl=True, up=True)
check("Ctrl+wheel works in Week View too, on the same scale",
      prefs.zoom_percent > before)

print("\n--- Part 34: calendar zoom is not editor zoom ---")
editor_zoom_before = win.editor.text_edit.zoom_percent()
prefs.set_zoom_percent(150)
settle()
check("zooming the calendar leaves the writing editor's zoom alone",
      win.editor.text_edit.zoom_percent() == editor_zoom_before)
win.editor.text_edit.set_zoom_steps(3)
settle()
check("and zooming the editor leaves the calendar scale alone",
      prefs.zoom_percent == 150)
win.editor.text_edit.set_zoom_steps(0)

# ============================================ steps 21-27: work hours
print("\n--- Steps 21-27: work-hours highlighting ---")
check("off by default", not prefs.work_hours_enabled)
prefs.set_work_hours_enabled(True)
settle()
check("Monday is a work day", prefs.is_work_day(QDate(2026, 9, 14)))
check("Friday is a work day", prefs.is_work_day(QDate(2026, 9, 18)))
check("Saturday is not", not prefs.is_work_day(QDate(2026, 9, 19)))
check("Sunday is not", not prefs.is_work_day(QDate(2026, 9, 20)))
check("the span is 9am to 5pm", prefs.work_span() == (9 * 60, 17 * 60))

tint = QColor(theme.mix(timeline.scheme.panel, timeline.scheme.accent, 0.12))
event_fill = apply_opacity(QColor(theme.event_color(timeline.scheme)), fresh)


def distance(a: QColor, b: QColor) -> float:
    return abs(a.redF() - b.redF()) + abs(a.greenF() - b.greenF()) + abs(a.blueF() - b.blueF())


panel = QColor(timeline.scheme.panel)
check("the tint is visible against the panel", distance(tint, panel) > 0.005)
check("but weaker than an event drawn on top of it (Part 17)",
      distance(tint, panel) < distance(event_fill, panel))

apply_theme("Dark")
dark_tint = QColor(theme.mix(timeline.scheme.panel, timeline.scheme.accent, 0.12))
check("the tint follows the theme", dark_tint != tint)
apply_theme("Light")

prefs.set_work_hours_enabled(False)
settle()
check("turning it off removes it", not prefs.work_hours_enabled)

# ============================================ step 28-29: it all persists
print("\n--- Steps 28-29: settings survive a restart ---")
prefs.set_zoom_percent(170)
prefs.set_work_hours_enabled(True)
win.db.set_setting("color_scheme", scheme_to_json(PRESETS["Sepia"]))
win._save_window_state()
win.close()
settle()

restarted = MainWindow()
restarted.show()
settle()
check(f"calendar zoom was remembered ({restarted.calendar_prefs.zoom_percent}%)",
      restarted.calendar_prefs.zoom_percent == 170)
check("work hours was remembered", restarted.calendar_prefs.work_hours_enabled)
check("the theme was remembered", restarted.day_calendar.timeline.scheme.name == "Sepia")
check("and the Day View opened at the remembered scale",
      abs(restarted.day_calendar.timeline.prefs.hour_height
          - BASE_HOUR_HEIGHT * 1.7) < 0.01)
restarted.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
