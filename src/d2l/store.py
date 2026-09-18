"""Local SQLite store: data/d2l.db. The one place everything else (dashboard, MCP, REST, notifications) reads from.

Each sync upserts rows and returns what is new or changed; `sync.py` turns those diffs into `events`, which is the
"what's new" feed and the notification queue. Full-text search (FTS5) covers announcements, grade items, content
titles and the extracted text of downloaded files.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .session import ROOT

DB = ROOT / "data" / "d2l.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY, name TEXT, code TEXT, short TEXT, section TEXT,
  academic INTEGER, current INTEGER, start TEXT, "end" TEXT, url TEXT, archived INTEGER DEFAULT 0, archived_at TEXT);
CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY, course_id INTEGER, title TEXT, date TEXT, body TEXT, html TEXT, attachments TEXT);
CREATE TABLE IF NOT EXISTS assignments (
  id INTEGER PRIMARY KEY, course_id INTEGER, name TEXT, due TEXT, instructions TEXT,
  submitted INTEGER, status TEXT, score TEXT);
CREATE TABLE IF NOT EXISTS grades (
  id INTEGER PRIMARY KEY, course_id INTEGER, name TEXT, category TEXT, type TEXT,
  max_points REAL, weight REAL, grade TEXT, points REAL, out_of REAL, comments TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS quizzes (
  id INTEGER PRIMARY KEY, course_id INTEGER, name TEXT, start TEXT, "end" TEXT, due TEXT, active INTEGER);
CREATE TABLE IF NOT EXISTS calendar (
  id INTEGER PRIMARY KEY, course_id INTEGER, title TEXT, start TEXT, "end" TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS content (
  id INTEGER PRIMARY KEY, course_id INTEGER, module TEXT, title TEXT, kind TEXT, url TEXT, ext TEXT,
  modified TEXT, file_path TEXT, file_status TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, course_id INTEGER, ref TEXT,
  summary TEXT, detail TEXT, url TEXT, sent INTEGER DEFAULT 0, UNIQUE(kind, ref));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(kind, ref UNINDEXED, course_id UNINDEXED, title, body,
  tokenize = 'porter unicode61');
"""

# columns compared to decide "changed" (everything except bulky or derived ones)
WATCH = {
    "announcements": ("title", "body"),
    "assignments": ("name", "due", "submitted", "score"),
    "grades": ("grade",),
    "quizzes": ("name", "start", "end", "due"),
    "content": ("title", "modified"),
    "calendar": ("title", "start"),
    "courses": ("name",),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DB.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    _migrate(db)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def _migrate(db) -> None:
    """Bring databases made by older versions up to the current schema."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(courses)")}
    if "archived" not in cols:
        db.execute("ALTER TABLE courses ADD COLUMN archived INTEGER DEFAULT 0")
        db.execute("ALTER TABLE courses ADD COLUMN archived_at TEXT")


def rows(db, sql: str, *args) -> list[dict]:
    return [dict(r) for r in db.execute(sql, args)]


def get_meta(db, key: str, default=None):
    r = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(db, key: str, value) -> None:
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def replace_course_rows(db, table: str, course_id: int, new: list[dict]) -> list[tuple[dict, dict | None]]:
    """Make `table` hold exactly `new` for this course. Returns (row, old_row_or_None) for new/changed rows."""
    old = {r["id"]: r for r in rows(db, f"SELECT * FROM {table} WHERE course_id = ?", course_id)}
    changes = []
    for r in new:
        prev = old.pop(r["id"], None)
        if prev is None or any(prev.get(k) != r.get(k) for k in WATCH[table]):
            changes.append((r, prev))
        # keep columns the fetch doesn't send at all (downloaded file path/text); everything it sends wins
        merged = {**(prev or {}), **r}
        cols = ", ".join(f'"{k}"' for k in merged)
        db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(merged))})",
                   list(merged.values()))
    for gone in old:
        db.execute(f"DELETE FROM {table} WHERE id = ?", (gone,))
    return changes


def update_courses(db, courses: list[dict]) -> tuple[list[dict], list[dict]]:
    """Store the courses being synced as active; archive the ones that dropped out (term ended) instead of deleting
    them, so last term's grades, announcements and files stay searchable. Returns (newly added, newly archived)."""
    before = {r["id"]: r for r in rows(db, "SELECT id, short, archived FROM courses")}
    now_ids = {c["id"] for c in courses}
    added = [c for c in courses if c["id"] not in before or before[c["id"]]["archived"]]
    archived = [r for i, r in before.items() if i not in now_ids and not r["archived"]]
    for c in courses:
        row = {**c, "archived": 0, "archived_at": None}
        cols = ", ".join(f'"{k}"' for k in row)
        db.execute(f"INSERT OR REPLACE INTO courses ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    for r in archived:
        db.execute("UPDATE courses SET archived = 1, archived_at = ? WHERE id = ?", (now(), r["id"]))
    return added, archived


def add_event(db, kind: str, ref: str, course_id: int | None, summary: str, detail: str = "", url: str = "",
              sent: bool = False) -> bool:
    """Queue an event once; the (kind, ref) pair is unique, so re-running a sync never repeats it."""
    cur = db.execute("INSERT OR IGNORE INTO events (ts, kind, course_id, ref, summary, detail, url, sent) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (now(), kind, course_id, ref, summary, detail, url, int(sent)))
    return cur.rowcount > 0


def rebuild_search(db) -> None:
    db.execute("DELETE FROM search")
    db.execute("INSERT INTO search SELECT 'announcement', id, course_id, title, body FROM announcements")
    db.execute("INSERT INTO search SELECT 'assignment', id, course_id, name, instructions FROM assignments")
    db.execute("INSERT INTO search SELECT 'grade', id, course_id, name, coalesce(category, '') || ' ' || coalesce(comments, '') FROM grades")
    db.execute("INSERT INTO search SELECT 'quiz', id, course_id, name, '' FROM quizzes")
    db.execute("INSERT INTO search SELECT CASE WHEN text IS NULL THEN 'content' ELSE 'file' END, id, course_id, title, "
               "coalesce(module, '') || ' ' || coalesce(text, '') FROM content")


def prune(db) -> None:
    """Drop rows whose course isn't stored at all (active or archived) — leftovers, never an archived term."""
    for t in ("announcements", "assignments", "grades", "quizzes", "calendar", "content"):
        db.execute(f"DELETE FROM {t} WHERE course_id NOT IN (SELECT id FROM courses)")
