"""Round 27 — new-event transparency and live work-hours (Parts 8, 9, 23).

Two calendar bug reports, and both had a cause worth naming.

New events opened the editor at 0% transparency and then stored an explicit
opacity of 100, because `CalendarEvent.opacity` defaulted to 100 in the
dataclass and a "new event" is that constructor. So the sentinel that means
"the user has not chosen a transparency" could never actually reach a new
event, whatever the default was set to.

The work-hours toggle wrote its setting and nothing repainted, because both
timelines read the setting through a CalendarPrefs OBJECT and repaint from
its `changed` signal — and nothing was telling that object to re-read. The
setting was correct on disk the whole time, which is exactly why it looked
right after a restart.

So these tests go through the real dialog and the real Settings window,
rather than checking the stored values.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r27c-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r27c-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.calendar_prefs import WORK_HOURS_SETTING  # noqa: E402
from app.database import CalendarEvent, OPACITY_FOLLOWS_DEFAULT  # noqa: E402
from app.day_calendar import EventDialog  # noqa: E402
from app.event_render import resolve_event_opacity  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402
from app.theme import DEFAULT_EVENT_TRANSPARENCY  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=4):
    for _ in range(times):
        app.processEvents()


win = MainWindow()
win.show()
settle()
db = win.db

# =============================== Parts 8, 23.1-23.6: new-event transparency
print("\n--- Part 8: a new event opens at the default (70% opaque, Group 3) ---")
check(f"the application default is {DEFAULT_EVENT_TRANSPARENCY}% transparent",
      DEFAULT_EVENT_TRANSPARENCY == 30)
check("an unset event resolves to 70% opaque, i.e. 30% transparent",
      resolve_event_opacity(CalendarEvent(id=0, date="2026-05-01",
                                          start_minute=540, end_minute=600)) == 70)
check("the dataclass default is the follow-the-default sentinel, not a number",
      CalendarEvent(id=0, date="2026-05-01", start_minute=540,
                    end_minute=600).opacity == OPACITY_FOLLOWS_DEFAULT)

# Step 1-2: create from the Day View, exactly as a drag does.
win.selected_date.set("2026-05-01")
settle()
day_draft = CalendarEvent(id=0, date="2026-05-01", start_minute=540, end_minute=600)
day_dialog = EventDialog(day_draft, parent=win.day_calendar, is_new=True)
check("Day View: the dialog opens with 30 already in the Transparency field",
      day_dialog.opacity_slider.value() == 30)
check("Day View: the field is labelled as transparency, not opacity",
      "transparent" in day_dialog.opacity_value.text())
check("Day View: leaving it alone stores 'follow the default', not a literal 70",
      day_dialog._chosen_opacity() == OPACITY_FOLLOWS_DEFAULT)

# Step 3-4: and from the Week View, which uses the same dialog.
week_draft = CalendarEvent(id=0, date="2026-05-01", start_minute=780, end_minute=840)
week_dialog = EventDialog(week_draft, parent=win.week_calendar, is_new=True)
check("Week View: the dialog opens at 30 as well",
      week_dialog.opacity_slider.value() == 30)
check("both views open the same editor class",
      type(day_dialog) is type(week_dialog))

# What actually lands in the database when a new event is created.
new_id = db.create_event(date="2026-05-01", start_minute=540, end_minute=600,
                          title="Created with defaults",
                          opacity=day_dialog._chosen_opacity()).id
stored = db.get_event(new_id)
check("a newly created event stores the sentinel, not 100",
      stored.opacity == OPACITY_FOLLOWS_DEFAULT)
check("and paints at 70% opaque / 30% transparent",
      resolve_event_opacity(stored) == 70)

print("\n--- Parts 8, 23.5-23.6: an explicit value is authoritative ---")
custom_dialog = EventDialog(CalendarEvent(id=0, date="2026-05-01",
                                          start_minute=900, end_minute=960),
                            parent=win.day_calendar, is_new=True)
custom_dialog.opacity_slider.setValue(20)
chosen = custom_dialog._chosen_opacity()
check("moving the slider to 20% transparent stores 80% opacity", chosen == 80)
custom_id = db.create_event(date="2026-05-01", start_minute=900, end_minute=960,
                             title="Deliberately solid", opacity=chosen).id
check("it is stored as chosen", db.get_event(custom_id).opacity == 80)
check("and resolves to itself, not to the default",
      resolve_event_opacity(db.get_event(custom_id)) == 80)

reopened = EventDialog(db.get_event(custom_id), parent=win.day_calendar)
check("reopening the editor shows the chosen value back",
      reopened.opacity_slider.value() == 20)
check("and saving without touching it keeps the same number",
      reopened._chosen_opacity() == 80)

# The default changing must not disturb it — that is the whole point of
# storing "follow the default" separately from a number.
import app.theme as theme_module  # noqa: E402

original_default = theme_module.DEFAULT_EVENT_TRANSPARENCY
theme_module.DEFAULT_EVENT_TRANSPARENCY = 40
try:
    check("the explicit event ignores a change to the default",
          resolve_event_opacity(db.get_event(custom_id)) == 80)
    check("the default-following event follows it",
          resolve_event_opacity(db.get_event(new_id)) == 60)
finally:
    theme_module.DEFAULT_EVENT_TRANSPARENCY = original_default

print("\n--- text stays readable, whatever the background does ---")
from PySide6.QtGui import QColor  # noqa: E402
from app.event_render import apply_opacity, readable_text_color  # noqa: E402

faded = apply_opacity(QColor("#5aa0f0"), db.get_event(new_id))
check("the background carries the transparency", faded.alpha() < 255)
for surface in ("#ffffff", "#1e1f22"):
    text = readable_text_color(faded, QColor(surface))
    check(f"the label is fully opaque over {surface}", text.alpha() == 255)

# =============================== Part 9, 23.7-23.13: live work hours
print("\n--- Part 9: the toggle repaints both calendars immediately ---")
check("work hours start off", win.calendar_prefs.work_hours_enabled is False)
check("the Day View agrees", win.day_calendar.timeline.prefs.work_hours_enabled is False)
check("the Week View agrees", win.week_calendar.timeline.prefs.work_hours_enabled is False)

repaints = {"day": 0, "week": 0}
win.day_calendar.timeline.prefs.changed.connect(
    lambda: repaints.__setitem__("day", repaints["day"] + 1))
win.week_calendar.timeline.prefs.changed.connect(
    lambda: repaints.__setitem__("week", repaints["week"] + 1))

settings = SettingsDialog(db, on_change=win._apply_settings, parent=win)
settings.work_hours_check.setChecked(True)      # the real control, really toggled
settle()

check("the setting was persisted", db.get_setting(WORK_HOURS_SETTING) == "1")
check("the shared preferences object was told, without a restart",
      win.calendar_prefs.work_hours_enabled is True)
check("the Day View's timeline sees it now",
      win.day_calendar.timeline.prefs.work_hours_enabled is True)
check("the Week View's timeline sees it now",
      win.week_calendar.timeline.prefs.work_hours_enabled is True)
check(f"both timelines were notified to repaint (day={repaints['day']}, "
      f"week={repaints['week']})",
      repaints["day"] > 0 and repaints["week"] > 0)

# It has to actually reach the paint, not just the state.
day_pixmap_on = win.day_calendar.timeline.grab()
week_pixmap_on = win.week_calendar.timeline.grab()

settings.work_hours_check.setChecked(False)
settle()
check("turning it off is equally immediate",
      win.calendar_prefs.work_hours_enabled is False
      and win.day_calendar.timeline.prefs.work_hours_enabled is False
      and win.week_calendar.timeline.prefs.work_hours_enabled is False)
check("the setting was persisted as off", db.get_setting(WORK_HOURS_SETTING) == "0")

day_pixmap_off = win.day_calendar.timeline.grab()
week_pixmap_off = win.week_calendar.timeline.grab()
check("the Day View actually looks different with it on",
      day_pixmap_on.toImage() != day_pixmap_off.toImage())
check("the Week View actually looks different with it on",
      week_pixmap_on.toImage() != week_pixmap_off.toImage())

check("no date change, tab switch or theme change was needed",
      win.current_date == "2026-05-01"
      and win.main_tabs.currentIndex() == 0)

settings.close()

print("\n--- Part 23.12-23.13: it survives a restart ---")
settings2 = SettingsDialog(db, on_change=win._apply_settings, parent=win)
settings2.work_hours_check.setChecked(True)
settings2.close()
settle()
win.editor.mark_clean()
win.projects_widget.editor.mark_clean()
win.close()
settle()

win2 = MainWindow()
win2.show()
settle()
check("work hours are still on after a restart",
      win2.calendar_prefs.work_hours_enabled is True)
check("and both timelines start with it",
      win2.day_calendar.timeline.prefs.work_hours_enabled is True
      and win2.week_calendar.timeline.prefs.work_hours_enabled is True)
check("the events created earlier are still there, with their transparencies",
      win2.db.get_event(new_id).opacity == OPACITY_FOLLOWS_DEFAULT
      and win2.db.get_event(custom_id).opacity == 80)
win2.editor.mark_clean()
win2.projects_widget.editor.mark_clean()
win2.close()
settle()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
