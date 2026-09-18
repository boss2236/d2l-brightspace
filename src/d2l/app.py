"""`d2l app`: the dashboard as a live local app, plus everything needed to connect AIs without the terminal.

    http://127.0.0.1:8766   dashboard + "Connect AI" tab (this computer only, no token)
    http://127.0.0.1:8765   MCP + REST for AIs and apps (token-gated; the only thing the public tunnel reaches)

From the Connect AI tab you can switch the public link for cloud AIs (claude.ai, ChatGPT) on and off and copy it,
add the connector to AI apps on this computer, run a sync and log in again. Two ports keep the control page off the
tunnel entirely: cloudflared forwards to 8765 only.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import notify, query, server, store, ui
from .session import ROOT

MCP_PORT, UI_PORT = 8765, 8766
SETTINGS = ROOT / "data" / "app.json"
D2L = str(Path(sys.executable).parent / "d2l")
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
        proc = await asyncio.create_subprocess_exec(D2L, *args, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        async for line in proc.stdout:
            self.log = (self.log + [line.decode(errors="ignore").rstrip()])[-30:]
        code = await proc.wait()
        self.state, self.finished = ("ok" if code == 0 else "failed"), time.time()

    def view(self) -> dict:
        return {"state": self.state, "log": self.log[-12:], "started": self.started, "finished": self.finished}


class Tunnel:
    """Cloudflare quick tunnel to the MCP port. Restarts itself if it drops (the address changes when it does)."""

    def __init__(self, security):
        self.security, self.proc, self.host, self.state, self.error, self.since = security, None, None, "off", None, None
        self.wanted = False

    async def start(self) -> None:
        self.wanted = True
        if self.proc or not shutil.which("cloudflared"):
            if not shutil.which("cloudflared"):
                self.state, self.error = "error", "cloudflared is not installed (sudo pacman -S cloudflared)"
            return
        self.state, self.error = "starting", None
        self.proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{MCP_PORT}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        asyncio.create_task(self._watch(self.proc))

    async def _watch(self, proc) -> None:
        async for raw in proc.stderr:
            m = re.search(r"https://([a-z0-9-]+\.trycloudflare\.com)", raw.decode(errors="ignore"))
            if m and not self.host:
                self.host = m.group(1)
                server.allow_host(self.security, self.host)
                (ROOT / "data" / "connector-url.txt").write_text(server.connector_url(self.host) + "\n")
                await asyncio.sleep(3)                    # the edge needs a moment before the address resolves
                self.state, self.since = "on", time.time()
        await proc.wait()
        self.proc, self.host = None, None
        if self.wanted:                                   # dropped, not stopped: come back with a new address
            self.state, self.error = "starting", "the link dropped and is restarting — the address will change"
            await asyncio.sleep(5)
            await self.start()
        else:
            self.state = "off"

    async def stop(self) -> None:
        self.wanted = False
        if self.proc:
            self.proc.terminate()
            await self.proc.wait()
        self.proc, self.host, self.state, self.error = None, None, "off", None

    def view(self) -> dict:
        return {"state": self.state, "error": self.error, "since": self.since,
                "url": server.connector_url(self.host) if self.host and self.state == "on" else None}


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

def build_ui_app(tunnel: Tunnel, jobs: dict[str, Job]):
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
            "tunnel": tunnel.view(), "sync": jobs["sync"].view(), "login": jobs["login"].view(),
            "last_sync": query.local(last), "session_expired": expired, "clients": clients_view(),
            "rest": {"base": f"http://127.0.0.1:{MCP_PORT}/api", "mcp": f"http://127.0.0.1:{MCP_PORT}/mcp",
                     "token": server.token()},
            "notify": {"channels": notify.channels(), "webhook": bool(os.environ.get("D2L_WEBHOOK_URL"))}})

    @route("/ui/tunnel", methods=("POST",))
    async def set_tunnel(request):
        on = bool((await request.json()).get("on"))
        _save(tunnel=on)
        await (tunnel.start() if on else tunnel.stop())
        return JSONResponse(tunnel.view())

    @route("/ui/sync", methods=("POST",))
    async def sync(request):
        asyncio.create_task(jobs["sync"].run("sync"))
        return JSONResponse({"ok": True})

    @route("/ui/login", methods=("POST",))
    async def login(request):
        asyncio.create_task(jobs["login"].run("login"))
        return JSONResponse({"ok": True})

    async def add(request: Request):
        async def fn(req):
            key = req.path_params["key"]
            if key not in CLIENTS:
                return JSONResponse({"ok": False, "message": "unknown app"}, status_code=404)
            return JSONResponse(await add_client(key))
        return await guard(request, fn)

    return Starlette(routes=[index, status, set_tunnel, sync, login,
                             Route("/ui/clients/{key}", add, methods=["POST"]),
                             Mount("/docs", StaticFiles(directory=ROOT / "docs", html=True))])


def run(open_browser: bool = False) -> None:
    import uvicorn

    async def main():
        mcp_app, security = server.build_http_app()
        tunnel, jobs = Tunnel(security), {"sync": Job("sync"), "login": Job("login")}
        if _settings().get("tunnel"):
            await tunnel.start()
        servers = [uvicorn.Server(uvicorn.Config(mcp_app, host="127.0.0.1", port=MCP_PORT, log_level="warning")),
                   uvicorn.Server(uvicorn.Config(build_ui_app(tunnel, jobs), host="127.0.0.1", port=UI_PORT,
                                                 log_level="warning"))]
        print(f"Brightspace app  http://127.0.0.1:{UI_PORT}\nMCP + REST       http://127.0.0.1:{MCP_PORT}")
        if open_browser:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{UI_PORT}/#connect")
        try:
            await asyncio.gather(*(s.serve() for s in servers))
        finally:
            await tunnel.stop()

    asyncio.run(main())
