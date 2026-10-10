"""Group 4, batch 4A — the shared command layer and the menus (Master Spec §51).

File | Edit | View | Settings | Help, one QAction per command (FP-10), Edit →
Find as the one Find command, History and Recovery in File (D3), the menu
widths (D10), Settings pages and the Help windows. Criteria C4A-1…C4A-11
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4A plan — agreed 2026-10-04"); C4A-12 is
the edits to older suites, C4A-13 the mutants of the batch report.

Edit → Undo calls the same QTextEdit.undo() as Ctrl+Z, so it also undoes the
invisible view-state steps of bugs 38 and 42 (fixed in 4C2). The undo checks
here therefore settle and size the window before typing and change nothing
between the typing and the undo; the two cases with a view change in between
are strict known failures that fail this suite if they start passing.

FP-9: real key events and real menu clicks; nothing here sends input to the
desktop (FP-15): every event goes to a widget of this process.
"""
import os
import pathlib
import subprocess
import sys
import time
import webbrowser

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-commands-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction, QDesktopServices, QFontMetrics, QKeySequence, QShortcut, QTextCursor, QWheelEvent
)
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import __version__, commands, help_dialogs  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.calendar_prefs import WORK_HOURS_SETTING, ZOOM_SETTING  # noqa: E402
from app.history_dialogs import RecoveryDialog  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import SAVE  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

STARTED = time.monotonic()
print("IMPORTED app FROM", mw.__file__)
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"

failures = []
known = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def known_failing(label, cond, ref, detail=""):
    """A strict known failure (4-0/D5): reported, not counted — until it
    passes, which fails the run so the mark is removed."""
    if cond:
        print(f"  FAIL  {label}  [known-failing check now PASSES ({ref}): remove the known-failing mark]")
        failures.append(label)
    else:
        print(f"  KNOWN FAIL ({ref})  {label}" + (f"  [{detail}]" if detail else ""))
        known.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


# Qt reports an ambiguous shortcut only as a warning ("Ambiguous shortcut
# overload"); collect every one.
qt_messages = []
qInstallMessageHandler(lambda _mode, _context, message: qt_messages.append(message))


def ambiguous():
    return [m for m in qt_messages if "mbiguous" in m]


# Nothing may be launched outside the application (C4A-9).
launched = []
subprocess.Popen = lambda *a, **k: launched.append(("Popen", a)) or None
webbrowser.open = lambda *a, **k: launched.append(("webbrowser", a)) or True
QDesktopServices.openUrl = staticmethod(lambda url: launched.append(("openUrl", url.toString())) or True)
if hasattr(os, "startfile"):
    os.startfile = lambda *a, **k: launched.append(("startfile", a))

mw.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_name = lambda *a, **k: ("Command project", True)
messages = []
QMessageBox.information = staticmethod(lambda *a, **k: messages.append(("info", a[1:3])) or QMessageBox.Ok)
QMessageBox.about = staticmethod(lambda *a, **k: messages.append(("about", a[1:3])))


def new_window():
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.activateWindow()
    settle(300)
    return win


def plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def menu_of(win, title):
    return next(a.menu() for a in win.menuBar().actions() if plain(a.text()) == title)


def labels_of(menu):
    return [plain(a.text()) for a in menu.actions() if not a.isSeparator()]


def click_menu(win, title, action):
    """A real click on the menu bar, then on the entry — the user's route."""
    win.activateWindow()
    settle()
    bar = win.menuBar()
    top = next(a for a in bar.actions() if plain(a.text()) == title)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.actionGeometry(top).center())
    settle(50)
    menu = top.menu()
    check(f"(the {title} menu opened for the click)", menu.isVisible())
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
    settle(50)


def key(widget, k, mods=Qt.NoModifier):
    widget.setFocus()
    settle()
    QTest.keyClick(widget, k, mods)
    settle()


def press(widget, command_id):
    """A command's shortcut as key events: its main binding on this platform,
    taken from the command table (Qt's standard keys where it uses them), so
    e.g. Redo is Ctrl+Y on Windows and Ctrl+Shift+Z on Linux."""
    widget.setFocus()
    settle()
    QTest.keySequence(widget, commands.key_sequences(command_id)[0])
    settle()


def entry_row(win, date):
    return win.db._conn.execute(
        "SELECT body_md, body_format, updated_at FROM entries WHERE date=?", (date,)).fetchone()


def data_snapshot(win):
    """Every user-data table, in full."""
    conn = win.db._conn
    out = {}
    for table in ("entries", "entry_revisions", "projects", "project_versions", "reader_notes",
                  "reader_notes_scoped", "recovery_checkpoints", "calendar_events", "todos",
                  "tasks", "day_markers", "period_titles"):
        try:
            out[table] = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        except Exception as exc:  # a table this schema does not have
            out[table] = repr(exc)
    return out


