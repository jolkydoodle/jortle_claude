"""Group 4, batch 4-0 — the rich-text round trip (Master Spec §§8, 14, 75).

The stress document (tests/stress_document.py) is built with the editor's own
controls in Daily Jorts, in a Project, and (as stored HTML plus a section
built with the compact toolbar) in both kinds of Reader's Notes, then taken
through save/reload, date and tab navigation, view changes (zoom, UI font,
theme), restart, backup/restore and the readable archive. Two comparisons
(4-0/D4): after the first save every section's expected properties must be
in the stored HTML (fingerprint), and every later save must store exactly the
same bytes.

Also here: the legacy Markdown row (D11, 4-0/C12) and bug 29 as two strict
known failures (4-0/C13, AM-2, AM-3).

FP-9: real key events for typing and shortcuts (items 1, 5), the database
read after closing and in a new window (2), repeated actions (3); teeth are
shown with mutants in the batch report (5, 7). Criteria: 4-0/C1–C14
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4-0 plan — agreed 2026-10-01").
"""
import os
import pathlib
import re
import sys
import time

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-roundtrip-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton  # noqa: E402

app = QApplication.instance() or QApplication([])

import stress_document as sd  # noqa: E402
from app import backup as backup_module  # noqa: E402
from app import backup_dialog  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.paths import get_attachments_dir, get_data_dir  # noqa: E402
from app.rich_editor import RichEditor  # noqa: E402
from app.saving import SAVE  # noqa: E402
from app.theme import PRESETS, scheme_to_json  # noqa: E402

STARTED = time.monotonic()
print("IMPORTED app FROM", mw.__file__)
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"

failures = []
known = []
known_refs = set()


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def known_failing(label, cond, ref, detail="", sink=None):
    """A strict known failure (4-0/D5): reported, not counted — until it
    passes, which fails the run so the mark is removed."""
    sink = failures if sink is None else sink
    if cond:
        mark = "  FAIL  " if sink is failures else "  (self-test: would FAIL)  "
        print(f"{mark}{label}  [known-failing check now PASSES ({ref}): remove the known-failing mark]")
        sink.append(label)
    else:
        print(f"  KNOWN FAIL ({ref})  {label}" + (f"  [{detail}]" if detail else ""))
        known.append(label)
        if sink is failures:
            known_refs.add(ref)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


# ------------------------------------------------------------ dialog answers
asked = []


def fake_ask(_parent, what):
    asked.append(what)
    return SAVE


