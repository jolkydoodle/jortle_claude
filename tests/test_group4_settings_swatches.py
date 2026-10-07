"""Group 4, fix batch 4A-F1 — the colour swatches wrap, captions under swatches.

4A's Settings window gained a page list (4A-D8). At UI font 24 on an 800 px
screen that left the Appearance page with a width floor of one row of five
swatches and their captions, which overflowed on ubuntu's fonts (CI run
37354402820). Each swatch and its caption are now one unit, in a flow that
wraps (4A-F1-D1, D2); the captions sit centred under their swatches (bug 28's
caption part, 4A-F1-D4). Criteria A-F1-1…A-F1-5 (JORTLE_IMPLEMENTATION_HANDOFF.md,
"4A-F1 plan — agreed 2026-10-06"); A-F1-6's teeth are the batch report's.
"""
import json
import os
import pathlib
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-swatches-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QScrollArea  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import settings_dialog as sd_module  # noqa: E402
from app.database import Database  # noqa: E402
from app.paths import get_data_dir  # noqa: E402
from app.settings_dialog import SWATCH_FIELDS, SettingsDialog  # noqa: E402
from app.theme import build_stylesheet, scheme_from_json  # noqa: E402

print("IMPORTED app FROM", sd_module.__file__)
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle():
    app.processEvents()
    app.processEvents()


db = Database()
ORIGINAL_SIZE = app.font().pointSize()


def at_font(size, styled=True):
    """The interface font and the app's stylesheet for it, in the order
    MainWindow._apply_settings uses (4A-F1/AM-4: the stylesheet changes the
    captions' size, so a dialog built without it is not the app's dialog).
    styled=False: no stylesheet at all, as test_round25_settings builds the
    dialog — where 4A's CI failure was measured (4A-F1/AM-5)."""
    if not styled:
        app.setStyleSheet("")
        font = app.font()
        font.setPointSize(size)
        app.setFont(font)
        settle()
        return
    sheet = build_stylesheet(scheme_from_json(db.get_setting("color_scheme")), size)
    if not app.styleSheet():
        app.setStyleSheet(sheet)
    font = app.font()
    font.setPointSize(size)
    app.setFont(font)
    app.setStyleSheet(sheet)
    settle()


def open_dialog(width=None):
    dlg = SettingsDialog(db, on_change=lambda: None)
    dlg.resize(*dlg._starting_size())
    if width is not None:
        dlg.resize(width, dlg.height())
    dlg.show()
    dlg.show_page("appearance")
    settle()
    return dlg


def scroll_of(dlg, page):
    return next(s for s in dlg.findChildren(QScrollArea) if s.widget() is dlg._contents[page])


def swatches(dlg):
    """(field, swatch button, caption label) for each colour, found by what
    the user sees, so the same check reads the code before and after 4A-F1."""
    content = dlg._contents["appearance"]
    out = []
    for field, caption_text in SWATCH_FIELDS:
        button = dlg.swatch_buttons[field]
        caption = next(lbl for lbl in content.findChildren(QLabel) if lbl.text() == caption_text)
        out.append((field, button, caption))
    return out


def rect_in(widget, ancestor):
    top_left = widget.mapTo(ancestor, QPoint(0, 0))
    return QRect(top_left, widget.size())


def rows(dlg):
    content = dlg._contents["appearance"]
    return sorted({rect_in(b, content).y() for _f, b, _c in swatches(dlg)})


