# SPDX-License-Identifier: AGPL-3.0-or-later
"""http://d2l.localhost: type that in a browser and land on the Brightspace app, whatever port it is on.

Browsers send every *.localhost name to this computer, and a URL without a port means port 80. This is a tiny
redirect server on port 80: each request reads the app's current port from .env and answers with a redirect to
http://d2l.localhost:<port>/…, so it keeps working when the app moves to another port. It serves nothing else and
reads nothing but .env.

    d2l web             is it on? (same as `d2l web status`)
    d2l web run         run it in the foreground (what the service runs)
    d2l web install     start it at boot. Linux: a system service running as you, allowed only to use port 80
                        (asks for sudo once). macOS: a launchd agent (macOS lets users use port 80).
    d2l web remove      undo that
    d2l web status
"""
import getpass
import plistlib
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ports
from .clients import command
from .schedule import _exec
from .session import ROOT

UNIT = Path("/etc/systemd/system/d2l-web.service")
AGENT = Path.home() / "Library" / "LaunchAgents" / "com.d2l-brightspace.web.plist"
NAMES = {ports.HOST, "localhost", "127.0.0.1", "[::1]"}


def _ui_port() -> int:
    """The app's port as currently saved, read fresh so a changed port is followed without a restart."""
    try:
        for line in (ROOT / ".env").read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "D2L_UI_PORT" and value.strip().isdigit():
                return int(value.strip())
    except OSError:
        pass
    return ports.DEFAULTS["D2L_UI_PORT"]


class Handler(BaseHTTPRequestHandler):
    server_version = "d2l-web"

    def _go(self):
        h = self.headers.get("Host") or ""
        host = h.split("]")[0] + "]" if h.startswith("[") else h.rsplit(":", 1)[0]
        if host not in NAMES:
            self.send_error(404)
            return
        path = self.path if self.path.startswith("/") else "/"
        self.send_response(302)
        self.send_header("Location", f"http://{ports.HOST}:{_ui_port()}{path}")
        self.send_header("X-D2L", "1")
        self.send_header("Cache-Control", "no-store")          # the port can change; never cache the redirect
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD = _go

    def log_message(self, *args):
        pass


def run() -> None:
    port = ports.web()
    try:
        servers = [ThreadingHTTPServer(("127.0.0.1", port), Handler)]
    except PermissionError:
        raise SystemExit(f"port {port} needs permission: run `d2l web install` to set up http://{ports.HOST} as a service")
    except OSError as e:
        raise SystemExit(f"port {port} isn't available ({e.strerror}); the app is at {ports.app_url()}")
    try:                                                         # browsers may try ::1 first for *.localhost
        class V6(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        servers.append(V6(("::1", port), Handler))
    except OSError:
        pass
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"http://{ports.HOST} → the Brightspace app (port {_ui_port()})", flush=True)
    servers[0].serve_forever()


def port_taken_by_other() -> bool:
    """Something other than this forwarder already answers on the web port (another web server, Docker…)."""
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", ports.web())) != 0:
            return False
    return not ports.forwarder_running()


def _sudo(*cmd: str, input: str | None = None) -> None:
    r = subprocess.run(["sudo", *cmd], input=input, text=True)
    if r.returncode:
        raise SystemExit(f"failed: sudo {' '.join(cmd)}")


def install() -> None:
    if ports.forwarder_running():
        print(f"already running: http://{ports.HOST}")
        return
    if port_taken_by_other():
        raise SystemExit(f"port {ports.web()} is used by another program; use {ports.app_url()} instead")
    if sys.platform == "linux":
        unit = f"""[Unit]
Description=http://{ports.HOST} -> the Brightspace app (d2l-brightspace)
After=network.target

[Service]
User={getpass.getuser()}
ExecStart={_exec(command('web', 'run'))}
Environment=D2L_WEB_PORT={ports.web()}
Restart=on-failure
RestartSec=3
# allowed to use port {ports.web()} and nothing more
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
        print(f"Setting up http://{ports.HOST} needs administrator rights once (a service that may only use port 80).")
        _sudo("tee", str(UNIT), input=unit)
        _sudo("systemctl", "daemon-reload")
        _sudo("systemctl", "enable", "--now", UNIT.name)
    elif sys.platform == "darwin":
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        with AGENT.open("wb") as f:
            plistlib.dump({"Label": AGENT.stem, "ProgramArguments": command("web", "run"), "RunAtLoad": True,
                           "KeepAlive": True, "WorkingDirectory": str(ROOT)}, f)
        subprocess.run(["launchctl", "unload", str(AGENT)], capture_output=True)
        subprocess.run(["launchctl", "load", str(AGENT)], check=True)
    else:
        raise SystemExit(f"not supported on {sys.platform}; use {ports.app_url()}")
    for _ in range(20):
        if ports.forwarder_running():
            print(f"ready: http://{ports.HOST}")
            return
        time.sleep(0.5)
    raise SystemExit(f"the forwarder didn't start; check: {'journalctl -u d2l-web' if sys.platform == 'linux' else AGENT}")


def remove() -> None:
    if sys.platform == "linux" and UNIT.exists():
        _sudo("systemctl", "disable", "--now", UNIT.name)
        _sudo("rm", "-f", str(UNIT))
        _sudo("systemctl", "daemon-reload")
    elif sys.platform == "darwin" and AGENT.exists():
        subprocess.run(["launchctl", "unload", str(AGENT)], capture_output=True)
        AGENT.unlink()
    print(f"removed the http://{ports.HOST} forwarder")


def status() -> None:
    print(f"http://{ports.HOST}: {'on' if ports.forwarder_running() else 'off'} · app port {ports.ui()} · "
          f"MCP port {ports.mcp()}")
