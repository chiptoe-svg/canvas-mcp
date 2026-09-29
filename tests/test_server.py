"""The generic tools, through the real MCP protocol (in-process SDK client) where it matters."""

import json

import anyio
import pytest
from mcp import Client, types

import canvas_mcp
from canvas_mcp import CONFIRM_SCHEMA, PreviewStore, Tools
from fake_canvas import BASE, TOKEN, FakeCanvas, client_for, link


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def course_fake():
    return FakeCanvas({
        ("GET", "/api/v1/courses"): lambda f, r: (
            (200, [{"id": 1, "name": "Bio 101", "secret_field": "x"}], link(BASE + "/api/v1/courses?page=2"))
            if "page" not in r["query"] else (200, [{"id": 2, "name": "Chem 201"}], {})),
        ("GET", "/api/v1/courses/1/pages/syllabus"): {"id": 7, "title": "Syllabus", "body": "<p>long</p>"},
        ("PUT", "/api/v1/courses/1/pages/syllabus"): lambda f, r: (200, {"id": 7, "title": r["body"]["wiki_page"]["title"]}),
        ("POST", "/api/v1/courses/1/discussion_topics"): (200, {"id": 55, "title": "Week 3", "published": False}),
        ("DELETE", "/api/v1/courses/1/pages/syllabus"): (502, None),
    })


def tools_for(fake, writes="confirm", clock=None):
    return Tools(lambda: client_for(fake), writes=writes, store=PreviewStore(ttl=600, clock=clock or Clock()))


def call(server, name, args, elicit=None, mode="legacy"):
    """Call a tool over the MCP protocol; return (structured result, elicitation params seen)."""
    seen = []

    async def on_elicit(context, params):
        seen.append(params)
        return elicit(params) if elicit else types.ElicitResult(action="decline")

    async def run():
        async with Client(server, mode=mode, elicitation_callback=on_elicit if elicit is not False else None) as c:
            res = await c.call_tool(name, args)
            return res.structured_content if res.structured_content is not None else json.loads(res.content[0].text)
    return anyio.run(run), seen


ACCEPT = lambda p: types.ElicitResult(action="accept", content={"decision": "apply"})


# ------------------------------------------------------------------------------------ reads
def test_read_all_pages_with_field_projection():
    t = tools_for(course_fake())
    out = t.read("courses", {"per_page": 100}, all_pages=True, fields=["id", "name"])
    assert out == {"ok": True, "path": "/api/v1/courses", "count": 2, "pages": 2,
                   "items": [{"id": 1, "name": "Bio 101"}, {"id": 2, "name": "Chem 201"}]}


def test_read_single_page_reports_more_pages():
    out = tools_for(course_fake()).read("courses")
    assert out["count"] == 1 and out["more_pages"] is True


def test_read_refuses_off_host_before_any_request():
    fake = course_fake()
    out = tools_for(fake).read("https://evil.example.com/api/v1/courses")
    assert out["ok"] is False and fake.requests == []


def test_empty_fields_list_is_refused():
    out = tools_for(course_fake()).read("courses", fields=[])
    assert out["ok"] is False


# ----------------------------------------------------------------------------- previews
def test_prepare_makes_no_mutation_and_shows_exact_request():
    fake = course_fake()
    out = tools_for(fake).prepare_write("put", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})
    assert out["ok"] and out["preview_id"].startswith("pv_")
    assert out["method"] == "PUT" and out["url"] == BASE + "/api/v1/courses/1/pages/syllabus"
    assert out["body"] == {"wiki_page": {"title": "New"}}
    assert out["current_target"] == {"id": 7, "title": "Syllabus"}      # summary only, no page body
    assert fake.writes() == []


@pytest.mark.parametrize("method,path,body", [
    ("GET", "courses/1", {"a": 1}), ("POST", "courses/1/pages", None), ("POST", "courses/1/pages", {}),
    ("PUT", "https://evil.com/api/v1/courses/1", {"a": 1}), ("DELETE", "courses/1/pages/x", {"a": 1}),
    ("POST", "users/self/tokens", {"token": {"purpose": "x"}}),
])
def test_prepare_refuses_bad_requests(method, path, body):
    fake = course_fake()
    out = tools_for(fake).prepare_write(method, path, body)
    assert out["ok"] is False and fake.writes() == []


