"""Rich paste: how HTML on the clipboard becomes text in the editor (batch 4C2b,
Master Spec §14.12; decisions 4C2b-D1…D5 and amendments 4C2/AM-5, AM-6).

The editor's one paste path (RichTextEditor.insertFromMimeData: Ctrl+V,
Edit → Paste, drag-and-drop) asks `document_from_mime` for a scratch
document holding what is to be inserted, and inserts it as one fragment in
one edit block (one undo step). This module only builds that scratch
document; it never touches the editor's own document.

Two kinds of source:

  * This app (or any Qt program, 4C2/AM-5): recognised by the
    `<meta name="qrichtext" content="1" />` Qt writes into its own HTML.
    Everything is kept as Qt reads it (4C2b-D3) — fonts as they looked in
    the source, colours, links, images with their attachment paths — except
    the size bump Qt adds to heading text it reads back, which the load path
    removes too (bug 37).

  * Everything else (browsers, Word, …): the HTML is read by Qt into a
    scratch document, and a second document is written from it keeping only
    what the editor's own formatting can express (4C2b-D4 as amended by AM-6):
    bold, italic, underline, strikethrough, superscript, subscript; chromatic
    text colours and highlights; links with a known scheme, in the editor's link
    style; headings as the editor's Heading 1–3; alignment, paragraph spacing
    (whole px), line height and lists as Qt reads them. Font family and size
    are never kept: the text takes the target document's font. Tables are
    flattened to one paragraph per row with tab-separated cells; images
    become files in the attachments folder (never downloaded from the
    network). Word's pseudo-lists (`mso-list` paragraphs) become real lists
    first (4C2b-D5).

The colour rule never reads the theme (FP-11): a colour is kept when it is
chromatic (HSL saturation at least GREY_SATURATION) and visible, whatever
the theme is, so pasting in Light and in Dark stores the same bytes.
"""
from __future__ import annotations

import base64
import binascii
import html as html_lib
import re
import shutil
import uuid
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QUrl
from PySide6.QtGui import (
    QColor, QFont, QImage, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextFormat, QTextImageFormat, QTextListFormat, QTextTable
)

# Qt's marker in the HTML it writes (QTextDocument.toHtml, and its copy).
JORTLE_MARKER = re.compile(r'<meta\s+name="qrichtext"\s+content="1"\s*/?>', re.IGNORECASE)

GREY_SATURATION = 0.15          # below this a colour counts as grey/black/white
MIN_LIGHTNESS, MAX_LIGHTNESS = 0.15, 0.85   # outside: nearly black / nearly white (AM-23)
LINK_COLOR = "#3f8ede"          # Insert Link's style (RichTextEditor.insert_link)
LINK_SCHEMES = ("http", "https", "mailto", "journal")
HEADING_SIZES = {1: 20, 2: 16, 3: 13}   # the editor's Heading 1–3 (RichEditor._apply_heading)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
DATA_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                    "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp"}
OBJECT_REPLACEMENT = "￼"


# ------------------------------------------------------------- the source
def is_jortle_html(html: str) -> bool:
    return bool(JORTLE_MARKER.search(html or ""))


def is_chromatic(color: QColor) -> bool:
    """A colour worth keeping (4C2b-D4, 4C2/AM-23, FP-11): visible, not a
    grey (HSL saturation at least GREY_SATURATION), and neither nearly black
    nor nearly white (HSL lightness within MIN_LIGHTNESS…MAX_LIGHTNESS) —
    those read like a grey in one of the themes, whatever their hue."""
    return (color.isValid() and color.alpha() > 0
            and color.hslSaturationF() >= GREY_SATURATION
            and MIN_LIGHTNESS <= color.lightnessF() <= MAX_LIGHTNESS)


def allowed_link(href: str) -> bool:
    scheme = QUrl(href).scheme().lower()
    return bool(href) and scheme in LINK_SCHEMES


# --------------------------------------------------------- Word clean-up
_DOWNLEVEL_LIST_MARKER = re.compile(r"<!\[if !supportLists\]>(.*?)<!\[endif\]>", re.S | re.I)
_DOWNLEVEL_TAGS = re.compile(r"<!\[(?:if [^\]]*|endif)\]>", re.I)
_OFFICE_P_TAGS = re.compile(r"</?o:p\s*>", re.I)
_PARAGRAPH = re.compile(r"<p\b([^>]*)>(.*?)</p\s*>", re.S | re.I)
_MSO_LIST = re.compile(r"mso-list\s*:\s*(l\d+)\s+level(\d+)\s+(lfo\d+)", re.I)
_TAG = re.compile(r"<[^>]+>")
_NUMBER_MARKER = re.compile(r"^\(?(\d+|[a-zA-Z]|[ivxlcdmIVXLCDM]+)[.)]$")


