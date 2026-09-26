"""Day Markers: naming, colouring and assigning them (Parts 22 and 23).

A Day Marker is a user-defined record — a name plus a colour — that can be
put on any day, and that's what draws the coloured bar across that day's cell
in the monthly calendar. It used to be a fixed list of four (Holiday, Trip,
Milestone, Important) hard-coded in theme.py; now the list lives in the
`day_markers` table and the user owns it: create, rename, recolour, delete.
The four old ones are still there on an existing database, carried across by
`Database._migrate_named_markers()` with the days that used them re-pointed
at the new rows — and seeded as ordinary, editable rows on a fresh install,
so the feature still works the moment the app opens.

Two widgets:

  * `TagPicker` — the small control under the calendar that assigns a marker
    to the selected day, and offers "Manage markers…" as the way into:
  * `DayMarkerDialog` — the list where markers themselves are edited.

Part 23 governs the width. A marker's name is arbitrary text, so neither the
combo nor the calendar pane may size themselves to it: the combo elides,
keeps its full text in the tooltip, and shows the marker's colour in a swatch
that stays identifiable at any width. Nothing here uses a fixed pixel size
that assumes one font — the swatch is measured from the current font metrics
so it grows with the application font setting.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget
)

NONE_LABEL = "No marker"
MANAGE_LABEL = "Manage markers…"
DEFAULT_NEW_COLOR = "#3f8ede"


def marker_icon(color: str, size: int) -> QIcon:
    """A plain colour chip, drawn at whatever size the caller measured from
    the current font — so marker colours scale with the application font
    setting instead of staying 16px forever (Part 23)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color or "#888888"))
    painter.setPen(QColor("#00000060"))
    painter.drawRoundedRect(0, 0, size - 1, size - 1, 3, 3)
    painter.end()
    return QIcon(pixmap)


