import sys
from argparse import Namespace
from pathlib import Path

import pytest
import tomllib

import config
import connect_canvas


def _prompts(monkeypatch, url, token):
    monkeypatch.setattr(connect_canvas, "prompt_url", lambda default=None: url)
    monkeypatch.setattr(connect_canvas, "prompt_token", lambda: token)
    monkeypatch.setattr(connect_canvas, "verify", lambda base_url, value: {"id": 7, "name": "Teacher"})


def test_prompt_url_explains_bad_value_and_reprompts(monkeypatch, capsys):
    answers = iter(["http://canvas.example.edu", "Canvas.Example.edu/"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert connect_canvas.prompt_url() == "https://canvas.example.edu"

    captured = capsys.readouterr()
    assert "must use https://" in captured.err
    assert "Token destination: https://canvas.example.edu" in captured.out


def test_first_connect_removes_token_if_settings_cannot_be_saved(fake_keyring, monkeypatch):
    _prompts(monkeypatch, "https://canvas.example.edu", "new-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(config.ConfigError, match="nothing was changed"):
        connect_canvas.cmd_connect(Namespace())

    assert fake_keyring.store == {}


def test_same_host_reconnect_restores_old_token_if_settings_fail(fake_keyring, monkeypatch):
    config.save_settings(config.Settings("https://canvas.example.edu"))
    config.save_token("canvas.example.edu", "old-secret")
    _prompts(monkeypatch, "https://canvas.example.edu", "new-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(config.ConfigError, match="previous connection was restored"):
        connect_canvas.cmd_connect(Namespace(), replace=True)

    assert config.read_token("canvas.example.edu") == "old-secret"


def test_new_host_reconnect_keeps_old_connection_if_settings_fail(fake_keyring, monkeypatch):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(config.ConfigError, match="previous connection was restored"):
        connect_canvas.cmd_connect(Namespace(), replace=True)

    assert config.load_settings().base_url == "https://old.example.edu"
    assert config.read_token("old.example.edu") == "old-secret"
    assert (config.KEYRING_SERVICE, "new.example.edu") not in fake_keyring.store


def test_settings_failure_restores_preexisting_destination_token(fake_keyring, monkeypatch):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    config.save_token("new.example.edu", "preexisting-secret")
    _prompts(monkeypatch, "https://new.example.edu", "candidate-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(config.ConfigError, match="previous connection was restored"):
        connect_canvas.cmd_connect(Namespace(), replace=True)

    assert config.read_token("old.example.edu") == "old-secret"
    assert config.read_token("new.example.edu") == "preexisting-secret"


def test_failed_credential_delete_is_reported_as_failed_rollback(fake_keyring, monkeypatch):
    _prompts(monkeypatch, "https://canvas.example.edu", "candidate-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(config, "delete_token", lambda host: False)

    with pytest.raises(config.ConfigError, match="credential rollback also failed"):
        connect_canvas.cmd_connect(Namespace())


def test_failed_old_token_cleanup_warns(fake_keyring, monkeypatch, capsys):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")
    monkeypatch.setattr(config, "delete_token", lambda host: False)

    assert connect_canvas.cmd_connect(Namespace(), replace=True) == 0

    assert "old token entry" in capsys.readouterr().err


def test_keyring_exception_never_echoes_candidate_token(fake_keyring, monkeypatch, capsys):
    _prompts(monkeypatch, "https://canvas.example.edu", "candidate-secret")
    monkeypatch.setattr(config, "save_token",
                        lambda host, token: (_ for _ in ()).throw(
                            RuntimeError("credential-store failure for " + token)))

    assert connect_canvas.main(["connect"]) == 2

    output = capsys.readouterr()
    assert "candidate-secret" not in output.out
    assert "candidate-secret" not in output.err
    assert "credential store" in output.err


def test_interrupt_after_settings_replace_keeps_committed_connection(
        fake_keyring, monkeypatch, capsys):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")
    real_replace = config.os.replace

    def replace_then_interrupt(source, destination):
        real_replace(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(config.os, "replace", replace_then_interrupt)

    assert connect_canvas.cmd_connect(Namespace(), replace=True) == 0

    assert config.load_settings().base_url == "https://new.example.edu"
    assert config.read_token("new.example.edu") == "new-secret"
    assert (config.KEYRING_SERVICE, "old.example.edu") not in fake_keyring.store
    assert "saved before setup was interrupted" in capsys.readouterr().err


def test_missing_temp_without_destination_change_is_not_treated_as_commit(
        fake_keyring, monkeypatch):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")

    def remove_temp_then_interrupt(source, destination):
        source.unlink()
        raise KeyboardInterrupt

    monkeypatch.setattr(config.os, "replace", remove_temp_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        connect_canvas.cmd_connect(Namespace(), replace=True)

    assert config.load_settings().base_url == "https://old.example.edu"
    assert config.read_token("old.example.edu") == "old-secret"
    assert (config.KEYRING_SERVICE, "new.example.edu") not in fake_keyring.store


def test_interrupt_during_old_token_cleanup_reports_committed_connection(
        fake_keyring, monkeypatch, capsys):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")
    real_delete = config.delete_token

    def interrupt_old(host):
        if host == "old.example.edu":
            raise KeyboardInterrupt
        return real_delete(host)

    monkeypatch.setattr(config, "delete_token", interrupt_old)

    assert connect_canvas.main(["reconnect"]) == 0

    assert config.load_settings().base_url == "https://new.example.edu"
    assert config.read_token("new.example.edu") == "new-secret"
    output = capsys.readouterr()
    assert "old token entry" in output.err
    assert "nothing was saved" not in output.err


def test_cancel_message_tells_user_to_verify_status(fake_keyring, monkeypatch, capsys):
    monkeypatch.setattr(connect_canvas, "prompt_url",
                        lambda default=None: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert connect_canvas.main(["connect"]) == 130

    output = capsys.readouterr()
    assert "status" in output.err
    assert "nothing was saved" not in output.err


def test_connection_errors_name_the_active_python_and_absolute_script():
    message = str(config.ConfigError(config.connect_command("connect")))

    assert str(Path(sys.executable).absolute()) in message
    assert str(Path(config.__file__).resolve().with_name("connect_canvas.py")) in message
    assert message.endswith(" connect")


def test_setup_info_prints_exact_read_only_command_and_advanced_block(capsys):
    assert connect_canvas.cmd_setup_info(Namespace()) == 0

    output = capsys.readouterr().out
    python = str(Path(sys.executable).absolute())
    server = str(Path(connect_canvas.__file__).resolve().with_name("canvas_mcp.py"))
    assert "Read-only Codex setup (recommended first):" in output
    assert python in output
    assert server in output
    assert "codex mcp add canvas --" in output
    assert '"--writes", "confirm", "--enable-rubrics"' in output
    assert "tool_timeout_sec = 600" in output
    assert 'approvals_reviewer = "user"' in output
    assert "does not edit" in output


def test_advanced_config_places_approval_reviewer_at_top_level():
    data = tomllib.loads(connect_canvas.advanced_config("/venv/python", "/repo/canvas_mcp.py"))

    assert data["approvals_reviewer"] == "user"
    assert "approvals_reviewer" not in data["mcp_servers"]["canvas"]
    assert data["mcp_servers"]["canvas"]["tool_timeout_sec"] == 600
