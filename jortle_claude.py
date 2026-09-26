"""jortle_claude — entry point.

Run from source with:  python jortle_claude.py   (or run_jortle_claude.bat on Windows)

Startup order matters here. The data directory is resolved — and, on a first
run after the rename, migrated — BEFORE the main window is constructed,
because building the window opens the database, and opening the database is
what would create a fresh empty installation. See app/data_migration.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import data_migration
from app.paths import APP_NAME, DISPLAY_NAME
from app.single_instance import SingleInstance, bring_to_front


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    # Nothing is stored under the Qt organization/application names (no
    # QSettings anywhere — see app/paths.py), so both follow the identity.
    app.setOrganizationName(APP_NAME)

    icon_path = Path(__file__).resolve().parent / "resources" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # One running instance per data folder (Master Spec §4.1), decided before
    # anything else touches the data — including the migration, so a second
    # launch during a first launch's migration never joins in.
    instance = SingleInstance()
    if not instance.acquire():
        if not instance.notify_running_instance():
            box = QMessageBox()
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle(DISPLAY_NAME)
            box.setText(f"{DISPLAY_NAME} is already running.")
            box.setInformativeText(
                "Another copy is open, but it did not respond just now. Switch to "
                "it from the taskbar. A second copy is not opened, because two "
                "copies editing the same journal could overwrite each other's work.")
            box.exec()
        return 0

    try:
        data_migration.resolve_data_dir()
    except data_migration.MigrationError as exc:
        # Deliberately fatal. Starting anyway would mean starting with an
        # empty journal, which looks exactly like data loss — and the one
        # thing this path guarantees is that the user's real data is still
        # sitting untouched where it always was. Say so, and stop.
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("jortle_claude — data migration failed")
        box.setText("jortle_claude could not start safely.")
        box.setInformativeText(exc.user_message())
        box.exec()
        instance.release()
        return 1

    # Encryption (app/security.py). Anything an interrupted "set up
    # encryption" left behind is sorted out first; then, if the journal is
    # encrypted, it is unlocked before anything opens it.
    from app import security
    data_dir = data_migration.resolve_data_dir()
    security.finish_interrupted_setup(data_dir)
    state = security.startup_state(data_dir)
    if state == "encrypted":
        if not security.unlock_with_plain_keys(data_dir):
            from app.backup_dialog import UnlockDialog
            if UnlockDialog(data_dir).exec() != UnlockDialog.Accepted:
                instance.release()
                return 0
        security.finish_after_unlock(data_dir)
    elif state == "missing-key":
        # Looks encrypted, but the key file that goes with it is missing.
        # Opening it would fail; starting fresh would look like data loss.
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(DISPLAY_NAME)
        box.setText("jortle_claude could not open your journal.")
        box.setInformativeText(
            f"The journal in {data_dir} is encrypted, but its key file "
            f"({security.DB_KEY_FILE}) is missing. Nothing has been changed. "
            f"{security.recovery_doc_path()} explains how to put a copy of the "
            "key file back (one is kept in the backup folder).")
        box.exec()
        instance.release()
        return 1

    from app.main_window import MainWindow

    window = MainWindow()
    instance.activationRequested.connect(lambda: bring_to_front(window))
    window.show()
    window.start_background_tasks()
    try:
        return app.exec()
    finally:
        instance.release()


if __name__ == "__main__":
    sys.exit(main())
