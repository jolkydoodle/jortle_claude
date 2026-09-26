"""The event editor, shared by every calendar view (Parts 24 and 26; Group 3).

Offers title, start and end (each a date and a time, so an event can run
past midnight or across days), untimed, done, repetition, colour,
transparency, notes and Delete. It edits values only: what is written, and
for a repeating event to which occurrences, is event_commands' business.
"""
from __future__ import annotations

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider,
    QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from .database import CalendarEvent, OPACITY_FOLLOWS_DEFAULT
from .date_state import to_qdate
from .day_calendar_model import snap_minute
from .event_render import resolve_event_opacity
from .recurrence import MINUTES_PER_DAY, RecurrenceRule, sunday_weekday

ISO = "yyyy-MM-dd"
REPEAT_CHOICES = (("Does not repeat", None), ("Daily", "daily"), ("Weekly", "weekly"),
                  ("Monthly", "monthly"), ("Yearly", "yearly"))
UNITS = {"daily": ("day", "days"), "weekly": ("week", "weeks"),
         "monthly": ("month", "months"), "yearly": ("year", "years")}
WEEKDAY_LETTERS = ("S", "M", "T", "W", "T", "F", "S")
WEEKDAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


class EventDialog(QDialog):
    """Editor for one event, or one occurrence of a repeating event.

    `rule` is the series' rule when editing a repeating event (shown in the
    Repeat section). `values()` returns the edited fields; `rule_changed()`
    says whether the repetition was changed.
    """

    def __init__(self, event: CalendarEvent, parent=None, is_new: bool = False,
                 rule: RecurrenceRule | None = None):
        super().__init__(parent)
        self.setWindowTitle("New Event" if is_new else "Edit Event")
        self.event = event
        self.delete_requested = False
        self._initial_rule = rule

        self.title_input = QLineEdit(event.title)
        self.title_input.setPlaceholderText("Event title")

        # Start and end each have a date: "9 PM to 2 AM" is one event whose
        # end is on the next day, and an untimed event covers a range.
        self.date_input = QDateEdit(to_qdate(event.date))
        self.end_date_input = QDateEdit(to_qdate(event.end_date or event.date))
        for widget in (self.date_input, self.end_date_input):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("MMMM d, yyyy")
        self._last_start_date = self.date_input.date()
        self.date_input.dateChanged.connect(self._on_start_date_changed)

        self.all_day_check = QCheckBox("Untimed (no specific time)")
        self.all_day_check.setChecked(event.all_day)
        self.all_day_check.toggled.connect(self._on_all_day_toggled)

        self.start_input = QTimeEdit(QTime(event.start_minute // 60, event.start_minute % 60))
        end_minute = event.end_minute % MINUTES_PER_DAY
        self.end_input = QTimeEdit(QTime(end_minute // 60, end_minute % 60))
        if event.end_minute == MINUTES_PER_DAY and not event.all_day:
            # The older "same day, 24:00" form: show it as midnight next day.
            self.end_date_input.setDate(to_qdate(event.date).addDays(1))
        for widget in (self.start_input, self.end_input):
            widget.setDisplayFormat("h:mm AP")
        self.end_input.timeChanged.connect(self._on_end_time_changed)

        start_row = QHBoxLayout()
        start_row.addWidget(self.date_input, stretch=1)
        start_row.addWidget(self.start_input)
        end_row = QHBoxLayout()
        end_row.addWidget(self.end_date_input, stretch=1)
        end_row.addWidget(self.end_input)

        self.done_check = QCheckBox("Done")
        self.done_check.setChecked(event.done)

        # ---- repetition (Master Spec §22.1)
        self.repeat_combo = QComboBox()
        for label, _freq in REPEAT_CHOICES:
            self.repeat_combo.addItem(label)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 99)
        self.interval_unit = QLabel()
        self.weekday_checks = []
        weekday_row = QHBoxLayout()
        for number, letter in enumerate(WEEKDAY_LETTERS):
            box = QCheckBox(letter)
            box.setToolTip(WEEKDAY_NAMES[number])
            self.weekday_checks.append(box)
            weekday_row.addWidget(box)
        weekday_row.addStretch(1)
        self.weekday_widget = QWidget()
        self.weekday_widget.setLayout(weekday_row)
        weekday_row.setContentsMargins(0, 0, 0, 0)
        self.until_check = QCheckBox("Ends on")
        self.until_input = QDateEdit()
        self.until_input.setCalendarPopup(True)
        self.until_input.setDisplayFormat("MMMM d, yyyy")
        self.until_check.toggled.connect(self.until_input.setEnabled)

        every_row = QHBoxLayout()
        every_row.addWidget(self.repeat_combo, stretch=1)
        every_row.addWidget(QLabel("every"))
        every_row.addWidget(self.interval_spin)
        every_row.addWidget(self.interval_unit)
        self._every_widgets = [every_row.itemAt(i).widget() for i in range(1, 4)]
        until_row = QHBoxLayout()
        until_row.addWidget(self.until_check)
        until_row.addWidget(self.until_input, stretch=1)
        self.until_widget = QWidget()
        self.until_widget.setLayout(until_row)
        until_row.setContentsMargins(0, 0, 0, 0)
        self._load_rule(rule)
        self.repeat_combo.currentIndexChanged.connect(self._on_repeat_changed)
        self.interval_spin.valueChanged.connect(self._on_repeat_changed)

        # Colour: "Use theme colour" is a real, persistent choice (stored as
        # NULL), not the theme's current colour copied in.
        self._custom_color = event.color
        self.theme_color_check = QCheckBox("Use theme default colour")
        self.theme_color_check.setChecked(not event.color)
        self.theme_color_check.toggled.connect(self._on_theme_color_toggled)
        self.color_button = QPushButton("Choose colour…")
        self.color_button.clicked.connect(self._pick_color)
        color_row = QHBoxLayout()
        color_row.addWidget(self.theme_color_check)
        color_row.addWidget(self.color_button)
        color_row.addStretch(1)
        self._color_row = color_row

        # Transparency applies to the BACKGROUND only. The column stores
        # opacity; the control shows transparency (100 − opacity). An event
        # that has never been given one follows the application default.
        self._stored_opacity = getattr(event, "opacity", OPACITY_FOLLOWS_DEFAULT)
        self._initial_transparency = 100 - resolve_event_opacity(event)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 90)
        self.opacity_slider.setSingleStep(5)
        self.opacity_slider.setPageStep(10)
        self.opacity_slider.setValue(self._initial_transparency)
        self.opacity_value = QLabel()
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText(f"{v}% transparent"))
        self.opacity_value.setText(f"{self.opacity_slider.value()}% transparent")
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_slider, stretch=1)
        opacity_row.addWidget(self.opacity_value)
        self._opacity_row = opacity_row

        self.notes_input = QPlainTextEdit(event.notes)
        self.notes_input.setPlaceholderText("Optional notes")
        self.notes_input.setFixedHeight(90)

        form = QFormLayout()
        form.addRow("Title", self.title_input)
        form.addRow("", self.all_day_check)
        form.addRow("Starts", start_row)
        form.addRow("Ends", end_row)
        form.addRow("", self.done_check)
        form.addRow("Repeat", every_row)
        form.addRow("On", self.weekday_widget)
        form.addRow("", self.until_widget)
        form.addRow("Colour", color_row)
        form.addRow("Transparency", opacity_row)
        form.addRow("Notes", self.notes_input)
        self._form = form

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._on_delete)
        if is_new:
            delete_btn.setVisible(False)
        button_row = QHBoxLayout()
        button_row.addWidget(delete_btn)
        button_row.addStretch(1)
        button_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(button_row)

        self._on_all_day_toggled(self.all_day_check.isChecked())
        self._on_theme_color_toggled(self.theme_color_check.isChecked())
        self._on_repeat_changed()
        if self._custom_color:
            self.color_button.setText(f"Colour: {self._custom_color}")
        self.title_input.setFocus()
        self.title_input.selectAll()
        # What the editor showed before the user touched anything, in the
        # same form it reports: `changed_values()` and `rule_changed()`
        # compare against these, so pressing Save without changing anything
        # changes nothing (Group 3 fixes, A1–A3).
        self._loaded_values = self.values()
        self._loaded_rule_state = self._rule_state()

    # ------------------------------------------------------------ repeat
    def _load_rule(self, rule: RecurrenceRule | None):
        freqs = [f for _l, f in REPEAT_CHOICES]
        self.repeat_combo.setCurrentIndex(freqs.index(rule.freq) if rule else 0)
        self.interval_spin.setValue(rule.interval if rule else 1)
        days = set(rule.weekdays) if rule and rule.weekdays else \
            {sunday_weekday(to_qdate(self.event.date).toPython())}
        for number, box in enumerate(self.weekday_checks):
            box.setChecked(number in days)
        if rule and rule.until:
            self.until_check.setChecked(True)
            self.until_input.setDate(to_qdate(rule.until))
        else:
            self.until_check.setChecked(False)
            self.until_input.setDate(to_qdate(self.event.date).addMonths(3))
        self.until_input.setEnabled(self.until_check.isChecked())

    def _freq(self):
        return REPEAT_CHOICES[self.repeat_combo.currentIndex()][1]

    def _on_repeat_changed(self, *_args):
        freq = self._freq()
        for widget in self._every_widgets:
            widget.setVisible(freq is not None)
        self._form.setRowVisible(self.weekday_widget, freq == "weekly")
        self._form.setRowVisible(self.until_widget, freq is not None)
        if freq:
            one, many = UNITS[freq]
            self.interval_unit.setText(one if self.interval_spin.value() == 1 else many)

    def _rule_state(self) -> tuple:
        """The repeat controls as they stand (what the user can see)."""
        return (self._freq(), self.interval_spin.value(),
                tuple(box.isChecked() for box in self.weekday_checks),
                self.until_check.isChecked(),
                self.until_input.date().toString(ISO) if self.until_check.isChecked() else None)

    def rule(self) -> RecurrenceRule | None:
        """The repetition as edited, or None for "does not repeat".

        Untouched repeat controls give back the rule exactly as it was
        loaded — including what the controls don't show (a series moved as a
        whole counts its weeks from a moved week start; a weekly rule may
        have no weekdays stored). An edited rule keeps the loaded week
        start, so "every 2 weeks" stays in the same counted weeks."""
        freq = self._freq()
        if freq is None:
            return None
        if self._initial_rule is not None and not self.rule_changed():
            return self._initial_rule
        weekdays = frozenset(n for n, box in enumerate(self.weekday_checks) if box.isChecked()) \
            if freq == "weekly" else frozenset()
        initial = self._initial_rule
        if (initial is not None and initial.freq == "weekly" == freq and not initial.weekdays
                and hasattr(self, "_loaded_rule_state")
                and self._rule_state()[2] == self._loaded_rule_state[2]):
            # A weekly rule with no stored weekdays repeats on its series'
            # own weekday; the boxes only show the opened occurrence's
            # weekday as a guide. Untouched, they must not become the rule.
            weekdays = frozenset()
        start = self.date_input.date()
        until = None
        if self.until_check.isChecked():
            until_q = max(self.until_input.date(), start)
            until = until_q.toString(ISO)
        week_start = self._initial_rule.week_start if self._initial_rule is not None else 0
        return RecurrenceRule(freq, self.interval_spin.value(), weekdays, until, week_start)

    def rule_changed(self) -> bool:
        """True only when the user changed the repeat controls."""
        if not hasattr(self, "_loaded_rule_state"):
            return False
        return self._rule_state() != self._loaded_rule_state

    def changed_values(self) -> dict:
        """The fields the user changed, with their new values — the only
        ones a "this and following" or "entire series" edit may apply to
        other occurrences (GF-2). `date` and `end_date` appear together
        whenever either changed, so callers can work out a move or a new
        length."""
        now = self.values()
        changed = {k: v for k, v in now.items() if self._loaded_values.get(k) != v}
        if "date" in changed or "end_date" in changed:
            changed["date"], changed["end_date"] = now["date"], now["end_date"]
        return changed

    # ------------------------------------------------------------- dates
    def _on_start_date_changed(self, new_date: QDate):
        """Moving the start date moves the end date with it, keeping the
        event's length — the way every calendar program behaves."""
        delta = self._last_start_date.daysTo(new_date)
        self._last_start_date = new_date
        if delta:
            self.end_date_input.setDate(self.end_date_input.date().addDays(delta))

    def _on_end_time_changed(self, _time: QTime):
        """An end time earlier than the start on the same date means the
        next day: 9 PM → 12 AM ends at midnight tomorrow, not 12 AM today."""
        if self.all_day_check.isChecked():
            return
        if self.end_date_input.date() == self.date_input.date() and \
                self.end_input.time() <= self.start_input.time():
            self.end_date_input.setDate(self.date_input.date().addDays(1))

    def _on_all_day_toggled(self, checked: bool):
        self.start_input.setEnabled(not checked)
        self.end_input.setEnabled(not checked)

    def _on_theme_color_toggled(self, use_theme: bool):
        self.color_button.setEnabled(not use_theme)

    def _pick_color(self):
        from PySide6.QtWidgets import QColorDialog
        start = QColor(self._custom_color) if self._custom_color else QColor("#3f6ea5")
        chosen = QColorDialog.getColor(start, self, "Event colour")
        if chosen.isValid():
            self._custom_color = chosen.name()
            self.theme_color_check.setChecked(False)
            self.color_button.setText(f"Colour: {self._custom_color}")

    def _on_delete(self):
        self.delete_requested = True
        self.accept()

    def values(self) -> dict:
        """The edited values, already made into a legal span so the caller
        never has to re-check that the end is after the start.

        Timed: an end at or before the start on the same date is read as the
        next day (overnight); anything else that ends too early gets the
        15-minute minimum. Untimed: the end date is never before the start.
        """
        all_day = self.all_day_check.isChecked()
        start_q = self.date_input.date()
        end_q = self.end_date_input.date()
        if all_day:
            start = end = 0
            if end_q < start_q:
                end_q = start_q
        else:
            # Snapped to the same 15-minute grid as every drag gesture.
            start = min(snap_minute(self.start_input.time().hour() * 60
                                    + self.start_input.time().minute()), MINUTES_PER_DAY - 15)
            end = snap_minute(self.end_input.time().hour() * 60 + self.end_input.time().minute())
            if end == MINUTES_PER_DAY:          # snapped up to midnight: next day, 0:00
                end_q, end = end_q.addDays(1), 0
            if end_q < start_q:
                end_q = start_q
            span = start_q.daysTo(end_q) * MINUTES_PER_DAY + end - start
            if span <= 0 and end_q == start_q:
                end_q = start_q.addDays(1)
                span = MINUTES_PER_DAY + end - start
            if span < 15:
                total = start + 15
                end_q = start_q.addDays(total // MINUTES_PER_DAY)
                end = total % MINUTES_PER_DAY
        date = start_q.toString(ISO)
        end_date = end_q.toString(ISO)
        return {
            "title": self.title_input.text().strip(),
            "date": date,
            "end_date": None if end_date == date else end_date,
            "start_minute": start,
            "end_minute": end,
            "all_day": all_day,
            "done": self.done_check.isChecked(),
            "notes": self.notes_input.toPlainText(),
            # None means "follow the theme" — stored as NULL, never as the
            # theme's current colour resolved into a fixed value.
            "color": None if self.theme_color_check.isChecked() else self._custom_color,
            "opacity": self._chosen_opacity(),
        }

    def _chosen_opacity(self) -> int:
        """The opacity to store, preserving "follow the default": opening an
        event and pressing Save must not freeze today's default onto it."""
        transparency = self.opacity_slider.value()
        if (self._stored_opacity == OPACITY_FOLLOWS_DEFAULT
                and transparency == self._initial_transparency):
            return OPACITY_FOLLOWS_DEFAULT
        return 100 - transparency
