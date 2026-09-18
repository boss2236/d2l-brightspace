"""The parts that decide who gets in: the token gate, public-link settings, SSO button labels, schedule parsing."""
import re

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from d2l import public, schedule, server, session

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
