"""Shared fixtures. Everything here is offline: no browser, no Brightspace, no network."""
from datetime import datetime, timedelta, timezone

import pytest

from d2l import store


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("D2L_BASE_URL", "https://school.example.com")
    monkeypatch.setenv("D2L_TZ", "UTC")
    for var in ("D2L_COURSE_CODE_REGEX", "D2L_COURSES_INCLUDE", "D2L_COURSES_EXCLUDE", "D2L_SSO_BUTTON"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh database in a temp folder, with the data folders pointed there too."""
    monkeypatch.setattr(store, "DB", tmp_path / "data" / "d2l.db")
    with store.connect() as conn:
        yield conn


def iso(days: float = 0, hours: float = 0) -> str:
    """A UTC timestamp relative to now, in Brightspace's format."""
    t = datetime.now(timezone.utc) + timedelta(days=days, hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def course(ou: int, code: str, name: str, start=None, end=None) -> dict:
    """A course row as fetch.courses() produces it."""
    return {"id": ou, "name": name, "code": code, "short": name, "section": None, "academic": 1, "current": 1,
            "start": start, "end": end, "url": f"https://school.example.com/d2l/home/{ou}"}