mw.ask_unsaved = fake_ask
pw_module.ask_unsaved = fake_ask
names = iter(["Stress project", "Other project"])
pw_module.ask_name = lambda *a, **k: (next(names), True)
messages = []
QMessageBox.information = staticmethod(lambda *a, **k: messages.append(("info", a[1:3])) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: messages.append(("critical", a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: messages.append(("warning", a[1:3])) or QMessageBox.Ok)

PHOTO = sd.make_photo(ROOT / "photo-source")
JOURNAL = sd.context("journal", PHOTO)
PROJECT = sd.context("project", PHOTO)
D = "2026-02-03"          # the stress entry
OTHER = "2026-02-20"      # somewhere else to go


def new_window():
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.activateWindow()
    settle(300)
    return win


def ctrl_s(win, widget):
    # Ctrl+S is a window shortcut: it reaches the window only while it is the
    # active one, as it is for a user typing in it. A progress or message
    # window (Back Up Now, restore) leaves it inactive under offscreen Qt.
    win.activateWindow()
    settle()
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key_S, Qt.ControlModifier)
    settle()


# ------------------------------------------------------------ stored rows
def entry_row(win, date=D):
    return win.db._conn.execute(
        "SELECT body_md, body_format, updated_at FROM entries WHERE date=?", (date,)).fetchone()


def entry_versions(win, date=D):
    return win.db._conn.execute("SELECT COUNT(*) FROM entry_revisions WHERE date=?", (date,)).fetchone()[0]


def project_row(win, pid):
    return win.db._conn.execute(
        "SELECT content_md, content_format, updated_at FROM projects WHERE id=?", (pid,)).fetchone()


def project_versions(win, pid):
    return win.db._conn.execute("SELECT COUNT(*) FROM project_versions WHERE project_id=?", (pid,)).fetchone()[0]


def notes_row(win, scope, ref):
    return win.db._conn.execute(
        "SELECT content, content_format, updated_at FROM reader_notes_scoped WHERE scope=? AND ref=?",
        (scope, str(ref))).fetchone()


def fp_of(html, fmt="html"):
    """The fingerprint of a stored document, loaded the way the app loads it."""
    editor = RichEditor()
    editor.load(html, fmt)
    fp = sd.fingerprint(editor.text_edit.document())
    editor.deleteLater()
    return fp


def fp_live(editor):
    return sd.fingerprint(editor.text_edit.document())


def first_missing(fp, ctx, extra=(), archive=False):
    missing = sd.missing(fp, ctx, archive=archive)
    missing += [f"{name}: {p.label}" for name, p in extra if (p.in_archive or not archive) and not p.found(fp)]
    return missing


def known_losses(where, fp, ctx, archive=False):
    """One strict known failure per recorded bug that loses properties here."""
    for ref, (labels, found) in sd.known(fp, ctx, archive=archive).items():
        known_failing(f"{where}: {', '.join(labels)}", found, ref)


# The changes the first reload + unedited Ctrl+S makes today, one per recorded
# bug (4-0/AM-13). Each turns the first save's HTML into what the next save
# stores; anything not explained by them is a new change and fails.
FRACTIONAL_MARGIN_RE = re.compile(r"(margin-(?:top|bottom|left|right):)(\d+\.\d+)px")
H3_SPAN_RE = re.compile(r'(<h3[^>]*><span style=")( font-weight:700;")')
KNOWN_RESAVE_CHANGES = (
    (sd.BUG_33, "fractional paragraph spacing is read back rounded (12.5px → 13px)",
     lambda html: FRACTIONAL_MARGIN_RE.sub(lambda m: f"{m.group(1)}{int(float(m.group(2)) + 0.5)}px", html)),
    (sd.BUG_37, "Heading 3 at the default size gains font-size:large",
     lambda html: H3_SPAN_RE.sub(r"\1 font-size:large;\2", html)),
)


def triggers_in(editor):
    """The known reload changes whose content the live editor holds (4-0-F1,
    AM-3): read from its document before the first save."""
    writing_size = float(win.db.get_setting("font_size", "13"))
    document = editor.text_edit.document()
    return {ref for ref, (_what, test) in sd.RELOAD_TRIGGERS.items() if test(document, writing_size)}


def resave_checks(name, first, now, triggers):
    """The first save against what the next reload + unedited Ctrl+S stored.

    `triggers`: the known reload changes whose content the document held
    before its first save (asserted present or absent in [C2]). For each,
    the known failure is evaluated whatever the first save looks like: if a
    change to the app stops the rewrite — even by changing the first save
    itself — the check passes and the run fails (4-0-F1, AM-13)."""
    expected = first
    now_lines = set(now.splitlines())
    for ref, what, change in KNOWN_RESAVE_CHANGES:
        if ref not in triggers:
            continue
        changed = change(first)
        # It occurred when the first save held what it rewrites and the lines
        # it produces are the stored ones.
        occurs = changed != first and all(line in now_lines for line in _changed_lines(first, changed))
        known_failing(f"{name}: a reload and an unedited Ctrl+S keep the first save's bytes "
                      f"(not: {what})", not occurs, ref)
        if occurs:
            expected = change(expected)
    check(f"{name}: ...and nothing else changed (only the known changes above)", expected == now,
          _diff(expected, now))


def _changed_lines(before, after):
    return [line for line in after.splitlines() if line not in before.splitlines()]


def _diff(a, b):
    import difflib
    return [line[:220] for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))][:6]


# ============================================================ the workspaces
class Doc:
    """One stress document in one workspace: how to reach it, save it, read it."""

    def __init__(self, name, ctx, extra=()):
        self.name, self.ctx, self.extra = name, ctx, list(extra)

    def stored(self, win):
        raise NotImplementedError

    def versions(self, win):
        return 0

    def state(self, win):
        """(stored row, version count): what "nothing was written" compares."""
        return self.stored(win), self.versions(win)


