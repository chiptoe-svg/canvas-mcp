# Changelog

Each release is an immutable Git tag (`vX.Y.Z`). Update by fetching tags and selecting a named
release; see the README's "Updating" section. Entries say when `SKILL.md` or the Codex config
example changed, because those are copied by hand.

## Unreleased

## [0.1.2] - 2026-09-28

- Made guarded writes and rubric creation part of the recommended setup, while keeping batch
  rubric grading behind the explicit `--enable-rubric-grading` flag. The beginner guide now uses
  `codex mcp add` directly and no longer requires a manual `config.toml` edit. The old
  `--enable-rubrics` flag remains as a deprecated compatibility alias. `SKILL.md` and
  `examples/codex-mcp-config.example.toml` changed.
- Fixed the rubric MCP wrappers so their prepare and apply tools call the tested rubric
  implementations instead of shadowing those function names and failing with an internal error.
- Made malformed or mismatched rubric and submission read-backs fail closed as
  `WRITE STATUS UNCERTAIN` with `do_not_retry`, including an unexpected-error backstop after a
  confirmed rubric apply.
- Removed the hero tagline from the GitHub Pages guide and shortened its title to “Safe Codex
  Access into Canvas.” `SKILL.md` and `examples/codex-mcp-config.example.toml` are unchanged.
- Added a “Why” section to the README and GitHub Pages guide explaining what canvas-mcp adds over
  direct Canvas API commands: token isolation, host/path restrictions, guarded writes, and tested,
  repeatable enforcement. `SKILL.md` and the config example are unchanged.
- Removed the developer-oriented “Why a tag?” callout from the beginner installation path without
  replacing it. `SKILL.md` and the config example are unchanged.
- Bumped the package, MCP server, and HTTP user-agent versions together to 0.1.2.

## [0.1.1] - 2026-09-28

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
- Added `update.py check/apply` and `connect_canvas.py install-skill` so tested project code—not
  long prose prompts—validates release tags, protects local work, backs up the installed skill,
  and keeps the tagged server and skill together. `SKILL.md` now mentions the safe confirmation
  check; `examples/codex-mcp-config.example.toml` is unchanged.
- Added `canvas_test_confirmation`, which exercises the real server-side MCP elicitation gate but
  cannot create or send a Canvas request. Documentation no longer claims a particular desktop UI
  presentation before this safe check is run in that client.
- Split `connect_canvas.py setup-info` so read-only output is the default, `--writes` prints only
  the write settings, and `--enable-rubrics` is an additional explicit opt-in.
- Bumped the package, MCP server, and HTTP user-agent versions together to 0.1.1.
- Added a durable security-review record for the connection rollback, nested uninstall manifest,
  skill installer, and updater. Destructive probes used scratch copies and fake credentials; the
  record discloses one accidental read-only scan of the maintainer's Codex config.

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
