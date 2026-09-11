"""Tests for the init-wizard new-node full flow (plan acp-init T7).

Fully scripted prompts via a fake PromptBundle source; every T5/T6 stage
referenced from the newnode namespace is patched WHERE IT IS REFERENCED
(``acp.init_wizard.newnode.*``), and ``SSHConnectionPool`` is patched at its
source module because ``run_new_node`` imports it function-locally (D7).
One integration-style test runs the REAL T5/T6 stage functions against a
fake pool/SFTP with only ``paramiko.SSHClient`` + ``NodeManager`` mocked.
No real network.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

pytest.importorskip("paramiko")

from acp.init_wizard.newnode import (  # noqa: E402
    BootstrapOutcome,
    ConnectedAuth,
    FlowResult,
    PromptBundle,
    run_new_node,
)
from acp.init_wizard.persist import InitAbort, load_target  # noqa: E402
from acp.scheduler.remote.config import RemoteNode  # noqa: E402
from acp.scheduler.remote.node_manager import BootstrapResult  # noqa: E402

KEY_FILE_RAW = "~/keys/id_ed25519"
KEY_FILE_EXPANDED = str(Path(KEY_FILE_RAW).expanduser())

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakePrompts:
    """Scripted PromptBundle source recording every prompt call.

    Scripted empty ask answers fall back to the prompt default (mirrors
    real ``ask``); exhausted scripts yield ``""`` everywhere, which the
    flow must treat as skip/default.
    """

    def __init__(
        self,
        menu_choices: list[int] | None = None,
        ask_answers: list[str] | None = None,
        secrets: list[str] | None = None,
        remote_dirs: list[str] | None = None,
    ) -> None:
        self.menu_choices = list(menu_choices or [])
        self.ask_answers = list(ask_answers or [])
        self.secrets = list(secrets or [])
        self.remote_dirs = list(remote_dirs or [])
        self.menu_calls: list[tuple[str, list[str], bool]] = []
        self.ask_calls: list[str] = []
        self.secret_calls: list[str] = []
        self.remote_dir_calls: list[tuple[str, str]] = []

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
        answer = self.ask_answers.pop(0) if self.ask_answers else ""
        if answer == "" and default is not None:
            answer = default
        if validate is not None and answer:
            error = validate(answer)
            assert error is None, f"scripted answer {answer!r} failed validation: {error}"
        return answer

    def ask_secret(self, prompt: str) -> str:
        self.secret_calls.append(prompt)
        return self.secrets.pop(0) if self.secrets else ""

    def ask_local_path(self, prompt: str) -> Path:
        raise AssertionError("ask_local_path not expected in the new-node flow")

    def ask_remote_dir(self, prompt: str, default: str) -> str:
        self.remote_dir_calls.append((prompt, default))
        return self.remote_dirs.pop(0) if self.remote_dirs else default

    def bundle(self) -> PromptBundle:
        return PromptBundle(
            menu=self.menu,
            ask=self.ask,
            ask_secret=self.ask_secret,
            ask_local_path=self.ask_local_path,
            ask_remote_dir=self.ask_remote_dir,
        )


class FakeSftpFile(io.BytesIO):
    """Fake SFTP file: text mode accepts str; captures writes to the sink."""

    def __init__(self, sink: dict[str, bytes], remote_path: str) -> None:
        super().__init__()
        self._sink = sink
        self._path = remote_path
        original = super().write

        def capturing_write(data: Any) -> int:
            written = original(data.encode("utf-8") if isinstance(data, str) else data)
            sink[remote_path] = self.getvalue()
            return written

        self.write = capturing_write  # type: ignore[assignment]


class FakeSftp:
    """In-memory SFTP subset the real FileStager exercises."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def file(self, remote_path: str, mode: str = "r") -> Any:
        if "w" in mode or "a" in mode:
            return FakeSftpFile(self.files, remote_path)
        return io.BytesIO(self.files.get(remote_path, b""))

    def stat(self, path: str) -> Any:
        if path in self.dirs:
            attr = mock.MagicMock()
            attr.st_mode = stat.S_IFDIR
            attr.st_size = 0
            attr.st_mtime = 0.0
            return attr
        if path not in self.files:
            raise FileNotFoundError(path)
        attr = mock.MagicMock()
        attr.st_mode = stat.S_IFREG
        attr.st_size = len(self.files[path])
        attr.st_mtime = 0.0
        return attr


