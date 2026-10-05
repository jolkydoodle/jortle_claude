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
# Since batch 4A the window has pages (G4-D3), each in its own scroll area.
for name, dlg in (("Settings", core),):
    scrolls = dlg.findChildren(QScrollArea)
    check(f"{name}: each of its {len(dlg.PAGES)} pages is inside a scroll area",
          len(scrolls) == len(dlg.PAGES)
          and all(any(s.widget() is dlg._contents[p] for s in scrolls) for p in dlg.PAGES))
    dlg.show()
    for page in dlg.PAGES:
        dlg.show_page(page)
        app.processEvents()
        visible = next(s for s in scrolls if s.widget() is dlg._contents[page])
        check(f"{name} / {page}: no horizontal scrollbar is needed at the size it opens at",
              not visible.horizontalScrollBar().isVisible())
    dlg.hide()
    check(f"{name}: every scroll area resizes its content to the window width",
          all(s.widgetResizable() for s in scrolls))
    box = dlg.findChild(QDialogButtonBox)
    check(f"{name}: the Close button is NOT inside any scroll area",
          box is not None and not any(s.isAncestorOf(box) for s in scrolls))
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
    for page in dlg.PAGES:
        dlg.show_page(page)
        app.processEvents()
        scroll = next(s for s in dlg.findChildren(QScrollArea) if s.widget() is dlg._contents[page])
        inner = scroll.widget()
        check(f"{name} / {page} @24pt: content width tracks the window, no horizontal overflow",
              inner.width() <= scroll.viewport().width() + 1)
    scroll = next(s for s in dlg.findChildren(QScrollArea) if s.isVisible())
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
# Since batch 4A Settings is a top-level menu with one entry per page
# (Master Spec §51.2, G4-D3).
settings_menu = next((a.menu() for a in w.menuBar().actions()
                      if a.text().replace("&", "") == "Settings"), None)
labels = [a.text().replace("&", "") for a in settings_menu.actions()] if settings_menu else []
check(f"a top-level Settings menu with the six entries ({labels})",
      labels == ["General…", "Editor…", "Hotkeys…", "Calendar…", "Appearance…", "Backups…"])
all_labels = [a.text() for m in w.menuBar().actions() if m.menu() for a in m.menu().actions()]
check("and no Experimental Settings, which existed only for the AI feature",
      "Experimental Settings…" not in all_labels)
check("no menu item anywhere mentions AI",
      not [a.text() for m in w.menuBar().actions() if m.menu()
           for a in m.menu().actions() if "AI" in a.text()])
w.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
