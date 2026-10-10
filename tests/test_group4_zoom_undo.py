"""Group 4, batch 4C2a — view zoom and scroll-past-end outside the document.

Criteria C2a-1…C2a-8 and the amendments 4C2/AM-2…AM-4
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4C2 plan — agreed 2026-10-10"):

- bug 42: a zoom change, by any route, adds no undo step, does not mark the
  document modified and leaves its formats alone (C2a-1);
- bug 38: neither does a resize, a UI-font, theme or writing-position change
  (C2a-2);
- bug 6: no Settings change resets any editor's zoom (C2a-4);
- each editor's zoom is remembered, across a restart and a restore (C2a-5);
- bug 48: sizes set while zoomed are stored as at 100%, the size box shows
  the real size, and saving never crashes (C2a-6);
- what is shown at a zoom is the 100% layout times the zoom (C2a-7);
- the scroll-past-end space is scrollbar range (C2a-8, 4C2/AM-3);
- the zoomed layout follows a change of the screen's DPI (4C2/AM-2).

FP-9: real key, wheel and menu events, the real Settings window's controls,
the database read after a restart and a restore. Nothing here sends input to
the desktop (FP-15): every event goes to a widget of this process, offscreen.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-zoom-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QTextCursor, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import backup as backup_module  # noqa: E402
from app import backup_dialog, commands  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.main_window import EDITOR_ZOOM_SETTINGS, MainWindow  # noqa: E402
from app.paths import get_attachments_dir, get_data_dir  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import SAVE  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

STARTED = time.monotonic()
print("IMPORTED app FROM", mw.__file__)
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


mw.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_unsaved = lambda *_a, **_k: SAVE
messages = []
QMessageBox.information = staticmethod(lambda *a, **k: messages.append(("info", a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: messages.append(("warning", a[1:3])) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: messages.append(("critical", a[1:3])) or QMessageBox.Ok)


def new_window():
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.activateWindow()
    settle(300)
    return win


def plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def click_menu(win, title, action):
    """A real click on the menu bar, then on the entry — the user's route."""
    win.activateWindow()
    settle()
    bar = win.menuBar()
    top = next(a for a in bar.actions() if plain(a.text()) == title)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.actionGeometry(top).center())
    settle(50)
    menu = top.menu()
    if not menu.isVisible():
        check(f"(the {title} menu opened for the click)", False)
        return
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
    settle(50)


