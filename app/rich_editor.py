"""A Word-style WYSIWYG editor: what you see while typing is what you get,
no separate preview pane. Bold/italic/headings/lists/links/images/alignment/
line-and-paragraph-spacing/superscript/subscript/strikethrough are all
applied live to the text, exactly like a normal word processor.

Persistence format (round 21 rewrite)
--------------------------------------
Through round 20, content was stored as plain CommonMark Markdown (via Qt's
built-in QTextEdit.toMarkdown() / setMarkdown()). That turned out to be the
root cause of a whole family of reported formatting-loss bugs: CommonMark
simply has no syntax for paragraph alignment, line height, paragraph
spacing-before/after, superscript, subscript, or text color, and Qt's own
Markdown writer collapses some blank-paragraph and margin structure that
has no Markdown equivalent either — none of that is a bug in any one
feature, it's a structural ceiling on the format itself. Every symptom
reported (spacing not surviving reload, blank lines disappearing,
no way to keep alignment/line-spacing after reopening an entry) traces back
to this one thing.

As of round 21, the canonical representation is Qt's own rich-text HTML
(QTextEdit.toHtml() / setHtml()) instead. This is Qt's native serialization
of its QTextDocument model — the same engine writes it and reads it back —
so it round-trips every block/character format this editor's toolbar can
apply, including blank paragraphs (verified directly: Qt's HTML writer
marks a truly empty paragraph with its own `-qt-paragraph-type:empty`
style hint specifically so its reader can tell "empty paragraph" apart from
"no paragraph here at all", which is exactly the distinction CommonMark
couldn't make). See RichEditor.load()/save() below for how this coexists
with rows saved before this change, and database.py's module docstring for
the storage side of this.

Photos are inserted inline, at the cursor, the same way Word or Google Docs
does it: pick a file, it drops into the text at that point, and you keep
typing around it. The actual image files live on disk under the app's
attachments folder; only a relative path is stored in the saved content
(as an image resource reference — Markdown `![](path)` syntax in a
pre-round-21 "markdown"-format row, an `<img src="path">` tag in a
round-21+ "html"-format row), and this editor resolves that path back to a
real file whenever the document is displayed, regardless of which of the
two (see loadResource). Large photos are automatically downscaled for the
inline view — the original file on disk is never modified, so
double-clicking an image still shows it at full resolution.
"""
from __future__ import annotations

import re

import shutil
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QImage, QKeySequence, QPixmap,
    QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextFormat, QTextListFormat
)
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox,
    QTextEdit, QToolBar, QToolButton, QVBoxLayout, QWidget
)

from . import date_links
from .paths import get_attachments_dir
from .saving import document_has_content
from .ui_util import font_scaled, make_shrinkable_combo

# Spec Part 18. "free" means no repositioning code runs at all — the default,
# and the behaviour Part 15 asked for. The others place the active line at a
# fraction of the viewport height.
# Where a fragment's unzoomed point size is stashed while zoom is applied.
# A custom text-format property: Qt does not serialize these to HTML, so it
# cannot leak into stored documents.
ZOOM_BASE_SIZE_PROPERTY = QTextFormat.UserProperty + 17

WRITING_POSITION_FREE = "free"
WRITING_POSITIONS = {
    WRITING_POSITION_FREE: None,
    "top": 1 / 3,
    "center": 1 / 2,
    "bottom": 2 / 3,
}
WRITING_POSITION_LABELS = [
    ("Free / Manual (recommended)", WRITING_POSITION_FREE),
    ("Top third", "top"),
    ("Center", "center"),
    ("Bottom third", "bottom"),
]
# Keys that count as "editing" for the purposes of repositioning. Navigation
# keys are deliberately absent: scrolling because someone pressed an arrow
# would be fighting a deliberate movement.
_EDIT_KEYS = {Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab}

MAX_INLINE_IMAGE_WIDTH = 640
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"

# Zoom is a temporary, view-only magnification of the writing text — it
# never touches the writing font SIZE setting (that's a separate, saved
# preference) and never gets baked into the saved content; see
# RichTextEditor.zoom_in/out/reset_zoom and RichEditor.save() below.
# Each "step" is one Qt zoomIn()/zoomOut() unit (roughly +/-1pt); the label
# shown to the user is a percentage purely for readability.
ZOOM_STEP_MIN = -5
ZOOM_STEP_MAX = 15
ZOOM_PERCENT_PER_STEP = 10

# How far one Indent/Outdent toolbar click moves a paragraph's left+right
# margins, in points — the same amount append_note() uses for an inserted
# block, so clicking Outdent a couple of times on one removes exactly the
# indentation it arrived with.
INDENT_STEP = 24


