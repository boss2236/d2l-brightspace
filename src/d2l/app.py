"""`d2l app`: the dashboard as a live local app, plus everything needed to connect AIs without the terminal.

    http://127.0.0.1:8766   dashboard + "Connect AI" tab (this computer only, no token)
    http://127.0.0.1:8765   MCP + REST for AIs and apps (token-gated; the only thing the public tunnel reaches)

From the Connect AI tab you can set up the public link for cloud AIs (claude.ai, ChatGPT) — quick link, your own
Cloudflare tunnel, or your own URL/IP (see public.py) — see whether it is really reachable, add the connector to AI
apps on this computer, run a sync and log in again. Two ports keep the control page off the public link entirely:
tunnels and the direct port only ever reach 8765.
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import notify, public, query, server, store, ui
from .session import ROOT

MCP_PORT, UI_PORT = 8765, 8766
SETTINGS = ROOT / "data" / "app.json"
D2L = [sys.executable, "-m", "d2l"]         # this same install, on any OS
UV = shutil.which("uv") or "uv"
STDIO_ARGS = ["run", "--directory", str(ROOT), "d2l", "mcp"]


def _settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def _save(**kw) -> None:
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps({**_settings(), **kw}, indent=1))


class Job:
    """A background `d2l …` run whose progress the page can show."""

    def __init__(self, name: str):
        self.name, self.state, self.log, self.started, self.finished = name, "idle", [], None, None

    async def run(self, *args: str) -> None:
        if self.state == "running":
            return
        self.state, self.log, self.started, self.finished = "running", [], time.time(), None
        proc = await asyncio.create_subprocess_exec(*D2L, *args, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        async for line in proc.stdout:
            self.log = (self.log + [line.decode(errors="ignore").rstrip()])[-30:]
        code = await proc.wait()
        self.state, self.finished = ("ok" if code == 0 else "failed"), time.time()

    def view(self) -> dict:
        return {"state": self.state, "log": self.log[-12:], "started": self.started, "finished": self.finished}


# --- AI apps on this computer -----------------------------------------------------------------------------------

def _json_has(path: str, *keys: str) -> bool:
    try:
        d = json.loads(Path(path).expanduser().read_text() or "{}")
    except (OSError, ValueError):
        return False
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return bool(d)


def _json_add(path: str, section: str, entry: dict) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = p.read_text().strip() if p.exists() else ""
    if raw and not p.with_name(p.name + ".bak-d2l").exists():
        p.with_name(p.name + ".bak-d2l").write_text(raw)
    d = json.loads(raw) if raw else {}
    d.setdefault(section, {})["brightspace"] = entry
    p.write_text(json.dumps(d, indent=2))


CLIENTS = {
    "claude-code": {
        "name": "Claude Code", "installed": lambda: bool(shutil.which("claude")),
        "connected": lambda: _json_has("~/.claude.json", "mcpServers", "brightspace"),
        "add": lambda: ["claude", "mcp", "add", "-s", "user", "brightspace", "--", UV, *STDIO_ARGS],
        "how": "Ask in any Claude Code session, e.g. “what’s due this week?”"},
    "gemini": {
        "name": "Gemini CLI", "installed": lambda: bool(shutil.which("gemini")),
        "connected": lambda: _json_has("~/.gemini/settings.json", "mcpServers", "brightspace"),
        "add": lambda: ["gemini", "mcp", "add", "-s", "user", "brightspace", UV, "--", *STDIO_ARGS],
        "how": "Start gemini in a terminal and ask; /mcp lists the tools."},
    "codex": {
        "name": "Codex CLI", "installed": lambda: bool(shutil.which("codex")),
        "connected": lambda: "[mcp_servers.brightspace]" in (Path("~/.codex/config.toml").expanduser().read_text()
                                                              if Path("~/.codex/config.toml").expanduser().exists() else ""),
        "add": lambda: ["codex", "mcp", "add", "brightspace", "--", UV, *STDIO_ARGS],
        "how": "Start codex in a terminal and ask."},
    "vscode": {
        "name": "VS Code (Copilot)", "installed": lambda: bool(shutil.which("code")),
        "connected": lambda: _json_has("~/.config/Code/User/mcp.json", "servers", "brightspace"),
        "add": lambda: _json_add("~/.config/Code/User/mcp.json", "servers", {"type": "stdio", "command": UV, "args": STDIO_ARGS}),
        "how": "Copilot Chat → Agent mode → tools picker. VS Code may ask once to trust the server."},
    "claude-desktop": {
        "name": "Claude Desktop", "installed": lambda: Path("~/.config/Claude").expanduser().exists(),
        "connected": lambda: _json_has("~/.config/Claude/claude_desktop_config.json", "mcpServers", "brightspace"),
        "add": lambda: _json_add("~/.config/Claude/claude_desktop_config.json", "mcpServers", {"command": UV, "args": STDIO_ARGS}),
        "how": "Restart Claude Desktop, then turn Brightspace on in the chat’s tools menu."},
}


async def add_client(key: str) -> dict:
    c = CLIENTS[key]
    action = c["add"]()
    if isinstance(action, list):                          # a CLI command; the others already wrote their file
        proc = await asyncio.create_subprocess_exec(*action, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        if proc.returncode:
            return {"ok": False, "message": out.decode(errors="ignore")[-300:]}
    return {"ok": c["connected"](), "message": c["how"]}


def clients_view() -> list[dict]:
    return [{"key": k, "name": c["name"], "installed": c["installed"](), "connected": c["connected"](), "how": c["how"]}
            for k, c in CLIENTS.items()]


# --- the local control page -------------------------------------------------------------------------------------

def public_settings() -> dict:
    s = _settings()
    if "public" in s:
        return s["public"]
    return {"mode": "quick" if s.get("tunnel") else "off"}       # settings from before the modes existed


def build_ui_app(link: public.PublicLink, jobs: dict[str, Job]):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    local = {f"127.0.0.1:{UI_PORT}", f"localhost:{UI_PORT}"}

    async def guard(request: Request, handler):
        # DNS-rebinding and cross-site protection: right Host, and POSTs need a header a foreign page can't send
        if request.headers.get("host") not in local:
            return PlainTextResponse("forbidden", status_code=403)
        if request.method == "POST" and request.headers.get("x-d2l") != "1":
            return PlainTextResponse("forbidden", status_code=403)
        return await handler(request)

    def route(path, methods=("GET",)):
        def wrap(fn):
            async def endpoint(request: Request):
                return await guard(request, fn)
            return Route(path, endpoint, methods=list(methods))
        return wrap

    @route("/")
    async def index(request):
        return HTMLResponse(ui.render(live=True))

    @route("/ui/status")
    async def status(request):
        with store.connect() as db:
            last = store.get_meta(db, "last_sync")
            expired = bool(db.execute("SELECT 1 FROM events WHERE kind = 'session_expired' AND ts > ?",
                                      (last or "",)).fetchone())
        return JSONResponse({
            "public": link.view(), "sync": jobs["sync"].view(), "login": jobs["login"].view(),
            "last_sync": query.local(last), "session_expired": expired, "clients": clients_view(),
            "rest": {"base": f"http://127.0.0.1:{MCP_PORT}/api", "mcp": f"http://127.0.0.1:{MCP_PORT}/mcp",
                     "token": server.token()},
            "notify": {"channels": notify.channels(), "webhook": bool(os.environ.get("D2L_WEBHOOK_URL"))}})

    @route("/ui/public", methods=("POST",))
    async def set_public(request):
        try:
            cfg = public.normalise(await request.json(), public_settings())
        except (public.ConfigError, ValueError) as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        _save(public=cfg)
        await link.apply(dict(cfg))
        return JSONResponse({"ok": True, **link.view()})

    @route("/ui/public/check", methods=("POST",))
    async def check_public(request):
        await link.check()
        return JSONResponse(link.view())

    @route("/ui/sync", methods=("POST",))
    async def sync(request):
        asyncio.create_task(jobs["sync"].run("sync"))
        return JSONResponse({"ok": True})

    @route("/ui/login", methods=("POST",))
    async def login(request):
        asyncio.create_task(jobs["login"].run("login"))
        return JSONResponse({"ok": True})

    async def open_file(request: Request):
        async def fn(req):
            return await serve_file(int(req.path_params["id"]), req.query_params.get("download") == "1")
        return await guard(request, fn)

    async def open_asset(request: Request):
        async def fn(req):
            return await serve_asset(int(req.path_params["id"]), req.path_params["rest"])
        return await guard(request, fn)

    async def add(request: Request):
        async def fn(req):
            key = req.path_params["key"]
            if key not in CLIENTS:
                return JSONResponse({"ok": False, "message": "unknown app"}, status_code=404)
            return JSONResponse(await add_client(key))
        return await guard(request, fn)

    return Starlette(routes=[index, status, set_public, check_public, sync, login,
                             Route("/ui/clients/{key}", add, methods=["POST"]),
                             Route("/files/{id:int}", open_file, methods=["GET"]),
                             Route("/files/{id:int}/{rest:path}", open_asset, methods=["GET"]),
                             Mount("/docs", StaticFiles(directory=ROOT / "docs", html=True))])


def lesson_dir(topic_id: int):
    """Folder an unpacked HTML lesson lives in (data/files/<course>/<id>/), if any."""
    with store.connect() as db:
        r = db.execute("SELECT course_id FROM content WHERE id = ?", (int(topic_id),)).fetchone()
    d = ROOT / "data" / "files" / str(r[0]) / str(int(topic_id)) if r else None
    return d if d and d.is_dir() else None


async def serve_asset(topic_id: int, rel: str):
    """A page or image inside an unpacked lesson, confined to that lesson's folder."""
    from starlette.responses import FileResponse, PlainTextResponse
    site = lesson_dir(topic_id)
    target = (site / rel).resolve() if site else None
    if not target or not target.is_relative_to(site.resolve()) or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(target)