def settings_snapshot(win):
    return dict(win.db._conn.execute("SELECT key, value FROM settings").fetchall())


win = new_window()
DATE = "2026-03-10"
win._request_date(DATE)
settle()
A = win.command_actions

# ================================================================ 4A/AM-10
print("\n--- [4A/AM-10] the spec's default shortcuts, pinned ---")
SK = QKeySequence.StandardKey
PINNED = {"save": "Ctrl+S", "find": SK.Find, "bold": "Ctrl+B", "italic": "Ctrl+I",
          "underline": "Ctrl+U", "zoom_out": "Ctrl+-", "zoom_reset": "Ctrl+0", "zoom_in": None,
          "undo": SK.Undo, "redo": SK.Redo, "cut": SK.Cut, "copy": SK.Copy, "paste": SK.Paste,
          "select_all": SK.SelectAll}
for command_id, wanted in PINNED.items():
    if wanted is None:
        expected_keys = []
    elif isinstance(wanted, str):
        expected_keys = [QKeySequence(wanted)]
    else:
        expected_keys = list(QKeySequence.keyBindings(wanted))
    check(f"{command_id}: default {wanted if not isinstance(wanted, SK) else wanted.name} "
          f"(the table gives {[k.toString() for k in commands.key_sequences(command_id)]})",
          commands.key_sequences(command_id) == expected_keys
          # a standard key (or none) is pinned as that standard key itself; a
          # portable form ("Ctrl+B") by the keys it stands for on this platform
          and (isinstance(wanted, str) or commands.command(command_id).key == wanted))

# ================================================================ C4A-1
print("\n--- [C4A-1] the menu bar ---")
titles = [plain(a.text()) for a in win.menuBar().actions()]
check(f"the menu bar reads File | Edit | View | Settings | Help {titles}",
      titles == ["File", "Edit", "View", "Settings", "Help"])
expected = {
    "File": ["Save", "History…", "Recovery…", "Back Up Now", "Export Backup…",
             "Restore from Backup…", "Backups & Security…", "Export Readable Archive (HTML)…",
             "Open Data Folder", "Data Usage…", "Find Unused Photos…", "Quit"],
    "Edit": ["Undo", "Redo", "Cut", "Copy", "Paste", "Select All", "Find…"],
    # Since 4A2 (4A2-D5) View ends with the Core Features submenu.
    "View": ["Zoom In", "Zoom Out", "Reset Zoom", "Calendar Zoom In", "Calendar Zoom Out",
             "Reset Calendar Zoom", "Highlight Work Hours", "Core Features"],
    "Settings": ["General…", "Editor…", "Hotkeys…", "Calendar…", "Appearance…", "Backups…"],
    "Help": ["Keyboard Shortcuts…", "Recovery Guide…", "About…"],
}
for title, wanted in expected.items():
    got = labels_of(menu_of(win, title))
    check(f"{title} holds {wanted}", got == wanted, got)
everything = [t for m in expected for t in labels_of(menu_of(win, m))]
from app import core_features  # noqa: E402
core_menu = next(a.menu() for a in menu_of(win, "View").actions() if a.menu())
check("no View All Reader's Notes, no Settings… in File; Core Features holds exactly the "
      "features of core_features.FEATURES, in order (4A2-D5)",
      not [t for t in everything if "View All Reader's Notes" in t]
      and "Settings…" not in labels_of(menu_of(win, "File"))
      and labels_of(core_menu) == [f.label for f in core_features.FEATURES]
      == ["Yearly Calendar", "Projects", "Reader's Notes"], labels_of(core_menu))
check("every entry is one of the window's command actions (a submenu's entries count as its own)",
      all(a in win.command_actions.values()
          for m in [menu_of(win, t) for t in expected] + [core_menu]
          for a in m.actions() if not a.isSeparator() and not a.menu()))

# ================================================================ C4A-2
print("\n--- [C4A-2] one command per operation: Save, History, Recovery ---")
ctrl_s = commands.key_sequences("save")[0]
holders = [a for a in win.findChildren(QAction) if ctrl_s in a.shortcuts()]
check("exactly one QAction in the window has Ctrl+S, and it is File → Save",
      holders == [A["save"]] and A["save"] in menu_of(win, "File").actions(), holders)
check("no QShortcut object is left in the main window",
      not [s for s in win.findChildren(QShortcut)], [s.key().toString() for s in win.findChildren(QShortcut)])
saves = []
A["save"].triggered.connect(lambda *_: saves.append(1))
te = win.editor.text_edit
notes = win.reader_notes.editor.text_edit
notes.setFocus()
QTest.keyClicks(notes, "Notes typed for Save")
te.setFocus()
QTest.keyClicks(te, "Typed for Ctrl+S")
settle()
press(te, "save")
row = entry_row(win, DATE)
check("Ctrl+S triggers the File → Save action (the same object)", saves == [1], saves)
check("...and saves the journal entry and its Reader's Notes",
      row is not None and "Typed for Ctrl+S" in row[0]
      and not win.editor.is_dirty() and not win.reader_notes.is_dirty())
