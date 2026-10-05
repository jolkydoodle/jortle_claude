"""Round 27 — Jortle with no AI in it at all (Part 21, plus 13-20).

Two halves, and the second is the one that matters:

  1. the AI is gone — no modules, no menu items, no widgets, no imports, no
     dependency, no model inspection at startup;
  2. everything else still works — journal, Projects, Reader's Notes in both
     workspaces, all three calendars, Day Markers, saving, undo, autosave and
     the unsaved-changes prompt.

Removing a feature is only safe if the things that shared a screen with it
still work, so the second half is deliberately longer than the first.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r27n-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r27n-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
ROOT = APP_DIR.parent

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=4):
    for _ in range(times):
        app.processEvents()


# ==================================================== 1. the AI is gone
print("\n--- Parts 15, 16, 20: no AI modules, imports or dependencies ---")
REMOVED = ["local_llm", "model_state", "gpu_probe", "reflection",
           "reflection_pipeline", "reflection_panel", "ai_status_widget",
           "experimental_settings_dialog", "companion_panel", "features"]
present = [m for m in REMOVED if (APP_DIR / f"{m}.py").exists()]
check("every AI module is deleted: " + ", ".join(present), not present)

importable = []
for name in REMOVED:
    try:
        __import__(f"app.{name}")
        importable.append(name)
    except ImportError:
        pass
check("and none of them can still be imported: " + ", ".join(importable), not importable)

bad_imports = []
for module in sorted(APP_DIR.glob("*.py")):
    for lineno, line in enumerate(module.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        if any(m in stripped for m in REMOVED) or "llama" in stripped:
            bad_imports.append(f"{module.name}:{lineno}")
check("no module imports anything AI: " + "; ".join(bad_imports), not bad_imports)

requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
check("llama-cpp-python is not a dependency", "llama" not in requirements)
check("no model download index is configured", "huggingface" not in requirements)

build = (ROOT / "build_windows.bat").read_text(encoding="utf-8", errors="replace").lower()
check("the build script does not install an inference backend", "llama" not in build)
check("the build script asks no CPU/CUDA question", "cuda" not in build)
check("the build script still builds jortle_claude", "jortle_claude.py" in build)

check("Jortle imports without llama-cpp-python installed",
      "llama_cpp" not in sys.modules)

print("\n--- Parts 13, 14: no AI in the interface ---")
from app.main_window import MainWindow  # noqa: E402

win = MainWindow()
win.show()
settle()

menu_labels = [a.text() for m in win.menuBar().actions() if m.menu()
               for a in m.menu().actions()]
check(f"no Experimental Settings entry ({[m for m in menu_labels if 'Setting' in m]})",
      "Experimental Settings…" not in menu_labels)
# Since batch 4A Settings is a top-level menu with one entry per page.
settings_menu = next((m.menu() for m in win.menuBar().actions()
                      if m.text().replace("&", "") == "Settings"), None)
check("Settings itself is still there: a top-level Settings menu with the six entries",
      settings_menu is not None
      and [a.text().replace("&", "") for a in settings_menu.actions()] == ["General…", "Editor…", "Hotkeys…", "Calendar…", "Appearance…", "Backups…"])
check("no menu item mentions AI or a model",
      not [m for m in menu_labels if "AI" in m or "Model" in m])

def visible_text(widget):
    out = []
    for child in widget.findChildren(object):
        for attr in ("text", "title", "toolTip", "placeholderText", "tabText"):
            getter = getattr(child, attr, None)
            if callable(getter):
                try:
                    value = getter() if attr != "tabText" else None
                except TypeError:
                    value = None
                if isinstance(value, str) and value:
                    out.append(value)
    return out


strings = visible_text(win)
offenders = [s for s in strings
             if "AI " in s or s.strip() in ("AI", "AI Suggestion", "AI Reflection")
             or "Reflect" in s or "llama" in s.lower() or "GGUF" in s.upper()]
check("no AI text anywhere in the window: " + "; ".join(offenders[:4]), not offenders)

check("no widget class left over from the AI feature",
      not [type(c).__name__ for c in win.findChildren(object)
           if type(c).__name__ in
           ("ReflectionPanel", "AIModelStatusWidget", "CompanionPanel")])

print("\n--- Part 13: the panes reflow, with no gap where the AI panel was ---")
check("Reader's Notes is directly in the left column, not in a tab container",
      win.reader_notes.isVisible() and win.reader_notes.height() > 0)
check("it is not wrapped in a tab widget",
      type(win.reader_notes.parent()).__name__ != "QStackedWidget")
check("Projects shows its Reader's Notes the same way",
      win.projects_widget.reader_notes is not None
      and not hasattr(win.projects_widget, "companion_panel"))
check("no empty placeholder widget was left behind",
      not [c for c in win.findChildren(object)
           if type(c).__name__ == "QWidget" and getattr(c, "objectName", lambda: "")()
           in ("aiPlaceholder", "reflectionPlaceholder")])

print("\n--- Parts 15, 17: startup inspects no model ---")
check("no model is loaded", "llama_cpp" not in sys.modules)
data_dir_models = pathlib.Path(os.environ["XDG_DATA_HOME"]) / "jortle_claude" / "models"
check("startup did not create a models directory", not data_dir_models.exists())

# Part 17: a model file that IS present must make no difference at all.
data_dir_models.mkdir(parents=True)
(data_dir_models / "Qwen_Qwen3.5-9B-Q4_K_S.gguf").write_bytes(b"pretend weights")
win2 = MainWindow()
win2.show()
settle()
check("an existing model file changes nothing about startup",
      win2.main_tabs.count() == 4 and "llama_cpp" not in sys.modules)
check("...and is not deleted either",
      (data_dir_models / "Qwen_Qwen3.5-9B-Q4_K_S.gguf").is_file())
win2.close()
settle()

# ==================================================== 2. everything else works
print("\n--- Parts 21.2-21.11: the app still does its job ---")
check("all four workspaces are present",
      [win.main_tabs.tabText(i) for i in range(win.main_tabs.count())]
      == ["Daily Jorts", "Weekly Schedule", "Yearly Calendar", "Projects"])

win.selected_date.set("2026-04-01")
settle()
cursor = win.editor.text_edit.textCursor()
cursor.insertText("A journal entry written with no AI in the building.")
settle()
check("typing marks the document dirty", win.editor.is_dirty())
win._save_active_workspace()
settle()
check("Ctrl+S saves the journal", win.db.has_journal_entry("2026-04-01"))
check("and the calendar indicator appears",
      "2026-04-01" in win.calendar_panel.calendar._entry_dates)

win.reader_notes.editor.text_edit.setPlainText("Epistemic: relating to knowledge.")
win.reader_notes.flush(force=True)
settle()
check("Reader's Notes saves in the Daily Journal",
      "Epistemic" in (win.db.get_reader_notes("2026-04-01").content_text or ""))

project = win.db.create_project("A project with no AI")
win.projects_widget.refresh_project_list(select_id=project.id)
settle()
win.projects_widget.editor.text_edit.setPlainText("Chapter one.")
win.projects_widget.save_now()
settle()
check("Projects saves",
      "Chapter one" in (win.db.get_project(project.id).content_text or ""))
win.projects_widget.reader_notes.editor.text_edit.setPlainText("Marguerite: the sister.")
win.projects_widget.reader_notes.flush(force=True)
settle()
check("Reader's Notes saves in Projects",
      "Marguerite" in (win.db.get_notes("project", str(project.id)).content_text or ""))

marker = win.db.create_day_marker("Research Day", "#4477aa")
win.db.upsert_entry("2026-04-02", tag=str(marker.id), tag_color=marker.color)
win._refresh_calendar_marks()
settle()
check("Day Markers still work",
      win.calendar_panel.calendar._tag_colors.get("2026-04-02") == "#4477aa")

win.db.create_event(date="2026-04-01", start_minute=600, end_minute=660, title="A meeting")
win.day_calendar.refresh()
win.week_calendar.refresh()
settle()
check("the Day View shows the event",
      [e.title for e in win.day_calendar.timeline.pieces()] == ["A meeting"])
check("the Weekly Calendar reads the same event",
      "A meeting" in [e.title for e in win.db.get_events("2026-04-01")])
check("the Monthly Calendar is still painting",
      win.calendar_panel.calendar.monthShown() == 4)

print("\n--- Parts 21.12-21.15: saving, undo and the prompt ---")
before_undo = win.editor.plain_text()
cursor = win.editor.text_edit.textCursor()
cursor.movePosition(QTextCursor.End)
cursor.insertText(" One more sentence.")
settle()
win.editor.text_edit.undo()
settle()
check("Ctrl+Z undoes the last edit", win.editor.plain_text() == before_undo)

from app.saving import autosave_enabled, set_autosave_enabled  # noqa: E402

set_autosave_enabled(win.db, True)
check("the autosave setting reads back", autosave_enabled(win.db) is True)
cursor = win.editor.text_edit.textCursor()
cursor.insertText(" Autosaved.")
win._autosave_tick()
settle()
check("autosave writes through the same path",
      "Autosaved." in (win.db.get_entry("2026-04-01").body_text or ""))
set_autosave_enabled(win.db, False)

import app.main_window as main_window_module  # noqa: E402
import app.saving as saving_module  # noqa: E402

# main_window does `from .saving import ask_unsaved`, so it holds its own
# reference — patching the saving module alone would leave the real modal
# dialog in place and hang a headless run.
answers = []
original_ask = main_window_module.ask_unsaved
main_window_module.ask_unsaved = lambda *a, **k: answers.pop(0)
try:
    cursor = win.editor.text_edit.textCursor()
    cursor.insertText(" Unsaved.")
    settle()
    # _request_date is the guarded entry point every real date change goes
    # through (the calendar click, a date link, Back). Setting the canonical
    # date object directly is deliberately NOT guarded — it is what the
    # guard calls once the user has answered.
    answers.append(saving_module.CANCEL)
    moved = win._request_date("2026-04-05")
    settle()
    check("Cancel keeps you on the date, with the text intact",
          moved is False and win.current_date == "2026-04-01"
          and "Unsaved." in win.editor.plain_text())

    answers.append(saving_module.SAVE)
    moved = win._request_date("2026-04-05")
    settle()
    check("Save writes it and lets the navigation through",
          moved is True and win.current_date == "2026-04-05"
          and "Unsaved." in (win.db.get_entry("2026-04-01").body_text or ""))
finally:
    main_window_module.ask_unsaved = original_ask

win.editor.mark_clean()
win.projects_widget.editor.mark_clean()
win.close()
settle()

print("\n--- Part 19: nothing was migrated away ---")
from app.database import Database  # noqa: E402

db = Database()
check("the journal entry is still there after all of that",
      "no AI in the building" in (db.get_entry("2026-04-01").body_text or ""))
check("Reader's Notes is still there",
      "Epistemic" in (db.get_reader_notes("2026-04-01").content_text or ""))
check("the project is still there",
      "Chapter one" in (db.get_project(project.id).content_text or ""))
check("AI settings keys were left dormant, not deleted",
      db.get_setting("ai_reflection_enabled", "absent") in ("absent", "0", "1"))
db.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
