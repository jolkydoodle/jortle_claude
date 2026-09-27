# Changelog

Notable changes to `jortle_claude`, newest first. Work is done in numbered
development groups; each section below is one group. The application's
version number (`app/__init__.py`) is still 1.0.0.

Changes from before the `jortle_claude` rename (the Jortle rounds, the
redesign, the backup/restore fixes and the bug hunt) are described in the
"What changed …" sections of `README.md`.

## Group 3 fixes — 2026-09-25

Fixes for the problems found by the independent re-audit of Group 3, plus
a few found alongside them.

### Repeating events
- Opening an occurrence and pressing Save without changing anything no
  longer rewrites the series or asks which occurrences to change.
- The event editor applies only the fields you actually changed, in all
  three scopes (this occurrence / this and following / entire series), so
  an occurrence keeps its own title, time and notes.
- Dragging or resizing with "this and following" or "entire series" shifts
  every affected occurrence by the same amount from its own time, including
  edited occurrences and untimed ranges.
- Changing the repeat rule keeps edited and cancelled occurrences attached:
  a date the new rule still produces stays; otherwise it moves to the new
  rule's occurrence in the same week, month or year. An edited occurrence
  with nowhere to go is kept as an ordinary event, never lost.
- A series split by "this and following" stays one series: "entire series"
  reaches every part, and a rule change on the entire series joins the
  parts back together.
- A series left with no occurrences is removed completely.

### Calendar and navigation
- Overlapping events in the Weekly Schedule sit side by side, the same way
  as in the Day View, at every window width.
- The Weekly Schedule's month navigator turns to the right month whenever
  stepping moves an on-screen day off its page.
- Right-click or Shift-click on a day in the Yearly Calendar always shows
  that day's Sunday–Saturday week.
- Today's number sits fully inside its circle in every month grid, at every
  interface font size and in every theme.

### Layout
- A pane dragged shut stays shut across tab switches and restarts.
- The Projects list returns to your width after changing the font size and
  back.
- Month panes are always wide enough for the month grid at the current
  font; the width you chose returns when the font gets smaller again.
- A saved interface font size is applied to everything at startup, exactly
  as a live change is.

### Projects, archive and other fixes
- Folder open/closed state in Projects is remembered across list refreshes
  and restarts.
- Categories made only of whitespace become "Uncategorized" instead of a
  folder with no name.
- Archive: no day page for a date with no occurrence, a series is listed
  from its first real occurrence, and events starting at the same time keep
  their order.
- A traceback printed by read-only previews (`bold_btn`) is gone.

## Group 3 — calendar, navigation, Tasks, Project folders — 2026-09-24

### Added
- **Yearly Calendar** workspace: all twelve months with entry dots, Day
  Markers, month titles and a year title. Click a day to open it; right-click
  or Shift-click to open its week.
- **Overnight and multi-day events**, timed and untimed. 9 PM to 2 AM is one
  event, drawn on both days.
- **Repeating events** (daily, weekly on chosen days, monthly, yearly, every
  N, optional end date), with "this occurrence / this and following / entire
  series" for every change. Occurrences are calculated, not stored.
- **Month and year titles**, one per month or year, shown in every view.
- **Project folders**, nested to any depth. Moving or renaming a project or
  folder keeps its notes and history. Existing categories became top-level
  folders.
- Weekly Schedule: week-by-week stepping (◀ Week / Week ▶, Shift+Left/Right)
  beside the existing day-by-day slide.

### Changed
- Workspaces are Daily Jorts, Weekly Schedule, Yearly Calendar and Projects.
  A saved custom tab order is kept, with the Yearly Calendar added after it.
- The Weekly Schedule follows the selected day; View → "Show This Day's
  Week" is gone.
- The Day View and the Weekly Schedule share one timeline and one set of
  event commands.
- New events are 70% opaque by default (they were 30%). Existing rows were
  not rewritten.
- Today is a filled circle in every month grid.
- "To do" became **Tasks**: a draggable boundary with the day calendar,
  several columns on wide panes, completed tasks greyed and struck through,
  and a "+" that is always visible.
- The calendar file in the readable archive exports overnight, multi-day and
  repeating events (DTEND, RRULE, EXDATE, RECURRENCE-ID).

### Fixed
- Pane widths are remembered across restarts again, and the month pane has
  one width in Daily Jorts and the Weekly Schedule.
- The Weekly Schedule's weekday header was white in dark themes.
- Completed tasks were nearly invisible in dark themes.

## Group 2 — backups, optional encryption, restore — 2026-09-23 (fixes 2026-09-24)

### Added
- **Automatic daily backups** while the app is open, into a backup folder
  outside the data folder (by default `jortle_claude Backups` beside it;
  configurable). File → Back Up Now, Export Backup… and Backups & Security…
  all make backups the same way.
