# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public link that cloud AIs (claude.ai, ChatGPT…) use to reach the MCP server on this laptop.

Three ways, picked in the app's Connect AI tab (or `d2l public …`):

  quick        Cloudflare Quick Tunnel. No account, no setup; slower, and a new random address on every restart.
  cloudflare   Your own Cloudflare tunnel (dashboard → Zero Trust → Tunnels): fixed address on your domain. The app
               runs `cloudflared tunnel run --token …` and keeps it up.
  custom       Any URL you already route to this machine yourself: own domain + reverse proxy, a public IP with port
               forwarding, ngrok, Tailscale Funnel… Optionally opens a "direct" port on all interfaces for it.

Whatever the mode, a reachability check calls <public url>/health every minute *from outside* and verifies the
answer carries this install's fingerprint, so a dead tunnel, wrong DNS, a proxy pointing elsewhere or a router
that doesn't forward shows up in the app as a specific problem instead of an AI silently failing to connect.
"""
import asyncio
import ipaddress
import os
import re
import shutil
import time
from urllib.parse import urlparse

import httpx

from . import server
from .session import ROOT

MCP_PORT = 8765
MODES = ("off", "quick", "cloudflare", "custom")
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9](-?[a-z0-9])*\.)+[a-z]{2,}$", re.I)


class ConfigError(ValueError):
    pass


def normalise(cfg: dict, saved: dict) -> dict:
    """Validate what the page sent; blank secret fields keep the saved value. Raises ConfigError with a readable
    message for anything that would only fail later and silently."""
    for field in ("cf_host", "cf_token", "custom_url"):
        v = cfg.get(field)
        if isinstance(v, str) and not v.isprintable():
            raise ConfigError(f"{field} can't contain line breaks or control characters")
    mode = cfg.get("mode", "off")
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}")
    out = {**saved, "mode": mode}
    if mode == "cloudflare":
        host = re.sub(r"^https?://", "", (cfg.get("cf_host") or saved.get("cf_host") or "").strip()).strip("/").lower()
        if not HOSTNAME.match(host):
            raise ConfigError("Public hostname should look like brightspace.example.com (no https://, no path)")
        tok = (cfg.get("cf_token") or "").strip()
        # people often paste the whole install command; keep only the token
        tok = re.sub(r"^.*?(--token\s+|service install\s+)", "", tok).strip() or saved.get("cf_token", "")
        if not re.fullmatch(r"[A-Za-z0-9_\-=+/]{40,}", tok or ""):
            raise ConfigError("That doesn't look like a tunnel token: copy it from Cloudflare → Zero Trust → Networks → "
                              "Tunnels → your tunnel → Configure (the long string after --token)")
        out.update(cf_host=host, cf_token=tok)
    if mode == "custom":
        url = (cfg.get("custom_url") or "").strip().rstrip("/")
        url = re.sub(r"(/c/[^/]+)?/mcp$", "", url)          # accept a pasted full connector URL too
        u = urlparse(url if "://" in url else "https://" + url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConfigError("Enter the public address, e.g. https://brightspace.example.com or http://203.0.113.7:8767")
        if u.path not in ("", "/"):
            raise ConfigError("Enter only the address (scheme, host and port), without a path")
        direct = bool(cfg.get("direct"))
        port = int(cfg.get("direct_port") or 8767)
        if direct and (not (1024 <= port <= 65535) or port in (MCP_PORT, 8766)):
            raise ConfigError("Direct port must be between 1024 and 65535, and not 8765/8766")
        out.update(custom_url=f"{u.scheme}://{u.netloc}", direct=direct, direct_port=port)
    return out


class Forwarder:
    """A plain TCP relay 0.0.0.0:<port> → 127.0.0.1:8765, for IP / port-forwarding / remote reverse-proxy setups.
    The MCP server itself stays on 127.0.0.1; this is the only thing that listens on other interfaces."""

    def __init__(self):
        self.server, self.port, self.error = None, None, None

    async def start(self, port: int) -> None:
        await self.stop()
        try:
            self.server = await asyncio.start_server(self._pipe, "0.0.0.0", port)
            self.port, self.error = port, None
        except OSError as e:
            self.error = f"couldn't open port {port}: {e.strerror}"

    async def _pipe(self, reader, writer):
        try:
            up_r, up_w = await asyncio.open_connection("127.0.0.1", MCP_PORT)
        except OSError:
            writer.close()
            return

        async def copy(r, w):
            try:
                while data := await r.read(65536):
                    w.write(data)
                    await w.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                w.close()
        await asyncio.gather(copy(reader, up_w), copy(up_r, writer))

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.server, self.port = None, None


class PublicLink:
    def __init__(self, security):
        self.security = security
        self.cfg: dict = {"mode": "off"}
        self.proc = None
        self.host = None                   # public hostname[:port] once known
        self.base = None                   # public base URL once known
        self.state, self.error = "off", None
        self.health = {"ok": None, "checked": None, "detail": None}
        self.forwarder = Forwarder()
        self.since = None                  # when the link last came up
        self._gen = 0                      # bumps on every apply(); stale watchers notice and stop
        self._checker = None

    # ---- lifecycle --------------------------------------------------------------------------------------------

    async def apply(self, cfg: dict) -> None:
        await self.stop()
        self.cfg, self._gen = cfg, self._gen + 1
        mode = cfg.get("mode", "off")
        if mode == "off":
            return
        if mode in ("quick", "cloudflare") and not shutil.which("cloudflared"):
            self.state, self.error = "error", "cloudflared is not installed (Arch: sudo pacman -S cloudflared)"
            return
        if mode == "quick":
            await self._spawn(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{MCP_PORT}"])
        elif mode == "cloudflare":
            self._set_public(cfg["cf_host"], f"https://{cfg['cf_host']}")
            # token via environment, not argv: argv is readable by every process on the machine (ps)
            await self._spawn(["cloudflared", "tunnel", "--no-autoupdate", "run"], {"TUNNEL_TOKEN": cfg["cf_token"]})
            asyncio.create_task(self._registration_deadline(self._gen))
        elif mode == "custom":
            u = urlparse(cfg["custom_url"])
            self._set_public(u.netloc, cfg["custom_url"])
            if cfg.get("direct"):
                await self.forwarder.start(cfg["direct_port"])
                if self.forwarder.error:
                    self.state, self.error = "error", self.forwarder.error
                    return
            self.state, self.since = "on", time.time()
        self._checker = asyncio.create_task(self._check_loop(self._gen))

    async def stop(self) -> None:
        self._gen += 1
        if self._checker:
            self._checker.cancel()
        if self.proc:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self.proc.kill()
        await self.forwarder.stop()
        self.proc, self.host, self.base, self.state, self.error, self.since = None, None, None, "off", None, None
        self.health = {"ok": None, "checked": None, "detail": None}

    def _set_public(self, host: str, base: str) -> None:
        self.host, self.base = host, base
        server.allow_host(self.security, host)
        (ROOT / "data" / "connector-url.txt").write_text(self.url() + "\n")

    def url(self) -> str | None:
        return f"{self.base}/c/{server.token()}/mcp" if self.base else None

    # ---- cloudflared supervision -------------------------------------------------------------------------------

    async def _spawn(self, cmd: list[str], env: dict | None = None) -> None:
        self.state, self.error = "starting", None
        self.proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.PIPE, env={**os.environ, **(env or {})})
        asyncio.create_task(self._watch(self.proc, cmd, env, self._gen, time.time()))

    async def _registration_deadline(self, gen) -> None:
        """A named tunnel with a bad or deleted token never registers: cloudflared retries (sometimes exiting and
        being restarted). If no connection registered within 45 s of connecting, across any restarts, stop and say
        why instead of showing "starting" indefinitely."""
        await asyncio.sleep(45)
        if gen == self._gen and self.state != "on":
            self._fail("Cloudflare won't accept this tunnel: the token is probably wrong, or the tunnel was deleted. Copy the token again from Zero Trust → Networks → Tunnels → your tunnel → Configure.")

    def _fail(self, message: str) -> None:
        self.state, self.error = "error", message
        self.cfg["_fatal"] = True                  # tells the watcher not to respawn
        if self.proc:
            self.proc.terminate()

    async def _watch(self, proc, cmd, env, gen, started) -> None:
        last_err, serve_errors = None, 0
        async for raw in proc.stderr:
            line = raw.decode(errors="ignore")
            if " ERR " in line or "error=" in line:
                last_err = re.sub(r"^\S+\s+ERR\s+", "", line.strip())[:240]
            if gen != self._gen:
                continue
            if "Serve tunnel error" in line and self.state != "on":
                serve_errors += 1
                if serve_errors >= 6 and self.cfg["mode"] == "cloudflare":
                    self._fail("Cloudflare won't accept this tunnel: the token is probably wrong, or the tunnel was deleted. Copy the token again from Zero Trust → Networks → Tunnels → your tunnel → Configure.")
            m = re.search(r"https://([a-z0-9-]+\.trycloudflare\.com)", line)
            if m and self.cfg["mode"] == "quick" and not self.host:
                self._set_public(m.group(1), f"https://{m.group(1)}")
                await asyncio.sleep(3)             # the edge needs a moment before a new name resolves
                self.state, self.since = "on", time.time()
                asyncio.create_task(self.check())
            if "Registered tunnel connection" in line and self.cfg["mode"] == "cloudflare" and self.state != "on":
                self.state, self.since = "on", time.time()
                asyncio.create_task(self.check())
        await proc.wait()
        if gen != self._gen or self.cfg.get("_fatal"):   # stopped/replaced on purpose, or failed for good
            if self.proc is proc:
                self.proc = None
            return
        self.proc = None
        lived = time.time() - started
        fails = self.cfg.get("_fails", 0) + 1 if lived < 30 else 1
        self.cfg["_fails"] = fails
        if fails >= 3:                             # dies right away every time: a config problem, not a blip
            self.state = "error"
            self.error = ("cloudflared keeps exiting: " + (last_err or f"exit code {proc.returncode}")
                          + (" — check the tunnel token" if self.cfg["mode"] == "cloudflare" else ""))
            return
        self.state = "starting"
        self.error = "the link dropped and is reconnecting" + (" (the quick-link address will change)"
                                                                 if self.cfg["mode"] == "quick" else "")
        if self.cfg["mode"] == "quick":
            self.host = self.base = None
        await asyncio.sleep(5 * fails)
        if gen == self._gen:
            await self._spawn(cmd, env)

    # ---- reachability ------------------------------------------------------------------------------------------

    async def _check_loop(self, gen) -> None:
        await asyncio.sleep(5)
        while gen == self._gen:
            if self.base and self.state == "on":
                await self.check()
            await asyncio.sleep(60)

    async def _resolve(self, host: str) -> str | None:
        """Public DNS answer (Cloudflare DNS-over-HTTPS). Raises LookupError for a name that doesn't exist; None
        when DoH itself isn't reachable, in which case the system resolver is used."""
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://cloudflare-dns.com/dns-query", params={"name": host, "type": "A"},
                                headers={"accept": "application/dns-json"})
            j = r.json()
        except Exception:
            return None
        ips = [a["data"] for a in j.get("Answer", []) if a.get("type") == 1]
        if ips:
            return ips[0]
        raise LookupError(host)

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        u = urlparse(url)
        ip = await self._resolve(u.hostname)
        if ip and ip != u.hostname:
            target = url.replace(u.netloc, f"{ip}:{u.port}" if u.port else ip, 1)
            kw["headers"] = {**kw.get("headers", {}), "Host": u.netloc}
            if u.scheme == "https":
                kw["extensions"] = {"sni_hostname": u.hostname}   # certificate still checked against the name
            url = target
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            return await c.request(method, url, **kw)

    async def check(self) -> dict:
        """Call <public url>/health like a cloud AI would and explain what's wrong if it doesn't come back to us."""
        if not self.base:
            self.health = {"ok": None, "checked": time.time(), "detail": "no public address yet"}
            return self.health
        ok, detail = False, None
        try:
            r = await self._request("GET", self.base + "/health")
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json") \
                    and r.json().get("instance") == server.instance_id():
                # /health skips host checks; make sure a real MCP call through the connector URL gets through too
                m = await self._request("POST", self.url(), json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                                        headers={"Accept": "application/json, text/event-stream"})
                if m.status_code == 200 and '"result"' in m.text:
                    ok, detail = True, f"reachable ({r.elapsed.total_seconds() * 1000:.0f} ms)"
                elif m.status_code == 421:
                    detail = ("reaches this app, but the proxy changes the Host header so MCP calls are refused — "
                              "make the proxy pass the original host (e.g. Caddy/nginx: preserve Host), or add the "
                              "host it sends to D2L_PUBLIC_HOSTS in .env")
                else:
                    detail = f"health works but an MCP call got HTTP {m.status_code} — the proxy may block POST or streaming"
            elif r.status_code == 200:
                detail = "something answers at that address, but it isn't this Brightspace app — check where it points"
            elif r.status_code in (502, 503, 504, 530):
                detail = (f"HTTP {r.status_code}: the tunnel/proxy is up but can't reach this laptop — "
                          f"point it at http://localhost:{MCP_PORT}")
            elif r.status_code in (301, 302, 307, 308):
                detail = f"redirects to {r.headers.get('location', '?')} — use that address instead"
            elif r.status_code == 404:
                detail = f"HTTP 404: the address works but doesn't route to this app (service should be http://localhost:{MCP_PORT})"
            else:
                detail = f"HTTP {r.status_code} from the public address"
        except LookupError:
            detail = "the domain doesn't resolve (DNS) — check the DNS record, or wait a minute after creating it"
        except httpx.ConnectError as e:
            msg = str(e).lower()
            if "name or service not known" in msg or "nodename" in msg or "getaddrinfo" in msg or "resolution" in msg:
                detail = "the domain doesn't resolve (DNS) — check the DNS record, or wait a minute after creating it"
            elif "refused" in msg:
                detail = "connection refused — nothing is listening there, or the router doesn't forward that port"
            elif "certificate" in msg or "ssl" in msg or "tls" in msg:
                detail = "TLS/certificate problem — cloud AIs need a valid https certificate"
            else:
                detail = f"can't connect: {e}"[:200]
        except httpx.TimeoutException:
            detail = ("timed out — a firewall or router is dropping it (checked from this laptop: some routers can't "
                      "loop back to their own public IP, so also try from your phone)")
        except Exception as e:
            detail = f"{e.__class__.__name__}: {e}"[:200]
        if ok and self.cfg.get("mode") == "custom" and self.base.startswith("http://"):
            detail += " — but it's plain http: claude.ai and ChatGPT require https"
        new_address = self.cfg.get("mode") == "quick" or (
            self.cfg.get("mode") == "cloudflare" and detail and ("DNS" in detail or "530" in detail or "502" in detail))
        if not ok and new_address and self.since and time.time() - self.since < 90:
            self.health = {"ok": None, "checked": time.time(), "detail": "the new link is going live…"}
            gen = self._gen
            async def again():
                await asyncio.sleep(10)
                if gen == self._gen:
                    await self.check()
            asyncio.create_task(again())
            return self.health
        self.health = {"ok": ok, "checked": time.time(), "detail": detail}
        return self.health

    # ---- what the page shows -----------------------------------------------------------------------------------

    def view(self) -> dict:
        c = self.cfg
        return {"mode": c.get("mode", "off"), "state": self.state, "error": self.error, "health": self.health,
                "url": self.url() if self.state == "on" else None,
                "cloudflared": bool(shutil.which("cloudflared")),
                "direct": {"on": bool(self.forwarder.port), "port": self.forwarder.port},
                "config": {"cf_host": c.get("cf_host", ""), "has_cf_token": bool(c.get("cf_token")),
                           "custom_url": c.get("custom_url", ""), "direct": bool(c.get("direct")),
                           "direct_port": c.get("direct_port", 8767)}}
