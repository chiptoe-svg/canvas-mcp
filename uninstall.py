"""Remove canvas-mcp from this computer.

    .venv/bin/python uninstall.py                  # macOS
    .\\.venv\\Scripts\\python.exe uninstall.py        # Windows PowerShell
    .venv/bin/python uninstall.py --keep-project   # keep this folder (and your edits)

It shows what it will remove and asks everything up front: one confirmation, then (to delete
the project folder) the folder's name. Only then does it remove, in this order:

1. the stored Canvas token (macOS Keychain / Windows Credential Manager) and the saved Canvas
   URL. If the token cannot be removed, nothing else is removed;
2. the Codex skill copy, ~/.codex/skills/canvas-mcp/;
3. this project's .venv;
4. this project folder. It is refused if the folder holds anything canvas-mcp does not ship
   (so a download folder is never swept away), and it warns about uncommitted, ignored,
   unpushed and stashed Git work.

On Windows a running Python cannot delete its own .venv or folder, so steps 3 and 4 print the
command to run once this script has exited.

It never edits Codex's config.toml; it shows you what to delete. It cannot revoke the token in
Canvas: do that under Account > Settings > Approved Integrations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
from pathlib import Path

import config

PROJECT_MARKERS = ("canvas_mcp.py", "connect_canvas.py", "canvas_client.py", "config.py", "uninstall.py")
# Everything a release ships at the top level, plus what setup and tests create. A folder
# holding anything else is not deleted. tests/test_uninstall.py checks this list is complete.
RELEASE_ENTRIES = {"README.md", "CHANGELOG.md", "SECURITY_REVIEW.md", "SKILL.md", "AGENTS.md", "pyproject.toml", ".gitignore",
                   "canvas_mcp.py", "canvas_client.py", "config.py", "connect_canvas.py",
                   "uninstall.py", "update.py", "extensions", "tests", "examples", "docs",
                   "references"}
RELEASE_NESTED_FILES = {
    "docs/index.html",
    "examples/codex-mcp-config.example.toml",
    "extensions/__init__.py",
    "extensions/rubrics.py",
    "references/rubrics.md",
    "references/writes.md",
    "tests/conftest.py",
    "tests/fake_canvas.py",
    "tests/test_client.py",
    "tests/test_config.py",
    "tests/test_connect.py",
    "tests/test_rubrics.py",
    "tests/test_server.py",
    "tests/test_uninstall.py",
    "tests/test_update.py",
}
RELEASE_DIRECTORIES = {name.split("/", 1)[0] for name in RELEASE_NESTED_FILES}
GENERATED_ENTRIES = {".git", ".venv", "__pycache__", ".pytest_cache", "canvas_mcp.egg-info",
                     ".DS_Store", "Thumbs.db", "desktop.ini"}      # the last three: Finder / Explorer
_GENERATED_NESTED = re.compile(r"(^|/)(\.venv|__pycache__|\.pytest_cache|[^/]*\.egg-info)(/|$)"
                               r"|\.py[cod]$|(^|/)(\.DS_Store|Thumbs\.db|desktop\.ini)$")
SKILL_NAME = "canvas-mcp"


class UninstallError(Exception):
    pass


# ------------------------------------------------------------------------------ locations
def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def skill_dir() -> Path:
    return codex_home() / "skills" / SKILL_NAME


def check_project_dir(path: Path) -> Path:
    """Refuse anything that is not clearly a canvas-mcp checkout, or that contains your home."""
    path = path.resolve()
    home = Path.home().resolve()
    if path == Path(path.anchor) or path == home or home.is_relative_to(path):
        raise UninstallError("refusing to treat %s as the project folder" % path)
    missing = [m for m in PROJECT_MARKERS if not (path / m).is_file()]
    if missing:
        raise UninstallError("%s does not look like a canvas-mcp folder (missing %s)"
                             % (path, ", ".join(missing)))
    return path


def foreign_entries(project: Path) -> list[str]:
    """Entries that canvas-mcp neither ships nor creates, including inside release folders."""
    known = RELEASE_ENTRIES | GENERATED_ENTRIES
    foreign = {p.name for p in project.iterdir() if p.name not in known}
    for directory in RELEASE_DIRECTORIES:
        root = project / directory
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(project).as_posix()
            if path.is_symlink():                  # uninstall removes the link, never its target
                continue
            if _GENERATED_NESTED.search(relative):
                continue
            if path.is_dir():
                if any(name.startswith(relative + "/") for name in RELEASE_NESTED_FILES):
                    continue
            elif relative in RELEASE_NESTED_FILES:
                continue
            foreign.add(relative)
    return sorted(foreign)


# ------------------------------------------------------------------------------ checks
def git_warnings(project: Path) -> list[str]:
    """What deleting the folder would lose that exists nowhere else."""
    if not (project / ".git").exists():
        return ["this folder is not a Git checkout; any edits in it exist only here"]
    checks = [
        (["status", "--porcelain"], "uncommitted changes", None),
        (["status", "--porcelain", "--ignored"], "git-ignored files that are not build output",
         lambda line: line.startswith("!! ") and not _GENERATED_NESTED.search(line[3:].strip('"'))),
        (["log", "HEAD", "--branches", "--not", "--remotes", "--oneline"],
         "commits not pushed to any remote", None),
        (["stash", "list"], "stashed changes", None),
    ]
    warnings = []
    for args, label, keep in checks:
        try:
            out = subprocess.run(["git", "-C", str(project)] + args, capture_output=True,
                                 text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return ["could not run git to check for local edits; assume there are some"]
        if out.returncode != 0:
            warnings.append("could not check for %s (git: %s)"
                            % (label, (out.stderr.strip().splitlines() or ["error"])[0]))
            continue
        lines = [l for l in out.stdout.splitlines() if l.strip() and (keep is None or keep(l))]
        if lines:
            warnings.append("%s: %d" % (label, len(lines)))
    return warnings


_SERVER_LINE = re.compile(r"""canvas_mcp\.py["']|["']-m["']\s*,\s*["']canvas_mcp["']""")


def _starts_this_server(table: dict) -> bool:
    """True when command/args run canvas_mcp.py, or `-m canvas_mcp`. Exact items only: a
    folder that merely contains "canvas_mcp" in its name does not count."""
    args = table.get("args") if isinstance(table.get("args"), list) else []
    items = [x for x in [table.get("command")] + args if isinstance(x, str)]
    if any(re.split(r"[\\/]", x)[-1] == "canvas_mcp.py" for x in items):
        return True
    return any(a == "-m" and b == "canvas_mcp" for a, b in zip(args, args[1:]))


def codex_config_mentions(project: Path) -> tuple[Path, list[str]]:
    """Where Codex's config.toml starts canvas_mcp.py: server names, or else line numbers."""
    path = codex_home() / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path, []
    try:
        import tomllib                             # Python 3.11+
        data = tomllib.loads(text)
    except Exception:                              # no tomllib, or a file it cannot parse
        return path, ["line %d" % n for n, line in enumerate(text.splitlines(), 1)
                      if _SERVER_LINE.search(line)]
    found = []

    def visit(table: dict, dotted: list) -> None:  # any table, e.g. under [profiles.x.mcp_servers]
        if dotted and _starts_this_server(table):
            found.append("the [%s] server" % ".".join(dotted))
        for key, value in table.items():
            if isinstance(value, dict):
                visit(value, dotted + [key])
    visit(data, [])
    return path, found


# ------------------------------------------------------------------------------ credentials
def credential_plan() -> dict:
    """Which token and settings file step 1 removes. Never raises."""
    path = config.config_path()
    if not path.exists():
        return {"host": None, "settings": None}
    try:
        return {"host": config.load_settings().host, "settings": path}
    except config.ConfigError:
        pass
    try:                                           # an invalid URL still names the token's host
        raw = json.loads(path.read_text(encoding="utf-8")).get("base_url") or ""
        raw = raw.strip()
        host = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw).hostname
    except Exception:
        host = None
    return {"host": (host or "").lower().rstrip(".") or None, "settings": path}


