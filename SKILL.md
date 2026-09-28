---
name: canvas-mcp
description: Work in the instructor's own Canvas account through the local canvas MCP server - summarise course activity, find late or missing work, draft feedback and announcements, prepare approved changes, and create or grade with rubrics.
---

# Canvas through canvas-mcp

The `canvas` MCP server reaches only the instructor's own Canvas account. The server enforces the
safety rules itself; this skill is how to work well within them.

## Ground rules

- **Ask when anything is ambiguous.** Before acting, confirm which course, date range, action or
  audience is meant. Use `canvas_read("courses", fields=[...])` to offer the choices.
- **Take only the student data the task needs.** Always pass `fields`. Prefer counts and IDs
  over names and content, and never pull a whole roster or all submissions "just in case".
- **Reads are free; changes are two steps.** First `prepare` (changes nothing), show the
  person the exact result, and get their yes. Then `apply` that one `preview_id`.
- **Never retry a write** whose result says `WRITE STATUS UNCERTAIN` or `do_not_retry`. Read
  the object back and tell the person what Canvas now holds.
- If a result says writes are disabled, show the person the prepared change so they can make it
  in Canvas themselves. Do not look for another way to write.

## Summarise activity (read-only)

```
canvas_read("courses", {"enrollment_type": "teacher", "enrollment_state": "active", "include[]": ["term"]},
            fields=["id", "name", "term.name"])
canvas_read("courses/123/assignments", {"bucket": "upcoming"}, all_pages=True, fields=["id", "name", "due_at"])
canvas_read("courses/123/analytics/student_summaries", all_pages=True, fields=["id", "page_views", "participations",
            "tardiness_breakdown.missing", "tardiness_breakdown.late"])
```

Report numbers and patterns. Name students only when the person asks who.

## Late and missing work; drafting feedback (no posting)

```
canvas_read("courses/123/students/submissions", {"student_ids[]": ["all"], "workflow_state": "submitted", "per_page": 100},
            all_pages=True, fields=["user_id", "assignment_id", "late", "missing", "submitted_at", "score"])
```

Draft feedback in the conversation. **Do not post comments or grades** unless the person
explicitly asks you to post that specific text. Then treat it as a change: prepare, show, apply.

## Announcements

1. Draft the text and show it with the exact course name and ID.
2. `canvas_prepare_write("POST", "courses/123/discussion_topics", {"title": ..., "message": ..., "is_announcement": true})`
3. Show the returned method, URL and body. Say whether it posts immediately or is delayed
   (`delayed_post_at`).
4. Only after the person approves that preview: `canvas_apply_write(preview_id)`.

## Other changes

Use `canvas_prepare_write` for any other ordinary change (a due date, a page, publishing a
module). For `PUT`/`PATCH`/`DELETE`, show `current_target` next to the new body so the person
sees what changes. One preview is one request.

## Rubrics (only when the server has `--enable-rubrics`)

- **Create:** `prepare_rubric_create(course_id, definition, assignment_id?)`. Show the plan
  (criteria, ratings, total, which assignment it attaches to), then `apply_rubric_create`.
- **Grade:**
  1. First read the assignment to get the live criterion IDs:
     `canvas_read("courses/123/assignments/20", fields=["rubric", "points_possible", "post_manually"])`.
  2. Then `prepare_rubric_grading(...)` for up to 50 ungraded students.
  3. Show each student's current grade, criterion points and comments, total, stated grade and
     SpeedGrader link, plus the posting change and any warnings.
  4. Only then `apply_rubric_grading`.
- Students who already have a score are refused. Changing a grade is a separate, per-student
  decision through `canvas_prepare_write`.
- After grading, tell the person that grades stay hidden until they click **Post grades** in
  the gradebook, unless the result says they are already visible.
