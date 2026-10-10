"""Group 4, batch 4C2b — rich paste, Paste as Plain Text, the Daily Jorts
empty-state text and the paragraph row's order.

Criteria C2b-1…C2b-8 with the amendments 4C2/AM-5…AM-10 and AM-18
(JORTLE_IMPLEMENTATION_HANDOFF.md, "4C2 plan — agreed 2026-10-10"); the
mutants of C2b-9 run against scratch copies of the app (see the handoff).

Sources (4C2b-D9, AM-5, AM-10): the user's real captures in
tests/fixtures/paste/ (Edge, Word), handed to the editor's paste path as a
Python-built QMimeData — never put on the clipboard; Jortle sources made in
this process with the editor's own Copy (the offscreen clipboard is this
process's own). Nothing here sends input to the desktop (FP-15).
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import sys
import time
import zipfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-paste-")
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "paste"
GRAB_DIR = pathlib.Path(os.environ.get("JORTLE_GRAB_DIR") or ROOT / "grabs")

from PySide6.QtCore import QBuffer, QByteArray, QMimeData, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QFontMetrics, QImage, QKeySequence, QPalette, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

import stress_document as sd  # noqa: E402
from app import archive, backup, commands, rich_paste  # noqa: E402
from app import main_window as mw  # noqa: E402
from app import projects_widget as pw_module  # noqa: E402
from app.cleanup import find_unused_photos  # noqa: E402
from app.hotkeys_page import HotkeysPage  # noqa: E402
from app.main_window import DAILY_JORTS_EMPTY_STATE, MainWindow  # noqa: E402
from app.paths import get_attachments_dir, get_data_dir  # noqa: E402
from app.rich_editor import RichEditor, RichTextEditor  # noqa: E402
from app.saving import SAVE  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

STARTED = time.monotonic()
print("IMPORTED app FROM", mw.__file__)
assert str(get_data_dir()).startswith(str(ROOT)), "data dir not isolated"
ATTACHMENTS = get_attachments_dir()

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def settle(ms=0):
    app.processEvents()
    if ms:
        QTest.qWait(ms)
    app.processEvents()


mw.ask_unsaved = lambda *_a, **_k: SAVE
pw_module.ask_unsaved = lambda *_a, **_k: SAVE
messages = []
QMessageBox.information = staticmethod(lambda *a, **k: messages.append(("info", a[1:3])) or QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: messages.append(("warning", a[1:3])) or QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: messages.append(("critical", a[1:3])) or QMessageBox.Ok)

# Every image resource any editor asks for (C2b-4: nothing remote, ever).
requested_resources = []
_original_load_resource = RichTextEditor.loadResource


def _recording_load_resource(self, kind, name):
    requested_resources.append(name.toString())
    return _original_load_resource(self, kind, name)


RichTextEditor.loadResource = _recording_load_resource

# ------------------------------------------------------------ the rule guard
# Every mapping rule of 4C2b-D3…D5 (AM-6) must be exercised by some check
# below (C2b-1's guard).
RULES = {
    "bold", "italic", "underline", "strikethrough", "superscript", "subscript", "alignment",
    "no external font family", "no external font size", "h1-h3 to Heading 1-3", "h4-h6 to Heading 3",
    "heading sizes are Jortle's", "spacing whole px", "line height kept", "lists as Qt reads them",
    "grey text colour removed", "chromatic text colour kept", "grey highlight removed",
    "chromatic highlight kept", "paragraph background removed", "table cell background removed",
    "http(s) link in Jortle style", "mailto and journal links kept", "other scheme becomes text",
    "table flattened", "nested table flattened", "local image copied", "data image decoded",
    "clipboard image saved as PNG", "remote image becomes a link", "missing local image becomes text",
    "image size kept", "forms, rules, scripts removed with text kept", "Word bullets become a list",
    "Word numbers become a numbered list", "Word list levels", "Word markers removed",
    "Jortle source keeps fonts", "Jortle source keeps colours and links", "Jortle source keeps image paths",
    "source order", "Qt heading bump removed (Jortle source)",
    # 4C2/AM-19…AM-23
    "empty-paragraph paste keeps own formats", "same font stores no span", "Jortle spaces kept",
    "trailing space kept at the end", "Word spacing in px", "nearly black or white colour removed",
}
exercised = set()


def rule(name, label, cond, detail=""):
    assert name in RULES, name
    exercised.add(name)
    check(f"[{name}] {label}", cond, detail)


# ------------------------------------------------------------ helpers
def bare(subdir="2026/10", family="Arial", size=11, link_dates=False):
    editor = RichEditor(link_dates=link_dates)
    editor.set_font(family, size)
    editor.set_storage_subdir(subdir)
    editor.resize(800, 600)
    editor.load("")
    return editor


def mime_html(html, text=None):
    mime = QMimeData()
    mime.setHtml(html)
    if text is not None:
        mime.setText(text)
    return mime


def fixture(name):
    """A captured fixture as the editor would receive it. Word's own temp
    files are not on this machine: a reference the capture copied is pointed
    at its copy in the fixture folder (file:///…/files/…)."""
    folder = FIXTURES / name
    html = (folder / "content.html").read_text(encoding="utf-8")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest.get("local_images", []):
        if entry.get("status") == "copied":
            html = html.replace(entry["reference"], QUrl.fromLocalFile(str(folder / entry["file"])).toString())
    text_path = folder / "content.txt"
    return mime_html(html, text_path.read_text(encoding="utf-8") if text_path.exists() else None)


def paste(editor, mime):
    editor.text_edit.insertFromMimeData(mime)
    settle()


def stored_fp(html):
    """A stored document read back the way the app reads it."""
    reader = RichEditor()
    reader.load(html)
    fp = sd.fingerprint(reader.text_edit.document())
    reader.deleteLater()
    return fp


def runs_with(fp, text):
    return [r for r in fp.runs if text in r.text]


def blocks_with(fp, text):
    return [b for b in fp.blocks if text in b.text]


def content_lines(html):
    """The stored paragraphs, without Qt's head and <body> line."""
    start = html.index(">", html.index("<body")) + 1
    lines = html[start:].split("\n")
    return lines[1:] if lines and lines[0] == "" else lines


def photo_files():
    return {p for p in ATTACHMENTS.rglob("*") if p.is_file()}


def without_photo_names(html):
    return re.sub(r"[0-9a-f]{32}\.(png|jpg|gif|bmp|webp)", "PHOTO", html)


def png_data_url(colour="#e01b24", w=6, h=4):
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(colour))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(data)).decode()


SOURCE_PHOTO = ROOT / "source-photo.png"
_img = QImage(30, 20, QImage.Format_RGB32)
_img.fill(QColor("#2e86c1"))
_img.save(str(SOURCE_PHOTO))


# ============================================================ C2b-1: mapping
print("\n--- [C2b-1] the Edge capture (Wikipedia article, real sample) ---")
before_files = photo_files()
edge = bare()
paste(edge, fixture("edge-article"))
edge_html = edge.save()[0]
fp = stored_fp(edge_html)
pasted_lines = content_lines(edge_html)
rule("no external font family", "no font-family on any pasted text",
     not any("font-family" in line for line in pasted_lines),
     [line[:120] for line in pasted_lines if "font-family" in line][:2])
sizes = {s for line in pasted_lines for s in re.findall(r"font-size:([\d.]+)pt", line)}
rule("no external font size", "the only sizes stored are Jortle's heading sizes", sizes <= {"20", "16", "13"}, sizes)
rule("grey text colour removed", "Wikipedia's #202122 and #101418-grey text greys are gone",
     "#202122" not in edge_html.lower() and "color:#ffffff" not in edge_html.lower())
rule("grey highlight removed", "white and black character backgrounds removed",
     "background-color:#ffffff" not in edge_html.lower() and "background-color:#000000" not in edge_html.lower())
rule("paragraph background removed", "no paragraph background stored",
     not any(re.match(r"<(p|li)\b[^>]*background", line) for line in pasted_lines))
