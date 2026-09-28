"""Local, per-user settings for canvas-mcp.

Two things are stored, in two places:

* the Canvas base URL (not a secret) in a small JSON file in your user config directory;
* the personal access token (a secret) in the operating system's credential store, through
  Python ``keyring`` only: macOS Keychain or Windows Credential Manager.

Nothing here reads environment variables for the token, writes a ``.env`` file, or edits
any Codex configuration.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "canvas-mcp"
KEYRING_SERVICE = "canvas-mcp"

# The only keyring backends accepted. Anything else (a plaintext file backend, a "null"
# backend, an unknown third-party backend) is refused rather than silently used.
ALLOWED_KEYRING_BACKENDS = {
    "keyring.backends.macOS.Keyring",               # macOS Keychain
    "keyring.backends.Windows.WinVaultKeyring",     # Windows Credential Manager
}

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
                       r"(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class ConfigError(Exception):
    """A setting is missing or unsafe. The message never contains a token."""


def config_dir() -> Path:
    """Your per-user config directory. CANVAS_MCP_CONFIG_DIR overrides it (used by tests)."""
    override = os.environ.get("CANVAS_MCP_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def normalize_base_url(raw: str) -> str:
    """Return 'https://host' for a Canvas URL, or raise ConfigError.

    Accepts 'school.instructure.com', 'https://school.instructure.com' and
    'https://school.instructure.com/'. Refuses http, credentials in the URL, a port,
    a path, a query, a fragment, an IP address, and anything that is not a plain DNS name.
    """
    text = (raw or "").strip()
    if not text:
        raise ConfigError("Canvas URL is empty")
    if any(ch.isspace() or ord(ch) < 32 for ch in text) or "\\" in text:
        raise ConfigError("Canvas URL contains whitespace, control characters or a backslash")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError as err:
        raise ConfigError("Canvas URL is not valid: %s" % err) from None
    if parts.scheme.lower() != "https":
        raise ConfigError("Canvas URL must use https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ConfigError("Canvas URL must not contain a user name or password")
    if port is not None:
        raise ConfigError("Canvas URL must not include a port")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError("Canvas URL must be just the site, e.g. https://school.instructure.com")
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        raise ConfigError("Canvas URL must be a host name, not an IP address")
    except ValueError:
        pass
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise ConfigError("Canvas URL host must be plain ASCII (use its punycode form)") from None
    if not _HOSTNAME.match(host):
        raise ConfigError("Canvas URL host is not a valid DNS name: %r" % host)
    return "https://" + host


def host_of(base_url: str) -> str:
    return urllib.parse.urlsplit(base_url).hostname or ""


@dataclass(frozen=True)
class Settings:
    base_url: str

    @property
    def host(self) -> str:
        return host_of(self.base_url)


def load_settings() -> Settings:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError("Canvas is not connected yet. In a terminal run: "
                          "python connect_canvas.py connect") from None
    except (OSError, ValueError) as err:
        raise ConfigError("cannot read %s: %s" % (path, err)) from None
    if not isinstance(data, dict) or not isinstance(data.get("base_url"), str):
        raise ConfigError("%s has no base_url; run: python connect_canvas.py reconnect" % path)
    return Settings(base_url=normalize_base_url(data["base_url"]))


def save_settings(settings: Settings) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"base_url": settings.base_url}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
    return path


def delete_settings() -> bool:
    try:
        config_path().unlink()
        return True
    except FileNotFoundError:
        return False


# ------------------------------------------------------------------------------ token storage
def _keyring():
    import keyring  # imported lazily so tests can substitute a fake module
    return keyring


def check_keyring_backend() -> str:
    """Return the backend name, or raise ConfigError if it is not an allowed OS store."""
    backend = _keyring().get_keyring()
    name = "%s.%s" % (type(backend).__module__, type(backend).__name__)
    if name not in ALLOWED_KEYRING_BACKENDS:
        raise ConfigError("refusing keyring backend %s: canvas-mcp stores the token only in the "
                          "macOS Keychain or Windows Credential Manager" % name)
    return name


def save_token(host: str, token: str) -> None:
    check_keyring_backend()
    _keyring().set_password(KEYRING_SERVICE, host, token)


def read_token(host: str) -> str:
    """The one place the token is read. Raise ConfigError (without the token) if absent."""
    check_keyring_backend()
    token = _keyring().get_password(KEYRING_SERVICE, host)
    if not token:
        raise ConfigError("no Canvas token is stored for %s; in a terminal run: "
                          "python connect_canvas.py connect" % host)
    return token


def has_token(host: str) -> bool:
    try:
        return bool(read_token(host))
    except ConfigError:
        return False


def delete_token(host: str) -> bool:
    check_keyring_backend()
    kr = _keyring()
    try:
        kr.delete_password(KEYRING_SERVICE, host)
        return True
    except kr.errors.PasswordDeleteError:
        return False
