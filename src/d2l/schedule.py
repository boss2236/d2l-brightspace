"""systemd *user* units: sync three times a day, and optionally keep `d2l app` running.

    d2l schedule install [--times 08:00,14:00,20:00] [--serve]   (--serve: always-on app + a "Brightspace" launcher)
    d2l schedule status
    d2l schedule remove

User units run as you, with your desktop session's D-Bus, so desktop notifications work; nothing needs root.
"""
import shutil
import subprocess
from pathlib import Path

from .session import ROOT

UNITS = Path.home() / ".config" / "systemd" / "user"
LAUNCHER = Path.home() / ".local" / "share" / "applications" / "brightspace.desktop"
UV = shutil.which("uv") or "uv"


def _write(name: str, text: str) -> None:
    UNITS.mkdir(parents=True, exist_ok=True)
    (UNITS / name).write_text(text)
    print(f"wrote {UNITS / name}")


def _ctl(*args: str, check: bool = True) -> str:
    r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(r.stderr.strip())
    return r.stdout


def install(times: str = "08:00,14:00,20:00", serve: bool = False) -> None:
    on_calendar = "\n".join(f"OnCalendar=*-*-* {t.strip()}:00" for t in times.split(",") if t.strip())
    _write("d2l-sync.service", f"""[Unit]
Description=Brightspace sync (d2l-brightspace)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={UV} run --directory {ROOT} d2l sync
Nice=10
""")
    _write("d2l-sync.timer", f"""[Unit]
Description=Brightspace sync, {times}

[Timer]
{on_calendar}
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
""")
    units = ["d2l-sync.timer"]
    if serve:
        _write("d2l-app.service", f"""[Unit]
Description=Brightspace app: dashboard :8766, MCP + REST :8765 (d2l-brightspace)
After=network-online.target

[Service]
WorkingDirectory={ROOT}
ExecStart={UV} run --directory {ROOT} d2l app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
        units.append("d2l-app.service")
        LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHER.write_text("""[Desktop Entry]
Type=Application
Name=Brightspace
Comment=Courses, deadlines and AI connections
Exec=xdg-open http://127.0.0.1:8766
Icon=accessories-dictionary
Terminal=false
Categories=Education;
""")
        print(f"wrote {LAUNCHER} (search 'Brightspace' in your app launcher)")
    _ctl("daemon-reload")
    _ctl("enable", "--now", *units)
    print("enabled:", ", ".join(units))
    status()


def status() -> None:
    print(_ctl("list-timers", "d2l-sync.timer", "--no-pager", check=False).strip() or "no d2l timer installed")
    for u in ("d2l-sync.service", "d2l-app.service"):
        state = _ctl("is-active", u, check=False).strip()
        print(f"{u}: {state}")
    print("logs: journalctl --user -u d2l-sync -n 50")


def remove() -> None:
    for u in ("d2l-sync.timer", "d2l-app.service"):
        _ctl("disable", "--now", u, check=False)
    for f in ("d2l-sync.service", "d2l-sync.timer", "d2l-app.service"):
        (UNITS / f).unlink(missing_ok=True)
    LAUNCHER.unlink(missing_ok=True)
    _ctl("daemon-reload")
    print("removed the d2l timer and services")