class FakeSshPool:
    """Fake SSHConnectionPool: canned execute results + close() tracking.

    Command results match by first substring rule; the python version probe
    succeeds by default (phase1 FakeSSHClient precedent).
    """

    def __init__(
        self,
        sftp: FakeSftp | None = None,
        results: list[tuple[str, tuple[int, str, str]]] | None = None,
    ) -> None:
        self.sftp = sftp if sftp is not None else FakeSftp()
        self.results: list[tuple[str, tuple[int, str, str]]] = list(results or [])
        self.commands: list[str] = []
        self.closed = False

    def execute(self, node: Any, command: str, timeout: int = 30) -> tuple[int, str, str]:
        self.commands.append(command)
        for needle, result in self.results:
            if needle in command:
                return result
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        return (0, "", "")

    @contextlib.contextmanager
    def sftp_session(self, node: Any) -> Iterator[FakeSftp]:
        yield self.sftp

    def close(self, node_name: str | None = None) -> None:
        self.closed = True


class StageMocks:
    """Patches every T5/T6 seam in the newnode namespace + the pool class.

    Pool is patched at ``acp.scheduler.remote.ssh`` (run_new_node imports it
    function-locally); every created pool is tracked in ``self.pools``.
    """

    def __init__(
        self,
        auth: ConnectedAuth | None = None,
        connect_ok: bool = True,
        outcome: BootstrapOutcome | None = None,
        software: dict[str, Any] | None = None,
        remote_data: dict[str, Any] | None = None,
        remote_mode: int | None = 0o644,
        home: str = "/home/ops",
    ) -> None:
        self.auth = (
            auth
            if auth is not None
            else ConnectedAuth(host="10.0.0.8", port=22, username="ops", password="pw")
        )
        self.outcome = outcome or BootstrapOutcome(ok=True, python_executable=None, error=None)
        self.software = software if software is not None else {}
        self.remote_data = remote_data if remote_data is not None else {}
        self.remote_mode = remote_mode
        self.home = home
        self.bootstrap_requests: list[tuple[Any, Any]] = []
        self.pools: list[FakeSshPool] = []
        self.connect_stage = mock.MagicMock(return_value=self.auth if connect_ok else None)

        def _bootstrap(pool: Any, node: Any, prompts: Any) -> BootstrapOutcome:
            self.bootstrap_requests.append((pool, node))
            self.bootstrap_passwords.append(node.password)
            return self.outcome

        self.bootstrap = mock.MagicMock(side_effect=_bootstrap)
        self.bootstrap_passwords: list[str | None] = []
        self.remote_home_m = mock.MagicMock(return_value=self.home)
        self.sniff = mock.MagicMock(
            return_value={"software": self.software, "error": None, "reachable": True}
        )
        self.read_cfg = mock.MagicMock(return_value=(self.remote_data, self.remote_mode))
        self.write_cfg = mock.MagicMock()
        self.symlinks = mock.MagicMock()

    def _pool_factory(self) -> Any:
        registry = self.pools

        class _Factory:
            def __call__(self, *args: Any, **kwargs: Any) -> FakeSshPool:
                pool = FakeSshPool()
                registry.append(pool)
                return pool

        return _Factory()

    def __enter__(self) -> StageMocks:
        stack = contextlib.ExitStack()
        self._stack = stack
        enter = stack.enter_context
        enter(mock.patch("acp.init_wizard.newnode.run_connect_stage", self.connect_stage))
        enter(mock.patch("acp.init_wizard.newnode.run_bootstrap_stage", self.bootstrap))
        enter(mock.patch("acp.init_wizard.newnode.remote_home", self.remote_home_m))
        enter(mock.patch("acp.init_wizard.newnode.sniff_remote", self.sniff))
        enter(mock.patch("acp.init_wizard.newnode.read_remote_config", self.read_cfg))
        enter(mock.patch("acp.init_wizard.newnode.write_remote_config", self.write_cfg))
        enter(mock.patch("acp.init_wizard.newnode.make_remote_symlinks", self.symlinks))
        enter(
            mock.patch(
                "acp.scheduler.remote.ssh.SSHConnectionPool",
                self._pool_factory(),
            )
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stack.close()


def _sw(resolved: str | None) -> dict[str, Any]:
    """Doctor-shaped software entry for scripted sniff reports."""
    return {"configured": resolved or "probe-name", "resolved": resolved, "version": None}


def _write_target(tmp_path: Path, data: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    target = tmp_path / "cccp.yaml"
    target.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target, load_target(target)


_KEY_AUTH = ConnectedAuth(
    host="10.0.0.8",
    port=22,
    username="ops",
    key_file=KEY_FILE_EXPANDED,
    host_key_policy="auto_add",
)


# --------------------------------------------------------------------------- #
# Clean aborts (menu q / connect abort / bootstrap give-up)
# --------------------------------------------------------------------------- #


def test_type_menu_quit_aborts_cleanly(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    before = target.read_text(encoding="utf-8")
    prompts = FakePrompts(menu_choices=[0])
    with StageMocks() as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    assert target.read_text(encoding="utf-8") == before
    assert m.pools == []
    assert prompts.menu_calls[0] == ("集群类型", ["LSF", "Openlava"], True)


def test_auth_menu_quit_aborts_cleanly(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {})
    prompts = FakePrompts(menu_choices=[1, 0], ask_answers=["10.0.0.8", "n1", "22", "ops"])
    with StageMocks() as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    assert m.pools == []
    assert "nodes" not in target_data.get("cluster", {})


def test_connect_abort_aborts_cleanly(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {})
    prompts = FakePrompts(menu_choices=[1, 2], ask_answers=["10.0.0.8", "n1", "22", "ops"])
    with StageMocks(connect_ok=False) as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    assert m.pools == []
    assert "nodes" not in target_data.get("cluster", {})


def test_bootstrap_give_up_persists_nothing(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    before = target.read_text(encoding="utf-8")
    prompts = FakePrompts(
        menu_choices=[1, 1],
        ask_answers=["10.0.0.9", "node-b", "22", "ops"],
        secrets=["pw"],
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.9", port=22, username="ops", password="pw"),
        outcome=BootstrapOutcome(ok=False, python_executable=None, error="pip exploded"),
    ) as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=False, node_name=None, aborted_cleanly=True)
    # NOTHING persisted: no node, no cluster.type/execution_mode write, no backup.
    assert target.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("cccp.yaml.bak-*"))
    assert "nodes" not in target_data.get("cluster", {})
    assert len(m.pools) == 1 and m.pools[0].closed


# --------------------------------------------------------------------------- #
# Happy path (key auth) — full YAML acceptance
# --------------------------------------------------------------------------- #


def test_happy_path_key_auth_persists_expected_yaml(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    prompts = FakePrompts(
        menu_choices=[2, 2, 1, 1],  # Openlava, key auth, create symlinks, execution_mode remote
        ask_answers=[
            "10.0.0.8",
            "node-a",
            "22",
            "ops",
            KEY_FILE_RAW,
            "",  # max_concurrent_jobs → default 5
            "",  # queue → default normal
            "/opt/orca/orca",
        ],
    )
    real_to_dict = RemoteNode.to_config_dict
    with StageMocks(
        auth=_KEY_AUTH,
        outcome=BootstrapOutcome(ok=True, python_executable="/opt/venv/bin/python", error=None),
        software={"xtb": _sw("/opt/xtb/bin/xtb"), "orca": _sw(None)},
        remote_data={"custom": {"keep": 1}},
        remote_mode=0o644,
    ) as m:
        with mock.patch.object(
            RemoteNode,
            "to_config_dict",
            autospec=True,
            side_effect=lambda self: real_to_dict(self),
        ) as spy:
            result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=True, node_name="node-a", aborted_cleanly=False)
    spy.assert_called_once()  # persistence shape came from RemoteNode.to_config_dict

    # connect stage received the pre-asked start values
    start_values = m.connect_stage.call_args.args[1]
    assert start_values["host"] == "10.0.0.8"
    assert start_values["port"] == 22
    assert start_values["username"] == "ops"
    assert start_values["password"] is None
    assert start_values["key_file"] == KEY_FILE_EXPANDED

    # the in-memory node carried the credentials + type for the pool stage
    pool_arg, node_arg = m.bootstrap_requests[0]
    assert pool_arg is m.pools[0]
    assert node_arg.type == "openlava"
    assert node_arg.key_file == KEY_FILE_EXPANDED
    assert node_arg.host_key_policy == "auto_add"
    assert node_arg.remote_code_dir == "~/acp_code"
    assert node_arg.remote_work_dir == "~/acp_jobs"
    assert node_arg.max_concurrent_jobs == 5
    assert node_arg.queue == "normal"

    # final target YAML
    final = yaml.safe_load(target.read_text(encoding="utf-8"))
    entry = final["cluster"]["nodes"][0]
    assert entry["name"] == "node-a"
    assert entry["type"] == "openlava"
    assert entry["key_file"] == KEY_FILE_EXPANDED
    assert entry["python_executable"] == "/opt/venv/bin/python"  # D14c back-write
    assert entry["bin_symlinks"] == {"orca": "/opt/orca/orca"}
    assert entry["capabilities"] == {"software": ["orca"], "tags": []}
    assert entry["queue"] == "normal"
    assert entry["host_key_policy"] == "auto_add"
    assert "password" not in entry
    assert "port" not in entry  # default 22 omitted by to_config_dict
    assert final["cluster"]["type"] == "openlava"  # local was overwritten (D2)
    assert final["cluster"]["execution_mode"] == "remote"
    assert "enabled" not in final["cluster"]  # GAP-1: wizard never writes cluster.enabled
    assert "password" not in target.read_text(encoding="utf-8")
    assert list(tmp_path.glob("cccp.yaml.bak-*"))  # backup created next to the target

    # remote config write merged the SAME executables paths, preserving remote keys
    w_args = m.write_cfg.call_args.args
    assert w_args[2] == "/home/ops"
    assert w_args[3]["executables"]["orca"]["path"] == "/opt/orca/orca"
    assert w_args[3]["custom"] == {"keep": 1}
    assert w_args[4] == 0o644

    # immediate symlink creation went through the one shared pool
    symlink_node = m.symlinks.call_args.args[1]
    assert symlink_node.name == "node-a"
    assert m.symlinks.call_args.args[2] == {"orca": "/opt/orca/orca"}
    assert m.remote_home_m.call_count == 1

    # ONE pool, closed exactly on the finally path
    assert len(m.pools) == 1 and m.pools[0].closed


# --------------------------------------------------------------------------- #
# D5 password paths (round-3 O-MAJOR)
# --------------------------------------------------------------------------- #


def test_password_auth_default_opt_out_keeps_password_out_of_yaml(
    tmp_path: Path, capsys: Any
) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    prompts = FakePrompts(
        menu_choices=[1, 1, 1, 2],  # LSF, 密码, D5 opt-out, D3 decline
        ask_answers=["10.0.0.9", "node-b", "22", "ops"],
        secrets=["s3cret"],
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.9", port=22, username="ops", password="s3cret"),
        outcome=BootstrapOutcome(ok=True, python_executable="python", error=None),
    ) as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=True, node_name="node-b", aborted_cleanly=False)
    text = target.read_text(encoding="utf-8")
    assert "password" not in text  # (f): NO password key anywhere in the YAML
    final = yaml.safe_load(text)
    entry = final["cluster"]["nodes"][0]
    assert "password" not in entry
    assert "python_executable" not in entry  # resolved "python" → no back-write
    assert "execution_mode" not in final["cluster"]  # D3 declined keeps local mode
    assert final["cluster"]["type"] == "lsf"

    # the in-memory node carried the password for the pool ops...
    assert m.bootstrap_passwords == ["s3cret"]
    # ...and was sanitized (password = None) before serialization
    node = m.bootstrap_requests[0][1]
    assert node.password is None
    assert "export ACP_REMOTE_PASSWORD_NODE_B=s3cret" in capsys.readouterr().out
    assert len(prompts.secret_calls) == 1  # opt-out asks NO re-type confirmation
    assert len(m.pools) == 1 and m.pools[0].closed


def test_password_auth_opt_in_stores_password_with_mode_600(tmp_path: Path, capsys: Any) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    prompts = FakePrompts(
        menu_choices=[1, 1, 2, 1],  # LSF, 密码, D5 opt-in, D3 remote
        ask_answers=["10.0.0.9", "node-b", "22", "ops"],
        secrets=["s3cret", "s3cret"],  # password + re-type confirm phrase
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.9", port=22, username="ops", password="s3cret"),
        outcome=BootstrapOutcome(ok=True, python_executable=None, error=None),
    ) as m:
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=True, node_name="node-b", aborted_cleanly=False)
    text = target.read_text(encoding="utf-8")
    final = yaml.safe_load(text)
    assert final["cluster"]["nodes"][0]["password"] == "s3cret"  # (f2)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600  # (f2): explicit 0600
    assert final["cluster"]["execution_mode"] == "remote"
    out = capsys.readouterr().out
    assert "export ACP_REMOTE_PASSWORD_" not in out  # stored → no env instruction
    assert len(m.pools) == 1 and m.pools[0].closed


