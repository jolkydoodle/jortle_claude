"""Round 22 required testing — Parts 76 through 81 of the spec.

Drives the real MainWindow/CompanionPanel/DayCalendarWidget classes, not
mocks of them, and compares RICH STRUCTURE (block formats, char formats,
anchors, list structure) rather than only visible plain text, which is what
Part 76 explicitly asks for.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
data_dir = tempfile.mkdtemp()
isolation.point_at(data_dir)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QTextBlockFormat, QTextCharFormat, QTextCursor, QColor, QTextListFormat,
)

app = QApplication.instance() or QApplication([])

from app import date_links  # noqa: E402
from app.archive import export_archive  # noqa: E402
from app.database import Database  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app import day_calendar  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


# =====================================================================
print("\n=== PART 76: rich-text round trips (structure, not just text) ===")

w = MainWindow()
w.selected_date.set("2026-09-15")
te = w.editor.text_edit
cur = te.textCursor()

# 1 plain + tab + blank paragraph
cur.insertText("Plain paragraph with\ta literal tab.")
cur.insertBlock()
cur.insertBlock()                      # deliberate blank paragraph

# 2 alignment + line spacing + paragraph spacing + first-line indent
bf = QTextBlockFormat()
bf.setAlignment(Qt.AlignHCenter)
bf.setLineHeight(200.0, QTextBlockFormat.ProportionalHeight.value)
bf.setTopMargin(12.0)
bf.setBottomMargin(18.0)
bf.setTextIndent(24.0)
bf.setLeftMargin(36.0)
cur.setBlockFormat(bf)
cur.insertText("Centered, double-spaced, spaced and indented.")
cur.insertBlock()

# 3 every character format at once
cur.setBlockFormat(QTextBlockFormat())
for text, mutate in [
    ("bold ", lambda f: f.setFontWeight(700)),
    ("italic ", lambda f: f.setFontItalic(True)),
    ("under ", lambda f: f.setFontUnderline(True)),
    ("strike ", lambda f: f.setFontStrikeOut(True)),
    ("super ", lambda f: f.setVerticalAlignment(QTextCharFormat.AlignSuperScript)),
    ("sub ", lambda f: f.setVerticalAlignment(QTextCharFormat.AlignSubScript)),
    ("colored ", lambda f: f.setForeground(QColor("#cc3344"))),
    ("highlighted ", lambda f: f.setBackground(QColor("#ffe066"))),
    ("big", lambda f: f.setFontPointSize(22.0)),
]:
    fmt = QTextCharFormat()
    mutate(fmt)
    cur.insertText(text, fmt)
cur.setCharFormat(QTextCharFormat())
cur.insertBlock()

# 4 hyperlink
link_fmt = QTextCharFormat()
link_fmt.setAnchor(True)
link_fmt.setAnchorHref("https://example.com/ref")
cur.insertText("external link", link_fmt)
cur.setCharFormat(QTextCharFormat())
cur.insertBlock()

# 5 internal date link (written as plain text; linkify runs on save)
cur.insertText("Revisit this on September 20, 2026 please.")
cur.insertBlock()

# 6 nested lists
cur.setBlockFormat(QTextBlockFormat())
cur.insertText("outer item")
outer = cur.createList(QTextListFormat.ListDisc)
cur.insertBlock()
cur.insertText("nested item")
nested_fmt = QTextListFormat()
nested_fmt.setStyle(QTextListFormat.ListCircle)
nested_fmt.setIndent(2)
cur.createList(nested_fmt)

w._save_current_entry()

structure_before = []
doc = te.document()
for i in range(doc.blockCount()):
    b = doc.findBlockByNumber(i)
    bfmt = b.blockFormat()
    structure_before.append((
        b.text(), bfmt.alignment() & Qt.AlignHorizontal_Mask, round(bfmt.lineHeight(), 2),
        round(bfmt.topMargin(), 2), round(bfmt.bottomMargin(), 2),
        round(bfmt.textIndent(), 2), round(bfmt.leftMargin(), 2),
        b.textList().format().indent() if b.textList() else -1,
    ))


def structure_now(window):
    d = window.editor.text_edit.document()
    out = []
    for i in range(d.blockCount()):
        b = d.findBlockByNumber(i)
        f = b.blockFormat()
        out.append((
            b.text(), f.alignment() & Qt.AlignHorizontal_Mask, round(f.lineHeight(), 2),
            round(f.topMargin(), 2), round(f.bottomMargin(), 2),
            round(f.textIndent(), 2), round(f.leftMargin(), 2),
            b.textList().format().indent() if b.textList() else -1,
        ))
    return out


def char_props(window):
    """Collect the distinct character formats present, to compare across reloads."""
    d = window.editor.text_edit.document()
    found = {"bold": False, "italic": False, "underline": False, "strike": False,
             "super": False, "sub": False, "color": False, "highlight": False,
             "bigfont": False, "anchor": False, "datelink": None}
    block = d.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                f = frag.charFormat()
                if f.fontWeight() >= 700: found["bold"] = True
                if f.fontItalic(): found["italic"] = True
                if f.fontUnderline() and not f.anchorHref(): found["underline"] = True
                if f.fontStrikeOut(): found["strike"] = True
                if f.verticalAlignment() == QTextCharFormat.AlignSuperScript: found["super"] = True
                if f.verticalAlignment() == QTextCharFormat.AlignSubScript: found["sub"] = True
                if f.foreground().color().name().lower() == "#cc3344": found["color"] = True
                if f.background().color().name().lower() == "#ffe066": found["highlight"] = True
                if f.fontPointSize() >= 21: found["bigfont"] = True
                if f.anchorHref() == "https://example.com/ref": found["anchor"] = True
                if f.anchorHref().startswith("journal://date/"):
                    found["datelink"] = f.anchorHref()
            it += 1
        block = block.next()
    return found


chars_before = char_props(w)
check("blank paragraph present before save", "" in [s[0] for s in structure_before])
check("literal tab present before save", any("\t" in s[0] for s in structure_before))
check("date link applied on save (in the stored row)",  # 4C1a: not in the live editor
      "journal://date/2026-09-20" in w.db.get_entry("2026-09-15").body_md)


def typed_formats(props):
    """Every format the user typed, without the date link: since 4C1a (bug 8,
    4C1a-D3) a date is linked only in the stored copy, so the live editor's
    baseline has no link on the date's characters and a reloaded one does."""
    return {key: value for key, value in props.items() if key != "datelink"}