async def serve_file(topic_id: int, download: bool):
    """A course file from the local copy. Not downloaded yet (videos, images, anything new since the last sync)?
    Fetch it from Brightspace first with the saved login — the same thing `d2l get <id>` does."""
    from html import escape
    from starlette.responses import HTMLResponse

    path = server.local_file(topic_id)
    if path is not None and not download and path.suffix.lower() in (".html", ".htm"):
        site = lesson_dir(topic_id)
        if site and path.is_relative_to(site):
            from urllib.parse import quote
            from starlette.responses import RedirectResponse
            return RedirectResponse(f"/files/{topic_id}/" + quote(str(path.relative_to(site))))
    if path is None:
        proc = await asyncio.create_subprocess_exec(*D2L, "get", str(topic_id), cwd=ROOT,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 600)
        except asyncio.TimeoutError:
            proc.kill()
            out = b"Timed out fetching the file from Brightspace."
        path = server.local_file(topic_id)
        if path is None:
            msg = out.decode(errors="ignore").strip().splitlines()[-1] if out.strip() else "Couldn't get this file."
            with store.connect() as db:
                row = db.execute("SELECT title, url FROM content WHERE id = ?", (topic_id,)).fetchone()
            link = f'<p><a href="{escape(row[1])}">Open it in Brightspace instead</a></p>' if row and row[1] else ""
            return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>File unavailable</title>