QTest.keyClicks(te, " and the menu")
settle()
click_menu(win, "File", A["save"])
check("File → Save (a real menu click) triggers the same action and saves",
      saves == [1, 1] and "and the menu" in entry_row(win, DATE)[0])
header = [b for b in win.findChildren(mw.QToolButton) if b.defaultAction() is win.history_action]
check("File → History… and the header button are the one history_action",
      len(header) == 1 and win.history_action in menu_of(win, "File").actions()
      and A["history"] is win.history_action)
opened = []
original_recovery_exec = RecoveryDialog.exec
RecoveryDialog.exec = lambda self: opened.append("recovery") or 0
click_menu(win, "File", win.recovery_action)
RecoveryDialog.exec = original_recovery_exec
check("File → Recovery… (a real menu click) opens the Recovery window",
      opened == ["recovery"] and A["recovery"] is win.recovery_action)

# ================================================================ C4A-3
print("\n--- [C4A-3] toolbar button and shortcut are one action, in every editor ---")
EDITORS = {
    "journal": win.editor,
    "date Reader's Notes": win.reader_notes.editor,
}


def show_projects_with_project():
    win.main_tabs.setCurrentWidget(win.projects_widget)
    settle()
    if win.projects_widget.current_project_id is None:
        next(b for b in win.projects_widget.findChildren(mw.QPushButton) if b.text() == "New Project").click()
        settle()


for name, editor in list(EDITORS.items()) + [("project", None), ("project Reader's Notes", None)]:
    if editor is None:
        show_projects_with_project()
        editor = (win.projects_widget.editor if name == "project"
                  else win.projects_widget.reader_notes.editor)
        EDITORS[name] = editor
    else:
        win.main_tabs.setCurrentWidget(win.daily_splitter)
        settle()
    edit = editor.text_edit
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, f"plain words in {name}")
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.StartOfBlock)
    cursor.movePosition(QTextCursor.EndOfWord, QTextCursor.KeepAnchor)
    edit.setTextCursor(cursor)
    settle()
    for cmd, btn, test in (("bold", editor.bold_btn, lambda f: f.fontWeight() >= 700),
                           ("italic", editor.italic_btn, lambda f: f.fontItalic()),
                           ("underline", editor.underline_btn, lambda f: f.fontUnderline())):
        before = btn.isChecked()
        press(edit, cmd)
        by_key = (btn.isChecked(), test(edit.textCursor().charFormat()))
        btn.trigger()
        settle()
        by_click = (btn.isChecked(), test(edit.textCursor().charFormat()))
        check(f"{name}: {commands.shortcut_text(cmd)} and the {cmd} button toggle the same checked state and format",
              by_key == (not before, not before) and by_click == (before, before), (before, by_key, by_click))
        check(f"{name}: the {cmd} button carries the shortcut and is this editor's action",
              btn.shortcuts() == commands.key_sequences(cmd) and btn.shortcuts()
              and btn in editor.actions() and btn.shortcutContext() == Qt.WidgetWithChildrenShortcut)
        check(f"{name}: the {cmd} tooltip comes from the command table ({btn.toolTip()})",
              btn.toolTip() == commands.tooltip(cmd) and commands.shortcut_text(cmd) in btn.toolTip())
    check(f"{name}: no QShortcut object in the editor",
          not editor.findChildren(QShortcut), [s.key().toString() for s in editor.findChildren(QShortcut)])
    if hasattr(editor, "align_center_btn"):
        for cmd, btn, flag in (("align_center", editor.align_center_btn, Qt.AlignHCenter),
                               ("align_right", editor.align_right_btn, Qt.AlignRight),
                               ("align_justify", editor.align_justify_btn, Qt.AlignJustify),
                               ("align_left", editor.align_left_btn, Qt.AlignLeft)):
            press(edit, cmd)
            horizontal = edit.textCursor().blockFormat().alignment() & Qt.AlignHorizontal_Mask
            check(f"{name}: {commands.shortcut_text(cmd)} aligns and checks the {cmd} button (the same action)",
                  horizontal == flag and btn.isChecked() and btn in editor.actions()
                  and btn.toolTip() == commands.tooltip(cmd))
    else:
        # Since 4B the compact editors have the alignment keys without the
        # buttons (bug 12; tests/test_group4_hotkeys.py, CB-8).
        check(f"{name}: compact editor has no alignment buttons",
              not hasattr(editor, "align_left_btn")
              and not [b for b in editor.toolbar.actions()
                       if b in getattr(editor, "alignment_actions", {}).values()])