talk = runs_with(fp, "Talk")
rule("http(s) link in Jortle style", "a link keeps its target and takes #3f8ede, underlined",
     any(r.href == "https://en.wikipedia.org/wiki/Talk:Iceland" and r.fg == "#3f8ede" and r.underline
         for r in talk), talk[:1])
flag = runs_with(fp, "[image: Flag of Iceland]")
rule("remote image becomes a link", "the flag is a link '[image: Flag of Iceland]' to its URL, not an image",
     flag and flag[0].href.startswith("https://thumb.wikimedia.org/") and "<img" not in edge_html, flag[:1])
check("an image without alt text becomes '[image]', linked", any(r.text == "[image]" and r.href.startswith("https://")
                                                                    for r in fp.runs))
check("nothing was downloaded or filed for the remote images", photo_files() == before_files)
rule("table flattened", "the infobox table is gone, its rows are tab-separated paragraphs",
     "<table" not in edge_html and any("\t" in b.text for b in fp.blocks))
rule("lists as Qt reads them", "the article's lists are lists", any(b.list_style for b in fp.blocks))
rule("superscript", "citation marks stay superscript",
     any(r.valign == "super" and "cite_note" in r.href for r in fp.runs))
rule("bold", "'Capital' stays bold", any(r.weight >= 600 for r in runs_with(fp, "Capital")))
rule("italic", "the hatnote stays italic", any(r.italic for r in runs_with(fp, "This article is about the country")))
kept_colours = sorted({c for c in re.findall(r"(?:color|background-color):(#[0-9a-fA-F]{6})", edge_html)})
print(f"  info  colours kept from the Edge capture: {kept_colours}")

print("\n--- [C2b-1] the Word capture (lists, colours, table, image; real sample) ---")
before_files = photo_files()
word = bare()
paste(word, fixture("word-lists"))
word_html = word.save()[0]
fp = stored_fp(word_html)
texts = fp.block_texts()
bullets = [b for b in fp.blocks if b.text in ("First bullet", "Second bullet")]
rule("Word bullets become a list", "both bullets are disc list items",
     len(bullets) == 2 and all(b.list_style == "disc" for b in bullets), bullets)
steps = [b for b in fp.blocks if b.text in ("First step", "Second step")]
rule("Word numbers become a numbered list", "both steps are decimal list items",
     len(steps) == 2 and all(b.list_style == "decimal" for b in steps), steps)
sub_index = next((b.index for b in fp.blocks if b.text == "Sub-item"), None)
sub = word.text_edit.document().findBlockByNumber(sub_index) if sub_index is not None else None
first = word.text_edit.document().findBlockByNumber(bullets[0].index) if bullets else None
rule("Word list levels", "the sub-item (level2) is one list level deeper than the bullets",
     sub is not None and sub.textList() is not None and first is not None and first.textList() is not None
     and sub.textList().format().indent() == first.textList().format().indent() + 1,
     (sub.textList().format().indent() if sub is not None and sub.textList() else None))
rule("Word markers removed", "no marker text (bullet glyph, 'o', '1.') is left in the items",
     all(t in texts for t in ("First bullet", "Second bullet", "Sub-item", "First step", "Second step"))
     and "\ufffd" not in word_html and "\uf0b7" not in word_html, texts)
rule("chromatic text colour kept", "'red' stays #ee0000", any(r.fg == "#ee0000" for r in runs_with(fp, "red")))
rule("chromatic highlight kept", "the yellow highlight stays",
     any(r.bg == "#ffff00" for r in runs_with(fp, "highlighted word")))
rule("table flattened", "the 2×2 table is two paragraphs 'A\\tB', 'C\\tD'",
     "A\tB" in texts and "C\tD" in texts and "<table" not in word_html, texts)
images = [r.image for r in fp.runs if r.image]
new_files = photo_files() - before_files
rule("data image decoded", "Word's data: image became a file in attachments/2026/10, referenced relatively",
     len(images) == 1 and re.fullmatch(r"2026/10/[0-9a-f]{32}\.jpg", images[0] or "")
     and (ATTACHMENTS / images[0]).is_file() and not QImage(str(ATTACHMENTS / images[0])).isNull()
     and len(new_files) == 1, (images, new_files))
rule("image size kept", "its width and height (625×416) are kept", 'width="625" height="416"' in word_html)
rule("empty-paragraph paste keeps own formats",
     "pasted into an empty entry, Word's first paragraph stays Heading 1 at 20 pt, bold (AM-19)",
     any(b.heading == 1 for b in blocks_with(fp, "Sample heading"))
     and any(r.size == 20 and r.weight >= 600 for r in runs_with(fp, "Sample heading")))
spacing = {b.text: (b.top, b.bottom) for b in fp.blocks}
rule("Word spacing in px", "Word's pt/inch spacing as whole px: Heading 1 24/5, Normal 0/11, list items 0/0 "
     "and 0/11 after a list's last item (AM-22)",
     spacing.get("Sample heading") == (24, 5) and spacing.get("This is a red word, a highlighted word, and a link.")
     == (0, 11) and spacing.get("First bullet") == (0, 0) and spacing.get("Sub-item") == (0, 11)
     and spacing.get("First step") == (0, 0) and spacing.get("Second step") == (0, 11), spacing)
rule("Word spacing in px", "no paragraph of the Word paste has Qt's default 12 px",
     not any(12 in (b.top, b.bottom) for b in fp.blocks), [b.text for b in fp.blocks if 12 in (b.top, b.bottom)])
check("the Edge page's near-black heading colour #101418 is removed (AM-23)", "#101418" not in edge_html.lower())

print("\n--- [C2b-1] synthetic cases ---")
syn = bare()
local_url = QUrl.fromLocalFile(str(SOURCE_PHOTO)).toString()
SYNTHETIC = (
    '<p>lead</p>'
    '<h1>Hone</h1><h2>Htwo</h2><h3>Hthree</h3><h4>Hfour</h4><h5>Hfive</h5><h6>Hsix</h6>'
    '<p align="center">centred</p><p style="text-align:right">righted</p>'
    '<p><u>underlined</u> <s>struck</s> x<sub>sub</sub> y<sup>sup</sup> '
    '<span style="background-color:#eeeeee">greyhl</span> <span style="background-color:#ffd54f">amberhl</span></p>'
    '<p style="margin-top:7.6px; margin-bottom:3.2px">spaced</p>'
    '<p style="line-height:150%">tall lines</p>'
    '<p><a href="mailto:a@example.org">mailme</a> <a href="journal://date/2026-01-02">datelink</a> '
    '<a href="javascript:alert(1)">jslink</a> <a href="ftp://example.org/f">ftplink</a></p>'
    '<table><tr><td>a1</td><td><table><tr><td>n1</td><td>n2</td></tr></table></td></tr>'
    '<tr><td>b1</td><td style="background-color:#ffd54f">b2</td></tr></table>'
    f'<p>local <img src="{local_url}" width="45" height="30"> data <img src="{png_data_url()}"> '
    'remote <img src="https://example.org/pic.png" alt="a pic"> '
    'missing <img src="file:///C:/nowhere/missing-photo.png"></p>'
    '<p>before rule</p><hr><form><input value="typed"><label>labeltext</label>'
    '<select><option>optiontext</option></select></form><script>alert("scripttext")</script>'
    '<p>after rule</p><p>tail</p>')
before_files = photo_files()
paste(syn, mime_html(SYNTHETIC))
syn_html = syn.save()[0]
fp = stored_fp(syn_html)
heading = {t: next((b.heading for b in fp.blocks if b.text == t), None)
           for t in ("Hone", "Htwo", "Hthree", "Hfour", "Hfive", "Hsix")}
rule("h1-h3 to Heading 1-3", "h1, h2, h3 are Heading 1, 2, 3", [heading[t] for t in ("Hone", "Htwo", "Hthree")] == [1, 2, 3],
     heading)
rule("h4-h6 to Heading 3", "h4, h5, h6 are Heading 3", [heading[t] for t in ("Hfour", "Hfive", "Hsix")] == [3, 3, 3],
     heading)