def ctrl_wheel(edit, notches):
    """Ctrl+wheel through Qt's own event delivery (not OS input)."""
    viewport = edit.viewport()
    centre = QPointF(viewport.width() / 2, viewport.height() / 2)
    for _ in range(abs(notches)):
        event = QWheelEvent(centre, QPointF(viewport.mapToGlobal(centre.toPoint())),
                            QPoint(0, 0), QPoint(0, 120 if notches > 0 else -120),
                            Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(viewport, event)
    settle()


def settings_snapshot(win):
    return dict(win.db._conn.execute("SELECT key, value FROM settings").fetchall())


win = new_window()
PROJECT_ID = win.db.create_project("Zoom project").id
win.projects_widget.refresh_project_list(select_id=PROJECT_ID)
settle()


def show_daily(w):
    w.main_tabs.setCurrentWidget(w.daily_splitter)
    settle()


def show_projects(w):
    w.main_tabs.setCurrentWidget(w.projects_widget)
    if w.projects_widget.current_project_id != PROJECT_ID:
        w.projects_widget.refresh_project_list(select_id=PROJECT_ID)
    settle()


# (name, how to reach the editor, how to put it on screen)
EDITORS = (
    ("journal", lambda w: w.editor, show_daily),
    ("date Reader's Notes", lambda w: w.reader_notes.editor, show_daily),
    ("project", lambda w: w.projects_widget.editor, show_projects),
    ("project Reader's Notes", lambda w: w.projects_widget.reader_notes.editor, show_projects),
)


def type_two_steps(edit):
    """'first', a new paragraph, 'second': the newest undo step is 'second'."""
    edit.setFocus()
    edit.clear()
    edit.document().clearUndoRedoStacks()
    settle()
    QTest.keyClicks(edit, "first")
    QTest.keyClick(edit, Qt.Key_Return)
    QTest.keyClicks(edit, "second")
    settle()


def undo_by(route, w, edit):
    edit.setFocus()
    settle()
    if route == "Ctrl+Z":
        QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
    else:
        w._refresh_command_states()
        click_menu(w, "Edit", w.command_actions["undo"])
    settle()


def view_change_keeps_document(label, w, editor, change, undo_route):
    """Typing, then a view change, then one undo: the change added no undo
    step, did not mark the document modified, did not touch its formats or
    emit textChanged (autosave), and the undo removes the last typing."""
    edit = editor.text_edit
    type_two_steps(edit)
    doc = edit.document()
    steps, html, modified = doc.availableUndoSteps(), edit.toHtml(), doc.isModified()
    text_changes = []
    editor.textChanged.connect(lambda: text_changes.append(1))
    change()
    settle(50)
    after = (doc.availableUndoSteps(), edit.toHtml() == html, doc.isModified(), len(text_changes))
    check(f"{label}: no undo step, not marked modified, formats untouched, no textChanged",
          after == (steps, True, modified, 0), f"steps {steps}->{after[0]}, same html {after[1]}, "
                                               f"modified {modified}->{after[2]}, textChanged {after[3]}")
    undo_by(undo_route, w, edit)
    check(f"{label}: one {undo_route} undoes the last typing", edit.toPlainText() == "first\n",
          repr(edit.toPlainText()[-20:]))


# =========================================================== C2a-1 (bug 42)
print("\n--- [C2a-1] zoom changes by every route: no undo step, not modified (bug 42) ---")
A = win.command_actions
for name, get, show in EDITORS:
    show(win)
    editor = get(win)
    edit = editor.text_edit
    routes = []
    if hasattr(editor, "zoom_spin"):
        routes.append(("Zoom % box", lambda e=editor: e.zoom_spin.setValue(130)))
    routes += [
        ("Ctrl+wheel", lambda e=edit: ctrl_wheel(e, 2)),
        ("View → Zoom In", lambda: click_menu(win, "View", A["zoom_in"])),
        ("View → Zoom Out", lambda: click_menu(win, "View", A["zoom_out"])),
        ("Ctrl+−", lambda e=edit: QTest.keyClick(e, Qt.Key_Minus, Qt.ControlModifier)),
    ]
    for route, change in routes:
        for undo_route in ("Ctrl+Z", "Edit → Undo"):
            edit.reset_zoom()
            settle()
            before = edit.zoom_percent()
            view_change_keeps_document(f"{name}, {route}", win, editor, change, undo_route)
            check(f"{name}, {route}: the zoom really changed ({undo_route} run)",
                  edit.zoom_percent() != before, edit.zoom_percent())
    # Reset by its key and by the menu, from a zoomed state.
    for route, change in (("Ctrl+0", lambda e=edit: QTest.keyClick(e, Qt.Key_0, Qt.ControlModifier)),
                          ("View → Reset Zoom", lambda: click_menu(win, "View", A["zoom_reset"]))):
        edit.set_zoom_steps(3)
        settle()
        view_change_keeps_document(f"{name}, {route}", win, editor, change, "Ctrl+Z")
        check(f"{name}, {route}: back at 100%", edit.zoom_percent() == 100, edit.zoom_percent())
    edit.reset_zoom()
    edit.clear()
    edit.document().setModified(False)
settle()

# =========================================================== C2a-2 (bug 38)
print("\n--- [C2a-2] resize, UI font, theme and writing position: no undo step (bug 38) ---")


def settings_dialog(w, page="general"):
    dlg = SettingsDialog(w.db, on_change=w._apply_settings, parent=w, set_autosave=w.set_autosave,
                         open_backups=lambda: None,
                         set_work_hours=w.calendar_prefs.set_work_hours_enabled, page=page)
    dlg.show()
    settle()
    return dlg


dlg = settings_dialog(win)
changes = (
    ("a resize", lambda: (win.resize(1300, 850), settle(100), win.resize(1600, 1000))),
    ("a UI-font change", lambda: dlg.ui_size_spin.setValue(18)),
    ("a theme change", lambda: dlg.scheme_combo.setCurrentText("Dark")),
    ("a writing-position change", lambda: dlg.writing_position_combo.setCurrentIndex(1)),
)
for name, get, show in EDITORS:
    show(win)
    editor = get(win)
    for change_name, change in changes:
        for undo_route in ("Ctrl+Z", "Edit → Undo"):
            view_change_keeps_document(f"{name} (on screen), {change_name}", win, editor, change, undo_route)
            dlg.ui_size_spin.setValue(13)
            dlg.scheme_combo.setCurrentText("Light")
            dlg.writing_position_combo.setCurrentIndex(0)
            settle(50)
    editor.text_edit.clear()
    editor.text_edit.document().setModified(False)
    check(f"{name}: the root frame keeps the document's own margin (no padding in the document)",
          editor.text_edit.document().rootFrame().frameFormat().bottomMargin()
          == editor.text_edit.document().documentMargin())
dlg.close()
settle()

# =========================================================== C2a-4 (bug 6)
print("\n--- [C2a-4] no Settings change resets any editor's zoom (bug 6) ---")
show_daily(win)
LEVELS = {"journal": 130, "date Reader's Notes": 120, "project": 80, "project Reader's Notes": 140}
for name, get, show in EDITORS:
    show(win)
    edit = get(win).text_edit
    edit.setFocus()
    edit.clear()
    QTest.keyClicks(edit, f"words in the {name}")      # a document with content
    edit.set_zoom_steps((LEVELS[name] - 100) // 10)
    settle()
show_daily(win)


def zooms(w):
    return {name: get(w).text_edit.zoom_percent() for name, get, _show in EDITORS}


check("(setup) the four editors are at four different zoom levels", zooms(win) == LEVELS, zooms(win))
dlg = settings_dialog(win)
settings_changes = (
    ("the writing font", lambda: dlg.font_combo.setCurrentFont(QFont("Arial"))),
    ("the writing size", lambda: dlg.size_spin.setValue(15)),
    ("the UI font", lambda: dlg.ui_size_spin.setValue(16)),
    ("the theme", lambda: dlg.scheme_combo.setCurrentText("Dark")),
    ("autosave, through Settings", lambda: dlg.autosave_check.setChecked(not dlg.autosave_check.isChecked())),
    ("the writing position", lambda: dlg.writing_position_combo.setCurrentIndex(2)),
)
for change_name, change in settings_changes:
    change()
    settle(100)
    check(f"after a change of {change_name}, all four editors keep their zoom", zooms(win) == LEVELS, zooms(win))
page = dlg.hotkeys_page
page.set_key("bold", QKeySequence("Ctrl+Shift+B"))
check("(setup) the Hotkeys page can apply the new key", page.can_apply())
page.apply()
settle(100)
check("after a Hotkeys Apply, all four editors keep their zoom", zooms(win) == LEVELS, zooms(win))
page.restore_all_defaults()
page.apply()
dlg.font_combo.setCurrentFont(QFont("Georgia"))
dlg.size_spin.setValue(13)
dlg.ui_size_spin.setValue(13)
dlg.scheme_combo.setCurrentText("Light")
dlg.writing_position_combo.setCurrentIndex(0)
dlg.close()
settle(100)
for name, get, _show in EDITORS:
    get(win).text_edit.clear()
    get(win).text_edit.document().setModified(False)

# =========================================================== C2a-5 (persistence)
print("\n--- [C2a-5] each editor's zoom is remembered: restart, restore, invalid values ---")
check("each editor's level is stored under its own key",
      [win.db.get_setting(k, "") for k in EDITOR_ZOOM_SETTINGS] == ["130", "120", "80", "140"],
      [win.db.get_setting(k, "") for k in EDITOR_ZOOM_SETTINGS])
before = settings_snapshot(win)
ctrl_wheel(win.editor.text_edit, 1)
after = settings_snapshot(win)
changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
check("a zoom change writes only that editor's key", changed == {"editor_zoom_journal"}
      and after["editor_zoom_journal"] == "140", changed)
ctrl_wheel(win.editor.text_edit, -1)
before = settings_snapshot(win)
for date in ("2026-01-02", "2026-01-03", "2026-01-02"):
    win._request_date(date)
    settle()
show_projects(win)
show_daily(win)
check("viewing (dates, tabs) writes no setting", settings_snapshot(win) == before)

win.close()
settle()
win = new_window()
check("after a restart, each editor is at its own remembered level", zooms(win) == LEVELS, zooms(win))
restarted = settings_snapshot(win)
check("the restart wrote no zoom setting",
      {k: restarted.get(k) for k in EDITOR_ZOOM_SETTINGS} == {k: before.get(k) for k in EDITOR_ZOOM_SETTINGS},
      sorted(k for k in set(before) | set(restarted) if before.get(k) != restarted.get(k)))

# Invalid stored values show 100% and are not rewritten.
for key, bad in zip(EDITOR_ZOOM_SETTINGS, ("abc", "105", "9999", "")):
    win.db.set_setting(key, bad)
bad_snapshot = settings_snapshot(win)
win.close()
settle()
win = new_window()
check("invalid stored levels ('abc', '105', '9999', '') show 100%",
      set(zooms(win).values()) == {100}, zooms(win))
check("...and nothing is written back", all(win.db.get_setting(k, None) == bad_snapshot[k] for k in EDITOR_ZOOM_SETTINGS),
      [win.db.get_setting(k, None) for k in EDITOR_ZOOM_SETTINGS])

# Restore: the backup's levels, applied at once; an older backup without
# them gives 100%.
for key in EDITOR_ZOOM_SETTINGS:
    win.db._conn.execute("DELETE FROM settings WHERE key=?", (key,))
win.db._conn.commit()
win._back_up_now()
settle()
no_zoom_backup = sorted(pathlib.Path(backup_module.backup_dir()).glob("*.zip"),
                        key=lambda p: p.stat().st_mtime)[-1]
for name, get, _show in EDITORS:
    get(win).text_edit.set_zoom_steps((LEVELS[name] - 100) // 10)
settle()
time.sleep(1.1)                                 # a distinct backup file name
win._back_up_now()
settle()
zoom_backup = sorted(pathlib.Path(backup_module.backup_dir()).glob("*.zip"),
                     key=lambda p: p.stat().st_mtime)[-1]
check("(setup) two different backups", zoom_backup != no_zoom_backup, zoom_backup)
for _name, get, _show in EDITORS:
    get(win).text_edit.set_zoom_steps(-2)
settle()
backup_dialog.confirm_restore = lambda *a, **k: (True, False)


def restore(path):
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    messages.clear()
    win._import_backup()
    settle(300)
    check(f"the restore of {path.name} reported no error",
          not [m for m in messages if m[0] == "critical"], messages)


restore(zoom_backup)
check("a restore brings back the backup's zoom levels at once", zooms(win) == LEVELS, zooms(win))
restore(no_zoom_backup)
check("a restore of a backup without zoom levels brings back 100% at once",
      set(zooms(win).values()) == {100}, zooms(win))
check("...and writes no zoom setting",
      all(win.db.get_setting(k, None) is None for k in EDITOR_ZOOM_SETTINGS),
      [win.db.get_setting(k, None) for k in EDITOR_ZOOM_SETTINGS])

# =========================================================== C2a-6 (bug 48, D25)
print("\n--- [C2a-6] the size box and sizes set while zoomed (bug 48) ---")
show_daily(win)
DOC = ("<html><body style=\" font-family:'Georgia'; font-size:13pt;\">"
       "<p>alpha beta gamma</p><p>title line</p></body></html>")


def entry_html(date):
    row = win.db._conn.execute("SELECT body_md FROM entries WHERE date=?", (date,)).fetchone()
    return row[0] if row else None


def select(edit, start, end):
    cursor = QTextCursor(edit.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    edit.setTextCursor(cursor)
    settle()


def size_and_heading_at(date, zoom):
    win.db._conn.execute(
        "INSERT INTO entries(date, body_md, body_format, body_text, created_at, updated_at) "
        "VALUES(?, ?, 'html', 'alpha beta gamma title line', '2026-01-01T08:00:00', '2026-01-01T08:00:00')",
        (date, DOC))
    win.db._conn.commit()
    win.editor.text_edit.set_zoom_steps((zoom - 100) // 10)
    win._request_date(date)
    settle()
    edit = win.editor.text_edit
    edit.setFocus()
    select(edit, 2, 2)
    shown = win.editor.size_spin.value()
    select(edit, 6, 10)                         # "beta"
    win.editor.size_spin.setValue(20)
    select(edit, 19, 19)                        # in "title line"
    win.editor.heading_combo.setCurrentIndex(1)
    settle()
    # Ctrl+S reaches the window only while it is the active one; a restore
    # above leaves it inactive under offscreen Qt (as in test_group4_roundtrip).
    win.activateWindow()
    settle()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_S, Qt.ControlModifier)
    settle()
    win.editor.text_edit.reset_zoom()
    return shown, entry_html(date)


shown_100, at_100 = size_and_heading_at("2026-02-01", 100)
for zoom, date in ((130, "2026-02-02"), (70, "2026-02-03")):
    shown, stored = size_and_heading_at(date, zoom)
    check(f"at {zoom}% the size box shows the real size (13)", shown == 13, shown)
    check(f"at {zoom}% a size chosen in the box and Heading 1 store exactly what they store at 100%",
          stored is not None and stored == at_100, "stored differs from the 100% result")
check("(guard) at 100% the size box shows 13, and the chosen size and Heading 1 are stored",
      shown_100 == 13 and at_100 is not None and "font-size:20pt" in at_100 and "<h1" in at_100,
      (shown_100, (at_100 or "")[-300:]))

# Saving never crashes: the bug 48 sequences in child processes (a crash
# ends the process, so it can only be seen from outside).
CHILD = textwrap.dedent(f"""
    import os, sys
    sys.path.insert(0, {str(REPO)!r}); sys.path.insert(0, {str(REPO / 'tests')!r})
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import isolation; isolation.isolate("jortle-g4-zoom-child-")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QTextCursor
    app = QApplication.instance() or QApplication([])
    from app.rich_editor import RichEditor
    for round_ in range(20):
        ed = RichEditor(); ed.show(); te = ed.text_edit
        ed.load({DOC!r})
        te.set_zoom_steps(3)
        c = te.textCursor(); c.setPosition(2); te.setTextCursor(c); app.processEvents()
        c.setPosition(6); c.setPosition(10, QTextCursor.KeepAnchor); te.setTextCursor(c)
        ed.size_spin.setValue(20); app.processEvents()
        ed.save()
        c = QTextCursor(te.document()); c.setPosition(6); c.setPosition(10, QTextCursor.KeepAnchor)
        te.setTextCursor(c); ed.size_spin.setValue(24)
        ed.save()
        ed.close(); ed.deleteLater(); app.processEvents()
    print("SAVED ALL")
""")
child_file = pathlib.Path(tempfile.mkdtemp(dir=ROOT)) / "bug48_child.py"
child_file.write_text(CHILD, encoding="utf-8")
result = subprocess.run([sys.executable, str(child_file)], capture_output=True, text=True, timeout=300,
                        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
check("the bug 48 sequences (size box at 130%, then save; 20 rounds) never crash a child process",
      result.returncode == 0 and "SAVED ALL" in result.stdout,
      f"exit code {result.returncode}, {(result.stdout + result.stderr)[-300:]}")

# =========================================================== C2a-7 (what is shown)
print("\n--- [C2a-7] shown at the 100% layout times the zoom; grabs for review ---")
STRESS = ("<html><body style=\" font-family:'Georgia'; font-size:13pt;\">"
          "<p>body text</p><p><span style=\" font-size:8pt;\">small</span></p>"
          "<p><span style=\" font-size:20pt;\">large</span></p>"
          "<h1 style=\" margin-top:18px;\"><span style=\" font-size:20pt; font-weight:700;\">Heading</span></h1>"
          "<p style=\" margin-top:24px;\"><span style=\" font-size:72pt;\">72</span></p></body></html>")
win._request_date("2026-02-10")
settle()
win.editor.load(STRESS)
settle()
edit = win.editor.text_edit


def line_heights(count=5):
    settle()
    return [edit.document().findBlockByNumber(i).layout().lineAt(0).height() for i in range(count)]


base_heights = line_heights()
for zoom in (130, 70):
    edit.set_zoom_steps((zoom - 100) // 10)
    heights = line_heights()
    expected = [h * zoom / 100 for h in base_heights]
    check(f"at {zoom}% every line is the 100% line height times the zoom (within 1 px)",
          all(abs(a - b) <= 1.0 for a, b in zip(heights, expected)),
          f"{[round(h, 1) for h in heights]} vs {[round(h, 1) for h in expected]}")
# At 200% font-metric rounding differs by font and platform (up to several px
# for some fonts, the same under the per-character zoom before 4C2a), so the
# check is coarse: within 10% of the 100% height × 2 — enough to tell no zoom
# (×1) and zoom applied twice (×4) from the real thing (4C2/AM-15).
edit.set_zoom_steps(10)
heights = line_heights()
expected = [h * 2 for h in base_heights]
check("at 200% every line is within 10% of the 100% line height times 2",
      all(abs(a - b) <= 0.10 * b for a, b in zip(heights, expected)),
      f"{[round(h, 1) for h in heights]} vs {[round(h, 1) for h in expected]}")
edit.reset_zoom()
GRABS = pathlib.Path(os.environ.get("JORTLE_GRAB_DIR") or (ROOT / "grabs"))
GRABS.mkdir(parents=True, exist_ok=True)
dlg = settings_dialog(win)
dlg.ui_size_spin.setValue(24)
for scheme in ("Light", "Dark"):
    dlg.scheme_combo.setCurrentText(scheme)
    settle(100)
    edit.set_zoom_steps(3)
    settle(100)
    path = GRABS / f"zoom130_ui24_{scheme.lower()}.png"
    win.editor.grab().save(str(path))
    check(f"grab at 130%, UI font 24, {scheme} saved for review", path.exists() and path.stat().st_size > 0, path)
    edit.reset_zoom()
dlg.ui_size_spin.setValue(13)
dlg.scheme_combo.setCurrentText("Light")
dlg.close()
settle(100)

# 4C2/AM-4: an image wider than a narrow pane, at 130%: horizontal overflow
# measured (reported against the code before 4C2a, which zoomed text only).
wide = QImage(900, 120, QImage.Format_RGB32)
wide.fill(QColor("#4080c0"))
(get_attachments_dir() / "zoomtest").mkdir(parents=True, exist_ok=True)
wide.save(str(get_attachments_dir() / "zoomtest" / "wide.png"))
narrow = RichEditor()
narrow.resize(420, 400)
narrow.show()
narrow.load("<p>before</p><p><img src=\"zoomtest/wide.png\" /></p><p>after</p>")
settle(100)
for zoom in (100, 130):
    narrow.text_edit.set_zoom_steps((zoom - 100) // 10)
    settle(100)
    bar = narrow.text_edit.horizontalScrollBar()
    print(f"  AM-4 MEASURE  at {zoom}%: viewport width {narrow.text_edit.viewport().width()} px, "
          f"horizontal scroll range {bar.maximum()} px")
    narrow.grab().save(str(GRABS / f"wide_image_narrow_pane_{zoom}.png"))
narrow.close()
settle()

# =========================================================== C2a-8 (scroll-past-end)
print("\n--- [C2a-8] scroll-past-end as scrollbar range (4C2/AM-3) ---")
LONG = "<html><body>" + "".join(f"<p>line {i}</p>" for i in range(60)) + "</body></html>"
probe = RichEditor()
probe.resize(600, 420)
probe.show()
settle(50)
te = probe.text_edit


def to_end_and_scroll_max():
    cursor = te.textCursor()
    cursor.movePosition(QTextCursor.End)
    te.setTextCursor(cursor)
    bar = te.verticalScrollBar()
    bar.setValue(bar.maximum())
    settle()
    return te.cursorRect().center().y(), te.viewport().height(), te.cursorRect().height()


for position, fraction in (("free", 0.5), ("top", 1 / 3)):
    te.set_writing_position(position)
    probe.load(LONG)
    settle(50)
    caret_y, height, line = to_end_and_scroll_max()
    check(f"writing position {position}: the last line can be scrolled to {fraction:.2f} of the viewport",
          abs(caret_y - height * fraction) <= line, f"caret y {caret_y}, viewport {height}")
te.set_writing_position("free")
probe.load(LONG)
settle(50)
caret_y, height, line = to_end_and_scroll_max()
bar = te.verticalScrollBar()
value = bar.value()
QTest.keyClick(te, Qt.Key_Return)
QTest.keyClicks(te, "typed at the end")
settle()
check("Enter and typing at the end, scrolled past the end, do not move the view",
      bar.value() == value, f"{value} -> {bar.value()}")
check("...and the caret stays visible", te.viewport().rect().contains(te.cursorRect()), te.cursorRect())
html, _plain = probe.save()
check("the stored HTML has no root-frame wrapper", "-qt-table-type" not in html)

# AM-3: the range extension's signal count is bounded (the re-entry guard).
emitted = []
bar.rangeChanged.connect(lambda lo, hi: emitted.append((lo, hi)))
probe.resize(600, 520)
settle(50)
check("a resize changes the scroll range a bounded number of times (re-entry guard)",
      1 <= len(emitted) <= 4, emitted)
check("...and the range ends at the document's range plus the scroll-past-end space",
      bar.maximum() == te._qt_scroll_max + te.bottom_padding() and te.bottom_padding() > 0,
      (bar.maximum(), te._qt_scroll_max, te.bottom_padding()))
for name, content in (("an empty document", ""), ("a one-line document", "<p>one line</p>")):
    probe.load(content)
    settle(50)
    check(f"{name}: the scroll range is the scroll-past-end space",
          bar.maximum() == te.bottom_padding() > 0, (bar.maximum(), te.bottom_padding()))
    te.setFocus()
    QTest.keyClick(te, Qt.Key_End, Qt.ControlModifier)
    QTest.keyClicks(te, "x")
    settle()
    check(f"{name}: the caret is visible after typing at the end",
          te.viewport().rect().contains(te.cursorRect()), te.cursorRect())
probe.close()
settle()

# =========================================================== 4C2/AM-2 (screen DPI)
print("\n--- [4C2/AM-2] the zoomed layout follows a change of the screen's DPI (simulated) ---")
probe = RichEditor()
probe.resize(500, 300)
probe.show()
probe.load("<p>some text</p>")
settle(50)
te = probe.text_edit
check("at 100% the layout draws on the viewport itself",
      te.document().documentLayout().paintDevice() is te.viewport())
te.set_zoom_steps(3)
settle()
device = te.document().documentLayout().paintDevice()
base_dpi = te.logicalDpiY()
check("at 130% the layout draws on a device of the screen DPI times 1.3",
      device is not te.viewport() and abs(device.logicalDpiY() - base_dpi * 1.3) <= 1,
      (device.logicalDpiY(), base_dpi))
te._screen_dpi = lambda: (144.0, 144.0)         # the window moved to a 144-DPI screen
te.viewport().repaint()
settle(50)
device = te.document().documentLayout().paintDevice()
check("after the screen's DPI changes to 144, the zoomed layout is made for 144 × 1.3",
      abs(device.logicalDpiY() - 144 * 1.3) <= 1, device.logicalDpiY())
del te._screen_dpi
te.viewport().repaint()
settle(50)
check("...and back again when the DPI returns",
      abs(te.document().documentLayout().paintDevice().logicalDpiY() - base_dpi * 1.3) <= 1)
probe.close()
settle()

# =========================================================== loads and the unsaved flag
# Qt's setHtml() emits modificationChanged(True) and clears the flag without
# emitting False; the padding change after each load used to emit it by
# accident. A load must leave the unsaved flag and the title's '*' clear.
print("\n--- a load leaves the unsaved indicator clear (with autosave off) ---")
win.set_autosave(False)
OTHER_PROJECT = win.db.create_project("Second project").id
for name, show, load_other in (
        ("Daily Jorts (date change)", show_daily, lambda: win._request_date("2026-03-04")),
        ("Projects (project change)", show_projects,
         lambda: win.projects_widget.refresh_project_list(select_id=OTHER_PROJECT))):
    show(win)
    settle()
    check(f"(setup) {name}: nothing unsaved before the load",
          not win.unsaved_label.isVisible() and not win.isWindowModified())
    load_other()
    settle()
    check(f"{name}: after the load, no unsaved flag and no '*'",
          not win.unsaved_label.isVisible() and not win.isWindowModified(),
          (win.unsaved_label.isVisible(), win.isWindowModified()))

# =========================================================== end
win.editor.mark_clean()
win.reader_notes.editor.mark_clean()
win.close()
settle()
print(f"\nrun time {time.monotonic() - STARTED:.0f} s")
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