# Two editors on screen at once never compete for a key.
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
journal_bold = win.editor.bold_btn.isChecked()
press(win.reader_notes.editor.text_edit, "bold")
check("Ctrl+B in the date Reader's Notes leaves the journal's Bold alone",
      win.editor.bold_btn.isChecked() == journal_bold)

# ================================================================ C4A-4 / C4A-5
print("\n--- [C4A-4] Edit commands act on the focused text, as the keys do ---")


def two_steps(edit):
    """A known document with three undo steps at the end: 'first', a new
    paragraph, 'second'. The window is settled and sized; nothing changes
    the view afterwards."""
    edit.setFocus()
    edit.clear()
    edit.document().clearUndoRedoStacks()
    settle()
    QTest.keyClicks(edit, "first")
    QTest.keyClick(edit, Qt.Key_Return)
    QTest.keyClicks(edit, "second")
    settle()


def state(edit):
    return edit.toHtml(), edit.document().availableUndoSteps(), edit.document().availableRedoSteps()


def run_edit(edit, how, command_id):
    if how == "key":
        press(edit, command_id)
    else:
        edit.setFocus()
        settle()
        win._refresh_command_states()
        A[command_id].trigger()
        settle()


for name, editor in EDITORS.items():
    if "project" in name:
        show_projects_with_project()
    else:
        win.main_tabs.setCurrentWidget(win.daily_splitter)
        settle()
    edit = editor.text_edit
    results = {}
    for how in ("key", "menu"):
        two_steps(edit)
        run_edit(edit, how, "undo")
        after_undo = state(edit)
        run_edit(edit, how, "redo")
        results[how] = (after_undo, state(edit), edit.toPlainText())
    check(f"{name}: Edit → Undo gives exactly what Ctrl+Z gives (HTML, undo and redo steps)",
          results["key"][0] == results["menu"][0])
    check(f"{name}: ...and the Undo removed 'second' but kept 'first'",
          "second" not in results["menu"][0][0] and "first" in results["menu"][0][0])
    check(f"{name}: Edit → Redo gives exactly what the Redo key gives",
          results["key"][1] == results["menu"][1] and results["menu"][2].endswith("second"))

    # Select All, Copy, Cut, Paste through the menu and through the keys.
    for how in ("key", "menu"):
        two_steps(edit)
        run_edit(edit, how, "select_all")
        selected = edit.textCursor().selectedText().replace("\u2029", "\n")
        QApplication.clipboard().clear()
        run_edit(edit, how, "copy")
        copied = QApplication.clipboard().text()
        run_edit(edit, how, "cut")
        after_cut = edit.toPlainText()
        run_edit(edit, how, "paste")
        results[how] = (selected, copied, after_cut, edit.toPlainText())
    check(f"{name}: Select All, Copy, Cut and Paste through the menu equal the keys",
          results["key"] == results["menu"] and results["menu"] == ("first\nsecond", "first\nsecond", "",
                                                                    "first\nsecond"), results)

# A text field: the find box of the journal editor.
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
win.editor.show_find_bar()
field = win.editor.find_input
results = {}
for how in ("key", "menu"):
    field.setFocus()
    field.clear()
    QTest.keyClicks(field, "abc")
    QTest.keyClicks(field, "def")
    settle()
    if how == "key":
        press(field, "undo")
    else:
        win._refresh_command_states()
        A["undo"].trigger()
        settle()
    undone = field.text()
    if how == "key":
        press(field, "select_all")
    else:
        A["select_all"].trigger()
        settle()
    results[how] = (undone, field.selectedText())
check("a focused text field: Edit → Undo and Select All act on it as its keys do",
      results["key"] == results["menu"] and isinstance(field, QLineEdit), results)
win.editor.hide_find_bar()

# With no text focused, the Edit commands are disabled.
win.calendar_panel.calendar.setFocus()
settle()
win._refresh_command_states()
check("with the focus outside any text, Undo, Redo, Cut, Copy, Paste and Select All are disabled",
      not any(A[c].isEnabled() for c in ("undo", "redo", "cut", "copy", "paste", "select_all")),
      QApplication.focusWidget())

# The real menu route: a click on Edit, then Undo, with the focus in the editor.
two_steps(te)
click_menu(win, "Edit", A["undo"])
check("Edit → Undo by a real menu click undoes the last typing in the focused editor",
      te.toPlainText() == "first\n", repr(te.toPlainText()))
check("no ambiguous-shortcut warning so far", not ambiguous(), ambiguous())

print("\n--- [C4A-5] bugs 38 and 42 through Edit → Undo (strict known failures → 4C2) ---")
two_steps(te)
win.editor.zoom_spin.setValue(120)
settle()
te.setFocus()
win._refresh_command_states()
A["undo"].trigger()
settle()
known_failing("Edit → Undo after a zoom change undoes the last typing", te.toPlainText() == "first\n",
              "bug 42 → 4C2", repr(te.toPlainText()[-20:]))
