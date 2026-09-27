"""Group 1 — daily-entry version history and recovery checkpoints.

Acceptance criteria 9–13 (see JORTLE_IMPLEMENTATION_HANDOFF): when versions
are made (and when they are not), that they cannot be changed, that the list
does not read bodies, restoring, deleting, large-deletion checkpoints and
File → Recovery — each checked against the database and, where it matters,
after a restart.
"""
import os
import pathlib
import sqlite3
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g1-hist-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QDate, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import entry_history  # noqa: E402
from app.entry_history import is_substantial_removal, removal  # noqa: E402
from app.history_dialogs import EntryHistoryDialog, RecoveryDialog  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.saving import set_autosave_enabled  # noqa: E402

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
    edit.moveCursor(QTextCursor.End)
    for index, part in enumerate(text.split("\n")):
        if index:
            QTest.keyClick(edit, Qt.Key_Return)
        if part:
            QTest.keyClicks(edit, part)
    settle()


def select_all_delete(editor_widget):
    edit = editor_widget.text_edit
    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key_Delete)
    settle()


# Every confirmation in the dialogs answers Yes.
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


# ================================================== pure policy
print("\n--- deletion measurement ---")
text = "\n".join(f"Paragraph {i}: " + "words " * 20 for i in range(10))
check("deleting everything removes all of it",
      removal(text, "")[0] == len(text.replace("\n", "")))
check("deleting 3 paragraphs counts 3 paragraphs",
      removal(text, "\n".join(text.split("\n")[3:]))[1] == 3)
reworded = text.replace("Paragraph 4", "Section 4")
check("rewording is not counted as deleting much", removal(text, reworded)[0] < 20)
check("1000 characters removed is substantial", is_substantial_removal(5000, 1000))
check("half of a 400-character entry is substantial", is_substantial_removal(400, 200))
check("150 characters of a 200-character entry is not (under 200)", not is_substantial_removal(200, 150))
check("300 characters of a 5000-character entry is not", not is_substantial_removal(5000, 300))


# ================================================== revisions
win = MainWindow()
win.show()
settle(50)
clock = FakeClock()
win.history.clock = clock
set_autosave_enabled(win.db, True)
win._sync_autosave_widgets()
db = win.db

print("\n--- [9] when versions are made ---")
old_date = "2026-02-10"
db.upsert_entry(old_date, body_md="<p>alpha written long ago</p>", body_format="html",
                body_text="alpha written long ago")
win._request_date(old_date)
settle()
check("opening an entry makes no version", not db.list_entry_revisions(old_date))
win._request_date("2026-02-11")
win._request_date(old_date)
settle()
check("opening and leaving without editing makes none", not db.list_entry_revisions(old_date))

type_text(win.editor, " and beta")
settle(1500)                                           # autosave writes
revs = db.list_entry_revisions(old_date)
check("the first overwrite keeps the pre-existing state first",
      len(revs) == 1 and revs[0].reason == entry_history.REASON_BEFORE_EDITING
      and "beta" not in (db.get_entry_revision(revs[0].id).body_text or ""))
type_text(win.editor, " and gamma")
settle(1500)
check("further autosaves in the same visit make no more versions",
      len(db.list_entry_revisions(old_date)) == 1)
win._request_date("2026-02-12")
settle()
revs = db.list_entry_revisions(old_date)
check("leaving after a change keeps the state it was left in",
      len(revs) == 2 and revs[0].reason == entry_history.REASON_LEFT
      and "gamma" in (db.get_entry_revision(revs[0].id).body_text or ""))
win._request_date(old_date)
win._request_date("2026-02-12")
settle()
check("re-visiting without change adds nothing", len(db.list_entry_revisions(old_date)) == 2)

