"""How the calendars are *displayed* — the time scale, and work hours.

One object, shared by Day View and Week View, for the same reason there is
one `SelectedDate`: two views of the same thing must not each keep their own
copy of how that thing is shown. Zooming the Day View and finding the Week
View still at the old scale would be the calendar equivalent of the
hand-synchronised date variables this app already had to get rid of.

    CalendarPrefs ──notifies──> Day View timeline
                  └───────────> Week View timeline
                  └───────────> (persisted to the settings table)

What this holds is a *display* preference, never event data (Part 16). An
event's start and end are minutes from midnight and never change; zoom only
changes how many pixels an hour is drawn as, so the same event is simply
recomputed at the new scale. That is also why zoom is emphatically not a
graphical scaling of the widget: at 200% the hour labels, the grid lines and
the event text are all still drawn at their normal size, just further apart.

Three zoom concepts exist in this app and are deliberately separate (Part 34):

    calendar zoom          pixels per hour on the timeline   (here)
    rich-text editor zoom  on-screen size of the writing     (rich_editor.py)
    application font size  the size of the interface itself  (settings)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

# Pixels per hour at 100%. This was the day calendar's fixed HOUR_HEIGHT, and
# it stays the definition of 100% so an existing install looks unchanged
# until the user actually zooms.
BASE_HOUR_HEIGHT = 56.0

MIN_ZOOM_PERCENT = 40      # ~22px/hour: the whole day at a glance
MAX_ZOOM_PERCENT = 300     # ~168px/hour: room to place things minute by minute
ZOOM_STEP_PERCENT = 10

# Work hours. Fixed rather than configurable, per Part 18 — a custom schedule
# is only worth building if it stays trivial, and "9 to 5, weekdays" is the
# thing being asked for. They live here rather than in the painting code so
# both views cannot disagree about them.
WORK_START_MINUTE = 9 * 60
WORK_END_MINUTE = 17 * 60
WORK_WEEKDAYS = (1, 2, 3, 4, 5)   # Qt: Monday=1 .. Sunday=7

ZOOM_SETTING = "calendar_zoom_percent"
WORK_HOURS_SETTING = "calendar_work_hours"


class CalendarPrefs(QObject):
    """The shared calendar time scale and work-hours toggle."""

    changed = Signal()

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._zoom_percent = self._load_int(ZOOM_SETTING, 100)
        self._work_hours = self._load_bool(WORK_HOURS_SETTING, False)

    # ------------------------------------------------------------ loading
    def _load_int(self, key: str, fallback: int) -> int:
        if self.db is None:
            return fallback
        try:
            return self._clamp(int(self.db.get_setting(key, str(fallback))))
        except (TypeError, ValueError):
            return fallback

    def _load_bool(self, key: str, fallback: bool) -> bool:
        if self.db is None:
            return fallback
        return self.db.get_setting(key, "1" if fallback else "0") == "1"

    def _save(self, key: str, value: str):
        if self.db is not None:
            self.db.set_setting(key, value)

    @staticmethod
    def _clamp(percent: int) -> int:
        return max(MIN_ZOOM_PERCENT, min(MAX_ZOOM_PERCENT, int(percent)))

    # --------------------------------------------------------------- zoom
    @property
    def zoom_percent(self) -> int:
        return self._zoom_percent

    @property
    def hour_height(self) -> float:
        """Pixels per hour at the current zoom — what the views paint with."""
        return BASE_HOUR_HEIGHT * self._zoom_percent / 100.0

    def set_zoom_percent(self, percent: int) -> bool:
        percent = self._clamp(percent)
        if percent == self._zoom_percent:
            return False
        self._zoom_percent = percent
        self._save(ZOOM_SETTING, str(percent))
        self.changed.emit()
        return True

    def zoom_by(self, steps: int) -> bool:
        """One notch of the wheel per step; positive zooms in."""
        return self.set_zoom_percent(self._zoom_percent + steps * ZOOM_STEP_PERCENT)

    def reset_zoom(self) -> bool:
        return self.set_zoom_percent(100)

    # -------------------------------------------------------- work hours
    @property
    def work_hours_enabled(self) -> bool:
        return self._work_hours

    def set_work_hours_enabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if enabled == self._work_hours:
            return False
        self._work_hours = enabled
        self._save(WORK_HOURS_SETTING, "1" if enabled else "0")
        self.changed.emit()
        return True

    @staticmethod
    def is_work_day(qdate) -> bool:
        """Weekdays only — a Saturday or Sunday column never gets the tint."""
        return qdate.isValid() and qdate.dayOfWeek() in WORK_WEEKDAYS

    def work_span(self) -> tuple:
        return WORK_START_MINUTE, WORK_END_MINUTE

    def reload(self, db=None):
        """Re-reads from the database — used after a restore from backup
        swaps the Database out from under the app."""
        if db is not None:
            self.db = db
        self._zoom_percent = self._load_int(ZOOM_SETTING, 100)
        self._work_hours = self._load_bool(WORK_HOURS_SETTING, False)
        self.changed.emit()
