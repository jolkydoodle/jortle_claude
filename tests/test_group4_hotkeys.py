"""Group 4, batch 4B — configurable hotkeys (Master Spec §§14.8, 14.10, 51.1,
51.3; G4-D1), plus bug 12 (alignment keys in Reader's Notes), bug 40 (a
release on a link after a drag navigates) and bug 44 (the shortcut tables).

Criteria CB-1…CB-11 and the amendments 4B/AM-1…AM-4
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4B plan — agreed 2026-10-08"); CB-12's
mutants and CB-13 are the batch report's.

Real key events throughout (FP-9): the =/+ forms a US keyboard produces,
keys typed into the Hotkeys page's key editors, mouse drags and clicks on
links. Nothing here sends input to the desktop (FP-15): every event goes to
a widget of this process. The layout checks of bug 44 run in child processes
with a screen of their own (`--layout <file>`).
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LAYOUT = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--layout" else None
ROOT = isolation.isolate(prefix="jortle-g4-hotkeys-layout-" if LAYOUT else "jortle-g4-hotkeys-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt, QTranslator, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QDesktopServices, QKeySequence, QTextCharFormat, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QKeySequenceEdit, QMessageBox, QScrollArea,  # noqa: E402
                               QWidget)

app = QApplication.instance() or QApplication([])

from app import backup, backup_dialog, commands, help_dialogs, security  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.database import Database  # noqa: E402
from app.hotkeys_page import COL_NOTE, ERROR_COLOUR, SYSTEM_NOTE, WARNING_COLOUR  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import SAVE  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402
from app.theme import PRESETS, build_stylesheet, scheme_to_json  # noqa: E402

STARTED = time.monotonic()
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"

failures = []
CTRL, SHIFT, ALT = Qt.ControlModifier, Qt.ShiftModifier, Qt.AltModifier


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


def native(text):
    return QKeySequence(text).toString(QKeySequence.NativeText)


# ===================================================================== bug 44
def content_width(table, widget_cols=None):
    """The width that shows every column whole, measured from the contents
    (not from the columns' current widths, which a stretched column inflates)."""
    widget_cols = widget_cols or {}
    header = table.horizontalHeader()
    columns = sum(max([table.sizeHintForColumn(c), header.sectionSizeHint(c)]
                      + [w.minimumSizeHint().width() for w in widget_cols.get(c, [])])
                  for c in range(table.columnCount()))
    return columns + 2 * table.frameWidth() + table.verticalScrollBar().sizeHint().width()


def layout_child(screen_file):
    """Both shortcut tables at 9, 13 and 24 pt, Light and Dark, with the
    application's stylesheet, on this process's screen (CB-10)."""
    db = Database()
    screen = app.primaryScreen().availableGeometry()
    print(f"  (screen {screen.width()}x{screen.height()})")
    parent = QWidget()
    parent.resize(min(700, screen.width() - 100), min(500, screen.height() - 100))
    parent.move(screen.topLeft() + QPoint(20, 20))
    parent.show()
    settle()
    for scheme in ("Light", "Dark"):
        for size in (9, 13, 24):
            db.set_setting("color_scheme", scheme_to_json(PRESETS[scheme]))
            sheet = build_stylesheet(PRESETS[scheme], size)
            if not app.styleSheet():
                app.setStyleSheet(sheet)
            font = app.font()
            font.setPointSize(size)
            app.setFont(font)
            app.setStyleSheet(sheet)
            settle()
            where = f"{scheme} {size}pt"
            # ---- Help → Keyboard Shortcuts
            dlg = help_dialogs.KeyboardShortcutsDialog(parent)
            dlg.show()
            settle(20)
            table = dlg.table
            heights = {table.rowHeight(r) for r in range(table.rowCount())}
            check(f"{where} Help: every row one height {sorted(heights)}, not below the text "
                  f"({table.fontMetrics().height()})",
                  len(heights) == 1 and min(heights) >= table.fontMetrics().height(), sorted(heights))
            margins = dlg.layout().contentsMargins()
            needed = content_width(table) + margins.left() + margins.right()
            room = needed <= screen.width() - 40
            cut = [c for c in range(table.columnCount())
                   if table.columnWidth(c) < table.sizeHintForColumn(c)]
            if room:
                check(f"{where} Help: the screen has room, so no column is cut off and no "
                      f"horizontal scrollbar (columns {[table.columnWidth(c) for c in range(3)]})",
                      not cut and table.horizontalScrollBar().maximum() == 0, cut)
            else:
                check(f"{where} Help: no room on this screen — every column still whole, reached by "
                      f"the horizontal scrollbar", not cut and table.horizontalScrollBar().maximum() > 0,
                      (cut, table.horizontalScrollBar().maximum(), needed, dlg.width(),
                       [table.columnWidth(c) for c in range(3)]))
            frame = dlg.frameGeometry()
            check(f"{where} Help: inside the screen {frame.getRect()}", screen.contains(frame),
                  (frame.getRect(), screen.getRect()))
            dlg.close()
            # ---- Settings → Hotkeys
            sd = SettingsDialog(db, on_change=lambda: None, parent=parent, page="hotkeys")
            sd.show()
            settle(20)
            page = getattr(sd, "hotkeys_page", None)
            if page is None:
                check(f"{where} Hotkeys: the page is an editor of the keys", False)
                sd.close()
                continue
            table = page.table
            heights = {table.rowHeight(r) for r in range(table.rowCount())}
            tallest = max(w.sizeHint().height() for w in list(page.editors.values())
                          + list(page.default_buttons.values()))
            check(f"{where} Hotkeys: every row one height {sorted(heights)}, as tall as its controls "
                  f"({tallest})", len(heights) == 1 and min(heights) >= tallest, sorted(heights))
            widget_cols = {1: list(page.editors.values()), 2: list(page.default_buttons.values())}
            cut = [c for c in range(table.columnCount())
                   if table.columnWidth(c) < table.sizeHintForColumn(c)
                   or any(table.columnWidth(c) < w.minimumSizeHint().width()
                          for w in widget_cols.get(c, []))]
            scroll = next(s for s in sd.findChildren(QScrollArea) if s.widget() is sd._contents["hotkeys"])
            sideways = scroll.horizontalScrollBar().maximum() + table.horizontalScrollBar().maximum()
            needed = content_width(table, widget_cols) + sd._chrome_width()
            room = needed <= screen.width() - 40
            if room:
                check(f"{where} Hotkeys: the screen has room, so no column is cut off and nothing "
                      f"scrolls sideways (window {sd.width()}, needs {needed})",
                      not cut and sideways == 0, (cut, sideways))
            else:
                check(f"{where} Hotkeys: no room on this screen (needs {needed}) — every column still "
                      f"whole, reached by the horizontal scrollbar", not cut and sideways > 0,
                      (cut, sideways))
            frame = sd.frameGeometry()
            check(f"{where} Hotkeys: inside the screen {frame.getRect()}", screen.contains(frame),
                  (frame.getRect(), screen.getRect()))
            # With a problem shown, the Note column has text: it too stays whole
            # (the table scrolls sideways instead of squeezing it).
            page.set_key("bold", QKeySequence("Ctrl+S"))
            settle()
            cut = [c for c in range(table.columnCount())
                   if table.columnWidth(c) < table.sizeHintForColumn(c)]
            check(f"{where} Hotkeys: with a problem note shown, every column is still whole "
                  f"(columns {[table.columnWidth(c) for c in range(table.columnCount())]})",
                  not cut and table.item(page.rows["bold"], COL_NOTE).text(), cut)
            sd.close()
            settle()
    parent.close()
    print("RESULT:" + json.dumps({"failures": failures}))
    sys.exit(0)


if LAYOUT:
    layout_child(LAYOUT)

# ===================================================================== main run
print("IMPORTED app FROM", mw.__file__)
qt_messages = []
qInstallMessageHandler(lambda _mode, _context, message: qt_messages.append(message))
opened_urls = []
QDesktopServices.openUrl = staticmethod(lambda url: opened_urls.append(url.toString()) or True)
mw.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_name = lambda *a, **k: ("Hotkeys project", True)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
security.update_config(get_data_dir(), storage_choice="unencrypted", automatic_backups="off")

emitted_changes = []
commands.notifier.changed.connect(lambda: emitted_changes.append(1))


def new_window():
    win = mw.MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.activateWindow()
    settle(200)
    return win


win = new_window()
A = win.command_actions
DATE = "2026-03-10"
win._request_date(DATE)
settle()


def show_projects():
    win.main_tabs.setCurrentWidget(win.projects_widget)
    settle()
    if win.projects_widget.current_project_id is None:
        next(b for b in win.projects_widget.findChildren(mw.QPushButton) if b.text() == "New Project").click()
        settle()


def show_daily():
    win.main_tabs.setCurrentWidget(win.daily_splitter)
    settle()


def editors():
    """(name, editor, show) for the four writing editors."""
    return [("journal", win.editor, show_daily),
            ("date Reader's Notes", win.reader_notes.editor, show_daily),
            ("project", win.projects_widget.editor, show_projects),
            ("project Reader's Notes", win.projects_widget.reader_notes.editor, show_projects)]


def select_first_word(editor, text):
    edit = editor.text_edit
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.StartOfBlock)
    cursor.movePosition(QTextCursor.EndOfWord, QTextCursor.KeepAnchor)
    edit.setTextCursor(cursor)
    settle()


