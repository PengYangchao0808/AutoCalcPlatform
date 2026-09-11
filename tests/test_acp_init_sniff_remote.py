"""Tests for acp.init_wizard.sniff_remote (plan acp-init T5).

Covers: remote_home, single-command shell PATH fallback probe (D8), the
doctor_node wrapper with fallback merge, the D9 remote-config read/write
protocol (backup -> tmp upload -> chmod -> mv), apply_remote_manual_spec
(D9 both-sides mutation + D12 space rejection), and make_remote_symlinks.

Fake pool/SFTP objects imitate tests/test_remote_phase1.py shapes
(FakeSFTPFile / FakeSFTP) — no real network I/O. The REAL FileStager runs
against the fake SFTP so remote_exists/read_remote_text/upload_text
semantics are genuinely exercised.
"""

from __future__ import annotations

import io
import os
import stat
from contextlib import contextmanager
from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from acp.init_wizard import sniff_remote
from acp.init_wizard.persist import InitAbort
from acp.scheduler.remote.config import RemoteNode

HOME = "/home/tester"
CFG_PATH = f"{HOME}/.cccp.yaml"

# ====================================================================== #
# Fakes (shapes imitate tests/test_remote_phase1.py)
# ====================================================================== #


class FakeSFTPFile(io.BytesIO):
    """Fake SFTP file: text mode accepts/returns str, binary mode bytes."""

    def __init__(self, data: bytes = b"", mode: str = "rb"):
        super().__init__(data if "b" in mode else b"")
        self.text_mode = "b" not in mode

    def write(self, data: Any) -> int:
        if self.text_mode and isinstance(data, str):
            data = data.encode("utf-8")
        return super().write(data)

    def read(self, size: int = -1) -> Any:
        raw = super().read(size)
        return raw.decode("utf-8") if self.text_mode else raw


