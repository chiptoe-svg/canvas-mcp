import subprocess
from pathlib import Path

import pytest

import update


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_remote(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    install = tmp_path / "install"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.invalid")
    (work / "CHANGELOG.md").write_text("# Changes\n\n## [0.1.0]\nold\n")
    (work / "SKILL.md").write_text("old skill\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "old")
    git(work, "tag", "-a", "v0.1.0", "-m", "old")
    (work / "CHANGELOG.md").write_text("# Changes\n\n## [0.1.1]\nnew\n")
    (work / "SKILL.md").write_text("new skill\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "new")
    git(work, "tag", "-a", "v0.1.1", "-m", "new")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main", "--tags")
    subprocess.run(["git", "clone", "-q", "--branch", "v0.1.0", str(origin), str(install)], check=True)
    return install


def test_check_reports_exact_current_and_latest_remote_tags(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")

    assert update.main(["check"], project_dir=project, expected_origin=origin) == 0

    output = capsys.readouterr().out
    assert "v0.1.0" in output and "v0.1.1" in output
    assert "No files were changed" in output


def test_verify_accepts_only_the_exact_annotated_remote_tag(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 0

    output = capsys.readouterr().out
    assert "Verified exact release: v0.1.0" in output
    assert git(project, "rev-parse", "HEAD") in output


def test_verify_refuses_untagged_head_even_when_main_contains_it(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    git(project, "config", "user.name", "Test")
    git(project, "config", "user.email", "test@example.invalid")
    (project / "local.txt").write_text("untagged\n")
    git(project, "add", "local.txt")
    git(project, "commit", "-q", "-m", "untagged")

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 2

    assert "does not exactly match" in capsys.readouterr().err


def test_verify_refuses_dirty_exact_tag(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    (project / "SKILL.md").write_text("locally changed\n")

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 2

    assert "local changes" in capsys.readouterr().err


def test_verify_refuses_untracked_file(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    (project / "private.txt").write_text("untracked\n")

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 2
    assert "local changes" in capsys.readouterr().err


def test_verify_refuses_lightweight_remote_tag_even_if_local_tag_is_annotated(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    head = git(project, "rev-parse", "HEAD")
    git(project, "tag", "v0.1.2", head)
    git(project, "push", "-q", "origin", "v0.1.2")
    git(project, "tag", "-d", "v0.1.2")
    git(project, "config", "user.name", "Test")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "tag", "-a", "v0.1.2", "-m", "fabricated", head)

    assert update.main(["verify", "v0.1.2"], project_dir=project, expected_origin=origin) == 2
    assert "not an annotated" in capsys.readouterr().err


def test_verify_refuses_locally_recreated_tag_object(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    head = git(project, "rev-parse", "HEAD")
    git(project, "config", "user.name", "Test")
    git(project, "config", "user.email", "test@example.invalid")
    git(project, "tag", "-d", "v0.1.0")
    git(project, "tag", "-a", "v0.1.0", "-m", "different annotation", head)

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 2
    assert "tag object does not match" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_verify_refuses_hidden_index_flags(tmp_path, capsys, flag):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    git(project, "update-index", flag, "SKILL.md")
    (project / "SKILL.md").write_text("hidden change\n")
    assert git(project, "status", "--porcelain") == ""

    assert update.main(["verify", "v0.1.0"], project_dir=project, expected_origin=origin) == 2
    assert "hidden index flags" in capsys.readouterr().err


def test_verify_refuses_wrong_origin(tmp_path, capsys):
    project = make_remote(tmp_path)

    assert update.main(["verify", "v0.1.0"], project_dir=project,
                       expected_origin="https://example.invalid/wrong.git") == 2
    assert "unexpected origin" in capsys.readouterr().err


def test_check_refuses_dirty_checkout_before_fetch(tmp_path, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    (project / "notes.txt").write_text("private\n")

    assert update.main(["check"], project_dir=project, expected_origin=origin) == 2

    assert "local changes" in capsys.readouterr().err
    assert (project / "notes.txt").read_text() == "private\n"


def test_apply_requires_exact_remote_release_and_rechecks_commit(tmp_path, monkeypatch, capsys):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    calls = []
    monkeypatch.setattr(update, "install_dependencies_and_test",
                        lambda project_dir: calls.append(("test", git(project_dir, "rev-parse", "HEAD"))))
    monkeypatch.setattr(update, "install_skill",
                        lambda project_dir: calls.append(("skill", (project_dir / "SKILL.md").read_text())))

    assert update.main(["apply", "v0.1.1"], project_dir=project, expected_origin=origin) == 0

    assert git(project, "describe", "--tags", "--exact-match") == "v0.1.1"
    assert [name for name, value in calls] == ["test", "skill"]
    assert "Updated to v0.1.1" in capsys.readouterr().out


@pytest.mark.parametrize("tag", ["main", "v0.1", "v0.1.1-rc1", "v01.1.1", "v9.9.9"])
def test_apply_refuses_nonrelease_or_missing_tag_without_switching(tmp_path, tag):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    before = git(project, "rev-parse", "HEAD")

    assert update.main(["apply", tag], project_dir=project, expected_origin=origin) == 2

    assert git(project, "rev-parse", "HEAD") == before


def test_apply_stops_before_switch_if_remote_tag_moves(tmp_path, monkeypatch):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    before = git(project, "rev-parse", "HEAD")
    real_plan = update.build_plan
    calls = {"count": 0}

    def changed_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 2:
            return plan._replace(target_commit="0" * 40)
        return plan

    monkeypatch.setattr(update, "build_plan", changed_plan)

    assert update.main(["apply", "v0.1.1"], project_dir=project, expected_origin=origin) == 2
    assert git(project, "rev-parse", "HEAD") == before


def test_apply_refuses_skill_modified_by_test_before_install(tmp_path, monkeypatch):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    installed = []

    def mutate(project_dir):
        (project_dir / "SKILL.md").write_text("mutated by test hook\n")

    monkeypatch.setattr(update, "install_dependencies_and_test", mutate)
    monkeypatch.setattr(update, "install_skill", lambda project_dir: installed.append(True))

    assert update.main(["apply", "v0.1.1"], project_dir=project,
                       expected_origin=origin) == 2
    assert installed == []
    assert (project / "SKILL.md").read_text() == "mutated by test hook\n"


def test_apply_current_tag_still_tests_and_repairs_skill(tmp_path, monkeypatch):
    project = make_remote(tmp_path)
    origin = git(project, "remote", "get-url", "origin")
    calls = []
    monkeypatch.setattr(update, "install_dependencies_and_test",
                        lambda project_dir: calls.append("test"))
    monkeypatch.setattr(update, "install_skill", lambda project_dir: calls.append("skill"))

    assert update.main(["apply", "v0.1.0"], project_dir=project,
                       expected_origin=origin) == 0
    assert calls == ["test", "skill"]
