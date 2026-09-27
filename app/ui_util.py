"""Small layout helpers for keeping the interface responsive as the
application font size changes (Parts 13, 36 and 42).

The problem these solve is always the same one. Qt sizes most widgets from
their CONTENT: a font combo asks for the width of the longest font name on
the machine, a model dropdown asks for the width of its longest entry, a
word-wrapped label asks for something close to its unwrapped width. At the
default 10pt that is invisible. At 24pt those minimums add up to a window
wider than the screen — and because a widget's minimum width is a hard
floor for every layout above it, the result is a dialog that can't be made
narrow enough to fit, with its text running off the right-hand edge.

The fix in both cases is to tell Qt that the widget's preferred width is not
binding: the layout decides the width, and the widget adapts to it. A combo
then elides its text, and a label re-wraps. Neither is allowed to dictate
how wide the window has to be.

Used by the settings dialogs, and applicable anywhere else a long string
would otherwise set a floor under the window width.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLayout, QSizePolicy, QWidget

# The application font size the app's pixel budgets were chosen at. Anything
# expressed in pixels that has to hold TEXT is scaled from here rather than
# used as written — see font_scaled().
BASE_UI_POINT_SIZE = 10.0


def font_scaled(pixels: float, base: float = BASE_UI_POINT_SIZE) -> int:
    """Scales a pixel budget by the current application font size.

    Splitter panes are the case this exists for. A side pane that is a
    comfortable 340px at 10pt holds the same controls at 18pt in 340px —
    which is why the font dropdown reads "ejaVu Serif" and the month grid
    loses its last column at a large interface font. The pane is not too
    small in absolute terms; it is too small FOR THE TEXT IN IT, and that is
    what this converts between.

    Never used for a fixed layout offset (Part 20 rules those out
    everywhere) — only for "how much room should this pane start with".
    """
    app = QApplication.instance()
    size = app.font().pointSizeF() if app is not None else base
    if size <= 0:
        size = base
    return int(round(pixels * max(base, size) / base))


# Pane widths the user has dragged, in the settings table (Group 3, D1).
# The left month pane is ONE width shared by Daily Jorts and the Weekly
# Schedule (Master Spec §§31, 53.1); the Day Calendar pane is Daily Jorts'
# own. Absent = the default proportions.
MONTHLY_PANE_WIDTH_SETTING = "layout_monthly_pane_width"
DAY_PANE_WIDTH_SETTING = "layout_day_pane_width"


def saved_width(db, key: str):
    """A remembered pane width (0 = dragged shut), or None when the pane
    was never dragged. Also None when the database has been closed under a
    still-pending deferred layout pass (window closing): a layout preference
    is never worth an exception."""
    try:
        value = db.get_setting(key)
        return max(0, int(value)) if value not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def side_pane_width(preferred: float, total: float, max_fraction: float = 0.3,
                     minimum: float = 180.0) -> int:
    """How wide a fixed side pane should start out.

    Two rules, in tension, both real:
      * it needs room for its own text at the current font (font_scaled);
      * it must not crowd out the pane that does the actual work — the
        writing area, or Week View, which the spec says should occupy most
        of the space.

    So the font-scaled width wins until it would take more than
    `max_fraction` of the window, and after that the fraction caps it and
    the pane starts scrolling or abbreviating instead. The user can always
    drag the splitter; this only decides where it starts.
    """
    if total <= 0:
        return int(font_scaled(preferred))
    return int(max(minimum, min(font_scaled(preferred), total * max_fraction)))


def make_shrinkable_combo(combo: QComboBox, visible_chars: int = 10) -> QComboBox:
    """Lets a combo box be narrower than its longest item, eliding instead.

    `visible_chars` is roughly how much text it asks to keep visible before
    eliding — a hint, not a floor, since the horizontal policy below means
    the layout can still go narrower when it has to.
    """
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(visible_chars)
    # Expanding, NOT Ignored. Ignored discards the size hint entirely, which
    # in a QFormLayout lets the field column collapse to nothing — the combo
    # renders as a zero-width sliver and the setting looks missing. Expanding
    # keeps the (now small, thanks to the adjust policy above) hint as a
    # floor and takes any spare width, which is the behavior wanted here.
    policy = combo.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Expanding)
    combo.setSizePolicy(policy)
    return combo


def remeasure_hidden(page: QWidget) -> None:
    """Makes a page that is not on screen (a workspace tab in the
    background) report the minimum size of the CURRENT font again.

    Qt caches size hints in the layouts, and a font change clears those
    caches through updateGeometry(), which stops short of the layouts of a
    page that is not shown. After a font change the pages in the
    background kept the minimums of the font before — and a QTabWidget's
    minimum is the largest of all its pages, so after 20pt → 10pt the window
    refused to get narrower than the 20pt layout needed until each tab had
    been visited (bug 26 / D10). Both caches are cleared: updateGeometry()
    on every widget (the size a layout keeps for each widget it holds) and
    invalidate() on every layout (the totals the layout keeps for itself);
    measured, each alone leaves some of the old minimums in place. Nothing
    is resized or shown.
    """
    for widget in [page, *page.findChildren(QWidget)]:
        widget.updateGeometry()
    for layout in page.findChildren(QLayout):
        layout.invalidate()


def wrapping_label(text: str = "", object_name: str | None = None) -> QLabel:
    """A word-wrapped label that re-wraps to whatever width it's given.

    A plain `setWordWrap(True)` label still reports a wide minimum size
    hint, which is what pushes explanatory text off the right side of a
    dialog at a large font size. Ignoring its horizontal hint (while keeping
    height-for-width, so it still gets the height its wrapped text needs)
    makes the text fill the width available and grow downwards — where a
    scroll area can deal with it.
    """
    label = QLabel(text)
    label.setWordWrap(True)
    if object_name:
        label.setObjectName(object_name)
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Ignored)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label


class YieldingHintLabel(QLabel):
    """A word-wrapped hint that gives way to the widgets above it.

    A wrapped QLabel is height-for-width, and a vertical layout treats a
    height-for-width child's whole preferred height as its minimum — so a
    hint under a month grid kept its lines while the grid lost its last week
    at large fonts (Group 3 fixes, C3; Master Spec §54). This label asks for
    its full wrapped height (so it shows in full whenever there is room) but
    its minimum is nothing, so it is the first thing to give up height when
    there isn't."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def hasHeightForWidth(self) -> bool:
        return False

    def sizeHint(self):
        hint = super().sizeHint()
        width = self.width() if self.width() > 1 else hint.width()
        hint.setHeight(super().heightForWidth(width))
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setHeight(0)
        return hint

    def resizeEvent(self, event):
        # The wrapped height depends on the width: tell the layout again
        # when the width changes, since it no longer asks per width.
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()
