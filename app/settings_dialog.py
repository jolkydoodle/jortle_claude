"""The Settings window: ordinary, core application preferences — the window
color scheme, the default writing font, the writing position, and the app's
own interface text size. It has pages (General, Editor, Hotkeys, Calendar,
Appearance, Backups), and each entry of the Settings menu opens this one
window at its page (G4-D3, batch 4A). Every control here applies and saves immediately when
you change it — there's no separate Save button to remember to click, so you
can flip through color schemes and see the whole app update live. "Close"
just dismisses the dialog; there's nothing left to discard.

This is the only settings window. There was a second one, File →
Experimental Settings, holding the controls for the optional AI feature;
that feature has been removed and so has the window, rather than leaving a
menu item that opens an empty dialog.

Pick a preset (Light/Dark/Sepia/...) or click any of the five swatches to
tweak that one color — doing so switches you to a "Custom" scheme seeded
from whichever preset you started from, so you can build your own look
without starting from nothing.

There are three DIFFERENT font-size controls in this app, on purpose —
they're easy to mix up, so here's exactly what each one does:

- **Default writing font**, below: the starting family/size for a brand-new
  entry or project, and for any text you haven't individually resized. It
  never overrides text you've already resized yourself.
- **The toolbar above the entry/project editor** (family + size, next to
  Bold and Italic): resizes whatever text is currently SELECTED — like
  Word or Google Docs, so one word can be size 15 and another size 20 in
  the same entry. With nothing selected, it sets what you're about to type
  next.
- **Application font size**, also below: resizes the app's OWN interface —
  buttons, labels, calendar, task list, menus. Never touches your writing
  at all.

"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget
)

from . import rich_editor
from .calendar_prefs import WORK_HOURS_SETTING
from .saving import AUTOSAVE_SETTING, autosave_enabled
from .database import Database
from .help_dialogs import shortcuts_table
from .ui_util import make_shrinkable_combo
from .theme import PRESETS, ColorScheme, scheme_from_json, scheme_to_json

MIN_WIDTH = 440
MIN_HEIGHT = 260
PREFERRED_WIDTH = 520
PREFERRED_HEIGHT = 420

SWATCH_FIELDS = [
    ("background", "Background"),
    ("panel", "Panels"),
    ("text", "Text"),
    ("accent", "Accent"),
    ("border", "Border"),
]


class SettingsDialog(QDialog):
    # The pages, in order; the Settings menu has one entry per page.
    PAGES = ("general", "editor", "hotkeys", "calendar", "appearance", "backups")
    PAGE_TITLES = {"general": "General", "editor": "Editor", "hotkeys": "Hotkeys",
                   "calendar": "Calendar", "appearance": "Appearance", "backups": "Backups"}

    def __init__(self, db: Database, on_change: Callable[[], None], parent=None,
                 set_autosave: Callable[[bool], None] | None = None,
                 open_backups: Callable[[], None] | None = None,
                 set_work_hours: Callable[[bool], None] | None = None,
                 page: str = "general"):
        super().__init__(parent)
        self.db = db
        self.on_change = on_change
        self._set_autosave = set_autosave
        self._set_work_hours = set_work_hours
        self._open_backups = open_backups
        self.setWindowTitle("Settings")

        self.font_combo = make_shrinkable_combo(QFontComboBox(), visible_chars=12)
        self.font_combo.setCurrentText(db.get_setting("font_family", "Georgia"))
        self.font_combo.setToolTip(
            "Starting font for new entries/projects and any text you haven't resized "
            "individually. To change just some words, select them in the toolbar above "
            "the editor instead."
        )
        self.font_combo.currentFontChanged.connect(self._on_default_font_changed)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 72)
        self.size_spin.setValue(int(db.get_setting("font_size", "13")))
        self.size_spin.setToolTip(
            "Starting size for new entries/projects and any text you haven't resized "
            "individually. To change just some words, select them in the toolbar above "
            "the editor instead."
        )
        self.size_spin.valueChanged.connect(self._on_default_font_size_changed)

        # Spec Part 18: optional, and Free/Manual is both the default and
        # the recommended value — the app does not reposition your writing
        # unless you ask it to.
        self.writing_position_combo = make_shrinkable_combo(QComboBox(), visible_chars=12)
        for label, value in rich_editor.WRITING_POSITION_LABELS:
            self.writing_position_combo.addItem(label, value)
        current_position = db.get_setting("writing_position",
                                           rich_editor.WRITING_POSITION_FREE)
        idx_pos = self.writing_position_combo.findData(current_position)
        self.writing_position_combo.setCurrentIndex(idx_pos if idx_pos >= 0 else 0)
        self.writing_position_combo.setToolTip(
            "Where the line you're typing settles in the writing pane. "
            "Free / Manual leaves the view exactly where you put it and only "
            "scrolls when the caret would otherwise go off-screen. The other "
            "options gently keep the active line around that part of the pane "
            "while you type; manual scrolling still takes priority."
        )
        self.writing_position_combo.currentIndexChanged.connect(
            self._on_writing_position_changed)

        self.ui_size_spin = QSpinBox()
        self.ui_size_spin.setRange(8, 24)
        # Default to the actual current interface font size (not a guessed
        # constant), so what's shown here always matches what's genuinely
        # applied until the user picks something different themselves.
        current_ui_size = QApplication.instance().font().pointSize()
        self.ui_size_spin.setValue(int(db.get_setting("ui_font_size", str(current_ui_size))))
        self.ui_size_spin.setToolTip(
            "Resizes the app's own interface — buttons, labels, calendar, task list, "
            "menus. Not your writing (see the toolbar above the editor for that)."
        )
        self.ui_size_spin.valueChanged.connect(self._on_ui_font_size_changed)

        # ---- saving -----------------------------------------------------
        self.autosave_check = QCheckBox("Enable autosave")
        self.autosave_check.setChecked(autosave_enabled(db))
        self.autosave_check.setToolTip(
            "With autosave on, your writing is saved a moment after you stop "
            "typing. With it off, nothing is written until you press Ctrl+S — "
            "and jortle_claude asks before anything would discard unsaved changes."
        )
        self.autosave_check.toggled.connect(self._on_autosave_toggled)

        # ---- calendar ---------------------------------------------------
        self.work_hours_check = QCheckBox("Highlight work hours")
        self.work_hours_check.setChecked(db.get_setting(WORK_HOURS_SETTING, "0") == "1")
        self.work_hours_check.setToolTip(
            "Tints 9:00 AM to 5:00 PM, Monday to Friday, in the Day and Week "
            "calendars. A visual guide only — it doesn't affect events."
        )
        self.work_hours_check.toggled.connect(self._on_work_hours_toggled)

        self.current_scheme: ColorScheme = scheme_from_json(db.get_setting("color_scheme"))

        self.scheme_combo = make_shrinkable_combo(QComboBox(), visible_chars=10)
        self.scheme_combo.addItems(list(PRESETS.keys()) + ["Custom"])
        idx = self.scheme_combo.findText(self.current_scheme.name)
        self.scheme_combo.setCurrentIndex(idx if idx >= 0 else self.scheme_combo.count() - 1)
        self.scheme_combo.currentTextChanged.connect(self._on_preset_chosen)

        self.swatch_buttons: dict[str, QPushButton] = {}
        swatch_row = QHBoxLayout()
        for field, label in SWATCH_FIELDS:
            col = QVBoxLayout()
            btn = QPushButton()
            btn.setFixedSize(32, 24)
            btn.clicked.connect(lambda _checked=False, f=field: self._pick_color(f))
            self.swatch_buttons[field] = btn
            small_label = QLabel(label)
            small_label.setObjectName("SubtleHint")
            col.addWidget(btn)
            col.addWidget(small_label)
            swatch_row.addLayout(col)
        self._refresh_swatches()

        # The backup folder is visible here (Master Spec §46.1); everything
        # about backups and encryption is managed in one window, reached from
        # here and from the File menu.
        # The folder is a read-only field, not a label: a path has no spaces
        # to wrap at, so a label set a width floor that pushed this window
        # wider than the screen at large fonts (bug 27). The field scrolls,
        # and the whole path can still be selected and copied.
        folder, status = self._backup_summary()
        self.backup_folder = QLineEdit(folder)
        self.backup_folder.setReadOnly(True)
        self.backup_status = QLabel(status)
        self.backup_status.setWordWrap(True)
        self.backup_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        manage = QPushButton("Manage…")
        manage.setEnabled(open_backups is not None)
        manage.clicked.connect(self._manage_backups)
        backup_text = QVBoxLayout()
        backup_text.addWidget(self.backup_folder)
        backup_text.addWidget(self.backup_status)
        backup_row = QHBoxLayout()
        backup_row.addLayout(backup_text, 1)
        backup_row.addWidget(manage, 0, Qt.AlignTop)

        # Hotkeys: the current shortcuts, read-only, from the command table —
        # the same list as Help → Keyboard Shortcuts (4A/AM-3).
        self.hotkeys_table = shortcuts_table()

        # The rows are the ones this window always had, each on its page.
        rows = {
            "general": [("Saving:", self.autosave_check)],
            "editor": [("Default writing font:", self.font_combo),
                       ("Default writing size:", self.size_spin),
                       ("Writing position:", self.writing_position_combo)],
            "hotkeys": [(None, self.hotkeys_table)],
            "calendar": [("Calendar:", self.work_hours_check)],
            "appearance": [("Color scheme:", self.scheme_combo),
                           ("Tweak colors:", swatch_row),
                           ("Application font size:", self.ui_size_spin)],
            "backups": [("Backups:", backup_row)],
        }

        # Part 36: every page scrolls and wraps. A page is short today, but it
        # grows with the application font size like everything else, and a
        # settings window that can't be closed at ui_font_size 24 is exactly
        # the failure being designed out.
        self.page_list = QListWidget()
        self.pages = QStackedWidget()
        self._contents: dict[str, QWidget] = {}
        self._scrolls: dict[str, QScrollArea] = {}
        for key in self.PAGES:
            form = QFormLayout()
            # Responsive rather than fixed-width (Parts 13/42): long labels
            # wrap onto their own row instead of squeezing the control or
            # forcing a horizontal scrollbar when the application font size
            # goes up, and the controls take the width that's left.
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            for label, field in rows[key]:
                if label is None:
                    form.addRow(field)
                else:
                    form.addRow(label, field)
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.addLayout(form)
            if key != "hotkeys":
                content_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidget(content)
            scroll.setWidgetResizable(True)
            # AsNeeded, not AlwaysOff: labels wrap and combos elide, so the bar
            # should never appear in practice — but on a display too small even
            # for the wrapped layout, scrolling to a control beats clipping it.
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setFrameShape(QScrollArea.NoFrame)
            self._contents[key] = content
            self._scrolls[key] = scroll
            self.pages.addWidget(scroll)
            self.page_list.addItem(self.PAGE_TITLES[key])
        self.page_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.page_list.setFixedWidth(self.page_list.sizeHintForColumn(0)
                                     + 2 * self.page_list.frameWidth() + 16)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)

        body = QHBoxLayout()
        body.addWidget(self.page_list)
        body.addWidget(self.pages, stretch=1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, stretch=1)
        layout.addWidget(buttons)  # outside the scroll areas: always reachable

        self.show_page(page)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(*self._starting_size())

    def show_page(self, key: str):
        """Shows one page: what each Settings menu entry asks for."""
        self.page_list.setCurrentRow(self.PAGES.index(key))

    def current_page(self) -> str:
        return self.PAGES[self.pages.currentIndex()]

    def _backup_summary(self) -> tuple[str, str]:
        """(backup folder, encryption status line)."""
        from . import backup, security
        from .paths import get_data_dir
        try:
            data_dir = get_data_dir()
            folder = backup.backup_dir(data_dir)
            db_enc = security.is_encrypted_install(data_dir)
            backups_enc = security.backups_encrypted(data_dir)
            paused = security.backups_paused_reason(data_dir)
        except Exception:
            return "", ""
        backups = ("paused (no backup passphrase set)" if paused else
                   "encrypted" if backups_enc else "not encrypted")
        return (str(folder), f"Database: {'encrypted' if db_enc else 'not encrypted'} · "
                             f"Backups: {backups}")

    def _manage_backups(self):
        if self._open_backups is not None:
            self._open_backups()
            folder, status = self._backup_summary()
            self.backup_folder.setText(folder)
            self.backup_status.setText(status)

    def _starting_size(self) -> tuple[int, int]:
        """Opens wide enough for its own content, but never bigger than the
        screen it's opening on.

        The width is taken from what the content actually needs at the
        current application font size, not from a fixed constant — at 10pt
        that is the comfortable default below, at 24pt it is wider, and on a
        small display it is whatever fits and the rest scrolls. A constant
        here is what left the dialog too narrow for its own controls once
        the interface font grew.
        """
        # Measured over every page, so moving between pages never needs a
        # wider or taller window than the one that opened.
        contents = list(self._contents.values())
        needed = max(c.minimumSizeHint().width() for c in contents) + self._chrome_width()
        width = max(PREFERRED_WIDTH, needed)
        # Height follows the same rule: show the whole page if the screen
        # allows it, and scroll only when it genuinely doesn't fit. Measured
        # from the CONTENT at the width just chosen (word-wrapped text is
        # taller in a narrow window), not from the dialog's own size hint —
        # a scroll area's hint says nothing about what's inside it.
        inner_width = width - self._chrome_width()
        content_height = 0
        for content in contents:
            page_height = content.heightForWidth(inner_width)
            if page_height <= 0:
                page_height = content.sizeHint().height()
            content_height = max(content_height, page_height)
        height = max(PREFERRED_HEIGHT, content_height + self._chrome_height())
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width() - 40)
            height = min(height, available.height() - 80)
        return max(MIN_WIDTH, width), max(MIN_HEIGHT, height)

    def _chrome_width(self) -> int:
        """Window margins, the page list, and room for the vertical
        scrollbar, so the content isn't squeezed by the bar that appears
        next to it."""
        margins = self.layout().contentsMargins()
        bar = self._scrolls[self.PAGES[0]].verticalScrollBar().sizeHint().width()
        return (margins.left() + margins.right() + self.page_list.minimumWidth()
                + self.layout().spacing() + bar + 8)

    def _chrome_height(self) -> int:
        """Window margins plus the button row below the scrolling area."""
        margins = self.layout().contentsMargins()
        buttons = self.findChild(QDialogButtonBox)
        return (margins.top() + margins.bottom()
                + (buttons.sizeHint().height() if buttons else 0)
                + self.layout().spacing() + 8)

    # --------------------------------------------------- default writing font
    def _on_default_font_changed(self, font):
        self.db.set_setting("font_family", font.family())
        self.on_change()

    def _on_default_font_size_changed(self, size: int):
        self.db.set_setting("font_size", str(size))
        self.on_change()

    def _on_writing_position_changed(self, _index: int):
        self.db.set_setting("writing_position", self.writing_position_combo.currentData())
        self.on_change()

    # ----------------------------------------------------------- saving
    def _on_autosave_toggled(self, enabled: bool):
        # The window's own switch when there is one (the same function the
        # status-bar toggle uses — one command, two routes), else just the
        # stored preference.
        if self._set_autosave is not None:
            self._set_autosave(enabled)
        else:
            self.db.set_setting(AUTOSAVE_SETTING, "1" if enabled else "0")
        self.on_change()

    def _on_work_hours_toggled(self, enabled: bool):
        # The window's own setter when there is one (the same function View →
        # Highlight Work Hours uses — one command, two routes), else just the
        # stored preference.
        if self._set_work_hours is not None:
            self._set_work_hours(enabled)
        else:
            self.db.set_setting(WORK_HOURS_SETTING, "1" if enabled else "0")
        self.on_change()

    # ------------------------------------------------------ application font
    def _on_ui_font_size_changed(self, size: int):
        self.db.set_setting("ui_font_size", str(size))
        self.on_change()

    # ---------------------------------------------------------- scheme
    def _on_preset_chosen(self, name: str):
        if name in PRESETS:
            self.current_scheme = replace(PRESETS[name])
            self._refresh_swatches()
            self._save_and_apply_scheme()

    def _pick_color(self, field: str):
        current = QColor(getattr(self.current_scheme, field))
        chosen = QColorDialog.getColor(current, self, f"Pick a color for {field}")
        if not chosen.isValid():
            return
        setattr(self.current_scheme, field, chosen.name())
        self.current_scheme.name = "Custom"
        self.scheme_combo.blockSignals(True)
        self.scheme_combo.setCurrentText("Custom")
        self.scheme_combo.blockSignals(False)
        self._refresh_swatches()
        self._save_and_apply_scheme()

    def _refresh_swatches(self):
        for field, btn in self.swatch_buttons.items():
            color = getattr(self.current_scheme, field)
            btn.setStyleSheet(
                f"background-color: {color}; border: 1px solid #888888; border-radius: 4px;"
            )

    def _save_and_apply_scheme(self):
        self.db.set_setting("color_scheme", scheme_to_json(self.current_scheme))
        self.on_change()

