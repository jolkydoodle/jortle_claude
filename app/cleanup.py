"""Housekeeping for inline photos.

Since photos are embedded directly in entry/project text rather than
tracked in their own table, removing a photo from your writing doesn't
delete its file — which is the safe default (never silently lose a file),
but it does mean files can pile up on disk. This module finds any file
under the attachments folder that NOTHING ANYWHERE in the database still
references, so they can be reviewed and deleted deliberately.

"Nothing anywhere" is meant literally, and _referenced_paths explains why
it is derived from the schema instead of from a list of the stores someone
remembered to check.

Two image-reference syntaxes are scanned for (round 21): the Markdown
`![alt](path)` form used by any row still in the pre-round-21 "markdown"
format, and the HTML `<img src="path">` form Qt's toHtml() writes for the
same inline image once a row has been saved in the round-21+ "html"
format (see rich_editor.py's RichTextEditor.insert_photo(), which inserts
the image as a document resource either way — it's only the SAVED
representation of that resource that differs by format). Rather than
branch on each row's stored format column, both regexes are simply run
over every string found — one of the two always finds nothing for a given
row, which is harmless, and this stays correct even for content that mixes
history across a format upgrade (e.g. a project's current content is
"html" but an older saved version of it is still "markdown")."""
from __future__ import annotations

import re
from pathlib import Path

from .database import Database
from .paths import get_attachments_dir

_IMAGE_REF_RE_MARKDOWN = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
_IMAGE_REF_RE_HTML = re.compile(r'<img[^>]*\bsrc="([^"]+)"')


def _image_refs(content: str) -> set[str]:
    return set(_IMAGE_REF_RE_MARKDOWN.findall(content)) | set(_IMAGE_REF_RE_HTML.findall(content))


def _referenced_paths(db: Database) -> set[str]:
    """Every attachment path referenced anywhere in the database.

    Derived from the schema, not from a list of places to look — and that is
    the whole point. This function used to name the stores it searched:
    entries, projects, project versions. It was written before Reader's Notes
    existed, and nobody added it when Reader's Notes turned into a rich-text
    editor that can hold an image (paste a paragraph with a photo in it out
    of the journal and into the notes beside it, which is an ordinary thing
    to do). The result was that a photo living only in your notes counted as
    unreferenced, and "Find Unused Photos" would offer to delete a file that
    was still on screen.

    So it now walks every TEXT column of every table and runs both image
    regexes over whatever it finds. That is more work than necessary and it
    is deliberately the safe direction to be wrong in: an accidental match
    means a file is KEPT, which costs disk space, while a missed match means
    a file the user can still see is deleted. A new column, a new table, or a
    new feature that stores rich text is covered the day it is added, without
    anyone remembering to come back here.
    """
    refs: set[str] = set()
    conn = db._conn
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in tables:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")
                   if str(row[2]).upper().startswith("TEXT")]
        if not columns:
            continue
        selected = ", ".join(f'"{c}"' for c in columns)
        for row in conn.execute(f'SELECT {selected} FROM "{table}"'):
            for value in row:
                if isinstance(value, str) and value:
                    refs.update(_image_refs(value))
    return refs


def find_unused_photos(db: Database) -> list[Path]:
    """Returns absolute paths of files under the attachments folder that
    aren't referenced by any current or historical content."""
    attachments_dir = get_attachments_dir()
    referenced = _referenced_paths(db)
    referenced_absolute = {(attachments_dir / rel).resolve() for rel in referenced}

    unused = []
    for path in attachments_dir.rglob("*"):
        if path.is_file() and path.resolve() not in referenced_absolute:
            unused.append(path)
    return unused


def delete_files(paths: list[Path]) -> int:
    """Deletes the given files, returning total bytes freed. Best-effort —
    a file that can't be removed is skipped rather than aborting the rest."""
    freed = 0
    for path in paths:
        try:
            size = path.stat().st_size
            path.unlink()
            freed += size
        except OSError:
            continue
    return freed
