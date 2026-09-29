"""canvas-mcp: a local stdio MCP server for your own Canvas account.

Start it from Codex (see examples/codex-mcp-config.example.toml); it is not a network
service. Tools:

    canvas_read(path, query=None, all_pages=False, fields=None)
    canvas_prepare_write(method, path, body)       -> preview_id (no change is made)
    canvas_apply_write(preview_id)                 -> applies exactly that preview, once

Also:
    prepare_rubric_create / apply_rubric_create

With --enable-rubric-grading, also:
    prepare_rubric_grading / apply_rubric_grading

Writes are OFF unless the server is started with --writes (see README, "Write approval").
"""

from __future__ import annotations

import argparse
import copy
import json
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

import config
from canvas_client import CanvasClient, CanvasError, WRITE_METHODS, build_url, normalize_path, project
from extensions.rubrics import RubricError

VERSION = "0.1.2"
DEFAULT_PREVIEW_TTL = 600            # seconds a preview stays valid
SUMMARY_KEYS = ("id", "name", "title", "display_name", "workflow_state", "published",
                "due_at", "html_url", "updated_at")


# ------------------------------------------------------------------------------ previews
class PreviewError(Exception):
    pass


@dataclass
class Preview:
    kind: str
    payload: dict
    expires_at: float
    host: str


@dataclass
class PreviewStore:
    """In-memory, per-process, short-lived, one-time previews.

    ``take`` removes the preview before anything is sent, so the same ID can never be applied
    twice - not after success, failure, refusal or a declined approval. A server restart
    forgets every preview.
    """
    ttl: float = DEFAULT_PREVIEW_TTL
    clock: Callable[[], float] = time.monotonic
    _items: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, kind: str, payload: dict, host: str) -> tuple[str, int]:
        preview_id = "pv_" + secrets.token_urlsafe(12)
        with self._lock:
            now = self.clock()
            for key in [k for k, p in self._items.items() if p.expires_at <= now]:
                del self._items[key]
            self._items[preview_id] = Preview(kind, copy.deepcopy(payload), now + self.ttl, host)
        return preview_id, int(self.ttl)

    def take(self, preview_id: Any, kind: str, host: str) -> dict:
        if not isinstance(preview_id, str):
            raise PreviewError("preview_id must be a string")
        with self._lock:
            preview = self._items.pop(preview_id, None)
        if preview is None:
            raise PreviewError("unknown or already-used preview_id; prepare the change again")
        if preview.expires_at <= self.clock():
            raise PreviewError("preview expired; prepare the change again")
        if preview.kind != kind:
            raise PreviewError("preview %s is a %s preview, not %s; it has been discarded"
                               % (preview_id, preview.kind, kind))
        if preview.host != host:
            raise PreviewError("preview was made for another Canvas host; it has been discarded")
        return preview.payload


# ------------------------------------------------------------------------------ approval
class ApprovalRequired(Exception):
    pass


# A hand-written schema: one required boolean and no "title" keys. Codex rejects a schema
# with a top-level "title" (as pydantic generates) and answers such an elicitation with
# "cancel"; a schema with no required field could be auto-accepted with empty content.
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"confirm": {"type": "boolean",
                               "description": "Apply this exact Canvas change now?"}},
    "required": ["confirm"],
}


async def require_approval(mode: str, ctx: Any, message: str) -> None:
    """The server-side gate every apply_* tool passes before sending anything.

    off     - refuse every write (the default).
    confirm - ask the person through MCP elicitation and proceed only on action "accept"
              with confirm == true. A client that cannot elicit, a decline, a cancel, a
              missing field, a timeout or any error refuses the write (fail closed).
    """
    if mode == "off":
        raise ApprovalRequired(
            "Canvas writes are disabled in this server (started without --writes confirm). "
            "The preview shows the exact change; the person can make it in Canvas themselves, "
            "or enable writes as described in the README's 'Write approval' section.")
    if mode != "confirm":
        raise ApprovalRequired("unknown write mode %r; refusing" % mode)
    caps = getattr(ctx, "client_capabilities", None)
    if caps is None or getattr(caps, "elicitation", None) is None:
        raise ApprovalRequired(
            "this MCP client did not declare elicitation support, so the server cannot ask you "
            "directly; refusing the write (fail closed).")
    try:
        result = await ctx.session.elicit_form(message=message, requested_schema=CONFIRM_SCHEMA,
                                               related_request_id=ctx.request_id)
    except Exception as err:
        raise ApprovalRequired("approval could not be obtained (%s); nothing was sent"
                               % type(err).__name__) from None
    content = getattr(result, "content", None) or {}
    if getattr(result, "action", None) != "accept" or content.get("confirm") is not True:
        raise ApprovalRequired("the change was not approved (%s); nothing was sent"
                               % getattr(result, "action", "no answer"))