<body style="font:15px/1.5 system-ui;max-width:640px;margin:48px auto;padding:0 16px">
<h2 style="margin-bottom:4px">{escape(row[0] if row else "File")}</h2><p>{escape(msg)}</p>{link}
<p><a href="/#files">← Back to Files</a></p>""", status_code=404)
    return server.file_response(topic_id, path, download)


def run(open_browser: bool = False) -> None:
    import uvicorn

    async def main():
        mcp_app, security = server.build_http_app()
        link, jobs = public.PublicLink(security), {"sync": Job("sync"), "login": Job("login")}
        servers = [uvicorn.Server(uvicorn.Config(mcp_app, host="127.0.0.1", port=MCP_PORT, log_level="warning")),
                   uvicorn.Server(uvicorn.Config(build_ui_app(link, jobs), host="127.0.0.1", port=UI_PORT,
                                                 log_level="warning"))]
        # start the public link once the MCP server is listening, so its first reachability check can pass
        async def start_link():
            await asyncio.sleep(2)
            await link.apply(dict(public_settings()))
        asyncio.create_task(start_link())
        print(f"Brightspace app  http://127.0.0.1:{UI_PORT}\nMCP + REST       http://127.0.0.1:{MCP_PORT}")
        if open_browser:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{UI_PORT}/#connect")
        try:
            await asyncio.gather(*(s.serve() for s in servers))
        finally:
            await link.stop()

    asyncio.run(main())