def key_in(widget, k, mods=Qt.NoModifier):
    # The window must be the active one for its shortcuts (a Settings window
    # shown by the test may have been the active one).
    widget.window().activateWindow()
    widget.setFocus()
    settle()
    QTest.keyClick(widget, k, mods)
    settle()


def vertical(editor):
    return editor.text_edit.textCursor().charFormat().verticalAlignment()


# ===================================================================== CB-1
print("\n--- [CB-1] superscript Ctrl+Shift+=, subscript Ctrl+=, real key events, no zoom ---")
check("defaults: superscript Ctrl+Shift+=, subscript Ctrl+=, zoom in none (G4-D1)",
      commands.default_keys("superscript") == [QKeySequence("Ctrl+Shift+=")]
      and commands.default_keys("subscript") == [QKeySequence("Ctrl+=")]
      and commands.default_keys("zoom_in") == [])
SUPER_FORMS = [("Ctrl+Shift+Key_Plus (what a US keyboard sends)", Qt.Key_Plus, CTRL | SHIFT),
               ("Ctrl+Key_Plus", Qt.Key_Plus, CTRL),
               ("Ctrl+Shift+Key_Equal", Qt.Key_Equal, CTRL | SHIFT)]
for name, editor, show in editors():
    show()
    select_first_word(editor, f"scriptword in {name}")
    edit = editor.text_edit
    zoom_before = (edit.zoom_percent(), A["zoom_in"].isEnabled())
    key_in(edit, Qt.Key_Equal, CTRL)
    check(f"{name}: Ctrl+= makes subscript", vertical(editor) == QTextCharFormat.AlignSubScript,
          vertical(editor))
    key_in(edit, Qt.Key_Equal, CTRL)
    check(f"{name}: ...and again turns it off", vertical(editor) == QTextCharFormat.AlignNormal)
    for form, k, mods in SUPER_FORMS:
        key_in(edit, k, mods)
        on = vertical(editor) == QTextCharFormat.AlignSuperScript
        key_in(edit, k, mods)
        off = vertical(editor) == QTextCharFormat.AlignNormal
        check(f"{name}: {form} toggles superscript", on and off, (on, off))
    check(f"{name}: none of them zoomed ({edit.zoom_percent()}%)", edit.zoom_percent() == zoom_before[0])
    edit.moveCursor(QTextCursor.End)
    typed_before = edit.toPlainText()
    for k, mods in ((Qt.Key_Plus, Qt.NoModifier), (Qt.Key_Plus, SHIFT), (Qt.Key_Equal, Qt.NoModifier)):
        key_in(edit, k, mods)
    added = edit.toPlainText()[len(typed_before):]
    check(f"AM-6 {name}: a plain '+' (with and without Shift) and '=' type themselves ({added!r}) and "
          f"toggle nothing", added == "++=" and vertical(editor) == QTextCharFormat.AlignNormal
          and not editor.super_btn.isChecked() and not editor.sub_btn.isChecked(), added)
    # A text field takes a printable key before any shortcut, so the typing
    # check alone cannot see a bare "+" shortcut; with the focus on a control
    # that types nothing (the heading box in the editor's own toolbar), it
    # would fire.
    select_first_word(editor, f"plusword in {name}")
    for k, mods in ((Qt.Key_Plus, Qt.NoModifier), (Qt.Key_Plus, SHIFT), (Qt.Key_Equal, Qt.NoModifier)):
        key_in(editor.heading_combo, k, mods)
    check(f"AM-6 {name}: '+' and '=' with the focus on the heading box toggle nothing",
          vertical(editor) == QTextCharFormat.AlignNormal and editor.heading_combo.currentIndex() == 0)
    check(f"{name}: tooltips {editor.super_btn.toolTip()!r}, {editor.sub_btn.toolTip()!r}",
          editor.super_btn.toolTip() == f"Superscript ({native('Ctrl+Shift+=')})"
          and editor.sub_btn.toolTip() == f"Subscript ({native('Ctrl+=')})")