# ================================================================ A-F1-1, A-F1-2
# Twice (4A-F1/AM-5): unstyled, as test_round25_settings builds the dialog and
# where 4A's ubuntu CI failure was measured — this run has the teeth
# (da14c66 needs 781 px there); and styled, as the app shows it — a
# preservation check, since the real app on Windows never overflowed
# (da14c66: 694 of 760 px), while CI's styled run shows ubuntu's.
screen = app.primaryScreen().availableGeometry()
check(f"(precondition) the offscreen screen is 800 px wide ({screen.width()})", screen.width() == 800)
for styled in (False, True):
    how = "styled" if styled else "unstyled"
    print(f"\n--- [A-F1-1, {how}] UI font 24 on an 800 px screen: the window fits with headroom, "
          f"every page fits ---")
    at_font(24, styled=styled)
    check(f"({how}: the application stylesheet is {'set' if styled else 'empty'})",
          bool(app.styleSheet()) == styled)
    dlg = open_dialog()
    # 4A-F1/AM-3: the headroom is measured against the screen limit. The
    # window's needed width is its widest page's minimum plus its chrome (as
    # _starting_size measures it); the cap is the screen's width less the
    # 40 px _starting_size leaves.
    needed = (max(c.minimumSizeHint().width() for c in dlg._contents.values()) + dlg._chrome_width())
    cap = screen.width() - 40
    margin = max(40, cap * 5 // 100)
    check(f"{how}: the window needs {needed}px, at most the {cap}px screen cap less {margin}px",
          needed <= cap - margin, f"headroom {cap - needed}px")
    for page in SettingsDialog.PAGES:
        dlg.show_page(page)
        settle()
        scroll = scroll_of(dlg, page)
        viewport = scroll.viewport().width()
        need = dlg._contents[page].minimumSizeHint().width()
        check(f"{how} {page}: content needs {need}px of a {viewport}px viewport, no horizontal scrollbar",
              need <= viewport and not scroll.horizontalScrollBar().isVisible(),
              f"scrollbar {scroll.horizontalScrollBar().isVisible()}")

    print(f"\n--- [A-F1-2, {how}] ...where the swatches wrap onto more rows ---")
    dlg.show_page("appearance")
    settle()
    content = dlg._contents["appearance"]
    inside = all(content.rect().contains(rect_in(w, content)) for _f, b, c in swatches(dlg) for w in (b, c))
    check(f"{how} 24pt: the five swatches take at least two rows ({len(rows(dlg))}), all within the page",
          len(rows(dlg)) >= 2 and inside)
    dlg.close()

# ================================================================ A-F1-3
print("\n--- [A-F1-3] a wide window keeps them on one row ---")
at_font(9)
dlg = open_dialog(width=1000)
reached = dlg.width()
check(f"9pt: the window reached the width asked for (1000 px; measured {reached} px, 4A-F1/AM-1)",
      reached == 1000)
check(f"9pt, {reached} px: all five swatches on one row ({rows(dlg)})", len(rows(dlg)) == 1)
dlg.close()

# ================================================================ A-F1-4
print("\n--- [A-F1-4] each caption centred under its own swatch, nothing clipped ---")


def caption_checks(dlg, where):
    content = dlg._contents["appearance"]
    units = []
    for field, button, caption in swatches(dlg):
        b, c = rect_in(button, content), rect_in(caption, content)
        text_width = QFontMetrics(caption.font()).horizontalAdvance(caption.text())
        offset = abs((b.x() + b.width() / 2) - (c.x() + c.width() / 2))
        unit = button.parentWidget()
        u = rect_in(unit, content)
        check(f"{where} {field}: caption centred under its swatch (offset {offset:.1f}px), "
              f"not clipped ({c.width()} ≥ {text_width}), swatch 32×24 inside its unit",
              offset <= 1 and c.width() >= text_width and caption.alignment() & Qt.AlignHCenter
              and (button.width(), button.height()) == (32, 24)
              and unit is caption.parentWidget() and unit is not content
              and u.contains(b) and u.contains(c),
              f"swatch {b}, caption {c}, unit {u}")
        units.append(u)
    overlaps = [(i, j) for i in range(len(units)) for j in range(i + 1, len(units))
                if units[i].intersects(units[j])]
    check(f"{where}: no two units overlap", not overlaps, overlaps)


for size in (9, 13, 24):
    at_font(size)
    dlg = open_dialog()
    caption_checks(dlg, f"{size}pt starting size,")
    # A narrow width that forces wrapping: the page list and chrome plus room
    # for about two and a half units.
    unit = max(max(QFontMetrics(c.font()).horizontalAdvance(c.text()), 32) for _f, _b, c in swatches(dlg))
    dlg.setMinimumSize(0, 0)
    dlg.resize(dlg._chrome_width() + int(unit * 2.5) + 40, dlg.height())
    settle()
    check(f"{size}pt narrow ({dlg.width()} px): the swatches wrap ({len(rows(dlg))} rows)", len(rows(dlg)) >= 2)
    caption_checks(dlg, f"{size}pt narrow,")
    dlg.close()

# ================================================================ 4A-F1/AM-4 (3)
print("\n--- [AM-4] every page opens with no vertical scrollbar where the screen has room ---")
for size in (9, 13, 24):
    at_font(size)
    dlg = open_dialog()
    height_cap = screen.height() - 80          # what _starting_size leaves
    room = dlg.height() < height_cap
    check(f"{size}pt: (precondition) the window opened below the screen's height cap "
          f"({dlg.height()} < {height_cap} px), so there was room", room)
    for page in SettingsDialog.PAGES:
        dlg.show_page(page)
        settle()
        scroll = scroll_of(dlg, page)
        viewport = scroll.viewport()
        need = dlg._contents[page].heightForWidth(viewport.width())
        check(f"{size}pt {page}: opens with no vertical scrollbar (needs {need}px of {viewport.height()}px)",
              not scroll.verticalScrollBar().isVisible() and need <= viewport.height())
    dlg.close()

# ================================================================ A-F1-5
print("\n--- [A-F1-5] each swatch still picks its own colour ---")
at_font(ORIGINAL_SIZE)
changes = []
dlg = SettingsDialog(db, on_change=lambda: changes.append(1))
dlg.show()
dlg.show_page("appearance")
settle()
original_get = QColorDialog.getColor
for index, (field, button, _caption) in enumerate(swatches(dlg)):
    colour = f"#1{index}2{index}3{index}"
    QColorDialog.getColor = staticmethod(lambda *a, c=colour, **k: QColor(c))
    count = len(changes)
    QTest.mouseClick(button, Qt.LeftButton)
    settle()
    stored = json.loads(db.get_setting("color_scheme"))
    check(f"{field}: a click picks its colour, stores it and switches to Custom",
          stored.get(field) == colour and stored.get("name") == "Custom"
          and dlg.scheme_combo.currentText() == "Custom" and len(changes) == count + 1
          and colour in button.styleSheet(), stored)
QColorDialog.getColor = original_get
dlg.close()

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