sizes = {t: next((r.size for r in fp.runs if r.text == t), None) for t in heading}
rule("heading sizes are Jortle's", "heading text at 20/16/13 pt, bold, with no Qt size bump",
     [sizes[t] for t in ("Hone", "Htwo", "Hthree", "Hfour")] == [20, 16, 13, 13]
     and all(r.weight >= 600 for t in heading for r in runs_with(fp, t)) and "-qt-fontsize-adjustment" not in syn_html,
     sizes)
align = {b.text: b.align for b in fp.blocks}
rule("alignment", "centre and right alignment kept", align.get("centred") == "center" and align.get("righted") == "right",
     align)
rule("underline", "<u> kept", any(r.underline for r in runs_with(fp, "underlined")))
rule("strikethrough", "<s> kept", any(r.strike for r in runs_with(fp, "struck")))
rule("subscript", "<sub> kept", any(r.valign == "sub" for r in runs_with(fp, "sub")))
rule("grey highlight removed", "a grey highlight (#eeeeee) removed", all(r.bg is None for r in runs_with(fp, "greyhl")))
rule("chromatic highlight kept", "an amber highlight kept", any(r.bg == "#ffd54f" for r in runs_with(fp, "amberhl")))
spaced = blocks_with(fp, "spaced")
rule("spacing whole px", "7.6 px before → 8, 3.2 px after → 3", spaced and (spaced[0].top, spaced[0].bottom) == (8, 3),
     spaced[:1])
tall = blocks_with(fp, "tall lines")
rule("line height kept", "line-height 150% kept", tall and tall[0].line_height == 150, tall[:1])
links = {t: [r.href for r in runs_with(fp, t)] for t in ("mailme", "datelink", "jslink", "ftplink")}
rule("mailto and journal links kept", "mailto: and journal:// stay links",
     links["mailme"] == ["mailto:a@example.org"] and links["datelink"] == ["journal://date/2026-01-02"], links)
rule("other scheme becomes text", "javascript: and ftp: links are plain text",
     links["jslink"] == [""] and links["ftplink"] == [""] and "javascript" not in syn_html, links)
texts = fp.block_texts()
rule("nested table flattened", "rows 'a1\\tn1\\tn2' and 'b1\\tb2'", "a1\tn1\tn2" in texts and "b1\tb2" in texts, texts)
rule("table cell background removed", "b2's cell background is not stored", all(r.bg is None for r in runs_with(fp, "b2"))
     and "#ffd54f" not in "".join(line for line in content_lines(syn_html) if "b2" in line))
images = [r.image for r in fp.runs if r.image]
new_files = sorted(photo_files() - before_files)
rule("local image copied", "the local file is copied into attachments/2026/10 under a new name, same pixels",
     len(images) == 2 and all(re.fullmatch(r"2026/10/[0-9a-f]{32}\.png", i) for i in images)
     and QImage(str(ATTACHMENTS / images[0])).pixelColor(2, 2) == QColor("#2e86c1"), images)
rule("data image decoded", "the data: PNG became a file with its pixels",
     len(images) == 2 and QImage(str(ATTACHMENTS / images[1])).pixelColor(1, 1) == QColor("#e01b24"), images)
rule("image size kept", "width/height 45×30 kept", 'width="45" height="30"' in syn_html)
rule("remote image becomes a link", "'[image: a pic]' linked to https://example.org/pic.png",
     [r.href for r in runs_with(fp, "[image: a pic]")] == ["https://example.org/pic.png"])
missing = [r for r in fp.runs if "[image]" in r.text]
rule("missing local image becomes text", "a missing local file is the plain text '[image]'",
     missing and all(r.href == "" for r in missing) and len(new_files) == 2, (missing, new_files))
rule("forms, rules, scripts removed with text kept", "label and option text kept; script, hr, inputs gone",
     "labeltext" in syn.plain_text() and "optiontext" in syn.plain_text() and "scripttext" not in syn.plain_text()
     and "<hr" not in syn_html and "typed" not in syn.plain_text(), syn.plain_text()[-80:])

print("\n--- [C2b-1] a dark-mode page ---")
dark = bare()
paste(dark, mime_html('<body style="background:#202124; color:#e8eaed"><p>lead</p>'
                      '<p style="background-color:#303134">dark page text <span style="color:#8ab4f8">blue words</span> '
                      '<span style="color:#ffffff">white words</span></p><p>tail</p></body>'))
dark_html = dark.save()[0]
fp = stored_fp(dark_html)
rule("grey text colour removed", "the page's light grey and white text keep no colour",
     all(r.fg is None for t in ("dark page text", "white words") for r in runs_with(fp, t)), fp.runs[:4])
rule("chromatic text colour kept", "#8ab4f8 is kept", any(r.fg == "#8ab4f8" for r in runs_with(fp, "blue words")))
rule("paragraph background removed", "the dark paragraph background is not stored",
     "#303134" not in dark_html and "#202124" not in dark_html)

