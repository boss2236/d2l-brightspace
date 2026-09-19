# SPDX-License-Identifier: AGPL-3.0-or-later
"""fetch.py turns Brightspace API responses into rows. The fake API returns the shapes seen on a real instance."""
from conftest import iso

from d2l import fetch


class FakeApi:
    def __init__(self, **data):
        self.data = data

    def __getattr__(self, name):
        value = self.data.get(name)
        return lambda *a, **k: value


def enrolment(ou, code, name, start=None, end=None, can_access=True):
    return {"OrgUnit": {"Id": ou, "Code": code, "Name": name},
            "Access": {"StartDate": start, "EndDate": end, "IsActive": True, "CanAccess": can_access}}


ENROLMENTS = [
    enrolment(90763, "StudentCentralServices", "Student Central Services"),
    enrolment(187317, "SELFHELP_2026Y1", "Self-Help Resources Year 1", start=iso(-180)),
    enrolment(172441, "COMM_1010_2277_1261", "COMM1010 English Communication I-2", iso(-250), iso(-150)),
    enrolment(197850, "MATH_1030_2565_1268", "MATH1030 Calculus I-19 (Lecture-Theatre)", iso(-25), iso(75)),
    enrolment(199108, "CHEM_1011_2187_1268", "CHEM1011 General Chemistry I (Lab)-21", iso(-25), iso(75)),
    enrolment(111111, "HIDDEN_1", "Hidden course", iso(-25), iso(75), can_access=False),
]


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_courses_current_term_needs_both_dates_and_today_inside():
    cs = by_id(fetch.courses(FakeApi(enrollments=ENROLMENTS)))
    assert {i for i, c in cs.items() if c["current"]} == {197850, 199108}
    assert not cs[90763]["academic"]            # service shell: no dates
    assert not cs[187317]["academic"]           # self-help: start date only
    assert cs[172441]["academic"] and not cs[172441]["current"]   # past term
    assert 111111 not in cs                     # no access


def test_courses_short_name_and_section():
    c = by_id(fetch.courses(FakeApi(enrollments=ENROLMENTS)))[197850]
    assert c["short"] == "MATH1030 Calculus I (Lecture-Theatre)"
    assert c["section"] == "19"
    assert c["url"] == "https://school.example.com/d2l/home/197850"


def test_courses_code_regex_include_and_exclude(monkeypatch):
    monkeypatch.setenv("D2L_COURSE_CODE_REGEX", r"^MATH_")
    monkeypatch.setenv("D2L_COURSES_INCLUDE", "90763")
    monkeypatch.setenv("D2L_COURSES_EXCLUDE", "197850")
    cs = by_id(fetch.courses(FakeApi(enrollments=ENROLMENTS)))
    assert not cs[199108]["current"]            # CHEM doesn't match the pattern
    assert cs[90763]["current"]                 # forced in
    assert not cs[197850]["current"]            # forced out


def test_pick_scopes():
    cs = fetch.courses(FakeApi(enrollments=ENROLMENTS))
    assert len(fetch.pick(cs, "current")) == 2
    assert len(fetch.pick(cs, "academic")) == 3
    assert len(fetch.pick(cs, "all")) == 5


def test_clean_normalises_brightspace_text():
    assert fetch._clean("Hi\r\n\r\n\r\n\r\nthere \xa0\t\nend") == "Hi\n\nthere\nend"


def test_content_flattens_tree_and_routes_links():
    toc = {"Modules": [
        {"Title": "Unit 1", "Topics": [
            {"TopicId": 1, "Title": "Notes", "TypeIdentifier": "File", "Url": "/content/enforced/1-X/Notes.PDF"},
            {"TopicId": 2, "Title": "Video", "TypeIdentifier": "Link", "Url": "https://youtu.be/abc"},
            {"TopicId": 3, "Title": "ALEKS", "TypeIdentifier": "Link",
             "Url": "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=5&type=lti&rcode=X"},
            {"TopicId": 4, "Title": "Secret", "TypeIdentifier": "File", "Url": "/x.pdf", "IsHidden": True}],
         "Modules": [{"Title": "Week 1", "Topics": [
             {"TopicId": 5, "Title": "Deep", "TypeIdentifier": "File", "Url": "/content/noext"}], "Modules": []}]},
        {"Title": "Hidden module", "IsHidden": True, "Topics": [{"TopicId": 6}], "Modules": []}]}
    rows = by_id(fetch.content(FakeApi(toc=toc), 5))
    assert set(rows) == {1, 2, 3, 5}
    assert rows[1]["ext"] == "pdf" and rows[1]["url"].endswith("/d2l/le/content/5/viewContent/1/View")
    assert rows[2]["url"] == "https://youtu.be/abc"
    assert rows[3]["url"].startswith("https://school.example.com/d2l/common/dialogs/quickLink/")
    assert rows[5]["module"] == "Unit 1 / Week 1" and rows[5]["ext"] is None


def test_grades_names_categories_and_skips_category_totals():
    api = FakeApi(
        grade_categories=[{"Id": 10, "Name": "Quizzes"}],
        my_grades=[{"GradeObjectIdentifier": "1", "GradeObjectName": "Quiz 1", "DisplayedGrade": "8 / 10",
                    "PointsNumerator": 8.0, "PointsDenominator": 10.0, "Comments": {"Text": "good"}},
                   {"GradeObjectIdentifier": "99", "GradeObjectName": "Final Grade", "DisplayedGrade": "80 %",
                    "PointsNumerator": 80.0, "PointsDenominator": 100.0}],
        grade_items=[{"Id": 1, "Name": "Quiz 1", "GradeType": "Numeric", "CategoryId": 10, "MaxPoints": 10.0, "Weight": 5},
                     {"Id": 2, "Name": "Quiz 2", "GradeType": "Numeric", "CategoryId": 10, "MaxPoints": 10.0, "Weight": 5},
                     {"Id": 10, "Name": "Quizzes", "GradeType": "Category"}])
    rows = by_id(fetch.grades(api, 7))
    assert set(rows) == {1, 2, 99}
    assert rows[1]["category"] == "Quizzes" and rows[1]["grade"] == "8 / 10" and rows[1]["comments"] == "good"
    assert rows[2]["grade"] is None and rows[2]["out_of"] == 10.0
    assert rows[99]["name"] == "Final Grade"


def test_assignments_submission_status_and_score():
    api = FakeApi(folders=[{"Id": 1, "Name": "Essay", "DueDate": iso(3), "Assessment": {"ScoreDenominator": 20}}],
                  my_submissions=[{"Status": 3, "Feedback": {"Score": 18.0},
                                   "Submissions": [{"Files": [{}, {}]}]}])
    a = fetch.assignments(api, 7)[0]
    assert a["submitted"] == 1 and a["status"] == "Feedback published, 2 files" and a["score"] == "18 / 20"