def manual_token_help() -> str:
    if sys.platform == "win32":
        return ("If a canvas-mcp token is still stored, remove it in Control Panel > Credential "
                "Manager > Windows Credentials (entries mentioning canvas-mcp).")
    return ("If a canvas-mcp token is still stored, remove it in Terminal with:\n"
            "  security delete-generic-password -s canvas-mcp\n"
            "(repeat until it reports that the item could not be found).")


def remove_credentials(plan: dict) -> list[str]:
    """Raises UninstallError if the token could not be removed; then nothing else is."""
    notes = []
    if plan["host"]:
        try:
            removed = config.delete_token(plan["host"])
        except Exception as err:
            raise UninstallError("could not remove the stored token for %s: %s"
                                 % (plan["host"], str(err) or type(err).__name__)) from None
        notes.append("Removed the stored token for %s." % plan["host"] if removed
                     else "No token was stored for %s. %s" % (plan["host"], manual_token_help()))
    else:
        notes.append("No saved Canvas connection. " + manual_token_help())
    if plan["settings"]:
        try:
            config.delete_settings()
            notes.append("Removed the saved Canvas URL.")
        except OSError as err:
            notes.append("Could not remove %s (%s); delete it by hand." % (plan["settings"], err))
        try:
            config.config_dir().rmdir()           # only if now empty
        except OSError:
            pass
    return notes


