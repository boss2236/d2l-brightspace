# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read your Brightspace content through the official API and normalise it into plain rows.

Endpoints confirmed against UDST's instance (d2l.udst.edu.qa), Sept 2026 — see NOTES.md:

* courses        GET /d2l/api/lp/<v>/enrollments/myenrollments/   (Valence; <v> is whatever the server supports)
* everything else     /d2l/api/le/<v>/{ou}/...                     (Valence, via api.Api with the browser session)

Read-only: nothing here writes to Brightspace. `sync.py` stores the rows; this module only fetches and shapes them.
"""
import json
import os
import re
from datetime import datetime, timezone

from .api import Api
from .session import ROOT, base_url

OUT = ROOT / "data"
SUBMISSION_STATUS = {0: "Not submitted", 1: "Submitted", 2: "Draft", 3: "Feedback published"}


def _ids(var: str) -> set[int]:
    return {int(x) for x in re.findall(r"\d+", os.environ.get(var, ""))}


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def courses(api: Api) -> list[dict]:
    """Every enrolment, tagged `academic` (a real course offering) and `current` (its term includes today).

    Portable rule: a real course has both a start and an end date; service pages (student services, self-help,
    orientation shells) usually don't. Instances that date those too can narrow it in .env:
      D2L_COURSE_CODE_REGEX   only codes matching this count as courses (UDST: ^[A-Z]{3,5}_\\d{4}_)
      D2L_COURSES_INCLUDE     org unit ids to always treat as current courses
      D2L_COURSES_EXCLUDE     org unit ids to always hide
    """
    items = api.enrollments()
    if not items:
        raise SystemExit("Brightspace returned no enrolments — session expired? run `d2l login`")
    pattern = os.environ.get("D2L_COURSE_CODE_REGEX", "").strip()
    include, exclude = _ids("D2L_COURSES_INCLUDE"), _ids("D2L_COURSES_EXCLUDE")
    now, out = datetime.now(timezone.utc), []
    for e in items:
        o, acc = e.get("OrgUnit") or {}, e.get("Access") or {}
        if not acc.get("CanAccess", True):
            continue
        ou, name, code = int(o["Id"]), o.get("Name") or "", o.get("Code") or ""
        start, end = acc.get("StartDate"), acc.get("EndDate")
        section = re.search(r"-(\d+)(?=\s*(\(|$))", name)
        academic = bool(start and end) and (not pattern or bool(re.search(pattern, code)))
        current = academic and _iso(start) <= now <= _iso(end)
        if ou in include:
            academic = current = True
        if ou in exclude:
            academic = current = False
        out.append({"id": ou, "name": name, "code": code,
                    # "MATH1030 Calculus I-19 (Lecture-Theatre)" -> "MATH1030 Calculus I (Lecture-Theatre)"
                    "short": re.sub(r"-\d+(?=\s*(\(|$))", "", name).strip(),
                    "section": section.group(1) if section else None,
                    "academic": academic, "current": current,
                    "start": start, "end": end,
                    "url": f"{base_url()}/d2l/home/{ou}"})
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


def grade_categories(api: Api, ou: int) -> list[dict]:
    """Grade categories with their weight in the final grade (e.g. Quizzes 25, Tests 35)."""
    return [{"id": c["Id"], "course_id": ou, "name": c.get("Name"), "weight": c.get("Weight")}
            for c in api.grade_categories(ou)]


def grades(api: Api, ou: int, categories: list[dict] | None = None) -> list[dict]:
    """Grade items with your released value, named by category. Category totals are not items, so they no longer
    show up as rows the way they did in the HTML grades table. Pass `categories` to avoid fetching them twice."""
    cats = {c["id"]: c["name"] for c in (categories if categories is not None else grade_categories(api, ou))}
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
        fb = entity.get("Feedback") or {}
        rubric = _rubric(f, fb)
        overall = _clean((fb.get("Feedback") or {}).get("Text") or "") or next(
            (_clean((r.get("OverallFeedback") or {}).get("Text") or "") for r in fb.get("RubricAssessments") or []
             if (r.get("OverallFeedback") or {}).get("Text")), "")
        dates = [s.get("SubmissionDate") for s in entity.get("Submissions") or [] if s.get("SubmissionDate")]
        out.append({"id": f["Id"], "course_id": ou, "name": f["Name"], "due": f.get("DueDate"),
                    "instructions": _clean((f.get("CustomInstructions") or {}).get("Text") or ""),
                    "submitted": int(bool(entity.get("Submissions"))),
                    "status": f"{status}, {files} file{'s' * (files != 1)}" if files else status,
                    "score": None if score is None else f"{score:g}" + (f" / {out_of:g}" if out_of else ""),
                    "feedback": overall or None,
                    "rubric": json.dumps(rubric) if rubric else None,
                    "feedback_files": json.dumps([x.get("FileName") for x in fb.get("Files") or []]) if fb.get("Files") else None,
                    "submitted_at": max(dates) if dates else None})
    return out


def _rubric(folder: dict, feedback: dict) -> list[dict]:
    """Rubric scores as rows: criterion, level reached, score out of the criterion's best level, and its comment.
    Criterion and level names come from the folder's rubric definition; the scores from your feedback."""
    names, levels, best = {}, {}, {}
    for rub in (folder.get("Assessment") or {}).get("Rubrics") or []:
        for group in rub.get("CriteriaGroups") or []:
            group_levels = {lv["Id"]: lv for lv in group.get("Levels") or []}
            levels.update({i: lv.get("Name") for i, lv in group_levels.items()})
            top = max((lv.get("Points") or 0 for lv in group_levels.values()), default=None)
            for c in group.get("Criteria") or []:
                names[c["Id"]] = c.get("Name")
                cells = [cell.get("Points") for cell in c.get("Cells") or [] if cell.get("Points") is not None]
                best[c["Id"]] = max(cells) if cells else top
    rows = []
    for assessment in feedback.get("RubricAssessments") or []:
        for o in assessment.get("CriteriaOutcome") or []:
            rows.append({"criterion": names.get(o.get("CriterionId"), f"Criterion {o.get('CriterionId')}"),
                         "level": levels.get(o.get("LevelId")), "score": o.get("Score"),
                         "out_of": best.get(o.get("CriterionId")),
                         "feedback": _clean((o.get("Feedback") or {}).get("Text") or "") or None})
    return rows


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
                # external links as they are; Brightspace-internal links (e.g. LTI tools like ALEKS, whose
                # quickLink launches the tool straight away) on the Brightspace host; files via their view page
                target = url if url.startswith("http") else (base_url() + url if kind != "File" and url.startswith("/d2l/")
                                                               else view)
                out.append({"id": t["TopicId"], "course_id": ou, "module": here, "title": t["Title"], "kind": kind,
                            "url": target, "ext": ext,
                            "modified": t.get("LastModifiedDate")})
            walk(m.get("Modules") or [], here)

    walk(api.toc(ou).get("Modules") or [], "")
    return out
