"""Round 25 — Reader's Notes for Projects (Part 33) + AI visibility (Part 34).

Tests the INTERACTION, not the widgets in isolation:
  * notes typed against a project are saved under (project, id) and come back
    when that project is re-selected;
  * switching projects flushes the outgoing project's notes (no loss, no
    bleed onto the newly-selected project);
  * date notes and project notes never see each other, and the archive export
    still exports only date notes;
  * with AI off, Projects shows Reader's Notes and nothing AI at all;
    turning it on adds exactly one tab and the status strip, live.
"""
import os
import pathlib
import sys
import tempfile

import isolation
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp(prefix="jortle-r25-")
isolation.point_at(tmp)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.database import Database  # noqa: E402
from app.projects_widget import ProjectsWidget  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)



def type_into(editor, text):
    """Replaces the editor's text the way a user does: select all, type."""
    edit = editor.text_edit
    edit.selectAll()
    edit.insertPlainText(text)


db = Database()

print("\n--- Part 33: Reader's Notes follows the selected project ---")
w = ProjectsWidget(db)
p1 = db.create_project("Novel")
p2 = db.create_project("Thesis")
w.refresh_project_list(select_id=p1.id)

check("a project is open", w.current_project_id == p1.id)
check("notes widget points at that project", w.reader_notes._loaded_ref == str(p1.id))
check("notes editor is enabled once a project is open", w.reader_notes.editor.isEnabled())

# An EDIT, as typing makes one. (setPlainText is not something a user can do,
# and Qt does not flag it as a change; since Group 1, switching subject writes
# only notes the user actually edited, so the test types like a user.)
type_into(w.reader_notes.editor, "Marguerite is the sister.")
w.refresh_project_list(select_id=p2.id)
check("switching project flushed the outgoing notes",
      (db.get_notes("project", str(p1.id)) or None) is not None
      and "Marguerite" in db.get_notes("project", str(p1.id)).content_text)
check("new project starts with empty notes",
      w.reader_notes.editor.plain_text().strip() == "")

type_into(w.reader_notes.editor, "Chapter 4 needs a source.")
w.refresh_project_list(select_id=p1.id)
check("returning to the first project restores ITS notes",
      "Marguerite" in w.reader_notes.editor.plain_text())
check("the second project's notes were saved separately",
      "Chapter 4" in (db.get_notes("project", str(p2.id)) or type("x", (), {"content_text": ""})).content_text)

print("\n--- Part 33: one notes system, two association keys ---")
db.save_reader_notes("2026-09-16", "<p>day notes</p>", content_text="day notes")
check("date notes unaffected by project notes",
      db.get_reader_notes("2026-09-16").content_text == "day notes")
check("project notes are not reachable as a date",
      db.get_reader_notes(str(p1.id)) is None)
check("date-marked calendar only counts date notes",
      db.reader_notes_dates() == {"2026-09-16"})
check("archive export only sees date notes",
      [n.date for n in db.all_reader_notes()] == ["2026-09-16"])
check("both live in the same table",
      db._conn.execute(
          "SELECT COUNT(DISTINCT scope) FROM reader_notes_scoped").fetchone()[0] == 2)

print("\n--- Part 33: no project selected ---")
w.refresh_project_list()
w.project_list.setCurrentItem(None)
w._load_project(None)
check("notes editor disabled with nothing selected", not w.reader_notes.editor.isEnabled())
w.reader_notes.editor.text_edit.setPlainText("stray typing")
w.reader_notes.flush()
check("a stray edit with no project is not persisted anywhere",
      db._conn.execute(
          "SELECT COUNT(*) FROM reader_notes_scoped WHERE content_text LIKE '%stray%'"
      ).fetchone()[0] == 0)

print("\n--- Reader's Notes is the whole companion area now ---")
w.refresh_project_list(select_id=p1.id)
check("Projects shows Reader's Notes directly, with no tab container",
      w.reader_notes.parent() is not None
      and not any(type(c).__name__ == "CompanionPanel"
                  for c in w.findChildren(object)))
check("its content is the project's own notes",
      w.reader_notes._loaded_ref == str(p1.id)
      and "Marguerite" in w.reader_notes.editor.plain_text())

print("\n--- no AI left in visible strings, and no AI modules ---")
import re  # noqa: E402
offenders = []
for path in Path(str(pathlib.Path(__file__).resolve().parent.parent / "app")).glob("*.py"):
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue  # a comment explaining the rename is not a visible string
        if re.search(r'"[^"\n]*\bAI\b[^"\n]*"', line):
            offenders.append(f"{path.name}:{lineno}")
check("no user-visible AI strings remain: " + "; ".join(offenders), not offenders)

gone = ["local_llm", "model_state", "gpu_probe", "reflection", "reflection_panel",
        "reflection_pipeline", "ai_status_widget", "experimental_settings_dialog",
        "companion_panel", "features"]
app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
still_there = [m for m in gone if (app_dir / f"{m}.py").exists()]
check("every AI module is gone from the package: " + ", ".join(still_there), not still_there)

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
