"""One sync: fetch this term's courses from Brightspace, store them, record what changed, download new course files,
send notifications, and write the JSON exports.

Safe to run on a timer: a lock stops overlapping runs, requests are serial and paced (api.PAUSE), files are only
downloaded when they're new or changed, and the first ever run records a baseline instead of flooding you with
"new" alerts for everything that already existed.
"""
import io
import os
import json
import re
import shutil
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fetch, notify, store
from .api import Api, NotFound
from .extract import MAX_BYTES, TEXT_TYPES, text_of
from .query import local
from .session import base_url, signed_in_page

FILES = fetch.OUT / "files"
LOCK = fetch.OUT / ".sync.lock"


@contextmanager
def _only_one_sync():
    """An OS-level lock (released automatically if the process dies) so two syncs never overlap."""
    fetch.OUT.mkdir(exist_ok=True)
    f = open(LOCK, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit("another sync is already running")
    try:
        yield
    finally:
        f.close()


def run(headless: bool = True, scope: str = "current", files: bool = True, quiet: bool = False) -> dict:
    with _only_one_sync():
        try:
            return _run(headless, scope, files, quiet)
        except SystemExit as e:
            if "Session expired" in str(e) or "No saved session" in str(e):
                with store.connect() as db:      # one alert per day, not one per timer run
                    store.add_event(db, "session_expired", datetime.now().strftime("%Y-%m-%d"), None,
                                    "Brightspace session expired — run `uv run d2l login`")
                    if not quiet:
                        notify.flush(db)
            raise


def _run(headless, scope, want_files, quiet) -> dict:
    stats = {"events": 0, "downloaded": 0}
    with signed_in_page(headless=headless) as page, store.connect() as db:
        api = Api(page)
        first = store.get_meta(db, "last_sync") is None
        cs = fetch.pick(fetch.courses(api), scope)
        added, archived = store.update_courses(db, cs)
        store.prune(db)
        print(f"{len(cs)} courses ({scope}){' — first run, recording a baseline' if first else ''}")

        def ev(*a, **k):
            if not first and store.add_event(db, *a, **k):
                stats["events"] += 1

        for c in added:
            ev("new_course", f"course:{c['id']}", c["id"], f"New course: {c['short']}", url=c["url"])
        for c in archived:
            ev("course_archived", f"archived:{c['id']}", c["id"],
               f"{c['short']} archived — its term ended; grades, announcements and files are kept")
            print(f"  archived {c['short']} (term ended)")

        for c in cs:
            ou, news_url = c["id"], f"{base_url()}/d2l/lms/news/main.d2l?ou={c['id']}"
            counts = {}
            for table, fn in (("announcements", fetch.announcements), ("grades", fetch.grades),
                              ("assignments", fetch.assignments), ("quizzes", fetch.quizzes),
                              ("content", fetch.content)):
                new = fn(api, ou)
                counts[table] = len(new)
                changes = store.replace_course_rows(db, table, ou, new)
                if table == "announcements":
                    for r, old in changes:
                        if old is None:
                            ev("new_announcement", f"ann:{r['id']}", ou, r["title"], r["body"][:400], news_url)
                elif table == "grades":
                    for r, old in changes:
                        if r["grade"] is not None:
                            kind = "grade_changed" if old and old.get("grade") else "new_grade"
                            ev(kind, f"grade:{r['id']}:{r['grade']}", ou, f"{r['name']}: {r['grade']}",
                               r.get("comments") or "", f"{base_url()}/d2l/lms/grades/my_grades/main.d2l?ou={ou}")
                elif table == "assignments":
                    for r, old in changes:
                        if old is None:
                            ev("new_assignment", f"asg:{r['id']}", ou, r["name"] + (f" — due {local(r['due'])}" if r["due"] else ""))
                        elif old.get("due") != r["due"] and r["due"]:
                            ev("due_changed", f"due:{r['id']}:{r['due']}", ou, f"{r['name']} now due {local(r['due'])}")
                elif table == "quizzes":
                    for r, old in changes:
                        if old is None:
                            ev("new_quiz", f"quiz:{r['id']}", ou, r["name"])
                elif table == "content":
                    for r, old in changes:           # changed file on the server -> fetch it again
                        if old is not None:
                            db.execute("UPDATE content SET file_status = NULL WHERE id = ?", (r["id"],))
                    added = [r for r, old in changes if old is None]
                    if added:
                        names = ", ".join(r["title"] for r in added[:4]) + (f" +{len(added) - 4} more" if len(added) > 4 else "")
                        ev("new_files", f"files:{ou}:{min(r['id'] for r in added)}:{len(added)}", ou,
                           f"{len(added)} new in course content: {names}", url=f"{base_url()}/d2l/le/content/{ou}/Home")
            print(f"  {c['short'][:42]:<42} " + "  ".join(f"{k[:5]} {v:>3}" for k, v in counts.items()))

        ids = [c["id"] for c in cs]
        cal = fetch.calendar(api, ids) if ids else []
        for ou in ids:
            store.replace_course_rows(db, "calendar", ou, [e for e in cal if e["course_id"] == ou])

        if want_files:
            stats["downloaded"] = _download(api, db)
        _due_soon(db, stats)
        store.rebuild_search(db)
        store.set_meta(db, "last_sync", store.now())
        db.commit()
        export(db)
        if first:
            print("baseline recorded — from the next sync on, anything new triggers a notification")
        if not quiet:
            sent = notify.flush(db)
            if sent:
                print("notified:", ", ".join(f"{k} {v}" for k, v in sent.items()))
    print(f"{stats['events']} new events, {stats['downloaded']} files downloaded")
    return stats


ON_DEMAND_MAX = 500 * 1024 * 1024        # files fetched because you clicked them (videos included)


def _store_file(api: Api, db, t: dict, limit: int) -> str:
    """Download one content topic into data/files/<course>/, extract its text if it has any, record it.
    Returns the new file_status: ok | missing | too_big."""
    try:
        data = api.download(t["course_id"], t["id"])
    except NotFound:
        db.execute("UPDATE content SET file_status = 'missing' WHERE id = ?", (t["id"],))
        return "missing"
    if len(data) > limit:
        db.execute("UPDATE content SET file_status = 'too_big' WHERE id = ?", (t["id"],))
        return "too_big"
    path = _save_bytes(t, data)
    db.execute("UPDATE content SET file_path = ?, file_status = 'ok', text = ? WHERE id = ?",
               (str(path.relative_to(fetch.OUT.parent)), text_of(path) if t["ext"] in TEXT_TYPES else None, t["id"]))
    db.commit()
    return "ok"


OFFICE = {"docx", "pptx", "xlsx", "zip"}          # formats that *are* zip files — leave them alone


def _save_bytes(t: dict, data: bytes) -> Path:
    """Write a downloaded topic to data/files/<course>/. Brightspace wraps HTML lessons and videos in a zip: a
    lesson is unpacked into its own folder (page + images, served as a site), a single wrapped file is stored as
    itself so it opens and plays normally."""
    folder = FILES / str(t["course_id"])
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", t["title"])[:80].strip() or "file"
    ext = t["ext"] or "bin"
    plain = folder / f"{t['id']}-{name}.{ext}"
    if data[:4] != b"PK\x03\x04" or ext in OFFICE:
        plain.write_bytes(data)
        return plain
    z = zipfile.ZipFile(io.BytesIO(data))
    members = [m for m in z.infolist() if not m.is_dir()]
    if ext in ("html", "htm"):
        dest = folder / str(t["id"])
        shutil.rmtree(dest, ignore_errors=True)
        z.extractall(dest)                       # extractall drops absolute paths and ".." components
        pages = [m for m in members if m.filename.lower().endswith((".html", ".htm"))] or members
        main = min(pages, key=lambda m: (m.filename.count("/"), len(m.filename)))
        return dest / main.filename
    same = [m for m in members if m.filename.lower().endswith("." + ext)]
    if len(same) == 1:
        plain.write_bytes(z.read(same[0]))
        return plain
    kept = plain.with_suffix(".zip")             # several files inside: keep the archive as a download
    kept.write_bytes(data)
    return kept


def unpack_stored(db) -> int:
    """One-off fix for files stored before unwrapping existed: unpack zipped lessons/videos already on disk."""
    n = 0
    for r in store.rows(db, "SELECT id, course_id, title, ext, file_path FROM content WHERE file_status = 'ok'"):
        p = fetch.OUT.parent / r["file_path"]
        if (r["ext"] or "") in OFFICE or not p.is_file() or not zipfile.is_zipfile(p) or p.suffix == ".zip":
            continue
        data = p.read_bytes()
        new = _save_bytes(r, data)
        if new != p:
            p.unlink()
        db.execute("UPDATE content SET file_path = ? WHERE id = ?", (str(new.relative_to(fetch.OUT.parent)), r["id"]))
        n += 1
    return n


def _download(api: Api, db) -> int:
    """Fetch course files that are new or changed and pull their text out for search. Videos and images are left
    for on-demand download (`get_file`), so a sync stays quick."""
    todo = store.rows(db, "SELECT id, course_id, title, ext FROM content WHERE kind = 'File' AND file_status IS NULL")
    n = 0
    for t in todo:
        if t["ext"] not in TEXT_TYPES:
            db.execute("UPDATE content SET file_status = 'skipped' WHERE id = ?", (t["id"],))
            continue
        try:
            if _store_file(api, db, t, MAX_BYTES) == "ok":
                n += 1
                print(f"  ↓ {t['title'][:70]}")
        except Exception as e:
            print(f"  ! {t['title']}: {e}")           # stays NULL, retried next sync
    return n


def get_file(topic_id: int, headless: bool = True):
    """The local copy of one course file, downloading it from Brightspace first if needed (e.g. a video the sync
    skipped, or a file added since the last sync). Raises SystemExit with a readable reason otherwise."""
    with store.connect() as db:
        rows = store.rows(db, "SELECT id, course_id, title, ext, kind, file_path, file_status FROM content WHERE id = ?",
                          int(topic_id))
    if not rows or rows[0]["kind"] != "File":
        raise SystemExit(f"{topic_id} is not a course file (it may be a link — open it in Brightspace)")
    t = rows[0]
    if t["file_status"] == "ok" and t["file_path"] and (fetch.OUT.parent / t["file_path"]).exists():
        return fetch.OUT.parent / t["file_path"]
    with signed_in_page(headless=headless) as page, store.connect() as db:
        status = _store_file(Api(page), db, t, ON_DEMAND_MAX)
        if status == "ok":
            store.rebuild_search(db)
    if status == "missing":
        raise SystemExit("Brightspace itself can't serve this file (it's missing there — the browser gets an "
                         "error too). The same material is often in another section of the course.")
    if status == "too_big":
        raise SystemExit("This file is over 500 MB — open it in Brightspace instead.")
    with store.connect() as db:
        return fetch.OUT.parent / db.execute("SELECT file_path FROM content WHERE id = ?", (int(topic_id),)).fetchone()[0]


def _due_soon(db, stats) -> None:
    """One reminder per item when it is less than 48 h away and still open (also on the first run)."""
    def ev(*a):
        if store.add_event(db, *a):
            stats["events"] += 1
    now = datetime.now(timezone.utc)
    soon = lambda iso: iso and now <= datetime.fromisoformat(iso.replace("Z", "+00:00")) <= now + timedelta(hours=48)
    for a in store.rows(db, "SELECT * FROM assignments WHERE submitted = 0"):
        if soon(a["due"]):
            ev("due_soon", f"asg:{a['id']}:{a['due']}", a["course_id"], f"{a['name']} due {local(a['due'])}")
    for q in store.rows(db, "SELECT * FROM quizzes WHERE active = 1"):
        when = q["due"] or q["end"]
        if soon(when):
            ev("due_soon", f"quiz:{q['id']}:{when}", q["course_id"], f"Quiz closes {local(when)}: {q['name']}")


def export(db) -> None:
    """data/*.json for anything that prefers flat files (and the old `d2l show`)."""
    from . import query
    dumps = {"courses": query.courses(db), "announcements": query.announcements(db, limit=10_000, full=True),
             "assignments": query.assignments(db), "grades": query.grades(db), "deadlines": query.deadlines(db, 60),
             "files": query.files(db)}
    for k, v in dumps.items():
        (fetch.OUT / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1))