class ImageViewerDialog(QDialog):
    """Full-resolution view of a photo, opened by double-clicking it inline."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(path.name)
        pixmap = QPixmap(str(path))
        screen_size = self.screen().availableSize() if self.screen() else QSize(1000, 800)
        max_w, max_h = int(screen_size.width() * 0.85), int(screen_size.height() * 0.85)
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label = QLabel()
        label.setPixmap(pixmap)
        layout = QVBoxLayout(self)
        layout.addWidget(label)


class RichTextEditor(QTextEdit):
    """The editing surface itself. Handles resource loading (inline images),
    link click-through, and image double-click-to-view. The toolbar lives in
    the wrapping RichEditor widget below, which is what other code should
    normally use.
    """

    dateLinkActivated = Signal(str)  # ISO date of a clicked journal://date/ link
    zoomChanged = Signal(int)        # new zoom percentage, whenever it changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)  # paste as plain text; formatting comes from our own toolbar
        self.setMouseTracking(True)
        self._current_relative_dir = ""  # "YYYY/MM" of the date/project currently being edited
        # Set by RichEditor(link_dates=True): written dates navigate on click
        # even before the next save/load has turned them into stored links.
        self.recognise_dates = False
        # The real, saved writing-font size (points) — deliberately tracked
        # separately from self.font(), which reflects whatever zoom is
        # currently applied on top of this. Anything that bakes an explicit
        # per-character font size into new text (below) must read THIS, not
        # self.font(), or a zoomed-in size would get permanently stuck into
        # that text even after zooming back out.
        self._base_point_size: float = 13.0
        self._zoom_steps: int = 0
        # Spec Part 18, optional: where the active writing line should sit.
        # "free" is the default and means exactly what round 21 established —
        # no repositioning code runs at all. The other values are a small
        # opt-in nudge, deliberately implemented as ~20 lines rather than a
        # subsystem ("do not create excessive infrastructure solely for this
        # setting").
        self._writing_position: str = WRITING_POSITION_FREE
        self._update_bottom_padding()

    def set_storage_subdir(self, relative_dir: str):
        """Where new photos inserted from now on should be filed, e.g. '2026/09'."""
        self._current_relative_dir = relative_dir

    # ----------------------------------------------------------------- font
    def set_base_font(self, family: str, size: int):
        """Sets the real, saved writing font — as opposed to zoom, which is
        a temporary on-screen magnification. Changing the real font resets
        any active zoom back to 0, so zoom always means 'temporarily larger
        or smaller than my current writing font', not a compounding offset."""
        self.reset_zoom()
        self._base_point_size = float(size)
        self.setFont(QFont(family, size))

    def adopt_document_font(self, family: str, size: float):
        """Makes `family`/`size` the writing font of the document about to be
        loaded — its own stored font (4C1a-D1), or the new-document font.

        Unlike set_base_font() this keeps the zoom level: it runs only just
        before setHtml()/setMarkdown() replace the document, and
        RichEditor.load() re-applies the active zoom afterwards
        (reapply_zoom_after_load), exactly as for any other load. The font
        must equal the stored <body> font before the HTML is read, or Qt
        writes the stored font onto every character as explicit formatting
        (bugs 29 and 35)."""
        self._base_point_size = float(size)
        font = QFont(family)
        font.setPointSizeF(float(size))
        self.setFont(font)
        # The document's own default font too, at the UNZOOMED size: setFont()
        # does not reach it when the widget's font is unchanged, and zoom sets
        # it to the zoomed size. Sizes Qt derives from it while loading (a
        # legacy Markdown heading's, 4C1b-D1) must not include the zoom, or
        # the zoom would be stored (4C1b-F1); the zoom is re-applied after.
        self.document().setDefaultFont(font)

    def zoom_default_font(self):
        """Sets the document's default font to the current zoom level, as
        _apply_zoom() does, without touching any character (no undo step)."""
        doc = self.document()
        default_font = QFont(doc.defaultFont())
        default_font.setPointSizeF(max(1.0, self._base_point_size * self.zoom_percent() / 100.0))
        doc.setDefaultFont(default_font)

    def base_font(self) -> QFont:
        """The real writing font at its SAVED size, with any active zoom
        factored out. Anything that bakes an explicit font into stored text
        (Clear Formatting, for one) must use this rather than self.font(),
        or a temporarily zoomed-in size would be written permanently into
        the document and survive zooming back out."""
        font = QFont(self.font())
        font.setPointSizeF(self._base_point_size)
        return font

    # ----------------------------------------------------------------- zoom
    # ---- view zoom ------------------------------------------------------
    #
    # Zoom is a VIEW property: it changes how big the text looks, never what
    # the document actually stores (see set_zoom_steps()'s guarantee below,
    # and save(), which exports at 100% regardless of what's on screen).
    #
    # It does NOT use QTextEdit.zoomIn()/zoomOut() any more, and that change
    # is the fix for a real regression rather than a preference. Qt's own
    # zoom only adjusts the document's DEFAULT font, which works for
    # characters that inherit it — but the moment a document is loaded with
    # setHtml(), Qt's HTML parser stamps the body's font-size onto every
    # character as an EXPLICIT point size, and explicit sizes ignore the
    # default entirely. Measured directly:
    #
    #     freshly typed document : per-char sizes [0.0]  -> zoomIn() works
    #     after a setHtml() load : per-char sizes [13.0] -> zoomIn() does nothing
    #
    # Since the round-21 switch to HTML persistence, every entry is loaded
    # through setHtml(), so Qt's zoom silently stopped having any visible
    # effect. Ctrl+wheel and the Zoom % control were both still firing
    # correctly into a mechanism that no longer did anything.
    #
    # So zoom now scales each character's own point size. To make that
    # exactly reversible — no drift after repeated zooming, and no chance of
    # a zoomed size being mistaken for the user's real typography — every
    # fragment's UNZOOMED size is stashed once in a custom text-format
    # property, and each zoom level is computed from that stored base rather
    # than from the currently displayed size. Custom properties are not
    # serialized by toHtml(), so they never reach storage.
    def zoom_in(self):
        self.set_zoom_steps(self._zoom_steps + 1)

    def zoom_out(self):
        self.set_zoom_steps(self._zoom_steps - 1)

    def reset_zoom(self):
        self.set_zoom_steps(0)

    def set_zoom_steps(self, steps: int):
        """The single authoritative zoom entry point. Ctrl+wheel and the
        Zoom % control both come through here, so the two can never disagree
        about the current level."""
        steps = max(ZOOM_STEP_MIN, min(ZOOM_STEP_MAX, int(steps)))
        if steps == self._zoom_steps:
            return
        self._zoom_steps = steps
        self._apply_zoom()
        self.zoomChanged.emit(self.zoom_percent())

    def _apply_zoom(self):
        """Rescales every character from its stored unzoomed size.

        Returning to 100% restores the document EXACTLY as it was, including
        for characters that carried no explicit size at all. That distinction
        matters: text the user has never individually resized inherits the
        "Default writing font" setting, and if zoom left a baked-in size
        behind, such text would silently stop following that setting forever
        after the first scroll — a regression caught by the round-7 suite,
        which checks precisely this. So an inherited size is recorded as the
        sentinel 0.0 and is genuinely CLEARED again at 100%, not rewritten
        as a number that merely looks the same today.

        Runs as one edit block with signals blocked: zoom is a view
        operation and must not look like the user typed (which would
        schedule an autosave and fill the undo stack with zoom steps).
        """
        factor = self.zoom_percent() / 100.0
        at_normal = abs(factor - 1.0) < 1e-9
        doc = self.document()

        default_font = QFont(doc.defaultFont())
        default_font.setPointSizeF(max(1.0, self._base_point_size * factor))
        doc.setDefaultFont(default_font)

        # Zoom is a VIEW operation. It has to touch stored character formats
        # (see the docstring), but it must not make the document look edited
        # — otherwise looking at an entry at 150% would leave it "unsaved"
        # and prompt on the way out.
        was_modified = doc.isModified()
        was_blocked = self.blockSignals(True)
        doc.blockSignals(True)
        anchor = QTextCursor(doc)
        anchor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    stored = fmt.property(ZOOM_BASE_SIZE_PROPERTY)
                    if stored is None:
                        # First zoom for this fragment: remember what it had
                        # at 100%. 0.0 means "inherits the document default"
                        # and must come back as inherited, not as a number.
                        stored = float(fmt.fontPointSize() or 0.0)
                        fmt.setProperty(ZOOM_BASE_SIZE_PROPERTY, stored)
                    stored = float(stored)

                    if at_normal:
                        fmt.clearProperty(ZOOM_BASE_SIZE_PROPERTY)
                        if stored <= 0:
                            fmt.clearProperty(QTextFormat.FontPointSize)
                        else:
                            fmt.setFontPointSize(stored)
                    else:
                        base = stored if stored > 0 else self._base_point_size
                        fmt.setFontPointSize(max(1.0, base * factor))

                    span = QTextCursor(doc)
                    span.setPosition(fragment.position())
                    span.setPosition(fragment.position() + fragment.length(),
                                     QTextCursor.KeepAnchor)
                    span.setCharFormat(fmt)
                it += 1
            block = block.next()
        anchor.endEditBlock()
        doc.blockSignals(False)
        self.blockSignals(was_blocked)
        doc.setModified(was_modified)
        self._update_bottom_padding()

    def reapply_zoom_after_load(self):
        """Re-applies the active zoom level to a freshly-loaded document.

        setHtml()/setMarkdown() rebuild the document from scratch, so the
        stashed unzoomed sizes vanish and every character comes back at its
        stored size. Without this, navigating to another day would look like
        zoom had reset itself."""
        if self._zoom_steps:
            self._apply_zoom()

    def base_point_size(self) -> float:
        """The unzoomed default font size — what a stored document should
        carry as its document default, whatever is on screen."""
        return self._base_point_size

    def strip_zoom_for_export(self) -> int:
        """Drops to 100% and returns the level that was active, so a caller
        can restore it after serializing. This is what keeps view zoom out
        of stored documents — see RichEditor.save()."""
        steps = self._zoom_steps
        if steps:
            self.set_zoom_steps(0)
        return steps

    def zoom_percent(self) -> int:
        return 100 + self._zoom_steps * ZOOM_PERCENT_PER_STEP

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------- bottom padding / typewriter scrolling
    #
    # By default, a QTextEdit can only ever scroll until the last line of
    # text is at the very bottom edge of the viewport — so the line you're
    # actively writing ends up pinned to the bottom edge of the screen the
    # moment the entry overflows one screenful, which is a cramped place to
    # stare at while writing. Two things fix that together: padding BELOW
    # the actual text (a blank margin added to the document itself, so
    # there's somewhere to scroll the last line UP to) and, while actively
    # typing, nudging the scroll position so the current line sits at the
    # vertical center of the viewport rather than wherever Qt's default
    # "just keep the cursor minimally visible" behavior would leave it.
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_bottom_padding()

    def set_writing_position(self, position: str):
        """One of WRITING_POSITIONS. Anything unrecognized falls back to
        "free", so a hand-edited or future setting value can never leave the
        editor in a state that fights the user."""
        self._writing_position = (
            position if position in WRITING_POSITIONS else WRITING_POSITION_FREE
        )
        # The amount of scroll-past-end space depends on this setting — see
        # _update_bottom_padding().
        self._update_bottom_padding()

    def _apply_writing_position(self, event):
        """Nudges the view so the caret sits near the configured fraction of
        the viewport while typing.

        Only runs for typing and editing keys, never for navigation
        (arrows, Home/End, PageUp/Down) — deliberately scrolling somewhere
        because someone pressed Down would be fighting them, and manual
        scrolling must take priority (Part 17). It also only ever scrolls
        when the caret is actually off the target by more than a line, so
        ordinary typing within the comfortable band doesn't twitch the view
        on every keystroke.
        """
        if not (event.text() or event.key() in _EDIT_KEYS):
            return
        fraction = WRITING_POSITIONS.get(self._writing_position)
        if fraction is None:
            return
        viewport_height = self.viewport().height()
        if viewport_height <= 0:
            return
        target_y = viewport_height * fraction
        cursor_y = self.cursorRect().center().y()
        delta = cursor_y - target_y
        # A dead band of roughly one line: without it, every keystroke would
        # re-scroll by a pixel or two and the page would feel unsteady.
        if abs(delta) <= max(12, self.cursorRect().height()):
            return
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + int(delta))

    def _update_bottom_padding(self):
        """Keeps a blank margin below the document roughly half the
        viewport's height, so the last line can always be scrolled up to
        the middle of the screen. Re-applied on every resize (the right
        amount of padding depends on the current viewport height) and
        after loading a different day/project's content (setHtml()/
        setMarkdown() both rebuild the document's root frame, which would
        otherwise silently drop this)."""
        # Half a viewport is enough for "free" and for keeping the last line
        # off the bottom edge. But a writing position that asks for the
        # active line HIGHER than the middle needs more space beneath it than
        # that: to sit the final line at one third of the viewport, two
        # thirds of a viewport has to exist below it. Without this, the
        # nudge in _apply_writing_position() simply runs out of scroll range
        # at the end of a document and the caret settles lower than asked —
        # a real interaction between scroll-past-end and writing position,
        # found by measuring where the caret actually landed.
        fraction = WRITING_POSITIONS.get(self._writing_position)
        needed = 0.5 if fraction is None else max(0.5, 1.0 - fraction)
        self._set_bottom_padding(max(0, int(self.viewport().height() * needed)))

    def _set_bottom_padding(self, pad: float):
        """The scroll-past-the-last-line space. A VIEW affordance, sized to
        the window — so like zoom and the font setting, it must not make the
        document look edited. Without this guard, merely resizing the window
        marked every open document unsaved (it is recomputed on every
        resizeEvent), and closing the app would ask to save writing nobody
        had touched."""
        root_frame = self.document().rootFrame()
        fmt = root_frame.frameFormat()
        if fmt.bottomMargin() != pad:
            was_modified = self.document().isModified()
            fmt.setBottomMargin(pad)
            root_frame.setFrameFormat(fmt)
            self.document().setModified(was_modified)

    def bottom_padding(self) -> float:
        return self.document().rootFrame().frameFormat().bottomMargin()

    # Round 21: this editor no longer forces the caret to any particular
    # vertical position while typing. Through round 20, _keep_cursor_centered()
    # (removed here) actively re-scrolled the view to pin the cursor's line to
    # the exact vertical middle of the viewport on every keystroke — a
    # "typewriter scrolling" effect that was explicitly asked for removal:
    # writing above or below the middle should stay exactly where you put it,
    # and a deliberate manual scroll should never get fought and snapped back.
    #
    # What replaces it is simply Qt's own default behavior for QTextEdit,
    # which was always running underneath the old forced-centering on top of
    # it: QTextCursor::ensureVisible()-style "soft following" — ordinary
    # typing/editing scrolls the view the MINIMUM amount needed to keep the
    # caret inside the visible viewport, and does nothing at all if the
    # caret is already visible. There is deliberately no custom code for
    # this anymore; removing the override is the fix. The blank space below
    # the last line to scroll into (so a long entry doesn't pin the current
    # line to the bottom edge) is a separate, independent mechanism —
    # _update_bottom_padding() below — and is unaffected by this change.

    # ---------------------------------------------------------------- find
    #
    # Ctrl+F opens a small find bar (built in RichEditor, not here) that
    # calls into this editor purely through QTextDocument.find() / setting
    # the text cursor + an ExtraSelection highlight — never anything that
    # inserts, removes, or reformats a single character of the document
    # itself, so searching can never modify the text (a hard requirement).
    def find_all_ranges(self, term: str, case_sensitive: bool = False) -> list[tuple[int, int]]:
        """Returns every non-overlapping (start, end) character position
        where `term` occurs, without moving the cursor or touching the
        document — used by RichEditor's find bar both to highlight every
        match and to report a match count."""
        if not term:
            return []
        doc = self.document()
        flags = QTextDocument.FindCaseSensitively if case_sensitive else QTextDocument.FindFlags()
        ranges = []
        cursor = QTextCursor(doc)
        while True:
            cursor = doc.find(term, cursor, flags)
            if cursor.isNull():
                break
            ranges.append((cursor.selectionStart(), cursor.selectionEnd()))
        return ranges

    # ------------------------------------------------------------ resources
    def loadResource(self, resource_type, name: QUrl):
        if resource_type == QTextDocument.ImageResource:
            relative = name.toString()
            file_path = get_attachments_dir() / relative
            image = QImage(str(file_path))
            if image.isNull():
                return image
            if image.width() > MAX_INLINE_IMAGE_WIDTH:
                image = image.scaledToWidth(MAX_INLINE_IMAGE_WIDTH, Qt.SmoothTransformation)
            return image
        return super().loadResource(resource_type, name)

    def _resolve_image_path(self, relative: str) -> Path:
        return get_attachments_dir() / relative

    @staticmethod
    def _normal_block_format() -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setHeadingLevel(0)
        return fmt

    def _reset_to_normal_style(self, cursor: QTextCursor):
        """Make the block the cursor is in plain body text again — used
        after Enter following a heading, and after programmatic inserts
        (photos), so headings don't 'leak' into whatever comes next, the
        way Word resets style after a heading paragraph."""
        cursor.setBlockFormat(self._normal_block_format())
        normal_char = QTextCharFormat()
        normal_char.setFontWeight(QFont.Normal)
        normal_char.setFontPointSize(self._base_point_size or 13)
        cursor.setCharFormat(normal_char)
        self.setCurrentCharFormat(normal_char)

    def keyPressEvent(self, event):
        cursor_before = self.textCursor()
        was_heading = cursor_before.blockFormat().headingLevel() > 0
        super().keyPressEvent(event)
        if was_heading and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._reset_to_normal_style(self.textCursor())
        # Default is "free": no forced repositioning at all, just Qt's own
        # keep-the-caret-visible behaviour (round 21, spec Part 15/17). The
        # other writing positions are strictly opt-in — see
        # _apply_writing_position().
        if self._writing_position != WRITING_POSITION_FREE:
            self._apply_writing_position(event)

    # ---------------------------------------------------------- image insert
    def insert_photo(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Insert Photo(s)", "", IMAGE_FILTER)
        if not paths:
            return
        subdir = self._current_relative_dir or "misc"
        dest_dir = get_attachments_dir() / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        cursor = self.textCursor()
        if not cursor.atBlockStart():
            cursor.insertBlock()
            self._reset_to_normal_style(cursor)
        for src in paths:
            src_path = Path(src)
            new_name = f"{uuid.uuid4().hex}{src_path.suffix.lower()}"
            dest_path = dest_dir / new_name
            try:
                shutil.copy2(src_path, dest_path)
            except OSError as e:
                QMessageBox.warning(self, "Couldn't add photo", f"{src_path.name}: {e}")
                continue
            relative_name = f"{subdir}/{new_name}"
            cursor.insertImage(relative_name)
            cursor.insertBlock()
            self._reset_to_normal_style(cursor)
        self.setTextCursor(cursor)
        self.setFocus()

    # ------------------------------------------------------------- linking
    def insert_link(self):
        cursor = self.textCursor()
        selected_text = cursor.selectedText()
        url, ok = QInputDialog.getText(self, "Insert Link", "URL:")
        if not ok or not url.strip():
            return
        url = url.strip()
        if not selected_text:
            text, ok2 = QInputDialog.getText(self, "Insert Link", "Link text:", text=url)
            if not ok2:
                return
            selected_text = text or url

        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(url)
        fmt.setForeground(QColor("#3f8ede"))
        fmt.setFontUnderline(True)
        cursor.insertText(selected_text, fmt)

    def _anchor_at(self, pos: QPoint) -> str:
        cursor = self.cursorForPosition(pos)
        return cursor.charFormat().anchorHref()

    def mouseMoveEvent(self, event):
        anchor = self._anchor_at(event.pos()) or self._unlinked_date_at(event.pos())
        self.viewport().setCursor(Qt.PointingHandCursor if anchor else Qt.IBeamCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        anchor = self._anchor_at(event.pos())
        if not anchor and not self.textCursor().hasSelection():
            # A date typed since this entry was loaded is not a link in the
            # live document (see "internal date links" below); it navigates
            # exactly as a stored link does.
            typed_date = self._unlinked_date_at(event.pos())
            if typed_date:
                anchor = date_links.link_href(typed_date)
        if anchor:
            internal_date = date_links.parse_link(anchor)
            if internal_date:
                # Internal date links navigate on a PLAIN click, per the
                # spec's "clicking one should…". External links keep
                # requiring Ctrl+click (below) because opening a browser is
                # a bigger interruption to land on by accident, and because
                # that's the convention this editor already had. The
                # trade-off is that clicking directly on a date link moves
                # the caret nowhere — to edit the date text itself, place
                # the caret just beside it and use the arrow keys.
                self.dateLinkActivated.emit(internal_date)
                return
            if event.modifiers() & Qt.ControlModifier:
                QDesktopServices.openUrl(QUrl(anchor))
                return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------- internal date links
    # Dates are linked only in the copy that is saved (link_dates_in, called
    # by RichEditor.save on export_document's clone), never in the live
    # document: linking there was an edit of its own, so after Ctrl+S one
    # Ctrl+Z undid the linking instead of the user's typing (bug 8, 4C1a-D3).
    # A date typed since the entry was loaded is therefore plain text on
    # screen until the next load; a plain click on it still navigates,
    # recognised here at click time.
    def _unlinked_date_at(self, pos: QPoint) -> str:
        """The ISO date of a written date under `pos` that is not (yet) a
        link in this document, or '' — only in editors that link dates."""
        if not self.recognise_dates:
            return ""
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        offset = cursor.position() - block.position()
        for start, end, iso in date_links.find_dates(block.text()):
            if start <= offset < end or (offset == end and offset > start):
                return iso
        return ""

    def mouseDoubleClickEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        char_format = cursor.charFormat()
        if char_format.isImageFormat():
            relative = char_format.toImageFormat().name()
            path = self._resolve_image_path(relative)
            if path.exists():
                dlg = ImageViewerDialog(path, self)
                dlg.exec()
                return
        super().mouseDoubleClickEvent(event)



# Qt's serialization of a non-default root frame: the whole body inside one
# table marked `-qt-table-type: root`. Every entry saved between round 22 and
# the fix in export_document() below was stored like this. Reading it back
# with setHtml() adds one empty paragraph whenever the first paragraph is
# empty, so load() unwraps it first. Only this exact Qt-generated shape —
# directly after <body>, closed directly before </body> — is touched; the
# paragraphs inside are passed to Qt unchanged.
_ROOT_FRAME_OPEN_RE = re.compile(
    r"(<body[^>]*>\s*)<table[^>]*-qt-table-type:\s*root[^>]*>\s*<tr>\s*<td[^>]*>", re.S)
_ROOT_FRAME_CLOSE_RE = re.compile(r"</td>\s*</tr>\s*</table>(\s*</body>)", re.S)


def unwrap_root_frame(html: str) -> str:
    """Stored HTML without Qt's root-frame table wrapper (see above).

    The wrapper only ever carries the root frame's margins — the document
    margin, plus, in rows saved between rounds 21 and 22, the leaked
    scroll-past-end padding — neither of which is the user's formatting.
    """
    if not html or "-qt-table-type" not in html:
        return html
    opening = _ROOT_FRAME_OPEN_RE.search(html)
    closings = list(_ROOT_FRAME_CLOSE_RE.finditer(html))
    if opening is None or not closings:
        return html
    closing = closings[-1]
    if closing.start() < opening.end():
        return html
    return (html[:opening.start()] + opening.group(1)
            + html[opening.end():closing.start()] + closing.group(1)
            + html[closing.end():])


def link_dates_in(doc: "QTextDocument") -> bool:
    """Applies journal://date/ links to every recognizable written date in
    `doc` that isn't already linked. Returns True if it changed anything.

    Called by RichEditor.save() on the copy that is stored (export_document's
    clone), never on the live document — see RichTextEditor's "internal date
    links" (bug 8). Idempotent: a range whose anchorHref already matches is
    skipped, so a document whose dates are all linked is left untouched.
    """
    pending: list[tuple[int, int, str]] = []
    block = doc.begin()
    while block.isValid():
        text = block.text()
        if text.strip():
            block_start = block.position()
            for start, end, iso in date_links.find_dates(text):
                probe = QTextCursor(doc)
                probe.setPosition(block_start + start)
                probe.setPosition(block_start + end, QTextCursor.KeepAnchor)
                if probe.charFormat().anchorHref() == date_links.link_href(iso):
                    continue
                pending.append((block_start + start, block_start + end, iso))
        block = block.next()
    if not pending:
        return False

    fmt = QTextCharFormat()
    fmt.setAnchor(True)
    fmt.setForeground(QColor("#3f8ede"))
    fmt.setFontUnderline(True)
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    for start, end, iso in pending:
        fmt.setAnchorHref(date_links.link_href(iso))
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(fmt)
    cursor.endEditBlock()
    return True


def _settle_heading_sizes(doc: "QTextDocument", keep_size: bool):
    """Removes the size bump (QTextFormat.FontSizeAdjustment) Qt puts on the
    text of headings it reads back. With keep_size the bumped size is first
    written onto the text as an explicit point size, so it looks the same and
    survives the upgrade to HTML (legacy Markdown rows); without it the text
    keeps the size stored with it (HTML rows, bug 37). Headings only."""
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().headingLevel() > 0:
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                fmt = fragment.charFormat()
                if fragment.isValid() and fmt.hasProperty(QTextFormat.FontSizeAdjustment):
                    if keep_size:
                        fmt.setFontPointSize(fmt.font().pointSizeF())
                    fmt.clearProperty(QTextFormat.FontSizeAdjustment)
                    span = QTextCursor(doc)
                    span.setPosition(fragment.position())
                    span.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                    span.setCharFormat(fmt)
                it += 1
        block = block.next()


_FONT_PROPERTIES = (QTextFormat.FontFamilies, QTextFormat.FontFamily, QTextFormat.FontPointSize)


def _clear_font_formats(doc: "QTextDocument"):
    """Removes font family and size from every paragraph and character
    format of a BLANK document (4C1a-F1/AM-1), so what is typed into it
    inherits the document's default font. Never called on written text."""
    cursor = QTextCursor(doc)
    block = doc.begin()
    while block.isValid():
        block_format = block.charFormat()
        for prop in _FONT_PROPERTIES:
            block_format.clearProperty(prop)
        cursor.setPosition(block.position())
        cursor.setBlockCharFormat(block_format)
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                for prop in _FONT_PROPERTIES:
                    fmt.clearProperty(prop)
                span = QTextCursor(doc)
                span.setPosition(fragment.position())
                span.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                span.setCharFormat(fmt)
            it += 1
        block = block.next()


# The document's own writing font, as Qt stores it in <body style="…">:
# font-family:'Georgia'; font-size:13pt (4C1a-D1).
_BODY_FONT_RE = re.compile(
    r"<body\b[^>]*\bstyle=\"[^\"]*font-family:'([^']*)';\s*font-size:(\d+(?:\.\d+)?)pt")


def stored_document_font(content: str):
    """(family, point size) of the writing font stored with a document, or
    None when it has none (legacy Markdown rows, empty documents)."""
    match = _BODY_FONT_RE.search(content or "")
    if not match:
        return None
    return match.group(1), float(match.group(2))


def export_document(text_edit) -> "QTextDocument":
    """A copy of the editor's document, normalized for storage.

    Saving used to do this in place: drop the view zoom, zero the
    scroll-past-end padding, serialize, then put both back. It produced the
    right bytes, but every save mutated the live document — which pushed
    entries onto the UNDO STACK and set the document's modified flag. The
    visible consequences were bad and were the same single cause:

      * Ctrl+S then Ctrl+Z undid the save's own invisible formatting churn
        instead of the user's last edit — with zoom active, six presses
        didn't get back to the text (measured, not assumed);
      * the document reported itself modified immediately after being
        saved, so "are there unsaved changes?" could never be answered.

    Cloning makes save a genuinely read-only operation: the normalization
    happens on a throwaway copy, and the live document — the user's undo
    history, their caret, their modified flag — is never touched.

    The two normalizations are unchanged, just applied to the copy:
      * zoom is a VIEW setting, so what gets stored is always the 100%
        document (each fragment restored from the base size zoom stashed on
        it, and genuinely cleared where the size was inherited);
      * the blank space below the last line is a view affordance sized to
        the window, so it must not end up in the stored bytes.
    """
    document = text_edit.document().clone(text_edit)

    # The scroll-past-end padding lives on the root frame's bottom margin.
    # Put it back to the document's own default margin — NOT to 0. Any root
    # frame format that differs from the default makes Qt wrap the whole
    # body in a `-qt-table-type: root` table when serializing, and Qt's own
    # HTML reader turns that wrapper back into one extra empty paragraph
    # whenever the document's first paragraph is empty. Setting 0 here was
    # therefore the source of the "ghost lines": an empty entry gained a
    # blank line on every save/reload, and so did any entry that began with
    # a blank line (measured 2026-09-23). With the default restored, Qt
    # writes a plain body and the round trip is exact.
    root_frame = document.rootFrame()
    frame_format = root_frame.frameFormat()
    default_margin = document.documentMargin()
    if frame_format.bottomMargin() != default_margin:
        frame_format.setBottomMargin(default_margin)
        root_frame.setFrameFormat(frame_format)

    default_font = QFont(document.defaultFont())
    default_font.setPointSizeF(max(1.0, text_edit.base_point_size()))
    document.setDefaultFont(default_font)

    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                stored = fmt.property(ZOOM_BASE_SIZE_PROPERTY)
                if stored is not None:
                    stored = float(stored)
                    fmt.clearProperty(ZOOM_BASE_SIZE_PROPERTY)
                    if stored <= 0:
                        fmt.clearProperty(QTextFormat.FontPointSize)
                    else:
                        fmt.setFontPointSize(stored)
                    span = QTextCursor(document)
                    span.setPosition(fragment.position())
                    span.setPosition(fragment.position() + fragment.length(),
                                     QTextCursor.KeepAnchor)
                    span.setCharFormat(fmt)
            it += 1
        block = block.next()

    return document


class RichEditor(QWidget):
    """RichTextEditor plus its formatting toolbar. This is the widget the
    rest of the app should embed — it exposes the same small API the old
    Markdown editor did (set_markdown / markdown / set_font / textChanged)
    so it drops in without ceremony.
    """
    textChanged = Signal()
    # Emitted when the user activates a journal://date/YYYY-MM-DD link. The
    # editor deliberately does NOT navigate itself — it has no idea what the
    # selected date is or who owns it. It reports the click and lets the
    # window that embeds it move the canonical SelectedDate.
    dateLinkActivated = Signal(str)

    def __init__(self, parent=None, read_only: bool = False, compact: bool = False,
                 link_dates: bool = False):
        """`compact` builds a deliberately reduced toolbar: the character
        basics plus lists, and nothing else. It exists for Reader's Notes,
        which is meant to be a lightweight companion area — duplicating the
        journal's full word-processor toolbar there would both crowd a narrow
        panel and imply the notes are a second journal, which they aren't.
        The underlying document, persistence and Ctrl+F are identical either
        way, so notes written in compact mode are still ordinary rich text."""
        super().__init__(parent)

        self.compact = compact
        self.link_dates = link_dates
        self.text_edit = RichTextEditor()
        self.text_edit.setReadOnly(read_only)
        self.text_edit.recognise_dates = link_dates and not read_only
        self.text_edit.dateLinkActivated.connect(self.dateLinkActivated.emit)
        # The font a new, empty document starts in (4C1a-D1): the writing-font
        # setting once set_font() has been called, otherwise the font this
        # editor was built with (the Reader's Notes editors never receive the
        # setting and keep the application font, 4C1a-D2).
        self._new_document_font = (self.text_edit.font().family(),
                                   self.text_edit.base_point_size())
        # The font of the document now in the editor (its stored font, or the
        # new-document font it started in) — what set_font() keeps it in.
        self._document_font = self._new_document_font

        self._find_ranges: list[tuple[int, int]] = []
        self._find_current_index = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toolbar: Optional[QToolBar] = None          # row 1 (character formatting)
        self.paragraph_toolbar: Optional[QToolBar] = None  # row 2 (paragraph formatting)
        if not read_only:
            if compact:
                # Reader's Notes keeps a single short row — a second row of
                # paragraph controls would be exactly the duplication of the
                # journal's full toolbar the spec says to avoid there.
                self.toolbar = self._build_font_toolbar()
                layout.addWidget(self.toolbar)
            else:
                self.toolbar, self.paragraph_toolbar = self._build_toolbars()
                layout.addWidget(self.toolbar)
                layout.addWidget(self.paragraph_toolbar)

        # Find works even in a read-only preview (it never edits text), so
        # it's built unconditionally — only the toolbar's own 🔍 button is
        # gated on read_only above; the Ctrl+F shortcut always works.
        self.find_bar = self._build_find_bar()
        layout.addWidget(self.find_bar)
        layout.addWidget(self.text_edit)

        find_shortcut = QShortcut(QKeySequence.Find, self.text_edit, activated=self.show_find_bar)
        find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)

        self.text_edit.textChanged.connect(self.textChanged.emit)
        self.text_edit.cursorPositionChanged.connect(self._sync_toolbar_state)
        self.text_edit.zoomChanged.connect(self._on_editor_zoom_changed)

    # ------------------------------------------------------------- toolbar
    #
    # A real QToolBar rather than a plain row of buttons — on purpose. This
    # editor's toolbar has grown over several rounds (font controls,
    # headings, bold/italic/underline, lists, quote, link, photo, and now
    # text color + indent/outdent below), and a QHBoxLayout of fixed-size
    # widgets can only ever get as narrow as the sum of all of their
    # minimum widths — which, in the three-pane Daily Journal layout,
    # silently became a floor on how far the splitter could shrink the
    # writing pane, well before anything actually looked cramped. A
    # QToolBar's minimum width is just enough for its "»" overflow button —
    # everything that doesn't fit collapses into that overflow menu instead
    # of forcing the whole bar (and the pane around it) to stay wide, so
    # the pane can now be resized as narrow as any other pane.
    def _build_toolbars(self) -> tuple:
        """Two logical rows instead of one long one (spec Part 7).

        Row 1 is character formatting, row 2 is paragraph/document
        formatting — the split follows what the controls actually do, which
        is also the split the user thinks in. Two rows is what makes the
        numeric spacing fields fit at their true size: cramming them into a
        single row was why they were being clipped, and widening the whole
        pane to compensate was explicitly ruled out.

        Both rows are still real QToolBars, so each keeps the overflow ("»")
        behaviour that lets the writing pane be dragged narrow — the reason
        this became a QToolBar in the first place.
        """
        row1 = self._build_font_toolbar()
        row2 = self._build_paragraph_toolbar()
        return row1, row2

    def _build_font_toolbar(self) -> QToolBar:
        bar = QToolBar()
        bar.setFloatable(False)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextOnly)  # every action here is a text/emoji
        # label, not an icon — keeps buttons compact instead of reserving icon space

        # ---- Font group: everything that formats CHARACTERS (round 21
        # groups the toolbar conceptually into Font vs. Paragraph, without
        # adding visible section chrome that would make one already-long
        # toolbar row taller/busier — the grouping is in the ordering and
        # in this comment, not in extra widgets).
        #
        # Writing font (family + size) lives right here, next to Bold/
        # Italic/etc. — and works exactly like Bold/Italic too: select some
        # text and change either one to restyle just that selection (one
        # word at size 20, the rest at whatever they already were); with
        # nothing selected, it sets what you're about to type next. There's
        # also a separate "Default writing font" in Settings — that's the
        # starting size/family for brand-new entries and for any text you
        # haven't customized individually; it never overrides a selection
        # you've already resized here.
        # Elides instead of clipping, and its cap follows the application
        # font: a flat 150px cap fit "DejaVu Serif" at 10pt and showed
        # "ejaVu Serif" at 18pt, with the text scrolled sideways inside the
        # box rather than truncated — which reads as a broken control, not
        # as a narrow one. refresh_control_sizes() re-applies the cap when
        # the application font changes.
        self.family_combo = make_shrinkable_combo(QFontComboBox(), visible_chars=8)
        self.family_combo.setMaximumWidth(font_scaled(150))
        self.family_combo.setToolTip("Font (select text to change just that text)")
        self.family_combo.currentFontChanged.connect(self._on_family_control_changed)
        bar.addWidget(self.family_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 72)
        self.size_spin.setToolTip("Font size (select text to change just that text)")
        self.size_spin.valueChanged.connect(self._on_size_control_changed)
        bar.addWidget(self.size_spin)

        self.heading_combo = QComboBox()
        self.heading_combo.addItems(["Normal", "Heading 1", "Heading 2", "Heading 3"])
        self.heading_combo.currentIndexChanged.connect(self._apply_heading)
        bar.addWidget(self.heading_combo)

        bar.addSeparator()

        self.bold_btn = self._tool_action(bar, "B", "Bold (Ctrl+B)", self._toggle_bold, checkable=True)
        self.italic_btn = self._tool_action(bar, "I", "Italic (Ctrl+I)", self._toggle_italic, checkable=True)
        self.underline_btn = self._tool_action(bar, "U", "Underline (Ctrl+U)", self._toggle_underline, checkable=True)
        self.strike_btn = self._tool_action(bar, "S̶", "Strikethrough", self._toggle_strikethrough, checkable=True)
        self.super_btn = self._tool_action(bar, "x²", "Superscript", self._toggle_superscript, checkable=True)
        self.sub_btn = self._tool_action(bar, "x₂", "Subscript", self._toggle_subscript, checkable=True)

        if self.compact:
            # Reader's Notes stops here: bold/italic/underline/strike/super/
            # sub, lists, and find — enough to write a readable definition
            # list, without a second copy of the journal's paragraph
            # machinery. Everything omitted still ROUND-TRIPS correctly if it
            # arrives by paste or from another editor; it just isn't offered
            # as a control here.
            bar.addSeparator()
            self._tool_action(bar, "•", "Bulleted list", self._toggle_bullet_list)
            self._tool_action(bar, "1.", "Numbered list", self._toggle_numbered_list)
            bar.addSeparator()
            self._tool_action(bar, "🔍", "Find in this text (Ctrl+F)", self.show_find_bar)
            return bar

        bar.addSeparator()

        self._tool_action(bar, "🎨", "Text color", self._pick_text_color, menu=self._build_color_menu())
        self._tool_action(bar, "🖍", "Highlight (text background color)",
                           self._pick_highlight_color, menu=self._build_highlight_menu())
        self._tool_action(bar, "✧", "Clear formatting (keep the text, drop its styling)",
                           self._clear_formatting)

        bar.addSeparator()
        self._tool_action(bar, "🔍", "Find in this entry (Ctrl+F)", self.show_find_bar)
        self._build_zoom_controls(bar)
        return bar

    def _build_paragraph_toolbar(self) -> QToolBar:
        bar = QToolBar()
        bar.setFloatable(False)
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextOnly)

        # ---- Paragraph group: everything that formats whole paragraphs
        # (alignment, line spacing, spacing before/after, indent, lists,
        # quote) — applied via _each_selected_block() to every paragraph
        # the selection touches, the same pattern indent/outdent already
        # established.
        self.align_left_btn = self._tool_action(bar, "⟸", "Align Left (Ctrl+Shift+L)",
                                                  lambda: self._set_alignment(Qt.AlignLeft), checkable=True)
        self.align_center_btn = self._tool_action(bar, "⟺", "Align Center (Ctrl+Shift+E)",
                                                    lambda: self._set_alignment(Qt.AlignHCenter), checkable=True)
        self.align_right_btn = self._tool_action(bar, "⟹", "Align Right (Ctrl+Shift+R)",
                                                   lambda: self._set_alignment(Qt.AlignRight), checkable=True)
        self.align_justify_btn = self._tool_action(bar, "☰", "Justify (Ctrl+Shift+J)",
                                                     lambda: self._set_alignment(Qt.AlignJustify), checkable=True)
        self._alignment_group = QActionGroup(self)
        self._alignment_group.setExclusive(True)
        for btn in (self.align_left_btn, self.align_center_btn, self.align_right_btn, self.align_justify_btn):
            self._alignment_group.addAction(btn)

        self.line_spacing_combo = QComboBox()
        self.line_spacing_combo.setToolTip("Line spacing (applies to the whole paragraph)")
        for label, _pct in self.LINE_SPACING_PRESETS:
            self.line_spacing_combo.addItem(label)
        self.line_spacing_combo.currentIndexChanged.connect(self._on_line_spacing_changed)
        bar.addWidget(self.line_spacing_combo)

        self.space_before_spin = self._build_spacing_spin(
            "Space before paragraph", self._on_space_before_changed)
        bar.addWidget(QLabel("¶↑"))
        bar.addWidget(self.space_before_spin)

        self.space_after_spin = self._build_spacing_spin(
            "Space after paragraph", self._on_space_after_changed)
        bar.addWidget(QLabel("¶↓"))
        bar.addWidget(self.space_after_spin)

        bar.addSeparator()

        self._tool_action(bar, "→|", "Increase indent", self._indent)
        self._tool_action(bar, "|←", "Decrease indent", self._outdent)
        self._tool_action(bar, "•", "Bullet list", self._toggle_bullet_list)
        self._tool_action(bar, "1.", "Numbered list", self._toggle_numbered_list)
        self._tool_action(bar, "❝", "Quote", self._toggle_quote)

        bar.addSeparator()

        # ---- Insert group
        self._tool_action(bar, "🔗", "Insert Link (select text first, optional)", self.text_edit.insert_link)
        self._tool_action(bar, "🖼", "Insert Photo", self.text_edit.insert_photo)

        QShortcut(QKeySequence.Bold, self.text_edit, activated=self._toggle_bold)
        QShortcut(QKeySequence.Italic, self.text_edit, activated=self._toggle_italic)
        QShortcut(QKeySequence.Underline, self.text_edit, activated=self._toggle_underline)
        QShortcut(QKeySequence.ZoomIn, self.text_edit, activated=self._zoom_in)
        QShortcut(QKeySequence.ZoomOut, self.text_edit, activated=self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self.text_edit, activated=self._zoom_reset)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self.text_edit, activated=lambda: self._set_alignment(Qt.AlignLeft))
        QShortcut(QKeySequence("Ctrl+Shift+E"), self.text_edit, activated=lambda: self._set_alignment(Qt.AlignHCenter))
        QShortcut(QKeySequence("Ctrl+Shift+R"), self.text_edit, activated=lambda: self._set_alignment(Qt.AlignRight))
        QShortcut(QKeySequence("Ctrl+Shift+J"), self.text_edit, activated=lambda: self._set_alignment(Qt.AlignJustify))
        return bar

    def _tool_action(self, bar: QToolBar, label, tooltip, handler, checkable=False, menu=None) -> QAction:
        action = QAction(label, bar)
        action.setToolTip(tooltip)
        action.setCheckable(checkable)
        action.triggered.connect(handler)
        bar.addAction(action)
        if menu is not None:
            # A small "▾"-style popup (right-click, or the widget Qt builds
            # for the action) offering related choices beyond the single
            # click action — used for Text color's "Remove color" option.
            tb = bar.widgetForAction(action)
            if isinstance(tb, QToolButton):
                tb.setMenu(menu)
                tb.setPopupMode(QToolButton.MenuButtonPopup)
        return action

    SPACING_MAX_PT = 72.0

    def _build_spacing_spin(self, tooltip: str, on_change) -> QDoubleSpinBox:
        """A paragraph-spacing field sized to its own widest legal value.

        The previous version capped these at 70px while the widest value
        ("72.00 pt") needs 78px, so the text was clipped — the reported
        symptom. Rather than guessing a bigger number, the width is measured:
        the box is asked for its own sizeHint with the maximum value loaded,
        and that becomes its minimum. One decimal instead of two, because
        paragraph spacing is never specified to a hundredth of a point and
        the extra digit was pure width.
        """
        # Whole pixels (4C1b, bugs 33 and Q5): Qt's paragraph margins are
        # pixels, stored as `margin-…:12px`, and Qt reads a fractional value
        # back rounded, so a fraction could not survive a reload.
        spin = QDoubleSpinBox()
        spin.setRange(0, self.SPACING_MAX_PT)
        spin.setDecimals(0)
        spin.setSingleStep(2)
        spin.setSuffix(" px")
        spin.setToolTip(tooltip)
        spin.setKeyboardTracking(False)  # don't apply half-typed values
        spin.setValue(self.SPACING_MAX_PT)
        spin.setMinimumWidth(spin.sizeHint().width())
        spin.setValue(0)
        spin.valueChanged.connect(on_change)
        return spin

    def _build_zoom_controls(self, bar: QToolBar):
        """Zoom is a temporary, view-only magnification of the writing text —
        separate from the writing font size, and never saved with the entry.

        The percentage is a real editable control rather than a label, and it
        shares one state with Ctrl+wheel: the editor owns the zoom level and
        emits zoomChanged, this box reflects it, and typing into this box
        drives the editor. Signals are blocked on the programmatic path so
        the two cannot ping-pong."""
        self._tool_action(bar, "−", "Zoom out (Ctrl+-)", self._zoom_out)

        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(100 + ZOOM_STEP_MIN * ZOOM_PERCENT_PER_STEP,
                                 100 + ZOOM_STEP_MAX * ZOOM_PERCENT_PER_STEP)
        self.zoom_spin.setSingleStep(ZOOM_PERCENT_PER_STEP)
        self.zoom_spin.setSuffix("%")
        self.zoom_spin.setKeyboardTracking(False)
        self.zoom_spin.setToolTip(
            "View zoom — magnifies the text on screen only. It never changes "
            "the font sizes stored in your entry. Ctrl+scroll does the same thing."
        )
        self.zoom_spin.setValue(100 + ZOOM_STEP_MAX * ZOOM_PERCENT_PER_STEP)
        self.zoom_spin.setMinimumWidth(self.zoom_spin.sizeHint().width())
        self.zoom_spin.setValue(100)
        self.zoom_spin.valueChanged.connect(self._on_zoom_spin_changed)
        bar.addWidget(self.zoom_spin)

        self._tool_action(bar, "+", "Zoom in (Ctrl++)", self._zoom_in)
        self._tool_action(bar, "⟲", "Reset zoom to 100% (Ctrl+0)", self._zoom_reset)

    def _on_zoom_spin_changed(self, percent: int):
        steps = round((percent - 100) / ZOOM_PERCENT_PER_STEP)
        self.text_edit.set_zoom_steps(steps)

    def _on_editor_zoom_changed(self, percent: int):
        """The editor changed zoom (Ctrl+wheel, a shortcut, or a reset) —
        reflect it without driving the editor back."""
        if not hasattr(self, "zoom_spin"):
            return
        blocked = self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(percent)
        self.zoom_spin.blockSignals(blocked)

    def _build_color_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Choose Color…", self._pick_text_color)
        menu.addAction("Remove Color (match surrounding text)", self._clear_text_color)
        return menu

    HIGHLIGHT_PRESETS = [
        ("Yellow", "#ffe066"),
        ("Green", "#c3f0ca"),
        ("Blue", "#cfe4ff"),
        ("Pink", "#ffd6e7"),
        ("Orange", "#ffd8a8"),
    ]

    def _build_highlight_menu(self) -> QMenu:
        """Preset swatches plus a full picker. Presets exist because
        highlighting is overwhelmingly a "mark this passage" action where any
        of a handful of pale colors will do, and making the common case one
        click rather than a modal color dialog is the difference between the
        control being used and being ignored."""
        menu = QMenu(self)
        for label, hex_value in self.HIGHLIGHT_PRESETS:
            menu.addAction(label, lambda checked=False, c=hex_value: self._apply_highlight(QColor(c)))
        menu.addSeparator()
        menu.addAction("Choose Color…", self._pick_highlight_color)
        menu.addAction("Remove Highlight", self._clear_highlight)
        return menu

    # -------------------------------------------------------------- state
    def _sync_toolbar_state(self):
        # A read-only editor (version and recovery previews) has no toolbar
        # to keep in step — moving the cursor in one raised AttributeError.
        if not hasattr(self, "bold_btn"):
            return
        fmt = self.text_edit.textCursor().charFormat()
        self.bold_btn.blockSignals(True)
        self.bold_btn.setChecked(fmt.fontWeight() >= QFont.Bold)
        self.bold_btn.blockSignals(False)
        self.italic_btn.blockSignals(True)
        self.italic_btn.setChecked(fmt.fontItalic())
        self.italic_btn.blockSignals(False)
        self.underline_btn.blockSignals(True)
        self.underline_btn.setChecked(fmt.fontUnderline())
        self.underline_btn.blockSignals(False)
        if hasattr(self, "strike_btn"):
            self.strike_btn.blockSignals(True)
            self.strike_btn.setChecked(fmt.fontStrikeOut())
            self.strike_btn.blockSignals(False)
            self.super_btn.blockSignals(True)
            self.super_btn.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript)
            self.super_btn.blockSignals(False)
            self.sub_btn.blockSignals(True)
            self.sub_btn.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSubScript)
            self.sub_btn.blockSignals(False)

        block_fmt = self.text_edit.textCursor().blockFormat()
        level = block_fmt.headingLevel()
        self.heading_combo.blockSignals(True)
        self.heading_combo.setCurrentIndex(level if 0 <= level <= 3 else 0)
        self.heading_combo.blockSignals(False)

        if hasattr(self, "align_left_btn"):
            alignment = block_fmt.alignment()
            # Qt.AlignLeft is 0 (no bits set) under the hood, which is also
            # what an alignment mask that only carries vertical flags looks
            # like — masking to just the horizontal bits before comparing
            # is what makes "no explicit alignment yet" reliably read as
            # "Left" instead of matching nothing.
            horizontal = alignment & Qt.AlignHorizontal_Mask
            for btn, flag in (
                (self.align_left_btn, Qt.AlignLeft), (self.align_center_btn, Qt.AlignHCenter),
                (self.align_right_btn, Qt.AlignRight), (self.align_justify_btn, Qt.AlignJustify),
            ):
                btn.blockSignals(True)
                btn.setChecked(horizontal == flag)
                btn.blockSignals(False)

            line_height = block_fmt.lineHeight()
            closest_index = min(
                range(len(self.LINE_SPACING_PRESETS)),
                key=lambda i: abs(self.LINE_SPACING_PRESETS[i][1] - line_height),
            ) if line_height > 0 else 0
            self.line_spacing_combo.blockSignals(True)
            self.line_spacing_combo.setCurrentIndex(closest_index)
            self.line_spacing_combo.blockSignals(False)

            self.space_before_spin.blockSignals(True)
            self.space_before_spin.setValue(block_fmt.topMargin())
            self.space_before_spin.blockSignals(False)
            self.space_after_spin.blockSignals(True)
            self.space_after_spin.setValue(block_fmt.bottomMargin())
            self.space_after_spin.blockSignals(False)

        if hasattr(self, "family_combo"):
            # A selection with no explicit override (fontPointSize()==0, or
            # empty fontFamilies()) falls back to the document's base/
            # default font, not to 0 or a blank combo — matching what's
            # actually rendered at the cursor.
            size = fmt.fontPointSize()
            if size <= 0:
                size = self.text_edit._base_point_size
            self.size_spin.blockSignals(True)
            self.size_spin.setValue(int(round(size)))
            self.size_spin.blockSignals(False)

            families = fmt.fontFamilies()
            family = families[0] if families else self.text_edit.font().family()
            self.family_combo.blockSignals(True)
            self.family_combo.setCurrentFont(QFont(family))
            self.family_combo.blockSignals(False)

    # ------------------------------------------------------- writing font
    def _on_family_control_changed(self, qfont: QFont):
        """Applies to the current selection only (like Bold/Italic) — or to
        whatever's typed next if nothing is selected. Does NOT change the
        saved default font; that's set_font()/Settings' doing."""
        fmt = QTextCharFormat()
        fmt.setFontFamilies([qfont.family()])
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _on_size_control_changed(self, size: int):
        """Applies to the current selection only (like Bold/Italic) — or to
        whatever's typed next if nothing is selected. Does NOT change the
        saved default font; that's set_font()/Settings' doing."""
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self.text_edit.mergeCurrentCharFormat(fmt)

    # -------------------------------------------------------------- zoom
    # Zoom is owned by the editor (see RichTextEditor.set_zoom_steps). These
    # just ask it to change; the displayed percentage updates from its
    # zoomChanged signal, so Ctrl+wheel, the buttons, the shortcuts and the
    # percentage box can never disagree about the current level.
    def _zoom_in(self):
        self.text_edit.zoom_in()

    def _zoom_out(self):
        self.text_edit.zoom_out()

    def _zoom_reset(self):
        self.text_edit.reset_zoom()

    def _update_zoom_label(self):
        self._on_editor_zoom_changed(self.text_edit.zoom_percent())

    # ----------------------------------------------------------- toggling
    def _toggle_bold(self):
        is_bold = self.text_edit.textCursor().charFormat().fontWeight() >= QFont.Bold
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if is_bold else QFont.Bold)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _toggle_italic(self):
        is_italic = self.text_edit.textCursor().charFormat().fontItalic()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not is_italic)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _toggle_underline(self):
        is_underline = self.text_edit.textCursor().charFormat().fontUnderline()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not is_underline)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _toggle_strikethrough(self):
        is_struck = self.text_edit.textCursor().charFormat().fontStrikeOut()
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not is_struck)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _toggle_superscript(self):
        """Superscript and subscript are mutually exclusive by construction
        — QTextCharFormat.verticalAlignment() is a single-valued property,
        not two independent flags — so turning one on always implicitly
        turns the other off; there's no separate bookkeeping needed for
        that here, only for the toolbar buttons' own checked state (see
        _sync_toolbar_state)."""
        current = self.text_edit.textCursor().charFormat().verticalAlignment()
        fmt = QTextCharFormat()
        fmt.setVerticalAlignment(
            QTextCharFormat.AlignNormal if current == QTextCharFormat.AlignSuperScript
            else QTextCharFormat.AlignSuperScript
        )
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _toggle_subscript(self):
        current = self.text_edit.textCursor().charFormat().verticalAlignment()
        fmt = QTextCharFormat()
        fmt.setVerticalAlignment(
            QTextCharFormat.AlignNormal if current == QTextCharFormat.AlignSubScript
            else QTextCharFormat.AlignSubScript
        )
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _apply_heading(self, index: int):
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        block_fmt = QTextBlockFormat()
        block_fmt.setHeadingLevel(index)
        cursor.mergeBlockFormat(block_fmt)

        # headingLevel alone drives Markdown export correctly, but doesn't
        # reliably restyle already-typed text in every Qt version — set the
        # visible size/weight for this block directly too, so toggling a
        # heading looks right immediately, not just after a save/reload.
        sizes = {0: None, 1: 20, 2: 16, 3: 13}
        line_cursor = QTextCursor(cursor)
        line_cursor.select(QTextCursor.LineUnderCursor)
        char_fmt = QTextCharFormat()
        char_fmt.setFontWeight(QFont.Normal if index == 0 else QFont.Bold)
        if sizes[index] is not None:
            char_fmt.setFontPointSize(sizes[index])
        else:
            char_fmt.setFontPointSize(self.text_edit._base_point_size or 13)
        line_cursor.mergeCharFormat(char_fmt)
        cursor.endEditBlock()
        self.text_edit.setTextCursor(cursor)

    def _toggle_bullet_list(self):
        cursor = self.text_edit.textCursor()
        current_list = cursor.currentList()
        if current_list and current_list.format().style() == QTextListFormat.ListDisc:
            self._remove_list(cursor)
        else:
            cursor.createList(QTextListFormat.ListDisc)

    def _toggle_numbered_list(self):
        cursor = self.text_edit.textCursor()
        current_list = cursor.currentList()
        if current_list and current_list.format().style() == QTextListFormat.ListDecimal:
            self._remove_list(cursor)
        else:
            cursor.createList(QTextListFormat.ListDecimal)

    def _remove_list(self, cursor: QTextCursor):
        block_fmt = cursor.blockFormat()
        block_fmt.setIndent(0)
        cursor.setBlockFormat(block_fmt)
        current_list = cursor.currentList()
        if current_list:
            current_list.remove(cursor.block())

    def _toggle_quote(self):
        cursor = self.text_edit.textCursor()
        block_fmt = cursor.blockFormat()
        is_quote = block_fmt.leftMargin() > 0
        block_fmt.setLeftMargin(0 if is_quote else 24)
        block_fmt.setRightMargin(0 if is_quote else 24)
        cursor.mergeBlockFormat(block_fmt)

    # ------------------------------------------------------------- color
    def _pick_text_color(self):
        """Colors the current selection (or, with nothing selected, what's
        about to be typed next) — the same selection-scoped mechanism as
        Bold/Italic. Also reachable via the color button's dropdown
        ("Choose Color…"), alongside "Remove Color" below — useful for
        toning down or removing the coloured label on an inserted block,
        or any other coloured text, without having to retype it."""
        cursor = self.text_edit.textCursor()
        current = cursor.charFormat().foreground().color()
        if not current.isValid():
            current = self.text_edit.palette().color(self.text_edit.foregroundRole())
        chosen = QColorDialog.getColor(current, self, "Text Color")
        if not chosen.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(chosen)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _clear_text_color(self):
        """Resets the selection's text color back to whatever plain text
        around it uses — there's no real Qt notion of "no color set" to
        revert to once a color has been explicitly applied, so this
        explicitly reapplies the editor's normal text color instead, which
        has the same visible effect."""
        fmt = QTextCharFormat()
        fmt.setForeground(self.text_edit.palette().color(self.text_edit.foregroundRole()))
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _apply_highlight(self, color: QColor):
        """Highlight is the character format's BACKGROUND brush — Qt's own
        native mechanism for it, which is why it round-trips through
        toHtml()/setHtml() as an ordinary background-color style and survives
        export to the static HTML archive unchanged (verified directly)."""
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _pick_highlight_color(self):
        cursor = self.text_edit.textCursor()
        current = cursor.charFormat().background().color()
        if not current.isValid():
            current = QColor(self.HIGHLIGHT_PRESETS[0][1])
        chosen = QColorDialog.getColor(current, self, "Highlight Color")
        if chosen.isValid():
            self._apply_highlight(chosen)

    def _clear_highlight(self):
        """Clears the background back to transparent.

        `setBackground(Qt.transparent)` rather than an empty QBrush: merging
        a format whose background property was never SET leaves the existing
        background untouched (merge only applies properties that are present),
        so an empty format would silently do nothing here."""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(Qt.transparent))
        self.text_edit.mergeCurrentCharFormat(fmt)

    def _clear_formatting(self):
        """Strips character formatting from the selection, keeping the text.

        Deliberately character-level only: paragraph properties (alignment,
        line spacing, spacing before/after, indentation, list membership)
        are left alone, because "clear formatting" in every word processor
        means "make this text look like plain body text", not "un-indent my
        list and re-left-align the paragraph I just centered". Those have
        their own controls.

        Applied by REPLACING the char format rather than merging one — a
        merge can only set properties, never unset them, so merging could
        never remove an existing bold/color/highlight. The replacement
        deliberately carries the editor's current default font family and
        size so cleared text matches surrounding body text rather than
        dropping to whatever Qt's built-in default happens to be.
        """
        cursor = self.text_edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setFont(self.text_edit.base_font())
        fmt.setForeground(self.text_edit.palette().color(self.text_edit.foregroundRole()))
        fmt.setBackground(QColor(Qt.transparent))
        if cursor.hasSelection():
            cursor.setCharFormat(fmt)
        else:
            self.text_edit.setCurrentCharFormat(fmt)
        self._sync_toolbar_state()

    # ------------------------------------------------------------ indent
    def _each_selected_block(self, cursor: QTextCursor):
        """Yields a fresh QTextCursor positioned on each block (paragraph)
        the current selection touches — just the one block the cursor sits
        in if nothing's selected — so Indent/Outdent can affect every
        paragraph in a multi-paragraph selection, not only the first one,
        the way a word processor's indent buttons do."""
        if not cursor.hasSelection():
            yield QTextCursor(cursor)
            return
        doc = self.text_edit.document()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = doc.findBlock(start)
        while block.isValid() and block.position() < end:
            yield QTextCursor(block)
            block = block.next()

    def _adjust_indent(self, delta: int):
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for block_cursor in self._each_selected_block(cursor):
            fmt = block_cursor.blockFormat()
            fmt.setLeftMargin(max(0, fmt.leftMargin() + delta))
            fmt.setRightMargin(max(0, fmt.rightMargin() + delta))
            block_cursor.setBlockFormat(fmt)
        cursor.endEditBlock()

    def _indent(self):
        self._adjust_indent(INDENT_STEP)

    def _outdent(self):
        self._adjust_indent(-INDENT_STEP)

    # --------------------------------------------------------- paragraph fmt
    #
    # Alignment, line spacing, and spacing-before/after are all paragraph-
    # (QTextBlockFormat-) level properties, applied to every paragraph the
    # current selection touches via _each_selected_block() — exactly the
    # same pattern _adjust_indent() above already established. All three
    # round-trip losslessly through toHtml()/setHtml() (see rich_editor.py's
    # module docstring) — none of this was expressible in the old Markdown
    # persistence format at all, which is why none of it existed before
    # round 21.
    def _set_alignment(self, alignment: Qt.AlignmentFlag):
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for block_cursor in self._each_selected_block(cursor):
            fmt = block_cursor.blockFormat()
            fmt.setAlignment(alignment)
            block_cursor.setBlockFormat(fmt)
        cursor.endEditBlock()
        self._sync_toolbar_state()

    # (label, line-height-as-percent-of-single-spacing) — QTextBlockFormat's
    # ProportionalHeight type takes exactly this: 100 == single spacing.
    LINE_SPACING_PRESETS = [
        ("Single", 100.0),
        ("1.15", 115.0),
        ("1.5", 150.0),
        ("Double", 200.0),
    ]

    def _on_line_spacing_changed(self, index: int):
        if index < 0:
            return
        _label, percent = self.LINE_SPACING_PRESETS[index]
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for block_cursor in self._each_selected_block(cursor):
            fmt = block_cursor.blockFormat()
            fmt.setLineHeight(percent, QTextBlockFormat.ProportionalHeight.value)
            block_cursor.setBlockFormat(fmt)
        cursor.endEditBlock()

    def _on_space_before_changed(self, value: float):
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for block_cursor in self._each_selected_block(cursor):
            fmt = block_cursor.blockFormat()
            fmt.setTopMargin(value)
            block_cursor.setBlockFormat(fmt)
        cursor.endEditBlock()

    def _on_space_after_changed(self, value: float):
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for block_cursor in self._each_selected_block(cursor):
            fmt = block_cursor.blockFormat()
            fmt.setBottomMargin(value)
            block_cursor.setBlockFormat(fmt)
        cursor.endEditBlock()

    # ------------------------------------------------------------- find bar
    #
    # Ctrl+F. Deliberately never touches the document's content — every
    # operation here is either a read (RichTextEditor.find_all_ranges()) or
    # moving/highlighting via QTextCursor selections and setExtraSelections
    # (a purely visual overlay, not a document edit) — so searching can
    # never modify what's saved, by construction, not just by care.
    def _build_find_bar(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.NoFrame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 2, 4, 2)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in this entry…")
        self.find_input.textChanged.connect(self._on_find_text_changed)
        self.find_input.installEventFilter(self)

        self.find_match_label = QLabel("")
        self.find_match_label.setMinimumWidth(70)

        prev_btn = QPushButton("↑ Prev")
        prev_btn.setToolTip("Previous match (Shift+Enter)")
        prev_btn.clicked.connect(lambda: self._jump_to_match(-1))
        next_btn = QPushButton("↓ Next")
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(lambda: self._jump_to_match(1))
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self.hide_find_bar)

        row.addWidget(QLabel("Find:"))
        row.addWidget(self.find_input, stretch=1)
        row.addWidget(self.find_match_label)
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addWidget(close_btn)
        frame.setVisible(False)
        return frame

    def show_find_bar(self):
        self.find_bar.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()
        if self.find_input.text():
            self._on_find_text_changed(self.find_input.text())

    def hide_find_bar(self):
        self.find_bar.setVisible(False)
        self._find_ranges = []
        self.text_edit.setExtraSelections([])
        self.text_edit.setFocus()

    def _on_find_text_changed(self, term: str):
        self._find_ranges = self.text_edit.find_all_ranges(term, case_sensitive=False)
        self._find_current_index = -1
        if not term:
            self.find_match_label.setText("")
            self.text_edit.setExtraSelections([])
            return
        if not self._find_ranges:
            self.find_match_label.setText("No matches")
            self.text_edit.setExtraSelections([])
            return
        # Jump to whichever match is at/after the current cursor position,
        # wrapping to the first match if the cursor is past all of them —
        # so typing a search term jumps straight to the nearest hit instead
        # of always restarting from the top of the document.
        pos = self.text_edit.textCursor().position()
        start_index = next(
            (i for i, (s, _e) in enumerate(self._find_ranges) if s >= pos), 0
        )
        self._select_match(start_index)

    def _jump_to_match(self, direction: int):
        if not self._find_ranges:
            return
        index = self._find_current_index + direction
        index %= len(self._find_ranges)
        self._select_match(index)

    def _select_match(self, index: int):
        self._find_current_index = index
        start, end = self._find_ranges[index]
        cursor = QTextCursor(self.text_edit.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        self.find_match_label.setText(f"{index + 1} of {len(self._find_ranges)}")
        self._highlight_all_matches(current_index=index)

    def _highlight_all_matches(self, current_index: int):
        selections = []
        for i, (start, end) in enumerate(self._find_ranges):
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(
                QColor("#ffb347") if i == current_index else QColor("#fff3b0")
            )
            selections.append(selection)
        self.text_edit.setExtraSelections(selections)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "find_input", None) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._jump_to_match(-1 if event.modifiers() & Qt.ShiftModifier else 1)
                return True
            if event.key() == Qt.Key_Escape:
                self.hide_find_bar()
                return True
        return super().eventFilter(obj, event)

    # -------------------------------------------------------------- content
    def set_storage_subdir(self, relative_dir: str):
        self.text_edit.set_storage_subdir(relative_dir)

    def load(self, content: str, fmt: str = "html"):  # noqa: D401
        """Loads previously-saved content into the editor. `fmt` is
        whatever database.py's body_format/content_format column said for
        this row: "html" (the round-21+ canonical representation — see this
        module's docstring) or "markdown" (any row saved before round 21,
        loaded through the old, still-fully-supported toMarkdown()/
        setMarkdown() path so nothing written before this change is ever
        misread). Every SAVE from here on always writes "html" (see
        save() below) — a "markdown" row transparently upgrades to "html"
        the next time it's saved, it's never rewritten just by opening it."""
        self.text_edit.blockSignals(True)
        # Each document keeps the writing font stored with it (4C1a-D1): the
        # editor takes that font BEFORE the HTML is read, so Qt has nothing
        # to write onto the characters as explicit formatting, and an
        # unedited save gives back the same bytes whatever the setting is now.
        # A document without a stored font (empty, legacy Markdown) starts in
        # the new-document font. Zoom is kept (adopt_document_font).
        own_font = stored_document_font(content) if fmt != "markdown" else None
        self._document_font = own_font or self._new_document_font
        self.text_edit.adopt_document_font(*self._document_font)
        if fmt == "markdown":
            self.text_edit.setMarkdown(content or "")
        else:
            self.text_edit.setHtml(unwrap_root_frame(content or ""))
        # Headings: Qt adds its own size bump to heading text it reads back
        # (+3 / +2 / +1 for H1–H3). From HTML that bump is spurious — the
        # size the user applied is already stored — so it is removed, and the
        # heading shows and re-saves exactly as written (bug 37). From a
        # legacy Markdown row it is the heading's only size, so it is made
        # explicit and the upgrade on save keeps it (4C1b-D1). Done here,
        # before the document is marked unmodified and its undo history
        # cleared (4C1b/AM-7).
        _settle_heading_sizes(self.text_edit.document(), keep_size=(fmt == "markdown"))
        # A stored document that is blank under the shared written/blank rule
        # (an emptied page that still records an old font) is a new, empty
        # document: it takes the current setting, as §14.9 says. A written one,
        # including an image-only one, keeps its stored font (4C1a/AM-13).
        # Nothing was written in it, so there is no text the font could be
        # baked into.
        if own_font is not None and not document_has_content(
                self.text_edit.toHtml(), self.text_edit.toPlainText()):
            self._document_font = self._new_document_font
            self.text_edit.adopt_document_font(*self._document_font)
            # Its empty paragraphs still carry the old font in their own
            # formats (written there when the page was saved): clear it, so
            # typed text inherits the document's font. No explicit font is set.
            _clear_font_formats(self.text_edit.document())
        # A fresh cursor: the editor's old cursor keeps the insertion format
        # of the PREVIOUS document's caret across setHtml(), so text typed
        # into a new document was stored in that document's font as explicit
        # spans (4C1a-F1/AM-1, §14.9). A fresh cursor has no insertion format
        # of its own; Qt takes it from the text at the caret — none in an
        # empty document (typed text inherits the document's own font), the
        # bold or heading text at the start of a written paragraph.
        self.text_edit.setTextCursor(QTextCursor(self.text_edit.document()))
        self.text_edit.blockSignals(False)
        # setHtml()/setMarkdown() both rebuild the document's root frame,
        # which silently drops the bottom padding set up in
        # RichTextEditor.__init__/resizeEvent — reapply it so switching to
        # a different day/project doesn't lose the "scroll past the last
        # line" padding.
        self.text_edit._update_bottom_padding()
        # Loading replaces the document wholesale, which discards the
        # per-fragment base sizes zoom relies on and resets every character
        # to its stored (100%) size. Re-apply the current zoom level so
        # switching days doesn't silently snap the view back to 100%.
        self.text_edit.reapply_zoom_after_load()
        # A freshly loaded document is, by definition, exactly what is
        # stored: it starts clean, with nothing to undo. Clearing the stack
        # here matters because re-applying the zoom level (just above) is a
        # real document edit — without this, the first Ctrl+Z after opening
        # an entry would undo the app's own zoom restoration rather than
        # anything the user did. Everything recorded after this point is a
        # genuine edit by them.
        self.text_edit.document().clearUndoRedoStacks()
        self.text_edit.document().setModified(False)
        # The font and size boxes show the loaded document's own font at once,
        # not the previous document's until the caret moves (4C1a-F1, F1-D3).
        self._sync_toolbar_state()

    def save(self) -> tuple[str, str]:
        """Returns (html, plain_text) for persistence — see database.py's
        body_format/body_text (or content_format/content_text) columns.
        The caller always saves html together with format="html": every
        save upgrades whatever was loaded (even a legacy "markdown" row) to
        the lossless native representation from here on, per load()'s
        docstring above. plain_text is the derived view that search and
        the entry-exists test read instead of html — see database.py's
        module docstring for why raw HTML must never leak into those.

        Exported at zoom=0 regardless of the current on-screen zoom, so
        what's saved is never influenced by a temporary view setting —
        belt-and-suspenders on top of zoom already not touching the saved
        document (zoomIn()/zoomOut() only ever change the on-screen QFont's
        pixel metrics, never any QTextCharFormat stored in the document).
        #
        # Resetting zoom and reapplying it changes on-screen font sizes
        # twice in a row, and Qt does not reliably preserve the exact
        # scroll position across that (it keeps roughly the same top-of-
        # viewport block anchored, not the same pixel offset) — visible as
        # the view jumping away from the bottom while you're mid-entry,
        # since this runs on every autosave. Explicitly saving and
        # restoring the scrollbar value around the zoom dance makes save()
        # a read-only operation from the user's point of view, exactly as
        # it should be — it's meant to just report the content, never to
        # move the view."""
        # Serialized from a CLONE — see export_document() for why. The live
        # document, its undo history and its modified flag are untouched by
        # saving, which is what makes Ctrl+Z after Ctrl+S undo the user's
        # edit rather than the save's own housekeeping.
        document = export_document(self.text_edit)
        try:
            # Newly written dates become internal links in the stored copy
            # only, never in the live document (bug 8, 4C1a-D3); on screen a
            # typed date navigates on click anyway (RichTextEditor). Gated
            # on link_dates because a Writing Project that happens to mention
            # a date shouldn't gain a link that jumps the Daily Journal
            # somewhere else.
            if self.link_dates:
                link_dates_in(document)
            html = document.toHtml()
            plain = document.toPlainText()
        finally:
            document.deleteLater()
        return html, plain

    def is_dirty(self) -> bool:
        """Whether this document has edits that aren't saved yet.

        Qt's own per-document modified flag, not a second flag kept in
        parallel — it is set by every real edit and by nothing else, now
        that saving works on a clone and zoom restores it (see
        export_document()). One flag, set by the editor, cleared by the one
        save path.
        """
        return self.text_edit.document().isModified()

    def mark_clean(self):
        """Called by the save path once the content is safely persisted."""
        self.text_edit.document().setModified(False)

    def plain_text(self) -> str:
        """A lightweight plain-text read of the current content — unlike
        save(), this doesn't need the zoom/scrollbar dance (zoom is purely
        a view-level font size change; it never affects toPlainText())."""
        return self.text_edit.toPlainText()

    def set_writing_position(self, position: str):
        self.text_edit.set_writing_position(position)

    def refresh_control_sizes(self):
        """Re-measures the numeric controls after an application font change.

        Their minimum widths are derived from the widest value they can
        display, which depends on the current font — so a width measured once
        at construction is wrong the moment the application font size
        changes, and the values start clipping again. Called from the
        window's settings-applied path."""
        if getattr(self, "family_combo", None) is not None:
            self.family_combo.setMaximumWidth(font_scaled(150))
        for spin, biggest in (
            (getattr(self, "space_before_spin", None), self.SPACING_MAX_PT),
            (getattr(self, "space_after_spin", None), self.SPACING_MAX_PT),
            (getattr(self, "zoom_spin", None), None),
        ):
            if spin is None:
                continue
            current = spin.value()
            blocked = spin.blockSignals(True)
            spin.setValue(biggest if biggest is not None else spin.maximum())
            spin.setMinimumWidth(spin.sizeHint().width())
            spin.setValue(current)
            spin.blockSignals(blocked)

    def set_font(self, family: str, size: int):
        """Sets the DEFAULT writing font — the font brand-new, empty
        documents start in; a document with content keeps the font stored
        with it (see load()) — as opposed to zoom (view-only) or the toolbar's own family/size
        controls (which restyle a selection, like Bold/Italic, and never
        touch this default). Safe to call whether or not this editor has a
        toolbar (read-only preview editors don't build one).

        Applying a SETTING is not an edit. Qt marks the document modified
        for any format change, so without this, opening Settings and
        changing the font would leave every open document "unsaved" and
        prompt on the way out — the user would be asked to save writing
        they never touched. Same rule as zoom: the modified flag means the
        user changed the content, and nothing else may set it."""
        was_modified = self.text_edit.document().isModified()
        # The setting is the font NEW documents start in (4C1a-D1, Master Spec
        # §14.9 decided 2026-10-02). An empty, unwritten document open now
        # takes it at once; a document with content keeps its own stored
        # font. Either way zoom is reset as before (bug 6, left to 4C2).
        self._new_document_font = (family, float(size))
        document = self.text_edit.document()
        if document_has_content(document.toHtml(), document.toPlainText()):
            # Re-assert the document's own font, not the setting: the
            # settings path has just applied the application font and the
            # stylesheet, which override this widget's font (FP-5), and the
            # document's font must not follow them.
            self.text_edit.reset_zoom()
            self.text_edit.adopt_document_font(*self._document_font)
        else:
            self.text_edit.set_base_font(family, size)
            self._document_font = (family, float(size))
        self.text_edit.document().setModified(was_modified)
        if hasattr(self, "family_combo"):
            # Re-derive the toolbar's displayed family/size from the
            # cursor's actual current format rather than force-setting it —
            # if the cursor happens to sit on text someone already resized
            # individually, the toolbar should keep showing THAT, not the
            # new default.
            self._sync_toolbar_state()
            self._update_zoom_label()

    def follow_application_font(self, family: str, size: float):
        """For editors that never receive the writing-font setting (Reader's
        Notes, 4C1a-D2): a new, empty document starts in the application
        font; a document with content keeps its own stored font. Called after
        every settings change, because the application font and stylesheet
        override this widget's font (FP-5). Keeps the zoom level and the
        modified flag — unlike set_font(), it is not the settings' writing-font
        route and leaves bug 6's zoom reset alone."""
        document = self.text_edit.document()
        was_modified = document.isModified()
        self._new_document_font = (family, float(size))
        if not document_has_content(document.toHtml(), document.toPlainText()):
            self._document_font = self._new_document_font
        self.text_edit.adopt_document_font(*self._document_font)
        # Keep the zoom level on the default font only: re-applying zoom to
        # the characters would be an undoable edit of an open document
        # (FP-3); their zoomed sizes are untouched by a font change anyway.
        self.text_edit.zoom_default_font()
        document.setModified(was_modified)

    def append_note(self, label: str, text: str):
        """Appends a visually distinct block at the end of the document —
        an indented, italic passage under a small bold label.

        Nothing in the app calls this now; the removed AI feature did. It is
        kept because entries written while that feature existed contain
        blocks in exactly this shape, and the Indent/Outdent and colour
        controls are specified against it (see INDENT_STEP) — the tests pin
        that those blocks can still be un-indented and recoloured in one
        click, which is what a user with such an entry would actually do."""
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not cursor.atBlockStart():
            cursor.insertBlock()
        self.text_edit._reset_to_normal_style(cursor)

        label_fmt = QTextCharFormat()
        label_fmt.setFontWeight(QFont.Bold)
        label_fmt.setForeground(QColor("#3f8ede"))
        cursor.insertText(label, label_fmt)
        cursor.insertBlock()

        quote_fmt = QTextBlockFormat()
        quote_fmt.setLeftMargin(24)
        quote_fmt.setRightMargin(24)
        cursor.setBlockFormat(quote_fmt)
        note_fmt = QTextCharFormat()
        note_fmt.setFontItalic(True)
        cursor.insertText(text, note_fmt)
        cursor.insertBlock()
        self.text_edit._reset_to_normal_style(cursor)

        self.text_edit.setTextCursor(cursor)

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
