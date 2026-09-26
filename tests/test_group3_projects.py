"""Group 3, stage 3E — nested Project folders in the Projects workspace.

Acceptance criteria 46-51 of the Group 3 plan are marked [46]..[51]. Starts
from a database in the pre-Group 3 shape (flat categories), drives the real
ProjectsWidget (its tree, context commands and drop handling), and checks
the database, the archive and a restarted window. The only stand-ins are the
three modal questions (a name, a destination, an information message).
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-g3-proj-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import archive, projects_widget  # noqa: E402
from app.database import Database  # noqa: E402
from app.paths import get_db_path  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=40):
    app.processEvents()
    QTest.qWait(ms)
    app.processEvents()


names, destinations, notices = [], [], []
projects_widget.ask_name = lambda parent, title, label, text="": (names.pop(0), True) if names else ("", False)
projects_widget.choose_folder = lambda parent, db, title, top, exclude=None, current=None: \
    (True, destinations.pop(0)) if destinations else (False, None)
projects_widget.notify = lambda parent, title, text: notices.append(title)

# ------------------------------------------ a journal from before Group 3
path = get_db_path()
path.parent.mkdir(parents=True, exist_ok=True)
seed = Database(path)
for title, category, archived in (("Novel draft", "Novel", 0), ("Chapter notes", " Novel ", 0),
                                  ("CHEM 173A", "Courses", 0), ("Old thesis", "Thesis", 1),
                                  ("Loose ends", None, 0)):
    seed._conn.execute(
        "INSERT INTO projects (title, content_md, content_format, content_text, created_at, "
        "updated_at, archived, category) VALUES (?, '<p>x</p>', 'html', 'x', 'a', 'a', ?, ?)",
        (title, archived, category))
seed._conn.commit()
seed.close()
con = sqlite3.connect(str(path))
con.execute("ALTER TABLE projects DROP COLUMN folder_id")
con.execute("DROP TABLE project_folders")
con.execute("DELETE FROM settings WHERE key = ?", (Database.FOLDERS_MIGRATION_SETTING,))
con.commit()
con.close()

from app.main_window import MainWindow  # noqa: E402  (opens — and migrates — the journal)

win = MainWindow()
win.resize(1300, 850)
win.show()
win.main_tabs.setCurrentWidget(win.projects_widget)
settle(100)
db = win.db
pw = win.projects_widget
tree = pw.project_list


def structure(widget):
    """The tree as nested (label, [children]) — what the user sees."""
    def walk(item):
        return (item.text(0), [walk(item.child(i)) for i in range(item.childCount())])
    return [walk(widget.project_list.topLevelItem(i))
            for i in range(widget.project_list.topLevelItemCount())]


def pid(title):
    return next(p.id for p in db.list_projects() + db.list_projects(archived=True) if p.title == title)


# ---------------------------------------------------------------- [46]
print("\n--- [46] categories became top-level folders ---")
shown = structure(pw)
check(f"the tree shows them as folders, loose projects last {shown}",
      [t for t, _c in shown] == ["Courses", "Novel", "Thesis", "Uncategorized"]
      and shown[0] == ("Courses", [("CHEM 173A", [])]) and shown[2] == ("Thesis", [])
      and shown[3] == ("Uncategorized", [("Loose ends", [])])
      and sorted(c for c, _x in shown[1][1]) == ["Chapter notes", "Novel draft"])
check("nothing renamed, nothing lost; the old category values are untouched",
      {p.title: p.category for p in db.list_projects() + db.list_projects(archived=True)}
      == {"Novel draft": "Novel", "Chapter notes": " Novel ", "CHEM 173A": "Courses",
          "Old thesis": "Thesis", "Loose ends": None})
pw.archive_toggle.setChecked(True)
settle()
check("the archived view shows the archived project in its folder",
      structure(pw) == [("Thesis", [("Old thesis", [])])])
pw.archive_toggle.setChecked(False)
settle()

# ---------------------------------------------------------------- [47]
print("\n--- [47] nesting, renaming and moving keep every identity ---")
courses = next(f.id for f in db.list_folders() if f.name == "Courses")
tree.setCurrentItem(pw.item_for("folder", courses))
names.append("Fall 2026")
fall = pw.new_folder(pw._selected_folder())
check("New Folder with a folder selected makes a subfolder", fall.parent_id == courses)
names.append("Week 1")
week1 = pw.new_folder(fall.id)
chem = pid("CHEM 173A")
db.save_notes("project", str(chem), "<p>Titration notes</p>", "html", "Titration notes")
db.add_version(chem, "<p>v1</p>", label="first", kind="manual")
check("four levels deep: Courses / Fall 2026 / Week 1",
      db.folder_path(week1.id) == ["Courses", "Fall 2026", "Week 1"])
check("Move to… puts the project inside", (destinations.append(week1.id) or True)
      and pw.ask_move("project", chem) and db.get_project(chem).folder_id == week1.id)
tree.setCurrentItem(pw.item_for("project", chem))
settle()
check("its editor shows the path", pw.folder_label.text() == "Courses / Fall 2026 / Week 1")
names.append("Autumn 2026")
pw.rename_folder(fall.id)
check("renaming a folder shows everywhere at once",
      pw.folder_label.text() == "Courses / Autumn 2026 / Week 1"
      and pw.item_for("folder", fall.id).text(0) == "Autumn 2026")
research = db.create_folder("Research")
check("moving a folder moves what is inside it",
      pw.move_to("folder", fall.id, research.id)
      and db.folder_path(db.get_project(chem).folder_id) == ["Research", "Autumn 2026", "Week 1"])
check("the project kept its id, its Reader's Notes and its versions",
      db.get_project(chem) is not None
      and "Titration notes" in db.get_notes("project", str(chem)).content
      and [v.label for v in db.get_project_versions(chem)] == ["first"])
out = pathlib.Path(tempfile.mkdtemp(prefix="g3-proj-archive-"))
archive.export_archive(db, out)
page = (out / "projects" / f"{chem}.html").read_text(encoding="utf-8")
check("its archive page keeps its name and shows the new path",
      "Research / Autumn 2026 / Week 1" in page)

# ---------------------------------------------------------------- [48]
print("\n--- [48] a folder can't go inside itself ---")
notices.clear()
check("moving Research into its own descendant is refused",
      not pw.move_to("folder", research.id, week1.id) and notices == ["Can't move there"]
      and db.get_folder(research.id).parent_id is None)
destinations.append(research.id)
check("and Move to… does not even offer it (the picker excludes it) — the command refuses anyway",
      not pw.ask_move("folder", research.id) and db.get_folder(research.id).parent_id is None)

# ---------------------------------------------------------------- [49]
print("\n--- [49] only empty folders can be deleted ---")
notices.clear()
check("a folder holding a project is kept",
      not pw.delete_folder(week1.id) and db.get_folder(week1.id) is not None
      and notices == ["Folder not empty"])
thesis = next(f.id for f in db.list_folders() if f.name == "Thesis")
check("a folder holding only an archived project is kept too",
      not pw.delete_folder(thesis) and db.get_folder(thesis) is not None)
names.append("Scratch")
scratch = pw.new_folder(None)
check("an empty folder is deleted", pw.delete_folder(scratch.id) and db.get_folder(scratch.id) is None
      and pw.item_for("folder", scratch.id) is None)

# ---------------------------------------------------------------- [51]
print("\n--- [51] drag-and-drop and Move to… are one command ---")
novel_draft = pid("Novel draft")
writing = db.create_folder("Writing")
pw.refresh_project_list(reload_editor=False)
settle()


def drop_onto(kind, ident, target_kind, target_ident):
    """A drop event carrying the tree's own drag data, handed to the tree's
    real drop handler.

    Handed to the handler rather than dispatched through Qt: Qt only
    delivers a drop that follows an accepted drag-move, and the item view
    accepts a move only when the drag's source is itself — which a
    synthetic event cannot claim (QDrag.exec is a blocking OS drag). So this
    covers everything from the drop position onward; the OS drag gesture
    itself is NOT TESTED here."""
    source = pw.item_for(kind, ident)
    tree.setCurrentItem(source)
    target_item = pw.item_for(target_kind, target_ident)
    rect = tree.visualItemRect(target_item)
    pos = QPointF(rect.center())
    mime = tree.model().mimeData([tree.indexFromItem(source)])
    event = QDropEvent(pos, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    tree.dropEvent(event)
    settle()


drop_onto("project", novel_draft, "folder", writing.id)
by_drag = db.get_project(novel_draft).folder_id
chapter = pid("Chapter notes")
destinations.append(writing.id)
pw.ask_move("project", chapter)
by_menu = db.get_project(chapter).folder_id
check("dropping a project on a folder moves it there", by_drag == writing.id)
check("Move to… gives the same stored result", by_menu == by_drag)
drop_onto("folder", writing.id, "folder", research.id)
check("dropping a folder on a folder nests it", db.get_folder(writing.id).parent_id == research.id)
drop_onto("project", novel_draft, "root", None)
check("dropping a project on Uncategorized takes it out of every folder",
      db.get_project(novel_draft).folder_id is None)
drop_onto("folder", research.id, "folder", writing.id)
check("dropping a folder into its own subfolder is refused",
      db.get_folder(research.id).parent_id is None)
check("the tree always shows what is stored (no half-moved items)",
      pw.item_for("project", novel_draft).parent().text(0) == "Uncategorized")

# ---------------------------------------------------------------- [50]
print("\n--- [50] the tree survives a restart ---")
before = structure(pw)
pw.archive_toggle.setChecked(True)
settle()
before_archived = structure(pw)
pw.archive_toggle.setChecked(False)
win.close()
settle()
win = MainWindow()
win.resize(1300, 850)
win.show()
win.main_tabs.setCurrentWidget(win.projects_widget)
settle(100)
pw = win.projects_widget
check("the same tree, folders and projects, after a restart", structure(pw) == before, structure(pw))
pw.archive_toggle.setChecked(True)
settle()
check("and the same archived view", structure(pw) == before_archived)
check("the folder migration did not run again",
      len([f for f in win.db.list_folders() if f.name == "Novel"]) == 1)
win.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
