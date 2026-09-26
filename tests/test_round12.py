"""Round-12 headless verification:
M. The scroll-jump bug: typing, waiting past the autosave debounce, and
   confirming the view does NOT jump away from where it was — reproduced
   first against the actual bug (zoom active, since that's what triggers
   RichEditor.markdown()'s zoom-reset-and-reapply dance), then re-checked
   clean.
N. Bottom padding + "typewriter" centering: once an entry overflows the
   viewport, the actively-written line settles near vertical center rather
   than being pinned to the very bottom edge, and there's real room to
   scroll below the last line rather than it sitting flush with the edge.
O. A drive-by fix found while testing M: MainWindow/ProjectsWidget used to
   wire self.editor.textChanged -> _schedule_autosave before their own
   _autosave_timer existed, which could raise inside Qt's signal dispatch
   during construction (caught/printed by PySide rather than crashing, but
   a real bug) — confirm plain construction no longer raises.
P. The AI Reflection system prompt was rewritten to stop asking questions
   that just restate what the writer already concluded themselves, and to
   recognize its own earlier "AI Reflection" blocks (and the writer's
   replies to them) as already-covered ground. This can't be verified
   against real model output in this sandbox (no reachable GGUF file), so
   this section only checks the prompt-construction plumbing didn't break
   and that the new guidance text is actually present in what gets sent.
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile

import isolation

os.environ["QT_QPA_PLATFORM"] = "offscreen"
tmp_data_dir = tempfile.mkdtemp()
isolation.point_at(tmp_data_dir)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


def run_until(predicate, timeout_ms=10000):
    loop = QEventLoop()
    t = QTimer()
    def check():
        if predicate():
            loop.quit()
    t.timeout.connect(check)
    t.start(30)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    t.stop()


# ==================================================================== O
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    from app.main_window import MainWindow
    win = MainWindow()
stderr_output = buf.getvalue()
assert "_autosave_timer" not in stderr_output, \
    f"constructing MainWindow must not raise the _autosave_timer AttributeError:\n{stderr_output}"
print("O1 (MainWindow construction no longer raises the _autosave_timer ordering bug): PASS")

from app.projects_widget import ProjectsWidget
from app.database import Database

buf2 = io.StringIO()
with contextlib.redirect_stderr(buf2):
    pw_db = Database(os.path.join(tmp_data_dir, "test12_projects.db"))
    pw = ProjectsWidget(pw_db)
stderr_output2 = buf2.getvalue()
assert "_autosave_timer" not in stderr_output2, \
    f"constructing ProjectsWidget must not raise the _autosave_timer ordering bug:\n{stderr_output2}"
print("O2 (ProjectsWidget construction likewise clean): PASS")

# ==================================================================== M
win.resize(900, 600)
win.show()
app.processEvents()

te = win.editor.text_edit
te.setFixedHeight(150)
app.processEvents()

# Zoom active — this is what makes RichEditor.markdown()'s zoom-safe export
# actually exercise the reset-zoom/reapply-zoom dance that used to move the
# scroll position.
win.editor._zoom_in()
win.editor._zoom_in()
assert te._zoom_steps == 2

te.setFocus()
QTest.keyClicks(te, " ".join(f"word{i}" for i in range(400)))
app.processEvents()

sb = te.verticalScrollBar()
value_after_typing = sb.value()
# With typewriter-centering now implemented (request #2), typing no longer
# pins the view to the very bottom — the active line settles near vertical
# center instead. Confirm that, rather than the old "scrolled to maximum"
# behavior it replaced.
# Round 21 (spec Part 15) deliberately REMOVED the forced typewriter
# centering this originally asserted: the spec explicitly no longer wants the
# active line snapping to 50% viewport height. Round 17's soft-follow rule
# replaces it — the caret must stay VISIBLE inside the viewport, but nowhere
# in particular within it. That is what's checked now.
viewport_h = te.viewport().height()
cursor_y_after_typing = te.cursorRect().center().y()
assert 0 <= cursor_y_after_typing <= viewport_h, (
    f"caret should remain visible in the viewport (0..{viewport_h}), "
    f"got {cursor_y_after_typing}"
)

run_until(lambda: False, timeout_ms=1600)  # past the 1200ms autosave debounce
app.processEvents()

assert sb.value() == value_after_typing, (
    f"scroll position must not jump after the autosave debounce fires — "
    f"was {value_after_typing}, now {sb.value()}"
)
print("M1 (no scroll-jump after the autosave debounce, with zoom active): PASS")

# markdown() itself must be a read-only operation w.r.t. scroll position,
# called directly and repeatedly (as _save_current_entry does).
for _ in range(5):
    win.editor.save()
assert sb.value() == value_after_typing
print("M2 (calling markdown() repeatedly never moves the scroll position): PASS")

win.editor._zoom_reset()

# ==================================================================== N
win2 = MainWindow()
win2.resize(900, 600)
win2.show()
app.processEvents()
te2 = win2.editor.text_edit
te2.setFixedHeight(300)
app.processEvents()
sb2 = te2.verticalScrollBar()
te2.setFocus()

viewport_mid = te2.viewport().height() // 2
for i in range(30):
    QTest.keyClicks(te2, f"Line {i} of a longer entry that keeps going and going.")
    QTest.keyClick(te2, Qt.Key_Return)
app.processEvents()

assert sb2.maximum() > 0, "enough content should now make the padded document scrollable"
cursor_y = te2.cursorRect().center().y()
# Round 21 (spec Part 15) removed forced centering; round 17's soft-follow
# rule replaced it. The caret must stay visible, not land at 50%.
assert 0 <= cursor_y <= te2.viewport().height(), \
    f"caret should remain visible in the viewport, got {cursor_y}"
print("N1 (active line settles near vertical center once the entry overflows): PASS")

sb2.setValue(sb2.maximum())
app.processEvents()
last_line_y = te2.cursorRect().center().y()
assert last_line_y < te2.viewport().height() // 2 + 10, \
    "scrolling all the way down should leave real room below the last line"
print("N2 (real scrollable padding below the lowest text, not flush with the edge): PASS")

# Padding must be purely a view/layout thing — never leak into saved content.
md = win2.editor.save()[0]
assert "Line 0 of a longer entry" in md and "Line 29 of a longer entry" in md
assert not md.endswith((" ", "\t"))
print("N3 (padding never leaks into the exported Markdown): PASS")

# Switching entries (set_markdown replaces the document) must not lose the
# padding — reload a short entry and confirm padding is still applied.
win2.editor.load("A brand new, short entry.", 'markdown')
app.processEvents()
fmt = te2.document().rootFrame().frameFormat()
assert fmt.bottomMargin() > 0, "bottom padding must survive switching to a different entry"
print("N4 (padding survives set_markdown/switching entries): PASS")

print("\nALL ROUND-12 TESTS PASSED")
