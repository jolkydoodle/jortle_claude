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


for date, reload_, ref in (("2026-02-01", False, "KF-2"), ("2026-02-02", True, "bug 29")):
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
    # Fixed in 4C1a (C1a-1, C1a-2): known failures until then, now plain checks.
    check(f"[{'C1a-1' if reload_ else 'C1a-2'}] {label}", after == after_font and new_versions == 0,
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

# =========================================================== 4C1a
print("\n--- [4C1a] each document keeps its own font; dates are linked in the saved copy only ---")
# Criteria C1a-3…C1a-7, C1a-10 and the zoom check (JORTLE_IMPLEMENTATION_HANDOFF.md,
# "4C1a plan — agreed 2026-10-02"); C1a-1 and C1a-2 are [C13] above.
win.main_tabs.setCurrentWidget(win.daily_splitter)
win.activateWindow()
settle()
pw_module.ask_name = lambda *a, **k: ("Font project", True)


def set_writing_font(family, size):
    win.db.set_setting("font_family", family)
    win.db.set_setting("font_size", str(size))
    win._apply_settings()
    settle()


def body_font(html):
    match = re.search(r"<body style=\"[^\"]*font-family:'([^']*)'; font-size:(\d+(?:\.\d+)?)pt", html or "")
    return (match.group(1), float(match.group(2))) if match else None


def font_spans(html):
    return len(re.findall(r"<span style=\"[^\"]*font-family", (html or "").split("<body", 1)[-1]))


def shown_font(editor):
    font = editor.text_edit.document().defaultFont()
    return font.family(), editor.text_edit.base_point_size()


# C1a-3, bug 35: the Reader's Notes received the journal's HTML (Georgia in its
# <body>) and were saved once; no font may have been baked into the text.
for notes in (date_notes, project_notes):
    check(f"[C1a-3] bug 35: {notes.name} saved the loaded document with no font baked into its text",
          font_spans(notes.first_save[0]) == font_spans(journal_html),
          f"font spans {font_spans(notes.first_save[0])} (the HTML it loaded had {font_spans(journal_html)})")

# C1a-4, bug 8: a new date typed and saved.
E1, E2 = "2026-04-01", "2026-04-02"
win._request_date(E1)
settle()
edit = win.editor.text_edit
edit.setFocus()
QTest.keyClick(edit, Qt.Key_End, Qt.ControlModifier)
QTest.keyClicks(edit, "met on 2026-04-07 at noon")
settle()
document = edit.document()
live_before, steps_before = document.toHtml(), document.availableUndoSteps()
ctrl_s(win, edit)
check("[C1a-4] bug 8: Ctrl+S leaves the live document unchanged (content and undo steps)",
      document.toHtml() == live_before and document.availableUndoSteps() == steps_before,
      f"undo steps {steps_before} -> {document.availableUndoSteps()}")
check("[C1a-4] ...and the stored row holds the date link",
      "journal://date/2026-04-07" in (entry_row(win, E1) or ("",))[0])
date_cursor = QTextCursor(document)
date_cursor.setPosition(document.toPlainText().index("2026-04-07") + 4)
QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, edit.cursorRect(date_cursor).center())
settle()
check("[C1a-4] ...and a plain click on the new date navigates to it", win.current_date == "2026-04-07",
      win.current_date)

# 4C1a/AM-11: a date with letters directly before or after it is not a date,
# the same rule as for saved links: a click on it does not navigate.
E7 = "2026-04-11"
win._request_date(E7)
settle()
edit = win.editor.text_edit
edit.setFocus()
QTest.keyClicks(edit, "x2026-04-09 and 2026-04-10x")
settle()
ctrl_s(win, edit)
for attached, iso in (("x2026-04-09", "2026-04-09"), ("2026-04-10x", "2026-04-10")):
    if win.current_date != E7:                  # a wrong navigation by the probe before
        win._request_date(E7)
        settle()
        edit = win.editor.text_edit
    probe = QTextCursor(edit.document())
    probe.setPosition(edit.toPlainText().index(attached) + attached.index(iso) + 4)
    QTest.mouseClick(edit.viewport(), Qt.LeftButton, Qt.NoModifier, edit.cursorRect(probe).center())
    settle()
    check(f"[C1a-4] a click on {attached!r} (letters attached) does not navigate, and it is not saved as a link",
          win.current_date == E7 and f"journal://date/{iso}" not in entry_row(win, E7)[0], win.current_date)
