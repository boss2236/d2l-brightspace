"""Your Brightspace as tools for any AI (MCP) and as a small JSON API for apps and n8n.

    uv run d2l mcp      stdio MCP — what Claude Code, Claude Desktop, Gemini CLI, VS Code Copilot, Cursor… launch
    uv run d2l serve    HTTP on 127.0.0.1:8765 — /mcp (streamable HTTP MCP) + /api/* (REST) behind a bearer token

Everything reads data/d2l.db (kept fresh by `d2l sync`); nothing here talks to Brightspace, and every tool is
read-only. docs/connect-ai.html has copy-paste setup for each client.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import query, store
from .session import ROOT

RO = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

mcp = MCPServer(
    name="brightspace",
    title="Brightspace (D2L)",
    instructions=(
        "The user's own Brightspace courses for the current term: announcements (full text), grades, assignments, "
        "quizzes, calendar deadlines, and the text of downloaded course files (lecture notes, practice books). "
        "Start with list_courses or whats_new. Use search to find material, then read_document for a file's text. "
        "`course` arguments accept a code like MATH1030, part of a name like 'chemistry', or an id. "
        "Times are already in the student's local time zone. Data is a local snapshot; sync_status says how fresh."),
)


def _since(days: float | None) -> str | None:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds") if days else None


def _db():
    return store.connect()


@mcp.tool(annotations=RO)
def list_courses() -> list[dict]:
    """This term's courses with ids, codes, sections, term dates and how much data each has."""
    with _db() as db:
        return query.courses(db)


@mcp.tool(annotations=RO)
def whats_new(since_days: float = 7) -> list[dict]:
    """Changes detected by recent syncs: new announcements, grades, assignments, quizzes, files, due-soon alerts."""
    with _db() as db:
        return query.events(db, _since(since_days), limit=100)


@mcp.tool(annotations=RO)
def get_deadlines(days: int = 14, course: str | None = None) -> list[dict]:
    """Everything dated in the next `days` days: open assignments, quizzes closing, calendar events. Soonest first."""
    with _db() as db:
        return query.deadlines(db, days, course)


@mcp.tool(annotations=RO)
def get_announcements(course: str | None = None, since_days: float | None = None, limit: int = 20) -> list[dict]:
    """Announcements, newest first, with a text preview. Use read_announcement for the full text."""
    with _db() as db:
        return query.announcements(db, course, _since(since_days), limit)


@mcp.tool(annotations=RO)
def read_announcement(id: int) -> dict:
    """Full text of one announcement."""
    with _db() as db:
        return query.announcement(db, id) or {"error": f"no announcement {id}"}


@mcp.tool(annotations=RO)
def get_grades(course: str | None = None) -> list[dict]:
    """Grade items with category, weight, your released grade (null = not graded yet) and instructor comments."""
    with _db() as db:
        return query.grades(db, course)


@mcp.tool(annotations=RO)
def get_assignments(course: str | None = None, open_only: bool = False) -> list[dict]:
    """Assignment folders: due date, instructions, submission status and score."""
    with _db() as db:
        return query.assignments(db, course, open_only)


@mcp.tool(annotations=RO)
def search(query_text: str, course: str | None = None, limit: int = 10) -> list[dict]:
    """Full-text search across announcements, assignments, grade items, content titles and course file text.
    Returns kind + ref (the id to pass to read_document / read_announcement) and a highlighted snippet."""
    with _db() as db:
        return query.search(db, query_text, course, limit=limit)


@mcp.tool(annotations=RO)
def list_files(course: str | None = None) -> list[dict]:
    """The course content tree: every topic with its module, type and whether its text is readable."""
    with _db() as db:
        return query.files(db, course)


@mcp.tool(annotations=RO)
def read_document(id: int, offset: int = 0, length: int = 20000) -> dict:
    """Extracted text of a course file (PDF/DOCX/PPTX/HTML), paged: call again with offset while `more` is true."""
    with _db() as db:
        return query.document(db, id, offset, min(length, 50000)) or {"error": f"no content item {id}"}


@mcp.tool(annotations=RO)
def course_brief(course: str) -> str:
    """One course as markdown: upcoming dates, grades, every announcement in full, and the content outline."""
    with _db() as db:
        ids = query.course_ids(db, course)
        return "\n\n---\n\n".join(query.course_markdown(db, i) for i in ids) or f"No course matches {course!r}"


@mcp.tool(annotations=RO)
def sync_status() -> dict:
    """When the data was last pulled from Brightspace, and what is stored."""
    with _db() as db:
        n = lambda t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        last = store.get_meta(db, "last_sync")
        return {"last_sync": query.local(last), "last_sync_utc": last,
                **{t: n(t) for t in ("courses", "announcements", "grades", "assignments", "quizzes", "content")},
                "files_with_text": db.execute("SELECT count(*) FROM content WHERE file_status = 'ok'").fetchone()[0]}


@mcp.resource("d2l://course/{course_id}", mime_type="text/markdown")
def course_resource(course_id: str) -> str:
    """A course brief as a resource, for clients that attach resources as context."""
    with _db() as db:
        return query.course_markdown(db, int(course_id))


@mcp.prompt()
def week_ahead() -> str:
    """Plan my week from Brightspace."""
    return ("Using the brightspace tools: call get_deadlines(days=7) and whats_new(since_days=7), skim any new "
            "announcements with read_announcement, then give me a day-by-day plan for the next 7 days: what is due, "
            "what to prepare, and which course material (search + read_document) to review for each item.")


# --- REST ------------------------------------------------------------------------------------------------------

