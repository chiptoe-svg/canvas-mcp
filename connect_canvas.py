"""Connect this computer's canvas-mcp to your Canvas account.

    .venv/bin/python connect_canvas.py connect      # ask for URL + token, verify, save
    .venv/bin/python connect_canvas.py status       # show URL and token validity
    .venv/bin/python connect_canvas.py reconnect    # replace token and optionally URL
    .venv/bin/python connect_canvas.py disconnect   # delete stored token and URL
    .venv/bin/python connect_canvas.py setup-info   # print setup commands; change nothing

Run it yourself in a terminal. The token is typed at a hidden prompt, checked against
Canvas's /api/v1/users/self, and saved only in the macOS Keychain or Windows Credential
Manager. It is never printed, logged, put in a file, or accepted as an argument.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import config
from canvas_client import CanvasClient, CanvasError

TOKEN_HELP = ("Create a token in Canvas: Account > Settings > Approved Integrations > "
              "+ New Access Token. Follow your institution's policy on personal access tokens.")


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
    try:
        config.save_token(new_host, token)
        path = config.save_settings(config.Settings(base_url))
    except BaseException as err:
        if getattr(err, "canvas_settings_committed", False):
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
                    "credential rollback also failed. Run `%s` to inspect the connection; do not "
                    "paste the token into chat." % config.connect_command("status")) from None
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


def advanced_config(python: str, server: str) -> str:
    """A complete TOML fragment with the top-level setting before any table."""
    return "\n".join([
        'approvals_reviewer = "user"',
        "",
        "[mcp_servers.canvas]",
        "command = %s" % _toml_string(python),
        "args = [%s, \"--writes\", \"confirm\", \"--enable-rubrics\"]"
        % _toml_string(server),
        "tool_timeout_sec = 600",
        'default_tools_approval_mode = "writes"',
    ])


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
    print("Optional writes + rubric tools (advanced; merge carefully, do not append blindly).")
    print("The first line below must stay before every [table] in config.toml:")
    print(advanced_config(python, server) + "\n")
    print("Optional skill source to copy to your Codex skills folder:")
    print("  %s" % skill)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Connect canvas-mcp to your Canvas account.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "status", "reconnect", "disconnect", "setup-info"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        if args.command == "connect":
            return cmd_connect(args)
        if args.command == "reconnect":
            return cmd_connect(args, replace=True)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "setup-info":
            return cmd_setup_info(args)
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
