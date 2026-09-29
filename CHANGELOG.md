# Changelog

Each release is an immutable Git tag (`vX.Y.Z`). Update by fetching tags and selecting a named
release; see the README's "Updating" section. Entries say when `SKILL.md` or the Codex config
example changed, because those are copied by hand.

## Unreleased

- Added a framework-free GitHub Pages guide covering beginner setup, the read-only-first flow,
  optional writes and rubrics, safety boundaries, and uninstall. `SKILL.md` and
  `examples/codex-mcp-config.example.toml` are unchanged.
- The README and GitHub Pages guide now label exactly what belongs in Codex versus Terminal,
  provide a Codex prompt that prepares write settings without editing configuration, and add
  separate prompts to refresh the matching skill or update the tagged project and skill together.
  `SKILL.md` and `examples/codex-mcp-config.example.toml` are unchanged.
- Corrected the write-setup instructions: the documented **Open config.toml** control belongs to
  the Codex IDE extension, not the current desktop Settings screen. The guide now gives a macOS
  command that creates a unique backup and opens the real configuration in a plain-text editor.
  `SKILL.md` and `examples/codex-mcp-config.example.toml` are unchanged.
- The README and GitHub Pages guide now state prominently that phones and tablets can display the
  responsive guide but cannot install canvas-mcp; setup requires the Mac or Windows computer
  running local Codex. `SKILL.md` and `examples/codex-mcp-config.example.toml` are unchanged.
- `uninstall.py` now checks nested release directories against an explicit file manifest, so an
  unrelated file placed inside `docs/`, `examples/`, `extensions/` or `tests/` keeps the project
  folder from being deleted.

## [0.1.0] - 2026-09-28

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
- `README.md`: added a copy-and-paste Codex setup flow that installs a tagged local checkout,
  registers read-only access first with `codex mcp add`, verifies the server, and leaves writes
  and rubrics as a separate opt-in. It stops rather than silently substituting `main` when the
  requested release tag is absent. Corrected dependency and tagged-update wording.
- `connect_canvas.py`: `setup-info` prints exact commands and the advanced config block without
  editing Codex configuration; status and errors now name the active Python and absolute script.
- The connection prompt now explains malformed or unsafe Canvas URLs and asks again before the
  hidden token prompt. It displays the normalized HTTPS token destination for a final visual check.
- Connection persistence is transactional across settings failures: a first connection removes
  the new token, while reconnect restores the prior token and settings. Settings permissions are
  applied to the temporary file before it atomically replaces the old file.
- `examples/codex-mcp-config.example.toml`: the advanced block now explicitly uses
  `default_tools_approval_mode = "writes"`. `SKILL.md` is unchanged.
