# d2l-brightspace

Your own Brightspace (D2L) account as a small local hub. It includes:
- A **synced database** of this term's courses: announcements in full, grades by category, assignments, quizzes, calendar, and the course content tree.
- **Downloaded lecture files with their text extracted.**
- **Notifications** when something changes.
- A **dashboard**.
- An **MCP server + REST API**, so any AI (Claude, Gemini, Copilot, Codex, ChatGPT…) or n8n workflow can use it as a knowledge base.

Read-only by design. It reads what your account already sees; it never submits anything.

## How it connects

Brightspace's official API (Valence) normally needs app keys from the institution. It also accepts the web session of
a logged-in user, meaning the cookies plus the `X-Csrf-Token` the web app keeps in localStorage. So:

1. `d2l login` opens a real browser window. You sign in normally (SSO + MFA) and the session is saved.
   The scripts never see your password.
2. `d2l sync` restores that session in a headless browser and calls the same JSON API the Brightspace web app uses.
   Calls go one at a time, about a second apart.
3. Everything lands in `data/d2l.db` (SQLite with full-text search). The CLI, dashboard, MCP server and REST API all
   read from there and never from Brightspace, so an AI can query as much as it likes without touching the
   university server.

Confirmed endpoints, dead ends and gotchas are in `NOTES.md`.

## Setup

```bash
cp .env.example .env          # set D2L_BASE_URL, e.g. https://your-school.brightspace.com
uv sync
uv run playwright install chromium
uv run d2l login              # sign in in the window that opens
uv run d2l sync               # first run records a baseline and downloads course files
uv run d2l schedule install   # then keep it fresh: 08:00, 14:00, 20:00 (systemd user timer)
```

## Use

```bash
uv run d2l due                      # what's coming up
uv run d2l new                      # what changed recently
uv run d2l search chain rule        # full-text, including lecture-note text
uv run d2l read 4026138             # a course file's text
uv run d2l ui                       # dashboard.html
uv run d2l notify --test            # check notification channels
```

Full reference: [`docs/commands.html`](docs/commands.html). AI and n8n setup: [`docs/connect-ai.html`](docs/connect-ai.html).

### Connect an AI