print("\n--- [C2b-1] a Jortle source (the editor's own Copy, offscreen clipboard) ---")
source = bare(family="Georgia", size=15)
src_edit = source.text_edit
src_edit.setFocus()
QTest.keyClicks(src_edit, "Jortle heading")
source._apply_heading(1)
QTest.keyClick(src_edit, Qt.Key_Return)
QTest.keyClicks(src_edit, "plain grey link")
cursor = QTextCursor(src_edit.document().findBlockByNumber(1))
cursor.movePosition(QTextCursor.NextWord, QTextCursor.KeepAnchor)
fmt = cursor.charFormat()
fmt.setForeground(QColor("#555555"))
fmt.setFontWeight(700)
cursor.mergeCharFormat(fmt)
cursor.clearSelection()
cursor.movePosition(QTextCursor.EndOfBlock)
cursor.movePosition(QTextCursor.StartOfWord, QTextCursor.KeepAnchor)
src_edit.setTextCursor(cursor)
from PySide6.QtWidgets import QInputDialog  # noqa: E402
_orig = QInputDialog.getText
QInputDialog.getText = staticmethod(lambda *a, **k: ("https://example.org/j", True))
src_edit.insert_link()
QInputDialog.getText = _orig
src_edit.moveCursor(QTextCursor.End)
QTest.keyClick(src_edit, Qt.Key_Return)
_orig_files = QFileDialog.getOpenFileNames
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(SOURCE_PHOTO)], ""))
src_edit.insert_photo()
QFileDialog.getOpenFileNames = _orig_files
settle()
source_photo = next(r.image for r in sd.fingerprint(src_edit.document()).runs if r.image)
src_edit.setFocus()
QTest.keyClick(src_edit, Qt.Key_A, Qt.ControlModifier)
QTest.keyClick(src_edit, Qt.Key_C, Qt.ControlModifier)
settle()
clip_html = QApplication.clipboard().mimeData().html()
check("the editor's own copy carries Qt's qrichtext marker (AM-5)", rich_paste.is_jortle_html(clip_html))
target = bare(family="Arial", size=11)
target.text_edit.setFocus()
QTest.keyClick(target.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
target_html = target.save()[0]
fp = stored_fp(target_html)
rule("Jortle source keeps fonts", "pasted text keeps Georgia 15 as it looked in the source",
     any("Georgia" in r.families and r.size == 15 for r in runs_with(fp, "link")), runs_with(fp, "link")[:1])
rule("Jortle source keeps colours and links", "grey #555555 (a Jortle colour) and the link are kept",
     any(r.fg == "#555555" and r.weight >= 600 for r in runs_with(fp, "plain"))
     and any(r.href == "https://example.org/j" for r in runs_with(fp, "link")))
rule("Jortle source keeps image paths", "the photo keeps its attachment path",
     any(r.image == source_photo for r in fp.runs), (source_photo, [r.image for r in fp.runs if r.image]))
rule("Qt heading bump removed (Jortle source)", "the pasted heading shows at its stored 20 pt, no bump",
     "-qt-fontsize-adjustment" not in target_html
     and any(r.size == 20 for r in runs_with(fp, "Jortle heading")))

print("\n--- [C2b-1] source order (4C2b-D2) ---")
order = bare()
both = mime_html("<p><b>htmlwins</b></p>", "textloses")
image = QImage(8, 6, QImage.Format_RGB32)
image.fill(QColor("#00aa00"))
both.setImageData(image)
paste(order, both)
order_after_html = order.plain_text()
shot = bare()
before_files = photo_files()
screenshot = QMimeData()
screenshot.setImageData(image)
paste(shot, screenshot)
shot_html = shot.save()[0]
new_files = sorted(photo_files() - before_files)
text_only = bare()
text_mime = QMimeData()
text_mime.setText("just text")
paste(text_only, text_mime)
rule("source order", "HTML first (text and image ignored), then an image alone, then plain text",
     "htmlwins" in order_after_html and "textloses" not in order_after_html
     and text_only.plain_text() == "just text" and len(new_files) == 1)
rule("clipboard image saved as PNG", "a screenshot is a PNG file in attachments, inserted at the caret",
     len(new_files) == 1 and new_files[0].suffix == ".png" and QImage(str(new_files[0])).pixelColor(1, 1) == QColor("#00aa00")
     and f'src="2026/10/{new_files[0].name}"' in shot_html, (new_files, shot_html[-200:]))

# ============================================================ AM-19…AM-23
print("\n--- [AM-19] pasting into an empty paragraph keeps the pasted ends' own formats ---")


def fp_live(editor):
    return sd.fingerprint(editor.text_edit.document())


def paste_into_empty_middle(html):
    editor = bare()
    editor.load('<p>above</p><p style="-qt-paragraph-type:empty;"><br /></p><p>below</p>')
    assert editor.text_edit.document().findBlockByNumber(1).length() == 1, "no empty paragraph"
    cursor = editor.text_edit.textCursor()
    cursor.setPosition(len("above") + 1)
    editor.text_edit.setTextCursor(cursor)
    paste(editor, mime_html(html))
    return stored_fp(editor.save()[0])


fp = paste_into_empty_middle("<h2>first head</h2><p>middle text</p><ul><li>last item</li></ul>")
rule("empty-paragraph paste keeps own formats", "first pasted paragraph stays Heading 2, last stays a bullet item",
     fp.block_texts() == ["above", "first head", "middle text", "last item", "below"]
     and fp.blocks[1].heading == 2 and fp.blocks[3].list_style == "disc" and fp.blocks[4].list_style is None,
     fp.blocks)
fp = paste_into_empty_middle('<ol><li>one</li><li>two</li></ol><p align="center">centred end</p>')
rule("empty-paragraph paste keeps own formats", "a paste starting with a list item: no extra empty paragraph, "
     "both items in one numbered list, the last paragraph keeps its alignment",
     fp.block_texts() == ["above", "one", "two", "centred end", "below"]
     and fp.blocks[1].list_style == fp.blocks[2].list_style == "decimal" and fp.blocks[3].align == "center",
     [(b.text, b.list_style, b.align) for b in fp.blocks])
nonempty = bare()
nonempty.load("<p>alpha omega</p>")
cursor = nonempty.text_edit.textCursor()
cursor.setPosition(len("alpha "))
nonempty.text_edit.setTextCursor(cursor)
paste(nonempty, mime_html("<h2>head start</h2><p>mid</p><ul><li>list end</li></ul>"))
fp = stored_fp(nonempty.save()[0])
check("into a non-empty paragraph the merge rule stays (both ends take the paragraph's format)",
      fp.block_texts() == ["alpha head start", "mid", "list endomega"]
      and fp.blocks[0].heading == 0 and fp.blocks[2].list_style is None, fp.blocks)

print("\n--- [AM-20] a paste in the same font stores no font span ---")
same = bare(family="Georgia", size=15)
same.load("")
same.text_edit.setFocus()
QTest.keyClicks(same.text_edit, "copy these words here")
cursor = same.text_edit.textCursor()
cursor.setPosition(len("copy "))
cursor.setPosition(len("copy these words"), QTextCursor.KeepAnchor)
same.text_edit.setTextCursor(cursor)
QTest.keyClick(same.text_edit, Qt.Key_C, Qt.ControlModifier)
same.text_edit.moveCursor(QTextCursor.End)
QTest.keyClick(same.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
same_html = same.save()[0]
rule("same font stores no span", "copy and paste within an entry: no font-family or font-size span",
     same.plain_text() == "copy these words herethese words"
     and not any("font-family" in line or "font-size" in line for line in content_lines(same_html)),
     content_lines(same_html))
other_doc = bare(family="Georgia", size=15)
other_doc.load("")
other_doc.text_edit.setFocus()
QTest.keyClick(other_doc.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
rule("same font stores no span", "between two documents in the same font (Georgia 15): no font span",
     other_doc.plain_text() == "these words"
     and not any("font-family" in line or "font-size" in line for line in content_lines(other_doc.save()[0])))
# Qt's HTML writer leaves out a font equal to the document's own, so the
# stored bytes alone cannot tell whether the paste wrote one: the live
# document's pasted text must carry no font of its own either (AM-20).
from PySide6.QtGui import QTextFormat  # noqa: E402


def runs_with_own_font(document):
    found = []
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.hasProperty(QTextFormat.FontFamilies) or fmt.hasProperty(QTextFormat.FontPointSize):
                found.append(it.fragment().text())
            it += 1
        block = block.next()
    return found


own_font = runs_with_own_font(other_doc.text_edit.document()) + runs_with_own_font(same.text_edit.document())
rule("same font stores no span", "…and the pasted text in the live document carries no font of its own",
     not own_font, own_font)
differ = bare(family="Arial", size=11)
differ.load("")
differ.text_edit.setFocus()
QTest.keyClick(differ.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
check("…while into another font (Arial 11) the text keeps Georgia 15 (D3)",
      "Georgia" in differ.save()[0] and "font-size:15pt" in differ.save()[0])

print("\n--- [AM-21] spaces: never dropped from a Jortle source; kept at a paste's end ---")
spaces = bare(family="Georgia", size=15)
spaces.load("")
spaces.text_edit.setFocus()
QTest.keyClicks(spaces.text_edit, "one  ")
QTest.keyClick(spaces.text_edit, Qt.Key_Return)
QTest.keyClicks(spaces.text_edit, "say hello there")
settle()
cursor = spaces.text_edit.textCursor()
cursor.setPosition(0)
cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
spaces.text_edit.setTextCursor(cursor)
QTest.keyClick(spaces.text_edit, Qt.Key_C, Qt.ControlModifier)
target = bare(family="Georgia", size=15)
target.load("<p>start end</p>")
cursor = target.text_edit.textCursor()
cursor.setPosition(len("start "))
target.text_edit.setTextCursor(cursor)
target.text_edit.setFocus()
QTest.keyClick(target.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
rule("Jortle spaces kept", "a Jortle paragraph's trailing spaces are pasted as they are",
     target.plain_text() == "start one  \nsay hello thereend", repr(target.plain_text()))
cursor = spaces.text_edit.textCursor()
cursor.setPosition(len("one  \nsay "))
cursor.setPosition(len("one  \nsay hello "), QTextCursor.KeepAnchor)
spaces.text_edit.setTextCursor(cursor)
QTest.keyClick(spaces.text_edit, Qt.Key_C, Qt.ControlModifier)
target.load("<p>startend</p>")
cursor = target.text_edit.textCursor()
cursor.setPosition(len("start"))
target.text_edit.setTextCursor(cursor)
target.text_edit.setFocus()
QTest.keyClick(target.text_edit, Qt.Key_V, Qt.ControlModifier)
settle()
rule("Jortle spaces kept", "'hello ' copied from the editor and pasted mid-paragraph keeps its space",
     target.plain_text() == "starthello end", repr(target.plain_text()))
web = bare()
web.load("<p>startend</p>")
cursor = web.text_edit.textCursor()
cursor.setPosition(len("start"))
web.text_edit.setTextCursor(cursor)
paste(web, mime_html('<html><body><!--StartFragment--><span style="color:#202124">hello </span>'
                     '<!--EndFragment--></body></html>'))
rule("trailing space kept at the end", "'hello ' from a web page pasted mid-paragraph keeps its space",
     web.plain_text() == "starthello end", repr(web.plain_text()))
web.load("<p>startend</p>")
cursor = web.text_edit.textCursor()
cursor.setPosition(len("start"))
web.text_edit.setTextCursor(cursor)
paste(web, mime_html("<p>first line   </p><p>last words </p>"))
rule("trailing space kept at the end", "a web paste: spaces at the end of an inner paragraph dropped, "
     "the last paragraph's kept", web.plain_text() == "startfirst line\nlast words end", repr(web.plain_text()))

print("\n--- [AM-23] nearly black and nearly white colours removed, whatever the hue ---")
tones = bare()
paste(tones, mime_html('<p>lead</p><p><span style="color:#101418">nearblack</span> '
                       '<span style="color:#006400">darkgreen</span> <span style="color:#8ab4f8">lightblue</span> '
                       '<span style="color:#fff8e1">nearwhite</span> '
                       '<span style="background-color:#fff2cc">pastelhl</span> '
                       '<span style="background-color:#1a0033">darkhl</span></p><p>tail</p>'))
fp = stored_fp(tones.save()[0])
colour_of = {t: [(r.fg, r.bg) for r in runs_with(fp, t)] for t in
             ("nearblack", "darkgreen", "lightblue", "nearwhite", "pastelhl", "darkhl")}
rule("nearly black or white colour removed", "#101418 and #fff8e1 text, #fff2cc and #1a0033 highlights removed",
     all(pair == (None, None) for t in ("nearblack", "nearwhite", "pastelhl", "darkhl") for pair in colour_of[t]),
     colour_of)
rule("chromatic text colour kept", "#006400 and #8ab4f8 kept (AM-23)",
     colour_of["darkgreen"] == [("#006400", None)] and colour_of["lightblue"] == [("#8ab4f8", None)], colour_of)

# ============================================================ windows and helpers
win = MainWindow()
win.resize(1600, 1000)
win.show()
win.activateWindow()
settle(300)
D, OTHER, EMPTY = "2026-02-03", "2026-02-20", "2026-03-04"
PROJECT_ID = win.db.create_project("Paste project").id
OTHER_PROJECT = win.db.create_project("Other project").id
win.projects_widget.refresh_project_list(select_id=PROJECT_ID)
settle()


def plain(text):
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def click_menu(w, title, action):
    w.activateWindow()
    settle()
    bar = w.menuBar()
    top = next(a for a in bar.actions() if plain(a.text()) == title)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.actionGeometry(top).center())
    settle(50)
    menu = top.menu()
    if not menu.isVisible():
        check(f"(the {title} menu opened for the click)", False)
        return
    QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
    settle(50)


def ctrl_s(w, widget):
    w.activateWindow()
    settle()
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key_S, Qt.ControlModifier)
    settle()


def show_daily(w, date=D):
    w.main_tabs.setCurrentWidget(w.daily_splitter)
    w._request_date(date)
    settle()


def show_project(w, pid=PROJECT_ID):
    w.main_tabs.setCurrentWidget(w.projects_widget)
    if w.projects_widget.current_project_id != pid:
        w.projects_widget.refresh_project_list(select_id=pid)
    settle()


def stored_row(w, kind):
    q = {"journal": ("SELECT body_md FROM entries WHERE date=?", (D,)),
         "date notes": ("SELECT content FROM reader_notes_scoped WHERE scope='date' AND ref=?", (D,)),
         "project": ("SELECT content_md FROM projects WHERE id=?", (PROJECT_ID,)),
         "project notes": ("SELECT content FROM reader_notes_scoped WHERE scope='project' AND ref=?",
                           (str(PROJECT_ID),))}[kind]
    row = w.db._conn.execute(*q).fetchone()
    return row[0] if row else None


# (name, editor, show it, leave it)
WORKSPACES = (
    ("journal", lambda w: w.editor, show_daily, lambda w: show_daily(w, OTHER)),
    ("date notes", lambda w: w.reader_notes.editor, show_daily, lambda w: show_daily(w, OTHER)),
    ("project", lambda w: w.projects_widget.editor, show_project, lambda w: show_project(w, OTHER_PROJECT)),
    ("project notes", lambda w: w.projects_widget.reader_notes.editor, show_project,
     lambda w: show_project(w, OTHER_PROJECT)),
)


def apply_settings(w, **changes):
    dlg = SettingsDialog(w.db, on_change=w._apply_settings, parent=w, set_autosave=w.set_autosave,
                         open_backups=lambda: None, set_work_hours=w.calendar_prefs.set_work_hours_enabled,
                         page="appearance")
    dlg.show()
    settle()
    if "theme" in changes:
        dlg.scheme_combo.setCurrentText(changes["theme"])
    if "ui" in changes:
        dlg.ui_size_spin.setValue(changes["ui"])
    settle(50)
    dlg.close()
    settle()


# ============================================================ C2b-2
print("\n--- [C2b-2] pasting into the middle of a stress document, all four editors (4C1a/AM-7) ---")
builder = RichEditor(link_dates=True)
builder.resize(900, 700)
builder.show()
settle()
sd.build(builder, sd.context("journal", sd.make_photo(ROOT / "stress-photo")), settle)
STRESS = builder.save()[0]
builder.close()
# The Jortle source for the second paste: a different font from the stress
# document's own.
jsource = bare(family="Courier New", size=17)
jsource.load('<p>jtext <b>jbold</b> jend</p>')
jsource.text_edit.selectAll()
jsource.text_edit.copy()


def split_paragraph(line):
    match = re.match(r"(<p[^>]*>)(.*)</p>$", line)
    return match.group(1), match.group(2)


def paste_middle(w, editor, paragraph, mime=None):
    """Caret after 'alpha ' in `paragraph`: an external mime through the
    paste path, or the Jortle clipboard by Ctrl+V."""
    edit = editor.text_edit
    block = edit.document().begin()
    while block.isValid() and block.text() != paragraph:
        block = block.next()
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + len("alpha "))
    edit.setTextCursor(cursor)
    edit.setFocus()
    if mime is not None:
        edit.insertFromMimeData(mime)
    else:
        QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier)
    settle()


def compare_around(label, before, after, paragraphs, external_ranges):
    """Every paragraph outside the pastes byte-identical; each pasted-into
    paragraph's opening tag, own head and own tail byte-identical; no font
    family or size on the externally pasted lines (AM-6)."""
    b, a = content_lines(before), content_lines(after)
    idx = [next(i for i, line in enumerate(b) if re.sub(r"<[^>]+>", "", line) == p) for p in paragraphs]
    ok, detail = True, ""
    ai = 0
    prev = 0
    for k, i in enumerate(idx):
        if a[ai:ai + (i - prev)] != b[prev:i]:
            return False, f"paragraphs before '{paragraphs[k]}' differ"
        ai += i - prev
        opening, own = split_paragraph(b[i])
        head, tail = own[:len("alpha ")], own[len("alpha "):]
        if not a[ai].startswith(opening + head):
            return False, f"pasted-into head of '{paragraphs[k]}': {a[ai][:160]}"
        start = ai
        while not a[ai].endswith(tail + "</p>"):
            ai += 1
            if ai >= len(a):
                return False, f"tail of '{paragraphs[k]}' not found"
        if not a[ai].startswith(opening):
            return False, f"pasted-into tail of '{paragraphs[k]}' lost its format: {a[ai][:160]}"
        if k in external_ranges:
            pasted = "\n".join(a[start:ai + 1])
            sizes = set(re.findall(r"font-size:([\d.]+)pt", pasted))
            if "font-family" in pasted or not sizes <= {"20", "16", "13"}:
                return False, f"external paste stored a font: family {'font-family' in pasted}, sizes {sizes}"
        ai += 1
        prev = i + 1
    if a[ai:] != b[prev:]:
        return False, "paragraphs after the pastes differ"
    if before.split("<body")[1].split(">")[0] != after.split("<body")[1].split(">")[0]:
        return False, "<body> changed"
    return ok, detail


# Document order: a real Word paste, a web-like paste that names its own font
# (AM-6: neither stores it), and a Jortle paste from the editor's own Copy.
PARAS = ("alpha italicword omega", "alpha underlineword omega", "alpha strikeword omega")
FONTED_HTML = ('<p style="font-family:Arial; font-size:20px">fonted <span style="font-family:Impact; '
               'font-size:9pt">small</span></p><h2 style="font-family:Georgia">fonted heading</h2>'
               '<p style="font-family:Arial">fonted tail</p>')
results = {}
for name, get, show, leave in WORKSPACES:
    show(win)
    editor = get(win)
    editor.load(STRESS)
    editor.text_edit.document().setModified(True)
    ctrl_s(win, editor.text_edit)
    leave(win)
    show(win)
    before = stored_row(win, name)
    check(f"{name}: the stress document is stored", before is not None and "italicword" in (before or ""))
    paste_middle(win, editor, PARAS[0], fixture("word-lists"))
    paste_middle(win, editor, PARAS[1], mime_html(FONTED_HTML))
    jsource.text_edit.selectAll()
    jsource.text_edit.copy()          # the editor's own Copy (AM-5)
    paste_middle(win, editor, PARAS[2])
    ctrl_s(win, editor.text_edit)
    after = stored_row(win, name)
    ok, detail = compare_around(name, before, after, PARAS, {0, 1})
    check(f"{name}: outside the pastes and the pasted-into paragraphs' own text and format: byte-identical; "
          f"<body> unchanged; no external font", ok, detail)
    check(f"{name}: the Jortle paste kept its own font (Courier New 17)",
          "Courier New" in after and "font-size:17pt" in after)
    stable = True
    for _ in range(3):
        leave(win)
        show(win)
        stable &= editor.save()[0] == after
        editor.text_edit.document().setModified(True)
        ctrl_s(win, editor.text_edit)
        stable &= stored_row(win, name) == after
    check(f"{name}: save/reload ×3 stores the same bytes", stable)
    results[name] = after
# A restart.
win.close()
settle()
win2 = MainWindow()
win2.resize(1600, 1000)
win2.show()
settle(300)
for name, get, show, leave in WORKSPACES:
    show(win2)
    check(f"{name}: after a restart the editor gives back the stored bytes",
          get(win2).save()[0] == results[name] == stored_row(win2, name))
win2.close()
settle()
win = MainWindow()
win.resize(1600, 1000)
win.show()
win.activateWindow()
settle(300)

# ============================================================ C2b-3
print("\n--- [C2b-3] zoom, one undo step, redo ---")
BASE = "<p>alpha middle omega</p><p>second paragraph</p>"


def pasted_at(zoom_steps):
    editor = bare()
    editor.load(BASE)
    editor.text_edit.set_zoom_steps(zoom_steps)
    cursor = editor.text_edit.textCursor()
    cursor.setPosition(len("alpha "))
    editor.text_edit.setTextCursor(cursor)
    paste(editor, fixture("word-lists"))
    return without_photo_names(editor.save()[0])


check("a paste at 130% stores the same bytes as at 100% (photo names aside)", pasted_at(3) == pasted_at(0))
show_daily(win, "2026-04-01")
edit = win.editor.text_edit
for route in ("Ctrl+Z", "Edit → Undo"):
    win.editor.load(BASE)
    cursor = edit.textCursor()
    cursor.setPosition(len("alpha "))
    edit.setTextCursor(cursor)
    edit.setFocus()
    html_before = edit.toHtml()
    edit.insertFromMimeData(fixture("word-lists"))
    settle()
    html_after = edit.toHtml()
    if route == "Ctrl+Z":
        QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
    else:
        win._refresh_command_states()
        click_menu(win, "Edit", win.command_actions["undo"])
    settle()
    check(f"one {route} removes the whole paste (toHtml identical to before)", edit.toHtml() == html_before)
    edit.setFocus()
    QTest.keySequence(edit, commands.key_sequences("redo")[0])
    settle()
    check(f"Redo after {route} restores the whole paste", edit.toHtml() == html_after)
win.editor.load("")
win.editor.text_edit.document().setModified(False)

# ============================================================ C2b-4
print("\n--- [C2b-4] images: files in the photo folder, nothing fetched, Find Unused Photos ---")
show_daily(win, "2026-05-06")
win.editor.load("")
before_files = photo_files()
paste(win.editor, mime_html(f'<p>pic <img src="{png_data_url("#123456")}"> and <img src="https://example.org/r.png"></p>'))
ctrl_s(win, win.editor.text_edit)
pasted_file = sorted(photo_files() - before_files)
stored_entry = win.db.get_entry("2026-05-06").body_md
check("the pasted image is a file under attachments/2026/05, referenced relatively",
      len(pasted_file) == 1 and pasted_file[0].parent == ATTACHMENTS / "2026" / "05"
      and f'src="2026/05/{pasted_file[0].name}"' in stored_entry, (pasted_file, stored_entry[-300:]))
win.editor.text_edit.viewport().grab()
settle()
check("the pasted image displays (the editor loads it from the photo folder)",
      any(name.endswith(pasted_file[0].name) for name in requested_resources) if pasted_file else False)
check("no image resource with a network scheme was ever requested",
      not any(re.match(r"^(https?|ftp):", n) for n in requested_resources),
      [n for n in requested_resources if "://" in n][:3])
# A remote image on a server of this test's own, on 127.0.0.1: pasted,
# shown, laid out at a zoom and saved, it is never requested. (Qt's image
# plugins load its network library whenever any image is decoded — Insert
# Photo too — so a loaded library is not evidence of a fetch; a request is.)
import http.server  # noqa: E402
import threading  # noqa: E402

served = []


class _Recorder(http.server.BaseHTTPRequestHandler):
    def do_GET(self):   # noqa: N802
        served.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_a):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
