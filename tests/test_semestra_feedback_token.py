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


def test_course_without_grades_is_still_sent_but_not_copyable(db):
    store.update_courses(db, [course(2, "CHEM_1", "Chemistry")])
    p, warnings = semestra.payload(db, 2)
    assert p["course"]["name"] == "Chemistry" and p["categories"] == []
    assert not semestra.copyable(p)                       # Semestra's Import page needs a category
    assert "no grade items published" in warnings[0]


def test_every_enrolled_course_is_in_a_push(maths):
    store.update_courses(maths, [course(1, "MATH_1030_1_1268", "MATH1030 Calculus I"),
                                 course(2, "CHEM_1010_1_1268", "CHEM1010 Chemistry")])
    sent = semestra.body(maths)["courses"]
    assert {c["code"] for c in sent} == {"MATH1030", "CHEM1010"}
    counts = sorted(len(c["payload"]["categories"]) for c in sent)
    assert counts[0] == 0 and counts[1] > 0          # the course without grades travels too, with no categories


def test_poll_asks_semestra_whether_a_sync_was_requested(maths, monkeypatch):
    monkeypatch.setenv("SEMESTRA_URL", "https://semestra.example/api/connector")
    monkeypatch.setenv("SEMESTRA_KEY", "sk_semestra_" + "d" * 30)
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=True):
        seen.update(body=json, headers=headers)
        return httpx.Response(200, json={"ok": True, "sync_requested_at": "2026-09-20T10:00:00Z"})
    monkeypatch.setattr(semestra.httpx, "post", fake_post)
    assert semestra.poll()["sync_requested_at"] == "2026-09-20T10:00:00Z"
    assert seen["body"] == {"version": 1, "action": "poll"}          # a question, never data
    assert seen["headers"]["Authorization"].startswith("Bearer sk_semestra_")
    assert semestra.sync_requested(maths) is True
    semestra.mark_request_handled(maths, "2026-09-20T10:00:00Z")
    assert semestra.sync_requested(maths) is False                    # the same request isn't handled twice


def test_poll_is_quiet_when_not_configured_or_unreachable(maths, monkeypatch):
    monkeypatch.delenv("SEMESTRA_URL", raising=False)
    monkeypatch.delenv("SEMESTRA_KEY", raising=False)
    assert semestra.poll() is None and semestra.sync_requested(maths) is False
    monkeypatch.setenv("SEMESTRA_URL", "https://semestra.example/api/connector")
    monkeypatch.setenv("SEMESTRA_KEY", "sk_semestra_" + "e" * 30)
    monkeypatch.setattr(semestra.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert semestra.poll() is None


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


def test_env_writer_adds_one_comment_per_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("D2L_BASE_URL=https://x\n")
    for _ in range(3):
        server._set_env("SEMESTRA_URL", "https://semestra.example/api/connector")
        server._set_env("SEMESTRA_KEY", "sk_semestra_" + "f" * 30)
    text = (tmp_path / ".env").read_text()
    assert text.count("SEMESTRA_URL=") == 1 and text.count("SEMESTRA_KEY=") == 1
    assert text.count("# Semestra connector key") == 1                       # not repeated on every write
    assert "bearer token for /api and /mcp" not in text                      # the right note for the right setting


def test_credit_hours_can_be_set_per_course(maths):
    store.update_courses(maths, [{**course(1, "MATH_1030_1_1268", "MATH1030 Calculus I"), "short": "MATH1030 Calculus I"},
                                 {**course(2, "CHEM_1011_1_1268", "CHEM1011 Chemistry Lab"), "short": "CHEM1011 Lab"}])
    sent = {c["code"]: c["payload"]["course"]["credits"] for c in semestra.body(maths)["courses"]}
    assert sent == {"MATH1030": 3.0, "CHEM1011": 3.0}          # Brightspace publishes none, so 3 by default
    warning = " ".join(semestra.all_payloads(maths)[0]["warnings"])
    assert "credit hours unknown" in warning and "labs are usually 1" in warning

    semestra.set_credits(maths, 2, 1)                           # the lab is one credit
    sent = {c["code"]: c["payload"]["course"]["credits"] for c in semestra.body(maths)["courses"]}
    assert sent == {"MATH1030": 3.0, "CHEM1011": 1.0}
    assert "credit hours unknown" not in " ".join(
        next(p for p in semestra.all_payloads(maths) if p["course_id"] == 2)["warnings"])

    semestra.set_credits(maths, 2, None)                        # cleared → back to the default
    assert semestra.credits_map(maths) == {}
    for bad in (0, -1, 500):
        with pytest.raises(ValueError):
            semestra.set_credits(maths, 2, bad)