win.editor.zoom_spin.setValue(100)
settle()
two_steps(notes)
size_before = win.db.get_setting("ui_font_size", "")
win.db.set_setting("ui_font_size", "16")
win._apply_settings()
settle(100)
notes.setFocus()
win._refresh_command_states()
A["undo"].trigger()
settle()
known_failing("Edit → Undo after a UI-font change undoes the last typing (date Reader's Notes)",
              notes.toPlainText() == "first\n", "bug 38 → 4C2", repr(notes.toPlainText()[-20:]))
if size_before:
    win.db.set_setting("ui_font_size", size_before)
else:
    win.db._conn.execute("DELETE FROM settings WHERE key='ui_font_size'")
    win.db._conn.commit()
win._apply_settings()
settle(100)

# ================================================================ C4A-6
print("\n--- [C4A-6] Find is one command ---")
ctrl_f = commands.key_sequences("find")[0]
holders = [a for a in win.findChildren(QAction) if ctrl_f in a.shortcuts()]
check("exactly one QAction in the window has Ctrl+F, and it is Edit → Find",
      holders == [A["find"]] and A["find"] in menu_of(win, "Edit").actions(), holders)
check("no 🔍 action in any toolbar",
      not [a for a in win.findChildren(QAction) if "🔍" in a.text() or "Find in this" in a.toolTip()])
finds = []
A["find"].triggered.connect(lambda *_: finds.append(1))
for name, editor in EDITORS.items():
    if "project" in name:
        show_projects_with_project()
    else:
        win.main_tabs.setCurrentWidget(win.daily_splitter)
        settle()
    for e in EDITORS.values():
        e.hide_find_bar()
    settle()
    count = len(finds)
    press(editor.text_edit, "find")
    others = [n for n, e in EDITORS.items() if e is not editor and e.find_bar.isVisible()]
    check(f"{name}: Ctrl+F triggers Edit → Find and opens this editor's find bar only",
          len(finds) == count + 1 and editor.find_bar.isVisible() and not others
          and editor.find_input.hasFocus(), others)
    editor.hide_find_bar()
    editor.text_edit.setFocus()
    click_menu(win, "Edit", A["find"])
    check(f"{name}: Edit → Find (a real menu click) opens the same find bar",
          editor.find_bar.isVisible() and len(finds) == count + 2)
    editor.hide_find_bar()
# Nothing focused: the workspace's editor.
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
win.calendar_panel.calendar.setFocus()
settle()
A["find"].trigger()
settle()
check("Daily Jorts, focus on the calendar: Find opens the journal entry's find bar",
      win.editor.find_bar.isVisible() and not win.reader_notes.editor.find_bar.isVisible())
win.editor.hide_find_bar()
show_projects_with_project()
win.projects_widget.project_list.setFocus()
settle()
A["find"].trigger()
settle()
check("Projects, focus outside the editors: Find opens the project's find bar",
      win.projects_widget.editor.find_bar.isVisible())
win.projects_widget.editor.hide_find_bar()
for tab in (win.week_calendar, win.year_calendar):
    win.main_tabs.setCurrentWidget(tab)
    settle()
    win._refresh_command_states()
    check(f"{tab.__class__.__name__}: Find and the editor zoom commands are disabled",
          not any(A[c].isEnabled() for c in ("find", "zoom_in", "zoom_out", "zoom_reset")))
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
preview = RichEditor(read_only=True)
preview.load("<p>a read-only preview with words</p>", "html")
preview.resize(500, 300)
preview.show()
preview.activateWindow()
settle(100)
press(preview.text_edit, "find")
check("Ctrl+F still opens the find bar in a read-only preview (its own shortcut, same show_find_bar)",
      preview.find_bar.isVisible())
preview.close()
win.activateWindow()
settle()

# ================================================================ C4A-7
print("\n--- [C4A-7] zoom and the calendar view commands ---")
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
te.setFocus()
settle()
A["zoom_in"].trigger()
settle()
by_menu = (te.zoom_percent(), win.editor.zoom_spin.value())
A["zoom_reset"].trigger()
settle()
win.editor.zoom_spin.setValue(110)
settle()
by_box = (te.zoom_percent(), win.editor.zoom_spin.value())
check(f"View → Zoom In changes the editor's zoom exactly as the Zoom % box does {by_menu} {by_box}",
      by_menu == by_box == (110, 110))