The easy way: open **Brightspace** from the app launcher (the `d2l app` service at http://127.0.0.1:8766) →
**Connect AI**. For cloud AIs (claude.ai, ChatGPT) pick how the public link is made:
- **Quick link.** A Cloudflare Quick Tunnel. No setup, but slower, and the address changes on restart.
- **My Cloudflare tunnel.** Your domain with a fixed address; paste a tunnel token and hostname.
- **My own URL / IP.** Your own proxy, public IP + port forwarding, ngrok, Tailscale…

A live reachability check says whether the link really works from the internet, and what's wrong if not. The tab
also adds the connector to local AI apps with one click, and can sync or log in again. The commands below do the
same by hand.

```bash
claude mcp add -s user brightspace -- uv run --directory "$PWD" d2l mcp     # Claude Code
uv run d2l serve                                                           # HTTP MCP + REST on 127.0.0.1:8765
```

Tools the AI gets:
- `whats_new`, `get_deadlines`
- `get_announcements` / `read_announcement`, `get_grades`, `get_assignments`
- `search`, `list_files` / `read_document`, `course_brief`
- `list_courses`, `sync_status`

### Notifications

Each sync compares what it fetched with what was stored and sends an event for each of these:
- a new announcement, grade, assignment, quiz or file
- a changed due date
- something due within 48 h that's still open
- an expired session

Desktop notifications are on by default. Telegram, Discord and a generic JSON webhook (n8n → WhatsApp, email,
anything) are configured in `.env`.

## Scope

Only **this term's** courses are synced: an enrolment counts if it has both a start and an end date and today falls
between them. Service pages (student services, self-help, employment) usually have no dates, so they're skipped,
and so are past terms. `sync --scope academic|all` brings them back, `d2l courses --all` shows what was filtered
and why, and "Using it at another university" below covers schools where that rule needs help.

## Before you rely on it

- Check your university's acceptable-use policy. The technical side is easy; an account flagged for unusual
  automated access is the real cost. Three syncs a day is gentle; don't put it on a fast timer.
- `data/`, `session/`, `user-data-dir/` and `.env` are gitignored. They contain your cookies, grades and coursework.
- `d2l serve` binds to localhost and requires a token. Exposing it through a tunnel for web AIs is opt-in and makes
  your data reachable from the internet; see `docs/connect-ai.html`.
- Endpoints can move. If a sync suddenly returns empty lists, check `NOTES.md`, look at DevTools → Network on the
  page in question, and update `src/d2l/api.py`.

## Layout

```
src/d2l/
  session.py   login once, restore the saved session
  api.py       Valence calls over the session, paced
  fetch.py     shape API responses into rows (courses, announcements, grades, assignments, quizzes, calendar, content)
  sync.py      fetch → store → detect changes → download + extract files → notify → JSON exports
  store.py     SQLite schema, upserts that report changes, events, FTS index
  extract.py   text from PDF / DOCX / PPTX / HTML (incl. zipped HTML lessons)
  query.py     every read: deadlines, search, grades, briefs… (shared by CLI, dashboard, MCP, REST)
  server.py    MCP (stdio + streamable HTTP) and REST, token-gated
  app.py       `d2l app`: live dashboard :8766 with the Connect AI tab; runs MCP/REST :8765 and the public link
  public.py    public link modes (quick / own Cloudflare tunnel / own URL or IP), direct port, reachability check
  notify.py    desktop, Telegram, Discord, webhook
  schedule.py  sync timer + always-on app: systemd (Linux), launchd (macOS), Task Scheduler (Windows)
  ui.py        dashboard.html
  cli.py       `d2l …` (also `python -m d2l …`)
tests/        offline pytest suite
```

## Using it at another university

It talks to Brightspace's standard API and asks your server which API versions it supports, so it isn't tied to
one school. It was built and tested against UDST (`d2l.udst.edu.qa`); other schools may need one of these in `.env`:

| Symptom | Setting |
|---|---|
| Service pages (student services, orientation…) show up as courses | `D2L_COURSE_CODE_REGEX`, or `D2L_COURSES_EXCLUDE` |
| A real course is missing | `D2L_COURSES_INCLUDE` (ids from `d2l courses --all`) |
| You're asked to log in again every few hours | `D2L_SSO_BUTTON`: the label of your login page's SSO button |
| Dates are in the wrong time zone | `D2L_TZ` |

When a term ends, its courses are archived rather than deleted: `d2l show courses --archived` lists them, and
naming a past course (e.g. `d2l search limits --course MATH1020`) still finds its data.

## Platforms

| | Linux | macOS | Windows |
|---|---|---|---|
| Sync, dashboard, app, MCP, REST, public link | ✓ | ✓ | ✓ |
| `d2l schedule` | systemd user timer | launchd agent | Task Scheduler |
| Desktop notifications | notify-send | Notification Center | use Telegram, Discord or the webhook |

Linux is what it's developed and tested on. The macOS and Windows scheduler and notification code follows those
systems' documented interfaces but hasn't been run on real machines yet, so reports and fixes are welcome.

## Development

```bash
uv sync
uv run pytest          # offline tests: parsing, change detection, archiving, files, search, access control
```

`NOTES.md` records which Brightspace endpoints work, which don't, and the gotchas found along the way. Update it
when you find something new, especially for a school other than UDST.

Contributions are welcome. Keep it read-only towards Brightspace, keep requests serial and gentle, and never commit
anything from `data/`, `session/`, `user-data-dir/` or `.env`.

## License

Copyright (C) 2026 boss2236 and contributors.

Licensed under the **GNU Affero General Public License v3.0 or later** (see [`LICENSE`](LICENSE)). You can use,
study, change and share it. If you distribute a modified version, or run one as a service that other people use over
a network, you must make your source code available under the same license.