def _marker_style(marker_html: str) -> int:
    """The list style a Word list marker shows: a number or a letter (with
    '.' or ')') is a numbered list, anything else a bullet."""
    text = html_lib.unescape(_TAG.sub("", marker_html)).replace("\xa0", " ").strip()
    match = _NUMBER_MARKER.match(text)
    if not match:
        return QTextListFormat.ListDisc
    value = match.group(1)
    if value.isdigit():
        return QTextListFormat.ListDecimal
    if len(value) > 1:      # ii, iv, …: Roman numerals
        return QTextListFormat.ListLowerRoman if value.islower() else QTextListFormat.ListUpperRoman
    return QTextListFormat.ListLowerAlpha if value.islower() else QTextListFormat.ListUpperAlpha


_LIST_CSS = {
    QTextListFormat.ListDisc: ("ul", "disc"), QTextListFormat.ListDecimal: ("ol", "decimal"),
    QTextListFormat.ListLowerAlpha: ("ol", "lower-alpha"),
    QTextListFormat.ListUpperAlpha: ("ol", "upper-alpha"),
    QTextListFormat.ListLowerRoman: ("ol", "lower-roman"),
    QTextListFormat.ListUpperRoman: ("ol", "upper-roman"),
}


def word_lists_to_html_lists(html: str) -> str:
    """Word's pseudo-lists as real HTML lists (4C2b-D5).

    Word writes a list item as a paragraph with `mso-list:l1 level2 lfo1` in
    its style and the marker ("·", "1.", "a)") as text inside
    `<![if !supportLists]>…<![endif]>`; Qt reads neither, so the markers
    would arrive as text and the items as ordinary paragraphs. Consecutive
    list paragraphs become nested <ul>/<ol>: the level from `levelN`, a new
    list where the Word list (lN) or the marker kind changes, the marker
    itself removed. Bullet styles below the first level are Qt's own
    (circle, square), as for a list nested by hand."""
    out, pos = [], 0
    run = []                            # [(level, style, list id, inner html)]

    def flush():
        if not run:
            return
        parts, stack = [], []           # stack of (level, style, list id, tag)
        for level, style, list_id, inner, margins in run:
            while stack and (stack[-1][0] > level or
                             (stack[-1][0] == level and stack[-1][1:3] != (style, list_id))):
                parts.append(f"</li></{stack.pop()[3]}>")
            if stack and stack[-1][0] == level:
                parts.append("</li>")
            else:
                tag, css = _LIST_CSS.get(style, ("ul", "disc"))
                # A nested bullet list keeps Qt's own style for its depth.
                # The list adds no spacing of its own: each item carries its
                # Word paragraph's (AM-22).
                nested_bullets = style == QTextListFormat.ListDisc and stack
                list_type = "" if nested_bullets else f" list-style-type:{css};"
                parts.append(f'<{tag} style="margin-top:0px; margin-bottom:0px;{list_type}">')
                stack.append((level, style, list_id, tag))
            parts.append(f"<li{margins}>{inner}")
        while stack:
            parts.append(f"</li></{stack.pop()[3]}>")
        out.append("".join(parts))
        run.clear()

    for match in _PARAGRAPH.finditer(html):
        attrs, inner = match.group(1), match.group(2)
        list_match = _MSO_LIST.search(attrs)
        between = html[pos:match.start()]
        if list_match is None:
            continue
        markers = _DOWNLEVEL_LIST_MARKER.findall(inner)
        style = _marker_style(markers[0]) if markers else QTextListFormat.ListDisc
        inner = _DOWNLEVEL_LIST_MARKER.sub("", inner)
        if run and between.strip():
            flush()
        if not run:
            out.append(html[pos:match.start()])
        spacing = _PX_MARGINS.findall(attrs)
        margins = (f' style="margin-top:{spacing[-1][0]}px; margin-bottom:{spacing[-1][1]}px;"'
                   if spacing else "")
        run.append((int(list_match.group(2)), style, list_match.group(1), inner, margins))
        pos = match.end()
    flush()
    out.append(html[pos:])
    return "".join(out)


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.S | re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/|<!--|-->", re.S)
_CSS_RULE = re.compile(r"([^{}]+)\{([^}]*)\}")
_BLOCK_OPEN = re.compile(r"<(p|h[1-6])\b([^>]*)>", re.I)
_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_PX_MARGINS = re.compile(r"margin-top:(\d+)px; margin-bottom:(\d+)px")
_LENGTH = re.compile(r"^(-?\d*\.?\d+)(pt|in|cm|mm|px|pc)?$", re.I)
_PX_PER_UNIT = {"pt": 96 / 72, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "px": 1, "pc": 16}


