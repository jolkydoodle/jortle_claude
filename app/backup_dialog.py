"""The windows for backups and encryption.

  UnlockDialog           at startup, when the journal database is encrypted
  NewPassphraseDialog    setting up database or backup encryption, or
                         changing either passphrase
  StorageChoiceDialog    the one-time "how should backups be stored?" question
  PausedBackupsDialog    at launch, at most once a day, while backups are paused
  BackupsSecurityDialog  File → Backups & Security… (and Settings → Manage…)

These windows only collect choices and show state. The work — and every
safety check — is in security.py and backup.py, and the main window runs the
operations that must close and reopen the database (MainWindow._reopen_database).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget
)

from . import backup, security
from .paths import DISPLAY_NAME, get_data_dir
from .ui_util import font_scaled, make_shrinkable_combo

MIN_PASSPHRASE = 8

DATABASE, BACKUPS = "database", "backups"

LOSS_WARNINGS = {
    DATABASE: ("If you forget this passphrase, your encrypted journal database cannot "
               "be opened — not by you, not by jortle_claude, and not by anyone else. "
               "There is no reset. Write it down and keep it somewhere safe. (Backups "
               "have their own passphrase, so a backup can still bring your writing back.)"),
    BACKUPS: ("If you forget this passphrase, your encrypted backups cannot be opened — "
              "not by you, not by jortle_claude, and not by anyone else. There is no "
              "reset. Write it down and keep it somewhere safe."),
}
# The phrase for each thing in running text.
NAMES = {DATABASE: "the database", BACKUPS: "backups"}

UNENCRYPTED_COPIES_WARNING = (
    "Unencrypted copies will be kept next to what is encrypted: a copy of the "
    "journal database (if it is encrypted), and an unencrypted .zip beside every "
    "encrypted backup (if backups are encrypted). Anyone who can open those files "
    "can read your journal: they are not protected by any passphrase, and "
    "jortle_claude will open without asking for one while this is on.\n\n"
    "Your encrypted files are not changed. Turning this off later deletes the "
    "unencrypted copies again."
)


def _wrap_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def _when(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def busy(fn):
    """Runs `fn` with the wait cursor showing."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    QApplication.processEvents()
    try:
        return fn()
    finally:
        QApplication.restoreOverrideCursor()


class _FitsText(QDialog):
    """A dialog tall enough for its word-wrapped text at the current width.

    Qt does not use a layout's height-for-width when sizing a top-level
    window, so wrapped labels in these small dialogs were cut off at larger
    interface font sizes (seen in screenshots). This asks the layout how tall
    it needs to be for the actual width, and grows to that."""

    def _fit(self):
        layout = self.layout()
        if layout is None or not layout.hasHeightForWidth():
            return
        needed = layout.totalHeightForWidth(self.width())
        if needed > 0:
            self.setMinimumHeight(needed)
            if needed > self.height():
                self.resize(self.width(), needed)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def _open_for_font(self, base_width: int = 480):
        """Wider at a larger interface font, up to the screen, and as tall as
        its text needs at that width. At 24 pt a 480 px window wrapped its
        text taller than a small screen, and Qt's own size hint measures the
        text at a narrower width than the window opens at, so the spare
        height showed as gaps between paragraphs (4A-43; bug 45). "The
        screen" is the one the parent window is on — where the dialog opens —
        not the primary screen (4A2/AM-8)."""
        width = font_scaled(base_width)
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            window = parent.window()
            screen = QGuiApplication.screenAt(window.frameGeometry().center()) or window.screen()
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            width = min(width, screen.availableGeometry().width() - 40)
        width = max(base_width, width)
        self.setMinimumWidth(width)
        self.resize(width, self.layout().totalHeightForWidth(width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


# ------------------------------------------------------------------ unlock
class UnlockDialog(_FitsText):
    """Asks for the database passphrase until it is right, or the user quits."""

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.setWindowTitle(f"{DISPLAY_NAME} — Unlock")
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.Password)
        self.passphrase.setPlaceholderText("Database passphrase")
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)
        info = _wrap_label("Your journal database is encrypted. Enter its passphrase to open it.")
        help_text = _wrap_label(
            "Forgotten it? Without the passphrase the journal cannot be decrypted. "
            f"{security.recovery_doc_path()} describes every file and what can "
            "still be done.")
        help_text.setStyleSheet("color: gray;")
        buttons = QDialogButtonBox()
        self.unlock_button = buttons.addButton("Unlock", QDialogButtonBox.AcceptRole)
        buttons.addButton("Quit", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._try)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for w in (info, self.passphrase, self.error, help_text, buttons):
            layout.addWidget(w)
        self.setMinimumWidth(420)

    def _try(self):
        text = self.passphrase.text()
        if not text:
            self.error.setText("Enter the passphrase.")
            return
        self.error.setText("Unlocking…")
        self.unlock_button.setEnabled(False)
        try:
            busy(lambda: security.unlock(self.data_dir, text))
        except security.WrongPassphrase:
            self.error.setText("That passphrase is not correct. Try again.")
            self.passphrase.selectAll()
            self.passphrase.setFocus()
            return
        except security.SecurityError as exc:
            self.error.setText(str(exc))
            return
        finally:
            self.unlock_button.setEnabled(True)
        self.accept()