win._request_date(E2)
settle()
edit = win.editor.text_edit
edit.setFocus()
QTest.keyClicks(edit, "seen on 2026-04-08 again")
settle()
typed = edit.toPlainText()
ctrl_s(win, edit)
QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
settle()
check("[C1a-4] ...and after Ctrl+S one Ctrl+Z undoes the last typing (not a linking step)",
      edit.toPlainText() != typed, repr(edit.toPlainText()))
QTest.keyClick(edit, Qt.Key_Y, Qt.ControlModifier)
settle()
win.editor.mark_clean()                         # back to the stored text; nothing left to save

# C1a-5: a new, empty entry and a new project start in the current writing font.
set_writing_font("Arial", 20)
E3 = "2026-04-03"
win._request_date(E3)
settle()
win.editor.text_edit.setFocus()
QTest.keyClicks(win.editor.text_edit, "new words")
ctrl_s(win, win.editor.text_edit)
check("[C1a-5] a new entry starts in the current writing font (Arial 20), with no font span",
      body_font(entry_row(win, E3)[0]) == ("Arial", 20.0) and font_spans(entry_row(win, E3)[0]) == 0,
      f"{body_font(entry_row(win, E3)[0])}, spans {font_spans(entry_row(win, E3)[0])}")
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
next(b for b in win.projects_widget.findChildren(QPushButton) if b.text() == "New Project").click()
settle()
font_project = win.projects_widget.current_project_id
win.projects_widget.editor.text_edit.setFocus()
QTest.keyClicks(win.projects_widget.editor.text_edit, "project words")
ctrl_s(win, win.projects_widget.editor.text_edit)
check("[C1a-5] a new project starts in the current writing font (Arial 20), with no font span",
      body_font(project_row(win, font_project)[0]) == ("Arial", 20.0) and font_spans(project_row(win, font_project)[0]) == 0,
      f"{body_font(project_row(win, font_project)[0])}, spans {font_spans(project_row(win, font_project)[0])}")

# C1a-10: an empty, unwritten document open when the setting changes takes it.
win.main_tabs.setCurrentWidget(win.daily_splitter)
E4 = "2026-04-04"
win._request_date(E4)
settle()
set_writing_font("Courier New", 15)
check("[C1a-10] an empty, unwritten Daily Jort open when the setting changes takes the new font",
      shown_font(win.editor) == ("Courier New", 15.0) and not win.editor.is_dirty() and entry_row(win, E4) is None,
      shown_font(win.editor))
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
next(b for b in win.projects_widget.findChildren(QPushButton) if b.text() == "New Project").click()
settle()
empty_project = win.projects_widget.current_project_id
set_writing_font("Arial", 20)
check("[C1a-10] an empty, unwritten project open when the setting changes takes the new font",
      shown_font(win.projects_widget.editor) == ("Arial", 20.0) and not win.projects_widget.editor.is_dirty()
      and not (project_row(win, empty_project)[0] or "").strip(), shown_font(win.projects_widget.editor))

# C1a-6: an existing entry (D, saved in Georgia 13) under the Arial 20 setting.
journal.show(win)
check("[C1a-6] an existing entry keeps its own font on screen (Georgia 13, setting Arial 20)",
      shown_font(win.editor) == ("Georgia", 13.0), shown_font(win.editor))
state = journal.state(win)
journal.save(win)
check("[C1a-6] ...an unedited Ctrl+S writes nothing", journal.state(win) == state)
spans_before = font_spans(journal.stored(win)[0])
edit = win.editor.text_edit
edit.setFocus()
QTest.keyClick(edit, Qt.Key_End, Qt.ControlModifier)
QTest.keyClicks(edit, "Z")
journal.save(win)
check("[C1a-6] ...and an edit adds no font spans to untouched text",
      font_spans(journal.stored(win)[0]) == spans_before and body_font(journal.stored(win)[0]) == ("Georgia", 13.0),
      f"{spans_before} -> {font_spans(journal.stored(win)[0])}, body {body_font(journal.stored(win)[0])}")

# C1a-7: rows saved under two different writing fonts are never rewritten by
# loading, by setting changes, or by an unedited Ctrl+S.
E5, E6 = "2026-04-05", "2026-04-06"
for date, (family, size) in ((E5, ("Georgia", 13)), (E6, ("Times New Roman", 16))):
    set_writing_font(family, size)
    win._request_date(date)
    settle()
    win.editor.text_edit.setFocus()
    QTest.keyClicks(win.editor.text_edit, f"written in {family}")
    ctrl_s(win, win.editor.text_edit)
