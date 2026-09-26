"""Saving: one authoritative path, and what counts as unsaved.

Three separate ideas that were previously tangled together, kept apart here
because the spec is explicit that they are different things (Part 34):

    unsaved editor content   ≠   saved journal state
    an empty saved journal   ≠   a failed save
    a database row exists    ≠   a meaningful journal entry exists

Autosave and Ctrl+S are not two implementations. Both call the same save
method on the same widget; autosave is only a timer deciding *when* to call
it. That is the requirement in Part 24 ("do not create a separate competing
persistence implementation solely for Ctrl+S") and it is also what makes the
dirty flag trustworthy — there is one place that clears it.

Manual saving is the default for a new install (Part 25). Autosave stays a
single checkbox away in File → Settings, and an install that had autosave
before keeps behaving the same way because the preference is written at
migration time (see `ensure_autosave_default`).
"""
from __future__ import annotations

AUTOSAVE_SETTING = "autosave_enabled"

# Manual saving is the normal state for a NEW preference (Part 25). An
# existing install is handled by ensure_autosave_default() below rather than
# by this constant, so nobody's established behaviour changes silently.
AUTOSAVE_DEFAULT = False


def autosave_enabled(db) -> bool:
    return db.get_setting(AUTOSAVE_SETTING, "1" if AUTOSAVE_DEFAULT else "0") == "1"


def set_autosave_enabled(db, enabled: bool):
    db.set_setting(AUTOSAVE_SETTING, "1" if enabled else "0")


def ensure_autosave_default(db) -> bool:
    """Writes an explicit autosave preference the first time the app runs.

    Without this, the change of default would silently change behaviour for
    someone who has been using the app for weeks: their writing used to be
    saved for them, and one upgrade later it quietly wasn't. So the choice is
    made once, explicitly, from evidence:

      * a database that already holds journal entries is an established
        install that has always had autosave — it keeps it, and the
        preference is written down so it is now a real choice they can see
        and change in Settings;
      * a fresh install gets the new default (manual saving).

    Either way the preference exists from then on and is never re-derived,
    which is the same migrate-on-read discipline the model-selection code
    learned the hard way: infer once, persist, then leave it alone.
    """
    existing = db.get_setting(AUTOSAVE_SETTING)
    if existing in ("0", "1"):
        return autosave_enabled(db)

    has_history = False
    try:
        row = db._conn.execute(
            "SELECT 1 FROM entries WHERE TRIM(COALESCE(body_text, '')) != '' LIMIT 1"
        ).fetchone()
        has_history = row is not None
    except Exception:  # noqa: BLE001 — a brand-new database simply has none
        has_history = False

    set_autosave_enabled(db, has_history)
    return has_history


# --------------------------------------------------------------- content
#
# THE rule for "is this a written entry or a blank one" (Master Spec §§9.1,
# 29, 59). Every place that marks, counts, exports or versions an entry asks
# `document_has_content()` (or, for many rows at once, the SQL built by
# `content_sql()` from the very same character set), so a blank entry can
# never look written in one place and blank in another.
#
# Blank means: nothing but characters that draw nothing. That is every
# character Python itself calls whitespace (spaces, tabs, line and paragraph
# breaks, the Unicode spaces rich text collects) plus the zero-width ones
# Python does not count as whitespace. An entry whose user deleted all of its
# text is therefore blank again — even if an empty list item or heading
# format is still sitting in the editor — while an entry holding only a photo
# is NOT blank: Qt's plain text shows an image as U+FFFC, which is not in this
# set, and the HTML check below catches any image the plain text missed.
_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff"
_BLANK_CHARS = "".join(sorted(
    {chr(c) for c in range(0x3001) if chr(c).isspace()} | set(_ZERO_WIDTH)
))
_BLANK_SET = frozenset(_BLANK_CHARS)

# Qt's placeholder for an embedded object (an inline image) in toPlainText().
OBJECT_REPLACEMENT_CHAR = "\ufffc"


def has_meaningful_text(plain_text) -> bool:
    """Whether a document's visible text amounts to anything (Part 20).

    Takes the PLAIN text, never the rich-text markup: `<p></p>` is a
    perfectly ordinary non-empty string that represents an empty entry, so
    testing the stored HTML's length answers a different question from the
    one being asked. The plain text is already stored alongside it
    (`body_text`), so this never has to touch the rich document to find out.
    """
    if not plain_text or not isinstance(plain_text, str):
        return False
    return any(ch not in _BLANK_SET for ch in plain_text)


def document_has_content(html, plain_text) -> bool:
    """Whether a document is a WRITTEN one rather than a blank one.

    Written = it has visible text, or it has an image. Used for the
    journal-entry marker, for deciding whether saving would create an entry
    at all, for Reader's Notes' marker, for the archive, and for version
    history. `html` may be None or a legacy Markdown string; an inline image
    in either format counts.
    """
    if has_meaningful_text(plain_text):
        return True
    markup = (html or "").lower()
    return "<img" in markup or "![" in markup


SQL_CONTENT_FUNCTION = "jortle_has_content"


def register_sql_functions(conn):
    """Makes `document_has_content` callable from SQL on this connection.

    The database asks "which of these dates hold a written entry?" for a
    whole month at once. Rather than a second, hand-translated definition in
    SQL (which is how the old one came to disagree with this one about
    zero-width spaces, and how Reader's Notes came to count a lone newline as
    content), SQL calls THIS function. One definition, two callers.
    """
    conn.create_function(
        SQL_CONTENT_FUNCTION, 2,
        lambda text, html: 1 if document_has_content(html, text) else 0,
        deterministic=True,
    )


def content_sql(text_column: str, html_column: str | None = None) -> str:
    """A WHERE-clause fragment for `document_has_content`, usable on any
    connection that went through `register_sql_functions`."""
    return f"{SQL_CONTENT_FUNCTION}({text_column}, {html_column or 'NULL'}) = 1"


# The journal-entry version of the question, used by the month grids.
MEANINGFUL_TEXT_SQL = content_sql("body_text", "body_md")


# ------------------------------------------------------------- prompting
SAVE, DISCARD, CANCEL = "save", "discard", "cancel"


def ask_unsaved(parent, what: str) -> str:
    """The Save / Discard / Cancel question (Part 26).

    `what` names the document in the user's terms — "this journal entry",
    "this project" — because the answer to "do you want to save?" depends
    entirely on knowing what would be lost.

    Deliberately explicit about what Discard does: "since the last save",
    not "everything", because the last saved version is never in danger.
    """
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Unsaved changes")
    box.setText(f"You have unsaved changes to {what}.")
    box.setInformativeText(
        "Save them, discard the changes you've made since the last save, or "
        "cancel and stay where you are."
    )
    save_btn = box.addButton("Save", QMessageBox.AcceptRole)
    discard_btn = box.addButton("Discard", QMessageBox.DestructiveRole)
    cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(save_btn)
    box.setEscapeButton(cancel_btn)
    box.exec()

    clicked = box.clickedButton()
    if clicked is save_btn:
        return SAVE
    if clicked is discard_btn:
        return DISCARD
    return CANCEL