class Journal(Doc):
    def editor(self, win):
        return win.editor

    def show(self, win):
        win.main_tabs.setCurrentWidget(win.daily_splitter)
        win._request_date(D)
        settle()

    def leave(self, win):
        win._request_date(OTHER)
        settle()

    def reload(self, win):
        self.leave(win)
        self.show(win)

    def save(self, win):
        ctrl_s(win, win.editor.text_edit)

    def stored(self, win):
        return entry_row(win)

    def versions(self, win):
        return entry_versions(win)


class Project(Doc):
    pid = None
    other = None

    def editor(self, win):
        return win.projects_widget.editor

    def show(self, win):
        win.main_tabs.setCurrentWidget(win.projects_widget)
        if win.projects_widget.current_project_id != self.pid:
            win.projects_widget.refresh_project_list(select_id=self.pid)
        settle()

    def leave(self, win):
        win.projects_widget.refresh_project_list(select_id=self.other)
        settle()

    def reload(self, win):
        self.leave(win)
        self.show(win)

    def save(self, win):
        ctrl_s(win, win.projects_widget.editor.text_edit)

    def stored(self, win):
        return project_row(win, self.pid)

    def versions(self, win):
        return project_versions(win, self.pid)


class DateNotes(Doc):
    def editor(self, win):
        return win.reader_notes.editor

    def show(self, win):
        Journal.show(self, win)

    def leave(self, win):
        Journal.leave(self, win)

    def reload(self, win):
        self.leave(win)
        self.show(win)

    def save(self, win):
        ctrl_s(win, win.reader_notes.editor.text_edit)

    def stored(self, win):
        return notes_row(win, "date", D)


class ProjectNotes(Doc):
    project = None

    def editor(self, win):
        return win.projects_widget.reader_notes.editor

    def show(self, win):
        self.project.show(win)

    def leave(self, win):
        self.project.leave(win)

    def reload(self, win):
        self.leave(win)
        self.show(win)

    def save(self, win):
        ctrl_s(win, win.projects_widget.reader_notes.editor.text_edit)

    def stored(self, win):
        return notes_row(win, "project", self.project.pid)


journal = Journal("Daily Jorts", JOURNAL)
project = Project("Projects", PROJECT)
NOTES = dict(JOURNAL, compact_appended=True)
date_notes = DateNotes("date Reader's Notes", NOTES, sd.compact_props())
project_notes = ProjectNotes("project Reader's Notes", NOTES, sd.compact_props())
project_notes.project = project
DOCS = (journal, project, date_notes, project_notes)

# =========================================================== 4-0/C14 first
print("\n--- [C14] the known-failing mechanism is strict ---")
sink, before = [], len(known)
known_failing("self-test: a known-failing check that fails", False, "self-test", sink=sink)
check("a known-failing check that fails is reported as KNOWN FAIL and not counted",
      sink == [] and len(known) == before + 1)
known_failing("self-test: a known-failing check that passes", True, "self-test", sink=sink)
check("a known-failing check that passes fails the run", sink == ["self-test: a known-failing check that passes"])
known.pop()

# =========================================================== build the documents
print("\n--- building the stress document in Daily Jorts, a Project and Reader's Notes ---")
win = new_window()
check("autosave starts off (fresh install)", win.db.get_setting("autosave_enabled", "0") in ("0", "false", "False"))
journal.show(win)
sd.build(win.editor, JOURNAL, settle)
journal.live_triggers = triggers_in(win.editor)
journal.save(win)
journal.first_save = entry_row(win)

win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
new_button = next(b for b in win.projects_widget.findChildren(QPushButton) if b.text() == "New Project")
new_button.click()
settle()
project.pid = win.projects_widget.current_project_id
new_button.click()
settle()
project.other = win.projects_widget.current_project_id
check("two projects were created through New Project", project.pid and project.other and project.pid != project.other)
project.show(win)
sd.build(win.projects_widget.editor, PROJECT, settle)
project.live_triggers = triggers_in(win.projects_widget.editor)
project.save(win)
project.first_save = project_row(win, project.pid)

