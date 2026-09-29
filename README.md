# canvas-mcp

A small, local MCP server that lets Codex safely read and make individually approved changes in
**your own** Canvas account. It is a Python project you download, read, and edit. It is not a hosted
service, not an app your institution runs, and nobody supports it for you.

**New here?** Use the [visual setup guide](https://chiptoe-svg.github.io/canvas-mcp/) for a
step-by-step installation, guarded-write, safety, and uninstall walkthrough.

**Install from a computer, not a phone or tablet.** The guide is responsive so it is readable on
any device, but setup must happen on the Mac or Windows computer where local Codex, Git, Python,
and a command-line window are available. Use **Terminal** on macOS or **PowerShell** on Windows.
The Windows instructions mean native Windows PowerShell, not WSL.

- **Local only.** Codex starts it as a child process over stdio. There is no server to host, no
  open port, no tunnel, no background service, and no OAuth app.
- **Your account, your token.** You create a Canvas personal access token and it is stored only
  in the macOS Keychain or Windows Credential Manager.
- **Guarded writes in the recommended setup.** Every change is previewed first and needs your
  explicit yes in a confirmation dialog. A strict read-only setup remains available.

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
- [Rubrics](#rubrics)
- [Token and data handling](#token-and-data-handling)
- [Security review record](SECURITY_REVIEW.md)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Changing it](#changing-it)

## What it can do

Six tools are always present:

| Tool | What it does |
|---|---|
| `canvas_read(path, query, all_pages, fields)` | `GET` any Canvas REST path, e.g. `courses/123/assignments`. `all_pages` follows Canvas's next-page links; `fields` keeps only the fields you name, so less student data reaches the model. |
| `canvas_prepare_write(method, path, body)` | Checks a `POST`/`PUT`/`PATCH`/`DELETE`, shows the exact URL and body plus what the target looks like now, and returns a one-time `preview_id`. **Changes nothing.** |
| `canvas_apply_write(preview_id)` | Sends exactly that previewed request, once, after you confirm it. |
| `canvas_test_confirmation()` | Opens the same server-side confirmation without preparing, sending, or changing anything in Canvas. |
| `prepare_rubric_create(course_id, definition, assignment_id?)` | Validates and previews a new rubric. **Changes nothing.** |
| `apply_rubric_create(preview_id)` | Creates the approved rubric and reads it back. |

With `--enable-rubric-grading`, two additional tools support guarded batch grading. See
[Rubrics](#rubrics).

Ordinary read-only questions use the registered MCP tools directly and do not load a skill. The
installed `canvas-mcp` skill is intentionally short and scoped only to writes and rubrics; it reads
the matching reference file when one of those advanced workflows is requested.

Every REST request is locked to your configured `https://` Canvas host and `/api/v1`. The only
path exception is the single fixed GraphQL post-policy mutation used by guarded rubric grading.
The server refuses absolute URLs, other hosts, `..` and percent-encoded paths, and redirects
(following one would send your token elsewhere). It also refuses the Canvas endpoints that create
or list access tokens and developer keys, because they would put a credential in front of the model.

### Why not use direct API commands?

Codex can call Canvas directly when it has a token. canvas-mcp does not add Canvas permissions;
it makes that access constrained and repeatable:

- The token is entered only at a hidden prompt, stored in the operating-system credential store,
  and read directly by the server instead of being supplied in a command, environment variable,
  file, or MCP input.
- Requests are locked to one configured HTTPS Canvas host and approved API paths. Redirects,
  other hosts, traversal paths, and credential-management endpoints are refused.
- When the server is write-enabled, every write is an exact, expiring, one-time
  preview followed by a server-side confirmation; uncertain writes are never automatically retried.
- Tested code enforces the rules on every request rather than relying on each prompt to remember
  them. Field projection and response limits help reduce unnecessary student data sent to Codex.

The token still carries whatever permissions Canvas assigned to it, and data returned by a tool
is visible to Codex. This project is a guardrail around access, not a smaller Canvas permission set
or a substitute for institutional policy.

## Set up with Codex

The easiest setup is to let Codex do the mechanical work while you keep control of the one
secret step. On the Mac or Windows computer where Codex runs, paste the prompt below into a
**local** Codex task. A phone or tablet can display these instructions but cannot perform the
installation. It installs a tagged source checkout, not a global application: there is no daemon,
open port, telemetry or auto-update.

```text
Install canvas-mcp v0.1.4 on this local computer. First identify the host environment. Support
macOS Terminal and native Windows PowerShell; stop if this is Linux or WSL. Clone exactly v0.1.4
from https://github.com/chiptoe-svg/canvas-mcp.git into `~/canvas-mcp` on macOS or
`$HOME\canvas-mcp` on Windows.
Never substitute main, and do not overwrite an existing folder.
Immediately after cloning, run `python3 update.py verify v0.1.4` on macOS or
`py update.py verify v0.1.4` in PowerShell and stop unless it prints `Verified exact release`.
Then read AGENTS.md, create .venv, install ".[test]", and run pytest. Use `.venv/bin/python` on
macOS or `.\.venv\Scripts\python.exe` on Windows for every later project command. Run
`connect_canvas.py status`. If disconnected, give me the connection command for Terminal or
PowerShell and wait while I enter the token at its hidden prompt. After I say done, verify status,
register only the `canvas` server with `codex mcp add`, using `--writes confirm`, run
`connect_canvas.py install-skill --apply`, and verify with `codex mcp get canvas`. Then run
`canvas_test_confirmation`; it must not contact Canvas. Never request my token or make a real
Canvas write during setup.
```

The only step Codex must not perform for you is entering the Canvas URL and access token. Run the
command it prints in Terminal (macOS) or PowerShell (Windows); the token prompt is hidden. The
recommended setup
includes guarded writes and rubric creation, but no write happens unless you ask for one, approve
its exact preview, and accept the server's confirmation. Batch rubric grading remains separate.

At any time, this checkout can print its exact connection and guarded-write `codex mcp add`
commands without changing anything. Add `--read-only` for a strict read-only registration:

macOS Terminal: `.venv/bin/python connect_canvas.py setup-info`

Windows PowerShell: `.\.venv\Scripts\python.exe connect_canvas.py setup-info`

## Setup on macOS

You need Python 3.10 or newer (`python3 --version`) and Git.

```sh
git clone --branch v0.1.4 --depth 1 https://github.com/chiptoe-svg/canvas-mcp.git
cd canvas-mcp
python3 update.py verify v0.1.4
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

The code includes native Windows paths, Windows Credential Manager selection, junction checks,
and PowerShell cleanup commands. Automated tests cover those branches with fakes and scratch
paths, but the real credential store and full setup/uninstall journey have not yet been run
end-to-end on a Windows computer. Treat Windows support as beta. Install Python 3.10+ from
python.org and Git for Windows. Use native
**PowerShell**, not WSL: WSL is Linux, and canvas-mcp intentionally refuses Linux keyring backends.

```powershell
git clone --branch v0.1.4 --depth 1 https://github.com/chiptoe-svg/canvas-mcp.git
cd canvas-mcp
py update.py verify v0.1.4
py -m venv .venv
.\.venv\Scripts\pip install -e .
```

Tests: `.\.venv\Scripts\pip install -e ".[test]"` then `.\.venv\Scripts\pytest`.

On Windows, use `.\.venv\Scripts\python.exe` wherever this README says `.venv/bin/python`.

## Connect Canvas

1. In Canvas, open **Account > Settings > Approved Integrations > + New Access Token**. Give it a
   purpose and, ideally, an expiry date. Copy the token.
2. In Terminal on macOS, in the project folder:

   ```sh
   .venv/bin/python connect_canvas.py connect
   ```

   Or in native Windows PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe connect_canvas.py connect
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
| `connect_canvas.py setup-info --read-only` | Prints a strict read-only registration command instead. |
| `connect_canvas.py setup-info --enable-rubric-grading` | Includes the optional batch rubric grading tools. |
| `connect_canvas.py install-skill` | Previews the matching write/rubric skill package; `--apply` backs up and installs all managed files. |
| `update.py check` | Checks the latest stable release and prints its tag, commit, and notes without changing project files. |
| `update.py apply TAG` | Rechecks and installs exactly the approved tag, runs tests, and refreshes the matching skill package. |

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

This project never edits Codex configuration directly. Use Codex's own registration command with
the absolute paths to your clone:

macOS Terminal:

```sh
codex mcp add canvas -- /Users/you/canvas-mcp/.venv/bin/python /Users/you/canvas-mcp/canvas_mcp.py --writes confirm
codex mcp get canvas
```

Windows PowerShell:

```powershell
codex mcp add canvas -- "$HOME\canvas-mcp\.venv\Scripts\python.exe" "$HOME\canvas-mcp\canvas_mcp.py" --writes confirm
codex mcp get canvas
```

If a server named `canvas` already exists, inspect it first; do not remove or replace it blindly.
You can also add a stdio server in **Codex Settings > MCP servers**. Restart Codex after adding
it, then use `/mcp` to confirm it is connected and ask something like "list my Canvas courses."

For strict read-only use, omit `--writes confirm`. Batch rubric grading is a separate opt-in:
append `--enable-rubric-grading`. Existing v0.1.1 configurations using `--enable-rubrics` remain
compatible, but the old name is deprecated.

To install the guarded-write skill package, run `connect_canvas.py install-skill` with the
platform-specific virtual-environment Python shown above to preview the destination, then repeat it
with `--apply` after review. The package contains a short entrypoint plus separate write and rubric
references.

For ordinary read-only Canvas work, Codex should call the registered `canvas` MCP tools directly
without loading the `canvas-mcp` skill. It should
not search memories for live Canvas facts, run a Python MCP client, use `curl`, or call the Canvas
API through the shell. If the tools are unavailable, check `/mcp`; do not bypass the server.

## Write approval

The recommended registration starts the server with `--writes confirm`. That does not allow
silent changes. A write still requires all of these steps:

For a write or rubric request, explicitly invoke `$canvas-mcp` or tell Codex to use the
`canvas-mcp` skill. The short entrypoint loads only the write or rubric reference needed for that
request.

1. Codex prepares an exact request and shows the current target. Nothing changes.
2. You approve that specific preview.
3. The server opens its own confirmation and proceeds only if you explicitly accept.
4. The one-time preview is sent once and, where supported, read back.

After setup, ask Codex to call `canvas_test_confirmation`. It exercises the real confirmation
gate but cannot contact or change Canvas. If you prefer that every apply tool refuse outright,
register the server using `connect_canvas.py setup-info --read-only`.

In `confirm` mode, before any `apply` tool sends anything, **the server itself** asks you through
a plain-language MCP confirmation dialog (an "elicitation") generated from the stored preview. For
example: **This will update question 10393750 in quiz 682181 in course 293855. Set question points
possible to 2.** It does not show an HTTP verb, URL, JSON, braces, or brackets. Rubric confirmations
similarly name the rubric or assignment and affected students in ordinary language. The complete
prepared preview remains the authoritative place to review every detail, while this short summary
helps you notice if a different target or visible change was substituted at the final step. It is
not a second complete rendering of every field. The server proceeds only if you select **Apply this
exact change** and submit that choice. **Do not apply** is the safe default. Codex currently labels
the submit button **Continue**; that button is part of Codex, while the two choices are supplied by
this server. Selecting **Do not apply**, choosing **Skip**, closing the dialog, an error, or a Codex
that cannot show the dialog all refuse the write. Each `preview_id` works once and expires after 10
minutes (`--preview-ttl` sets 60 to 3600 seconds).

**Why the server asks you itself.** Codex does mark tools annotated as destructive and, by
default, asks before running them. That prompt cannot be relied on alone:

- a per-tool `approval_mode = "approve"` in your Codex config removes it;
- `approvals_reviewer = "auto_review"` lets a model approve in your place;
- headless `codex exec` cancels every confirmation. With this server, writes then simply fail,
  which is the safe outcome.

So you may see two prompts for one change: Codex's, then the server's. The server's prompt is the
independent final choice tied to the prepared request. Keep `approvals_reviewer = "user"` in your
Codex config, and never set `approval_mode = "approve"` on this server's `apply_*` tools.

The automated suite tests both supported MCP protocol modes and proves that declining, cancelling,
missing elicitation support, and malformed answers all send nothing. Client UI presentation is
not claimed until `canvas_test_confirmation` is run in that client; that check itself cannot call
Canvas.

**If a result says `WRITE STATUS UNCERTAIN`**, the request was sent but its effect could not be
confirmed. Do not retry. Look at the object in Canvas (or read it back) first.

## Rubrics

Rubric creation is included in the standard tool set. It uses the same preview, personal
confirmation, one-time apply, and read-back workflow as other writes. Batch rubric grading is a
separate advanced opt-in: register with `--enable-rubric-grading`. It also needs
`--writes confirm` to change anything.

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
  what the task needs, and use `fields` to trim results. Your
  institution's rules on student records apply.
- **Nothing is kept.** Previews live in memory and disappear when Codex stops the server. There
  is no log, database or telemetry.

## Updating

Published versions are immutable Git tags (`v0.1.0`, `v0.1.1`, …), each described in
`CHANGELOG.md`. Nothing updates itself. Do not treat the moving `main` branch as a release.

**Refresh the skill only** (a quick repair that keeps the current server release):

```text
Use the canvas-mcp checkout on this local host: `~/canvas-mcp` and `.venv/bin/python` on macOS,
or `$HOME\canvas-mcp` and `.\.venv\Scripts\python.exe` in native Windows PowerShell. Run
`connect_canvas.py install-skill`, show me the preview, and wait. After I approve, run it again
with `--apply`, report the verified backup and result, then tell me to restart Codex. Do not edit
Codex configuration or call Canvas. Stop if this is Linux or WSL.
```

**Update everything** (recommended; updates the tagged project and its matching skill package together):

```text
Use the canvas-mcp checkout on this local host: `~/canvas-mcp` and `.venv/bin/python` on macOS,
or `$HOME\canvas-mcp` and `.\.venv\Scripts\python.exe` in native Windows PowerShell. Run
`update.py check`, show me the exact current and target tags, commits, and release notes, then
wait. After I approve that named tag, run `update.py apply TAG` with that exact tag. Report the
test and skill-backup results and tell me to restart Codex. Do not edit config.toml or call
Canvas. Stop if this is Linux or WSL.
```

**One-time update from v0.1.0:** that first release predates `update.py`. Use this short Codex
prompt once; later releases use the command above:

```text
Upgrade my existing canvas-mcp checkout from v0.1.0 to exactly v0.1.4. It is at
`~/canvas-mcp` on macOS or `$HOME\canvas-mcp` in native Windows PowerShell. Stop on Linux or WSL.
Read AGENTS.md. Verify the checkout is clean, origin is
https://github.com/chiptoe-svg/canvas-mcp.git, and remote v0.1.4 is contained in origin/main.
Show me its commit and changelog, then wait. After I approve, switch detached to that exact
commit, reinstall ".[test]", run pytest, and run `connect_canvas.py install-skill --apply` with
the checkout's virtual-environment Python. Do not edit config.toml, request a token, or call Canvas.
```

Do not download only a newer `SKILL.md` or reference file from a different release: the skill
package describes this server's exact tools and safeguards, so the installed package and server
tag should match.

For the manual route, move an unedited checkout to a specific published version. These commands
are for macOS Terminal; in Windows PowerShell, use `.\.venv\Scripts\pip` and
`.\.venv\Scripts\pytest` for the last two commands:

```sh
cd canvas-mcp
git status                  # stop and ask Codex for help if this shows local edits
git fetch --tags
git switch --detach v0.1.4  # replace with the release you reviewed
.venv/bin/pip install -e .  # picks up any new dependency
.venv/bin/pytest            # optional: confirm it still passes
```

Record the tag and commit Codex reports. If you edit this project, create a branch first rather
than editing a detached release checkout. If `git status` shows changes, do not switch releases
until you have reviewed and saved them. Common options for an experienced Git user are:

- Keep your edits on your own branch (`git switch -c my-changes`, then commit them).
- Ask Codex to compare your branch with the new tag and help carry the changes forward.

Read `CHANGELOG.md` before updating. The updater refreshes the matching skill package; the
changelog identifies any configuration-example change for you to review manually.

The skill installer copies references before activating their short `SKILL.md` entrypoint, backs up
every managed file it replaces, and rejects symlinks, Windows junctions, and installed-file changes
it observes.
It is not a security boundary against another malicious process already running as the same user,
which can race any user-owned file operation; close editors or sync tools that modify the skill
during installation and rerun if it reports a change.

## Uninstalling

From the project folder, use the command for your operating system:

```sh
.venv/bin/python uninstall.py                 # macOS
.\.venv\Scripts\python.exe uninstall.py       # Windows PowerShell
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
outside the project. This path has automated coverage with scratch directories but has not yet
been run through the complete uninstall journey on a real Windows computer.

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
| `extensions/rubrics.py` | Standard rubric creation and optional batch grading tools |
| `tests/` | Tests against an in-memory fake Canvas; no network |

If you change it with an AI coding agent, `AGENTS.md` gives the agent the project's rules.
Run `pytest` after any change. The tests encode the safety rules, so a failing test usually
means a rule was loosened.
