"""The one table of commands (Master Spec §51.3; batch 4A, 4A-D1).

Every command the menus, the toolbars and the keyboard can invoke is listed
here once, with its label, its default shortcut and where it works. The
routes read the shortcut from this table and nowhere else: the menu entry
shows it, a toolbar button's tooltip is built from it, Help → Keyboard
Shortcuts and Settings → Hotkeys list it. A route never keeps its own copy
of a key, so changing a shortcut here changes it everywhere at once — which
is also what lets a later batch make the shortcuts configurable (4B)
without hunting for hard-coded keys.

Two scopes:

    window  one QAction in the main window (MainWindow._command_action),
            shared by its menu entry and its shortcut;
    editor  one QAction per writing editor (RichEditor._command_action):
            the toolbar button *is* that action and carries the shortcut,
            active while the focus is in that editor.

The ordinary editing keys (Undo, Cut, Copy, Paste, Select All) are taken by
the text widget itself (QTextEdit / QLineEdit accept them before any window
shortcut), and the Edit menu calls that widget's own slot, so both routes end
in the same Qt function. They are listed with the platform's standard keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from PySide6.QtGui import QKeySequence

WINDOW = "window"
EDITOR = "editor"

# What the Keyboard Shortcuts list says about where a command works.
ANYWHERE = "Anywhere in the main window"
IN_TEXT = "In the text you are editing"
IN_EDITOR = "In a writing editor"
IN_FULL_EDITOR = "In the journal and project editors"

Key = Union[str, QKeySequence.StandardKey, None]


@dataclass(frozen=True)
class Command:
    id: str
    label: str            # menu text ("&" marks the mnemonic, "&&" a literal "&")
    tooltip: str          # without the shortcut; tooltip() adds it
    key: Key = None       # default shortcut: portable text, a Qt standard key, or None
    scope: str = WINDOW
    where: str = ANYWHERE


COMMANDS: tuple[Command, ...] = (
    # ---- File
    Command("save", "&Save", "Save what is open in this workspace", "Ctrl+S"),
    Command("history", "History…",
            "Previous versions of this day's entry — preview, restore, or delete them"),
    Command("recovery", "Recovery…", "Text kept just before a large deletion in an entry or project"),
    Command("back_up_now", "Back Up Now", "Makes a backup in the backup folder and checks it"),
    Command("export_backup", "Export Backup…", "Makes a backup in a place you choose"),
    Command("restore_backup", "Restore from Backup…", "Replaces your journal with a backup"),
    Command("backups_security", "Backups && Security…", "Backup folder, encryption and passphrases"),
    Command("export_archive", "Export Readable Archive (HTML)…",
            "Writes a static website of your journal, Reader's Notes and calendar "
            "that opens in any browser without this application."),
    Command("open_data_folder", "Open Data Folder", "Shows the folder your journal is kept in"),
    Command("data_usage", "Data Usage…", "The space your data takes"),
    Command("find_unused_photos", "Find Unused Photos…", "Photos no entry or project uses any more"),
    Command("quit", "&Quit", "Closes the application"),
    # ---- Edit (the focused text: an editor, or a text field)
    Command("undo", "&Undo", "Undo the last change in the text you are editing",
            QKeySequence.Undo, where=IN_TEXT),
    Command("redo", "&Redo", "Redo what was just undone", QKeySequence.Redo, where=IN_TEXT),
    Command("cut", "Cu&t", "Cut the selection", QKeySequence.Cut, where=IN_TEXT),
    Command("copy", "&Copy", "Copy the selection", QKeySequence.Copy, where=IN_TEXT),
    Command("paste", "&Paste", "Paste at the cursor", QKeySequence.Paste, where=IN_TEXT),
    Command("select_all", "Select &All", "Select all of the text", QKeySequence.SelectAll,
            where=IN_TEXT),
    Command("find", "&Find…", "Find in the text you are editing (or in this workspace's editor)",
            QKeySequence.Find, where=IN_EDITOR),
    # ---- View
    # Zoom in has no default key (G4-D1; 4A/AM-2): Ctrl+wheel, the Zoom % box
    # and View → Zoom In do it.
    Command("zoom_in", "Zoom &In", "Zoom in", None, where=IN_EDITOR),
    Command("zoom_out", "Zoom &Out", "Zoom out", "Ctrl+-", where=IN_EDITOR),
    Command("zoom_reset", "&Reset Zoom", "Reset zoom to 100%", "Ctrl+0", where=IN_EDITOR),
    Command("calendar_zoom_in", "Calendar Zoom In", "Show more of each hour in the calendars"),
    Command("calendar_zoom_out", "Calendar Zoom Out", "Show more hours at once in the calendars"),
    Command("calendar_zoom_reset", "Reset Calendar Zoom", "Calendar time scale back to 100%"),
    Command("work_hours", "Highlight Work Hours",
            "Tints 9:00 AM to 5:00 PM, Monday to Friday, in the Day and Week calendars"),
    # ---- Settings (each opens the one Settings window at that page)
    Command("settings_general", "&General…", "Saving"),
    Command("settings_editor", "&Editor…", "Default writing font and writing position"),
    Command("settings_hotkeys", "&Hotkeys…", "The keyboard shortcuts"),
    Command("settings_calendar", "&Calendar…", "Work hours"),
    Command("settings_appearance", "&Appearance…", "Colours and the application font size"),
    Command("settings_backups", "&Backups…", "The backup folder and its encryption"),
    # ---- Help
    Command("keyboard_shortcuts", "&Keyboard Shortcuts…", "Every shortcut, as currently assigned"),
    Command("recovery_guide", "&Recovery Guide…",
            "How to read your journal without this application"),
    Command("about", "&About…", "Version information"),
    # ---- Editor toolbar commands (one action per editor)
    Command("bold", "Bold", "Bold", QKeySequence.Bold, EDITOR, IN_EDITOR),
    Command("italic", "Italic", "Italic", QKeySequence.Italic, EDITOR, IN_EDITOR),
    Command("underline", "Underline", "Underline", QKeySequence.Underline, EDITOR, IN_EDITOR),
    Command("align_left", "Align Left", "Align Left", "Ctrl+Shift+L", EDITOR, IN_FULL_EDITOR),
    Command("align_center", "Align Center", "Align Center", "Ctrl+Shift+E", EDITOR, IN_FULL_EDITOR),
    Command("align_right", "Align Right", "Align Right", "Ctrl+Shift+R", EDITOR, IN_FULL_EDITOR),
    Command("align_justify", "Justify", "Justify", "Ctrl+Shift+J", EDITOR, IN_FULL_EDITOR),
)

BY_ID: dict[str, Command] = {c.id: c for c in COMMANDS}
assert len(BY_ID) == len(COMMANDS), "duplicate command id"


def command(command_id: str) -> Command:
    return BY_ID[command_id]


def key_sequences(command_id: str) -> list[QKeySequence]:
    """Every key binding of a command (a standard key can have several on a
    platform, e.g. Redo); empty when it has no shortcut."""
    key = BY_ID[command_id].key
    if key is None:
        return []
    if isinstance(key, QKeySequence.StandardKey):
        return list(QKeySequence.keyBindings(key))
    return [QKeySequence(key)]


def shortcut_text(command_id: str) -> str:
    """The shortcut as the user reads it ("Ctrl+B"), or "" for none."""
    keys = key_sequences(command_id)
    return keys[0].toString(QKeySequence.NativeText) if keys else ""


def tooltip(command_id: str) -> str:
    """The tooltip with the current shortcut, e.g. "Bold (Ctrl+B)"."""
    text = BY_ID[command_id].tooltip
    keys = shortcut_text(command_id)
    return f"{text} ({keys})" if keys else text


def plain_label(command_id: str) -> str:
    """The menu label without mnemonics or the ellipsis."""
    return (BY_ID[command_id].label.replace("&&", "\0").replace("&", "")
            .replace("\0", "&").rstrip("…"))


def shortcut_rows() -> list[tuple[str, str, str]]:
    """(command, shortcut, where) for every command that has a shortcut —
    the Keyboard Shortcuts list and the Hotkeys page. A standard key shows
    its main binding (what the menu shows), not every platform alias."""
    return [(plain_label(c.id), shortcut_text(c.id), c.where)
            for c in COMMANDS if key_sequences(c.id)]
