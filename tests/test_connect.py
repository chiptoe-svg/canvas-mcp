import sys
import hashlib
import types
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


def test_interrupt_after_save_settings_returns_keeps_exact_committed_connection(
        fake_keyring, monkeypatch, capsys):
    config.save_settings(config.Settings("https://old.example.edu"))
    config.save_token("old.example.edu", "old-secret")
    _prompts(monkeypatch, "https://new.example.edu", "new-secret")
    real_save = config.save_settings

    def save_then_interrupt(settings):
        real_save(settings)
        raise KeyboardInterrupt

    monkeypatch.setattr(config, "save_settings", save_then_interrupt)

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


def test_disconnect_keeps_settings_when_stored_token_cannot_be_deleted(
        fake_keyring, monkeypatch, capsys):
    config.save_settings(config.Settings("https://canvas.example.edu"))
    config.save_token("canvas.example.edu", "old-secret")
    monkeypatch.setattr(config, "delete_token", lambda host: False)

    assert connect_canvas.cmd_disconnect(Namespace()) == 2
    assert config.load_settings().base_url == "https://canvas.example.edu"
    assert config.read_token("canvas.example.edu") == "old-secret"
    assert "kept the saved URL" in capsys.readouterr().err


def test_failed_rollback_names_possible_orphan_credential(fake_keyring, monkeypatch):
    _prompts(monkeypatch, "https://new.example.edu", "candidate-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(config, "delete_token", lambda host: False)

    with pytest.raises(config.ConfigError, match="new.example.edu.*may remain"):
        connect_canvas.cmd_connect(Namespace())


def test_commit_probe_keyring_error_still_rolls_back_candidate(fake_keyring, monkeypatch):
    config.save_settings(config.Settings("https://canvas.example.edu"))
    config.save_token("canvas.example.edu", "old-secret")
    _prompts(monkeypatch, "https://canvas.example.edu", "candidate-secret")
    monkeypatch.setattr(config, "save_settings",
                        lambda settings: (_ for _ in ()).throw(OSError("disk full")))
    real_read = config.read_token
    calls = {"count": 0}

    def fail_only_during_commit_probe(host):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("credential store temporarily unavailable")
        return real_read(host)

    monkeypatch.setattr(config, "read_token", fail_only_during_commit_probe)

    with pytest.raises(config.ConfigError, match="previous connection was restored"):
        connect_canvas.cmd_connect(Namespace(), replace=True)
    assert real_read("canvas.example.edu") == "old-secret"


def test_connection_errors_name_the_active_python_and_absolute_script():
    message = str(config.ConfigError(config.connect_command("connect")))

    assert str(Path(sys.executable).absolute()) in message
    assert str(Path(config.__file__).resolve().with_name("connect_canvas.py")) in message
    assert message.endswith(" connect")


def test_setup_info_prints_exact_guarded_write_command(capsys):
    assert connect_canvas.cmd_setup_info(Namespace()) == 0

    output = capsys.readouterr().out
    python = str(Path(sys.executable).absolute())
    server = str(Path(connect_canvas.__file__).resolve().with_name("canvas_mcp.py"))
    assert "Guarded-write Codex setup (recommended):" in output
    assert python in output
    assert server in output
    assert "codex mcp add canvas --" in output
    assert "--writes confirm" in output
    assert "does not edit" in output


def test_advanced_config_places_approval_reviewer_at_top_level():
    data = tomllib.loads(connect_canvas.advanced_config("/venv/python", "/repo/canvas_mcp.py",
                                                        enable_rubric_grading=True))

    assert data["approvals_reviewer"] == "user"
    assert "approvals_reviewer" not in data["mcp_servers"]["canvas"]
    assert data["mcp_servers"]["canvas"]["tool_timeout_sec"] == 600


def test_setup_info_read_only_and_rubric_grading_are_separate(capsys):
    assert connect_canvas.main(["setup-info", "--read-only"]) == 0
    read_only = capsys.readouterr().out
    assert "--writes confirm" not in read_only
    assert "--enable-rubric-grading" not in read_only

    assert connect_canvas.main(["setup-info", "--enable-rubric-grading"]) == 0
    grading = capsys.readouterr().out
    assert "--writes confirm --enable-rubric-grading" in grading
    assert "config.toml" not in grading

    assert connect_canvas.main(["setup-info", "--writes", "--enable-rubrics"]) == 0
    legacy = capsys.readouterr().out
    assert "--writes confirm --enable-rubric-grading" in legacy


def test_install_skill_preview_changes_nothing(tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    installed = codex_home / "skills" / "canvas-mcp" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("old skill\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert connect_canvas.main(["install-skill"]) == 0

    assert installed.read_text() == "old skill\n"
    output = capsys.readouterr().out
    assert "Preview only" in output
    assert "--apply" in output


def test_install_skill_apply_backs_up_and_atomically_replaces(tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    installed = codex_home / "skills" / "canvas-mcp" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("old skill\n")
    source = tmp_path / "source-SKILL.md"
    source.write_text("new skill\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(connect_canvas, "SKILL_SOURCE", source)

    assert connect_canvas.main(["install-skill", "--apply"]) == 0

    assert installed.read_text() == "new skill\n"
    backups = list((codex_home / "backups" / "canvas-mcp").glob("*/SKILL.md"))
    assert len(backups) == 1 and backups[0].read_text() == "old skill\n"
    assert hashlib.sha256(installed.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
    assert "Restart Codex" in capsys.readouterr().out


def test_install_skill_refuses_symlink_destination(tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    outside = tmp_path / "outside"
    outside.mkdir()
    skill_dir = codex_home / "skills" / "canvas-mcp"
    skill_dir.parent.mkdir(parents=True)
    skill_dir.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert connect_canvas.main(["install-skill", "--apply"]) == 2
    assert not (outside / "SKILL.md").exists()
    assert "symbolic link" in capsys.readouterr().err


def test_link_check_rejects_windows_reparse_points_without_is_junction(tmp_path, monkeypatch):
    candidate = tmp_path / "junction"
    candidate.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)
    monkeypatch.setattr(Path, "lstat",
                        lambda self: types.SimpleNamespace(st_file_attributes=0x400))

    assert connect_canvas._is_linklike(candidate) is True


def test_install_skill_refuses_concurrent_destination_edit_and_preserves_it(
        tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    installed = codex_home / "skills" / "canvas-mcp" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("old skill\n")
    source = tmp_path / "source-SKILL.md"
    source.write_text("new skill\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(connect_canvas, "SKILL_SOURCE", source)
    real_fingerprint = connect_canvas._regular_fingerprint
    checks = {"destination": 0}

    def edit_before_final_check(path, *, allow_missing):
        if path == installed:
            checks["destination"] += 1
            if checks["destination"] == 3:
                installed.write_text("concurrent important edit\n")
        return real_fingerprint(path, allow_missing=allow_missing)

    monkeypatch.setattr(connect_canvas, "_regular_fingerprint", edit_before_final_check)

    assert connect_canvas.main(["install-skill", "--apply"]) == 2
    assert installed.read_text() == "concurrent important edit\n"
    backups = list((codex_home / "backups" / "canvas-mcp").glob("*/SKILL.md"))
    assert len(backups) == 1 and backups[0].read_text() == "old skill\n"
    assert "changed after preview" in capsys.readouterr().err


def test_install_skill_failure_after_backup_reports_backup(tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    installed = codex_home / "skills" / "canvas-mcp" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("old skill\n")
    source = tmp_path / "source-SKILL.md"
    source.write_text("new skill\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(connect_canvas, "SKILL_SOURCE", source)
    monkeypatch.setattr(connect_canvas.tempfile, "mkstemp",
                        lambda **kwargs: (_ for _ in ()).throw(PermissionError()))

    assert connect_canvas.main(["install-skill", "--apply"]) == 2
    assert installed.read_text() == "old skill\n"
    error = capsys.readouterr().err
    assert "destination was not replaced" in error and "verified backup:" in error


def test_install_skill_post_replace_failure_reports_changed_state_and_backup(
        tmp_path, monkeypatch, capsys):
    codex_home = tmp_path / "codex"
    installed = codex_home / "skills" / "canvas-mcp" / "SKILL.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("old skill\n")
    source = tmp_path / "source-SKILL.md"
    source.write_text("new skill\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(connect_canvas, "SKILL_SOURCE", source)
    real_replace = connect_canvas.os.replace

    def replace_then_corrupt(source_path, destination_path):
        real_replace(source_path, destination_path)
        Path(destination_path).write_text("corrupt after replacement\n")

    monkeypatch.setattr(connect_canvas.os, "replace", replace_then_corrupt)

    assert connect_canvas.main(["install-skill", "--apply"]) == 2
    assert installed.read_text() == "corrupt after replacement\n"
    backups = list((codex_home / "backups" / "canvas-mcp").glob("*/SKILL.md"))
    assert len(backups) == 1 and backups[0].read_text() == "old skill\n"
    error = capsys.readouterr().err
    assert "was replaced but verification failed" in error
    assert str(backups[0]) in error