win._request_date(E1)                          # leave the last typed entry: its "left the entry"
settle()                                       # version is the normal end of that editing session
left = [r[0] for r in win.db._conn.execute(
    "SELECT reason FROM entry_revisions WHERE date IN (?, ?) ORDER BY id", (E5, E6))]
check("[C1a-7] (setup) the two typed entries each got only their 'left the entry' version",
      left == ["left the entry", "left the entry"], left)
set_writing_font("Arial", 20)
seeded = {d: (entry_row(win, d), entry_versions(win, d)) for d in (E5, E6)}
for date in (E5, E6, E5, E6):
    win._request_date(date)
    settle()
    ctrl_s(win, win.editor.text_edit)
set_writing_font("Courier New", 15)
set_writing_font("Arial", 20)
now_seeded = {d: (entry_row(win, d), entry_versions(win, d)) for d in (E5, E6)}
check("[C1a-7] rows saved in two writing fonts: loading, unedited Ctrl+S and setting changes rewrite nothing",
      now_seeded == seeded
      and body_font(seeded[E5][0][0]) == ("Georgia", 13.0) and body_font(seeded[E6][0][0]) == ("Times New Roman", 16.0),
      f"bodies {body_font(seeded[E5][0][0])}, {body_font(seeded[E6][0][0])}; changed: "
      f"{[(d, 'row' if now_seeded[d][0] != seeded[d][0] else 'versions', seeded[d][1], now_seeded[d][1]) for d in (E5, E6) if now_seeded[d] != seeded[d]]}")

# The user's note for D1: the editor zoom survives date changes, project
# changes and Reader's Notes loads (each loaded document's own font is applied
# without resetting zoom).
win._request_date(E5)
settle()
win.editor.zoom_spin.setValue(130)
settle()
kept = []
for date in (E6, D, E5):
    win._request_date(date)
    settle()
    kept.append(win.editor.text_edit.zoom_percent())
check("[zoom] the Daily Jorts zoom survives date changes (documents in different fonts)", kept == [130] * 3, kept)
notes_kit = sd.Kit(win.reader_notes.editor, PHOTO.parent, settle)
notes_kit.ctrl_wheel(3)
kept = []
for date in (D, E5, D):
    win._request_date(date)
    settle()
    kept.append(win.reader_notes.editor.text_edit.zoom_percent())
check("[zoom] the Reader's Notes zoom survives note loads", kept == [130] * 3, kept)
notes_kit.ctrl_wheel(-3)
win.editor.zoom_spin.setValue(100)
win.main_tabs.setCurrentWidget(win.projects_widget)
project.show(win)
win.projects_widget.editor.zoom_spin.setValue(130)
settle()
kept = []
for pid in (font_project, project.pid, empty_project, project.pid):
    win.projects_widget.refresh_project_list(select_id=pid)
    settle()
    kept.append(win.projects_widget.editor.text_edit.zoom_percent())
check("[zoom] the Projects zoom survives project changes", kept == [130] * 4, kept)
win.projects_widget.editor.zoom_spin.setValue(100)
win.main_tabs.setCurrentWidget(win.daily_splitter)
set_writing_font("Georgia", 13)


# =========================================================== 4C1a-F1
print("\n--- [4C1a-F1] settings changes and the Reader's Notes; blank stored documents; the toolbar after a load ---")
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QFontComboBox  # noqa: E402
from app.rich_editor import stored_document_font  # noqa: E402

# F1a-1: after a writing-font, UI-font and theme change, both Reader's Notes
# editors (holding content, at a non-100% zoom) stay unmodified, gain no undo
# step and keep their zoom. Daily Jorts is in front, so the date notes are on
# screen: there the UI-font change adds bug 38's padding undo steps.
project.show(win)                               # loads the project notes (content)
journal.show(win)                               # loads the date notes for D (content)
date_editor, project_editor = win.reader_notes.editor, win.projects_widget.reader_notes.editor
for editor, notches in ((date_editor, 2), (project_editor, -2)):
    sd.Kit(editor, PHOTO.parent, settle).ctrl_wheel(notches)
zooms = {date_editor: date_editor.text_edit.zoom_percent(), project_editor: project_editor.text_edit.zoom_percent()}
check("[F1a-1] (setup) both Reader's Notes hold content, are unmodified and zoomed",
      all(e.text_edit.toPlainText().strip() and not e.is_dirty() for e in zooms) and
      sorted(zooms.values()) == [80, 120], zooms)
