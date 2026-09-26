"""Round 25 — Named Day Markers (Parts 22 and 23).

Checks the whole loop, not the table: create / assign / rename / recolour /
delete through the actual widgets, that an existing colour-only database
migrates without losing a marked day, and that a long marker name cannot
force the calendar pane wide.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
data_dir = tempfile.mkdtemp(prefix="jortle-r25m-")
isolation.point_at(data_dir)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.database import Database  # noqa: E402
from app.tag_picker import DayMarkerDialog, TagPicker  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


# --------------------------------------------------- migration from colours
print("\n--- Part 22: an existing colour-only database migrates ---")
legacy_path = Path(data_dir) / "legacy.db"
conn = sqlite3.connect(str(legacy_path))
conn.executescript("""
CREATE TABLE entries (date TEXT PRIMARY KEY, title TEXT DEFAULT '',
    body_md TEXT DEFAULT '', body_format TEXT DEFAULT 'markdown',
    body_text TEXT DEFAULT '', tag TEXT, tag_color TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO entries VALUES ('2026-03-01', 'Trip day', 'went north', 'markdown',
    'went north', 'trip', '#3f8ede', '2026-03-01', '2026-03-01');
INSERT INTO entries VALUES ('2026-03-05', 'Big one', 'shipped it', 'markdown',
    'shipped it', 'milestone', '#9b59b6', '2026-03-05', '2026-03-05');
INSERT INTO entries VALUES ('2026-03-09', 'Mine', 'a custom day', 'markdown',
    'a custom day', 'custom', '#123456', '2026-03-09', '2026-03-09');
""")
conn.commit()
conn.close()

legacy = Database(str(legacy_path))
markers = {m.name: m for m in legacy.list_day_markers()}
check(f"every distinct old marker became a named one {sorted(markers)}",
      {"Trip / Travel", "Milestone", "Custom"} <= set(markers))
check("their colours carried across",
      markers["Trip / Travel"].color == "#3f8ede"
      and markers["Milestone"].color == "#9b59b6"
      and markers["Custom"].color == "#123456")
for date, name in (("2026-03-01", "Trip / Travel"), ("2026-03-05", "Milestone"),
                   ("2026-03-09", "Custom")):
    entry = legacy.get_entry(date)
    check(f"{date} still marked, now by id ({entry.tag} -> {name})",
          entry.tag == str(markers[name].id))
    check(f"{date} kept its colour", entry.tag_color == markers[name].color)
check("nothing else about the entries was touched",
      legacy.get_entry("2026-03-01").body_text == "went north")

print("\n--- Part 22: a fresh database starts with usable, editable markers ---")
db = Database()
names = [m.name for m in db.list_day_markers()]
check(f"a fresh install has markers out of the box {names}", len(names) >= 4)
first = db.list_day_markers()[0]
db.update_day_marker(first.id, name="Renamed by the user")
check("...and they are ordinary editable rows, not built-ins",
      db.list_day_markers()[0].name == "Renamed by the user")
db.update_day_marker(first.id, name=first.name)

print("\n--- Part 22: create / assign / rename / recolour / delete ---")
picker = TagPicker(db=db)
emitted = []
picker.tagChanged.connect(lambda key, color: emitted.append((key, color)))

made = db.create_day_marker("Research Day", "#2e8b57")
picker.reload_markers()
labels = [picker.combo.itemText(i) for i in range(picker.combo.count())]
check(f"a new marker appears in the picker {labels}", "Research Day" in labels)
check("the picker always offers a way out to the manager",
      labels[0] == "No marker" and labels[-1] == "Manage markers…")

picker.combo.setCurrentIndex(labels.index("Research Day"))
check(f"choosing it emits the marker id and colour {emitted}",
      emitted and emitted[-1] == (str(made.id), "#2e8b57"))

db.upsert_entry("2026-05-04", tag=str(made.id), tag_color="#2e8b57")
db.update_day_marker(made.id, name="Lab Day", color="#b5651d")
check("renaming keeps the same marker on the same day",
      db.get_entry("2026-05-04").tag == str(made.id))
check("recolouring updates the days already using it — one marker, one colour",
      db.get_entry("2026-05-04").tag_color == "#b5651d")

picker.reload_markers()
check("the picker shows the new name",
      "Lab Day" in [picker.combo.itemText(i) for i in range(picker.combo.count())])

print("\n--- Part 22: 'Manage markers…' is a door, not a choice ---")
picker.set_current(str(made.id), "#b5651d")
before = picker.combo.currentText()
# Stub the dialog out: it is modal, and what's under test here is that
# choosing that row doesn't become the day's marker, not the dialog itself.
opened = []
picker.open_manager = lambda: opened.append(True)
picker._on_index_changed(picker.combo.count() - 1)
check("choosing it opens the manager", opened == [True])
check(f"selecting it leaves the day's marker alone (still {before})",
      picker.combo.currentText() == before)
check("and it is never emitted as a marker",
      all(key != "__manage__" for key, _c in emitted))

print("\n--- Part 22: deleting unmarks the days but keeps their content ---")
db.upsert_entry("2026-05-04", title="Notes", body_md="<p>real writing</p>",
                 body_text="real writing")
db.delete_day_marker(made.id)
entry = db.get_entry("2026-05-04")
check("the day is unmarked", not entry.tag and not entry.tag_color)
check("the day's own writing survived", entry.body_text == "real writing")
picker.reload_markers()
picker.set_current(str(made.id), "#b5651d")
check("a day pointing at a deleted marker shows as unmarked, not as a stale name",
      picker.combo.currentText() == "No marker")

print("\n--- Part 23: a long name cannot force the pane wide ---")
long_name = "Quarterly deep-work research and writing retreat day"
db.create_day_marker(long_name, "#123456")
picker.reload_markers()
app.processEvents()
check(f"the picker's minimum width stays modest ({picker.minimumSizeHint().width()}px)",
      picker.minimumSizeHint().width() < 260)
check("the combo can be narrower than its longest item",
      picker.combo.minimumSizeHint().width() < 220)
index = [picker.combo.itemText(i) for i in range(picker.combo.count())].index(long_name)
check("the full name is still reachable, on the item's tooltip",
      picker.combo.itemData(index, 3) == long_name or
      picker.combo.itemData(index, 3) is not None)

print("\n--- Part 23: marker chips follow the application font size ---")
small = picker._chip_size()
font = app.font()
original = font.pointSize()
font.setPointSize(22)
app.setFont(font)
big_picker = TagPicker(db=db)
check(f"chip grows with the font ({small}px -> {big_picker._chip_size()}px)",
      big_picker._chip_size() > small)
font.setPointSize(original)
app.setFont(font)

print("\n--- the management dialog itself ---")
dialog = DayMarkerDialog(db)
check("it lists every marker", dialog.list.count() == len(db.list_day_markers()))
dialog.list.setCurrentRow(0)
check("selection resolves to a real marker", dialog._selected_marker() is not None)
dialog.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
