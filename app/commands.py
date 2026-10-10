"""The one table of commands (Master Spec §51.3; batch 4A, 4A-D1) and the
keys currently assigned to them (Master Spec §51.1; batch 4B, 4B-D1).

Every command the menus, the toolbars and the keyboard can invoke is listed
here once, with its label, its default shortcut and where it works. The
routes read the shortcut from this module and nowhere else: the menu entry
shows it, a toolbar button's tooltip is built from it, Help → Keyboard
Shortcuts and Settings → Hotkeys list it. A route never keeps its own copy
of a key.

Two scopes:

    window  one QAction in the main window (MainWindow._command_action),
            shared by its menu entry and its shortcut;
    editor  one QAction per writing editor (RichEditor._command_action):
            the toolbar button *is* that action and carries the shortcut,
            active while the focus is in that editor.

The ordinary editing keys (Undo, Cut, Copy, Paste, Select All) are taken by
the text widget itself (QTextEdit / QLineEdit accept them before any window
shortcut), and the Edit menu calls that widget's own slot, so both routes end
in the same Qt function. They are listed with the platform's standard keys
and cannot be reassigned (4B, answer 3).

Custom keys (4B): the user may change the key of every other command in
Settings → Hotkeys. Only the changes are stored, in the settings table
(`hotkeys`, JSON `{command_id: [portable key, …]}`; an empty list means "no
key"), in Qt's portable form; keys are shown in the platform's native form.
`load()` reads them at startup and after a restore, `save()` stores a set
that passed `validate()`, and either one emits `notifier.changed`, which
every route listens to, so a change applies at once, without a restart.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Union

from PySide6.QtCore import QKeyCombination, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

WINDOW = "window"
EDITOR = "editor"

# What the Keyboard Shortcuts list says about where a command works.
ANYWHERE = "Anywhere in the main window"
IN_TEXT = "In the text you are editing"
IN_EDITOR = "In a writing editor"
IN_FULL_EDITOR = "In the journal and project editors"

Key = Union[str, QKeySequence.StandardKey, None]

HOTKEYS_SETTING = "hotkeys"


@dataclass(frozen=True)
class Command:
    id: str
    label: str            # menu text ("&" marks the mnemonic, "&&" a literal "&")
    tooltip: str          # without the shortcut; tooltip() adds it
    key: Key = None       # default shortcut: portable text, a Qt standard key, or None
    scope: str = WINDOW
    where: str = ANYWHERE
    assignable: bool = True   # False: the platform's own key, not configurable (4B-D3)


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
    # ---- Edit (the focused text: an editor, or a text field). The six
    # standard edit keys are the platform's and are not configurable (4B-D3).
    Command("undo", "&Undo", "Undo the last change in the text you are editing",
            QKeySequence.Undo, where=IN_TEXT, assignable=False),
    Command("redo", "&Redo", "Redo what was just undone", QKeySequence.Redo, where=IN_TEXT,
            assignable=False),
    Command("cut", "Cu&t", "Cut the selection", QKeySequence.Cut, where=IN_TEXT, assignable=False),
    Command("copy", "&Copy", "Copy the selection", QKeySequence.Copy, where=IN_TEXT,
            assignable=False),
    Command("paste", "&Paste", "Paste at the cursor", QKeySequence.Paste, where=IN_TEXT,
            assignable=False),
    Command("select_all", "Select &All", "Select all of the text", QKeySequence.SelectAll,
            where=IN_TEXT, assignable=False),
    Command("find", "&Find…", "Find in the text you are editing (or in this workspace's editor)",
            QKeySequence.Find, where=IN_EDITOR),
    # ---- View
    # Zoom in has no default key (G4-D1; 4A/AM-2): Ctrl+wheel, the Zoom % box
    # and View → Zoom In do it. Its usual keys are subscript and superscript.
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
    # ---- Editor toolbar commands (one action per editor). The ids are
    # stored in the user's hotkeys, so they never change (4B, Q1).
    Command("bold", "Bold", "Bold", QKeySequence.Bold, EDITOR, IN_EDITOR),
    Command("italic", "Italic", "Italic", QKeySequence.Italic, EDITOR, IN_EDITOR),
    Command("underline", "Underline", "Underline", QKeySequence.Underline, EDITOR, IN_EDITOR),
    Command("strikethrough", "Strikethrough", "Strikethrough", None, EDITOR, IN_EDITOR),
    # Superscript and subscript take the usual zoom-in keys (G4-D1).
    Command("superscript", "Superscript", "Superscript", "Ctrl+Shift+=", EDITOR, IN_EDITOR),
    Command("subscript", "Subscript", "Subscript", "Ctrl+=", EDITOR, IN_EDITOR),
    Command("bullet_list", "Bullet List", "Bullet list", None, EDITOR, IN_EDITOR),
    Command("numbered_list", "Numbered List", "Numbered list", None, EDITOR, IN_EDITOR),
    # Reader's Notes editors have the alignment keys without the buttons (bug 12).
    Command("align_left", "Align Left", "Align Left", "Ctrl+Shift+L", EDITOR, IN_EDITOR),
    Command("align_center", "Align Center", "Align Center", "Ctrl+Shift+E", EDITOR, IN_EDITOR),
    Command("align_right", "Align Right", "Align Right", "Ctrl+Shift+R", EDITOR, IN_EDITOR),
    Command("align_justify", "Justify", "Justify", "Ctrl+Shift+J", EDITOR, IN_EDITOR),
    Command("indent", "Increase Indent", "Increase indent", None, EDITOR, IN_FULL_EDITOR),
    Command("outdent", "Decrease Indent", "Decrease indent", None, EDITOR, IN_FULL_EDITOR),
    Command("quote", "Quote", "Quote", None, EDITOR, IN_FULL_EDITOR),
    Command("insert_link", "Insert Link", "Insert Link (select text first, optional)", None,
            EDITOR, IN_FULL_EDITOR),
    Command("insert_photo", "Insert Photo", "Insert Photo", None, EDITOR, IN_FULL_EDITOR),
    Command("clear_formatting", "Clear Formatting",
            "Clear formatting (keep the text, drop its styling)", None, EDITOR, IN_FULL_EDITOR),
    Command("text_color", "Text Color", "Text color", None, EDITOR, IN_FULL_EDITOR),
    Command("highlight", "Highlight", "Highlight (text background color)", None,
            EDITOR, IN_FULL_EDITOR),
)

BY_ID: dict[str, Command] = {c.id: c for c in COMMANDS}
assert len(BY_ID) == len(COMMANDS), "duplicate command id"


class _Notifier(QObject):
    # Emitted once whenever the assigned keys change (a Settings → Hotkeys
    # Apply, a load at startup or after a restore). Connect bound methods of
    # QObjects, so Qt drops the connection when the receiver is destroyed.
    changed = Signal()


notifier = _Notifier()

# The user's changes to the default keys: {command_id: [QKeySequence, …]}.
_overrides: dict[str, list[QKeySequence]] = {}
# What load() ignored, as sentences for the Hotkeys page (4B, answer 6).
load_notes: list[str] = []


def command(command_id: str) -> Command:
    return BY_ID[command_id]


def portable(seq: QKeySequence) -> str:
    return seq.toString(QKeySequence.PortableText)


def native(seq: QKeySequence) -> str:
    return seq.toString(QKeySequence.NativeText)


def default_keys(command_id: str) -> list[QKeySequence]:
    """The table's keys for a command (a standard key can have several on a
    platform, e.g. Redo); empty when it has none."""
    key = BY_ID[command_id].key
    if key is None:
        return []
    if isinstance(key, QKeySequence.StandardKey):
        return list(QKeySequence.keyBindings(key))
    return [QKeySequence(key)]


def key_sequences(command_id: str) -> list[QKeySequence]:
    """The keys now assigned to a command: the user's, else the default."""
    if command_id in _overrides:
        return list(_overrides[command_id])
    return default_keys(command_id)


