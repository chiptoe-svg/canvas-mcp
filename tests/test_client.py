import http.server
import threading
import urllib.request

import pytest

import canvas_client
from canvas_client import CanvasError, build_url, next_page_url, normalize_path
from fake_canvas import BASE, TOKEN, FakeCanvas, client_for, link


@pytest.mark.parametrize("raw,expected", [
    ("courses", "/api/v1/courses"),
    ("/courses/1/assignments", "/api/v1/courses/1/assignments"),
    ("api/v1/courses/1", "/api/v1/courses/1"),
    ("/api/v1/users/self", "/api/v1/users/self"),
    ("courses/sis_course_id:ABC-101", "/api/v1/courses/sis_course_id:ABC-101"),
])
def test_normalizes_relative_api_paths(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize("raw", [
    "https://evil.com/api/v1/courses", "http://canvas.example.edu/api/v1/courses",
    "//evil.com/api/v1/courses", "evil.com:443/courses",
    "courses/../../admin", "courses/./1", "../api/v1", "courses//1",
    "courses/%2e%2e/admin", "courses%2F1", "courses/1%00", "courses\\1", "courses/ 1",
    "courses/1?per_page=100", "courses/1#x", "courses/\n1",
    "api/graphql", "api/v2/courses", "/api/lti/x",
    "users/self/tokens", "users/1/tokens/5", "developer_keys", "accounts/1/developer_keys",
    "users/self/tokens.json", "users/1/tokens/5.json", "developer_keys.json",
    "accounts/1/developer_keys.json", "login.json", "inst_access_tokens", "inst_access_tokens.json",
    "", "   ",
])
def test_refuses_escapes_and_credential_paths(raw):
    with pytest.raises(CanvasError):
        normalize_path(raw)


def test_build_url_is_pinned_and_encodes_query_only():
    url = build_url(BASE, "courses", {"include[]": ["term", "total_students"], "per_page": 100,
                                      "enrollment_state": "active"})
    assert url.startswith(BASE + "/api/v1/courses?")
    assert "include%5B%5D=term" in url and "per_page=100" in url


def test_query_values_must_be_scalars():
    with pytest.raises(CanvasError):
        build_url(BASE, "courses", {"x": {"nested": 1}})


def test_token_goes_only_in_the_authorization_header():
    fake = FakeCanvas({("GET", "/api/v1/users/self"): {"id": 1, "name": "Ada"}})
    client = client_for(fake)
    assert client.get("users/self")["id"] == 1
    req = fake.requests[0]
    assert req["headers"]["Authorization"] == "Bearer " + TOKEN
    assert TOKEN not in req["url"]
    assert TOKEN not in repr(client)


def test_pagination_follows_same_host_next_links():
    fake = FakeCanvas()
    fake.route("GET", "/api/v1/courses", lambda f, r: (
        (200, [{"id": 1}, {"id": 2}], link(BASE + "/api/v1/courses?page=2&per_page=2"))
        if "page" not in r["query"] else
        (200, [{"id": 3}], {}) if r["query"]["page"] == ["2"] else (500, None)))
    items, pages = client_for(fake).get_all("courses", {"per_page": 2})
    assert [i["id"] for i in items] == [1, 2, 3] and pages == 2
    assert all(r["host"] == "canvas.example.edu" for r in fake.requests)


@pytest.mark.parametrize("next_url", [
    "https://evil.com/api/v1/courses?page=2",
    "http://canvas.example.edu/api/v1/courses?page=2",
    "https://canvas.example.edu:8443/api/v1/courses?page=2",
    "https://user@canvas.example.edu/api/v1/courses?page=2",
    "https://canvas.example.edu/api/graphql?page=2",
    "https://canvas.example.edu/api/v1/users/self/tokens?page=2",
    "https://canvas.example.edu/api/v1/courses/%2e%2e/x?page=2",
])
def test_pagination_refuses_off_host_or_off_api_links(next_url):
    fake = FakeCanvas({("GET", "/api/v1/courses"): lambda f, r: (200, [{"id": 1}], link(next_url))})
    with pytest.raises(CanvasError):
        client_for(fake).get_all("courses")
    assert len(fake.requests) == 1              # the bad link was never requested


def test_pagination_detects_loops_and_caps_pages():
    fake = FakeCanvas({("GET", "/api/v1/courses"): lambda f, r: (200, [{"id": 1}], link(BASE + "/api/v1/courses"))})
    with pytest.raises(CanvasError, match="loop"):
        client_for(fake).get_all("courses")
    counter = {"n": 0}

    def endless(f, r):
        counter["n"] += 1
        return 200, [{"id": counter["n"]}], link(BASE + "/api/v1/courses?page=%d" % (counter["n"] + 1))
    fake = FakeCanvas({("GET", "/api/v1/courses"): endless})
    with pytest.raises(CanvasError, match="more than 3 pages"):
        client_for(fake).get_all("courses", page_cap=3)


def test_next_page_url_none_without_link():
    assert next_page_url(None, BASE) is None
    assert next_page_url('<%s/api/v1/x?page=1>; rel="prev"' % BASE, BASE) is None


def test_4xx_write_is_refused_but_5xx_write_is_uncertain():
    fake = FakeCanvas({("PUT", "/api/v1/courses/1"): (400, {"errors": [{"message": "bad date"}]}),
                       ("POST", "/api/v1/courses/1/pages"): (502, None)})
    client = client_for(fake)
    with pytest.raises(CanvasError) as refused:
        client.request("PUT", "courses/1", body={"course": {"x": 1}})
    assert refused.value.outcome == "refused" and "bad date" in str(refused.value)
    with pytest.raises(CanvasError) as unsure:
        client.request("POST", "courses/1/pages", body={"wiki_page": {"title": "t"}})
    assert unsure.value.outcome == "uncertain"


def test_401_names_reconnect_and_never_echoes_the_token():
    fake = FakeCanvas({("GET", "/api/v1/courses"): (401, {"errors": [{"message": "Invalid token " + TOKEN}]})})
    with pytest.raises(CanvasError) as err:
        client_for(fake).get("courses")
    assert "reconnect" in str(err.value) and TOKEN not in str(err.value)


def test_error_body_echoing_the_token_is_scrubbed():
    fake = FakeCanvas({("GET", "/api/v1/courses/9"): (403, {"errors": [{"message": "token " + TOKEN + " lacks scope"}]})})
    with pytest.raises(CanvasError) as err:
        client_for(fake).get("courses/9")
    assert TOKEN not in str(err.value) and "<redacted>" in str(err.value)


class _Redirector(http.server.BaseHTTPRequestHandler):
    seen_auth = []

    def do_GET(self):
        self.seen_auth.append(self.headers.get("Authorization"))
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/steal")
        self.end_headers()

    def log_message(self, *a):
        pass


def test_the_real_opener_refuses_redirects():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        opener = canvas_client._default_opener()
        req = urllib.request.Request("http://127.0.0.1:%d/api/v1/x" % server.server_port,
                                     headers={"Authorization": "Bearer t"})
        with pytest.raises(CanvasError, match="redirect"):
            opener.open(req, timeout=5)
        assert len(_Redirector.seen_auth) == 1       # the Location was never requested
    finally:
        server.shutdown()


def test_redirect_on_a_write_is_uncertain():
    class RedirectingOpener:
        def open(self, req, timeout=None):
            raise CanvasError("Canvas answered with a redirect (302)", status=302)
    client = canvas_client.CanvasClient(BASE, lambda: TOKEN, opener=RedirectingOpener())
    with pytest.raises(CanvasError) as err:
        client.request("POST", "courses/1/pages", body={"a": 1})
    assert err.value.outcome == "uncertain"
