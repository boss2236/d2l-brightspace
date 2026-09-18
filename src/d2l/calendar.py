"""Deadlines from the Brightspace iCal feed — the no-automation route.

Brightspace publishes a personal calendar feed (Calendar → Subscribe). It needs no login automation, does not
break on UI changes, and is the sanest source for "what is due". Paste the URL into .env as D2L_ICAL_URL.
"""
import os
import re
from datetime import datetime, timezone

import httpx


def deadlines(url: str | None = None, days: int = 30) -> list[dict]:
    url = url or os.environ.get("D2L_ICAL_URL", "")
    if not url:
        raise SystemExit("Set D2L_ICAL_URL (Brightspace → Calendar → Subscribe → copy the feed URL)")
    text = httpx.get(url, timeout=30, follow_redirects=True).text
    now = datetime.now(timezone.utc)
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        fields = dict(re.findall(r"^([A-Z-]+)(?:;[^:]*)?:(.*)$", block, re.M))
        start = fields.get("DTSTART") or fields.get("DTEND")
        if not start:
            continue
        try:
            when = datetime.strptime(re.sub(r"[^0-9TZ]", "", start), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                when = datetime.strptime(start[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        delta = (when - now).days
        if -1 <= delta <= days:
            out.append({"due": when.isoformat(timespec="minutes"), "in_days": delta,
                        "title": fields.get("SUMMARY", "").replace("\\,", ","),
                        "course": fields.get("LOCATION", "") or fields.get("CATEGORIES", "")})
    return sorted(out, key=lambda r: r["due"])
