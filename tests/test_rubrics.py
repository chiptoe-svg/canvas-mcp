"""Rubric extension: validation, create read-back, grading safeguards, partial/uncertain runs."""

import copy

import pytest

from extensions import rubrics
from extensions.rubrics import RubricError
from fake_canvas import BASE, FakeCanvas, client_for

RUBRIC_DEF = {"title": "Lab report", "criteria": [
    {"description": "Method", "points": 10, "ratings": [
        {"description": "Complete", "points": 10}, {"description": "Partial", "points": 5},
        {"description": "Missing", "points": 0}]},
    {"description": "Writing", "points": 5, "long_description": "Clarity",
     "ratings": [{"description": "Clear", "points": 5}, {"description": "Unclear", "points": 0}]},
]}

LIVE_RUBRIC = [
    {"id": "_a", "description": "Method", "points": 10},
    {"id": "_b", "description": "Writing", "points": 5},
    {"id": "_c", "description": "Outcome", "points": 4, "ignore_for_scoring": True},
]


# ------------------------------------------------------------------------------ fake course
class Course:
    """A stateful fake: an assignment with a live rubric, and submissions that PUTs update."""

    def __init__(self, post_manually=False, scores=None, posted=None, use_for_grading=False):
        self.assignment = {"id": 20, "name": "Lab 1", "points_possible": 15, "rubric": copy.deepcopy(LIVE_RUBRIC),
                           "rubric_settings": {"id": 900}, "use_rubric_for_grading": use_for_grading,
                           "post_manually": post_manually, "html_url": BASE + "/courses/1/assignments/20"}
        self.subs = {sid: {"user_id": sid, "score": (scores or {}).get(sid), "grade": None, "graded_at": None,
                           "workflow_state": "submitted", "posted_at": (posted or {}).get(sid),
                           "rubric_assessment": None, "user": {"id": sid, "name": "Student %d" % sid}}
                     for sid in (101, 102, 103)}
        self.events = []                 # ordered write log: ("policy", manual) / ("put", sid)
        self.put_behaviour = {}          # sid -> "refuse" | "5xx" | "mismatch" | "raise"
        self.graphql = "ok"              # "ok" | "errors" | "5xx"
        self.fake = FakeCanvas({("GET", "/api/v1/courses/1"): {"id": 1, "name": "Biology"},
                                ("GET", "/api/v1/courses/1/assignments/20"): lambda f, r: (200, copy.deepcopy(self.assignment)),
                                ("POST", "/api/graphql"): self._graphql})
        for sid in self.subs:
            self.fake.route("GET", "/api/v1/courses/1/assignments/20/submissions/%d" % sid,
                            lambda f, r, sid=sid: (200, copy.deepcopy(self.subs[sid])))
            self.fake.route("PUT", "/api/v1/courses/1/assignments/20/submissions/%d" % sid,
                            lambda f, r, sid=sid: self._put(sid, r["body"]))

    def _graphql(self, f, r):
        self.events.append(("policy", r["body"]["variables"]["manual"]))
        if self.graphql == "errors":
            return 200, {"errors": [{"message": "not allowed"}]}
        if self.graphql == "5xx":
            return 503, None
        self.assignment["post_manually"] = r["body"]["variables"]["manual"]
        return 200, {"data": {"setAssignmentPostPolicy": {"postPolicy": {"postManually": True}}}}

    def _put(self, sid, body):
        self.events.append(("put", sid))
        mode = self.put_behaviour.get(sid)
        if mode == "refuse":
            return 400, {"errors": [{"message": "cannot grade"}]}
        if mode == "raise":
            raise KeyboardInterruptLike()
        sub = self.subs[sid]
        assessment = {cid: dict(v, rating_id=None) for cid, v in body["rubric_assessment"].items()}
        if mode == "mismatch":
            assessment[next(iter(assessment))]["points"] = 0.5
        sub["rubric_assessment"] = assessment
        if "submission" in body:
            sub["score"] = body["submission"]["posted_grade"]
        if mode == "5xx":
            return 504, None
        return 200, {"id": sid}

    @property
    def client(self):
        return client_for(self.fake)


