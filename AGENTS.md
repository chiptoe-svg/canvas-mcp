# AGENTS.md

Instructions for AI coding agents (Codex and others) working **on** this repository. For how to
*use* the Canvas tools, see `SKILL.md`; for users, see `README.md`.

## What this is

A local stdio MCP server that gives Codex access to one instructor's own Canvas account. It is a
small, readable Python project that users clone and edit. Keep it that way: no frameworks, no
new dependencies without a clear need, no abstractions nobody asked for.

| File | Purpose |
|---|---|
| `config.py` | Canvas URL validation, settings file, keyring access (the only token reader) |
| `canvas_client.py` | Host-locked HTTP client: path checks, credential deny list, pagination, errors |
| `canvas_mcp.py` | MCP tools, one-time previews, the write-approval gate |
| `connect_canvas.py` | `connect` / `status` / `reconnect` / `disconnect` |
| `uninstall.py` | Removes token, settings, skill copy, `.venv`, optionally the folder |
| `extensions/rubrics.py` | Optional rubric creation and batch grading (`--enable-rubrics`) |
| `tests/` | Tests against an in-memory fake Canvas (`tests/fake_canvas.py`); no network |

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -q
```

Run the full suite before and after every change. It must stay green. Tests never need a real
Canvas account or token; never add one that does.

## Boundaries that must not be loosened

These come from the project's original specification. Changing any of them is a design change
to raise with the maintainer, not an implementation detail.

- **Local stdio only.** No hosted server, network listener, tunnel, daemon, OAuth flow,
  telemetry, auto-update, `sudo`, or global install.
- **Token secrecy.** The token is read only by `config.read_token()`, from the macOS Keychain or
  Windows Credential Manager (`ALLOWED_KEYRING_BACKENDS`). Never accept it from arguments,
  environment variables, files, `.env` or MCP tool input, and never let it reach logs or
  model-visible output (`scrub` exists as a backstop, not a licence).
- **Host lock.** Every request goes through `build_url` / `next_page_url`: one configured
  `https://` host, under `/api/v1`, no redirects followed. The only exception is the single
  fixed GraphQL post-policy mutation in `canvas_client.py`.
- **Credential deny list** (`DENIED_PATHS`). Only ever add to it. Each rule must also match a
  format suffix such as `.json`.
- **Writes.** Off by default. Every write goes prepare (no change, one-time `preview_id`) then
  apply, and apply passes `require_approval`: an MCP elicitation that proceeds only on an
  explicit accept with `confirm: true`, and fails closed on anything else. Tool annotations
  must stay accurate, but never rely on them or on the client for consent.
- **Uncertain writes are never retried**, anywhere. A write whose result cannot be proved is
  reported as `uncertain` with `do_not_retry`.
- **Rubric grading safeguards** (see README "The rubric extension"): live-rubric validation,
  0..max points, 50-student cap, refuse already-scored students, manual posting before the
  first grade, re-check before each write, read-back after each write, honest partial reports.
- **No automatic config edits.** Nothing edits the user's Codex `config.toml`; print what to
  change instead.
- **No institution-specific assumptions** beyond examples and the note to follow local policy.

## How to change code here

1. **Reproduce before fixing.** Show the failing command, test or output first. Reading code
   and deciding it "can't work" is a hypothesis, not a bug.
2. **Red-proof every regression test.** A new test must be shown to fail with only its fix
   reverted, then pass with it. A test that was never red proves nothing.
3. **Anything that can delete or overwrite** (files, keyring entries, Canvas data) needs an
   independent adversarial review: a fresh agent whose job is to break it, running every probe
   on scratch copies with a fake keyring, never the real Keychain, a real Canvas, or this
   checkout. Iterate until it honestly accepts. `uninstall.py` went through four rounds.
4. **Never run the server with `--writes confirm` against a real course while developing.**
   Use the fake Canvas in `tests/`.
5. **Keep changes minimal** and in the style of the surrounding code; don't refactor what the
   task doesn't touch.
6. **A new top-level file** must be added to `RELEASE_ENTRIES` in `uninstall.py` (a test
   enforces this), otherwise uninstall will refuse to delete the folder.

## Docs and releases

- User-visible behaviour changes update `README.md`, and `SKILL.md` if tool usage changes.
- Every change gets a line in `CHANGELOG.md` under the unreleased version. Say explicitly when
  `SKILL.md` or `examples/codex-mcp-config.example.toml` changed, because users copy those by
  hand.
- Releases are Git tags `vX.Y.Z` on `main`; users update with `git pull --ff-only`. Never
  rewrite published history. Bump `version` in `pyproject.toml`, `VERSION` in `canvas_mcp.py`
  and `USER_AGENT` in `canvas_client.py` together.
- Commit or push only when the maintainer asks.
