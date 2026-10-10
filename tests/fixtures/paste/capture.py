"""Saves what the Windows clipboard hands Jortle's editor on a paste, as a
test fixture for rich paste (batch 4C2b, decision 4C2b-D9).

Run it yourself, by hand, right after copying a sample (with no personal
content) in Edge or Chrome, or in Word:

    .venv\\Scripts\\python.exe tests\\fixtures\\paste\\capture.py <name>

It writes tests/fixtures/paste/<name>/:

    manifest.json   the formats on the clipboard (name and size), what Qt
                    reports (has HTML / text / image / URLs), what was saved,
                    and how much was removed or replaced (see below)
    content.html    QMimeData.html(): the HTML the editor's paste receives
    content.txt     QMimeData.text()
    image.png       QMimeData.imageData(), when the clipboard holds an image
    files/          the local image files the HTML refers to (file: URLs and
                    Windows paths), copied; manifest.json maps each
                    reference, as it reads in content.html, to its copy

Before anything is written:

  * the user's profile folder (USERPROFILE) is replaced by C:/Users/user in
    every form it appears in (backslashes, forward slashes, URL-encoded);
  * the user name is replaced by "user" inside file URLs and Windows paths;
    anywhere else it is left alone and only counted (look for it before
    committing: it would be personal content in the sample);
  * Word's document-properties blocks (<o:DocumentProperties> and
    <o:CustomDocumentProperties>: author, company, dates) are removed.

Only the native Windows clipboard is read: on any other Qt platform (the
offscreen one used by the tests holds an in-process clipboard) the tool
refuses before touching it. Nothing else on the machine is read or changed,
and an existing capture is replaced only with --force. Review the files
before they are committed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
SCRUBBED_PROFILE = "C:/Users/user"
SCRUBBED_USER = "user"
NAME_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff",
                  ".emf", ".wmf", ".svg", ".ico"}
WORD_PROPERTY_BLOCKS = re.compile(
    r"<o:(DocumentProperties|CustomDocumentProperties)\b[^>]*>.*?</o:\1\s*>",
    re.IGNORECASE | re.DOTALL)
# A file URL or a Windows drive path, up to a quote, whitespace or tag end.
# "file:/" so a page name such as Wikipedia's /wiki/File:Flag.svg is not one.
FILE_URL = re.compile(r"(?<![A-Za-z0-9/])file:/[^\s\"'<>]*", re.IGNORECASE)
DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]*")


# ------------------------------------------------------------- scrubbing
def default_profile() -> str:
    return os.environ.get("USERPROFILE") or str(Path.home())


def default_user_names(profile: str) -> list:
    names = [os.environ.get("USERNAME", ""), Path(profile).name]
    try:
        import getpass
        names.append(getpass.getuser())
    except Exception:  # noqa: BLE001 — no login name is fine
        pass
    unique = []
    for name in names:
        if name and name.lower() not in (n.lower() for n in unique):
            unique.append(name)
    return unique


class Scrubber:
    """Replaces the profile folder and the user name; counts what it did."""

    def __init__(self, profile: str, user_names: list):
        forward = profile.replace("\\", "/").rstrip("/")
        self.profile_forms = []   # (compiled pattern, replacement)
        variants = {
            forward: SCRUBBED_PROFILE,
            forward.replace("/", "\\"): SCRUBBED_PROFILE.replace("/", "\\"),
            forward.replace("/", "\\\\"): SCRUBBED_PROFILE.replace("/", "\\\\"),
            quote(forward, safe=":/"): SCRUBBED_PROFILE,
            quote(forward, safe=":/").replace("/", "%5C"): SCRUBBED_PROFILE.replace("/", "%5C"),
        }
        # Longest first, so a form never eats part of a longer one.
        for form in sorted(variants, key=len, reverse=True):
            if form:
                self.profile_forms.append(
                    (re.compile(re.escape(form) + r"(?![A-Za-z0-9])", re.IGNORECASE), variants[form]))
        self.user_patterns = []
        for name in user_names:
            for form in {name, quote(name)}:
                if form:
                    self.user_patterns.append(re.compile(re.escape(form), re.IGNORECASE))
        self.profile_replaced = 0
        self.user_name_in_paths = 0

    def _in_path(self, match) -> str:
        text = match.group(0)
        for pattern in self.user_patterns:
            text, n = pattern.subn(SCRUBBED_USER, text)
            self.user_name_in_paths += n
        return text

    def scrub(self, text: str) -> str:
        for pattern, replacement in self.profile_forms:
            text, n = pattern.subn(lambda _m, r=replacement: r, text)
            self.profile_replaced += n
        text = FILE_URL.sub(self._in_path, text)
        text = DRIVE_PATH.sub(self._in_path, text)
        return text

    def count_left(self, text: str) -> int:
        """Occurrences of the user name that scrub() left (outside paths)."""
        return sum(len(p.findall(text)) for p in self.user_patterns)


def strip_word_properties(html: str):
    return WORD_PROPERTY_BLOCKS.subn("", html)


# ---------------------------------------------------------- local images
def local_references(html: str) -> list:
    """Every file URL and Windows drive path in the HTML, in order, once."""
    urls = list(FILE_URL.finditer(html))
    inside_url = [(m.start(), m.end()) for m in urls]
    paths = [m for m in DRIVE_PATH.finditer(html)
             if not any(start <= m.start() < end for start, end in inside_url)]
    seen = []
    for match in sorted(urls + paths, key=lambda m: m.start()):
        if match.group(0) not in seen:
            seen.append(match.group(0))
    return seen


def reference_to_path(ref: str) -> Path:
    from PySide6.QtCore import QUrl
    if ref.lower().startswith("file:"):
        return Path(QUrl(ref).toLocalFile())
    return Path(ref)


def copy_local_images(html: str, files_dir: Path, scrubber: Scrubber) -> list:
    entries = []
    used = set()
    for ref in local_references(html):
        path = reference_to_path(ref)
        entry = {"reference": scrubber.scrub(ref)}
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            entry["status"] = "not an image (not copied)"
        elif not path.is_file():
            entry["status"] = "missing"
        else:
            files_dir.mkdir(parents=True, exist_ok=True)
            name = scrubber.scrub(path.name)
            stem, suffix, n = Path(name).stem, Path(name).suffix, 1
            while name.lower() in used:
                n += 1
                name = f"{stem}-{n}{suffix}"
            used.add(name.lower())
            shutil.copyfile(path, files_dir / name)
            entry["status"] = "copied"
            entry["file"] = f"files/{name}"
        entries.append(entry)
    return entries


# --------------------------------------------------------------- capture
def capture_from_mime(mime, out_dir: Path, *, profile: str | None = None,
                      user_names: list | None = None, platform_name: str = "",
                      note: str = "") -> dict:
    """Writes one capture of `mime` (a QMimeData) into out_dir, which must
    not exist yet. Returns the manifest (also written as manifest.json)."""
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QImage, QPixmap

    profile = profile if profile is not None else default_profile()
    user_names = user_names if user_names is not None else default_user_names(profile)
    scrubber = Scrubber(profile, user_names)   # the content; its counts go in the manifest
    meta = Scrubber(profile, user_names)       # names, references, the note: not counted
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "tool": "tests/fixtures/paste/capture.py (4C2b-D9)",
        "captured_on": datetime.date.today().isoformat(),
        "platform": platform_name,
        "qt": qVersion(),
        "pyside": pyside_version,
        "note": meta.scrub(note),
        "has_html": mime.hasHtml(),
        "has_text": mime.hasText(),
        "has_image": mime.hasImage(),
        "has_urls": mime.hasUrls(),
        "formats": [{"name": meta.scrub(f), "bytes": len(bytes(mime.data(f)))}
                    for f in mime.formats()],
        "saved": [],
    }
    left = 0

    if mime.hasHtml():
        raw = mime.html()
        manifest["local_images"] = copy_local_images(raw, out_dir / "files", meta)
        html, removed = strip_word_properties(raw)
        manifest["word_property_blocks_removed"] = removed
        html = scrubber.scrub(html)
        left += scrubber.count_left(html)
        (out_dir / "content.html").write_text(html, encoding="utf-8", newline="")
        manifest["saved"].append("content.html")
    if mime.hasText():
        text = scrubber.scrub(mime.text())
        left += scrubber.count_left(text)
        (out_dir / "content.txt").write_text(text, encoding="utf-8", newline="")
        manifest["saved"].append("content.txt")
    if mime.hasImage():
        image = mime.imageData()
        if isinstance(image, QPixmap):
            image = image.toImage()
        if isinstance(image, QImage) and not image.isNull():
            image.save(str(out_dir / "image.png"), "PNG")
            manifest["image"] = {"width": image.width(), "height": image.height()}
            manifest["saved"].append("image.png")
    if mime.hasUrls():
        manifest["urls"] = [meta.scrub(u.toString()) for u in mime.urls()]

    # Counted in content.html and content.txt only.
    manifest["profile_path_replaced"] = scrubber.profile_replaced
    manifest["user_name_replaced_in_paths"] = scrubber.user_name_in_paths
    manifest["user_name_left_elsewhere"] = left
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def clipboard_mime():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.clipboard().mimeData()


def main(argv=None, *, get_mime=clipboard_mime, platform_name=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("name", help="folder name for this sample, e.g. edge-article or word-lists")
    parser.add_argument("--note", default="", help="what was copied, from where (no personal content)")
    parser.add_argument("--force", action="store_true", help="replace an existing capture of that name")
    parser.add_argument("--out-root", default=str(HERE), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not NAME_RULE.match(args.name):
        print(f"Refused: '{args.name}' — use letters, digits, '.', '_' and '-' only.", file=sys.stderr)
        return 2
    out_dir = Path(args.out_root) / args.name
    if out_dir.exists():
        if not args.force:
            print(f"Refused: {out_dir} exists. Use --force to replace it.", file=sys.stderr)
            return 2
        if not (out_dir / "manifest.json").is_file():
            print(f"Refused: {out_dir} is not a capture (no manifest.json); not replacing it.",
                  file=sys.stderr)
            return 2

    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    platform_name = platform_name or app.platformName()
    if platform_name != "windows":
        print(f"Refused: the Qt platform is '{platform_name}', not 'windows'. This tool reads "
              "the native Windows clipboard only (unset QT_QPA_PLATFORM).", file=sys.stderr)
        return 2

    mime = get_mime()
    if mime is None or not mime.formats():
        print("The clipboard is empty: copy the sample first.", file=sys.stderr)
        return 1
    if out_dir.exists():
        shutil.rmtree(out_dir)
    manifest = capture_from_mime(mime, out_dir, platform_name=platform_name, note=args.note)

    print(f"Saved to {out_dir}:")
    for name in manifest["saved"]:
        print(f"  {name}")
    for entry in manifest.get("local_images", []):
        print(f"  local image {entry['status']}: {entry['reference']}")
    print(f"Formats on the clipboard: {len(manifest['formats'])}")
    print(f"Profile path replaced {manifest['profile_path_replaced']}x; user name replaced in "
          f"paths {manifest['user_name_replaced_in_paths']}x; Word property blocks removed "
          f"{manifest.get('word_property_blocks_removed', 0)}.")
    if manifest["user_name_left_elsewhere"]:
        print(f"WARNING: your user name still appears {manifest['user_name_left_elsewhere']}x "
              "outside file paths. Check content.html and content.txt before committing.")
    print("Review the files before they are committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
