"""A small Canvas REST client locked to one HTTPS host and to /api/v1.

Every URL is built by ``build_url``. It accepts only a relative Canvas API path and refuses
absolute URLs, other hosts, path traversal, percent-encoded paths, backslashes and control
characters. Redirects are never followed, because following one would forward the
Authorization header. Pagination follows only ``rel="next"`` links on the same host under
/api/v1, bounded by a page cap and loop detection.

The token is read from the OS credential store at request time and used only for the
Authorization header. ``scrub`` removes it from any text that could reach the model.

Adapted from canvas-api-guard's canvas_api_guard.py (normalise_path, canvas_url,
RefuseRedirects, next_link, project, error_messages), without its installer, audit log,
provenance checks or CLI.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

import config

API_PREFIX = "/api/v1"
TIMEOUT = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_BODY_BYTES = 256 * 1024
PAGE_CAP = 50
USER_AGENT = "canvas-mcp/0.1.4"
READ_METHODS = ("GET",)
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Credential-bearing endpoints. Creating or listing access tokens or developer keys through
# the model would put a secret in model-visible output, so they are refused for every method.
# Each rule also matches a format suffix ("tokens.json"), which Canvas's routes accept.
# Edit this list if you know what you are doing; the tests document why each entry is here.
DENIED_PATHS = (
    re.compile(r"^/api/v1/users/[^/]+/tokens([/.]|$)"),
    re.compile(r"^/api/v1/(accounts/[^/]+/)?developer_keys([/.]|$)"),
    re.compile(r"^/api/v1/login([/.]|$)"),
    re.compile(r"^/api/v1/inst_access_tokens([/.]|$)"),
)

# The one non-/api/v1 request in this project: Canvas has no REST route for an assignment's
# grade post policy, so the rubric extension sends this fixed GraphQL document and nothing
# else. The generic tools cannot reach it: build_url always prefixes /api/v1.
GRAPHQL_PATH = "/api/graphql"
POST_POLICY_MUTATION = ("mutation ($id: ID!, $manual: Boolean!) { "
                        "setAssignmentPostPolicy(input: {assignmentId: $id, postManually: $manual}) "
                        "{ postPolicy { postManually } } }")

_SEGMENT = re.compile(r"^[A-Za-z0-9_\-.:~@!$&'()*+,;=]+$")


class CanvasError(Exception):
    """A request failed. ``outcome`` says whether a write could have been applied:

    * ``refused``   - not sent, or Canvas answered 4xx: nothing was applied.
    * ``uncertain`` - a write was sent and its result is unknown (timeout, 5xx, transport
      failure, unreadable response). Never retry automatically: inspect Canvas first.
    """

    def __init__(self, message: str, status: int | None = None, outcome: str = "refused"):
        super().__init__(message)
        self.status = status
        self.outcome = outcome

    def as_dict(self) -> dict:
        return {"ok": False, "outcome": self.outcome, "status": self.status, "error": str(self)}


# ------------------------------------------------------------------------------ path locking
def normalize_path(path: str) -> str:
    """'courses/1', '/courses/1', 'api/v1/courses/1', '/api/v1/courses/1' -> '/api/v1/courses/1'.

    Query parameters go in the separate ``query`` argument, never in the path.
    """
    if not isinstance(path, str):
        raise CanvasError("path must be a string")
    raw = path.strip()
    if not raw:
        raise CanvasError("path is empty")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw) or "\\" in raw:
        raise CanvasError("path contains whitespace, control characters or a backslash")
    if "?" in raw or "#" in raw:
        raise CanvasError("put query parameters in `query`, not in the path")
    if "%" in raw:
        raise CanvasError("percent-encoding is not allowed in the path")
    if "://" in raw or raw.startswith("//") or ":" in raw.split("/")[0]:
        raise CanvasError("path must be relative to the Canvas API, not a URL")
    segments = [s for s in raw.strip("/").split("/")]
    if any(s == "" for s in segments):
        raise CanvasError("path must not contain empty segments ('//')")
    if any(s in (".", "..") for s in segments):
        raise CanvasError("path must not contain '.' or '..' segments")
    if not all(_SEGMENT.match(s) for s in segments):
        raise CanvasError("path contains characters that are not allowed in a Canvas API path")
    if segments[:2] == ["api", "v1"]:
        segments = segments[2:]
    elif segments[0] == "api":
        raise CanvasError("only the Canvas REST API under /api/v1 is reachable")
    if not segments:
        raise CanvasError("path names no Canvas resource")
    npath = API_PREFIX + "/" + "/".join(segments)
    for rule in DENIED_PATHS:
        if rule.match(npath):
            raise CanvasError("%s is refused: it can create or reveal credentials" % npath)
    return npath


def encode_query(query: dict | None) -> str:
    """Encode {"include[]": ["user", "term"], "per_page": 100} as a query string."""
    if query is None:
        return ""
    if not isinstance(query, dict):
        raise CanvasError("query must be an object of parameter -> value or list of values")
    pairs = []
    for key, value in query.items():
        if not isinstance(key, str) or not key:
            raise CanvasError("query parameter names must be non-empty strings")
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, bool):
                item = "true" if item else "false"
            if item is None or not isinstance(item, (str, int, float)):
                raise CanvasError("query value for %r must be a string, number or boolean" % key)
            pairs.append((key, str(item)))
    return urllib.parse.urlencode(pairs)


def build_url(base_url: str, path: str, query: dict | None = None) -> str:
    """The only builder for /api/v1 URLs. Pins the scheme, the host and the prefix."""
    base = config.normalize_base_url(base_url)
    npath = normalize_path(path)
    qs = encode_query(query)
    url = base + npath + (("?" + qs) if qs else "")
    check = urllib.parse.urlsplit(url)
    if (check.scheme != "https" or check.netloc != config.host_of(base)
            or not check.path.startswith(API_PREFIX + "/")):
        raise CanvasError("refusing a URL that leaves the configured Canvas host")
    return url


def next_page_url(link_header: str | None, base_url: str) -> str | None:
    """The rel="next" URL from a Link header, only if it stays on the host under /api/v1."""
    if not link_header:
        return None
    for part in link_header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;(.*)$', part)
        if match and re.search(r'rel="?next"?', match.group(2)):
            url = match.group(1)
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != config.host_of(base_url)
                    or parsed.username or parsed.password or parsed.fragment):
                raise CanvasError("next-page link leaves the configured Canvas host; stopped")
            if "%" in parsed.path or not parsed.path.startswith(API_PREFIX + "/"):
                raise CanvasError("next-page link is not a plain /api/v1 path; stopped")
            normalize_path(parsed.path)               # same traversal/deny checks
            return url
    return None


# ------------------------------------------------------------------------------- projection
def field_value(obj: Any, dotted: str) -> Any:
    value = obj
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def project(data: Any, fields: list[str] | None) -> Any:
    """Keep only the named dot-path fields, in order. A missing field is present and null."""
    if fields is None:
        return data
    if not isinstance(fields, list) or not fields or not all(
            isinstance(f, str) and f.strip() for f in fields):
        raise CanvasError("fields must be a non-empty list of field names, e.g. [\"id\", \"name\"]")
    fields = [f.strip() for f in fields]
    if isinstance(data, list):
        return [{f: field_value(item, f) for f in fields} for item in data]
    if isinstance(data, dict):
        return {f: field_value(data, f) for f in fields}
    return data


# ---------------------------------------------------------------------------------- errors
def error_messages(value: Any) -> list[str]:
    if isinstance(value, dict):
        if "message" in value:
            return [str(value["message"])]
        found = []
        for key, inner in value.items():
            found.extend("%s: %s" % (key, m) for m in error_messages(inner))
        return found
    if isinstance(value, list):
        found = []
        for inner in value:
            found.extend(error_messages(inner))
        return found
    return [str(value)] if value not in (None, "") else []


def canvas_reason(body: bytes) -> str:
    text = " ".join(body.decode("utf-8", "replace").split())
    if not text:
        return ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            text = "; ".join(error_messages(data.get("errors", data.get("message")))) or text
    except ValueError:
        pass
    return text[:300] + ("..." if len(text) > 300 else "")


# ------------------------------------------------------------------------------- transport
class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CanvasError("Canvas answered with a redirect (%s); redirects are never followed "
                          "because they would carry the token elsewhere" % code, status=code)


@dataclass
class Response:
    status: int
    headers: dict
    data: Any


def _default_opener():
    return urllib.request.build_opener(_RefuseRedirects)


@dataclass
class CanvasClient:
    """Canvas API access for one configured host.

    ``token_reader`` is called per request and its value is used only for the Authorization
    header. Tests pass a fake reader and a fake opener; nothing else changes.
    """
    base_url: str
    token_reader: Callable[[], str]
    opener: Any = field(default_factory=_default_opener)

    def __post_init__(self):
        self.base_url = config.normalize_base_url(self.base_url)

    @property
    def host(self) -> str:
        return config.host_of(self.base_url)

    def __repr__(self):
        return "CanvasClient(base_url=%r)" % self.base_url

    @classmethod
    def from_settings(cls, settings: config.Settings | None = None) -> "CanvasClient":
        settings = settings or config.load_settings()
        return cls(settings.base_url, lambda: config.read_token(settings.host))

    # -- secrecy ------------------------------------------------------------------------
    def scrub(self, value: Any) -> Any:
        """Remove the live token from any string inside value (defence in depth)."""
        try:
            token = self.token_reader()
        except Exception:
            return value
        return _scrub(value, token)

    # -- one request ----------------------------------------------------------------------
    def request(self, method: str, path: str, query: dict | None = None, body: Any = None,
                *, url: str | None = None, graphql: bool = False) -> Response:
        method = method.upper()
        if method not in READ_METHODS + WRITE_METHODS:
            raise CanvasError("method must be one of %s" % ", ".join(READ_METHODS + WRITE_METHODS))
        if graphql:
            target = self.base_url + GRAPHQL_PATH
        elif url is not None:
            target = url                               # a next_page_url() result only
        else:
            target = build_url(self.base_url, path, query)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            if len(payload) > MAX_BODY_BYTES:
                raise CanvasError("request body is larger than %d bytes" % MAX_BODY_BYTES)
            headers["Content-Type"] = "application/json"
        try:
            token = self.token_reader()
        except config.ConfigError as err:
            raise CanvasError(str(err)) from None
        headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(target, data=payload, headers=headers, method=method)
        is_write = method in WRITE_METHODS
        try:
            raw = self.opener.open(req, timeout=TIMEOUT)
            try:
                status, head = raw.status, dict(raw.headers)
                text = raw.read(MAX_RESPONSE_BYTES + 1)
            finally:
                raw.close()
        except CanvasError as err:                        # a refused redirect
            err.outcome = "uncertain" if is_write else "refused"
            raise
        except urllib.error.HTTPError as err:
            status = err.code
            try:
                reason = canvas_reason(err.read(64 * 1024))
            except Exception:
                reason = ""
            reason = _scrub(reason, token)
            if status == 401:
                raise CanvasError("Canvas rejected the stored token (401): it may have expired or "
                                  "been revoked. In a terminal run: python connect_canvas.py "
                                  "reconnect", status=401) from None
            if 400 <= status < 500:
                label = {403: "not permitted for your Canvas account", 404: "not found"}.get(
                    status, "refused by Canvas")
                raise CanvasError("%s %s: %s (%d)%s" % (method, _display(target), label, status,
                                                         (" - Canvas says: " + reason) if reason else ""),
                                  status=status) from None
            raise CanvasError("%s %s: Canvas server error %d%s" % (
                method, _display(target), status, (" - " + reason) if reason else ""),
                status=status, outcome="uncertain" if is_write else "refused") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise CanvasError("%s %s: network failure: %s" % (method, _display(target),
                                                               type(reason).__name__),
                              outcome="uncertain" if is_write else "refused") from None
        if len(text) > MAX_RESPONSE_BYTES:
            raise CanvasError("response larger than %d bytes; narrow the request with fields, "
                              "per_page or a filter" % MAX_RESPONSE_BYTES, status=status,
                              outcome="uncertain" if is_write else "refused")
        try:
            data = json.loads(text.decode("utf-8")) if text.strip() else None
        except ValueError:
            data = None
        return Response(status=status, headers=head, data=data)

    # -- helpers ---------------------------------------------------------------------------
    def get(self, path: str, query: dict | None = None) -> Any:
        return self.request("GET", path, query).data

    def get_all(self, path: str, query: dict | None = None, page_cap: int = PAGE_CAP) -> tuple[list, int]:
        """Every page of a list. Returns (items, pages)."""
        first = build_url(self.base_url, path, query)
        url, seen, items, pages = first, set(), [], 0
        while url:
            if url in seen:
                raise CanvasError("pagination loop detected; stopped")
            seen.add(url)
            if pages >= page_cap:
                raise CanvasError("more than %d pages; narrow the request with a filter or "
                                  "per_page=100" % page_cap)
            resp = self.request("GET", path, url=url)
            if not isinstance(resp.data, list):
                raise CanvasError("all_pages needs a Canvas list endpoint")
            items.extend(resp.data)
            pages += 1
            url = next_page_url(_header(resp.headers, "Link"), self.base_url)
        return items, pages

    def set_post_policy(self, assignment_id: int, manual: bool) -> Response:
        """The single GraphQL call (rubric extension only): fixed document, typed variables."""
        body = {"query": POST_POLICY_MUTATION,
                "variables": {"id": str(int(assignment_id)), "manual": bool(manual)}}
        return self.request("POST", GRAPHQL_PATH, body=body, graphql=True)


def _header(headers: dict, name: str) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _display(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return parsed.path


def _scrub(value: Any, token: str) -> Any:
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "<redacted>")
    if isinstance(value, dict):
        return {_scrub(k, token): _scrub(v, token) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, token) for v in value]
    return value
