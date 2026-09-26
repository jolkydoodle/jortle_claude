"""Round 29 — the bounded hunt for two bug shapes.

The backup/restore data loss came from two bugs that were the same mistake
in different clothes:

    one fact stored in two places, where one copy stopped being updated

Shape 1: something LISTS what it should DERIVE. The restore's hand-written
list of objects holding a database handle had gone stale.

Shape 2: something copies a file the database owns. The export copied
journal.db while committed writes were still in the write-ahead log.

This suite pins what the hunt found. Each check is written so that it fails
if the derivation is ever turned back into a list — which is the actual
regression risk, since a list is always correct on the day it is written.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

import isolation

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
isolation.isolate(prefix="jortle-r29-")
os.environ["HOME"] = tempfile.mkdtemp(prefix="jortle-r29-home-")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.archive import export_archive  # noqa: E402
from app.cleanup import find_unused_photos  # noqa: E402
from app.database import Database  # noqa: E402
from app.paths import get_attachments_dir  # noqa: E402

failures = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def photo(relative: str) -> pathlib.Path:
    path = get_attachments_dir() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    return path


db = Database()

# ================================================== Find Unused Photos
print("\n--- a photo is 'unused' only if NOTHING in the database points at it ---")
# The original bug: this scanned entries, projects and project versions,
# and was never updated when Reader's Notes became rich text. Copy a
# paragraph with a photo out of the journal into the notes beside it and
# the file became a deletion candidate while still on screen.
photo("2026/09/in_notes.png")
db.save_notes("date", "2026-09-15",
              '<p>The IMP diagram: <img src="2026/09/in_notes.png" /></p>',
              content_text="The IMP diagram:")
unused = {p.name for p in find_unused_photos(db)}
check("a photo referenced only from a day's Reader's Notes is not 'unused'",
      "in_notes.png" not in unused)

project = db.create_project("The Novel")
photo(f"projects/{project.id}/notes/in_project_notes.png")
db.save_notes("project", str(project.id),
              f'<p><img src="projects/{project.id}/notes/in_project_notes.png" /></p>',
              content_text="")
unused = {p.name for p in find_unused_photos(db)}
check("nor one referenced only from a PROJECT's Reader's Notes",
      "in_project_notes.png" not in unused)

# The stores it always did cover must still be covered.
photo("2026/09/in_entry.png")
db.upsert_entry("2026-09-16", body_md='<p><img src="2026/09/in_entry.png" /></p>',
                body_format="html", body_text="")
photo("2026/09/in_project.png")
db.save_project_content(project.id, '<p><img src="2026/09/in_project.png" /></p>',
                        content_text="")
db.add_version(project.id, '<p><img src="2026/09/in_version.png" /></p>',
               label="draft", kind="manual", content_format="html")
photo("2026/09/in_version.png")
unused = {p.name for p in find_unused_photos(db)}
for name in ("in_entry.png", "in_project.png", "in_version.png"):
    check(f"{name} is still recognised as referenced", name not in unused)

# And a genuinely orphaned file must still be found, or the feature is
# pointless — being over-cautious is the safe direction, not a free pass.
photo("2026/09/nobody_wants_me.png")
unused = {p.name for p in find_unused_photos(db)}
check("a genuinely unreferenced photo IS still reported",
      "nobody_wants_me.png" in unused)

print("\n--- and the scan is derived from the schema, not from a list ---")
# A table that did not exist when the scan was written. If the scan still
# named its stores, this reference would be invisible to it.
db._conn.execute("CREATE TABLE some_future_feature (id INTEGER PRIMARY KEY, body TEXT)")
db._conn.execute("INSERT INTO some_future_feature (body) VALUES (?)",
                 ('<p><img src="2026/09/future.png" /></p>',))
db._conn.commit()
photo("2026/09/future.png")
unused = {p.name for p in find_unused_photos(db)}
check("a photo referenced from a table added later is found automatically",
      "future.png" not in unused)

# ================================================== the readable archive
print("\n--- the readable archive contains every kind of writing ---")
db.upsert_entry("2026-09-17", body_md="<p>A journal day.</p>",
                body_format="html", body_text="A journal day.")
db.save_project_content(project.id, "<p>Chapter one. It was a dark night.</p>",
                        content_text="Chapter one. It was a dark night.")
db.add_version(project.id, "<p>An earlier draft.</p>", label="first draft",
               kind="manual", content_format="html")
archived = db.create_project("An Abandoned Thing")
db.save_project_content(archived.id, "<p>Never finished.</p>", content_text="Never finished.")
db.set_project_archived(archived.id, True)

root = pathlib.Path(tempfile.mkdtemp()) / "archive"
summary = export_archive(db, root)
check(f"projects are exported at all ({summary.project_count})",
      summary.project_count >= 2)
check("there is a project index", (root / "projects.html").is_file())
project_page = (root / "projects" / f"{project.id}.html").read_text(encoding="utf-8")
check("a project's writing is in it", "dark night" in project_page)
# The note on this project is a single photo with no caption: no plain
# text at all. Filtering the archive on plain text alone dropped it.
check("its Reader's Notes are in it, even as a photo with no caption",
      "in_project_notes.png" in project_page)
check("its version history is listed", "first draft" in project_page)
versions = list((root / "projects").glob(f"{project.id}-v*.html"))
check(f"each saved version has its own readable page ({len(versions)})", versions)
check("an earlier draft is readable in full",
      any("earlier draft" in v.read_text(encoding="utf-8") for v in versions))
check("archived projects are included too",
      (root / "projects" / f"{archived.id}.html").is_file())
check("the index links to the projects",
      "projects.html" in (root / "index.html").read_text(encoding="utf-8"))
check("the journal side is still there",
      (root / "entries" / "2026-09-17.html").is_file())

# ================================================== shape 2, everywhere
print("\n--- nothing copies the database file without flushing the log first ---")
from app import archive as archive_module  # noqa: E402
from app import backup as backup_module  # noqa: E402
from app import data_migration as dm  # noqa: E402

source = pathlib.Path(__file__).resolve().parent.parent / "app"
offenders = []
for module in sorted(source.glob("*.py")):
    text = module.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if "copy2(" not in line or "db_path" not in line:
            continue
        # The twenty lines before a database-file copy must flush the log.
        before = "\n".join(text.splitlines()[max(0, lineno - 20):lineno])
        if "checkpoint()" not in before:
            offenders.append(f"{module.name}:{lineno}")
check("every database-file copy checkpoints first: " + "; ".join(offenders),
      not offenders)

# commit() is NOT a flush, and the comment that claimed it was is what hid
# this in the pre-migration safety copy for three rounds.
db_text = (source / "database.py").read_text(encoding="utf-8")
check("no comment still claims commit() flushes the write-ahead log",
      "commit()  # flush WAL" not in db_text)

# The export's snapshot must survive on a database whose every write is
# still in the log.
fresh_dir = pathlib.Path(tempfile.mkdtemp())
fresh = Database(str(fresh_dir / "journal.db"))
fresh.upsert_entry("2026-02-02", body_md="<p>unflushed</p>", body_format="html",
                   body_text="unflushed")
snapshot = fresh_dir / "snap.db"
backup_module._snapshot_database(fresh_dir / "journal.db", snapshot)
con = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
row = con.execute("SELECT body_text FROM entries WHERE date='2026-02-02'").fetchone()
check("a snapshot of an unflushed database still has the writing",
      row is not None and row[0] == "unflushed")
con.close()
fresh.close()

# ================================================== shape 1, in migration
print("\n--- migration validation reads every table, not a chosen few ---")
victim = pathlib.Path(tempfile.mkdtemp()) / "data"
victim.mkdir()
seed = Database(str(victim / "journal.db"))
seed.upsert_entry("2026-03-03", body_md="<p>hello</p>", body_format="html",
                  body_text="hello")
p2 = seed.create_project("Checked?")
seed.add_version(p2.id, "<p>v1</p>", label="v1", kind="manual", content_format="html")
seed.checkpoint()
seed.close()
checks = dm.validate_data_dir(victim)
reported = " ".join(checks)
for table in ("project version history", "journal entries", "Reader's Notes",
              "calendar events", "Day Markers", "settings"):
    check(f"validation reports {table}", table in reported)

# A table nobody listed must still be walked.
con = sqlite3.connect(str(victim / "journal.db"))
con.execute("CREATE TABLE invented_later (x TEXT)")
con.execute("INSERT INTO invented_later VALUES ('y')")
con.commit()
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.close()
check("a table added later is validated without being named",
      "invented_later" in " ".join(dm.validate_data_dir(victim)))

db.close()
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
