"""TDD tests for acp.init_wizard.flows + the `acp init` CLI wiring (plan T8).

Acceptance criteria covered:
(a) parser accepts ``init --config X --log-level DEBUG`` (default ~/.cccp.yaml);
(b) dispatch invokes run_init (mocked) and maps its return code;
(c) REAL pipe smoke: ``printf 'q\\n' | acp init --config <0-byte tmpfile>``
    exits 0 with the tmpfile still 0 bytes;
(d) paramiko-absent stub: local flow still runs, remote selection prints the
    install hint and returns to the menu;
(d2) ``import acp.init_wizard`` (+ flows) succeeds in a fresh interpreter with
    paramiko stubbed out (no transitive paramiko import at module level);
(e) malformed entries (``port: "abc"`` / ``max_concurrent_jobs: "xyz"``)
    render ``name (配置无效: …)`` unselectable, a validator-bypassing direct
    selection returns to the menu with the reason and NO traceback;
plus: disabled-node refusal, broken-choice re-menu, q → 0, InitAbort → 1.
"""

from __future__ import annotations

import builtins
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import acp.init_wizard.flows as flows_module
from acp.cli import build_parser
from acp.init_wizard.flows import run_init
from acp.init_wizard.newnode import FlowResult, PromptBundle
from cccp.software import SoftwareDiscovery

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point ~ at tmp_path and chdir into it so any cccp config merge that
    leaks into a flow test stays hermetic (mirrors the T4 fixture)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def _script_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Feed a scripted answer sequence to builtins.input."""
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(it))


def _write_target(tmp_path: Path, nodes: list[dict[str, Any]]) -> Path:
    """Write a wizard target YAML carrying the given cluster.nodes entries."""
    target = tmp_path / "wizard_target.yaml"
    target.write_text(
        yaml.dump({"cluster": {"nodes": nodes}}, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def _node_entry(**overrides: Any) -> dict[str, Any]:
    """A minimally valid node entry."""
    entry: dict[str, Any] = {
        "name": "node-a",
        "host": "10.0.0.1",
        "username": "user",
        "remote_work_dir": "~/acp_jobs",
        "remote_code_dir": "~/acp_code",
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# (a) parser surface
# ---------------------------------------------------------------------------


def test_parser_accepts_init_flags(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    args = build_parser().parse_args(["init", "--config", str(target), "--log-level", "DEBUG"])
    assert args.command == "init"
    assert args.config == target
    assert args.log_level == "DEBUG"


def test_parser_init_defaults() -> None:
    args = build_parser().parse_args(["init"])
    assert args.config == Path.home() / ".cccp.yaml"
    assert args.log_level == "INFO"


def test_parser_init_help_states_write_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D18c: --config is a WRITE target, not a merge source — the help says so."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["init", "--help"])
    assert "create/update" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (b) dispatch maps run_init's return code
# ---------------------------------------------------------------------------


def test_main_dispatch_invokes_run_init_and_maps_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import acp.cli as cli_module

    seen: list[Path] = []

    def fake_run_init(config_path: Path) -> int:
        seen.append(config_path)
        return 7

    monkeypatch.setattr(flows_module, "run_init", fake_run_init)
    target = tmp_path / "w.yaml"
    assert cli_module.main(["init", "--config", str(target)]) == 7
    assert seen == [Path(target)]

    monkeypatch.setattr(flows_module, "run_init", lambda config_path: 0)
    assert cli_module.main(["init", "--config", str(target)]) == 0


# ---------------------------------------------------------------------------
# (c) REAL pipe smoke (also captured to evidence outside pytest)
# ---------------------------------------------------------------------------


def test_cli_smoke_quit_leaves_empty_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "smoke.yaml"
    target.write_bytes(b"")
    proc = subprocess.run(
        [sys.executable, "-m", "acp.cli", "init", "--config", str(target)],
        input="q\n",
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.read_bytes() == b""
    assert "本地" in proc.stdout
    assert "声明新集群" in proc.stdout
    assert "q) 退出" in proc.stdout


# ---------------------------------------------------------------------------
# (d2) import discipline: no paramiko anywhere in the package import graph
# ---------------------------------------------------------------------------


def test_package_imports_without_paramiko() -> None:
    code = (
        "import sys\n"
        "sys.modules['paramiko'] = None\n"
        "import acp.init_wizard\n"
        "import acp.init_wizard.flows\n"
        "from acp.init_wizard import run_init, FlowResult\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/tmp",
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# (d) paramiko absent: local flow runs, remote selection gets hint + menu
# ---------------------------------------------------------------------------


def test_paramiko_absent_local_flow_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "paramiko", None)
    monkeypatch.setattr(
        flows_module,
        "sniff_local",
        lambda target: {"xtb": SoftwareDiscovery(name="xtb", resolved=None, source=None)},
    )
    monkeypatch.setattr(flows_module, "render_sniff_table", lambda entries: "SNIFF-TABLE")
    monkeypatch.setattr(flows_module, "manual_spec_local", lambda missing, prompts: {})

    target = tmp_path / "cfg.yaml"
    target.write_bytes(b"")
    _script_input(monkeypatch, ["1", "q"])

    assert run_init(target) == 0
    out = capsys.readouterr().out
    assert "SNIFF-TABLE" in out
    assert out.count("选择要初始化的资源") == 2  # menu re-shown after the flow


def test_paramiko_absent_remote_entry_shows_hint_and_returns_to_menu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "paramiko", None)
    target = _write_target(tmp_path, [_node_entry()])
    _script_input(monkeypatch, ["2", "q"])

    assert run_init(target) == 0
    out = capsys.readouterr().out
    assert "pip install -e" in out  # install hint, never a traceback/exit
    assert out.count("选择要初始化的资源") == 2  # hint returned control to the menu


# ---------------------------------------------------------------------------
# (e) malformed entries + menu semantics
# ---------------------------------------------------------------------------


def test_malformed_entries_render_invalid_and_unselectable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nodes = [
        _node_entry(name="bad-port", port="abc"),
        _node_entry(name="bad-mcj", max_concurrent_jobs="xyz"),
    ]
    target = _write_target(tmp_path, nodes)
    _script_input(monkeypatch, ["2", "q"])  # pick bad-port -> reason -> menu -> q

    assert run_init(target) == 0
    out = capsys.readouterr().out
    assert "bad-port (配置无效:" in out
    assert "bad-mcj (配置无效:" in out
    assert out.count("选择要初始化的资源") == 2  # broken choice re-showed the menu


def test_existing_node_flow_broken_dict_returns_to_menu_no_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defense-in-depth round-2 O-MAJOR-1: bypassing the menu validator and
    calling the existing-node entrypoint with a broken dict must not
    traceback — it prints the reason and returns (to the menu)."""
    from acp.init_wizard.flows import _run_existing_node_flow

    broken = _node_entry(name="bad", port="abc")
    _run_existing_node_flow(tmp_path / "cfg.yaml", {}, broken, PromptBundle())
    out = capsys.readouterr().out
    assert "bad (配置无效:" in out