# Reader's Notes: the full document arrives as HTML saved by the full editor
# (the compact editor cannot create most formats; 4-0/D3), then the compact
# section is added with the compact toolbar and saved with Ctrl+S.
journal_html = entry_row(win)[0]
win.db.save_notes("date", D, journal_html, "html", win.db.get_entry(D).body_text)
win.db.save_notes("project", str(project.pid), journal_html, "html", win.db.get_entry(D).body_text)
for notes in (date_notes, project_notes):
    notes.reload(win)
    sd.build_compact(notes.editor(win), settle)
    notes.live_triggers = triggers_in(notes.editor(win))
    notes.save(win)
    notes.first_save = notes.stored(win)

# =========================================================== C1, C2
print("\n--- [C1] every section is checked ---")
sections_without = [s.name for s in sd.SECTIONS if not s.expect(JOURNAL) or not s.expect(PROJECT)]
check("every section lists at least one expected property", not sections_without, sections_without)
check(f"the stress document has {len(sd.SECTIONS)} sections and "
      f"{len(sd.all_props(JOURNAL))} properties", len(sd.SECTIONS) >= 13 and len(sd.all_props(JOURNAL)) >= 50)

print("\n--- [C2/C9/C10] the first save holds every expected property ---")
for doc in DOCS:
    row = doc.first_save                    # as the first save stored it (Ctrl+S elsewhere saves it again)
    check(f"{doc.name}: stored as HTML", row is not None and row[1] == "html")
    missing = first_missing(fp_of(row[0]), doc.ctx, doc.extra)
    check(f"{doc.name}: every expected property is in the stored HTML", not missing, missing)
    known_losses(f"{doc.name}: stored", fp_of(row[0]), doc.ctx)
    # A later Ctrl+S (it saves the workspace's document and its Reader's
    # Notes together) has already reloaded and saved this document again.
    now = doc.stored(win)[0]
    # 4-0-F1/AM-1: Daily Jorts and Projects are built holding the content
    # each known reload change acts on; the Reader's Notes receive it through
    # a reload, so they hold neither. Asserted both ways, never skipped.
    triggers = doc.live_triggers
    for ref, (what, _test) in sd.RELOAD_TRIGGERS.items():
        if doc.ctx.get("compact_appended"):
            check(f"{doc.name}: before its first save it does not hold {what} ({ref} trigger absent, "
                  "as expected for content that arrives through a reload)", ref not in triggers)
        else:
            check(f"{doc.name}: before its first save it holds {what} ({ref} trigger present)",
                  ref in triggers)
    resave_checks(doc.name, row[0], now, triggers)
    doc.baseline = now
    doc.baseline_fp = fp_of(now)

# =========================================================== C3
print("\n--- [C3] save then reload, three times: the same bytes ---")
for doc in DOCS:
    doc.show(win)
    blocks = doc.editor(win).text_edit.document().blockCount()
    results = []
    for _ in range(3):
        doc.reload(win)
        state = doc.state(win)
        doc.save(win)                           # unedited Ctrl+S
        results.append((doc.state(win) == state, doc.stored(win)[0] == doc.baseline,
                        fp_live(doc.editor(win)) == doc.baseline_fp,
                        doc.editor(win).text_edit.document().blockCount() == blocks))
    check(f"{doc.name}: 3 reloads keep the bytes, fingerprint and paragraph count",
          all(r[1] and r[2] and r[3] for r in results), results)
    check(f"{doc.name}: an unedited Ctrl+S writes nothing (bytes, updated_at, versions)",
          all(r[0] for r in results), results)

# =========================================================== C4, C5
print("\n--- [C4] date / project navigation, autosave off and on ---")
for autosave in (False, True):
    win.set_autosave(autosave)
    settle()
    for doc in DOCS:
        doc.show(win)
        state = doc.state(win)
        same_fp, clean = True, True
        for _ in range(2):
            doc.leave(win)
            settle(1700 if autosave else 0)          # past the autosave timers
            doc.show(win)
            settle(1700 if autosave else 0)
            same_fp = same_fp and fp_live(doc.editor(win)) == doc.baseline_fp
            clean = clean and not doc.editor(win).is_dirty()
        check(f"{doc.name}, autosave {'on' if autosave else 'off'}: twice away and back keeps the "
              "fingerprint and writes nothing", same_fp and clean and doc.state(win) == state)