print("\n--- [9] long editing sessions ---")
long_date = "2026-02-15"
win._request_date(long_date)
settle()
type_text(win.editor, "Start of a long session.")
settle(1500)
count0 = len(db.list_entry_revisions(long_date))
clock.now += 31 * 60
type_text(win.editor, " " + "More text written much later. " * 10)
settle(1500)
revs = db.list_entry_revisions(long_date)
check("after 30+ minutes with a substantial change, a version is kept",
      len(revs) == count0 + 1 and revs[0].reason == entry_history.REASON_LONG_SESSION)
clock.now += 31 * 60
type_text(win.editor, " tiny")
settle(1500)
check("...but not for a tiny change", len(db.list_entry_revisions(long_date)) == count0 + 1)

print("\n--- [9] blank states are never versions ---")
blank_date = "2026-02-16"
win._request_date(blank_date)
settle()
type_text(win.editor, "x")
select_all_delete(win.editor)
settle(1500)
win._request_date("2026-02-17")
settle()
check("typing and deleting leaves no version", not db.list_entry_revisions(blank_date))

print("\n--- [9] closing is a boundary ---")
close_date = "2026-02-20"
win._request_date(close_date)
settle()
type_text(win.editor, "written just before closing")
settle(1500)
win.close()
settle()
win = MainWindow()
win.show()
settle()
db = win.db
clock = FakeClock()
win.history.clock = clock
revs = db.list_entry_revisions(close_date)
check("closing the app keeps the state the entry was left in",
      any(r.reason == entry_history.REASON_CLOSED for r in revs))

print("\n--- [10] versions are immutable, and listed without their bodies ---")
rev_id = db.list_entry_revisions(old_date)[0].id
try:
    db._conn.execute("UPDATE entry_revisions SET body='changed' WHERE id=?", (rev_id,))
    db._conn.commit()
    immutable = False
except sqlite3.DatabaseError:
    db._conn.rollback()
    immutable = True
check("an UPDATE of a version is refused by the database", immutable)
check("the version is unchanged", "changed" != db.get_entry_revision(rev_id).body)
listed = db.list_entry_revisions(old_date)
check("the list carries no bodies", all(r.body is None and r.body_text is None for r in listed))

loads = []
original_get = db.get_entry_revision
db.get_entry_revision = lambda rid: loads.append(rid) or original_get(rid)
win._request_date(old_date)
settle()
dialog = EntryHistoryDialog(db, old_date, parent=win)
check("opening Version History loads exactly one body (the selected one)",
      len(loads) == 1, f"{loads}")
dialog.list.setCurrentRow(1)
check("selecting another loads just that one", len(loads) == 2)
db.get_entry_revision = original_get

print("\n--- [11] restoring a version ---")
before_restore_text = win.editor.text_edit.toPlainText()
oldest = db.list_entry_revisions(old_date)[-1]
dialog.restoreRequested.connect(win._restore_entry_revision)
dialog.list.setCurrentRow(dialog.list.count() - 1)
dialog._request_restore()
settle()
check("the entry now holds the restored version",
      win.editor.text_edit.toPlainText().strip() == "alpha written long ago")
check("...stored, not just shown", (db.get_entry(old_date).body_text or "").strip() == "alpha written long ago")
check("the state it replaced was kept first",
      any(r.reason == entry_history.REASON_BEFORE_RESTORE or
          (db.get_entry_revision(r.id).body_text or "") == before_restore_text
          for r in db.list_entry_revisions(old_date)))
check("a restore does not raise a recovery copy", not db.list_recovery_checkpoints())
dialog.close()
win.close()
settle()
win = MainWindow()
win.show()
settle()
db = win.db
check("the restored version survives restart",
      (db.get_entry(old_date).body_text or "").strip() == "alpha written long ago")

print("\n--- [12] deleting previous versions ---")
db._conn.execute(
    "INSERT INTO entry_revisions (date, title, body, body_format, body_text, content_hash, "
    "reason, created_at) VALUES (?, '', '<p>ancient</p>', 'html', 'ancient', 'h-anc', "
    "'test', '2025-01-01T10:00:00')", (old_date,))
