"""The rich-text stress document (Group 4, batch 4-0; Master Spec §75).

One deliberately complex document holding every format the editor has today,
built through the editor's own controls, and a fingerprint that reads back
what a stored document actually contains. `test_group4_roundtrip.py` runs it
through save/reload, navigation, restart, backup/restore and the archive.

HOW IT IS BUILT. Every section contributes paragraphs of plain text, which are
typed first with real key events (QTest), and then formats some of them with
the editor's real controls: keyboard shortcuts where the editor has them
(Ctrl+B/I/U, Ctrl+Shift+L/E/R/J), the toolbar's own QActions and widgets
otherwise. Placing the caret stands in for a mouse click (`Kit.place`);
selections are extended with real Shift+Ctrl+Right key events. Only the
static dialogs a control opens (colour, link, photo) are answered.

HOW IT IS CHECKED. `fingerprint()` reads every paragraph's and every text
run's formatting from a QTextDocument; each section lists the properties it
must find there (`Prop`). A property names the text it looks for and the
attributes that text must have.

ADDING A SECTION (later batches: nested and lettered lists, semantic quotes,
resized images, …): insert a `Section` into `SECTIONS` before the photo
section, which must stay last (inserting a photo adds paragraphs, and the
photo section expects the document's last paragraph to be blank). Nothing
else needs to change; the suite iterates this list, and its guard check
fails if a section lists no property or a property is never found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QAction, QColor, QFontDatabase, QImage, QKeyEvent, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextListFormat,
                           QWheelEvent)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QFileDialog, QInputDialog

# ------------------------------------------------------------------ fingerprint

_ALIGN_NAMES = ((Qt.AlignJustify, "justify"), (Qt.AlignHCenter, "center"), (Qt.AlignRight, "right"))
_LIST_NAMES = {QTextListFormat.ListDisc: "disc", QTextListFormat.ListCircle: "circle",
               QTextListFormat.ListSquare: "square", QTextListFormat.ListDecimal: "decimal",
               QTextListFormat.ListLowerAlpha: "lower-alpha", QTextListFormat.ListUpperAlpha: "upper-alpha",
               QTextListFormat.ListLowerRoman: "lower-roman", QTextListFormat.ListUpperRoman: "upper-roman"}


def _alignment(fmt: QTextBlockFormat) -> str:
    horizontal = fmt.alignment() & Qt.AlignHorizontal_Mask
    for flag, name in _ALIGN_NAMES:
        if (horizontal & flag) == flag:
            return name
    return "left"


def _colour(brush) -> Optional[str]:
    if brush.style() == Qt.NoBrush:
        return None
    colour = brush.color()
    if colour.alpha() == 0:
        return "transparent"
    return colour.name(QColor.HexArgb) if colour.alpha() != 255 else colour.name()


@dataclass(frozen=True)
class Block:
    index: int
    text: str
    heading: int
    align: str
    line_height: float
    line_height_type: int
    top: float
    bottom: float
    left: float
    right: float
    indent: int
    list_style: Optional[str]


@dataclass(frozen=True)
class Run:
    block: int
    text: str
    families: tuple
    size: float
    weight: int
    italic: bool
    underline: bool
    strike: bool
    valign: str
    fg: Optional[str]
    bg: Optional[str]
    href: str
    image: str


@dataclass(frozen=True)
class Fingerprint:
    blocks: tuple
    runs: tuple

    def block_texts(self) -> list:
        return [b.text for b in self.blocks]


def fingerprint(document: QTextDocument) -> Fingerprint:
    """Every paragraph's and every run's formatting, as stored values."""
    blocks, runs = [], []
    block = document.begin()
    index = 0
    while block.isValid():
        fmt = block.blockFormat()
        text_list = block.textList()
        blocks.append(Block(
            index=index, text=block.text().replace("￼", ""), heading=fmt.headingLevel(),
            align=_alignment(fmt), line_height=round(fmt.lineHeight(), 2),
            line_height_type=fmt.lineHeightType(), top=round(fmt.topMargin(), 2),
            bottom=round(fmt.bottomMargin(), 2), left=round(fmt.leftMargin(), 2),
            right=round(fmt.rightMargin(), 2), indent=fmt.indent(),
            list_style=_LIST_NAMES.get(text_list.format().style(), str(text_list.format().style()))
            if text_list else None,
        ))
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                cf = fragment.charFormat()
                valign = {QTextCharFormat.AlignSuperScript: "super",
                          QTextCharFormat.AlignSubScript: "sub"}.get(cf.verticalAlignment(), "normal")
                image = cf.toImageFormat().name() if cf.isImageFormat() else ""
                runs.append(Run(
                    block=index, text=fragment.text().replace("￼", ""),
                    families=tuple(cf.fontFamilies() or ()), size=round(cf.fontPointSize(), 2),
                    weight=int(cf.fontWeight()), italic=cf.fontItalic(), underline=cf.fontUnderline(),
                    strike=cf.fontStrikeOut(), valign=valign, fg=_colour(cf.foreground()),
                    bg=_colour(cf.background()), href=cf.anchorHref() if cf.isAnchor() else "",
                    image=image,
                ))
            it += 1
        block = block.next()
        index += 1
    return Fingerprint(tuple(blocks), tuple(runs))


# --------------------------------------------------------------------- properties

@dataclass
class Prop:
    """One thing a stored document must contain.

    kind "block": some paragraph whose text contains `text` has every attribute
    in `attrs`. kind "run": some text run containing `text` does. kind "at":
    the paragraph at position `index` (negative counts from the end) has them;
    position checks are skipped for archive pages, which wrap the document in
    page chrome. An attribute value may be a callable that receives the actual
    value and returns whether it is acceptable.
    """
    label: str
    kind: str
    text: str = ""
    attrs: dict = field(default_factory=dict)
    index: int = 0
    in_archive: bool = True
    test: Optional[Callable] = None          # kind "custom": test(fingerprint) -> bool
    # Known failures (4-0/D5): where the property is known to be lost today,
    # and the bug that loses it, e.g. {"stored": "bug 33 → 4C1"}. Places:
    # "stored" (the stored document) and "archive" (the readable archive).
    known: dict = field(default_factory=dict)

    def _fits(self, item) -> bool:
        for name, wanted in self.attrs.items():
            actual = getattr(item, name)
            if callable(wanted):
                if not wanted(actual):
                    return False
            elif actual != wanted:
                return False
        return True

    def found(self, fp: Fingerprint) -> bool:
        if self.kind == "custom":
            return bool(self.test(fp))
        if self.kind == "at":
            if not fp.blocks or not (-len(fp.blocks) <= self.index < len(fp.blocks)):
                return False
            block = fp.blocks[self.index]
            return self.text in block.text if self.text else block.text == "" and self._fits(block)
        items = fp.blocks if self.kind == "block" else fp.runs
        return any(self.text in item.text and self._fits(item) for item in items)


# --------------------------------------------------------------------------- kit

def type_into(widget, text: str):
    """Types `text` with key events. ASCII goes through QTest.keyClicks; a tab
    is the Tab key; any other character (accents, CJK, emoji) is a key event
    carrying that text, which is how a keyboard layout or an input method
    delivers it. (QTest.keyClicks ends the whole process, with exit code 127
    and no message, on a character such as "日" or "🙂" — measured on
    Windows, 2026-10-01.)"""
    widget.setFocus()
    ascii_run = []

    def flush():
        if ascii_run:
            QTest.keyClicks(widget, "".join(ascii_run))
            ascii_run.clear()

    for char in text:
        if char == "\t":
            flush()
            QTest.keyClick(widget, Qt.Key_Tab)
        elif ord(char) < 128:
            ascii_run.append(char)
        else:
            flush()
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                QApplication.sendEvent(widget, QKeyEvent(kind, 0, Qt.NoModifier, char))
    flush()


class Kit:
    """The editor's controls, driven the way a user drives them."""

    def __init__(self, editor, attachments_root: Path, settle: Callable[[], None]):
        self.editor = editor
        self.edit = editor.text_edit
        self.attachments_root = Path(attachments_root)
        self.settle = settle

    # caret and selection
    def block(self, index: int):
        return self.edit.document().findBlockByNumber(index)

    def index_of(self, text: str) -> int:
        block = self.edit.document().begin()
        while block.isValid():
            if block.text() == text:
                return block.blockNumber()
            block = block.next()
        raise LookupError(f"no paragraph {text!r}")

    def place(self, index: int, offset: int = 0):
        """Put the caret in a paragraph — what a mouse click there does."""
        cursor = QTextCursor(self.block(index))
        cursor.setPosition(self.block(index).position() + offset)
        self.edit.setTextCursor(cursor)
        self.edit.setFocus()
        self.settle()

    def select_words(self, paragraph: str, first_word: str, words: int = 1):
        """Select `words` words of a paragraph, starting at `first_word`, with
        Shift+Ctrl+Right key presses (each also takes the following space)."""
        index = self.index_of(paragraph)
        self.place(index, paragraph.index(first_word))
        for _ in range(words):
            QTest.keyClick(self.edit, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
        self.settle()

    def select_paragraphs(self, first: str, last: str):
        start, end = self.index_of(first), self.index_of(last)
        self.place(start)
        for _ in range(end - start):
            QTest.keyClick(self.edit, Qt.Key_Down, Qt.ShiftModifier)
        QTest.keyClick(self.edit, Qt.Key_End, Qt.ShiftModifier)
        self.settle()

    # keys and controls
    def key(self, key, modifiers=Qt.NoModifier):
        QTest.keyClick(self.edit, key, modifiers)
        self.settle()

    def type(self, text: str):
        """Real key events; a tab character is sent as the Tab key."""
        type_into(self.edit, text)
        self.settle()

    def action(self, tooltip_start: str) -> QAction:
        for action in self.editor.findChildren(QAction):
            if action.toolTip().startswith(tooltip_start) or action.text() == tooltip_start:
                return action
        raise LookupError(f"no toolbar action {tooltip_start!r}")

    def trigger(self, tooltip_start: str):
        self.action(tooltip_start).trigger()
        self.settle()

    def with_colour(self, colour: str, tooltip_start: str):
        original = QColorDialog.getColor
        QColorDialog.getColor = staticmethod(lambda *a, **k: QColor(colour))
        try:
            self.trigger(tooltip_start)
        finally:
            QColorDialog.getColor = original

    def with_link(self, url: str):
        original = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: (url, True))
        try:
            self.trigger("Insert Link")
        finally:
            QInputDialog.getText = original

    def with_photo(self, path: Path):
        original = QFileDialog.getOpenFileNames
        QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(path)], ""))
        try:
            self.trigger("Insert Photo")
        finally:
            QFileDialog.getOpenFileNames = original

    def paste_external(self, html: str):
        """Pastes HTML from 'another application': a Python-built QMimeData
        handed to the editor's own paste path, never to the clipboard
        (4C2/AM-5)."""
        from PySide6.QtCore import QMimeData
        mime = QMimeData()
        mime.setHtml(html)
        self.edit.insertFromMimeData(mime)
        self.settle()

    def combo(self, combo, index: int):
        combo.setCurrentIndex(index)
        self.settle()

    def spin(self, spin, value):
        spin.setValue(value)
        self.settle()

    def ctrl_wheel(self, notches: int):
        """Ctrl+wheel through Qt's own event delivery (not OS input)."""
        viewport = self.edit.viewport()
        centre = QPointF(viewport.width() / 2, viewport.height() / 2)
        for _ in range(abs(notches)):
            event = QWheelEvent(centre, QPointF(viewport.mapToGlobal(centre.toPoint())),
                                QPoint(0, 0), QPoint(0, 120 if notches > 0 else -120),
                                Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(viewport, event)
        self.settle()


# ------------------------------------------------------------------------ sections

@dataclass
class Section:
    """A part of the stress document.

    `paragraphs`: the plain text it types (in order, one per paragraph).
    `apply(kit, ctx)`: formats them with the editor's controls.
    `expect(ctx)`: the properties a stored copy must contain.
    `ctx` is a dict: "context" is "journal" or "project" (journal documents
    link dates when saved); "families" the two font families to use;
    "photo" the PNG to insert; "compact_appended" is set for Reader's Notes,
    which carry the compact section after the stress document.
    """
    name: str
    paragraphs: list
    apply: Callable
    expect: Callable


def _families() -> tuple:
    """Two installed families, one with a space in its name if there is one,
    chosen the same way on every run of one machine."""
    families = sorted(f for f in QFontDatabase.families() if not f.startswith("."))
    spaced = [f for f in families if " " in f]
    plain = [f for f in families if " " not in f]
    first = spaced[0] if spaced else families[0]
    second = next((f for f in plain if f != first), families[-1])
    return first, second


def make_photo(folder: Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "stress-photo.png"
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor("#2e86c1"))
    image.save(str(path))
    return path


def context(kind: str, photo: Path) -> dict:
    return {"context": kind, "families": _families(), "photo": Path(photo)}


# --- the sections. Paragraph texts are unique so a property can find them.

TEXT = "Café naïve façade — 日本語 🙂 ✓"

# Known failures recorded by the user (4-0/AM-7). Bugs 33 and 37 were fixed in
# 4C1b (whole-number spacing; Qt's heading size bump removed at load).
BUG_34 = "bug 34 → Group 7"   # the archive collapses tabs


# The content in this document that each known RELOAD change acts on (4-0-F1):
# the first reload + unedited Ctrl+S rewrites a document that holds it. Read
# from the live editor's document BEFORE its first save, never from the first
# save, so a change that alters the first save cannot hide the known failure.
# {bug: (what the content is, test(QTextDocument, writing size))}. None at
# present: bugs 33 and 37, the two it held, were fixed in 4C1b.
RELOAD_TRIGGERS = {}


def _blank_apply(kit, ctx):
    pass


def _blank_expect(ctx):
    return [
        Prop("a blank paragraph first", "at", index=0, in_archive=False),
        Prop("one blank paragraph, then text", "at", index=2, in_archive=False),
        Prop("two blank paragraphs in a row (the first)", "at", index=4, in_archive=False),
        Prop("two blank paragraphs in a row (the second)", "at", index=5, in_archive=False),
        Prop("the text after them", "at", text="After two blank lines", index=6, in_archive=False),
        Prop("accents and emoji typed as text", "block", text=TEXT),
    ]


def _tabs_expect(ctx):
    collapsed = {"archive": BUG_34}                 # the archive drops white-space: pre-wrap
    return [Prop("a literal tab at the start of a paragraph", "block", text="\tTab at start", known=collapsed),
            Prop("a literal tab in mid-text", "block", text="Mid\ttab here", known=collapsed)]


def _fonts_apply(kit, ctx):
    first, second = ctx["families"]
    from PySide6.QtGui import QFont
    kit.select_words("alpha familyone omega", "familyone")
    kit.editor.family_combo.setCurrentFont(QFont(first)); kit.settle()
    kit.select_words("alpha familytwo omega", "familytwo")
    kit.editor.family_combo.setCurrentFont(QFont(second)); kit.settle()
    for word, size in (("tinyword", 8), ("largeword", 20), ("hugeword", 72)):
        kit.select_words(f"alpha {word} omega", word)
        kit.spin(kit.editor.size_spin, size)


def _fonts_expect(ctx):
    first, second = ctx["families"]
    return [Prop(f"font family with a space ({first})", "run", "familyone", {"families": lambda f: f[:1] == (first,)}),
            Prop(f"a second font family ({second})", "run", "familytwo", {"families": lambda f: f[:1] == (second,)}),
            Prop("size 8", "run", "tinyword", {"size": 8.0}),
            Prop("size 20", "run", "largeword", {"size": 20.0}),
            Prop("size 72", "run", "hugeword", {"size": 72.0})]


def _headings_apply(kit, ctx):
    for level, text in ((1, "Heading level one"), (2, "Heading level two"), (3, "Heading level three")):
        kit.place(kit.index_of(text))
        kit.combo(kit.editor.heading_combo, level)


def _headings_expect(ctx):
    return [Prop(f"Heading {n}", "block", t, {"heading": n}) for n, t in
            ((1, "Heading level one"), (2, "Heading level two"), (3, "Heading level three"))]


def _inline_apply(kit, ctx):
    for word, key in (("boldword", Qt.Key_B), ("italicword", Qt.Key_I), ("underlineword", Qt.Key_U)):
        kit.select_words(f"alpha {word} omega", word)
        kit.key(key, Qt.ControlModifier)
    for word, tip in (("strikeword", "Strikethrough"), ("superword", "Superscript"), ("subword", "Subscript")):
        kit.select_words(f"alpha {word} omega", word)
        kit.trigger(tip)
    kit.select_words("alpha mixedword omega", "mixedword")
    for key in (Qt.Key_B, Qt.Key_I, Qt.Key_U):
        kit.key(key, Qt.ControlModifier)
    kit.select_words("alpha overlapone overlaptwo omega", "overlapone", 2)
    kit.key(Qt.Key_B, Qt.ControlModifier)
    kit.select_words("alpha overlapone overlaptwo omega", "overlaptwo")
    kit.key(Qt.Key_I, Qt.ControlModifier)


def _inline_expect(ctx):
    bold = lambda w: w >= 600  # noqa: E731
    return [Prop("bold", "run", "boldword", {"weight": bold}),
            Prop("italic", "run", "italicword", {"italic": True}),
            Prop("underline", "run", "underlineword", {"underline": True}),
            Prop("strikethrough", "run", "strikeword", {"strike": True}),
            Prop("superscript", "run", "superword", {"valign": "super"}),
            Prop("subscript", "run", "subword", {"valign": "sub"}),
            Prop("bold + italic + underline on one word", "run", "mixedword",
                 {"weight": bold, "italic": True, "underline": True}),
            Prop("overlapping formats: bold alone", "run", "overlapone", {"weight": bold, "italic": False}),
            Prop("overlapping formats: bold and italic", "run", "overlaptwo", {"weight": bold, "italic": True})]


def _colour_apply(kit, ctx):
    kit.select_words("alpha colourword omega", "colourword")
    kit.with_colour("#8e44ad", "Text color")
    kit.select_words("alpha presetmark omega", "presetmark")
    kit.trigger("Yellow")
    kit.select_words("alpha custommark omega", "custommark")
    kit.with_colour("#a0e0ff", "Highlight")
    kit.select_words("alpha clearedword omega", "clearedword")
    kit.key(Qt.Key_B, Qt.ControlModifier)
    kit.key(Qt.Key_I, Qt.ControlModifier)
    kit.trigger("Clear formatting")


def _colour_expect(ctx):
    return [Prop("text colour", "run", "colourword", {"fg": "#8e44ad"}),
            Prop("highlight preset (Yellow)", "run", "presetmark", {"bg": "#ffe066"}),
            Prop("custom highlight", "run", "custommark", {"bg": "#a0e0ff"}),
            Prop("Clear Formatting removed bold and italic", "run", "clearedword",
                 {"weight": lambda w: w < 600, "italic": False})]


def _align_apply(kit, ctx):
    kit.place(kit.index_of("Centred paragraph text"))
    kit.key(Qt.Key_E, Qt.ControlModifier | Qt.ShiftModifier)
    kit.place(kit.index_of("Right aligned paragraph text"))
    kit.key(Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier)
    kit.place(kit.index_of("Justified paragraph text"))
    kit.editor.align_justify_btn.trigger(); kit.settle()
    kit.place(kit.index_of("Left again paragraph text"))
    kit.key(Qt.Key_E, Qt.ControlModifier | Qt.ShiftModifier)
    kit.editor.align_left_btn.trigger(); kit.settle()


def _align_expect(ctx):
    return [Prop("centred (Ctrl+Shift+E)", "block", "Centred paragraph text", {"align": "center"}),
            Prop("right (Ctrl+Shift+R)", "block", "Right aligned paragraph text", {"align": "right"}),
            Prop("justified (button)", "block", "Justified paragraph text", {"align": "justify"}),
            Prop("centred, then left again (button)", "block", "Left again paragraph text", {"align": "left"})]


_SPACINGS = (("Line spacing single", 0, 100.0), ("Line spacing one fifteen", 1, 115.0),
             ("Line spacing one half", 2, 150.0), ("Line spacing double", 3, 200.0))


def _line_apply(kit, ctx):
    for text, index, _ in _SPACINGS:
        kit.place(kit.index_of(text))
        if index == 0:                      # the combo already shows Single: choose another first
            kit.combo(kit.editor.line_spacing_combo, 1)
        kit.combo(kit.editor.line_spacing_combo, index)


def _line_expect(ctx):
    proportional = QTextBlockFormat.ProportionalHeight.value
    return [Prop(f"line spacing {pct:g}%", "block", text, {"line_height": pct, "line_height_type": proportional})
            for text, _, pct in _SPACINGS]


def _spacing_apply(kit, ctx):
    kit.place(kit.index_of("Space six before"))
    kit.spin(kit.editor.space_before_spin, 6)
    kit.place(kit.index_of("Space twelve after"))
    kit.spin(kit.editor.space_after_spin, 12)
    kit.place(kit.index_of("Space before and after"))
    kit.spin(kit.editor.space_before_spin, 6)
    kit.spin(kit.editor.space_after_spin, 12)


def _spacing_expect(ctx):
    # Whole numbers only since 4C1b (bug 33): the spacing boxes take no fraction.
    return [Prop("no extra spacing", "block", "Space none at all", {"top": 0.0, "bottom": 0.0}),
            Prop("6 px before", "block", "Space six before", {"top": 6.0, "bottom": 0.0}),
            Prop("12 px after", "block", "Space twelve after", {"top": 0.0, "bottom": 12.0}),
            Prop("6 before and 12 after", "block", "Space before and after", {"top": 6.0, "bottom": 12.0})]


def _indent_apply(kit, ctx):
    kit.place(kit.index_of("Indent level one"))
    kit.trigger("Increase indent")
    kit.place(kit.index_of("Indent level two"))
    kit.trigger("Increase indent"); kit.trigger("Increase indent")
    kit.place(kit.index_of("Indent two then back one"))
    kit.trigger("Increase indent"); kit.trigger("Increase indent"); kit.trigger("Decrease indent")


def _indent_expect(ctx):
    return [Prop("indent level 1", "block", "Indent level one", {"left": 24.0}),
            Prop("indent level 2", "block", "Indent level two", {"left": 48.0}),
            Prop("indent 2, outdent 1", "block", "Indent two then back one", {"left": 24.0})]


def _lists_apply(kit, ctx):
    kit.select_paragraphs("Bullet item one", "Bullet item two")
    kit.trigger("Bullet list")
    kit.select_paragraphs("Number item one", "Number item two")
    kit.trigger("Numbered list")
    kit.place(kit.index_of("Quoted paragraph text"))
    kit.trigger("Quote")


def _lists_expect(ctx):
    return [Prop("bullet list item", "block", "Bullet item one", {"list_style": "disc"}),
            Prop("bullet list, second item", "block", "Bullet item two", {"list_style": "disc"}),
            Prop("numbered list item", "block", "Number item one", {"list_style": "decimal"}),
            Prop("numbered list, second item", "block", "Number item two", {"list_style": "decimal"}),
            Prop("quote (today's form: 24 pt each side)", "block", "Quoted paragraph text",
                 {"left": 24.0, "right": 24.0})]


def _links_apply(kit, ctx):
    kit.select_words("alpha linkword omega", "linkword")
    kit.with_link("https://example.org/jortle")


def _links_expect(ctx):
    props = [Prop("external link", "run", "linkword", {"href": "https://example.org/jortle"})]
    if ctx["context"] == "journal":
        props.append(Prop("a typed date became a date link", "run", "2026-02-14",
                          {"href": lambda h: h == "journal://date/2026-02-14" or h.endswith("entries/2026-02-14.html")}))
    else:
        props.append(Prop("no date link outside the journal", "run", "2026-02-14", {"href": ""}))
    return props


# Rich paste (4C2b): a web-like paste into the middle of a paragraph, and a
# Jortle → Jortle paste made with the editor's own Copy and Ctrl+V (the
# offscreen clipboard is the test process's own).
PASTED_HTML = ('<p style="font-family:Arial; font-size:20px; color:#202124">Pasted <b>pbold</b> '
               '<span style="color:#c00000">pred</span> <a href="https://example.org/pasted">plink</a></p>'
               '<h1 style="font-size:2em">Pasted heading</h1><p style="color:#5f6368">ptail</p>')


def _pasted_apply(kit, ctx):
    kit.place(kit.index_of("alpha webpaste omega"), len("alpha "))
    kit.paste_external(PASTED_HTML)
    kit.select_words("alpha boldword omega", "boldword")
    kit.key(Qt.Key_C, Qt.ControlModifier)
    kit.place(kit.index_of("alpha jortlepaste omega"), len("alpha "))
    kit.key(Qt.Key_V, Qt.ControlModifier)


def _pasted_expect(ctx):
    bold = lambda w: w >= 600  # noqa: E731

    def typed_font(fp):
        return next(((r.families, r.size) for r in fp.runs if r.text == "jortlepaste omega"), None)

    def in_typed_font(text, **attrs):
        """A pasted run in the same font as typed text (4C2/AM-6): read
        back, both show the document's font, whatever it is."""
        def test(fp):
            return any(text in r.text and (r.families, r.size) == typed_font(fp)
                       and all(getattr(r, k) == v if not callable(v) else v(getattr(r, k))
                               for k, v in attrs.items()) for r in fp.runs)
        return test

    def jortle_bold(fp):
        blocks = [b.index for b in fp.blocks if b.text == "alpha boldword jortlepaste omega"]
        return any(r.block in blocks and "boldword" in r.text and r.weight >= 600 for r in fp.runs)

    return [Prop("pasted bold, in the document's font", "custom", test=in_typed_font("pbold", weight=bold),
                 in_archive=False),
            Prop("pasted grey text keeps no colour, in the document's font", "custom",
                 test=in_typed_font("Pasted ", fg=None), in_archive=False),
            Prop("pasted chromatic colour kept", "run", "pred", {"fg": "#c00000"}),
            Prop("pasted link in Jortle's link style", "run", "plink",
                 {"href": "https://example.org/pasted", "fg": "#3f8ede", "underline": True}),
            Prop("pasted h1 is Heading 1", "block", "Pasted heading", {"heading": 1}),
            Prop("pasted heading text at Jortle's 20 pt, bold", "run", "Pasted heading",
                 {"size": 20.0, "weight": bold}, in_archive=False),
            Prop("the pasted-into paragraph's tail keeps its own format", "block", "ptailwebpaste omega",
                 {"heading": 0, "list_style": None}),
            Prop("Jortle → Jortle paste keeps the bold", "custom", test=jortle_bold)]


def _photo_apply(kit, ctx):
    kit.place(kit.index_of("Photo follows") + 1)
    kit.with_photo(ctx["photo"])
    image_block = kit.index_of("Photo follows") + 1
    kit.place(image_block)
    kit.key(Qt.Key_E, Qt.ControlModifier | Qt.ShiftModifier)


def _photo_expect(ctx):
    folder = r"\d{4}/\d{2}" if ctx["context"] == "journal" else r"projects/\d+"
    pattern = re.compile(folder + r"/[0-9a-f]{32}\.png$")
    is_photo = lambda n: bool(pattern.search(n))  # noqa: E731

    def photo_centred(fp):
        blocks = {r.block for r in fp.runs if is_photo(r.image)}
        return any(b.index in blocks and b.align == "center" for b in fp.blocks)

    return [Prop("an inline photo in the attachments folder", "run", "", {"image": is_photo}),
            Prop("the photo's paragraph centred", "custom", test=photo_centred)] + (
        [] if ctx.get("compact_appended") else      # Reader's Notes add their compact section after it
        [Prop("a blank paragraph last", "at", index=-1, in_archive=False)])


SECTIONS: list = [
    Section("blank paragraphs and plain text",
            ["", "Blank lines kept below", "", "One blank above", "", "", "After two blank lines", TEXT],
            _blank_apply, _blank_expect),
    Section("tabs", ["\tTab at start", "Mid\ttab here"], _blank_apply, _tabs_expect),
    Section("fonts and sizes", ["alpha familyone omega", "alpha familytwo omega", "alpha tinyword omega",
                                "alpha largeword omega", "alpha hugeword omega"], _fonts_apply, _fonts_expect),
    Section("headings", ["Heading level one", "Heading level two", "Heading level three"],
            _headings_apply, _headings_expect),
    Section("character formats", ["alpha boldword omega", "alpha italicword omega", "alpha underlineword omega",
                                  "alpha strikeword omega", "alpha superword omega", "alpha subword omega",
                                  "alpha mixedword omega", "alpha overlapone overlaptwo omega"],
            _inline_apply, _inline_expect),
    Section("colours and Clear Formatting", ["alpha colourword omega", "alpha presetmark omega",
                                             "alpha custommark omega", "alpha clearedword omega"],
            _colour_apply, _colour_expect),
    Section("alignment", ["Centred paragraph text", "Right aligned paragraph text",
                          "Justified paragraph text", "Left again paragraph text"], _align_apply, _align_expect),
    Section("line spacing", [t for t, _, _ in _SPACINGS], _line_apply, _line_expect),
    Section("paragraph spacing", ["Space none at all", "Space six before", "Space twelve after",
                                  "Space before and after"], _spacing_apply, _spacing_expect),
    Section("indentation", ["Indent level one", "Indent level two", "Indent two then back one"],
            _indent_apply, _indent_expect),
    Section("lists and quote", ["Bullet item one", "Bullet item two", "Number item one", "Number item two",
                                "Quoted paragraph text"], _lists_apply, _lists_expect),
    Section("links", ["alpha linkword omega", "Dated 2026-02-14 here"], _links_apply, _links_expect),
    Section("rich paste", ["alpha webpaste omega", "alpha jortlepaste omega"], _pasted_apply, _pasted_expect),
    # Last: inserting a photo adds paragraphs.
    Section("photo", ["Photo follows", "", "Last words", ""], _photo_apply, _photo_expect),
]


def build(editor, ctx: dict, settle: Callable[[], None]) -> Kit:
    """Types every section's paragraphs, then formats them section by section."""
    kit = Kit(editor, ctx["photo"].parent, settle)
    edit = editor.text_edit
    edit.setFocus()
    edit.moveCursor(QTextCursor.End)
    first = True
    for section in SECTIONS:
        for paragraph in section.paragraphs:
            if not first:
                QTest.keyClick(edit, Qt.Key_Return)
            first = False
            if paragraph:
                kit.type(paragraph)
    settle()
    for section in SECTIONS:
        section.apply(kit, ctx)
    return kit


def all_props(ctx: dict, sections=None) -> list:
    """(section name, Prop) for every property the given sections expect."""
    return [(section.name, prop) for section in (sections or SECTIONS) for prop in section.expect(ctx)]


def missing(fp: Fingerprint, ctx: dict, archive: bool = False, sections=None) -> list:
    """Labels of the expected properties not found in a fingerprint, leaving
    out the ones a recorded bug is known to lose there (see `known`)."""
    where = "archive" if archive else "stored"
    return [f"{name}: {prop.label}" for name, prop in all_props(ctx, sections)
            if (prop.in_archive or not archive) and where not in prop.known and not prop.found(fp)]


def known(fp: Fingerprint, ctx: dict, archive: bool = False, sections=None) -> dict:
    """{bug: (labels, all found)} for the properties a recorded bug loses there."""
    where = "archive" if archive else "stored"
    result = {}
    for name, prop in all_props(ctx, sections):
        if where in prop.known and (prop.in_archive or not archive):
            labels, found = result.get(prop.known[where], ([], True))
            result[prop.known[where]] = (labels + [f"{name}: {prop.label}"], found and prop.found(fp))
    return result


# --- the compact (Reader's Notes) section: built with the compact toolbar.

COMPACT_PARAGRAPHS = ["notes nboldword nstrikeword nsuperword nsubword end", "Notes bullet one", "Notes bullet two"]


def build_compact(editor, settle) -> None:
    """Appends the compact section at the end of a Reader's Notes editor,
    using only the compact toolbar's own actions (no Ctrl+B there today)."""
    kit = Kit(editor, Path("."), settle)
    edit = editor.text_edit
    edit.setFocus()
    edit.moveCursor(QTextCursor.End)
    for paragraph in COMPACT_PARAGRAPHS:
        QTest.keyClick(edit, Qt.Key_Return)
        kit.type(paragraph)
    first = COMPACT_PARAGRAPHS[0]
    for word, tip in (("nboldword", "Bold"), ("nstrikeword", "Strikethrough"),
                      ("nsuperword", "Superscript"), ("nsubword", "Subscript")):
        kit.select_words(first, word)
        kit.trigger(tip)
    kit.select_paragraphs("Notes bullet one", "Notes bullet two")
    kit.trigger("Bullet list")


def compact_props() -> list:
    bold = lambda w: w >= 600  # noqa: E731
    return [("compact toolbar", p) for p in (
        Prop("bold (compact toolbar)", "run", "nboldword", {"weight": bold}),
        Prop("strikethrough (compact toolbar)", "run", "nstrikeword", {"strike": True}),
        Prop("superscript (compact toolbar)", "run", "nsuperword", {"valign": "super"}),
        Prop("subscript (compact toolbar)", "run", "nsubword", {"valign": "sub"}),
        Prop("bulleted list (compact toolbar)", "block", "Notes bullet two", {"list_style": "disc"}),
    )]
