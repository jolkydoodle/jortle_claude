"""Round 26 — undo, Ctrl+S, optional autosave, Save/Discard/Cancel (Part 32).

The spec's 27-step script. The Save/Discard/Cancel prompt is modal, so it is
stubbed with a scripted answer — what is under test is that the prompt is
ASKED at the right moments and that each answer does the right thing, not
that QMessageBox works.

The undo checks matter most here: the bug being fixed was that saving
mutated the live document (to strip zoom and padding for export), which
pushed entries onto the undo stack and set the modified flag. Ctrl+Z after a
save undid the save's own housekeeping instead of the user's edit.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r26s-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r26s-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import main_window as main_window_module  # noqa: E402
from app import projects_widget as projects_module  # noqa: E402
from app import saving  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import CANCEL, DISCARD, SAVE, autosave_enabled, set_autosave_enabled  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=4):
    for _ in range(times):
        app.processEvents()


# The scripted answer to the modal prompt, plus a record of every time it
# was asked — "was the user asked at all?" is half of what Part 26 requires.
asked = []
answer = {"value": CANCEL}


def fake_ask(parent, what):
    asked.append(what)
    return answer["value"]


main_window_module.ask_unsaved = fake_ask
projects_module.ask_unsaved = fake_ask

win = MainWindow()
win.show()
settle()


def type_text(text, editor=None):
    editor = editor or win.editor
    cursor = editor.text_edit.textCursor()
    cursor.insertText(text)
    settle()


# ================================================ steps 1-10: undo and save
print("\n--- Steps 1-10: undo survives saving ---")
set_autosave_enabled(win.db, False)
win.selected_date.set("2026-03-02")
settle()
check("a freshly opened entry is clean", not win.editor.is_dirty())
check("...with nothing to undo", not win.editor.text_edit.document().isUndoAvailable())

type_text("first sentence.")
check("typing makes it dirty", win.editor.is_dirty())
win.editor.text_edit.undo()
settle()
check(f"Ctrl+Z undoes the edit ({win.editor.text_edit.toPlainText()!r})",
      win.editor.text_edit.toPlainText().strip() == "")
win.editor.text_edit.redo()
settle()
check("and redo puts it back", "first sentence." in win.editor.text_edit.toPlainText())

# A separate edit block, which is what a pause in real typing produces —
# Qt merges consecutive programmatic insertions into one undo step, so
# without this the test would be checking a single command, not two.
cursor = win.editor.text_edit.textCursor()
cursor.beginEditBlock()
cursor.insertText(" second sentence.")
cursor.endEditBlock()
settle()
win._save_active_workspace()
settle()
check("Ctrl+S saves", win.db.has_journal_entry("2026-03-02"))
check("the saved text matches the editor",
      win.db.get_entry("2026-03-02").body_text.strip()
      == win.editor.text_edit.toPlainText().strip())
check("the document is clean after saving", not win.editor.is_dirty())

win.editor.text_edit.undo()
settle()
text_after_undo = win.editor.text_edit.toPlainText()
check(f"ONE Ctrl+Z after a save undoes the user's edit, not the save "
      f"({text_after_undo!r})",
      "first sentence." in text_after_undo and "second sentence." not in text_after_undo)

print("\n--- undo covers the operations the spec lists ---")
editor = RichEditor(link_dates=True)
editor.load("<p>base text</p>", "html")
cursor = editor.text_edit.textCursor()
cursor.movePosition(QTextCursor.End)
cursor.insertText(" typed")
editor.text_edit.undo()
check("typing", editor.text_edit.toPlainText().strip() == "base text")

cursor = editor.text_edit.textCursor()
cursor.select(QTextCursor.Document)
cursor.removeSelectedText()
editor.text_edit.undo()
check("deletion", editor.text_edit.toPlainText().strip() == "base text")

cursor = editor.text_edit.textCursor()
cursor.select(QTextCursor.Document)
editor.text_edit.setTextCursor(cursor)
editor.text_edit.insertHtml("<p>pasted rich text</p>")
editor.text_edit.undo()
check("paste", "base text" in editor.text_edit.toPlainText())

cursor = editor.text_edit.textCursor()
cursor.select(QTextCursor.Document)
editor.text_edit.setTextCursor(cursor)
editor._toggle_bold()
bold_after = editor.text_edit.textCursor().charFormat().fontWeight()
editor.text_edit.undo()
check("a formatting change",
      editor.text_edit.textCursor().charFormat().fontWeight() != bold_after
      or not editor.text_edit.document().isUndoAvailable())

from PySide6.QtCore import Qt as _Qt  # noqa: E402
editor._set_alignment(_Qt.AlignCenter)
alignment_after = editor.text_edit.textCursor().blockFormat().alignment()
editor.text_edit.undo()
check("a paragraph change",
      editor.text_edit.textCursor().blockFormat().alignment() != alignment_after
      and editor.text_edit.toPlainText().strip() == "base text")

print("\n--- saving repeatedly does not pile up undo steps ---")
editor2 = RichEditor(link_dates=True)
editor2.load("<p>start</p>", "html")
cursor = editor2.text_edit.textCursor()
cursor.movePosition(QTextCursor.End)
cursor.insertText(" edited once")
for _ in range(5):
    editor2.save()
steps = 0
while editor2.text_edit.document().isUndoAvailable() and steps < 8:
    editor2.text_edit.undo()
    steps += 1
    if "edited once" not in editor2.text_edit.toPlainText():
        break
check(f"five saves later, ONE undo still reaches the edit ({steps} step(s))", steps == 1)

print("\n--- and neither does zoom, or a settings change ---")
editor3 = RichEditor(link_dates=True)
editor3.load("<p>zoomed</p>", "html")
editor3.text_edit.set_zoom_steps(4)
check("zooming does not make a document look edited", not editor3.is_dirty())
editor3.set_font("Georgia", 15)
check("applying the font setting does not either", not editor3.is_dirty())

# ================================ steps 11-19: Save / Discard / Cancel
print("\n--- Steps 11-19: leaving a dirty journal entry ---")
win.selected_date.set("2026-03-05")
settle()
type_text("unsaved words")
saved_before = win.db.has_journal_entry("2026-03-05")

asked.clear()
answer["value"] = CANCEL
moved = win._request_date("2026-03-06")
settle()
check("the prompt is shown", asked == ["this journal entry"])
check("Cancel aborts the navigation", not moved and win.current_date == "2026-03-05")
check("the unsaved text is still in the editor",
      "unsaved words" in win.editor.text_edit.toPlainText())
check("and still unsaved", win.db.has_journal_entry("2026-03-05") == saved_before)

asked.clear()
answer["value"] = SAVE
moved = win._request_date("2026-03-06")
settle()
check("Save persists it and continues", moved and win.current_date == "2026-03-06")
check("the entry is on disk", win.db.has_journal_entry("2026-03-05"))
check("with the right text",
      "unsaved words" in win.db.get_entry("2026-03-05").body_text)

win.selected_date.set("2026-03-05")
settle()
type_text(" and more that gets discarded")
asked.clear()
answer["value"] = DISCARD
moved = win._request_date("2026-03-07")
settle()
check("Discard continues the navigation", moved and win.current_date == "2026-03-07")
win.selected_date.set("2026-03-05")
settle()
check("the last SAVED version is intact",
      "unsaved words" in win.editor.text_edit.toPlainText())
check("and the discarded words are gone",
      "discarded" not in win.editor.text_edit.toPlainText())
check("discard did not delete the entry", win.db.has_journal_entry("2026-03-05"))

print("\n--- no prompt when there is nothing to lose ---")
asked.clear()
win._request_date("2026-03-08")
settle()
check("a clean document is not asked about", asked == [])

set_autosave_enabled(win.db, True)
type_text("typed with autosave on")
win._autosave_tick()
settle()
asked.clear()
win._request_date("2026-03-09")
settle()
check("with autosave on, no prompt either", asked == [])
set_autosave_enabled(win.db, False)

# ============================================ step 20-21: closing
print("\n--- Steps 20-21: closing with unsaved changes ---")
win.selected_date.set("2026-03-12")
settle()
type_text("words at closing time")
asked.clear()
answer["value"] = CANCEL
from PySide6.QtGui import QCloseEvent  # noqa: E402
close_event = QCloseEvent()
win.closeEvent(close_event)
check("closing asks", asked == ["this journal entry"])
check("Cancel stops the close", not close_event.isAccepted())
check("the text is still there", "closing time" in win.editor.text_edit.toPlainText())

asked.clear()
answer["value"] = SAVE
close_event = QCloseEvent()
win.closeEvent(close_event)
check("Save lets the close proceed", close_event.isAccepted())
# The close also closed the database, which is the point — so the check has
# to be made against a freshly opened one, exactly as the next launch would.
from app.database import Database as _Database  # noqa: E402
_reopened = _Database()
check("and the entry was written before shutting down",
      _reopened.has_journal_entry("2026-03-12"))
_reopened.close()

# ====================================== steps 22-27: autosave behaviour
print("\n--- Steps 22-27: autosave, and remembering the preference ---")
win2 = MainWindow()
win2.show()
settle()
set_autosave_enabled(win2.db, True)
win2.selected_date.set("2026-04-01")
settle()
type_text("autosaved text", editor=win2.editor)
check("editing schedules a save when autosave is on",
      win2._autosave_timer.isActive())
win2._autosave_tick()
settle()
check("autosave writes it", win2.db.has_journal_entry("2026-04-01"))
check("and clears the dirty flag", not win2.editor.is_dirty())

type_text(" more", editor=win2.editor)
win2._autosave_tick()
settle()
win2.editor.text_edit.undo()
settle()
check("autosave does not destroy undo history",
      "autosaved text" in win2.editor.text_edit.toPlainText()
      and "more" not in win2.editor.text_edit.toPlainText())

set_autosave_enabled(win2.db, False)
win2.selected_date.set("2026-04-02")
settle()
type_text("not autosaved", editor=win2.editor)
check("with autosave off, no save is scheduled", not win2._autosave_timer.isActive())
win2._autosave_tick()
settle()
check("and even a stray timer tick writes nothing",
      not win2.db.has_journal_entry("2026-04-02"))

answer["value"] = SAVE
close_event = QCloseEvent()
win2.closeEvent(close_event)
settle()

print("\n--- the preference survives a restart ---")
win3 = MainWindow()
win3.show()
settle()
check("autosave is remembered as OFF", not autosave_enabled(win3.db))
set_autosave_enabled(win3.db, True)
answer["value"] = SAVE
close_event = QCloseEvent()
win3.closeEvent(close_event)
settle()
win4 = MainWindow()
win4.show()
settle()
check("and remembered as ON after being switched on", autosave_enabled(win4.db))

print("\n--- Part 25: an established install keeps autosave ---")
from app.database import Database  # noqa: E402
established = Database(str(pathlib.Path(tempfile.mkdtemp()) / "old.db"))
established.upsert_entry("2025-01-01", body_md="<p>years of writing</p>",
                         body_format="html", body_text="years of writing")
check("a database with journal history keeps autosave on",
      saving.ensure_autosave_default(established) is True)
established.close()
brand_new = Database(str(pathlib.Path(tempfile.mkdtemp()) / "new.db"))
check("a fresh install starts on manual saving",
      saving.ensure_autosave_default(brand_new) is False)
brand_new.close()

print("\n--- Part 24: Projects and Reader's Notes save the same way ---")
win5 = MainWindow()
win5.show()
settle()
set_autosave_enabled(win5.db, False)
project = win5.db.create_project("Saving test")
win5.projects_widget.refresh_project_list(select_id=project.id)
win5.main_tabs.setCurrentWidget(win5.projects_widget)
settle()
type_text("project words", editor=win5.projects_widget.editor)
check("the project is dirty", win5.projects_widget.is_dirty())
win5._save_active_workspace()
settle()
check("Ctrl+S saves the project",
      "project words" in win5.db.get_project(project.id).content_text)
check("and the project is clean again", not win5.projects_widget.is_dirty())

win5.projects_widget.reader_notes.editor.text_edit.setPlainText("a project note")
win5._save_active_workspace()
settle()
check("Ctrl+S saves Reader's Notes too",
      "a project note" in win5.db.get_notes("project", str(project.id)).content_text)

type_text(" unsaved project words", editor=win5.projects_widget.editor)
asked.clear()
answer["value"] = CANCEL
allowed = win5.projects_widget.allow_leaving_project()
check("switching project asks", asked == ["this project"])
check("and Cancel keeps it open", not allowed)
answer["value"] = SAVE
close_event = QCloseEvent()
win5.closeEvent(close_event)
settle()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
