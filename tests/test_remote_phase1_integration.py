"""
Phase 1 real integration tests against 10.16.5.157.

Requires: ACP_REMOTE_PASSWORD_COMPUTE_01 env var (NOT stored in any file).
Skips automatically if the env var or paramiko is missing.

Run with:
    PYTHONPATH=src ACP_REMOTE_PASSWORD_COMPUTE_01='...' \
        python3 tests/test_remote_phase1_integration.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.sync import CodeSyncer

NODE = RemoteNode(
    name="compute-01",
    host="10.16.5.157",
    username="<user>",
    remote_work_dir="/home/<user>/acp_test_jobs",
    remote_code_dir="/home/<user>/acp_test_code",
    max_concurrent_jobs=5,
    host_key_policy="auto_add",
)
TEST_BASE = "/home/<user>/acp_test_phase1"


def _check_creds() -> bool:
    return bool(os.environ.get("ACP_REMOTE_PASSWORD_COMPUTE_01"))


pytestmark = pytest.mark.skipif(
    not _check_creds(),
    reason="Requires ACP_REMOTE_PASSWORD_COMPUTE_01 env var for real SSH node",
)


def _cleanup(pool: SSHConnectionPool, stager: FileStager) -> None:
    try:
        pool.execute(
            NODE,
            f"rm -rf {TEST_BASE} {NODE.remote_code_dir} {NODE.remote_work_dir}",
            timeout=15,
        )
    except Exception:
        pass
    pool.close()


def test_ssh_connect_and_run() -> int:
    """SSH execute returns hostname/whoami."""
    pool = SSHConnectionPool()
    try:
        code, out, err = pool.execute(NODE, "hostname && whoami", timeout=15)
        assert code == 0, (code, out, err)
        assert "<user>" in out, out
        print(f"  [OK] SSH connect: {out.strip()}")
        return 0
    finally:
        pool.close()


def test_sftp_roundtrip() -> int:
    """upload_text + read_remote_text + tail_log + list_remote_dir."""
    pool = SSHConnectionPool()
    stager = FileStager(pool)
    try:
        d = f"{TEST_BASE}/sftp"
        stager.make_remote_dir(NODE, d)
        stager.upload_text(NODE, "line1\nline2\n", f"{d}/log.txt")
        content = stager.read_remote_text(NODE, f"{d}/log.txt")
        assert content == "line1\nline2\n", repr(content)
        print("  [OK] upload_text/read_remote_text roundtrip")

        data, off = stager.tail_log(NODE, f"{d}/log.txt", offset=0)
        assert data == b"line1\nline2\n" and off == 12
        pool.execute(NODE, f'echo "line3" >> {d}/log.txt')
        data2, off2 = stager.tail_log(NODE, f"{d}/log.txt", offset=off)
        assert data2 == b"line3\n", repr(data2)
        print(f"  [OK] tail_log incremental: +{len(data2)} bytes, offset {off}->{off2}")

        listing = stager.list_remote_dir(NODE, d)
        assert any(e.name == "log.txt" for e in listing)
        print(f"  [OK] list_remote_dir: {[e.name for e in listing]}")

        assert stager.remote_exists(NODE, f"{d}/log.txt") is True
        assert stager.remote_exists(NODE, f"{d}/nope") is False
        print("  [OK] remote_exists")
        return 0
    finally:
        _cleanup(pool, stager)


def test_codesyncer_real() -> int:
    """Full code sync + incremental + exclusion verification + remote import."""
    pool = SSHConnectionPool()
    stager = FileStager(pool)
    state_dir = Path(tempfile.mkdtemp(prefix="acp_sync_"))
    syncer = CodeSyncer(pool, state_dir=state_dir)
    try:
        # First sync — all files
        r1 = syncer.sync_code(NODE, force=True)
        assert r1.ok, r1.errors[:3]
        assert r1.uploaded == r1.total
        print(f"  [OK] first sync: {r1.uploaded}/{r1.total} files")

        # Verify exclusions
        checks = [
            ("src/acp/cli.py", True),
            ("src/acp/scheduler/__init__.py", False),
            ("src/acp/api/server.py", False),
            ("src/cccp/__init__.py", True),
            ("scripts/run_g16_worker.sh", False),
            ("config/defaults.yaml", False),
        ]
        for rel, expected in checks:
            exists = stager.remote_exists(NODE, f"{NODE.remote_code_dir}/{rel}")
            assert exists == expected, f"{rel}: expected {expected}, got {exists}"
        print("  [OK] sync exclusions verified (api/scheduler/config excluded)")

        # Second sync — no changes
        r2 = syncer.sync_code(NODE)
        assert r2.uploaded == 0 and r2.skipped == r2.total
        print(f"  [OK] incremental: 0 uploaded, {r2.skipped} skipped")

        # check_sync_needed
        assert syncer.check_sync_needed(NODE) is False
        print("  [OK] check_sync_needed=False after sync")

        # Remote can import the execution path
        script = (
            "import sys\n"
            f'sys.path.insert(0, "{NODE.remote_code_dir}/src")\n'
            "from acp.cli import main\n"
            "from acp.workflows.ensemble import run_ensemble_generation\n"
            "from cccp.core.engine import ConformerEngine\n"
            "print('IMPORT_OK')\n"
            "try:\n"
            "    import acp.scheduler\n"
            "    print('ERROR: scheduler importable')\n"
            "except ModuleNotFoundError:\n"
            "    print('scheduler_excluded_OK')\n"
        )
        stager.upload_text(NODE, script, f"{NODE.remote_code_dir}/t.py")
        code, out, err = pool.execute(NODE, f"python3 {NODE.remote_code_dir}/t.py", timeout=30)
        assert code == 0 and "IMPORT_OK" in out and "scheduler_excluded_OK" in out, (code, out, err)
        print("  [OK] remote imports execution path; scheduler correctly excluded")

        # CLI --help
        code, out, err = pool.execute(
            NODE,
            f"PYTHONPATH={NODE.remote_code_dir}/src python3 -m acp.cli --help 2>&1 | head -1",
            timeout=20,
        )
        assert code == 0 and "usage: acp" in out, (code, out, err)
        print("  [OK] acp.cli --help on remote")
        return 0
    finally:
        _cleanup(pool, stager)
        shutil.rmtree(state_dir, ignore_errors=True)


def main() -> int:
    if not _check_creds():
        print("SKIP: set ACP_REMOTE_PASSWORD_COMPUTE_01 to run integration tests")
        return 0
    tests = [test_ssh_connect_and_run, test_sftp_roundtrip, test_codesyncer_real]
    failed = 0
    for t in tests:
        print(f"RUN  {t.__name__}")
        try:
            failed += t()
        except Exception as e:
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{'=' * 60}")
    print(f"Integration: {len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
