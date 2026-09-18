"""Brightspace's official REST API (Valence), called with your own logged-in browser session.

Students don't get Valence app keys, but the API also accepts the web session: the cookies Playwright restored plus
the `X-Csrf-Token` Brightspace keeps in localStorage. Confirmed routes and shapes are in NOTES.md.

Every call is serial and paced (PAUSE seconds apart) so a full sync looks like someone clicking through their
courses, not a crawler.
"""
import time
from datetime import datetime, timedelta, timezone

from .session import base_url

LE, LP = "/d2l/api/le/1.99", "/d2l/api/lp/1.63"
PAUSE = 1.0


class NotFound(Exception):
    pass


class Api:
    def __init__(self, page):
        self.page = page
        token = page.evaluate("() => localStorage.getItem('XSRF.Token') || ''") or ""
        self.headers = {"Accept": "application/json", "X-Csrf-Token": token}
        self._last = 0.0

    def _wait(self):
        gap = PAUSE - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def get(self, path: str, default=None):
        """JSON from a route; `default` on 403/404 (tool not enabled in that course)."""
        self._wait()
        r = self.page.request.get(base_url() + path, headers=self.headers)
        if r.status in (401,) or "/d2l/login" in r.url:
            raise SystemExit("Session expired — run `d2l login` again.")
        if r.status in (403, 404):
            return default
        if not r.ok:
            raise RuntimeError(f"{r.status} from {path}: {r.text()[:200]}")
        return r.json()

    def paged(self, path: str) -> list:
        """Follow both paging styles Valence uses: {Objects, Next} and {Items, PagingInfo.Bookmark}."""
        out, url = [], path
        while url:
            j = self.get(url, default={})
            if "Objects" in j:
                out += j["Objects"]
                nxt = j.get("Next")
                url = nxt.replace(base_url(), "") if nxt else None
            else:
                out += j.get("Items", [])
                info = j.get("PagingInfo") or {}
                sep = "&" if "?" in path else "?"
                url = f"{path}{sep}bookmark={info['Bookmark']}" if info.get("HasMoreItems") else None
        return out

    def download(self, ou: int, topic_id: int) -> bytes:
        self._wait()
        r = self.page.request.get(f"{base_url()}/d2l/le/content/{ou}/topics/files/download/{topic_id}/DirectFileTopicDownload",
                                  headers={"X-Csrf-Token": self.headers["X-Csrf-Token"]}, timeout=120_000)
        if r.status == 404:
            raise NotFound(topic_id)
        if not r.ok:
            raise RuntimeError(f"{r.status} downloading topic {topic_id}")
        return r.body()

    # --- one method per thing we read -------------------------------------------------------------------

    def whoami(self) -> dict:
        return self.get(f"{LP}/users/whoami")

    def news(self, ou: int) -> list:
        return self.get(f"{LE}/{ou}/news/", default=[])

    def toc(self, ou: int) -> dict:
        return self.get(f"{LE}/{ou}/content/toc", default={"Modules": []})

    def grade_items(self, ou: int) -> list:
        return self.get(f"{LE}/{ou}/grades/", default=[])

    def grade_categories(self, ou: int) -> list:
        return self.get(f"{LE}/{ou}/grades/categories/", default=[])

    def my_grades(self, ou: int) -> list:
        return self.get(f"{LE}/{ou}/grades/values/myGradeValues/", default=[])

    def quizzes(self, ou: int) -> list:
        return self.paged(f"{LE}/{ou}/quizzes/")

    def folders(self, ou: int) -> list:
        return self.get(f"{LE}/{ou}/dropbox/folders/", default=[])

    def my_submissions(self, ou: int, folder_id: int) -> list:
        return self.get(f"{LE}/{ou}/dropbox/folders/{folder_id}/submissions/mysubmissions/", default=[])

    def calendar(self, ous: list[int], days_back: int = 7, days_ahead: int = 120) -> list:
        now = datetime.now(timezone.utc)
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        ids = ",".join(map(str, ous))
        return self.paged(f"{LE}/calendar/events/myEvents/?orgUnitIdsCSV={ids}"
                          f"&startDateTime={fmt(now - timedelta(days=days_back))}&endDateTime={fmt(now + timedelta(days=days_ahead))}")