# --------------------------------------------------------------------------- tool logic
def _summary(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: obj[k] for k in SUMMARY_KEYS if k in obj}
    return obj


def _leaves(obj: Any, prefix: str = "") -> dict:
    if not isinstance(obj, dict):
        return {prefix: obj}
    out = {}
    for k, v in obj.items():
        out.update(_leaves(v, k if not prefix else prefix + "." + k))
    return out


class Tools:
    """The tool implementations, independent of the MCP SDK so tests can call them directly."""

    def __init__(self, client_factory: Callable[[], CanvasClient], writes: str = "off",
                 store: PreviewStore | None = None):
        self.client_factory = client_factory
        self.writes = writes
        self.store = store or PreviewStore()

    def _client(self) -> CanvasClient:
        return self.client_factory()

    # -- canvas_read -----------------------------------------------------------------------
    def read(self, path: str, query: dict | None = None, all_pages: bool = False,
             fields: list[str] | None = None) -> dict:
        client = None

        def run():
            nonlocal client
            client = self._client()
            project(None, fields)                         # validate fields before any request
            npath = normalize_path(path)
            if all_pages:
                items, pages = client.get_all(path, query)
                return {"ok": True, "path": npath, "count": len(items), "pages": pages,
                        "items": project(items, fields)}
            resp = client.request("GET", path, query)
            out = {"ok": True, "path": npath}
            if isinstance(resp.data, list):
                out.update(count=len(resp.data), items=project(resp.data, fields),
                           more_pages=_has_next(resp.headers))
            else:
                out["object"] = project(resp.data, fields)
            return out
        return self._run_with(run, lambda: client)

    def _run_with(self, fn: Callable[[], dict], get_client: Callable[[], CanvasClient | None]) -> dict:
        """Run fn; turn every expected failure into a concise result; scrub the token."""
        try:
            result = fn()
        except CanvasError as err:
            result = err.as_dict()
        except (PreviewError, ApprovalRequired, RubricError, config.ConfigError) as err:
            result = {"ok": False, "outcome": "refused", "error": str(err)}
        except Exception as err:                         # never echo arbitrary exception text
            result = {"ok": False, "outcome": "refused", "error": "internal error: %s" % type(err).__name__}
        client = get_client()
        return client.scrub(result) if client else result

    # -- canvas_prepare_write --------------------------------------------------------------
    def prepare_write(self, method: str, path: str, body: dict | None) -> dict:
        client = None

        def run():
            nonlocal client
            m = (method or "").upper()
            if m not in WRITE_METHODS:
                raise CanvasError("method must be one of POST, PUT, PATCH, DELETE")
            if m == "DELETE":
                if body not in (None, {}):
                    raise CanvasError("DELETE takes no body")
                clean_body = None
            else:
                if not isinstance(body, dict) or not body:
                    raise CanvasError("%s needs a non-empty JSON object body" % m)
                clean_body = json.loads(json.dumps(body))          # plain JSON, deep copy
            client = self._client()
            npath = normalize_path(path)
            url = build_url(client.base_url, path)
            current = None
            if m in ("PUT", "PATCH", "DELETE"):
                try:
                    current = _summary(client.get(path))
                except CanvasError as err:
                    current = {"unavailable": err.as_dict()["error"]}
            payload = {"method": m, "path": npath, "body": clean_body}
            preview_id, ttl = self.store.put("write", payload, client.host)
            return {"ok": True, "preview_id": preview_id, "expires_in_seconds": ttl,
                    "method": m, "url": url, "body": clean_body, "current_target": current,
                    "writes_enabled": self.writes != "off",
                    "next_step": ("Show the person this exact method, URL and body. Only after they "
                                  "approve, call canvas_apply_write with this preview_id."
                                  if self.writes != "off" else
                                  "Writes are disabled in this server; nothing can be applied. Show "
                                  "the person this exact change so they can make it in Canvas.")}
        return self._run_with(run, lambda: client)

    # -- canvas_apply_write ----------------------------------------------------------------
    async def apply_write(self, preview_id: str, ctx: Any) -> dict:
        client = None
        try:
            client = self._client()
            if self.writes == "off":
                await require_approval("off", ctx, "")
            payload = self.store.take(preview_id, "write", client.host)
            await require_approval(self.writes, ctx, client.scrub(_approval_text(client, payload)))
            resp = await anyio.to_thread.run_sync(
                lambda: client.request(payload["method"], payload["path"], body=payload["body"]))
            result = {"ok": True, "outcome": "applied", "status": resp.status,
                      "method": payload["method"], "path": payload["path"],
                      "result": _summary(resp.data)}
            if payload["body"] and isinstance(resp.data, dict):
                result["echo_check"] = _echo_check(payload["body"], resp.data)
        except CanvasError as err:
            result = err.as_dict()
            if err.outcome == "uncertain":
                result["do_not_retry"] = True
                result["error"] = "WRITE STATUS UNCERTAIN: " + result["error"]
        except (PreviewError, ApprovalRequired, config.ConfigError) as err:
            result = {"ok": False, "outcome": "refused", "error": str(err)}
        return client.scrub(result) if client else result

    async def test_confirmation(self, ctx: Any) -> dict:
        """Exercise the real approval gate without creating or sending a Canvas request."""
        try:
            await require_approval(
                self.writes, ctx,
                "Confirmation test only. No Canvas request has been prepared and accepting this "
                "test cannot change Canvas. Check confirm to verify the dialog.")
            return {"ok": True, "outcome": "confirmed", "canvas_changed": False}
        except ApprovalRequired as err:
            return {"ok": False, "outcome": "refused", "canvas_changed": False,
                    "error": str(err)}

    # -- rubric extension ------------------------------------------------------------------
    def prepare_rubric(self, kind: str, fn: Callable[[CanvasClient], dict]) -> dict:
        client = None

        def run():
            nonlocal client
            client = self._client()
            plan = fn(client)
            preview_id, ttl = self.store.put(kind, plan, client.host)
            return {"ok": True, "preview_id": preview_id, "expires_in_seconds": ttl,
                    "writes_enabled": self.writes != "off", "plan": plan["summary"],
                    "requests": plan.get("writes") or [plan.get("request")]}
        return self._run_with(run, lambda: client)

    async def apply_rubric(self, kind: str, preview_id: str, ctx: Any,
                           fn: Callable[[CanvasClient, dict], dict], describe: Callable[[dict], str]) -> dict:
        client = None
        try:
            client = self._client()
            if self.writes == "off":
                await require_approval("off", ctx, "")
            plan = self.store.take(preview_id, kind, client.host)
            await require_approval(self.writes, ctx, client.scrub(describe(plan)))
            try:
                result = await anyio.to_thread.run_sync(lambda: fn(client, plan))
            except (CanvasError, RubricError):
                raise
            except Exception as err:
                result = {"ok": False, "outcome": "uncertain", "do_not_retry": True,
                          "error": ("WRITE STATUS UNCERTAIN: rubric apply failed after confirmation "
                                    "with internal %s; inspect Canvas before doing anything else"
                                    % type(err).__name__)}
        except CanvasError as err:
            result = err.as_dict()
            if err.outcome == "uncertain":
                result.update(do_not_retry=True, error="WRITE STATUS UNCERTAIN: " + result["error"])
        except (PreviewError, ApprovalRequired, RubricError, config.ConfigError) as err:
            result = {"ok": False, "outcome": "refused", "error": str(err)}
        return client.scrub(result) if client else result


