"""Read your Brightspace content through the official API and normalise it into plain rows.

Endpoints confirmed against UDST's instance (d2l.udst.edu.qa), Sept 2026 — see NOTES.md:

* courses        GET /d2l/le/manageCourses/api/mycourses       (the list the home page's course picker uses)
* everything else     /d2l/api/le/1.99/{ou}/...                (Valence, via api.Api with the browser session)

Read-only: nothing here writes to Brightspace. `sync.py` stores the rows; this module only fetches and shapes them.
"""
import json
import re
from datetime import datetime, timezone

from .api import Api
from .session import ROOT, base_url

OUT = ROOT / "data"
COURSES_API = "/d2l/le/manageCourses/api/mycourses?pageSize=100&sort=current&autoPinCourses=false&orgUnitTypeId=3&promotePins="
# Real course offerings have codes like MATH_1030_2566_1268; service shells ("StudentCentralServices",
# "SELFHELP_2026Y1") don't.
ACADEMIC_CODE = re.compile(r"^[A-Z]{3,5}_\d{4}_\d+_\d+$")
SUBMISSION_STATUS = {0: "Not submitted", 1: "Submitted", 2: "Draft", 3: "Feedback published"}


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def courses(api: Api) -> list[dict]:
    """Every enrolment, tagged `academic` (a real course offering) and `current` (its term includes today)."""
    j = api.get(COURSES_API)
    if j is None:
        raise SystemExit("courses endpoint refused — session expired? run `d2l login`")
    now, out = datetime.now(timezone.utc), []
    for c in j.get("Courses", []):
        name, code = c.get("Name") or "", c.get("Code") or ""
        start, end = c.get("StartDate"), c.get("EndDate")
        section = re.search(r"-(\d+)(?=\s*(\(|$))", name)
        academic = bool(ACADEMIC_CODE.match(code))
        out.append({"id": int(c["OrgUnitId"]), "name": name, "code": code,
                    # "MATH1030 Calculus I-19 (Lecture-Theatre)" -> "MATH1030 Calculus I (Lecture-Theatre)"
                    "short": re.sub(r"-\d+(?=\s*(\(|$))", "", name).strip(),
                    "section": section.group(1) if section else None,
                    "academic": academic,
                    "current": academic and bool(start and end) and _iso(start) <= now <= _iso(end),
                    "start": start, "end": end,
                    "url": f"{base_url()}/d2l/home/{c['OrgUnitId']}"})
    return out


def pick(cs: list[dict], scope: str = "current") -> list[dict]:
    """current = this term's course offerings; academic = every course offering; all = including service shells."""
    return [c for c in cs if scope == "all" or c[scope]]


def _clean(text: str) -> str:
    """Brightspace bodies come with \\r\\n and runs of blank lines; keep single blank lines only."""
    text = text.replace("\r", "").replace("\xa0", " ")
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def announcements(api: Api, ou: int) -> list[dict]:
    out = []
    for n in api.news(ou):
        if n.get("IsHidden") or not n.get("IsPublished", True):
            continue
        body = n.get("Body") or {}
        out.append({"id": n["Id"], "course_id": ou, "title": n["Title"], "date": n.get("StartDate") or n.get("CreatedDate"),
                    "body": _clean(body.get("Text") or ""), "html": body.get("Html") or "",
                    "attachments": json.dumps([a.get("FileName") for a in n.get("Attachments") or []])})
    return out


def grades(api: Api, ou: int) -> list[dict]:
    """Grade items with your released value, named by category. Category totals are not items, so they no longer
    show up as rows the way they did in the HTML grades table."""
    cats = {c["Id"]: c["Name"] for c in api.grade_categories(ou)}
    mine = {int(v["GradeObjectIdentifier"]): v for v in api.my_grades(ou)}
    out = []
    for g in api.grade_items(ou):
        if g.get("GradeType") == "Category":
            continue
        v = mine.pop(g["Id"], None)
        out.append(_grade_row(ou, g["Id"], g["Name"], cats.get(g.get("CategoryId")), g.get("GradeType"),
                              g.get("MaxPoints"), g.get("Weight"), v))
    for gid, v in mine.items():                       # released values whose item we can't list (e.g. final grade)
        out.append(_grade_row(ou, gid, v["GradeObjectName"], None, v.get("GradeObjectTypeName"), None, None, v))
    return out


