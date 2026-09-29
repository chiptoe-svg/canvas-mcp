# Ordinary Canvas writes

Use `canvas_read` to resolve the exact course and inspect the target. Ask when the course, target,
audience, date, or requested action is ambiguous. Request only the fields needed for the task.

Draft text in the conversation before preparing a post. Do not post a draft merely because the
person asked you to write it.

1. Call `canvas_prepare_write(method, path, body)`. This changes nothing.
2. Show the returned method, URL, body, and `current_target` when present. Explain whether an
   announcement posts immediately or uses `delayed_post_at`.
3. Wait for approval of that exact preview.
4. Call `canvas_apply_write(preview_id)` once. In the server dialog, the person must select
   **Apply this exact change** and submit it; **Do not apply**, Skip, cancel, or no answer refuses.
5. Report the result. If it is uncertain, do not retry; read back the target.

An announcement normally uses:

```text
canvas_prepare_write("POST", "courses/123/discussion_topics",
  {"title": ..., "message": ..., "is_announcement": true})
```

Use the same prepare/show/approve/apply sequence for due dates, pages, modules, feedback, comments,
grades, publishing, and deletes. If writes are disabled, show the prepared change so the person can
make it in Canvas; do not bypass the server. `canvas_test_confirmation()` may be used to demonstrate
the confirmation UI without contacting Canvas.
