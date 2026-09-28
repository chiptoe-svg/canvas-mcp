# canvas-mcp

A small, local MCP server that lets Codex read — and, only if you turn it on, change — **your
own** Canvas account. It is a Python project you download, read, and edit. It is not a hosted
service, not an app your institution runs, and nobody supports it for you.

**New here?** Use the [visual setup guide](https://chiptoe-svg.github.io/canvas-mcp/) for a
step-by-step installation, write opt-in, safety, and uninstall walkthrough.

- **Local only.** Codex starts it as a child process over stdio. There is no server to host, no
  open port, no tunnel, no background service, and no OAuth app.
- **Your account, your token.** You create a Canvas personal access token and it is stored only
  in the macOS Keychain or Windows Credential Manager.
- **Reads by default, writes off.** Changes are disabled until you start the server with
  `--writes confirm`, and even then every change is previewed first and needs your explicit
  yes in a confirmation dialog.

**Follow your institution's policies.** Many institutions have rules about personal access
tokens and about sending student information to AI services. Check them before you use this with
real courses. This project makes no assumption about any particular institution.

## Contents

- [What it can do](#what-it-can-do)
- [Set up with Codex](#set-up-with-codex)
- [Setup on macOS](#setup-on-macos)
- [Setup on Windows](#setup-on-windows)
- [Connect Canvas](#connect-canvas)
- [Add it to Codex](#add-it-to-codex)
- [Write approval](#write-approval)
- [The rubric extension](#the-rubric-extension)
- [Token and data handling](#token-and-data-handling)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Changing it](#changing-it)

## What it can do

Three tools are always present:

| Tool | What it does |
|---|---|
| `canvas_read(path, query, all_pages, fields)` | `GET` any Canvas REST path, e.g. `courses/123/assignments`. `all_pages` follows Canvas's next-page links; `fields` keeps only the fields you name, so less student data reaches the model. |
| `canvas_prepare_write(method, path, body)` | Checks a `POST`/`PUT`/`PATCH`/`DELETE`, shows the exact URL and body plus what the target looks like now, and returns a one-time `preview_id`. **Changes nothing.** |
| `canvas_apply_write(preview_id)` | Sends exactly that previewed request, once, after you confirm it. |

With `--enable-rubrics`, four more tools create rubrics and grade with them. See
[The rubric extension](#the-rubric-extension).

`SKILL.md` is a short Codex skill describing how to use these tools for everyday instructor
work: summarising activity, finding late work, drafting feedback and announcements.

Every REST request is locked to your configured `https://` Canvas host and `/api/v1`. The only
path exception is the single fixed GraphQL post-policy mutation used by guarded rubric grading.
The server refuses absolute URLs, other hosts, `..` and percent-encoded paths, and redirects
(following one would send your token elsewhere). It also refuses the Canvas endpoints that create
or list access tokens and developer keys, because they would put a credential in front of the model.

## Set up with Codex

The easiest setup is to let Codex do the mechanical work while you keep control of the one
secret step. Paste the prompt below into a **local** Codex task. It installs a tagged source
checkout, not a global application: there is no daemon, open port, telemetry or auto-update.

```text
Set up canvas-mcp release v0.1.0 from https://github.com/chiptoe-svg/canvas-mcp.git in
~/canvas-mcp. If that path already exists, do not overwrite it: inspect it and tell me what is
there. Confirm that the remote tag v0.1.0 exists before cloning. If it does not, stop and tell me;
never substitute the moving main branch or an untagged commit. After cloning, read AGENTS.md
before doing anything else. Confirm Git and Python 3.10+ are available, report the exact
checked-out tag and commit, create .venv, run
.venv/bin/pip install -e ".[test]", and run .venv/bin/pytest -q. Never ask me to put a Canvas
token in chat, a command, a file, or an environment variable. Run
.venv/bin/python connect_canvas.py status. If it is not connected,
show me the exact .venv/bin/python connect_canvas.py connect command to run myself in a visible
terminal at its hidden prompt, then stop and wait. After I say that is done, run status again.
With my authorization in this prompt, register only the read-only server using `codex mcp add`
and the absolute paths from this checkout; do not enable writes or rubrics, and do not replace an
existing `canvas` MCP entry. Copy SKILL.md to ~/.codex/skills/canvas-mcp/SKILL.md, then verify the
server with `codex mcp get canvas`. Report every change and tell me to restart Codex. Do not make
any Canvas write while setting this up.
```

The only step Codex must not perform for you is entering the Canvas URL and access token. Run the
command it prints in a terminal you control; the token prompt is hidden. The first setup is
deliberately read-only. After you have used reads successfully, see [Write approval](#write-approval)
to opt into changes and the optional rubric tools.

At any time, this checkout can print its exact connection command, read-only `codex mcp add`
command and advanced configuration block without changing anything:

```sh
.venv/bin/python connect_canvas.py setup-info
```

## Setup on macOS

You need Python 3.10 or newer (`python3 --version`) and Git.

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/chiptoe-svg/canvas-mcp.git
cd canvas-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

`pip install -e .` installs the three direct dependencies (`mcp`, `keyring`, `anyio`) and their
transitive dependencies into `.venv`.
The project runs in place from this folder, so your own edits take effect directly.

To run the tests (no Canvas account or token needed):

```sh
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

## Setup on Windows

The core connection path is designed for Windows, but the full setup and uninstall workflow has
not yet been tested on a Windows computer. Install Python 3.10+ from python.org and Git for
Windows. In PowerShell:

```powershell
git clone --branch v0.1.0 --depth 1 https://github.com/chiptoe-svg/canvas-mcp.git
cd canvas-mcp
py -m venv .venv
.venv\Scripts\pip install -e .
```

Tests: `.venv\Scripts\pip install -e ".[test]"` then `.venv\Scripts\pytest`.

On Windows, use `.venv\Scripts\python` wherever this README says `.venv/bin/python`.

## Connect Canvas

1. In Canvas, open **Account > Settings > Approved Integrations > + New Access Token**. Give it a
   purpose and, ideally, an expiry date. Copy the token.
2. In a terminal, in the project folder:

   ```sh
   .venv/bin/python connect_canvas.py connect
   ```

   Enter your Canvas URL (e.g. `https://school.instructure.com`), then paste the token at the
   hidden prompt. It is checked against Canvas (`/api/v1/users/self`) and saved only if Canvas
   accepts it.

Other commands:

| Command | What it does |
|---|---|
| `connect_canvas.py status` | Shows the URL, where the token is stored, and whether Canvas still accepts it. |
| `connect_canvas.py reconnect` | Replaces the token, and optionally the URL. Use it when a token expires. |
| `connect_canvas.py disconnect` | Deletes the stored token and the saved URL from this computer. |
| `connect_canvas.py setup-info` | Prints exact connection and Codex setup instructions; changes nothing. |

Disconnecting does not revoke the token in Canvas. To revoke it, delete it under **Approved
Integrations**.

The token cannot be passed as a command-line argument, environment variable or file. It is only
ever typed at the hidden prompt.

If the URL is malformed or unsafe (for example, `http://`, an IP address, a URL with a path, or a
host containing credentials), the connector explains the problem and asks again before requesting
a token. It prints the normalized HTTPS destination immediately before the hidden token prompt;
check that hostname carefully. A valid-looking but incorrect hostname cannot be identified from
its spelling alone. If Canvas rejects the URL/token pair or the host cannot be reached, nothing is
saved and you can run the connection command again.

## Add it to Codex

This project never edits Codex configuration. For a read-only server, use Codex's configuration
command with the absolute paths to your clone:

```sh
codex mcp add canvas -- /Users/you/canvas-mcp/.venv/bin/python /Users/you/canvas-mcp/canvas_mcp.py
codex mcp get canvas
```

If a server named `canvas` already exists, inspect it first; do not remove or replace it blindly.
You can also add a stdio server in **Codex Settings > MCP servers**. Restart Codex after adding
it, then use `/mcp` to confirm it is connected and ask something like "list my Canvas courses."

For finer settings, Codex reads MCP servers from `~/.codex/config.toml` (Windows:
`%USERPROFILE%\.codex\config.toml`). Copy the block from
[`examples/codex-mcp-config.example.toml`](examples/codex-mcp-config.example.toml) and replace
the paths with the absolute paths to your clone.

```toml
[mcp_servers.canvas]
command = "/Users/you/canvas-mcp/.venv/bin/python"
args = ["/Users/you/canvas-mcp/canvas_mcp.py"]
```

That block also starts the server read-only.

To use the skill, copy `SKILL.md` to `~/.codex/skills/canvas-mcp/SKILL.md`.

## Write approval

Writes are **off** by default. Every `apply` tool then refuses and sends nothing. The preview
still shows the exact change, so you can make it yourself in Canvas.

Enable writes only after read-only use is working. Run `connect_canvas.py setup-info` to print
advanced settings with the real paths for this checkout. Merge them carefully rather than
appending the whole fragment blindly: its top-level `approvals_reviewer` line must appear before
every `[table]` in `config.toml`. Or add `"--writes", "confirm"` to the existing server's `args`:

```toml
args = ["/Users/you/canvas-mcp/canvas_mcp.py", "--writes", "confirm"]
```

To add the optional rubric tools, also add `"--enable-rubrics"` and set
`tool_timeout_sec = 600`. A 50-student grading batch can exceed Codex's default tool timeout.
Keep `default_tools_approval_mode = "writes"` on this server and `approvals_reviewer = "user"`
as a top-level setting. Do not enable writes or rubrics merely to test the installation.

In `confirm` mode, before any `apply` tool sends anything, **the server itself** asks you through
an MCP confirmation dialog (an "elicitation") that repeats the exact request. It proceeds only if
you accept with `confirm` ticked. A decline, a cancel, a closed dialog, an error, or a Codex that
cannot show the dialog all refuse the write. Each `preview_id` works once and expires after 10
minutes (`--preview-ttl` sets 60 to 3600 seconds).

**Why the server asks you itself.** Codex does mark tools annotated as destructive and, by
default, asks before running them. That prompt cannot be relied on alone:

- a per-tool `approval_mode = "approve"` in your Codex config removes it;
- `approvals_reviewer = "auto_review"` lets a model approve in your place;
- headless `codex exec` cancels every confirmation. With this server, writes then simply fail,
  which is the safe outcome.

So you may see two prompts for one change: Codex's, then the server's. The server's prompt is
the one that shows the exact request. Keep `approvals_reviewer = "user"` in your Codex config,
and never set `approval_mode = "approve"` on this server's `apply_*` tools.

These findings come from testing Codex 0.150.0-alpha.12.2. Tests used `codex exec` and a
script acting as the desktop app's client; clicking through the interactive TUI or desktop app
by hand has not yet been tested.

**If a result says `WRITE STATUS UNCERTAIN`**, the request was sent but its effect could not be
confirmed. Do not retry. Look at the object in Canvas (or read it back) first.

## The rubric extension

Optional, for instructors who grade with rubrics. Enable it by adding `"--enable-rubrics"` to
`args`. It needs `--writes confirm` to actually change anything.

| Tool | What it does |
|---|---|
| `prepare_rubric_create(course_id, definition, assignment_id?)` | Validates a simple rubric definition, builds Canvas's indexed rubric format, and previews the request. Changes nothing. |
| `apply_rubric_create(preview_id)` | Creates it, then reads every criterion and rating back. If attached to an assignment, "use this rubric for grading" is left **off**. |
| `prepare_rubric_grading(course_id, assignment_id, grades, keep_post_policy?)` | Checks up to 50 students' scores against the assignment's live rubric and each live submission. Changes nothing. |
| `apply_rubric_grading(preview_id)` | Writes each student's rubric assessment and reads it back. |

How grading protects you:

- **Checked against the live rubric.** Criterion IDs must exist on the assignment's current
  rubric, and points must be numbers between 0 and that criterion's maximum. Duplicate students
  and batches over 50 are refused.
- **The preview shows everything.** For each student it shows the current grade, each
  criterion's points and comments, the rubric total, any stated grade, criteria excluded from
  the total, and a SpeedGrader link.
- **Existing grades are never overwritten.** If any student in the batch already has a score,
  the whole batch is refused. Changing a grade is a separate, one-student decision through
  `canvas_prepare_write`.
- **Grades stay hidden until you post them.** Unless `keep_post_policy` is true, an assignment
  that posts grades automatically is switched to manual posting before any grade is written. New
  grades, rubric scores and comments then stay hidden until you click **Post grades** in the
  Canvas gradebook. The preview warns you if a student's submission is already posted, because
  Canvas shows new marks on those immediately.
- **Re-checked just before writing.** The rubric is re-read before any grade is written. Each
  student is re-read before their own write. If anything changed since the preview, the batch
  stops.
- **Every write is verified, and nothing is retried.** Each write is read back. The first
  refusal or unconfirmed write stops the batch. The result lists exactly who was written, who
  is uncertain, and who was not attempted.
- **"Use this rubric for grading" must be off.** Grading refuses an assignment with it on,
  because Canvas would then overwrite stated grades with the rubric total.

## Token and data handling

- **The token** lives only in the macOS Keychain or Windows Credential Manager, under the service
  name `canvas-mcp`. Other keyring backends are refused, including plaintext files. The token
  is read at the moment of each request and used only in the `Authorization` header. It is never
  written to a file, log, argument, environment variable or tool input. It is also removed from
  anything returned to Codex, in case Canvas echoes it.
- **The Canvas URL** is saved in a small `config.json`:
  - macOS: `~/Library/Application Support/canvas-mcp/`
  - Windows: `%APPDATA%\canvas-mcp\`
- **Linux** is not supported for token storage. The keyring backends there are not on the
  allowed list.
- **Student data.** Anything a tool returns goes to the AI model Codex is using. Ask for only
  what the task needs, and use `fields` to trim results. The skill tells Codex to do this. Your
  institution's rules on student records apply.
- **Nothing is kept.** Previews live in memory and disappear when Codex stops the server. There
  is no log, database or telemetry.

## Updating

Published versions are immutable Git tags (`v0.1.0`, `v0.2.0`, …), each described in
`CHANGELOG.md`. Nothing updates itself. Do not treat the moving `main` branch as a release. To
move an unedited checkout to a specific published version:

```sh
cd canvas-mcp
git status                  # stop and ask Codex for help if this shows local edits
git fetch --tags
git switch --detach v0.2.0  # replace with the release you reviewed
.venv/bin/pip install -e .  # picks up any new dependency
.venv/bin/pytest            # optional: confirm it still passes
```

Record the tag and commit Codex reports. If you edit this project, create a branch first rather
than editing a detached release checkout. If `git status` shows changes, do not switch releases
until you have reviewed and saved them. Common options for an experienced Git user are:

- Keep your edits on your own branch (`git switch -c my-changes`, then commit them).
- Ask Codex to compare your branch with the new tag and help carry the changes forward.

Read `CHANGELOG.md` before updating. It says when a release changes `SKILL.md` (copy it to
`~/.codex/skills/canvas-mcp/` again) or the Codex config example.

## Uninstalling

From the project folder:

```sh
.venv/bin/python uninstall.py                 # macOS
.venv\Scripts\python uninstall.py             # Windows
```

It lists what it will remove and asks its questions before removing anything. Pressing Ctrl-C
at either question leaves everything as it was. Then it removes:

1. The stored token and the saved Canvas URL. If the token cannot be removed, it stops and
   removes nothing else. If it cannot tell which token to remove, it prints the command to
   remove it yourself.
2. The Codex skill copy, `~/.codex/skills/canvas-mcp/`.
3. The project's `.venv`.
4. The project folder itself, only if you type its name. Use `--keep-project` to skip this
   step. Before asking, it warns about:
   - uncommitted changes;
   - git-ignored files such as `.env`;
   - unpushed commits;
   - stashes.

   It refuses to delete the folder if the folder holds anything canvas-mcp did not put
   there, and lists those items instead. That way a folder you also use for other files is
   never swept away.

Links inside the folder are removed, never the files they point to.

On Windows, a running Python cannot delete its own folder. The script prints a `Remove-Item`
command for steps 3 and 4. Run it in PowerShell after the script exits, from a folder
outside the project. This Windows path has not been tested on Windows yet.

Two things it does not do:

- **It does not edit your Codex config.** It tells you which `[mcp_servers.…]` section to
  delete from `config.toml`.
- **It does not revoke the token in Canvas.** Delete it under **Account > Settings > Approved
  Integrations**.

## Changing it

The code is meant to be read:

| File | Purpose |
|---|---|
| `config.py` | Canvas URL validation, settings file, keyring access |
| `canvas_client.py` | The host-locked HTTP client: path checks, credential-path deny list, pagination, errors |
| `canvas_mcp.py` | The MCP tools, previews and the write-approval gate |
| `connect_canvas.py` | The `connect` / `status` / `reconnect` / `disconnect` command |
| `uninstall.py` | Removes the token, settings, skill copy, `.venv` and (optionally) the folder |
| `extensions/rubrics.py` | The optional rubric tools |
| `tests/` | Tests against an in-memory fake Canvas; no network |

If you change it with an AI coding agent, `AGENTS.md` gives the agent the project's rules.
Run `pytest` after any change. The tests encode the safety rules, so a failing test usually
means a rule was loosened.
