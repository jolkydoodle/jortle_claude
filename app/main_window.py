from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QDate, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QSplitter, QStatusBar, QTabWidget,
    QToolButton, QVBoxLayout, QWidget
)

from .archive import export_archive, default_archive_dirname
from . import backup, backup_dialog, security
from .backup import default_backup_filename, restore_backup
from .backup_dialog import (
    UNENCRYPTED_COPIES_WARNING, BackupsSecurityDialog, NewPassphraseDialog,
    StorageChoiceDialog, ask_passphrase, busy
)
from .calendar_panel import CalendarPanel
from .calendar_widget import month_marks
from .period_titles import PeriodTitles
from .year_calendar import YearCalendarWidget
from .calendar_prefs import CalendarPrefs
from .event_commands import EventCommands
from .cleanup import find_unused_photos, delete_files
from .database import Database
from .date_state import (
    NavigationHistory, SelectedDate, VisibleWeek, VisibleYear, human, human_long
)
from .day_calendar import DayCalendarWidget
from .entry_history import (
    REASON_CLOSED, REASON_LEFT, EntryHistory, stored_state
)
from .history_dialogs import EntryHistoryDialog, RecoveryDialog
from .paths import DISPLAY_NAME, dir_size_bytes, get_data_dir, human_size
from .projects_widget import ProjectsWidget
from .reader_notes_widget import ReaderNotesWidget
from .rich_editor import RichEditor
from .saving import (
    CANCEL, SAVE, ask_unsaved, autosave_enabled, document_has_content,
    ensure_autosave_default, set_autosave_enabled
)
from .settings_dialog import SettingsDialog
from .theme import build_stylesheet, scheme_from_json
from .ui_util import (
    DAY_PANE_WIDTH_SETTING, MONTHLY_PANE_WIDTH_SETTING, saved_width, side_pane_width
)
from .week_calendar import WeekCalendarWidget