threading.Thread(target=server.serve_forever, daemon=True).start()
remote = f"http://127.0.0.1:{server.server_address[1]}/remote.png"
show_daily(win, "2026-05-07")
win.editor.load("")
paste(win.editor, mime_html(f'<p>lead</p><p>remote <img src="{remote}" alt="local server"> '
                            f'<a href="{remote}">a link to it</a></p><p>tail</p>'))
win.editor.text_edit.set_zoom_steps(3)
win.editor.text_edit.viewport().grab()
ctrl_s(win, win.editor.text_edit)
win.editor.text_edit.reset_zoom()
show_daily(win, "2026-05-08")
show_daily(win, "2026-05-07")
win.editor.text_edit.viewport().grab()
settle(300)
server.shutdown()
check("a remote image is never fetched: pasted, shown, zoomed, saved and reloaded, the server got no request",
      served == [] and f'href="{remote}"' in win.db.get_entry("2026-05-07").body_md
      and "<img" not in win.db.get_entry("2026-05-07").body_md, served)
check("PySide6.QtNetwork is never imported", "PySide6.QtNetwork" not in sys.modules)
unused = {p.resolve() for p in find_unused_photos(win.db)}
check("Find Unused Photos keeps a pasted photo that an entry uses", pasted_file and pasted_file[0].resolve() not in unused)
before_files = photo_files()
paste(win.editor, mime_html(f'<p>undone <img src="{png_data_url("#654321")}"></p>'))
QTest.keyClick(win.editor.text_edit, Qt.Key_Z, Qt.ControlModifier)
ctrl_s(win, win.editor.text_edit)
orphan = sorted(photo_files() - before_files)
unused = {p.resolve() for p in find_unused_photos(win.db)}
check("…and lists one whose paste was undone, as for an inserted photo", len(orphan) == 1 and orphan[0].resolve() in unused)

