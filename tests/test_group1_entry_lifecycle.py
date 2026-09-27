"""Group 1 — entry semantics, saving, visible autosave state, prompts.

Everything here goes through the real window with real key events, and
checks the DATABASE (after closing and re-opening where it matters), because
the defects this group fixes lived in the relationship between the editor,
the save path and the calendar — not in any one of them.

Covers the approved acceptance criteria 1–8 (see JORTLE_IMPLEMENTATION_HANDOFF).
"""
import os
import pathlib
import sqlite3
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g1-life-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent, QColor, QImage, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextEdit  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import main_window as mw  # noqa: E402
from app import saving  # noqa: E402
from app.archive import _has_content as archive_has_content  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_attachments_dir  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import (  # noqa: E402
    CANCEL, DISCARD, SAVE, MEANINGFUL_TEXT_SQL, autosave_enabled,
    document_has_content, has_meaningful_text, register_sql_functions,
    set_autosave_enabled
)
from app.settings_dialog import SettingsDialog  # noqa: E402

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


def type_text(editor_widget, text):
    edit = editor_widget.text_edit
    edit.setFocus()
    for part_index, part in enumerate(text.split("\n")):
        if part_index:
            QTest.keyClick(edit, Qt.Key_Return)
        if part:
            QTest.keyClicks(edit, part)
    settle()


def delete_all(editor_widget):
    edit = editor_widget.text_edit
    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key_Delete)
    settle()


asked = []
answer = {"value": CANCEL}


def fake_ask(_parent, what):
    asked.append(what)
    return answer["value"]


mw.ask_unsaved = fake_ask
import app.projects_widget as pw  # noqa: E402
pw.ask_unsaved = fake_ask


def rows(win, sql, *params):
    return win.db._conn.execute(sql, params).fetchall()


def marks(win, date):
    y, m = int(date[:4]), int(date[5:7])
    win.calendar_panel.calendar.setCurrentPage(y, m)
    win._refresh_calendar_marks()
    cal = win.calendar_panel.calendar
    return date in cal._entry_dates, date in cal._other_content_dates


# ====================================================== 1. no growth
print("\n--- [2] save/reload is exact: no blank lines appear from nowhere ---")
shapes = {
    "empty": "", "blank line first": "\n\nhello", "blank lines last": "hello\n\n",
    "blank lines in the middle": "a\n\n\nb", "just a newline": "\n",
}
for label, text in shapes.items():
    ed = RichEditor()
    ed.show()
    ed.load("", "html")
    if text:
        ed.text_edit.insertPlainText(text)
    blocks = ed.text_edit.document().blockCount()
    plain = ed.text_edit.toPlainText()
    for _ in range(10):
        html, _ = ed.save()
        ed.load(html, "html")
    check(f"{label}: 10 save/reload cycles keep {blocks} paragraph(s)",
          ed.text_edit.document().blockCount() == blocks,
          f"{ed.text_edit.document().blockCount()}")
    check(f"{label}: ...and exactly the same text", ed.text_edit.toPlainText() == plain)
    check(f"{label}: saved HTML has no root-frame wrapper",
          "-qt-table-type" not in ed.save()[0])

print("\n--- [3] rows stored in the old shape load exactly ---")
for label, text in (("empty", ""), ("blank line first", "\n\nhello")):
    old = QTextEdit()
    old.setHtml("")
    if text:
        old.insertPlainText(text)
    frame = old.document().rootFrame().frameFormat()
    frame.setBottomMargin(0)                       # what the old save did
    old.document().rootFrame().setFrameFormat(frame)
    legacy_html = old.document().toHtml()
    ed = RichEditor()
    ed.load(legacy_html, "html")
    check(f"old-shape '{label}' opens with its real paragraph count",
          ed.text_edit.document().blockCount() == old.document().blockCount())
    check(f"old-shape '{label}' opens with its real text",
          ed.text_edit.toPlainText() == old.toPlainText())


