"""End-to-end round 21 test: drives MainWindow and ProjectsWidget like a
real user would (type, format, switch dates, "restart" the app by tearing
down and rebuilding against the same on-disk database) and checks that
formatting, blank lines, search and cleanup all still work correctly with
the html-based persistence."""
import os
import pathlib
import sys
import tempfile

import isolation

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
data_dir = tempfile.mkdtemp()
isolation.point_at(data_dir)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow  # noqa: E402
from app import cleanup  # noqa: E402

# ---- 1. write a formatted entry, save, "restart", verify -------------------
w = MainWindow()
w._load_date("2026-09-16")

cursor = w.editor.text_edit.textCursor()
cursor.insertText("Today was a good day.")
cursor.insertBlock()
cursor.insertBlock()  # deliberate blank line
cursor.insertText("Second paragraph, centered and double-spaced.")
w.editor.text_edit.setTextCursor(cursor)

# select the second paragraph and apply centered alignment + double spacing
sel = w.editor.text_edit.textCursor()
sel.select(QTextCursor.BlockUnderCursor)
w.editor.text_edit.setTextCursor(sel)
w.editor._set_alignment(Qt.AlignHCenter)
w.editor.line_spacing_combo.setCurrentIndex(3)  # Double

w._save_current_entry()
w.close()
del w

# "restart": brand new MainWindow against the same on-disk database
w2 = MainWindow()
w2._load_date("2026-09-16")
doc = w2.editor.text_edit.document()
block_texts = [doc.findBlockByNumber(i).text() for i in range(doc.blockCount())]
assert block_texts == [
    "Today was a good day.", "", "Second paragraph, centered and double-spaced.",
], block_texts
last_block = doc.findBlockByNumber(doc.blockCount() - 1)
assert (last_block.blockFormat().alignment() & Qt.AlignHorizontal_Mask) == Qt.AlignHCenter
assert abs(last_block.blockFormat().lineHeight() - 200.0) < 0.01
print("OK: blank line + alignment + double line-spacing all survive a full app restart")

# ---- 2. search still finds text across a formatted entry -------------------
results = w2.db.search_entries("centered")
assert any(e.date == "2026-09-16" for e in results), "search did not find text inside formatted html entry"
print("OK: search_entries finds text inside an html-format entry")

# ---- 3. calendar length heuristic uses plain text, not raw html boilerplate
entries = w2.db.entries_in_range("2026-09-16", "2026-09-16")
entry = entries["2026-09-16"]
assert len(entry.body_text) < len(entry.body_md), "body_text should be much shorter than the html blob"
assert len(entry.body_text) < 200, f"body_text should be roughly plain-text length, got {len(entry.body_text)}"
print(f"OK: body_text ({len(entry.body_text)} chars) is plain, body_md ({len(entry.body_md)} chars) is full html")

# ---- 5. Writing Projects: same persistence path ----------------------------
# NOTE: deliberately calling db.create_project() directly + refresh_project_list()
# instead of ProjectsWidget._new_project(), which pops a blocking QInputDialog
# (fine interactively, but hangs forever headless with no one to click OK).
w2.main_tabs.setCurrentWidget(w2.projects_widget)  # Writing Projects tab
proj_widget = w2.projects_widget
new_project = w2.db.create_project("Test Novel")
proj_widget.refresh_project_list(select_id=new_project.id)
pc = proj_widget.editor.text_edit.textCursor()
pc.insertText("Chapter one.")
pc.insertBlock()
pc.insertBlock()
pc.insertText("Chapter two, indented.")
proj_widget.editor.text_edit.setTextCursor(pc)
proj_widget.editor._indent()
proj_widget._save_content()

project_id = proj_widget.current_project_id
w2.close()
del w2

w3 = MainWindow()
w3.main_tabs.setCurrentWidget(w3.projects_widget)
w3.projects_widget._load_project(project_id)
pdoc = w3.projects_widget.editor.text_edit.document()
ptexts = [pdoc.findBlockByNumber(i).text() for i in range(pdoc.blockCount())]
assert ptexts == ["Chapter one.", "", "Chapter two, indented."], ptexts
last_pblock = pdoc.findBlockByNumber(pdoc.blockCount() - 1)
assert last_pblock.blockFormat().leftMargin() > 0, "indent should have survived restart"
print("OK: Writing Projects blank line + indent survive a full app restart")

# ---- 6. cleanup.py still finds inline photo references (html + markdown) ---
w3.db.upsert_entry("2026-09-18", body_md='<p>Look at this <img src="2026/09/pic.png" /></p>',
                    body_format="html", body_text="Look at this ")
w3.db.upsert_entry("2026-09-19", body_md="Old note ![alt](2026/09/legacy.png)",
                    body_format="markdown", body_text="Old note ![alt](2026/09/legacy.png)")
refs = cleanup._referenced_paths(w3.db)
assert "2026/09/pic.png" in refs, refs
assert "2026/09/legacy.png" in refs, refs
print("OK: cleanup.py finds both html <img> and legacy markdown image references")

w3.close()
print("\nALL END-TO-END ROUND 21 TESTS PASSED")
