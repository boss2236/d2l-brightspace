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

## Official API (Valence) works with the browser session — preferred since 18 Sep 2026

No app keys needed: the logged-in session cookie + `X-Csrf-Token` (from `localStorage['XSRF.Token']`) is accepted.
Versions: `le` 1.94–1.99, `lp` 1.58–1.63 (`GET /d2l/api/versions/`, public).

| Thing | Endpoint | Notes |
|---|---|---|
| Who am I | `GET /d2l/api/lp/1.63/users/whoami` | `Identifier` = user id |
| Enrolments | `GET /d2l/api/lp/1.63/enrollments/myenrollments/?orgUnitTypeId=3` | paged (`Bookmark`) |
| Announcements | `GET /d2l/api/le/1.99/{ou}/news/` | full `Body.Text/Html`, `StartDate`, `Attachments` |
| Content tree | `GET /d2l/api/le/1.99/{ou}/content/toc` | nested `Modules[].Topics[]`; `TypeIdentifier` File/Link/ContentService |
| File bytes | `GET /d2l/le/content/{ou}/topics/files/download/{topicId}/DirectFileTopicDownload` | also `/content/enforced/...?ou={ou}`. Old topics can 404 (file gone) |
| Grade items | `GET /d2l/api/le/1.99/{ou}/grades/` | `Id, Name, GradeType, CategoryId, MaxPoints, Weight` |
| Grade categories | `GET /d2l/api/le/1.99/{ou}/grades/categories/` | `Name, Weight, Grades[]` — the "Quizzes"/"Tests" rows of the HTML table |
| My grades | `GET /d2l/api/le/1.99/{ou}/grades/values/myGradeValues/` | only released items; `DisplayedGrade`, `PointsNumerator/Denominator` |
| Quizzes | `GET /d2l/api/le/1.99/{ou}/quizzes/` | `{Objects, Next}`; `StartDate/EndDate/DueDate` |
| Assignments | `GET /d2l/api/le/1.99/{ou}/dropbox/folders/` | `DueDate` in UTC |
| My submissions | `GET /d2l/api/le/1.99/{ou}/dropbox/folders/{id}/submissions/mysubmissions/` | `Status`, `Feedback.Score` |
| Calendar | `GET /d2l/api/le/1.99/calendar/events/myEvents/?orgUnitIdsCSV=a,b&startDateTime=…Z&endDateTime=…Z` | 400 without `orgUnitIdsCSV` |

Content file types seen in MATH1030: pdf 21, mp4 18, html 14, docx 1, links 46 (YouTube etc.). Videos are skipped.
The `/d2l/api/le/1.99/{ou}/content/topics/{id}/file` route 404s here — use `DirectFileTopicDownload`.

## Dead ends (returned 404/403 on this instance)

- `/d2l/api/hm/enrollments/myenrollments/` — 404
- `/d2l/api/hm/courses/`, `/d2l/api/hm/news/{ou}/` — 404
- `/d2l/le/dropbox/{ou}/folders/api/list` — not present
- `/d2l/le/news/{ou}/` — 404 (the list page is `/d2l/lms/news/main.d2l?ou=`)
- `/d2l/lms/dropbox/user/folders_list.d2l?ou=` — loads but renders no rows

## Gotchas found the hard way

- **Two logins, two lifetimes.** Brightspace's own session (`d2lSessionVal`) times out after a few idle hours
  (it died between a 15:33 and a 20:07 sync). The Microsoft SSO cookie (`ESTSAUTHPERSISTENT`) lasts ~90 days and is
  extended each time it's used. `session._renew` presses the login page's "UDST - Single Sign On Login" button
  (it runs `/d2l/lp/auth/saml/initiate-login` with parameters of its own — opening that URL bare 404s), Microsoft
  signs straight back in, and the refreshed cookies are saved after every run. Manual `d2l login` is only needed
  when Microsoft itself asks for a password/MFA again.

- **SSO cookies are session cookies.** Reusing the browser *profile* is not enough — after the login window
  closes you land back on `/d2l/login`. Saving `storage_state` and restoring it into a fresh context works.
- **Stopping Playwright from inside a context-close handler deadlocks.** Use a `@contextmanager` that owns the
  `sync_playwright()` block.
- **`python-dotenv` must be loaded before modules read `os.environ`** — read config inside functions, not at
  import time.
- Wrong course ids (scraped from home-page widgets) give "Not authorized"; use `OrgUnitId` from the courses API.
- The courses list mixes real offerings with service shells (`StudentCentralServices`, `SELFHELP_2026Y1`,
  `StudentEmployment`) and every past term — all `IsActive: true`. Filter on code shape + Start/EndDate instead.
- The news table interleaves attachment rows (`"Attachment(s): file.pdf (1 MB)"`); keep only rows with a real date.
- Assignment rows fold the due date into the folder cell as a second line: `"Name\nDue on Apr 14, 2026 11:59 PM"`.

## First real run (18 Sep 2026)

18 courses, 130 announcements, 8 assignment folders, 161 grade items. Most courses have no assignments posted
yet; grades exist but are mostly unmarked (`- / 100`).

## Second iteration (18 Sep 2026): API sync, knowledge base, MCP

- Switched every read from HTML scraping to Valence over the session (table above). The old HTML routes stay
  documented under "What works" as a fallback.
- The HTML grades table mixed category totals ("Quizzes", "Tests") in with items; `grades/` + `grades/categories/`
  separate them properly.
- `DirectFileTopicDownload` serves HTML topics as a **zip** (page + assets); `extract.py` opens the zip.
- 31 topics in MATH1030-19 (Lecture-Theatre) 404 on download *and* in the browser: the course copy lost its files.
  Same files are fine in MATH1030-20. Marked `missing`, never retried.
- pypdf emits some PDFs one word per line; `extract._tidy` rejoins them. Scanned PDFs (few words per page) get a
  "mostly images" marker so an AI knows the text is incomplete.
- Calendar needs `orgUnitIdsCSV`; without it the cross-course route is a 400.
- mcp SDK is 2.x: `FastMCP` is now `mcp.server.mcpserver.MCPServer`.

## Ideas

- [ ] Assignment submissions feedback files / rubric text (route: `.../mysubmissions/` has `Feedback`)
- [ ] Discussions (`/d2l/api/le/1.99/{ou}/discussions/forums/` — empty in current courses)
- [ ] OCR for scanned PDFs (tesseract) so the "mostly images" files become searchable
- [ ] Export upcoming deadlines as an .ics for Google Calendar