# ====================================================== 4. the rule
print("\n--- [4] one rule for written vs blank, everywhere ---")
image_path = get_attachments_dir() / "2026" / "01"
image_path.mkdir(parents=True, exist_ok=True)
img = QImage(24, 24, QImage.Format_RGB32)
img.fill(QColor("red"))
img.save(str(image_path / "dot.png"))
img_doc = RichEditor()
img_doc.load("", "html")
img_doc.text_edit.textCursor().insertImage("2026/01/dot.png")
img_html, img_plain = img_doc.save()

conn = sqlite3.connect(":memory:")
register_sql_functions(conn)
conn.execute("CREATE TABLE e (body_text TEXT, body_md TEXT)")
samples = [
    ("", "<p></p>", False), ("   ", "", False), ("\n\n\n", "", False), ("\t", "", False),
    (" ", "", False), ("​", "", False), ("　 ", "", False),
    ("x", "", True), ("  hello ", "", True), (img_plain, img_html, True),
    ("", '<p><img src="a.png" /></p>', True),
]
for text, html, expected in samples:
    conn.execute("DELETE FROM e")
    conn.execute("INSERT INTO e VALUES (?, ?)", (text, html))
    sql_says = conn.execute(f"SELECT COUNT(*) FROM e WHERE {MEANINGFUL_TEXT_SQL}").fetchone()[0] == 1
    python_says = document_has_content(html, text)
    check(f"{text!r}: Python={python_says} SQL={sql_says} archive={archive_has_content(html, text)} "
          f"expected {expected}",
          python_says == sql_says == archive_has_content(html, text) == expected)
check("an image-only document counts as written", document_has_content(img_html, img_plain))
check("plain-text rule unchanged for blank input", not has_meaningful_text("​"))

# The same samples through the three places that used to keep their own rule
# (1R-F1): the migration check, diagnose_data.py and the autosave default.
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402

import diagnose_data  # noqa: E402
from app import data_migration  # noqa: E402
from app.database import Database  # noqa: E402

blank_samples = [(t, h) for t, h, expected in samples if not expected] + [
    ("﻿", ""), ("⁠", ""), ("", "<ul><li></li></ul>"),
]
written_samples = [("x", "<p>x</p>"), (img_plain, img_html), ("", '<p><img src="a.png" /></p>')]
scratch = pathlib.Path(tempfile.mkdtemp(prefix="jortle-g1-rule-"))


def one_row_folder(name, sql, *params):
    folder = scratch / name
    folder.mkdir()
    db = Database(str(folder / "journal.db"))
    if sql:
        db._conn.execute(sql, params)
        db._conn.commit()
    return folder, db


ENTRY = ("INSERT INTO entries (date, title, body_md, body_format, body_text, tag, created_at, "
         "updated_at) VALUES ('2026-01-05', ?, ?, 'html', ?, ?, 'x', 'x')")
NOTE = ("INSERT INTO reader_notes_scoped (scope, ref, content, content_format, content_text, "
        "created_at, updated_at) VALUES ('date', '2026-01-05', ?, 'html', ?, 'x', 'x')")
for i, (text, html) in enumerate(blank_samples + written_samples):
    expected = (text, html) in written_samples
    folder, db = one_row_folder(f"entry-{i}", ENTRY, "", html, text, None)
    autosave_says = saving.ensure_autosave_default(db)
    db.close()
    check(f"{text!r}/{html[:24]!r}: migration check says user content = {expected}",
          data_migration.has_user_content(folder) is expected)
    check(f"{text!r}/{html[:24]!r}: diagnose_data counts it as writing = {expected}",
          ("2026-01-05" in diagnose_data.entry_lengths(folder)) is expected)
    check(f"{text!r}/{html[:24]!r}: autosave default for an install holding only it = {expected}",
          autosave_says is expected)
    folder, db = one_row_folder(f"note-{i}", NOTE, html, text)
    db.close()
    check(f"{text!r}/{html[:24]!r}: a Reader's Note alone is user content = {expected}",
          data_migration.has_user_content(folder) is expected)

