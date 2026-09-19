# SPDX-License-Identifier: AGPL-3.0-or-later
"""Semestra export and push, assignment feedback/rubrics, token rotation, OCR switch."""
import json

import httpx
import pytest
from conftest import course

from d2l import extract, fetch, semestra, server, store


def grade(i, name, category=None, gtype="Numeric", max_points=100.0, grade_text=None, points=None, out_of=None, weight=None):
    return {"id": i, "course_id": 1, "name": name, "category": category, "type": gtype, "max_points": max_points,
            "weight": weight, "grade": grade_text, "points": points, "out_of": out_of or max_points, "comments": None,
            "updated": None}


@pytest.fixture
def maths(db):
    store.update_courses(db, [{**course(1, "MATH_1030_1_1268", "MATH1030 Calculus I-19"), "short": "MATH1030 Calculus I",
                               "start": "2026-08-24T21:00:01.000Z", "end": "2026-12-03T20:59:59.000Z"}])
    store.replace_course_rows(db, "grade_categories", 1, [
        {"id": 10, "course_id": 1, "name": "Quizzes", "weight": 25.0},
        {"id": 11, "course_id": 1, "name": "Assignments", "weight": 10.0},
        {"id": 12, "course_id": 1, "name": "Tests", "weight": 35.0},
        {"id": 13, "course_id": 1, "name": "Final", "weight": 30.0},
        {"id": 14, "course_id": 1, "name": "Participation", "weight": 0.0}])
    store.replace_course_rows(db, "grades", 1, [
        grade(1, "Quiz 1", "Quizzes", grade_text="8 / 10", points=8.0, out_of=10.0),   # graded out of 10, max 100
        grade(2, "Assignment #1", max_points=10.0, grade_text="10 / 10", points=10.0),
        grade(3, "Test 1", "Tests"), grade(4, "Final Exam", "Final"),
        grade(5, "Cumulative Mid Term Grade", gtype="Calculated", max_points=None),
        grade(6, "Bonus lab", max_points=5.0, weight=2.0)])
    return db


def test_semestra_payload_maps_brightspace_grades(maths):
    p, warnings = semestra.payload(maths, 1)
    assert p["course"] == {"name": "MATH1030 Calculus I", "term": "Fall 2026", "credits": 3.0}
    cats = {c["name"]: c for c in p["categories"]}
    assert cats["Quizzes"]["items"] == [{"name": "Quiz 1", "max_points": 100.0, "achieved_points": 80.0}]   # rescaled
    assert cats["Assignments"]["items"][0]["achieved_points"] == 10.0                 # placed by name
    assert cats["Bonus lab"]["weight"] == 2.0                                          # uncategorised → own category
    assert "Participation" not in cats                                                 # no items → left out
    joined = " ".join(warnings)
    assert "Cumulative Mid Term Grade" in joined and "by its name" in joined and "Participation" in joined


def test_semestra_payload_matches_semestras_import_schema(maths):
    p, _ = semestra.payload(maths, 1)
    # the same rules as Semestra's zod importPayloadSchema
    assert p["course"]["name"].strip() and p["course"]["credits"] > 0
    assert len(p["categories"]) >= 1
    for c in p["categories"]:
        assert c["name"].strip() and c["weight"] >= 0
        for i in c["items"]:
            assert i["name"].strip() and i["max_points"] > 0
            assert i["achieved_points"] is None or i["achieved_points"] >= 0


def test_semestra_payload_needs_grades(db):
    store.update_courses(db, [course(2, "CHEM_1", "Chemistry")])
    assert semestra.payload(db, 2) == (None, ["no grade items published in this course yet"])


@pytest.mark.parametrize("url, ok", [
    ("https://abc.supabase.co/functions/v1/connector-ingest", True),
    ("http://127.0.0.1:8799/ingest", True),
    ("http://example.com/ingest", False),
    ("https://user:pw@example.com/x", False),
    ("ftp://example.com", False),
])
def test_semestra_url_rules(url, ok):
    if ok:
        assert semestra.check_url(url) == url
    else:
        with pytest.raises(ValueError):
            semestra.check_url(url)