# --- A. save -> change date -> return
w.selected_date.set("2026-09-16")
w.selected_date.set("2026-09-15")
check("A: structure identical after date change and return", structure_now(w) == structure_before)
check("A: character formats identical", typed_formats(char_props(w)) == typed_formats(chars_before))
check("A: the date is a link once the entry is reloaded", char_props(w)["datelink"] == "journal://date/2026-09-20",
      char_props(w)["datelink"])

# --- C. autosave -> change date -> return
c2 = w.editor.text_edit.textCursor()
c2.movePosition(QTextCursor.End)
c2.insertText(" appended")
w._schedule_autosave()
w._autosave_timer.stop()
w._save_current_entry()           # what the timer would have done
w.selected_date.set("2026-09-17")
w.selected_date.set("2026-09-15")
check("C: appended text survived autosave + navigation",
      w.editor.text_edit.toPlainText().rstrip().endswith("appended"))

expected_after_append = structure_now(w)
w.close()

# --- B. save -> restart application -> return
w2 = MainWindow()
w2.selected_date.set("2026-09-15")
check("B: structure identical after full application restart",
      structure_now(w2) == expected_after_append)
check("B: character formats identical after restart", typed_formats(char_props(w2)) == typed_formats(chars_before))
b = w2.editor.text_edit.document().findBlockByNumber(1)
check("B: blank paragraph is still a real empty paragraph", b.text() == "")
check("B: literal tab survived restart",
      any("\t" in w2.editor.text_edit.document().findBlockByNumber(i).text()
          for i in range(w2.editor.text_edit.document().blockCount())))
nested = [w2.editor.text_edit.document().findBlockByNumber(i)
          for i in range(w2.editor.text_edit.document().blockCount())]
check("B: nested list indent survived restart",
      any(blk.textList() and blk.textList().format().indent() == 2 for blk in nested))

# =====================================================================
print("\n=== PART 77: Reader's Notes ===")