def css_length_px(value: str) -> Optional[int]:
    """A CSS length in whole px (96 px to the inch), or None if unreadable."""
    match = _LENGTH.match(value.strip())
    if match is None:
        return None
    number, unit = float(match.group(1)), (match.group(2) or "").lower()
    if not unit:
        return 0 if number == 0 else None
    return max(0, round(number * _PX_PER_UNIT[unit]))


def _margins(declarations: str) -> dict:
    """{"top": px, "bottom": px} from CSS declarations (shorthand too)."""
    found = {}
    for declaration in declarations.split(";"):
        name, _, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "margin":
            parts = value.split()
            if parts:
                top = parts[0]
                bottom = parts[2] if len(parts) > 2 else parts[0]
                for key, raw in (("top", top), ("bottom", bottom)):
                    px = css_length_px(raw)
                    if px is not None:
                        found[key] = px
        elif name in ("margin-top", "margin-bottom"):
            px = css_length_px(value)
            if px is not None:
                found[name[len("margin-"):]] = px
    return found


def word_spacing_to_px(html: str) -> str:
    """Word's paragraph spacing, which Qt does not read (it is in pt and
    inches, in Word's style block and inline), written onto each paragraph
    and heading as whole px (4C2/AM-22). The spacing comes from, in order:
    Word's document default (.MsoPapDefault), the tag (h1…), the class
    (p.MsoNormal), the paragraph's own style; anything not given is 0."""
    rules = {}
    for block in _STYLE_BLOCK.findall(html):
        for selectors, declarations in _CSS_RULE.findall(_CSS_COMMENT.sub("", block)):
            margins = _margins(declarations)
            if margins:
                for selector in selectors.split(","):
                    rules.setdefault(selector.strip().lower(), {}).update(margins)

    def fix(match):
        tag, attrs = match.group(1).lower(), match.group(2)
        class_match = _CLASS_ATTR.search(attrs)
        css_class = next((g for g in class_match.groups() if g), "").lower() if class_match else ""
        spacing = {"top": 0, "bottom": 0}
        for selector in (".msopapdefault", tag, f".{css_class}", f"{tag}.{css_class}"):
            spacing.update(rules.get(selector, {}))
        style_match = _STYLE_ATTR.search(attrs)
        own = next((g for g in style_match.groups() if g is not None), "") if style_match else ""
        spacing.update(_margins(own))
        px = f"margin-top:{spacing['top']}px; margin-bottom:{spacing['bottom']}px"
        if style_match:
            quote_char = attrs[style_match.end() - 1]
            new_style = f"style={quote_char}{own.rstrip('; ')}; {px}{quote_char}"
            attrs = attrs[:style_match.start()] + new_style + attrs[style_match.end():]
        else:
            attrs = f'{attrs} style="{px}"'
        return f"<{match.group(1)}{attrs}>"

    return _BLOCK_OPEN.sub(fix, html)


def is_word_html(html: str) -> bool:
    return "urn:schemas-microsoft-com:office" in html or re.search(r"class=[\"']?Mso", html) is not None


def clean_office_html(html: str) -> str:
    """Word/Office markup Qt would otherwise show as text or misread:
    paragraph spacing in pt and inches, pseudo-lists, the down-level
    `<![if …]>` markers (their content is kept: `<![if !vml]><img …><![endif]>`
    is the image), and `<o:p>` tags."""
    if is_word_html(html):
        html = word_spacing_to_px(html)
    if "mso-list" in html:
        html = word_lists_to_html_lists(html)
    html = _DOWNLEVEL_LIST_MARKER.sub("", html)
    html = _DOWNLEVEL_TAGS.sub("", html)
    return _OFFICE_P_TAGS.sub("", html)