db._conn.commit()
db.add_recovery_checkpoint("date", old_date, "<p>kept</p>", "html", "kept", removed_chars=1000)
other_date = close_date
other_count = len(db.list_entry_revisions(other_date))
total = len(db.list_entry_revisions(old_date))
dialog = EntryHistoryDialog(db, old_date, parent=win)
dialog.cutoff.setDate(QDate(2026, 1, 1))
dialog._delete_older()
check("'older than' deletes only the older version",
      len(db.list_entry_revisions(old_date)) == total - 1
      and not any(r.created_at.startswith("2025") for r in db.list_entry_revisions(old_date)))
dialog._delete_all()
check("'delete all' leaves no previous versions of this entry",
      not db.list_entry_revisions(old_date))
check("the entry itself is untouched", "alpha" in (db.get_entry(old_date).body_text or ""))
check("other entries' versions are untouched", len(db.list_entry_revisions(other_date)) == other_count)
check("recovery copies are untouched", len(db.list_recovery_checkpoints()) == 1)
dialog.close()
db._conn.execute("DELETE FROM recovery_checkpoints")
db._conn.commit()
win.close()
settle()
win = MainWindow()
win.show()
settle()
db = win.db
check("deletions persist after restart", not db.list_entry_revisions(old_date))


# ================================================== recovery
print("\n--- [13] a large deletion is caught ---")
set_autosave_enabled(db, True)
big_date = "2026-03-10"
win._request_date(big_date)
settle()
paragraphs = [f"Paragraph {i}. " + "Some real sentences go here. " * 3 for i in range(15)]
type_text(win.editor, "\n".join(paragraphs))
settle(1500)
win._request_date("2026-03-11")                   # a new visit starts from the stored text
win._request_date(big_date)
settle()
prior = len(win.editor.text_edit.toPlainText().replace("\n", ""))   # line breaks aren't characters
select_all_delete(win.editor)
settle(1500)
cps = db.list_recovery_checkpoints()
check("select-all + delete of a long entry keeps exactly one recovery copy", len(cps) == 1)
cp = db.get_recovery_checkpoint(cps[0].id) if cps else None
check("it holds the text as it was", cp is not None and "Paragraph 14" in cp.body_text)
check("with the right numbers",
      cp is not None and cp.prior_chars == prior and cp.removed_chars >= prior - 20
      and cp.prior_paragraphs == 15 and cp.remaining_chars == 0,
      f"{cp and (cp.prior_chars, cp.removed_chars, cp.prior_paragraphs, cp.remaining_chars)} vs {prior}")

print("\n--- [13] File → Recovery restores it ---")
dialog = RecoveryDialog(db, parent=win)
check("the Recovery window lists it", dialog.list.count() == 1)
check("and previews its text", "Paragraph 3" in dialog.preview.text_edit.toPlainText())
dialog.restoreRequested.connect(win._restore_recovery_checkpoint)
dialog._request_restore()
settle()
check("restoring puts the text back", "Paragraph 14" in (db.get_entry(big_date).body_text or ""))
check("and the calendar marks the entry again", db.has_journal_entry(big_date))
dialog._copy()
check("Copy Text puts the recovered text on the clipboard",
      "Paragraph 7" in QApplication.clipboard().text())
dialog.close()

print("\n--- [13] a deletion made a little at a time is caught once ---")
db._conn.execute("DELETE FROM recovery_checkpoints")
db._conn.commit()
slow_date = "2026-03-12"
win._request_date(slow_date)
settle()
type_text(win.editor, "\n".join(paragraphs))
settle(1500)
win._request_date("2026-03-13")
win._request_date(slow_date)
settle()
for _ in range(5):                                  # three paragraphs per autosave
    edit = win.editor.text_edit
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.Start)
    for _ in range(3):
        cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
    edit.setTextCursor(cursor)
    QTest.keyClick(edit, Qt.Key_Delete)
    settle(1500)
