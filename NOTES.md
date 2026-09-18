# Notes — UDST Brightspace (D2L)

Instance: `https://d2l.udst.edu.qa`. Confirmed working 18 Sep 2026.

## What works

| Thing | Endpoint | Shape |
|---|---|---|
| Courses | `GET /d2l/le/manageCourses/api/mycourses?pageSize=100&sort=current&orgUnitTypeId=3` | JSON `{Courses: [{OrgUnitId, Name, Code, IsActive, StartDate, EndDate, …}]}` |
| Announcements | `/d2l/lms/news/main.d2l?ou=<id>` | HTML table: Title, Start Date |
| Assignments | `/d2l/lms/dropbox/dropbox.d2l?ou=<id>` | HTML table: Folder, Completion Status, Score, Evaluation Status |
| Grades | `/d2l/lms/grades/index.d2l?ou=<id>` | HTML table: item, grade |
| Course home | `/d2l/home/<id>` | nav links reveal each tool's real URL — the fastest way to find endpoints |

JSON calls need `X-Csrf-Token`, which Brightspace keeps in `localStorage['XSRF.Token']`.

## Dead ends (returned 404/403 on this instance)

- `/d2l/api/hm/enrollments/myenrollments/` — 404
- `/d2l/api/hm/courses/`, `/d2l/api/hm/news/{ou}/` — 404
- `/d2l/le/dropbox/{ou}/folders/api/list` — not present
- `/d2l/le/news/{ou}/` — 404 (the list page is `/d2l/lms/news/main.d2l?ou=`)
- `/d2l/lms/dropbox/user/folders_list.d2l?ou=` — loads but renders no rows

## Gotchas found the hard way

- **SSO cookies are session cookies.** Reusing the browser *profile* is not enough — after the login window
  closes you land back on `/d2l/login`. Saving `storage_state` and restoring it into a fresh context works.
- **Stopping Playwright from inside a context-close handler deadlocks.** Use a `@contextmanager` that owns the
  `sync_playwright()` block.
- **`python-dotenv` must be loaded before modules read `os.environ`** — read config inside functions, not at
  import time.
- Wrong course ids (scraped from home-page widgets) give "Not authorized"; use `OrgUnitId` from the courses API.
- Assignment rows fold the due date into the folder cell as a second line: `"Name\nDue on Apr 14, 2026 11:59 PM"`.

## First real run (18 Sep 2026)

18 courses, 130 announcements, 8 assignment folders, 161 grade items. Most courses have no assignments posted
yet; grades exist but are mostly unmarked (`- / 100`).

## Still to do

- [ ] Calendar → Subscribe → put the feed URL in `.env` as `D2L_ICAL_URL` (gives deadlines without any scraping)
- [ ] Quizzes tool (`/d2l/lms/quizzing/quizzing.d2l?ou=`) — due dates live there too
- [ ] Content listing (`/d2l/le/content/<ou>/Home`) for files and links
- [ ] Diff between runs → notify on new announcement or new grade
