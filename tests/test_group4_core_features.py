"""Group 4, batch 4A2 — View → Core Features (Master Spec §51.2, G4-D2).

One list of features (app/core_features.py) drives the submenu, the stored
visibility and the first-launch tooltip. Hiding a tab keeps its place;
hiding Reader's Notes hides both of its panes; hiding never changes data and
follows the existing rules for unsaved work. Criteria CF-1…CF-11 with
amendments 4A2/AM-1…AM-4 (JORTLE_IMPLEMENTATION_HANDOFF.md, "4A2 plan —
agreed 2026-10-07").

Everything is driven in-process (FP-15). The first-launch cases of 4A2/AM-1
each run in a child process of this file (`--case <name>`), because the
data-folder decision is made once per process; each child has its own
isolated root and a timeout.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
CASE = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--case" else None
ROOT = isolation.isolate(prefix=f"jortle-g4-cf-{CASE}-" if CASE else "jortle-g4-cf-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QMessageBox, QPushButton,  # noqa: E402
                               QWidget)

app = QApplication.instance() or QApplication([])

from app import backup, backup_reminder, core_features, data_migration, security  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.core_features import FEATURES, Feature  # noqa: E402
from app.core_features_tip import CoreFeaturesTip  # noqa: E402
from app.database import Database  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.saving import CANCEL, SAVE, set_autosave_enabled  # noqa: E402
from app.theme import PRESETS, build_stylesheet  # noqa: E402

DATA = get_data_dir()
assert str(DATA).startswith(str(ROOT)), "data dir not isolated"

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


TODAY = {"date": "2026-12-01"}
backup_reminder.today = lambda: TODAY["date"]
ANSWERS = {"unsaved": SAVE, "question": QMessageBox.Yes}
asked = []


def fake_ask(_parent, what):
    asked.append(what)
    return ANSWERS["unsaved"]


mw.ask_unsaved = fake_ask
pw_module.ask_unsaved = fake_ask
pw_module.ask_name = lambda *a, **k: ("Feature project", True)
questions = []
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda _p, title, text, *a, **k:
                                    questions.append((title, text)) or ANSWERS["question"])


def tips():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, CoreFeaturesTip) and w.isVisible()]


def new_window():
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.activateWindow()
    settle(300)
    return win


def launch(win):
    """One launch's startup step (start_background_tasks runs it 500 ms
    after the window shows)."""
    win._startup_backup_tasks()
    settle(50)


def close_window(win):
    win.close()
    settle()


# ------------------------------------------------------------ AM-1: children
def child(case):
    """Prints `RESULT:{json}` for one first-launch case (4A2/AM-1)."""
    out = {}
    security.update_config(DATA, storage_choice="unencrypted", automatic_backups="off")
    if case in ("existing", "legacy"):
        db = Database()
        db.upsert_entry("2026-01-05", body_md="<p>Written before</p>", body_format="html",
                        body_text="Written before")
        db.close()
        if case == "legacy":
            legacy = DATA.parent / "Jortle"
            shutil.move(str(DATA), str(legacy))
        data_migration.reset_for_tests()
        get_data_dir()
        out["case"] = data_migration.migration_result().case
        out["first_launch"] = core_features.first_launch()
        security.update_config(get_data_dir(), storage_choice="unencrypted", automatic_backups="off")
        win = new_window()
        launch(win)
        out["tip_shown"] = bool(tips())
        close_window(win)
    elif case in ("escape_then_write", "close_then_write"):
        # 4A2/AM-6: dismissed (not acknowledged) at the first launch, then an
        # entry is written, the app quits and starts again.
        out["case_first"] = data_migration.migration_result().case
        win = new_window()
        launch(win)
        out["tip_shown_first"] = bool(tips())
        for tip in tips():
            if case == "escape_then_write":
                QTest.keyClick(tip, Qt.Key_Escape)
            else:
                tip.close()
        settle()
        win.db.upsert_entry("2026-01-06", body_md="<p>Written after the tip</p>", body_format="html",
                            body_text="Written after the tip")
        close_window(win)
        data_migration.reset_for_tests()
        get_data_dir()
        out["case_next"] = data_migration.migration_result().case
        win = new_window()
        launch(win)
        shown = tips()
        out["tip_shown_next"] = bool(shown)
        if shown:
            QTest.mouseClick(shown[0].button, Qt.LeftButton)
            settle()
        close_window(win)
        win = new_window()
        launch(win)
        out["tip_shown_after_cool"] = bool(tips())
        close_window(win)
    elif case == "twoscreen":
        # 4A2/AM-8: run with an offscreen two-screen configuration (set by
        # the parent): A, the primary, 1200×900 at (0, 0); B, 800×800, to
        # its LEFT at (-800, 0).
        screens = app.screens()
        out["screens"] = [s.geometry().getRect() for s in screens]
        a, b = screens[0].availableGeometry(), screens[1].availableGeometry()
        win = new_window()

        def placed(label):
            shown = tips()
            if not shown:
                out[label] = None
                return
            t = shown[0]
            bar = win.menuBar()
            action = next(x for x in bar.actions() if x.text().replace("&", "") == "View")
            anchor = bar.mapToGlobal(bar.actionGeometry(action).bottomLeft())
            out[label] = {"tip": [t.x(), t.y(), t.width(), t.height()], "anchor": [anchor.x(), anchor.y()]}

        # Wholly on B.
        win.showNormal()
        win.resize(700, 600)
        win.move(b.x() + 20, b.y() + 30)
        settle(50)
        launch(win)
        placed("on_b_first")
        win.move(b.x() + 40, b.y() + 60)
        settle(50)
        placed("on_b_moved")
        # Straddling: the left part (with the View anchor) on B, most of the window on A.
        win.resize(1400, 700)
        win.move(b.right() - 300, b.y() + 30)
        settle(50)
        # Once more after Qt has moved the window to the first screen (its
        # screen changes after the move), so "the anchor's screen" and "the
        # window's screen" really differ when the tooltip is re-placed.
        win.resize(win.width() + 2, win.height())
        settle(50)
        placed("straddle")
        out["straddle_window_screen"] = win.screen().geometry().getRect() if win.screen() else None
        for t in tips():
            t.close()
        settle()
        # The startup dialogs, at 24 pt, with the main window wholly on B.
        from app.backup_dialog import PausedBackupsDialog, StorageChoiceDialog
        from app.theme import PRESETS as _P, build_stylesheet as _bs
        font = app.font()
        font.setPointSize(24)
        app.setFont(font)
        app.setStyleSheet(_bs(_P["Light"], 24))
        # A realistic parent (4A2/AM-9): a window no larger than the screen it
        # is on. At 24 pt the main window's minimum size (about 1433×925) is
        # larger than this 800×800 screen, so a dialog parented to it is
        # placed against an off-screen window; a 700×600 window wholly on B
        # stands in for it, and the check below confirms it fits.
        holder = QWidget()
        holder.resize(700, 600)
        holder.move(b.x() + 50, b.y() + 50)
        holder.show()
        settle(50)
        hf = holder.frameGeometry()
        out["holder"] = [hf.x(), hf.y(), hf.width(), hf.height()]
        for name, make in (("paused", lambda: PausedBackupsDialog(DATA, holder)),
                           ("storage", lambda: StorageChoiceDialog(holder))):
            d = make()
            d.show()
            settle(50)
            frame = d.frameGeometry()       # the real frame rectangle (4A2/AM-9)
            out[name] = [frame.x(), frame.y(), frame.width(), frame.height()]
            d.close()
        out["b"] = [b.x(), b.y(), b.width(), b.height()]
        out["a"] = [a.x(), a.y(), a.width(), a.height()]
        close_window(win)
    elif case == "restore":
        db = Database()
        db.upsert_entry("2026-01-05", body_md="<p>Written before</p>", body_format="html",
                        body_text="Written before")
        db.close()
        summary = backup.create_backup("manual")
        keep = ROOT / "elsewhere"
        keep.mkdir()
        zip_copy = keep / summary.zip_path.name
        shutil.copy2(summary.zip_path, zip_copy)
        shutil.rmtree(DATA)
        shutil.rmtree(backup.backup_dir(), ignore_errors=True)
        data_migration.reset_for_tests()
        get_data_dir()
        out["case_before"] = data_migration.migration_result().case
        security.update_config(get_data_dir(), storage_choice="unencrypted", automatic_backups="off")
        win = new_window()
        launch(win)
        out["tip_shown_first"] = bool(tips())
        for tip in tips():
            QTest.keyClick(tip, Qt.Key_Escape)
        settle()
        close_window(win)
        backup.restore_backup(zip_copy)
        data_migration.reset_for_tests()
        get_data_dir()
        out["case_after"] = data_migration.migration_result().case
        out["first_launch_after"] = core_features.first_launch()
        win = new_window()
        launch(win)
        out["tip_shown_after"] = bool(tips())
        close_window(win)
    print("RESULT:" + json.dumps(out), flush=True)
    sys.exit(0)


if CASE:
    child(CASE)

print("IMPORTED app FROM", mw.__file__)


def run_child(case, platform="offscreen", cwd=None):
    env = dict(os.environ, QT_QPA_PLATFORM=platform, PYTHONIOENCODING="utf-8")
    try:
        run = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), "--case", case],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env=env, timeout=240, cwd=cwd)
        out = run.stdout + run.stderr
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT {exc}"
    result = next((json.loads(l[7:]) for l in out.splitlines() if l.startswith("RESULT:")), None)
    return result, out


def data_snapshot(win):
    conn = win.db._conn
    out = {}
    for table in ("entries", "entry_revisions", "projects", "project_versions", "reader_notes_scoped",
                  "recovery_checkpoints", "calendar_events", "todos", "day_markers", "period_titles"):
        try:
            out[table] = [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
        except Exception as exc:
            out[table] = repr(exc)
    return out


def settings_snapshot(win):
    return dict(win.db._conn.execute("SELECT key, value FROM settings").fetchall())


def tab_visible(win, widget):
    return win.main_tabs.isTabVisible(win.main_tabs.indexOf(widget))


security.update_config(DATA, storage_choice="unencrypted", automatic_backups="off")

# ================================================================ CF-1 (one list)
print("\n--- [CF-1] one list: a test-only entry needs no other code ---")
original_build_ui = MainWindow._build_ui


def build_ui_with_test_tab(self):
    original_build_ui(self)
    self._test_tab = QWidget()
    self._add_primary_tab("testtab", self._test_tab, "Test Tab")


MainWindow._build_ui = build_ui_with_test_tab
FEATURES.append(Feature("testfeature", "Test Feature", tab_key="testtab"))
win0 = new_window()
labels = [a.text() for a in win0.core_features_menu.actions()]
check(f"the submenu lists the test entry too ({labels})", labels[-1] == "Test Feature")
test_action = win0.command_actions.get("feature_testfeature")
if test_action is not None:
    test_action.trigger()
    settle()
check("hiding it hides its tab and stores its visibility, with no other code",
      test_action is not None and not tab_visible(win0, win0._test_tab)
      and win0.db.get_setting(core_features.setting_name("testfeature")) == "0")
tip = CoreFeaturesTip(win0, FEATURES)
check("the tooltip names it too", "Test Feature" in tip.label.text())
tip.close()
if test_action is not None:
    test_action.trigger()
    settle()
close_window(win0)
FEATURES.pop()
MainWindow._build_ui = original_build_ui

# ================================================================ CF-2
print("\n--- [CF-2] the menu ---")
win = new_window()
view = next(a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "View")
core_menu = win.core_features_menu
labels = [a.text() for a in core_menu.actions()]
check(f"View → Core Features lists exactly Yearly Calendar, Projects, Reader's Notes ({labels})",
      labels == ["Yearly Calendar", "Projects", "Reader's Notes"]
      and core_menu.menuAction() in view.actions())
check("all checked by default, everything visible",
      all(a.isChecked() for a in core_menu.actions())
      and all(win.main_tabs.isTabVisible(i) for i in range(win.main_tabs.count()))
      and not win.reader_notes.isHidden() and not win.projects_widget.reader_notes.isHidden())
everything = [a.text() for m in win.menuBar().actions() if m.menu() for a in m.menu().actions()] + labels
check("Daily Jorts, the Weekly Schedule and the month calendar are never listed",
      not [t for t in labels if t in ("Daily Jorts", "Weekly Schedule", "Monthly Calendar", "Month")])

# ================================================================ CF-4 (tabs)
print("\n--- [CF-4] hiding a tab keeps its place ---")
order = win._current_tab_order()
check(f"(the default order {order})", order == ["daily", "week", "year", "projects"])
win.main_tabs.setCurrentWidget(win.year_calendar)
settle()
win.command_actions["feature_year"].trigger()
settle()
check("hiding the current tab (Yearly) brings Daily Jorts forward and hides only that tab",
      win.main_tabs.currentWidget() is win.daily_splitter and not tab_visible(win, win.year_calendar)
      and tab_visible(win, win.week_calendar) and tab_visible(win, win.projects_widget))
check("...it keeps its place in the tab bar and in the saved order",
      win._current_tab_order() == ["daily", "week", "year", "projects"])
# A real drag of the Projects tab to the front, with Yearly hidden.
bar = win.main_tabs.tabBar()
start = bar.tabRect(win.main_tabs.indexOf(win.projects_widget)).center()
end = bar.tabRect(win.main_tabs.indexOf(win.daily_splitter)).center() - QPoint(20, 0)
QTest.mousePress(bar, Qt.LeftButton, Qt.NoModifier, start)
for step in range(1, 11):
    QTest.mouseMove(bar, start + (end - start) * step / 10)
    settle(10)
QTest.mouseRelease(bar, Qt.LeftButton, Qt.NoModifier, end)
settle(100)
dragged = win._current_tab_order()
saved = win.db.get_setting(win.TAB_ORDER_SETTING)
print(f"  (after the drag: {dragged}; saved {saved})")
if dragged[0] != "projects":    # the offscreen drag did not take; move as a drag does
    bar.moveTab(win.main_tabs.indexOf(win.projects_widget), 0)
    settle()
    dragged = win._current_tab_order()
    saved = win.db.get_setting(win.TAB_ORDER_SETTING)
check(f"dragging a tab while Yearly is hidden keeps 'year' in its place ({dragged}; saved {saved})",
      dragged == ["projects", "daily", "week", "year"] and saved == "projects,daily,week,year")
win.command_actions["feature_year"].trigger()
settle()
check("showing Yearly again puts it back in its place, visible",
      tab_visible(win, win.year_calendar) and win._current_tab_order() == ["projects", "daily", "week", "year"])
win.command_actions["feature_projects"].trigger()
settle()
check("hiding Projects hides only its tab", not tab_visible(win, win.projects_widget)
      and tab_visible(win, win.year_calendar))

# ================================================================ CF-3 (persistence)
print("\n--- [CF-3] visibility survives a restart ---")
close_window(win)
win = new_window()
check("after a restart Projects is still hidden, in its place, its entry unchecked; the others shown",
      not tab_visible(win, win.projects_widget) and tab_visible(win, win.year_calendar)
      and not win.command_actions["feature_projects"].isChecked()
      and win.command_actions["feature_year"].isChecked()
      and win._current_tab_order() == ["projects", "daily", "week", "year"]
      and win.main_tabs.currentWidget() is not win.projects_widget)
win.command_actions["feature_projects"].trigger()
settle()

# ================================================================ CF-6 (routes)
print("\n--- [CF-6] routes into a hidden feature ---")
project = win.db.create_project("Recovered project")
win.db.save_project_content(project.id, "<p>Current text</p>", "html", "Current text")
win.projects_widget.refresh_project_list()
checkpoint = win.db.add_recovery_checkpoint("project", str(project.id), "<p>Recovered words</p>", "html",
                                             "Recovered words", title="Recovered project")
win.set_feature_visible("projects", False)
settle()
ANSWERS["question"] = QMessageBox.No
questions.clear()
before = data_snapshot(win)
result = win._restore_recovery_checkpoint(checkpoint.id)
check("Recovery restore with Projects hidden asks; No changes nothing",
      result is False and len(questions) == 1 and "Projects is hidden" in questions[0][1]
      and not tab_visible(win, win.projects_widget) and data_snapshot(win) == before)
ANSWERS["question"] = QMessageBox.Yes
result = win._restore_recovery_checkpoint(checkpoint.id)
settle()
stored = win.db.get_project(project.id)
check("...Yes shows Projects again and restores",
      tab_visible(win, win.projects_widget) and win.feature_visible("projects")
      and "Recovered words" in (stored.content_text or stored.content_md or ""))
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
win.set_feature_visible("projects", False)
win.set_feature_visible("reader_notes", False)
settle()
win.calendar_panel.calendar.setFocus()
settle()
check("Find and zoom act on the journal editor, never on a hidden one",
      win._command_editor() is win.editor)
win.reader_notes.editor.text_edit.setFocus()
settle()
check("even if a hidden notes editor is asked for the focus, Find and zoom never target it",
      win._command_editor() is win.editor)
win._on_date_link_activated("2026-02-02")
settle()
check("a date link still goes to Daily Jorts", win.current_date == "2026-02-02"
      and win.main_tabs.currentWidget() is win.daily_splitter)
win._go_back()
settle()
check("...and Back returns", win.current_date != "2026-02-02")
win.set_feature_visible("projects", True)
win.set_feature_visible("reader_notes", True)
settle()

# ================================================================ CF-5, AM-2, CF-11
print("\n--- [CF-5, AM-2] hiding Reader's Notes ---")
DATE = "2026-03-03"
win._request_date(DATE)
settle()
set_autosave_enabled(win.db, False)
win._sync_autosave_widgets()
daily_sizes = win.daily_splitter.sizes()
projects_state = bytes(win.projects_widget.splitter.saveState())
settings_before = settings_snapshot(win)
notes_before = data_snapshot(win)["reader_notes_scoped"]
win.set_feature_visible("reader_notes", False)
settle()
check("hiding unedited notes writes nothing",
      data_snapshot(win)["reader_notes_scoped"] == notes_before)
check("both panes are hidden", win.reader_notes.isHidden() and win.projects_widget.reader_notes.isHidden())
settings_after = settings_snapshot(win)
changed = {k for k in set(settings_before) | set(settings_after) if settings_before.get(k) != settings_after.get(k)}
check(f"pane widths, splitter state and settings unchanged but the visibility key ({changed})",
      win.daily_splitter.sizes() == daily_sizes
      and bytes(win.projects_widget.splitter.saveState()) == projects_state
      and changed == {core_features.setting_name("reader_notes")})
win.set_feature_visible("reader_notes", True)
settle()
notes_edit = win.reader_notes.editor.text_edit
notes_edit.setFocus()
QTest.keyClicks(notes_edit, "A note typed with autosave off")
settle()
win.set_feature_visible("reader_notes", False)
settle()
row = win.db.get_reader_notes(DATE)
check("edited notes are saved when hidden, even with autosave off (D2)",
      row is not None and "A note typed with autosave off" in (row.content_text or ""))
calls = []
original_flush = type(win.reader_notes).flush


def counting_flush(self, *a, **k):
    calls.append(k)
    return original_flush(self, *a, **k)


type(win.reader_notes).flush = counting_flush
te = win.editor.text_edit
te.setFocus()
QTest.keyClicks(te, "Journal words")
win._save_active_workspace()
type(win.reader_notes).flush = original_flush
check("Ctrl+S with Reader's Notes hidden saves the entry and skips the hidden notes",
      not [c for c in calls if c.get("force")] and "Journal words" in win.db.get_entry(DATE).body_text)

print("\n--- [CF-11] the month calendars still mark days with notes ---")
NOTE_DATE = "2026-03-17"
win.db.save_reader_notes(NOTE_DATE, "<p>A note</p>", "html", "A note")
win._refresh_calendar_marks()
win.week_calendar.refresh()
settle()
check("with Reader's Notes hidden, the Daily Jorts month calendar marks the day with notes",
      NOTE_DATE in win.calendar_panel.calendar._other_content_dates)
check("...and so does the Weekly Schedule's month navigator",
      NOTE_DATE in win.week_calendar.navigator.calendar._other_content_dates)
win.set_feature_visible("reader_notes", True)
settle()
check("showing Reader's Notes again shows the current date's notes",
      not win.reader_notes.isHidden() and "A note typed with autosave off" in win.reader_notes.editor.text_edit.toPlainText())

print("\n--- [AM-2] hiding Projects with unsaved work ---")
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
win.projects_widget.refresh_project_list(select_id=project.id)
settle()
before_project = win.db.get_project(project.id).content_text
pe = win.projects_widget.editor.text_edit
pe.setFocus()
QTest.keyClicks(pe, " plus unsaved words")
settle()
win.set_feature_visible("projects", False)
settle()
check("autosave off: hiding Projects writes nothing; the work stays in the hidden editor, '*' stays",
      win.db.get_project(project.id).content_text == before_project
      and "unsaved words" in pe.toPlainText() and win.isWindowModified()
      and win.main_tabs.currentWidget() is win.daily_splitter)
asked.clear()
ANSWERS["unsaved"] = CANCEL
win.close()
settle()
check("...closing asks Save / Discard / Cancel; Cancel keeps the window open",
      asked and win.isVisible())
ANSWERS["unsaved"] = SAVE
win.close()
settle()
reopened = Database()
check("...Save writes the project on the way out",
      "unsaved words" in (reopened.get_project(project.id).content_text or ""))
reopened.close()

win = new_window()
set_autosave_enabled(win.db, True)
win._sync_autosave_widgets()
win.set_feature_visible("projects", True)
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
win.projects_widget.refresh_project_list(select_id=project.id)
settle()
pe = win.projects_widget.editor.text_edit
pe.setFocus()
QTest.keyClicks(pe, " autosaved on hiding")
settle()
win.set_feature_visible("projects", False)
settle()
check("autosave on: hiding Projects saves the edited project",
      "autosaved on hiding" in (win.db.get_project(project.id).content_text or ""))
set_autosave_enabled(win.db, False)
win._sync_autosave_widgets()
win.set_feature_visible("projects", True)
settle()

# ================================================================ CF-7 (no data change)
print("\n--- [CF-7] hiding changes no data; the archive keeps everything ---")
from app.archive import export_archive  # noqa: E402
data_before = data_snapshot(win)
security_before = security.load_config(DATA)
shown_archive = ROOT / "archive-shown"
export_archive(win.db, shown_archive)
for f in FEATURES:
    win.set_feature_visible(f.key, False)
settle()
hidden_archive = ROOT / "archive-hidden"
export_archive(win.db, hidden_archive)
close_window(win)
win = new_window()
for f in FEATURES:
    win.set_feature_visible(f.key, True)
settle()
check("hiding and showing all features, with a restart between, changes no data table",
      data_snapshot(win) == data_before)
check("...and no security.json key", security.load_config(DATA) == security_before)
listing = lambda root: sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())  # noqa: E731
check("the archive exported with features hidden has the same files as with them shown",
      listing(shown_archive) == listing(hidden_archive) and len(listing(shown_archive)) > 0)
text_of = lambda root: " ".join(p.read_text(encoding="utf-8", errors="replace")  # noqa: E731
                                for p in root.rglob("*.html"))
check("...including the project and the notes",
      "Recovered project" in text_of(hidden_archive) and "A note" in text_of(hidden_archive))

# ================================================================ CF-8 (tooltip), AM-1
print("\n--- [CF-8] the first-launch tooltip ---")
check("(this process is a first launch: case C)",
      data_migration.migration_result().case == "C" and core_features.first_launch())
launch(win)
shown = tips()
check("shown at launch", len(shown) == 1)
tip = shown[0] if shown else None
if tip is not None:
    buttons = [b.text() for b in tip.findChildren(QPushButton)]
    bar = win.menuBar()
    view_action = next(a for a in bar.actions() if a.text().replace("&", "") == "View")
    anchor = bar.mapToGlobal(bar.actionGeometry(view_action).bottomLeft())
    check(f"one button, exactly 'Cool!' ({buttons}); anchored under 'View' ({tip.pos().toTuple()} vs "
          f"{anchor.toTuple()})",
          buttons == ["Cool!"] and abs(tip.y() - anchor.y()) <= 2 and abs(tip.x() - anchor.x()) <= 2)
    check("it names View → Core Features and every feature",
          "View → Core Features" in tip.label.text()
          and all(f.label in tip.label.text() for f in FEATURES))
    QTest.keyClick(tip, Qt.Key_Escape)
    settle()
check("Escape hides it without acknowledging",
      not tips() and not security.load_config(DATA).get(core_features.TIP_ACKNOWLEDGED))
launch(win)
check("...so the next launch shows it again", len(tips()) == 1)
for t in tips():
    t.close()
settle()
check("closing it does not acknowledge either",
      not security.load_config(DATA).get(core_features.TIP_ACKNOWLEDGED))
blocker = QDialog(win)
blocker.setModal(True)
blocker.open()
settle(50)
launch(win)
shown_with_modal = bool(tips())
blocker.reject()
settle()
check("never shown while a modal window is open", not shown_with_modal)
win._backup_timer.timeout.emit()
settle(50)
check("never from the hourly check", not tips())

# Not stacked on the storage question or the paused-backups message: they
# come first, and the tooltip appears only after they are closed.
seen_with = []


def watch_modal():
    w = QApplication.activeModalWidget()
    if w is not None and w.isVisible():
        seen_with.append((type(w).__name__, bool(tips())))
        w.reject()
    else:
        QTest.qWait(0)


from PySide6.QtCore import QTimer  # noqa: E402
security.update_config(DATA, storage_choice=None)
timer = QTimer()
timer.timeout.connect(watch_modal)
timer.start(10)
launch(win)
timer.stop()
check(f"the storage question first, with no tooltip on screen; the tooltip after it ({seen_with})",
      seen_with and seen_with[0] == ("StorageChoiceDialog", False) and len(tips()) == 1)
for t in tips():
    t.close()
security.update_config(DATA, storage_choice="encrypted")
seen_with.clear()
TODAY["date"] = "2026-12-02"
timer.start(10)
launch(win)
timer.stop()
check(f"the paused-backups message first, with no tooltip on screen; the tooltip after it ({seen_with})",
      seen_with and seen_with[0] == ("PausedBackupsDialog", False) and len(tips()) == 1)
for t in tips():
    t.close()
security.update_config(DATA, storage_choice="unencrypted")

print("\n--- [CF-8] its layout at 9, 13 and 24 pt, Light and Dark ---")
size_before_layout = app.font().pointSize()
for scheme_name in ("Light", "Dark"):
    for size in (9, 13, 24):
        font = app.font()
        font.setPointSize(size)
        app.setFont(font)
        app.setStyleSheet(build_stylesheet(PRESETS[scheme_name], size))
        settle()
        t = CoreFeaturesTip(win, FEATURES)
        bar = win.menuBar()
        t.show_under(bar, next(a for a in bar.actions() if a.text().replace("&", "") == "View"))
        settle(30)
        screen = app.primaryScreen().availableGeometry()
        label = t.label
        button = t.button
        ok = (screen.contains(t.frameGeometry())
              and label.heightForWidth(label.width()) <= label.height()
              and t.rect().contains(button.geometry())
              and QFontMetrics(button.font()).horizontalAdvance(button.text()) <= button.width()
              and t.rect().contains(t.close_button.geometry()) and t.close_button.isVisible())
        check(f"{scheme_name} {size}pt: inside the screen, text not clipped, 'Cool!' and × visible "
              f"({t.width()}×{t.height()} at {t.pos().toTuple()})", ok)
        t.close()
font = app.font()
font.setPointSize(size_before_layout)       # back to the size the run started with
app.setFont(font)
win._apply_settings()
settle()

print("\n--- [AM-7] the × button, the window it belongs to, minimize and move ---")
from PySide6.QtWidgets import QAbstractButton  # noqa: E402


def view_anchor(window):
    bar = window.menuBar()
    action = next(a for a in bar.actions() if a.text().replace("&", "") == "View")
    return bar.mapToGlobal(bar.actionGeometry(action).bottomLeft())


launch(win)
shown = tips()
check("(the tooltip is showing)", len(shown) == 1)
tip = shown[0] if shown else None
close_buttons = [b for b in tip.findChildren(QAbstractButton) if b.text() == "×"] if tip else []
check("it has a × close button", len(close_buttons) == 1, [b.text() for b in tip.findChildren(QAbstractButton)] if tip else None)
check("its parent is the main window; a frameless tool window that never takes the focus when shown",
      tip is not None and tip.parent() is win
      and bool(tip.windowFlags() & Qt.Tool) and bool(tip.windowFlags() & Qt.FramelessWindowHint)
      and tip.testAttribute(Qt.WA_ShowWithoutActivating))
if tip is not None:
    win.move(win.x() + 60, win.y() + 40)
    settle(50)
    anchor = view_anchor(win)
    check(f"moving the window: the tooltip follows, still under 'View' ({tip.pos().toTuple()} vs "
          f"{anchor.toTuple()})",
          tip.isVisible() and abs(tip.x() - anchor.x()) <= 2 and abs(tip.y() - anchor.y()) <= 2)
    # Resize alone (4A2/AM-8, the FP-9 gap): pushed away first, so only the
    # resize can bring it back under "View".
    tip.move(0, 0)
    win.resize(win.width() - 40, win.height() - 30)
    settle(50)
    anchor = view_anchor(win)
    check(f"resizing the window: the tooltip is re-placed under 'View' ({tip.pos().toTuple()} vs "
          f"{anchor.toTuple()})",
          tip.isVisible() and abs(tip.x() - anchor.x()) <= 2 and abs(tip.y() - anchor.y()) <= 2)
    win.showMinimized()
    settle(50)
    check("minimizing the window hides the tooltip", not tip.isVisible())
    win.showNormal()
    win.activateWindow()
    settle(100)
    check("restoring the window shows it again", tip.isVisible())
    if close_buttons:
        QTest.mouseClick(close_buttons[0], Qt.LeftButton)
        settle()
    cfg = security.load_config(DATA)
    check("× closes it without acknowledging; it stays due",
          not tips() and not cfg.get(core_features.TIP_ACKNOWLEDGED) and bool(cfg.get(core_features.TIP_DUE)))
    win.showMinimized()
    settle(50)
    win.showNormal()
    win.activateWindow()
    settle(100)
    check("...and minimizing and restoring the window does not bring a closed tooltip back", not tips())
launch(win)
check("...the next launch shows it again", len(tips()) == 1)
for t in tips():
    t.close()
settle()

print("\n--- [AM-8] closing the main window closes the tooltip ---")
win2 = new_window()
launch(win2)
check("(a second window shows the tooltip, still due)", len(tips()) == 1)
close_window(win2)
settle(50)
check("closing the main window closes the tooltip with it", not tips())

print("\n--- [AM-8] two screens: the tooltip and the startup dialogs ---")
(ROOT / "screens2.json").write_text(json.dumps({"screens": [
    {"name": "A", "x": 0, "y": 0, "width": 1200, "height": 900},
    {"name": "B", "x": -800, "y": 0, "width": 800, "height": 800}]}), encoding="utf-8")
result, out = run_child("twoscreen", platform="offscreen:configfile=screens2.json", cwd=str(ROOT))
if result is None:
    check("(the two-screen child ran)", False, out[-600:])
else:
    def inside(rect, screen):
        x, y, w, h = rect
        sx, sy, sw, sh = screen
        return sx <= x and sy <= y and x + w <= sx + sw and y + h <= sy + sh

    print(f"  (screens {result['screens']})")
    b, a = result["b"], result["a"]
    for label in ("on_b_first", "on_b_moved"):
        r = result.get(label)
        check(f"window wholly on the second screen ({label}): the tooltip under 'View', on that screen ({r})",
              r is not None and inside(r["tip"], b)
              and abs(r["tip"][0] - r["anchor"][0]) <= 2 and abs(r["tip"][1] - r["anchor"][1]) <= 2)
    r = result.get("straddle")
    expected_x = None if r is None else min(r["anchor"][0], b[0] + b[2] - r["tip"][2])
    check(f"window straddling both, 'View' on the second screen, most of it on the first "
          f"(window's screen {result.get('straddle_window_screen')}): the tooltip on the anchor's screen, "
          f"as near 'View' as it fits ({r}; expected x {expected_x})",
          r is not None and inside(r["tip"], b) and abs(r["tip"][0] - expected_x) <= 2
          and abs(r["tip"][1] - r["anchor"][1]) <= 2)
    check(f"(the dialogs' parent window fits the second screen: {result.get('holder')} in {b})",
          result.get("holder") is not None and inside(result["holder"], b))
    for name in ("paused", "storage"):
        check(f"the {name} dialog at 24 pt, its parent on the second (smaller) screen: its whole frame "
              f"fits that screen ({result.get(name)} in {b})",
              result.get(name) is not None and inside(result[name], b))

print("\n--- [CF-8] Cool! acknowledges for good ---")
summary = backup.create_backup("manual")         # an older backup, made before the acknowledgement
launch(win)
shown = tips()
check("(the tooltip shows before Cool! is clicked)", len(shown) == 1)
if shown:
    QTest.mouseClick(shown[0].button, Qt.LeftButton)
    settle()
check("Cool! records the acknowledgement in security.json, not the settings table",
      security.load_config(DATA).get(core_features.TIP_ACKNOWLEDGED) == TODAY["date"]
      and not [k for k in settings_snapshot(win) if "tip" in k])
launch(win)
check("...and it never shows again", not tips())
close_window(win)
backup.restore_backup(summary.zip_path)
win = new_window()
launch(win)
check("...not even after restoring a backup made before it was acknowledged",
      not tips() and security.load_config(DATA).get(core_features.TIP_ACKNOWLEDGED) == TODAY["date"])
close_window(win)

print("\n--- [AM-1] what counts as a first launch ---")
for case, want in (("existing", {"case": "A", "first_launch": False, "tip_shown": False}),
                   ("legacy", {"case": "B", "first_launch": False, "tip_shown": False})):
    result, out = run_child(case)
    check(f"{case} install: case {want['case']}, not a first launch, no tooltip ({result})",
          result == want, out[-500:])
result, out = run_child("restore")
check(f"a fresh install that restores a backup at once: the tooltip at its first launch, and still "
      f"due after the restore until Cool! (4A2/AM-6) ({result})",
      result == {"case_before": "C", "tip_shown_first": True, "case_after": "A",
                 "first_launch_after": False, "tip_shown_after": True}, out[-500:])
for case, how in (("escape_then_write", "Escape"), ("close_then_write", "closing it")):
    result, out = run_child(case)
    check(f"brand-new install, {how} at the first launch, an entry written, relaunched: the tooltip "
          f"shows again; after Cool! never (4A2/AM-6) ({result})",
          result == {"case_first": "C", "tip_shown_first": True, "case_next": "A",
                     "tip_shown_next": True, "tip_shown_after_cool": False}, out[-500:])

# The unlock window exists only for an encrypted journal, and an encrypted
# journal is never classified as a new installation, so the tooltip can never
# follow an unlock on a first launch.
enc_root = ROOT / "encrypted-probe"
enc_root.mkdir()
shutil.copy2(DATA / "journal.db", enc_root / "journal.db")
saved_session = (security.session.db_key, security.session.identity)
security.set_up_database_encryption(enc_root, "correct horse battery")
security.session.db_key, security.session.identity = saved_session
state = data_migration.classify_target(enc_root)
check(f"an encrypted journal (the only kind with an unlock window) is never classified as new "
      f"({state})", state not in ("absent", "scaffolding"))

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