for label, title, tag in (("a Day Marker", "", "Rest"), ("a title", "A title", None)):
    folder, db = one_row_folder(f"entry-{label}", ENTRY, title, "", "", tag)
    db.close()
    check(f"a blank entry with {label} is still user content",
          data_migration.has_user_content(folder) is True)

# A database from before round 21 has no body_text: judged on body_md.
for label, body, expected in (("blank", "\n\n", False), ("written", "old words", True)):
    folder = scratch / f"pre-round21-{label}"
    folder.mkdir()
    conn = sqlite3.connect(str(folder / "journal.db"))
    conn.execute("CREATE TABLE entries (date TEXT PRIMARY KEY, title TEXT, body_md TEXT, "
                 "tag TEXT, tag_color TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO entries VALUES ('2025-05-05', '', ?, NULL, NULL, 'x', 'x')", (body,))
    conn.commit()
    conn.close()
    check(f"a pre-round-21 database with a {label} entry: user content = {expected}",
          data_migration.has_user_content(folder) is expected)

# diagnose_data's comparison: an image-only entry missing here is never "safe to delete".
current, db = one_row_folder("compare-current", None)
db.close()
other, db = one_row_folder("compare-image", ENTRY, "", '<p><img src="a.png" /></p>', "", None)
db.close()
blank_other, db = one_row_folder("compare-blank", ENTRY, "", "<p></p>", "\n\n", None)
db.close()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    diagnose_data.compare(current, [other, blank_other])
report = out.getvalue()
image_part = report.split(other.name, 1)[1].split(blank_other.name, 1)[0]
check("diagnose_data: an image-only entry absent here is reported missing, not safe to delete",
      "2026-01-05" in image_part and "Safe to delete" not in image_part, report)
check("diagnose_data: a folder holding only a blank row has nothing missing",
      "HAS WRITING" not in report.split(blank_other.name, 1)[1], report)


# ====================================================== 1. empty dates
win = MainWindow()
win.show()
settle(50)

for autosave in (True, False):
    set_autosave_enabled(win.db, autosave)
    win._sync_autosave_widgets()
    base = "2026-03" if autosave else "2026-04"
    a, b, c = f"{base}-02", f"{base}-03", f"{base}-04"
    print(f"\n--- [1] visiting empty dates, autosave {'ON' if autosave else 'OFF'} ---")
    for date in (a, b, a, c, a, b, a, c, a):
        win._request_date(date)
        settle(1400)                                  # let any timer fire
    check("no journal row for any visited date",
          not rows(win, "SELECT date FROM entries WHERE date LIKE ?", base + "%"))
    check("no Reader's Notes row for any visited date",
          not rows(win, "SELECT ref FROM reader_notes_scoped WHERE ref LIKE ?", base + "%"))
    check("no version record for any visited date",
          not rows(win, "SELECT id FROM entry_revisions WHERE date LIKE ?", base + "%"))
    check("no filled marker and no hollow marker", marks(win, a) == (False, False))
    check("the editor is one genuinely empty paragraph",
          win.editor.text_edit.document().blockCount() == 1
          and win.editor.text_edit.toPlainText() == "")
    check("Reader's Notes editor is empty too",
          win.reader_notes.editor.text_edit.document().blockCount() == 1)

print("\n--- [1] existing blank rows from earlier versions ---")
ghost_date = "2026-05-09"
win.db.upsert_entry(ghost_date, body_md="<html><body><p><br /></p><p><br /></p><p><br /></p></body></html>",
                    body_format="html", body_text="\n\n")
win.db.save_notes("date", ghost_date, "<p><br /></p>", content_text="\n")
win._request_date(ghost_date)
settle()
check("a stored blank row opens as an empty editor (no ghost lines)",
      win.editor.text_edit.document().blockCount() == 1)