check("gradual deletion of most of an entry keeps one recovery copy",
      len(db.list_recovery_checkpoints()) == 1, f"{len(db.list_recovery_checkpoints())}")

print("\n--- [13] ordinary editing raises nothing ---")
db._conn.execute("DELETE FROM recovery_checkpoints")
db._conn.commit()
edit_date = "2026-03-14"
win._request_date(edit_date)
settle()
type_text(win.editor, "\n".join(paragraphs))
settle(1500)
for _ in range(10):
    QTest.keyClick(win.editor.text_edit, Qt.Key_Backspace)
settle(1500)
type_text(win.editor, "a few more words")
settle(1500)
check("small deletions and additions make no recovery copy", not db.list_recovery_checkpoints())

print("\n--- [13] projects are protected too ---")
project = db.create_project("Recovery test")
pwid = win.projects_widget
pwid.refresh_project_list(select_id=project.id)
win.main_tabs.setCurrentWidget(pwid)
settle()
type_text(pwid.editor, "\n".join(paragraphs))
pwid.save_now()
pwid.refresh_project_list(select_id=None)
pwid.refresh_project_list(select_id=project.id)
settle()
select_all_delete(pwid.editor)
pwid.save_now()
cps = [c for c in db.list_recovery_checkpoints() if c.scope == "project"]
check("a large deletion in a project keeps a recovery copy", len(cps) == 1)
dialog = RecoveryDialog(db, parent=win)
dialog.restoreRequested.connect(win._restore_recovery_checkpoint)
dialog._request_restore()
settle()
check("restoring it puts the project text back",
      "Paragraph 14" in (db.get_project(project.id).content_text or ""))
check("the emptied state is blank, so it is not kept as a version",
      not any(v.label == "Before recovery restore" for v in db.get_project_versions(project.id)))
dialog.close()

print("\n--- [13] the 50% rule counts characters one way (line breaks excluded) ---")
# Through the save path, not is_substantial_removal: the prior text used to be
# measured with its line breaks, the removal without them (1R-F4).
short_lines = "\n".join(["ab"] * 200)               # 400 characters of text


def delete_first_lines(editor_widget, count):
    edit = editor_widget.text_edit
    edit.setFocus()
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.Start)
    for _ in range(count):
        cursor.movePosition(QTextCursor.NextBlock, QTextCursor.KeepAnchor)
    edit.setTextCursor(cursor)
    QTest.keyClick(edit, Qt.Key_Delete)


win.main_tabs.setCurrentWidget(win.daily_splitter)
for day, lines, expected in ((22, 99, 0), (23, 100, 1), (24, 140, 1), (25, 149, 1)):
    db._conn.execute("DELETE FROM recovery_checkpoints")
    db._conn.commit()
    date = f"2026-03-{day}"
    win._request_date(date)
    settle()
    win.editor.text_edit.setPlainText(short_lines)
    win._save_current_entry()
    win._request_date("2026-03-30")                 # a new visit starts from the stored text
    win._request_date(date)
    settle()
    delete_first_lines(win.editor, lines)
    settle(1500)                                    # the real autosave timer
    cps = db.list_recovery_checkpoints()
    check(f"deleting {lines} of 200 short lines ({2 * lines} of 400 characters) keeps "
          f"{expected} recovery cop{'y' if expected == 1 else 'ies'}", len(cps) == expected,
          f"{len(cps)}")
    if lines == 140 and cps:
        cp = cps[0]
        check("its numbers use the same count: prior 400, removed 280, remaining 120",
              (cp.prior_chars, cp.removed_chars, cp.remaining_chars) == (400, 280, 120),
              f"{(cp.prior_chars, cp.removed_chars, cp.remaining_chars)}")

