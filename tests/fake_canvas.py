"""An in-memory fake Canvas for tests. No network, no real token.

It stands in for the urllib opener: CanvasClient hands it each urllib Request and it
answers from a route table, recording every request (method, URL, headers, body).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

from canvas_client import CanvasClient

BASE = "https://canvas.example.edu"
TOKEN = "fake-token-7Hq2-DO-NOT-LEAK-9x"


class FakeResponse:
    def __init__(self, status, data, headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self._body = b"" if data is None else json.dumps(data).encode()

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def close(self):
        pass


class FakeCanvas:
    """routes: {("GET", "/api/v1/courses/1"): handler_or_value}.

    A handler is called as handler(fake, request, query_dict, body) and returns
    (status, data[, headers]) or raises. A plain value means (200, value).
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests = []

    def route(self, method, path, value):
        self.routes[(method, path)] = value

    def open(self, req, timeout=None):
        parsed = urllib.parse.urlsplit(req.full_url)
        body = json.loads(req.data.decode()) if req.data else None
        record = {"method": req.get_method(), "url": req.full_url, "host": parsed.netloc,
                  "path": parsed.path, "query": urllib.parse.parse_qs(parsed.query),
                  "headers": dict(req.header_items()), "body": body}
        self.requests.append(record)
        key = (record["method"], parsed.path)
        if key not in self.routes:
            raise _http_error(req.full_url, 404, {"errors": [{"message": "The specified resource does not exist."}]})
        handler = self.routes[key]
        if callable(handler):
            result = handler(self, record)
        elif isinstance(handler, tuple):
            result = handler
        else:
            result = (200, handler)
        status, data = result[0], result[1]
        headers = result[2] if len(result) > 2 else {}
        if status >= 400:
            raise _http_error(req.full_url, status, data)
        return FakeResponse(status, data, headers)

    def writes(self):
        return [r for r in self.requests if r["method"] != "GET"]


def _http_error(url, status, data):
    body = io.BytesIO(json.dumps(data).encode() if data is not None else b"")
    return urllib.error.HTTPError(url, status, "error", {}, body)


def client_for(fake: FakeCanvas, token: str = TOKEN) -> CanvasClient:
    return CanvasClient(BASE, lambda: token, opener=fake)


def link(next_url):
    return {"Link": '<%s>; rel="next", <%s/api/v1/x?page=1>; rel="first"' % (next_url, BASE)}

