"""Month and year titles: one canonical value per month and per year
(Master Spec §§11-12), shown wherever that period is on screen.

A thin shared store over Database.get/set_period_title with a `changed`
signal. The Daily Jorts month panel, the Weekly Schedule navigator and the
Yearly Calendar all read and write through the ONE instance the main window
owns, so a title edited in one place appears in the others at once — there
are no per-view copies to keep in step.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


class PeriodTitles(QObject):
    changed = Signal(str, str)      # kind ('month' / 'year'), period

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db                # repointed by a restore (MainWindow._database_holders)

    def get(self, kind: str, period: str) -> str:
        return self.db.get_period_title(kind, period)

    def month_titles(self, year: int) -> dict:
        return self.db.period_titles("month", f"{year:04d}-")

    def set(self, kind: str, period: str, title: str) -> str:
        """Stores the title (one line, at most 80 characters; blank removes
        it) and tells every view. Does nothing when the value is unchanged,
        so re-showing a field never counts as an edit."""
        if self.get(kind, period) == " ".join((title or "").split())[:self.db.TITLE_MAX].strip():
            return self.get(kind, period)
        stored = self.db.set_period_title(kind, period, title)
        self.changed.emit(kind, period)
        return stored
