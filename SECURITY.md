<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Security

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's private reporting instead: **Security →
Report a vulnerability** on this repository. Include what you found, how to reproduce it, and what an attacker could
do with it. You'll get an acknowledgement, and a fix or an explanation, as soon as possible. Credit is given in the
release notes unless you prefer otherwise.

## What this app holds

It runs on your own computer and stores everything in the project folder:

| Where | What | Sensitivity |
|---|---|---|
| `session/state.json`, `user-data-dir/` | Your Brightspace and university sign-in cookies | **High:** equivalent to being logged in as you |
| `.env` | API token, optional Telegram/Discord/webhook/Semestra settings | High |
| `data/d2l.db`, `data/files/` | Your courses, grades, announcements, feedback and course files | Personal |

All of these are gitignored. `.env` is written with owner-only permissions (0600).

## Design rules

- **Read-only towards Brightspace.** No code path submits, posts or changes anything there.
- **Your password is never handled.** You sign in yourself in a real browser window; only the resulting cookies are
  kept.
- **Local by default.**
  - The dashboard and controls listen on `127.0.0.1:8766`, and only accept requests whose Host is localhost.
    Actions also need a custom header that other websites can't send (CSRF and DNS-rebinding protection).
  - The MCP/REST server listens on `127.0.0.1:8765` and requires the API token for everything except `/health`.
- **The public link is opt-in.** It's off until you turn it on in the app. It only exposes the token-protected
  MCP/REST port, never the control page. Anyone who has your connector link (which contains the token) can read
  your data. Keep it secret, and use **New token** in the app if it leaks.
- **Semestra push is opt-in.** It sends only courses, grade structure, your grades and deadlines, over HTTPS (plain
  HTTP only to localhost). It never follows redirects, so the connector key can't be forwarded elsewhere. It never
  sends cookies, files, announcements or feedback text.
- **Scraped content is treated as untrusted.** Titles, bodies and file names from Brightspace are escaped before
  they reach any page. Lesson files are only served from their own folder.
- **Subprocesses** (cloudflared, pdftoppm, tesseract, notify-send) are run with argument lists, never through a
  shell. The Cloudflare tunnel token goes through an environment variable, not the command line.

## Known limits

- Anyone with access to your user account on the computer can read the files above; that's the same as your browser
  profile. Use disk encryption on laptops.
- The public link relies on a bearer token in the URL: simple, but a leaked URL is a leaked token. Rotate it if in
  doubt.
- Instructor-provided files are downloaded and parsed (PDF, DOCX, PPTX, HTML, OCR). Parsing runs with the same
  rights as you; keep the dependencies up to date (`uv lock --upgrade`).
