"""View → Core Features: the optional parts of jortle_claude (Master Spec
§51.2; batch 4A2).

This list is the one place the features are defined. The View → Core
Features submenu, the stored visibility (one setting per feature, in the
settings table) and the first-launch tooltip all read it, so a future feature
(Maps, Lifetime) is one more entry here (FP-1).

A feature hides either a primary tab, by its key (`MainWindow._add_primary_tab`),
or panes, by the attribute paths of their widgets on the main window. Daily
Jorts, the Weekly Schedule and the month-navigator pane are never hideable
(G4-D2) and are not listed. Hiding changes presentation only; it never
deletes or changes data.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import data_migration


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    tab_key: str | None = None      # a primary tab, by its key
    panes: tuple = ()               # widgets on the main window, as attribute paths


FEATURES: list[Feature] = [
    Feature("year", "Yearly Calendar", tab_key="year"),
    Feature("projects", "Projects", tab_key="projects"),
    # Both of its panes: Daily Jorts' and Projects' (G4-D2).
    Feature("reader_notes", "Reader's Notes",
            panes=("reader_notes", "projects_widget.reader_notes")),
]

# security.json: when the first-launch tooltip was acknowledged with "Cool!".
# There, not in the settings table, because it survives a restore (4A2/AM-4).
TIP_ACKNOWLEDGED = "core_features_tip_acknowledged"
# security.json: the date a first launch made the tooltip due; it then shows
# at every launch until acknowledged, even after a restore (4A2/AM-6).
TIP_DUE = "core_features_tip_due"


def setting_name(key: str) -> str:
    return f"feature_visible_{key}"


def is_visible(db, key: str) -> bool:
    """Every feature is visible until the user hides it."""
    return db.get_setting(setting_name(key), "1") != "0"


def set_visible(db, key: str, visible: bool):
    db.set_setting(setting_name(key), "1" if visible else "0")


def first_launch() -> bool:
    """A launch of a new installation (4A2/AM-1): this process's data-folder
    decision was case C — no installation with user content in the
    jortle_claude folder, and no legacy folder. An existing installation (A),
    a migration from a legacy folder (B, D) and an unreadable folder (E) are
    not first launches."""
    result = data_migration.migration_result()
    return result is not None and result.case == "C"
