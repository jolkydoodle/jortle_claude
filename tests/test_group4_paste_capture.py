"""Group 4, batch 4C2b, phase 1 — the paste capture tool (4C2b-D9, 4C2/AM-10,
4C2/AM-18): tests/fixtures/paste/capture.py.

The tool is run by the user only, on the native Windows clipboard. These
checks never touch any clipboard: they hand the tool's capture function a
Python-built QMimeData, and run its command line with an injected source
that records whether it was asked. Paths and the user name are made up and
live under this suite's isolated root.

JORTLE_CAPTURE_DIR points the suite at another copy of the tool (mutants).
"""
import importlib.util
import json
import os
import pathlib
import sys

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = isolation.isolate(prefix="jortle-g4-capture-")
HERE = pathlib.Path(__file__).resolve().parent

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

TOOL = pathlib.Path(os.environ.get("JORTLE_CAPTURE_DIR", HERE / "fixtures" / "paste")) / "capture.py"
spec = importlib.util.spec_from_file_location("capture", TOOL)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
print(f"capture tool: {TOOL}")

failures = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def all_bytes(folder: pathlib.Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())


# A made-up Windows profile under the isolated root, with Word's temp folder.
USER = "exampleuser"
PROFILE = ROOT / "Users" / USER
CLIP = PROFILE / "AppData" / "Local" / "Temp" / "msohtmlclip1" / "01"
CLIP.mkdir(parents=True)
img1 = QImage(30, 20, QImage.Format_RGB32)
img1.fill(QColor("#e01b24"))
img1.save(str(CLIP / "clip_image001.png"))
img2 = QImage(12, 12, QImage.Format_RGB32)
img2.fill(QColor("#2f5496"))
img2.save(str(CLIP / "clip_image002.jpg"))
(CLIP / "clip_filelist.xml").write_text("<xml/>", encoding="utf-8")
SPACED = ROOT / "Users" / USER / "My Pictures"
SPACED.mkdir(parents=True)
img1.save(str(SPACED / "photo one.png"))

fwd = str(PROFILE).replace("\\", "/")
back = fwd.replace("/", "\\")
url1 = QUrl.fromLocalFile(str(CLIP / "clip_image001.png")).toString()          # file:///…
url_spaced = QUrl.fromLocalFile(str(SPACED / "photo one.png")).toString(QUrl.FullyEncoded)
url_upper = "file:///" + fwd.upper() + "/AppData/Local/Temp/msohtmlclip1/01/clip_image001.png"
WORD_HTML = f"""<html xmlns:o="urn:schemas-microsoft-com:office:office">
<head><meta charset="utf-8"><link rel=File-List href="{QUrl.fromLocalFile(str(CLIP / 'clip_filelist.xml')).toString()}">
<!--[if gte mso 9]><xml>
<o:DocumentProperties>
  <o:Author>Ann Author</o:Author><o:LastAuthor>{USER}</o:LastAuthor><o:Company>Secret Co</o:Company>
</o:DocumentProperties>
<o:CustomDocumentProperties><o:Client dt:dt="string">Client Name</o:Client></o:CustomDocumentProperties>
<o:OfficeDocumentSettings><o:AllowPNG/></o:OfficeDocumentSettings>
</xml><![endif]-->
</head><body>
<p class=MsoListParagraph style='mso-list:l0 level1 lfo1'>First item</p>
<p><img width=30 height=20 src="{url1}" alt="red"></p>
<p><img src="{url_spaced}"></p>
<p><img src="{url_upper}"></p>
<p><v:imagedata src="{back}\\AppData\\Local\\Temp\\msohtmlclip1\\01\\clip_image002.jpg" o:title=""/></p>
<p><img src="{fwd}/AppData/Local/Temp/msohtmlclip1/01/missing.png"></p>
<p><img src="data:image/png;base64,iVBORw0KGgo="></p>
<p>Written by {USER} in plain text.</p>
</body></html>"""
TEXT = f"First item\r\nSaved in {back}\\Documents\\notes.docx\r\n"


