# d2l-brightspace

Pulls **your own** Brightspace (D2L) content into local JSON so other tools can use it: courses, assignments with
due dates and submission status, announcements, grades, and deadlines.

Read-only by design. It fetches what your account already sees in the browser; it does not submit anything.

## How it connects

Brightspace has an official API (Valence) but it needs keys from your institution's admins, which students don't
get. So this uses the two routes that need nothing but your own login:

| Route | Used for | Breaks when |
|---|---|---|
| **iCal feed** (`d2l due`) | deadlines | almost never — it's a published feature |
| **Logged-in browser** (`d2l dump`) | courses, assignments, announcements, grades | Brightspace changes its UI or internal endpoints |

You sign in **once**, by hand, in a real browser window — normal SSO and MFA. Playwright keeps that browser
profile in `user-data-dir/`, so later runs reuse the session. The scripts never see your password. When the
session eventually expires, run `d2l login` again.

Inside `fetch.py` each item tries Brightspace's own JSON endpoint first (`/d2l/api/hm/...`, `/d2l/le/...` — the
same calls the web UI makes, with the CSRF token it keeps in localStorage) and falls back to reading the rendered
page if that returns nothing.

## Setup

```bash
cp .env.example .env          # set D2L_BASE_URL, e.g. https://your-school.brightspace.com
uv sync
uv run playwright install chromium
uv run d2l login              # sign in in the window that opens
```

## Use

```bash
uv run d2l courses                  # list enrolled courses
uv run d2l dump                     # everything -> data/*.json
uv run d2l dump --show-browser      # same, but watch it work
uv run d2l show assignments --open  # only what is still unsubmitted
uv run d2l due --days 14            # deadlines from the iCal feed (no login needed)
```

Output lands in `data/` as `courses.json`, `assignments.json`, `announcements.json`, `grades.json` — plain JSON for
whatever you build next.

## Before you rely on it

- Check your university's acceptable-use policy. The technical side is easy; an account flagged for unusual
  automated access is the real cost.
- Keep it gentle: this fetches serially, per course, on demand. Don't put it on a fast timer.
- `data/`, `session/`, `user-data-dir/` and `.env` are gitignored — they contain your cookies and coursework.
- Endpoints move. If `dump` suddenly returns empty lists, open DevTools → Network on the page in question, see
  what the UI calls now, and update `fetch.py`. Note findings in `NOTES.md`.

## Status

Working against `d2l.udst.edu.qa` as of 18 Sep 2026: first run pulled 18 courses, 130 announcements, 8 assignment
folders (with due dates, scores and submission status) and 161 grade items. Confirmed endpoints, dead ends and
gotchas are in `NOTES.md`.

`d2l due` uses the iCal feed when `D2L_ICAL_URL` is set, and otherwise falls back to unsubmitted assignments with
due dates from the last `dump`.