def assignments() -> dict[str, list[QKeySequence]]:
    """The keys of every assignable command, as now assigned."""
    return {c.id: key_sequences(c.id) for c in COMMANDS if c.assignable}


# ---------------------------------------------------------------- the = / + key
# On a US keyboard, Ctrl+Shift+= arrives as Key_Plus with Ctrl+Shift, which
# Qt matches as Ctrl+Shift++ (or Ctrl++), never as Ctrl+Shift+= (4B-D6). So
# the three forms of "Shift and the =/+ key" are one key here: it is stored
# and shown as Shift+=, registered under all three forms, and each form is
# occupied by whichever command holds the key (4B/AM-1). Ctrl+= (no Shift)
# stays a key of its own. Other keyboard layouts: not tested.
def _single_chord(seq: QKeySequence):
    if seq.count() != 1:
        return None
    return seq[0]


def _without(mods, flag):
    """`mods` with `flag` cleared. (In PySide `mods & ~flag` can lose the
    other modifiers too: Ctrl+Shift with ~Shift came out as no modifier.)"""
    return Qt.KeyboardModifier(mods.value & ~flag.value)


def canonical(seq: QKeySequence) -> QKeySequence:
    """Shift+= for any form of the shifted =/+ key; any other key unchanged."""
    chord = _single_chord(seq)
    if chord is None:
        return seq
    mods = chord.keyboardModifiers()
    if chord.key() == Qt.Key_Plus or (chord.key() == Qt.Key_Equal and mods & Qt.ShiftModifier):
        return QKeySequence(QKeyCombination(mods | Qt.ShiftModifier, Qt.Key_Equal))
    return seq