show_daily()

# ===================================================================== CB-8
print("\n--- [CB-8] bug 12: alignment keys in both Reader's Notes editors, no buttons ---")
for name, editor, show in editors()[1::2]:
    show()
    select_first_word(editor, f"aligned words in {name}")
    edit = editor.text_edit
    results = []
    for command_id, k, flag in (("align_center", Qt.Key_E, Qt.AlignHCenter),
                                ("align_right", Qt.Key_R, Qt.AlignRight),
                                ("align_justify", Qt.Key_J, Qt.AlignJustify),
                                ("align_left", Qt.Key_L, Qt.AlignLeft)):
        key_in(edit, k, CTRL | SHIFT)
        results.append((command_id, int(edit.textCursor().blockFormat().alignment()
                                        & Qt.AlignHorizontal_Mask) == int(flag)))
    check(f"{name}: Ctrl+Shift+E/R/J/L align the paragraph {results}", all(r for _c, r in results), results)
    toolbar_actions = editor.toolbar.actions()
    check(f"{name}: still no alignment buttons; the keys are actions of the editor",
          not hasattr(editor, "align_left_btn") and editor.paragraph_toolbar is None
          and all(a in editor.actions() and a not in toolbar_actions
                  for a in getattr(editor, "alignment_actions", {}).values())
          and len(getattr(editor, "alignment_actions", {})) == 4)
check("the alignment commands' Where reads 'In a writing editor'",
      all(commands.command(c).where == commands.IN_EDITOR
          for c in ("align_left", "align_center", "align_right", "align_justify")))
show_daily()

# ===================================================================== CB-9
print("\n--- [CB-9 / AM-4] bug 40: a release on a link navigates only after a click ---")
LINKS = ('<p>Words before <a href="journal://date/2026-03-01">2026-03-01</a> and words after it here, '
         'then <a href="journal://date/2026-04-02">2026-04-02</a> and a tail of words, '
         '<a href="https://example.org/x">an external link</a> at the end.</p>')


def link_editor():
    ed = RichEditor(link_dates=True)
    ed.resize(1200, 400)
    ed.show()
    settle()
    ed.load(LINKS, "html")
    settle()
    got = []
    ed.dateLinkActivated.connect(got.append)
    return ed, ed.text_edit, got


def at(edit, text, offset):
    cursor = QTextCursor(edit.document())
    cursor.setPosition(edit.toPlainText().index(text) + offset)
    return edit.cursorRect(cursor).center()


def drag(edit, a, b, steps=8):
    QTest.mousePress(edit.viewport(), Qt.LeftButton, Qt.NoModifier, a)
    for i in range(1, steps + 1):
        QTest.mouseMove(edit.viewport(), a + (b - a) * i / steps)
    QTest.mouseRelease(edit.viewport(), Qt.LeftButton, Qt.NoModifier, b)
    settle()


def click(edit, point, mods=Qt.NoModifier):
    QTest.mouseClick(edit.viewport(), Qt.LeftButton, mods, point)
    settle()


ed, edit, got = link_editor()
drag(edit, at(edit, "Words before", 2), at(edit, "2026-03-01", 4))
check(f"a drag-selection ending on a date link selects ({edit.textCursor().selectedText()!r}) and "
      f"does not navigate ({got})", not got and edit.textCursor().selectedText().startswith("rds before"))
ed.close()
ed, edit, got = link_editor()
drag(edit, at(edit, "2026-03-01", 4), at(edit, "tail of", 4))
check(f"a drag starting on a link selects text ({edit.textCursor().selectedText()[:24]!r}…) and does "
      f"not navigate", not got and edit.textCursor().selectedText().startswith("-03-01 and words"))
ed.close()
ed, edit, got = link_editor()
click(edit, at(edit, "2026-03-01", 4))
check(f"a plain click on a date link still navigates ({got})", got == ["2026-03-01"])
got.clear()
press_at = at(edit, "2026-04-02", 3)
QTest.mousePress(edit.viewport(), Qt.LeftButton, Qt.NoModifier, press_at)
QTest.mouseMove(edit.viewport(), press_at + QPoint(1, 0))
QTest.mouseRelease(edit.viewport(), Qt.LeftButton, Qt.NoModifier, press_at + QPoint(1, 0))
settle()
check(f"a click with a 1 px tremble (under the drag distance) still navigates ({got})",
      got == ["2026-04-02"])
