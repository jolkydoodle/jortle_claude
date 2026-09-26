# jortle_claude

A desktop journaling app built around a single idea: keeping a daily
record of your life should be one screen, not a maze of tabs. Pick a date
on the calendar, and your entry, your day's calendar, and everything else
are right there together — write, format text like a normal word
processor, drop photos in wherever they belong in the story, cite a source
with a real hyperlink, and plan your day without ever leaving the page.

There are four top-level workspaces:

```
Daily Jorts        one day at a time: writing, its Tasks and day calendar, Reader's Notes
Weekly Schedule    seven days side by side, with a month navigator
Yearly Calendar    all twelve months of a year at a glance
Projects           longer-term work in nested folders, with version history
```

**Drag a tab to put the workspaces in whatever order you like.** The order
is remembered between sessions. It is a display preference and nothing
else — it changes no entry, project, event or task. (Daily Jorts was
called Daily Jorts, and before that Daily Journal; the Weekly Schedule was
the Weekly Schedule. Only the labels changed. A tab order saved by an older
version still applies, with the Yearly Calendar placed after it.)

**The app is named `jortle_claude`, and so is its data folder — after a
migration that copies before it commits.** The name is deliberately distinct
from a separately developed application called Jortle, and the two never
share a data folder. The folder is `%APPDATA%\jortle_claude` on Windows
(`~/.local/share/jortle_claude` on Linux). If an older folder from a previous
version is found — `Jortle` (the builds just before this rename) first, then
`DailyJournal`, `.journal-app`, `.jortle`, `.daily-journal` — the app copies
it to the new name, opens the copy and checks it (database integrity, every
table's row count, a real rich-text entry, every JSON file), and only then
renames the copy into place. **The old folder is never deleted and never
modified**, so if anything goes wrong the original is still sitting there,
intact, and the app says so rather than starting up looking empty. (The
`Jortle` folder therefore stays exactly as it was; delete it yourself once
you're happy — `diagnose_data.py` shows what is in each folder first.)

If the `jortle_claude` folder already exists, the app works out what it
actually is before doing anything: a real journal is used as-is and never
overwritten; a folder holding only app scaffolding (an empty folder, a
leftover `models` folder, a database nobody has written in) is renamed out
of the way so the migration can finish, then its files are put back; and
anything it can't read is left strictly alone and reported. See
`app/data_migration.py`, and `diagnose_data.py` below if you ever want to
see the state of those folders for yourself.

**Only one copy runs at a time.** Launching the app while it is already
open brings the open window to the front instead of starting a second copy
— two copies editing one journal could overwrite each other's work. If the
app crashed or was killed, the next launch notices the old copy is gone and
starts normally.

What is deliberately *not* renamed: identifiers that hold data — the
database's table and column names, settings keys and migration flags —
because renaming them would make existing data unreadable for no benefit.

Built with Python + PySide6 (Qt) — the same kind of stack as your other
tools (the electrical plotter, the Japanese trainer) — so you can freely
read or modify the code later.

## The Daily Jorts screen

Three things, always visible side by side, resizable by dragging the
dividers between them:

- **Calendar (left).** Click any date to open it. A **filled dot** marks a
  day whose journal entry actually has writing in it — not a day you
  merely opened, and not a day that only has an event or a note on it;
  those get a **hollow dot** instead, so "I wrote that day" and "something
  is on that day" stay visually distinct. An entry whose text you delete
  and save loses its dot immediately, without changing month or
  restarting. A colored tint marks a day you've
  tagged as a Holiday, Trip, Milestone, Important, or a custom color via
  the "Day marker" dropdown beneath the calendar. Today's number sits in
  a filled circle in your accent color. Under the month name there is a
  line for an optional **month title** ("The move to Santa Barbara") — one
  title per month, shown wherever that month is (the Weekly Schedule's
  navigator and the Yearly Calendar too). Month and year navigation
  is a plain combo box and an always-visible up/down year spinner — no
  overlapping arrows, no hidden controls that only appear once you click
  in.
- **Today's writing (center).** A genuine WYSIWYG editor: click Bold and
  the selected text turns bold immediately, exactly like Word or Google
  Docs — there's no separate "preview" pane to flip to. Photos go in
  inline, right at your cursor, so you can write a paragraph, drop in a
  picture, and keep writing below it, same as any word processor. Select
  text and click the link button to turn it into a real clickable
  hyperlink (handy for citing a source) — Ctrl+click any link to open it.

  The toolbar is organized in two groups, left to right: **Font** (family,
  size, heading, Bold/Italic/Underline/Strikethrough/Superscript/
  Subscript, text color) and **Paragraph** (alignment, line spacing,
  spacing before/after, indent/outdent, bullet/numbered lists, blockquote)
  — followed by Insert (link, photo, find) and Zoom at the far right.
  Alignment (`⟸`/`⟺`/`⟹`/`☰` — Left/Center/Right/Justify, also
  Ctrl+Shift+L/E/R/J) and line spacing (a dropdown: Single/1.15/1.5/Double)
  apply to whichever paragraph(s) your selection touches; the two small
  `¶↑`/`¶↓` spin boxes next to it set space-before/space-after for the same
  paragraph(s), in points — genuine paragraph spacing, not a fake blank
  line, so it doesn't get confused with (or collapse) a deliberately blank
  paragraph you've typed. Superscript and subscript are mutually exclusive,
  like a real word processor's — turning one on turns the other off.
  Increase/Decrease Indent (`→|` / `|←`) work the same way on whichever
  paragraph(s) your selection touches, floored at no indent, so a couple of
  Outdent clicks always gets a paragraph fully back to flush; the text
  color swatch's dropdown arrow has a "Remove Color" option to bring
  colored text back to normal.
  The font dropdown and size box work exactly like Bold or Italic: select
  some text and change either one to restyle just that selection — one
  word can be size 15 and the next word size 20 in the same entry, the way
  Word or Google Docs works. With nothing selected, they set what you're
  about to type next. (The starting font for a brand-new entry, before
  you've customized anything, comes from the "Default writing font" in
  Settings — see below.)
  At the far right, `−` / percentage / `+` (or Ctrl+Scroll, Ctrl+= /
  Ctrl+- / Ctrl+0) zoom the text in and out temporarily, the way a browser
  or PDF viewer does — it's just a bigger/smaller on-screen view, separate
  from the actual writing font/size above, resets each time you reopen the
  app, and is never saved into the entry itself.

  **Ctrl+F finds text in the current entry** (also the 🔍 toolbar button):
  a small bar drops in above the writing area with a match count, Prev/Next
  buttons, and Enter/Shift+Enter to step forward/backward through matches
  (Esc closes it). It's find-only — there's no replace — and it never
  changes a single character of your entry, whether or not it finds
  anything; it only moves the cursor and highlights matches. Case-
  insensitive by default.

  The toolbar itself shrinks down to a small "»" overflow button when
  there isn't room for everything, rather than forcing the writing pane to
  stay at least as wide as every button on it laid out in a row — so
  dragging the divider between panes narrower than before (to give more
  room to the calendar or the task list, say) no longer gets stuck early.
  Anything tucked into "»" is still one click away; nothing is hidden for
  good, and every toolbar action's keyboard shortcut works regardless of
  whether its button happens to be visible or tucked away at the moment.

  There's real blank space below the last line you can scroll into, so a
  long entry doesn't pin the line you're writing to the very bottom edge of
  the screen — it's purely a view thing and is never saved as part of the
  entry text. Beyond that, scrolling is entirely yours: the editor no
  longer forces the line you're typing to any particular spot on screen
  (an earlier build snapped it to the vertical middle on every keystroke —
  removed, since manual scrolling deserves to stay where you put it).
  Typing simply keeps the cursor visible, nudging the view the minimum
  amount needed if it would otherwise scroll off-screen, and does nothing
  at all the rest of the time.
- **Day Calendar (right).** An interactive, scrollable 24-hour day view for
  the selected date, in place of the old Daily Tasks checklist. Drag through
  empty space to create an event, or **double-click empty space for a
  one-hour one**; drag an event to move it (its duration is preserved); drag
  its top or bottom edge to resize it; double-click it to edit its title,
  date, times and notes; select it and press Delete to remove it.
  Everything snaps to 15 minutes, overlapping events are laid out
  side-by-side automatically, and when you're looking at today a red line
  marks the current time. Anything genuinely untimed sits in a strip above
  the timeline — the same strip the Weekly Schedule uses (see below) — which
  is where your existing tasks went: every one of them was converted into an
  untimed event on its own date, rather than being given an invented start
  time.

  **Above the calendar is the ToDo list** for that day, when there is one.
  It is a separate thing from a calendar event, and it has its own section
  below.

  There is deliberately no "previous day / next day" control here. The
  monthly calendar on the left is the only date selector in the app, and
  this view always shows whatever day it has selected.

- **Reader's Notes (left, below the calendar).** A lightweight companion
  area for the same date — definitions, concepts, short references, the
  terminology you want within reach while writing. It's saved separately
  from the journal entry itself (its own record per day), so it never mixes
  into your writing, and it follows the selected date exactly as the journal
  does. It has a deliberately reduced toolbar: bold/italic/underline,
  strikethrough, super/subscript, lists and Ctrl+F, but not the journal's
  full paragraph machinery — the journal stays the full writing surface.

  Reader's Notes is a core part of the app, and always was — it shared a
  pane with the removed AI feature but never depended on it, which is why
  removing that feature needed no changes to it at all.

- **Dates in your writing become links.** Write `September 20, 2026`,
  `09/20/2026`, or `2026-09-20` in an entry and it turns into a link you can
  click to jump straight to that day — the journal, the day calendar and
  Reader's Notes all move together. A **← Return to …** button appears next
  to the date heading so you can get back where you came from, and it
  remembers a trail if you follow several in a row. Deliberately conservative
  about what it recognizes: a bare `3/4` stays ordinary text, because
  turning a fraction into a link that jumps your journal somewhere would be
  worse than not linking at all. Links to days you haven't written yet work
  fine — you just arrive at an empty entry.

**On-disk format:** entries are stored as Qt's own rich-text HTML — the
same engine that draws the editor also writes and reads this, so every
formatting property above (alignment, line/paragraph spacing, colors,
lists, blank paragraphs, everything) survives a save/reload exactly as you
left it. This replaced plain Markdown as of this format change; anything
written before it stays in the older Markdown format until you next edit
and save it (nothing is rewritten just by opening it), so no existing entry
is ever touched or at risk from this change.

## Projects

A separate tab for anything that isn't a daily entry — a novel, a thesis
chapter, a memoir — that you want to keep working on over weeks or years
without it being tied to a specific date. The same WYSIWYG editor, inline
photos, and hyperlinks are available here too. Each project additionally
tracks:

- when it was created, and every calendar day you worked on it;
- a version history: one automatic snapshot at the start of each new day
  you touch it, plus as many manual, labeled checkpoints as you want
  ("Save Version…"). Any past version can be viewed read-only or restored
  — restoring always saves your current text as a checkpoint first, so a
  restore can never quietly erase work.

Projects can be archived (hidden from the main list without being
deleted) and, from the archived view, permanently deleted if you're sure.

**Folders.** The list on the left is a tree of folders you make and name
yourself, nested as deep as you like — "Courses → Fall 2026 → CHEM 173A",
"Research → GaN → EES". **New Folder** makes one inside the selected folder;
right-click a folder to add a subfolder, rename it, move it or delete it; drag
a project or folder onto another folder to move it (or use **Move to…**, also
beside the folder path above the editor). Projects in no folder are listed
under "Uncategorized" at the end. Moving or renaming never breaks anything:
a project keeps its Reader's Notes, versions and history wherever it goes.
Only an empty folder can be deleted (archived projects count), so deleting a
folder never takes writing with it. Categories from earlier versions became
top-level folders of the same names the first time this version opened.

**Reader's Notes.** Projects have Reader's Notes too, below the project
list — the same feature, the same editor and the same storage as the
journal's, just attached to a project instead of to a date. Characters,
continuity notes, terminology, a source you keep re-checking: things that
belong beside the writing rather than in it. Each project has its own set,
and they are saved the moment you switch projects. Like the journal's, they
are a core feature: the Projects tab is the project editor and Reader's
Notes, and that's all.

## The Weekly Schedule screen

A workspace of its own, not a mode of Daily Jorts:

```
Weekly Schedule
├── Monthly Calendar         a navigator, on the left
└── Week View                seven day columns over one shared time axis
```

The navigator pane has **the same width as the month pane in Daily Jorts** —
drag either and the other follows, now and after a restart.

The divider between them drags, same as every other pane in the app.

Week View behaves like seven Day Views side by side, and it is the same
events — there is one `calendar_events` table and both views are drawings
of it. Anything you do in one is true in the other immediately: create,
move, resize, retitle, recolour, change transparency, delete.

What you can do on it:

* **drag on empty space** in a day column to create an event there;
* **drag an event up or down** to move it in time, duration preserved;
* **drag an event sideways** into another day column — Tuesday 2:00–3:00 PM
  dropped on Wednesday becomes Wednesday 2:00–3:00 PM, same duration;
* **drag its top or bottom edge** to change when it starts or ends;
* **double-click** it to open the same editor the Day View uses;
* **select it and press Delete** to remove it;
* **click a day's name** at the top of its column to open that day in
  Daily Jorts.

Untimed events appear in a strip between the day names and the timeline,
since they have no position on a time axis — the same reasoning as the Day
View's "Untimed" list, but per column, because an untimed event still
belongs to a particular day.

### The seven-day window

The Weekly Schedule shows **seven consecutive days**. It shows the
Sunday–Saturday week of the selected day, and you can move it two ways:

```
◀ Day / Day ▶      Left / Right         slide the window one day
◀ Week / Week ▶    Shift+Left / Right   step it a whole week (seven days)
```

Sliding by a day turns Sunday–Saturday into Monday–Sunday and it stays there;
stepping by a week keeps whatever alignment the window has. "This week",
clicking a day in the navigator, or selecting a day anywhere in the app gives
the Sunday–Saturday week again. The navigator shades all seven visible days.

The arrow keys work when the Weekly Schedule itself has focus — a plain key
handler on that workspace, not a global shortcut, so a text field or the
event editor keeps its own arrow keys.

### The selected day and the visible week

Daily Jorts has a **selected day**; the Weekly Schedule has a **visible
seven-day window**. When the selected day changes — a click in a month grid,
a date link, Back, the Yearly Calendar — the week follows it. Browsing the
week never changes the selected day; clicking a day name is how you hand a
day to Daily Jorts. (The old View → "Show This Day's Week" is gone: the week
is already there.)

### Zooming the timeline

**Ctrl+scroll** on either the Day View or the Week View timeline makes the
hours taller or shorter — 40% to 300% of the normal hour height, in 10%
steps. A plain scroll still scrolls, as it always did; Ctrl is what turns
it into a zoom.

There are **three separate zooms in this app, and they stay separate**:

```
calendar zoom        how tall an hour is on the timeline        Ctrl+scroll on a timeline
editor zoom          how big your writing looks, temporarily    Ctrl+scroll in the editor
application font     the size of the whole UI                   File → Settings
```

Calendar zoom is one setting shared by the Day View and the Week View —
they are two drawings of the same events, so zooming one zooms the other —
and it is remembered between runs. It changes only the time axis: event
text, the hour labels and everything else keep the font size you chose in
Settings.

### Work hours

**File → Settings → Calendar → "Highlight work hours"** tints 9:00 AM to
5:00 PM, Monday to Friday, on both timelines. It's off by default, it's
purely a background tint, and it changes nothing else: events outside it
are ordinary events, you can still create and drag anything anywhere, and
weekends get no tint at all. The tint is derived from the current colour
scheme, so it stays a faint wash in light themes and a faint lift in dark
ones.

## The Yearly Calendar screen

All twelve months of one year, each with its entry dots, Day Markers, today's
circle and its month title, and the year's own optional title at the top.
◀ / ▶ go a year back or forward. **Click a day** to open it in Daily Jorts;
**right-click or Shift-click** it to open its week in the Weekly Schedule
(the day is selected either way). The months re-flow — four, three, two or
one per row — to fit the window and the interface font.

## Overnight, multi-day and repeating events

An event has a start and an end, each with its own date, so **9 PM to
2 AM is one event** — drawn on both days, dragged, resized, edited and
deleted as one. Untimed events can cover several days too: one bar across
their columns in the Weekly Schedule (drag its end to lengthen it), shown on
each of those days in the Day View.

In the event editor, **Repeat** makes an event recur daily, weekly (on the
days you tick), monthly or yearly, every N of those, optionally until a date
you choose. When you change, move or delete one occurrence of a repeating
event you are asked which to change: **this occurrence only**, **this and
following**, or **the entire series**. Monthly events on the 31st skip the
months without one; yearly events on 29 February appear in leap years.

## Events: colour and transparency

An event has **no colour of its own unless you give it one**. Left alone,
it follows your colour scheme's accent — so switching themes recolours
every ordinary event, and it looks right in all of them. Tick off "Use
theme default colour" in the event editor to pick a specific colour, and
that event keeps it in every theme, because you asked for it by hand.

This is a real distinction in the data: "follow the theme" is stored as
*no colour*, not as a copy of today's theme colour.

Transparency is a slider in the same editor, **70% opaque by default**
(it was 30% opaque before; events you never set a transparency for changed
with the default), so
the grid lines and any work-hours tint show through an event instead of
being covered by it. The label text is recoloured against what the event
will actually look like once it's blended, so it stays readable at any
transparency, over any background, in any theme.

## Saving: Ctrl+S, autosave, and never losing an edit

```
Ctrl+S            save the workspace you're in, right now
Ctrl+Z / Ctrl+Y   undo / redo your writing
File → Settings → Saving → "Enable autosave"
```

**Manual saving is the default.** `Ctrl+S` saves whatever you're working
in — the journal entry and its Reader's Notes on the Daily Jorts tab, or
the project and its notes on the Projects tab. The **Autosave** switch in
the bottom-right corner of the window shows whether autosave is on, and
turns it on or off with one click (it is the same setting as the checkbox
in Settings). With autosave off, **● Unsaved changes — Ctrl+S to save**
appears next to it whenever the workspace in front has unsaved work, and
the window title gets a `*`.

Turn **autosave** on and the same save runs by itself a moment after you
stop typing. It is the *same save*: one code path writes to the database,
whether a timer or your keyboard asked for it, so there is no "autosave
version" of your entry that behaves differently from a manually saved one.
(If you're upgrading from a version that always autosaved, autosave stays
on — your habits don't change under you. New installs start manual.)

**Looking is not writing.** Selecting a date, moving between days, or
switching tabs never writes anything unless you actually changed
something. A date either has a written entry or it has none: a blank
editor never becomes an "entry", and it never gets a marker.

**Deleting an entry = deleting all of its text.** Select all, delete, and
save (or let autosave save): the entry is gone and so is its dot on the
calendar. What it said is kept in its version history (below), so it can
be brought back.

**Version history (the History… button above the entry).** Each day's
entry keeps its earlier versions — not one per keystroke or autosave, but
one when you leave an entry you changed, when you close the app, before a
restore, and during long editing sessions, plus the state an entry was in
before you first changed it. Versions can't be edited. From the History
window you can preview any version, restore it (the current text is kept
as a version first), delete all previous versions, or delete the ones
older than a date you pick.

**File → Recovery.** If a save removes a large part of an entry or a
project (at least 1,000 characters, or at least half of a shorter one), the
text as it was just before is kept as a recovery copy. File → Recovery
lists them with how much was removed, previews them, and restores or
copies them. They are never deleted automatically. These are not backups —
they protect against one big accidental deletion; File → Export Backup
protects against everything else.

If you try to leave an entry with unsaved changes — switching days,
switching tabs, or closing the app — you get **Save / Discard / Cancel**.
Cancel puts you back where you were, with the edit still there. Nothing is
thrown away without you saying so.

**Undo is per document and genuinely deep.** Ctrl+Z steps back through
your typing and your formatting, and it is no longer disturbed by saving,
by resizing the window, or by changing the font in Settings — those used
to quietly poison the undo history, because saving edited the live
document to do its work. Saving now works on a copy. Undo does not cross
between documents: your journal entry, your Reader's Notes and each
project each have their own history, and switching days starts a fresh one
rather than offering to undo its way into yesterday.

## Day Markers

A Day Marker is a **name plus a colour** that you can put on any day; it's
what draws the coloured bar across that day in the month grid. The list is
yours to edit — pick "Manage markers…" in the Marker dropdown under the
calendar to create, rename, recolour or delete them.

```
Day Marker
├── name       "Research Day", "Travel", "Deadline", "Day Off", anything
└── colour
```

A new install starts with four (Holiday, Trip / Travel, Milestone,
Important) purely so the feature works out of the box — they're ordinary
rows you can rename or delete like any other. If you already had marked
days, each colour you'd used became a named marker and those days now
point at it, so nothing you'd marked was lost.

Renaming or recolouring a marker updates every day already using it.
Deleting one unmarks those days and leaves everything else about them —
writing, notes, events — untouched.

Marker names never widen the calendar pane: the dropdown elides long names
and keeps the full text on hover, and the colour chip stays visible at any
width and grows with the application font size.

## Settings

There is one settings window, `File → Settings`, and every control in it
applies the moment you change it — no Save button. It scrolls and its text
wraps, so nothing runs off a small screen at any interface font size.

There used to be a second window, `File → Experimental Settings`, holding
the controls for the optional AI feature. That feature is gone (see the end
of this file), so the window is gone too rather than sitting in the menu
opening on nothing.

## Photos: inline, not a separate tab

Earlier versions of this app had a dedicated "Photos" tab with a thumbnail
strip. That's gone now — photos live directly inside your writing, at
whatever point in the text you inserted them, the way they would in a
real document. Click the photo button in the toolbar, pick an image, and
it drops in at your cursor; double-click any inline photo to view it at
full resolution.

Under the hood, each entry/project just stores a relative reference to the
image file (standard Markdown `![]()` syntax), and the editor
automatically shows a reasonably-sized version inline — you're never
looking at an unscaled 12-megapixel photo squeezed into your journal. The
original file on disk is never modified or resized, so "view full size"
always shows the real thing.

Because there's no longer a table tracking which photo belongs to which
entry, removing a photo from your text doesn't delete its file — that's
the safer default. Instead, **File → Find Unused Photos** scans every
entry, project, and saved version for image references and finds any file
that isn't mentioned anywhere anymore, so you can review and delete them
deliberately when you want to reclaim space.

## Color schemes

File → Settings now offers a dozen built-in color schemes — Light, Dark,
Sepia, Slate, Forest, Ocean, Rose Quartz, Charcoal, Solarized Light,
Solarized Dark, Midnight, and High Contrast — plus five color swatches
(Background, Panels, Text, Accent, Border). Click any swatch to change
just that one color via a color picker; doing so switches you to a
"Custom" scheme seeded from whichever preset you started from, so you can
build your own look without starting from nothing.

Settings has two more font controls, deliberately separate from each other
and from the toolbar's per-selection font controls described above:

- **Default writing font** — the starting family/size for a brand-new
  entry or project, and for any text you haven't individually resized. It
  never overrides text you've already resized yourself with the toolbar.
- **Application font size** — resizes the app's own interface text
  (buttons, labels, the calendar, the task list, menus), never your entry
  or project text.

Both, plus the color scheme, apply the instant you change them — there's
nothing to save.

## Running the tests

```
python tests/run_all.py
```

24 suites, each a standalone script you can also run on its own. They build
real widgets against a throwaway data directory — never your journal — and
several of them drive real mouse and key events, because the thing worth
testing about a drag is that it's actually wired to the mouse. On Linux the
runner sets Qt's offscreen platform for you; on Windows it just runs.

## Why Python/PySide6 and SQLite + files

Two design choices worth knowing about, since long-term data stewardship
was part of the original ask:

1. **Storage format.** Entries and projects live in one SQLite database
   file (`journal.db`) as plain Markdown text; photos live as ordinary
   image files in an `attachments/` folder next to it, referenced by
   relative path. All of it is open, non-proprietary, and human-readable —
   if this app is ever gone in 20 years, your data is still just a
   database file, some plain text, and a folder of pictures.
2. **Where it lives.** Everything is under one folder:
   `%APPDATA%\jortle_claude` on Windows. Backing up your whole journal is
   always "zip that one folder" — which is exactly what File → Export
   Backup automates for you.

## Running it (development / any OS)

```bash
pip install -r requirements.txt
python jortle_claude.py
```

This works on Windows, macOS, or Linux as-is.

## Building the Windows app (with a desktop shortcut)

This part **must be run on a Windows machine** — PyInstaller bundles an
app for whatever OS it's run on, and it can't cross-build a Windows `.exe`
from this Linux environment.

1. Copy this whole `jortle_claude` folder onto your Windows PC, and keep it
   at one fixed location — when you get updated source later, overwrite
   the files inside this same folder rather than extracting to a new one,
   so your desktop shortcut keeps pointing at the right place. (Nothing
   in the app reads the folder's name — every path resolves relative to
   the file asking for it — so if yours is still called `journal_app`
   from an earlier download, renaming it is optional.)
2. Make sure Python 3.10+ is installed
   ([python.org](https://www.python.org/downloads/) — check "Add
   python.exe to PATH" during install).
3. Double-click **`build_windows.bat`**. It installs the two dependencies
   (PySide6 and PyInstaller) and produces `dist\jortle_claude\jortle_claude.exe`. It
   asks you nothing — there is no optional component to choose any more.
4. Run **`create_desktop_shortcut.bat`** to drop a "jortle_claude"
   shortcut on your Desktop pointing at that .exe. If you still have an old
   "Daily Journal" shortcut from a previous build, that script removes it,
   since it points at an .exe that no longer exists. An old "Jortle"
   shortcut is deliberately left alone (a separate application is also
   called Jortle); delete it yourself if it pointed at this project's old
   `dist\Jortle` folder.

This build uses PyInstaller's `--onedir` mode (a folder of files) rather
than `--onefile` (a single .exe). A `--onefile` executable has to silently
unpack itself into a temp folder *every time you launch it* — for an app
built on PySide6 (a large toolkit), that's a genuinely noticeable delay.
`--onedir` starts almost immediately since there's nothing to unpack; the
only difference is that the app now lives in a folder instead of a single
file, which is what `create_desktop_shortcut.bat` already expects.

An Inno Setup installer script (`installer.iss`) is still included and
kept up to date, in case you ever want a real installer with an
uninstaller entry — but it's entirely optional; the two steps above are
everything you need.

Rebuilding after a code update is the same two steps: re-run
`build_windows.bat`, then `create_desktop_shortcut.bat` again if you like
(it just recreates the same shortcut, so it's harmless to re-run or skip).

## The readable archive (File → Export Readable Archive)

Separate from the backup zip, and for a different purpose. **File → Export
Readable Archive (HTML)** writes a small static website of everything you've
written:

```
journal_archive/
├── index.html          browse by year → month → day
├── styles.css
├── entries/2026-09-15.html
├── readers_notes/2026-09-15.html
├── calendar/calendar.ics, calendar.csv
├── settings.json
└── backup_manifest.json
```

Open `index.html` in any browser. No server, no Python, no application, no
JavaScript needed to read it. Your formatting is preserved because the pages
carry the same rich-text HTML the editor itself produced; Reader's Notes
appear as a clearly marked section on each day's page (and as their own file
too); internal date links become ordinary relative links, so the archive
keeps its cross-date navigation; and the calendar exports as standard `.ics`
plus a spreadsheet-friendly `.csv`.

The archive is **not encrypted** — the app says so before writing it — and it
no longer contains the database: restoring the app is what backups are for.
Its folder picker remembers its own last location and does not start in the
backup folder.

## Backups and optional encryption

**Backups are automatic.** Once a day, while the app is open (checked at
launch and every hour), a backup goes into the backup folder — by default
`jortle_claude Backups` beside the data folder (`%APPDATA%\jortle_claude
Backups` on Windows), outside the folder it protects. **File → Back Up Now**
makes one on demand, **File → Export Backup…** puts one wherever you choose,
and **File → Backups & Security…** (also reachable from Settings) shows the
folder, the last successful backup, whether it was checked, whether it was
encrypted, and whether one is overdue. All of them make a backup the same
way.

**Every backup is checked before it counts.** It is read back from disk: the
zip's own checksums, a SHA-256 for every file listed in its manifest, and
the database opened and integrity-checked. An encrypted backup is checked
before it is encrypted, then read back from disk; when the backup passphrase
has been entered this session (or is the same as the database passphrase) it
is also decrypted again for the same checks. Backups & Security says which
check was done. Only then is a backup recorded as made.

**Keeping backups:** by default every backup is kept. You can choose to keep
only the newest N automatic backups. Only automatic backups the app made and
checked are ever deleted; manual backups, the newest checked backup, copies
you made yourself, and anything that could not be checked are kept.

**Encryption is optional, and the database and backups are separate
choices,** each with its own passphrase (when setting up the second you can
tick "use the same passphrase"). Both are in File → Backups & Security.

- **Backups:** on first launch you are asked whether backups should be
  encrypted. Encrypted backups are `.jcbackup` files (age), which carry
  everything needed to open them except the backup passphrase; making one
  never needs the passphrase. If you choose encryption but don't set a
  passphrase, or haven't answered yet, automatic backups wait and the status
  bar says "Backups paused" — they never quietly fall back to unencrypted.
- **Database:** encrypted at rest (SQLCipher); the app asks for the database
  passphrase when it starts. Photos in the data folder's `attachments`
  folder are not encrypted (inside encrypted backups they are).

**Keep unencrypted copies** (off by default) keeps unencrypted copies of
whatever is encrypted: a copy of the database beside the encrypted one,
updated within a few seconds of each save and on close, and every backup as
both `.jcbackup` and `.zip`.
Turning it on creates those copies (including a `.zip` twin of each existing
encrypted backup); turning it off deletes them again. The encrypted files are
never changed either way, and unencrypted backups with no encrypted twin are
never deleted automatically. While it is on, the app opens without asking for
the database passphrase — the unencrypted copy is readable anyway, and a
prompt would only suggest otherwise.

If you lose a passphrase, what it protects cannot be opened by anyone.
**`docs/RECOVERY.md`** describes every file and how to open each one with the
standard `age` and `sqlcipher` tools, without this app.

**Restoring** (File → Restore from Backup…) accepts `.zip` and `.jcbackup`
files, including older backups. The backup is checked completely before
anything changes; your current data is always moved to a
`jortle_claude.before-restore-…` folder first, never deleted — even if you
have turned the warning off. If the database is encrypted, the restored
journal is encrypted with this installation's key before it is put in place.

### Getting one entry back without restoring everything

```
python recover_entry.py <backup>                    list what's in it
python recover_entry.py <backup> 2026-09-15         show that entry
python recover_entry.py <backup> 2026-09-15 --write put it back
```

`<backup>` can be a `.zip`, an encrypted `.jcbackup` (it asks for the
passphrase and keeps the contents in memory), or an old data folder.

A full restore replaces your whole journal, which is the wrong tool when
you want one day back and have written other things since. This copies a
single entry — formatting and photos included — into the journal you're
using now and touches nothing else. Nothing is written without `--write`,
and if that date already has writing in it, the existing version is saved
to a dated `.html` file beside the database first.

## Project layout

```
jortle_claude/
  jortle_claude.py            entry point (was jortle.py, and before that main.py)
  app/
    paths.py                  where data lives on disk: display name vs storage identity
    data_migration.py         copy-validate-commit move of the data folder to jortle_claude/
    single_instance.py        one running copy per data folder; a second launch activates the first
    entry_history.py          CORE: when entry versions and recovery copies are made
    history_dialogs.py        the Version History and File → Recovery windows
    saving.py                 CORE: written vs blank (one rule, also callable from SQL); autosave pref; Save/Discard/Cancel
    calendar_prefs.py         CORE: calendar zoom + work-hours highlighting, shared by both views
    database.py                SQLite schema + all read/write functions
    calendar_widget.py         calendar grid painting (day markers, today outline)
    calendar_panel.py           calendar + custom month/year nav + day-marker picker
    rich_editor.py               WYSIWYG editor: formatting, inline photos, links, Ctrl+F
    date_state.py                CORE: the one canonical selectedDate + navigation history
    date_links.py                CORE: recognizing written dates, journal://date/ links
    all_day_strip.py             CORE: the untimed-event strip, shared by both calendars
    todo_widget.py               CORE: the Day View's Tasks — not a calendar event
    calendar_grid.py             CORE: the timed grid shared by the Day View and the Weekly Schedule
    event_commands.py            CORE: every event change (create/edit/move/resize/delete, repeat scopes)
    event_dialog.py              CORE: the event editor
    recurrence.py                CORE: event spans, repeating rules and their occurrences (no Qt)
    year_calendar.py             CORE: the Yearly Calendar workspace
    period_titles.py             CORE: month and year titles, one shared store
    reader_notes_widget.py       CORE: Reader's Notes, for a day or a project
    day_calendar_model.py        CORE: day-calendar maths — time<->pixels, snapping, overlap
    day_calendar.py              CORE: the interactive day view + event editor
    archive.py                   CORE: the static HTML/ICS/CSV readable archive
    tag_picker.py                 Day Markers: the per-day picker + the manage dialog
    week_calendar.py             CORE: the Weekly Schedule workspace (week view + navigator)
    event_render.py              CORE: how one calendar event is painted, shared by both views
    ui_util.py                   CORE: responsive-layout helpers (elide, wrap, pane sizing)
    projects_widget.py            long-term writing projects + version history
    cleanup.py                    "Find Unused Photos" scan
    settings_dialog.py           core settings only: colours, fonts, writing position
    theme.py                     color schemes, stylesheet generation, tag colors
    backup.py                     backups: make, check, keep, restore (.zip / .jcbackup)
    security.py                   optional encryption: SQLCipher database, age keys
    backup_dialog.py              unlock, passphrase, and Backups & Security windows
    main_window.py                wires everything together
  resources/
    icon.ico / icon.png          app icon
  tests/
    run_all.py                 runs every suite below: python tests/run_all.py
    test_*.py                  21 standalone suites — rich text, database and
                               data-folder migrations, drag interactions, the
                               week view, day markers, responsive layout,
                               calendar colour/transparency/zoom/work hours,
                               entry indicators, saving + undo, and that no
                               AI remains anywhere in the application
  docs/RECOVERY.md               every file, and how to open it without the app
  diagnose_data.py               read-only report on the data folders (see below)
  recover_entry.py               pull one entry out of a backup (see below)
  requirements.txt
  build_windows.bat              one-time Windows build script (--onedir)
  create_desktop_shortcut.bat    creates the Desktop shortcut
  installer.iss                  optional Inno Setup installer script
```

## What changed in this redesign

If you're comparing against an earlier build: the Journal Entry / Daily
Tasks / Photos tabs are gone in favor of the three-pane single screen
described above; the Markdown-with-separate-preview editor was replaced
with a true WYSIWYG editor (same underlying Markdown storage); photos are
now inline in your writing instead of a separate attachment gallery; the
calendar's month/year controls were rebuilt from scratch to fix the
overlapping-arrow and hidden-spinner issues in Qt's built-in ones; color
schemes grew from a light/dark toggle to twelve presets plus full custom
tweaking; and the Windows build switched from a single `.exe` to a faster-
starting `--onedir` build. The `markdown` and `Pillow` Python packages are
no longer dependencies — Qt's own Markdown and image support cover
everything they were doing, so there's one less thing to install and one
less thing loaded at startup.

## What changed in the Jortle round

* The app is now called **Jortle**.
* **AI Reflection → AI Suggestion** and **Writing Projects → Projects**
  throughout the interface. Settings keys, database tables and file paths
  keep their old names on purpose, so nothing you already have moves.
* A third workspace, **Weekly Calendar**: seven day columns over one time
  axis, with a month grid beside it as a navigator. It shows the same
  events as the Day View, because both read the same table — create, drag,
  resize, recolour or delete in either and the other agrees. Events can be
  dragged sideways between days as well as up and down in time.
* The week shown is **seven consecutive days**, not a fixed calendar week.
  Left/Right slide it one day at a time, so Sunday–Saturday can become
  Monday–Sunday and stay there; clicking a row in the navigator is what
  re-aligns it to a conventional week.
* **Day Markers now have names**, not just colours — create, rename,
  recolour and delete them under "Manage markers…" beside the calendar.
  Existing marked days were migrated: each old colour became a named
  marker and the days using it now point at that marker.
* **File → Settings** and **File → Experimental Settings** are separate.
  Everything AI lives in the second one, which is scrollable so its
  explanatory text can't run off a small screen. Ordinary Settings no
  longer imports the AI code at all.
* **Reader's Notes now applies to Projects too**, on the same storage and
  the same widget as the journal's — with AI off, Projects is just the
  project editor and Reader's Notes, with no AI controls anywhere.
* **Journal zoom** was repaired (Ctrl+wheel and the Zoom % box), and zoom
  is strictly a view setting again: it never changes the font sizes stored
  in your document.
* The rich-text toolbar is now **two rows**, so nothing hides behind an
  overflow chevron at ordinary window widths.
* **Application font size** now propagates to the whole interface, and the
  layout adapts to it: panes re-proportion, labels wrap, long values elide
  instead of clipping, and the month grid keeps all six week rows.

## What changed in the second Jortle round

* **The rename now reaches disk.** The entry point is `jortle.py`, the
  build produces `dist\Jortle\Jortle.exe`, the shortcut says Jortle, and
  the data folder is `Jortle` — reached by a copy-validate-commit
  migration that never writes to or deletes the old folder. Your
  *settings* key stays `DailyJournal` on purpose; renaming it would have
  reset every preference.
* **Theme-aware event colours.** Events that follow the theme were coming
  out the same fixed blue in every colour scheme, because they asked the
  widget palette for its highlight colour and this app themes itself with
  a stylesheet — which never touches the palette. They now read the same
  accent the rest of the theme uses.
* **Events are 70% opaque by default**, with a transparency slider in the
  editor, and "follows the theme" is stored as *no colour* rather than as
  a snapshot of today's theme colour. Existing events that were fully
  opaque were moved to the new default once; any event you'd given an
  explicit colour keeps it.
* **Calendar zoom** (Ctrl+scroll, 40–300%) and optional **work-hours
  highlighting**, both shared by the Day View and the Week View, both
  remembered between runs, and both kept separate from editor zoom and
  from the application font size.
* **The calendar dot now means "there is writing here."** Merely opening a
  date, or an empty autosave, no longer marks it; whitespace doesn't count
  as writing; emptying an entry and saving clears the dot at once. Days
  that have only an event or a note get a hollow dot instead, which is a
  different statement.
* **Ctrl+S, optional autosave, Save/Discard/Cancel, and real undo.** One
  save path serves both the timer and the keyboard. Undo was being
  destroyed by saving, by resizing the window, and by changing the font in
  Settings — all three were the same root cause (view operations editing
  the live document), and saving now works on a clone instead.
* **Window geometry and pane splits persist** across restarts.

## If a migration ever goes wrong: `diagnose_data.py`

```
python diagnose_data.py
```

A read-only report on every data folder Jortle knows about — the current
one, any older one, and anything left behind by an interrupted migration.
For each it prints the size, when it was last written, whether the
database opens and passes SQLite's integrity check, how many entries,
projects, notes, events and markers are in it, how many entries actually
have writing in them, and whether Jortle would treat it as real data or as
scaffolding. It ends by saying what the app would do on the next launch,
and why.

It opens everything read-only and changes nothing, so it is always safe to
run — including *before* letting a migration happen, if you'd rather look
first.

## What changed in the third Jortle round

* **The migration no longer fails when the destination already exists.**
  It was promoting the validated copy with a plain directory rename, which
  on Windows raises `[WinError 183]` if anything is already at that name —
  and something was. The promotion now classifies the existing folder
  first and handles all three cases (real data, scaffolding, unreadable)
  instead of assuming one of them. A second Jortle process starting at the
  same time is handled too: the loser keeps its copy aside rather than
  failing.
* **New events default to 70% transparency, as they were always meant to.**
  `CalendarEvent.opacity` defaulted to `100` in the dataclass, and a "new
  event" is exactly that constructor — so every new event opened the editor
  at fully opaque and then saved 100 as though you had chosen it. The
  sentinel that means "no transparency chosen" could never reach a new
  event at all.
* **"Highlight work hours" applies immediately.** The setting was being
  saved correctly and nothing was repainting, because both timelines read
  it through a shared preferences object that nothing was telling to
  re-read. That is now part of the single settings-changed notification,
  so the tint appears and disappears as you toggle it.
* **All AI functionality is gone.** No AI Suggestion tab, no model
  downloads, no inference backend, no GPU probing, no Experimental
  Settings window, and `llama-cpp-python` is no longer a dependency —
  `build_windows.bat` asks you nothing and installs two packages. Eight
  modules were deleted outright.
* **Reader's Notes is untouched**, in both Daily Notes and Projects.
  It shared a pane with the AI panel but never depended on it, so it now
  simply fills that pane, with no tab bar and no gap.
* **Nothing was migrated away to remove the AI.** The old AI settings keys
  are still in your database, unread. If a `models` folder with a
  downloaded model in it is still on your disk, it is left exactly where
  it is — nothing reads it, nothing recreates it, and File → Data Usage
  will tell you how big it is so you can delete it yourself.

## What changed after the backup/restore report

Two bugs, either of which could lose writing on its own.

* **Backups could silently omit recent writing.** The export copied
  `journal.db` as a file while the app was running in write-ahead-log
  mode, so anything since SQLite's last internal checkpoint — which
  happens on its own schedule, not on yours — stayed in `journal.db-wal`
  and never reached the zip. On a young database nothing has been
  checkpointed at all and the exported file had no tables in it. The
  export now uses SQLite's backup API, which sees everything committed and
  produces a self-contained file. The same mistake was in the safety copy
  taken before a schema migration (`commit()` where it needed
  `checkpoint()`); that's fixed too.
* **Restore could abort silently and then be undone.** Restoring builds a
  new database connection and re-points every object that holds one — from
  a hand-written list that had gone stale and missed the calendar
  preferences. The first call into it hit a closed connection and threw,
  before the editors were reloaded, so no error appeared, the pre-restore
  text stayed on screen, and the next save wrote it back over the restored
  entry. The list is gone: the app now finds every object holding the old
  handle and repoints all of them. A restore that fails is reported and
  leaves your data exactly as it was, and the editors are made clean
  before the swap so nothing on screen can be written back afterwards.

## What the bug hunt found

After the backup/restore incident, both bugs turned out to be the same
mistake — **one fact stored in two places, where one copy stopped being
updated** — so the codebase was searched for that shape rather than tidied.
Four more instances, in rough order of how much they could cost you:

* **Find Unused Photos could delete a photo you were still looking at.** It
  searched entries, projects and saved project versions — a list written
  before Reader's Notes could hold an image. Paste a paragraph with a photo
  from your journal into the notes beside it, delete it from the journal,
  and that file became a deletion candidate while still on screen. It now
  scans every text column of every table, so a new feature that stores rich
  text is covered the day it is added. Being over-cautious here keeps a
  file; being under-cautious deletes one.
* **The readable archive left out Projects entirely** — the index, the
  writing, the version history and the project Reader's Notes. The database
  goes into the archive too, so nothing was lost, but the half of the
  archive that is meant to be readable with no software at all covered only
  the journal. Projects now get an index, a page each, and a readable page
  per saved version.
* **The archive dropped anything whose plain text was empty.** A Reader's
  Note that is a single photo with no caption has no plain text, so it was
  filtered out — the same mistake as treating `<p></p>` as an empty entry. An
  image is content.
* **Migration validation checked six named tables.** `project_versions` was
  not one of them, so a copy with damaged version history would have
  validated and been promoted. It now walks every table the database
  actually has.

The corresponding fix for the second shape: `commit()` does not flush the
write-ahead log, and the one place still claiming it did — the safety copy
taken before a schema migration — now calls `checkpoint()`. A test asserts
that no database-file copy anywhere is missing that step.

`diagnose_data.py` also grew the question worth asking before deleting an
old data folder: not "is this old" but **"does it hold a single character
the current journal is missing"**. It compares every folder it finds against
the one in use and names the dates, and `recover_entry.py` accepts one of
those folders directly.

## Untimed events, and Tasks

**An untimed event looks and behaves the same in both calendars.** It sits
in a strip above the timeline, under the day it belongs to — one column in
the Day View, seven in the Weekly Schedule — and the strip disappears when
there is nothing untimed to show. Double-click one to edit it; double-click
empty space in the strip to create one on that day. It is literally the same
widget in both views (`app/all_day_strip.py`), so the two cannot drift.

Before this, the Day View had a fixed-height list sitting *outside* the
calendar with checkboxes on it, left over from the retired Daily Tasks
feature — so "untimed event" meant one thing on a single day and another
across a week.

**Tasks are a separate thing**, above the calendar. A task has a checkbox
and a line of text; no time, no duration, no colour, no place on the grid. It is its own table, and it is deliberately
*not* the old `tasks` table — those rows were converted into calendar events
years of versions ago, and reading them here would show you your old tasks a
second time beside the events they already became.

* the boundary between Tasks and the calendar **drags**; until you drag it,
  the pane is as tall as the day's tasks (up to a few rows), and once you
  have, your height is kept;
* on a wide pane the tasks flow into **several columns**; the list scrolls
  when they don't all fit;
* with no tasks, only the "Tasks +" heading shows — **+** adds one;
* a completed task stays visible, **struck through and greyed**, across days
  and restarts;
* double-click a task to edit it, right-click for edit/delete, Delete to
  remove the selected one;
* **Add task…** sits beside **Add event…**, because they make different
  things.

## Double-click to create

Double-clicking empty space on either timeline starts a new one-hour event
at that time, through the same editor every other creation route uses. The
Weekly Schedule takes the date from the column you clicked; the Day View
uses the selected day. It snaps the same way a drag does, so double-clicking
at 2:07 gives you 2:00–3:00, and double-clicking at 11:30 PM gives you
11:30 PM–12:30 AM, ending on the next day.

Double-clicking an *existing* event still edits it and never creates a
second one underneath. Drag-to-create, dragging to move, edge-resizing and
scrolling are all untouched.

## What changed in Group 1 (the jortle_claude rename, entries, history)

* **The app is `jortle_claude`** everywhere it names itself: window title,
  `jortle_claude.py`, `run_jortle_claude.bat`, `dist\jortle_claude\jortle_claude.exe`,
  the installer (with its own AppId, so it never replaces a Jortle install),
  the Desktop shortcut, backups (`jortle_claude-backup-*.zip`) and the
  archive footer. Data moves — by copying — from `%APPDATA%\Jortle` to
  `%APPDATA%\jortle_claude`. Old backups still restore, whatever they are
  called.
* **The "ghost lines" are fixed at the source.** Saving used to leave a
  setting on the document that made Qt wrap it in a hidden table; reading
  that back added one blank line whenever the entry's first line was blank —
  so an empty day grew a line every time it was saved, and so did any entry
  that started with a blank line. Saving no longer does that, and entries
  stored the old way load with exactly the lines they had. `diagnose_data.py`
  lists written entries that start with blank lines stored the old way, in
  case some of those lines were added by the bug; it changes nothing.
* **Written vs blank is one rule** (`saving.document_has_content`: visible
  text or an image), used for the calendar dots, Reader's Notes' hollow dot,
  the archive, migration checks and version history — and called directly
  from SQL rather than re-written there.
* **Viewing never writes.** Journal, Reader's Notes and Projects save only
  what you actually changed; a blank document never creates an entry;
  emptying an entry deletes it (its text stays in History).
* **Version history** for daily entries, **File → Recovery** for large
  deletions (entries and projects), the **Autosave** switch and **Unsaved
  changes** flag in the status bar, Save/Discard/Cancel before restoring a
  backup, and **one running copy at a time**.

## What changed in Group 3 (calendar, navigation, Tasks, folders)

* Four workspaces: Daily Jorts, Weekly Schedule, Yearly Calendar (new),
  Projects.
* Events can cross midnight and span days, untimed ones included; events can
  repeat, with "this / this and following / all" when changing one. Both
  calendar views now share one grid and one set of event commands.
* New events are 70% opaque by default.
* The Weekly Schedule follows the selected day and steps by week as well as
  by day; "Show This Day's Week" is gone.
* Month and year titles; today is a filled circle in every month grid.
* Tasks (formerly "To do"): a draggable boundary with the calendar, columns
  on wide panes, greyed when done, a "+" that is always there.
* Pane widths are remembered across restarts again (they were being reset),
  and the month pane is one width in Daily Jorts and the Weekly Schedule.
* Projects live in nested folders; old categories became folders.

## Possible future additions (not built, since you asked to hold off)

You mentioned interest in eventually viewing this on your phone, but
asked not to act on that yet since you haven't decided about internet
accessibility. Nothing here was built toward that — no server, no sync,
and no ongoing network code of any kind — the app makes no network
requests at all. The plain SQLite + HTML/Markdown + files format this app
uses is a reasonable foundation for phone access whenever you're ready to
consider that.
