"""Round-7 verification, per-selection font formatting.

This suite used to have a first half that ran a fake HTTP server to watch a
language-model download report progress. That feature no longer exists, so
that half is gone; what remains is the part that was never about it — that
changing the toolbar's size control restyles exactly the selected words
through the REAL MainWindow-wired RichEditor, and that re-selecting them
reports the size back.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
tmp_data_dir = tempfile.mkdtemp()
isolation.point_at(tmp_data_dir)
os.environ["HOME"] = tempfile.mkdtemp()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication              # noqa: E402
from PySide6.QtGui import QTextCursor                   # noqa: E402

KEEP_ANCHOR = QTextCursor.MoveMode.KeepAnchor
app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow

win = MainWindow()

editor = win.editor  # RichEditor wrapping a RichTextEditor
te = editor.text_edit
te.setPlainText("one two three")

# Select the middle word "two"
cursor = te.textCursor()
text = te.toPlainText()
start = text.index("two")
cursor.setPosition(start)
cursor.setPosition(start + len("two"), KEEP_ANCHOR)
te.setTextCursor(cursor)

# Simulate using the toolbar size control on just this selection.
editor.size_spin.setValue(20)
editor._on_size_control_changed(20)

# Now check char formats across the doc: only "two" should carry an
# explicit 20pt size; "one"/"three"/spaces should not.
doc = te.document()
results = []
c = doc.begin()
full_text = te.toPlainText()
check_cursor = te.textCursor()
for i, ch in enumerate(full_text):
    check_cursor.setPosition(i)
    check_cursor.setPosition(i + 1, KEEP_ANCHOR)
    fmt = check_cursor.charFormat()
    results.append((ch, fmt.fontPointSize()))

two_sizes = [sz for ch, sz in results if full_text[results.index((ch, sz))] ]
# simpler: map by index
sizes_by_char = list(zip(full_text, [r[1] for r in results]))
two_region = sizes_by_char[start:start + 3]
other_region = sizes_by_char[0:start] + sizes_by_char[start + 3:]

print("two region sizes:", two_region)
print("other region sizes (sample):", other_region)

assert all(sz == 20.0 for _, sz in two_region), f"expected 'two' at size 20, got {two_region}"
assert all(sz == 0.0 for _, sz in other_region), f"expected rest unset, got {other_region}"

# Toolbar should reflect the selection's actual size when re-selecting it.
cursor2 = te.textCursor()
cursor2.setPosition(start)
cursor2.setPosition(start + 3, KEEP_ANCHOR)
te.setTextCursor(cursor2)
editor._sync_toolbar_state()
print("toolbar size_spin after re-selecting 'two':", editor.size_spin.value())
assert editor.size_spin.value() == 20

# And selecting "one" (unset) should show the base/default size, not 20.
cursor3 = te.textCursor()
cursor3.setPosition(0)
cursor3.setPosition(3, KEEP_ANCHOR)
te.setTextCursor(cursor3)
editor._sync_toolbar_state()
print("toolbar size_spin after selecting 'one':", editor.size_spin.value(), "(base:", te._base_point_size, ")")
assert editor.size_spin.value() == te._base_point_size

# Markdown export should not be affected by the per-word size override.
md = te.toMarkdown()
print("markdown export:", repr(md.strip()))
assert "20" not in md or "two" in md  # sanity: just make sure export still has the words
assert "one two three" in md.replace("\\", "")

print("PART 2 (per-selection font size) : PASS")

# Typing above left the entry dirty, and this install defaults to manual
# saving, so closing would raise the unsaved-changes prompt and block a
# headless run. The prompt is tested properly in test_round26_saving.py;
# here the edit is deliberately abandoned.
win.editor.mark_clean()
win.close()
print("\nALL ROUND-7 TESTS PASSED")
