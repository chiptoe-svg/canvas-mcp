# Rubric creation and grading

Use only the rubric tools exposed by the registered `canvas` MCP server. Request only the student
data needed for the task.

## Create a rubric

1. Call `prepare_rubric_create(course_id, definition, assignment_id?)`.
2. Show the criteria, ratings, total points, and assignment attachment plan.
3. Wait for approval of that exact preview.
4. Call `apply_rubric_create(preview_id)` once and report the read-back result.

## Grade with a rubric

Rubric grading is available only when the server has `--enable-rubric-grading`.

1. Read the live assignment and resolve its rubric association and criterion IDs. A rubric ID or
   `rubric_settings.id` is not an association ID.
2. Call `prepare_rubric_grading(...)` for no more than 50 ungraded students.
3. Show each student's current grade, criterion points and comments, calculated total, stated grade,
   SpeedGrader link, posting change, and warnings.
4. Wait for approval of that exact preview.
5. Call `apply_rubric_grading(preview_id)` once. Read back and report every attempted grade.

Never grade evidence that was not submitted. Students who already have a score are refused; changing
an existing score is a separate, per-student decision through the ordinary write workflow. If any
write becomes uncertain, stop the batch and do not retry it. After grading, state whether grades are
hidden for manual posting or already visible.
