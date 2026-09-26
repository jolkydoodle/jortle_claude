"""The two windows over the history stores: one entry's Version History, and
File → Recovery (Master Spec §§44.2, 44.5).

Both are browse-and-choose windows. They read metadata first and load a
body only when the user selects that row (lazy loading, §44.1), preview it
in the same read-only rich-text editor the Projects version preview uses,
and never write to the working entry themselves: restoring is REQUESTED by
signal and carried out by the main window through the ordinary save path,
so the usual Save / Discard / Cancel and the "keep the current state first"
rule apply exactly as they do everywhere else.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QDateEdit, QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget
)

from .date_state import human, human_long
from .rich_editor import RichEditor


def _when(iso_timestamp: str) -> str:
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
    except ValueError:
        return iso_timestamp


def _wrapping_list() -> QListWidget:
    """A list whose two-line rows wrap to the pane instead of scrolling
    sideways — the pane is narrow, and at large interface fonts the rows
    would otherwise be cut off mid-word."""
    widget = QListWidget()
    widget.setWordWrap(True)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    widget.setTextElideMode(Qt.ElideNone)
    return widget


def _preview_editor() -> RichEditor:
    editor = RichEditor(read_only=True)
    return editor


class EntryHistoryDialog(QDialog):
    """Previous versions of ONE daily entry, newest first."""

    restoreRequested = Signal(int)   # revision id

    def __init__(self, db, date: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.date = date
        self.setWindowTitle(f"Version History — {human_long(date)}")
        self.resize(820, 560)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("SubtleHint")

        self.list = _wrapping_list()
        self.list.currentItemChanged.connect(self._on_selected)
        self.list.itemDoubleClicked.connect(lambda _item: self._request_restore())

        self.preview = _preview_editor()
        self.preview.set_storage_subdir(date[:7].replace("-", "/"))

        splitter = QSplitter()
        splitter.addWidget(self.list)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 540])

        self.restore_btn = QPushButton("Restore This Version…")
        self.restore_btn.clicked.connect(self._request_restore)

        self.delete_all_btn = QPushButton("Delete All Previous Versions…")
        self.delete_all_btn.clicked.connect(self._delete_all)

        self.cutoff = QDateEdit(QDate.currentDate())
        self.cutoff.setCalendarPopup(True)
        self.cutoff.setDisplayFormat("yyyy-MM-dd")
        self.delete_older_btn = QPushButton("Delete Versions Older Than…")
        self.delete_older_btn.clicked.connect(self._delete_older)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.restore_btn)
        actions.addStretch(1)
        actions.addWidget(self.delete_all_btn)
        actions.addWidget(self.delete_older_btn)
        actions.addWidget(self.cutoff)
        actions.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(actions)

        self.reload()

    # ------------------------------------------------------------ content
    def reload(self):
        revisions = self.db.list_entry_revisions(self.date)   # metadata only
        self.list.blockSignals(True)
        self.list.clear()
        for rev in revisions:
            title = f" — “{rev.title}”" if rev.title else ""
            item = QListWidgetItem(
                f"{_when(rev.created_at)}{title}\n{rev.reason} · {rev.char_count:,} characters")
            item.setData(Qt.UserRole, rev.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        count = len(revisions)
        if count:
            self.summary.setText(
                f"{count} previous version{'s' if count != 1 else ''} of this entry. "
                "Versions are kept when you leave an entry you changed, when you close "
                "the app, before a restore, and during long editing sessions — not on "
                "every autosave. Restoring keeps the current text as a version first.")
            self.list.setCurrentRow(0)
        else:
            self.summary.setText(
                "No previous versions of this entry yet. A version is kept once you "
                "have changed the entry and then leave it or close the app.")
            self.preview.load("", "html")
        for button in (self.restore_btn, self.delete_all_btn, self.delete_older_btn):
            button.setEnabled(bool(count))

    def _selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_selected(self, item, _previous):
        if item is None:
            self.preview.load("", "html")
            return
        revision = self.db.get_entry_revision(item.data(Qt.UserRole))   # the one body
        if revision is not None:
            self.preview.load(revision.body, revision.body_format)

    # ------------------------------------------------------------ actions
    def _request_restore(self):
        revision_id = self._selected_id()
        if revision_id is None:
            return
        answer = QMessageBox.question(
            self, "Restore version",
            "Replace this entry with the selected version?\n\n"
            "The entry's current text is kept as a version first, so this can be "
            "undone from this window.",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.restoreRequested.emit(revision_id)
        self.reload()

    def _delete_all(self):
        answer = QMessageBox.warning(
            self, "Delete all previous versions",
            f"Permanently delete every previous version of the entry for "
            f"{human(self.date)}?\n\nThe entry itself is not changed. Recovery "
            f"copies (File → Recovery) and backups are not affected.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.db.delete_entry_revisions(self.date)
        self.reload()

    def _delete_older(self):
        cutoff = self.cutoff.date().toString("yyyy-MM-dd")
        answer = QMessageBox.warning(
            self, "Delete older versions",
            f"Permanently delete the versions of this entry saved before "
            f"{human(cutoff)}?\n\nThe entry itself is not changed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.db.delete_entry_revisions_before(self.date, cutoff)
        self.reload()


class RecoveryDialog(QDialog):
    """File → Recovery: content kept just before a large deletion."""

    restoreRequested = Signal(int)   # checkpoint id

    def __init__(self, db, project_title=None, parent=None):
        super().__init__(parent)
        self.db = db
        # Projects can be renamed; the window shows the current title when
        # the project still exists, the title at the time otherwise.
        self._project_title = project_title or (lambda _ref: None)
        self.setWindowTitle("Recovery")
        self.resize(860, 580)

        intro = QLabel(
            "When a save removes a large part of an entry or project, the text as "
            "it was just before is kept here. These are recovery copies, not "
            "backups: they protect against one big accidental deletion. They are "
            "never deleted automatically.")
        intro.setWordWrap(True)
        intro.setObjectName("SubtleHint")

        self.list = _wrapping_list()
        self.list.currentItemChanged.connect(self._on_selected)

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.preview = _preview_editor()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.details)
        right_layout.addWidget(self.preview, stretch=1)

        splitter = QSplitter()
        splitter.addWidget(self.list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 540])

        self.restore_btn = QPushButton("Restore…")
        self.restore_btn.clicked.connect(self._request_restore)
        self.copy_btn = QPushButton("Copy Text")
        self.copy_btn.clicked.connect(self._copy)
        self.delete_btn = QPushButton("Delete This Copy…")
        self.delete_btn.clicked.connect(self._delete)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.restore_btn)
        actions.addWidget(self.copy_btn)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)
        actions.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(actions)

        self._current = None
        self.reload()

    def _label_for(self, cp) -> str:
        if cp.scope == "date":
            where = f"Daily entry · {human(cp.ref)}"
            if cp.title:
                where += f" — “{cp.title}”"
        else:
            title = self._project_title(cp.ref) or cp.title or "(untitled)"
            where = f"Project · {title}"
        return where

    def reload(self):
        checkpoints = self.db.list_recovery_checkpoints()   # metadata only
        self.list.blockSignals(True)
        self.list.clear()
        for cp in checkpoints:
            item = QListWidgetItem(
                f"{self._label_for(cp)}\n{_when(cp.created_at)} · large deletion: "
                f"{cp.removed_chars:,} characters removed")
            item.setData(Qt.UserRole, cp.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        has_any = bool(checkpoints)
        for button in (self.restore_btn, self.copy_btn, self.delete_btn):
            button.setEnabled(has_any)
        if has_any:
            self.list.setCurrentRow(0)
        else:
            self._current = None
            self.details.setText("Nothing to recover — no large deletions have been detected.")
            self.preview.load("", "html")

    def _on_selected(self, item, _previous):
        if item is None:
            return
        cp = self.db.get_recovery_checkpoint(item.data(Qt.UserRole))   # the one body
        self._current = cp
        if cp is None:
            return
        paragraphs = (f", {cp.removed_paragraphs:,} of {cp.prior_paragraphs:,} paragraphs"
                      if cp.removed_paragraphs else "")
        self.details.setText(
            f"<b>{self._label_for(cp)}</b><br>"
            f"Detected {_when(cp.created_at)}: a save removed {cp.removed_chars:,} of "
            f"{cp.prior_chars:,} characters{paragraphs}; {cp.remaining_chars:,} characters "
            f"remained. Shown below is the text as it was just before.")
        if cp.scope == "date":
            self.preview.set_storage_subdir(cp.ref[:7].replace("-", "/"))
        else:
            self.preview.set_storage_subdir(f"projects/{cp.ref}")
        self.preview.load(cp.body, cp.body_format)

    def _request_restore(self):
        if self._current is None:
            return
        answer = QMessageBox.question(
            self, "Restore recovered text",
            f"Replace the current {('entry' if self._current.scope == 'date' else 'project')} "
            "with this recovered text?\n\nWhat it holds now is kept first (as a previous "
            "version), so this can be undone.",
            QMessageBox.Yes | QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.restoreRequested.emit(self._current.id)

    def _copy(self):
        if self._current is None:
            return
        # Copied by the preview editor itself, which holds exactly this text:
        # Qt builds the clipboard data (formatted and plain) natively. A
        # QMimeData built in Python and handed to the clipboard crashes the
        # process at exit under PySide (reproduced), so none is made here.
        edit = self.preview.text_edit
        cursor = edit.textCursor()
        edit.selectAll()
        edit.copy()
        cursor.clearSelection()
        edit.setTextCursor(cursor)

    def _delete(self):
        if self._current is None:
            return
        answer = QMessageBox.warning(
            self, "Delete recovery copy",
            "Permanently delete this recovery copy? The entry or project itself is "
            "not changed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.db.delete_recovery_checkpoint(self._current.id)
        self.reload()
