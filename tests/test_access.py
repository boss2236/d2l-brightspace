# SPDX-License-Identifier: AGPL-3.0-or-later
"""The parts that decide who gets in: the token gate, public-link settings, SSO button labels, schedule parsing."""
import asyncio
import re
import sys
import types

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from d2l import app, public, schedule, server, session

TOKEN = "t0ken-for-tests-" + "x" * 30


@pytest.fixture
def client():
    async def echo(request):
        return PlainTextResponse(request.url.path)
    app = Starlette(routes=[Route("/{p:path}", echo)])
    return TestClient(server.TokenGate(app, TOKEN))


def test_token_gate(client):
    assert client.get("/health").status_code == 200                          # open, for reachability checks
    assert client.get("/api/courses").status_code == 401
    assert client.get("/api/courses", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/courses", headers={"Authorization": f"Bearer {TOKEN}"}).text == "/api/courses"
    assert client.get(f"/api/courses?token={TOKEN}").status_code == 200
    assert client.get(f"/c/{TOKEN}/mcp").text == "/mcp"                       # secret-in-path form, rewritten
    assert client.get("/c/wrong/mcp").status_code == 401
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404


def test_instance_id_is_stable_and_reveals_nothing(monkeypatch):
    monkeypatch.setenv("D2L_API_TOKEN", TOKEN)
    a, b = server.instance_id(), server.instance_id()
    assert a == b and len(a) == 16 and TOKEN[:8] not in a


@pytest.mark.parametrize("cfg, message", [
    ({"mode": "cloudflare", "cf_host": "https://bad host/", "cf_token": "x" * 60}, "Public hostname"),
    ({"mode": "cloudflare", "cf_host": "bs.example.com", "cf_token": "short"}, "tunnel token"),
    ({"mode": "custom", "custom_url": "ftp://x"}, "public address"),
    ({"mode": "custom", "custom_url": "https://x.example.com/path"}, "without a path"),
    ({"mode": "custom", "custom_url": "http://1.2.3.4:9000", "direct": True, "direct_port": 8766}, "Direct port"),
    ({"mode": "nope"}, "unknown mode"),
])
def test_public_settings_are_validated(cfg, message):
    with pytest.raises(public.ConfigError, match=message):
        public.normalise(cfg, {})


def test_public_settings_accept_what_people_paste():
    tok = "eyJ" + "A" * 80
    cfg = public.normalise({"mode": "cloudflare", "cf_host": "https://BS.Example.com/",
                            "cf_token": f"sudo cloudflared service install {tok}"}, {})
    assert cfg["cf_host"] == "bs.example.com" and cfg["cf_token"] == tok
    kept = public.normalise({"mode": "cloudflare", "cf_host": "bs.example.com", "cf_token": ""}, cfg)
    assert kept["cf_token"] == tok                                            # blank keeps the saved token
    url = public.normalise({"mode": "custom", "custom_url": f"https://my.example.com/c/{TOKEN}/mcp"}, {})
    assert url["custom_url"] == "https://my.example.com"                      # pasted connector URL trimmed


@pytest.mark.parametrize("label, expected", [
    ("UDST - Single Sign On Login", True), ("Sign in with Microsoft", True), ("SSO Login", True),
    ("Log in with Google", True), ("Institutional login", True), ("Log In", False), ("Forgot your password?", False),
])
def test_sso_button_labels(label, expected):
    assert bool(re.search(session.SSO_LABELS, label, re.I)) is expected


def test_schedule_times_and_quoting():
    assert schedule._times("08:00, 14:30,20:05") == [(8, 0), (14, 30), (20, 5)]
    with pytest.raises(SystemExit):
        schedule._times("25:00")
    assert schedule._exec(["/usr/bin/uv", "--directory", "/home/me/My Stuff"]) == '/usr/bin/uv --directory "/home/me/My Stuff"'


# --- regression tests from the pre-release security review ---------------------------------------------------------

def test_token_gate_handles_hostile_input(client):
    assert client.get("/api/x", headers={"Authorization": "Bearer ✓✓✓".encode()}).status_code == 401   # was a 500
    assert client.get("/api/x?token=%E2%9C%93").status_code == 401
    assert client.get(f"/c/{TOKEN}").status_code == 401                                          # no path after it
    assert client.get(f"/c/{TOKEN[:-1]}X/mcp").status_code == 401


def test_env_values_cannot_inject_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        server._set_env("SEMESTRA_URL", "https://x.example\nD2L_API_TOKEN=attacker")
    with pytest.raises(public.ConfigError):
        public.normalise({"mode": "custom", "custom_url": "https://x.example\nY"}, {})


def test_hostile_titles_cannot_break_the_page(db, monkeypatch):
    from conftest import course
    from d2l import store, ui
    store.update_courses(db, [course(1, "X_1", "Course")])
    evil = '</script><script>window.pwned=1</script><!--<script>'
    store.replace_course_rows(db, "announcements", 1, [{"id": 1, "course_id": 1, "title": evil, "date": "2026-09-01T00:00:00Z",
                                                        "body": evil, "html": "", "attachments": "[]"}])
    db.commit()
    page = ui.render()
    script = page.split("<script>", 1)[1]
    assert "</script>" not in script.split("const DATA", 1)[1].split("\n", 1)[0]          # data line can't close the tag
    assert "<!--" not in page.split("const DATA", 1)[1].split("\n", 1)[0]


def test_zip_bombs_are_refused(tmp_path, monkeypatch):
    import io
    import zipfile
    from d2l import sync
    monkeypatch.setattr(sync, "FILES", tmp_path)
    monkeypatch.setattr(sync, "UNZIP_MAX", 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.mp4", b"\0" * (3 * 1024 * 1024))                                    # 3 MB, compresses to ~3 KB
    with pytest.raises(ValueError, match="unpacks to more than"):
        sync._save_bytes({"id": 1, "course_id": 1, "title": "bomb", "ext": "mp4"}, buf.getvalue())


# --- the sign-in window: it needs a desktop, and the app service often starts before there is one ---------------

def test_windowed_env_keeps_a_display_it_already_has(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: pytest.fail("shouldn't ask systemd"))
    assert app._windowed_env()["WAYLAND_DISPLAY"] == "wayland-1"


def test_windowed_env_asks_the_session_manager_when_started_before_the_desktop(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    shown = ("XDG_RUNTIME_DIR=/run/user/1000\nDISPLAY=:0\nWAYLAND_DISPLAY=wayland-1\n"
             "HYPRLAND_CMD=$'Hyprland --watchdog-fd 4'\nPATH=/usr/bin\n")
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=shown, returncode=0))
    env = app._windowed_env()
    assert (env["DISPLAY"], env["WAYLAND_DISPLAY"]) == (":0", "wayland-1")
    assert env.get("HYPRLAND_CMD") != "$'Hyprland --watchdog-fd 4'"   # systemd-quoted values are left alone
    assert env["PATH"] != "/usr/bin"                                  # only the display variables are taken


def test_windowed_env_survives_a_machine_without_systemd(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError))
    assert app._no_display(app._windowed_env())


def test_no_display_only_judges_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")        # macOS and Windows open windows without DISPLAY
    assert not app._no_display({})
    monkeypatch.setattr(sys, "platform", "linux")
    assert app._no_display({})
    assert not app._no_display({"DISPLAY": ":0"})


def test_login_job_says_what_to_do_instead_of_showing_a_chrome_crash(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(app.asyncio, "create_subprocess_exec",
                        lambda *a, **k: pytest.fail("no window, so nothing should be launched"))
    job = app.Job("login")
    asyncio.run(job.run("login", env={"PATH": "/usr/bin"}))
    assert job.state == "failed"
    assert "d2l login" in "\n".join(job.log)