got.clear()
drag(edit, at(edit, "Words before", 2), at(edit, "2026-03-01", 4))
click(edit, at(edit, "2026-03-01", 2))
check(f"a click on a link inside a selection still navigates ({got})", got == ["2026-03-01"])
got.clear()
click(edit, at(edit, "external link", 3), CTRL)
check(f"Ctrl+click on an external link still opens it ({opened_urls})",
      opened_urls == ["https://example.org/x"] and not got)
ed.close()
# AM-4: after a click on link A, a later drag never navigates to A.
for ending in ("plain text", "link B"):
    ed, edit, got = link_editor()
    click(edit, at(edit, "2026-03-01", 4))
    clicked = list(got)
    got.clear()
    end = at(edit, "tail of", 4) if ending == "plain text" else at(edit, "2026-04-02", 4)
    drag(edit, at(edit, "and words after", 2), end)
    check(f"AM-4: after a click on link A ({clicked}), a drag ending on {ending} navigates nowhere "
          f"({got})", clicked == ["2026-03-01"] and got == [] and edit.textCursor().hasSelection())
    ed.close()
# A typed date (not yet a link in the live document) behaves the same.
ed, edit, got = link_editor()
edit.moveCursor(QTextCursor.End)
QTest.keyClicks(edit, " Typed 2026-05-06 here.")
settle()
drag(edit, at(edit, "at the end", 1), at(edit, "2026-05-06", 4))
dragged = list(got)
click(edit, at(edit, "2026-05-06", 4))
check(f"a typed date: a drag ending on it does not navigate ({dragged}); a click does ({got})",
      dragged == [] and got == ["2026-05-06"])
ed.close()
# The same through the main window.
win.db.upsert_entry("2026-03-11", body_md=LINKS, body_format="html", body_text="links")
win._request_date("2026-03-11")
settle()
te = win.editor.text_edit
click(te, at(te, "2026-03-01", 4))
first = win.current_date
win._request_date("2026-03-11")
settle()
drag(te, at(te, "Words before", 2), at(te, "2026-04-02", 4))
check(f"main window: a click on a link goes to its date ({first}); a drag ending on another link "
      f"stays on the entry ({win.current_date})", first == "2026-03-01" and win.current_date == "2026-03-11")
win._request_date(DATE)
settle()

# ===================================================================== CB-4 / CB-5 / AM-1…AM-3
print("\n--- [CB-4 / CB-5] validation on the Hotkeys page ---")
captured = []
original_settings_exec = SettingsDialog.exec
SettingsDialog.exec = lambda self: captured.append(self) or 0


def open_hotkeys():
    A["settings_hotkeys"].trigger()
    settle()
    dlg = captured[-1]
    dlg.show()
    settle()
    return dlg, dlg.hotkeys_page


def row_state(page, command_id):
    row = page.rows[command_id]
    item = page.table.item(row, 0)
    colour = item.background().color() if item.background().style() != Qt.NoBrush else None
    return colour, page.table.item(row, COL_NOTE).text()


def stored_hotkeys():
    raw = win.db.get_setting(commands.HOTKEYS_SETTING, None)
    return None if raw is None else json.loads(raw)


dlg, page = open_hotkeys()
check("the Hotkeys page is an editor: a key editor per configurable command, on the Hotkeys page",
      set(page.editors) == {c.id for c in commands.COMMANDS if c.assignable}
      and dlg._contents["hotkeys"].isAncestorOf(page))
check("nothing is wrong with the defaults, and Apply is off with nothing changed",
      not commands.validate(commands.assignments()) and not page.apply_button.isEnabled(),
      commands.validate(commands.assignments()))

# CB-5: the standard edit keys.
fixed = [c.id for c in commands.COMMANDS if not c.assignable]
check(f"CB-5: the six standard edit keys are shown, not editable, 'Set by the system' ({fixed})",
      fixed == ["undo", "redo", "cut", "copy", "paste", "select_all"]
      and all(page.table.cellWidget(page.rows[c], 1) is None
              and page.table.item(page.rows[c], 1).text() == commands.shortcut_text(c)
              and page.table.item(page.rows[c], 1).text()
              and page.table.item(page.rows[c], COL_NOTE).text() == SYSTEM_NOTE for c in fixed))
commands.apply(dict(commands.assignments(), undo=[QKeySequence("Ctrl+Shift+U")]))
check("CB-5: a key for Undo is ignored even when applied directly",
      commands.key_sequences("undo") == commands.default_keys("undo"))
commands.apply(commands.assignments())


def try_key(command_id, seq, via_keys=None):
    """Puts a key in a row — typed into its key editor when `via_keys` is
    given (real key events), else as the editor would set it — and returns
    the row's (colour, note) and the page's problems for it."""
    page.reload()
    if via_keys is not None:
        widget = page.editors[command_id]
        widget.clear()
        QTest.keyClick(widget, via_keys[0], via_keys[1])
        settle()
    else:
        page.set_key(command_id, seq)
    return row_state(page, command_id), [p for p in page.problems if p.command_id == command_id]