check("a newline-only note gives no hollow marker any more", marks(win, ghost_date) == (False, False))
before = rows(win, "SELECT body_md, updated_at FROM entries WHERE date=?", ghost_date)
set_autosave_enabled(win.db, True)
win._request_date("2026-05-10")
settle(1400)
check("viewing it did not rewrite the row",
      rows(win, "SELECT body_md, updated_at FROM entries WHERE date=?", ghost_date) == before)


# ====================================================== 5. deleting an entry
print("\n--- [5] deleting all of an entry's text deletes the entry ---")
set_autosave_enabled(win.db, False)
win._sync_autosave_widgets()
del_date = "2026-06-11"
win._request_date(del_date)
settle()
type_text(win.editor, "A short entry that will be deleted.")
win._save_active_workspace()
settle()
check("typed + Ctrl+S: the entry exists", win.db.has_journal_entry(del_date))
check("...and is marked", marks(win, del_date)[0])
delete_all(win.editor)
check("select-all + Delete leaves the editor blank",
      not has_meaningful_text(win.editor.text_edit.toPlainText()))
win._save_active_workspace()
settle()
check("after saving, there is no entry", not win.db.has_journal_entry(del_date))
check("...and no marker", marks(win, del_date) == (False, False))
stored = win.db.get_entry(del_date)
check("the stored body is empty, not blank boilerplate",
      stored is not None and stored.body_md == "" and stored.body_text == "")
revs = win.db.list_entry_revisions(del_date)
check("the text it had is kept as a version (even though it was short)",
      any("deleted" in (win.db.get_entry_revision(r.id).body_text or "") for r in revs))
win._request_date("2026-06-12")
win._request_date(del_date)
settle()
check("returning shows a genuinely empty editor",
      win.editor.text_edit.document().blockCount() == 1 and win.editor.text_edit.toPlainText() == "")

print("\n--- [5] same with autosave ON (the timer does the saving) ---")
set_autosave_enabled(win.db, True)
auto_date = "2026-06-14"
win._request_date(auto_date)
settle()
type_text(win.editor, "Written with autosave on.")
settle(1500)
check("autosave wrote it", win.db.has_journal_entry(auto_date))
delete_all(win.editor)
settle(1500)
check("autosave of the emptied editor deletes the entry", not win.db.has_journal_entry(auto_date))
check("marker gone", marks(win, auto_date) == (False, False))

print("\n--- [5] a formatting-only document is not an entry ---")
fmt_date = "2026-06-15"
win._request_date(fmt_date)
settle()
win.editor._toggle_bullet_list()          # an empty list item: structure, no text
win.editor.text_edit.document().setModified(True)
win._save_active_workspace()
settle()
check("an empty list item does not create an entry",
      not rows(win, "SELECT date FROM entries WHERE date=?", fmt_date))


# ====================================================== 6. saving
print("\n--- [6] explicit and automatic saving ---")
set_autosave_enabled(win.db, False)
save_date = "2026-07-01"
win._request_date(save_date)
settle()
type_text(win.editor, "manual words")
win._autosave_tick()
check("with autosave off, the timer never writes", not win.db.has_journal_entry(save_date))
win._save_active_workspace()
check("Ctrl+S always writes", win.db.has_journal_entry(save_date))
stamp = rows(win, "SELECT updated_at, body_md FROM entries WHERE date=?", save_date)
QTest.qWait(1100)
win._save_active_workspace()
check("Ctrl+S on an unchanged entry does not rewrite it",
      rows(win, "SELECT updated_at, body_md FROM entries WHERE date=?", save_date) == stamp)
set_autosave_enabled(win.db, True)
win._save_current_entry(automatic=True)
check("an automatic save of a clean entry writes nothing",
      rows(win, "SELECT updated_at, body_md FROM entries WHERE date=?", save_date) == stamp)


