# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local ports this install listens on, chosen once and kept in .env so every part agrees.

    D2L_MCP_PORT   MCP + REST for AI apps (the only port a public link ever reaches)
    D2L_UI_PORT    the Brightspace app: dashboard and Connect AI (this computer only)
    D2L_WEB_PORT   the tiny forwarder behind http://d2l.localhost (80, so no port has to be typed)

The installer picks random free ports (`d2l ports --assign`). If one is later taken by another program, the app
picks a new free one when it starts and saves it, so the dashboard link, the MCP address, the public link and the
d2l.localhost forwarder all follow. AI apps connected over stdio don't use a port at all.
Installs from before this keep 8765/8766 until something else takes them.
"""
import json
import os
import random
import socket
import urllib.error
import urllib.request

DEFAULTS = {"D2L_MCP_PORT": 8765, "D2L_UI_PORT": 8766}
HOST = "d2l.localhost"           # browsers send every *.localhost name to this computer
RANGE = (20000, 60999)


def mcp() -> int:
    return _get("D2L_MCP_PORT")


def ui() -> int:
    return _get("D2L_UI_PORT")


def web() -> int:
    return int(os.environ.get("D2L_WEB_PORT") or 80)


def _get(key: str) -> int:
    try:
        return int(os.environ.get(key) or DEFAULTS[key])
    except ValueError:
        return DEFAULTS[key]


def app_url(path: str = "/") -> str:
    """The address to show people: http://d2l.localhost when the forwarder runs, else d2l.localhost:<port>."""
    if forwarder_running():
        return f"http://{HOST}{path}" if web() == 80 else f"http://{HOST}:{web()}{path}"
    return f"http://{HOST}:{ui()}{path}"


def is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def ours_running() -> bool:
    """Is this install's app already listening? (/health answers with this install's instance id.)"""
    from . import server
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{mcp()}/health", timeout=2) as r:
            return json.load(r).get("instance") == server.instance_id()
    except (OSError, ValueError):
        return False


def forwarder_running() -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{web()}/", headers={"Host": HOST})
        opener = urllib.request.build_opener(_NoRedirect)
        opener.open(req, timeout=1)
    except urllib.error.HTTPError as e:
        return e.code in (301, 302, 307, 308) and e.headers.get("X-D2L") == "1"
    except OSError:
        return False
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def pick(taken: set[int]) -> int:
    for _ in range(500):
        p = random.randint(*RANGE)
        if p not in taken and is_free(p):
            return p
    raise SystemExit("couldn't find a free port")


def assign(new: bool = False) -> list[str]:
    """Make sure both ports are set and free; `new` picks fresh random ones. Returns what changed, for printing.

    Call only when this install's app is not running (its own ports are busy then, which is fine).
    """
    from .server import _set_env
    changed, taken = [], set()
    for key in ("D2L_MCP_PORT", "D2L_UI_PORT"):
        current = os.environ.get(key)
        port = _get(key)
        if new and not current or not is_free(port) or port in taken:
            old, port = port, pick(taken | {web()})
            _set_env(key, str(port))
            changed.append(f"{key}: {port}" + (f" ({old} was in use)" if current and not new else ""))
        elif not current:
            _set_env(key, str(port))
        taken.add(port)
    return changed