class KeyboardInterruptLike(Exception):
    pass


def entry(sid, a=8, b=4, grade=None, **extra):
    e = {"student_id": sid, "criteria": {"_a": {"points": a, "comments": "Good method"}, "_b": {"points": b}}}
    if grade is not None:
        e["grade"] = grade
    e.update(extra)
    return e


# ---------------------------------------------------------------------- rubric validation
def test_definition_becomes_canvas_indexed_shape():
    rubric = rubrics.rubric_definition(RUBRIC_DEF)
    assert list(rubric["criteria"]) == ["0", "1"]
    assert rubric["criteria"]["0"]["ratings"]["1"] == {"description": "Partial", "points": 5}
    assert "free_form_criterion_comments" not in rubric


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.pop("title"), "missing"),
    (lambda d: d.update(extra=1), "unsupported"),
    (lambda d: d.update(criteria=[]), "non-empty"),
    (lambda d: d["criteria"][0].update(points=-1), "negative"),
    (lambda d: d["criteria"][0].update(points=True), "number"),
    (lambda d: d["criteria"][0].update(points=float("nan")), "finite"),
    (lambda d: d["criteria"][0]["ratings"][0].update(points=11), "exceed"),
    (lambda d: d["criteria"][0].update(ratings=[]), "non-empty"),
    (lambda d: d["criteria"][0].update(description="  "), "non-empty string"),
    (lambda d: d["criteria"][0]["ratings"][0].pop("description"), "missing"),
    (lambda d: d.update(criteria=[d["criteria"][0]] * 51), "more than 50"),
])
def test_rubric_definition_validation(mutate, match):
    d = copy.deepcopy(RUBRIC_DEF)
    mutate(d)
    with pytest.raises(RubricError, match=match):
        rubrics.rubric_definition(d)


# ------------------------------------------------------------------------- rubric create
def create_fake(readback=None, attached=None, after_attach=None):
    state = {"assignment": {"id": 20, "name": "Lab 1", "rubric_settings": {"id": attached} if attached else None,
                            "use_rubric_for_grading": False, "html_url": BASE + "/courses/1/assignments/20"}}
    good = {"id": 77, "data": [
        {"id": "_1", "description": "Method", "points": 10.0, "ratings": [
            {"description": "Complete", "points": 10.0}, {"description": "Partial", "points": 5.0},
            {"description": "Missing", "points": 0.0}]},
        {"id": "_2", "description": "Writing", "points": 5.0, "ratings": [
            {"description": "Clear", "points": 5.0}, {"description": "Unclear", "points": 0.0}]}]}

    def post(f, r):
        if r["body"]["rubric_association"]["association_type"] == "Assignment":
            state["assignment"].update(after_attach or {"rubric_settings": {"id": 77}})
        return 200, {"rubric": {"id": 77}, "rubric_association": {"id": 5}}
    return FakeCanvas({("GET", "/api/v1/courses/1"): {"id": 1, "name": "Biology"},
                       ("GET", "/api/v1/courses/1/assignments/20"): lambda f, r: (200, dict(state["assignment"])),
                       ("POST", "/api/v1/courses/1/rubrics"): post,
                       ("GET", "/api/v1/courses/1/rubrics/77"): readback or good})


def test_create_attaches_with_use_for_grading_off_and_reads_back():
    fake = create_fake()
    plan = rubrics.prepare_rubric_create(client_for(fake), 1, RUBRIC_DEF, 20)
    assert fake.writes() == []
    assoc = plan["request"]["body"]["rubric_association"]
    assert assoc == {"association_type": "Assignment", "association_id": 20, "purpose": "grading",
                     "use_for_grading": False}
    result = rubrics.apply_rubric_create(client_for(fake), plan)
    assert result["ok"] and result["outcome"] == "verified" and result["rubric_id"] == 77
    assert result["use_rubric_for_grading"] is False
    assert len(fake.writes()) == 1


def test_create_without_assignment_bookmarks_on_course():
    plan = rubrics.prepare_rubric_create(client_for(create_fake()), 1, RUBRIC_DEF)
    assert plan["request"]["body"]["rubric_association"] == {
        "association_type": "Course", "association_id": 1, "purpose": "bookmark"}