def word_mime():
    mime = QMimeData()
    mime.setHtml(WORD_HTML)
    mime.setText(TEXT)
    image = QImage(40, 25, QImage.Format_ARGB32)
    image.fill(QColor("#33aa55"))
    mime.setImageData(image)
    return mime


# ---------------------------------------------------------------- capture
print("Capture of a Word-like clipboard (synthetic, made-up profile)")
out = ROOT / "out" / "word-sample"
manifest = capture.capture_from_mime(word_mime(), out, profile=str(PROFILE), user_names=[USER],
                                     platform_name="windows", note=f"from {back}")
html = (out / "content.html").read_text(encoding="utf-8")
text = (out / "content.txt").read_text(encoding="utf-8")
on_disk = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

check("manifest.json is what the function returned", on_disk == manifest)
check("saved: content.html, content.txt, image.png",
      manifest["saved"] == ["content.html", "content.txt", "image.png"], manifest["saved"])
check("has-flags recorded (html, text, image)",
      manifest["has_html"] and manifest["has_text"] and manifest["has_image"])
names = [f["name"] for f in manifest["formats"]]
# A Python-built QMimeData holds its image as an object: its format reports
# 0 bytes. On the Windows clipboard each format has its real size.
check("format list holds text/html and text/plain with their sizes, and the image format",
      {f["name"]: f["bytes"] for f in manifest["formats"]}.get("text/html", 0) > 0
      and {f["name"]: f["bytes"] for f in manifest["formats"]}.get("text/plain", 0) > 0
      and "application/x-qt-image" in names, manifest["formats"])

blob = all_bytes(out).lower()
check("the user name appears nowhere in any written file except the one counted body line",
      blob.count(USER.encode()) == 1, blob.count(USER.encode()))
check("the isolated root's path (stand-in for the profile) appears in no written file",
      str(ROOT).lower().encode() not in blob
      and str(ROOT).replace("\\", "/").lower().encode() not in blob)
check("the user name outside paths is left alone and counted (1)",
      f"Written by {USER} in plain text." in html and manifest["user_name_left_elsewhere"] == 1,
      manifest["user_name_left_elsewhere"])
check("profile replaced in forward, back-slash, URL-encoded and upper-case forms",
      "file:///C:/Users/user/AppData/Local/Temp/msohtmlclip1/01/clip_image001.png" in html
      and "C:\\Users\\user\\AppData\\Local\\Temp\\msohtmlclip1\\01\\clip_image002.jpg" in html
      and "file:///C:/Users/user/My%20Pictures/photo%20one.png" in html
      and html.count("C:/Users/user/AppData/Local/Temp/msohtmlclip1/01/clip_image001.png") == 2,
      [line for line in html.splitlines() if "img" in line or "imagedata" in line])
check("plain text scrubbed too", "C:\\Users\\user\\Documents\\notes.docx" in text, text)
check("note scrubbed", manifest["note"] == "from C:\\Users\\user", manifest["note"])

check("Word's document-properties blocks removed (2), with their contents",
      manifest["word_property_blocks_removed"] == 2
      and "Ann Author" not in html and "Secret Co" not in html and "Client Name" not in html
      and "DocumentProperties" not in html, manifest["word_property_blocks_removed"])
check("the rest of Word's markup kept (OfficeDocumentSettings, mso-list, v:imagedata, data: image)",
      "<o:OfficeDocumentSettings>" in html and "mso-list:l0 level1 lfo1" in html
      and "<v:imagedata" in html and "data:image/png;base64,iVBORw0KGgo=" in html)

images = {e["reference"]: e for e in manifest["local_images"]}
copied = [e for e in manifest["local_images"] if e["status"] == "copied"]
check("every referenced local image is listed once by its reference as it reads in content.html",
      all(ref in html for ref in images), [r for r in images if r not in html])
