# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hand your grades to Semestra (a grade planner): its import format, and an optional push after each sync.

    export   the exact JSON Semestra's Import page accepts, per course — paste it in, no screenshots, no AI transcription
    push     POST every course to a Semestra "connector" endpoint with a connector key (see docs/semestra-contract.md)

Semestra's import format (its `importPayloadSchema`):
    {"course": {"name", "term", "credits"},
     "categories": [{"name", "weight", "items": [{"name", "max_points", "achieved_points"}]}]}

Brightspace doesn't map onto that cleanly, so `payload()` also returns human-readable warnings:
  * items outside any category (UDST's "Assignment #1") are placed in the category their name matches ("Assignments")
  * calculated/formula/text grade items (e.g. "Cumulative Mid Term Grade") are left out — they total other items
  * categories with no gradable items are left out so they don't skew the planner

Only courses, grade structure, your grades and upcoming deadlines are ever sent — never announcements, files, feedback
text or login cookies.
"""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from . import query, store
from .session import base_url

NUMERIC = {"Numeric", None}                  # everything else (Calculated, Formula, Text, PassFail, SelectBox) is skipped
CONTRACT_VERSION = 1


def _stem(name: str) -> str:
    """'Assignment #1' -> 'assignment', 'Assignments' -> 'assignment', 'Quizzes' -> 'quiz'."""
    s = re.sub(r"[^a-z]+", " ", (name or "").lower()).split()
    w = s[0] if s else ""
    return re.sub(r"(zes|es|s)$", "", w) if len(w) > 4 else w


def _category_for(item: str, categories: list[str]) -> str | None:
    """The category an uncategorised item belongs to by name: 'Assignment #1' → 'Assignments', 'Exercise 2' →
    'Exercises'. Stems must share a prefix of at least 4 letters, so 'Final Exam' doesn't land in 'Finance'."""
    a = _stem(item)
    for name in categories:
        b = _stem(name)
        if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
            return name
    return None


def term_name(start: str | None) -> str | None:
    if not start:
        return None
    d = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(query.tz())
    return ("Fall" if d.month >= 8 else "Summer" if d.month >= 5 else "Spring") + f" {d.year}"


def payload(db, course_id: int, credits: float | None = None) -> tuple[dict | None, list[str]]:
    """Semestra import JSON for one course, plus warnings about anything adapted or left out. (None, [reason]) if the
    course has nothing Semestra can use yet."""
    c = next(iter(store.rows(db, "SELECT * FROM courses WHERE id = ?", int(course_id))), None)
    if not c:
        return None, [f"no course {course_id}"]
    warnings: list[str] = []
    cats = store.rows(db, "SELECT name, weight FROM grade_categories WHERE course_id = ? ORDER BY id", int(course_id))
    items = store.rows(db, "SELECT * FROM grades WHERE course_id = ? ORDER BY id", int(course_id))
    if not items:
        return None, ["no grade items published in this course yet"]

    buckets: dict[str, list[dict]] = {k["name"]: [] for k in cats}
    weights = {k["name"]: k["weight"] or 0 for k in cats}
    for g in items:
        if g["type"] not in NUMERIC:
            warnings.append(f"left out “{g['name']}”: it's a {g['type'].lower()} item (a total of other grades)")
            continue
        max_points = g["max_points"] or g["out_of"]
        if not max_points:
            warnings.append(f"left out “{g['name']}”: no maximum points")
            continue
        achieved = None
        if g["grade"] is not None and g["points"] is not None:
            scale = max_points / g["out_of"] if g["out_of"] else 1
            achieved = round(g["points"] * scale, 4)
        row = {"name": g["name"], "max_points": max_points, "achieved_points": achieved}
        cat = g["category"]
        if not cat:
            match = _category_for(g["name"], [k["name"] for k in cats])
            if match:
                cat = match
                warnings.append(f"“{g['name']}” has no category in Brightspace; placed in “{match}” by its name")
            else:
                cat = g["name"]
                weights[cat] = g["weight"] or 0
                buckets[cat] = []
                warnings.append(f"“{g['name']}” has no category; it became its own category (weight {weights[cat]:g})")
        buckets.setdefault(cat, []).append(row)
        weights.setdefault(cat, 0)

    categories = []
    for name, rows in buckets.items():
        if not rows:
            warnings.append(f"left out category “{name}” (weight {weights[name]:g}): no gradable items yet")
            continue
        categories.append({"name": name, "weight": weights[name], "items": rows})
    if not categories:
        return None, warnings + ["nothing gradable to import"]
    total = sum(k["weight"] for k in categories)
    if categories and abs(total - 100) > 0.5:
        warnings.append(f"category weights add up to {total:g}%, not 100% — check them in Semestra")
    name = re.sub(r"\s+", " ", c["short"] or c["name"]).strip()
    return {"course": {"name": name, "term": term_name(c["start"]), "credits": float(credits or 3)},
            "categories": categories}, warnings


def all_payloads(db) -> list[dict]:
    """Every current course that has grades, with its warnings and identifiers — the dashboard and push use this."""
    out = []
    for c in query.courses(db):
        p, w = payload(db, c["id"])
        out.append({"course_id": c["id"], "code": c["code"], "section": c["section"], "payload": p, "warnings": w})
    return out


# --- push --------------------------------------------------------------------------------------------------------

def _settings():
    return os.environ.get("SEMESTRA_URL", "").strip(), os.environ.get("SEMESTRA_KEY", "").strip()


def configured() -> bool:
    return all(_settings())


def check_url(url: str) -> str:
    """Only https (or plain http to this computer), no credentials or fragments in the URL."""
    if not url.isprintable() or any(c.isspace() for c in url):
        raise ValueError("Semestra URL can't contain spaces or control characters")
    u = urlparse(url)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise ValueError("Semestra URL must be https:// (plain http is only allowed to localhost)")
    if not u.hostname or u.username or u.password or u.fragment:
        raise ValueError("Semestra URL must be a plain address like https://<project>.supabase.co/functions/v1/connector-ingest")
    return url


def body(db) -> dict:
    """What a push sends (contract v1, docs/semestra-contract.md)."""
    host = urlparse(base_url()).hostname
    courses = []
    for p in all_payloads(db):
        if not p["payload"]:
            continue
        courses.append({
            "external_id": f"d2l:{host}:{p['course_id']}",
            "code": p["code"], "section": p["section"],
            "payload": p["payload"],
            "deadlines": [{"external_id": f"d2l:{host}:{d['kind']}:{d['title']}:{d['when_utc']}", "title": d["title"],
                           "kind": d["kind"], "due": d["when_utc"]}
                          for d in query.deadlines(db, 60, p["course_id"])],
        })
    return {"version": CONTRACT_VERSION, "source": "d2l-brightspace", "source_host": host,
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "courses": courses}


def push(db=None) -> dict:
    """Send the current courses to Semestra. Returns {"ok": bool, "message": str}; also recorded for the app to show."""
    url, key = _settings()
    if not (url and key):
        return {"ok": False, "message": "not configured (SEMESTRA_URL and SEMESTRA_KEY)"}
    try:
        check_url(url)
    except ValueError as e:
        return _record(db, False, str(e))
    own = db is None
    if own:
        ctx = store.connect()
        db = ctx.__enter__()
    try:
        data = body(db)
        try:
            r = httpx.post(url, json=data, timeout=30, follow_redirects=False,       # never forward the key elsewhere
                           headers={"Authorization": f"Bearer {key}", "X-Connector": "d2l-brightspace/1"})
        except httpx.HTTPError as e:
            return _record(db, False, f"couldn't reach Semestra: {e.__class__.__name__}")
        if r.status_code == 401:
            return _record(db, False, "Semestra rejected the connector key — create a new one in Semestra's settings")
        if 300 <= r.status_code < 400:
            return _record(db, False, f"Semestra answered with a redirect ({r.status_code}); use the final URL")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("error", ""))[:200]
            except ValueError:
                pass
            return _record(db, False, f"Semestra returned HTTP {r.status_code} {detail}".strip())
        try:
            info = r.json()
        except ValueError:
            info = {}
        notes = []
        if info.get("linked_to_existing"):
            notes.append(f"{info['linked_to_existing']} linked to a course you had entered by hand")
        if info.get("paused"):
            notes.append(f"{info['paused']} paused in Semestra, left unchanged")
        return _record(db, True, f"sent {len(data['courses'])} course(s)" + (" · " + " · ".join(notes) if notes else ""))
    finally:
        if own:
            ctx.__exit__(None, None, None)


def _record(db, ok: bool, message: str) -> dict:
    result = {"ok": ok, "message": message, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if db is not None:
        store.set_meta(db, "semestra_last_push", result)
        db.commit()
    return result


def last_push(db) -> dict | None:
    return store.get_meta(db, "semestra_last_push")


def dumps(p: dict) -> str:
    return json.dumps(p, ensure_ascii=False, indent=2)