win.set_autosave(False)
settle()

print("\n--- [C5] visiting every tab and coming back ---")
tabs = [win.main_tabs.widget(i) for i in range(win.main_tabs.count())]
check("four tabs to visit", len(tabs) == 4)
for doc in DOCS:
    doc.show(win)
    state = doc.state(win)
    home = win.main_tabs.currentWidget()
    ok = True
    for _ in range(2):
        for tab in tabs:
            win.main_tabs.setCurrentWidget(tab)
            settle()
        win.main_tabs.setCurrentWidget(home)
        settle()
        ok = ok and fp_live(doc.editor(win)) == doc.baseline_fp and not doc.editor(win).is_dirty()
    check(f"{doc.name}: two tours of the tabs keep the fingerprint and write nothing",
          ok and doc.state(win) == state)


# =========================================================== C11
def fp3_checks(label, doc):
    """(1) not modified, (2) an unedited Ctrl+S writes nothing, (3) type one
    character, Ctrl+S, Ctrl+Z removes it. Then the character is saved away."""
    doc.show(win)
    editor = doc.editor(win)
    edit = editor.text_edit
    check(f"{doc.name}, {label}: (1) the document is not marked modified", not editor.is_dirty())
    state = doc.state(win)
    doc.save(win)
    check(f"{doc.name}, {label}: (2) an unedited Ctrl+S writes nothing", doc.state(win) == state)
    text_before = edit.toPlainText()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_End, Qt.ControlModifier)
    QTest.keyClicks(edit, "Q")
    settle()
    doc.save(win)
    saved_q = doc.stored(win)[0]
    QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
    settle()
    check(f"{doc.name}, {label}: (3) after typing and Ctrl+S, Ctrl+Z removes the typed character",
          "Q" in fp_of(saved_q).blocks[-1].text and edit.toPlainText() == text_before)
    doc.save(win)                               # store the document without the Q again
    check(f"{doc.name}, {label}: ...and saving after the undo stores the original bytes again",
          doc.stored(win)[0] == doc.baseline)


print("\n--- [C11] view state never reaches the stored document (FP-3) ---")
for doc in DOCS:
    doc.show(win)
    editor = doc.editor(win)
    if hasattr(editor, "zoom_spin"):
        editor.zoom_spin.setValue(130)
        settle()
        check(f"{doc.name}: the Zoom % box set +3 steps", editor.text_edit.zoom_percent() == 130)
        fp3_checks("zoom +3 (Zoom % box)", doc)
        editor.zoom_spin.setValue(100)
        settle()
    kit = sd.Kit(editor, PHOTO.parent, settle)
    kit.ctrl_wheel(3)
    check(f"{doc.name}: Ctrl+wheel zoomed +3 steps", editor.text_edit.zoom_percent() == 130,
          editor.text_edit.zoom_percent())
    fp3_checks("zoom +3 (Ctrl+wheel)", doc)
    kit.ctrl_wheel(-3)

win.db.set_setting("ui_font_size", "20")
win._apply_settings()
settle(200)
for doc in DOCS:
    fp3_checks("UI font 20", doc)
win.db.set_setting("ui_font_size", str(app.font().pointSize()))
win.db._conn.execute("DELETE FROM settings WHERE key='ui_font_size'")
win.db._conn.commit()
win._apply_settings()
settle()

for scheme in ("Dark", "Light"):
    win.db.set_setting("color_scheme", scheme_to_json(PRESETS[scheme]))
    win._apply_settings()
    settle(200)
    for doc in DOCS:
        fp3_checks(f"theme {scheme}", doc)

# =========================================================== C6
print("\n--- [C6] restart ---")
before_close = {doc.name: doc.state(win) for doc in DOCS}
win.close()
settle()
win = new_window()
settle(500)                                     # past deferred layout timers
for doc in DOCS:
    check(f"{doc.name}: closing the window changed nothing stored", doc.state(win) == before_close[doc.name])
    doc.show(win)
    check(f"{doc.name}: after restart the document reloads with the same fingerprint",
          fp_live(doc.editor(win)) == doc.baseline_fp)
    state = doc.state(win)
    doc.save(win)
    check(f"{doc.name}: after restart an unedited Ctrl+S writes nothing", doc.state(win) == state)

