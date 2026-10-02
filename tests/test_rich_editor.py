"""Round 21 smoke test for the rewritten rich_editor.py: exercises the new
load()/save()/plain_text() API, the new toolbar controls (alignment, line
spacing, paragraph spacing, superscript, subscript, strikethrough), the
Ctrl+F find bar, blank-paragraph/multi-blank-paragraph persistence, legacy
Markdown loading, and confirms the forced-caret-centering method is gone."""
import pathlib
import sys

# Not for data isolation (this suite builds bare editors and opens no data
# folder): for Qt's real fonts on Windows and the shared recorder of errors
# inside Qt callbacks, which every Qt suite gets this way (D24).
import isolation  # noqa: F401

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor, QTextCharFormat  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.rich_editor import RichEditor, RichTextEditor  # noqa: E402

# ---- 1. forced centering is really gone -----------------------------------
assert not hasattr(RichTextEditor, "_keep_cursor_centered"), "_keep_cursor_centered still exists!"
assert not hasattr(RichTextEditor, "_RECENTER_KEYS"), "_RECENTER_KEYS still exists!"
print("OK: forced caret-centering code removed")

# ---- 2. basic html round trip, including blank paragraphs -----------------
editor = RichEditor()
cursor = editor.text_edit.textCursor()
cursor.insertText("First paragraph.")
cursor.insertBlock()
cursor.insertBlock()  # two consecutive blank paragraphs
cursor.insertBlock()
cursor.insertText("Second paragraph.")
editor.text_edit.setTextCursor(cursor)

html, plain = editor.save()
assert "First paragraph." in plain and "Second paragraph." in plain
print("OK: save() returns (html, plain_text)")

editor2 = RichEditor()
editor2.load(html, fmt="html")
doc2 = editor2.text_edit.document()
assert doc2.blockCount() == 4, f"expected 4 blocks (2 real + 2 blank), got {doc2.blockCount()}"
texts = [doc2.findBlockByNumber(i).text() for i in range(4)]
assert texts == ["First paragraph.", "", "", "Second paragraph."], texts
print("OK: blank paragraphs (including consecutive ones) survive html save/load round trip")

# ---- 3. legacy markdown load still works -----------------------------------
editor3 = RichEditor()
editor3.load("**bold legacy** entry\n\nwith a paragraph break", fmt="markdown")
assert "bold legacy" in editor3.plain_text()
print("OK: legacy fmt='markdown' rows still load correctly")

# ---- 4. alignment / line spacing / paragraph spacing -----------------------
editor4 = RichEditor()
editor4.text_edit.insertPlainText("Align me")
c = editor4.text_edit.textCursor()
c.select(QTextCursor.BlockUnderCursor)
editor4.text_edit.setTextCursor(c)
editor4._set_alignment(Qt.AlignHCenter)
block_fmt = editor4.text_edit.textCursor().blockFormat()
assert (block_fmt.alignment() & Qt.AlignHorizontal_Mask) == Qt.AlignHCenter
print("OK: alignment toolbar action applies AlignHCenter")

editor4.line_spacing_combo.setCurrentIndex(2)  # "1.5"
block_fmt = editor4.text_edit.textCursor().blockFormat()
assert abs(block_fmt.lineHeight() - 150.0) < 0.01, block_fmt.lineHeight()
print("OK: line spacing preset (1.5) applies via combo")

editor4.space_before_spin.setValue(18)
editor4.space_after_spin.setValue(24)
block_fmt = editor4.text_edit.textCursor().blockFormat()
assert abs(block_fmt.topMargin() - 18) < 0.01
assert abs(block_fmt.bottomMargin() - 24) < 0.01
print("OK: paragraph spacing before/after apply via spin boxes")

html4, _ = editor4.save()
editor4b = RichEditor()
editor4b.load(html4, fmt="html")
c2 = editor4b.text_edit.textCursor()
c2.movePosition(QTextCursor.Start)
bf2 = c2.blockFormat()
assert (bf2.alignment() & Qt.AlignHorizontal_Mask) == Qt.AlignHCenter
assert abs(bf2.lineHeight() - 150.0) < 0.01
assert abs(bf2.topMargin() - 18) < 0.01
assert abs(bf2.bottomMargin() - 24) < 0.01
print("OK: alignment + line spacing + paragraph spacing all survive save/load round trip")