def test_preview_is_one_time_and_expires():
    clock = Clock()
    store = PreviewStore(ttl=600, clock=clock)
    pid, _ = store.put("write", {"x": 1}, "canvas.example.edu")
    assert store.take(pid, "write", "canvas.example.edu") == {"x": 1}
    with pytest.raises(canvas_mcp.PreviewError, match="already-used"):
        store.take(pid, "write", "canvas.example.edu")
    pid2, _ = store.put("write", {"x": 2}, "canvas.example.edu")
    clock.now += 601
    with pytest.raises(canvas_mcp.PreviewError, match="expired"):
        store.take(pid2, "write", "canvas.example.edu")


def test_preview_kind_and_host_are_bound():
    store = PreviewStore()
    pid, _ = store.put("rubric_grading", {"x": 1}, "canvas.example.edu")
    with pytest.raises(canvas_mcp.PreviewError, match="not write"):
        store.take(pid, "write", "canvas.example.edu")
    pid, _ = store.put("write", {"x": 1}, "canvas.example.edu")
    with pytest.raises(canvas_mcp.PreviewError, match="another Canvas host"):
        store.take(pid, "write", "other.example.edu")


def test_preview_payload_cannot_be_changed_after_prepare():
    fake = course_fake()
    t = tools_for(fake)
    body = {"wiki_page": {"title": "New"}}
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", body)["preview_id"]
    body["wiki_page"]["title"] = "Tampered"
    out, _ = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert out["ok"] and fake.writes()[0]["body"] == {"wiki_page": {"title": "New"}}


# --------------------------------------------------------------------- apply + approval
def test_writes_off_refuses_and_sends_nothing():
    fake = course_fake()
    t = tools_for(fake, writes="off")
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    out, seen = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert out["ok"] is False and "disabled" in out["error"] and seen == [] and fake.writes() == []


def test_confirm_applies_exactly_the_preview_once_after_explicit_accept():
    fake = course_fake()
    t = tools_for(fake)
    server = canvas_mcp.build_server(t)
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    out, seen = call(server, "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert out["ok"] and out["outcome"] == "applied" and out["status"] == 200
    assert len(fake.writes()) == 1 and fake.writes()[0]["method"] == "PUT"
    assert seen[0].message == (
        "Apply the Canvas change you just reviewed?\n\n"
        "This will update page syllabus in course 1.\n"
        "Set wiki page title to “New”.\n\n"
        "Nothing has been sent yet."
    )
    assert "PUT" not in seen[0].message and "{" not in seen[0].message
    again, _ = call(server, "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert again["ok"] is False and "already-used" in again["error"] and len(fake.writes()) == 1


def test_approval_message_is_plain_language_and_bound_to_the_actual_request():
    message = canvas_mcp._approval_text({
        "method": "PUT",
        "path": "courses/293855/quizzes/682181/questions/10393750",
        "body": {"question": {"points_possible": 2}},
    })
    assert message == (
        "Apply the Canvas change you just reviewed?\n\n"
        "This will update question 10393750 in quiz 682181 in course 293855.\n"
        "Set question points possible to 2.\n\n"
        "Nothing has been sent yet."
    )
    assert all(mark not in message for mark in ("PUT", "https://", "/api/v1", "{", "}", "[", "]"))


def test_elicitation_schema_is_codex_compatible():
    """Codex cancels a schema with a top-level title; the decision must be explicit."""
    assert "title" not in CONFIRM_SCHEMA and CONFIRM_SCHEMA["required"] == ["decision"]
    decision = CONFIRM_SCHEMA["properties"]["decision"]
    assert decision["default"] == "do_not_apply"
    assert decision["oneOf"] == [
        {"const": "do_not_apply", "title": "Do not apply"},
        {"const": "apply", "title": "Apply this exact change"},
    ]
    fake = course_fake()
    t = tools_for(fake)
    pid = t.prepare_write("POST", "courses/1/discussion_topics", {"title": "Week 3"})["preview_id"]
    _, seen = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    sent = seen[0].requested_schema
    sent = sent.model_dump(by_alias=True, exclude_none=True) if hasattr(sent, "model_dump") else sent
    assert "title" not in sent and sent["required"] == ["decision"]
    assert sent["properties"]["decision"]["default"] == "do_not_apply"
    assert sent["properties"]["decision"]["oneOf"][0]["const"] == "do_not_apply"


@pytest.mark.parametrize("answer", [
    types.ElicitResult(action="decline"),
    types.ElicitResult(action="cancel"),
    types.ElicitResult(action="accept", content={"decision": "do_not_apply"}),
    types.ElicitResult(action="accept", content={}),
    types.ElicitResult(action="accept", content={"decision": True}),
    types.ElicitResult(action="accept", content={"decision": "APPLY"}),
])
def test_anything_but_explicit_accept_refuses_and_consumes(answer):
    fake = course_fake()
    t = tools_for(fake)
    server = canvas_mcp.build_server(t)
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    out, _ = call(server, "canvas_apply_write", {"preview_id": pid}, elicit=lambda p: answer)
    assert out["ok"] is False and out["outcome"] == "refused" and fake.writes() == []
    again, _ = call(server, "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert again["ok"] is False and fake.writes() == []


def test_client_without_elicitation_fails_closed():
    fake = course_fake()
    t = tools_for(fake)
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    out, _ = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=False)
    assert out["ok"] is False and "elicitation" in out["error"] and fake.writes() == []


def test_uncertain_write_is_labelled_and_not_retried():
    fake = course_fake()
    t = tools_for(fake)
    pid = t.prepare_write("DELETE", "courses/1/pages/syllabus", None)["preview_id"]
    out, _ = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert out["outcome"] == "uncertain" and out["do_not_retry"] is True
    assert out["error"].startswith("WRITE STATUS UNCERTAIN")
    assert len([w for w in fake.writes() if w["method"] == "DELETE"]) == 1


def test_expired_preview_is_refused_over_mcp():
    fake, clock = course_fake(), Clock()
    t = tools_for(fake, clock=clock)
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    clock.now += 10_000
    out, seen = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid}, elicit=ACCEPT)
    assert out["ok"] is False and "expired" in out["error"] and fake.writes() == [] and seen == []


