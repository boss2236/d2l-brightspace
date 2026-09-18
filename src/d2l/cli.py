"""Command line: d2l login | courses | dump | due | show

    uv run d2l login          # sign in once in a real browser window; session is saved
    uv run d2l courses        # list enrolled courses
    uv run d2l dump           # pull courses, assignments, announcements, grades into data/
    uv run d2l due            # deadlines from the iCal feed (no login needed)
    uv run d2l show assignments --open   # read what was pulled, e.g. only unsubmitted ones
"""
import argparse
import json
import os

from dotenv import load_dotenv

from . import fetch, session, ui
from .calendar import deadlines


def _due_from_dump(days: int) -> list[dict]:
    """Fallback when no iCal feed is configured: unsubmitted assignments from data/assignments.json."""
    from datetime import datetime, timezone
    path = fetch.OUT / "assignments.json"
    if not path.exists():
        raise SystemExit("No iCal feed set and nothing fetched yet — run `d2l dump`, or set D2L_ICAL_URL in .env")
    names = {c["id"]: c["name"] for c in json.loads((fetch.OUT / "courses.json").read_text())}
    now, out = datetime.now(timezone.utc), []
    for a in json.loads(path.read_text()):
        if a.get("submitted") or not a.get("due"):
            continue
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
            try:
                when = datetime.strptime(a["due"], fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                when = None
        if not when:
            continue
        delta = (when - now).days
        if -1 <= delta <= days:
            out.append({"due": when.isoformat(timespec="minutes"), "in_days": delta,
                        "title": a["name"], "course": names.get(a["course_id"], "")})
    return sorted(out, key=lambda r: r["due"])


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="d2l", description="Read your Brightspace content into local JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="sign in once in a visible browser; the profile keeps you logged in")
    sub.add_parser("courses", help="list enrolled courses")
    d = sub.add_parser("dump", help="fetch everything into data/")
    d.add_argument("--show-browser", action="store_true", help="watch it work instead of running headless")
    du = sub.add_parser("due", help="deadlines from the iCal feed")
    du.add_argument("--days", type=int, default=30)
    u = sub.add_parser("ui", help="build dashboard.html from the fetched data and open it")
    u.add_argument("--no-open", action="store_true")
    s = sub.add_parser("show", help="print what was pulled")
    s.add_argument("what", choices=["courses", "assignments", "announcements", "grades"])
    s.add_argument("--open", action="store_true", help="assignments only: hide the ones already submitted")
    args = ap.parse_args()

    if args.cmd == "login":
        session.login()
    elif args.cmd == "courses":
        with session.signed_in_page() as page:
            for c in fetch.courses(page):
                print(f"{c['id']:>8}  {'' if c['active'] else '(inactive) '}{c['name']}")
    elif args.cmd == "dump":
        fetch.dump_all(headless=not args.show_browser)
    elif args.cmd == "due":
        rows = deadlines(days=args.days) if os.environ.get("D2L_ICAL_URL") else _due_from_dump(args.days)
        if not rows:
            print("nothing due in that window")
        for r in rows:
            when = "today" if r["in_days"] == 0 else f"in {r['in_days']}d"
            print(f"{r['due'][:16]}  {when:<7} {r['title'][:60]:<60} {r['course'][:30]}")
    elif args.cmd == "ui":
        ui.build(open_browser=not args.no_open)
    elif args.cmd == "show":
        path = fetch.OUT / f"{args.what}.json"
        if not path.exists():
            raise SystemExit(f"{path} not there yet — run `d2l dump` first")
        rows = json.loads(path.read_text())
        if args.what == "assignments" and args.open:
            rows = [r for r in rows if not r.get("submitted")]
        for r in rows:
            print(" | ".join(f"{k}={str(v)[:60]}" for k, v in r.items() if v not in (None, "")))
        print(f"\n{len(rows)} rows")