# ============================================================ C2b-5
print("\n--- [C2b-5] colours in Light and Dark (FP-11) ---")
COLOUR_HTML = ('<p>lead</p><p style="font-size:30px"><span style="color:#202124">GREYGREYGREY</span></p>'
               '<p><span style="color:#c00000">redtext</span> <span style="background-color:#ffff00">yellowhl</span></p>'
               '<p>tail</p>')
stored_by_theme = {}
ink = {}
for theme in ("Light", "Dark"):
    apply_settings(win, theme=theme)
    show_daily(win, f"2026-06-0{1 if theme == 'Light' else 2}")
    win.editor.load("")
    paste(win.editor, mime_html(COLOUR_HTML))
    stored_by_theme[theme] = win.editor.save()[0]
    settle(50)
    edit = win.editor.text_edit
    grab = edit.viewport().grab().toImage()
    GRAB_DIR.mkdir(parents=True, exist_ok=True)
    grab.save(str(GRAB_DIR / f"c2b5-grey-text-{theme}.png"))
    block = edit.document().findBlockByNumber(1)
    rect = edit.document().documentLayout().blockBoundingRect(block).toRect()
    rect.translate(0, -edit.verticalScrollBar().value())
    background = grab.pixelColor(2, 2)
    farthest, far_colour = -1, background
    for x in range(max(0, rect.left()), min(grab.width(), rect.right()), 2):
        for y in range(max(0, rect.top()), min(grab.height(), rect.bottom())):
            c = grab.pixelColor(x, y)
            d = abs(c.red() - background.red()) + abs(c.green() - background.green()) + abs(c.blue() - background.blue())
            if d > farthest:
                farthest, far_colour = d, c
    text_colour = edit.palette().color(QPalette.Text)
    distance = (abs(far_colour.red() - text_colour.red()) + abs(far_colour.green() - text_colour.green())
                + abs(far_colour.blue() - text_colour.blue()))
    ink[theme] = far_colour.name()
    check(f"{theme}: pasted greyscale text is drawn in the theme's text colour ({text_colour.name()})",
          distance < 90, f"ink {far_colour.name()}")
