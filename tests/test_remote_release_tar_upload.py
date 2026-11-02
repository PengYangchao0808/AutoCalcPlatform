"""W-04 — opt-in tar-stream release upload (``FileStager.upload_tar_stream``).

The release upload path used to cost one SFTP ``put`` per file even inside one
session.  ``upload_tar_stream`` packs the whole batch into ONE local tar,
uploads it once, and unpacks it on the node with a single
``mkdir -p ... && tar -xf ... -C ... && rm -f ...`` command.  The path is
*flagged off by default* (``ACP_REMOTE_TAR_UPLOAD``), so ``_upload_missing``
must behave exactly as before unless the operator opts in.

Covered here:
- flag on + large batch → exactly 1 upload + 1 remote tar command
- flag off (unset / "0") → historical per-file path, no tar command
- flag on but small batch → historical per-file path (threshold)
- extraction reproduces the exact content digests (release verification passes)
- archive modes are normalised (exec bit preserved, setuid stripped)
- absolute path / ``..`` traversal / duplicate / symlink item sets are refused
- remote tar failure → ``False`` → caller falls back to per-file upload

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_remote_release_tar_upload.py -q
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import posixpath
import shlex
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.release import (
    ReleaseManifest,
    _tar_upload_enabled,
    _upload_missing,
    _verify_release_dir,
)
from acp.scheduler.remote.sftp import FileStager

STAGING = "/home/test/acp_code/releases/.staging/0d0e0fa11ce00001.abcdef123456"
RELEASE_ID = "0d0e0fa11ce00001"


# ====================================================================== #
# Fake SFTP / SSH
# ====================================================================== #


def _attr(size: int = 0, mtime: float = 0.0, is_dir: bool = False):
    return SimpleNamespace(
        st_size=size, st_mtime=mtime, st_mode=stat.S_IFDIR if is_dir else stat.S_IFREG
    )


class FakeSFTP:
    """Only the primitives ``FileStager`` uses for uploads/verification."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.put_log: list[str] = []

    def put(self, localpath, remotepath):
        with open(localpath, "rb") as handle:
            self.files[remotepath] = handle.read()
        self.put_log.append(remotepath)

    def stat(self, path):
        if path in self.dirs:
            return _attr(is_dir=True)
        if path in self.files:
            return _attr(size=len(self.files[path]))
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self.dirs.add(path)

    def remove(self, path):
        if path in self.files:
            del self.files[path]
            return
        raise FileNotFoundError(path)

    def file(self, remote_path, mode="r"):
        # Only used by upload_text, which the release upload path never calls.
        raise AssertionError(f"unexpected sftp.file({remote_path!r}, {mode!r})")

    def close(self):
        pass


class FakeSSHClient:
    """Records commands; unpacks the uploaded tar exactly like GNU tar would."""

    def __init__(self, sftp: FakeSFTP) -> None:
        self.sftp = sftp
        self.commands: list[str] = []
        self.tar_commands: list[str] = []
        self.tar_fail = False
        self.extracted_modes: dict[str, int] = {}

    def run(self, command: str) -> tuple[int, str, str]:
        self.commands.append(command)
        stripped = command.strip()
        if stripped.startswith("sha256sum "):
            return self._sha256(stripped)
        if "tar -xf" in stripped:
            self.tar_commands.append(stripped)
            return self._untar(stripped)
        return 0, "", ""

    def _sha256(self, command: str) -> tuple[int, str, str]:
        paths = [part for part in shlex.split(command)[1:] if part != "--"]
        missing = [path for path in paths if path not in self.sftp.files]
        if missing:
            return 1, "", f"sha256sum: {missing[0]}: No such file or directory"
        lines = [f"{hashlib.sha256(self.sftp.files[path]).hexdigest()}  {path}\n" for path in paths]
        return 0, "".join(lines), ""

    def _untar(self, command: str) -> tuple[int, str, str]:
        if self.tar_fail:
            return 1, "", "tar: (gzip) unexpected end of file"
        parts = shlex.split(command)
        tar_path = parts[parts.index("-xf") + 1]
        root = parts[parts.index("-C") + 1]
        payload = self.sftp.files.get(tar_path)
        if payload is None:
            return 1, "", f"tar: {tar_path}: Cannot open: No such file or directory"
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive.getmembers():
                self.extracted_modes[member.name] = member.mode
                target = posixpath.join(root, member.name)
                source = archive.extractfile(member)
                self.sftp.files[target] = source.read() if source is not None else b""
                parent = posixpath.dirname(target)
                while parent and parent != "/":
                    self.sftp.dirs.add(parent)
                    parent = posixpath.dirname(parent)
        self.sftp.files.pop(tar_path, None)
        return 0, "", ""