cases = [
    ("window/window duplicate", "quit", QKeySequence("Ctrl+S"), "also assigned to Save", True),
    ("editor/editor duplicate", "italic", QKeySequence("Ctrl+B"), "also assigned to Bold", True),
    ("window/editor duplicate", "save", QKeySequence("Ctrl+B"), "also assigned to Bold", True),
    ("reserved: Copy", "bold", QKeySequence.keyBindings(QKeySequence.Copy)[0], "Copy", True),
    ("reserved: move by word", "bold",
     QKeySequence.keyBindings(QKeySequence.MoveToNextWord)[0], "move by word", True),
    ("AM-2: Ctrl+Tab", "bold", QKeySequence("Ctrl+Tab"), "switching tabs", True),
    ("AM-2: Ctrl+Shift+Tab", "bold", QKeySequence("Ctrl+Shift+Tab"), "switching tabs", True),
    ("AM-2: Ctrl+Shift+Backtab", "bold", QKeySequence("Ctrl+Shift+Backtab"), "switching tabs", True),
    ("AM-2: Ctrl+PgUp", "bold", QKeySequence("Ctrl+PgUp"), "switching tabs", True),
    ("AM-2: Ctrl+PgDown", "bold", QKeySequence("Ctrl+PgDown"), "switching tabs", True),
    ("typing: a letter", "bold", QKeySequence("B"), "types or edits text", True),
    ("typing: Shift+letter", "bold", QKeySequence("Shift+B"), "types or edits text", True),
    ("typing: a digit", "bold", QKeySequence("7"), "types or edits text", True),
    ("typing: Tab", "bold", QKeySequence("Tab"), "types or edits text", True),
    ("typing: Return", "bold", QKeySequence("Return"), "types or edits text", True),
    ("typing: Left", "bold", QKeySequence("Left"), "types or edits text", True),
    ("Alt+F (File menu)", "bold", QKeySequence("Alt+F"), "opens the File menu", True),
    ("Alt+E (Edit menu)", "bold", QKeySequence("Alt+E"), "opens the Edit menu", True),
    ("Alt+V (View menu)", "bold", QKeySequence("Alt+V"), "opens the View menu", True),
    ("Alt+S (Settings menu)", "bold", QKeySequence("Alt+S"), "opens the Settings menu", True),
    ("Alt+H (Help menu)", "bold", QKeySequence("Alt+H"), "opens the Help menu", True),
    ("AM-1: Ctrl++ while superscript is Ctrl+Shift+=", "zoom_in", QKeySequence("Ctrl++"),
     "also assigned to Superscript", True),
    ("AM-1: Ctrl+Shift++", "zoom_in", QKeySequence("Ctrl+Shift++"), "also assigned to Superscript", True),
    ("Ctrl+= is subscript's", "zoom_in", QKeySequence("Ctrl+="), "also assigned to Subscript", True),
    ("AM-3: Ctrl+Alt+B (AltGr) is a warning", "bold", QKeySequence("Ctrl+Alt+B"), "AltGr", False),
]
for label, command_id, seq, reason, is_error in cases:
    (colour, note), found = try_key(command_id, seq)
    wanted_colour = ERROR_COLOUR if is_error else WARNING_COLOUR
    ok = (any(reason in p.reason and p.error == is_error for p in found) and reason in note
          and colour == wanted_colour and page.apply_button.isEnabled() == (not is_error)
          and reason in page.problems_label.text())
    check(f"CB-4 {label}: {seq.toString()} on {command_id} → {note!r}, "
          f"{'Apply off' if not page.apply_button.isEnabled() else 'Apply on'}", ok,
          (found, colour.name() if colour else None, page.apply_button.isEnabled()))
for form in ("Ctrl++", "Ctrl+Shift++"):
    found = [p for p in commands.validate(dict(commands.assignments(), zoom_in=[QKeySequence(form)]))
             if p.command_id == "zoom_in"]
    check(f"AM-1 validate(): {form} as given (not rewritten) is occupied by Superscript's Ctrl+Shift+= "
          f"({[p.reason for p in found]})", any("Superscript" in p.reason and p.error for p in found))
check("a duplicate marks both rows", "also assigned to Italic" in
      (page.reload() or page.set_key("italic", QKeySequence("Ctrl+B")) or row_state(page, "bold")[1]))
for label, seq in (("F5", "F5"), ("Shift+F5", "Shift+F5"), ("Alt+G", "Alt+G"), ("Ctrl+Shift+P", "Ctrl+Shift+P")):
    (_colour, note), found = try_key("bold", QKeySequence(seq))
    check(f"CB-4 accepted: {label} on Bold (no problem, Apply on)", not found and page.apply_button.isEnabled(),
          found)
import dataclasses  # noqa: E402
found = [p for p in commands.validate(dict(commands.assignments(),
                                           find=commands.default_keys("find")))
         if p.command_id == "find"]
check(f"AM-7: Find's own standard bindings ({[k.toString() for k in commands.default_keys('find')]}) "
      f"pass the per-key rules ({[p.reason for p in found]})", not found)
original_align = commands.BY_ID["align_left"]
commands.BY_ID["align_left"] = dataclasses.replace(original_align, key="Alt+F")
try:
    found = [p for p in commands.validate(dict(commands.assignments(), align_left=[QKeySequence("Alt+F")]))
             if p.command_id == "align_left"]
finally:
    commands.BY_ID["align_left"] = original_align
check(f"AM-7: a default given as text is checked like any key (a default of Alt+F would be "
      f"rejected: {[p.reason for p in found]})", any("File menu" in p.reason for p in found))
bare = [(c.id, form.toString()) for c in commands.COMMANDS for form in commands.registered_sequences(c.id)
        if not form[0].keyboardModifiers() & (CTRL | ALT | Qt.MetaModifier)
        and form[0].key() < Qt.Key_Escape]
check(f"AM-6: no command is registered under a bare typing key, alias forms included ({bare})", not bare)
check("AM-7: every default key passes every rule on this platform",
      not commands.validate({c.id: commands.default_keys(c.id) for c in commands.COMMANDS if c.assignable}))
for label, seq in (("a multi-key sequence", QKeySequence("Ctrl+K, Ctrl+B")),
                   ("a modifier alone", QKeySequence(QKeySequence(Qt.CTRL | Qt.Key_Control))),
                   ("an empty unknown key", QKeySequence.fromString("Ctrl+Foo"))):
    found = [p for p in commands.validate(dict(commands.assignments(), bold=[seq])) if p.command_id == "bold"]
    check(f"CB-4 validate(): {label} is rejected ({[p.reason for p in found]})",
          found and all(p.error for p in found)
          and any("several keys" in p.reason or "registered" in p.reason for p in found))