notes = w2.reader_notes
w2.selected_date.set("2026-09-15")
notes.editor.text_edit.setPlainText("Epistemic: relating to knowledge.")
notes.flush()

w2.selected_date.set("2026-09-16")
notes.editor.text_edit.setPlainText("Instrumental convergence: shared sub-goals.")
notes.flush()

w2.selected_date.set("2026-09-15")
check("notes for 9/15 come back", "Epistemic" in notes.editor.plain_text())
w2.selected_date.set("2026-09-16")
check("notes for 9/16 come back", "Instrumental" in notes.editor.plain_text())
check("notes did not drift across dates", "Epistemic" not in notes.editor.plain_text())

check("notes stored in their own table, not the journal entry",
      "Epistemic" not in (w2.db.get_entry("2026-09-15").body_text or ""))

w2.close()
w3 = MainWindow()
w3.selected_date.set("2026-09-15")
check("notes persist across application restart", "Epistemic" in w3.reader_notes.editor.plain_text())

# Reader's Notes now fills the companion area directly, with no tab
# container around it — so "does editing it survive switching away and
# back" is about the workspace tabs, which Part 79 below covers.
date_before_edit = w3.selected_date.value
w3.reader_notes.editor.text_edit.setPlainText("Edited after the AI removal.")
w3.reader_notes.flush(force=True)
check("Reader's Notes still writes through to its own table",
      "Edited after the AI removal."
      in (w3.db.get_reader_notes("2026-09-15").content_text or ""))
check("editing notes does not change selectedDate",
      w3.selected_date.value == date_before_edit)

# =====================================================================
print("\n=== PART 79: one canonical selected date ===")

w3.selected_date.set("2026-09-18")
check("journal followed selectedDate", w3._last_loaded_date == "2026-09-18")
check("day calendar followed selectedDate",
      w3.day_calendar.heading.text().endswith("September 18, 2026"))
check("Reader's Notes followed selectedDate", w3.reader_notes._loaded_ref == "2026-09-18")
check("monthly calendar followed selectedDate",
      w3.calendar_panel.selected_date().toString("yyyy-MM-dd") == "2026-09-18")
check("current_date property agrees", w3.current_date == "2026-09-18")

# internal link navigation + Back
w3.selected_date.set("2026-09-15")
check("Return button hidden before any link is followed", w3.back_button.isHidden())
w3._on_date_link_activated("2026-09-20")
check("link navigation moved the canonical date", w3.selected_date.value == "2026-09-20")
check("link navigation moved Reader's Notes too", w3.reader_notes._loaded_ref == "2026-09-20")
check("Return button now offers the source date",
      not w3.back_button.isHidden() and "September 15, 2026" in w3.back_button.text(),
      w3.back_button.text())
w3._go_back()
check("Back returned to the source date", w3.selected_date.value == "2026-09-15")
check("Return button hides once history is exhausted", w3.back_button.isHidden())

w3._on_date_link_activated("2031-01-02")   # a day with no entry at all
check("link to an empty date works", w3.selected_date.value == "2031-01-02")
check("empty date shows an empty editor", w3.editor.plain_text().strip() == "")
w3._go_back()

# =====================================================================
print("\n=== PART 80: day calendar ===")

w3.selected_date.set("2026-09-21")
dc = w3.day_calendar


class FakeDialog:
    """Stands in for the modal EventDialog so create/edit paths can be
    exercised headlessly — a real .exec() would block forever with nobody
    to click Save."""
    instance_values = {}
    delete = False

    def __init__(self, event, parent=None, is_new=False):
        self.event = event
        self.delete_requested = FakeDialog.delete

    def exec(self):
        return QDialog.Accepted

    def values(self):
        base = {
            "title": "Test event", "date": self.event.date,
            "start_minute": self.event.start_minute,
            "end_minute": self.event.end_minute,
            "all_day": False, "done": False, "notes": "",
            # Events gained an optional colour (None = follow the theme) and
            # a background transparency; the real dialog always supplies both.
            "color": None, "opacity": 100,
        }
        base.update(FakeDialog.instance_values)
        return base


# The editor is opened by the shared event commands (Group 3), so that is
# where the stand-in goes.
from app import event_commands  # noqa: E402
real_dialog = event_commands.EventDialog
event_commands.EventDialog = FakeDialog