class FakePool:
    """The two pool primitives ``FileStager`` needs (execute + sftp_session)."""

    def __init__(self, sftp: FakeSFTP, client: FakeSSHClient) -> None:
        self.sftp = sftp
        self.client = client

    def execute(self, node, command, timeout=30):  # noqa: ARG002 - signature parity
        return self.client.run(command)

    @contextlib.contextmanager
    def sftp_session(self, node):  # noqa: ARG002 - signature parity
        yield self.sftp


def make_node() -> RemoteNode:
    return RemoteNode(
        name="compute-01",
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=5,
        host_key_policy="auto_add",
    )


def make_env() -> SimpleNamespace:
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    pool = FakePool(sftp, client)
    return SimpleNamespace(
        sftp=sftp,
        client=client,
        pool=pool,
        stager=FileStager(pool),
        node=make_node(),
    )


# ====================================================================== #
# Local release tree + manifest
# ====================================================================== #


def make_release_tree(project_root: Path, count: int = 20) -> ReleaseManifest:
    files: dict[str, dict[str, str | int]] = {}
    for index in range(count):
        relative = f"src/acp/mod_{index:03d}.py"
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"# module {index}\n".encode()
        path.write_bytes(content)
        files[relative] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    return ReleaseManifest(
        schema_version=1,
        release_id=RELEASE_ID,
        files=files,
        requirements_sha256="",
        defaults_sha256="",
        git_commit=None,
        dirty=False,
    )


def upload(env: SimpleNamespace, manifest: ReleaseManifest, project_root: Path) -> int:
    return _upload_missing(env.node, manifest, project_root, STAGING, env.stager, force=False)


# ====================================================================== #
# Flag on — one upload + one remote tar command
# ====================================================================== #