def alias_forms(seq: QKeySequence) -> list[QKeySequence]:
    """Every form a key is registered and occupied under: itself, plus, for
    the shifted =/+ key, its Key_Plus forms with and without Shift."""
    seq = canonical(seq)
    chord = _single_chord(seq)
    if chord is None or chord.key() != Qt.Key_Equal or not chord.keyboardModifiers() & Qt.ShiftModifier:
        return [seq]
    mods = chord.keyboardModifiers()
    return [seq, QKeySequence(QKeyCombination(mods, Qt.Key_Plus)),
            QKeySequence(QKeyCombination(_without(mods, Qt.ShiftModifier), Qt.Key_Plus))]


def registered_sequences(command_id: str) -> list[QKeySequence]:
    """What a command's QAction is given: its keys and their alias forms,
    the main key first (that is the one a menu shows)."""
    out, seen = [], set()
    for key in key_sequences(command_id):
        for form in alias_forms(key):
            if portable(form) not in seen:
                seen.add(portable(form))
                out.append(form)
    return out


# ---------------------------------------------------------------- display
def shortcut_text(command_id: str) -> str:
    """The shortcut as the user reads it ("Ctrl+B"), or "" for none."""
    keys = key_sequences(command_id)
    return native(keys[0]) if keys else ""


def tooltip(command_id: str) -> str:
    """The tooltip with the current shortcut, e.g. "Bold (Ctrl+B)"."""
    text = BY_ID[command_id].tooltip
    keys = shortcut_text(command_id)
    return f"{text} ({keys})" if keys else text



def save_key_phrase() -> str:
    """The words for saving by key in a sentence ("press Ctrl+S"), or the
    menu route when Save has no key (4B-D5)."""
    key = shortcut_text("save")
    return f"press {key}" if key else "use File → Save"


def save_key_hint() -> str:
    """The unsaved-changes hint ("Ctrl+S to save")."""
    key = shortcut_text("save")
    return f"{key} to save" if key else "File → Save to save"

def plain_label(command_id: str) -> str:
    """The menu label without mnemonics or the ellipsis."""
    return (BY_ID[command_id].label.replace("&&", "\0").replace("&", "")
            .replace("\0", "&").rstrip("…"))


def shortcut_rows() -> list[tuple[str, str, str]]:
    """(command, shortcut, where) for every command that has a shortcut —
    Help → Keyboard Shortcuts. A standard key shows its main binding (what
    the menu shows), not every platform alias."""
    return [(plain_label(c.id), shortcut_text(c.id), c.where)
            for c in COMMANDS if key_sequences(c.id)]


# ---------------------------------------------------------------- validation
@dataclass(frozen=True)
class Problem:
    command_id: str
    key: str          # the key in native form
    reason: str
    error: bool = True    # False: a warning, which does not stop Apply (4B/AM-3)


SK = QKeySequence.StandardKey
# Keys the text widgets take for themselves before any shortcut (4B-D3):
# a shortcut on one of them would silently never fire in a text field.
_TEXT_KEYS = {
    SK.Undo: "Undo", SK.Redo: "Redo", SK.Cut: "Cut", SK.Copy: "Copy", SK.Paste: "Paste",
    SK.SelectAll: "Select All", SK.Deselect: "Deselect", SK.Delete: "Delete",
    SK.Backspace: "Backspace",
    SK.MoveToNextWord: "move by word", SK.MoveToPreviousWord: "move by word",
    SK.MoveToStartOfLine: "move to line start", SK.MoveToEndOfLine: "move to line end",
    SK.MoveToStartOfBlock: "move to paragraph start", SK.MoveToEndOfBlock: "move to paragraph end",
    SK.MoveToStartOfDocument: "move to the start", SK.MoveToEndOfDocument: "move to the end",
    SK.MoveToNextLine: "move by line", SK.MoveToPreviousLine: "move by line",
    SK.MoveToNextPage: "move by page", SK.MoveToPreviousPage: "move by page",
    SK.MoveToNextChar: "move by character", SK.MoveToPreviousChar: "move by character",
    SK.SelectNextWord: "select by word", SK.SelectPreviousWord: "select by word",
    SK.SelectStartOfLine: "select to line start", SK.SelectEndOfLine: "select to line end",
    SK.SelectStartOfBlock: "select to paragraph start",
    SK.SelectEndOfBlock: "select to paragraph end",
    SK.SelectStartOfDocument: "select to the start", SK.SelectEndOfDocument: "select to the end",
    SK.SelectNextLine: "select by line", SK.SelectPreviousLine: "select by line",
    SK.SelectNextPage: "select by page", SK.SelectPreviousPage: "select by page",
    SK.SelectNextChar: "select by character", SK.SelectPreviousChar: "select by character",
    SK.DeleteStartOfWord: "delete a word", SK.DeleteEndOfWord: "delete a word",
    SK.DeleteEndOfLine: "delete to line end", SK.DeleteCompleteLine: "delete the line",
    SK.InsertParagraphSeparator: "new paragraph", SK.InsertLineSeparator: "new line",
}
# The tab-switching keys (4B/AM-2). Shift+Tab arrives as Backtab.
_TAB_KEYS = ("Ctrl+Tab", "Ctrl+Shift+Tab", "Ctrl+Backtab", "Ctrl+Shift+Backtab",
             "Ctrl+PgUp", "Ctrl+PgDown")
