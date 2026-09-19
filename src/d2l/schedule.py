# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run sync on a timer, and optionally keep `d2l app` running — with whatever the OS provides.

    d2l schedule install [--times 08:00,14:00,20:00] [--serve]   (--serve: always-on app + a launcher entry)
    d2l schedule status
    d2l schedule remove

    Linux    systemd *user* units (no root; desktop notifications work through your session's D-Bus)
    macOS    launchd agents in ~/Library/LaunchAgents
    Windows  Task Scheduler tasks for your user (sync at the times given; the app at logon)
"""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .session import ROOT

UV = shutil.which("uv") or "uv"
SYNC = [UV, "run", "--directory", str(ROOT), "d2l", "sync"]
APP = [UV, "run", "--directory", str(ROOT), "d2l", "app"]


def _times(times: str) -> list[tuple[int, int]]:
    out = []
    for t in times.split(","):
        h, _, m = t.strip().partition(":")
        if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
            raise SystemExit(f"bad time {t!r}: use HH:MM, e.g. 08:00,14:00,20:00")
        out.append((int(h), int(m)))
    return out


def _run(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit((r.stderr or r.stdout).strip())
    return r.stdout


def install(times: str = "08:00,14:00,20:00", serve: bool = False) -> None:
    slots = _times(times)
    {"linux": _linux_install, "darwin": _mac_install, "win32": _win_install}.get(sys.platform, _unsupported)(slots, serve)
    status()


def status() -> None:
    {"linux": _linux_status, "darwin": _mac_status, "win32": _win_status}.get(sys.platform, lambda: None)()


def remove() -> None:
    {"linux": _linux_remove, "darwin": _mac_remove, "win32": _win_remove}.get(sys.platform, lambda: None)()
    print("removed the d2l schedule")


def _unsupported(*_):
    raise SystemExit(f"no scheduler support for {sys.platform}; run `d2l sync` from cron or similar 3× a day")


# --- Linux: systemd user units ----------------------------------------------------------------------------------

UNITS = Path.home() / ".config" / "systemd" / "user"
LAUNCHER = Path.home() / ".local" / "share" / "applications" / "brightspace.desktop"


def _exec(cmd: list[str]) -> str:
    """A systemd ExecStart line; double quotes keep paths with spaces in one argument."""
    return " ".join(f'"{a}"' if any(c in a for c in ' "\\') else a for a in cmd)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}")


def _linux_install(slots, serve):
    _write(UNITS / "d2l-sync.service", f"""[Unit]
Description=Brightspace sync (d2l-brightspace)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={_exec(SYNC)}
Nice=10
""")
    calendar = "\n".join(f"OnCalendar=*-*-* {h:02d}:{m:02d}:00" for h, m in slots)
    _write(UNITS / "d2l-sync.timer", f"""[Unit]
Description=Brightspace sync

[Timer]
{calendar}
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
""")
    units = ["d2l-sync.timer"]
    if serve:
        _write(UNITS / "d2l-app.service", f"""[Unit]
Description=Brightspace app: dashboard :8766, MCP + REST :8765 (d2l-brightspace)
After=network-online.target

[Service]
WorkingDirectory={ROOT}
ExecStart={_exec(APP)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
        units.append("d2l-app.service")
        _write(LAUNCHER, """[Desktop Entry]
Type=Application
Name=Brightspace
Comment=Courses, deadlines and AI connections
Exec=xdg-open http://127.0.0.1:8766
Icon=accessories-dictionary
Terminal=false
Categories=Education;
""")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", *units])
    print("enabled:", ", ".join(units))


def _linux_status():
    print(_run(["systemctl", "--user", "list-timers", "d2l-sync.timer", "--no-pager"], check=False).strip()
          or "no d2l timer installed")
    for u in ("d2l-sync.service", "d2l-app.service"):
        print(f"{u}: {_run(['systemctl', '--user', 'is-active', u], check=False).strip()}")
    print("logs: journalctl --user -u d2l-sync -n 50")


def _linux_remove():
    for u in ("d2l-sync.timer", "d2l-app.service"):
        _run(["systemctl", "--user", "disable", "--now", u], check=False)
    for f in ("d2l-sync.service", "d2l-sync.timer", "d2l-app.service"):
        (UNITS / f).unlink(missing_ok=True)
    LAUNCHER.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)


# --- macOS: launchd agents --------------------------------------------------------------------------------------

AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGS = ROOT / "data" / "logs"


def _plist(label: str, body: dict) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = AGENTS / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump({"Label": label, "WorkingDirectory": str(ROOT),
                       "StandardOutPath": str(LOGS / f"{label}.log"), "StandardErrorPath": str(LOGS / f"{label}.log"),
                       **body}, f)
    print(f"wrote {path}")
    return path


def _mac_install(slots, serve):
    uid = os.getuid()
    agents = [_plist("com.d2l-brightspace.sync", {"ProgramArguments": SYNC,
                                                   "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in slots]})]
    if serve:
        agents.append(_plist("com.d2l-brightspace.app", {"ProgramArguments": APP, "RunAtLoad": True, "KeepAlive": True}))
    for p in agents:
        _run(["launchctl", "bootout", f"gui/{uid}", str(p)], check=False)
        _run(["launchctl", "bootstrap", f"gui/{uid}", str(p)])
    print("loaded:", ", ".join(p.stem for p in agents), "| app: http://127.0.0.1:8766" if serve else "")


def _mac_status():
    out = _run(["launchctl", "list"], check=False)
    for label in ("com.d2l-brightspace.sync", "com.d2l-brightspace.app"):
        print(f"{label}: {'loaded' if label in out else 'not loaded'}")
    print(f"logs: {LOGS}")


def _mac_remove():
    for label in ("com.d2l-brightspace.sync", "com.d2l-brightspace.app"):
        p = AGENTS / f"{label}.plist"
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], check=False)
        p.unlink(missing_ok=True)


# --- Windows: Task Scheduler ------------------------------------------------------------------------------------

def _cmdline(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd)


def _win_install(slots, serve):
    _win_remove()
    for h, m in slots:
        _run(["schtasks", "/Create", "/F", "/TN", f"d2l-brightspace\\sync {h:02d}{m:02d}", "/SC", "DAILY",
              "/ST", f"{h:02d}:{m:02d}", "/TR", _cmdline(SYNC)])
    if serve:
        _run(["schtasks", "/Create", "/F", "/TN", "d2l-brightspace\\app", "/SC", "ONLOGON", "/TR", _cmdline(APP)])
        _run(["schtasks", "/Run", "/TN", "d2l-brightspace\\app"], check=False)
    print("created Task Scheduler tasks under \\d2l-brightspace", "| app: http://127.0.0.1:8766" if serve else "")


def _win_tasks() -> list[str]:
    out = _run(["schtasks", "/Query", "/FO", "CSV", "/NH"], check=False)
    return [line.split(",")[0].strip('"') for line in out.splitlines() if "d2l-brightspace" in line]


def _win_status():
    tasks = _win_tasks()
    print("\n".join(tasks) if tasks else "no d2l tasks")


def _win_remove():
    for t in _win_tasks():
        _run(["schtasks", "/Delete", "/F", "/TN", t], check=False)
