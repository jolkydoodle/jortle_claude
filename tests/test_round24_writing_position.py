"""Round 24 — Part 18's optional writing-position preference.

The important requirement is that "Free / Manual" is the default and does
nothing at all, preserving the Part 15/17 behaviour exactly; the other three
are an opt-in nudge.
"""
import os, pathlib, sys, tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
isolation.isolate()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt
app = QApplication.instance() or QApplication([])

from app.database import Database
from app.main_window import MainWindow
from app.rich_editor import (
    RichTextEditor, WRITING_POSITIONS, WRITING_POSITION_FREE, WRITING_POSITION_LABELS,
)

fails = []
def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"   {detail}" if not cond and detail else ""))
    if not cond: fails.append(label)


def editor_at(position):
    te = RichTextEditor()
    te.resize(600, 420)
    te.show()
    app.processEvents()
    te.set_writing_position(position)
    te.setPlainText("\n".join(f"line {i}" for i in range(400)))
    app.processEvents()
    c = te.textCursor(); c.movePosition(QTextCursor.End); te.setTextCursor(c)
    app.processEvents()
    return te


print("\n=== the default does nothing ===")
te = editor_at(WRITING_POSITION_FREE)
check("default is Free / Manual", te.set_writing_position(None) or
      te._writing_position == WRITING_POSITION_FREE)
te = editor_at(WRITING_POSITION_FREE)
sb = te.verticalScrollBar()
sb.setValue(sb.value() - 150)
app.processEvents()
before = sb.value()
te.setFocus(); QTest.keyClicks(te, "xyz"); app.processEvents()
check("typing in Free mode does not snap the view to any fraction",
      abs(te.cursorRect().center().y() - te.viewport().height() / 2) > 40)
te.close()

print("\n=== each position places the active line where it says ===")
for name, fraction in (("top", 1/3), ("center", 1/2), ("bottom", 2/3)):
    te = editor_at(name)
    vh = te.viewport().height()
    sb = te.verticalScrollBar(); sb.setValue(sb.value() - 150); app.processEvents()
    te.setFocus(); QTest.keyClicks(te, "xyz"); app.processEvents()
    landed = te.cursorRect().center().y()
    check(f"'{name}' settles the caret near {fraction:.2f} of the viewport",
          abs(landed - vh * fraction) <= 30, f"landed {landed}, target {vh*fraction:.0f}")
    te.close()

print("\n=== navigation keys are never fought ===")
te = editor_at("center")
sb = te.verticalScrollBar()
sb.setValue(sb.value() - 200)
app.processEvents()
scroll_before = sb.value()
te.setFocus()
for key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right, Qt.Key_PageUp):
    QTest.keyClick(te, key)
app.processEvents()
check("arrow/page keys do not trigger repositioning",
      abs(sb.value() - scroll_before) < 400, f"{scroll_before} -> {sb.value()}")
te.close()

print("\n=== an unrecognized value falls back to Free ===")
te = RichTextEditor()
te.set_writing_position("nonsense-from-a-hand-edited-setting")
check("unknown value degrades to Free", te._writing_position == WRITING_POSITION_FREE)
te.close()

print("\n=== scroll-past-end space adapts to the setting ===")
te = editor_at(WRITING_POSITION_FREE)
free_pad = te.bottom_padding()
te.set_writing_position("top")
app.processEvents()
top_pad = te.bottom_padding()
check("asking for the top third deepens the space below the last line",
      top_pad > free_pad, f"free={free_pad} top={top_pad}")
te.set_writing_position(WRITING_POSITION_FREE)
app.processEvents()
check("and returns to the normal half-viewport for Free",
      abs(te.bottom_padding() - free_pad) < 2, f"{te.bottom_padding()} vs {free_pad}")
te.close()

print("\n=== the preference is persisted and applied ===")
w = MainWindow()
check("app default is Free", w.editor.text_edit._writing_position == WRITING_POSITION_FREE)
w.db.set_setting("writing_position", "center")
w._apply_settings()
check("changing the setting reaches the editor",
      w.editor.text_edit._writing_position == "center")
check("Reader's Notes is deliberately left unmanaged",
      w.reader_notes.editor.text_edit._writing_position == WRITING_POSITION_FREE)
w.close()

w2 = MainWindow()
check("the preference survives a restart",
      w2.editor.text_edit._writing_position == "center")
w2.close()

from app.settings_dialog import SettingsDialog
db = Database()
dlg = SettingsDialog(db, on_change=lambda: None)
labels = [dlg.writing_position_combo.itemText(i)
          for i in range(dlg.writing_position_combo.count())]
check("Settings offers all four options", len(labels) == 4, str(labels))
check("Free / Manual is listed first", "Free" in labels[0], labels[0])
db.close()

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED:")
    for f in fails: print("  -", f)
    sys.exit(1)
print("ALL WRITING-POSITION CHECKS PASSED")
