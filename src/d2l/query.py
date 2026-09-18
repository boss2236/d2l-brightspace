"""Read-side of the store: the questions the CLI, dashboard, MCP server and REST API all ask.

Everything here reads data/d2l.db only — never Brightspace — so an AI calling these as often as it likes costs the
university server nothing.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import store


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("D2L_TZ", "Asia/Qatar"))


def local(iso: str | None) -> str | None:
    """'2026-09-20T20:59:59.000Z' -> 'Sun 20 Sep 2026, 23:59' in the institution's time zone."""
    if not iso:
        return None
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz())
    return d.strftime("%a %d %b %Y, %H:%M")


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def course_ids(db, course: str | int | None) -> list[int]:
    """Accept an org unit id, a code ('MATH1030', 'chem1010') or any part of the name ('chemistry')."""
    cs = store.rows(db, "SELECT id, code, name FROM courses")
    if course in (None, "", "all"):
        return [c["id"] for c in cs]
    s = str(course).strip().lower()
    if s.isdigit():
        return [int(s)]
    key = re.sub(r"[\s_-]", "", s)
    hit = [c["id"] for c in cs if re.sub(r"[\s_-]", "", c["code"].lower()).startswith(key)]
    return hit or [c["id"] for c in cs if s in c["name"].lower()]


def _in(ids: list[int]) -> str:
    return "(" + ",".join(str(int(i)) for i in ids) + ")" if ids else "(NULL)"


def courses(db) -> list[dict]:
    out = []
    for c in store.rows(db, "SELECT * FROM courses ORDER BY code"):
        n = lambda t, extra="": db.execute(f"SELECT count(*) FROM {t} WHERE course_id = ? {extra}", (c["id"],)).fetchone()[0]
        out.append({"id": c["id"], "code": c["code"].split("_")[0] + c["code"].split("_")[1] if "_" in c["code"] else c["code"],
                    "name": re.sub(r"^[A-Z]{3,5}\d{4}\s+", "", c["short"] or ""), "section": c["section"], "url": c["url"],
                    "term": f"{local(c['start'])} → {local(c['end'])}" if c["start"] else None, "start": c["start"],
                    "counts": {"announcements": n("announcements"), "files": n("content", "AND file_status = 'ok'"),
                               "graded": n("grades", "AND grade IS NOT NULL"), "grade_items": n("grades"),
                               "assignments": n("assignments"), "quizzes": n("quizzes")}})
    return out


def _names(db) -> dict[int, str]:
    return {r["id"]: r["short"] for r in store.rows(db, "SELECT id, short FROM courses")}


def announcements(db, course=None, since: str | None = None, limit: int = 30, full: bool = False) -> list[dict]:
    names, sql = _names(db), f"SELECT * FROM announcements WHERE course_id IN {_in(course_ids(db, course))}"
    args = []
    if since:
        sql += " AND date >= ?"
        args.append(since)
    out = []
    for a in store.rows(db, sql + " ORDER BY date DESC LIMIT ?", *args, int(limit)):
        body = a["body"] or ""
        out.append({"id": a["id"], "course": names.get(a["course_id"]), "course_id": a["course_id"], "title": a["title"],
                    "posted": local(a["date"]), "date": a["date"],
                    "body": body if full else (body[:280] + "…" if len(body) > 280 else body)})
    return out


def announcement(db, id: int) -> dict | None:
    r = store.rows(db, "SELECT * FROM announcements WHERE id = ?", int(id))
    if not r:
        return None
    a = r[0]
    return {"id": a["id"], "course": _names(db).get(a["course_id"]), "title": a["title"], "posted": local(a["date"]),
            "body": a["body"], "attachments": a["attachments"]}


def grades(db, course=None) -> list[dict]:
    names = _names(db)
    out = []
    for g in store.rows(db, f"SELECT * FROM grades WHERE course_id IN {_in(course_ids(db, course))} "
                            "ORDER BY course_id, category, id"):
        out.append({"course": names.get(g["course_id"]), "course_id": g["course_id"], "item": g["name"],
                    "category": g["category"], "grade": g["grade"],
                    "points": None if g["points"] is None else f"{g['points']:g} / {g['out_of']:g}" if g["out_of"] else f"{g['points']:g}",
                    "out_of": g["out_of"], "weight": g["weight"], "comments": g["comments"],
                    "graded": g["grade"] is not None})
    return out


def assignments(db, course=None, open_only: bool = False) -> list[dict]:
    names = _names(db)
    sql = f"SELECT * FROM assignments WHERE course_id IN {_in(course_ids(db, course))}"
    if open_only:
        sql += " AND submitted = 0"
    return [{"id": a["id"], "course": names.get(a["course_id"]), "course_id": a["course_id"], "name": a["name"],
             "due": local(a["due"]), "due_utc": a["due"], "submitted": bool(a["submitted"]), "status": a["status"],
             "score": a["score"], "instructions": a["instructions"]}
            for a in store.rows(db, sql + " ORDER BY due IS NULL, due")]


