"""The launch message while backups are paused (Master Spec §46.2, decided
2026-10-04; batch 4A-43, bug 43).

Backups are paused when encrypted backups were chosen but no backup
passphrase exists yet (security.backups_paused_reason): no backup of any kind
is made, rather than an unencrypted one. Until 4A-43 the only sign of it was
the status-bar indicator, and an install went more than two weeks without a
backup. Now the main window shows a message at launch, at most once a day,
until backups are no longer paused.

"Last shown" is a date in security.json (paused_notice_shown_on), not in the
settings table: security.json holds the rest of the backup state, is
per-installation as the pause is, and is carried over by a restore, while a
restore replaces the settings table with the backup's own (4A-43, answer 2).

`today()` is the one place the date is read, so tests set it instead of the
system clock.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import security

SHOWN_ON = "paused_notice_shown_on"


def today() -> str:
    """The local date, YYYY-MM-DD. Tests replace this function."""
    return date.today().isoformat()


def should_show(data_dir: Path, on: str) -> bool:
    """Backups are paused and the message has not been shown on `on`. Only
    the pause counts: while the storage question is unanswered, the
    question itself is the reminder (4A-43, answer 1)."""
    if not security.backups_paused_reason(data_dir):
        return False
    return security.load_config(data_dir).get(SHOWN_ON) != on


def record_shown(data_dir: Path, on: str):
    security.update_config(data_dir, **{SHOWN_ON: on})