# Alt plus these letters opens a menu: File, Edit, View, Settings, Help.
MENU_LETTERS = {Qt.Key_F: "File", Qt.Key_E: "Edit", Qt.Key_V: "View", Qt.Key_S: "Settings",
                Qt.Key_H: "Help"}
# Keys that type text or move/edit in it; without Ctrl, Alt or Meta they are
# typing, not shortcuts. Character keys (below Key_Escape) count too.
_TYPING_SPECIAL = {Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Backspace, Qt.Key_Return, Qt.Key_Enter,
                   Qt.Key_Insert, Qt.Key_Delete, Qt.Key_Escape, Qt.Key_Home, Qt.Key_End,
                   Qt.Key_Left, Qt.Key_Up, Qt.Key_Right, Qt.Key_Down, Qt.Key_PageUp,
                   Qt.Key_PageDown, Qt.Key_Space}
_MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_AltGr, Qt.Key_Meta,
                  Qt.Key_Super_L, Qt.Key_Super_R, Qt.Key_Hyper_L, Qt.Key_Hyper_R,
                  Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock, Qt.Key_unknown}


def reserved_keys() -> dict[str, str]:
    """{portable key: what it is reserved for}, read from the running
    platform's own bindings."""
    out = {}
    for standard, name in _TEXT_KEYS.items():
        for seq in QKeySequence.keyBindings(standard):
            out.setdefault(portable(seq), f"reserved for text editing ({name})")
    for text in _TAB_KEYS:
        out.setdefault(portable(QKeySequence(text)), "reserved for switching tabs")
    return out


def key_problems(seq: QKeySequence) -> list[tuple[str, bool]]:
    """(reason, is_error) for one key on its own, before duplicates."""
    if seq.count() > 1:
        return [("a sequence of several keys: use one key combination", True)]
    if seq.isEmpty() or not portable(seq):
        return [("not a key combination that can be registered", True)]
    chord = seq[0]
    key = chord.key()
    mods = _without(chord.keyboardModifiers(), Qt.KeypadModifier)
    if key in _MODIFIER_KEYS:
        return [("not a key combination that can be registered (a modifier key alone)", True)]
    if QKeySequence.fromString(portable(seq), QKeySequence.PortableText) != seq:
        return [("not a key combination that can be registered", True)]
    if not mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
        if Qt.Key_F1 <= key <= Qt.Key_F24:
            return []
        if key < Qt.Key_Escape or key in _TYPING_SPECIAL:
            return [("types or edits text: a shortcut needs Ctrl, Alt or Meta "
                     "(or is a function key, F1–F24)", True)]
    if mods == Qt.AltModifier and key in MENU_LETTERS:
        return [(f"opens the {MENU_LETTERS[key]} menu", True)]
    reserved = reserved_keys()
    for form in alias_forms(seq):
        if portable(form) in reserved:
            return [(reserved[portable(form)], True)]
    if (mods & Qt.ControlModifier and mods & Qt.AltModifier and not mods & Qt.MetaModifier
            and key < Qt.Key_Escape and key != Qt.Key_Space):
        return [("on some keyboard layouts Ctrl+Alt (AltGr) with this key types a character, "
                 "so the shortcut may not work there", False)]
    return []