# ------------------------------------------------------------------------------ removal
def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(path)
    attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _remove_link(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)                            # a Windows directory link or junction


def unlink_links(root: Path) -> None:
    """Remove every symlink/junction inside root (never their targets). Run before a Windows
    Remove-Item, which in PowerShell 5.1 can follow junctions. Never walks a root that is a link."""
    if _is_link(root):
        raise OSError("%s is itself a link" % root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if _is_link(p):
                _remove_link(p)


def remove_tree(path: Path) -> str:
    if path.is_symlink():
        path.unlink()
        return "Removed the link %s." % path
    if not path.exists():
        return "%s was already gone." % path
    try:
        shutil.rmtree(path)
    except OSError as err:
        return "Could not fully remove %s: %s. Delete what is left by hand." % (path, err)
    return "Removed %s." % path


def remove_project(project: Path) -> str:
    """Delete marker files last so the folder remains identifiable until the final phase.

    An interruption during that final marker loop can leave a partial folder that needs manual
    cleanup; the safety check may correctly refuse it because it is no longer a complete project.
    """
    try:
        for child in project.iterdir():
            if child.name not in PROJECT_MARKERS:
                if child.is_symlink() or not child.is_dir():
                    child.unlink()
                else:
                    shutil.rmtree(child)
        for name in PROJECT_MARKERS:
            (project / name).unlink(missing_ok=True)
        project.rmdir()
    except OSError as err:
        return "Could not fully remove %s: %s. Delete what is left by hand." % (project, err)
    return "Removed %s." % project


def windows_delete_command(path: Path) -> str:
    text = str(path)
    for quote in "'‘’‚‛":        # every character PowerShell treats as '
        text = text.replace(quote, quote * 2)
    return "Remove-Item -LiteralPath '%s' -Recurse -Force" % text


def ask(input_fn, prompt: str) -> str | None:
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


# ------------------------------------------------------------------------------ main
def main(argv=None, *, project_dir: Path | None = None, input_fn=input) -> int:
    parser = argparse.ArgumentParser(description="Remove canvas-mcp from this computer.")
    parser.add_argument("--keep-project", action="store_true",
                        help="keep this project folder; remove everything else")
    args = parser.parse_args(argv)
    windows = sys.platform == "win32"
    try:
        project = check_project_dir(project_dir or Path(__file__).parent)
    except UninstallError as err:
        print("Nothing was removed: %s" % err, file=sys.stderr)
        return 2
    creds = credential_plan()
    venv = project / ".venv"
    has_venv = (venv / "pyvenv.cfg").is_file()
    skill = skill_dir()
    has_skill = skill.exists() or skill.is_symlink()
    delete_project = not args.keep_project
    foreign = foreign_entries(project) if delete_project else []
    warnings = git_warnings(project) if delete_project and not foreign else []

    print("canvas-mcp uninstall will remove:")
    if creds["host"]:
        print("  - the Canvas token for %s, and the saved URL (%s)" % (creds["host"], creds["settings"]))
    elif creds["settings"]:
        print("  - the saved settings %s (no Canvas host could be read from it)" % creds["settings"])
    else:
        print("  - (no saved Canvas connection)")
    if has_skill:
        print("  - the Codex skill %s" % skill)
    if delete_project and foreign:
        print("  - NOT the project folder %s: it also holds %s, which canvas-mcp did not put there."
              % (project, ", ".join(foreign)))
        print("    Move those out and run uninstall again, or delete the folder yourself.")
        delete_project = False
    if delete_project:
        print("  - this project folder, including .venv: %s  (you will be asked to type its name)" % project)
    elif has_venv:
        print("  - the virtual environment %s" % venv)
    for w in warnings:
        print("  ! %s" % w)

    answer = ask(input_fn, "Continue? [y/N] ")
    if answer is None or answer.strip().lower() not in ("y", "yes"):
        print("\nCancelled; nothing was removed.")
        return 1
    if delete_project:
        answer = ask(input_fn, "Type the folder name %r to delete it, or press Enter to keep it: " % project.name)
        if answer is None:
            print("\nCancelled; nothing was removed.")
            return 1
        if answer.strip() != project.name:
            print("The folder will be kept.")
            delete_project = False

    try:
        for note in remove_credentials(creds):
            print(note)
    except UninstallError as err:
        print("Stopped: %s. Nothing else was removed." % err, file=sys.stderr)
        return 2
    if has_skill:
        print(remove_tree(skill))

    later = []
    target = project if delete_project else (venv if has_venv else None)
    if target is not None:
        if delete_project:                          # re-check right before deleting
            try:
                check_project_dir(project)
                if foreign_entries(project):
                    raise UninstallError("new files appeared in %s" % project)
            except UninstallError as err:
                print("Kept the project folder: %s." % err)
                target = venv if has_venv else None
        if target is not None and windows and _is_link(target):
            try:
                _remove_link(target)                # only the link; never what it points to
                print("Removed the link %s." % target)
            except OSError as err:
                print("Could not remove the link %s (%s); delete it by hand." % (target, err))
            target = None
        elif target is not None and windows:
            try:
                unlink_links(target)
            except OSError as err:
                print("Could not remove links inside %s (%s); delete it by hand." % (target, err))
                target = None
            if target is not None:
                later.append(windows_delete_command(target))
        elif target == project:
            print(remove_project(project))
        elif target is not None:
            print(remove_tree(target))

    path, mentions = codex_config_mentions(project)
    print()
    if mentions:
        print("Codex still starts canvas_mcp.py. In %s, delete %s" % (path, ", ".join(mentions)))
        print("(with any .tools or .env tables under it), then restart Codex.")
    print("To revoke the token itself, delete it in Canvas under Account > Settings > "
          "Approved Integrations.")
    if later:
        print("After this script has exited, and from a folder outside %s, run in PowerShell:" % project)
        for cmd in later:
            print("  " + cmd)
    if target == project and not windows:
        try:
            inside = Path.cwd().resolve().is_relative_to(project)
        except OSError:
            inside = True
        if inside:
            print("Your shell's current folder was deleted: cd somewhere else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