def deadlines(db, days: int = 14, course=None) -> list[dict]:
    """Everything with a date in the next `days` days: unsubmitted assignments, quizzes closing, calendar events."""
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(hours=12), now + timedelta(days=days)
    ids, names, out = _in(course_ids(db, course)), _names(db), []

    def add(kind, title, when, cid, extra=None):
        if when and lo <= _utc(when) <= hi:
            left = _utc(when) - now
            out.append({"kind": kind, "title": title, "course": names.get(cid), "course_id": cid, "when": local(when),
                        "when_utc": when, "in_hours": round(left.total_seconds() / 3600, 1), **(extra or {})})

    for a in store.rows(db, f"SELECT * FROM assignments WHERE course_id IN {ids} AND submitted = 0"):
        add("assignment", a["name"], a["due"], a["course_id"])
    seen = set()
    for q in store.rows(db, f"SELECT * FROM quizzes WHERE course_id IN {ids} AND active = 1"):
        seen.add(q["name"])
        add("quiz closes", q["name"], q["due"] or q["end"], q["course_id"], {"opens": local(q["start"])})
    for e in store.rows(db, f"SELECT * FROM calendar WHERE course_id IN {ids}"):
        if not any(e["title"].startswith(n) for n in seen):       # quiz events duplicate the quiz rows
            add("event", e["title"], e["start"], e["course_id"])
    return sorted(out, key=lambda r: r["when_utc"])


def _fts(q: str) -> str:
    """Turn free text into a safe FTS5 query: every word must appear (prefix match on the last one)."""
    words = re.findall(r"\w+", q)
    return " ".join(f'"{w}"' for w in words[:-1]) + (f' "{words[-1]}"*' if words else "")


def search(db, query: str, course=None, kinds: list[str] | None = None, limit: int = 15) -> list[dict]:
    if not re.search(r"\w", query or ""):
        return []
    names = _names(db)
    sql = (f"SELECT kind, ref, course_id, title, snippet(search, 4, '«', '»', ' … ', 24) AS snippet "
           f"FROM search WHERE search MATCH ? AND course_id IN {_in(course_ids(db, course))}")
    args = [_fts(query)]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    return [{**r, "course": names.get(r["course_id"])}
            for r in store.rows(db, sql + " ORDER BY rank LIMIT ?", *args, int(limit))]


def files(db, course=None) -> list[dict]:
    names = _names(db)
    return [{"id": c["id"], "course": names.get(c["course_id"]), "course_id": c["course_id"], "module": c["module"], "title": c["title"],
             "kind": c["kind"], "type": c["ext"], "url": c["url"], "modified": local(c["modified"]),
             "has_text": bool(c["text"]), "status": c["file_status"], "file_path": c["file_path"]}
            for c in store.rows(db, f"SELECT id, course_id, module, title, kind, ext, url, modified, file_status, file_path, "
                                    f"text IS NOT NULL AND length(text) > 40 AS text FROM content "
                                    f"WHERE course_id IN {_in(course_ids(db, course))} ORDER BY course_id, module, id")]


def document(db, id: int, offset: int = 0, length: int = 20000) -> dict | None:
    r = store.rows(db, "SELECT id, course_id, module, title, ext, url, text FROM content WHERE id = ?", int(id))
    if not r:
        return None
    d, text = r[0], r[0]["text"] or ""
    return {"id": d["id"], "course": _names(db).get(d["course_id"]), "module": d["module"], "title": d["title"],
            "type": d["ext"], "url": d["url"], "total_chars": len(text), "offset": offset,
            "text": text[offset:offset + length] or "(no extracted text — the file may be a scan, a video or a link)",
            "more": offset + length < len(text)}


def events(db, since: str | None = None, limit: int = 50) -> list[dict]:
    names = _names(db)
    sql, args = "SELECT * FROM events", []
    if since:
        sql += " WHERE ts >= ?"
        args.append(since)
    return [{"when": local(e["ts"]), "ts": e["ts"], "kind": e["kind"], "course": names.get(e["course_id"]),
             "course_id": e["course_id"],
             "summary": e["summary"], "detail": e["detail"], "url": e["url"]}
            for e in store.rows(db, sql + " ORDER BY id DESC LIMIT ?", *args, int(limit))]


def course_markdown(db, id: int) -> str:
    """One course as a markdown brief: an AI can load this as context in one go."""
    c = next((c for c in courses(db) if c["id"] == int(id)), None)
    if not c:
        return f"No course {id}"
    lines = [f"# {c['code']} {c['name']} (section {c['section']})", f"Term: {c['term']}  ", f"Brightspace: {c['url']}", ""]
    dl = deadlines(db, 30, id)
    lines += ["## Upcoming (30 days)"] + ([f"- {d['when']} — {d['kind']}: {d['title']}" for d in dl] or ["- nothing dated"])
    lines += ["", "## Grades"]
    for g in grades(db, id):
        lines.append(f"- {g['category'] + ' / ' if g['category'] else ''}{g['item']}: {g['grade'] or 'not graded'}"
                     + (f" ({g['points']})" if g["points"] else "") + (f", weight {g['weight']:g}" if g["weight"] else ""))
    lines += ["", "## Announcements (newest first)"]
    for a in announcements(db, id, limit=100, full=True):
        lines += [f"### {a['title']} — {a['posted']}", a["body"] or "(no text)", ""]
    lines += ["## Course content"]
    for f in files(db, id):
        lines.append(f"- {f['module']} / {f['title']} [{f['type'] or f['kind']}]" + (f" — doc id {f['id']}" if f["has_text"] else ""))
    return "\n".join(lines)