def test_password_confirm_mismatch_falls_back_to_opt_out(tmp_path: Path, capsys: Any) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    prompts = FakePrompts(
        menu_choices=[1, 1, 2, 2],  # LSF, 密码, D5 opt-in (mismatch), D3 decline
        ask_answers=["10.0.0.9", "node-b", "22", "ops"],
        secrets=["s3cret", "nope"],
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.9", port=22, username="ops", password="s3cret"),
        outcome=BootstrapOutcome(ok=True, python_executable=None, error=None),
    ):
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=True, node_name="node-b", aborted_cleanly=False)
    text = target.read_text(encoding="utf-8")
    assert "password" not in text  # mismatch → opt-out sanitization
    assert stat.S_IMODE(target.stat().st_mode) != 0o600
    assert "export ACP_REMOTE_PASSWORD_NODE_B=s3cret" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Name collision (D14d)
# --------------------------------------------------------------------------- #


def test_name_collision_default_reprompts_and_keeps_original(tmp_path: Path) -> None:
    existing = {
        "name": "node-a",
        "host": "old-host",
        "username": "u",
        "remote_work_dir": "/w",
        "remote_code_dir": "/c",
    }
    target, target_data = _write_target(tmp_path, {"cluster": {"nodes": [existing]}})
    prompts = FakePrompts(
        menu_choices=[1, 1, 2, 2],  # LSF, 换个名称, key auth, D3 decline
        ask_answers=["10.0.0.8", "node-a", "node-b", "22", "ops", KEY_FILE_RAW],
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.8", port=22, username="ops", key_file=KEY_FILE_EXPANDED),
        outcome=BootstrapOutcome(ok=True, python_executable=None, error=None),
    ):
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result.node_name == "node-b"
    final = yaml.safe_load(target.read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in final["cluster"]["nodes"]}
    assert nodes["node-a"]["host"] == "old-host"  # original entry untouched
    assert nodes["node-b"]["host"] == "10.0.0.8"
    collision_menu = prompts.menu_calls[1]
    assert "node-a" in collision_menu[0]
    assert collision_menu[1] == ["换个名称（推荐）", "覆盖已有节点"]


