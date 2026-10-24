"""Persistent task-directory selection through init, CLI and systemd."""

from __future__ import annotations

import os
import site
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from acp.cli import _handle_serve, build_parser
from acp.core.paths import resolve_run_root
from acp.init_wizard.flows import _params_secrets_step, run_init
from acp.init_wizard.persist import InitAbort
from acp.init_wizard.plan import build_plan

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACP_RUN_ROOT", raising=False)


def _write_config(path: Path, root: object) -> None:
    path.write_text(yaml.safe_dump({"deployment": {"params": {"run_root": root}}}))


def test_init_saved_directory_is_used_by_fresh_process_and_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "usage": "local",
                "tier": "standalone",
                "params": {"run_root": "~/ACP_runs"},
            }
        )
    )
    target = tmp_path / ".cccp.yaml"
    assert run_init(target, answers_path=answers) == 0
    expected = tmp_path / "ACP_runs"
    assert resolve_run_root() == expected
    assert not expected.exists(), "init must not create or migrate the task tree"
    assert "不会自动迁移" in capsys.readouterr().out

    child_env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    if site.USER_BASE:
        child_env["PYTHONUSERBASE"] = site.USER_BASE
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from acp.core.paths import resolve_run_root; print(resolve_run_root())",
        ],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == str(expected)

    import uvicorn

    launches: list[str] = []
    monkeypatch.setattr(
        uvicorn, "run", lambda *args, **kwargs: launches.append(os.environ["ACP_RUN_ROOT"])
    )
    args = build_parser().parse_args(["run", "serve", "--no-browser"])
    assert _handle_serve(args) == 0
    assert launches == [str(expected)]
    assert expected.is_dir()


def test_user_project_env_and_cli_directory_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path / ".cccp.yaml", str(tmp_path / "home-data"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    assert resolve_run_root() == tmp_path / "home-data"
    _write_config(project / "cccp.yaml", str(tmp_path / "project-data"))
    assert resolve_run_root() == tmp_path / "project-data"
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "env-data"))
    assert resolve_run_root() == tmp_path / "env-data"
    assert resolve_run_root(tmp_path / "cli-data") == tmp_path / "cli-data"
    monkeypatch.setenv("ACP_RUN_ROOT", "")
    assert resolve_run_root() == tmp_path / "project-data"


def test_interactive_init_reprompts_for_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["1", "1", "q", "", "relative/data", "~/ACP_runs", "", "", "1", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run_init(tmp_path / ".cccp.yaml") == 0
    assert resolve_run_root() == tmp_path / "ACP_runs"
    assert "必须使用绝对路径" in capsys.readouterr().out


@pytest.mark.parametrize("bad_root", [42, False, [], {}, "relative/data", " ", "a\0b"])
def test_invalid_persistent_root_is_rejected_before_writing(
    tmp_path: Path, bad_root: object
) -> None:
    target = tmp_path / ".cccp.yaml"
    with pytest.raises(InitAbort, match="run_root"):
        build_plan(target, {}, usage="local", tier="standalone", params={"run_root": bad_root})
    assert not target.exists()
    _write_config(target, bad_root)
    with pytest.raises(ValueError, match="run_root"):
        resolve_run_root()


def test_run_root_cannot_be_an_existing_file(tmp_path: Path) -> None:
    file = tmp_path / "ordinary-file"
    file.write_text("preserve me")
    _write_config(tmp_path / ".cccp.yaml", str(file))
    with pytest.raises(ValueError, match="不是目录"):
        resolve_run_root()
    assert file.read_text() == "preserve me"


def test_existing_target_root_is_default_even_when_env_overrides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "environment"))
    saved = str(tmp_path / "configured")

    class DefaultPrompts:
        def ask(self, prompt: str, default: str = "", **kwargs: object) -> str:
            return default

    params, _ = _params_secrets_step(
        DefaultPrompts(), {"deployment": {"params": {"run_root": saved}}}
    )
    assert params["run_root"] == saved


def test_answer_file_warns_about_override_without_moving_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / "old-data"
    old.mkdir()
    (old / "history.txt").write_text("old results")
    monkeypatch.setenv("ACP_RUN_ROOT", str(old))
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "usage": "local",
                "tier": "standalone",
                "params": {"run_root": str(tmp_path / "new-data")},
            }
        )
    )
    assert run_init(tmp_path / ".cccp.yaml", answers_path=answers) == 0
    assert "ACP_RUN_ROOT 环境变量当前覆盖" in capsys.readouterr().out
    assert (old / "history.txt").read_text() == "old results"
    assert not (tmp_path / "new-data").exists()


@pytest.mark.parametrize("override", ["", "/data/explicit-acp"])
def test_systemd_can_select_config_or_explicit_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'tee':\n"
        "    target = Path(os.environ['ACP_TEST_UNIT_DIR']) / Path(sys.argv[2]).name\n"
        "    target.write_bytes(sys.stdin.buffer.read())\n"
        "elif sys.argv[1] not in ('mkdir', 'systemctl'):\n"
        "    raise SystemExit(1)\n"
    )
    sudo.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\necho active\n")
    systemctl.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ACP_TEST_UNIT_DIR", str(tmp_path))
    subprocess.run(
        [
            "bash",
            str(REPO / "scripts/install_systemd.sh"),
            "--run-root",
            override,
            "--run-root-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        (tmp_path / "run-root.conf")
        .read_text()
        .endswith(f'Environment="ACP_RUN_ROOT={override}"\n')
    )
    assert not (tmp_path / "acp.service").exists(), (
        "config-only change must preserve the installed unit"
    )