# ---------------------------------------------------------------- images
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""\b(src|alt)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


def image_alt_texts(html: str) -> dict:
    """{src: alt} for every <img> in the HTML (Qt keeps no alt text)."""
    alts = {}
    for tag in _IMG_TAG.findall(html):
        attrs = {}
        for name, a, b, c in _ATTR.findall(tag):
            attrs[name.lower()] = html_lib.unescape(a or b or c)
        if "src" in attrs and attrs["src"] not in alts:
            alts[attrs["src"]] = attrs.get("alt", "")
    return alts


class ImageStore:
    """Files pasted images into the editor's photo folder, as Insert Photo
    does (a new unique name under attachments/<subdir>/), and returns the
    relative name the document stores. Created per paste."""

    def __init__(self, folder: Callable[[], tuple]):
        self._folder = folder           # () -> (relative subdir, absolute folder)

    def _destination(self, suffix: str) -> tuple:
        subdir, folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        return f"{subdir}/{name}", folder / name

    def copy_file(self, path: Path) -> Optional[str]:
        relative, destination = self._destination(path.suffix.lower())
        try:
            shutil.copy2(path, destination)
        except OSError:
            return None
        return relative

    def save_bytes(self, data: bytes, suffix: str) -> Optional[str]:
        relative, destination = self._destination(suffix)
        try:
            destination.write_bytes(data)
        except OSError:
            return None
        return relative

    def save_image(self, image: QImage) -> Optional[str]:
        relative, destination = self._destination(".png")
        return relative if image.save(str(destination), "PNG") else None


def local_image_path(src: str) -> Optional[Path]:
    """The local file an image source names (a file: URL or an absolute
    path), or None for anything else."""
    if src.lower().startswith("file:"):
        return Path(QUrl(src).toLocalFile())
    if re.match(r"^[A-Za-z]:[\\/]", src) or src.startswith("\\\\") or src.startswith("/"):
        return Path(src)
    return None


def decode_data_image(src: str) -> Optional[tuple]:
    """(bytes, suffix) of a base64 `data:` image Qt can read, or None."""
    match = re.match(r"^data:([\w/+.-]+)(?:;[^,]*)?;base64,(.*)$", src, re.S | re.I)
    if not match or match.group(1).lower() not in DATA_IMAGE_TYPES:
        return None
    try:
        data = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        return None
    if QImage.fromData(QByteArray(data)).isNull():
        return None
    return data, DATA_IMAGE_TYPES[match.group(1).lower()]


# --------------------------------------------------------------- mapping
def _external_char_format(source: QTextCharFormat, heading: int) -> QTextCharFormat:
    """The formats the editor keeps from text pasted from another application."""
    fmt = QTextCharFormat()
    if source.hasProperty(QTextFormat.FontWeight) and source.fontWeight() >= QFont.DemiBold:
        fmt.setFontWeight(QFont.Bold)
    if source.fontItalic():
        fmt.setFontItalic(True)
    if source.fontUnderline():
        fmt.setFontUnderline(True)
    if source.fontStrikeOut():
        fmt.setFontStrikeOut(True)
    if source.verticalAlignment() in (QTextCharFormat.AlignSuperScript,
                                      QTextCharFormat.AlignSubScript):
        fmt.setVerticalAlignment(source.verticalAlignment())
    if source.hasProperty(QTextFormat.ForegroundBrush) and is_chromatic(source.foreground().color()):
        fmt.setForeground(source.foreground().color())
    if source.hasProperty(QTextFormat.BackgroundBrush) and is_chromatic(source.background().color()):
        fmt.setBackground(source.background().color())
    href = source.anchorHref()
    if allowed_link(href):
        fmt.setAnchor(True)
        fmt.setAnchorHref(href)
        fmt.setForeground(QColor(LINK_COLOR))
        fmt.setFontUnderline(True)
    if heading:
        fmt.setFontWeight(QFont.Bold)
        fmt.setFontPointSize(HEADING_SIZES[heading])
    return fmt