check("clip_image001.png (file URL) copied, same pixels",
      any(e["file"] == "files/clip_image001.png" for e in copied)
      and QImage(str(out / "files" / "clip_image001.png")).pixelColor(3, 3) == QColor("#e01b24"))
check("clip_image002.jpg (Windows back-slash path in v:imagedata) copied",
      any(e["file"] == "files/clip_image002.jpg" for e in copied)
      and (out / "files" / "clip_image002.jpg").is_file())
check("URL-encoded path with spaces resolved and copied ('photo one.png')",
      any(e["file"] == "files/photo one.png" for e in copied))
check("the upper-case reference to the same file copied under another name (no overwrite)",
      any(e["file"] == "files/clip_image001-2.png" for e in copied), [e.get("file") for e in copied])
check("a missing local image is listed as missing",
      any(e["status"] == "missing" and e["reference"].endswith("missing.png") for e in manifest["local_images"]))
check("a non-image local file (Word's file list) is listed, not copied",
      any(e["status"] == "not an image (not copied)" and e["reference"].endswith("clip_filelist.xml")
          for e in manifest["local_images"])
      and not (out / "files" / "clip_filelist.xml").exists())
check("data: images are not 'local images'",
      not any(r.startswith("data:") for r in images))
saved_image = QImage(str(out / "image.png"))
check("clipboard image data saved as PNG with its size and pixels",
      saved_image.size().width() == 40 and saved_image.size().height() == 25
      and saved_image.pixelColor(5, 5) == QColor("#33aa55")
      and manifest["image"] == {"width": 40, "height": 25})
check("counts recorded: the profile 6x in content.html + 1x in content.txt; no bare user name in paths",
      manifest["profile_path_replaced"] == 7 and manifest["user_name_replaced_in_paths"] == 0,
      (manifest["profile_path_replaced"], manifest["user_name_replaced_in_paths"]))

print("User name in a path outside the profile")
mime = QMimeData()
mime.setHtml(f'<img src="file:///D:/Shared/{USER}/a.png"><img src="file:///D:/Shared/{USER.upper()}%20x/b.png">'
             f'<p>D:\\Backups\\{USER}\\c.png</p>')
m2 = capture.capture_from_mime(mime, ROOT / "out" / "elsewhere", profile=str(PROFILE), user_names=[USER])
h2 = (ROOT / "out" / "elsewhere" / "content.html").read_text(encoding="utf-8")
check("replaced inside file URLs and drive paths, any case",
      USER not in h2.lower() and "D:/Shared/user/a.png" in h2 and "D:/Shared/user%20x/b.png" in h2
      and "D:\\Backups\\user\\c.png" in h2 and m2["user_name_replaced_in_paths"] == 3, h2)
check("not 'left elsewhere'", m2["user_name_left_elsewhere"] == 0)
check("no text, no image: only content.html saved",
      m2["saved"] == ["content.html"] and not (ROOT / "out" / "elsewhere" / "image.png").exists())

print("A browser-like clipboard (no file references)")
mime = QMimeData()
mime.setHtml('<p style="color:#202124"><a href="https://example.org/a?x=1&amp;y=2">link</a>'
             '<a href="https://en.wikipedia.org/wiki/File:Flag.svg">a wiki file page</a>'
             '<img src="https://example.org/i.png"></p>')
mime.setText("link")
mime.setUrls([QUrl("https://example.org/a")])
m3 = capture.capture_from_mime(mime, ROOT / "out" / "browser", profile=str(PROFILE), user_names=[USER])
h3 = (ROOT / "out" / "browser" / "content.html").read_text(encoding="utf-8")
check("HTML saved exactly as QMimeData.html() gives it", h3 == mime.html())
check("no local images (a wiki 'File:' page is not a file URL); remote image untouched", m3["local_images"] == [] and "https://example.org/i.png" in h3)
check("URLs recorded", m3["urls"] == ["https://example.org/a"], m3.get("urls"))
check("nothing replaced", m3["profile_path_replaced"] == 0 and m3["user_name_replaced_in_paths"] == 0)