def test_name_collision_overwrite_replaces_entry(tmp_path: Path) -> None:
    existing = {
        "name": "node-a",
        "host": "old-host",
        "username": "u",
        "remote_work_dir": "/w",
        "remote_code_dir": "/c",
    }
    target, target_data = _write_target(tmp_path, {"cluster": {"nodes": [existing]}})
    prompts = FakePrompts(
        menu_choices=[1, 2, 2, 2],  # LSF, 覆盖, key auth, D3 decline
        ask_answers=["10.0.0.8", "node-a", "22", "ops", KEY_FILE_RAW],
    )
    with StageMocks(
        auth=ConnectedAuth(host="10.0.0.8", port=22, username="ops", key_file=KEY_FILE_EXPANDED),
        outcome=BootstrapOutcome(ok=True, python_executable=None, error=None),
    ):
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result.node_name == "node-a"
    final = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert len(final["cluster"]["nodes"]) == 1
    assert final["cluster"]["nodes"][0]["host"] == "10.0.0.8"


# --------------------------------------------------------------------------- #
# Pool hygiene on exception paths
# --------------------------------------------------------------------------- #


def test_pool_closed_and_nothing_persisted_when_remote_write_aborts(tmp_path: Path) -> None:
    target, target_data = _write_target(tmp_path, {"cluster": {"type": "local"}})
    before = target.read_text(encoding="utf-8")
    prompts = FakePrompts(
        menu_choices=[2, 2, 2, 2],  # Openlava, key auth, skip symlinks, D3 decline
        ask_answers=[
            "10.0.0.8",
            "node-a",
            "22",
            "ops",
            KEY_FILE_RAW,
            "",
            "",
            "/opt/orca/orca",
        ],
    )
    with StageMocks(
        auth=_KEY_AUTH,
        outcome=BootstrapOutcome(ok=True, python_executable=None, error=None),
        software={"orca": _sw(None)},
    ) as m:
        m.write_cfg.side_effect = InitAbort("远端写入失败")
        with pytest.raises(InitAbort):
            run_new_node(prompts.bundle(), target, target_data)

    assert m.pools[0].closed  # finally-close on the exception path
    assert target.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("cccp.yaml.bak-*"))