for change, apply in (
        ("writing font", lambda: win.db.set_setting("font_family", "Arial")),
        ("UI font", lambda: win.db.set_setting("ui_font_size", "18")),
        ("theme", lambda: win.db.set_setting("color_scheme", scheme_to_json(PRESETS["Dark"])))):
    before = {e: e.text_edit.document().availableUndoSteps() for e in zooms}
    apply()
    win._apply_settings()
    settle(200)
    for editor, name in ((date_editor, "date Reader's Notes (on screen)"), (project_editor, "project Reader's Notes")):
        check(f"[F1a-1] {change} change: {name} is not marked modified", not editor.is_dirty())
        check(f"[F1a-1] {change} change: {name} keeps its zoom ({zooms[editor]}%)",
              editor.text_edit.zoom_percent() == zooms[editor], editor.text_edit.zoom_percent())
        undo_kept = editor.text_edit.document().availableUndoSteps() == before[editor]
        label = f"[F1a-1] {change} change: {name} gains no undo step"
        detail = f"{before[editor]} -> {editor.text_edit.document().availableUndoSteps()}"
        if change == "UI font" and editor is date_editor:
            known_failing(label, undo_kept, "bug 38 → 4C2", detail)
        else:
            check(label, undo_kept, detail)
win.db.set_setting("font_family", "Georgia")
win.db._conn.execute("DELETE FROM settings WHERE key='ui_font_size'")
win.db._conn.commit()
win.db.set_setting("color_scheme", scheme_to_json(PRESETS["Light"]))
win._apply_settings()
settle(200)
for editor, notches in ((date_editor, -2), (project_editor, 2)):
    sd.Kit(editor, PHOTO.parent, settle).ctrl_wheel(notches)

# F1a-3: a stored blank document takes the current setting when loaded; a
# written one, including an image-only one, keeps its stored font.
set_writing_font("Georgia", 13)
win.main_tabs.setCurrentWidget(win.projects_widget)
settle()
next(b for b in win.projects_widget.findChildren(QPushButton) if b.text() == "New Project").click()
settle()
blank_project = win.projects_widget.current_project_id
edit = win.projects_widget.editor.text_edit
edit.setFocus()
QTest.keyClicks(edit, "gone")
edit.selectAll()
QTest.keyClick(edit, Qt.Key_Delete)
ctrl_s(win, edit)
stored_blank = project_row(win, blank_project)[0]
check("[F1a-3] (setup) the blank project's row is an empty HTML page recording Georgia 13",
      "<body" in stored_blank and body_font(stored_blank) == ("Georgia", 13.0)
      and not project_row(win, blank_project)[0].split("<body", 1)[1].count("gone"), body_font(stored_blank))
win.projects_widget.refresh_project_list(select_id=project.pid)
settle()
set_writing_font("Verdana", 18)
win.projects_widget.refresh_project_list(select_id=blank_project)
settle()
check("[F1a-3] reopened after the setting changed to Verdana 18, the blank project is shown in Verdana 18",
      shown_font(win.projects_widget.editor) == ("Verdana", 18.0), shown_font(win.projects_widget.editor))
edit = win.projects_widget.editor.text_edit
edit.setFocus()
QTest.keyClicks(edit, "fresh words")
ctrl_s(win, edit)
check("[F1a-3] ...and the first typed words are stored in Verdana 18, with no font span",
      body_font(project_row(win, blank_project)[0]) == ("Verdana", 18.0) and font_spans(project_row(win, blank_project)[0]) == 0,
      f"{body_font(project_row(win, blank_project)[0])}, spans {font_spans(project_row(win, blank_project)[0])}")
win.main_tabs.setCurrentWidget(win.daily_splitter)
set_writing_font("Georgia", 13)
E8 = "2026-04-12"
win._request_date(E8)
settle()
image_kit = sd.Kit(win.editor, PHOTO.parent, settle)
image_kit.with_photo(PHOTO)
ctrl_s(win, win.editor.text_edit)
image_row = entry_row(win, E8)
check("[F1a-3] (setup) an image-only entry is stored in Georgia 13 and counts as written",
      image_row is not None and body_font(image_row[0]) == ("Georgia", 13.0) and "<img" in image_row[0])