def _grade_row(ou, gid, name, category, gtype, max_points, weight, v) -> dict:
    return {"id": gid, "course_id": ou, "name": name, "category": category, "type": gtype,
            "max_points": max_points, "weight": weight,
            "grade": (v or {}).get("DisplayedGrade") or None,
            "points": (v or {}).get("PointsNumerator"),
            "out_of": (v or {}).get("PointsDenominator") or max_points,
            "comments": ((v or {}).get("Comments") or {}).get("Text") or None,
            "updated": (v or {}).get("LastModified")}


def assignments(api: Api, ou: int) -> list[dict]:
    out = []
    for f in api.folders(ou):
        if f.get("IsHidden"):
            continue
        subs = api.my_submissions(ou, f["Id"])
        entity = subs[0] if subs else {}
        files = sum(len(s.get("Files") or []) for s in entity.get("Submissions") or [])
        score = (entity.get("Feedback") or {}).get("Score")
        out_of = (f.get("Assessment") or {}).get("ScoreDenominator")
        status = SUBMISSION_STATUS.get(entity.get("Status"), "Not submitted")
        out.append({"id": f["Id"], "course_id": ou, "name": f["Name"], "due": f.get("DueDate"),
                    "instructions": _clean((f.get("CustomInstructions") or {}).get("Text") or ""),
                    "submitted": int(bool(entity.get("Submissions"))),
                    "status": f"{status}, {files} file{'s' * (files != 1)}" if files else status,
                    "score": None if score is None else f"{score:g}" + (f" / {out_of:g}" if out_of else "")})
    return out


def quizzes(api: Api, ou: int) -> list[dict]:
    return [{"id": q["QuizId"], "course_id": ou, "name": q["Name"], "start": q.get("StartDate"),
             "end": q.get("EndDate"), "due": q.get("DueDate"), "active": int(bool(q.get("IsActive")))}
            for q in api.quizzes(ou)]


def calendar(api: Api, ous: list[int]) -> list[dict]:
    return [{"id": e["CalendarEventId"], "course_id": e["OrgUnitId"], "title": e["Title"],
             "start": e.get("StartDateTime"), "end": e.get("EndDateTime"),
             "kind": ((e.get("AssociatedEntity") or {}).get("AssociatedEntityType") or "Event").split(".")[-1]}
            for e in api.calendar(ous)]


def content(api: Api, ou: int) -> list[dict]:
    """Flatten the content tree: one row per visible topic, with its module path ("Course Notes / Unit 1")."""
    out = []

    def walk(modules, path):
        for m in modules:
            if m.get("IsHidden"):
                continue
            here = f"{path} / {m['Title']}" if path else m["Title"]
            for t in m.get("Topics") or []:
                if t.get("IsHidden"):
                    continue
                url, kind = t.get("Url") or "", t.get("TypeIdentifier")
                ext = url.rsplit(".", 1)[-1].lower()[:5] if kind == "File" and "." in url.rsplit("/", 1)[-1] else None
                view = f"{base_url()}/d2l/le/content/{ou}/viewContent/{t['TopicId']}/View"
                out.append({"id": t["TopicId"], "course_id": ou, "module": here, "title": t["Title"], "kind": kind,
                            "url": url if url.startswith("http") else view, "ext": ext,
                            "modified": t.get("LastModifiedDate")})
            walk(m.get("Modules") or [], here)

    walk(api.toc(ou).get("Modules") or [], "")
    return out
