# SPDX-License-Identifier: AGPL-3.0-or-later
"""store.py (change detection, archiving) and query.py (what the CLI, dashboard, MCP and REST read)."""
import sqlite3

from conftest import course, iso

from d2l import query, store


def ann(i, ou, title, body="", days=0):
    return {"id": i, "course_id": ou, "title": title, "date": iso(days), "body": body, "html": "", "attachments": "[]"}


def test_replace_course_rows_reports_new_changed_and_removes_gone(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    first = store.replace_course_rows(db, "announcements", 1, [ann(1, 1, "a"), ann(2, 1, "b")])
    assert [r["id"] for r, old in first] == [1, 2] and all(old is None for _, old in first)
    again = store.replace_course_rows(db, "announcements", 1, [ann(1, 1, "a"), ann(2, 1, "b")])
    assert again == []                                           # nothing changed → nothing to notify
    edited = store.replace_course_rows(db, "announcements", 1, [ann(1, 1, "a", body="new text")])
    assert [(r["id"], old["body"]) for r, old in edited] == [(1, "")]
    assert [r["id"] for r in store.rows(db, "SELECT id FROM announcements")] == [1]   # 2 was removed upstream


def test_replace_keeps_downloaded_text_the_fetch_does_not_send(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    row = {"id": 5, "course_id": 1, "module": "U1", "title": "Notes", "kind": "File", "url": "u", "ext": "pdf",
           "modified": "2026-01-01"}
    store.replace_course_rows(db, "content", 1, [row])
    db.execute("UPDATE content SET text = 'lecture text', file_status = 'ok' WHERE id = 5")
    store.replace_course_rows(db, "content", 1, [row])
    assert store.rows(db, "SELECT text FROM content")[0]["text"] == "lecture text"


def test_term_change_archives_old_courses_and_keeps_their_data(db):
    store.update_courses(db, [course(1, "COMM_1", "Old course")])
    store.replace_course_rows(db, "announcements", 1, [ann(1, 1, "old news")])
    added, archived = store.update_courses(db, [course(2, "MATH_2", "New course")])
    store.prune(db)
    assert [c["id"] for c in added] == [2] and [c["id"] for c in archived] == [1]
    assert store.rows(db, "SELECT title FROM announcements") == [{"title": "old news"}]    # kept
    assert [c["id"] for c in query.courses(db)] == [2]                                     # hidden by default
    assert {c["id"] for c in query.courses(db, include_archived=True)} == {1, 2}
    assert query.course_ids(db, None) == [2]
    assert query.course_ids(db, "comm") == [1]              # a named past course is still reachable
    again, _ = store.update_courses(db, [course(1, "COMM_1", "Old course")])
    assert [c["id"] for c in again] == [1]                  # coming back counts as new again


def test_prune_only_removes_orphans(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    db.execute("INSERT INTO announcements (id, course_id, title) VALUES (9, 404, 'orphan')")
    store.prune(db)
    assert store.rows(db, "SELECT id FROM announcements") == []


def test_events_are_recorded_once(db):
    assert store.add_event(db, "new_grade", "grade:1:8/10", 1, "Quiz 1: 8/10")
    assert not store.add_event(db, "new_grade", "grade:1:8/10", 1, "Quiz 1: 8/10")
    assert store.add_event(db, "new_grade", "grade:1:9/10", 1, "Quiz 1: 9/10")


def test_old_databases_are_migrated(tmp_path, monkeypatch):
    path = tmp_path / "data" / "d2l.db"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT, code TEXT, short TEXT, section TEXT, "
                "academic INTEGER, current INTEGER, start TEXT, \"end\" TEXT, url TEXT)")
    old.execute("INSERT INTO courses (id, name, code, short) VALUES (1, 'A', 'A_1', 'A')")
    old.commit()
    old.close()
    monkeypatch.setattr(store, "DB", path)
    with store.connect() as db:
        assert store.rows(db, "SELECT id, archived FROM courses") == [{"id": 1, "archived": 0}]


def test_deadlines_window_and_quiz_calendar_dedupe(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    store.replace_course_rows(db, "assignments", 1, [
        {"id": 1, "course_id": 1, "name": "Soon", "due": iso(2), "instructions": "", "submitted": 0, "status": "", "score": None},
        {"id": 2, "course_id": 1, "name": "Done", "due": iso(2), "instructions": "", "submitted": 1, "status": "", "score": None},
        {"id": 3, "course_id": 1, "name": "Far", "due": iso(40), "instructions": "", "submitted": 0, "status": "", "score": None}])
    store.replace_course_rows(db, "quizzes", 1, [
        {"id": 7, "course_id": 1, "name": "Quiz 1", "start": iso(1), "end": iso(5), "due": None, "active": 1}])
    store.replace_course_rows(db, "calendar", 1, [
        {"id": 8, "course_id": 1, "title": "Quiz 1 - Availability Ends", "start": iso(5), "end": iso(5), "kind": "Quiz"},
        {"id": 9, "course_id": 1, "title": "Lab safety", "start": iso(3), "end": iso(3), "kind": "Event"}])
    got = [(d["kind"], d["title"]) for d in query.deadlines(db, 14)]
    assert got == [("assignment", "Soon"), ("event", "Lab safety"), ("quiz closes", "Quiz 1")]


def test_search_survives_punctuation_and_matches_prefix(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    store.replace_course_rows(db, "announcements", 1, [ann(1, 1, "Quiz", body="Chain rule practice (C++ style)")])
    store.rebuild_search(db)
    assert query.search(db, 'chain "rul') and query.search(db, "c++ (") and query.search(db, "***") == []


def test_document_is_paged(db):
    store.update_courses(db, [course(1, "MATH_1", "Maths")])
    db.execute("INSERT INTO content (id, course_id, title, text) VALUES (5, 1, 'Notes', ?)", ("x" * 250,))
    page = query.document(db, 5, offset=200, length=100)
    assert page["total_chars"] == 250 and len(page["text"]) == 50 and page["more"] is False


def test_local_time_uses_configured_zone(monkeypatch):
    monkeypatch.setenv("D2L_TZ", "Asia/Qatar")
    assert query.local("2026-12-03T20:59:59.000Z") == "Thu 03 Dec 2026, 23:59"
