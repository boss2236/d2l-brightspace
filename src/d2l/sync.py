"""One sync: fetch this term's courses from Brightspace, store them, record what changed, download new course files,
send notifications, and write the JSON exports.

Safe to run on a timer: a lock stops overlapping runs, requests are serial and paced (api.PAUSE), files are only
downloaded when they're new or changed, and the first ever run records a baseline instead of flooding you with
"new" alerts for everything that already existed.
"""
import fcntl
import json
import re
from datetime import datetime, timedelta, timezone

from . import fetch, notify, store
from .api import Api, NotFound
from .extract import MAX_BYTES, TEXT_TYPES, text_of
from .query import local
from .session import base_url, signed_in_page

FILES = fetch.OUT / "files"
LOCK = fetch.OUT / ".sync.lock"


def run(headless: bool = True, scope: str = "current", files: bool = True, quiet: bool = False) -> dict:
    fetch.OUT.mkdir(exist_ok=True)
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another sync is already running")
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
        store.replace_courses(db, cs)
        store.prune(db, [c["id"] for c in cs])
        print(f"{len(cs)} courses ({scope}){' — first run, recording a baseline' if first else ''}")

        def ev(*a, **k):
            if not first and store.add_event(db, *a, **k):
                stats["events"] += 1

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


def _download(api: Api, db) -> int:
    """Fetch course files that are new or changed and pull their text out for search. Videos are skipped."""
    todo = store.rows(db, "SELECT id, course_id, title, ext FROM content WHERE kind = 'File' AND file_status IS NULL")
    n = 0
    for t in todo:
        if t["ext"] not in TEXT_TYPES:
            db.execute("UPDATE content SET file_status = 'skipped' WHERE id = ?", (t["id"],))
            continue
        try:
            data = api.download(t["course_id"], t["id"])
        except NotFound:
            db.execute("UPDATE content SET file_status = 'missing' WHERE id = ?", (t["id"],))
            continue
        except Exception as e:
            print(f"  ! {t['title']}: {e}")
            continue                                  # stays NULL, retried next sync
        if len(data) > MAX_BYTES:
            db.execute("UPDATE content SET file_status = 'too_big' WHERE id = ?", (t["id"],))
            continue
        folder = FILES / str(t["course_id"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{t['id']}-{re.sub(r'[^A-Za-z0-9._ -]+', '_', t['title'])[:80].strip()}.{t['ext']}"
        path.write_bytes(data)
        db.execute("UPDATE content SET file_path = ?, file_status = 'ok', text = ? WHERE id = ?",
                   (str(path.relative_to(fetch.OUT.parent)), text_of(path), t["id"]))
        db.commit()
        n += 1
        print(f"  ↓ {t['title'][:70]}")
    return n


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