for lines, expected in ((99, 0), (140, 1)):
    db._conn.execute("DELETE FROM recovery_checkpoints")
    db._conn.commit()
    lined = db.create_project(f"Short lines {lines}")
    pwid.refresh_project_list(select_id=lined.id)
    win.main_tabs.setCurrentWidget(pwid)
    settle()
    pwid.editor.text_edit.setPlainText(short_lines)
    pwid.save_now()
    pwid.refresh_project_list(select_id=None)       # a new session starts from the stored text
    pwid.refresh_project_list(select_id=lined.id)
    settle()
    delete_first_lines(pwid.editor, lines)
    pwid.save_now()
    cps = [c for c in db.list_recovery_checkpoints() if c.scope == "project"]
    check(f"a project: deleting {lines} of 200 short lines keeps {expected} recovery "
          f"cop{'y' if expected == 1 else 'ies'}", len(cps) == expected, f"{len(cps)}")

print("\n--- [13] a restore starts the session afresh (1R-F5, cause B) ---")
long_text = "\n".join(f"Long paragraph {i}. " + "Words that were written here. " * 3
                      for i in range(30))                         # about 3,000 characters


def type_at_end(editor_widget, text):
    edit = editor_widget.text_edit
    edit.setFocus()
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.End)
    edit.setTextCursor(cursor)
    QTest.keyClicks(edit, text)
    settle()


def entry_checkpoints(date):
    return [c for c in db.list_recovery_checkpoints() if c.scope == "date" and c.ref == date]


def project_checkpoints(pid):
    return [c for c in db.list_recovery_checkpoints() if c.scope == "project" and c.ref == str(pid)]


def store_and_leave(date, text):
    win._request_date(date)
    settle()
    win.editor.text_edit.setPlainText(text)
    win._save_current_entry()
    win._request_date("2026-04-30")                   # leaving ends the session


win.main_tabs.setCurrentWidget(win.daily_splitter)
b_date = "2026-04-01"
store_and_leave(b_date, "A short version.")
store_and_leave(b_date, long_text)
win._request_date(b_date)                            # a new session starts from the long text
settle()
dialog = EntryHistoryDialog(db, b_date, parent=win)
dialog.restoreRequested.connect(win._restore_entry_revision)
short_rev = next(r for r in db.list_entry_revisions(b_date)
                 if (db.get_entry_revision(r.id).body_text or "").strip() == "A short version.")
dialog.list.setCurrentRow([r.id for r in db.list_entry_revisions(b_date)].index(short_rev.id))
dialog._request_restore()
settle()
dialog.close()
check("History restored the short version",
      (db.get_entry(b_date).body_text or "").strip() == "A short version.")
type_at_end(win.editor, ".")
settle(1500)                                          # the real autosave timer
check("typing after a History restore makes no recovery copy", not entry_checkpoints(b_date),
      f"{len(entry_checkpoints(b_date))}")

r_date = "2026-04-02"
store_and_leave(r_date, long_text)
recovered = db.add_recovery_checkpoint("date", r_date, "<p>Recovered short text.</p>", "html",
                                       "Recovered short text.", removed_chars=1000)
win._request_date(r_date)
settle()
win._restore_recovery_checkpoint(recovered.id)
settle()
type_at_end(win.editor, ".")
settle(1500)
check("typing after a Recovery restore makes no new recovery copy",
      len(entry_checkpoints(r_date)) == 1, f"{len(entry_checkpoints(r_date))}")

p_date = "2026-04-03"
second_text = "\n".join(f"Another text {i}. " + "Different words entirely here. " * 3 for i in range(40))
store_and_leave(p_date, long_text)
store_and_leave(p_date, second_text)              # (replacing it is itself a large deletion)
db._conn.execute("DELETE FROM recovery_checkpoints WHERE scope='date' AND ref=?", (p_date,))
db._conn.commit()
win._request_date(p_date)
settle()
target = next(r for r in db.list_entry_revisions(p_date)
              if "Long paragraph 29" in (db.get_entry_revision(r.id).body_text or ""))
