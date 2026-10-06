# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI apps on this computer that can use the Brightspace MCP server: find them, connect them, disconnect them.

    d2l connect                    which AI apps are here and which already have Brightspace
    d2l connect codex hermes       add it to those (or `all` for every installed one)
    d2l disconnect --all           take it out of every app it was added to

Apps with their own `mcp add/remove` command get it through that command; for the others the config file is edited
directly, after a one-time copy to `<file>.bak-d2l`. Every app gets the same launch command: this install's own
Python running `-m d2l mcp`, so it works whether or not `uv` is on the app's PATH.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .session import ROOT

NAME = "brightspace"


def python() -> str:
    """This install's virtualenv Python (stable across `uv sync`), else whatever is running now."""
    venv = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def command(*args: str) -> list[str]:
    """`d2l <args>` as an absolute command line, for MCP configs and schedulers."""
    return [python(), "-m", "d2l", *args]


def _app_config(linux: str, mac: str, windows: str) -> Path:
    path = {"darwin": mac, "win32": windows}.get(sys.platform, linux)
    return Path(os.path.expandvars(path)).expanduser()


# --- JSON config files (VS Code, Claude Desktop) ----------------------------------------------------------------

def _backup(p: Path, raw: str) -> None:
    """Keep the file exactly as it was before our first edit (raw, untrimmed)."""
    bak = p.with_name(p.name + ".bak-d2l")
    if raw.strip() and not bak.exists():
        bak.write_text(raw)


def _json_read(p: Path) -> dict:
    try:
        return json.loads(p.read_text() or "{}")
    except (OSError, ValueError):
        return {}


def _json_has(p: Path, section: str) -> bool:
    d = _json_read(p).get(section)
    return isinstance(d, dict) and NAME in d


def _json_add(p: Path, section: str, entry: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = p.read_text() if p.exists() else ""
    _backup(p, raw)
    d = json.loads(raw) if raw.strip() else {}
    d.setdefault(section, {})[NAME] = entry
    p.write_text(json.dumps(d, indent=2))


def _json_remove(p: Path, section: str) -> None:
    if not _json_has(p, section):
        return
    raw = p.read_text()
    _backup(p, raw)
    d = json.loads(raw)
    d[section].pop(NAME, None)
    p.write_text(json.dumps(d, indent=2))
    _restore_if_same(p, lambda text: json.loads(text) == d)


def _restore_if_same(p: Path, same) -> None:
    """Nothing but our entry changed since the backup: put the original bytes back and drop the backup."""
    bak = p.with_name(p.name + ".bak-d2l")
    try:
        if bak.exists() and same(bak.read_text()):
            p.write_text(bak.read_text())
            bak.unlink()
    except (OSError, ValueError):
        pass


# --- Hermes Agent: mcp_servers in ~/.hermes/config.yaml -----------------------------------------------------------
# `hermes mcp add` connects and asks which tools to enable, so it can't run unattended; the entry it would write is
# a plain block (command + args) and is written here in the same shape. JSON strings are valid YAML scalars.

def _hermes_config() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "config.yaml"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _hermes_block(lines: list[str]) -> tuple[int, int, int] | None:
    """(header, start, end) of our entry inside `mcp_servers:`; start == -1 when the section has no entry of ours."""
    head = next((i for i, l in enumerate(lines) if re.match(r"mcp_servers:\s*(\{\}\s*)?(#.*)?$", l)), None)
    if head is None:
        return None
    for i in range(head + 1, len(lines)):
        line = lines[i]
        if line.strip() and _indent(line) == 0:
            break
        if line.strip() and re.match(rf"\s+{NAME}:\s*$", line):
            depth, end = _indent(line), i + 1
            while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > depth):
                end += 1
            while end > i + 1 and not lines[end - 1].strip():          # leave trailing blank lines in place
                end -= 1
            return head, i, end
    return head, -1, -1


def _hermes_has() -> bool:
    p = _hermes_config()
    found = _hermes_block(p.read_text().splitlines()) if p.exists() else None
    return bool(found and found[1] >= 0)


def _hermes_remove() -> None:
    p = _hermes_config()
    if not _hermes_has():
        return
    raw = p.read_text()
    _backup(p, raw)
    lines = raw.splitlines()
    head, start, end = _hermes_block(lines)
    del lines[start:end]
    if not any(l.strip() and _indent(l) > 0 for l in lines[head + 1:_next_top(lines, head)]):
        del lines[head]                                                  # the section held only our entry
        if head == len(lines) and head and not lines[head - 1].strip():
            del lines[head - 1]                                          # and the blank line _hermes_add put before it
    p.write_text("\n".join(lines) + "\n")
    _restore_if_same(p, lambda text: text.strip() == p.read_text().strip())


def _next_top(lines: list[str], head: int) -> int:
    return next((i for i in range(head + 1, len(lines)) if lines[i].strip() and _indent(lines[i]) == 0), len(lines))


def _hermes_add() -> None:
    _hermes_remove()                                                     # re-adding replaces a stale command
    p = _hermes_config()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = p.read_text() if p.exists() else ""
    _backup(p, raw)
    cmd = command("mcp")
    entry = [f"  {NAME}:", f"    command: {json.dumps(cmd[0])}", "    args:", *[f"    - {json.dumps(a)}" for a in cmd[1:]],
             "    enabled: true"]
    lines = raw.splitlines()
    found = _hermes_block(lines)
    if found is None:
        lines += ([""] if lines and lines[-1].strip() else []) + ["mcp_servers:", *entry]
    else:
        lines[found[0]] = "mcp_servers:"                                 # `mcp_servers: {}` becomes a block
        lines[found[0] + 1:found[0] + 1] = entry
    p.write_text("\n".join(lines) + "\n")