def test_create_refuses_assignment_that_already_has_a_rubric():
    with pytest.raises(RubricError, match="already has rubric 5"):
        rubrics.prepare_rubric_create(client_for(create_fake(attached=5)), 1, RUBRIC_DEF, 20)


def test_create_readback_mismatch_is_uncertain():
    bad = {"id": 77, "data": [{"description": "Method", "points": 10.0, "ratings": []}]}
    fake = create_fake(readback=bad)
    plan = rubrics.prepare_rubric_create(client_for(fake), 1, RUBRIC_DEF)
    result = rubrics.apply_rubric_create(client_for(fake), plan)
    assert result["ok"] is False and result["outcome"] == "uncertain"
    assert result["do_not_retry"] is True and result["error"].startswith("WRITE STATUS UNCERTAIN:")


def test_create_attach_with_grading_on_is_uncertain():
    fake = create_fake(after_attach={"rubric_settings": {"id": 77}, "use_rubric_for_grading": True})
    plan = rubrics.prepare_rubric_create(client_for(fake), 1, RUBRIC_DEF, 20)
    result = rubrics.apply_rubric_create(client_for(fake), plan)
    assert result["outcome"] == "uncertain" and "use this rubric for grading" in result["error"]


# -------------------------------------------------------------------- grading validation
@pytest.mark.parametrize("grades,match", [
    ([entry(101, a=11)], "exceeds the live maximum"),
    ([entry(101, a=-1)], "negative"),
    ([entry(101, a="8")], "number"),
    ([entry(101, grade=-2)], "negative"),
    ([{"student_id": 101, "criteria": {"_zz": {"points": 1}}}], "does not have"),
    ([entry(101), entry(101)], "repeats student_id"),
    ([entry(i) for i in range(1, 52)], "limited to 50"),
    ([], "non-empty"),
    ([entry("abc")], "numeric ID"),
    ([entry(101, extra_field=1)], "unsupported"),
    ([{"student_id": 101, "criteria": {"_a": {"points": 1, "rating": "x"}}}], "unsupported"),
])
def test_grading_validation_refuses_before_any_write(grades, match):
    course = Course()
    with pytest.raises(RubricError, match=match):
        rubrics.prepare_rubric_grading(course.client, 1, 20, grades)
    assert course.fake.writes() == []


def test_live_criterion_without_points_accepts_any_score():
    course = Course()
    course.assignment["rubric"][0]["points"] = None
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, a=11)])
    assert plan["writes"][0]["body"]["rubric_assessment"]["_a"]["points"] == 11


def test_grading_refuses_whole_batch_when_a_student_already_has_a_score():
    course = Course(scores={102: 12})
    with pytest.raises(RubricError, match="already have a score"):
        rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, grade=12), entry(102)])
    assert course.fake.writes() == []


def test_grading_refuses_when_rubric_grades_automatically():
    course = Course(use_for_grading=True)
    with pytest.raises(RubricError, match="use this rubric for grading"):
        rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101)])


def test_preview_shows_everything_the_instructor_reviews():
    course = Course(posted={103: "2026-09-01T00:00:00Z"})
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [
        entry(101, grade=12), entry(102, grade=10), {"student_id": 103, "criteria": {"_c": {"points": 3}}}])
    s = plan["summary"]
    assert s["post_policy"] == {"before": "automatic", "after": "manual", "will_switch_to_manual_first": True}
    one = s["students"][0]
    assert one["student_name"] == "Student 101" and one["current_grade"]["score"] is None
    assert one["criterion_total"] == 12 and one["stated_grade"] == 12
    assert one["criteria"][0] == {"criterion_id": "_a", "description": "Method", "points": 8, "max": 10,
                                  "comments": "Good method"}
    assert one["speedgrader_url"] == BASE + "/courses/1/gradebook/speed_grader?assignment_id=20&student_id=101"
    assert s["students"][1]["difference_from_total"] == -2
    assert s["students"][2]["excluded_from_total"] == ["_c"] and s["students"][2]["criterion_total"] == 0
    assert any("already have posted" in w for w in s["warnings"])     # posted grade warning kept
    assert course.fake.writes() == []


