"""Local CLI paths must not require the optional ``[remote]`` extra.

F3 found that a bare root wheel (no ``paramiko``) failed every local
``acp run singlepoint`` because the publication composition chain imported the
remote package at module import.  The remote import is now performed only when
a remote operation is actually used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_local_publication_and_simple_workflow_import_without_paramiko() -> None:
    code = """
import sys

class BlockParamiko:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "paramiko" or fullname.startswith("paramiko."):
            raise ModuleNotFoundError("No module named 'paramiko'", name="paramiko")
        return None

sys.meta_path.insert(0, BlockParamiko())
import acp.services_data_bootstrap
import acp.services_publication_bootstrap
import acp.workflows.simple
assert "paramiko" not in sys.modules
assert "acp.scheduler.remote" not in sys.modules, sorted(
    name for name in sys.modules if name.startswith("acp.scheduler.remote")
)
print("ok")
"""
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "ok"