FakeDialog.instance_values = {"title": "Morning work"}
dc._on_create_requested("2026-09-21", 9 * 60, 10 * 60)       # simulates a completed drag
events = w3.db.get_events("2026-09-21")
check("drag-to-create persisted an event", len(events) == 1 and events[0].title == "Morning work")
check("created event inherited selectedDate", events[0].date == "2026-09-21")
check("created event snapped span is valid", events[0].end_minute > events[0].start_minute)

# move (duration preserved) via the grid's move request — the path a
# finished drag takes (the real mouse drag is test_round24_drag's job)
ev = events[0]
original_duration = ev.duration
dc._on_move_requested(ev.id, "2026-09-21", 11 * 60)
moved = w3.db.get_events("2026-09-21")[0]
check("move persisted", moved.start_minute == 11 * 60)
check("move preserved duration", moved.duration == original_duration)

# resize
dc._on_resize_requested(moved.id, "bottom", "2026-09-21", 13 * 60)
resized = w3.db.get_events("2026-09-21")[0]
check("resize persisted", resized.end_minute == 13 * 60)

# overlap layout through the real widget
w3.db.create_event("2026-09-21", 11 * 60 + 30, 12 * 60 + 30, title="Overlapping")
dc.refresh()
layouts = dc.timeline._layouts_by_date["2026-09-21"]
check("overlapping events laid out side by side",
      len(layouts) == 2 and all(l.column_count == 2 for l in layouts),
      [(l.event.title, l.column, l.column_count) for l in layouts])

# edit: change the date, event should leave this day's view
FakeDialog.instance_values = {"title": "Moved away", "date": "2026-09-22"}
dc._open_editor(resized)
check("event moved to another date left this day",
      all(e.id != resized.id for e in w3.db.get_events("2026-09-21")))
check("event arrived on the new date",
      any(e.id == resized.id for e in w3.db.get_events("2026-09-22")))

# delete
remaining = w3.db.get_events("2026-09-21")[0]
dc._delete_event(remaining.id)
check("delete removed the event", w3.db.get_events("2026-09-21") == [])

event_commands.EventDialog = real_dialog

# tasks migration
mig_db_dir = tempfile.mkdtemp()
legacy_path = os.path.join(mig_db_dir, "journal.db")
import sqlite3
conn = sqlite3.connect(legacy_path)
conn.executescript("""
CREATE TABLE entries (date TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
  body_md TEXT NOT NULL DEFAULT '', tag TEXT, tag_color TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
  text TEXT NOT NULL, checked INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO tasks (date, text, checked, position, created_at)
  VALUES ('2026-09-01','Buy milk',0,0,'2026-09-01'),
         ('2026-09-01','Call Dad',1,1,'2026-09-01');
""")
conn.commit()
conn.close()

from pathlib import Path  # noqa: E402
mdb = Database(Path(legacy_path))
migrated = mdb.get_events("2026-09-01")
check("every old task became an event", len(migrated) == 2)
check("task text preserved", {e.title for e in migrated} == {"Buy milk", "Call Dad"})
check("task checked state preserved",
      {e.title: e.done for e in migrated} == {"Buy milk": False, "Call Dad": True})
check("migrated tasks are untimed, not given invented times",
      all(e.all_day for e in migrated))
check("original tasks table left intact",
      len(mdb._conn.execute("SELECT * FROM tasks").fetchall()) == 2)
check("pre-migration backup file was written",
      os.path.exists(os.path.join(mig_db_dir, "journal.pre-round22-daycalendar.db")))
mdb.close()
mdb2 = Database(Path(legacy_path))
check("migration does not run twice", len(mdb2.get_events("2026-09-01")) == 2)
mdb2.close()

# =====================================================================
print("\n=== PART 81: static HTML archive ===")

w3.db.create_event("2026-09-15", 14 * 60, 15 * 60, title="Archive test event",
                    notes="with notes")
w3._save_current_entry()
w3.reader_notes.flush()

archive_root = Path(tempfile.mkdtemp()) / "journal_archive"
summary = export_archive(w3.db, archive_root)