# =========================================================== C7
print("\n--- [C7] backup and restore through the File menu routes ---")
win._back_up_now()
settle()
backups = sorted(pathlib.Path(backup_module.backup_dir()).glob("*.zip"), key=lambda p: p.stat().st_mtime)
check("File → Back Up Now made a backup", backups, messages[-3:])
photo_files = list(pathlib.Path(get_attachments_dir()).rglob("*.png"))
check("the photos are in the attachments folder", len(photo_files) >= 2, photo_files)
win.activateWindow()
settle()
check("Ctrl+S reaches the window after Back Up Now (guard: the edits below are really saved)",
      QApplication.activeWindow() is win, QApplication.activeWindow())
for doc in DOCS:                                # edit everything after the backup
    doc.show(win)
    edit = doc.editor(win).text_edit
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_End, Qt.ControlModifier)
    QTest.keyClicks(edit, "after the backup")
    settle()
    doc.save(win)
check("the documents differ from the backup before restoring",
      all(doc.stored(win)[0] != doc.baseline for doc in DOCS),
      [doc.name for doc in DOCS if doc.stored(win)[0] == doc.baseline])
for photo in photo_files:
    photo.unlink()
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(backups[-1]), ""))
backup_dialog.confirm_restore = lambda *a, **k: (True, False)
messages.clear()
win._import_backup()
settle(300)
check("the restore reported no error", not [m for m in messages if m[0] == "critical"], messages)
for doc in DOCS:
    check(f"{doc.name}: the restore brings back the exact stored bytes", doc.stored(win)[0] == doc.baseline)
    doc.show(win)
    check(f"{doc.name}: the restored document reloads with the same fingerprint",
          fp_live(doc.editor(win)) == doc.baseline_fp)
check("the photo files are back after the restore", all(p.exists() for p in photo_files),
      [p for p in photo_files if not p.exists()])

# =========================================================== C8
print("\n--- [C8] File → Export Readable Archive ---")
archive_root = ROOT / "archive-out"
archive_root.mkdir()
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(archive_root))
messages.clear()
win._export_archive()
settle()
pages = list(archive_root.rglob("index.html"))
check("the archive was written", pages, messages)
site = pages[0].parent if pages else archive_root


def page_fp(path, section_marker=None):
    text = path.read_text(encoding="utf-8")
    if section_marker:
        start = text.find(section_marker)
        text = text[start:] if start >= 0 else ""
    document = QTextDocument()
    document.setHtml(text)
    return sd.fingerprint(document), text


entry_page = site / "entries" / f"{D}.html"
notes_page = site / "readers_notes" / f"{D}.html"
project_page = site / "projects" / f"{project.pid}.html"
for label, path, ctx, extra, marker in (
        ("entry page", entry_page, JOURNAL, (), None),
        ("project page", project_page, PROJECT, (), None),
        ("date Reader's Notes page", notes_page, NOTES, sd.compact_props(), None),
        ("project Reader's Notes section", project_page, NOTES, sd.compact_props(), "readers-notes")):
    if not path.exists():
        check(f"{label}: exists", False, path)
        continue
    fp, text = page_fp(path, marker)
    missing = first_missing(fp, ctx, extra, archive=True)
    check(f"{label}: every section's text and HTML properties are in the archive", not missing, missing)
    known_losses(label, fp, ctx, archive=True)

# =========================================================== C12 (D11)
print("\n--- [C12] a legacy Markdown row (D11) ---")
MARKDOWN = ("# Old heading\n\nSome *emphasised* words.\n\nAfter a blank line\n\n"
            "- first item\n- second item\n")
LEGACY_VIEW, LEGACY_EDIT = "2025-11-05", "2025-11-06"
for date in (LEGACY_VIEW, LEGACY_EDIT):
    win.db._conn.execute(
        "INSERT INTO entries(date, body_md, body_format, body_text, created_at, updated_at) "
        "VALUES(?, ?, 'markdown', ?, '2025-11-05T08:00:00', '2025-11-05T08:00:00')",
        (date, MARKDOWN, "Old heading Some emphasised words. After a blank line first item second item"))
