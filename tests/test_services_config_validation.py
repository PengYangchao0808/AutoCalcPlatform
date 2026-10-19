"""Shared Paramiko-free node-config validation suite (todo 37, B07-3).

``acp_services.contracts.config_validation`` is the single schema behind BOTH
the interactive ``acp init`` resource menu and the authoritative
``RemoteNode.from_config_dict`` parser.  This suite locks the extraction's
contract:

* the two consumers reach the *same* conclusion for every corpus entry, and
  flag the *same* offending key;
* each consumer keeps its established message shape (no visibility change);
* the module is stdlib-only and ``acp.init_wizard.flows`` imports with
  ``paramiko`` blocked (core-only install);
* validation never reads a TTY, and ``run_init`` aborts cleanly on EOF
  instead of blocking;
* unknown node fields survive untouched and the env-var password override
  is unchanged.
"""

from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acp.init_wizard.flows import _node_label, run_init
from acp_services.contracts import config_validation as cv
from acp_services.contracts.config_validation import (
    NODE_REQUIRED_KEYS,
    node_entry_error,
    require_valid_node_entry,
    validate_node_entry,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_VALIDATOR_PATH = _SRC / "acp_services" / "contracts" / "config_validation.py"

_VALID: dict[str, object] = {
    "name": "compute-01",
    "host": "10.0.0.1",
    "username": "acp",
    "remote_work_dir": "/scratch/acp/acp_jobs",
    "remote_code_dir": "/home/acp/acp_code",
}

#: Entries covering every previously duplicated check + the valid case.
_CORPUS: list[object] = [
    _VALID,
    {**_VALID, "port": 2222, "max_concurrent_jobs": 8},
    {**_VALID, "port": "abc"},
    {**_VALID, "max_concurrent_jobs": "xyz"},
    {**_VALID, "remote_work_dir": "/path with space"},
    {k: v for k, v in _VALID.items() if k != "name"},
    {k: v for k, v in _VALID.items() if k != "host"},
    {k: v for k, v in _VALID.items() if k != "username"},
    {k: v for k, v in _VALID.items() if k != "remote_work_dir"},
    {k: v for k, v in _VALID.items() if k != "remote_code_dir"},
    "not-a-mapping",
    None,
    [],
]


# ---------------------------------------------------------------------- #
# Single conclusion across interactive / non-interactive
# ---------------------------------------------------------------------- #


def _require_raises(entry: object) -> bool:
    try:
        require_valid_node_entry(entry)
    except ValueError:
        return True
    return False


@pytest.mark.parametrize("entry", _CORPUS)
def test_interactive_and_noninteractive_agree(entry: object) -> None:
    """``node_entry_error`` is None iff ``require_valid_node_entry`` accepts."""
    menu_reason = node_entry_error(entry)
    assert (menu_reason is None) == (not _require_raises(entry))


def test_valid_entry_accepted_by_all_entry_points() -> None:
    assert validate_node_entry(_VALID) is None
    assert node_entry_error(_VALID) is None
    require_valid_node_entry(_VALID)  # must not raise


@pytest.mark.parametrize(
    ("entry", "key"),
    [
        ({k: v for k, v in _VALID.items() if k != "host"}, "host"),
        ({k: v for k, v in _VALID.items() if k != "remote_code_dir"}, "remote_code_dir"),
        ({**_VALID, "port": "abc"}, "port"),
        ({**_VALID, "max_concurrent_jobs": "xyz"}, "max_concurrent_jobs"),
        ({**_VALID, "remote_work_dir": "/a b"}, "remote_work_dir"),
    ],
)
def test_same_offending_key_reported_by_both_paths(entry: object, key: str) -> None:
    menu_reason = node_entry_error(entry)
    assert menu_reason is not None and key in menu_reason
    with pytest.raises(ValueError) as excinfo:
        require_valid_node_entry(entry)
    assert key in str(excinfo.value)


def test_every_required_key_is_mandatory() -> None:
    for key in NODE_REQUIRED_KEYS:
        entry = {k: v for k, v in _VALID.items() if k != key}
        assert validate_node_entry(entry) is not None
        assert _require_raises(entry)


# ---------------------------------------------------------------------- #
# Preserved message shapes (no visibility change)
# ---------------------------------------------------------------------- #


def test_menu_reason_strings_unchanged() -> None:
    assert node_entry_error("x") == "节点条目必须是映射"
    assert node_entry_error({**_VALID, "port": "abc"}) == "port 不是有效整数（'abc'）"
    assert (
        node_entry_error({**_VALID, "max_concurrent_jobs": "xyz"})
        == "max_concurrent_jobs 不是有效整数（'xyz'）"
    )
    assert (
        node_entry_error({**_VALID, "remote_work_dir": "/a b"})
        == "remote_work_dir 不能包含空格（'/a b'）"
    )
    assert (
        node_entry_error({k: v for k, v in _VALID.items() if k != "host"}) == "缺少必需字段 'host'"
    )


def test_remote_message_shapes_unchanged() -> None:
    with pytest.raises(ValueError, match="must not contain spaces"):
        require_valid_node_entry({**_VALID, "remote_work_dir": "/a b"})
    with pytest.raises(ValueError, match="missing required key: 'host'"):
        require_valid_node_entry({k: v for k, v in _VALID.items() if k != "host"})


# ---------------------------------------------------------------------- #
# Delegation: both consumers use the shared validator
# ---------------------------------------------------------------------- #


def test_flows_delegates_label_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    import acp.init_wizard.flows as flows

    seen: list[object] = []

    def _spy(entry: object) -> str:
        seen.append(entry)
        return "BOOM"

    monkeypatch.setattr(flows, "node_entry_error", _spy)
    assert _node_label(_VALID) == "compute-01 (配置无效: BOOM)"
    assert seen == [_VALID]


def test_parser_delegates_to_shared_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("paramiko")
    import acp.scheduler.remote.config as remote_config

    seen: list[object] = []

    def _spy(entry: dict[str, object]) -> None:
        seen.append(entry)
        raise ValueError("sentinel-from-validator")

    monkeypatch.setattr(remote_config, "require_valid_node_entry", _spy)
    with pytest.raises(ValueError, match="sentinel-from-validator"):
        remote_config.RemoteNode.from_config_dict(_VALID)
    assert seen == [_VALID]


# ---------------------------------------------------------------------- #
# Core-only install: no paramiko, no acp import
# ---------------------------------------------------------------------- #


def test_validator_module_is_stdlib_only() -> None:
    tree = ast.parse(_VALIDATOR_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "dataclasses", "typing"}, imported


def test_flows_imports_with_paramiko_blocked() -> None:
    """A core-only install must import the wizard + validator cleanly."""
    code = (
        "import sys\n"
        "sys.modules['paramiko'] = None\n"
        "import acp_services.contracts.config_validation as v\n"
        "import acp.init_wizard.flows as f\n"
        "assert hasattr(f, 'run_init')\n"
        "assert v.node_entry_error({'name': 'n', 'host': 'h', 'username': 'u',"
        " 'remote_work_dir': '/w', 'remote_code_dir': '/c'}) is None\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_SRC) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------- #
# No-TTY: never blocks, aborts with a clear code
# ---------------------------------------------------------------------- #


def test_validation_never_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("validation must not touch stdin/TTY")

    monkeypatch.setattr("builtins.input", _boom)
    assert node_entry_error(_VALID) is None
    with pytest.raises(ValueError):
        require_valid_node_entry({**_VALID, "port": "abc"})


def test_run_init_no_tty_aborts_without_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _eof(*args: object, **kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    target = tmp_path / "cfg.yaml"
    target.write_bytes(b"")
    assert run_init(target) == 0  # graceful abort, never a hang
    assert "已中止" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# Unknown fields + env override semantics preserved
# ---------------------------------------------------------------------- #


def test_unknown_fields_not_rejected_or_rewritten() -> None:
    entry = {**_VALID, "custom": {"keep": 1}, "note": "hi", "port": 2222}
    snapshot = copy.deepcopy(entry)
    assert validate_node_entry(entry) is None
    require_valid_node_entry(entry)
    assert entry == snapshot  # validator never mutates / strips keys


def test_unknown_fields_preserved_by_parser() -> None:
    pytest.importorskip("paramiko")
    from acp.scheduler.remote.config import RemoteNode

    node = RemoteNode.from_config_dict({**_VALID, "custom": {"keep": 1}, "note": "hi"})
    assert node.name == "compute-01"
    assert node.remote_work_dir == "/scratch/acp/acp_jobs"
    assert "custom" not in node.to_config_dict()


def test_env_password_override_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("paramiko")
    from acp.scheduler.remote.config import RemoteNode, _env_var_name

    node = RemoteNode.from_config_dict(_VALID)
    monkeypatch.setenv(_env_var_name(node.name), "s3cret")
    assert node.resolved_password() == "s3cret"


def test_cv_module_exports_public_surface() -> None:
    assert set(cv.__all__) == {
        "NODE_REQUIRED_KEYS",
        "NodeConfigViolation",
        "node_entry_error",
        "require_valid_node_entry",
        "validate_node_entry",
    }
