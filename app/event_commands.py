"""Event commands: the one implementation of every change a user can make to
a calendar event, whichever view or gesture it came from (shared command
rule, Master Spec §§8, 66; Group 3).

The Day View and the Weekly Schedule both hand their requests here: create
(drag, double-click, Add Event…, the untimed strip), edit (double-click an
event), move and resize (drags), change an untimed range (strip edge drag)
and delete (Delete key, the editor's Delete button). For a repeating event
each one asks which occurrences it applies to — this occurrence only, this
and following, or the entire series (§22.1) — and Cancel cancels.

Every method returns True when something was written, so the calling view
knows to refresh; False means nothing changed (cancelled, or refused).
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox

from .database import CalendarEvent, Database
from .event_dialog import EventDialog
from .recurrence import MINUTES_PER_DAY, days_between, span_from

SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL = "this", "following", "all"

_VERBS = {"edit": "Change", "move": "Move", "resize": "Change", "delete": "Delete"}


def ask_scope(parent, action: str, allow_this: bool = True):
    """Which occurrences of a repeating event an action applies to.

    Returns SCOPE_THIS / SCOPE_FOLLOWING / SCOPE_ALL, or None for Cancel.
    A module-level function so tests can answer it without a modal dialog.
    `allow_this` is False when the change is to the repetition itself,
    which a single occurrence cannot have.
    """
    verb = _VERBS.get(action, "Change")
    box = QMessageBox(parent)
    box.setWindowTitle("Repeating event")
    box.setText(f"{verb} which occurrences of this repeating event?")
    this_btn = box.addButton("This occurrence only", QMessageBox.AcceptRole) if allow_this else None
    following_btn = box.addButton("This and following", QMessageBox.AcceptRole)
    all_btn = box.addButton("The entire series", QMessageBox.AcceptRole)
    box.addButton(QMessageBox.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if this_btn is not None and clicked is this_btn:
        return SCOPE_THIS
    if clicked is following_btn:
        return SCOPE_FOLLOWING
    if clicked is all_btn:
        return SCOPE_ALL
    return None


def confirm_delete(parent) -> bool:
    """"Delete this event?" for an ordinary event (patchable in tests)."""
    return QMessageBox.question(parent, "Delete event", "Delete this event?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


def run_dialog(dialog) -> bool:
    """dialog.exec() == Accepted — a seam so tests can drive the editor."""
    return dialog.exec() == QDialog.Accepted


class EventCommands(QObject):
    """A QObject so that restoring a backup finds it and repoints its
    database handle with everything else (MainWindow._database_holders)."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db

    # ------------------------------------------------------------ lookup
    def occurrence(self, key):
        if isinstance(key, CalendarEvent):
            key = self.key_of(key)
        return self.db.occurrence_by_key(key)

    # ------------------------------------------------------------ create
    @staticmethod
    def draft(date: str, start_minute: int, end_minute: int, all_day: bool = False) -> CalendarEvent:
        """A new, unsaved event for the editor. An `end_minute` past 1440
        means the end is on a later day."""
        if all_day:
            return CalendarEvent(id=0, date=date, start_minute=0, end_minute=0, all_day=True)
        end_date, end = span_from(date, start_minute, max(15, end_minute - start_minute))
        return CalendarEvent(id=0, date=date, start_minute=start_minute, end_minute=end,
                             end_date=end_date)

    def create(self, parent, date: str, start_minute: int, end_minute: int,
               all_day: bool = False) -> bool:
        return self.create_from_draft(parent, self.draft(date, start_minute, end_minute, all_day))

    def create_from_draft(self, parent, draft: CalendarEvent) -> bool:
        """Opens the editor on a new event and inserts it if saved."""
        dialog = EventDialog(draft, parent=parent, is_new=True)
        if not run_dialog(dialog):
            return False
        rule = getattr(dialog, "rule", lambda: None)()
        self.db.create_event(recurrence=rule, **dialog.values())
        return True

    # -------------------------------------------------------------- edit
    @staticmethod
    def key_of(event) -> object:
        """The occurrence key for an event row or a drawn piece."""
        key = getattr(event, "id", None)
        if isinstance(event, CalendarEvent) and event.series_id is not None and event.occurrence_date:
            return (event.series_id, event.occurrence_date)
        return key

    def edit(self, parent, key) -> bool:
        """The editor on an existing event or occurrence.

        Only what the user changed is applied (GF-2): Save with nothing
        changed writes nothing and asks nothing, and an edit reaching other
        occurrences never copies this occurrence's own title, time or notes
        onto them."""
        occ = self.occurrence(key)
        if occ is None:
            return False
        shown = replace(occ.event, date=occ.date,
                        end_date=None if occ.end_date == occ.date else occ.end_date)
        shown_rule = occ.rule
        if occ.rule is not None and occ.series_id is not None:
            # "Ends on" is the whole series' end, even when this occurrence
            # is in an earlier part of a series split by "this and following".
            shown_rule = replace(occ.rule, until=self.db.series_end(occ.series_id))
        dialog = EventDialog(shown, parent=parent, rule=shown_rule) if shown_rule is not None \
            else EventDialog(shown, parent=parent)
        if not run_dialog(dialog):
            return False
        if dialog.delete_requested:
            return self.delete(parent, key, confirm=True)
        changed = dialog.changed_values() if hasattr(dialog, "changed_values") else \
            self._differences(shown, dialog.values())
        rule_changed = getattr(dialog, "rule_changed", lambda: False)()
        rule_change = dialog.rule() if rule_changed else _UNCHANGED
        if not changed and rule_change is _UNCHANGED:
            return False
        change = {"fields": changed, "rule": rule_change, "shown_rule": shown_rule}
        values = dialog.values()
        new_date = values.get("date", occ.date)
        new_span = days_between(new_date, values.get("end_date") or new_date)
        if "date" in changed:
            change["shift_days"] = days_between(occ.date, new_date)
        # A new length reaches the other occurrences only when the user
        # changed this one's length, or typed both its times (the typed
        # range then applies as a whole). Moving it to another date keeps
        # every occurrence's own length (Group 3 fixes, A3).
        both_times = "start_minute" in changed and "end_minute" in changed
        if new_span != days_between(occ.date, occ.end_date) or (both_times and not values.get("all_day")):
            change["span"] = new_span
        return self._apply(parent, occ, "edit", change)

    @staticmethod
    def _differences(shown, values: dict) -> dict:
        """What an editor without changed_values() changed, measured against
        the occurrence it was opened on."""
        changed = {k: v for k, v in values.items() if getattr(shown, k, None) != v}
        if "date" in changed or "end_date" in changed:
            changed["date"] = values.get("date", shown.date)
            changed["end_date"] = values.get("end_date")
        return changed

    # ---------------------------------------------------- move / resize
    #
    # A drag is described twice: as the dragged occurrence's new values
    # (for an ordinary event or "this occurrence only") and as the change in
    # its start and end, in minutes (for "this and following" / "entire
    # series", where every occurrence moves by that much from its own time).
    @staticmethod
    def _absolute(date_str: str, minute: int, origin: str) -> int:
        return days_between(origin, date_str) * MINUTES_PER_DAY + minute

    def move(self, parent, key, new_date: str, new_start: int) -> bool:
        """A drag: the occurrence starts at (new_date, new_start), same length."""
        occ = self.occurrence(key)
        if occ is None:
            return False
        length = days_between(occ.date, occ.end_date) * 1440 + occ.end_minute - occ.start_minute
        end_date, end = span_from(new_date, new_start, length)
        delta = self._absolute(new_date, new_start, occ.date) - occ.start_minute
        if delta == 0:
            return False
        days = days_between(occ.date, new_date)
        return self._apply(parent, occ, "move", {
            "fields": {"date": new_date, "start_minute": new_start,
                       "end_date": end_date, "end_minute": end},
            "start_delta": delta, "end_delta": delta, "start_days": days, "end_days": days})

    def resize(self, parent, key, edge: str, date: str, minute: int) -> bool:
        """A drag of the top (new start) or bottom (new end) edge."""
        occ = self.occurrence(key)
        if occ is None:
            return False
        if edge == "top":
            fields = {"date": date, "start_minute": minute,
                      "end_date": occ.end_date, "end_minute": occ.end_minute}
            start_delta = self._absolute(date, minute, occ.date) - occ.start_minute
            end_delta = 0
            start_days, end_days = days_between(occ.date, date), 0
        else:
            fields = {"date": occ.date, "start_minute": occ.start_minute,
                      "end_date": date, "end_minute": minute}
            start_delta = 0
            end_delta = (self._absolute(date, minute, occ.date)
                         - self._absolute(occ.end_date, occ.end_minute, occ.date))
            start_days, end_days = 0, days_between(occ.end_date, date)
        if start_delta == 0 and end_delta == 0:
            return False
        return self._apply(parent, occ, "resize", {
            "fields": fields, "start_delta": start_delta, "end_delta": end_delta,
            "start_days": start_days, "end_days": end_days})

    def set_untimed_range(self, parent, key, first: str, last: str) -> bool:
        """An untimed event's dates, from dragging an end of its chip."""
        occ = self.occurrence(key)
        if occ is None or last < first:
            return False
        start_delta = days_between(occ.date, first) * MINUTES_PER_DAY
        end_delta = days_between(occ.end_date, last) * MINUTES_PER_DAY
        if start_delta == 0 and end_delta == 0:
            return False
        return self._apply(parent, occ, "resize", {
            "fields": {"date": first, "end_date": last},
            "start_delta": start_delta, "end_delta": end_delta})

    # ------------------------------------------------------------ delete
    def delete(self, parent, key, confirm: bool = False) -> bool:
        occ = self.occurrence(key)
        if occ is None:
            return False
        if occ.is_recurring:
            scope = ask_scope(parent, "delete")
            if scope is None:
                return False
            if scope == SCOPE_THIS:
                self.db.delete_occurrence(occ.series_id, occ.occurrence_date)
            elif scope == SCOPE_FOLLOWING:
                self.db.delete_following(occ.series_id, occ.occurrence_date)
            else:
                self.db.delete_series(occ.series_id)
            return True
        if confirm and not confirm_delete(parent):
            return False
        self.db.delete_event(occ.event.id)
        return True

    # ------------------------------------------------------ the one write
    def _apply(self, parent, occ, action: str, change: dict) -> bool:
        """Writes one change to the right rows.

        `change` holds `fields` (this occurrence's new values — for the
        editor only the ones the user changed), and for a series-wide change
        either `shift_days` / `span` (editor) or `start_delta` / `end_delta`
        (drags), plus `rule`: a RecurrenceRule, None ("stop repeating") or
        _UNCHANGED. A rule change applies to a whole series (or to the part
        from this occurrence on), never to a single occurrence.
        """
        fields = dict(change.get("fields", {}))
        rule_change = change.get("rule", _UNCHANGED)
        if "end_date" in fields and fields.get("end_date") == fields.get("date", occ.date):
            fields["end_date"] = None
        try:
            if not occ.is_recurring:
                if fields:
                    self.db.update_event(occ.event.id, **fields)
                if rule_change is not _UNCHANGED and rule_change is not None:
                    self.db.set_recurrence(occ.event.id, rule_change)
                return True
            scope = ask_scope(parent, action, allow_this=rule_change is _UNCHANGED)
            if scope is None:
                return False
            if scope == SCOPE_THIS:
                if fields:
                    self.db.edit_occurrence(occ.series_id, occ.occurrence_date, **fields)
                return True
            series_wide = {k: v for k, v in fields.items() if k not in ("date", "end_date")}
            if "start_delta" in change:
                # A drag moves every occurrence by the same amount from its
                # own time; its absolute times are not copied onto them.
                series_wide = {}
            kwargs = dict(shift_days=change.get("shift_days", 0), span=change.get("span"),
                          start_delta=change.get("start_delta"),
                          end_delta=change.get("end_delta"),
                          start_days=change.get("start_days"),
                          end_days=change.get("end_days"), **series_wide)
            if scope == SCOPE_FOLLOWING:
                part = self.db.edit_following(occ.series_id, occ.occurrence_date, **kwargs)
            else:
                self.db.edit_series(occ.series_id, **kwargs)
                part = self.db.get_event(self.db.series_family(occ.series_id)[0])
            if rule_change is not _UNCHANGED:
                shown_rule = change.get("shown_rule") or occ.rule
                keep_until = rule_change is not None and shown_rule is not None and \
                    rule_change.until == shown_rule.until
                self.db.change_series_rule(part.id, rule_change, keep_until=keep_until)
            return True
        except ValueError as exc:
            QMessageBox.warning(parent, "Event not changed", str(exc))
            return False


class _Unchanged:
    def __repr__(self):
        return "UNCHANGED"


_UNCHANGED = _Unchanged()
