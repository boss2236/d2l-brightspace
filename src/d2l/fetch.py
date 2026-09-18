"""Pull your Brightspace content into local JSON: courses, announcements, assignments, grades.

Endpoints confirmed against UDST's instance (d2l.udst.edu.qa), Sept 2026 — see NOTES.md:

* courses      GET  /d2l/le/manageCourses/api/mycourses   (JSON, needs the X-Csrf-Token from localStorage)
* announcements     /d2l/lms/news/main.d2l?ou=<id>        (HTML table)
* assignments       /d2l/lms/dropbox/dropbox.d2l?ou=<id>  (HTML table, empty when none are posted)
* grades            /d2l/lms/grades/index.d2l?ou=<id>     (HTML table)

Read-only: it fetches what your account already sees and writes JSON to data/.
"""
import json
import re
from pathlib import Path

from .session import ROOT, base_url, signed_in_page

OUT = ROOT / "data"
COURSES_API = "/d2l/le/manageCourses/api/mycourses?pageSize=100&sort=current&autoPinCourses=false&orgUnitTypeId=3&promotePins="


def _csrf(page) -> str:
    try:
        return page.evaluate("() => localStorage.getItem('XSRF.Token') || ''") or ""
    except Exception:
        return ""


def _table_rows(page, min_cells: int = 2) -> list[list[str]]:
    """Rows of the first real table on the page, across frames; D2L renders tool pages inside a frame."""
    best: list[list[str]] = []
    for fr in page.frames:
        rows = []
        for tr in fr.query_selector_all("table tr"):
            cells = [c.inner_text().strip() for c in tr.query_selector_all("th, td")]
            cells = [c for c in cells if c]
            if len(cells) >= min_cells:
                rows.append(cells)
        if len(rows) > len(best):
            best = rows
    return best


def courses(page) -> list[dict]:
    """Enrolled courses from Brightspace's own JSON endpoint."""
    r = page.request.get(base_url() + COURSES_API,
                         headers={"Accept": "application/json", "X-Csrf-Token": _csrf(page)})
    if not r.ok:
        raise SystemExit(f"courses endpoint returned {r.status} — session expired? run `d2l login`")
    out = []
    for c in r.json().get("Courses", []):
        out.append({"id": int(c["OrgUnitId"]), "name": c.get("Name"), "code": c.get("Code"),
                    "active": bool(c.get("IsActive")), "start": c.get("StartDate"), "end": c.get("EndDate"),
                    "url": f"{base_url()}/d2l/home/{c['OrgUnitId']}"})
    return out


def announcements(page, course_id: int) -> list[dict]:
    page.goto(f"{base_url()}/d2l/lms/news/main.d2l?ou={course_id}", wait_until="networkidle")
    rows, out = _table_rows(page), []
    for cells in rows:
        title, date = cells[0], next((c for c in cells[1:] if re.search(r"\d{4}", c)), None)
        if title.lower() in ("title", "start date") or not date:
            continue
        out.append({"course_id": course_id, "title": title, "date": date})
    return out


def assignments(page, course_id: int) -> list[dict]:
    """Assignment folders.

    The dropbox table is: Folder | Completion Status | Score | Evaluation Status, with category rows in
    between (single cell) and the due date folded into the folder cell as a second line ("Name\\nDue on ...").
    """
    page.goto(f"{base_url()}/d2l/lms/dropbox/dropbox.d2l?ou={course_id}", wait_until="networkidle")
    out = []
    for cells in _table_rows(page, min_cells=2):
        first = cells[0]
        if first.strip().lower().startswith("folder"):        # header row
            continue
        name, _, tail = first.partition("\n")
        due = tail.replace("Due on", "").strip() or None
        status = cells[1] if len(cells) > 1 else ""
        score = next((c for c in cells[2:] if "/" in c), None)
        out.append({"course_id": course_id, "name": name.strip(), "due": due,
                    "submitted": "submission" in status.lower(),
                    "status": status or None,
                    "score": None if score in (None, "- / -") else score})
    return out


def grades(page, course_id: int) -> list[dict]:
    page.goto(f"{base_url()}/d2l/lms/grades/index.d2l?ou={course_id}", wait_until="networkidle")
    out = []
    for cells in _table_rows(page):
        if cells[0].lower().startswith("grade item"):
            continue
        out.append({"course_id": course_id, "item": cells[0], "grade": cells[1] if len(cells) > 1 else None})
    return out


def dump_all(headless: bool = True, active_only: bool = True) -> dict:
    OUT.mkdir(exist_ok=True)
    with signed_in_page(headless=headless) as page:
        cs = courses(page)
        wanted = [c for c in cs if c["active"]] if active_only else cs
        print(f"{len(cs)} courses ({len(wanted)} to fetch)")
        out = {"courses": cs, "announcements": [], "assignments": [], "grades": []}
        for c in wanted:
            a = announcements(page, c["id"])
            d = assignments(page, c["id"])
            g = grades(page, c["id"])
            out["announcements"] += a
            out["assignments"] += d
            out["grades"] += g
            print(f"  {c['name'][:44]:<44} announcements {len(a):>2}  assignments {len(d):>2}  grades {len(g):>2}")
        for key, rows in out.items():
            (OUT / f"{key}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        print("written to", OUT)
        return out