win._request_date(E1)
settle()
set_writing_font("Arial", 20)
win._request_date(E8)
settle()
state = (entry_row(win, E8), entry_versions(win, E8))
ctrl_s(win, win.editor.text_edit)
check("[F1a-3] the image-only entry keeps its stored font under Arial 20, and an unedited Ctrl+S writes nothing",
      shown_font(win.editor) == ("Georgia", 13.0) and (entry_row(win, E8), entry_versions(win, E8)) == state,
      shown_font(win.editor))
set_writing_font("Georgia", 13)


# F1a-4: right after a load, with no caret movement, the font and size boxes
# show the loaded document's own font, in all four editors.
def combo_shows(family):
    """What a font box shows for `family` (an uninstalled family resolves to
    a fallback, the same way in the editor's own box)."""
    probe = QFontComboBox()
    probe.setCurrentFont(QFont(family))
    shown = probe.currentFont().family()
    probe.deleteLater()
    return shown


def boxes(editor):
    return editor.family_combo.currentFont().family(), editor.size_spin.value()


def expected_boxes(family, size):
    return combo_shows(family), int(round(size))


win.main_tabs.setCurrentWidget(win.daily_splitter)
results = []
for date in (E6, E5, E6):                       # Times New Roman 16, Georgia 13, Times New Roman 16
    win._request_date(date)
    settle()
    family, size = stored_document_font(entry_row(win, date)[0])
    results.append((date, boxes(win.editor), expected_boxes(family, size)))
check("[F1a-4] Daily Jorts: right after a load the font and size boxes show the entry's own font",
      all(shown == expected for _d, shown, expected in results) and results[0][2] != results[1][2], results)
win.main_tabs.setCurrentWidget(win.projects_widget)
results = []
for pid in (font_project, project.pid, font_project):       # Arial 20, Georgia 13, Arial 20
    win.projects_widget.refresh_project_list(select_id=pid)
    settle()
    family, size = stored_document_font(project_row(win, pid)[0])
    results.append((pid, boxes(win.projects_widget.editor), expected_boxes(family, size)))
check("[F1a-4] Projects: right after a load the font and size boxes show the project's own font",
      all(shown == expected for _p, shown, expected in results) and results[0][2] != results[1][2], results)
# Reader's Notes: one note saved in the notes' own font, one holding the
# journal's HTML (Georgia 13 in its <body>, written there as at [C2]).
win.main_tabs.setCurrentWidget(win.daily_splitter)
E9 = "2026-04-13"
win._request_date(E9)
settle()
notes_edit = win.reader_notes.editor.text_edit
notes_edit.setFocus()
QTest.keyClicks(notes_edit, "a plain note")
ctrl_s(win, notes_edit)
win.db.save_notes("date", E5, entry_row(win, E5)[0], "html", "written in Georgia")
win.db.save_notes("project", str(font_project), project_row(win, font_project)[0], "html", "project words")
results = []
for date in (E9, E5, E9):
    win._request_date(date)
    settle()
    family, size = stored_document_font(notes_row(win, "date", date)[0])
    results.append((date, boxes(win.reader_notes.editor), expected_boxes(family, size)))
check("[F1a-4] date Reader's Notes: right after a load the font and size boxes show the note's own font",
      all(shown == expected for _d, shown, expected in results) and results[0][2] != results[1][2], results)
win.main_tabs.setCurrentWidget(win.projects_widget)
results = []
for pid in (project.pid, font_project, project.pid):
    win.projects_widget.refresh_project_list(select_id=pid)
    settle()
    family, size = stored_document_font(notes_row(win, "project", pid)[0])
    results.append((pid, boxes(win.projects_widget.reader_notes.editor), expected_boxes(family, size)))
check("[F1a-4] project Reader's Notes: right after a load the font and size boxes show the note's own font",
      all(shown == expected for _p, shown, expected in results) and results[0][2] != results[1][2], results)
win.main_tabs.setCurrentWidget(win.daily_splitter)
settle()


# F1a-8 (4C1a-F1/AM-1): a load starts typing from the loaded document, not
# from the previous document's caret. First a caret in text of another font.
def caret_in_text_of(date):
    win._request_date(date)
    settle()
    win.editor.text_edit.setFocus()
    QTest.keyClick(win.editor.text_edit, Qt.Key_End, Qt.ControlModifier)
    settle()


win.main_tabs.setCurrentWidget(win.daily_splitter)
set_writing_font("Georgia", 13)
# The caret is left inside a run with an explicit font of its own (the stress
# document's "familyone" word), so a carried-over insertion format would show.
journal.show(win)
family_kit = sd.Kit(win.editor, PHOTO.parent, settle)
family_kit.place(family_kit.index_of("alpha familyone omega"), len("alpha familyo"))
check("[F1a-8] (setup) the caret sits in text with an explicit font family",
      bool(win.editor.text_edit.textCursor().charFormat().fontFamilies()),
      win.editor.text_edit.textCursor().charFormat().fontFamilies())
