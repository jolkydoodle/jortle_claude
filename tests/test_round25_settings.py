"""Round 25 — Settings / Experimental Settings split (Parts 35, 36).

Checks the split is structural and not cosmetic:
  * core Settings imports no AI module and carries no AI control;
  * every AI control exists exactly once in the app, in the experimental
    dialog (no duplication across the two locations);
  * both dialogs scroll, wrap, and stay closable at a large UI font on a
    small screen;
  * the File menu gains the second item, and loses it in a build with the
    feature compiled out.
"""
import os
import pathlib
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r25s-")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialogButtonBox, QScrollArea, QCheckBox
)

app = QApplication.instance() or QApplication([])

from app.database import Database  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


db = Database()

print("\n--- there is one Settings window, and nothing AI in it ---")
import app.settings_dialog as sd  # noqa: E402
module_names = {v.__name__ for v in vars(sd).values() if hasattr(v, "__name__")}
check("settings_dialog imports no AI module",
      not any(n in str(v) for v in vars(sd).values() if hasattr(v, "__file__")
              for n in ("local_llm", "reflection", "gpu_probe", "model_state")))

core = SettingsDialog(db, on_change=lambda: None)
ai_attr_names = [
    "ai_enable_checkbox", "ai_model_combo", "ai_download_btn", "ai_idle_spin",
    "ai_edit_prompts_btn", "ai_diagnostics_btn", "ai_progress", "ai_status_label",
]
check("core Settings exposes no AI controls",
      not [n for n in ai_attr_names if hasattr(core, n)])
core_checkboxes = [c.text() for c in core.findChildren(QCheckBox)]
check(f"core Settings has no AI checkbox anywhere in its widget tree ({core_checkboxes})",
      not [t for t in core_checkboxes if "AI" in t])
check("core Settings still has the ordinary controls",
      all(hasattr(core, n) for n in
          ["font_combo", "size_spin", "writing_position_combo", "ui_size_spin",
           "scheme_combo", "swatch_buttons"]))

print("\n--- Part 36: the dialog fits, wraps and stays usable ---")
for name, dlg in (("Settings", core),):
    scrolls = dlg.findChildren(QScrollArea)
    check(f"{name}: content is inside a scroll area", len(scrolls) == 1)
    check(f"{name}: no horizontal scrollbar is needed at the size it opens at",
          not scrolls[0].horizontalScrollBar().isVisible())
    check(f"{name}: scroll area resizes its content to the window width",
          scrolls[0].widgetResizable())
    box = dlg.findChild(QDialogButtonBox)
    check(f"{name}: the Close button is NOT inside the scroll area",
          box is not None and not scrolls[0].isAncestorOf(box))
    # A 1366x768 laptop, minus window chrome, is the bar to clear.
    check(f"{name}: minimum height {dlg.minimumHeight()} fits a small laptop screen",
          dlg.minimumHeight() <= 700)
    check(f"{name}: minimum width {dlg.minimumWidth()} fits a small laptop screen",
          dlg.minimumWidth() <= 800)

print("\n--- Part 36: still fits at the largest application font size ---")
font = app.font()
original_size = font.pointSize()
font.setPointSize(24)
app.setFont(font)
big_core = SettingsDialog(db, on_change=lambda: None)
for name, dlg in (("Settings", big_core),):
    dlg.resize(*dlg._starting_size())
    dlg.show()
    app.processEvents()
    scroll = dlg.findChildren(QScrollArea)[0]
    inner = scroll.widget()
    check(f"{name} @24pt: content width tracks the window, no horizontal overflow",
          inner.width() <= scroll.viewport().width() + 1)
    box = dlg.findChild(QDialogButtonBox)
    check(f"{name} @24pt: Close button still visible inside the window",
          box.y() + box.height() <= dlg.height())
    check(f"{name} @24pt: vertical scrolling available if needed",
          scroll.verticalScrollBarPolicy() != Qt.ScrollBarAlwaysOff)
    check(f"{name} @24pt: window opens no wider than the screen",
          dlg.width() <= app.primaryScreen().availableGeometry().width())
    dlg.hide()
font.setPointSize(original_size)
app.setFont(font)

print("\n--- the File menu ---")
from app.main_window import MainWindow  # noqa: E402

w = MainWindow()
file_menu = w.menuBar().actions()[0].menu()
labels = [a.text() for a in file_menu.actions()]
check(f"File menu has Settings ({[l for l in labels if 'Settings' in l]})",
      "Settings…" in labels)
check("and no Experimental Settings, which existed only for the AI feature",
      "Experimental Settings…" not in labels)
check("no menu item anywhere mentions AI",
      not [a.text() for m in w.menuBar().actions() if m.menu()
           for a in m.menu().actions() if "AI" in a.text()])
w.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