win.db._conn.commit()
md_fp = fp_of(MARKDOWN, "markdown")


def legacy_shape(fp):
    """Heading, emphasis, list and paragraph structure as the Markdown row shows them."""
    return ([(b.text, b.heading, b.list_style) for b in fp.blocks],
            sorted(r.text for r in fp.runs if r.italic))


check("the Markdown row loads with a heading, emphasis and a list",
      any(b.heading == 1 for b in md_fp.blocks) and any(r.italic and "emphasised" in r.text for r in md_fp.runs)
      and sum(1 for b in md_fp.blocks if b.list_style == "disc") == 2, legacy_shape(md_fp))
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()
for autosave in (False, True):
    win.set_autosave(autosave)
    settle()
    start = (entry_row(win, LEGACY_VIEW), entry_versions(win, LEGACY_VIEW))
    shown = True
    for _ in range(2):
        win._request_date(LEGACY_VIEW)
        settle(1700 if autosave else 0)
        shown = shown and legacy_shape(fp_live(win.editor)) == legacy_shape(md_fp)
        win._request_date(OTHER)
        settle(1700 if autosave else 0)
    check(f"autosave {'on' if autosave else 'off'}: viewing it twice never rewrites it, "
          "and the editor shows its heading, emphasis and list",
          shown and (entry_row(win, LEGACY_VIEW), entry_versions(win, LEGACY_VIEW)) == start)
win.set_autosave(False)
settle()


def no_font_spans(html):
    if "<body" not in html:
        return False
    body = html.split("<body", 1)[1].split(">", 1)[1]
    return "font-family" not in body


# Unedited Ctrl+S: the intended upgrade (4-0/AM-1).
win.main_tabs.setCurrentWidget(win.daily_splitter)
win._request_date(LEGACY_VIEW)
settle()
ctrl_s(win, win.editor.text_edit)
row = entry_row(win, LEGACY_VIEW)
kept = win.db._conn.execute(
    "SELECT body, body_format FROM entry_revisions WHERE date=? ORDER BY id", (LEGACY_VIEW,)).fetchall()
kept = [tuple(r) for r in kept]
check("unedited Ctrl+S upgrades the row to HTML", row[1] == "html", row[1])
check("...keeps the old Markdown as a version", (MARKDOWN, "markdown") in kept, kept)
check("...keeps the heading, emphasis, list and paragraph structure the Markdown row showed",
      legacy_shape(fp_of(row[0])) == legacy_shape(md_fp), (legacy_shape(fp_of(row[0])), legacy_shape(md_fp)))
check("...and bakes no writing font into the text (FP-3)", no_font_spans(row[0]),
      re.findall(r"<span[^>]*font-family[^>]*>", row[0])[:3])

# Edit + Ctrl+S.
win._request_date(LEGACY_EDIT)
settle()
edit = win.editor.text_edit
edit.setFocus()
QTest.keyClick(edit, Qt.Key_End, Qt.ControlModifier)
QTest.keyClicks(edit, " plus an edit")
settle()
ctrl_s(win, edit)
row = entry_row(win, LEGACY_EDIT)
kept = win.db._conn.execute(
    "SELECT body, body_format FROM entry_revisions WHERE date=? ORDER BY id", (LEGACY_EDIT,)).fetchall()
kept = [tuple(r) for r in kept]
edited_fp = fp_of(row[0])
shape, md_shape = legacy_shape(edited_fp), legacy_shape(md_fp)
expected_blocks = [b if b[0] != "second item" else ("second item plus an edit",) + b[1:] for b in md_shape[0]]
check("an edit + Ctrl+S stores HTML with every original structure plus the edit",
      row[1] == "html" and shape[0] == expected_blocks and shape[1] == md_shape[1], (shape, md_shape))
check("...and keeps the old Markdown as a version", (MARKDOWN, "markdown") in kept, kept)
upgraded = row[0]
stable = []
for _ in range(3):
    win._request_date(OTHER)
    win._request_date(LEGACY_EDIT)
    settle()
    ctrl_s(win, win.editor.text_edit)
    stable.append(entry_row(win, LEGACY_EDIT)[0] == upgraded)