def _external_block_format(source: QTextBlockFormat, heading: int) -> QTextBlockFormat:
    fmt = QTextBlockFormat()
    if source.hasProperty(QTextFormat.BlockAlignment):
        fmt.setAlignment(source.alignment())
    if heading:
        fmt.setHeadingLevel(heading)
    if source.hasProperty(QTextFormat.BlockTopMargin):
        fmt.setTopMargin(round(source.topMargin()))
    if source.hasProperty(QTextFormat.BlockBottomMargin):
        fmt.setBottomMargin(round(source.bottomMargin()))
    if source.hasProperty(QTextFormat.LineHeight):
        fmt.setLineHeight(source.lineHeight(), source.lineHeightType())
    return fmt


def _outermost_table(block) -> Optional[QTextTable]:
    table = QTextCursor(block).currentTable()
    while table is not None:
        parent = table.parentFrame()
        if isinstance(parent, QTextTable):
            table = parent
        else:
            break
    return table


class _Writer:
    """Writes the normalized copy of an external source, paragraph by
    paragraph (4C2b-D4)."""

    def __init__(self, font: QFont, images: ImageStore, alts: dict):
        self.document = QTextDocument()
        self.document.setDefaultFont(font)
        self.cursor = QTextCursor(self.document)
        self.images = images
        self.alts = alts
        self.lists = {}                 # source list's object index -> new list
        self.started = False

    def new_paragraph(self, block_format: QTextBlockFormat):
        if self.started:
            self.cursor.insertBlock(block_format, QTextCharFormat())
        else:
            self.cursor.setBlockFormat(block_format)
            self.started = True

    def add_to_list(self, source_list):
        key = source_list.formatIndex()
        if key in self.lists:
            self.lists[key].add(self.cursor.block())
            return
        source_format = source_list.format()
        list_format = QTextListFormat()
        list_format.setStyle(source_format.style())
        list_format.setIndent(max(1, source_format.indent()))
        self.lists[key] = self.cursor.createList(list_format)

    def write_fragments(self, block, heading: int, cell_background: Optional[QColor] = None):
        """The text of one source paragraph. Spaces at its end are dropped
        (a browser does not show them; they come from the source's line
        wrapping), and a paragraph of nothing but spaces and no-break spaces
        (how HTML writes an empty paragraph, as Word's `<o:p>&nbsp;</o:p>`)
        stays empty. Neither applies to the source's LAST paragraph, which
        merges with the text after the caret, so "hello " pasted into a
        sentence keeps its space (4C2/AM-21). In a table cell, Qt gives the
        text the cell's own background, which is a cell background, not a
        highlight: removed."""
        text = block.text()
        last = not block.next().isValid()
        if not last and not text.strip(" \t\xa0"):
            return
        keep = len(text) if last else len(text.rstrip(" "))
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                start = fragment.position() - block.position()
                piece = fragment.text()[:max(0, keep - start)]
                source = fragment.charFormat()
                fmt = _external_char_format(source, heading)
                if cell_background is not None and fmt.background().color() == cell_background:
                    fmt.clearBackground()
                if source.isImageFormat():
                    for _ in piece:
                        self.write_image(source.toImageFormat(), fmt)
                elif piece:
                    self.cursor.insertText(piece.replace(OBJECT_REPLACEMENT, ""), fmt)
            it += 1

    def write_image(self, source: QTextImageFormat, fmt: QTextCharFormat):
        """An image: a local file or a data: image is copied into the photo
        folder; a remote image is never fetched and becomes a link to it; a
        missing file becomes "[image]" (4C2b-D4)."""
        src = source.name()
        relative = None
        decoded = decode_data_image(src) if src.lower().startswith("data:") else None
        if decoded is not None:
            relative = self.images.save_bytes(*decoded)
        else:
            path = local_image_path(src)
            if (path is not None and path.suffix.lower() in IMAGE_SUFFIXES and path.is_file()
                    and not QImage(str(path)).isNull()):
                relative = self.images.copy_file(path)
        if relative is not None:
            image = QTextImageFormat()
            image.merge(fmt)
            image.setName(relative)
            if source.hasProperty(QTextFormat.ImageWidth):
                image.setWidth(source.width())
            if source.hasProperty(QTextFormat.ImageHeight):
                image.setHeight(source.height())
            self.cursor.insertImage(image)
            return
        scheme = QUrl(src).scheme().lower()
        if scheme in ("http", "https"):
            alt = self.alts.get(src, "")
            link = QTextCharFormat(fmt)
            link.setAnchor(True)
            link.setAnchorHref(src)
            link.setForeground(QColor(LINK_COLOR))
            link.setFontUnderline(True)
            self.cursor.insertText(f"[image: {alt}]" if alt else "[image]", link)
            return
        plain = QTextCharFormat(fmt)
        if not allowed_link(plain.anchorHref()):
            plain.clearProperty(QTextFormat.AnchorHref)
            plain.setAnchor(False)
        self.cursor.insertText("[image]", plain)

    def write(self, scratch: QTextDocument):
        block = scratch.begin()
        while block.isValid():
            table = _outermost_table(block)
            if table is not None:
                block = self.write_table_row(block, table)
                continue
            source_format = block.blockFormat()
            heading = min(source_format.headingLevel(), 3)
            self.new_paragraph(_external_block_format(source_format, heading))
            if block.textList() is not None:
                self.add_to_list(block.textList())
            self.write_fragments(block, heading)
            block = block.next()

    def write_table_row(self, block, table: QTextTable):
        """One row of a table (nested tables included) as one paragraph:
        cells separated by tabs, the paragraphs of one cell by a space.
        The empty paragraphs Qt puts around a nested table inside a cell are
        skipped; an empty cell still counts, so the columns stay in place.
        Returns the first block after the row."""
        row = table.cellAt(block.position()).row()
        self.new_paragraph(QTextBlockFormat())
        previous_cell = None
        while block.isValid() and _outermost_table(block) is table \
                and table.cellAt(block.position()).row() == row:
            inner = QTextCursor(block).currentTable()
            cell = inner.cellAt(block.position())
            key = (id(inner), cell.row(), cell.column())
            cell_is_empty = cell.firstPosition() == cell.lastPosition()
            if block.length() <= 1 and not cell_is_empty:
                block = block.next()
                continue
            if previous_cell is not None:
                self.cursor.insertText("\t" if key != previous_cell else " ", QTextCharFormat())
            previous_cell = key
            cell_format = cell.format()
            background = (cell_format.background().color()
                          if cell_format.hasProperty(QTextFormat.BackgroundBrush) else None)
            self.write_fragments(block, 0, background)
            block = block.next()
        return block


