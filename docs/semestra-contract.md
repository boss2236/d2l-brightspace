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
POST <connector address>            e.g. https://<project>.supabase.co/functions/v1/connector-ingest
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
- Only courses with at least one gradable item are sent. A course missing from a later push means it has no grades
  yet or its term ended. It is **not** a request to delete it.

## 3. What the endpoint must do

1. **Authenticate.** Hash the presented key (SHA-256) and look it up among non-revoked keys. Unknown or revoked →
   `401 {"error": "invalid key"}`. Compare hashes in constant time. Never store or log keys in plain text.
2. **Validate.** Reject bodies over 1 MB (`413`), a `version` it doesn't support, or any `payload` that fails
   `importPayloadSchema` (`422 {"error": "..."}`). Cap the number of courses (e.g. 50) and deadlines per course.
3. **Upsert, scoped to the key's user.**
   - Course: match by (`user_id`, `external_id`). Create it the first time with the payload's name, term and credits;
     afterwards keep the student's own edits to name and credits.
   - Categories and items: match by name within the course. Update weight, `max_points` and `achieved_points`;
     add new ones. **Don't delete** categories or items the student added in Semestra, or ones Brightspace no
     longer lists (mark them stale at most).
   - Deadlines: optional. Upsert by `external_id` if Semestra shows deadlines.
4. **Record** the key's `last_used_at` and the course's `synced_at`.
5. **Answer** `200 {"ok": true, "courses": <n upserted>}`.

Rate-limit per key (e.g. 60 requests/hour). The connector syncs about 3× a day.

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