def test_tar_upload_flag_on_single_upload_and_one_tar_command(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    manifest = make_release_tree(tmp_path, count=20)

    assert upload(env, manifest, tmp_path) == 20

    assert len(env.sftp.put_log) == 1
    assert env.sftp.put_log[0].startswith(STAGING + "/")
    assert env.sftp.put_log[0].endswith(".tar")
    assert len(env.client.tar_commands) == 1
    command = env.client.tar_commands[0]
    assert command.startswith(f"mkdir -p {shlex.quote(STAGING)}")
    assert f"-C {shlex.quote(STAGING)}" in command
    # No per-file put slipped through.
    assert not any(path.endswith(".py") for path in env.sftp.put_log)
    # Remote temp tar removed; every manifest file extracted under staging.
    assert not any(path.endswith(".tar") for path in env.sftp.files)
    for relative in manifest.files:
        assert posixpath.join(STAGING, relative) in env.sftp.files
    # The unpacked tree verifies against the manifest (content-equivalent).
    assert _verify_release_dir(env.node, manifest, STAGING, env.stager) is True
    print("  [OK] flag on: 1 upload + 1 tar command, content verifies")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_tar_upload_env_truthy_values(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", value)
    env = make_env()
    manifest = make_release_tree(tmp_path, count=20)

    assert upload(env, manifest, tmp_path) == 20
    assert len(env.client.tar_commands) == 1
    print(f"  [OK] flag on: {value!r} enables the tar path")


def test_tar_upload_preserves_exec_bit_and_strips_setuid(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    manifest = make_release_tree(tmp_path, count=19)
    executable = tmp_path / "src/acp/tool.sh"
    executable.write_bytes(b"#!/bin/sh\necho ok\n")
    os.chmod(executable, 0o4755)  # setuid + exec on the local source
    manifest.files["src/acp/tool.sh"] = {
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "size": executable.stat().st_size,
    }

    assert upload(env, manifest, tmp_path) == 20

    assert env.client.extracted_modes["src/acp/tool.sh"] == 0o755
    assert env.client.extracted_modes["src/acp/mod_000.py"] == 0o644
    print("  [OK] tar member modes: 0755 kept, setuid stripped, files 0644")


def test_tar_upload_large_batched_manifest(tmp_path, monkeypatch):
    """> _BATCH_VERIFY_THRESHOLD files: batched sha256 verify + one tar upload."""
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    manifest = make_release_tree(tmp_path, count=40)

    assert upload(env, manifest, tmp_path) == 40

    assert len(env.sftp.put_log) == 1
    assert len(env.client.tar_commands) == 1
    assert _verify_release_dir(env.node, manifest, STAGING, env.stager) is True
    print("  [OK] batched verify + tar upload: 40 files, 1 upload")


def test_tar_upload_retry_skips_already_staged_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    manifest = make_release_tree(tmp_path, count=20)
    assert upload(env, manifest, tmp_path) == 20
    env.sftp.put_log.clear()
    env.client.commands.clear()
    env.client.tar_commands.clear()

    # A retried ensure resumes the same staging dir: nothing is re-uploaded.
    assert upload(env, manifest, tmp_path) == 0

    assert env.sftp.put_log == []
    assert env.client.tar_commands == []
    print("  [OK] retry: verified staging files are not re-uploaded")


# ====================================================================== #
# Flag off — historical behaviour unchanged
# ====================================================================== #


def test_tar_upload_default_off_uses_per_file_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ACP_REMOTE_TAR_UPLOAD", raising=False)
    env = make_env()
    manifest = make_release_tree(tmp_path, count=20)

    assert upload(env, manifest, tmp_path) == 20

    assert env.client.tar_commands == []
    assert len(env.sftp.put_log) == 20
    assert all(path.endswith(".py") for path in env.sftp.put_log)
    print("  [OK] default off: 20 per-file uploads, no tar command")


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_tar_upload_explicit_off_values(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", value)
    env = make_env()
    manifest = make_release_tree(tmp_path, count=20)

    assert upload(env, manifest, tmp_path) == 20
    assert env.client.tar_commands == []
    assert len(env.sftp.put_log) == 20
    print(f"  [OK] flag off: {value!r} keeps the per-file path")


def test_tar_upload_below_threshold_uses_per_file_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    manifest = make_release_tree(tmp_path, count=5)

    assert upload(env, manifest, tmp_path) == 5

    assert env.client.tar_commands == []
    assert len(env.sftp.put_log) == 5
    print("  [OK] threshold: small batch ignores the flag")


def test_tar_upload_enabled_predicate():
    env = make_env()
    assert _tar_upload_enabled(env.stager, 0) is False
    assert _tar_upload_enabled(env.stager, 16) is False
    print("  [OK] predicate: threshold gate holds")


# ====================================================================== #
# Tar failure → caller falls back to per-file upload
# ====================================================================== #


def test_tar_upload_remote_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_REMOTE_TAR_UPLOAD", "1")
    env = make_env()
    env.client.tar_fail = True
    manifest = make_release_tree(tmp_path, count=20)

    assert upload(env, manifest, tmp_path) == 20

    # One tar attempt (rejected), then every file uploaded per file.
    assert len(env.client.tar_commands) == 1
    assert len(env.sftp.put_log) == 21
    # The failed attempt leaves no temporary tar behind.
    assert not any(path.endswith(".tar") for path in env.sftp.files)
    for relative in manifest.files:
        assert posixpath.join(STAGING, relative) in env.sftp.files
    assert _verify_release_dir(env.node, manifest, STAGING, env.stager) is True
    print("  [OK] tar failure: False -> per-file fallback, no leftovers")


def test_upload_tar_stream_returns_false_on_ssh_exception(tmp_path, monkeypatch):
    env = make_env()
    manifest = make_release_tree(tmp_path, count=3)
    local = tmp_path / "src/acp/mod_000.py"

    def boom(*_args, **_kwargs):
        raise RuntimeError("ssh went away")

    env.pool.execute = boom  # type: ignore[assignment]
    items = [(local, posixpath.join(STAGING, "src/acp/mod_000.py"))]

    assert env.stager.upload_tar_stream(env.node, items, STAGING) is False
    # Best-effort cleanup of the uploaded tar did happen.
    assert not any(path.endswith(".tar") for path in env.sftp.files)
    assert manifest.files  # tree untouched; sanity
    print("  [OK] upload_tar_stream: SSH failure -> False, never raises")


# ====================================================================== #
# Path safety
# ====================================================================== #


def test_upload_tar_stream_rejects_absolute_and_traversal_paths(tmp_path):
    env = make_env()
    local = tmp_path / "payload.py"
    local.write_text("print('x')\n", encoding="utf-8")

    refused = [
        (local, "/etc/passwd"),
        (local, posixpath.join(STAGING, "../escape.py")),
        (local, STAGING),
        (local, posixpath.join(STAGING, "a/../../escape.py")),
    ]
    for item in refused:
        assert env.stager.upload_tar_stream(env.node, [item], STAGING) is False

    assert env.sftp.put_log == []
    assert env.client.tar_commands == []
    print("  [OK] path safety: absolute / .. / root-only paths refused")


def test_upload_tar_stream_rejects_duplicates_and_missing_local(tmp_path):
    env = make_env()
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    target = posixpath.join(STAGING, "src/acp/same.py")

    assert (
        env.stager.upload_tar_stream(env.node, [(first, target), (second, target)], STAGING)
        is False
    )
    assert (
        env.stager.upload_tar_stream(
            env.node, [(tmp_path / "missing.py", posixpath.join(STAGING, "src/acp/x.py"))], STAGING
        )
        is False
    )
    assert env.sftp.put_log == []
    print("  [OK] path safety: duplicate archive names / missing locals refused")


def test_upload_tar_stream_rejects_symlink(tmp_path):
    env = make_env()
    real = tmp_path / "real.py"
    real.write_text("x\n", encoding="utf-8")
    link = tmp_path / "link.py"
    link.symlink_to(real)

    result = env.stager.upload_tar_stream(
        env.node, [(link, posixpath.join(STAGING, "src/acp/link.py"))], STAGING
    )

    assert result is False
    assert env.sftp.put_log == []
    print("  [OK] path safety: symlinked local file refused (per-file path handles it)")


def test_upload_tar_stream_accepts_absolute_destination_under_root(tmp_path):
    env = make_env()
    local = tmp_path / "ok.py"
    local.write_text("ok\n", encoding="utf-8")

    assert (
        env.stager.upload_tar_stream(
            env.node, [(local, posixpath.join(STAGING, "src/acp/ok.py"))], STAGING
        )
        is True
    )

    assert len(env.sftp.put_log) == 1
    assert env.sftp.files[posixpath.join(STAGING, "src/acp/ok.py")] == b"ok\n"
    assert not any(path.endswith(".tar") for path in env.sftp.files)
    print("  [OK] absolute destination under staging is accepted")


def test_upload_tar_stream_empty_items_is_noop(tmp_path):
    env = make_env()

    assert env.stager.upload_tar_stream(env.node, [], STAGING) is True
    assert env.sftp.put_log == []
    assert env.client.commands == []
    print("  [OK] empty batch is a no-op")
