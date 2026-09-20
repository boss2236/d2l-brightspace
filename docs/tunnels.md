<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Reaching this from outside

Everything here runs on your own computer, and apps on that computer reach it at `127.0.0.1` with nothing to set
up. You only need a public address for something that isn't on your computer:

| You want to… | Need a public address? |
|---|---|
| Claude Code, Cursor, Copilot, Gemini CLI on this machine | No — they run `d2l mcp` over stdio |
| n8n, a script or another app on this machine | No — `http://127.0.0.1:8765` with the token |
| Another of your own machines, or your phone | Only a **private** route (Tailscale, VPN) |
| **claude.ai, ChatGPT and other cloud AIs** | Yes — they connect from Anthropic's or OpenAI's servers |
| Semestra's **Sync now** | No — the connector polls Semestra, so nothing has to reach you |

Pick a route below, then set it in the app: **Brightspace → Connect AI → Public link** (`d2l app`, at
http://127.0.0.1:8766).

## What is actually exposed

| Port | What it is | Exposed? |
|---|---|---|
| 8765 | MCP + REST for AIs and apps, token-gated | **This one, and only this one** |
| 8766 | the app itself: dashboard, settings, sync and login buttons | **Never.** Localhost only, and it refuses requests whose `Host` isn't localhost |

The connector link you give an AI looks like this, and the middle part is your API token:

```
https://<your public host>/c/<token>/mcp
```

**Treat that link as a password.** Anyone who has it can read everything the AI can: your courses, grades,
announcements, deadlines and the text of your course files. It can't write anything to Brightspace — nothing in
this project can — but it is your data. `/health` is the one route that answers without a token, so the app (and
you) can check the link is alive:

```bash
curl https://<your public host>/health
# {"ok":true,"instance":"eb07714376ad1fdf"}   ← same "instance" as http://127.0.0.1:8765/health means it's really you
```

## Choosing a route

| Route | Address | Account | Who can reach it | Set it in |
|---|---|---|---|---|
| **Cloudflare quick tunnel** | new one on every restart | none | anyone with the link | Quick link |
| **Cloudflare named tunnel** | fixed, on your domain | Cloudflare (free) | anyone with the link | My Cloudflare tunnel |
| **Tailscale** | fixed `*.ts.net` | Tailscale (free) | **only your own devices** | My own URL / IP |
| **Tailscale Funnel** | fixed `*.ts.net` | Tailscale (free) | anyone with the link | My own URL / IP |
| **ngrok** | new one per run (free tier) | ngrok | anyone with the link | My own URL / IP |
| **Your own domain + reverse proxy** | fixed | a server you rent | anyone with the link | My own URL / IP |
| **Public IP + port forwarding** | your IP, may change | none | anyone with the link | My own URL / IP + direct port |

If a cloud AI is not involved, prefer Tailscale or a VPN: nothing is published to the internet at all.

## Cloudflare quick tunnel

Nothing to set up. Needs `cloudflared` installed (`sudo pacman -S cloudflared`, `brew install cloudflared`, or
Cloudflare's package). Choose **Quick link** in the app and copy the connector URL it shows.

The catch is in the name: the address is random and **changes every time the app restarts or you reboot**, so each
time you must re-copy the link into the cloud AI. Fine for trying things out, annoying as a permanent setup.

## Your own Cloudflare tunnel

A fixed address on a domain you own, still with no ports open on your router. You need a Cloudflare account with
your domain on it, and `cloudflared` installed.

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** → **Cloudflared**.
2. Name it (e.g. `d2l-brightspace`) and save. Ignore the install commands it shows — the app runs `cloudflared`
   itself.
3. **Public Hostname** → **Add a public hostname**:
   - Subdomain + Domain: e.g. `brightspace` . `example.com`
   - Type **HTTP**, URL `localhost:8765`
4. **Configure** → copy the token: the long string after `--token` in the install command.
5. In the app: **My Cloudflare tunnel** → paste the hostname and the token → Save.

The app runs and supervises `cloudflared` from then on, and passes the token through the environment rather than
the command line, so it doesn't show up in `ps`. If the token is wrong or the tunnel was deleted, `cloudflared`
retries forever instead of failing; the app gives it 45 seconds and then says so.

**The token is a credential, one per person.** It authorises carrying traffic into your machine under your
Cloudflare account. Never commit it, never paste it in an issue, and if you fork this project don't ship yours —
every user makes their own tunnel, or uses Quick link, or their own URL.

## Tailscale — private, nothing published

Your own devices only, over WireGuard. A cloud AI **cannot** use this; your laptop, desktop, phone and any
self-hosted AI on your tailnet can.

```bash
tailscale up                                            # once, on each device
tailscale serve --bg --https=443 http://127.0.0.1:8765  # publish to your tailnet only
tailscale serve status                                  # shows https://<machine>.<tailnet>.ts.net
```

Then in the app: **My own URL / IP** → `https://<machine>.<tailnet>.ts.net`. Leave the direct port off — Tailscale
reaches `127.0.0.1` itself, so nothing has to listen on your other network interfaces.

Undo it with `tailscale serve --https=443 off`.

## Tailscale Funnel — the same name, public

Funnel puts that `ts.net` name on the real internet, so cloud AIs can reach it. Enable Funnel for the node in the
Tailscale admin console (Access controls → `nodeAttrs` → `funnel`), then:

```bash
tailscale funnel --bg 8765
tailscale funnel status
```

Same address in the app as above. It is now as public as any other route: the link is the only thing protecting
your data.

## ngrok

```bash
ngrok http 8765
```

Copy the `https://….ngrok-free.app` address into **My own URL / IP**. On the free tier the address changes every
run, the same nuisance as the quick tunnel; a reserved domain on a paid plan fixes that. ngrok's browser warning
page doesn't affect API clients.

## Your own domain through a reverse proxy

For a server you already rent. Get the traffic from the server to your laptop first — WireGuard, Tailscale, or an
SSH reverse tunnel kept up by autossh:

```bash
ssh -N -R 127.0.0.1:8765:127.0.0.1:8765 you@your-server
```

Then terminate TLS there. Caddy needs no more than this:

```caddyfile
brightspace.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

nginx needs the `Host` header passed through, and the two upgrade headers if you want streaming:

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_buffering off;
}
```

**Rewriting `Host` is the classic way to break this.** MCP checks the host it was asked for (DNS-rebinding
protection). The app adds whatever hostname you configure to the allow-list by itself, but a proxy that replaces
`Host` with `127.0.0.1:8765` makes `/health` pass while every MCP call answers `421`. If you run `d2l serve`
directly instead of `d2l app`, tell it the public name yourself:

```bash
D2L_PUBLIC_HOSTS=brightspace.example.com uv run d2l serve
```

Comma-separate several.

## A public IP and port forwarding

Only with a real public IP (not CGNAT) and a router you control. In the app: **My own URL / IP**, enter
`https://your.domain` or `http://203.0.113.7:8767`, and turn on **direct port** — that opens a relay on
`0.0.0.0:<port>` forwarding to the MCP server, which otherwise stays on `127.0.0.1`. Forward that port on the
router.

Plain HTTP over the internet sends the token in clear text. Put TLS in front of it, or use one of the routes above.

## Does it actually work?

The app checks every minute, from outside: it resolves the name through Cloudflare's DNS-over-HTTPS (not your
laptop's resolver, which lies about names it cached before they existed), fetches `/health`, checks the `instance`
fingerprint is this install, and sends a real MCP `ping` through the connector URL. **Check now** in the app runs
it on demand. What the answers mean:

| What you see | What it is |
|---|---|
| `reachable (900 ms)` | working |
| name doesn't resolve | DNS not published yet, or your own resolver cached the failure — a brand-new quick tunnel gets 90 seconds' grace |
| `/health` passes but MCP fails `421` | a proxy is rewriting `Host` — see above |
| stuck on "starting" for 45 s, then a token complaint | Cloudflare never accepted the tunnel: wrong or deleted token |
| `cloudflared is not installed` | install it, or use a route that doesn't need it |

By hand:

```bash
curl https://<host>/health                     # open, no token: {"ok":true,"instance":"…"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<host>/mcp    # 401 — correct, the token is missing
```

## Keeping it safe

- **The link is a password.** Give it only to the AI you meant to, and turn the public link off when you don't
  need it (**Public link → Off**).
- **Rotate if it leaks**: `uv run d2l token --rotate`, or **New token** in the app. Every existing connector link,
  n8n credential and `?token=` URL stops working, so update them afterwards.
- **Never expose 8766.** It has the buttons that log in and sync; it listens on localhost and rejects non-localhost
  `Host` headers, and no route here should point at it.
- **Least exposure wins.** If only your own devices need it, Tailscale or a VPN means nothing is published at all.
- **Your tunnel is yours.** Tokens, credentials and `.env` stay out of git — they're gitignored for that reason.