A["zoom_out"].trigger()
settle()
check("View → Zoom Out", te.zoom_percent() == 100)
A["zoom_out"].trigger()
A["zoom_reset"].trigger()
settle()
check("View → Reset Zoom", te.zoom_percent() == 100 and win.editor.zoom_spin.value() == 100)
for name, editor in (("journal", win.editor), ("date Reader's Notes", win.reader_notes.editor)):
    edit = editor.text_edit
    press(edit, "zoom_out")
    out = edit.zoom_percent()
    press(edit, "zoom_reset")
    reset = edit.zoom_percent()
    key(edit, Qt.Key_Equal, Qt.ControlModifier)
    key(edit, Qt.Key_Plus, Qt.ControlModifier)
    key(edit, Qt.Key_Plus, Qt.ControlModifier | Qt.ShiftModifier)
    check(f"{name}: {commands.shortcut_text('zoom_out')} zooms out, {commands.shortcut_text('zoom_reset')} "
          f"resets, Ctrl+= and Ctrl++ no longer zoom in (G4-D1)",
          (out, reset, edit.zoom_percent()) == (90, 100, 100), (out, reset, edit.zoom_percent()))
zoom_tips = {a.text(): a.toolTip() for a in win.editor.toolbar.actions() if a.text() in ("−", "+", "⟲")}
check(f"the toolbar zoom buttons' tooltips come from the command table {zoom_tips}",
      zoom_tips == {"−": commands.tooltip("zoom_out"), "+": commands.tooltip("zoom_in"),
                    "⟲": commands.tooltip("zoom_reset")} and "(" not in zoom_tips["+"])
check("the zoom keys are the View actions' (Ctrl+−, Ctrl+0), zoom in has none",
      A["zoom_out"].shortcuts() == commands.key_sequences("zoom_out") == [QKeySequence("Ctrl+-")]
      and A["zoom_reset"].shortcuts() == commands.key_sequences("zoom_reset") == [QKeySequence("Ctrl+0")]
      and A["zoom_in"].shortcuts() == [])
prefs = win.calendar_prefs
prefs.reset_zoom()
A["calendar_zoom_in"].trigger()
by_menu = prefs.zoom_percent
prefs.reset_zoom()
grid = win.day_calendar.timeline
QApplication.sendEvent(grid, QWheelEvent(QPointF(10, 10), QPointF(grid.mapToGlobal(QPoint(10, 10))),
                                         QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.ControlModifier,
                                         Qt.NoScrollPhase, False))
by_wheel = prefs.zoom_percent
check(f"View → Calendar Zoom In equals one Ctrl+wheel step ({by_menu} vs {by_wheel})",
      by_menu == by_wheel and by_menu > 100)
A["calendar_zoom_out"].trigger()
A["calendar_zoom_out"].trigger()
out = prefs.zoom_percent
A["calendar_zoom_reset"].trigger()
check("Calendar Zoom Out and Reset Calendar Zoom", out < 100 and prefs.zoom_percent == 100)
A["work_hours"].trigger()
settle()
check("View → Highlight Work Hours turns work hours on and persists it",
      prefs.work_hours_enabled and A["work_hours"].isChecked()
      and win.db.get_setting(WORK_HOURS_SETTING) == "1")
dialogs = []
original_settings_exec = SettingsDialog.exec


def capture_exec(self):
    dialogs.append(self)
    return 0


SettingsDialog.exec = capture_exec
A["settings_calendar"].trigger()
settings_box = dialogs[-1]
check("...and the Settings checkbox shows it", settings_box.work_hours_check.isChecked())
settings_box.work_hours_check.setChecked(False)
settle()
check("turning it off in Settings unchecks View → Highlight Work Hours",
      not A["work_hours"].isChecked() and not prefs.work_hours_enabled
      and win.db.get_setting(WORK_HOURS_SETTING) == "0")
settings_box.work_hours_check.setChecked(True)
settle()
check("...and on again checks it", A["work_hours"].isChecked())

# ================================================================ C4A-8
print("\n--- [C4A-8] Settings: one window, at the page asked for ---")
pages = {"general": ["autosave_check"],
         "editor": ["font_combo", "size_spin", "writing_position_combo"],
         "hotkeys": ["hotkeys_page"],
         "calendar": ["work_hours_check"],
         "appearance": ["scheme_combo", "ui_size_spin"],
         "backups": ["backup_folder", "backup_status"]}
for page, attrs in pages.items():
    count = len(dialogs)
    click_menu(win, "Settings", A[f"settings_{page}"])
    made = dialogs[count:]
    check(f"Settings → {page.title()}: one Settings window, opened modally (exec) at that page",
          len(made) == 1 and made[0].current_page() == page
          and len([w for w in QApplication.topLevelWidgets()
                   if isinstance(w, SettingsDialog) and w.isVisible()]) == 0, len(made))
    dlg = made[0]
    for attr in attrs:
        widget = getattr(dlg, attr)
        holders = [p for p in SettingsDialog.PAGES if dlg._contents[p].isAncestorOf(widget)]
        check(f"  {attr} is on the {page} page, once", holders == [page], holders)
swatches = dialogs[-1].swatch_buttons
check("the colour swatches are on the Appearance page",
      all(dialogs[-1]._contents["appearance"].isAncestorOf(b) for b in swatches.values()))
