"""Rubric creation plus optional advanced batch rubric grading.

Creation is part of the standard tool set. Batch grading is enabled only when the server is
started with ``--enable-rubric-grading``. Ported from
canvas-api-guard's level2/canvas_api_operations.py (create_rubric, grade_with_rubric), with
the CLI, subprocess guard calls and audit log removed. Every Canvas request goes through the
same host-locked CanvasClient as the generic tools.

Each operation has two halves:

* ``prepare_*`` reads Canvas, validates everything, and returns a plan: an exact, reviewed
  description of every write. It changes nothing.
* ``apply_*`` takes that plan (from a one-time preview ID held by the server), re-checks the
  live state it depends on, performs the writes, and reads each one back.

No write is ever retried. A write whose result cannot be proved stops the run and is reported
as ``uncertain``, together with exactly which students were written before it.
"""

from __future__ import annotations

import re
from typing import Any

import anyio
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from canvas_client import CanvasClient, CanvasError

MAX_GRADING_BATCH = 50
MAX_CRITERIA = 50
MAX_RATINGS = 20
MAX_TEXT = 5000
SCORE_TOLERANCE = 0.005          # Canvas rounds scores to two decimals

VISIBILITY = {
    "manual": "hidden from students until you click Post grades in the Canvas gradebook",
    "automatic": "each grade, criterion score and comment is visible to its student as soon as it is written",
}


class RubricError(Exception):
    """Validation failed or live state changed; nothing was written."""


# --------------------------------------------------------------------------- validation
def canvas_id(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise RubricError("%s must be a Canvas numeric ID" % label)
    text = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,17}", text):
        raise RubricError("%s must be a Canvas numeric ID, got %r" % (label, value))
    return int(text)


def exact_object(value: Any, required: tuple, optional: tuple = (), label: str = "definition") -> dict:
    if not isinstance(value, dict):
        raise RubricError("%s must be an object" % label)
    unknown = set(value) - set(required) - set(optional)
    missing = set(required) - set(value)
    if unknown:
        raise RubricError("%s has unsupported field(s): %s" % (label, ", ".join(sorted(unknown))))
    if missing:
        raise RubricError("%s is missing required field(s): %s" % (label, ", ".join(sorted(missing))))
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RubricError("%s must be a non-empty string" % label)
    if len(value) > MAX_TEXT:
        raise RubricError("%s is longer than %d characters" % (label, MAX_TEXT))
    return value