# Real key events into a key editor: the =/+ forms are kept as Shift+= (4B-D6).
(_colour, note), found = try_key("zoom_in", None, via_keys=(Qt.Key_Plus, CTRL | SHIFT))
check(f"keys typed into Zoom In's editor (Ctrl+Shift+Key_Plus) read {page.editors['zoom_in'].keySequence().toString()!r} "
      f"and clash with Superscript ({note!r})",
      page.pending["zoom_in"] == [QKeySequence("Ctrl+Shift+=")] and "Superscript" in note)
# Nothing is stored while invalid; the refused Apply stores nothing.
page.reload()
page.set_key("bold", QKeySequence("Ctrl+S"))
before = stored_hotkeys()
refused = page.apply()
check("CB-4: with a row invalid, Apply is disabled, refuses, and stores nothing",
      not page.apply_button.isEnabled() and refused is False and stored_hotkeys() == before
      and commands.key_sequences("bold") == commands.default_keys("bold"))
try:
    commands.save(win.db, dict(commands.assignments(), bold=[QKeySequence("Ctrl+S")]))
    saved_anyway = True
except ValueError:
    saved_anyway = False
check("CB-4: commands.save refuses an invalid set too", not saved_anyway and stored_hotkeys() == before)
# AM-1: freed when superscript moves; then Ctrl++ zooms in.
page.reload()
page.set_key("superscript", QKeySequence("Ctrl+Shift+P"))
page.set_key("zoom_in", QKeySequence("Ctrl++"))
check(f"AM-1: with superscript moved to Ctrl+Shift+P, Ctrl++ is free for Zoom In "
      f"({[p.reason for p in page.problems]})", not page.problems and page.apply_button.isEnabled())
check("CB-4: a valid set applies", page.apply() is True)
show_daily()
edit = win.editor.text_edit
for form, k, mods in SUPER_FORMS:
    before_zoom = edit.zoom_percent()
    key_in(edit, k, mods)
    check(f"AM-1: {form} now zooms in ({before_zoom}% → {edit.zoom_percent()}%)",
          edit.zoom_percent() > before_zoom)
win.editor.text_edit.set_zoom_steps(0)
select_first_word(win.editor, "superword moved")
key_in(edit, Qt.Key_P, CTRL | SHIFT)
check("AM-1: ...and Ctrl+Shift+P is superscript", vertical(win.editor) == QTextCharFormat.AlignSuperScript)
page.restore_all_defaults()
page.apply()
check("back to the defaults: nothing stored", stored_hotkeys() == {})
dlg.close()

# ===================================================================== CB-2
print("\n--- [CB-2] a new key applies at once, everywhere; the old one stops ---")
preview = RichEditor(read_only=True)
preview.load("<p>A version preview with words to find.</p>", "html")
preview.show()
settle()
dlg, page = open_hotkeys()
emitted_changes.clear()
QTest.keyClick(page.editors["bold"], Qt.Key_B, CTRL | SHIFT)        # typed into the key editor
page.set_key("save", QKeySequence("Ctrl+Shift+S"))
page.set_key("find", QKeySequence("Ctrl+Shift+F"))
page.set_key("zoom_in", QKeySequence("F7"))
check("the page holds the typed key", page.pending["bold"] == [QKeySequence("Ctrl+Shift+B")],
      page.pending["bold"])
page.apply()
check(f"Apply emits the change signal once ({len(emitted_changes)})", len(emitted_changes) == 1)
dlg.close()
file_menu = next(a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "File")
check("the menu entry shows the new key (File → Save: Ctrl+Shift+S)",
      A["save"] in file_menu.actions() and A["save"].shortcut() == QKeySequence("Ctrl+Shift+S")
      and A["save"].toolTip().endswith(f"({native('Ctrl+Shift+S')})"))
check("Edit → Find has Ctrl+Shift+F", A["find"].shortcut() == QKeySequence("Ctrl+Shift+F"))
for name, editor, show in editors():
    show()
    select_first_word(editor, f"boldword in {name}")
    edit = editor.text_edit
    key_in(edit, Qt.Key_B, CTRL)
    old = edit.textCursor().charFormat().fontWeight() >= 600
    key_in(edit, Qt.Key_B, CTRL | SHIFT)
    new = edit.textCursor().charFormat().fontWeight() >= 600
    check(f"{name}: Ctrl+Shift+B bolds, Ctrl+B no longer does (old {old}, new {new})", not old and new)
    check(f"{name}: the Bold tooltip reads {editor.bold_btn.toolTip()!r}",
          editor.bold_btn.toolTip() == f"Bold ({native('Ctrl+Shift+B')})")
    zoom_tips = [a.toolTip() for a, c in getattr(editor, "_zoom_buttons", []) if c == "zoom_in"]
    if not editor.compact:
        check(f"{name}: the zoom-in button's tooltip shows its new key {zoom_tips}",
              zoom_tips == [f"Zoom in ({native('F7')})"])
show_daily()
saves = []
A["save"].triggered.connect(lambda *_: saves.append(1))
key_in(win.editor.text_edit, Qt.Key_S, CTRL)
key_in(win.editor.text_edit, Qt.Key_S, CTRL | SHIFT)
check(f"Ctrl+S no longer saves; Ctrl+Shift+S does ({saves})", saves == [1])
key_in(preview.text_edit, Qt.Key_F, CTRL)
old_find = preview.find_bar.isVisible()
key_in(preview.text_edit, Qt.Key_F, CTRL | SHIFT)
check(f"a read-only preview: Ctrl+F no longer opens Find, Ctrl+Shift+F does ({old_find}, "
      f"{preview.find_bar.isVisible()})", not old_find and preview.find_bar.isVisible())