def test_keep_post_policy_warns_everything_is_visible():
    plan = rubrics.prepare_rubric_grading(Course().client, 1, 20, [entry(101)], keep_post_policy=True)
    assert plan["summary"]["post_policy"]["after"] == "automatic"
    assert any("visible to its student the moment" in w for w in plan["summary"]["warnings"])


# ---------------------------------------------------------------------------- grading run
def test_switches_to_manual_posting_before_the_first_grade_and_verifies_each():
    course = Course()
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, grade=12), entry(102)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert course.events == [("policy", True), ("put", 101), ("put", 102)]
    assert report["outcome"] == "verified" and report["written"] == [101, 102]
    assert report["post_policy_switched"] is True and report["grades_not_yet_visible"] == 1
    assert "Post grades" in report["release"]


def test_already_manual_assignment_is_not_switched():
    course = Course(post_manually=True)
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, grade=12)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert ("policy", True) not in course.events and report["outcome"] == "verified"


def test_keep_post_policy_never_touches_the_policy():
    course = Course()
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, grade=12)], keep_post_policy=True)
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert course.events == [("put", 101)] and report["already_visible"] == 1


def test_post_policy_refusal_writes_no_student():
    course = Course()
    course.graphql = "errors"
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101)])
    with pytest.raises(RubricError, match="refused the switch"):
        rubrics.apply_rubric_grading(course.client, plan)
    assert [e for e in course.events if e[0] == "put"] == []


def test_uncertain_post_policy_switch_stops_before_students():
    course = Course()
    course.graphql = "5xx"
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert report["outcome"] == "uncertain" and report["written"] == [] and report["not_attempted"] == [101]
    assert [e for e in course.events if e[0] == "put"] == []


def test_refused_student_write_stops_and_reports_partial_batch():
    course = Course()
    course.put_behaviour[102] = "refuse"
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101), entry(102), entry(103)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert report["outcome"] == "stopped" and report["written"] == [101]
    assert report["not_attempted"] == [102, 103] and report["do_not_retry"] is True


@pytest.mark.parametrize("mode", ["5xx", "mismatch", "raise"])
def test_uncertain_student_write_is_never_retried(mode):
    course = Course()
    course.put_behaviour[102] = mode
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101), entry(102, grade=12), entry(103)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert report["outcome"] == "uncertain" and report["uncertain_student"] == 102
    assert report["written"] == [101] and report["not_attempted"] == [103]
    assert "UNCERTAIN" in report["error"] and report["do_not_retry"] is True
    assert course.events.count(("put", 102)) == 1 and ("put", 103) not in course.events


def test_non_object_submission_readback_after_grade_is_uncertain():
    course = Course(post_manually=True)
    path = "/api/v1/courses/1/assignments/20/submissions/101"
    course.fake.route("GET", path, lambda f, r: (
        (200, [1]) if course.subs[101]["rubric_assessment"] is not None
        else (200, copy.deepcopy(course.subs[101]))))
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101, grade=12)])
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert course.events == [("put", 101)]
    assert report["outcome"] == "uncertain" and report["uncertain_student"] == 101
    assert report["do_not_retry"] is True and "WRITE STATUS UNCERTAIN" in report["error"]


def test_rubric_changed_after_preview_writes_nothing():
    course = Course()
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101)])
    course.assignment["rubric"][0]["points"] = 20
    with pytest.raises(RubricError, match="rubric changed"):
        rubrics.apply_rubric_grading(course.client, plan)
    assert course.fake.writes() == []


def test_student_scored_after_preview_stops_before_their_write():
    course = Course()
    plan = rubrics.prepare_rubric_grading(course.client, 1, 20, [entry(101), entry(102)])
    course.subs[102]["score"] = 9
    report = rubrics.apply_rubric_grading(course.client, plan)
    assert report["outcome"] == "stopped" and report["written"] == [101] and report["not_attempted"] == [102]
    assert ("put", 102) not in course.events
