# Changelog

Each release is a Git tag (`vX.Y.Z`). Update with `git pull --ff-only`; see the README's
"Updating" section. Entries say when `SKILL.md` or the Codex config example changed, because
those are copied by hand.

## [0.1.0] - unreleased

First version.

- `canvas_read`, `canvas_prepare_write`, `canvas_apply_write`: host-locked Canvas REST access
  with same-host pagination, field projection, and one-time, expiring write previews.
- Writes off by default. `--writes confirm` asks through an MCP confirmation dialog before each
  write and refuses anything but an explicit accept.
- `connect_canvas.py` (`connect`, `status`, `reconnect`, `disconnect`). The token is kept only in
  the macOS Keychain or Windows Credential Manager.
- Optional rubric extension (`--enable-rubrics`): rubric creation with read-back, and batch
  rubric grading (up to 50 ungraded students) with manual posting and a check after each write.
- The endpoints that create or list access tokens and developer keys are refused, including
  with a `.json` suffix. `inst_access_tokens` is refused too.
- `uninstall.py`: removes the token, settings, skill copy, `.venv` and, after you type its
  name, the project folder. It never deletes a folder that holds files canvas-mcp did not put
  there. It names the Codex config server to delete by hand.
- `AGENTS.md`: instructions for AI coding agents working on this repository.
- New files: `SKILL.md` and `examples/codex-mcp-config.example.toml`.