AUTOSAVE_INTERVAL_MS = 1200
# How often an automatic backup is checked for while the app is open, and how
# soon after a save the unencrypted copy (when one is kept) is brought up to date.
BACKUP_CHECK_INTERVAL_MS = 60 * 60 * 1000
MIRROR_INTERVAL_MS = 4000
# Remembered separately per action (Master Spec §51): a restore is usually
# picked from somewhere different from where an archive is written.
PICKER_EXPORT_BACKUP = "picker_dir_export_backup"
PICKER_RESTORE_BACKUP = "picker_dir_restore_backup"
PICKER_ARCHIVE = "picker_dir_archive"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # "[*]" is Qt's placeholder for the unsaved-changes marker: it shows
        # "*" while setWindowModified(True), nothing otherwise.
        self.setWindowTitle(f"{DISPLAY_NAME}[*]")
        self.resize(1280, 800)

        self.db = Database()
        # Settle the autosave preference before anything can autosave — an
        # install that has always had it keeps it; a new one starts manual.
        ensure_autosave_default(self.db)
        # Daily-entry version history and recovery checkpoints: WHEN they are
        # made (entry_history.py). A child of this window so a restore's
        # database swap re-points it like every other holder.
        self.history = EntryHistory(self.db, parent=self)

        # The one canonical selected date (see date_state.py). Every
        # date-aware part of the app — the journal editor, the day calendar,
        # Reader's Notes — reads from
        # this single object instead of keeping its own copy. The monthly
        # calendar is the only thing that writes to it directly; internal
        # date links and Back navigation write to it too, but go through the
        # same setter rather than a parallel mechanism.
        self.selected_date = SelectedDate()
        self.selected_date.changed.connect(self._on_selected_date_changed)
        # The Weekly Calendar's own date state, alongside (never inside)
        # the journal's selected day — see date_state.VisibleWeek. It starts
        # on the week containing today and moves only when the user asks it
        # to, so browsing the journal never drags the week view around.
        self.visible_week = VisibleWeek()
        # The year the Yearly Calendar shows; like the week, it follows the
        # selected date when that changes (decision G3-5).
        self.visible_year = VisibleYear()
        # Month and year titles: one shared store, so every view of a month
        # shows the one stored title (Master Spec §11).
        self.period_titles = PeriodTitles(self.db, self)
        # How the calendars are DISPLAYED — the shared time scale and the
        # work-hours toggle. One object for both views (see calendar_prefs.py),
        # persisted in the settings table, and never mixed up with event data.
        self.calendar_prefs = CalendarPrefs(self.db)
        self.nav_history = NavigationHistory()
        self.nav_history.changed.connect(self._update_back_button)
        # True while _on_selected_date_changed is running, so the journal's
        # own save-before-switch can't recurse back through the setter.
        self._loading_date = False
        # The date whose entry is actually in the editor right now. Not a
        # rival source of truth for "the selected day" — it's the answer to
        # "which day does the text on screen belong to", which briefly
        # differs from the selected day at exactly one moment: after the
        # selection has moved but before the outgoing entry has been saved.
        self._last_loaded_date: str | None = None
        # The interface font size the panes were last proportioned for. The
        # side panes are re-proportioned when (and only when) this changes,
        # so a font change doesn't leave them too narrow for their own
        # controls — and so an ordinary settings change, or a splitter the
        # user has dragged where they want it, is left alone.
        self._applied_ui_size: int | None = None
        self._panes_sized = False

        # Built before _build_ui()/_apply_settings()/_load_date() below,
        # since those wire up self.editor.textChanged -> _schedule_autosave
        # and, depending on Qt version, some of that setup (e.g. applying
        # the default writing font) can itself emit textChanged even on an
        # empty, freshly-built editor — which would otherwise call
        # _schedule_autosave() before this timer exists (a real, if mostly
        # silent, bug: caught and printed by Qt's own exception handling
        # rather than actually crashing startup, but a needless traceback
        # on every launch and a dropped autosave scheduling).
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_tick)

        self._build_ui()
        self._build_menu()
        self._apply_settings()
        self._refresh_calendar_marks()
        self._load_date(self.selected_date.value)

        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_active_workspace)

        # Backups and the unencrypted copy (when kept). The hourly backup
        # check is started by start_background_tasks(), called by the entry
        # point once the window is up — not here, so a window built by a test
        # never pops a question or starts a backup on its own.
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(BACKUP_CHECK_INTERVAL_MS)
        self._backup_timer.timeout.connect(self._automatic_backup_if_due)
        self._mirror_changes = -1
        self._mirror_timer = QTimer(self)
        self._mirror_timer.setInterval(MIRROR_INTERVAL_MS)
        self._mirror_timer.timeout.connect(self._refresh_unencrypted_copy)
        self._mirror_timer.start()
        # Last, so it overrides the default sizing rather than being
        # overwritten by it.
        self._restore_window_state()

    # --------------------------------------------------------------- setup
    def _build_ui(self):
        # Left: calendar + day-marker tagging, always visible, with
        # Reader's Notes filling the space beneath them.
        self.calendar_panel = CalendarPanel(db=self.db, titles=self.period_titles)
        self.calendar_panel.dateClicked.connect(self._on_date_clicked)
        self.calendar_panel.pageChanged.connect(lambda *_: self._refresh_calendar_marks())
        self.calendar_panel.tag_picker.tagChanged.connect(self._on_tag_changed)
        # Renaming or recolouring a marker changes what the grid should
        # paint for every day using it, so the marks are re-read when the
        # marker list itself is edited — not only when a day is assigned one.
        self.calendar_panel.tag_picker.markersChanged.connect(self._refresh_calendar_marks)

        # Reader's Notes sits directly under the calendar. It used to be
        # wrapped in a tab container that existed only so an AI panel could
        # be a second tab; with that gone the wrapper was a QTabWidget with
        # one tab and a hidden tab bar, so it went too rather than leaving a
        # container with nothing to contain.
        self.reader_notes = ReaderNotesWidget(self.db, self.selected_date)
        self.reader_notes.changed.connect(self._refresh_calendar_marks)

        left_column = QWidget()
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.addWidget(self.calendar_panel)
        left_column_layout.addWidget(self.reader_notes, stretch=1)

        # Center: the day's writing, front and center — no tabs to click through.
        self.date_header = QLabel()
        self.date_header.setObjectName("DateHeader")

        # Back/Return, shown only after an internal date link has actually
        # been followed — it's meaningless otherwise, and a permanently
        # greyed-out button next to the date would be clutter.
        self.back_button = QPushButton()
        self.back_button.setVisible(False)
        self.back_button.clicked.connect(self._go_back)

        self.editor = RichEditor(link_dates=True)
        self.editor.textChanged.connect(self._schedule_autosave)
        self.editor.dateLinkActivated.connect(self._on_date_link_activated)

        # Version history of the entry on screen. An action rather than a bare
        # button, so a later menu entry can share it (one command, many routes).
        self.history_action = QAction("History…", self)
        self.history_action.setToolTip(
            "Previous versions of this day's entry — preview, restore, or delete them")
        self.history_action.triggered.connect(self._open_entry_history)
        history_button = QToolButton()
        history_button.setDefaultAction(self.history_action)
        history_button.setToolButtonStyle(Qt.ToolButtonTextOnly)

        header_row = QHBoxLayout()
        header_row.addWidget(self.date_header)
        header_row.addStretch(1)
        header_row.addWidget(self.back_button)
        header_row.addWidget(history_button)

        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addLayout(header_row)
        editor_layout.addWidget(self.editor)

        # Right: the interactive day calendar, which replaced the old Daily
        # Tasks checklist in round 22. Same position in the layout, same
        # "alongside the writing, not behind a tab" reasoning — a day view
        # you have to go looking for doesn't get used while writing.
        # One set of event commands for both calendar views (shared command
        # rule): creating, editing, moving and deleting an event is the same
        # code whichever view or gesture it comes from.
        self.event_commands = EventCommands(self.db, self)
        self.day_calendar = DayCalendarWidget(self.db, self.selected_date, self.calendar_prefs,
                                              commands=self.event_commands)
        self.day_calendar.changed.connect(self._refresh_calendar_marks)

        daily_journal_tab = QSplitter()
        daily_journal_tab.addWidget(left_column)
        daily_journal_tab.addWidget(editor_container)
        daily_journal_tab.addWidget(self.day_calendar)
        daily_journal_tab.setStretchFactor(0, 0)
        daily_journal_tab.setStretchFactor(1, 1)
        daily_journal_tab.setStretchFactor(2, 0)
        self.daily_splitter = daily_journal_tab
        self.daily_splitter.splitterMoved.connect(self._on_daily_splitter_moved)
        self._rebalance_panes()

        self.projects_widget = ProjectsWidget(self.db, history=self.history)

        # The Weekly Calendar is a workspace of its own, not a mode of the
        # Daily Journal (Part 24). It keeps its own VisibleWeek — see
        # date_state.py for why that is deliberately NOT the journal's
        # selected day — and the two are connected only by explicit
        # handoffs: clicking a day name there opens that day here.
        self.week_calendar = WeekCalendarWidget(self.db, self.visible_week, self.calendar_prefs,
                                                commands=self.event_commands,
                                                titles=self.period_titles)
        self.week_calendar.changed.connect(self._refresh_calendar_marks)
        self.week_calendar.openDayRequested.connect(self._on_open_day_from_week)
        self.week_calendar.splitter.splitterMoved.connect(self._on_week_splitter_moved)
        # The week follows the selected date: whenever it changes (a click in
        # the month grid, a date link, Back, the Yearly Calendar), the Weekly
        # Schedule shows the Sunday–Saturday week containing it. Browsing or
        # sliding the week never changes the selected date (G3-5).
        self.selected_date.changed.connect(self.week_calendar.show_week_of)
        self.week_calendar.show_week_of(self.selected_date.value)

        self.year_calendar = YearCalendarWidget(self.db, self.selected_date, self.period_titles,
                                                self.visible_year)
        self.year_calendar.openDayRequested.connect(self._on_open_day_from_week)
        self.year_calendar.openWeekRequested.connect(self._on_open_week_from_year)

        self.main_tabs = QTabWidget()
        # Each primary tab carries a STABLE key, and the remembered order is
        # a list of those keys rather than a list of positions. That is what
        # makes a future fourth tab safe: an old saved order simply doesn't
        # mention it, and it keeps its default place instead of the order
        # being discarded or a stored index pointing at the wrong tab. The
        # keys are UI identifiers only — nothing reads them but this file.
        self._tab_keys = {}
        self._applying_tab_order = False
        # The four workspaces in the default order of Master Spec §4. The keys
        # `daily` and `week` predate the names and are kept, so an order the
        # user saved before still applies; `year` is new, and a saved order
        # that doesn't mention it puts it after the tabs the user arranged.
        self._add_primary_tab("daily", daily_journal_tab, "Daily Jorts")
        self._add_primary_tab("week", self.week_calendar, "Weekly Schedule")
        self._add_primary_tab("year", self.year_calendar, "Yearly Calendar")
        self._add_primary_tab("projects", self.projects_widget, "Projects")

        # Drag a tab to move it. Presentation only: the tab bar is the sole
        # thing that changes, and every place that cares about a particular
        # tab reaches it by widget, never by index — see _on_main_tab_changed.
        self.main_tabs.setMovable(True)
        self.main_tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        self.setCentralWidget(self.main_tabs)
        self.setStatusBar(QStatusBar())
        self._build_save_state_widgets()

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")

        # Back Up Now, Export Backup…, automatic backups and the Backups &
        # Security window all make a backup through _run_backup →
        # backup.create_backup: one command, several routes.
        self.back_up_now_action = file_menu.addAction("Back Up Now")
        self.back_up_now_action.setToolTip("Makes a backup in the backup folder and checks it")
        self.back_up_now_action.triggered.connect(self._back_up_now)

        export_action = file_menu.addAction("Export Backup…")
        export_action.setToolTip("Makes a backup in a place you choose")
        export_action.triggered.connect(self._export_backup)

        import_action = file_menu.addAction("Restore from Backup…")
        import_action.triggered.connect(self._import_backup)

        self.backups_security_action = file_menu.addAction("Backups && Security…")
        self.backups_security_action.triggered.connect(self._open_backups_security)

        self.recovery_action = file_menu.addAction("Recovery…")
        self.recovery_action.setToolTip(
            "Text kept just before a large deletion in an entry or project")
        self.recovery_action.triggered.connect(self._open_recovery)

        archive_action = file_menu.addAction("Export Readable Archive (HTML)…")
        archive_action.setToolTip(
            "Writes a static website of your journal, Reader's Notes and calendar "
            "that opens in any browser without this application."
        )
        archive_action.triggered.connect(self._export_archive)

        file_menu.addSeparator()

        open_folder_action = file_menu.addAction("Open Data Folder")
        open_folder_action.triggered.connect(self._open_data_folder)

        usage_action = file_menu.addAction("Data Usage…")
        usage_action.triggered.connect(self._show_data_usage)

        cleanup_action = file_menu.addAction("Find Unused Photos…")
        cleanup_action.triggered.connect(self._find_unused_photos)

        file_menu.addSeparator()

        settings_action = file_menu.addAction("Settings…")
        settings_action.triggered.connect(self._open_settings)

        # There is no "Experimental Settings" entry. It existed to hold the
        # AI feature's controls and nothing else, so with that feature gone
        # the menu item would open an empty window — worse than no item.
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

        # There is no View → "Show This Day's Week" any more (Master Spec
        # §§29, 34): the Weekly Schedule already shows the selected date's
        # week, so the command had nothing left to do. The View menu held
        # only that command, so it is gone until the menu group gives it
        # real contents.

    def _on_main_tab_changed(self, _index: int):
        # Switching tabs doesn't unload anything — the journal editor and the
        # project editor both keep their content and their undo history — so
        # this is a flush, not a decision point, and it only happens when the
        # user has asked for timed saving.
        if autosave_enabled(self.db):
            self._save_current_entry(automatic=True)
            self.reader_notes.flush(automatic=True)
            self.projects_widget.flush()
        self._update_dirty_indicator()
        # Arriving at the Weekly Calendar re-reads the events (they may have
        # been edited from the Day View since it was last on screen) and
        # gives it keyboard focus, so Left/Right work without having to
        # click the timeline first.
        if self.main_tabs.currentWidget() is self.week_calendar:
            self.week_calendar.refresh()
            self.week_calendar._apply_pane_sizes()      # the shared month-pane width
            self.week_calendar.setFocus(Qt.OtherFocusReason)
        elif self.main_tabs.currentWidget() is self.daily_splitter:
            self._apply_pane_sizes()

    def _on_open_day_from_week(self, date_str: str):
        """The Weekly Calendar → Daily Journal handoff (Part 32).

        Explicit and one-way: clicking a day name in Week View asks the
        journal to select that day and brings it forward. The reverse does
        not happen implicitly — moving around the journal never drags the
        week window with it.
        """
        if not self._request_date(date_str):
            return
        self._show_daily_tab()

    def _on_open_week_from_year(self, date_str: str):
        """Yearly Calendar right-click / Shift-click (Master Spec §38.2):
        the selected date becomes that day, and the Weekly Schedule — which
        follows it — comes forward showing the week containing it."""
        if not self._request_date(date_str):
            return
        # Aligned here as well: when the day was already the selected date,
        # no date change arrives to align the week (B3).
        self.week_calendar.show_week_of(date_str)
        self.main_tabs.setCurrentWidget(self.week_calendar)
        self.week_calendar.setFocus(Qt.OtherFocusReason)

    # --------------------------------------------------------------- dates
    #
    # There is exactly one date variable in this window — self.selected_date
    # (a SelectedDate). Everything below either WRITES to it (the monthly
    # calendar, an internal date link, Back) or REACTS to it changing. No
    # code path loads a day without going through it, which is what
    # guarantees the journal, the day calendar and Reader's Notes can never
    # drift onto different days.
    @property
    def current_date(self) -> str:
        """The selected day, as an ISO string.

        Kept as a property rather than being renamed away: `current_date` is
        referenced throughout this window (and by existing tests) and reads
        naturally at every call site. It's now a view onto the canonical
        value rather than a second copy of it — assignment goes through the
        same setter everything else uses, so it cannot get out of step.
        """
        return self.selected_date.value

    @current_date.setter
    def current_date(self, value: str):
        self.selected_date.set(value)

    def _on_date_clicked(self, qdate: QDate):
        """The monthly calendar is the canonical date selector (Part 19).
        Ordinary browsing, so it deliberately does NOT push navigation
        history — only following a link does, otherwise 'Return to…' would
        point at whatever day happened to precede this one."""
        target = qdate.toString("yyyy-MM-dd")
        if not self._request_date(target):
            # Cancelled: put the calendar's own selection back, or it would
            # be sitting on a day the journal never moved to.
            self.calendar_panel.calendar.setSelectedDate(
                self.selected_date.qdate())

    def _on_selected_date_changed(self, date_str: str):
        """The single reaction point for a date change, wherever it came from.

        With autosave on, the outgoing day is flushed here — addressed to
        the day the text was typed into, never to the day just selected.
        With autosave off, the user has already been asked (see
        _request_date/_allow_leaving_entry), so there is nothing to flush
        and flushing anyway would be a timed save they switched off.
        """
        if self._loading_date:
            return
        if autosave_enabled(self.db):
            self._save_current_entry(date_override=self._last_loaded_date, automatic=True)
        # Leaving an entry is an editing boundary: if this visit changed it,
        # the state it was left in becomes a version (entry_history.py).
        if self._last_loaded_date and self._last_loaded_date != date_str:
            self.history.end_session("date", self._last_loaded_date, REASON_LEFT)
        self._load_date(date_str)

    def _load_date(self, date_str: str):
        """Renders `date_str` into the journal view.

        Also accepts being called as a navigation request. Before round 22
        this method WAS how you changed days, and several call sites (plus
        older tests) still use it that way — but rendering a day without
        moving the canonical SelectedDate would leave the journal showing
        one date while the day calendar and Reader's Notes showed another,
        which is precisely the drift this round exists to eliminate. So a
        call naming a different day is routed through the canonical setter,
        which comes straight back here with everything else already in step.
        """
        if date_str != self.selected_date.value:
            self.selected_date.set(date_str)
            return

        self._loading_date = True
        try:
            qdate = QDate.fromString(date_str, "yyyy-MM-dd")
            self.calendar_panel.set_selected_date(qdate)
            self.date_header.setText(human_long(date_str))

            entry = self.db.get_entry(date_str)
            self.editor.set_storage_subdir(qdate.toString("yyyy/MM"))
            # A date either has a written entry or it has none (Master Spec
            # §9.1). A stored row with nothing written in it — the blank rows
            # earlier versions wrote just by visiting a date, or an entry the
            # user emptied — opens as a genuinely empty editor, not as its
            # leftover blank paragraphs. The row itself is not rewritten.
            written = entry is not None and document_has_content(entry.body_md, entry.body_text)
            self.editor.load(entry.body_md if written else "",
                              entry.body_format if written else "html")
            self.calendar_panel.tag_picker.set_current(
                entry.tag if entry else None, entry.tag_color if entry else None
            )
            # The day calendar and Reader's Notes subscribe to
            # SelectedDate.changed themselves and have already refreshed by
            # the time this runs — they're not driven from here, which is
            # the point of having one canonical value rather than a window
            # that has to remember to poke every dependent widget.
            self._last_loaded_date = date_str
            self.history.begin_session("date", date_str)
            self.statusBar().showMessage(f"Viewing {human(date_str)}", 3000)
        finally:
            self._loading_date = False

    # -------------------------------------------------- internal date links
    def _on_date_link_activated(self, iso_date: str):
        """A journal://date/ link was clicked in the entry (Part 40).

        Order matters: the current entry is saved by the selected-date
        change itself (see _on_selected_date_changed), and the day we're
        leaving is pushed onto the history stack first so Return points at
        the right place even if the destination then links somewhere else.
        Links to a day with no entry yet work exactly like any other day —
        the editor simply opens empty (Part 43).
        """
        origin = self.current_date
        if iso_date == origin:
            return
        if not self._request_date(iso_date, push_history=True):
            return
        self._show_daily_tab()

    def _go_back(self):
        target = self.nav_history.back_target()
        if not target:
            return
        if not self._request_date(target):
            return   # cancelled: the trail is left exactly as it was
        self.nav_history.pop()
        self._show_daily_tab()

    def _update_back_button(self):
        target = self.nav_history.back_target()
        if target:
            self.back_button.setText(f"← Return to {human(target)}")
            self.back_button.setVisible(True)
        else:
            self.back_button.setVisible(False)

    def _schedule_autosave(self):
        """An edit happened. Whether that leads to a save depends on the
        preference; either way the window shows the document is dirty."""
        if autosave_enabled(self.db):
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()
        # The unsaved flag is NOT refreshed here: this runs on every
        # keystroke, and the flag only changes when a document's modified
        # state changes — which `modificationChanged` already reports.

    def _autosave_tick(self):
        """The autosave timer fired. Re-checked here as well as when the
        save was queued, because the preference can change in between."""
        if autosave_enabled(self.db):
            self._save_current_entry(automatic=True)

    # ------------------------------------------------ visible save state
    def _build_save_state_widgets(self):
        """The always-visible autosave switch and unsaved-changes flag
        (Master Spec §44.4), as permanent status-bar widgets so they are there
        in every workspace and are never replaced by a passing message."""
        self.autosave_action = QAction("Autosave", self)
        self.autosave_action.setCheckable(True)
        self.autosave_action.setChecked(autosave_enabled(self.db))
        self.autosave_action.setToolTip(
            "Autosave on: your writing is saved a moment after you stop typing.\n"
            "Autosave off: nothing is written until you press Ctrl+S, and you are "
            "asked before unsaved changes would be lost.")
        self.autosave_action.toggled.connect(self.set_autosave)
        self.autosave_button = QToolButton()
        self.autosave_button.setDefaultAction(self.autosave_action)
        self.autosave_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.autosave_button.setObjectName("AutosaveToggle")

        self.unsaved_label = QLabel("● Unsaved changes — Ctrl+S to save")
        self.unsaved_label.setObjectName("UnsavedIndicator")
        self.unsaved_label.setVisible(False)

        # Shown only when an automatic backup is overdue or the last one failed.
        self._backup_indicator = QLabel("")
        self._backup_indicator.setObjectName("BackupIndicator")
        self._backup_indicator.setVisible(False)

        self.statusBar().addPermanentWidget(self._backup_indicator)
        self.statusBar().addPermanentWidget(self.unsaved_label)
        self.statusBar().addPermanentWidget(self.autosave_button)
        self._sync_autosave_widgets()

        # Every editable document reports its own modified-state changes, so
        # the indicator follows typing, saving, discarding and loading alike.
        for editor in (self.editor, self.reader_notes.editor,
                       self.projects_widget.editor,
                       self.projects_widget.reader_notes.editor):
            editor.text_edit.document().modificationChanged.connect(
                lambda _modified: self._update_dirty_indicator())

    def _sync_autosave_widgets(self):
        enabled = autosave_enabled(self.db)
        blocked = self.autosave_action.blockSignals(True)
        self.autosave_action.setChecked(enabled)
        self.autosave_action.blockSignals(blocked)
        self.autosave_action.setText("Autosave: On" if enabled else "Autosave: Off")

    def set_autosave(self, enabled: bool):
        """THE way autosave is switched — the status-bar toggle and the
        Settings checkbox both come here, so the preference, both controls,
        the timers and the unsaved flag always agree."""
        set_autosave_enabled(self.db, bool(enabled))
        self._sync_autosave_widgets()
        if enabled:
            # Switching autosave on with unsaved work pending: save it soon,
            # exactly as if the user had just typed.
            if self.editor.is_dirty():
                self._autosave_timer.start()
            if self.reader_notes.is_dirty():
                self.reader_notes._autosave_timer.start()
            if self.projects_widget.is_dirty():
                self.projects_widget._autosave_timer.start()
        else:
            self._autosave_timer.stop()
        self._update_dirty_indicator()

    def _active_documents_dirty(self) -> bool:
        if self.main_tabs.currentWidget() is self.projects_widget:
            return (self.projects_widget.is_dirty()
                    or self.projects_widget.reader_notes.is_dirty())
        return self.editor.is_dirty() or self.reader_notes.is_dirty()

    def _any_document_dirty(self) -> bool:
        return (self.editor.is_dirty() or self.reader_notes.is_dirty()
                or self.projects_widget.is_dirty()
                or self.projects_widget.reader_notes.is_dirty())

    def _update_dirty_indicator(self):
        """Shows whether anything is unsaved.

        The window title's "*" marks unsaved work anywhere. The status-bar
        flag is for the workspace in front, and only with autosave off — with
        it on, the answer is always "saved a moment from now" and a permanent
        flag would be noise.
        """
        if not hasattr(self, "unsaved_label"):
            return
        autosave = autosave_enabled(self.db)
        self.unsaved_label.setVisible(not autosave and self._active_documents_dirty())
        self.setWindowModified(self._any_document_dirty())

    def _save_current_entry(self, date_override: str | None = None,
                            automatic: bool = False) -> bool:
        """Writes the editor's contents back to the day they belong to.

        THE save path for a journal entry: Ctrl+S calls it, the autosave
        timer calls it, and leaving a dirty day calls it. There is no second
        implementation (Part 24/34), which is also what makes the dirty flag
        meaningful — exactly one place clears it.

        `automatic` callers (the timer, changing date or tab, closing,
        exports) only write when the user actually edited the entry, so
        looking at a date never writes anything. Explicit callers (Ctrl+S, a
        "Save" answer) write whenever the text differs from what is stored.

        `date_override` exists for one case: the selected date has already
        moved, and we're flushing the entry the user was typing into a
        moment ago. Saving that text under the NEW date would silently copy
        one day's entry onto another.
        """
        target = date_override or self.current_date
        if not target:
            return False
        if automatic and not self.editor.is_dirty():
            return True
        html, plain = self.editor.save()
        changed = self._store_entry(target, html, "html", plain)
        self.editor.mark_clean()
        if changed:
            self._refresh_calendar_marks()
        self._update_dirty_indicator()
        return True

    def _store_entry(self, date: str, html: str, fmt: str, plain: str,
                     detect_removal: bool = True) -> bool:
        """The one write of a journal entry's body. Returns whether anything
        was written.

        Either a date has a written entry, or it has none (Master Spec §9.1):

          * a blank document never CREATES an entry row — selecting a date,
            or typing and deleting, leaves no trace;
          * emptying a written entry (select all, delete) stores it as "" —
            the entry is gone and so is its marker; the text it had is kept
            by version history and, when a lot was removed, by a recovery
            checkpoint;
          * an unchanged document is not re-written.

        The version-history and recovery hooks run here, around the write,
        so every route that stores an entry gets them.
        """
        written = document_has_content(html, plain)
        if not written:
            html, fmt, plain = "", "html", ""
        stored = self.db.get_entry(date)
        if stored is None and not written:
            return False
        if stored is not None and stored.body_md == html and (stored.body_format or "html") == fmt:
            return False
        title = stored.title if stored is not None else ""
        before = (stored_state(stored.body_md, stored.body_format, stored.body_text, stored.title)
                  if stored is not None else None)
        self.history.before_write("date", date, before, html, plain, title=title,
                                  detect_removal=detect_removal)
        self.db.upsert_entry(date, body_md=html, body_format=fmt, body_text=plain)
        self.history.after_write("date", date, html, fmt, plain, title=title)
        return True

    # --------------------------------------------------------- saving UX
    def _save_active_workspace(self):
        """Ctrl+S: save whatever is editable in the workspace in front.

        Saves the journal entry and Reader's Notes together on the Daily
        Journal tab, and the project and its notes together on Projects —
        they are what is on screen, and asking the user which pane had focus
        would be a worse answer than just saving both. Each goes through its
        own ordinary save path; none of them is a Ctrl+S-only shortcut.
        """
        current = self.main_tabs.currentWidget()
        saved = []
        if current is self.projects_widget:
            if self.projects_widget.save_now():
                saved.append("project")
            if self.projects_widget.reader_notes.flush(force=True):
                saved.append("Reader's Notes")
        else:
            self._save_current_entry()
            saved.append("journal entry")
            if self.reader_notes.flush(force=True):
                saved.append("Reader's Notes")
        self.statusBar().showMessage("Saved " + " and ".join(saved) + ".", 2500)

    def _request_date(self, date_str: str, push_history: bool = False) -> bool:
        """Every user-initiated move to another day goes through here.

        The guard has to run BEFORE the selection changes: `SelectedDate`
        notifies after the fact, and by then the choice to cancel no longer
        exists. So the single canonical setter is still the only thing that
        changes the date — this just asks permission first.
        """
        if date_str == self.current_date:
            return True
        if not self._allow_leaving_entry():
            return False
        if push_history:
            self.nav_history.push(self.current_date)
        return self.selected_date.set(date_str)

    def _allow_leaving_entry(self) -> bool:
        """Save / Discard / Cancel for the journal entry (Part 26).

        Silent when there is nothing to lose: with autosave on, or with a
        clean document, this never prompts.
        """
        if autosave_enabled(self.db) or not self.editor.is_dirty():
            return True
        answer = ask_unsaved(self, "this journal entry")
        if answer == CANCEL:
            return False
        if answer == SAVE:
            return self._save_current_entry()      # explicit: the user said Save
        # Discard: reload the day from what is actually stored, so the
        # editor stops holding changes the user has said they don't want.
        # The SAVED entry is untouched — discard never deletes it.
        self._load_date(self.current_date)
        return True

    def _on_tag_changed(self, tag: str, color: str):
        if tag:
            self.db.upsert_entry(self.current_date, tag=tag, tag_color=color)
        else:
            self.db.clear_tag(self.current_date)
        self._refresh_calendar_marks()

    def _refresh_calendar_marks(self):
        """Re-reads what the month grid should show.

        Called after anything that could change it — a save, a marker edit,
        a calendar change, paging the month — so the indicators are never
        waiting on a restart or a month change to catch up (Part 21).
        """
        year = self.calendar_panel.calendar.yearShown()
        month = self.calendar_panel.calendar.monthShown()
        start = QDate(year, month, 1)
        end = QDate(year, month, start.daysInMonth())
        start_str, end_str = start.toString("yyyy-MM-dd"), end.toString("yyyy-MM-dd")

        # What the grid marks comes from the database's day metadata — the
        # one content rule, no document bodies loaded (calendar_widget.
        # month_marks, shared by every month grid).
        entry_dates, tag_colors, lengths, other_dates = month_marks(self.db, start_str, end_str)
        self.calendar_panel.calendar.set_month_data(
            entry_dates, tag_colors, lengths, other_content_dates=other_dates)
        self.calendar_panel.reload_month_title()

        # Both calendar views read the same tables, so anything that changes
        # the marks here changes what they show — re-read both, in both
        # directions. An event dragged in Week View is on the Day View's
        # timeline the moment this runs, and vice versa (Part 26: two
        # presentations of one event store, so keeping them current is a
        # re-read and never a copy between two stores).
        if hasattr(self, "day_calendar"):
            self.day_calendar.refresh()
        if hasattr(self, "week_calendar"):
            self.week_calendar.refresh()
        if hasattr(self, "year_calendar"):
            self.year_calendar.refresh()      # deferred while not on screen

    # ------------------------------------------------------------ settings
    def _apply_settings(self):
        family = self.db.get_setting("font_family", "Georgia")
        size = int(self.db.get_setting("font_size", "13"))
        # Fresh install (nothing saved yet): fall back to whatever the
        # native/current interface font size already is, so first launch
        # doesn't silently shrink or grow the UI before anyone's touched
        # Settings — it only changes once the user deliberately picks a size.
        default_ui_size = QApplication.instance().font().pointSize()
        ui_size = int(self.db.get_setting("ui_font_size", str(default_ui_size)))
        scheme = scheme_from_json(self.db.get_setting("color_scheme"))

        # Order here is load-bearing, and both halves of it were learned
        # from real bugs.
        #
        # 1. The APPLICATION font first. QApplication.setFont() propagates to
        #    existing widgets only on the next event cycle — so when it came
        #    after the stylesheet, every ordinary widget kept painting with
        #    the PREVIOUS size until something else happened to repolish it.
        #    Measured: setting the UI size to 16 left the day view's own font
        #    at 9, and setting it to 22 left it at 16 — always one change
        #    behind. That is why event titles, hour labels and similar text
        #    appeared not to follow the setting at all.
        # 2. The stylesheet second. Applying it repolishes every widget,
        #    which delivers the new application font immediately instead of
        #    a cycle later — so the two steps together make the change take
        #    effect at once, everywhere.
        # 3. Explicit WIDGET fonts (the writing font) last, because that same
        #    repolish resets any widget font set before it. Reversing 2 and 3
        #    is why writing-font-size changes once silently did nothing.
        # 0. At startup there is no stylesheet yet, and Qt drops an
        #    application-font change made in the same breath as installing
        #    the first one: a saved interface size of 20 left every widget
        #    at the system size until the next change (Group 3 fixes, C4).
        #    Installing the stylesheet first makes startup the same as any
        #    later change: font, then stylesheet.
        sheet = build_stylesheet(scheme, ui_size)
        if not QApplication.instance().styleSheet():
            QApplication.instance().setStyleSheet(sheet)
        self._apply_app_font_size(ui_size)
        QApplication.instance().setStyleSheet(sheet)
        self.editor.set_font(family, size)
        # Part 18: applies to the journal's main writing surface only —
        # Reader's Notes is a short companion area where repositioning the
        # view while typing would be noise rather than help.
        self.editor.set_writing_position(
            self.db.get_setting("writing_position", "free")
        )
        self.projects_widget.apply_font(family, size)
        self.calendar_panel.calendar.set_scheme(scheme)
        # Both calendars resolve a theme-following event's colour from this
        # scheme. They used to read it from the widget palette, which a Qt
        # stylesheet never touches — so every "theme" event was Qt's own
        # default blue in every theme. See theme.event_color().
        self.day_calendar.apply_theme(scheme)
        # The Weekly Calendar's navigator is the same calendar class and
        # needs the same theme — events that follow the theme colour are
        # resolved at paint time in both views (see event_render.py), so
        # nothing else has to be told about a theme change.
        self.week_calendar.apply_theme(scheme)
        self.year_calendar.apply_theme(scheme)
        # Numeric toolbar controls size themselves from the widest value they
        # can show, which depends on the current application font — so they
        # have to re-measure whenever it changes or they start clipping again.
        self.editor.refresh_control_sizes()
        self.projects_widget.editor.refresh_control_sizes()
        self.reader_notes.editor.refresh_control_sizes()
        self.projects_widget.reader_notes.editor.refresh_control_sizes()
        self.calendar_panel.apply_compact_headers()
        self.week_calendar.apply_compact_headers()

        # Calendar preferences live in the database like every other setting,
        # but both timelines hold a CalendarPrefs OBJECT and repaint from its
        # `changed` signal — so writing the setting is only half of applying
        # it. Without this line "Highlight work hours" was persisted correctly
        # and had no visible effect until the next launch, because nothing
        # ever told the in-memory object to re-read. This is the one place
        # every settings change arrives, so it is the right place to say so;
        # the alternative, rebuilding the calendars, would throw away their
        # scroll position and selection to redraw a background tint.
        self.calendar_prefs.reload()
        if hasattr(self, "autosave_action"):
            self._sync_autosave_widgets()
            self._update_dirty_indicator()

        if ui_size != self._applied_ui_size:
            self._applied_ui_size = ui_size
            self._rebalance_panes()

    def _rebalance_panes(self):
        """Lays the panes out for the current interface font size (Parts 13
        and 42) — at startup and whenever the font size changes.

        A pane the user has dragged keeps the width they gave it (it is
        stored, see _on_daily_splitter_moved); only panes still at their
        defaults are re-proportioned, and Qt's own minimum sizes grow a pane
        that the new font would otherwise clip. Deferred by one event-loop
        turn, because right after construction or a font change the
        splitters report stale widths.
        """
        QTimer.singleShot(0, self._apply_pane_sizes)
        if hasattr(self, "projects_widget"):
            self.projects_widget.rebalance_panes()
        if hasattr(self, "week_calendar"):
            self.week_calendar.rebalance_panes()

    def showEvent(self, event):
        """Sizes the panes again once the window is really on screen, when
        the splitter finally knows its width. Harmless to repeat: sizing
        reads the user's remembered widths, so it can never undo them."""
        super().showEvent(event)
        self._apply_pane_sizes()

    def _apply_pane_sizes(self):
        """Daily Jorts' three panes: the month pane at the shared remembered
        width, the Day Calendar pane at its remembered width, the writing
        pane taking the rest — or the default proportions for whichever was
        never dragged. Because every sizing path goes through here and reads
        the remembered values, the order in which startup, restore and
        font-change sizing happen no longer matters (D1, FP-5)."""
        splitter = self.daily_splitter
        total = splitter.width() or self.width() or 1280
        left = saved_width(self.db, MONTHLY_PANE_WIDTH_SETTING)
        right = saved_width(self.db, DAY_PANE_WIDTH_SETTING)
        # 0 = dragged shut, and kept shut (GF-4); None = never dragged.
        if left is None:
            left = side_pane_width(340, total, max_fraction=0.30)
        if right is None:
            right = side_pane_width(280, total, max_fraction=0.26)
        middle = max(240, total - left - right)
        splitter.setSizes([left, middle, right])

    def _on_daily_splitter_moved(self, _pos: int, _index: int):
        """The user dragged a Daily Jorts divider: remember the month pane
        (shared with the Weekly Schedule, which follows at once) and the Day
        Calendar pane."""
        # A pane dragged shut is remembered as 0 and stays shut (GF-4).
        sizes = self.daily_splitter.sizes()
        self.db.set_setting(MONTHLY_PANE_WIDTH_SETTING, str(max(0, sizes[0])))
        self.db.set_setting(DAY_PANE_WIDTH_SETTING, str(max(0, sizes[2])))
        self.week_calendar._apply_pane_sizes()

    def _on_week_splitter_moved(self, _pos: int, _index: int):
        """The user dragged the Weekly Schedule divider: the month pane
        width is shared, so Daily Jorts follows at once."""
        width = self.week_calendar.splitter.sizes()[
            self.week_calendar.splitter.indexOf(self.week_calendar.nav_panel)]
        self.db.set_setting(MONTHLY_PANE_WIDTH_SETTING, str(max(0, width)))
        self._apply_pane_sizes()

    def _save_window_state(self):
        """Remembers the window and the panes, in the settings table.

        Stored as base64 of Qt's own geometry/state blobs rather than as
        numbers we interpret ourselves — Qt already handles multi-monitor
        setups, DPI and maximized state correctly, and reimplementing that
        badly is how a window ends up restored off-screen.
        """
        from PySide6.QtCore import QByteArray
        try:
            self.db.set_setting(
                "window_geometry",
                bytes(self.saveGeometry().toBase64()).decode("ascii"))
            self.db.set_setting("window_maximized", "1" if self.isMaximized() else "0")
            for key, splitter in self._named_splitters():
                self.db.set_setting(
                    f"splitter_{key}",
                    bytes(splitter.saveState().toBase64()).decode("ascii"))
        except Exception:  # noqa: BLE001 — a layout preference is never worth a crash
            pass

    def _restore_window_state(self):
        """Puts them back, if there is anything to put back.

        Anything unreadable is ignored rather than repaired: the cost of
        getting this wrong is a window in a strange place, so a bad value
        falls back to the ordinary default size.
        """
        from PySide6.QtCore import QByteArray
        try:
            blob = self.db.get_setting("window_geometry")
            if blob:
                self.restoreGeometry(QByteArray.fromBase64(blob.encode("ascii")))
            if self.db.get_setting("window_maximized", "0") == "1":
                self.showMaximized()
            self._restore_tab_order()
            for key, splitter in self._named_splitters():
                state = self.db.get_setting(f"splitter_{key}")
                if state:
                    splitter.restoreState(QByteArray.fromBase64(state.encode("ascii")))
        except Exception:  # noqa: BLE001
            pass
        # The Daily and Weekly panes come from their remembered widths.
        self._apply_pane_sizes()

    def _named_splitters(self):
        """The splitters remembered as Qt state blobs, by settings key.

        Only Projects now. Daily Jorts and the Weekly Schedule remember pane
        WIDTHS instead (layout_monthly_pane_width, shared by both, and
        layout_day_pane_width; see _apply_pane_sizes), written the moment a
        divider is dragged. Their old blob keys — `splitter_daily`,
        `splitter_week_navleft` and the even older `splitter_week` — are left
        in the settings table and never read: they were being overridden by
        the default sizing anyway (D1), so nothing a user saw is lost.
        """
        return [
            ("projects", self.projects_widget.splitter),
        ]

    # ------------------------------------------------------- main tab order
    #
    # Remembered as a comma-separated list of tab keys, e.g. "week,daily,
    # projects", in the settings table — the same place the window geometry
    # and splitter positions live. It is a UI preference and touches no
    # entry, project, event or ToDo.
    #
    # Keys, not indices, for one reason: indices stop meaning anything the
    # moment the set of tabs changes. If a fourth tab is added later, a
    # stored "2,0,1" is ambiguous at best and silently wrong at worst,
    # whereas a stored list of keys is merely incomplete — and incomplete is
    # something _apply_tab_order can handle exactly, by keeping the order the
    # user chose for the tabs it names and putting any it doesn't after them.

    TAB_ORDER_SETTING = "main_tab_order"

    def _add_primary_tab(self, key: str, widget, label: str):
        self._tab_keys[widget] = key
        self.main_tabs.addTab(widget, label)

    def _show_daily_tab(self):
        """Bring Daily Notes forward.

        By widget, never by index. Three places used to say
        `setCurrentIndex(0)` — the Weekly Calendar's day-name handoff, an
        internal date link, and Back — which was correct only for as long as
        Daily Notes was guaranteed to be the leftmost tab. The moment tabs
        became draggable that assumption became a bug that would show up as
        "clicking a day name in Week View opens Projects".
        """
        self.main_tabs.setCurrentWidget(self.daily_splitter)

    def _current_tab_order(self) -> list:
        """The keys of the primary tabs, left to right as they are now."""
        return [self._tab_keys[self.main_tabs.widget(i)]
                for i in range(self.main_tabs.count())
                if self.main_tabs.widget(i) in self._tab_keys]

    def _on_tab_moved(self, _from_index: int, _to_index: int):
        """Written the moment a tab is dropped, not at shutdown.

        A preference the user set with a deliberate gesture should not be
        lost because the process died before it could close tidily.

        Qt emits tabMoved for a programmatic moveTab too, so the restore
        path sets a flag: without it, applying a saved order would write
        each intermediate arrangement back over the saved one.
        """
        if self._applying_tab_order:
            return
        try:
            self.db.set_setting(self.TAB_ORDER_SETTING,
                                ",".join(self._current_tab_order()))
        except Exception:  # noqa: BLE001 — a tab order is never worth a crash
            pass

    def _restore_tab_order(self):
        saved = self.db.get_setting(self.TAB_ORDER_SETTING)
        if saved:
            self._apply_tab_order([k for k in saved.split(",") if k])

    def _apply_tab_order(self, wanted: list):
        """Reorder the tab bar to `wanted`, tolerating a stale saved list.

        Three things can be wrong with a list that was written by an older
        or newer version, and each has a defined answer:

          * it names a tab that no longer exists  -> ignored;
          * it omits a tab that exists now (a tab added by a later version)
            -> it goes after the ones the user did arrange, rather than
            displacing them. A new tab appearing at the end is predictable;
            a new tab inserted into the middle rearranges a layout the user
            chose deliberately;
          * it repeats a key                      -> the repeat is ignored.

        Moves are done with QTabBar.moveTab, which carries the page widget,
        its label and its state with it. Re-inserting the pages instead would
        re-parent live editors, and re-parenting a QTextEdit is how you lose
        an undo stack for a cosmetic change.
        """
        bar = self.main_tabs.tabBar()
        self._applying_tab_order = True
        try:
            self._reorder_tabs(bar, wanted)
        finally:
            self._applying_tab_order = False

    def _reorder_tabs(self, bar, wanted: list):
        seen = set()
        target = []
        for key in wanted:                      # named tabs, in the saved order
            if key in seen:
                continue
            seen.add(key)
            if key in self._tab_keys.values():
                target.append(key)
        for key in self._current_tab_order():   # anything new, appended
            if key not in seen:
                seen.add(key)
                target.append(key)

        for position, key in enumerate(target):
            current = self._current_tab_order()
            if current[position] == key:
                continue
            bar.moveTab(current.index(key), position)

    def _apply_app_font_size(self, size: int):
        """The app's own interface text (buttons, labels, calendar, task
        list, menus) — distinct from the writing font above, and from zoom,
        which only affects the writing editor's on-screen text."""
        app = QApplication.instance()
        font = app.font()
        font.setPointSize(size)
        app.setFont(font)

    def _open_settings(self):
        """Every setting lives here, and each one applies as it changes —
        `on_change` is the single notification point, which is what makes a
        preference like work-hours highlighting visible immediately rather
        than on the next launch (see _apply_settings)."""
        dlg = SettingsDialog(self.db, on_change=self._apply_settings, parent=self,
                             set_autosave=self.set_autosave,
                             open_backups=self._open_backups_security)
        dlg.exec()

    # ------------------------------------------------------- backup/export
    def _flush_all(self):
        """Writes every open document before a backup or export, whatever the
        autosave preference says: a backup of what is on disk, taken while
        an editor holds newer text, is a backup of the wrong thing."""
        self._save_current_entry(automatic=True)
        self.reader_notes.flush(automatic=True)
        self.projects_widget.flush()

    def _picker_dir(self, key: str, fallback: Path) -> str:
        stored = self.db.get_setting(key, None)
        if stored and Path(stored).is_dir():
            return stored
        return str(fallback)

    def _remember_picker_dir(self, key: str, chosen: Path):
        self.db.set_setting(key, str(chosen))

    def _run_backup(self, kind: str, destination: Path | None = None,
                    quiet: bool = False):
        """The one way a backup is made from the interface. Returns the
        summary, or None if it failed (the reason is shown, or with `quiet`,
        put in the status bar and kept for the Backups & Security window)."""
        self._flush_all()
        self._refresh_unencrypted_copy(force=True)
        progress = QProgressDialog("Making a backup…", None, 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0 if not quiet else 800)

        def on_progress(done, total):
            progress.setValue(int(done / total * 100) if total else 100)
            QApplication.processEvents()

        try:
            summary = busy(lambda: backup.create_backup(kind, destination, on_progress))
        except backup.BackupPaused as exc:
            # Encrypted backups were chosen but no backup passphrase exists:
            # no backup rather than an unencrypted one. Asked backups offer
            # to set it up now; automatic ones just say why they are waiting.
            progress.close()
            self._update_backup_indicator()
            if quiet:
                self.statusBar().showMessage(f"Automatic backups are paused. {exc}", 15000)
                return None
            answer = QMessageBox.question(
                self, "Backups are paused", f"{exc}\n\nSet a backup passphrase now?")
            if answer == QMessageBox.Yes and self._set_up_backup_encryption(back_up_after=False):
                return self._run_backup(kind, destination, quiet)
            return None
        except Exception as exc:
            progress.close()
            security.update_config(get_data_dir(), last_error=f"{_now()}: {exc}")
            if quiet:
                self.statusBar().showMessage(f"Automatic backup failed: {exc}", 15000)
            else:
                QMessageBox.critical(self, "Backup failed",
                                     f"{exc}\n\nNo backup was recorded as made.")
            self._update_backup_indicator()
            return None
        progress.close()
        self._update_backup_indicator()
        return summary

    def _backup_summary_text(self, summary) -> str:
        lines = [f"Saved to:\n{summary.zip_path}"]
        if summary.plain_path:
            lines.append(f"Unencrypted copy:\n{summary.plain_path}")
        lines.append(
            f"{summary.entry_count} journal entries, {summary.event_count} calendar events, "
            f"{summary.notes_count} set(s) of Reader's Notes, {summary.photo_count} photos\n"
            f"Size: {human_size(summary.size_bytes)}")
        lines.append("Encrypted: yes" if summary.encrypted else "Encrypted: no")
        if summary.verification == backup.VERIFIED_WRITTEN:
            lines.append("The backup was checked before it was encrypted, and the encrypted "
                         "file was read back and found identical. (A full check decrypts it "
                         "again; that needs the backup passphrase.)")
        else:
            lines.append("The backup was read back and checked.")
        return "\n\n".join(lines)

    def _back_up_now(self):
        summary = self._run_backup("manual")
        if summary is not None:
            QMessageBox.information(self, "Backup created", self._backup_summary_text(summary))

    def _export_backup(self):
        encrypted = security.backups_encrypted(get_data_dir())
        suffix = backup.ENCRYPTED_SUFFIX if encrypted else backup.PLAIN_SUFFIX
        name = Path(default_backup_filename()).stem + suffix
        start = self._picker_dir(PICKER_EXPORT_BACKUP, backup.backup_dir())
        pattern = ("Encrypted backups (*.jcbackup)" if encrypted else "Zip files (*.zip)")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Backup", str(Path(start) / name), pattern)
        if not path:
            return
        destination = Path(path)
        if destination.suffix != suffix:
            destination = destination.with_name(destination.stem + suffix)
        self._remember_picker_dir(PICKER_EXPORT_BACKUP, destination.parent)
        summary = self._run_backup("manual", destination)
        if summary is not None:
            QMessageBox.information(self, "Backup created", self._backup_summary_text(summary))

    def start_background_tasks(self):
        """Called by the entry point once the window is showing: the one-time
        storage question, then the automatic-backup check, now and hourly."""
        self._backup_timer.start()
        QTimer.singleShot(500, self._startup_backup_tasks)

    def _startup_backup_tasks(self):
        cfg = security.load_config(get_data_dir())
        if cfg.get("storage_choice") is None:
            self._ask_storage_choice()
        self._automatic_backup_if_due()
        self._update_backup_indicator()

    def _ask_storage_choice(self):
        """The one-time question. "Encrypted" is recorded as the choice before
        the passphrase is asked for, so cancelling the passphrase leaves
        backups paused (never quietly unencrypted). Closing the question
        records nothing; it is asked again at the next launch."""
        dlg = StorageChoiceDialog(self)
        dlg.exec()
        if dlg.choice == StorageChoiceDialog.ENCRYPTED:
            security.update_config(get_data_dir(), storage_choice="encrypted")
            self._set_up_backup_encryption(back_up_after=False)
        elif dlg.choice == StorageChoiceDialog.UNENCRYPTED:
            self._keep_backups_unencrypted()

    def _keep_backups_unencrypted(self):
        security.update_config(get_data_dir(), storage_choice="unencrypted")
        self._update_backup_indicator()

    def _automatic_backup_if_due(self):
        data_dir = get_data_dir()
        try:
            due = backup.automatic_backup_due(data_dir)
        except Exception:
            due = False
        if not due:
            return
        security.update_config(data_dir, last_automatic_attempt=_now())
        summary = self._run_backup("automatic", quiet=True)
        if summary is not None:
            self.statusBar().showMessage(
                f"Automatic backup made and checked: {summary.zip_path.name}", 8000)

    def _update_backup_indicator(self):
        label = getattr(self, "_backup_indicator", None)
        if label is None:
            return
        try:
            st = backup.status(get_data_dir())
        except Exception:
            return
        if st.paused_reason:
            label.setText("Backups paused")
            label.setToolTip(st.paused_reason)
            label.show()
        elif st.last_error and (not st.last_success or st.overdue):
            label.setText("Backup failed")
            label.setToolTip(st.last_error)
            label.show()
        elif st.overdue:
            label.setText("Backup overdue")
            label.setToolTip("File → Back Up Now, or see File → Backups & Security")
            label.show()
        else:
            label.hide()

    def _open_backups_security(self):
        dlg = BackupsSecurityDialog({
            "back_up_now": self._back_up_now,
            "set_up_database_encryption": self._set_up_database_encryption,
            "set_up_backup_encryption": self._set_up_backup_encryption,
            "change_database_passphrase": self._change_database_passphrase,
            "change_backup_passphrase": self._change_backup_passphrase,
            "keep_backups_unencrypted": self._keep_backups_unencrypted,
            "set_keep_copies": self._set_keep_unencrypted_copies,
        }, parent=self)
        dlg.exec()
        self._update_backup_indicator()

    # ---------------------------------------------------------- encryption
    def _reopen_database(self, operation):
        """Closes the database, runs `operation()`, and opens it again, with
        every holder re-pointed. The editors are saved first; with autosave
        off, unsaved work gets the usual Save / Discard / Cancel. Returns
        (ok, result or exception). The database is always reopened, whether
        or not the operation succeeded."""
        if not self._allow_leaving_entry():
            return False, None
        if not self.projects_widget.allow_leaving_project():
            return False, None
        self._flush_all()
        self._autosave_timer.stop()
        self._mirror_timer.stop()
        self.reader_notes.stop_timers()
        self.projects_widget.stop_timers()
        self.history.end_all_sessions(REASON_LEFT)
        self.history.reset()
        old_db = self.db
        self.db.close()
        try:
            result, ok = operation(), True
        except Exception as exc:        # reported by the caller
            result, ok = exc, False
        self.db = Database()
        self._rebind_database(old_db)
        self._reload_everything()
        self._mirror_changes = -1
        self._mirror_timer.start()
        return ok, result

    def _set_up_database_encryption(self) -> bool:
        """Encrypts the journal database with its own passphrase (or, if the
        user ticks it, the same one as backups)."""
        data_dir = get_data_dir()
        if security.is_encrypted_install(data_dir):
            return False
        backups_enc = security.backups_encrypted(data_dir)
        dlg = NewPassphraseDialog(
            "Encrypt the Journal Database",
            "Choose a database passphrase. It encrypts the journal database; you will "
            "enter it each time jortle_claude starts. Backups are separate: they have "
            "their own passphrase.", subject=backup_dialog.DATABASE,
            same_as=backup_dialog.BACKUPS if backups_enc else None,
            verify_same=(lambda text: security.passphrase_opens(data_dir, "backup", text))
            if backups_enc else None, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return False
        passphrase = dlg.value()
        ok, result = self._reopen_database(
            lambda: busy(lambda: security.set_up_database_encryption(data_dir, passphrase)))
        if not ok:
            if result is not None:
                QMessageBox.critical(
                    self, "The database was not encrypted",
                    f"{result}\n\nYour journal is unchanged and still unencrypted.")
            return False
        backup.copy_key_to_backup_folder(data_dir)
        leftovers = security.unencrypted_leftovers(data_dir)
        text = "Your journal database is now encrypted."
        if not backups_enc:
            text += ("\n\nBackups are still NOT encrypted. To encrypt them too, use "
                     "\"Encrypt Backups…\" in File → Backups & Security.")
        text += ("\n\nOlder unencrypted copies are not deleted automatically. "
                 "File → Backups & Security lists them.")
        if leftovers:
            text += f" ({len(leftovers)} found.)"
        QMessageBox.information(self, "Database encrypted", text)
        return True

    def _set_up_backup_encryption(self, back_up_after: bool = True) -> bool:
        """Turns backup encryption on with its own passphrase (or, if the
        user ticks it, the same one as the database)."""
        data_dir = get_data_dir()
        if security.backups_encrypted(data_dir):
            return False
        db_enc = security.is_encrypted_install(data_dir)
        dlg = NewPassphraseDialog(
            "Encrypt Backups",
            "Choose a backup passphrase. Every backup made from now on is encrypted; "
            "making one never needs the passphrase, restoring one does. Existing "
            "backups are not changed.", subject=backup_dialog.BACKUPS,
            same_as=backup_dialog.DATABASE if db_enc else None,
            verify_same=(lambda text: security.passphrase_opens(data_dir, "database", text))
            if db_enc else None, parent=self)
        if dlg.exec() != QDialog.Accepted:
            self._update_backup_indicator()
            return False
        passphrase = dlg.value()
        try:
            busy(lambda: security.set_up_backup_encryption(data_dir, passphrase))
        except Exception as exc:
            QMessageBox.critical(self, "Backups were not encrypted", str(exc))
            self._update_backup_indicator()
            return False
        backup.copy_key_to_backup_folder(data_dir)
        self._update_backup_indicator()
        text = "Backups are now encrypted."
        if back_up_after:
            summary = self._run_backup("manual")
            if summary is not None:
                text += f"\n\nAn encrypted backup was made and checked:\n{summary.zip_path}"
        text += ("\n\nBackups made before now are not encrypted and are not changed. "
                 "File → Backups & Security lists them.")
        QMessageBox.information(self, "Backups encrypted", text)
        return True

    def _change_passphrase_of(self, which: str):
        data_dir = get_data_dir()
        if which == backup_dialog.DATABASE:
            title = "Change Database Passphrase"
            intro = ("The database keeps its key; only the passphrase that protects the "
                     "key changes. You will enter the new one when jortle_claude starts.")
            change = security.change_database_passphrase
        else:
            title = "Change Backup Passphrase"
            intro = ("Backups keep their key; only the passphrase that protects it "
                     "changes. Each older backup still opens with the passphrase it was "
                     "made with — or with backup-key.age from the backup folder, which "
                     "is updated now, with the new one.")
            change = security.change_backup_passphrase
        dlg = NewPassphraseDialog(title, intro, subject=which, ask_current=True, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return False
        try:
            busy(lambda: change(data_dir, dlg.current_value(), dlg.value()))
        except security.WrongPassphrase:
            QMessageBox.warning(self, title,
                                "The current passphrase is not correct. Nothing was changed.")
            return False
        except Exception as exc:
            QMessageBox.critical(self, title, f"{exc}\n\nThe passphrase was not changed.")
            return False
        backup.copy_key_to_backup_folder(data_dir)
        QMessageBox.information(self, title, "The passphrase has been changed.")
        return True

    def _change_database_passphrase(self):
        return self._change_passphrase_of(backup_dialog.DATABASE)

    def _change_backup_passphrase(self):
        return self._change_passphrase_of(backup_dialog.BACKUPS)

    def _set_keep_unencrypted_copies(self, enabled: bool) -> bool:
        data_dir = get_data_dir()
        if enabled:
            answer = QMessageBox.warning(
                self, "Keep unencrypted copies", UNENCRYPTED_COPIES_WARNING,
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer not in (QMessageBox.Ok, QMessageBox.Yes):
                return False
            if security.backups_encrypted(data_dir) and not security.session.identity:
                passphrase = ask_passphrase(
                    self, "Keep unencrypted copies",
                    "Enter your backup passphrase to decrypt the existing backups.")
                if passphrase is None:
                    return False
                try:
                    busy(lambda: security.unlock_backup_key(data_dir, passphrase))
                except security.WrongPassphrase:
                    QMessageBox.warning(self, "Keep unencrypted copies",
                                        "That passphrase is not correct.")
                    return False
        else:
            answer = QMessageBox.question(
                self, "Stop keeping unencrypted copies",
                "The unencrypted copy of the database and the unencrypted twin of each "
                "encrypted backup will be deleted. Your encrypted files are not changed. "
                "Unencrypted backups that have no encrypted twin, and files you copied "
                "yourself, are kept.\n\nContinue?")
            if answer != QMessageBox.Yes:
                return False
        self._flush_all()
        try:
            result = busy(lambda: backup.set_keep_unencrypted_copies(self.db, enabled))
        except Exception as exc:
            QMessageBox.critical(self, "Unencrypted copies", str(exc))
            return False
        self._mirror_changes = self.db.total_changes()
        if enabled:
            text = f"Created {len(result['created'])} unencrypted copies."
            if result["failed"]:
                text += "\n\nThese backups could not be copied and were left as they are:\n"
                text += "\n".join(f"• {name}: {why}" for name, why in result["failed"])
        else:
            text = f"Deleted {len(result['deleted'])} unencrypted copies."
            if result["kept"]:
                text += ("\n\nThese unencrypted backups have no encrypted twin, or were "
                         "not made by jortle_claude, so they were kept. Delete them yourself "
                         "if you no longer want them:\n")
                text += "\n".join(f"• {name}" for name in result["kept"])
        QMessageBox.information(self, "Unencrypted copies", text)
        return True

    def _refresh_unencrypted_copy(self, force: bool = False):
        """Brings journal.unencrypted.db up to date after saves, while the user
        keeps unencrypted copies. Cheap to call: it does nothing unless the
        database has changed since the last copy."""
        if not getattr(self, "db", None) or not self.db.encrypted:
            return
        data_dir = get_data_dir()
        if not security.load_config(data_dir).get("keep_unencrypted_copies"):
            return
        changes = self.db.total_changes()
        if changes == self._mirror_changes and not force \
                and (data_dir / security.MIRROR_DB).exists():
            return
        try:
            self.db.export_plain_copy(data_dir / security.MIRROR_DB)
            self._mirror_changes = changes
        except Exception as exc:
            self.statusBar().showMessage(
                f"The unencrypted copy could not be updated: {exc}", 10000)

    def _export_archive(self):
        """Explicit, user-initiated static-site export (Part 74) — never
        driven by autosave, which is a different concept entirely."""
        answer = QMessageBox.warning(
            self, "Export Readable Archive",
            "The readable archive is a set of ordinary web pages. It is NOT "
            "encrypted: anyone who can open the folder can read your journal, "
            "Reader's Notes, projects and calendar.\n\n"
            "It is for reading without jortle_claude, not for restoring — use "
            "File → Back Up Now for that.",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer not in (QMessageBox.Ok, QMessageBox.Yes):
            return
        self._flush_all()

        start = self._picker_dir(PICKER_ARCHIVE, Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose where to write the readable archive", start
        )
        if not chosen:
            return
        target = Path(chosen)
        self._remember_picker_dir(PICKER_ARCHIVE, target)
        # getExistingDirectory returns a folder that already exists, so write
        # into a named subfolder rather than scattering an index.html and a
        # handful of directories directly into whatever they picked.
        if target.name != default_archive_dirname():
            target = target / default_archive_dirname()

        try:
            summary = export_archive(self.db, target)
        except Exception as exc:
            QMessageBox.critical(self, "Archive export failed", str(exc))
            return

        QMessageBox.information(
            self, "Readable archive created",
            f"Wrote {summary.entry_count} journal entries, {summary.notes_count} "
            f"set(s) of Reader's Notes, {summary.project_count} project(s) "
            f"and {summary.event_count} calendar events to:\n\n"
            f"{summary.root}\n\n"
            f"Open index.html in any browser — no application needed. "
            f"Remember that these files are not encrypted.",
        )

    def _import_backup(self):
        start = self._picker_dir(PICKER_RESTORE_BACKUP, backup.backup_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore from Backup", start,
            "jortle_claude backups (*.jcbackup *.zip);;All files (*)")
        if not path:
            return
        path = Path(path)
        self._remember_picker_dir(PICKER_RESTORE_BACKUP, path.parent)
        data_dir = get_data_dir()
        # Restoring replaces everything on screen. With autosave off, unsaved
        # work gets the same Save / Discard / Cancel as any other way of
        # leaving it — it used to be dropped without asking.
        if not self._allow_leaving_entry():
            return
        if not self.projects_widget.allow_leaving_project():
            return

        # Checked before the warning and before anything moves: a backup that
        # cannot be used is reported now, with the journal untouched.
        passphrase = None
        while True:
            try:
                _src, manifest, has_checksums, was_encrypted = busy(
                    lambda: backup.read_backup_payload(path, passphrase))
                break
            except backup.NeedsPassphrase:
                prompt = "This backup is encrypted. Enter the backup passphrase it was made with."
            except security.WrongPassphrase:
                prompt = "That passphrase does not open this backup. Try again."
            except Exception as exc:
                QMessageBox.critical(
                    self, "Restore failed",
                    f"{exc}\n\nYour journal has been left as it was and is still open.")
                return
            passphrase = ask_passphrase(self, "Restore from Backup", prompt)
            if passphrase is None:
                return

        cfg = security.load_config(data_dir)
        when = manifest.get("exported_at", "an unknown date")
        if not has_checksums:
            notice = ("This backup was made by an earlier version and has no checksums, so "
                      "only its database could be checked, not its photos.\n\n")
        else:
            notice = ""
        if cfg.get("restore_warning", True) or not has_checksums:
            go_ahead, dont_show = backup_dialog.confirm_restore(
                self, f"{notice}This replaces your current journal with the backup "
                      f"from {when}.\n\nYour current data will be moved to a "
                      f"safety folder first, not deleted.\n\nContinue?")
            if not go_ahead:
                return
            if dont_show:
                security.update_config(data_dir, restore_warning=False)

        # Nothing that is on screen may be written back after this point: the
        # editors still hold the PRE-restore documents, and an autosave tick
        # or a flush landing after the swap would put them straight back on
        # top of the restored data. Stop the timers and drop the dirty flags
        # before the database moves.
        self._autosave_timer.stop()
        self._mirror_timer.stop()
        self.reader_notes.stop_timers()
        self.projects_widget.stop_timers()
        self.editor.mark_clean()
        self.projects_widget.editor.mark_clean()
        self.reader_notes.editor.mark_clean()
        self.projects_widget.reader_notes.editor.mark_clean()

        # The sessions describe documents in the database being replaced.
        self.history.reset()
        old_db = self.db
        try:
            self.db.close()
            manifest = busy(lambda: restore_backup(path, passphrase))
        except Exception as e:
            # restore_backup guarantees the data directory is either replaced
            # or left exactly as it was, so reopening is always safe here.
            self.db = Database()
            self._rebind_database(old_db)
            self._reload_everything()
            self._mirror_timer.start()
            QMessageBox.critical(
                self, "Restore failed",
                f"{e}\n\nYour journal has been left as it was and is still open.",
            )
            return

        self.db = Database()
        self._rebind_database(old_db)
        self._reload_everything()
        self._mirror_changes = self.db.total_changes()
        self._mirror_timer.start()
        QMessageBox.information(
            self, "Restore complete",
            f"Restored the backup from {manifest.get('exported_at', 'an unknown date')}.\n\n"
            f"Your previous data was kept in:\n{manifest.get('_safety_dir', '')}"
        )

    def _database_holders(self):
        """Every object in the application that caches a `Database` handle.

        Found rather than listed. The hand-maintained list this replaced went
        stale the moment a new object started holding a reference: it missed
        `calendar_prefs`, so restoring a backup called into a closed
        connection, the exception escaped before the editors were reloaded,
        and the restore looked like it had silently done nothing — while the
        pre-restore text sat in the editor waiting to be saved back over the
        restored entry. One missing line, and the user's answer to "did my
        backup restore?" was no.

        Qt's own object tree covers the widgets; the rest are plain
        attributes of this window and of the two workspaces, so those are
        walked too. Anything holding the OLD handle is repointed, which also
        means an object that deliberately uses a different database is left
        alone.
        """
        candidates = [self]
        candidates.extend(self.findChildren(QObject))
        for owner in (self, self.projects_widget, self.calendar_panel,
                      self.week_calendar, self.day_calendar):
            candidates.extend(
                value for value in vars(owner).values() if isinstance(value, QObject)
            )
        seen, out = set(), []
        for obj in candidates:
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            out.append(obj)
        return out

    def _rebind_database(self, old_db) -> int:
        """Points everything that held `old_db` at the current one."""
        rebound = 0
        for obj in self._database_holders():
            if getattr(obj, "db", None) is old_db:
                obj.db = self.db
                rebound += 1
        return rebound

    def _reload_everything(self):
        """Re-reads the whole UI from the current database.

        Used after a restore, in both the success and the failure path — in
        the failure path the database is the same one as before, so this is
        simply a refresh, and doing it unconditionally means there is no
        state in which the window is left showing something the database
        does not contain.
        """
        self.calendar_panel.tag_picker.reload_markers()
        self.projects_widget.refresh_project_list()
        self.nav_history.clear()
        self._apply_settings()
        self._refresh_calendar_marks()
        self._load_date(self.current_date)
        self.day_calendar.refresh()
        self.week_calendar.refresh()
        self.reader_notes.load(self.current_date)
        # The editors now hold what the database holds, so nothing is
        # pending; the timers can run again.
        self.editor.mark_clean()
        self.reader_notes.editor.mark_clean()
        self._update_dirty_indicator()

    def _open_data_folder(self):
        folder = str(get_data_dir())
        if sys.platform == "win32":
            subprocess.Popen(["explorer", folder])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            try:
                subprocess.Popen(["xdg-open", folder])
            except FileNotFoundError:
                webbrowser.open(f"file://{folder}")

    def _show_data_usage(self):
        size = dir_size_bytes(get_data_dir())
        # A `models` folder is a leftover from a feature this version no
        # longer has. Nothing reads it, nothing recreates it, and it is never
        # deleted automatically — but it can be several gigabytes, so it is
        # worth telling the user it is there and that it is theirs to remove.
        leftover_dir = get_data_dir() / "models"
        leftover = dir_size_bytes(leftover_dir) if leftover_dir.is_dir() else 0
        model_note = (
            f"\n\nThat figure includes {human_size(leftover)} in a 'models' folder "
            f"left over from a feature that has since been removed. Nothing in "
            f"jortle_claude reads it, and nothing will put it back. You can delete that "
            f"folder yourself (File → Open Data Folder) to reclaim the space."
            if leftover else ""
        )
        QMessageBox.information(
            self, "Data Usage",
            f"Your journal is currently using {human_size(size)} on disk "
            f"(database, task lists, writing projects, and all photos "
            f"combined).{model_note}\n\n"
            f"Photos are usually the biggest contributor to this number "
            f"otherwise. Use File → Export Backup regularly to keep a "
            f"compressed copy elsewhere as this grows, and File → Find Unused "
            f"Photos to reclaim space from images you've since removed from "
            f"your writing.",
        )

    def _find_unused_photos(self):
        self._save_current_entry(automatic=True)
        self.projects_widget.flush()
        unused = find_unused_photos(self.db)
        if not unused:
            QMessageBox.information(
                self, "Find Unused Photos",
                "No unused photo files found — every photo on disk is referenced "
                "somewhere in your entries, projects, or version history."
            )
            return
        total_size = sum(f.stat().st_size for f in unused if f.exists())
        confirm = QMessageBox.question(
            self, "Find Unused Photos",
            f"Found {len(unused)} photo file(s) ({human_size(total_size)}) that aren't "
            f"referenced by any current entry, project, or saved version — usually "
            f"left behind after removing an inline photo from your writing.\n\n"
            f"Delete these files permanently?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        freed = delete_files(unused)
        QMessageBox.information(self, "Find Unused Photos", f"Freed {human_size(freed)}.")

    # ------------------------------------------------- history & recovery
    def _open_entry_history(self):
        """Version History for the entry on screen."""
        # Anything the user has typed and autosave would store anyway is
        # stored first, so the list is complete.
        if autosave_enabled(self.db):
            self._save_current_entry(automatic=True)
        dialog = EntryHistoryDialog(self.db, self.current_date, parent=self)
        dialog.restoreRequested.connect(self._restore_entry_revision)
        dialog.exec()

    def _restore_entry_revision(self, revision_id: int) -> bool:
        revision = self.db.get_entry_revision(revision_id)
        if revision is None:
            return False
        return self._restore_entry_content(revision.date, revision.body,
                                           revision.body_format, revision.body_text)

    def _restore_entry_content(self, date: str, html: str, fmt: str, plain: str) -> bool:
        """Replaces a day's entry with earlier content — a version, or a
        recovery copy. Never destroys the current state: unsaved edits get the
        usual Save / Discard / Cancel, and what is stored is kept as a version
        first. Goes through the ordinary entry write (_store_entry)."""
        if date != self.current_date:
            if not self._request_date(date):
                return False
            self._show_daily_tab()
        elif not self._allow_leaving_entry():
            return False
        if self.editor.is_dirty():       # autosave on: store what was typed
            self._save_current_entry()
        self.history.record_before_restore(date)
        self._store_entry(date, html, fmt or "html", plain, detect_removal=False)
        self._load_date(date)
        self._refresh_calendar_marks()
        self._update_dirty_indicator()
        self.statusBar().showMessage(f"Restored an earlier version of {human(date)}.", 4000)
        return True

    def _open_recovery(self):
        """File → Recovery."""
        def project_title(ref):
            try:
                project = self.db.get_project(int(ref))
            except (TypeError, ValueError):
                return None
            return project.title if project else None
        dialog = RecoveryDialog(self.db, project_title=project_title, parent=self)

        def restore(checkpoint_id):
            if self._restore_recovery_checkpoint(checkpoint_id):
                dialog.accept()
        dialog.restoreRequested.connect(restore)
        dialog.exec()

    def _restore_recovery_checkpoint(self, checkpoint_id: int) -> bool:
        checkpoint = self.db.get_recovery_checkpoint(checkpoint_id)
        if checkpoint is None:
            return False
        if checkpoint.scope == "date":
            return self._restore_entry_content(checkpoint.ref, checkpoint.body,
                                               checkpoint.body_format, checkpoint.body_text)
        if checkpoint.scope == "project":
            try:
                project_id = int(checkpoint.ref)
            except ValueError:
                return False
            if self.db.get_project(project_id) is None:
                QMessageBox.information(
                    self, "Recovery",
                    "That project no longer exists. Use Copy Text to take the "
                    "recovered writing somewhere else.")
                return False
            self.main_tabs.setCurrentWidget(self.projects_widget)
            return self.projects_widget.restore_content(
                project_id, checkpoint.body, checkpoint.body_format, checkpoint.body_text)
        return False

    def closeEvent(self, event):
        """Closing is the last chance to keep unsaved work (Part 26).

        Asked per document, and Cancel genuinely cancels the close — the
        window stays open with the text still in it.
        """
        if not self._allow_leaving_entry():
            event.ignore()
            return
        if not self.projects_widget.allow_leaving_project():
            event.ignore()
            return

        if autosave_enabled(self.db):
            self._save_current_entry(automatic=True)
            self.reader_notes.flush(automatic=True)
            self.projects_widget.flush()
        else:
            # Reader's Notes are small and have no prompt of their own; with
            # autosave off an EDITED note is still written on the way out
            # rather than silently dropped. An untouched one is not.
            self.reader_notes.flush(automatic=True)
            self.projects_widget.reader_notes.flush(automatic=True)

        # Closing is an editing boundary: an entry changed in this visit
        # keeps the state it was left in as a version.
        self.history.end_all_sessions(REASON_CLOSED)
        self._save_window_state()

        # Everything that polls the database on a timer stops BEFORE the
        # connection does. A tick that lands after the close would raise
        # from inside Qt's event dispatch — harmless, but it prints a
        # traceback on exit that reads like a crash.
        self._autosave_timer.stop()
        self.reader_notes.stop_timers()
        self.projects_widget.stop_timers()
        self.day_calendar._clock.stop()
        self.week_calendar._clock.stop()
        self._backup_timer.stop()
        self._mirror_timer.stop()
        # The unencrypted copy, when kept, leaves with everything saved.
        self._refresh_unencrypted_copy(force=True)

        self.db.close()
        super().closeEvent(event)


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