def validate(proposed: dict[str, list[QKeySequence]]) -> list[Problem]:
    """Every problem in a whole set of assignments (4B-D3). A key that is
    one of the bindings of Qt's own standard key for that command is the
    platform's choice and is checked only for duplicates (Qt's Find binding
    includes the keyboard's dedicated Find key, for example); every other
    key, a default given as text included, is checked in full (4B/AM-7)."""
    problems: list[Problem] = []
    occupied: dict[str, list[str]] = {}
    for command_id, keys in proposed.items():
        standard = BY_ID[command_id].key if command_id in BY_ID else None
        exempt = ({portable(k) for k in QKeySequence.keyBindings(standard)}
                  if isinstance(standard, QKeySequence.StandardKey) else set())
        for seq in keys:
            if portable(seq) not in exempt:
                for reason, error in key_problems(seq):
                    problems.append(Problem(command_id, native(seq), reason, error))
            for form in alias_forms(seq):
                holders = occupied.setdefault(portable(form), [])
                if command_id not in holders:
                    holders.append(command_id)
    for command_id, keys in proposed.items():
        for seq in keys:
            others = []
            for form in alias_forms(seq):
                others += [c for c in occupied.get(portable(form), []) if c != command_id
                           and c not in others]
            if others:
                names = ", ".join(plain_label(c) for c in others)
                problems.append(Problem(command_id, native(seq), f"also assigned to {names}"))
    return problems


def errors(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.error]


# ---------------------------------------------------------------- storing and applying
def _overrides_of(proposed: dict[str, list[QKeySequence]]) -> dict[str, list[QKeySequence]]:
    out = {}
    for command_id, keys in proposed.items():
        keys = [canonical(k) for k in keys]
        if [portable(k) for k in keys] != [portable(k) for k in default_keys(command_id)]:
            out[command_id] = keys
    return out


def apply(proposed: dict[str, list[QKeySequence]]):
    """Makes a set of assignments current (not stored) and tells every route."""
    global _overrides
    _overrides = _overrides_of({c: k for c, k in proposed.items()
                                if c in BY_ID and BY_ID[c].assignable})
    notifier.changed.emit()


def save(db, proposed: dict[str, list[QKeySequence]]):
    """Stores a valid set (only the changes, in portable form) and applies
    it. A set with errors is refused: nothing is stored (4B-D3)."""
    bad = errors(validate(proposed))
    if bad:
        raise ValueError("; ".join(f"{p.key}: {p.reason}" for p in bad))
    overrides = _overrides_of(proposed)
    db.set_setting(HOTKEYS_SETTING, json.dumps(
        {c: [portable(k) for k in keys] for c, keys in overrides.items()}, sort_keys=True))
    apply(proposed)


def parse_portable(text) -> QKeySequence | None:
    """A stored key, or None when it is not a key Qt reads back as itself."""
    if not isinstance(text, str) or not text:
        return None
    seq = QKeySequence.fromString(text, QKeySequence.PortableText)
    if seq.isEmpty() or portable(seq) != text:
        return None
    return seq


def load(db):
    """Reads the stored keys and applies them (startup, and after a restore
    replaced the settings table). A stored key for a command the table no
    longer has is ignored, and dropped at the next save; a stored key that
    is not valid now (a new default took it, say) is ignored, the default
    is used, and `load_notes` says so (4B, answer 6)."""
    notes: list[str] = []
    raw = db.get_setting(HOTKEYS_SETTING, None)
    stored: dict = {}
    if raw:
        try:
            stored = json.loads(raw)
        except ValueError:
            notes.append("The stored hotkeys could not be read; the defaults are used.")
        if not isinstance(stored, dict):
            stored = {}
    proposed = {c.id: default_keys(c.id) for c in COMMANDS if c.assignable}
    from_user = set()
    for command_id, texts in stored.items():
        if command_id not in proposed or not isinstance(texts, list):
            continue          # an unknown (or fixed) command: ignored
        keys = [parse_portable(t) for t in texts]
        if any(k is None for k in keys):
            bad = ", ".join(str(t) for t, k in zip(texts, keys) if k is None)
            notes.append(f"{plain_label(command_id)}: the stored key {bad} is not a key "
                         f"combination that can be registered; the default is used.")
            continue
        proposed[command_id] = [canonical(k) for k in keys]
        from_user.add(command_id)
    # Drop the user's keys that are invalid now, until the set is valid.
    while True:
        bad = [p for p in errors(validate(proposed)) if p.command_id in from_user]
        if not bad:
            break
        for problem in bad:
            if problem.command_id in from_user:
                from_user.discard(problem.command_id)
                proposed[problem.command_id] = default_keys(problem.command_id)
                notes.append(f"{plain_label(problem.command_id)}: the stored key {problem.key} "
                             f"is not used ({problem.reason}); the default is used.")
    load_notes[:] = notes
    apply(proposed)