# ====================================================== 7. visible state
print("\n--- [7] autosave switch and unsaved-changes flag ---")
win.set_autosave(False)
check("toggle shows OFF", not win.autosave_action.isChecked() and "Off" in win.autosave_action.text())
check("stored OFF", not autosave_enabled(win.db))
vis_date = "2026-07-05"
win._request_date(vis_date)
settle()
check("clean: no unsaved flag", not win.unsaved_label.isVisible() and not win.isWindowModified())
type_text(win.editor, "unsaved")
check("typing with autosave off shows the unsaved flag", win.unsaved_label.isVisible())
check("...and the window title's '*'", win.isWindowModified())
win._save_active_workspace()
settle()
check("saving clears both", not win.unsaved_label.isVisible() and not win.isWindowModified())
type_text(win.reader_notes.editor, "a note")
check("an unsaved Reader's Note also shows the flag", win.unsaved_label.isVisible())
win._save_active_workspace()
settle()
check("...cleared by Ctrl+S", not win.unsaved_label.isVisible())

win.autosave_button.click()
settle()
check("clicking the status-bar toggle turns autosave ON",
      autosave_enabled(win.db) and win.autosave_action.isChecked())
dlg = SettingsDialog(win.db, on_change=win._apply_settings, parent=win,
                     set_autosave=win.set_autosave)
check("Settings shows the same state", dlg.autosave_check.isChecked())
dlg.autosave_check.setChecked(False)
settle()
check("unticking Settings turns the status-bar toggle off too",
      not win.autosave_action.isChecked() and not autosave_enabled(win.db))
dlg.close()
type_text(win.editor, " more")
check("dirty with autosave off: flag shown", win.unsaved_label.isVisible())
win.set_autosave(True)
settle(1500)
check("switching autosave ON saves pending work",
      "more" in (win.db.get_entry(vis_date).body_text or "") and not win.editor.is_dirty())
check("...and the flag goes", not win.unsaved_label.isVisible())
win.set_autosave(False)
win.close()
settle()
win2 = MainWindow()
win2.show()
settle()
check("autosave OFF survives restart",
      not autosave_enabled(win2.db) and not win2.autosave_action.isChecked())
win2.set_autosave(True)
win2.close()
settle()
win3 = MainWindow()
check("autosave ON survives restart", win3.autosave_action.isChecked())


# ====================================================== 8. prompts
print("\n--- [8] Save / Discard / Cancel, including the new places ---")
win3.set_autosave(False)
win3.show()
settle()
p_date = "2026-07-20"
win3._request_date(p_date)
settle()
type_text(win3.editor, "prompt text")
asked.clear()
answer["value"] = CANCEL
from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402
orig_open, orig_warning = QFileDialog.getOpenFileName, QMessageBox.warning
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("/nonexistent/backup.zip", ""))
warnings = []
QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a) or QMessageBox.No)
win3._import_backup()
check("restoring a backup asks about unsaved work first", asked == ["this journal entry"])
check("Cancel stops the restore before its own confirmation", not warnings)
check("...and the text is still there", "prompt text" in win3.editor.text_edit.toPlainText())
QFileDialog.getOpenFileName, QMessageBox.warning = orig_open, orig_warning

asked.clear()
answer["value"] = CANCEL
moved = win3._request_date("2026-07-21")
check("changing date asks; Cancel keeps you here", asked and not moved)
answer["value"] = DISCARD
moved = win3._request_date("2026-07-21")
check("Discard moves on and writes nothing", moved and not win3.db.has_journal_entry(p_date))
win3._request_date(p_date)
type_text(win3.editor, "closing text")
asked.clear()
answer["value"] = CANCEL
event = QCloseEvent()
win3.closeEvent(event)
check("closing asks; Cancel keeps the window open", asked and not event.isAccepted())
answer["value"] = SAVE
event = QCloseEvent()
win3.closeEvent(event)
check("Save lets the close proceed", event.isAccepted())
win4 = MainWindow()
check("...and the entry was written before shutting down",
      "closing text" in (win4.db.get_entry(p_date).body_text or ""))
win4.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