class FakeSFTP:
    """In-memory mock of paramiko.SFTPClient (subset FileStager needs)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def _attr(self, is_dir: bool) -> MagicMock:
        attr = MagicMock()
        attr.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        attr.st_size = 0
        attr.st_mtime = 0.0
        return attr

    def file(self, remote_path: str, mode: str = "r") -> FakeSFTPFile:
        if "w" in mode or "a" in mode:
            handle = FakeSFTPFile(b"", mode)
            original_write = handle.write

            def capturing_write(data: Any) -> int:
                written = original_write(data)
                self.files[remote_path] = handle.getvalue()
                return written

            handle.write = capturing_write  # type: ignore[assignment]
            return handle
        return FakeSFTPFile(self.files.get(remote_path, b""), mode)

    def stat(self, path: str) -> MagicMock:
        if path in self.dirs:
            return self._attr(is_dir=True)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self._attr(is_dir=False)

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)


class FakePool:
    """Fake SSHConnectionPool: logs commands, matches canned results by substring.

    Unmatched commands return ``(0, "", "")`` except the python version
    probe, which succeeds by default (phase1 FakeSSHClient precedent).
    """

    def __init__(
        self,
        sftp: FakeSFTP | None = None,
        results: list[tuple[str, tuple[int, str, str]]] | None = None,
    ) -> None:
        self.sftp = sftp if sftp is not None else FakeSFTP()
        # Ordered (substring, (code, stdout, stderr)) rules, first match wins.
        self.results: list[tuple[str, tuple[int, str, str]]] = list(results or [])
        self.commands: list[str] = []

    def execute(self, node: RemoteNode, command: str, timeout: int = 30) -> tuple[int, str, str]:
        self.commands.append(command)
        for needle, result in self.results:
            if needle in command:
                return result
        if "sys.version_info" in command:
            return (0, "3.12.4\n", "")
        return (0, "", "")

    @contextmanager
    def sftp_session(self, node: RemoteNode):  # type: ignore[no-untyped-def]
        yield self.sftp


def make_node() -> RemoteNode:
    return RemoteNode(
        name="node-a",
        host="10.0.0.1",
        username="tester",
        remote_work_dir="/scratch/acp_jobs",
        remote_code_dir=f"{HOME}/acp_code",
    )


# ====================================================================== #
# remote_home / shell_path_probe
# ====================================================================== #


def test_remote_home_strips_output():
    node = make_node()
    pool = FakePool(results=[("echo $HOME", (0, f"  {HOME}\n", ""))])
    assert sniff_remote.remote_home(pool, node) == HOME
    assert pool.commands == ["echo $HOME"]


def test_shell_path_probe_single_command_parses_all_names():
    node = make_node()
    sweep_out = (
        "orca=/opt/orca/orca\n"
        "xtb=\n"
        "crest=/usr/bin/crest\n"
        "censo=\n"
        "Shermo=/opt/shermo/Shermo\n"
        "shermo=\n"
        "isostat=\n"
        "molclus=\n"
    )
    pool = FakePool(results=[("command -v", (0, sweep_out, ""))])
    found = sniff_remote.shell_path_probe(pool, node)
    # ONE SSH command for the whole sweep (D8).
    assert len(pool.commands) == 1
    assert "command -v" in pool.commands[0]
    for name in ("orca", "xtb", "crest", "censo", "Shermo", "shermo", "isostat", "molclus"):
        assert name in found
    assert found["orca"] == "/opt/orca/orca"
    assert found["xtb"] is None
    assert found["Shermo"] == "/opt/shermo/Shermo"


# ====================================================================== #
# sniff_remote — doctor wrapper + shell fallback (acceptance b)
# ====================================================================== #


def test_sniff_remote_falls_back_when_software_probe_fails():
    node = make_node()
    sweep_out = "xtb=/opt/xtb/bin/xtb\n" + "".join(
        f"{n}=\n" for n in ("orca", "crest", "censo", "Shermo", "shermo", "isostat", "molclus")
    )
    pool = FakePool(
        results=[
            ("cccp.software", (1, "", "ModuleNotFoundError: No module named 'cccp'")),
            ("command -v", (0, sweep_out, "")),
        ]
    )
    report = sniff_remote.sniff_remote(pool, node)
    assert report["software_fallback"] is True
    # Doctor error retained; software mapping filled from the shell probe.
    assert report["error"] is not None
    assert report["reachable"] is True
    xtb = report["software"]["xtb"]
    # Mirrors the doctor entry shape {configured, resolved, version} + source marker.
    assert xtb == {
        "configured": "/opt/xtb/bin/xtb",
        "resolved": "/opt/xtb/bin/xtb",
        "version": None,
        "source": "shell",
    }
    assert report["software"]["censo"] == {
        "configured": "censo",
        "resolved": None,
        "version": None,
        "source": "shell",
    }
    # All seven doctor names present.
    for name in ("orca", "xtb", "crest", "censo", "shermo", "isostat", "molclus"):
        assert name in report["software"]


def test_sniff_remote_falls_back_when_no_python_early_return():
    node = make_node()
    sweep_out = "shermo=/opt/shermo/shermo\n" + "".join(
        f"{n}=\n" for n in ("orca", "xtb", "crest", "censo", "Shermo", "isostat", "molclus")
    )
    pool = FakePool(
        results=[
            # No candidate interpreter works -> doctor python=None early-return
            # (error stays None, software stays {}) — fallback must still run.
            ("sys.version_info", (1, "", "command not found")),
            ("command -v", (0, sweep_out, "")),
        ]
    )
    report = sniff_remote.sniff_remote(pool, node)
    assert report["software_fallback"] is True
    assert report["error"] is None
    assert report["python"] is None
    assert report["software"]["shermo"]["resolved"] == "/opt/shermo/shermo"


def test_sniff_remote_keeps_doctor_report_when_probe_succeeds():
    node = make_node()
    doctor_json = (
        '{"xtb": {"configured": "xtb", "resolved": "/opt/xtb/bin/xtb", "version": "6.7.0"}}'
    )
    pool = FakePool(results=[("cccp.software", (0, doctor_json + "\n", ""))])
    report = sniff_remote.sniff_remote(pool, node)
    assert report["software_fallback"] is False
    assert report["software"]["xtb"]["version"] == "6.7.0"
    assert "source" not in report["software"]["xtb"]
    for key in ("node", "host", "reachable", "python", "software", "symlinks", "error"):
        assert key in report


# ====================================================================== #
# apply_remote_manual_spec — pure dict mutation (D9/D12)
# ====================================================================== #


def test_apply_remote_manual_spec_merges_preserving_order_and_dedup():
    data: dict[str, Any] = {
        "cluster": {
            "nodes": [
                {
                    "name": "node-a",
                    "bin_symlinks": {"orca": "/opt/orca/orca"},
                    "capabilities": {"software": ["orca"], "tags": ["gpu"]},
                }
            ]
        }
    }
    sniff_remote.apply_remote_manual_spec(
        data, "node-a", {"xtb": "/opt/xtb/bin/xtb", "orca": "/new/orca"}
    )
    node_entry = data["cluster"]["nodes"][0]
    # executables.<name>.path = REMOTE paths (doctor/remote-config shape).
    assert node_entry["executables"]["xtb"]["path"] == "/opt/xtb/bin/xtb"
    assert node_entry["executables"]["orca"]["path"] == "/new/orca"
    # bin_symlinks on the node entry.
    assert node_entry["bin_symlinks"]["xtb"] == "/opt/xtb/bin/xtb"
    assert node_entry["bin_symlinks"]["orca"] == "/new/orca"
    # capabilities.software: existing order preserved + append new + dedup.
    assert node_entry["capabilities"]["software"] == ["orca", "xtb"]
    assert node_entry["capabilities"]["tags"] == ["gpu"]
    # RemoteNode.from_config_dict must produce the plain-list dict shape.
    assert isinstance(node_entry["capabilities"], dict)


def test_apply_remote_manual_spec_creates_missing_node_entry():
    data: dict[str, Any] = {}
    sniff_remote.apply_remote_manual_spec(data, "fresh-node", {"xtb": "/x/xtb"})
    node_entry = data["cluster"]["nodes"][0]
    assert node_entry["name"] == "fresh-node"
    assert node_entry["bin_symlinks"] == {"xtb": "/x/xtb"}
    assert node_entry["capabilities"]["software"] == ["xtb"]


def test_apply_remote_manual_spec_rejects_space_before_mutating():
    data: dict[str, Any] = {"cluster": {"nodes": [{"name": "node-a", "queue": "normal"}]}}
    snapshot = deepcopy(data)
    with pytest.raises(ValueError, match="space|空格"):
        sniff_remote.apply_remote_manual_spec(data, "node-a", {"xtb": "/opt/xt b/xtb"})
    assert data == snapshot
    with pytest.raises(ValueError):
        sniff_remote.apply_remote_manual_spec(data, "node-a", {"xtb": ""})
    assert data == snapshot


# ====================================================================== #
# read_remote_config / write_remote_config (D9 protocol)
# ====================================================================== #


def test_read_write_roundtrip_preserves_mode_and_command_order(monkeypatch: pytest.MonkeyPatch):
    node = make_node()
    sftp = FakeSFTP()
    sftp.files[CFG_PATH] = yaml.dump(
        {"executables": {"orca": {"path": "/opt/orca/orca"}}},
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    pool = FakePool(sftp=sftp, results=[("stat -c %a", (0, "600\n", ""))])
    monkey_stamp = "20260911-120000"
    monkeypatch.setattr(sniff_remote, "_backup_timestamp", lambda: monkey_stamp)
    data, mode = sniff_remote.read_remote_config(pool, node, HOME)
    assert mode == 0o600
    assert data["executables"]["orca"]["path"] == "/opt/orca/orca"

    sniff_remote.apply_remote_manual_spec(data, "node-a", {"xtb": "/opt/xtb/bin/xtb"})
    sniff_remote.write_remote_config(pool, node, HOME, data, mode)

    pid = os.getpid()
    tmp_path = f"{CFG_PATH}.tmp-{pid}"
    backup_path = f"{CFG_PATH}.bak-{monkey_stamp}"
    # Command sequence IN ORDER: backup copy -> chmod tmp -> mv over target.
    cp_idx = next(i for i, c in enumerate(pool.commands) if c.startswith("cp -p "))
    chmod_idx = next(i for i, c in enumerate(pool.commands) if c.startswith("chmod "))
    mv_idx = next(i for i, c in enumerate(pool.commands) if c.startswith("mv "))
    assert pool.commands[cp_idx] == f"cp -p {CFG_PATH} {backup_path}"
    assert pool.commands[chmod_idx] == f"chmod 600 {tmp_path}"
    assert pool.commands[mv_idx] == f"mv {tmp_path} {CFG_PATH}"
    assert cp_idx < chmod_idx < mv_idx
    # Tmp upload carries the mutated payload (node entry executables + capabilities).
    written = yaml.safe_load(sftp.files[tmp_path].decode("utf-8"))
    assert written["executables"]["orca"]["path"] == "/opt/orca/orca"
    node_entry = written["cluster"]["nodes"][0]
    assert node_entry["executables"]["xtb"]["path"] == "/opt/xtb/bin/xtb"
    assert node_entry["capabilities"]["software"] == ["xtb"]


def test_write_fresh_node_defaults_to_644_and_skips_backup():
    node = make_node()
    pool = FakePool()  # no files on the node; default (0, "", "") for stat
    data, mode = sniff_remote.read_remote_config(pool, node, HOME)
    assert data == {}
    assert mode is None
    sniff_remote.apply_remote_manual_spec(data, "node-a", {"xtb": "/opt/xtb/bin/xtb"})
    sniff_remote.write_remote_config(pool, node, HOME, data, mode)
    assert not any(c.startswith("cp -p ") for c in pool.commands)
    assert f"chmod 644 {CFG_PATH}.tmp-{os.getpid()}" in pool.commands
    assert any(c.startswith("mv ") for c in pool.commands)


def test_read_remote_config_parse_error_aborts_without_writes():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files[CFG_PATH] = b"executables: [unclosed\n"
    pool = FakePool(sftp=sftp)
    with pytest.raises(InitAbort) as excinfo:
        sniff_remote.read_remote_config(pool, node, HOME)
    assert CFG_PATH in str(excinfo.value)
    # NO write side-effects: no cp/chmod/mv commands, no tmp uploads.
    assert not any(c.startswith(("cp -p ", "chmod ", "mv ")) for c in pool.commands)
    assert not any(".tmp-" in key for key in sftp.files)


def test_read_remote_config_non_dict_root_aborts():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files[CFG_PATH] = b"- a\n- b\n"
    pool = FakePool(sftp=sftp)
    with pytest.raises(InitAbort, match="映射"):
        sniff_remote.read_remote_config(pool, node, HOME)


def test_read_remote_config_empty_file_loads_empty_dict():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files[CFG_PATH] = b""
    pool = FakePool(sftp=sftp, results=[("stat -c %a", (0, "644\n", ""))])
    data, mode = sniff_remote.read_remote_config(pool, node, HOME)
    assert data == {}
    assert mode == 0o644


# ====================================================================== #
# make_remote_symlinks
# ====================================================================== #


def test_make_remote_symlinks_issues_bootstrap_command():
    node = make_node()
    pool = FakePool()
    sniff_remote.make_remote_symlinks(pool, node, {"xtb": "/opt/xtb/bin/xtb"})
    assert pool.commands == ["mkdir -p ~/bin && ln -sf /opt/xtb/bin/xtb ~/bin/xtb"]