check("Light and Dark draw that text differently (it follows the theme)", ink["Light"] != ink["Dark"], ink)
check("pasting in Light and in Dark stores identical bytes", stored_by_theme["Light"] == stored_by_theme["Dark"])
check("chromatic text and highlight stored as pasted",
      "color:#c00000" in stored_by_theme["Light"] and "background-color:#ffff00" in stored_by_theme["Light"]
      and "#202124" not in stored_by_theme["Light"])
apply_settings(win, theme="Light")


def contrast(c1, c2):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    a, b = sorted((lum(QColor(c1)), lum(QColor(c2))), reverse=True)
    return (a + 0.05) / (b + 0.05)


low = []
for colour in sorted(set(kept_colours) | {"#ee0000", "#8ab4f8"}):
    for theme, panel in (("Light", "#ffffff"), ("Dark", "#26272b")):
        ratio = contrast(colour, panel)
        if ratio < 3:
            low.append(f"{colour} on {theme} ({ratio:.1f}:1)")
print(f"  info  AM-9: fixture colours kept but low-contrast (< 3:1) in one theme: {low or 'none'}")

# ============================================================ C2b-6
print("\n--- [C2b-6] Paste as Plain Text (Ctrl+Shift+V) ---")
clip_source = bare(family="Georgia", size=15)
clip_source.load('<p><span style=" font-weight:700; color:#c00000;">boldclip</span></p>')


def fill_clipboard():
    clip_source.text_edit.selectAll()
    clip_source.text_edit.copy()
    settle()


win.activateWindow()
settle()
action = win.command_actions["paste_plain"]
edit_menu = next(a.menu() for a in win.menuBar().actions() if plain(a.text()) == "Edit")
menu_ids = [next((k for k, v in win.command_actions.items() if v is a), "|") for a in edit_menu.actions()]
check("Edit menu: Paste as Plain Text right after Paste",
      menu_ids[menu_ids.index("paste") + 1] == "paste_plain", menu_ids)
for name, get, show, leave in WORKSPACES:
    show(win)
    editor = get(win)
    edit = editor.text_edit
    for route in ("Ctrl+Shift+V", "Edit menu"):
        editor.load('<p>start <span style=" font-style:italic;">italic</span> end</p>')
        cursor = edit.textCursor()
        cursor.setPosition(len("start it"))
        edit.setTextCursor(cursor)
        edit.setFocus()
        fill_clipboard()
        edit.setFocus()
        settle()
        before_html = edit.toHtml()
        if route == "Ctrl+Shift+V":
            QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
        else:
            win._refresh_command_states()
            check(f"{name}: Paste as Plain Text is enabled in the editor", action.isEnabled())
            click_menu(win, "Edit", action)
        settle()
        fp = sd.fingerprint(edit.document())
        inserted = [r for r in fp.runs if "boldclip" in r.text]
        check(f"{name}, {route}: the clipboard's text is inserted in the caret's (italic) format, "
              f"no source formatting", edit.toPlainText() == "start itboldclipalic end"
              and inserted and all(r.italic and r.weight < 600 and r.fg is None and not r.families for r in inserted),
              (edit.toPlainText(), inserted[:1]))
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key_Z, Qt.ControlModifier)
        settle()
        check(f"{name}, {route}: one undo step", edit.toHtml() == before_html)
    editor.load("")
    editor.text_edit.document().setModified(False)
show_project(win)
title = win.projects_widget.title_edit
title.setFocus()
title.setText("Title ")
title.end(False)
fill_clipboard()
title.setFocus()
QTest.keyClick(title, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
settle()
check("a line edit: Ctrl+Shift+V pastes", title.text() == "Title boldclip", title.text())
title.setText("Paste project")
rows = {label: (key, where) for label, key, where in commands.shortcut_rows()}
check("Help → Keyboard Shortcuts lists Paste as Plain Text, Ctrl+Shift+V",
      rows.get("Paste as Plain Text", ("", ""))[0] == QKeySequence("Ctrl+Shift+V").toString(QKeySequence.NativeText),
      rows.get("Paste as Plain Text"))
page = HotkeysPage(win.db)
labels = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
row = labels.index("Paste as Plain Text") if "Paste as Plain Text" in labels else -1
check("the Hotkeys page lists it as assignable (a key editor, not a system key)",
      row >= 0 and page.table.cellWidget(row, 1) is not None)
commands.save(win.db, dict(commands.assignments(), paste_plain=[QKeySequence("Ctrl+Alt+V")]))
settle()
show_daily(win, "2026-07-01")
edit = win.editor.text_edit
win.editor.load("<p>x</p>")
edit.moveCursor(QTextCursor.End)
fill_clipboard()
edit.setFocus()
QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
settle()
old_key_text = edit.toPlainText()
QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier | Qt.AltModifier)
settle()
check("reassigned live: the new key pastes as plain text, the old one no longer does",
      old_key_text == "x" and edit.toPlainText() == "xboldclip", (old_key_text, edit.toPlainText()))
commands.save(win.db, {k: commands.default_keys(k) for k in commands.assignments()})
settle()
win.editor.load("")
win.editor.text_edit.document().setModified(False)

# ============================================================ C2b-7
print("\n--- [C2b-7] the Daily Jorts empty-state text (§13) ---")
edit = win.editor.text_edit


def disclaimer_painted(e):
    """Whether the grey text is on screen: the viewport differs from the
    same viewport with the text switched off."""
    on = e.viewport().grab().toImage()
    text, e.empty_state_text = e.empty_state_text, ""
    e.viewport().repaint()
    off = e.viewport().grab().toImage()
    e.empty_state_text = text
    e.viewport().repaint()
    return on != off and bool(text)


check("the wording is §13's, with the application's name",
      DAILY_JORTS_EMPTY_STATE.startswith("jortle_claude treats journal entries as individual historical records.")
      and DAILY_JORTS_EMPTY_STATE.endswith("rewriting or reformatting large portions of journal history."))
show_daily(win, EMPTY)
check("viewing an empty date writes no entry row",
      win.db._conn.execute("SELECT COUNT(*) FROM entries WHERE date=?", (EMPTY,)).fetchone()[0] == 0)
