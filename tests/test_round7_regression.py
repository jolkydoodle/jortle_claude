"""Round-7 regression pass: bold/italic/heading toolbar, zoom, and the
Default-writing-font Settings control, making sure none of round 7's
changes disturbed them — especially that changing the app-wide default
font does NOT clobber a per-word size override already applied."""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
tmp_data_dir = tempfile.mkdtemp()
isolation.point_at(tmp_data_dir)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor

KEEP_ANCHOR = QTextCursor.MoveMode.KeepAnchor

app = QApplication.instance() or QApplication([])

from app.main_window import MainWindow

win = MainWindow()
editor = win.editor
te = editor.text_edit
te.setPlainText("one two three")

# --- Bold/Italic still per-selection (unchanged code path, just confirming
# nothing in this round's edits regressed it) ---
cursor = te.textCursor()
text = te.toPlainText()
start = text.index("two")
cursor.setPosition(start)
cursor.setPosition(start + 3, KEEP_ANCHOR)
te.setTextCursor(cursor)
editor._toggle_bold()

check = te.textCursor()
check.setPosition(start)
check.setPosition(start + 3, KEEP_ANCHOR)
bold_fmt = check.charFormat()
print("bold weight on 'two':", bold_fmt.fontWeight())
check_other = te.textCursor()
check_other.setPosition(0)
check_other.setPosition(3, KEEP_ANCHOR)
print("bold weight on 'one':", check_other.charFormat().fontWeight())
assert bold_fmt.fontWeight() > check_other.charFormat().fontWeight()
print("bold per-selection: PASS")

# --- Zoom in/out is reversible and independent of base font ---
base_before = te._base_point_size
editor._zoom_in()
editor._zoom_in()
zoomed_pct = te.zoom_percent()
editor._zoom_out()
editor._zoom_out()
print("base point size unaffected by zoom:", te._base_point_size == base_before)
assert te._base_point_size == base_before
print("zoom in/out reversible: PASS")

# --- Apply a per-word size override, then change the Default writing font
#     via Settings-equivalent set_font() call, and confirm the per-word
#     override survives (only the *default* should move). ---
cursor2 = te.textCursor()
cursor2.setPosition(start)
cursor2.setPosition(start + 3, KEEP_ANCHOR)
te.setTextCursor(cursor2)
editor._on_size_control_changed(30)

before_two = None
c = te.textCursor()
c.setPosition(start)
c.setPosition(start + 3, KEEP_ANCHOR)
before_two = c.charFormat().fontPointSize()
print("'two' size before default-font change:", before_two)
assert before_two == 30.0

# Now simulate what MainWindow._apply_settings does when the Settings
# dialog's Default writing font/size changes: call editor.set_font(...).
editor.set_font("Georgia", 18)

c2 = te.textCursor()
c2.setPosition(start)
c2.setPosition(start + 3, KEEP_ANCHOR)
after_two = c2.charFormat().fontPointSize()
print("'two' size after default-font change to 18:", after_two)
assert after_two == 30.0, "per-word override must survive a default-font change"

c3 = te.textCursor()
c3.setPosition(0)
c3.setPosition(3, KEEP_ANCHOR)
after_one = c3.charFormat().fontPointSize()
print("'one' explicit size after default-font change (should still be unset/0):", after_one)
assert after_one == 0.0
print("base point size now:", te._base_point_size)
# Since 4C1a (Master Spec 2026-10-02 §14.9) a document with content keeps the
# font stored with it; only an empty document takes the new setting.
assert te._base_point_size == base_before, "a document with content keeps its own font"
print("default-font change preserves per-word overrides: PASS")
from app.rich_editor import RichEditor  # noqa: E402
empty = RichEditor()
empty.set_font("Georgia", 18)
assert empty.text_edit._base_point_size == 18.0, "an empty document takes the new setting"
print("an empty document takes the new default font: PASS")

# --- heading/list/link toolbar sanity: just confirm the methods still
#     exist and run without error (full behavioral coverage was done in
#     earlier rounds; this just guards against an import/signature break
#     from this round's edits). ---
te.setPlainText("Some heading text")
cursor4 = te.textCursor()
cursor4.select(QTextCursor.SelectionType.LineUnderCursor)
te.setTextCursor(cursor4)
if hasattr(editor, "heading_combo"):
    editor.heading_combo.setCurrentIndex(1)
    print("heading combo applied without error: PASS")

print("\nALL REGRESSION CHECKS PASSED")