def test_modern_protocol_mode_also_gates():
    fake = course_fake()
    t = tools_for(fake)
    pid = t.prepare_write("PUT", "courses/1/pages/syllabus", {"wiki_page": {"title": "New"}})["preview_id"]
    out, _ = call(canvas_mcp.build_server(t), "canvas_apply_write", {"preview_id": pid},
                  elicit=lambda p: types.ElicitResult(action="decline"), mode="auto")
    assert out["ok"] is False and fake.writes() == []


def test_confirmation_check_uses_same_gate_without_contacting_canvas():
    fake = course_fake()
    server = canvas_mcp.build_server(tools_for(fake))

    out, seen = call(server, "canvas_test_confirmation", {}, elicit=ACCEPT)

    assert out == {"ok": True, "outcome": "confirmed", "canvas_changed": False}
    assert len(seen) == 1 and "test only" in seen[0].message.lower()
    assert fake.requests == []


def test_confirmation_check_decline_is_safe_and_writes_off_does_not_prompt():
    fake = course_fake()
    declined, _ = call(canvas_mcp.build_server(tools_for(fake)),
                       "canvas_test_confirmation", {},
                       elicit=lambda p: types.ElicitResult(action="decline"))
    disabled, seen = call(canvas_mcp.build_server(tools_for(fake, writes="off")),
                          "canvas_test_confirmation", {}, elicit=ACCEPT)

    assert declined["ok"] is False and disabled["ok"] is False
    assert seen == [] and fake.requests == []


# -------------------------------------------------------------------------- token secrecy
def test_token_never_appears_in_any_tool_output():
    leaky = FakeCanvas({
        ("GET", "/api/v1/courses"): (200, [{"id": 1, "note": "token=" + TOKEN}]),
        ("GET", "/api/v1/courses/1/pages/p"): (400, {"errors": [{"message": "bad " + TOKEN}]}),
        ("PUT", "/api/v1/courses/1/pages/p"): (200, {"id": 1, "echo": TOKEN}),
    })
    t = tools_for(leaky)
    server = canvas_mcp.build_server(t)
    outputs = [t.read("courses"), t.read("courses/1/pages/p"),
               t.prepare_write("PUT", "courses/1/pages/p", {"wiki_page": {"title": TOKEN}})]
    out, seen = call(server, "canvas_apply_write", {"preview_id": outputs[-1]["preview_id"]}, elicit=ACCEPT)
    outputs.append(out)
    text = json.dumps(outputs) + "".join(p.message for p in seen)
    assert TOKEN not in text


