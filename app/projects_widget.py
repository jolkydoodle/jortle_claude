"""Projects: long-form or sustained work that isn't tied to one date (a
novel, a thesis chapter, a research line).

A project tracks:
  - when it was created,
  - every calendar day it was worked on (project_sessions),
  - a version history (project_versions): one automatic snapshot at the
    start of each new work day, plus any number of manual, labeled
    checkpoints you save yourself, and
  - where it lives in a tree of user-created folders (Group 3, Master Spec
    §40) — "Courses → Fall 2026 → CHEM 173A", "Research → GaN → EES",
    whatever the user makes. Folders and projects are referred to by id
    everywhere, so renaming or moving either never breaks a reference
    (Reader's Notes, versions, recovery copies, archive pages). Projects
    without a folder are shown under "Uncategorized" at the end.

Moving is one command (`move_to`), reached from "Move to…" (the context
menu and the button beside the folder path) and from dragging in the tree.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)

from .database import Database, FolderNotEmpty, InvalidMove
from .reader_notes_widget import ReaderNotesWidget
from .rich_editor import RichEditor
from .entry_history import stored_state
from .saving import CANCEL, SAVE, ask_unsaved, autosave_enabled
from .ui_util import side_pane_width

AUTOSAVE_INTERVAL_MS = 1500
# The same key MainWindow._save_window_state writes at close.
PROJECTS_SPLITTER_SETTING = "splitter_projects"
# Folders the user closed in the tree, as a comma list of folder ids ("root"
# = Uncategorized). Everything else is open, so a new folder starts open
# (Group 3 fixes, D1).
COLLAPSED_FOLDERS_SETTING = "projects_collapsed_folders"


def _fmt(iso_timestamp: str) -> str:
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
    except ValueError:
        return iso_timestamp


def _fmt_date(iso_date: str) -> str:
    try:
        return datetime.fromisoformat(iso_date).strftime("%b %d, %Y")
    except ValueError:
        return iso_date


class VersionPreviewDialog(QDialog):
    restoreRequested = Signal()

    def __init__(self, title: str, content_md: str, content_format: str, storage_subdir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 500)

        browser = RichEditor(read_only=True)
        browser.set_storage_subdir(storage_subdir)
        browser.load(content_md, content_format)

        restore_btn = QPushButton("Restore This Version")
        restore_btn.clicked.connect(self._on_restore)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(restore_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addLayout(btn_row)

    def _on_restore(self):
        self.restoreRequested.emit()
        self.accept()


FOLDER_ROLE = Qt.UserRole + 1     # on folder items: the folder id; on "Uncategorized": ROOT
ROOT = "root"
UNCATEGORIZED = "Uncategorized"


def ask_name(parent, title: str, label: str, text: str = ""):
    """(text, accepted) — a module function so tests can answer it."""
    return QInputDialog.getText(parent, title, label, text=text)


def notify(parent, title: str, text: str):
    """An information message — a module function so tests can see it."""
    QMessageBox.information(parent, title, text)


class FolderPicker(QDialog):
    """Pick a destination folder: the folder tree plus the top level."""

    def __init__(self, db: Database, title: str, top_label: str, exclude: int | None = None,
                 current: int | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        top = QTreeWidgetItem([top_label])
        top.setData(0, FOLDER_ROLE, ROOT)
        self.tree.addTopLevelItem(top)
        excluded = set()
        folders = db.list_folders()
        if exclude is not None:
            # A folder can't go inside itself or anything inside it.
            excluded = {exclude}
            grew = True
            while grew:
                grew = False
                for f in folders:
                    if f.parent_id in excluded and f.id not in excluded:
                        excluded.add(f.id)
                        grew = True
        items = {}
        for f in folders:
            if f.id in excluded:
                continue
            items[f.id] = QTreeWidgetItem([Database.folder_label(f.name)])
            items[f.id].setData(0, FOLDER_ROLE, f.id)
        for f in folders:
            if f.id not in items:
                continue
            parent_item = items.get(f.parent_id)
            (parent_item.addChild if parent_item else self.tree.addTopLevelItem)(items[f.id])
        self.tree.expandAll()
        self.tree.setCurrentItem(items.get(current, top))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)
        self.resize(360, 420)

    def chosen(self):
        item = self.tree.currentItem()
        value = item.data(0, FOLDER_ROLE) if item else ROOT
        return None if value == ROOT else value


def choose_folder(parent, db, title, top_label, exclude=None, current=None):
    """(accepted, folder id or None for the top level) — a seam for tests."""
    picker = FolderPicker(db, title, top_label, exclude, current, parent)
    if picker.exec() != QDialog.Accepted:
        return False, None
    return True, picker.chosen()


class ProjectTree(QTreeWidget):
    """The folder / project tree. Dragging an item onto a folder (or onto
    "Uncategorized", or empty space) asks to move it there; the tree itself
    is never rearranged by Qt — the move goes through ProjectsWidget.move_to
    and the tree is rebuilt from the database, so what you see is always
    what is stored."""

    moveRequested = Signal(str, int, object)     # "folder"/"project", id, target folder or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    @staticmethod
    def describe(item):
        """("folder", id) / ("project", id) / ("root", None) / (None, None)."""
        if item is None:
            return None, None
        project_id = item.data(0, Qt.UserRole)
        if project_id is not None:
            return "project", project_id
        folder = item.data(0, FOLDER_ROLE)
        if folder == ROOT:
            return "root", None
        if folder is not None:
            return "folder", folder
        return None, None

    def target_folder_at(self, pos):
        """The folder a drop at `pos` means: the folder under the pointer,
        the folder of the project under it, or None (top level)."""
        item = self.itemAt(pos)
        kind, ident = self.describe(item)
        if kind == "folder":
            return ident
        if kind == "project":
            return self.describe(item.parent())[1] if item.parent() is not None and \
                self.describe(item.parent())[0] == "folder" else None
        return None

    def dropEvent(self, event):
        kind, ident = self.describe(self.currentItem())
        target = self.target_folder_at(event.position().toPoint())
        event.setDropAction(Qt.IgnoreAction)
        event.ignore()
        if kind in ("folder", "project"):
            # After the drag has finished: the move rebuilds this tree, which
            # must not happen while Qt's drag is still holding its items.
            QTimer.singleShot(0, lambda: self.moveRequested.emit(kind, ident, target))


class ProjectsWidget(QWidget):
    def __init__(self, db: Database, parent=None, history=None):
        super().__init__(parent)
        self.db = db
        # entry_history.EntryHistory, when the host provides one: projects get
        # recovery checkpoints for large deletions (Master Spec §44.5).
        self.history = history
        self.current_project_id: int | None = None
        self.showing_archived = False

        # Built before self.editor below (and its textChanged ->
        # _schedule_autosave connection) — see main_window.py's
        # MainWindow.__init__ for why: some widget setup can itself emit
        # textChanged even on an empty, freshly-built editor, which would
        # otherwise call _schedule_autosave() before this timer exists.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_tick)

        # ---- left: the folder / project tree -------------------------------------
        self.project_list = ProjectTree()
        self.project_list.currentItemChanged.connect(self._on_selection_changed)
        # Opening or closing a folder is remembered (D1). The tree's signals
        # are blocked while it is rebuilt, so a rebuild never records itself.
        self.project_list.itemExpanded.connect(lambda item: self._remember_folder_open(item, True))
        self.project_list.itemCollapsed.connect(lambda item: self._remember_folder_open(item, False))
        self.project_list.moveRequested.connect(
            lambda kind, ident, target: self.move_to(kind, ident, target))
        self.project_list.customContextMenuRequested.connect(self._on_tree_menu)

        new_btn = QPushButton("New Project")
        new_btn.clicked.connect(self._new_project)
        new_folder_btn = QPushButton("New Folder")
        new_folder_btn.setToolTip("A folder in the selected folder (or at the top level)")
        new_folder_btn.clicked.connect(lambda: self.new_folder(self._selected_folder()))
        new_row = QHBoxLayout()
        new_row.addWidget(new_btn)
        new_row.addWidget(new_folder_btn)

        self.archive_toggle = QCheckBox("Show archived")
        self.archive_toggle.toggled.connect(self._on_toggle_archived)

        self.archive_btn = QPushButton("Archive")
        self.archive_btn.clicked.connect(self._archive_current)
        self.restore_project_btn = QPushButton("Restore")
        self.restore_project_btn.clicked.connect(self._unarchive_current)
        self.delete_forever_btn = QPushButton("Delete Forever")
        self.delete_forever_btn.clicked.connect(self._delete_current_forever)

        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self.archive_btn)
        list_buttons.addWidget(self.restore_project_btn)
        list_buttons.addWidget(self.delete_forever_btn)

        # Below the project list: Reader's Notes, scoped to the open
        # project. Same widget class the Daily Journal uses, on the same
        # storage — Projects gets notes on one architecture rather than a
        # second one that would have to be kept in step by hand.
        self.reader_notes = ReaderNotesWidget(
            self.db, scope="project",
            hint="Definitions, concepts and references to keep beside this project.",
            placeholder=(
                "Characters:\n"
                "Marguerite — the narrator's sister, introduced in chapter 3.\n\n"
                "Continuity:\n"
                "The letter arrives before the storm, not after."
            ),
        )
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addLayout(new_row)
        left_layout.addWidget(self.project_list)
        left_layout.addWidget(self.archive_toggle)
        left_layout.addLayout(list_buttons)
        left_layout.addWidget(self.reader_notes, stretch=1)

        # ---- right: editor + metadata + version history -------------------------
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Untitled project")
        self.title_edit.editingFinished.connect(self._on_title_changed)

        # Where the project lives, and the one way to change it from here.
        self.folder_label = QLabel()
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.move_button = QPushButton("Move…")
        self.move_button.setToolTip("Move this project to another folder")
        self.move_button.clicked.connect(self._move_current_project)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        folder_row.addWidget(self.folder_label, stretch=1)
        folder_row.addWidget(self.move_button)

        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.meta_label.setObjectName("SubtleHint")  # sized by the app font setting, see theme.py

        self.editor = RichEditor()
        self.editor.textChanged.connect(self._schedule_autosave)

        save_version_btn = QPushButton("Save Version…")
        save_version_btn.clicked.connect(self._save_named_version)

        self.version_list = QListWidget()
        self.version_list.itemDoubleClicked.connect(self._preview_version)

        view_version_btn = QPushButton("View")
        view_version_btn.clicked.connect(lambda: self._preview_version(self.version_list.currentItem()))
        restore_version_btn = QPushButton("Restore Selected")
        restore_version_btn.clicked.connect(self._restore_selected_version)

        version_buttons = QHBoxLayout()
        version_buttons.addWidget(view_version_btn)
        version_buttons.addWidget(restore_version_btn)
        version_buttons.addStretch(1)
        version_buttons.addWidget(save_version_btn)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(self.title_edit)
        right_layout.addLayout(folder_row)
        right_layout.addWidget(self.meta_label)
        right_layout.addWidget(self.editor, stretch=3)
        right_layout.addWidget(QLabel("Version History (double-click to view):"))
        right_layout.addWidget(self.version_list, stretch=1)
        right_layout.addLayout(version_buttons)

        splitter = QSplitter()
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.splitter = splitter
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self._panes_sized = False
        self.rebalance_panes()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._set_editing_enabled(False)
        self.refresh_project_list()

    # ------------------------------------------------------------- utilities
    def apply_font(self, family: str, size: int):
        # The project's own writing surface only. Reader's Notes deliberately
        # stays on the application font here, exactly as it does beside the
        # journal — it's a reference area, not a second manuscript, and having
        # it silently follow the manuscript font would be a behavior change
        # the journal doesn't share.
        self.editor.set_font(family, size)

    def showEvent(self, event):
        """Sizes the panes every time this comes on screen. Repeatable: a
        remembered arrangement is restored rather than overwritten (D1) —
        and it has to be repeated, because a font change made while this tab
        was hidden may have pushed the list wider (to its minimum at the
        larger font) and left it there once the font shrank again (C2)."""
        super().showEvent(event)
        self._panes_sized = True
        self._apply_pane_sizes()

    def rebalance_panes(self):
        """Project list wide enough for its text at the current app font;
        the editor takes the rest. See main_window._rebalance_panes."""
        QTimer.singleShot(0, self._apply_pane_sizes)

    def _apply_pane_sizes(self):
        """The arrangement the user dragged (splitter_projects, saved the
        moment they drag), or the default proportions if there is none."""
        try:
            state = self.db.get_setting(PROJECTS_SPLITTER_SETTING)
        except Exception:  # noqa: BLE001 — closed under a deferred pass; keep defaults
            state = None
        if state:
            from PySide6.QtCore import QByteArray
            if self.splitter.restoreState(QByteArray.fromBase64(state.encode("ascii"))):
                return
        total = self.splitter.width() or self.width() or 1160
        left = side_pane_width(280, total, max_fraction=0.30)
        self.splitter.setSizes([left, max(280, total - left)])

    def _on_splitter_moved(self, _pos: int, _index: int):
        self.db.set_setting(PROJECTS_SPLITTER_SETTING,
                            bytes(self.splitter.saveState().toBase64()).decode("ascii"))

    def _set_editing_enabled(self, enabled: bool):
        self.title_edit.setEnabled(enabled)
        self.move_button.setEnabled(enabled)
        self.editor.setEnabled(enabled)

    # ---------------------------------------------------------------- list
    @staticmethod
    def _folder_key(item) -> str:
        folder = item.data(0, FOLDER_ROLE)
        return "root" if folder == ROOT else str(folder)

    def _collapsed_folders(self) -> set:
        try:
            text = self.db.get_setting(COLLAPSED_FOLDERS_SETTING) or ""
        except Exception:  # noqa: BLE001 — closed under a deferred pass
            return set()
        return {part for part in text.split(",") if part}

    def _remember_folder_open(self, item, is_open: bool):
        if item.data(0, FOLDER_ROLE) is None:      # a project, not a folder
            return
        closed = self._collapsed_folders()
        key = self._folder_key(item)
        if is_open:
            closed.discard(key)
        else:
            closed.add(key)
        existing = {str(f.id) for f in self.db.list_folders()} | {"root"}
        self.db.set_setting(COLLAPSED_FOLDERS_SETTING, ",".join(sorted(closed & existing)))

    def refresh_project_list(self, select_id: int | None = None, reload_editor: bool = True,
                             reveal: bool = False):
        """Rebuilds the folder tree from the database.

        `reveal` opens the folders around the selected project (and
        remembers them as open) — for a project the user has just made,
        moved or asked to see. An ordinary rebuild keeps the selection
        without opening anything, so a folder the user closed stays closed
        (Group 3 fixes, D1).

        Every folder is shown in the ordinary view (an empty one is somewhere
        to put things); the archived view shows only the folders leading to
        archived projects. Projects without a folder come last, under
        "Uncategorized".
        """
        self.project_list.blockSignals(True)
        self.project_list.clear()
        projects = self.db.list_projects(archived=self.showing_archived)
        folders = self.db.list_folders()
        by_id = {f.id: f for f in folders}
        if self.showing_archived:
            needed = set()
            for p in projects:
                folder_id = p.folder_id
                while folder_id is not None and folder_id not in needed and folder_id in by_id:
                    needed.add(folder_id)
                    folder_id = by_id[folder_id].parent_id
            folders = [f for f in folders if f.id in needed]

        folder_items = {}
        for f in folders:
            item = QTreeWidgetItem([Database.folder_label(f.name)])
            item.setData(0, FOLDER_ROLE, f.id)
            bold = item.font(0)
            bold.setBold(True)
            item.setFont(0, bold)
            item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            folder_items[f.id] = item
        for f in folders:
            parent_item = folder_items.get(f.parent_id)
            if parent_item is not None:
                parent_item.addChild(folder_items[f.id])
            else:
                self.project_list.addTopLevelItem(folder_items[f.id])

        loose = QTreeWidgetItem([UNCATEGORIZED])
        loose.setData(0, FOLDER_ROLE, ROOT)
        bold = loose.font(0)
        bold.setBold(True)
        loose.setFont(0, bold)
        loose.setFlags((loose.flags() | Qt.ItemIsDropEnabled) & ~Qt.ItemIsDragEnabled)

        selected_item = None
        for p in projects:          # most recently edited first, as before
            child = QTreeWidgetItem([p.title or "(untitled)"])
            child.setData(0, Qt.UserRole, p.id)
            child.setFlags((child.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            (folder_items.get(p.folder_id) or loose).addChild(child)
            if select_id is not None and p.id == select_id:
                selected_item = child
        if loose.childCount():
            self.project_list.addTopLevelItem(loose)
        closed = self._collapsed_folders()
        for item in folder_items.values():
            item.setExpanded(self._folder_key(item) not in closed)
        loose.setExpanded(self._folder_key(loose) not in closed)
        revealed = []
        if selected_item is not None:
            if reveal:
                parent = selected_item.parent()
                while parent is not None:
                    if not parent.isExpanded():
                        parent.setExpanded(True)
                        revealed.append(parent)
                    parent = parent.parent()
            # Without auto-scroll, selecting an item inside a closed folder
            # does not open it (QTreeView.scrollTo expands parents).
            auto_scroll = self.project_list.hasAutoScroll()
            self.project_list.setAutoScroll(reveal and auto_scroll)
            self.project_list.setCurrentItem(selected_item)
            self.project_list.setAutoScroll(auto_scroll)
        self.project_list.blockSignals(False)
        # A folder opened to show the project is open as far as the user can
        # see, so it is remembered as open.
        for item in revealed:
            self._remember_folder_open(item, True)

        self.archive_btn.setVisible(not self.showing_archived)
        self.restore_project_btn.setVisible(self.showing_archived)
        self.delete_forever_btn.setVisible(self.showing_archived)

        if not reload_editor:
            # The caller (a move) knows the open project is unchanged; don't
            # reload the editor and lose the cursor and scroll position.
            if self.current_project_id is not None:
                self._refresh_folder_label(self.current_project_id)
            return

        if self.project_list.currentItem() is None:
            self._load_project(None)
        else:
            self._on_selection_changed(self.project_list.currentItem(), None)

    def _iter_items(self):
        stack = [self.project_list.topLevelItem(i) for i in range(self.project_list.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def item_for(self, kind: str, ident):
        for item in self._iter_items():
            if ProjectTree.describe(item) == (kind, ident):
                return item
        return None

    # ------------------------------------------------------------ folders
    def _selected_folder(self):
        """The folder a new item should go in: the selected folder, or the
        folder of the selected project; None for the top level."""
        item = self.project_list.currentItem()
        kind, ident = ProjectTree.describe(item)
        if kind == "folder":
            return ident
        if kind == "project" and item.parent() is not None:
            parent_kind, parent_id = ProjectTree.describe(item.parent())
            return parent_id if parent_kind == "folder" else None
        return None

    def new_folder(self, parent_id=None):
        name, ok = ask_name(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return None
        folder = self.db.create_folder(name, parent_id)
        self.refresh_project_list(select_id=self.current_project_id, reload_editor=False)
        item = self.item_for("folder", folder.id)
        if item is not None:
            self.project_list.blockSignals(True)
            self.project_list.setCurrentItem(item)
            self.project_list.blockSignals(False)
        return folder

    def rename_folder(self, folder_id: int) -> bool:
        folder = self.db.get_folder(folder_id)
        if folder is None:
            return False
        name, ok = ask_name(self, "Rename Folder", "Folder name:", folder.name)
        if not ok or not name.strip():
            return False
        self.db.rename_folder(folder_id, name)
        self.refresh_project_list(select_id=self.current_project_id, reload_editor=False)
        return True

    def delete_folder(self, folder_id: int) -> bool:
        """Only an empty folder can be deleted (decision G3-7) — nothing a
        user wrote ever goes with it."""
        try:
            self.db.delete_folder(folder_id)
        except FolderNotEmpty:
            notify(self, "Folder not empty",
                   "This folder still holds folders or projects (archived ones count too). "
                   "Move them out first; only an empty folder can be deleted.")
            return False
        self.refresh_project_list(select_id=self.current_project_id, reload_editor=False)
        return True

    def move_to(self, kind: str, ident: int, target) -> bool:
        """THE move: a folder under another folder (or to the top level), or
        a project into a folder (or to Uncategorized). Only the parent link
        changes; ids — and everything keyed by them — stay as they were.
        Used by Move to… and by dragging in the tree."""
        try:
            if kind == "folder":
                self.db.move_folder(ident, target)
            elif kind == "project":
                self.db.move_project(ident, target)
            else:
                return False
        except InvalidMove:
            notify(self, "Can't move there", "A folder can't be moved into itself or into "
                   "one of its own folders.")
            return False
        # A project the user moves is shown where it went.
        self.refresh_project_list(select_id=self.current_project_id, reload_editor=False,
                                  reveal=kind == "project" and ident == self.current_project_id)
        return True

    def ask_move(self, kind: str, ident: int) -> bool:
        """Move to…: pick a destination, then the one move command."""
        if kind == "folder":
            folder = self.db.get_folder(ident)
            ok, target = choose_folder(self, self.db, f"Move “{folder.name}” to", "Top level",
                                       exclude=ident, current=folder.parent_id)
        else:
            project = self.db.get_project(ident)
            ok, target = choose_folder(self, self.db, f"Move “{project.title}” to", UNCATEGORIZED,
                                       current=project.folder_id)
        return self.move_to(kind, ident, target) if ok else False

    def _move_current_project(self):
        if self.current_project_id is not None:
            self.ask_move("project", self.current_project_id)

    def _on_tree_menu(self, point):
        item = self.project_list.itemAt(point)
        kind, ident = ProjectTree.describe(item)
        menu = QMenu(self)
        if kind == "folder":
            menu.addAction("New Subfolder…", lambda: self.new_folder(ident))
            menu.addAction("Rename…", lambda: self.rename_folder(ident))
            menu.addAction("Move to…", lambda: self.ask_move("folder", ident))
            menu.addSeparator()
            menu.addAction("Delete Folder", lambda: self.delete_folder(ident))
        elif kind == "project":
            menu.addAction("Move to…", lambda: self.ask_move("project", ident))
        else:
            menu.addAction("New Folder…", lambda: self.new_folder(None))
        menu.exec(self.project_list.viewport().mapToGlobal(point))

    def _on_toggle_archived(self, checked: bool):
        self.showing_archived = checked
        self.refresh_project_list()

    def _on_selection_changed(self, current: QTreeWidgetItem | None, _previous):
        project_id = current.data(0, Qt.UserRole) if current else None
        if current is not None and project_id is None:
            return  # a folder became "current" (e.g. via arrow keys) — ignore it
        if project_id == self.current_project_id:
            return
        if not self.allow_leaving_project():
            # Cancelled: move the list's selection back to the project that
            # is actually open, or the two would disagree.
            self._reselect_current_project()
            return
        if autosave_enabled(self.db):
            self._save_content(automatic=True)  # flush a pending edit on the previously-open project
        self._load_project(project_id)

    def _reselect_current_project(self):
        self.project_list.blockSignals(True)
        item = self.item_for("project", self.current_project_id)
        if item is not None:
            self.project_list.setCurrentItem(item)
        self.project_list.blockSignals(False)

    # ------------------------------------------------------------- loading
    def _load_project(self, project_id: int | None):
        if self.history is not None and self.current_project_id is not None \
                and self.current_project_id != project_id:
            self.history.end_session("project", self.current_project_id, "")
        self.current_project_id = project_id
        # Reader's Notes follows the open project. set_ref() flushes the
        # previous project's notes before switching, so an edit made a
        # fraction of a second before clicking another project is kept —
        # and it is given the id as the association key, which is what makes
        # these the same notes system the journal uses rather than a parallel
        # one (Part 33).
        self.reader_notes.set_ref(project_id)
        if project_id is None:
            self.title_edit.setText("")
            self.folder_label.setText("")
            self.meta_label.setText("Select a project on the left, or create a new one.")
            self.editor.load("", "html")
            self.version_list.clear()
            self._set_editing_enabled(False)
            return

        project = self.db.get_project(project_id)
        if project is None:
            return
        self._set_editing_enabled(True)
        self.title_edit.blockSignals(True)
        self.title_edit.setText(project.title)
        self.title_edit.blockSignals(False)
        self._refresh_folder_label(project_id)
        self.editor.set_storage_subdir(f"projects/{project_id}")
        self.editor.load(project.content_md, project.content_format)
        self._refresh_meta(project_id)
        self._refresh_versions(project_id)
        if self.history is not None:
            self.history.begin_session("project", project_id, stored_state(
                project.content_md, project.content_format, project.content_text, project.title))

    def _refresh_folder_label(self, project_id: int):
        project = self.db.get_project(project_id)
        path = self.db.folder_path(project.folder_id) if project else []
        self.folder_label.setText(" / ".join(path) if path else UNCATEGORIZED)

    def _refresh_meta(self, project_id: int):
        project = self.db.get_project(project_id)
        sessions = self.db.get_project_sessions(project_id)
        created = _fmt_date(project.created_at[:10])
        if sessions:
            last_worked = _fmt_date(sessions[-1].work_date)
            day_count = len(sessions)
            day_word = "day" if day_count == 1 else "days"
            self.meta_label.setText(
                f"Created {created} · last worked {last_worked} · worked on {day_count} {day_word}"
            )
        else:
            self.meta_label.setText(f"Created {created} · no edits yet")

    def _refresh_versions(self, project_id: int):
        self.version_list.clear()
        for v in self.db.get_project_versions(project_id):
            when = _fmt(v.saved_at)
            if v.kind == "auto":
                text = f"{when} — Start of day (auto)"
            elif v.label:
                text = f"{when} — {v.label}"
            else:
                text = f"{when} — Saved version"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, v.id)
            self.version_list.addItem(item)

    # ---------------------------------------------------------------- edits
    def _schedule_autosave(self):
        if autosave_enabled(self.db):
            self._autosave_timer.start()

    def _autosave_tick(self):
        if autosave_enabled(self.db):
            self._save_content(automatic=True)

    def is_dirty(self) -> bool:
        return self.current_project_id is not None and self.editor.is_dirty()

    def stop_timers(self):
        """See ReaderNotesWidget.stop_timers — called on the way out."""
        self._autosave_timer.stop()
        self.reader_notes.stop_timers()

    def save_now(self) -> bool:
        """The manual save (Ctrl+S). Same path as autosave — see
        main_window._save_current_entry for why there is only one."""
        return self._save_content()

    def _save_content(self, automatic: bool = False) -> bool:
        """THE project save path. Explicit calls (Ctrl+S, Save Version…)
        write whenever the text differs from what is stored; automatic ones
        (timer, switching project, leaving the tab, closing, exports) only
        when the user actually edited it. Either way an unchanged project is
        not re-saved — which used to record a "worked on" day and a
        start-of-day version just for having the app open."""
        if self.current_project_id is None:
            return False
        if automatic and not self.editor.is_dirty():
            return False
        html, plain = self.editor.save()
        project = self.db.get_project(self.current_project_id)
        if project is None or project.content_md == html:
            self.editor.mark_clean()
            return False
        if self.history is not None:
            self.history.before_write(
                "project", self.current_project_id,
                stored_state(project.content_md, project.content_format,
                             project.content_text, project.title),
                html, plain, title=project.title)
        self.db.save_project_content(self.current_project_id, html, content_format="html", content_text=plain)
        if self.history is not None:
            self.history.after_write("project", self.current_project_id, html, "html", plain,
                                     title=project.title)
        self.editor.mark_clean()
        self._refresh_meta(self.current_project_id)
        self._refresh_versions(self.current_project_id)
        return True

    def allow_leaving_project(self) -> bool:
        """Save / Discard / Cancel before a project would be unloaded.

        Same contract as the journal's (Part 26), and silent for the same
        reasons: nothing to ask about with autosave on, or with no unsaved
        changes.
        """
        if autosave_enabled(self.db) or not self.is_dirty():
            return True
        answer = ask_unsaved(self, "this project")
        if answer == CANCEL:
            return False
        if answer == SAVE:
            return self._save_content()
        # Discard: reload the project from what is stored. The saved version
        # is never touched.
        self._load_project(self.current_project_id)
        return True

    def _on_title_changed(self):
        if self.current_project_id is None:
            return
        title = self.title_edit.text().strip() or "(untitled)"
        self.db.rename_project(self.current_project_id, title)
        current_item = self.project_list.currentItem()
        if current_item:
            current_item.setText(0, title)

    # ------------------------------------------------------------- actions
    def _new_project(self):
        title, ok = ask_name(self, "New Project", "Project title:")
        if not ok or not title.strip():
            return
        # In the folder that is selected (or the selected project's folder).
        project = self.db.create_project(title.strip(), folder_id=self._selected_folder())
        self.refresh_project_list(select_id=project.id, reveal=True)

    def _archive_current(self):
        if self.current_project_id is None:
            return
        self.db.set_project_archived(self.current_project_id, True)
        self.refresh_project_list()

    def _unarchive_current(self):
        if self.current_project_id is None:
            return
        self.db.set_project_archived(self.current_project_id, False)
        self.refresh_project_list()

    def _delete_current_forever(self):
        if self.current_project_id is None:
            return
        confirm = QMessageBox.warning(
            self, "Delete project permanently",
            "This permanently deletes this project, all its version history, "
            "and its work-day log. This cannot be undone.\n\nDelete forever?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self.db.delete_project(self.current_project_id)
        self.current_project_id = None
        self.refresh_project_list()

    def _save_named_version(self):
        if self.current_project_id is None:
            return
        self._save_content()  # make sure the version reflects the latest text
        label, ok = QInputDialog.getText(self, "Save Version", "Label for this checkpoint (optional):")
        if not ok:
            return
        project = self.db.get_project(self.current_project_id)
        self.db.add_version(self.current_project_id, project.content_md, label=label.strip() or None,
                             kind="manual", content_format=project.content_format,
                             content_text=project.content_text)
        self._refresh_versions(self.current_project_id)

    def _selected_version_id(self) -> int | None:
        item = self.version_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _preview_version(self, item: QListWidgetItem | None):
        if item is None or self.current_project_id is None:
            return
        version_id = item.data(Qt.UserRole)
        version = self.db.get_version(version_id)
        if version is None:
            return
        dlg = VersionPreviewDialog(
            item.text(), version.content_md, version.content_format,
            f"projects/{self.current_project_id}", self
        )
        dlg.restoreRequested.connect(lambda: self._restore_version(version_id))
        dlg.exec()

    def _restore_selected_version(self):
        version_id = self._selected_version_id()
        if version_id is None:
            QMessageBox.information(self, "No version selected", "Select a version from the list first.")
            return
        self._restore_version(version_id)

    def _restore_version(self, version_id: int):
        if self.current_project_id is None:
            return
        confirm = QMessageBox.question(
            self, "Restore version",
            "Restore this version? Your current text will itself be saved as "
            "a checkpoint first, so nothing is lost.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        restored = self.db.restore_project_version(self.current_project_id, version_id)
        if restored is not None:
            restored_content, restored_format = restored
            version = self.db.get_version(version_id)
            self.history.rebase_session("project", self.current_project_id, stored_state(
                restored_content, restored_format, version.content_text if version else ""))
            self.editor.load(restored_content, restored_format)
            self._refresh_meta(self.current_project_id)
            self._refresh_versions(self.current_project_id)

    def restore_content(self, project_id: int, html: str, fmt: str, plain: str,
                        label: str = "Before recovery restore") -> bool:
        """Puts recovered content back into a project (File → Recovery).

        Same guarantees as restoring a version: the project is opened first
        (with the usual Save / Discard / Cancel if the open one has unsaved
        changes), and whatever it currently holds is kept as a version before
        being replaced, so this can itself be undone from Version History.
        """
        project = self.db.get_project(project_id)
        if project is None:
            return False
        if bool(project.archived) != self.showing_archived:
            self.archive_toggle.setChecked(bool(project.archived))
        if self.current_project_id != project_id:
            self.refresh_project_list(select_id=project_id, reveal=True)
            if self.current_project_id != project_id:
                return False      # the user cancelled leaving the open project
        elif not self.allow_leaving_project():
            return False
        self.db.keep_project_before_restore(project_id, label)
        self.db.save_project_content(project_id, html, content_format=fmt or "html",
                                     content_text=plain)
        self.history.rebase_session("project", project_id, stored_state(html, fmt or "html", plain))
        self.editor.load(html, fmt or "html")
        self._refresh_meta(project_id)
        self._refresh_versions(project_id)
        return True

    def flush(self, force: bool = False):
        """Called by the main window before closing/switching away, and
        before anything that reads the database back (a backup, an export).

        Calling this means "write it now" — see ReaderNotesWidget.flush for
        why the autosave preference is the timer's business, not this
        method's.
        """
        self._save_content(automatic=True)
        self.reader_notes.flush(force=force, automatic=True)