print("The default profile is USERPROFILE (isolated here)")
check("default_profile() reads USERPROFILE", capture.default_profile() == os.environ["USERPROFILE"])
check("default user names include the profile folder's name",
      pathlib.Path(os.environ["USERPROFILE"]).name.lower()
      in [n.lower() for n in capture.default_user_names(os.environ["USERPROFILE"])])

try:
    capture.capture_from_mime(word_mime(), out, profile=str(PROFILE), user_names=[USER])
    raised = False
except FileExistsError:
    raised = True
check("capture_from_mime refuses an existing folder", raised)

# ------------------------------------------------------------ command line
print("Command line (injected source; no clipboard)")
asked = []


def source(mime_factory):
    def get():
        asked.append(True)
        return mime_factory()
    return get


OUT_ROOT = ROOT / "cli"
OUT_ROOT.mkdir()
rc = capture.main(["sample", "--out-root", str(OUT_ROOT)], get_mime=source(word_mime))
check("refuses on the offscreen platform (exit 2) without asking for the clipboard",
      rc == 2 and not asked and not (OUT_ROOT / "sample").exists(), (rc, asked))
rc = capture.main(["../escape", "--out-root", str(OUT_ROOT)], get_mime=source(word_mime), platform_name="windows")
check("refuses a name with a path in it, without asking", rc == 2 and not asked)
rc = capture.main(["sample", "--out-root", str(OUT_ROOT)], get_mime=source(QMimeData), platform_name="windows")
check("an empty clipboard writes nothing (exit 1)", rc == 1 and not (OUT_ROOT / "sample").exists())
asked.clear()
rc = capture.main(["sample", "--out-root", str(OUT_ROOT), "--note", "Word test"],
                  get_mime=source(word_mime), platform_name="windows")
check("on 'windows' it captures (exit 0) into <out-root>/<name>",
      rc == 0 and asked and (OUT_ROOT / "sample" / "manifest.json").is_file()
      and (OUT_ROOT / "sample" / "content.html").is_file(), rc)
first = (OUT_ROOT / "sample" / "manifest.json").read_bytes()
asked.clear()
rc = capture.main(["sample", "--out-root", str(OUT_ROOT)], get_mime=source(word_mime), platform_name="windows")
check("an existing capture is not replaced without --force (exit 2, not asked)",
      rc == 2 and not asked and (OUT_ROOT / "sample" / "manifest.json").read_bytes() == first)


def browser_mime():
    mime = QMimeData()
    mime.setHtml("<p>replacement</p>")
    return mime


(OUT_ROOT / "sample" / "files" / "stale.png").write_bytes(b"x")
rc = capture.main(["sample", "--out-root", str(OUT_ROOT), "--force"],
                  get_mime=source(browser_mime), platform_name="windows")
check("--force replaces the whole capture (old files gone)",
      rc == 0 and "replacement" in (OUT_ROOT / "sample" / "content.html").read_text(encoding="utf-8")
      and not (OUT_ROOT / "sample" / "files").exists()
      and not (OUT_ROOT / "sample" / "image.png").exists())
(OUT_ROOT / "not-a-capture").mkdir()
(OUT_ROOT / "not-a-capture" / "keep.txt").write_text("keep", encoding="utf-8")
asked.clear()
rc = capture.main(["not-a-capture", "--out-root", str(OUT_ROOT), "--force"],
                  get_mime=source(word_mime), platform_name="windows")
check("--force never deletes a folder that is not a capture",
      rc == 2 and not asked and (OUT_ROOT / "not-a-capture" / "keep.txt").is_file())

print("The test suite itself")
src = pathlib.Path(__file__).read_text(encoding="utf-8")
check("this suite never asks Qt for a clipboard", src.count("clipboard" + "()") == 0)
check("run_all does not run the tool as a suite (it is not test_*.py)", not TOOL.name.startswith("test_"))

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("ALL PASS")
