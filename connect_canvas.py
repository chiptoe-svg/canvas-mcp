"""Connect this computer's canvas-mcp to your Canvas account.

    .venv/bin/python connect_canvas.py connect      # ask for URL + token, verify, save
    .venv/bin/python connect_canvas.py status       # show URL and token validity
    .venv/bin/python connect_canvas.py reconnect    # replace token and optionally URL
    .venv/bin/python connect_canvas.py disconnect   # delete stored token and URL
    .venv/bin/python connect_canvas.py setup-info   # print setup commands; change nothing
    .venv/bin/python connect_canvas.py install-skill  # preview matching skill installation

Run it yourself in a terminal. The token is typed at a hidden prompt, checked against
Canvas's /api/v1/users/self, and saved only in the macOS Keychain or Windows Credential
Manager. It is never printed, logged, put in a file, or accepted as an argument.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config
from canvas_client import CanvasClient, CanvasError

TOKEN_HELP = ("Create a token in Canvas: Account > Settings > Approved Integrations > "
              "+ New Access Token. Follow your institution's policy on personal access tokens.")
SKILL_SOURCE = Path(__file__).resolve().with_name("SKILL.md")


def verify(base_url: str, token: str) -> dict:
    """Call /api/v1/users/self with a candidate token. Returns id and name only."""
    client = CanvasClient(base_url, lambda: token)
    me = client.get("users/self")
    if not isinstance(me, dict) or "id" not in me:
        raise CanvasError("Canvas did not return a user for this token")
    return {"id": me.get("id"), "name": me.get("name") or me.get("short_name")}


def prompt_url(default: str | None = None) -> str:
    suffix = " [%s]" % default if default else ""
    while True:
        raw = input("Canvas URL (e.g. https://school.instructure.com)%s: " % suffix).strip()
        try:
            base_url = config.normalize_base_url(raw or default or "")
        except config.ConfigError as err:
            print("That URL was not accepted: %s. Please try again." % err, file=sys.stderr)
            continue
        print("Token destination: %s" % base_url)
        return base_url


def prompt_token() -> str:
    print(TOKEN_HELP)
    token = getpass.getpass("Canvas access token (input hidden): ").strip()
    if not token:
        raise config.ConfigError("no token entered; nothing was saved")
    return token


def _optional_token(host: str) -> str | None:
    try:
        return config.read_token(host)
    except config.ConfigError:
        return None


def _restore_token(host: str, previous: str | None) -> None:
    """Restore exactly the credential that existed before an attempted connection."""
    if previous is not None:
        config.save_token(host, previous)
        if config.read_token(host) != previous:
            raise config.ConfigError("credential restore could not be verified")
        return
    if _optional_token(host) is None:
        return
    if not config.delete_token(host) or _optional_token(host) is not None:
        raise config.ConfigError("new credential could not be removed")


def _settings_identity() -> tuple | None:
    try:
        stat = config.config_path().stat(follow_symlinks=False)
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _connection_is_exactly_saved(base_url: str, token: str, current,
                                 previous_identity: tuple | None) -> bool:
    """True only when both persisted settings and credential are the candidate pair."""
    try:
        settings = config.load_settings()
        exact = settings.base_url == base_url and config.read_token(settings.host) == token
        if not exact:
            return False
        # A same-host reconnect already had identical settings text. Require proof that the
        # atomic settings replacement occurred; otherwise a pre-write failure would look saved
        # merely because the candidate token was written first.
        return (current is None or current.base_url != base_url or
                _settings_identity() != previous_identity)
    except Exception:
        return False


def cmd_connect(args, *, replace: bool = False) -> int:
    config.check_keyring_backend()
    current = None
    try:
        current = config.load_settings()
    except config.ConfigError:
        pass
    if current and not replace and config.has_token(current.host):
        print("Already connected to %s. Use `reconnect` to replace the token." % current.base_url)
        return 0
    base_url = prompt_url(current.base_url if current else None)
    token = prompt_token()
    try:
        who = verify(base_url, token)
    except CanvasError as err:
        print("Not saved: %s" % _clean(str(err), token), file=sys.stderr)
        return 2
    new_host = config.host_of(base_url)
    destination_token = _optional_token(new_host)
    previous_settings_identity = _settings_identity()
    try:
        config.save_token(new_host, token)
        path = config.save_settings(config.Settings(base_url))
    except BaseException as err:
        if _connection_is_exactly_saved(base_url, token, current, previous_settings_identity):
            path = config.config_path()
            print("Warning: the connection was saved before setup was interrupted; continuing "
                  "with cleanup. Run `%s` afterward to verify it."
                  % config.connect_command("status"), file=sys.stderr)
        else:
            try:
                _restore_token(new_host, destination_token)
            except BaseException:
                del token, destination_token
                raise config.ConfigError(
                    "Canvas accepted the token, but the connection could not be saved and the "
                    "credential rollback also failed. The credential for %s in %s may remain "
                    "changed. Status checks only the active saved URL; inspect that named entry "
                    "in the credential store and do not paste the token into chat."
                    % (new_host, _store_name())) from None
            restored = "previous connection was restored" if current else "nothing was changed"
            del token, destination_token
            if not isinstance(err, Exception):
                raise
            raise config.ConfigError(
                "Canvas accepted the token, but the credential store or settings could not be "
                "saved (%s); %s" % (type(err).__name__, restored)) from None
    if current and current.host != new_host:
        try:
            if config.has_token(current.host) and not config.delete_token(current.host):
                raise config.ConfigError("old credential could not be removed")
        except BaseException:
            print("Warning: connected successfully, but the old token entry for %s could not be "
                  "removed from %s." % (current.host, _store_name()), file=sys.stderr)
    del token
    del destination_token
    print("Connected to %s as %s (Canvas user %s)." % (base_url, who["name"], who["id"]))
    print("Token saved in %s. Settings: %s" % (_store_name(), path))
    return 0


def cmd_status(args) -> int:
    try:
        settings = config.load_settings()
    except config.ConfigError as err:
        print(str(err))
        return 1
    print("Canvas URL:   %s" % settings.base_url)
    print("Token store:  %s" % _store_name())
    if not config.has_token(settings.host):
        print("Token:        not stored - run: %s" % config.connect_command())
        return 1
    try:
        client = CanvasClient.from_settings(settings)
        me = client.get("users/self")
        print("Token:        valid for %s (Canvas user %s)" % (
            me.get("name") or me.get("short_name"), me.get("id")))
        return 0
    except CanvasError as err:
        print("Token:        stored, but Canvas check failed: %s" % client.scrub(str(err)))
        return 2


def cmd_disconnect(args) -> int:
    try:
        settings = config.load_settings()
    except config.ConfigError:
        print("Nothing to disconnect.")
        return 0
    removed = config.delete_token(settings.host)
    if not removed and config.has_token(settings.host):
        print("Could not remove the stored token for %s from %s; kept the saved URL so the "
              "credential is not stranded. Unlock or inspect the credential store and try again."
              % (settings.host, _store_name()), file=sys.stderr)
        return 2
    config.delete_settings()
    print("Removed %s and the saved Canvas URL." % ("the stored token" if removed else "settings"))
    print("To revoke the token itself, delete it in Canvas under Account > Settings > "
          "Approved Integrations.")
    return 0


def _store_name() -> str:
    return "Windows Credential Manager" if sys.platform == "win32" else "macOS Keychain"


def _clean(text: str, token: str) -> str:
    return text.replace(token, "<redacted>") if token else text


def _toml_string(value: str) -> str:
    return json.dumps(value)


def advanced_config(python: str, server: str, *, enable_rubrics: bool = False) -> str:
    """A complete TOML fragment with the top-level setting before any table."""
    arguments = [server, "--writes", "confirm"]
    if enable_rubrics:
        arguments.append("--enable-rubrics")
    lines = [
        'approvals_reviewer = "user"',
        "",
        "[mcp_servers.canvas]",
        "command = %s" % _toml_string(python),
        "args = [%s]" % ", ".join(_toml_string(value) for value in arguments),
        'default_tools_approval_mode = "writes"',
    ]
    if enable_rubrics:
        lines.insert(-1, "tool_timeout_sec = 600")
    return "\n".join(lines)


def cmd_setup_info(args) -> int:
    """Print exact local setup commands. This command never edits Codex configuration."""
    python = str(Path(sys.executable).absolute())
    project = Path(__file__).resolve().parent
    server = str(project / "canvas_mcp.py")
    skill = str(project / "SKILL.md")
    print("This command only prints instructions; it does not edit your Codex configuration.\n")
    print("Connect Canvas (run this yourself so the token stays in the hidden terminal prompt):")
    print("  %s\n" % config.connect_command())
    print("Read-only Codex setup (recommended first):")
    print("  %s\n" % config.shell_command(["codex", "mcp", "add", "canvas", "--", python, server]))
    print("Verify after adding it:")
    print("  codex mcp get canvas\n")
    if getattr(args, "writes", False):
        label = "Optional writes + rubric tools" if getattr(args, "enable_rubrics", False) else "Optional writes"
        print("%s (advanced; merge carefully, do not append blindly)." % label)
        print("The first line below must stay before every [table] in config.toml:")
        print(advanced_config(python, server,
                              enable_rubrics=getattr(args, "enable_rubrics", False)) + "\n")
    print("Install this release's matching Codex skill (preview first):")
    print("  %s" % config.shell_command([python, Path(__file__).resolve(), "install-skill"]))
    print("Skill source: %s" % skill)
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_linklike(path: Path) -> bool:
    junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or junction():
        return True
    try:
        # Python 3.10/3.11 do not expose Path.is_junction(). Windows marks junctions and
        # other reparse points with FILE_ATTRIBUTE_REPARSE_POINT (0x400).
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _regular_fingerprint(path: Path, *, allow_missing: bool) -> tuple | None:
    if _is_linklike(path):
        raise config.ConfigError("refusing symbolic link or junction: %s" % path)
    try:
        details = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise config.ConfigError("required file is missing: %s" % path) from None
    if not stat.S_ISREG(details.st_mode):
        raise config.ConfigError("expected a regular file: %s" % path)
    return (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)


def _stable_file_bytes(path: Path, *, allow_missing: bool = False) -> tuple[bytes | None, tuple | None]:
    before = _regular_fingerprint(path, allow_missing=allow_missing)
    if before is None:
        return None, None
    content = path.read_bytes()
    after = _regular_fingerprint(path, allow_missing=False)
    if after != before:
        raise config.ConfigError("file changed while it was being inspected: %s" % path)
    return content, before


def _codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _check_path_chain(path: Path) -> None:
    """Refuse any existing symlink or non-directory in a directory path."""
    current = path
    chain = []
    while True:
        chain.append(current)
        if current.parent == current:
            break
        current = current.parent
    for item in reversed(chain):
        if _is_linklike(item):
            raise config.ConfigError("refusing symbolic link or junction in skill path: %s" % item)
        if item.exists() and not item.is_dir():
            raise config.ConfigError("skill path component is not a directory: %s" % item)


def _make_directory(path: Path) -> None:
    _check_path_chain(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for item in reversed(missing):
        item.mkdir()
        if _is_linklike(item) or not item.is_dir():
            raise config.ConfigError("could not create a safe skill directory: %s" % item)


def install_skill(*, apply: bool = False, source: Path | None = None,
                  codex_home: Path | None = None) -> dict:
    """Preview or safely install this release's SKILL.md, with a verified backup."""
    source = Path(source or SKILL_SOURCE)
    codex_home = Path(codex_home or _codex_home())
    skill_dir = codex_home / "skills" / "canvas-mcp"
    destination = skill_dir / "SKILL.md"
    backup_root = codex_home / "backups" / "canvas-mcp"
    source_content, source_fingerprint = _stable_file_bytes(source)
    _check_path_chain(skill_dir)
    _check_path_chain(backup_root)
    destination_content, destination_fingerprint = _stable_file_bytes(destination, allow_missing=True)
    source_hash = _bytes_sha256(source_content)
    if destination_content is not None and _bytes_sha256(destination_content) == source_hash:
        return {"changed": False, "source": source, "destination": destination, "backup": None}
    if not apply:
        return {"changed": True, "source": source, "destination": destination,
                "backup": "required" if destination_content is not None else None}

    backup = None
    replaced = False
    try:
        _make_directory(skill_dir)
        if destination_content is not None:
            _make_directory(backup_root)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup_dir = backup_root / stamp
            backup_dir.mkdir()
            backup = backup_dir / "SKILL.md"
            with backup.open("xb") as handle:
                handle.write(destination_content)
                handle.flush()
                os.fsync(handle.fileno())
            backup_content, _ = _stable_file_bytes(backup)
            if backup_content != destination_content:
                raise config.ConfigError("skill backup verification failed; installed skill was not changed")

        fd, temp_name = tempfile.mkstemp(prefix=".SKILL.md.", suffix=".tmp", dir=skill_dir)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(source_content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_content, _ = _stable_file_bytes(temporary)
            if temporary_content != source_content:
                raise config.ConfigError("temporary skill copy could not be verified")
            _check_path_chain(skill_dir)
            if _regular_fingerprint(source, allow_missing=False) != source_fingerprint:
                raise config.ConfigError("skill source changed before installation; nothing was replaced")
            if _regular_fingerprint(destination, allow_missing=True) != destination_fingerprint:
                raise config.ConfigError("installed skill changed after preview; nothing was replaced")
            os.replace(temporary, destination)
            replaced = True
            try:
                installed_content, _ = _stable_file_bytes(destination)
                verified = installed_content == source_content
            except config.ConfigError:
                verified = False
            if not verified:
                detail = "previous copy: %s" % backup if backup else "there was no previous copy"
                raise config.ConfigError("installed skill was replaced but verification failed; %s" % detail)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temporary.unlink()
            except (FileNotFoundError, UnboundLocalError):
                pass
    except config.ConfigError:
        raise
    except OSError as err:
        state = "destination may have changed" if replaced else "destination was not replaced"
        backup_note = "; verified backup: %s" % backup if backup else ""
        raise config.ConfigError("skill installation failed (%s); %s%s"
                                 % (type(err).__name__, state, backup_note)) from None
    return {"changed": True, "source": source, "destination": destination, "backup": backup}


def cmd_install_skill(args) -> int:
    result = install_skill(apply=args.apply)
    print("Skill source:      %s" % result["source"])
    print("Skill destination: %s" % result["destination"])
    if not result["changed"]:
        print("Already current; no files were changed.")
    elif not args.apply:
        print("Preview only; no files were changed.")
        if result["backup"]:
            print("The existing skill will be backed up before replacement.")
        print("To install it, run: %s" % config.shell_command(
            [Path(sys.executable).absolute(), Path(__file__).resolve(), "install-skill", "--apply"]))
    else:
        if result["backup"]:
            print("Verified backup:  %s" % result["backup"])
        print("Installed and checksum-verified. Restart Codex to load the skill.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Connect canvas-mcp to your Canvas account.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "status", "reconnect", "disconnect"):
        sub.add_parser(name)
    setup_parser = sub.add_parser("setup-info")
    setup_parser.add_argument("--writes", action="store_true",
                              help="print the optional write-enabled configuration block")
    setup_parser.add_argument("--enable-rubrics", action="store_true",
                              help="include optional rubric tools (requires --writes)")
    install_parser = sub.add_parser("install-skill")
    install_parser.add_argument("--apply", action="store_true",
                                help="install after previewing; backs up an existing skill")
    args = parser.parse_args(argv)
    if getattr(args, "enable_rubrics", False) and not getattr(args, "writes", False):
        parser.error("--enable-rubrics requires --writes")
    try:
        if args.command == "connect":
            return cmd_connect(args)
        if args.command == "reconnect":
            return cmd_connect(args, replace=True)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "setup-info":
            return cmd_setup_info(args)
        if args.command == "install-skill":
            return cmd_install_skill(args)
        return cmd_disconnect(args)
    except config.ConfigError as err:
        print(str(err), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; run `%s` to verify whether the connection changed."
              % config.connect_command("status"), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