win._restore_entry_revision(target.id)
settle()
type_at_end(win.editor, ".")
settle(1500)
check("a restore itself, and typing after it, make no recovery copy", not entry_checkpoints(p_date))
select_all_delete(win.editor)
settle(1500)
cps = entry_checkpoints(p_date)
check("a large deletion of the restored text is still caught, once",
      len(cps) == 1 and "Long paragraph 29" in (db.get_recovery_checkpoint(cps[0].id).body_text or ""),
      f"{len(cps)}")

win.main_tabs.setCurrentWidget(pwid)
settle()


def open_project_afresh(pid):
    pwid.refresh_project_list(select_id=None)
    pwid.refresh_project_list(select_id=pid)
    settle()


def new_project(name, text):
    project = db.create_project(name)
    pwid.refresh_project_list(select_id=project.id)
    settle()
    pwid.editor.text_edit.setPlainText(text)
    pwid.save_now()
    return project.id


pr = new_project("Restore then type (Recovery)", long_text)
pr_cp = db.add_recovery_checkpoint("project", str(pr), "<p>Short project text.</p>", "html",
                                   "Short project text.", removed_chars=1000)
open_project_afresh(pr)
win._restore_recovery_checkpoint(pr_cp.id)
settle()
type_at_end(pwid.editor, ".")
pwid.save_now()
check("a project: typing after a Recovery restore makes no new recovery copy",
      len(project_checkpoints(pr)) == 1, f"{len(project_checkpoints(pr))}")

pv = new_project("Restore then type (Version History)", "Short project version.")
short_version = db.add_version(pv, db.get_project(pv).content_md, label="short",
                               content_format="html", content_text="Short project version.")
pwid.editor.text_edit.setPlainText(long_text)
pwid.save_now()
open_project_afresh(pv)
pwid._restore_version(short_version.id)
settle()
type_at_end(pwid.editor, ".")
pwid.save_now()
check("a project: typing after a Version History restore makes no recovery copy",
      not project_checkpoints(pv), f"{len(project_checkpoints(pv))}")

print("\n--- [13] project restores keep only a real, new state first (1R-F5, cause C) ---")


def versions(pid):
    return db.get_project_versions(pid)


blank = new_project("Blank before Recovery restore", long_text)
open_project_afresh(blank)
select_all_delete(pwid.editor)
pwid.save_now()
check("(setup) blanking the project stored HTML with no text",
      db.get_project(blank).content_md and not db.get_project(blank).content_text.strip())
blank_cp = project_checkpoints(blank)[0]
count = len(versions(blank))
win._restore_recovery_checkpoint(blank_cp.id)
settle()
check("Recovery restore over a blank project keeps no version of the blank state",
      len(versions(blank)) == count, f"{count} -> {len(versions(blank))}")
check("...and restores the text", "Long paragraph 29" in db.get_project(blank).content_text)

blank_v = new_project("Blank before Version History restore", long_text)
long_version = db.add_version(blank_v, db.get_project(blank_v).content_md, label="long",
                              content_format="html", content_text=long_text)
open_project_afresh(blank_v)
select_all_delete(pwid.editor)
pwid.save_now()
count = len(versions(blank_v))
pwid._restore_version(long_version.id)
settle()
check("Version History restore over a blank project keeps no version of the blank state",
      len(versions(blank_v)) == count, f"{count} -> {len(versions(blank_v))}")

dup = new_project("Already a version", "Current text that was saved as a version.")
current = db.get_project(dup)
db.add_version(dup, current.content_md, label="saved by the user",
               content_format=current.content_format, content_text=current.content_text)
dup_cp = db.add_recovery_checkpoint("project", str(dup), "<p>Older text.</p>", "html",
                                    "Older text.", removed_chars=1000)