def _has_next(headers: dict) -> bool:
    link = next((v for k, v in (headers or {}).items() if k.lower() == "link"), "") or ""
    return 'rel="next"' in link


def _echo_check(body: dict, response: dict) -> dict:
    """Which requested leaf values Canvas's response echoes back (informational only)."""
    matched, differs = [], []
    for dotted, want in _leaves(body).items():
        leaf = dotted.split(".")[-1]
        if leaf in response:
            (matched if response[leaf] == want else differs).append(leaf)
    return {"matched": matched, "differs": differs}


def _approval_text(client: CanvasClient, payload: dict) -> str:
    body = json.dumps(payload["body"], indent=1, sort_keys=True) if payload["body"] is not None else "(none)"
    if len(body) > 1500:
        body = body[:1500] + "\n... (truncated; see the preview)"
    return "Apply this Canvas change?\n\n%s %s%s\n\nBody:\n%s" % (
        payload["method"], client.base_url, payload["path"], body)


# ------------------------------------------------------------------------------ server
READ_ONLY = dict(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
PREPARE = dict(read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
APPLY = dict(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
LOCAL_TEST = dict(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                  open_world_hint=False)


def build_server(tools: Tools, enable_rubric_grading: bool = False):
    server = MCPServer(
        name="canvas", version=VERSION,
        instructions=("Local access to the user's own Canvas account. Read with canvas_read. "
                      "Every change is two steps: prepare (shows the exact request, changes nothing) "
                      "and apply (only after the person approves that exact preview). Request only "
                      "the student data the task needs; use `fields` to narrow results."))

    @server.tool(annotations=ToolAnnotations(title="Read Canvas", **READ_ONLY))
    async def canvas_read(path: str, query: dict[str, Any] | None = None, all_pages: bool = False,
                          fields: list[str] | None = None) -> dict:
        """GET a Canvas API path such as "courses" or "courses/123/assignments".

        path: relative Canvas API path (no host, no query string).
        query: query parameters, e.g. {"per_page": 100, "include[]": ["term"]}.
        all_pages: follow rel="next" pages on the same host and return one list.
        fields: keep only these (dot-path) fields, e.g. ["id", "name", "due_at"].
        """
        return await anyio.to_thread.run_sync(lambda: tools.read(path, query, all_pages, fields))

    @server.tool(annotations=ToolAnnotations(title="Prepare a Canvas change (no change made)", **PREPARE))
    async def canvas_prepare_write(method: str, path: str, body: dict[str, Any] | None = None) -> dict:
        """Validate a POST/PUT/PATCH/DELETE and return a one-time preview_id. Changes nothing.

        Show the person the returned method, url, body and current_target, and get their
        explicit approval before calling canvas_apply_write.
        """
        return await anyio.to_thread.run_sync(lambda: tools.prepare_write(method, path, body))

    @server.tool(annotations=ToolAnnotations(title="Apply an approved Canvas change", **APPLY))
    async def canvas_apply_write(preview_id: str, ctx: Context) -> dict:
        """Apply exactly the change a canvas_prepare_write preview described, once.

        Call only after the person approved that preview. The ID is consumed by this call
        whatever the outcome. If the result says WRITE STATUS UNCERTAIN, do not retry: read
        the object back and tell the person what Canvas holds.
        """
        return await tools.apply_write(preview_id, ctx)

    @server.tool(annotations=ToolAnnotations(title="Test the confirmation dialog (no Canvas change)",
                                             **LOCAL_TEST))
    async def canvas_test_confirmation(ctx: Context) -> dict:
        """Open the real write-confirmation gate without making any request to Canvas.

        Use after enabling ``--writes confirm`` to verify that the current Codex client displays
        the server-side confirmation. Accepting or declining this test never changes Canvas.
        """
        return await tools.test_confirmation(ctx)

    from extensions import rubrics
    rubrics.register(server, tools, PREPARE, APPLY, enable_grading=enable_rubric_grading)
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local stdio MCP server for your Canvas account.")
    parser.add_argument("--writes", choices=("off", "confirm"), default="off",
                        help="off (default): no Canvas changes. confirm: each apply asks you "
                             "through MCP elicitation and refuses without an explicit accept.")
    parser.add_argument("--enable-rubric-grading", action="store_true",
                        help="add optional batch rubric grading; rubric creation is standard")
    parser.add_argument("--enable-rubrics", dest="legacy_enable_rubrics", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--preview-ttl", type=int, default=DEFAULT_PREVIEW_TTL,
                        help="seconds a preview stays valid (60-3600, default 600)")
    args = parser.parse_args(argv)
    if not 60 <= args.preview_ttl <= 3600:
        parser.error("--preview-ttl must be between 60 and 3600")
    tools = Tools(CanvasClient.from_settings, writes=args.writes, store=PreviewStore(ttl=args.preview_ttl))
    enable_grading = args.enable_rubric_grading or args.legacy_enable_rubrics
    if args.legacy_enable_rubrics:
        print("canvas-mcp: --enable-rubrics is deprecated; use --enable-rubric-grading", file=sys.stderr)
    print("canvas-mcp %s: writes=%s rubric_grading=%s" % (VERSION, args.writes, enable_grading),
          file=sys.stderr)
    build_server(tools, enable_rubric_grading=enable_grading).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