E10 = "2026-04-14"
win._request_date(E10)
settle()
win.editor.text_edit.setFocus()
QTest.keyClicks(win.editor.text_edit, "new entry words")
ctrl_s(win, win.editor.text_edit)
check("[F1a-8] after a caret in another font's text, text typed into a new entry stores no font span",
      font_spans(entry_row(win, E10)[0]) == 0 and body_font(entry_row(win, E10)[0]) == ("Georgia", 13.0),
      f"spans {font_spans(entry_row(win, E10)[0])}, body {body_font(entry_row(win, E10)[0])}")

win.main_tabs.setCurrentWidget(win.projects_widget)
project.show(win)                               # the stress project, its "familyone" word
family_kit = sd.Kit(win.projects_widget.editor, PHOTO.parent, settle)
family_kit.place(family_kit.index_of("alpha familyone omega"), len("alpha familyo"))
check("[F1a-8] (setup) the project caret sits in text with an explicit font family",
      bool(win.projects_widget.editor.text_edit.textCursor().charFormat().fontFamilies()))
next(b for b in win.projects_widget.findChildren(QPushButton) if b.text() == "New Project").click()
settle()
fresh_project = win.projects_widget.current_project_id
win.projects_widget.editor.text_edit.setFocus()
QTest.keyClicks(win.projects_widget.editor.text_edit, "new project words")
ctrl_s(win, win.projects_widget.editor.text_edit)
check("[F1a-8] after a caret in another font's text, text typed into a new project stores no font span",
      font_spans(project_row(win, fresh_project)[0]) == 0, font_spans(project_row(win, fresh_project)[0]))

win.main_tabs.setCurrentWidget(win.daily_splitter)
win._request_date(E5)                           # its note holds Georgia 13 in its <body>
settle()
notes_edit = win.reader_notes.editor.text_edit
notes_edit.setFocus()
QTest.keyClick(notes_edit, Qt.Key_End, Qt.ControlModifier)
E11 = "2026-04-15"
win._request_date(E11)
settle()
notes_edit = win.reader_notes.editor.text_edit
notes_edit.setFocus()
QTest.keyClicks(notes_edit, "new note words")
ctrl_s(win, notes_edit)
check("[F1a-8] after a Georgia note, text typed into a new Reader's Note stores no font span",
      font_spans(notes_row(win, "date", E11)[0]) == 0, font_spans(notes_row(win, "date", E11)[0]))

# A written document: typing at the start after loading keeps its formatting.
E12, E13 = "2026-04-16", "2026-04-17"
win._request_date(E12)
settle()
start_kit = sd.Kit(win.editor, PHOTO.parent, settle)
start_kit.type("boldstart rest of line")
start_kit.select_words("boldstart rest of line", "boldstart")
start_kit.key(Qt.Key_B, Qt.ControlModifier)
ctrl_s(win, win.editor.text_edit)
win._request_date(E13)
settle()
start_kit = sd.Kit(win.editor, PHOTO.parent, settle)
start_kit.type("Heading start")
start_kit.place(0)
start_kit.combo(win.editor.heading_combo, 2)
ctrl_s(win, win.editor.text_edit)
for date, typed, want, what in ((E12, "XX", {"weight": lambda w: w >= 600}, "bold"),
                                (E13, "YY", {"weight": lambda w: w >= 600, "size": 16.0}, "the Heading 2")):
    caret_in_text_of(E6)                        # coming from a document in another font
    win._request_date(date)
    settle()
    QTest.keyClicks(win.editor.text_edit, typed)   # at the caret the load left (no movement)
    ctrl_s(win, win.editor.text_edit)
    stored = entry_row(win, date)[0]
    fp = fp_of(stored)
    run_ok = sd.Prop(what, "run", typed, want).found(fp)
    heading_ok = what != "the Heading 2" or sd.Prop(what, "block", typed, {"heading": 2}).found(fp)
    check(f"[F1a-8] typing at the start of a paragraph that starts with {what} continues {what} "
          "and stores no font span", run_ok and heading_ok and font_spans(stored) == 0,
          f"run {[r for r in fp.runs if typed in r.text]}, spans {font_spans(stored)}")
set_writing_font("Georgia", 13)

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