def _rest():
    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse

    def route(path):
        def wrap(fn):
            async def endpoint(request: Request):
                q = dict(request.query_params)
                try:
                    with _db() as db:
                        out = fn(db, request.path_params, q)
                except (ValueError, TypeError) as e:
                    return JSONResponse({"error": str(e)}, status_code=400)
                if isinstance(out, str):
                    return PlainTextResponse(out, media_type="text/markdown")
                return JSONResponse(out)
            mcp.custom_route(path, methods=["GET"])(endpoint)
        return wrap

    num = lambda q, k, d: float(q[k]) if q.get(k) else d
    route("/api/courses")(lambda db, p, q: query.courses(db))
    route("/api/deadlines")(lambda db, p, q: query.deadlines(db, int(num(q, "days", 14)), q.get("course")))
    route("/api/announcements")(lambda db, p, q: query.announcements(db, q.get("course"), _since(num(q, "since_days", None)),
                                                                    int(num(q, "limit", 30)), q.get("full") == "1"))
    route("/api/announcements/{id:int}")(lambda db, p, q: query.announcement(db, p["id"]))
    route("/api/grades")(lambda db, p, q: query.grades(db, q.get("course")))
    route("/api/assignments")(lambda db, p, q: query.assignments(db, q.get("course"), q.get("open") == "1"))
    route("/api/search")(lambda db, p, q: query.search(db, q.get("q", ""), q.get("course"), limit=int(num(q, "limit", 15))))
    route("/api/files")(lambda db, p, q: query.files(db, q.get("course")))
    route("/api/documents/{id:int}")(lambda db, p, q: query.document(db, p["id"], int(num(q, "offset", 0)), int(num(q, "length", 20000))))
    route("/api/events")(lambda db, p, q: query.events(db, _since(num(q, "since_days", 7)), int(num(q, "limit", 100))))
    route("/api/courses/{id:int}/brief")(lambda db, p, q: query.course_markdown(db, p["id"]))
    route("/api/status")(lambda db, p, q: sync_status())

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        # "instance" lets the app's reachability check tell *this* server apart from anything else answering
        return JSONResponse({"ok": True, "instance": instance_id()})


class TokenGate:
    """Token check in front of everything except /health.

    Accepted forms, for clients with different abilities:
      Authorization: Bearer <token>      local apps, n8n, CLIs
      /c/<token>/mcp                     cloud AI connectors (claude.ai, ChatGPT…) that only take a URL
      ?token=<token>                     anything else that can't set headers
    /.well-known/* answers 404 so connectors don't go looking for an OAuth login this server doesn't have.
    """

    def __init__(self, app, token: str):
        self.app, self.token = app, token
        self.prefix = f"/c/{token}"

    async def _deny(self, send, status: int, body: bytes):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if path.startswith("/.well-known/"):
                return await self._deny(send, 404, b'{"error": "no oauth here"}')
            if path.startswith(self.prefix + "/"):
                scope = {**scope, "path": path[len(self.prefix):], "raw_path": path[len(self.prefix):].encode()}
            elif path != "/health":
                headers = dict(scope.get("headers") or [])
                auth = headers.get(b"authorization", b"").decode()
                qs = dict(p.split("=", 1) for p in scope.get("query_string", b"").decode().split("&") if "=" in p)
                if not (secrets.compare_digest(auth, f"Bearer {self.token}")
                        or secrets.compare_digest(qs.get("token", ""), self.token)):
                    return await self._deny(send, 401, b'{"error": "missing or wrong token"}')
        await self.app(scope, receive, send)


def instance_id() -> str:
    """A public fingerprint of this install (derived from the token, reveals nothing about it)."""
    import hashlib
    return hashlib.sha256(("d2l-instance:" + token()).encode()).hexdigest()[:16]


def token() -> str:
    """D2L_API_TOKEN from .env, generated and saved there on first use."""
    t = os.environ.get("D2L_API_TOKEN")
    if t:
        return t
    t = secrets.token_urlsafe(32)
    env = ROOT / ".env"
    with open(env, "a") as f:
        f.write(f"\n# generated by `d2l serve`: bearer token for /api and /mcp\nD2L_API_TOKEN={t}\n")
    os.environ["D2L_API_TOKEN"] = t
    print(f"generated D2L_API_TOKEN and saved it to {env.name}")
    return t


def run_stdio() -> None:
    mcp.run()


def build_http_app(host: str = "127.0.0.1", public_hosts: list[str] | None = None):
    """The token-gated MCP + REST ASGI app, and its host allow-list (a live list: add a tunnel's host at runtime)."""
    from mcp.server.transport_security import TransportSecuritySettings

    _rest()
    extra = [h.strip() for h in os.environ.get("D2L_PUBLIC_HOSTS", "").split(",") if h.strip()] + (public_hosts or [])
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", *extra],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", *(f"https://{h}" for h in extra)])
    # stateless: cloud connectors may reconnect through the tunnel at any time; every tool call stands alone anyway
    app = TokenGate(mcp.streamable_http_app(host=host, transport_security=security, stateless_http=True), token())
    return app, security


def allow_host(security, host: str) -> None:
    if host not in security.allowed_hosts:
        security.allowed_hosts.append(host)
        security.allowed_origins.append(f"https://{host}")


def connector_url(public_host: str) -> str:
    return f"https://{public_host}/c/{token()}/mcp"


def run_http(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    app, _ = build_http_app(host)
    print(f"MCP  http://{host}:{port}/mcp\nREST http://{host}:{port}/api/…  (Authorization: Bearer $D2L_API_TOKEN)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
