"""Send queued events to every configured channel, then mark them sent.

Channels (all optional, set in .env):
  desktop   notify-send — on by default when available; D2L_NOTIFY_DESKTOP=0 turns it off
  telegram  D2L_TELEGRAM_TOKEN + D2L_TELEGRAM_CHAT
  discord   D2L_DISCORD_WEBHOOK
  webhook   D2L_WEBHOOK_URL — POSTs {"source": "d2l", "events": [...]} as JSON. Point it at n8n, a custom app,
            or anything that forwards to WhatsApp/email/Notion.
"""
import os
import shutil
import subprocess

import httpx

from . import store

ICON = {"new_announcement": "📢", "new_grade": "🎯", "grade_changed": "🎯", "new_assignment": "📝",
        "due_changed": "📝", "new_quiz": "⏱", "new_files": "📄", "due_soon": "⏰", "session_expired": "🔑",
        "test": "✅"}


def channels() -> list[str]:
    out = []
    if os.environ.get("D2L_NOTIFY_DESKTOP", "1") != "0" and shutil.which("notify-send"):
        out.append("desktop")
    if os.environ.get("D2L_TELEGRAM_TOKEN") and os.environ.get("D2L_TELEGRAM_CHAT"):
        out.append("telegram")
    if os.environ.get("D2L_DISCORD_WEBHOOK"):
        out.append("discord")
    if os.environ.get("D2L_WEBHOOK_URL"):
        out.append("webhook")
    return out


def _line(e: dict) -> str:
    course = f"[{e['course']}] " if e.get("course") else ""
    return f"{ICON.get(e['kind'], '•')} {course}{e['summary']}"


def send(events: list[dict]) -> dict[str, str]:
    """Deliver a batch; returns {channel: "ok" | error}. One failing channel doesn't stop the others."""
    if not events:
        return {}
    result = {}
    text = "\n".join(_line(e) for e in events[:30]) + (f"\n…and {len(events) - 30} more" if len(events) > 30 else "")
    for ch in channels():
        try:
            if ch == "desktop":
                if len(events) <= 4:
                    for e in events:
                        subprocess.run(["notify-send", "-a", "Brightspace", "-u",
                                        "critical" if e["kind"] in ("due_soon", "session_expired") else "normal",
                                        _line(e), (e.get("detail") or "")[:300]], check=True, timeout=10)
                else:
                    subprocess.run(["notify-send", "-a", "Brightspace", f"Brightspace: {len(events)} updates", text[:900]],
                                   check=True, timeout=10)
            elif ch == "telegram":
                r = httpx.post(f"https://api.telegram.org/bot{os.environ['D2L_TELEGRAM_TOKEN']}/sendMessage",
                               json={"chat_id": os.environ["D2L_TELEGRAM_CHAT"], "text": text[:4000],
                                     "disable_web_page_preview": True}, timeout=20)
                r.raise_for_status()
            elif ch == "discord":
                r = httpx.post(os.environ["D2L_DISCORD_WEBHOOK"], json={"content": text[:1900]}, timeout=20)
                r.raise_for_status()
            elif ch == "webhook":
                r = httpx.post(os.environ["D2L_WEBHOOK_URL"], json={"source": "d2l", "events": events}, timeout=20)
                r.raise_for_status()
            result[ch] = "ok"
        except Exception as ex:
            result[ch] = f"{ex.__class__.__name__}: {str(ex)[:160]}"
    return result


def flush(db) -> dict[str, str]:
    """Send every unsent event. Marked sent if any channel took it (or none is configured), so nothing piles up."""
    pending = store.rows(db, "SELECT e.id, e.ts, e.kind, e.course_id, e.summary, e.detail, e.url, "
                             "coalesce(c.short, '') AS course FROM events e LEFT JOIN courses c ON c.id = e.course_id "
                             "WHERE sent = 0 ORDER BY e.id")
    if not pending:
        return {}
    result = send(pending)
    if not result or any(v == "ok" for v in result.values()):
        db.executemany("UPDATE events SET sent = 1 WHERE id = ?", [(e["id"],) for e in pending])
    return result
