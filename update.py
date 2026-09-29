"""Safely move an unedited canvas-mcp checkout to a named published release.

This updater never edits Codex configuration, reads a Canvas token, or calls Canvas. ``verify``
proves that the checkout exactly matches one named annotated remote tag. ``check`` fetches release
metadata and prints the newest stable tag. ``apply`` requires that exact tag as an argument,
rechecks it against the remote, switches to its immutable commit, installs and tests the release,
then installs the matching Codex skill with a verified backup.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

OFFICIAL_ORIGIN = "https://github.com/chiptoe-svg/canvas-mcp.git"
RELEASE_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
Plan = namedtuple("Plan", "current_tag current_commit target_tag target_commit notes")


class UpdateError(Exception):
    pass


def _git(project_dir: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(project_dir), *args], capture_output=True, text=True)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise UpdateError("git %s failed: %s" % (" ".join(args), detail[-1] if detail else "error"))
    return result.stdout.strip()


def _version(tag: str) -> tuple[int, int, int]:
    match = RELEASE_TAG.fullmatch(tag)
    if not match:
        raise UpdateError("release must be an exact stable tag such as v0.1.1")
    return tuple(int(value) for value in match.groups())


def _remote_tags(project_dir: Path) -> dict[str, str]:
    refs = _remote_tag_refs(project_dir)
    return {tag: peeled or direct for tag, (direct, peeled) in refs.items()}


def _remote_tag_refs(project_dir: Path) -> dict[str, tuple[str, str | None]]:
    """Return stable tag -> (tag object or commit, peeled commit when annotated)."""
    output = _git(project_dir, "ls-remote", "--tags", "origin", "refs/tags/v*")
    direct, peeled = {}, {}
    for line in output.splitlines():
        try:
            commit, ref = line.split("\t", 1)
        except ValueError:
            continue
        suffix = ref.removeprefix("refs/tags/")
        if suffix.endswith("^{}"):
            peeled[suffix[:-3]] = commit
        else:
            direct[suffix] = commit
    return {tag: (commit, peeled.get(tag)) for tag, commit in direct.items()
            if RELEASE_TAG.fullmatch(tag)}


def _release_notes(changelog: str, tag: str) -> str:
    version = tag.removeprefix("v")
    match = re.search(r"(?ms)^## \[%s\][^\n]*\n(.*?)(?=^## |\Z)" % re.escape(version),
                      changelog)
    if not match:
        raise UpdateError("target CHANGELOG.md has no [%s] release entry" % version)
    return match.group(0).strip()


def _require_clean(project_dir: Path) -> None:
    if _git(project_dir, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("checkout has local changes; save or remove them before updating")


def _require_plain_index(project_dir: Path) -> None:
    assumed = [line[2:] for line in _git(project_dir, "ls-files", "-v").splitlines()
               if line and line[0].islower()]
    skipped = [line[2:] for line in _git(project_dir, "ls-files", "-t").splitlines()
               if line.startswith("S ")]
    hidden = sorted(set(assumed + skipped))
    if hidden:
        shown = ", ".join(hidden[:5]) + (" …" if len(hidden) > 5 else "")
        raise UpdateError("checkout uses hidden index flags (assume-unchanged or skip-worktree): %s"
                          % shown)


def verify_exact_release(project_dir: Path, tag: str, *,
                         expected_origin: str = OFFICIAL_ORIGIN) -> str:
    """Prove HEAD is the clean commit named by one annotated tag on the expected remote."""
    project_dir = Path(project_dir).resolve()
    _version(tag)
    if not (project_dir / ".git").exists():
        raise UpdateError("not a Git checkout: %s" % project_dir)
    _require_plain_index(project_dir)
    _require_clean(project_dir)
    origin = _git(project_dir, "remote", "get-url", "origin")
    if origin != expected_origin:
        raise UpdateError("refusing unexpected origin %r; expected %r" % (origin, expected_origin))
    refs = _remote_tag_refs(project_dir)
    if tag not in refs:
        raise UpdateError("release %s does not exist in origin" % tag)
    remote_object, remote_commit = refs[tag]
    if remote_commit is None:
        raise UpdateError("remote release %s is not an annotated tag" % tag)
    head = _git(project_dir, "rev-parse", "HEAD")
    if head != remote_commit:
        raise UpdateError("HEAD %s does not exactly match remote release %s (%s)"
                          % (head, tag, remote_commit))
    if _git(project_dir, "cat-file", "-t", "refs/tags/%s" % tag) != "tag":
        raise UpdateError("local %s is not an annotated release tag" % tag)
    local_object = _git(project_dir, "rev-parse", "refs/tags/%s" % tag)
    if local_object != remote_object:
        raise UpdateError("local %s tag object does not match origin" % tag)
    if _git(project_dir, "rev-parse", "refs/tags/%s^{}" % tag) != remote_commit:
        raise UpdateError("local %s does not peel to HEAD" % tag)
    return head


def build_plan(project_dir: Path, target_tag: str | None = None, *, fetch: bool = True,
               expected_origin: str = OFFICIAL_ORIGIN) -> Plan:
    project_dir = Path(project_dir).resolve()
    if not (project_dir / ".git").exists():
        raise UpdateError("not a Git checkout: %s" % project_dir)
    _require_clean(project_dir)
    origin = _git(project_dir, "remote", "get-url", "origin")
    if origin != expected_origin:
        raise UpdateError("refusing unexpected origin %r; expected %r" % (origin, expected_origin))
    if fetch:
        _git(project_dir, "fetch", "--prune", "origin", "main")
        _git(project_dir, "fetch", "--prune", "--tags", "origin")
    tags = _remote_tags(project_dir)
    if not tags:
        raise UpdateError("origin publishes no stable vX.Y.Z tags")
    head = _git(project_dir, "rev-parse", "HEAD")
    current = sorted((tag for tag, commit in tags.items() if commit == head), key=_version)
    if not current:
        raise UpdateError("HEAD does not exactly match a published stable release tag")
    current_tag = current[-1]
    selected = target_tag or max(tags, key=_version)
    _version(selected)
    if selected not in tags:
        raise UpdateError("release %s does not exist in origin" % selected)
    if _version(selected) < _version(current_tag):
        raise UpdateError("refusing downgrade from %s to %s" % (current_tag, selected))
    target_commit = tags[selected]
    if _git(project_dir, "merge-base", "--is-ancestor", target_commit, "origin/main", check=False):
        raise UpdateError("release %s is not contained in origin/main" % selected)
    changelog = _git(project_dir, "show", "%s:CHANGELOG.md" % target_commit)
    notes = _release_notes(changelog, selected)
    return Plan(current_tag, head, selected, target_commit, notes)


def _print_plan(plan: Plan) -> None:
    print("Current release: %s (%s)" % (plan.current_tag, plan.current_commit))
    print("Target release:  %s (%s)" % (plan.target_tag, plan.target_commit))
    print("\n%s" % plan.notes)


def install_dependencies_and_test(project_dir: Path) -> None:
    commands = [
        [sys.executable, "-m", "pip", "install", "-e", ".[test]"],
        [sys.executable, "-m", "pytest", "-q"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=project_dir)
        if result.returncode:
            raise UpdateError("command failed after checkout: %s" % " ".join(command))


def install_skill(project_dir: Path) -> None:
    command = [sys.executable, str(project_dir / "connect_canvas.py"),
               "install-skill", "--apply"]
    result = subprocess.run(command, cwd=project_dir)
    if result.returncode:
        raise UpdateError("matching skill installation failed in the target release")


def cmd_check(project_dir: Path, expected_origin: str) -> int:
    import config

    plan = build_plan(project_dir, expected_origin=expected_origin)
    _print_plan(plan)
    if plan.current_tag == plan.target_tag:
        print("\nAlready on the newest published stable release. No files were changed.")
    else:
        command = [Path(sys.executable).absolute(), Path(__file__).resolve(), "apply", plan.target_tag]
        print("\nNo files were changed. After reviewing that exact release, run:")
        print("  %s" % config.shell_command(command))
    return 0


def cmd_verify(project_dir: Path, tag: str, expected_origin: str) -> int:
    commit = verify_exact_release(project_dir, tag, expected_origin=expected_origin)
    print("Verified exact release: %s (%s). Checkout is clean." % (tag, commit))
    return 0


def cmd_apply(project_dir: Path, tag: str, expected_origin: str) -> int:
    first = build_plan(project_dir, tag, expected_origin=expected_origin)
    _print_plan(first)
    second = build_plan(project_dir, tag, fetch=False, expected_origin=expected_origin)
    if (second.current_commit != first.current_commit or
            second.target_commit != first.target_commit or second.target_tag != first.target_tag):
        raise UpdateError("release state changed during approval; nothing was switched")
    if first.current_commit != first.target_commit:
        _git(project_dir, "checkout", "--detach", first.target_commit)
    try:
        install_dependencies_and_test(project_dir)
        _require_clean(project_dir)
        install_skill(project_dir)
    except BaseException:
        print("Update stopped after checkout. The checkout is now at %s (%s); do not assume the "
              "installation is usable until the reported failure is resolved."
              % (first.target_tag, first.target_commit), file=sys.stderr)
        raise
    verb = "Verified and refreshed" if first.current_commit == first.target_commit else "Updated to"
    print("%s %s (%s). Restart Codex to load the matching server and skill."
          % (verb, first.target_tag, first.target_commit))
    return 0


def main(argv=None, *, project_dir: Path | None = None,
         expected_origin: str = OFFICIAL_ORIGIN) -> int:
    parser = argparse.ArgumentParser(description="Update canvas-mcp to a reviewed release tag.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify_parser = sub.add_parser("verify", help="prove HEAD exactly matches one named remote release")
    verify_parser.add_argument("tag")
    sub.add_parser("check", help="show the newest published stable release; change no project files")
    apply_parser = sub.add_parser("apply", help="install one exact release shown by check")
    apply_parser.add_argument("tag")
    args = parser.parse_args(argv)
    project_dir = Path(project_dir or Path(__file__).resolve().parent)
    try:
        if args.command == "verify":
            return cmd_verify(project_dir, args.tag, expected_origin)
        if args.command == "check":
            return cmd_check(project_dir, expected_origin)
        return cmd_apply(project_dir, args.tag, expected_origin)
    except UpdateError as err:
        print("Update refused: %s" % err, file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nUpdate cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