class DayMarkerDialog(QDialog):
    """Create, rename, recolour and delete Day Markers."""

    changed = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Day Markers")
        self.setMinimumSize(360, 300)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._rename())

        hint = QLabel(
            "Markers colour a day on the monthly calendar. Rename or recolour "
            "one and every day already using it follows."
        )
        hint.setWordWrap(True)
        hint.setObjectName("SubtleHint")

        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self._create)
        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self._rename)
        color_btn = QPushButton("Colour…")
        color_btn.clicked.connect(self._recolor)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete)

        buttons_row = QHBoxLayout()
        for button in (new_btn, rename_btn, color_btn, delete_btn):
            buttons_row.addWidget(button)

        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.accept)
        close_box.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.list, stretch=1)
        layout.addLayout(buttons_row)
        layout.addWidget(close_box)

        self.refresh()

    def refresh(self):
        selected = self._selected_id()
        self.list.clear()
        size = QFontMetrics(self.font()).height()
        for marker in self.db.list_day_markers():
            item = QListWidgetItem(marker_icon(marker.color, size), marker.name)
            item.setData(Qt.UserRole, marker.id)
            item.setToolTip(f"{marker.name} · {marker.color}")
            self.list.addItem(item)
            if marker.id == selected:
                self.list.setCurrentItem(item)

    def _selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _selected_marker(self):
        marker_id = self._selected_id()
        if marker_id is None:
            QMessageBox.information(self, "No marker selected",
                                     "Select a marker in the list first.")
            return None
        for marker in self.db.list_day_markers():
            if marker.id == marker_id:
                return marker
        return None

    def _create(self):
        name, ok = QInputDialog.getText(self, "New Day Marker", "Name:")
        if not ok or not name.strip():
            return
        color = QColorDialog.getColor(QColor(DEFAULT_NEW_COLOR), self,
                                       f"Colour for “{name.strip()}”")
        self.db.create_day_marker(name.strip(),
                                   color.name() if color.isValid() else DEFAULT_NEW_COLOR)
        self.refresh()
        self.changed.emit()

    def _rename(self):
        marker = self._selected_marker()
        if marker is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Day Marker", "Name:",
                                         text=marker.name)
        if not ok or not name.strip():
            return
        self.db.update_day_marker(marker.id, name=name.strip())
        self.refresh()
        self.changed.emit()

    def _recolor(self):
        marker = self._selected_marker()
        if marker is None:
            return
        color = QColorDialog.getColor(QColor(marker.color), self,
                                       f"Colour for “{marker.name}”")
        if not color.isValid():
            return
        # update_day_marker also recolours the days already using this
        # marker — see database.py; the calendar paints from the day's own
        # stored colour, so leaving those behind would split one marker into
        # two colours on screen.
        self.db.update_day_marker(marker.id, color=color.name())
        self.refresh()
        self.changed.emit()

    def _delete(self):
        marker = self._selected_marker()
        if marker is None:
            return
        confirm = QMessageBox.question(
            self, "Delete marker",
            f"Delete “{marker.name}”?\n\nDays marked with it become unmarked. "
            f"Their entries, notes and events are not touched.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self.db.delete_day_marker(marker.id)
        self.refresh()
        self.changed.emit()


class TagPicker(QWidget):
    """Assigns a Day Marker to the selected day."""

    # (marker id as text, or "" for none; the marker's colour, or "")
    tagChanged = Signal(str, str)
    markersChanged = Signal()  # the marker LIST itself was edited

    def __init__(self, parent=None, db=None):
        super().__init__(parent)
        self.db = db

        self.combo = QComboBox()
        # Marker names are user-defined and can be long, so the combo must
        # elide rather than hold the calendar pane open (Part 23). The full
        # name is always available via the tooltip, and the colour chip
        # beside it stays identifiable whatever the width.
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(5)
        self.combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.combo.currentIndexChanged.connect(self._on_index_changed)

        self.swatch = QLabel()
        self._set_swatch_color(None)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("Marker:")
        layout.addWidget(self._label)
        layout.addWidget(self.combo, stretch=1)
        layout.addWidget(self.swatch)

        self._suppress_signal = False
        self._current_id: str = ""
        self.reload_markers()

    # ------------------------------------------------------------ sizing
    def _chip_size(self) -> int:
        """Measured from the font, not fixed at 16px, so marker colours stay
        proportionate at every application font size (Part 23)."""
        return max(10, QFontMetrics(self.font()).height() - 2)

    def _set_swatch_color(self, hex_color: str | None):
        size = self._chip_size()
        self.swatch.setFixedSize(QSize(size, size))
        if hex_color:
            self.swatch.setStyleSheet(
                f"background-color: {hex_color}; border: 1px solid #888; border-radius: 3px;")
        else:
            self.swatch.setStyleSheet(
                "background-color: transparent; border: 1px solid #888; border-radius: 3px;")

    # -------------------------------------------------------------- data
    def reload_markers(self):
        """Rebuilds the list from the database, keeping the current choice
        selected if it still exists."""
        previous = self._current_id
        self._suppress_signal = True
        self.combo.clear()
        self.combo.addItem(NONE_LABEL, ("", ""))
        size = self._chip_size()
        markers = self.db.list_day_markers() if self.db is not None else []
        for marker in markers:
            self.combo.addItem(marker_icon(marker.color, size), marker.name,
                                (str(marker.id), marker.color))
            self.combo.setItemData(self.combo.count() - 1, marker.name, Qt.ToolTipRole)
        if self.db is not None:
            self.combo.addItem(MANAGE_LABEL, ("__manage__", ""))
        self._suppress_signal = False
        self.set_current(previous, None)

    def set_current(self, tag: str | None, tag_color: str | None):
        self._suppress_signal = True
        self._current_id = tag or ""
        matched = False
        if tag:
            for index in range(self.combo.count()):
                key, color = self.combo.itemData(index)
                if key == str(tag):
                    self.combo.setCurrentIndex(index)
                    self._set_swatch_color(tag_color or color)
                    matched = True
                    break
        if not matched:
            # Includes the case where the marker was deleted while this day
            # still referenced it: show "No marker" rather than a stale name.
            self.combo.setCurrentIndex(0)
            self._set_swatch_color(None)
            self._current_id = ""
        self.combo.setToolTip(self.combo.currentText())
        self._suppress_signal = False

    def _on_index_changed(self, index: int):
        if self._suppress_signal:
            return
        data = self.combo.itemData(index)
        if data is None:
            return
        key, color = data
        if key == "__manage__":
            # Not a choice — a door. Put the selection back where it was
            # before opening the editor, so "Manage markers…" can never end
            # up looking like the day's marker.
            self.set_current(self._current_id, None)
            self.open_manager()
            return
        self._current_id = key
        self._set_swatch_color(color or None)
        self.combo.setToolTip(self.combo.currentText())
        self.tagChanged.emit(key, color)

    def open_manager(self):
        if self.db is None:
            return
        dialog = DayMarkerDialog(self.db, parent=self)
        dialog.changed.connect(self._on_markers_changed)
        dialog.exec()

    def _on_markers_changed(self):
        self.reload_markers()
        self.markersChanged.emit()
