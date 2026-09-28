"""Connect this computer's canvas-mcp to your Canvas account.

    python connect_canvas.py connect      # ask for Canvas URL + token, verify, save
    python connect_canvas.py status       # show the URL and whether a token is stored/valid
    python connect_canvas.py reconnect    # replace the stored token (and optionally the URL)
    python connect_canvas.py disconnect   # delete the stored token and the saved URL

Run it yourself in a terminal. The token is typed at a hidden prompt, checked against
Canvas's /api/v1/users/self, and saved only in the macOS Keychain or Windows Credential
Manager. It is never printed, logged, put in a file, or accepted as an argument.
"""

from __future__ import annotations

import argparse
import getpass
import sys

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
    raw = input("Canvas URL (e.g. https://school.instructure.com)%s: " % suffix).strip()
    return config.normalize_base_url(raw or default or "")


def prompt_token() -> str:
    print(TOKEN_HELP)
    token = getpass.getpass("Canvas access token (input hidden): ").strip()
    if not token:
        raise config.ConfigError("no token entered; nothing was saved")
    return token


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
    if current and current.host != config.host_of(base_url):
        config.delete_token(current.host)                 # do not leave the old one behind
    config.save_token(config.host_of(base_url), token)
    path = config.save_settings(config.Settings(base_url))
    del token
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
        print("Token:        not stored - run: python connect_canvas.py connect")
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Connect canvas-mcp to your Canvas account.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "status", "reconnect", "disconnect"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        if args.command == "connect":
            return cmd_connect(args)
        if args.command == "reconnect":
            return cmd_connect(args, replace=True)
        if args.command == "status":
            return cmd_status(args)
        return cmd_disconnect(args)
    except config.ConfigError as err:
        print(str(err), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; nothing was saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