SettingsDialog.exec = original_settings_exec

# ================================================================ C4A-9
print("\n--- [C4A-9] Help ---")
shown = []
original_shortcuts_exec = help_dialogs.KeyboardShortcutsDialog.exec
help_dialogs.KeyboardShortcutsDialog.exec = lambda self: shown.append(self) or 0
click_menu(win, "Help", A["keyboard_shortcuts"])
help_dialogs.KeyboardShortcutsDialog.exec = original_shortcuts_exec
table = shown[0].table
listed = [tuple(table.item(r, c).text() for c in range(3)) for r in range(table.rowCount())]
check("Keyboard Shortcuts lists every command that has a shortcut, with that shortcut",
      listed == commands.shortcut_rows()
      and {r[0] for r in listed} == {commands.plain_label(c.id) for c in commands.COMMANDS
                                     if commands.key_sequences(c.id)})
print("  (bold row in Keyboard Shortcuts:", [r for r in listed if r[0] == "Bold"], ")")
for cmd in ("save", "find", "bold", "zoom_out", "undo"):
    check(f"  ...including {cmd} as {commands.shortcut_text(cmd)}",
          any(r[0] == commands.plain_label(cmd) and commands.shortcut_text(cmd) in r[1] for r in listed))
# Since 4B the Hotkeys page is an editor of every command's key (4B-D4); it
# shows each listed shortcut in that command's row.
hotkeys = dialogs[-1].hotkeys_page
page_rows = {}
for r in range(hotkeys.table.rowCount()):
    label = hotkeys.table.item(r, 0).text()
    editor = hotkeys.table.cellWidget(r, 1)
    page_rows[label] = (editor.keySequence().toString(QKeySequence.NativeText) if editor is not None
                        else hotkeys.table.item(r, 1).text(), hotkeys.table.item(r, 3).text())
check("  ...and the Hotkeys page shows the same shortcuts, in each command's row",
      all(page_rows.get(label) == (key, where) for label, key, where in listed),
      [(label, page_rows.get(label)) for label, key, where in listed
       if page_rows.get(label) != (key, where)])
guides = []
original_guide_exec = help_dialogs.RecoveryGuideDialog.exec
help_dialogs.RecoveryGuideDialog.exec = lambda self: guides.append(self) or 0
click_menu(win, "Help", A["recovery_guide"])
help_dialogs.RecoveryGuideDialog.exec = original_guide_exec
guide_text = guides[0].browser.toPlainText()
check("Recovery Guide shows docs/RECOVERY.md",
      "Recovering a jortle_claude journal without jortle_claude" in guide_text
      and "db-key.age" in guide_text)
check("...and starts no process", not launched, launched)

# Clicking links: in the real guide (if it has any) and in a guide with each kind.
link_doc = ROOT / "links.md"
link_doc.write_text("# Links\n\n[web](https://example.org/x)\n\n[file](file:///C:/Windows/notepad.exe)\n\n"
                    "[relative](other.md)\n\n[inside](#links)\n", encoding="utf-8")