check("later save/reload cycles store identical bytes", all(stable), stable)

# =========================================================== C13 (AM-2, AM-3)
print("\n--- [C13] bug 29: the 1R-A audit's sequence (p10_font_diff.py), on isolated data ---")
# Copied step for step from the audit's probe: per date, with autosave off,
# reset the writing font to Georgia 13, load the date, type two lines and
# press Ctrl+S; Ctrl+S again with nothing changed (the control); change the
# writing font to Arial 20; for the second date reload the entry (load
# another date, then this one); Ctrl+S.
win.db.set_setting("autosave_enabled", "0")
win._apply_settings()
win.main_tabs.setCurrentWidget(win.daily_splitter)
win.activateWindow()                            # the audit's window was the active one
settle()


def audit_typing(widget, text):
    widget.setFocus()
    for index, part in enumerate(text.split("\n")):
        if index:
            QTest.keyClick(widget, Qt.Key_Return)
        if part:
            QTest.keyClicks(widget, part)
    settle()


def audit_ctrl_s():
    QTest.keyClick(win.focusWidget() or win, Qt.Key_S, Qt.ControlModifier)
    settle()


for date, reload_, ref in (("2026-02-01", False, "KF-2 → 4C1"), ("2026-02-02", True, "bug 29 → 4C1")):
    win.db.set_setting("font_size", "13")
    win.db.set_setting("font_family", "Georgia")
    win._apply_settings()
    settle()
    win._load_date(date)
    settle()
    audit_typing(win.editor.text_edit, "some words\nsecond line")
    audit_ctrl_s()
    settle(1100)
    r0 = entry_row(win, date)
    win.editor.text_edit.setFocus()
    audit_ctrl_s()
    settle(1100)
    control = entry_row(win, date)
    check(f"{date}: control — the first Ctrl+S stored the typed text", r0 is not None and "some words" in r0[0],
          r0)
    check(f"{date}: control — Ctrl+S with no font change and no edit changes nothing",
          r0 is not None and control == r0)
    win.db.set_setting("font_size", "20")
    win.db.set_setting("font_family", "Arial")
    win._apply_settings()
    settle()
    if reload_:
        win._load_date("2026-02-10")
        settle()
        win._load_date(date)
        settle()
    after_font = entry_row(win, date)
    check(f"{date}: the font change alone writes nothing", after_font == control)
    versions_before_save = entry_versions(win, date)
    win.editor.text_edit.setFocus()
    audit_ctrl_s()
    after = entry_row(win, date)
    new_versions = entry_versions(win, date) - versions_before_save
    stored_after = after[0] if after else ""          # no row: the control checks above have failed
    body_spans = re.findall(r"<span[^>]*font-family:'Georgia'[^>]*>", stored_after.split("<body", 1)[-1])
    label = (("bug 29: writing-font change, reload, unedited Ctrl+S — the stored HTML stays byte-identical "
              "and no version is recorded") if reload_ else
             ("KF-2: writing-font change, unedited Ctrl+S on the open entry — the stored HTML stays "
              "byte-identical and no version is recorded"))
    known_failing(label, after == after_font and new_versions == 0, ref,
                  f"row changed: {after != after_font}; new versions from this Ctrl+S: {new_versions}; "
                  f"old font baked into spans: {len(body_spans)}")
    if reload_:
        reasons = [r[0] for r in win.db._conn.execute(
            "SELECT reason FROM entry_revisions WHERE date=? ORDER BY id", (date,))]
        print(f"        versions of {date} over the whole sequence: {reasons} "
              "(the audit's 0 → 1: recorded when the reload left the entry typed in this session)")
win.db.set_setting("font_size", "13")
win.db.set_setting("font_family", "Georgia")
win._apply_settings()
settle()

# =========================================================== end
win.editor.mark_clean()
win.close()
settle()
check("no Save / Discard / Cancel question was asked during the run", not asked, asked)
elapsed = time.monotonic() - STARTED
print(f"\nrun time {elapsed:.0f} s")
summary = f"ALL PASS ({len(known)} known failures: {', '.join(sorted(known_refs))})"
print("\n" + (summary if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