open_project_afresh(dup)
count = len(versions(dup))
win._restore_recovery_checkpoint(dup_cp.id)
settle()
check("Recovery restore when the current text is already a version adds no duplicate",
      len(versions(dup)) == count, f"{count} -> {len(versions(dup))}")
older = db.add_version(dup, "<p>Oldest text.</p>", label="old", content_format="html",
                       content_text="Oldest text.")
current = db.get_project(dup)
db.add_version(dup, current.content_md, label="saved again",
               content_format=current.content_format, content_text=current.content_text)
count = len(versions(dup))
pwid._restore_version(older.id)
settle()
check("Version History restore when the current text is already a version adds no duplicate",
      len(versions(dup)) == count, f"{count} -> {len(versions(dup))}")

written = new_project("Written, not a version", "Written text that is not a version yet.")
written_cp = db.add_recovery_checkpoint("project", str(written), "<p>Recovered text.</p>", "html",
                                        "Recovered text.", removed_chars=1000)
open_project_afresh(written)
before_versions = versions(written)
win._restore_recovery_checkpoint(written_cp.id)
settle()
added = [v for v in versions(written) if v.id not in {b.id for b in before_versions}]
check("Recovery restore over written text that is not a version keeps exactly one version of it",
      len(added) == 1 and added[0].label == "Before recovery restore"
      and "not a version yet" in added[0].content_text,
      f"{[(v.label, v.content_text[:30]) for v in added]}")
check("...then restores", "Recovered text." in db.get_project(written).content_text)
written_v = new_project("Written, not a version (Version History)", "First words.")
first = db.add_version(written_v, db.get_project(written_v).content_md, label="first",
                       content_format="html", content_text="First words.")
pwid.editor.text_edit.setPlainText("Second words, never saved as a version.")
pwid.save_now()
open_project_afresh(written_v)
before_versions = versions(written_v)
pwid._restore_version(first.id)
settle()
added = [v for v in versions(written_v) if v.id not in {b.id for b in before_versions}]
check("Version History restore over written text that is not a version keeps exactly one version",
      len(added) == 1 and added[0].label == "Before restore"
      and "never saved as a version" in added[0].content_text,
      f"{[(v.label, v.content_text[:30]) for v in added]}")
restored_projects = {written: "Recovered text.", written_v: "First words."}

print("\n--- autosave stays fast on long paragraphs (typing-lag regression) ---")
import random  # noqa: E402
import time  # noqa: E402
random.seed(7)
vocabulary = "the quick brown fox jumps over a lazy dog while writing about spectroscopy".split()
huge = " ".join(random.choice(vocabulary) for _ in range(1500))
lag_date = "2026-03-20"
win.main_tabs.setCurrentWidget(win.daily_splitter)
win._request_date(lag_date)
settle()
win.editor.text_edit.setPlainText(huge)
win._save_current_entry()                       # stored; next visit starts from it
win._request_date("2026-03-21")
win._request_date(lag_date)
settle()
timings = []
for _ in range(4):
    type_text(win.editor, " a few more words")
    win._autosave_timer.stop()
    started = time.perf_counter()
    win._save_current_entry(automatic=True)
    timings.append((time.perf_counter() - started) * 1000)
# Measured: ~1,800 ms per autosave before the fix, ~5 ms after (Linux). The
# bound is loose so a slow CI machine doesn't fail it, and tight enough that
# the old behaviour would.
check("an autosave in a 1,500-word paragraph takes well under 250 ms",
      max(timings) < 250, f"{[round(t) for t in timings]} ms")

print("\n--- [13] recovery copies survive restart ---")
count = len(db.list_recovery_checkpoints())
win.close()
settle()
win = MainWindow()
check("still listed after restart", len(win.db.list_recovery_checkpoints()) == count and count > 0)
check("restored projects keep their restored text after restart",
      all(text in win.db.get_project(pid).content_text for pid, text in restored_projects.items()))
win.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
