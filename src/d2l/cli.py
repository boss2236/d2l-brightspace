# SPDX-License-Identifier: AGPL-3.0-or-later
"""Command line. `uv run d2l --help` lists everything; docs/commands.html explains each command.

    uv run d2l login          # sign in once in a real browser window; session is saved
    uv run d2l sync           # pull this term into data/d2l.db, download course files, notify on changes
    uv run d2l due            # what's coming up
    uv run d2l new            # what changed recently
    uv run d2l search limits  # full-text search, including lecture-note text
    uv run d2l ui             # build dashboard.html and open it
    uv run d2l mcp | serve    # expose it to AI tools (stdio MCP) or apps/n8n (HTTP MCP + REST)
    uv run d2l schedule install   # sync 3x a day with a systemd user timer
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from .session import ROOT


def _print(rows: list[dict], cols: list[str]) -> None:
    for r in rows:
        print("  ".join(str(r.get(c) if r.get(c) is not None else "—") for c in cols))
    print(f"\n{len(rows)} rows")


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="d2l", description="Your Brightspace, locally: sync, search, dashboard, AI access")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="sign in once in a visible browser; the session is saved")

    for name in ("sync", "dump"):
        s = sub.add_parser(name, help="pull this term from Brightspace, download files, notify on changes"
                           if name == "sync" else "same as sync")
        s.add_argument("--show-browser", action="store_true", help="watch it work instead of running headless")
        s.add_argument("--scope", choices=["current", "academic", "all"], default="current",
                       help="current = this term (default), academic = every term, all = also service shells")
        s.add_argument("--no-files", action="store_true", help="skip downloading course files")
        s.add_argument("--quiet", action="store_true", help="record changes but don't send notifications")

    c = sub.add_parser("courses", help="list courses live from Brightspace")
    c.add_argument("--all", action="store_true", help="include past terms and service shells (Student Services, …)")

    du = sub.add_parser("due", help="upcoming deadlines: assignments, quizzes, calendar events")
    du.add_argument("--days", type=int, default=14, help="look this many days ahead (default 14)")
    du.add_argument("--course", help="code, name fragment or id, e.g. MATH1030 or chem")

    n = sub.add_parser("new", help="what changed in recent syncs")
    n.add_argument("--days", type=float, default=7)

    se = sub.add_parser("search", help="full-text search: announcements, assignments, grades, course file text")
    se.add_argument("query", nargs="+")
    se.add_argument("--course")

    s = sub.add_parser("show", help="print stored data")
    s.add_argument("what", choices=["courses", "announcements", "assignments", "grades", "files"])
    s.add_argument("--course")
    s.add_argument("--open", action="store_true", help="assignments only: hide the ones already submitted")
    s.add_argument("--json", action="store_true", help="print JSON instead of a table")
    s.add_argument("--archived", action="store_true", help="courses: include past terms that were archived")

    r = sub.add_parser("read", help="print one course file's extracted text (id from `show files` or `search`)")
    r.add_argument("id", type=int)

    g = sub.add_parser("get", help="download one course file (id from `show files`/`search`) and print its path")
    g.add_argument("id", type=int)

    sub.add_parser("reindex", help="re-extract text from downloaded files (no network)")

    u = sub.add_parser("ui", help="build dashboard.html from the stored data and open it")
    u.add_argument("--no-open", action="store_true", help="just write dashboard.html, don't open a browser")

    sub.add_parser("mcp", help="run the MCP server on stdio (for Claude, Gemini CLI, Copilot, Cursor…)")
    sv = sub.add_parser("serve", help="run MCP over HTTP + the REST API (for apps, n8n, remote AIs)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)

    ap_ = sub.add_parser("app", help="the dashboard as a live app at http://127.0.0.1:8766, with the Connect AI tab")
    ap_.add_argument("--open", action="store_true", help="open it in the browser")

    se2 = sub.add_parser("semestra", help="send grades to Semestra: export its import JSON, or push to a connector")
    se2.add_argument("action", choices=["export", "push", "status", "credits"])
    se2.add_argument("--course", help="export: code, name fragment or id (default: every course with grades)")
    se2.add_argument("--credits", type=float, help="export: credit hours to put in the course (default 3)")
    se2.add_argument("set", nargs="*", help="credits: CODE=HOURS pairs, e.g. CHEM1011=1 MATH1030=3")

    tk = sub.add_parser("token", help="show or rotate the API token used by serve/app (REST, HTTP MCP, public link)")
    tk.add_argument("--rotate", action="store_true", help="replace it; existing links and n8n credentials stop working")

    no = sub.add_parser("notify", help="send pending notifications, or --test every channel")
    no.add_argument("--test", action="store_true")

    sc = sub.add_parser("schedule", help="install/remove the 3x-a-day systemd user timer")
    sc.add_argument("action", choices=["install", "status", "remove"])
    sc.add_argument("--times", default="08:00,14:00,20:00", help="comma-separated HH:MM (default 08:00,14:00,20:00)")
    sc.add_argument("--serve", action="store_true", help="also keep `d2l app` running (dashboard + AI connections) and add a launcher")

    args = ap.parse_args()

    if args.cmd == "login":
        from . import session
        session.login()
    elif args.cmd in ("sync", "dump"):
        from . import sync
        sync.run(headless=not args.show_browser, scope=args.scope, files=not args.no_files, quiet=args.quiet)
    elif args.cmd == "courses":
        from . import fetch, session
        from .api import Api
        with session.signed_in_page() as page:
            cs = fetch.courses(Api(page))
        for c in fetch.pick(cs, "all" if args.all else "current"):
            tag = "" if c["current"] else ("  (past term)" if c["academic"] else "  (not a course)")
            print(f"{c['id']:>8}  {c['short']}{tag}")
    elif args.cmd == "mcp":
        from . import server
        server.run_stdio()
    elif args.cmd == "serve":
        from . import server
        server.run_http(args.host, args.port)
    elif args.cmd == "token":
        from . import server
        if args.rotate:
            server.rotate_token()
            print("new token saved to .env — restart `d2l app`/`d2l serve` if running, then update your connectors")
        else:
            print(server.token())
    elif args.cmd == "app":
        from . import app
        app.run(open_browser=args.open)
    elif args.cmd == "schedule":
        from . import schedule
        {"install": lambda: schedule.install(args.times, args.serve), "status": schedule.status,
         "remove": schedule.remove}[args.action]()
    elif args.cmd == "get":
        from . import sync
        print(sync.get_file(args.id))
    elif args.cmd == "ui":
        from . import ui
        ui.build(open_browser=not args.no_open)
    else:
        _local(args)


def _local(args) -> None:
    """Commands that only read data/d2l.db."""
    from . import notify, query, store
    if not store.DB.exists():
        raise SystemExit("Nothing stored yet — run `uv run d2l sync` first.")
    with store.connect() as db:
        if args.cmd == "due":
            rows = query.deadlines(db, args.days, args.course)
            if not rows:
                print(f"nothing dated in the next {args.days} days")
            for d in rows:
                h = d["in_hours"]
                left = "past" if h < 0 else f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"
                print(f"{d['when']:<24} {left:>5}  {d['kind']:<12} {d['title'][:58]:<58} {d['course'] or ''}")
        elif args.cmd == "new":
            rows = query.events(db, (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat())
            for e in rows:
                print(f"{e['when']:<24} {notify.ICON.get(e['kind'], '•')} {(e['course'] or '')[:28]:<28} {e['summary'][:90]}")
            print(f"\n{len(rows)} changes in the last {args.days:g} days (last sync {query.local(store.get_meta(db, 'last_sync'))})")
        elif args.cmd == "search":
            for h in query.search(db, " ".join(args.query), args.course):
                print(f"[{h['kind']} {h['ref']}] {h['course']} — {h['title']}\n    {h['snippet'].strip()}\n")
        elif args.cmd == "read":
            d = query.document(db, args.id, 0, 10**9)
            print(json.dumps(d, indent=1, ensure_ascii=False) if not d or "text" not in d else f"# {d['title']}\n\n{d['text']}")
        elif args.cmd == "reindex":
            from .extract import reindex
            from .sync import unpack_stored
            print(f"unpacked {unpack_stored(db)} zipped files")
            print(f"re-extracted {reindex(db, ROOT)} files")
            store.rebuild_search(db)
        elif args.cmd == "semestra":
            import sys
            from . import semestra
            if args.action == "export":
                ids = query.course_ids(db, args.course)
                exported = 0
                for cid in ids:
                    p, warnings = semestra.payload(db, cid, args.credits)
                    for w in warnings:
                        print(f"# {cid}: {w}", file=sys.stderr)
                    if p:
                        print(semestra.dumps(p))
                        exported += 1
                if not exported:
                    raise SystemExit("nothing to export yet (no course with published grade items)")
            elif args.action == "credits":
                for pair in args.set:
                    code, _, value = pair.partition("=")
                    for cid in query.course_ids(db, code):
                        semestra.set_credits(db, cid, float(value) if value else None)
                for p in semestra.all_payloads(db):
                    print(f"{p['code']}-{p['section']:<3} {p['credits']:>4g} credits  {p['name']}")
            elif args.action == "push":
                r = semestra.push(db)
                print(("✓ " if r["ok"] else "✕ ") + r["message"])
            else:
                print("configured:", semestra.configured(), "| last push:", semestra.last_push(db) or "never")
        elif args.cmd == "notify":
            if args.test:
                chans = notify.channels()
                print("channels:", ", ".join(chans) or "none configured")
                print(notify.send([{"kind": "test", "summary": "d2l notifications work", "detail": "test message",
                                    "course": "", "url": ""}]))
            else:
                print(notify.flush(db) or "nothing pending")
        elif args.cmd == "show":
            fn = {"courses": lambda: query.courses(db, args.archived), "announcements": lambda: query.announcements(db, args.course, limit=500),
                  "assignments": lambda: query.assignments(db, args.course, args.open),
                  "grades": lambda: query.grades(db, args.course), "files": lambda: query.files(db, args.course)}
            rows = fn[args.what]()
            if args.json:
                print(json.dumps(rows, indent=1, ensure_ascii=False))
                return
            cols = {"courses": ["id", "code", "section", "name"], "announcements": ["posted", "course", "title"],
                    "assignments": ["due", "course", "name", "status", "score"],
                    "grades": ["course", "category", "item", "grade", "points"],
                    "files": ["id", "course", "type", "status", "title"]}[args.what]
            _print(rows, cols)