def settle_heading_bump(document: QTextDocument):
    """Removes the size bump Qt adds to heading text it reads (bug 37)."""
    from .rich_editor import _settle_heading_sizes
    _settle_heading_sizes(document, keep_size=False)


_BODY_TAG = re.compile(r"<body\b([^>]*)>", re.I)


def with_document_font(html: str, font: QFont) -> str:
    """Copied HTML that says which font its text was shown in: Qt writes a
    bare <body> for a selection, so the copy's document font is added there
    (the text without a font of its own is in that font)."""
    match = _BODY_TAG.search(html)
    if match is None or "style=" in match.group(1):
        return html
    size = f"{font.pointSizeF():g}"
    style = f" style=\" font-family:'{font.family()}'; font-size:{size}pt;\""
    return html[:match.start()] + f"<body{match.group(1)}{style}>" + html[match.end():]


def bake_source_font(document: QTextDocument, family: str, size: float):
    """Writes the source's font onto every run that has none of its own, so
    the pasted text keeps the font it was shown in (4C2b-D3)."""
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                font = QTextCharFormat()
                if not fmt.hasProperty(QTextFormat.FontFamilies) and not fmt.hasProperty(QTextFormat.FontFamily):
                    font.setFontFamilies([family])
                if not fmt.hasProperty(QTextFormat.FontPointSize):
                    font.setFontPointSize(size)
                if font.properties():
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                    cursor.mergeCharFormat(font)
            it += 1
        block = block.next()


def document_from_html(html: str, font: QFont, images: ImageStore) -> QTextDocument:
    """The scratch document to insert for pasted HTML (4C2b-D1…D5)."""
    scratch = QTextDocument()
    scratch.setDefaultFont(font)
    if is_jortle_html(html):
        from .rich_editor import stored_document_font
        scratch.setHtml(html)
        settle_heading_bump(scratch)
        source_font = stored_document_font(html)
        if source_font is not None and source_font != (font.family(), font.pointSizeF()):
            bake_source_font(scratch, *source_font)
        return scratch
    html = clean_office_html(html)
    scratch.setHtml(html)
    writer = _Writer(font, images, image_alt_texts(html))
    writer.write(scratch)
    return writer.document
