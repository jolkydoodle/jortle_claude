"""Round 26 — journal-entry indicators (Part 31, plus Parts 19-22, 27-28).

Walks the spec's 22-step script with both autosave settings, and checks the
distinction the whole fix rests on:

    a database row exists   ≠   a meaningful journal entry exists

Typing is done through the real editor and saving through the real save path,
because the bug was in the relationship between those two and the calendar —
not in any one of them alone.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r26i-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r26i-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402
from app.saving import has_meaningful_text, set_autosave_enabled  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def settle(times=4):
    for _ in range(times):
        app.processEvents()


win = MainWindow()
win.show()
settle()

marked = win.calendar_panel.calendar._entry_dates
other = win.calendar_panel.calendar._other_content_dates


def indicator(date: str) -> bool:
    """What the month grid would actually paint for this day."""
    return date in win.calendar_panel.calendar._entry_dates


def type_text(text: str):
    cursor = win.editor.text_edit.textCursor()
    cursor.insertText(text)
    settle()


def clear_text():
    cursor = win.editor.text_edit.textCursor()
    cursor.select(QTextCursor.Document)
    cursor.removeSelectedText()
    settle()


# ============================== Part 20: the predicate itself
print("\n--- Part 20: what counts as text ---")
for value, expected in [
    ("", False), ("   ", False), ("\t\t", False), ("\n\n\n", False),
    (" ", False), ("​", False), ("   \t\n ", False),
    ("x", True), ("  hello  ", True), (" word", True),
]:
    check(f"has_meaningful_text({value!r}) is {expected}",
          has_meaningful_text(value) is expected)
check("the rich-text markup for an empty entry is NOT empty as a string",
      len("<p></p>") > 0 and not has_meaningful_text(""))

# ============================== steps 1-6: an untouched date
print("\n--- Steps 1-6: open a date, type nothing ---")
set_autosave_enabled(win.db, True)
empty_date = "2026-06-01"
win.selected_date.set(empty_date)
settle()
check("selecting a date does not create an indicator", not indicator(empty_date))

# let the autosave timer genuinely fire, with nothing typed
win._autosave_tick()
settle()
check("an autosave with nothing typed does not create one either",
      not indicator(empty_date))
row = win.db.get_entry(empty_date)
check(f"even if a database row now exists (row present: {row is not None})",
      not win.db.has_journal_entry(empty_date))

win.selected_date.set("2026-06-02")
settle()
win.selected_date.set(empty_date)
settle()
check("navigating away and back leaves it empty", not indicator(empty_date))

# ============================== steps 7-12: manual save, both directions
print("\n--- Steps 7-12: with autosave OFF, Ctrl+S drives the indicator ---")
set_autosave_enabled(win.db, False)
manual_date = "2026-06-10"
win.selected_date.set(manual_date)
settle()

type_text("a real journal entry")
check("typing alone does not persist anything", not win.db.has_journal_entry(manual_date))
check("...and does not mark the calendar", not indicator(manual_date))
check("but the document is dirty", win.editor.is_dirty())

win._save_active_workspace()
settle()
check("Ctrl+S persists it", win.db.has_journal_entry(manual_date))
check("and the indicator appears immediately", indicator(manual_date))
check("the document is no longer dirty", not win.editor.is_dirty())

clear_text()
win._save_active_workspace()
settle()
check("saving an emptied entry is a valid save, not a failure",
      not win.db.has_journal_entry(manual_date))
check("and the indicator disappears immediately", not indicator(manual_date))
check("the emptied save really was written",
      (win.db.get_entry(manual_date).body_text or "").strip() == "")

# ============================== steps 13-19: autosave, both directions
print("\n--- Steps 13-19: with autosave ON ---")
set_autosave_enabled(win.db, True)
auto_date = "2026-06-20"
win.selected_date.set(auto_date)
settle()
type_text("written with autosave on")
win._autosave_tick()          # what the timer does when it fires
settle()
check("autosave persists it", win.db.has_journal_entry(auto_date))
check("and the indicator appears", indicator(auto_date))

clear_text()
win._autosave_tick()
settle()
check("emptying and autosaving removes the entry", not win.db.has_journal_entry(auto_date))
check("and the indicator goes with it", not indicator(auto_date))

# ============================== steps 20-22: whitespace only
print("\n--- Steps 20-22: whitespace-only is still empty ---")
blank_date = "2026-06-25"
win.selected_date.set(blank_date)
settle()
type_text("   \t\n  \n ")
win._save_active_workspace()
settle()
check("spaces, tabs and line breaks do not make an entry",
      not win.db.has_journal_entry(blank_date))
check("and no indicator appears", not indicator(blank_date))

# ============================== the indicator is about the JOURNAL
print("\n--- Part 20: an event is not a journal entry ---")
event_date = "2026-06-28"
win.db.create_event(date=event_date, start_minute=600, end_minute=660, title="A meeting")
win._refresh_calendar_marks()
settle()
check("a day with only an event gets no journal-entry indicator",
      not indicator(event_date))
check("...but is still marked as having something on it",
      event_date in win.calendar_panel.calendar._other_content_dates)

note_date = "2026-06-29"
win.db.save_notes("date", note_date, "<p>just a note</p>", content_text="just a note")
win._refresh_calendar_marks()
settle()
check("a day with only Reader's Notes gets no journal-entry indicator",
      not indicator(note_date))
check("...but is marked as having something",
      note_date in win.calendar_panel.calendar._other_content_dates)

# ============================== the two views agree
print("\n--- one definition, used by both calendars ---")
win.selected_date.set("2026-07-01")
settle()
type_text("shown in both calendars")
win._save_active_workspace()
settle()
win.week_calendar.navigator.calendar.setCurrentPage(2026, 7)
win.week_calendar.refresh_marks()
settle()
check("the Weekly Calendar's navigator marks the same day",
      "2026-07-01" in win.week_calendar.navigator.calendar._entry_dates)
check("the two calendars use the same query",
      win.db.dates_with_entries("2026-07-01", "2026-07-01") == {"2026-07-01"})

print("\n--- Part 21: no restart or month change needed ---")
check("the month currently shown was refreshed in place",
      win.calendar_panel.calendar.monthShown() == 7
      and "2026-07-01" in win.calendar_panel.calendar._entry_dates)

# ============================== the SQL and the Python agree
print("\n--- the database's definition matches the widget's ---")
for text, label in [("  ", "spaces"), ("\t\n", "tab and newline"),
                    (" ", "a non-breaking space"), ("real", "actual text")]:
    date = f"2026-08-{abs(hash(label)) % 25 + 1:02d}"
    win.db.upsert_entry(date, body_md="<p>x</p>", body_format="html", body_text=text)
    sql_says = date in win.db.dates_with_entries(date, date)
    python_says = has_meaningful_text(text)
    check(f"{label}: SQL and Python agree ({sql_says})", sql_says == python_says)

win.close()
settle()
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
