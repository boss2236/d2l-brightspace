# d2l-brightspace

Your own Brightspace (D2L) account as a small local hub. It includes:
- A **synced database** of this term's courses: announcements in full, grades by category, assignments, quizzes, calendar, and the course content tree.
- **Downloaded lecture files with their text extracted.**
- **Notifications** when something changes.
- A **dashboard**.
- An **MCP server + REST API**, so any AI (Claude, Gemini, Copilot, Codex, ChatGPT…) or n8n workflow can use it as a knowledge base.
- **Assignment feedback**: instructor comments, rubric scores per criterion, and feedback files.
- **Text recognition (OCR)** for scanned PDFs, so worksheets and scanned solutions become searchable too.
- A connector for **[Semestra](docs/semestra-contract.md)**, a grade planner: copy a course's grades in its import
  format, or push them automatically after each sync.

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

## Install

One command on Linux or macOS (Windows: inside WSL):

```bash
curl -fsSL https://raw.githubusercontent.com/boss2236/d2l-brightspace/main/install.sh | bash
```

It walks you through everything, and you can re-run it any time to update:
- **Brings its own tools.** A pinned, checksum-verified `uv`, then Python 3.11+ if needed, the locked
  dependencies and Chromium. It offers to install git/curl if they're missing. Nothing global is replaced.
- **Asks for your school's Brightspace address** and checks it answers like Brightspace.
- **Picks two random free ports** (the app, and the AI connection) and tells you which. They're saved in `.env`;
  if another program takes one later, the app moves to a new free port by itself and everything follows.
  `d2l ports` shows them.
- **Opens a browser window once for you to sign in** (SSO + MFA as usual), then downloads this term's courses.
- **Lets you pick which AI apps get access:** Claude Code, Codex, Hermes Agent, Gemini CLI, VS Code, Claude Desktop.
- **Keeps it fresh:** syncs 3× a day and runs the app in the background.
- **Makes `d2l.localhost` work:** type it in your browser and the app opens. A tiny forwarder on port 80 points
  it at the app's port; on Linux this asks for your password once and runs as you, allowed nothing but port 80.

Every change is recorded and can be undone. Your shell files and AI-app configs go back to exactly how they were:

```bash
bash ~/.local/share/d2l-brightspace/app/install.sh --uninstall   # asks before deleting your data
bash ~/.local/share/d2l-brightspace/app/install.sh --purge       # everything, including data and sign-in
```

`--help` lists the options for scripted installs (`--yes`, `--school URL`, `--apps codex,hermes`, `--no-schedule`…).

### Manual setup (for development)

```bash
cp .env.example .env          # set D2L_BASE_URL, e.g. https://your-school.brightspace.com
uv sync
uv run playwright install chromium
uv run d2l login              # sign in in the window that opens
uv run d2l sync               # first run records a baseline and downloads course files
uv run d2l schedule install   # then keep it fresh: 08:00, 14:00, 20:00 (systemd user timer)
uv run d2l connect all        # add it to every AI app found on this computer (d2l disconnect --all undoes it)
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

The easy way: open **http://d2l.localhost** (or **Brightspace** from the app launcher, or `d2l open`) →
**Connect AI**. For cloud AIs (claude.ai, ChatGPT) pick how the public link is made:
- **Quick link.** A Cloudflare Quick Tunnel. No setup, but slower, and the address changes on restart.
- **My Cloudflare tunnel.** Your domain with a fixed address; paste a tunnel token and hostname.
- **My own URL / IP.** Your own proxy, public IP + port forwarding, ngrok, Tailscale…

Step-by-step for each of those, including Tailscale (private, nothing published), a named Cloudflare tunnel with a
fixed address, and reverse-proxy gotchas: [`docs/tunnels.md`](docs/tunnels.md).

A live reachability check says whether the link really works from the internet, and what's wrong if not. The tab
also adds the connector to local AI apps with one click, and can sync or log in again. The commands below do the
same by hand.

```bash
uv run d2l connect codex hermes      # or: all · `d2l connect` alone shows what's installed
uv run d2l serve                     # HTTP MCP + REST on 127.0.0.1:<your MCP port> (`d2l ports`)
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
  your data reachable from the internet; see `docs/connect-ai.html` and [`docs/tunnels.md`](docs/tunnels.md).
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
  app.py       `d2l app`: live dashboard with the Connect AI tab; runs MCP/REST and the public link
  clients.py   AI apps on this computer: detect, connect, disconnect (Claude Code, Codex, Hermes, Gemini, VS Code…)
  ports.py     the per-install ports, chosen once, moved automatically if something else takes them
  web.py       http://d2l.localhost: a tiny port-80 redirect to the app's port
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

## Semestra

**Credit hours:** Brightspace doesn't publish them, so every course is sent as 3 unless you set it — in the app's
**Connect AI → Semestra → Credit hours per course**, or with `d2l semestra credits CHEM1011=1`. You can also set it
in Semestra (Edit course); either way a sync never overwrites your value. Labs are usually 1 credit and the GPA is
credit-weighted, so it's worth setting.

Every course you're enrolled in is sent, including ones with no grades posted yet (they arrive without categories
so you can still see and plan them). Pressing **Sync now** in Semestra also works: the app checks every two minutes
and then pulls fresh data from Brightspace — no public link involved. Courses with grades have a **Copy for
Semestra** button on the Grades tab. It produces the exact JSON Semestra's
Import page accepts, with notes on anything adapted (uncategorised items placed by name, calculated totals left
out). To have it happen automatically, create a connector key in Semestra, then paste its address and key into
**Connect AI → Semestra**. After every sync the app sends courses, grade structure, your grades and deadlines, and
nothing else. From the terminal: `d2l semestra export --course MATH1030`, `d2l semestra push`.
[`docs/semestra-contract.md`](docs/semestra-contract.md) specifies the connection for anyone implementing the
receiving side.

## Security

See [`SECURITY.md`](SECURITY.md) for what the app stores, how it's protected, and how to report a vulnerability
privately.

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

