"""Tests for the init-wizard newnode connection/bootstrap half (plan T6).

NO real network: ``paramiko.SSHClient`` is patched for every
``connect_once``/``run_connect_stage`` test; the bootstrap tests patch
``NodeManager`` entirely.  The wizard module itself must not import
paramiko or ``acp.scheduler.remote`` at module level (D7) — locked by a
discipline test.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

pytest.importorskip("paramiko")

import paramiko  # noqa: E402  (importorskip guard above)

from acp.init_wizard.newnode import (  # noqa: E402
    BootstrapOutcome,
    ConnectedAuth,
    ConnectResult,
    PromptBundle,
    TofuCapture,
    connect_once,
    run_bootstrap_stage,
    run_connect_stage,
)
from acp.scheduler.remote.config import RemoteNode  # noqa: E402
from acp.scheduler.remote.node_manager import BootstrapResult  # noqa: E402

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

_FP_BYTES = b"\xde\xad\xbe\xef" * 8
_FP_HEX = ":".join(f"{b:02x}" for b in _FP_BYTES)


class _FakeKey:
    """Duck-typed stand-in for paramiko PKey (fingerprint + key name)."""

    def __init__(self, fingerprint: bytes = _FP_BYTES) -> None:
        self._fingerprint = fingerprint

    def get_fingerprint(self) -> bytes:
        return self._fingerprint

    def get_name(self) -> str:
        return "ssh-ed25519"

    def get_base64(self) -> str:
        return "AAAABASE64FAKEKEY"


class _FakePrompts:
    """Scripted PromptBundle source recording every prompt call."""

    def __init__(
        self,
        menu_choices: list[int] | None = None,
        ask_answers: list[str] | None = None,
        secrets: list[str] | None = None,
    ) -> None:
        self.menu_choices = list(menu_choices or [])
        self.ask_answers = list(ask_answers or [])
        self.secrets = list(secrets or [])
        self.menu_calls: list[tuple[str, list[str], bool]] = []
        self.ask_calls: list[str] = []
        self.secret_calls: list[str] = []

    def menu(self, title: str, options: list[str], allow_q: bool = True) -> int:
        self.menu_calls.append((title, list(options), allow_q))
        return self.menu_choices.pop(0)

    def ask(
        self,
        prompt: str,
        default: str | None = None,
        validate: Any = None,
        allow_empty: bool = False,
    ) -> str:
        self.ask_calls.append(prompt)
        if self.ask_answers:
            answer = self.ask_answers.pop(0)
        elif default is not None:
            answer = default
        else:
            answer = ""
        if validate is not None and answer:
            error = validate(answer)
            assert error is None, f"scripted answer {answer!r} failed validation: {error}"
        return answer

    def ask_secret(self, prompt: str) -> str:
        self.secret_calls.append(prompt)
        return self.secrets.pop(0) if self.secrets else ""

    def ask_local_path(self, prompt: str) -> Path:
        raise AssertionError("ask_local_path not expected in these scenarios")

    def ask_remote_dir(self, prompt: str, default: str) -> str:
        raise AssertionError("ask_remote_dir not expected in these scenarios")

    def bundle(self) -> PromptBundle:
        return PromptBundle(
            menu=self.menu,
            ask=self.ask,
            ask_secret=self.ask_secret,
            ask_local_path=self.ask_local_path,
            ask_remote_dir=self.ask_remote_dir,
        )


class _ClientHarness:
    """Patch helper: a fake paramiko.SSHClient class with queued connect effects.

    ``holder`` captures whatever host-key policy the code under test installs
    so tests can assert on it (TofuCapture fingerprint, RejectPolicy default).
    Create with ``_client([...])``; effects may be wired afterwards via
    ``harness.instance.connect.side_effect = [...]`` (needed when an effect
    must reference the harness itself).
    """

    def __init__(self, effects: list[Any]) -> None:
        self.holder: dict[str, Any] = {}
        self._mock_cls = mock.MagicMock(name="paramiko.SSHClient")
        instance = self._mock_cls.return_value
        instance.set_missing_host_key_policy.side_effect = self._capture_policy
        instance.connect.side_effect = list(effects)
        self._instance = instance
        self._patcher = mock.patch("paramiko.SSHClient", self._mock_cls)

    def _capture_policy(self, policy: Any) -> None:
        self.holder["policy"] = policy

    def __enter__(self) -> _ClientHarness:
        self._patcher.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._patcher.stop()

    @property
    def instance(self) -> Any:
        return self._instance


def _client(effects: list[Any]) -> _ClientHarness:
    return _ClientHarness(effects)


def _tofu_effect(harness: _ClientHarness, host: str) -> Any:
    """Plain-function connect() side effect simulating paramiko's TOFU path.

    NOTE: callables inside an iterable side_effect are RETURNED, not called
    (CPython mock `_execute_mock_call`), so this must be installed as a
    plain-function side_effect.  paramiko's real connect invokes
    ``policy.missing_host_key(...)`` for an unknown host; the duck-typed
    TofuCapture then raises RuntimeError out of connect() — reproduced here
    through the policy the code under test installed.  Later connects run
    under AutoAddPolicy and succeed.
    """

    def effect(**kwargs: Any) -> None:
        policy = harness.holder.get("policy")
        if isinstance(policy, TofuCapture):
            policy.missing_host_key(None, host, _FakeKey())

    return effect


# --------------------------------------------------------------------------- #
# connect_once unit tests (paramiko.SSHClient patched)
# --------------------------------------------------------------------------- #


def test_connect_once_success_reject_policy_default() -> None:
    with _client([None]) as harness:
        result = connect_once("10.0.0.8", 22, "ops")
    assert isinstance(result, ConnectResult)
    assert result.ok is True
    assert result.policy_to_persist is None
    assert result.fingerprint is None
    assert isinstance(harness.holder["policy"], paramiko.RejectPolicy)
    harness.instance.load_system_host_keys.assert_called_once_with()
    kwargs = harness.instance.connect.call_args.kwargs
    assert kwargs["hostname"] == "10.0.0.8"
    assert kwargs["port"] == 22
    assert kwargs["username"] == "ops"
    assert "timeout" in kwargs
    assert "password" not in kwargs
    assert "key_filename" not in kwargs


def test_connect_once_auto_add_policy_reports_persist() -> None:
    with _client([None]) as harness:
        result = connect_once("10.0.0.8", 22, "ops", policy="auto_add")
    assert result.ok is True
    assert result.policy_to_persist == "auto_add"
    assert isinstance(harness.holder["policy"], paramiko.AutoAddPolicy)


def test_connect_once_credentials_expand_key_file() -> None:
    with _client([None]) as harness:
        result = connect_once(
            "10.0.0.8",
            2222,
            "ops",
            password="s3cret",
            key_file="~/id_rsa",
        )
    assert result.ok is True
    kwargs = harness.instance.connect.call_args.kwargs
    assert kwargs["password"] == "s3cret"
    assert kwargs["key_filename"] == str(Path("~/id_rsa").expanduser())
    assert kwargs["port"] == 2222


def test_connect_once_bad_host_key_maps_to_mitm_abort() -> None:
    exc = paramiko.BadHostKeyException("10.0.0.8", _FakeKey(), _FakeKey(b"\x00" * 32))
    with _client([exc]):
        result = connect_once("10.0.0.8", 22, "ops")
    assert result.ok is False
    assert "MITM" in result.detail
    assert "known_hosts" in result.detail
    assert result.policy_to_persist is None
    assert result.fingerprint is None


def test_connect_once_tofu_capture_records_fingerprint() -> None:
    harness = _client([])
    harness.instance.connect.side_effect = _tofu_effect(harness, "10.0.0.8")
    with harness:
        tofu = TofuCapture()
        first = connect_once("10.0.0.8", 22, "ops", policy=tofu)
    assert first.ok is False
    assert first.detail == "unknown-host"
    assert first.fingerprint == _FP_HEX
    assert first.policy_to_persist is None
    assert tofu.fingerprint == _FP_HEX


def test_connect_once_generic_error_carries_text() -> None:
    with _client([OSError("network is down")]):
        result = connect_once("10.0.0.8", 22, "ops", password="pw")
    assert result.ok is False
    assert "network is down" in result.detail
    assert result.policy_to_persist is None


# --------------------------------------------------------------------------- #
# run_connect_stage — acceptance criteria (a) (b) (c)
# --------------------------------------------------------------------------- #


def test_acceptance_a_unknown_host_tofu_accept_then_auto_add(capsys: Any) -> None:
    """(a) unknown-host → fingerprint hex shown → accept → auto_add persisted."""
    harness = _client([])
    harness.instance.connect.side_effect = _tofu_effect(harness, "10.0.0.8")
    prompts = _FakePrompts(menu_choices=[1, 1], secrets=["s3cret"])
    with harness:
        auth = run_connect_stage(
            prompts.bundle(),
            {"host": "10.0.0.8", "port": 22, "username": "ops"},
        )

    assert isinstance(auth, ConnectedAuth)
    assert auth.host == "10.0.0.8"
    assert auth.port == 22
    assert auth.username == "ops"
    assert auth.password == "s3cret"
    assert auth.key_file is None
    assert auth.host_key_policy == "auto_add"

    # The TofuCapture policy installed on connect #1 recorded the fingerprint.
    first_policy = harness.instance.set_missing_host_key_policy.call_args_list[0].args[0]
    assert isinstance(first_policy, TofuCapture)
    assert first_policy.fingerprint == _FP_HEX
    # Second connect ran under AutoAddPolicy.
    second_policy = harness.instance.set_missing_host_key_policy.call_args_list[1].args[0]
    assert isinstance(second_policy, paramiko.AutoAddPolicy)
    assert harness.instance.connect.call_count == 2

    # The fingerprint hex was shown, and the accept prompt carried the warning.
    out = capsys.readouterr().out
    assert _FP_HEX in out
    accept_menu = prompts.menu_calls[1]
    assert "记住" in accept_menu[0] or "接受" in accept_menu[0]
    assert any("静默接受" in opt for opt in accept_menu[1])


def test_acceptance_b_bad_host_key_aborts_without_accept_prompt(capsys: Any) -> None:
    """(b) MITM abort message, zero prompts, no auto_add retry."""
    exc = paramiko.BadHostKeyException("10.0.0.8", _FakeKey(), _FakeKey(b"\x00" * 32))
    prompts = _FakePrompts()
    with _client([exc]) as harness:
        auth = run_connect_stage(
            prompts.bundle(),
            {
                "host": "10.0.0.8",
                "port": 22,
                "username": "ops",
                "password": "s3cret",
            },
        )

    assert auth is None
    assert harness.instance.connect.call_count == 1  # no accept/retry path
    out = capsys.readouterr().out
    assert "MITM" in out
    assert "known_hosts" in out
    # NO prompt of any kind was invoked.
    assert prompts.menu_calls == []
    assert prompts.ask_calls == []
    assert prompts.secret_calls == []


def test_acceptance_c_auth_failure_three_times_returns_none(capsys: Any) -> None:
    """(c) auth failure ×3 → None (back to the parent menu)."""
    prompts = _FakePrompts(
        menu_choices=[1, 1, 1],  # auth menu=密码, then 重试, 重试
        # host, port (validated), username — port takes the scripted "22"
        ask_answers=["10.0.0.9", "22", "ops"],
        secrets=["pw"],
    )
    with _client([paramiko.AuthenticationException("bad credentials")]) as harness:
        auth = run_connect_stage(prompts.bundle(), {})

    assert auth is None
    assert harness.instance.connect.call_count == 3
    # The failure menu offered exactly the D11 options on each failure.
    for title, options, _allow_q in prompts.menu_calls[1:]:
        assert "失败" in title
        assert options == ["重试", "重新输入认证信息", "返回上级菜单"]
    out = capsys.readouterr().out
    assert "3" in out  # exhaustion notice mentions the attempt budget


# --------------------------------------------------------------------------- #
# run_bootstrap_stage — acceptance criteria (d) (d2) + python-probe retry
# --------------------------------------------------------------------------- #


def _make_node() -> RemoteNode:
    return RemoteNode(
        name="n1",
        host="10.0.0.8",
        username="ops",
        remote_work_dir="~/acp_jobs",
        remote_code_dir="~/acp_code",
    )


def test_acceptance_d_bootstrap_error_retry_then_give_up(capsys: Any) -> None:
    """(d) bootstrap error → 重试 → error again → 放弃 → outcome.ok=False."""
    node = _make_node()
    prompts = _FakePrompts(menu_choices=[1, 2])
    with mock.patch("acp.scheduler.remote.node_manager.NodeManager") as nm_cls:
        manager = nm_cls.return_value
        manager.bootstrap_node.side_effect = [
            BootstrapResult(node="n1", reachable=False, error="pip exploded"),
            BootstrapResult(node="n1", reachable=False, error="pip exploded again"),
        ]
        outcome = run_bootstrap_stage(mock.MagicMock(), node, prompts.bundle())

    assert isinstance(outcome, BootstrapOutcome)
    assert outcome.ok is False
    assert outcome.error == "pip exploded again"
    assert manager.bootstrap_node.call_count == 2
    assert manager.bootstrap_node.call_args_list[0].args == ("n1",)
    assert prompts.menu_calls[0][1] == ["重试 bootstrap", "放弃（节点不保存）"]
    out = capsys.readouterr().out
    assert "pip exploded" in out

    # (d2) the manager was constructed from an IN-MEMORY config holding
    # exactly this in-memory node object (same identity, not persisted).
    cfg = nm_cls.call_args.args[0]
    assert cfg.execution_mode == "remote"
    assert len(cfg.nodes) == 1
    assert cfg.nodes[0] is node


def test_acceptance_d2_in_memory_config_identity() -> None:
    """(d2) dedicated: ctor config carries exactly the in-memory node."""
    node = _make_node()
    prompts = _FakePrompts()
    with mock.patch("acp.scheduler.remote.node_manager.NodeManager") as nm_cls:
        nm_cls.return_value.bootstrap_node.return_value = BootstrapResult(
            node="n1", reachable=True, exit_code=0, python_executable="python3.12"
        )
        outcome = run_bootstrap_stage(mock.MagicMock(), node, prompts.bundle())

    assert outcome.ok is True
    assert outcome.python_executable == "python3.12"
    cfg = nm_cls.call_args.args[0]
    assert cfg.nodes[0] is node
    assert cfg.get_node("n1") is node


def test_bootstrap_python_probe_failure_retry_with_path() -> None:
    """重试 on a python-probe failure prompts a path and mutates the node."""
    node = _make_node()
    prompts = _FakePrompts(
        menu_choices=[1],
        ask_answers=["/opt/miniconda3/bin/python"],
    )
    with mock.patch("acp.scheduler.remote.node_manager.NodeManager") as nm_cls:
        manager = nm_cls.return_value
        manager.bootstrap_node.side_effect = [
            BootstrapResult(
                node="n1",
                reachable=True,
                error=(
                    "no usable Python interpreter: no Python 3.10+ interpreter "
                    "found — configure cluster.nodes[].python_executable"
                ),
            ),
            BootstrapResult(
                node="n1",
                reachable=True,
                exit_code=0,
                python_executable="/opt/miniconda3/bin/python",
                stderr="Successfully installed numpy rdkit",
            ),
        ]
        outcome = run_bootstrap_stage(mock.MagicMock(), node, prompts.bundle())

    assert outcome.ok is True
    assert outcome.python_executable == "/opt/miniconda3/bin/python"
    assert "Successfully installed" in outcome.stderr_tail
    assert node.python_executable == "/opt/miniconda3/bin/python"
    assert manager.bootstrap_node.call_count == 2


def test_bootstrap_give_up_on_python_probe_no_mutation() -> None:
    """放弃 on a python-probe failure leaves the node untouched."""
    node = _make_node()
    prompts = _FakePrompts(menu_choices=[2])
    with mock.patch("acp.scheduler.remote.node_manager.NodeManager") as nm_cls:
        manager = nm_cls.return_value
        manager.bootstrap_node.return_value = BootstrapResult(
            node="n1",
            reachable=True,
            error="no usable Python interpreter: probed all candidates",
        )
        outcome = run_bootstrap_stage(mock.MagicMock(), node, prompts.bundle())

    assert outcome.ok is False
    assert "no usable Python interpreter" in outcome.error
    assert node.python_executable == "python"
    assert prompts.ask_calls == []  # 放弃 never prompts for a python path
    assert manager.bootstrap_node.call_count == 1


# --------------------------------------------------------------------------- #
# Import discipline (D7) — locked at the newnode module level
# --------------------------------------------------------------------------- #


def test_module_level_import_discipline() -> None:
    import acp.init_wizard.newnode as newnode

    assert "paramiko" not in vars(newnode)
    for value in vars(newnode).values():
        module = getattr(value, "__module__", "")
        assert not module.startswith("acp.scheduler"), (
            f"module-level acp.scheduler import leaked: {value!r} from {module}"
        )
    source = inspect.getsource(newnode)
    assert "import paramiko" not in source.split("def connect_once")[0]