check("index.html written", (archive_root / "index.html").exists())
check("styles.css written", (archive_root / "styles.css").exists())
check("entry page written", (archive_root / "entries" / "2026-09-15.html").exists())
check("reader's notes page written", (archive_root / "readers_notes" / "2026-09-15.html").exists())
check("calendar.ics written", (archive_root / "calendar" / "calendar.ics").exists())
check("calendar.csv written", (archive_root / "calendar" / "calendar.csv").exists())
check("settings.json written", (archive_root / "settings.json").exists())
check("manifest written", (archive_root / "backup_manifest.json").exists())
check("no database in the readable archive (backups are for restoring)", not (archive_root / "journal.db").exists())

entry_html = (archive_root / "entries" / "2026-09-15.html").read_text(encoding="utf-8")
check("archive preserves bold", "font-weight:700" in entry_html or "font-weight:600" in entry_html)
check("archive preserves alignment", "text-align:center" in entry_html)
check("archive preserves line spacing", "line-height:200%" in entry_html)
check("archive preserves highlight", "background-color:#ffe066" in entry_html.lower())
check("archive preserves superscript", "vertical-align:super" in entry_html)
check("archive preserves subscript", "vertical-align:sub" in entry_html)
check("archive preserves indentation", "margin-left:36px" in entry_html)
check("archive preserves external link", "https://example.com/ref" in entry_html)
check("internal date link became a relative path",
      "../entries/2026-09-20.html" in entry_html,
      [l for l in entry_html.splitlines() if "2026-09-20" in l][:1])
check("no journal:// scheme leaked into the archive", "journal://" not in entry_html)
check("reader's notes clearly distinguished in the day page",
      'class="readers-notes"' in entry_html and "Edited after the AI removal." in entry_html)
check("archive needs no javascript to read", "<script" not in entry_html.lower())

index_html = (archive_root / "index.html").read_text(encoding="utf-8")
check("index links to the entry", 'href="entries/2026-09-15.html"' in index_html)
check("index groups by year", ">2026<" in index_html)

ics = (archive_root / "calendar" / "calendar.ics").read_text(encoding="utf-8")
check("ics has the event", "Archive test event" in ics and "BEGIN:VEVENT" in ics)
check("ics carries notes", "with notes" in ics)

import json  # noqa: E402
manifest = json.loads((archive_root / "backup_manifest.json").read_text(encoding="utf-8"))
check("manifest reports counts",
      manifest["journal_entry_count"] >= 1 and manifest["readers_notes_count"] >= 1
      and manifest["calendar_event_count"] >= 1)
check("manifest reports schema version", manifest["database_schema_version"] >= 3)

w3.close()

# =====================================================================
print("\n=== PART 78: scroll behavior ===")
w4 = MainWindow()
w4.selected_date.set("2026-10-01")
te4 = w4.editor.text_edit
check("forced caret centering is gone", not hasattr(te4, "_keep_cursor_centered"))
te4.setPlainText("\n".join(f"line {i}" for i in range(200)))
w4.editor.text_edit.resize(600, 400)
sb = te4.verticalScrollBar()
sb.setValue(sb.maximum() // 2)
before_scroll = sb.value()
saved_html_1, _ = w4.editor.save()
check("saving does not move the scroll position", sb.value() == before_scroll,
      f"{before_scroll} -> {sb.value()}")
check("scroll-past-end padding is view-only, not paragraphs",
      te4.document().blockCount() == 200)
# Part 16: the extra space must not alter persisted content or export. Qt
# stores it as a root-frame bottom margin, which toHtml() would otherwise
# serialize into every saved entry, sized to the window height at save time.
import re as _re  # noqa: E402
_m = _re.search(r"-qt-table-type:\s*root[^>]*?margin-bottom:\s*(\d+)px", saved_html_1)
check("scroll padding is NOT serialized into saved content",
      _m is None or _m.group(1) == "0", _m.group(1) if _m else "")
check("padding is still applied in the view", te4.bottom_padding() > 0)
te4.resize(600, 900)
w4.editor.text_edit.resize(600, 900)
saved_html_3, _ = w4.editor.save()
check("saved bytes do not depend on window height", saved_html_1 == saved_html_3)
saved_html_2, _ = w4.editor.save()
check("repeated saves are byte-identical (no drift)", saved_html_1 == saved_html_2)
w4.close()

# =====================================================================
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL ROUND 22 CHECKS PASSED")