- **Every backup is checked before it counts**: read back from disk, each
  file compared against the SHA-256 in its manifest, and the database opened
  and integrity-checked.
- **Backup retention**: keep everything (default) or the newest N automatic
  backups. Only automatic backups the app made and checked are ever deleted.
- **Optional encryption**, with two independent choices and a separate
  passphrase for each (with a "use the same passphrase" option): the
  database (SQLCipher) and backups (age, `.jcbackup` files).
- If encrypted backups are chosen but no passphrase is set, automatic
  backups pause and the status bar says why. They never fall back to
  unencrypted.
- **Keep unencrypted copies** (off by default): unencrypted copies of
  whatever is encrypted. Turning it on or off only creates or deletes those
  copies; encrypted files are never changed.
- **Restore** accepts `.zip` and `.jcbackup` files, including older backups.
  The backup is checked completely before anything changes, and current data
  is always moved aside first, never deleted.
- `docs/RECOVERY.md`: every file the app writes and how to open each one
  with standard tools, without the app.
- `recover_entry.py` and `diagnose_data.py` work with encrypted backups and
  installations.

### Changed
- The readable archive warns that it is not encrypted, no longer contains
  the database, and remembers its own folder instead of starting in the
  backup folder.

### Fixed
- Backups were not checked after writing and had no integrity hashes.
- The default backup location was inside the data folder, so a restore
  could move backups into its safety folder.
- Two restores within one second nested the second safety copy inside the
  first.
- An encrypted installation was not recognised by the data-folder migration
  when an older `Jortle` folder was also present.
- Found by the Group 2 audit and fixed on 2026-09-24:
  - one shared passphrase and one encryption switch had been built instead
    of the separate passphrases and independent switches that were agreed;
  - choosing encrypted backups and then cancelling the passphrase produced
    unencrypted automatic backups (they now pause);
  - a copy of a backup made by hand could take over the original's record,
    and retention could then delete the copy;
  - two automatic backups in the same second could make retention delete
    the newest one.

## Group 1 — entries, saving, version history — 2026-09-23

### Added
- **Version history for daily entries** (History… above the entry). A version
  is kept when you leave an entry you changed, when you close the app, before
  a restore, when an entry is emptied, and during long editing sessions;
  never per keystroke or autosave. Versions can be previewed, restored and
  deleted, but not edited.
- **File → Recovery**: when a save removes a large part of an entry or a
  project, the text as it was just before is kept as a recovery copy.
- An **Autosave** switch and an **Unsaved changes** indicator in the status
  bar, and a `*` in the window title.
- **Save / Discard / Cancel** before a backup restore when there are unsaved
  changes.
- **One running copy at a time**: a second launch brings the open window to
  the front. After a crash, the next launch starts normally.

### Changed
- **Manual saving is the default for new installs.** Installations that
  already had journal text keep autosave on.
- **Viewing never writes.** Journal entries, Reader's Notes and projects are
  saved only when you changed them, a blank document never becomes an entry,
  and emptying an entry deletes it (its text stays in History).
- **Written vs blank is one rule** (visible text or an image), used for the
  calendar dots, Reader's Notes' hollow dot, the archive, migration checks
  and version history.

### Fixed
- **Blank lines growing on every save and reload.** Saving made Qt wrap the
  document in a hidden table, and reading it back added a blank line
  whenever the first line was blank. This affected empty days and every
  entry starting with a blank line. Entries stored the old way now load with
  exactly the lines they had; `diagnose_data.py` lists entries that may have
  gained lines from the bug (it changes nothing).
- Dates that were only visited got a hollow dot.
- Viewing an entry or a project with autosave on could write to it.
- The migration's check for user data ignored Tasks.
- Restoring a backup could discard unsaved work without asking.

## The `jortle_claude` rename — 2026-09-23 (with Group 1)

- The application is named `jortle_claude`, distinct from a separately
  developed application called Jortle: window title, `jortle_claude.py`,
  `run_jortle_claude.bat`, `dist\jortle_claude\jortle_claude.exe`, the
  installer (with its own AppId, so it never replaces a Jortle install), the
  Desktop shortcut, backup and archive names, and the diagnostic and
  recovery tools.
- The data folder is `jortle_claude` (`%APPDATA%\jortle_claude` on Windows,
  `~/.local/share/jortle_claude` on Linux). On first launch, data from an
  older folder (`Jortle` first, then `DailyJournal` and earlier names) is
  copied, checked and only then put in place. The old folder is never
  modified or deleted.
- Database table and column names, settings keys and migration flags keep
  their old names, so existing data stays readable. Old backups still
  restore, whatever they are called.
