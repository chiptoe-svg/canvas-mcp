"""uninstall.py: what it removes, in what order, and what it refuses to touch."""

import subprocess
from pathlib import Path

import pytest

import config
import uninstall

HOST = "canvas.example.edu"


def make_project(root: Path, git=False) -> Path:
    project = root / "canvas-mcp"
    project.mkdir()
    for name in uninstall.PROJECT_MARKERS:
        (project / name).write_text("# stub\n")
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = x\n")
    if git:
        run = lambda *a: subprocess.run(["git", "-C", str(project)] + list(a), check=True, capture_output=True)
        run("init", "-q")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    return project


@pytest.fixture
def env(fake_keyring, tmp_path, monkeypatch):
    """A connected fake install: token + settings, a skill copy, a Codex config, a project."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    config.save_settings(config.Settings("https://" + HOST))
    config.save_token(HOST, "tok")
    skill = tmp_path / "codex" / "skills" / "canvas-mcp"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: canvas-mcp\n---\n")
    (tmp_path / "codex" / "config.toml").write_text(
        'model = "x"\n\n[mcp_servers.canvas]\ncommand = "/p/.venv/bin/python"\n'
        'args = ["/p/canvas_mcp.py", "--writes", "confirm"]\n\n[mcp_servers.other]\ncommand = "o"\n')
    project = make_project(tmp_path)
    return {"keyring": fake_keyring, "project": project, "skill": skill, "tmp": tmp_path}


def answers(*replies):
    it = iter(replies)
    return lambda prompt: next(it)


def token_stored():
    return bool(config._keyring().get_password(config.KEYRING_SERVICE, HOST))


def test_full_uninstall_removes_everything(env, capsys):
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    out = capsys.readouterr().out
    assert rc == 0
    assert not token_stored() and not config.config_path().exists()
    assert not env["skill"].exists() and not env["project"].exists()
    assert "[mcp_servers.canvas]" in out and "[mcp_servers.other]" not in out
    assert "Approved Integrations" in out


def test_declining_removes_nothing(env):
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("n"))
    assert rc == 1
    assert token_stored() and config.config_path().exists()
    assert env["skill"].exists() and (env["project"] / ".venv").exists()


def test_wrong_folder_name_keeps_the_project_but_removes_the_venv(env):
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "yes"))
    assert rc == 0 and not token_stored()
    assert env["project"].exists() and (env["project"] / "canvas_mcp.py").exists()
    assert not (env["project"] / ".venv").exists()


def test_keep_project_never_asks_for_the_name(env):
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and env["project"].exists() and not (env["project"] / ".venv").exists()


def test_token_failure_stops_before_anything_else_is_removed(env, monkeypatch):
    def broken(host):
        raise config.ConfigError("refusing keyring backend x")
    monkeypatch.setattr(config, "delete_token", broken)
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    assert rc == 2
    assert config.config_path().exists() and env["skill"].exists() and env["project"].exists()
    assert (env["project"] / ".venv").exists()


def test_invalid_saved_url_still_names_and_removes_the_token(env):
    config.config_path().write_text('{"base_url": "http://canvas.example.edu"}')
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and not token_stored() and not config.config_path().exists()


def test_unreadable_settings_print_how_to_remove_the_token(env, capsys):
    config.config_path().write_text("{not json")
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and "security delete-generic-password -s canvas-mcp" in capsys.readouterr().out


def test_any_keyring_error_stops_before_anything_else(env, monkeypatch):
    class KeyringLocked(Exception):
        pass

    def locked(host):
        raise KeyringLocked()
    monkeypatch.setattr(config, "delete_token", locked)
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    assert rc == 2 and config.config_path().exists() and env["skill"].exists() and env["project"].exists()


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
@pytest.mark.parametrize("at", [0, 1])
def test_interrupt_at_either_prompt_removes_nothing(env, interrupt, at):
    replies = ["y", "canvas-mcp"]

    def reply(prompt, n=[0]):
        n[0] += 1
        if n[0] - 1 == at:
            raise interrupt()
        return replies[n[0] - 1]
    rc = uninstall.main([], project_dir=env["project"], input_fn=reply)
    assert rc == 1 and token_stored() and env["skill"].exists() and (env["project"] / ".venv").exists()


def test_folder_with_foreign_files_is_never_deleted(env, capsys):
    (env["project"] / "taxes-2025.pdf").write_text("x")
    (env["project"] / "photos").mkdir()
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y"))   # no name prompt
    out = capsys.readouterr().out
    assert rc == 0 and "photos, taxes-2025.pdf" in out
    assert (env["project"] / "taxes-2025.pdf").exists() and not (env["project"] / ".venv").exists()
    assert not token_stored()


def test_foreign_file_inside_release_directory_is_never_deleted(env, capsys):
    docs = env["project"] / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("shipped page")
    private = docs / "private-course-notes.txt"
    private.write_text("keep me")

    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y"))

    out = capsys.readouterr().out
    assert rc == 0 and "docs/private-course-notes.txt" in out
    assert private.read_text() == "keep me" and env["project"].exists()
    assert not (env["project"] / ".venv").exists() and not token_stored()


def test_marker_files_are_deleted_last(env, monkeypatch):
    order = []
    real_unlink, real_rmtree = Path.unlink, uninstall.shutil.rmtree
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (self.exists() and order.append(self.name),
                                                             real_unlink(self, *a, **k)))
    monkeypatch.setattr(uninstall.shutil, "rmtree", lambda p, *a, **k: (order.append(Path(p).name), real_rmtree(p, *a, **k)))
    (env["project"] / "README.md").write_text("x")
    uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    tail = order[-len(uninstall.PROJECT_MARKERS):]
    assert sorted(tail) == sorted(uninstall.PROJECT_MARKERS) and ".venv" in order and not env["project"].exists()


def test_release_entry_list_covers_the_repository():
    root = Path(__file__).resolve().parent.parent
    names = [p.name for p in root.iterdir()]
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore"] + names, capture_output=True, text=True).stdout.split()
    assert set(names) - set(ignored) - {".git"} <= uninstall.RELEASE_ENTRIES


@pytest.mark.parametrize("toml,expected", [
    ('[mcp_servers.canvas]\ncommand = "p"\nargs = ["/p/canvas_mcp.py"]\n', ["the [mcp_servers.canvas] server"]),
    ('mcp_servers.cv.args = ["/p/canvas_mcp.py"]\nmcp_servers.cv.command = "p"\n', ["the [mcp_servers.cv] server"]),
    ('[mcp_servers]\ncanvas = { command = "p", args = ["/p/canvas_mcp.py"] }\n', ["the [mcp_servers.canvas] server"]),
    ('[mcp_servers.other]\ncommand = "o"  # was /p/canvas_mcp.py\n', []),
    ('[mcp_servers.other]\ncommand = "o"\n[[profiles.x]]\nnote = "canvas_mcp.py"\n', []),
])
def test_codex_config_mentions(env, toml, expected):
    (env["tmp"] / "codex" / "config.toml").write_text(toml)
    assert uninstall.codex_config_mentions(env["project"])[1] == expected


def test_codex_config_unparseable_falls_back_to_line_numbers(env):
    (env["tmp"] / "codex" / "config.toml").write_text('[broken\nargs = ["/p/canvas_mcp.py"]\n')
    assert uninstall.codex_config_mentions(env["project"])[1] == ["line 2"]


def test_not_connected_still_uninstalls(env):
    config.delete_settings()
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and not env["skill"].exists()


@pytest.mark.parametrize("target", ["home", "parent_of_home", "root"])
def test_refuses_home_and_its_ancestors(env, monkeypatch, target):
    home = env["tmp"] / "home"
    home.mkdir()
    for name in uninstall.PROJECT_MARKERS:          # even if it looks like a project
        (home / name).write_text("x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    path = {"home": home, "parent_of_home": env["tmp"], "root": Path("/")}[target]
    rc = uninstall.main([], project_dir=path, input_fn=answers("y", path.name))
    assert rc == 2 and home.exists() and token_stored()


def test_refuses_a_folder_that_is_not_canvas_mcp(env):
    other = env["tmp"] / "notes"
    other.mkdir()
    (other / "config.py").write_text("x")
    rc = uninstall.main([], project_dir=other, input_fn=answers("y", "notes"))
    assert rc == 2 and other.exists() and token_stored()


def test_skill_symlink_is_unlinked_not_followed(env):
    target = env["tmp"] / "my-skills-source"
    target.mkdir()
    (target / "SKILL.md").write_text("keep me")
    import shutil
    shutil.rmtree(env["skill"])
    env["skill"].symlink_to(target)
    uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert not env["skill"].is_symlink() and (target / "SKILL.md").read_text() == "keep me"


def test_git_warnings_name_what_would_be_lost(tmp_path):
    project = make_project(tmp_path, git=True)
    assert any("uncommitted" in w for w in uninstall.git_warnings(project))   # stub files untracked
    assert any("not pushed" in w for w in uninstall.git_warnings(project))    # no remote at all


def test_git_warns_about_ignored_files_and_detached_commits(tmp_path):
    project = make_project(tmp_path, git=True)
    run = lambda *a: subprocess.run(["git", "-C", str(project)] + list(a), check=True, capture_output=True)
    (project / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "all")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "x.pyc").write_text("x")
    assert uninstall.git_warnings(project) == []                         # build output is not a loss
    (project / ".env").write_text("SECRET=1")
    assert any("git-ignored" in w for w in uninstall.git_warnings(project))
    (project / ".env").unlink()
    run("checkout", "-q", "--detach")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "detached")
    assert any("not pushed" in w for w in uninstall.git_warnings(project))


def test_non_git_folder_is_warned(tmp_path):
    assert "not a Git checkout" in uninstall.git_warnings(make_project(tmp_path))[0]


def test_windows_prints_commands_instead_of_deleting(env, monkeypatch, capsys):
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    out = capsys.readouterr().out
    assert rc == 0 and not token_stored() and env["project"].exists()
    assert "Remove-Item -LiteralPath '%s' -Recurse -Force" % env["project"].resolve() in out


def test_windows_removes_links_inside_before_printing_the_command(env, monkeypatch):
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (env["project"] / "tests").mkdir()
    (env["project"] / "tests" / "link").symlink_to(outside)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    assert not (env["project"] / "tests" / "link").exists() and (outside / "keep.txt").exists()


@pytest.mark.parametrize("name,quoted", [("O'Brien", "O''Brien"), ("Bob\u2019s", "Bob\u2019\u2019s"),
                                         ("old [1]", "old [1]")])
def test_windows_command_quotes_every_powershell_quote(name, quoted):
    assert uninstall.windows_delete_command(Path("/a") / name) == "Remove-Item -LiteralPath '/a/%s' -Recurse -Force" % quoted


# ------------------------------------------------------------------ review round 2
def test_windows_symlinked_venv_only_unlinks_the_link(env, monkeypatch, capsys):
    shared = env["tmp"] / "shared-venv"
    (shared / "bin").mkdir(parents=True)
    (shared / "pyvenv.cfg").write_text("home = x\n")
    (shared / "bin" / "python3.12").write_text("x")
    (shared / "bin" / "python").symlink_to("python3.12")
    import shutil
    shutil.rmtree(env["project"] / ".venv")
    (env["project"] / ".venv").symlink_to(shared)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and not (env["project"] / ".venv").is_symlink()
    assert (shared / "bin" / "python").is_symlink() and "Remove-Item" not in capsys.readouterr().out


def test_host_without_a_token_prints_how_to_find_it(env, capsys):
    config.save_settings(config.Settings("https://canvas.exmaple.edu"))       # typo'd host
    uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert "security delete-generic-password -s canvas-mcp" in capsys.readouterr().out


def test_finder_metadata_does_not_block_folder_deletion(env):
    (env["project"] / ".DS_Store").write_text("x")
    rc = uninstall.main([], project_dir=env["project"], input_fn=answers("y", "canvas-mcp"))
    assert rc == 0 and not env["project"].exists()


def test_a_build_folder_is_treated_as_foreign(env):
    (env["project"] / "build").mkdir()
    (env["project"] / "build" / "NOTES.md").write_text("mine")
    uninstall.main([], project_dir=env["project"], input_fn=answers("y"))
    assert (env["project"] / "build" / "NOTES.md").exists()


@pytest.mark.parametrize("toml,expected", [
    ('[profiles.work.mcp_servers.canvas]\ncommand = "p"\nargs = ["/p/canvas_mcp.py"]\n',
     ["the [profiles.work.mcp_servers.canvas] server"]),
    ('[mcp_servers.cm]\ncommand = "python"\nargs = ["-m", "canvas_mcp"]\n', ["the [mcp_servers.cm] server"]),
])
def test_codex_config_mentions_other_layouts(env, toml, expected):
    (env["tmp"] / "codex" / "config.toml").write_text(toml)
    assert uninstall.codex_config_mentions(env["project"])[1] == expected


# ------------------------------------------------------------------ review round 3
@pytest.mark.parametrize("toml,expected", [
    ('[mcp_servers.git]\ncommand = "uvx"\nargs = ["mcp-server-git", "--repository", "/u/projects/canvas_mcp"]\n', []),
    ('[projects."/u/projects/canvas_mcp"]\ntrust_level = "trusted"\n', []),
    ('command = "/p/canvas_mcp.py"\n', []),
    ('[mcp_servers.w]\ncommand = \'C:\\u\\canvas-mcp\\.venv\\Scripts\\python.exe\'\n'
     'args = [\'C:\\u\\canvas-mcp\\canvas_mcp.py\']\n', ["the [mcp_servers.w] server"]),
])
def test_codex_config_names_only_servers_that_run_this_project(env, toml, expected):
    (env["tmp"] / "codex" / "config.toml").write_text(toml)
    assert uninstall.codex_config_mentions(env["project"])[1] == expected


@pytest.mark.parametrize("line,hit", [
    ('args = ["/p/canvas_mcp.py"]', True), ('args = ["-m", "canvas_mcp"]', True),
    ('[projects."/u/projects/canvas_mcp"]', False), ('args = ["--repository", "/u/canvas_mcp"]', False),
])
def test_codex_config_fallback_line_match(env, line, hit):
    (env["tmp"] / "codex" / "config.toml").write_text("[broken\n" + line + "\n")
    assert (uninstall.codex_config_mentions(env["project"])[1] == ["line 2"]) is hit


def test_windows_link_removal_failure_is_reported_not_raised(env, monkeypatch, capsys):
    import shutil
    shared = env["tmp"] / "shared"
    shared.mkdir()
    (shared / "pyvenv.cfg").write_text("home = x\n")
    shutil.rmtree(env["project"] / ".venv")
    (env["project"] / ".venv").symlink_to(shared)
    monkeypatch.setattr(uninstall.sys, "platform", "win32")
    monkeypatch.setattr(uninstall, "_remove_link", lambda p: (_ for _ in ()).throw(PermissionError("busy")))
    rc = uninstall.main(["--keep-project"], project_dir=env["project"], input_fn=answers("y"))
    assert rc == 0 and "delete it by hand" in capsys.readouterr().out