def points_value(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RubricError("%s must be a number" % label)
    if value != value or value in (float("inf"), float("-inf")):
        raise RubricError("%s must be a finite number" % label)
    if value < 0:
        raise RubricError("%s must not be negative" % label)
    return value


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def same_number(a: Any, b: Any) -> bool:
    x, y = number(a), number(b)
    return x is not None and y is not None and abs(x - y) <= SCORE_TOLERANCE


# ------------------------------------------------------------------------ rubric creation
def criterion(value: Any, index: int) -> dict:
    label = "criterion %d" % (index + 1)
    row = exact_object(value, ("description", "points", "ratings"), ("long_description",), label)
    text(row["description"], label + " description")
    if "long_description" in row and row["long_description"] is not None:
        text(row["long_description"], label + " long_description")
    maximum = points_value(row["points"], label + " points")
    ratings = row["ratings"]
    if not isinstance(ratings, list) or not ratings:
        raise RubricError("%s ratings must be a non-empty list" % label)
    if len(ratings) > MAX_RATINGS:
        raise RubricError("%s has more than %d ratings" % (label, MAX_RATINGS))
    clean = []
    for r_index, rating in enumerate(ratings):
        r_label = "%s rating %d" % (label, r_index + 1)
        rating = exact_object(rating, ("description", "points"), ("long_description",), r_label)
        text(rating["description"], r_label + " description")
        if rating.get("long_description") is not None and "long_description" in rating:
            text(rating["long_description"], r_label + " long_description")
        if points_value(rating["points"], r_label + " points") > maximum:
            raise RubricError("%s points exceed the criterion's %s points" % (r_label, maximum))
        clean.append(dict(rating))
    # Canvas wants criteria AND ratings as index-keyed objects; an array gets a bare HTTP 500.
    out = dict(row, ratings={str(i): r for i, r in enumerate(clean)})
    return out


def rubric_definition(definition: Any) -> dict:
    """Validate the simple definition and return Canvas's indexed ``rubric`` object."""
    d = exact_object(definition, ("title", "criteria"), ("free_form_criterion_comments",), "rubric definition")
    text(d["title"], "rubric title")
    crit = d["criteria"]
    if not isinstance(crit, list) or not crit:
        raise RubricError("rubric criteria must be a non-empty list")
    if len(crit) > MAX_CRITERIA:
        raise RubricError("rubric has more than %d criteria" % MAX_CRITERIA)
    rubric = {"title": d["title"], "criteria": {str(i): criterion(c, i) for i, c in enumerate(crit)}}
    ffc = d.get("free_form_criterion_comments")
    if ffc is not None and not isinstance(ffc, bool):
        raise RubricError("free_form_criterion_comments must be true or false")
    if ffc:                       # sent only when true: Canvas stores false as null
        rubric["free_form_criterion_comments"] = True
    return rubric


def speedgrader_url(assignment: dict, student_id: Any = None) -> str | None:
    match = re.match(r"(https://[^/]+)/courses/(\d+)/assignments/(\d+)$", str(assignment.get("html_url") or ""))
    if not match:
        return None
    url = "%s/courses/%s/gradebook/speed_grader?assignment_id=%s" % match.groups()
    return url if student_id is None else url + "&student_id=%s" % student_id


def prepare_rubric_create(client: CanvasClient, course_id: Any, definition: Any,
                          assignment_id: Any = None) -> dict:
    """Validate and build the exact create request. Reads Canvas; writes nothing."""
    course_id = canvas_id(course_id, "course_id")
    rubric = rubric_definition(definition)
    course = client.get("courses/%d" % course_id) or {}
    plan = {"kind": "rubric_create", "course_id": course_id,
            "course_name": course.get("name"), "assignment_id": None}
    if assignment_id is not None:
        assignment_id = canvas_id(assignment_id, "assignment_id")
        assignment = client.get("courses/%d/assignments/%d" % (course_id, assignment_id)) or {}
        attached = (assignment.get("rubric_settings") or {}).get("id")
        if attached is not None:
            raise RubricError("assignment %d already has rubric %s attached; detach it in Canvas first, "
                              "or create this rubric without assignment_id" % (assignment_id, attached))
        association = {"association_type": "Assignment", "association_id": assignment_id,
                       "purpose": "grading", "use_for_grading": False}
        plan.update(assignment_id=assignment_id, assignment_name=assignment.get("name"))
    else:
        association = {"association_type": "Course", "association_id": course_id, "purpose": "bookmark"}
    plan["request"] = {"method": "POST", "path": "/api/v1/courses/%d/rubrics" % course_id,
                       "body": {"rubric": rubric, "rubric_association": association}}
    plan["summary"] = {
        "course": {"id": course_id, "name": course.get("name")},
        "attach_to_assignment": ({"id": assignment_id, "name": plan.get("assignment_name"),
                                  "use_rubric_for_grading": False} if assignment_id else None),
        "title": rubric["title"],
        "criteria": [{"description": c["description"], "points": c["points"],
                      "ratings": [[r["description"], r["points"]] for r in c["ratings"].values()]}
                     for c in rubric["criteria"].values()],
        "total_points": sum(c["points"] for c in rubric["criteria"].values()),
    }
    return plan


def apply_rubric_create(client: CanvasClient, plan: dict) -> dict:
    """Send the one prepared POST, then read back every criterion and rating."""
    course_id, assignment_id = plan["course_id"], plan["assignment_id"]
    body = plan["request"]["body"]
    if assignment_id is not None:                  # the live state the plan depended on
        live = client.get("courses/%d/assignments/%d" % (course_id, assignment_id)) or {}
        if (live.get("rubric_settings") or {}).get("id") is not None:
            raise RubricError("assignment %d gained a rubric after the preview; nothing was written"
                              % assignment_id)
    resp = client.request("POST", "courses/%d/rubrics" % course_id, body=body)
    created = (resp.data or {}).get("rubric") if isinstance(resp.data, dict) else None
    rubric_id = (created or {}).get("id") if isinstance(created, dict) else None
    if rubric_id is None:
        return _uncertain("Canvas accepted the create but returned no rubric ID; check the course's "
                          "Rubrics page before trying again", rubric_id=None)
    try:
        back = client.get("courses/%d/rubrics/%s" % (course_id, rubric_id)) or {}
    except CanvasError as err:
        return _uncertain("rubric %s was created but could not be read back: %s" % (rubric_id, err),
                          rubric_id=rubric_id)
    problems = compare_rubric(body["rubric"], back)
    result = {"ok": not problems, "outcome": "verified" if not problems else "uncertain",
              "rubric_id": rubric_id, "course_id": course_id, "assignment_id": assignment_id}
    if problems:
        return _uncertain("rubric %s was created but did not read back as prepared: %s" % (
            rubric_id, "; ".join(problems)), rubric_id=rubric_id, course_id=course_id,
            assignment_id=assignment_id)
    if assignment_id is not None:
        try:
            assignment = client.get("courses/%d/assignments/%d" % (course_id, assignment_id)) or {}
        except CanvasError as err:
            return _uncertain("rubric %s created; the assignment could not be read back: %s"
                              % (rubric_id, err), rubric_id=rubric_id)
        if str((assignment.get("rubric_settings") or {}).get("id")) != str(rubric_id):
            return _uncertain("rubric %s created, but assignment %d does not show it attached"
                              % (rubric_id, assignment_id), rubric_id=rubric_id)
        if assignment.get("use_rubric_for_grading"):
            return _uncertain("rubric %s attached, but 'use this rubric for grading' reads back ON; "
                              "turn it off in Canvas before grading" % rubric_id, rubric_id=rubric_id)
        result["use_rubric_for_grading"] = False
        result["speedgrader_url"] = speedgrader_url(assignment)
    return result


def compare_rubric(expected: dict, actual: dict) -> list[str]:
    problems = []
    got = actual.get("data") if isinstance(actual, dict) else None
    want = list(expected["criteria"].values())
    if not isinstance(got, list) or len(got) != len(want):
        return ["criterion count %s, expected %d" % (len(got) if isinstance(got, list) else "?", len(want))]
    for i, (w, g) in enumerate(zip(want, got), 1):
        if not isinstance(g, dict):
            problems.append("criterion %d malformed" % i)
            continue
        if g.get("description") != w["description"] or not same_number(g.get("points"), w["points"]):
            problems.append("criterion %d" % i)
            continue
        wr, gr = list(w["ratings"].values()), g.get("ratings")
        if (not isinstance(gr, list) or len(wr) != len(gr)
                or any(not isinstance(a, dict) or a.get("description") != b["description"]
                       or not same_number(a.get("points"), b["points"])
                       for a, b in zip(gr if isinstance(gr, list) else [], wr))):
            problems.append("criterion %d ratings" % i)
    return problems


def _uncertain(message: str, **extra) -> dict:
    return dict({"ok": False, "outcome": "uncertain", "error": "WRITE STATUS UNCERTAIN: " + message,
                 "do_not_retry": True}, **extra)


# ------------------------------------------------------------------------- rubric grading
def live_assignment(client: CanvasClient, course_id: int, assignment_id: int) -> dict:
    assignment = client.get("courses/%d/assignments/%d" % (course_id, assignment_id)) or {}
    if not isinstance(assignment.get("rubric"), list) or not assignment["rubric"]:
        raise RubricError("assignment %d has no attached rubric; attach one first" % assignment_id)
    if assignment.get("use_rubric_for_grading"):
        raise RubricError(
            "assignment %d has 'use this rubric for grading' ON, so Canvas would overwrite each stated "
            "grade with the rubric total and grade entries that have none. Turn it off in Canvas "
            "(edit the rubric on the assignment), then prepare again." % assignment_id)
    return assignment


def rubric_signature(assignment: dict) -> list:
    """What grading depends on: each live criterion's id, maximum and ignore_for_scoring."""
    return [[str(r.get("id")), number(r.get("points")), bool(r.get("ignore_for_scoring"))]
            for r in assignment.get("rubric") or []]


def grade_entry(value: Any, limits: dict, index: int) -> dict:
    label = "grades[%d]" % index
    entry = exact_object(value, ("student_id", "criteria"), ("grade",), label)
    student_id = canvas_id(entry["student_id"], label + ".student_id")
    grade = entry.get("grade")
    if grade is not None:
        grade = points_value(grade, label + ".grade")
    crit = entry["criteria"]
    if not isinstance(crit, dict) or not crit:
        raise RubricError("%s.criteria must be a non-empty object keyed by live criterion ID" % label)
    unknown = sorted(set(map(str, crit)) - set(limits))
    if unknown:
        raise RubricError("%s names criterion ID(s) %s, which the assignment's live rubric does not have"
                          % (label, ", ".join(unknown)))
    assessment, rows, total, excluded = {}, [], 0.0, []
    for cid, score in crit.items():
        cid = str(cid)
        c_label = "%s.criteria[%s]" % (label, cid)
        score = exact_object(score, ("points",), ("comments",), c_label)
        pts = points_value(score["points"], c_label + ".points")
        maximum, ignored, description = limits[cid]
        if maximum is not None and pts > maximum + SCORE_TOLERANCE:
            raise RubricError("%s: %s points exceeds the live maximum %s" % (c_label, pts, maximum))
        comments = score.get("comments")
        if comments is not None and not isinstance(comments, str):
            raise RubricError("%s.comments must be a string" % c_label)
        if comments is not None and len(comments) > MAX_TEXT:
            raise RubricError("%s.comments is longer than %d characters" % (c_label, MAX_TEXT))
        assessment[cid] = {"points": pts} if comments is None else {"points": pts, "comments": comments}
        rows.append({"criterion_id": cid, "description": description, "points": pts,
                     "max": maximum, "comments": comments})
        if ignored:
            excluded.append(cid)
        else:
            total += pts
    return {"student_id": student_id, "assessment": assessment, "criteria": rows,
            "criterion_total": round(total, 2), "excluded_from_total": sorted(excluded), "grade": grade}


def validate_grades(grades: Any) -> list:
    if not isinstance(grades, list) or not grades:
        raise RubricError("grades must be a non-empty list")
    if len(grades) > MAX_GRADING_BATCH:
        raise RubricError("a reviewed grading batch is limited to %d students (got %d)"
                          % (MAX_GRADING_BATCH, len(grades)))
    seen = set()
    for i, entry in enumerate(grades):
        sid = str(entry.get("student_id")).strip() if isinstance(entry, dict) else None
        if sid in seen:
            raise RubricError("grades[%d] repeats student_id %s" % (i, sid))
        seen.add(sid)
    return grades


def submission_path(course_id: int, assignment_id: int, student_id: int) -> str:
    return "courses/%d/assignments/%d/submissions/%d" % (course_id, assignment_id, student_id)


def current_grade(sub: dict) -> dict:
    return {k: sub.get(k) for k in ("workflow_state", "score", "grade", "graded_at")}


def prepare_rubric_grading(client: CanvasClient, course_id: Any, assignment_id: Any, grades: Any,
                           keep_post_policy: bool = False) -> dict:
    """Validate every entry against the live rubric and each student's live submission.
    Reads Canvas; writes nothing."""
    course_id = canvas_id(course_id, "course_id")
    assignment_id = canvas_id(assignment_id, "assignment_id")
    if not isinstance(keep_post_policy, bool):
        raise RubricError("keep_post_policy must be true or false")
    validate_grades(grades)
    assignment = live_assignment(client, course_id, assignment_id)
    limits = {str(r.get("id")): (number(r.get("points")), bool(r.get("ignore_for_scoring")), r.get("description"))
              for r in assignment["rubric"]}
    entries = [grade_entry(e, limits, i) for i, e in enumerate(grades)]   # all validated first
    already_scored, rows = [], []
    for e in entries:
        sub = client.get(submission_path(course_id, assignment_id, e["student_id"]),
                         {"include[]": ["user"]}) or {}
        if str(sub.get("user_id")) != str(e["student_id"]):
            raise RubricError("student %s has no submission record for this assignment" % e["student_id"])
        if sub.get("score") is not None:
            already_scored.append({"student_id": e["student_id"], "score": sub.get("score"),
                                   "grade": sub.get("grade"), "graded_at": sub.get("graded_at")})
            continue
        row = {"student_id": e["student_id"],
               "student_name": (sub.get("user") or {}).get("name"),
               "current_grade": current_grade(sub),
               "posted_at": sub.get("posted_at"),
               "criteria": e["criteria"],
               "criterion_total": e["criterion_total"],
               "excluded_from_total": e["excluded_from_total"],
               "stated_grade": e["grade"],
               "speedgrader_url": speedgrader_url(assignment, e["student_id"])}
        if e["grade"] is not None and abs(e["grade"] - e["criterion_total"]) > SCORE_TOLERANCE:
            row["difference_from_total"] = round(e["grade"] - e["criterion_total"], 2)
        possible = number(assignment.get("points_possible"))
        if e["grade"] is not None and possible is not None and e["grade"] > possible + SCORE_TOLERANCE:
            row["exceeds_points_possible"] = possible
        rows.append(row)
    if already_scored:
        raise RubricError(
            "refusing the whole batch: %d student(s) already have a score in Canvas (%s). Batch rubric "
            "grading only fills in ungraded submissions; remove them from the batch. Changing an existing "
            "grade is a separate, individual decision (use canvas_prepare_write on that submission)."
            % (len(already_scored), ", ".join("%s: %s" % (s["student_id"], s["score"]) for s in already_scored)))
    manual_now = bool(assignment.get("post_manually"))
    switch = not manual_now and not keep_post_policy
    after = "manual" if (manual_now or switch) else "automatic"
    warnings = []
    visible = [r["student_id"] for r in rows if r["posted_at"]]
    if after == "automatic":
        warnings.append("keep_post_policy is on and this assignment posts automatically: every grade, "
                        "criterion score and comment is visible to its student the moment it is written.")
    elif visible:
        warnings.append("%d student(s) already have posted submissions (%s); Canvas shows what is written "
                        "for them immediately, even under manual posting." % (len(visible), ", ".join(map(str, visible))))
    writes = []
    for e in entries:
        body = {"rubric_assessment": e["assessment"]}
        if e["grade"] is not None:
            body["submission"] = {"posted_grade": e["grade"]}
        writes.append({"student_id": e["student_id"], "method": "PUT",
                       "path": "/api/v1/" + submission_path(course_id, assignment_id, e["student_id"]),
                       "body": body})
    return {
        "kind": "rubric_grading", "course_id": course_id, "assignment_id": assignment_id,
        "rubric_signature": rubric_signature(assignment), "keep_post_policy": keep_post_policy,
        "writes": writes,
        "summary": {
            "assignment": {"id": assignment_id, "name": assignment.get("name"),
                           "points_possible": assignment.get("points_possible")},
            "post_policy": {"before": "manual" if manual_now else "automatic", "after": after,
                            "will_switch_to_manual_first": switch},
            "student_visibility": VISIBILITY[after],
            "warnings": warnings,
            "students": rows,
        },
    }


def _post_policy_manual(client: CanvasClient, assignment_id: int, course_id: int) -> str | None:
    """Switch to manual posting and prove it. Returns None on success, else an error string.
    Raises RubricError when Canvas refused (nothing changed)."""
    try:
        resp = client.set_post_policy(assignment_id, manual=True)
    except CanvasError as err:
        if err.outcome == "refused":
            raise RubricError("Canvas refused the switch to manual posting; nothing was written: %s" % err)
        return "the switch to manual posting may or may not have applied: %s" % err
    errors = (resp.data or {}).get("errors") if isinstance(resp.data, dict) else None
    if errors:
        raise RubricError("Canvas refused the switch to manual posting; nothing was written: %s"
                          % "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors))
    try:
        back = client.get("courses/%d/assignments/%d" % (course_id, assignment_id)) or {}
    except CanvasError as err:
        return "the switch to manual posting was sent but could not be read back: %s" % err
    if back.get("post_manually") is not True:
        return "the switch to manual posting was sent but post_manually reads back %r" % back.get("post_manually")
    return None


def verify_submission(sub: Any, write: dict) -> list[str]:
    if not isinstance(sub, dict):
        return ["submission read-back was not an object"]
    problems = []
    got = sub.get("rubric_assessment") if isinstance(sub.get("rubric_assessment"), dict) else None
    for cid, want in write["body"]["rubric_assessment"].items():
        have = (got or {}).get(cid)
        if not isinstance(have, dict):
            problems.append("criterion %s not present" % cid)
            continue
        if not same_number(have.get("points"), want["points"]):
            problems.append("criterion %s points %r" % (cid, have.get("points")))
        if "comments" in want and (have.get("comments") or "").strip() != want["comments"].strip():
            problems.append("criterion %s comments differ" % cid)
    if "submission" in write["body"]:
        if not same_number(sub.get("score"), write["body"]["submission"]["posted_grade"]):
            problems.append("score reads back %r" % sub.get("score"))
    return problems


def apply_rubric_grading(client: CanvasClient, plan: dict) -> dict:
    """Perform the prepared batch. Stops at the first refusal, changed state or unproved write."""
    course_id, assignment_id = plan["course_id"], plan["assignment_id"]
    assignment = live_assignment(client, course_id, assignment_id)       # immediately before grading
    if rubric_signature(assignment) != plan["rubric_signature"]:
        raise RubricError("the assignment's rubric changed after the preview; nothing was written. "
                          "Prepare the batch again.")
    report = {"course_id": course_id, "assignment_id": assignment_id, "total": len(plan["writes"]),
              "written": [], "uncertain_student": None, "not_attempted": [],
              "post_policy_switched": False}
    manual = bool(assignment.get("post_manually"))
    if not manual and not plan["keep_post_policy"]:
        problem = _post_policy_manual(client, assignment_id, course_id)
        if problem:
            return _finish(report, plan, "uncertain", "WRITE STATUS UNCERTAIN: " + problem +
                           "; no student was written. Check the assignment's post policy in Canvas.", 0)
        manual, report["post_policy_switched"] = True, True
    report["post_policy_after"] = "manual" if manual else "automatic"
    for i, write in enumerate(plan["writes"]):
        sid = write["student_id"]
        path = submission_path(course_id, assignment_id, sid)
        try:
            live = client.get(path) or {}
        except CanvasError as err:
            return _finish(report, plan, "stopped", "could not re-read student %s before writing: %s" % (sid, err), i)
        if live.get("score") is not None:
            return _finish(report, plan, "stopped", "student %s received a score (%s) after the preview; "
                           "stopped before writing them" % (sid, live.get("score")), i)
        try:
            client.request("PUT", path, body=write["body"])
        except CanvasError as err:
            if err.outcome == "refused":
                return _finish(report, plan, "stopped", "Canvas refused the write for student %s (nothing "
                               "applied for them): %s" % (sid, err), i)
            report["uncertain_student"] = sid
            return _finish(report, plan, "uncertain", "WRITE STATUS UNCERTAIN for student %s: %s" % (sid, err), i + 1)
        except Exception as err:                           # an interruption mid-write
            report["uncertain_student"] = sid
            return _finish(report, plan, "uncertain", "WRITE STATUS UNCERTAIN for student %s: interrupted "
                           "(%s)" % (sid, type(err).__name__), i + 1)
        try:
            back = client.get(path, {"include[]": ["rubric_assessment"]}) or {}
        except CanvasError as err:
            report["uncertain_student"] = sid
            return _finish(report, plan, "uncertain", "WRITE STATUS UNCERTAIN for student %s: sent, but the "
                           "read-back failed: %s" % (sid, err), i + 1)
        problems = verify_submission(back, write)
        if problems:
            report["uncertain_student"] = sid
            return _finish(report, plan, "uncertain", "WRITE STATUS UNCERTAIN for student %s: read-back did "
                           "not match (%s)" % (sid, "; ".join(problems)), i + 1)
        report["written"].append(sid)
    return _finish(report, plan, "verified", None, len(plan["writes"]))


def _finish(report: dict, plan: dict, outcome: str, error: str | None, next_index: int) -> dict:
    report["not_attempted"] = [w["student_id"] for w in plan["writes"][next_index:]]
    report["outcome"] = outcome
    report["ok"] = outcome == "verified"
    if error:
        report["error"] = error
    if outcome != "verified":
        report["do_not_retry"] = True
        report["next_step"] = ("Writes listed in `written` stand. Do not re-run this batch: check the "
                               "listed students in SpeedGrader, then prepare a new batch for the rest.")
    grades = {w["student_id"] for w in plan["writes"] if "submission" in w["body"]}
    posted = {s["student_id"] for s in plan["summary"]["students"] if s.get("posted_at")}
    written_grades = [s for s in report["written"] if s in grades]
    manual = report.get("post_policy_after") == "manual"
    report["grades_written"] = len(written_grades)
    report["grades_not_yet_visible"] = len([s for s in written_grades if s not in posted]) if manual else 0
    report["already_visible"] = len(written_grades) - report["grades_not_yet_visible"]
    if manual and report["grades_not_yet_visible"]:
        report["release"] = ("Nothing new is visible yet. When ready, in the Canvas gradebook open this "
                             "assignment's menu, choose Post grades, then 'Graded'.")
    return report


# ------------------------------------------------------------------------------ MCP tools
def _describe_create(plan: dict) -> str:
    s = plan["summary"]
    where = ("attach to assignment %s (%s), 'use for grading' OFF" % (s["attach_to_assignment"]["id"],
             s["attach_to_assignment"]["name"]) if s["attach_to_assignment"] else "add to the course rubric list")
    return ("Create rubric %r in course %s (%s); %d criteria, %s points total; %s?"
            % (s["title"], s["course"]["id"], s["course"]["name"], len(s["criteria"]), s["total_points"], where))


def _describe_grading(plan: dict) -> str:
    s = plan["summary"]
    lines = ["Write rubric assessments for %d student(s) on assignment %s (%s)?"
             % (len(s["students"]), s["assignment"]["id"], s["assignment"]["name"]),
             "Posting: %s -> %s. Visibility: %s." % (s["post_policy"]["before"], s["post_policy"]["after"],
                                                     s["student_visibility"])]
    lines += ["WARNING: " + w for w in s["warnings"]]
    for r in s["students"][:MAX_GRADING_BATCH]:
        lines.append("- %s %s: rubric %s, grade %s" % (r["student_id"], r.get("student_name") or "",
                                                       r["criterion_total"], r["stated_grade"]))
    return "\n".join(lines)


_PREPARE_CREATE = prepare_rubric_create
_APPLY_CREATE = apply_rubric_create
_PREPARE_GRADING = prepare_rubric_grading
_APPLY_GRADING = apply_rubric_grading


def register(server, tools, prepare_hints: dict, apply_hints: dict,
             *, enable_grading: bool = False) -> None:
    """Add rubric creation, and add batch grading only when explicitly enabled."""

    @server.tool(annotations=ToolAnnotations(title="Prepare rubric creation (no change made)", **prepare_hints))
    async def prepare_rubric_create(course_id: int, definition: dict[str, Any],
                                    assignment_id: int | None = None) -> dict:
        """Validate a rubric and preview the exact create request. Changes nothing.

        definition: {"title": str, "criteria": [{"description": str, "points": number,
        "long_description"?: str, "ratings": [{"description": str, "points": number}, ...]}, ...],
        "free_form_criterion_comments"?: bool}. With assignment_id, the new rubric is attached
        to that assignment with 'use this rubric for grading' OFF.
        """
        return await anyio.to_thread.run_sync(lambda: tools.prepare_rubric(
            "rubric_create", lambda c: _PREPARE_CREATE(c, course_id, definition, assignment_id)))

    @server.tool(annotations=ToolAnnotations(title="Create the approved rubric", **apply_hints))
    async def apply_rubric_create(preview_id: str, ctx: Context) -> dict:
        """Create the rubric from an approved prepare_rubric_create preview, then read every
        criterion and rating back. The preview ID is consumed. Never retry an uncertain result."""
        return await tools.apply_rubric("rubric_create", preview_id, ctx, _APPLY_CREATE, _describe_create)

    if not enable_grading:
        return

    @server.tool(annotations=ToolAnnotations(title="Prepare rubric grading (no change made)", **prepare_hints))
    async def prepare_rubric_grading(course_id: int, assignment_id: int, grades: list[dict[str, Any]],
                                     keep_post_policy: bool = False) -> dict:
        """Validate up to 50 students' rubric scores against the live rubric. Changes nothing.

        grades: [{"student_id": int, "criteria": {"<live criterion id>": {"points": number,
        "comments"?: str}}, "grade"?: number}]. Students who already have a score are refused.
        Unless keep_post_policy is true, an automatically-posting assignment is switched to
        manual posting before any grade is written.
        """
        return await anyio.to_thread.run_sync(lambda: tools.prepare_rubric(
            "rubric_grading", lambda c: _PREPARE_GRADING(c, course_id, assignment_id, grades,
                                                         keep_post_policy)))

    @server.tool(annotations=ToolAnnotations(title="Write the approved rubric grades", **apply_hints))
    async def apply_rubric_grading(preview_id: str, ctx: Context) -> dict:
        """Write an approved prepare_rubric_grading batch, verifying each student. Stops at the
        first refusal or uncertain write and reports exactly who was written. Never retry."""
        return await tools.apply_rubric("rubric_grading", preview_id, ctx, _APPLY_GRADING, _describe_grading)