check("shown on an empty entry", disclaimer_painted(edit))
win.editor.load('<p style="-qt-paragraph-type:empty;"><br /></p><p style="-qt-paragraph-type:empty;"><br /></p>'
                '<p style="-qt-paragraph-type:empty;"><br /></p>')
check("shown on an old stored blank page (several empty paragraphs)", disclaimer_painted(edit))
# AM-24, the rule: shown while the entry's text, apart from paragraph breaks,
# is empty — no letter, space, tab, image or other character.
win.editor.load("")
edit.setFocus()
for _ in range(3):
    QTest.keyClick(edit, Qt.Key_Return)
settle()
check("AM-24: still shown on an empty entry after three Enters", disclaimer_painted(edit))
for name, key in (("a space", Qt.Key_Space), ("a tab", Qt.Key_Tab)):
    QTest.keyClick(edit, key)
    settle()
    check(f"AM-24: hidden once the entry holds {name}", not disclaimer_painted(edit))
    QTest.keyClick(edit, Qt.Key_Backspace)
    settle()
check("AM-24: shown again with only the empty paragraphs left", disclaimer_painted(edit))
win.editor.load('<p><img src="2026/03/photo.png" /></p>')
check("AM-24: hidden on an entry holding only an image", not disclaimer_painted(edit))
win.editor.load("")
edit.setFocus()
QTest.keyClick(edit, Qt.Key_X)
settle()
check("gone after one typed character", not disclaimer_painted(edit))
QTest.keyClick(edit, Qt.Key_Backspace)
settle()
check("back once the character is deleted", disclaimer_painted(edit))
win.editor.text_edit.document().setModified(False)
show_daily(win, "2026-03-05")
check("back on another empty date", disclaimer_painted(edit))
needle = "historical records"
check("never in toHtml, toPlainText or save()",
      needle not in edit.toHtml() and needle not in edit.toPlainText() and needle not in "".join(win.editor.save()))
edit.setFocus()
QTest.keyClicks(edit, "Written entry with words")
ctrl_s(win, edit)
check("not in search results", win.db.search_entries("historical") == [] and win.db.search_entries("records") == [])
show_daily(win, "2026-03-06")
win.editor.show_find_bar()
win.editor.find_input.setText("historical")
settle()
check("Edit → Find on an empty entry finds nothing", win.editor._find_ranges == [], win.editor._find_ranges)
win.editor.hide_find_bar()
archive_dir = ROOT / "archive-out"
archive.export_archive(win.db, archive_dir)
archive_text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in archive_dir.rglob("*")
                       if p.is_file() and p.suffix in (".html", ".css", ".js", ".txt", ".json"))
check("not in the readable archive", needle not in archive_text and "Written entry with words" in archive_text)
try:
    summary = backup.create_backup(destination=ROOT / "c2b7-backup.zip")
    with zipfile.ZipFile(summary.zip_path) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    check("not in a backup", needle.encode() not in blob)
except Exception as exc:     # noqa: BLE001
    check("not in a backup", False, repr(exc))
check("not in the database at all",
      not any(needle in str(v) for t in ("entries", "settings", "projects", "reader_notes_scoped")
              for row in win.db._conn.execute(f"SELECT * FROM {t}") for v in row))
show_project(win)
check("not in the project editor", win.projects_widget.editor.text_edit.empty_state_text == ""
      and not disclaimer_painted(win.projects_widget.editor.text_edit))
check("the Reader's Notes keep their own placeholder and have none of it",
      win.reader_notes.editor.text_edit.empty_state_text == ""
      and needle not in win.reader_notes.editor.text_edit.placeholderText()
      and win.reader_notes.editor.text_edit.placeholderText() != "")
GRAB_DIR.mkdir(parents=True, exist_ok=True)
for theme in ("Light", "Dark"):
    for ui in (9, 24):
        apply_settings(win, theme=theme, ui=ui)
        show_daily(win, "2026-03-07")
        settle(50)
        edit = win.editor.text_edit
        win.editor.text_edit.viewport().grab().save(str(GRAB_DIR / f"c2b7-disclaimer-{theme}-ui{ui}.png"))
        margin = int(edit.document().documentMargin())
        area = edit.viewport().rect().adjusted(margin, margin, -margin, -margin)
        needed = QFontMetrics(edit.font()).boundingRect(area, Qt.AlignTop | Qt.TextWordWrap, DAILY_JORTS_EMPTY_STATE)
        check(f"{theme}, UI font {ui}: shown, fits the editor (no clipping)",
              disclaimer_painted(edit) and needed.height() <= area.height() and needed.width() <= area.width(),
              (needed, area))
        check(f"{theme}, UI font {ui}: drawn in the placeholder colour (the theme's text colour, reduced alpha)",
              edit.palette().color(QPalette.PlaceholderText).alpha() < 255
              or edit.palette().color(QPalette.PlaceholderText) != edit.palette().color(QPalette.Text))
before_zoom = edit.viewport().grab().toImage()
edit.set_zoom_steps(5)
settle()
check("not zoomed (the grey text looks the same at 150%)", edit.viewport().grab().toImage() == before_zoom)
edit.reset_zoom()
apply_settings(win, theme="Light", ui=10)

# ============================================================ C2b-8
print("\n--- [C2b-8] the paragraph row's order (§14.1) ---")
EXPECTED_ROW = ["line_spacing", "¶↑", "space_before", "¶↓", "space_after", "|",
                "align_left", "align_center", "align_right", "align_justify", "|",
                "indent", "outdent", "bullet_list", "numbered_list", "quote", "|",
                "insert_link", "insert_photo"]


def row_ids(editor):
    bar = editor.paragraph_toolbar
    inverse = {id(a): k for k, a in editor.command_actions.items()}
    ids = []
    for a in bar.actions():
        widget = bar.widgetForAction(a)
        if a.isSeparator():
            ids.append("|")
        elif widget is editor.line_spacing_combo:
            ids.append("line_spacing")
        elif widget is editor.space_before_spin:
            ids.append("space_before")
        elif widget is editor.space_after_spin:
            ids.append("space_after")
        elif isinstance(widget, QLabel):
            ids.append(widget.text())
        else:
            ids.append(inverse.get(id(a), a.text()))
    return ids


for name, editor in (("journal", win.editor), ("project", win.projects_widget.editor)):
    check(f"{name}: paragraph row in the §14.1 order", row_ids(editor) == EXPECTED_ROW, row_ids(editor))
show_daily(win, "2026-03-08")
check("the ¶↑/¶↓ boxes say px (4C1b-D2), in the new order too",
      win.editor.space_before_spin.suffix() == " px" and win.editor.space_after_spin.suffix() == " px"
      and win.projects_widget.editor.space_before_spin.suffix() == " px",
      win.editor.space_before_spin.suffix())
readme = (REPO / "README.md").read_text(encoding="utf-8")
spacing_sentence = readme[readme.index("`¶↑`/`¶↓` spin boxes"):][:200]
check("the README describes the spacing boxes in px, as they are labelled (4C2/AM-26)",
      "px" in spacing_sentence and "points" not in spacing_sentence, spacing_sentence)
for ui in (9, 13, 24):
    apply_settings(win, ui=ui)
    settle(50)
    for spin in (win.editor.space_before_spin, win.editor.space_after_spin):
        spin.setValue(72)
        settle()
        line = spin.lineEdit()
        needed = line.fontMetrics().horizontalAdvance(line.text())
        check(f"UI font {ui}: '{line.text()}' fits its box (not clipped)",
              spin.isVisible() and line.width() >= needed, (line.width(), needed, spin.isVisible()))
        spin.setValue(0)
apply_settings(win, ui=10)

# ============================================================ the guard
print("\n--- [C2b-1] guard: every mapping rule is exercised ---")
check("every rule of 4C2b-D3…D5 was exercised by some check", exercised == RULES, sorted(RULES - exercised))

win.close()
settle()
print(f"\n({time.monotonic() - STARTED:.0f} s)")
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("ALL PASS")