# --------------------------------------------------------------------------- #
# Integration-style: real T5/T6 stages over a fake SSH pool (mocked SSH only)
# --------------------------------------------------------------------------- #


def test_integration_real_stages_over_fake_ssh_pool(tmp_path: Path, capsys: Any) -> None:
    target = tmp_path / "cccp.yaml"
    target.write_text("", encoding="utf-8")  # empty target loads as {}
    target_data = load_target(target)
    prompts = FakePrompts(
        menu_choices=[1, 1, 1, 1, 1],  # LSF, 密码, create symlinks, D5 opt-out, D3 remote
        ask_answers=[
            "10.0.0.8",
            "node-i",
            "22",
            "ops",
            "",  # max_concurrent_jobs → default 5
            "",  # queue → default normal
            "/opt/orca/orca",
        ],
        secrets=["s3cret"],
    )
    sftp = FakeSftp()
    sweep = "".join(
        f"{n}=\n"
        for n in ("orca", "xtb", "crest", "censo", "Shermo", "shermo", "isostat", "molclus")
    )
    shared_pool = FakeSshPool(
        sftp=sftp,
        results=[
            ("echo $HOME", (0, "/home/ops\n", "")),
            ("cccp.software", (1, "", "ModuleNotFoundError: No module named 'cccp'")),
            ("command -v", (0, sweep, "")),
        ],
    )

    fake_client_cls = mock.MagicMock(name="paramiko.SSHClient")
    with (
        mock.patch("paramiko.SSHClient", fake_client_cls),
        mock.patch("acp.scheduler.remote.ssh.SSHConnectionPool", lambda *a, **k: shared_pool),
        mock.patch("acp.scheduler.remote.node_manager.NodeManager") as nm_cls,
    ):
        nm_cls.return_value.bootstrap_node.return_value = BootstrapResult(
            node="node-i",
            reachable=True,
            exit_code=0,
            python_executable="python3.12",
            stderr="Successfully installed numpy rdkit",
        )
        result = run_new_node(prompts.bundle(), target, target_data)

    assert result == FlowResult(persisted=True, node_name="node-i", aborted_cleanly=False)
    assert shared_pool.closed  # the flow's own pool instance was closed

    text = target.read_text(encoding="utf-8")
    assert "password" not in text
    final = yaml.safe_load(text)
    entry = final["cluster"]["nodes"][0]
    assert entry["type"] == "lsf"
    assert entry["python_executable"] == "python3.12"  # D14c from BootstrapResult
    assert entry["bin_symlinks"] == {"orca": "/opt/orca/orca"}
    assert entry["capabilities"] == {"software": ["orca"], "tags": []}
    assert entry["queue"] == "normal"
    assert final["cluster"]["type"] == "lsf"
    assert final["cluster"]["execution_mode"] == "remote"
    assert "export ACP_REMOTE_PASSWORD_NODE_I=s3cret" in capsys.readouterr().out

    # the REAL remote write uploaded the merged executables path
    tmp_key = f"/home/ops/.cccp.yaml.tmp-{os.getpid()}"
    uploaded = yaml.safe_load(sftp.files[tmp_key].decode("utf-8"))
    assert uploaded["executables"]["orca"]["path"] == "/opt/orca/orca"
    # the REAL symlink creation issued the bootstrap-shaped command
    assert "mkdir -p ~/bin && ln -sf /opt/orca/orca ~/bin/orca" in shared_pool.commands
    # the REAL connect stage probed once through its own paramiko client
    assert fake_client_cls.return_value.connect.call_count == 1
    # bootstrap ran against an in-memory config holding exactly the flow's node
    cfg = nm_cls.call_args.args[0]
    assert cfg.execution_mode == "remote"
    assert cfg.nodes[0].name == "node-i"
