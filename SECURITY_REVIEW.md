# Security review record

## v0.1.1 - 2026-09-28

Three independent adversarial reviews covered every v0.1.1 path that can delete or overwrite a
credential or file. Reviewers were instructed to use temporary trees and fake keyrings, never a
real Canvas course or token. Findings were reproduced, fixed, regression-tested, and re-reviewed.

### Connection persistence and disconnect

The reviewer exercised first connection, same-host and new-host reconnect, pre-existing
destination credentials, interruptions around settings replacement, rollback failure, credential
read failure, and disconnect failure. Initial review found that an interruption immediately after
settings replacement could leave settings and token state mismatched, rollback guidance could hide
an orphan credential, and disconnect could remove settings while a token remained. Re-review found
one more credential-read exception that escaped the commit probe. The fixes now verify the exact
persisted pair, distinguish a same-host atomic settings replacement by file identity, fail closed
into rollback on ordinary probe errors, retain settings when token deletion fails, and name the
possibly affected host and credential store. Final acceptance used only a fake keyring and scratch
configuration directories.

### Uninstall manifest and deletion order

The reviewer probed foreign files and directories at multiple depths, ignored-looking secrets,
late-arriving files, hardlinks, symlinks, broken symlinks, marker deletion order, and the new
release manifest entries. One inaccurate comment claimed every marker-phase interruption could be
re-run; it now states that a final-phase interruption may require manual cleanup. Final review
accepted the deletion behavior and confirmed that link targets and foreign files were preserved.
One custom probe accidentally allowed a read-only scan of the maintainer's real Codex config; it
made no change and accessed neither the Keychain nor Canvas.

### Tagged updater and skill installer

The reviewer probed dirty trees, annotated tags, moved remote tags, test-hook mutations, use of old
versus target-release code, same-tag repair, symlink and junction paths, concurrent destination
edits, backup integrity, and failures before and after atomic replacement. Initial findings showed
that a test could mutate the installed skill, the old installer module remained loaded after
checkout, same-tag apply skipped repair, filesystem races were not reported honestly, and Python
3.10/3.11 on Windows lacked junction detection. The fixes recheck a clean tree after tests, invoke
the target installer in a fresh process, refresh the current tag, snapshot and compare source and
destination files, report verified backups and post-replacement uncertainty, detect Windows
reparse points, and document the same-user race boundary. Final review accepted the implementation.

### Evidence and limits

- The complete automated suite passed after the fixes; tests use the in-memory fake Canvas and a
  fake keyring.
- Each new regression was demonstrated failing before its fix and passing afterward.
- `canvas_test_confirmation` exercises the same MCP elicitation gate without constructing or
  sending a Canvas request. Automated protocol tests cover accept, decline, cancel, malformed
  answers, missing elicitation support, and both supported MCP protocol modes.
- No real Canvas write was used during development, as required by `AGENTS.md`. The documentation
  does not claim that a particular Codex desktop UI presentation was hand-tested. Users can run
  the no-Canvas confirmation test in their own client after enabling writes.
- Files owned by the same user are not a security boundary against another malicious process
  running as that user and racing filesystem changes. The installer detects observed link and
  content changes and fails closed; this residual local race is documented for users.