def test_disabled_node_refused_with_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _write_target(tmp_path, [_node_entry(name="sleepy", enabled=False)])
    _script_input(monkeypatch, ["2", "q"])

    assert run_init(target) == 0
    out = capsys.readouterr().out
    assert "sleepy (已禁用)" in out
    assert "enabled: false" in out  # refusal carries the reason (D16d)
    assert out.count("选择要初始化的资源") == 2  # back at the menu, never crashed


def test_run_init_returns_zero_on_quit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "cfg.yaml"
    target.write_bytes(b"")
    _script_input(monkeypatch, ["q"])
    assert run_init(target) == 0


def test_run_init_returns_one_on_init_abort(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "list.yaml"
    target.write_text("- a\n- b\n", encoding="utf-8")
    assert run_init(target) == 1
    assert "错误" in capsys.readouterr().out


def test_eof_abort_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D15: EOF mid-menu is a graceful quit (exit 0), not a traceback."""

    def _eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", _eof)
    assert run_init(tmp_path / "cfg.yaml") == 0


# ---------------------------------------------------------------------------
# flow dispatch + repeat loop
# ---------------------------------------------------------------------------


def test_new_node_flow_dispatch_and_repeat_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Path, dict[str, Any]]] = []

    def fake_run_new_node(
        prompts: PromptBundle, target_path: Path, target_data: dict[str, Any]
    ) -> FlowResult:
        calls.append((target_path, target_data))
        return FlowResult(persisted=True, node_name="n1", aborted_cleanly=False)

    monkeypatch.setattr(flows_module, "run_new_node", fake_run_new_node)
    target = tmp_path / "cfg.yaml"
    target.write_bytes(b"")
    _script_input(monkeypatch, ["2", "q"])

    assert run_init(target) == 0
    assert len(calls) == 1
    assert calls[0][0] == target
    out = capsys.readouterr().out
    assert "n1 已保存" in out
    assert out.count("选择要初始化的资源") == 2  # repeat loop after the flow


def test_local_flow_applies_specs_and_prints_final_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_entries = {
        "orca": SoftwareDiscovery(name="orca", resolved=Path("/fake/orca"), source="path"),
        "xtb": SoftwareDiscovery(name="xtb", resolved=None, source=None),
    }
    monkeypatch.setattr(flows_module, "sniff_local", lambda target: dict(fake_entries))
    monkeypatch.setattr(
        flows_module, "render_sniff_table", lambda entries: f"TABLE({len(entries)})"
    )
    monkeypatch.setattr(
        flows_module, "manual_spec_local", lambda missing, prompts: {"xtb": "/fake/xtb"}
    )
    applied: list[tuple[Path, dict[str, Any], dict[str, str]]] = []
    monkeypatch.setattr(
        flows_module,
        "apply_local_spec",
        lambda target_path, data, specs: applied.append((target_path, data, specs))
        or fake_entries,
    )

    target = tmp_path / "cfg.yaml"
    target.write_bytes(b"")
    _script_input(monkeypatch, ["1", "q"])

    assert run_init(target) == 0
    assert len(applied) == 1
    assert applied[0][0] == target
    assert applied[0][2] == {"xtb": "/fake/xtb"}
    out = capsys.readouterr().out
    assert "TABLE(2)" in out  # initial + final table both rendered


# ---------------------------------------------------------------------------
# existing-node flow happy path (seams patched at the flows module)
# ---------------------------------------------------------------------------


def test_existing_node_flow_happy_path_saves_and_resniffs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from acp.init_wizard.flows import _run_existing_node_flow
    from acp.scheduler.remote import ssh as remote_ssh

    class FakePool:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    pools: list[FakePool] = []
    monkeypatch.setattr(
        remote_ssh,
        "SSHConnectionPool",
        lambda: pools.append(FakePool()) or pools[-1],
    )

    reports = [
        # initial sniff: xtb missing
        {
            "software": {"xtb": {"configured": "xtb", "resolved": None, "version": None}},
            "error": None,
        },
        # re-sniff confirm: xtb resolved from the remote config write
        {
            "software": {
                "xtb": {
                    "configured": "/opt/xtb/bin/xtb",
                    "resolved": "/opt/xtb/bin/xtb",
                    "version": None,
                }
            },
            "error": None,
        },
    ]
    monkeypatch.setattr(flows_module, "remote_home", lambda pool, node: "/home/user")
    monkeypatch.setattr(flows_module, "sniff_remote", lambda pool, node: reports.pop(0))
    written: list[tuple[dict[str, Any], int | None]] = []
    monkeypatch.setattr(
        flows_module,
        "read_remote_config",
        lambda pool, node, home: ({"executables": {}}, None),
    )

    def fake_write(
        pool: object, node: object, home: str, data: dict[str, Any], mode: int | None
    ) -> None:
        written.append((data, mode))

    monkeypatch.setattr(flows_module, "write_remote_config", fake_write)

    node_entry = _node_entry(key_file="~/.ssh/id_rsa")
    target = _write_target(tmp_path, [node_entry])
    target_data = yaml.safe_load(target.read_text(encoding="utf-8"))
    entry = target_data["cluster"]["nodes"][0]

    _script_input(monkeypatch, ["/opt/xtb/bin/xtb", "2"])  # spec path, symlink 跳过
    _run_existing_node_flow(target, target_data, entry, PromptBundle())

    assert len(written) == 1
    assert written[0][0]["executables"]["xtb"]["path"] == "/opt/xtb/bin/xtb"
    assert pools and pools[0].closed  # ONE pool, closed in finally
    assert len(reports) == 0  # initial + re-sniff confirm both consumed
    # ACP-side target persisted: executables + bin_symlinks + capabilities
    saved = yaml.safe_load(target.read_text(encoding="utf-8"))
    saved_node = saved["cluster"]["nodes"][0]
    assert saved_node["executables"]["xtb"]["path"] == "/opt/xtb/bin/xtb"
    assert saved_node["bin_symlinks"]["xtb"] == "/opt/xtb/bin/xtb"
    assert saved_node["capabilities"]["software"] == ["xtb"]
