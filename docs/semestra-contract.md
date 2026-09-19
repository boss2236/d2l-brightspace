<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Semestra connector contract (v1)

How `d2l-brightspace` sends a student's courses and grades to [Semestra](https://github.com/boss2236/Semestra), a grade
planner, and what a Semestra-side endpoint must do with them. Both sides are built against this document; any other
app that wants the same feed can implement it too.

## 1. Direction and trust

```
Brightspace ──(student's own login, on their computer)──▶ d2l-brightspace ──(HTTPS POST, connector key)──▶ Semestra
```

- **Push, never pull.** The Brightspace login stays on the student's machine. Semestra never receives or stores
  university credentials or cookies, and never logs in to Brightspace itself.
- **One key per student.** Semestra issues a *connector key* in its settings. The key identifies the student's
  account; the request body never carries a user id, and anything that looks like one must be ignored.
- **Least data.** Only course names, term, grade structure (categories, weights, items, max points), the student's
  achieved points, and upcoming deadline titles and dates. Never announcements, files, instructor feedback text, or
  anything about other students.

## 2. Request

```
POST <connector address>            e.g. https://<semestra-site>/api/connector   (forwarded to the edge function)
Authorization: Bearer <connector key>
Content-Type: application/json
X-Connector: d2l-brightspace/1
```

Clients must use `https://`; plain `http://` is only for `localhost` while developing. Clients never follow
redirects, so the key can't be forwarded to another host.

### Body

```jsonc
{
  "version": 1,                              // contract version
  "source": "d2l-brightspace",               // the connector that sent it
  "source_host": "d2l.udst.edu.qa",          // the Brightspace instance
  "sent_at": "2026-09-19T18:10:57+00:00",
  "courses": [
    {
      "external_id": "d2l:d2l.udst.edu.qa:197850",   // stable per course: upsert key
      "code": "MATH1030",
      "section": "19",
      "payload": {                                    // exactly Semestra's import format (importPayloadSchema)
        "course": { "name": "MATH1030 Calculus I (Lecture-Theatre)", "term": "Fall 2026", "credits": 3 },
        "categories": [
          { "name": "Quizzes", "weight": 25,
            "items": [ { "name": "Quiz 1", "max_points": 100, "achieved_points": null } ] },
          { "name": "Assignments", "weight": 10,
            "items": [ { "name": "Assignment #1", "max_points": 10, "achieved_points": 10 } ] }
        ]
      },
      "deadlines": [                                  // next 60 days; may be empty
        { "external_id": "d2l:d2l.udst.edu.qa:quiz closes:Quiz 2:2026-10-01T20:59:59.000Z",
          "title": "Quiz 2", "kind": "quiz closes", "due": "2026-10-01T20:59:59.000Z" }
      ]
    }
  ]
}
```

- `payload` validates against Semestra's existing `importPayloadSchema` (`src/shared/lib/importFormat.ts`).
  `achieved_points` is `null` until a grade is released.
- **Every course the student is currently enrolled in is sent**, including ones with no grades published yet; those
  arrive with `categories: []` so the course can be shown and planned, and the categories follow on a later push.
  (Semestra's Import *page* still requires at least one category; this is the connector path.)
- A course missing from a later push means its term ended. It is **not** a request to delete it.

## 3. What the endpoint must do

1. **Authenticate.** Hash the presented key (SHA-256) and look it up among non-revoked keys. Unknown or revoked →
   `401 {"error": "invalid key"}`. Compare hashes in constant time. Never store or log keys in plain text.
2. **Validate.** Reject bodies over 1 MB (`413`), a `version` it doesn't support, or any `payload` that fails
   `importPayloadSchema` (`422 {"error": "..."}`). Cap the number of courses (e.g. 50) and deadlines per course.
3. **Upsert, scoped to the key's user.** Manual use and the connector must coexist: a student can type courses in
   by hand, connect later, and pause syncing per course.
   - Course: match by (`user_id`, `external_id`). If there's no match, first **link to a course the student entered
     by hand** (same name, or exactly one unlinked course whose name contains the course `code`); only if none or
     several match, create a new one. Keep the student's own name and credits afterwards.
   - A course the student **paused** (`sync_enabled = false`) is skipped entirely and counted as `paused`.
   - Categories and items: match by name within the course. Update weight, `max_points` and `achieved_points`;
     add new ones. **Don't delete** categories or items the student added in Semestra, or ones Brightspace no
     longer lists (mark them stale at most).
   - Deadlines: optional. Upsert by `external_id` if Semestra shows deadlines.
4. **Record** the key's `last_used_at` and the course's `synced_at`.
5. **Answer** `200 {"ok": true, "courses": <n updated>, "linked_to_existing": <n>, "paused": <n>}`.

Rate-limit per key (e.g. 60 requests/hour). The connector syncs about 3× a day.

### Polling: has the student asked for a sync?

Semestra can't reach the student's computer, so the connector asks:

```
POST <connector address>        {"version": 1, "action": "poll"}
→ 200 {"ok": true, "sync_requested_at": "2026-09-20T10:00:00Z" | null, "last_push_at": "…"}
```

When `sync_requested_at` is newer than the last one handled, the connector pulls fresh data from Brightspace and
pushes; the push clears the request. The connector polls every couple of minutes while it's running, so "Sync now"
in Semestra takes effect within a few minutes and needs no public link.

## 4. Responses the connector understands

| Status | Meaning | Connector shows |
|---|---|---|
| 200 | stored | "sent N course(s)" |
| 401 | bad or revoked key | "create a new one in Semestra's settings" |
| 3xx | redirect | refused; "use the final URL" |
| 413 / 422 | too big / invalid | the `error` text |
| 429 | rate-limited | the status; retried on the next sync |
| 5xx | Semestra problem | the status; retried on the next sync |

## 5. Versioning

Additive fields don't change `version`; clients and servers ignore fields they don't know. A breaking change bumps
`version`, and servers should keep accepting the previous version for at least one semester.