def test_semestra_push_sends_key_and_minimal_body_without_redirects(maths, monkeypatch):
    monkeypatch.setenv("SEMESTRA_URL", "https://semestra.example/functions/v1/connector-ingest")
    monkeypatch.setenv("SEMESTRA_KEY", "sk_semestra_" + "a" * 30)
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=True):
        seen.update(url=url, body=json, headers=headers, follow=follow_redirects)
        return httpx.Response(200, json={"ok": True, "courses": 1})
    monkeypatch.setattr(semestra.httpx, "post", fake_post)
    r = semestra.push(maths)
    assert r["ok"] and r["message"] == "updated 1 of 1 course(s)"
    assert seen["headers"]["Authorization"] == "Bearer sk_semestra_" + "a" * 30 and seen["follow"] is False
    body = json.dumps(seen["body"])
    assert seen["body"]["courses"][0]["external_id"] == "d2l:school.example.com:1"
    assert "announcement" not in body and "file_path" not in body and "comments" not in body
    assert semestra.last_push(maths)["ok"] is True


def test_semestra_push_reports_bad_key(maths, monkeypatch):
    monkeypatch.setenv("SEMESTRA_URL", "https://semestra.example/ingest")
    monkeypatch.setenv("SEMESTRA_KEY", "sk_semestra_" + "b" * 30)
    monkeypatch.setattr(semestra.httpx, "post", lambda *a, **k: httpx.Response(401))
    assert "rejected the connector key" in semestra.push(maths)["message"]


def test_assignment_feedback_and_rubric_are_parsed():
    class Api:
        def folders(self, ou):
            return [{"Id": 1, "Name": "Proposal", "Assessment": {"ScoreDenominator": 40, "Rubrics": [{"CriteriaGroups": [{
                "Levels": [{"Id": 100, "Name": "Excellent", "Points": 20.0}, {"Id": 101, "Name": "Good", "Points": 16.0}],
                "Criteria": [{"Id": 1, "Name": "Clarity"}, {"Id": 2, "Name": "Research"}]}]}]}}]

        def my_submissions(self, ou, folder):
            return [{"Status": 3, "Submissions": [{"SubmissionDate": "2026-03-16T20:11:18Z", "Files": [{}]}],
                     "Feedback": {"Score": 36.0, "Feedback": {"Text": "Well argued.\r\n"},
                                  "Files": [{"FileName": "marked.pdf"}],
                                  "RubricAssessments": [{"CriteriaOutcome": [
                                      {"CriterionId": 1, "LevelId": 100, "Score": 20.0, "Feedback": {"Text": ""}},
                                      {"CriterionId": 2, "LevelId": 101, "Score": 16.0, "Feedback": {"Text": "cite more"}}]}]}}]
    a = fetch.assignments(Api(), 7)[0]
    assert a["feedback"] == "Well argued." and a["submitted_at"] == "2026-03-16T20:11:18Z"
    assert json.loads(a["feedback_files"]) == ["marked.pdf"]
    assert json.loads(a["rubric"]) == [
        {"criterion": "Clarity", "level": "Excellent", "score": 20.0, "out_of": 20.0, "feedback": None},
        {"criterion": "Research", "level": "Good", "score": 16.0, "out_of": 20.0, "feedback": "cite more"}]


def test_token_rotation_rewrites_env_privately(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("D2L_BASE_URL=https://x\nD2L_API_TOKEN=old\n# comment\n")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setenv("D2L_API_TOKEN", "old")
    new = server.rotate_token()
    text = env.read_text()
    assert new != "old" and f"D2L_API_TOKEN={new}" in text and "D2L_API_TOKEN=old" not in text
    assert "D2L_BASE_URL=https://x" in text and "# comment" in text
    assert (env.stat().st_mode & 0o777) == 0o600
    assert server.token() == new


def test_token_gate_follows_rotation():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    current = {"t": "first-" + "x" * 30}
    app = Starlette(routes=[Route("/api", lambda r: PlainTextResponse("ok"))])
    client = TestClient(server.TokenGate(app, lambda: current["t"]))
    assert client.get("/api", headers={"Authorization": f"Bearer {current['t']}"}).status_code == 200
    old, current["t"] = current["t"], "second-" + "y" * 30
    assert client.get("/api", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert client.get(f"/c/{current['t']}/api").status_code == 200


def test_ocr_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("D2L_OCR", "0")
    assert extract.ocr_available() is False and extract._ocr(__file__, [1]) == {}


def test_semestra_push_reports_linked_and_paused_courses(maths, monkeypatch):
    monkeypatch.setenv("SEMESTRA_URL", "https://semestra.example/ingest")
    monkeypatch.setenv("SEMESTRA_KEY", "sk_semestra_" + "c" * 30)
    monkeypatch.setattr(semestra.httpx, "post", lambda *a, **k: httpx.Response(
        200, json={"ok": True, "courses": 0, "linked_to_existing": 1, "paused": 1}))
    msg = semestra.push(maths)["message"]
    assert msg.startswith("updated 0 of 1 course(s)")
    assert "linked to a course you had entered by hand" in msg and "paused in Semestra" in msg