# --- the registry -------------------------------------------------------------------------------------------------

def _mentions_here(p: Path) -> bool:
    """Does this config file refer to this install's folder? (Another copy may have connected the same app.)"""
    try:
        text = p.read_text()
    except OSError:
        return False
    return str(ROOT) in text or json.dumps(str(ROOT))[1:-1] in text


def _codex_has() -> bool:
    p = Path("~/.codex/config.toml").expanduser()
    return p.exists() and f"[mcp_servers.{NAME}]" in p.read_text()


VSCODE = lambda: _app_config("~/.config/Code/User/mcp.json", "~/Library/Application Support/Code/User/mcp.json",
                             "%APPDATA%/Code/User/mcp.json")
CLAUDE_DESKTOP = lambda: _app_config("~/.config/Claude/claude_desktop_config.json",
                                     "~/Library/Application Support/Claude/claude_desktop_config.json",
                                     "%APPDATA%/Claude/claude_desktop_config.json")

CLIENTS = {
    "claude-code": {
        "name": "Claude Code", "installed": lambda: bool(shutil.which("claude")),
        "connected": lambda: _json_has(Path("~/.claude.json").expanduser(), "mcpServers"),
        "add": lambda: ["claude", "mcp", "add", "-s", "user", NAME, "--", *command("mcp")],
        "remove": lambda: ["claude", "mcp", "remove", "-s", "user", NAME],
        "file": lambda: Path("~/.claude.json").expanduser(),
        "how": "Ask in any Claude Code session, e.g. “what’s due this week?”"},
    "codex": {
        "name": "Codex CLI", "installed": lambda: bool(shutil.which("codex")),
        "connected": _codex_has,
        "add": lambda: ["codex", "mcp", "add", NAME, "--", *command("mcp")],
        "remove": lambda: ["codex", "mcp", "remove", NAME],
        "file": lambda: Path("~/.codex/config.toml").expanduser(),
        "how": "Start codex in a terminal and ask."},
    "hermes": {
        "name": "Hermes Agent", "installed": lambda: bool(shutil.which("hermes")) or _hermes_config().exists(),
        "connected": _hermes_has, "add": _hermes_add, "remove": _hermes_remove, "file": _hermes_config,
        "how": "Start hermes and ask; `hermes mcp list` shows it. A running Hermes picks it up after a restart."},
    "gemini": {
        "name": "Gemini CLI", "installed": lambda: bool(shutil.which("gemini")),
        "connected": lambda: _json_has(Path("~/.gemini/settings.json").expanduser(), "mcpServers"),
        "add": lambda: ["gemini", "mcp", "add", "-s", "user", NAME, command("mcp")[0], "--", *command("mcp")[1:]],
        "remove": lambda: ["gemini", "mcp", "remove", "-s", "user", NAME],
        "file": lambda: Path("~/.gemini/settings.json").expanduser(),
        "how": "Start gemini in a terminal and ask; /mcp lists the tools."},
    "vscode": {
        "name": "VS Code (Copilot)", "installed": lambda: bool(shutil.which("code")),
        "connected": lambda: _json_has(VSCODE(), "servers"),
        "add": lambda: _json_add(VSCODE(), "servers", {"type": "stdio", "command": command("mcp")[0],
                                                        "args": command("mcp")[1:]}),
        "remove": lambda: _json_remove(VSCODE(), "servers"), "file": VSCODE,
        "how": "Copilot Chat → Agent mode → tools picker. VS Code may ask once to trust the server."},
    "claude-desktop": {
        "name": "Claude Desktop", "installed": lambda: CLAUDE_DESKTOP().parent.exists(),
        "connected": lambda: _json_has(CLAUDE_DESKTOP(), "mcpServers"),
        "add": lambda: _json_add(CLAUDE_DESKTOP(), "mcpServers", {"command": command("mcp")[0],
                                                                  "args": command("mcp")[1:]}),
        "remove": lambda: _json_remove(CLAUDE_DESKTOP(), "mcpServers"), "file": CLAUDE_DESKTOP,
        "how": "Restart Claude Desktop, then turn Brightspace on in the chat’s tools menu."},
}


def _run(action) -> str | None:
    """Run an add/remove action: a CLI command (list) or a function. Returns an error message, or None."""
    if not isinstance(action, list):
        return None
    try:
        r = subprocess.run(action, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return (r.stdout + r.stderr).strip()[-300:] or f"exit {r.returncode}" if r.returncode else None


def connect(key: str) -> dict:
    c = CLIENTS[key]
    try:
        if c["connected"]():                                             # replace, so the command is current
            _run(c["remove"]())
        err = _run(c["add"]())
    except (OSError, ValueError) as e:
        err = str(e)
    ok = c["connected"]()
    return {"key": key, "ok": ok, "message": c["how"] if ok else (err or "not added")}


def disconnect(key: str) -> dict:
    c = CLIENTS[key]
    try:
        err = _run(c["remove"]()) if c["connected"]() else None
    except (OSError, ValueError) as e:
        err = str(e)
    ok = not c["connected"]()
    return {"key": key, "ok": ok, "message": "removed" if ok else (err or "still connected")}


def mine(key: str) -> bool:
    """Connected, and to this install (not to another copy of d2l-brightspace)."""
    c = CLIENTS[key]
    return c["connected"]() and _mentions_here(c["file"]())


def view() -> list[dict]:
    return [{"key": k, "name": c["name"], "installed": c["installed"](), "connected": c["connected"](), "mine": mine(k),
             "how": c["how"]} for k, c in CLIENTS.items()]