# ---- 5. superscript / subscript / strikethrough ----------------------------
editor5 = RichEditor()
editor5.text_edit.insertPlainText("plain")
editor5._toggle_superscript()
fmt = editor5.text_edit.textCursor().charFormat()
# nothing selected -> affects what's typed NEXT; verify via currentCharFormat
current_fmt = editor5.text_edit.currentCharFormat()
assert current_fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript
editor5._toggle_subscript()
current_fmt = editor5.text_edit.currentCharFormat()
assert current_fmt.verticalAlignment() == QTextCharFormat.AlignSubScript, "subscript should override superscript"
editor5._toggle_subscript()  # toggle off
current_fmt = editor5.text_edit.currentCharFormat()
assert current_fmt.verticalAlignment() == QTextCharFormat.AlignNormal
print("OK: superscript/subscript are mutually exclusive and toggle off correctly")

editor5b = RichEditor()
editor5b.text_edit.insertPlainText("struck")
c3 = editor5b.text_edit.textCursor()
c3.select(QTextCursor.Document)
editor5b.text_edit.setTextCursor(c3)
editor5b._toggle_strikethrough()
assert editor5b.text_edit.textCursor().charFormat().fontStrikeOut()
print("OK: strikethrough toggles on")

# ---- 6. Ctrl+F find bar -----------------------------------------------------
editor6 = RichEditor()
editor6.show()  # top-level widgets default to hidden until shown -- needed
# for isVisible() below to reflect setVisible(True) rather than "hidden
# because the top-level ancestor itself was never shown" (an offscreen-test
# artifact, not something that happens once this is embedded in a real window).
editor6.text_edit.insertPlainText("the quick brown fox jumps over the lazy fox")
assert not editor6.find_bar.isVisible()
editor6.show_find_bar()
assert editor6.find_bar.isVisible()
editor6.find_input.setText("fox")
ranges = editor6.text_edit.find_all_ranges("fox")
assert len(ranges) == 2, ranges
editor6._select_match(0)
assert editor6.text_edit.textCursor().selectedText() == "fox"
editor6._jump_to_match(1)
assert editor6._find_current_index == 1
editor6._jump_to_match(1)  # should wrap back to 0
assert editor6._find_current_index == 0
# confirm searching never modifies the document
html_before = editor6.text_edit.toHtml()
editor6.find_input.setText("quick")
html_after = editor6.text_edit.toHtml()
plain_before_after = editor6.text_edit.toPlainText()
assert "the quick brown fox jumps over the lazy fox" == plain_before_after
editor6.hide_find_bar()
assert not editor6.find_bar.isVisible()
print("OK: Ctrl+F find bar finds/highlights/navigates/wraps matches and never modifies text")

# ---- 7. image reference survives html round trip ---------------------------
import tempfile
from pathlib import Path
from app import paths as app_paths

tmp_attach = Path(tempfile.mkdtemp())
app_paths.get_attachments_dir = lambda: tmp_attach  # monkeypatch for this test
(tmp_attach / "2026" / "09").mkdir(parents=True, exist_ok=True)
# write a tiny valid PNG so QImage can load it
png_bytes = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000155e621bc00000000"
    "49454e44ae426082"
)
(tmp_attach / "2026" / "09" / "test.png").write_bytes(png_bytes)

editor7 = RichEditor()
editor7.set_storage_subdir("2026/09")
cursor7 = editor7.text_edit.textCursor()
cursor7.insertImage("2026/09/test.png")
html7, _ = editor7.save()
assert "test.png" in html7, "image src not present in exported html"
editor7b = RichEditor()
editor7b.load(html7, fmt="html")
found_image = False
block = editor7b.text_edit.document().begin()
while block.isValid():
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid() and frag.charFormat().isImageFormat():
            found_image = True
        it += 1
    block = block.next()
assert found_image, "image resource lost on html round trip"
print("OK: inline image reference survives html save/load round trip")

print("\nALL RICH EDITOR TESTS PASSED")