def click_every_link(dialog):
    dialog.show()
    settle(100)
    browser = dialog.browser
    doc = browser.document()
    clicked = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            href = fragment.charFormat().anchorHref()
            if href:
                cursor = QTextCursor(doc)
                cursor.setPosition(fragment.position() + 1)
                browser.setTextCursor(cursor)
                browser.ensureCursorVisible()
                settle()
                rect = browser.cursorRect(cursor)
                QTest.mouseClick(browser.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
                settle()
                clicked.append(href)
            it += 1
        block = block.next()
    dialog.close()
    return clicked


real_links = click_every_link(help_dialogs.RecoveryGuideDialog(win))
test_dialog = help_dialogs.RecoveryGuideDialog(win, path=link_doc)
test_links = click_every_link(test_dialog)
check(f"clicking every link in the Recovery Guide window ({len(real_links)} in the real guide, "
      f"{test_links} in a test guide) opens nothing outside the app and starts no process",
      len(test_links) == 4 and not launched and "Link (not opened)" in test_dialog.link_label.text(),
      launched)
click_menu(win, "Help", A["about"])
about = [m for m in messages if m[0] == "about"]
check(f"About shows the version {__version__}", about and __version__ in about[-1][1][1], about)

# ================================================================ C4A-10
print("\n--- [C4A-10] menu widths follow their content (D10; 4A/AM-8) ---")
# Each menu is exactly as wide as a reference menu with the same entries, and
# nothing is clipped. The reference is a plain QMenu built here (not through
# the application's menu code) with the same labels, shortcuts and check
# marks, under the same font and stylesheet: it is Qt's own width for that
# content, so any width the application imposes shows as a difference.
size_before = win.db.get_setting("ui_font_size", "")
for size in (9, 13, 24):
    win.db.set_setting("ui_font_size", str(size))
    win._apply_settings()
    settle(100)
    for top in win.menuBar().actions():
        menu = top.menu()
        menu.popup(win.mapToGlobal(QPoint(40, 40)))
        settle(50)
        metrics = QFontMetrics(menu.font())
        entries = [a for a in menu.actions() if not a.isSeparator()]
        clipped = [plain(a.text()) for a in entries
                   if menu.actionGeometry(a).width()
                   < metrics.horizontalAdvance(plain(a.text()))
                   + metrics.horizontalAdvance(a.shortcut().toString(QKeySequence.NativeText))]
        reference = QMenu()
        reference.setFont(menu.font())
        reference.setStyleSheet(menu.styleSheet())
        for a in menu.actions():
            if a.isSeparator():
                reference.addSeparator()
            elif a.menu():
                reference.addMenu(QMenu(a.text(), reference))   # a submenu entry (4A2)
            else:
                entry = reference.addAction(a.text())
                entry.setShortcuts(a.shortcuts())
                entry.setCheckable(a.isCheckable())
                entry.setChecked(a.isChecked())
        reference.popup(win.mapToGlobal(QPoint(60, 60)))
        settle(20)
        same_style = (reference.font() == menu.font()
                      and reference.styleSheet() == menu.styleSheet()
                      and app.styleSheet() != "")
        check(f"{size}pt {plain(top.text())}: {menu.width()}px, as wide as a reference menu with the same "
              f"entries ({reference.width()}px), nothing clipped",
              same_style and menu.width() == reference.width() and not clipped, clipped)
        reference.hide()
        reference.deleteLater()
        menu.hide()
        settle()
    check(f"{size}pt: the menu bar is not the window's width floor "
          f"({win.menuBar().minimumSizeHint().width()} ≤ {win.centralWidget().minimumSizeHint().width()})",
          win.menuBar().minimumSizeHint().width() <= win.centralWidget().minimumSizeHint().width())
if size_before:
    win.db.set_setting("ui_font_size", size_before)
else:
    win.db._conn.execute("DELETE FROM settings WHERE key='ui_font_size'")
    win.db._conn.commit()
win._apply_settings()
settle(100)

# ================================================================ C4A-11
print("\n--- [C4A-11] the commands change no stored data ---")
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
win._request_date("2026-03-11")
settle()
te.setFocus()
QTest.keyClicks(te, "Stored text for C4A-11")
press(te, "save")
# Leaving an entry typed in during this visit records a "left the entry"
# version (entry_history); leave and come back first, so this visit has no
# typing of its own and leaving it again must write nothing.
win._request_date("2026-03-12")
settle()
win._request_date("2026-03-11")
settle()
data_before = data_snapshot(win)
settings_before = settings_snapshot(win)
undo_before = te.document().availableUndoSteps()
te.setFocus()
for command_id in ("find", "zoom_out", "zoom_reset", "calendar_zoom_in", "calendar_zoom_reset",
                   "keyboard_shortcuts", "recovery_guide", "about"):
    for cls in (help_dialogs.KeyboardShortcutsDialog, help_dialogs.RecoveryGuideDialog):
        cls.exec = lambda self: 0
    A[command_id].trigger()
    settle()
help_dialogs.KeyboardShortcutsDialog.exec = original_shortcuts_exec
help_dialogs.RecoveryGuideDialog.exec = original_guide_exec
SettingsDialog.exec = lambda self: 0
for page in SettingsDialog.PAGES:
    A[f"settings_{page}"].trigger()
    settle()
SettingsDialog.exec = original_settings_exec
win.editor.hide_find_bar()
check("Find, zoom, calendar zoom, Help and opening every Settings page change no stored row",
      data_snapshot(win) == data_before)
changed = {k for k in set(settings_before) | set(settings_snapshot(win))
           if settings_before.get(k) != settings_snapshot(win).get(k)}
check(f"...and no setting other than the calendar zoom the user changed ({changed})",
      changed <= {ZOOM_SETTING})
check("...and the entry is not marked modified", not win.editor.is_dirty())
win._request_date("2026-03-12")
settle()
after = data_snapshot(win)
check("leaving the entry afterwards writes nothing", after == data_before,
      [t for t in after if after[t] != data_before[t]])

print("\n--- restart: work hours and the menus come back ---")
win.close()
settle()
win2 = new_window()
check("after a restart work hours are still on, and View → Highlight Work Hours is checked",
      win2.calendar_prefs.work_hours_enabled and win2.command_actions["work_hours"].isChecked())
check("no ambiguous-shortcut warning in the whole run", not ambiguous(), ambiguous())
win2.close()
settle()

print(f"\n{len(known)} known failure(s): {known}")
print(f"elapsed {time.monotonic() - STARTED:.1f}s")
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
