import json
import types

import pytest

import config


@pytest.mark.parametrize("raw,expected", [
    ("school.instructure.com", "https://school.instructure.com"),
    ("https://school.instructure.com", "https://school.instructure.com"),
    ("https://School.Instructure.com/", "https://school.instructure.com"),
    ("  canvas.example.edu  ", "https://canvas.example.edu"),
])
def test_accepts_plain_https_hosts(raw, expected):
    assert config.normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "http://school.instructure.com", "ftp://school.instructure.com",
    "https://user:pw@school.instructure.com", "https://school.instructure.com@evil.com",
    "https://school.instructure.com:8443", "https://school.instructure.com/api/v1",
    "https://school.instructure.com/?x=1", "https://school.instructure.com/#frag",
    "https://10.0.0.5", "https://[::1]", "https://localhost", "https://exa mple.com",
    "https://school.instructure.com\\evil.com", "https://schöol.edu", "https://-bad-.edu",
    "javascript:alert(1)",
])
def test_refuses_unsafe_urls(raw):
    with pytest.raises(config.ConfigError):
        config.normalize_base_url(raw)


def test_settings_file_never_holds_the_token(fake_keyring):
    config.save_settings(config.Settings("https://canvas.example.edu"))
    config.save_token("canvas.example.edu", "secret-token-abc")
    text = config.config_path().read_text()
    assert "secret-token-abc" not in text
    assert json.loads(text) == {"base_url": "https://canvas.example.edu"}
    assert config.read_token("canvas.example.edu") == "secret-token-abc"


def test_settings_save_does_not_follow_a_predictable_temp_symlink(fake_keyring):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    victim = path.parent / "victim.txt"
    victim.write_text("do not change", encoding="utf-8")
    path.with_suffix(".tmp").symlink_to(victim)

    config.save_settings(config.Settings("https://canvas.example.edu"))

    assert victim.read_text(encoding="utf-8") == "do not change"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "base_url": "https://canvas.example.edu"}


def test_settings_interrupt_removes_its_unique_temp_file(fake_keyring, monkeypatch):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(config.os, "fdopen",
                        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        config.save_settings(config.Settings("https://canvas.example.edu"))

    assert list(path.parent.glob("config.json.*.tmp")) == []


def test_refuses_an_unapproved_keyring_backend(monkeypatch):
    class PlaintextKeyring:                 # e.g. keyrings.alt.file.PlaintextKeyring
        pass
    fake = types.SimpleNamespace(get_keyring=lambda: PlaintextKeyring())
    monkeypatch.setattr(config, "_keyring", lambda: fake)
    with pytest.raises(config.ConfigError, match="refusing keyring backend"):
        config.save_token("canvas.example.edu", "x")


def test_missing_token_message_names_the_fix_without_a_secret(fake_keyring):
    with pytest.raises(config.ConfigError, match="connect_canvas.py connect"):
        config.read_token("canvas.example.edu")


def test_real_backends_are_the_os_stores():
    assert config.ALLOWED_KEYRING_BACKENDS == {
        "keyring.backends.macOS.Keyring", "keyring.backends.Windows.WinVaultKeyring"}