def test_tool_annotations_are_accurate():
    server = canvas_mcp.build_server(tools_for(course_fake()), enable_rubric_grading=True)
    tools = {t.name: t.annotations for t in anyio.run(server.list_tools)}
    for name in ("canvas_apply_write", "apply_rubric_create", "apply_rubric_grading"):
        assert tools[name].destructive_hint is True and tools[name].read_only_hint is False
    for name in ("canvas_read", "canvas_prepare_write", "prepare_rubric_create", "prepare_rubric_grading"):
        assert tools[name].read_only_hint is True and tools[name].destructive_hint is False


def test_rubric_creation_is_standard_but_grading_is_opt_in():
    names = {t.name for t in anyio.run(canvas_mcp.build_server(tools_for(course_fake())).list_tools)}
    assert names == {"canvas_read", "canvas_prepare_write", "canvas_apply_write",
                     "canvas_test_confirmation", "prepare_rubric_create", "apply_rubric_create"}
    assert "prepare_rubric_grading" not in names
    assert "apply_rubric_grading" not in names


def test_standard_rubric_create_still_refuses_when_server_writes_are_off():
    fake = FakeCanvas({("GET", "/api/v1/courses/1"): {"id": 1, "name": "Bio 101"}})
    server = canvas_mcp.build_server(tools_for(fake, writes="off"))
    definition = {"title": "Simple", "criteria": [{"description": "Quality", "points": 5,
                  "ratings": [{"description": "Complete", "points": 5},
                              {"description": "Missing", "points": 0}]}]}
    preview, _ = call(server, "prepare_rubric_create", {"course_id": 1, "definition": definition})
    assert preview["ok"], preview
    out, seen = call(server, "apply_rubric_create", {"preview_id": preview["preview_id"]}, elicit=ACCEPT)
    assert out["ok"] is False and "disabled" in out["error"]
    assert seen == [] and fake.writes() == []


def test_rubric_create_tools_call_the_implementations_over_mcp():
    from test_rubrics import RUBRIC_DEF, create_fake

    fake = create_fake()
    server = canvas_mcp.build_server(tools_for(fake))
    preview, _ = call(server, "prepare_rubric_create", {"course_id": 1, "definition": RUBRIC_DEF})
    result, seen = call(server, "apply_rubric_create", {"preview_id": preview["preview_id"]}, elicit=ACCEPT)
    assert result["ok"] and result["rubric_id"] == 77
    assert len(seen) == 1 and len(fake.writes()) == 1


def test_malformed_rubric_readback_after_write_is_uncertain_and_never_retryable():
    from test_rubrics import RUBRIC_DEF, create_fake

    fake = create_fake(readback={"id": 77, "data": [None, None]})
    server = canvas_mcp.build_server(tools_for(fake))
    preview, _ = call(server, "prepare_rubric_create", {"course_id": 1, "definition": RUBRIC_DEF})
    result, _ = call(server, "apply_rubric_create", {"preview_id": preview["preview_id"]}, elicit=ACCEPT)
    assert len(fake.writes()) == 1
    assert result["ok"] is False and result["outcome"] == "uncertain"
    assert result["do_not_retry"] is True and result["error"].startswith("WRITE STATUS UNCERTAIN:")


def test_rubric_grading_tools_call_the_implementations_over_mcp():
    from test_rubrics import Course, entry

    course = Course(post_manually=True)
    server = canvas_mcp.build_server(tools_for(course.fake), enable_rubric_grading=True)
    preview, _ = call(server, "prepare_rubric_grading",
                      {"course_id": 1, "assignment_id": 20, "grades": [entry(101, grade=12)]})
    result, seen = call(server, "apply_rubric_grading", {"preview_id": preview["preview_id"]}, elicit=ACCEPT)
    assert result["ok"] and result["written"] == [101]
    assert len(seen) == 1


def test_default_write_mode_is_off():
    import inspect
    assert inspect.signature(Tools).parameters["writes"].default == "off"


@pytest.mark.parametrize("flag", ["--enable-rubric-grading", "--enable-rubrics"])
def test_current_and_legacy_grading_flags_enable_only_grading(flag, monkeypatch, capsys):
    captured = {}

    class Server:
        def run(self, transport):
            captured["transport"] = transport

    def fake_build(tools, enable_rubric_grading=False):
        captured["enabled"] = enable_rubric_grading
        return Server()

    monkeypatch.setattr(canvas_mcp, "build_server", fake_build)
    assert canvas_mcp.main([flag]) == 0
    assert captured == {"enabled": True, "transport": "stdio"}
    warning = capsys.readouterr().err
    assert ("deprecated" in warning) is (flag == "--enable-rubrics")