class NewPassphraseDialog(_FitsText):
    """A new passphrase for the database or for backups, typed twice, with the
    loss warning acknowledged.

    `ask_current`: also asks for the current passphrase (changing it).
    `same_as` + `verify_same`: offers "Use the same passphrase as <the other>",
    for when the other one is already set up; `verify_same(text)` must return
    True for the other thing's passphrase. The two are separate by default.
    """

    def __init__(self, title: str, intro: str, subject: str = DATABASE,
                 ask_current: bool = False, same_as: Optional[str] = None,
                 verify_same: Optional[Callable[[str], bool]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.subject = subject
        self.verify_same = verify_same
        self.same_as = same_as
        self.current = QLineEdit() if ask_current else None
        self.first = QLineEdit()
        self.second = QLineEdit()
        form = self._form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        if self.current is not None:
            self.current.setEchoMode(QLineEdit.Password)
            form.addRow("Current passphrase:", self.current)
        for field in (self.first, self.second):
            field.setEchoMode(QLineEdit.Password)
        self.first_label = QLabel("New passphrase:")
        self.second_label = QLabel("Type it again:")
        form.addRow(self.first_label, self.first)
        form.addRow(self.second_label, self.second)
        self.same = None
        if same_as and verify_same is not None:
            # Short: a check box's text does not wrap.
            self.same = QCheckBox(f"Same passphrase as {NAMES[same_as]}")
            self.same.toggled.connect(lambda on: self._same_toggled(on, same_as))
        self.understand = QCheckBox("I understand this warning")
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(_wrap_label(intro))
        if self.same is not None:
            layout.addWidget(self.same)
        layout.addLayout(form)
        warning = _wrap_label(LOSS_WARNINGS[subject])
        warning.setStyleSheet("font-weight: bold;")
        layout.addWidget(warning)
        layout.addWidget(self.understand)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)

    def _same_toggled(self, on: bool, other: str):
        self.first_label.setText(f"Passphrase of {NAMES[other]}:" if on else "New passphrase:")
        self.second.setVisible(not on)
        self.second_label.setVisible(not on)
        self.error.setText("")

    def uses_same(self) -> bool:
        return self.same is not None and self.same.isChecked()

    def _check(self):
        if self.uses_same():
            if not busy(lambda: self.verify_same(self.first.text())):
                self.error.setText(f"That is not the passphrase of {NAMES[self.same_as]}.")
                return
        else:
            if len(self.first.text()) < MIN_PASSPHRASE:
                self.error.setText(f"Use at least {MIN_PASSPHRASE} characters. "
                                   "A few unrelated words is easy to remember and hard to guess.")
                return
            if self.first.text() != self.second.text():
                self.error.setText("The two passphrases are different.")
                return
        if not self.understand.isChecked():
            self.error.setText("Tick the box to confirm you have read the warning.")
            return
        self.accept()

    def value(self) -> str:
        return self.first.text()

    def current_value(self) -> str:
        return self.current.text() if self.current is not None else ""


class StorageChoiceDialog(_FitsText):
    """Asked at launch until answered. Closing it answers nothing: the
    question comes back next time, and automatic backups wait meanwhile."""

    ENCRYPTED, UNENCRYPTED, LATER = "encrypted", "unencrypted", "later"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How should your backups be stored?")
        self.choice = self.LATER
        layout = QVBoxLayout(self)
        layout.addWidget(_wrap_label(
            "jortle_claude now makes backups automatically, once a day, into "
            "a folder outside its data folder:"))
        folder = QLineEdit(str(backup.backup_dir()))
        folder.setReadOnly(True)
        layout.addWidget(folder)
        layout.addWidget(_wrap_label(
            "Backups can be encrypted with a backup passphrase. An encrypted backup "
            "can only be opened with that passphrase, which also means that if you "
            "lose it, nobody can open it. (Encrypting the journal database itself is "
            "a separate choice, in File → Backups & Security.)"))
        # One button per line: side by side they are cut off at large sizes.
        for label, choice in (("Encrypt backups… (recommended)", self.ENCRYPTED),
                              ("Keep backups unencrypted", self.UNENCRYPTED)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, c=choice: self._choose(c))
            layout.addWidget(button)
        layout.addWidget(_wrap_label(
            "Until you choose, automatic backups wait. You can change your mind later "
            "in File → Backups & Security."))
        self._open_for_font()

    def _choose(self, choice: str):
        self.choice = choice
        self.accept()


UNENCRYPTED_BACKUPS_MEANING = (
    "New backups will be ordinary .zip files: anyone who can open the backup folder "
    "can read them. Your journal database's own encryption, if it has any, is not "
    "changed, and backups already made are not changed. You can switch to encrypted "
    "backups later in File → Backups & Security.")


def last_backup_line(data_dir: Path) -> str:
    """When the last checked backup was made — never "never" when backups
    exist outside the backup folder (4A-43/AM-2): an older version kept them
    inside the data folder, and those are mentioned instead."""
    st = backup.status(data_dir)
    if st.last_success:
        return f"The last checked backup in the backup folder was made on {_when(st.last_success)}."
    line = "There is no backup in the backup folder yet."
    legacy = backup.legacy_backup_dirs(data_dir)
    if legacy:
        line += (" Older backups, made by an earlier version, are in "
                 f"{', '.join(str(p) for p in legacy)}.")
    return line


class PausedBackupsDialog(_FitsText):
    """Shown at launch, at most once a day, while backups are paused
    (Master Spec §46.2; backup_reminder.py). It only collects the choice:
    the main window runs the same commands as Backups & Security. Closing
    it, Escape and Not now choose nothing."""

    PASSPHRASE, UNENCRYPTED, LATER = "passphrase", "unencrypted", "later"

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Backups are paused")
        self.choice = self.LATER
        layout = QVBoxLayout(self)
        reason = security.backups_paused_reason(data_dir) or ""
        layout.addWidget(_wrap_label(f"No backups are being made. {reason}"))
        layout.addWidget(_wrap_label(last_backup_line(data_dir)))
        layout.addWidget(_wrap_label(f"Use unencrypted backups: {UNENCRYPTED_BACKUPS_MEANING}"))
        # One button per line: side by side they are cut off at large sizes.
        self.buttons = {}
        for label, choice in (("Set a backup passphrase…", self.PASSPHRASE),
                              ("Use unencrypted backups", self.UNENCRYPTED),
                              ("Not now", self.LATER)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, c=choice: self._choose(c))
            layout.addWidget(button)
            self.buttons[choice] = button
        self._open_for_font()

    def _choose(self, choice: str):
        self.choice = choice
        self.accept()


def confirm_restore(parent, text: str) -> tuple:
    """The restore warning, with its "Don't show this warning again" box.
    Returns (go ahead, don't show again)."""
    box = QMessageBox(QMessageBox.Warning, "Restore backup", text,
                      QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No)
    dont_show = QCheckBox("Don't show this warning again")
    box.setCheckBox(dont_show)
    return box.exec() == QMessageBox.Yes, dont_show.isChecked()


def ask_passphrase(parent, title: str, text: str) -> Optional[str]:
    field_dialog = _FitsText(parent)
    field_dialog.setWindowTitle(title)
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.Password)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(field_dialog.accept)
    buttons.rejected.connect(field_dialog.reject)
    layout = QVBoxLayout(field_dialog)
    layout.addWidget(_wrap_label(text))
    layout.addWidget(edit)
    layout.addWidget(buttons)
    field_dialog.setMinimumWidth(420)
    if field_dialog.exec() != QDialog.Accepted:
        return None
    return edit.text()


