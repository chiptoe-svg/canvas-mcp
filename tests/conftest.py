import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class MemoryKeyring:
    """Stands in for the OS store. Its class path is registered as an allowed backend in tests."""

    def __init__(self):
        self.items = {}


@pytest.fixture
def fake_keyring(monkeypatch, tmp_path):
    """A fake `keyring` module and a private config dir; the real Keychain is never touched."""
    import types

    import config

    store = {}

    class PasswordDeleteError(Exception):
        pass

    backend = MemoryKeyring()
    module = types.SimpleNamespace(
        get_keyring=lambda: backend,
        set_password=lambda s, u, p: store.__setitem__((s, u), p),
        get_password=lambda s, u: store.get((s, u)),
        delete_password=lambda s, u: store.pop((s, u)) if (s, u) in store else (_ for _ in ()).throw(PasswordDeleteError()),
        errors=types.SimpleNamespace(PasswordDeleteError=PasswordDeleteError),
    )
    monkeypatch.setattr(config, "_keyring", lambda: module)
    monkeypatch.setattr(config, "ALLOWED_KEYRING_BACKENDS",
                        config.ALLOWED_KEYRING_BACKENDS | {"conftest.MemoryKeyring"})
    monkeypatch.setenv("CANVAS_MCP_CONFIG_DIR", str(tmp_path / "cfg"))
    module.store = store
    return module
