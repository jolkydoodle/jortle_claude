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
prior = len(win.editor.text_edit.toPlainText())
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
check("the emptied state was kept as a project version first",
      any(v.label == "Before recovery restore" for v in db.get_project_versions(project.id)))
dialog.close()

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
win.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