# ------------------------------------------------------ Backups & Security
class BackupsSecurityDialog(QDialog):
    """Everything §46.5 asks to be visible, and the controls that go with it.

    `actions` supplies the operations that live in the main window because
    they close and reopen the database or need its editors saved first:
      back_up_now(), set_up_database_encryption(), set_up_backup_encryption(),
      change_database_passphrase(), change_backup_passphrase(),
      keep_backups_unencrypted(), set_keep_copies(bool) -> bool
    """

    def __init__(self, actions: dict, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.data_dir = get_data_dir()
        self.setWindowTitle("Backups & Security")

        # --- backups
        # A read-only field rather than a label: a path has no spaces to wrap
        # at, and a label would force the window wider than a small screen.
        # The field scrolls, and its text can be selected and copied intact.
        self.folder_label = QLineEdit("")
        self.folder_label.setReadOnly(True)
        change_folder = QPushButton("Change…")
        change_folder.clicked.connect(self._change_folder)
        default_folder = QPushButton("Use Default")
        default_folder.clicked.connect(self._default_folder)

        self.status_label = _wrap_label("")
        self.automatic_combo = make_shrinkable_combo(QComboBox(), visible_chars=6)
        self.automatic_combo.addItem("Once a day, while jortle_claude is open", "daily")
        self.automatic_combo.addItem("Off", "off")
        self.automatic_combo.currentIndexChanged.connect(self._automatic_changed)

        self.retention_combo = make_shrinkable_combo(QComboBox(), visible_chars=6)
        self.retention_combo.addItem("Keep all backups", None)
        self.retention_combo.addItem("Keep only the newest automatic backups", "n")
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 999)
        self.retention_spin.setValue(30)
        self.retention_combo.currentIndexChanged.connect(self._retention_changed)
        self.retention_spin.valueChanged.connect(self._retention_changed)
        retention_row = QHBoxLayout()
        retention_row.addWidget(self.retention_combo, 1)
        retention_row.addWidget(self.retention_spin)
        retention_note = _wrap_label(
            "Only automatic backups that jortle_claude made and checked are ever "
            "deleted. Manual backups, the newest checked backup, copies you made "
            "yourself, and anything that could not be checked are kept.")
        retention_note.setStyleSheet("color: gray;")

        self.back_up_button = QPushButton("Back Up Now")
        self.back_up_button.clicked.connect(self._back_up_now)

        # A plain column rather than a QFormLayout: word-wrapped labels in a
        # form row get too little height and are cut off (seen in the first
        # screenshots of this window), and every line here matters.
        backups_box = QGroupBox("Backups")
        blayout = QVBoxLayout(backups_box)
        blayout.addWidget(self._heading("Folder"))
        blayout.addWidget(self.folder_label)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(change_folder)
        buttons_row.addWidget(default_folder)
        buttons_row.addStretch(1)
        blayout.addLayout(buttons_row)
        self.same_drive_label = _wrap_label("")
        self.same_drive_label.setStyleSheet("color: gray;")
        blayout.addWidget(self.same_drive_label)
        blayout.addWidget(self._heading("Status"))
        blayout.addWidget(self.status_label)
        automatic_row = QHBoxLayout()
        automatic_row.addWidget(QLabel("Automatic backups:"))
        automatic_row.addWidget(self.automatic_combo, 1)
        blayout.addLayout(automatic_row)
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Keep:"))
        keep_row.addLayout(retention_row, 1)
        blayout.addLayout(keep_row)
        blayout.addWidget(retention_note)
        now_row = QHBoxLayout()
        now_row.addWidget(self.back_up_button)
        now_row.addStretch(1)
        blayout.addLayout(now_row)

        # --- encryption: two independent parts, each with its own passphrase
        self.db_label = _wrap_label("")
        self.db_setup_button = QPushButton("Encrypt the Database…")
        self.db_setup_button.clicked.connect(lambda: self._act("set_up_database_encryption"))
        self.db_change_button = QPushButton("Change Database Passphrase…")
        self.db_change_button.clicked.connect(lambda: self._act("change_database_passphrase"))
        db_buttons = QHBoxLayout()
        db_buttons.addWidget(self.db_setup_button)
        db_buttons.addWidget(self.db_change_button)
        db_buttons.addStretch(1)

        self.backup_enc_label = _wrap_label("")
        self.backup_setup_button = QPushButton("Encrypt Backups…")
        self.backup_setup_button.clicked.connect(lambda: self._act("set_up_backup_encryption"))
        self.backup_plain_button = QPushButton("Keep Backups Unencrypted")
        self.backup_plain_button.clicked.connect(lambda: self._act("keep_backups_unencrypted"))
        self.backup_change_button = QPushButton("Change Backup Passphrase…")
        self.backup_change_button.clicked.connect(lambda: self._act("change_backup_passphrase"))
        backup_buttons = QHBoxLayout()
        backup_buttons.addWidget(self.backup_setup_button)
        backup_buttons.addWidget(self.backup_plain_button)
        backup_buttons.addWidget(self.backup_change_button)
        backup_buttons.addStretch(1)

        self.keep_copies = QCheckBox("Keep unencrypted copies")
        self.keep_copies.setToolTip(UNENCRYPTED_COPIES_WARNING)
        self.keep_copies.clicked.connect(self._keep_copies_clicked)
        self.copies_label = _wrap_label("")
        self.leftovers_label = _wrap_label("")
        self.leftovers_label.setStyleSheet("color: gray;")
        enc_box = QGroupBox("Encryption")
        elayout = QVBoxLayout(enc_box)
        elayout.addWidget(self._heading("Journal database"))
        elayout.addWidget(self.db_label)
        elayout.addLayout(db_buttons)
        elayout.addWidget(self._heading("Backups"))
        elayout.addWidget(self.backup_enc_label)
        elayout.addLayout(backup_buttons)
        elayout.addWidget(self.keep_copies)
        elayout.addWidget(self.copies_label)
        elayout.addWidget(self.leftovers_label)
        # Kept as names for callers that look for "the" buttons.
        self.setup_button = self.db_setup_button
        self.change_button = self.db_change_button
        self.encryption_label = self.db_label

        # --- restore
        self.restore_warning = QCheckBox("Warn before restoring a backup")
        self.restore_warning.toggled.connect(
            lambda on: security.update_config(self.data_dir, restore_warning=bool(on)))
        restore_note = _wrap_label(
            "Turning the warning off never turns off the checks: a backup is always "
            "checked before it is used, and your current journal is always set aside "
            "first.")
        restore_note.setStyleSheet("color: gray;")
        restore_box = QGroupBox("Restoring")
        rlayout = QVBoxLayout(restore_box)
        rlayout.addWidget(self.restore_warning)
        rlayout.addWidget(restore_note)

        content = QWidget()
        clayout = QVBoxLayout(content)
        clayout.addWidget(backups_box)
        clayout.addWidget(enc_box)
        clayout.addWidget(restore_box)
        clayout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # Everything wraps or shrinks, so the content always fits the width;
        # only the height scrolls.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(close)
        self.setMinimumSize(460, 360)
        self.resize(620, 640)
        self.refresh()

    # ------------------------------------------------------------- display
    def refresh(self):
        cfg = security.load_config(self.data_dir)
        st = backup.status(self.data_dir)
        db_enc = security.is_encrypted_install(self.data_dir)
        backups_enc = security.backups_encrypted(self.data_dir)
        paused = security.backups_paused_reason(self.data_dir)
        keep = bool(cfg.get("keep_unencrypted_copies")) and (db_enc or backups_enc)
        available = security.available()

        self.folder_label.setText(str(st.folder))
        self.same_drive_label.setText(
            "This folder is on the same drive as your journal, so it does not protect "
            "against that drive failing. Consider a folder on another drive, or one "
            "that is synced elsewhere." if st.same_device else "")
        self.same_drive_label.setVisible(bool(st.same_device))

        if st.last_success:
            kind = "encrypted" if st.last_encrypted else "unencrypted"
            if not st.last_verified:
                checked = "NOT checked"
            elif st.last_verification == backup.VERIFIED_WRITTEN:
                checked = ("checked before encryption and read back from disk; enter the "
                           "backup passphrase for a full check")
            else:
                checked = "checked"
            lines = [f"Last successful backup: {_when(st.last_success)} ({kind}, {checked})."]
        else:
            lines = ["No backup has been made yet."]
        if st.paused_reason:
            lines.append("Automatic backups are PAUSED. " + st.paused_reason)
        elif st.overdue:
            lines.append("An automatic backup is overdue.")
        if paused:
            lines.append("New backups: none until this is resolved.")
        else:
            lines.append("New backups are "
                         + ("encrypted, with unencrypted copies." if backups_enc and keep else
                            "encrypted." if backups_enc else "NOT encrypted."))
        if st.last_error:
            lines.append(f"The last attempt failed: {st.last_error}")
        old = backup.legacy_backup_dirs(self.data_dir)
        if old:
            lines.append(f"Backups made by earlier versions are still in {old[0]}.")
        self.status_label.setText("\n".join(lines))

        self._set_combo(self.automatic_combo, cfg.get("automatic_backups", "daily"))
        keep_n = cfg.get("keep_automatic")
        self.retention_combo.blockSignals(True)
        self.retention_spin.blockSignals(True)
        self.retention_combo.setCurrentIndex(1 if keep_n else 0)
        if keep_n:
            self.retention_spin.setValue(int(keep_n))
        self.retention_spin.setEnabled(bool(keep_n))
        self.retention_combo.blockSignals(False)
        self.retention_spin.blockSignals(False)

        if not available:
            self.db_label.setText("Encryption is not available in this copy of jortle_claude.")
        elif db_enc:
            self.db_label.setText(
                "Encrypted with the database passphrase, which jortle_claude asks for when "
                "it starts. Photos in the data folder's attachments folder are not "
                "encrypted (inside encrypted backups they are).")
        else:
            self.db_label.setText(
                "Not encrypted: anyone who can open the data folder can read the journal.")
        self.db_setup_button.setVisible(available and not db_enc)
        self.db_change_button.setVisible(db_enc)

        if not available:
            self.backup_enc_label.setText("")
        elif backups_enc:
            self.backup_enc_label.setText(
                "Encrypted with the backup passphrase. Making a backup never needs it; "
                "restoring one does.")
        elif paused:
            self.backup_enc_label.setText(
                "Encrypted backups were chosen, but no backup passphrase has been set, "
                "so no backups are being made. Set one, or keep backups unencrypted.")
        elif cfg.get("storage_choice") is None:
            self.backup_enc_label.setText(
                "Not chosen yet: automatic backups wait until you choose. Backups you "
                "make yourself are not encrypted.")
        else:
            self.backup_enc_label.setText(
                "Not encrypted: anyone who can open a backup can read the journal.")
        self.backup_setup_button.setVisible(available and not backups_enc)
        self.backup_plain_button.setVisible(
            not backups_enc and cfg.get("storage_choice") != "unencrypted")
        self.backup_change_button.setVisible(backups_enc)

        self.keep_copies.setVisible(db_enc or backups_enc)
        self.keep_copies.setChecked(keep)
        if keep:
            parts = []
            if db_enc:
                parts.append("journal.unencrypted.db in the data folder")
            if backups_enc:
                parts.append("a .zip beside each encrypted backup")
            self.copies_label.setText("Unencrypted copies are being kept: " + " and ".join(parts)
                                      + ". They are readable without any passphrase.")
        self.copies_label.setVisible(keep)

        leftovers = security.unencrypted_leftovers(self.data_dir) if db_enc else []
        plain_only = []
        if backups_enc:
            groups = backup._twins_by_id(backup.scan_backups(st.folder))
            plain_only = [r.file for g in groups.values() if not g["encrypted"]
                          for r in g["plain"]]
        if leftovers or plain_only:
            parts = ["Unencrypted copies that encryption does not cover (jortle_claude "
                     "will not delete these; delete them yourself if you want them gone):"]
            parts += [f"• {what}: {where}" for what, where in leftovers]
            parts += [f"• Unencrypted backup: {name}" for name in plain_only]
            self.leftovers_label.setText("\n".join(parts))
            self.leftovers_label.show()
        else:
            self.leftovers_label.hide()

        self.restore_warning.blockSignals(True)
        self.restore_warning.setChecked(bool(cfg.get("restore_warning", True)))
        self.restore_warning.blockSignals(False)

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    @staticmethod
    def _set_combo(combo: QComboBox, value):
        combo.blockSignals(True)
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    # ------------------------------------------------------------ changes
    def _change_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the backup folder", str(backup.backup_dir(self.data_dir)))
        if not chosen:
            return
        chosen_path = Path(chosen).resolve()
        data = self.data_dir.resolve()
        if chosen_path == data or data in chosen_path.parents:
            QMessageBox.warning(
                self, "Backup folder",
                "Choose a folder outside jortle_claude's data folder, so the backups "
                "survive if that folder is lost or replaced.")
            return
        security.update_config(self.data_dir, backup_dir=str(chosen_path))
        backup.copy_key_to_backup_folder(self.data_dir)
        self.refresh()

    def _default_folder(self):
        security.update_config(self.data_dir, backup_dir=None)
        backup.copy_key_to_backup_folder(self.data_dir)
        self.refresh()

    def _automatic_changed(self, _index):
        security.update_config(self.data_dir,
                               automatic_backups=self.automatic_combo.currentData())
        self.refresh()

    def _retention_changed(self, *_):
        if self.retention_combo.currentData() is None:
            security.update_config(self.data_dir, keep_automatic=None)
        else:
            security.update_config(self.data_dir, keep_automatic=self.retention_spin.value())
        self.refresh()

    def _back_up_now(self):
        self.actions["back_up_now"]()
        self.refresh()

    def _act(self, name: str):
        self.actions[name]()
        self.refresh()

    def _keep_copies_clicked(self, checked: bool):
        self.actions["set_keep_copies"](checked)
        self.refresh()