preview.close()
shown = []
original_shortcuts_exec = help_dialogs.KeyboardShortcutsDialog.exec
help_dialogs.KeyboardShortcutsDialog.exec = lambda self: shown.append(self) or 0
A["keyboard_shortcuts"].trigger()
help_dialogs.KeyboardShortcutsDialog.exec = original_shortcuts_exec
table = shown[0].table
listed = {table.item(r, 0).text(): table.item(r, 1).text() for r in range(table.rowCount())}
check(f"Help → Keyboard Shortcuts lists the new keys (Bold {listed.get('Bold')}, Save {listed.get('Save')}, "
      f"Zoom In {listed.get('Zoom In')})",
      listed.get("Bold") == native("Ctrl+Shift+B") and listed.get("Save") == native("Ctrl+Shift+S")
      and listed.get("Zoom In") == native("F7"))
win.editor.text_edit.setFocus()
QTest.keyClicks(win.editor.text_edit, " dirty")
win.set_autosave(False)
settle()
check(f"the Save texts name the new key: {win.unsaved_label.text()!r}",
      native("Ctrl+Shift+S") in win.unsaved_label.text() and "Ctrl+S " not in win.unsaved_label.text()
      and f"press {native('Ctrl+Shift+S')}" in win.autosave_action.toolTip())
dlg, page = open_hotkeys()
check("...and so does the Settings autosave tooltip",
      f"press {native('Ctrl+Shift+S')}" in dlg.autosave_check.toolTip())
page.set_key("save", QKeySequence())
page.apply()
check(f"with no Save key the texts say File → Save ({win.unsaved_label.text()!r})",
      "File → Save" in win.unsaved_label.text() and "File → Save" in win.autosave_action.toolTip()
      and "File → Save" in dlg.autosave_check.toolTip() and A["save"].shortcuts() == [])
page.set_key("save", QKeySequence("Ctrl+Shift+S"))
page.apply()
dlg.close()
win._save_active_workspace()
win.set_autosave(True)
settle()

# ===================================================================== CB-7
print("\n--- [CB-7] shown in the native form, stored in the portable form ---")


class Translator(QTranslator):
    """Native key names as a German system shows them, so native and
    portable text differ on every platform."""
    NAMES = {"Ctrl": "Strg", "Shift": "Umschalt", "Alt": "Alt", "+": "+"}

    def translate(self, context, source, disambiguation=None, n=-1):
        if context == "QShortcut" and source in self.NAMES:
            return self.NAMES[source]
        return ""

    def isEmpty(self):
        return False


translator = Translator()
app.installTranslator(translator)
dlg, page = open_hotkeys()
page.set_key("italic", QKeySequence("Ctrl+Shift+I"))
page.apply()
stored = stored_hotkeys()
check(f"stored in portable form: {stored}",
      stored.get("italic") == ["Ctrl+Shift+I"] and stored.get("bold") == ["Ctrl+Shift+B"])
check(f"shown in native form: tooltip {win.editor.italic_btn.toolTip()!r}, key editor "
      f"{page.editors['italic'].keySequence().toString(QKeySequence.NativeText)!r}",
      win.editor.italic_btn.toolTip() == "Italic (Strg+Umschalt+I)"
      and commands.shortcut_text("italic") == "Strg+Umschalt+I")
dlg.close()
app.removeTranslator(translator)
settle()

# ===================================================================== CB-3 / Q3
print("\n--- [CB-3] persistence, restart, and a restore ---")
check(f"only the changes are stored ({sorted(stored)})",
      set(stored) == {"bold", "save", "find", "zoom_in", "italic"})
win.close()
settle()
win = new_window()
A = win.command_actions
win._request_date(DATE)
settle()
select_first_word(win.editor, "restartword stays")
key_in(win.editor.text_edit, Qt.Key_B, CTRL | SHIFT)
check("after a restart Ctrl+Shift+B still bolds",
      win.editor.text_edit.textCursor().charFormat().fontWeight() >= 600)
check("...and File → Save still has Ctrl+Shift+S", A["save"].shortcut() == QKeySequence("Ctrl+Shift+S"))


def restore(path):
    original_open = mw.QFileDialog.getOpenFileName
    original_confirm = backup_dialog.confirm_restore
    mw.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    backup_dialog.confirm_restore = lambda *a, **k: (True, False)
    try:
        win._import_backup()
    finally:
        mw.QFileDialog.getOpenFileName = original_open
        backup_dialog.confirm_restore = original_confirm
    settle(50)


with_keys = backup.create_backup("manual").zip_path        # holds bold, save, find, zoom_in, italic
dlg, page = open_hotkeys()
page.restore_all_defaults()
page.set_key("bold", QKeySequence("Ctrl+Alt+K"))
page.apply()
dlg.close()
emitted_changes.clear()
restore(with_keys)
select_first_word(win.editor, "restoredword one")
key_in(win.editor.text_edit, Qt.Key_B, CTRL | SHIFT)
check(f"Q3: restoring a backup with hotkeys applies its keys at once (signal {len(emitted_changes)}x; "
      f"Ctrl+Shift+B bolds; tooltip {win.editor.bold_btn.toolTip()!r})",
      emitted_changes and win.editor.text_edit.textCursor().charFormat().fontWeight() >= 600
      and win.editor.bold_btn.toolTip() == f"Bold ({native('Ctrl+Shift+B')})"
      and A["save"].shortcut() == QKeySequence("Ctrl+Shift+S"))
# An older backup, from before hotkeys existed: no hotkeys setting at all.
win.db._conn.execute("DELETE FROM settings WHERE key=?", (commands.HOTKEYS_SETTING,))
win.db._conn.commit()
older = backup.create_backup("manual").zip_path          # no hotkeys setting at all
commands.load(win.db)
dlg, page = open_hotkeys()
page.set_key("bold", QKeySequence("Ctrl+Shift+B"))
page.apply()
dlg.close()
emitted_changes.clear()
restore(older)
select_first_word(win.editor, "olderword two")
key_in(win.editor.text_edit, Qt.Key_B, CTRL | SHIFT)
shifted = win.editor.text_edit.textCursor().charFormat().fontWeight() >= 600
key_in(win.editor.text_edit, Qt.Key_B, CTRL)
check(f"CB-3/Q3: restoring an older backup without hotkeys brings back the defaults, live "
      f"(signal {len(emitted_changes)}x; Ctrl+Shift+B bolds: {shifted}; Ctrl+B bolds)",
      emitted_changes and not shifted
      and win.editor.text_edit.textCursor().charFormat().fontWeight() >= 600
      and win.db.get_setting(commands.HOTKEYS_SETTING, None) is None
      and A["save"].shortcut() == QKeySequence("Ctrl+S"))

# ===================================================================== CB-6
print("\n--- [CB-6] Restore Defaults, unknown commands, keys invalid now ---")
dlg, page = open_hotkeys()
page.set_key("bold", QKeySequence("Ctrl+Shift+B"))
page.set_key("italic", QKeySequence("Ctrl+Shift+I"))
page.apply()
page.set_default("bold")
check("a row's Default is only proposed until Apply",
      page.editors["bold"].keySequence() == commands.default_keys("bold")[0]
      and commands.key_sequences("bold") == [QKeySequence("Ctrl+Shift+B")] and page.apply_button.isEnabled())
page.apply()
check(f"...Apply makes it current ({stored_hotkeys()})",
      commands.key_sequences("bold") == commands.default_keys("bold") and stored_hotkeys() == {"italic": ["Ctrl+Shift+I"]})
page.set_key("italic", QKeySequence("Ctrl+B"))
page.set_default("bold")
check("a Default that would clash is validated like any edit (Apply off)", not page.apply_button.isEnabled())
page.restore_all_defaults()
check("Restore All Defaults puts every row back, pending until Apply",
      all(page.pending[c] == commands.default_keys(c) for c in page.pending)
      and commands.key_sequences("italic") == [QKeySequence("Ctrl+Shift+I")])
page.apply()
check("...and Apply stores nothing but the defaults", stored_hotkeys() == {}
      and commands.key_sequences("italic") == commands.default_keys("italic"))
dlg.close()
win.db.set_setting(commands.HOTKEYS_SETTING, json.dumps({
    "no_such_command": ["Ctrl+Shift+Q"], "bold": ["Ctrl+Shift+B"],
    "italic": ["Ctrl+Shift+="],          # superscript's default: invalid now
    "underline": ["Ctrl+Foo"],           # not a key
    "strikethrough": ["Ctrl+K, Ctrl+S"],  # several keys
    "undo": ["Ctrl+Shift+U"]}))          # a fixed command
commands.load(win.db)
check("at load: the known valid key applies, the unknown command and the fixed one are ignored",
      commands.key_sequences("bold") == [QKeySequence("Ctrl+Shift+B")]
      and commands.key_sequences("undo") == commands.default_keys("undo")
      and "no_such_command" not in commands.assignments())
check(f"...the invalid stored keys are ignored and the defaults used ({commands.load_notes})",
      commands.key_sequences("italic") == commands.default_keys("italic")
      and commands.key_sequences("underline") == commands.default_keys("underline")
      and commands.key_sequences("strikethrough") == [])
dlg, page = open_hotkeys()
notes = page.notes_label.text()
check(f"...and the Hotkeys page names each with its reason: {notes!r}",
      page.notes_label.isVisibleTo(dlg) and "Italic" in notes and "Superscript" in notes
      and "Underline" in notes and "Ctrl+Foo" in notes and "Strikethrough" in notes
      and "several keys" in notes)
page.set_key("align_left", QKeySequence("Ctrl+Shift+Y"))
page.apply()
check(f"the next Apply drops the unknown command and the ignored keys ({stored_hotkeys()})",
      stored_hotkeys() == {"bold": ["Ctrl+Shift+B"], "align_left": ["Ctrl+Shift+Y"]})
page.restore_all_defaults()
page.apply()
dlg.close()
SettingsDialog.exec = original_settings_exec

# ===================================================================== CB-10
print("\n--- [CB-10] bug 44: both tables at 9/13/24 pt, Light and Dark, styled ---")
screens = {"800x600 (the default offscreen screen)": None,
           "2560x1440": {"screens": [{"name": "Big", "x": 0, "y": 0, "width": 2560, "height": 1440}]}}
for label, config in screens.items():
    platform = "offscreen"
    if config is not None:
        (ROOT / "big_screen.json").write_text(json.dumps(config), encoding="utf-8")
        platform = "offscreen:configfile=big_screen.json"
    env = dict(os.environ, QT_QPA_PLATFORM=platform, PYTHONIOENCODING="utf-8")
    try:
        run = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--layout", label],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env=env, timeout=300, cwd=str(ROOT))
        out = run.stdout + run.stderr
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT {exc}"
    print(f"  [{label}]")
    for line in out.splitlines():
        if line.startswith(("  PASS", "  FAIL", "  (")):
            print("  " + line)
    result = next((json.loads(line[7:]) for line in out.splitlines() if line.startswith("RESULT:")), None)
    check(f"bug 44 on a {label} screen: every layout check passed",
          result is not None and not result["failures"], (result or {}).get("failures") or out[-800:])

sd = SettingsDialog(win.db, on_change=lambda: None, parent=win, page="general")
sd.show()
settle()
opened = sd.width()
cap = app.primaryScreen().availableGeometry().width() - 40
wanted = min(sd.hotkeys_page.preferred_width() + sd._chrome_width(), cap)
widths = []
for page_key in ("hotkeys", "appearance", "general", "hotkeys"):
    sd.show_page(page_key)
    settle()
    widths.append(sd.width())
check(f"AM-5: the Settings window opens wide enough for the Hotkeys table, capped by the screen "
      f"({opened} px, wants {wanted}) and keeps that width on every page switch ({widths})",
      opened >= wanted and set(widths) == {opened})
sd.close()

# ===================================================================== CB-11
print("\n--- [CB-11] no ambiguous shortcut ---")
amb = [m for m in qt_messages if "mbiguous" in m]
check("no ambiguous-shortcut warning in the whole run", not amb, amb)
win.close()
settle()
print(f"\nelapsed {time.monotonic() - STARTED:.1f}s")
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
