"""Probe an installed release role from outside the repository (no source path)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def science_probe() -> dict:
    import cccp
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
    from cccp.calculation.tasks.singlepoint import run_singlepoint
    from cccp.qc.interfaces.base import QCResult

    class ControlledBackend:
        name = "orca"

        def single_point(self, coordinates, symbols, **kwargs):
            assert kwargs["method"] == "HF"
            return QCResult(success=True, energy=-1.0, symbols=list(symbols), converged=True)

    result = run_singlepoint(
        TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("He",)),
            level=MethodSpec(method="HF", basis="def2-SVP"),
        ),
        context=TaskContext(backend=ControlledBackend()),
    )
    assert result.complete and result.energy_hartree == -1.0
    assets = Path(cccp.__file__).parent / "analysis/nmr/model_assets"
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(assets.iterdir())
        if p.is_file()
    }
    assert {"NOTICE.md", "LICENSE-DP5", "atomic_reps.gz", "frag_reps.gz"} <= hashes.keys()
    return {"controlled_science": "PASS", "model_assets": hashes}


def worker_probe(root: Path) -> dict:
    from acp_services.contracts.envelope import ExecutionEnvelope, expected_submission_id
    from acp_services.runtime.attempt_freeze import AttemptFreezeSpec, write_frozen_attempt
    from acp_services.runtime.execution_chain import frozen_group_dir
    from acp_services.runtime.worker import FrozenStep
    from acp_services.worker_bootstrap import freeze_frozen_plan

    plan = freeze_frozen_plan(
        payload={"task": "singlepoint"},
        steps=(FrozenStep(kind="singlepoint", payload={"task": "singlepoint"}),),
        item_symbols=("He",),
        plan_identity="v2:release-probe",
        step_identities=("v2:release-probe:step0",),
    )
    envelope = ExecutionEnvelope(
        envelope_version=1,
        job_id="release-probe",
        attempt=1,
        submission_id=expected_submission_id("release-probe", 1),
        release_id="role-acceptance",
        owner="release-probe",
        fencing_token="release-probe-fence",
        plan_digest=plan.identity.plan_digest,
        plan_identity=plan.identity.plan_identity,
        step_identities=plan.identity.step_identities,
        entrypoint="worker.bootstrap",
    )
    work = root / "worker-task"
    write_frozen_attempt(work, AttemptFreezeSpec("singlepoint", envelope, plan))
    frozen = frozen_group_dir(work)
    receipt = work / "execution_receipt.json"
    args = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        str(frozen / "envelope.json"),
        "--plan",
        str(frozen / "plan.json"),
        "--work-dir",
        str(work),
        "--require-frozen-group",
        "--attempt-lock",
        "--probe-executor",
        "ok",
    ]
    subprocess.run(args, check=True, capture_output=True, text=True)
    document = json.loads(receipt.read_text())
    assert document["state"] == "completed"
    assert document["submission_id"] == envelope.submission_id
    assert not list(root.rglob("*.db"))
    before = receipt.read_bytes()
    # Persistent dead claim without a proven boundary must refuse replay.
    refused = subprocess.run(args, capture_output=True, text=True)
    assert refused.returncode != 0
    assert receipt.read_bytes() == before
    return {"frozen_worker_probe": "PASS", "unproven_replay_refused": "PASS", "jobs_db": False}


def web_probe(root: Path) -> dict:
    import acp_web_assets

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = root / "web.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "acp.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(base, timeout=2) as response:
                        html = response.read()
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(log.read_text()) from None
                    time.sleep(0.1)
            assets = Path(acp_web_assets.__file__).parent
            assert html == (assets / "ACP_Workbench_v2.html").read_bytes()
            relative = next(p.relative_to(assets).as_posix() for p in (assets / "js").rglob("*.js"))
            with urllib.request.urlopen(base + "/" + relative, timeout=5) as response:
                assert response.read() == (assets / relative).read_bytes()
                assert response.headers["Cache-Control"] == "no-cache"
                etag = response.headers["ETag"]
            request = urllib.request.Request(base + "/" + relative, headers={"If-None-Match": etag})
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError("unchanged warm asset must return 304")
            except urllib.error.HTTPError as exc:
                assert exc.code == 304
        finally:
            process.terminate()
            process.wait(timeout=20)
    return {"installed_web_http": "PASS", "packaged_html_js": "PASS", "warm_etag": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("cccp", "worker", "web"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    forbidden = {
        "cccp": ("acp", "acp_services", "fastapi"),
        "worker": ("acp", "fastapi"),
        "web": (),
    }
    for module in forbidden[args.role]:
        assert importlib.util.find_spec(module) is None, f"unexpected installed package: {module}"
    with tempfile.TemporaryDirectory(prefix="acp-role-probe-") as directory:
        root = Path(directory)
        os.environ["ACP_RUN_ROOT"] = str(root / "run-root")
        facts = science_probe()
        if args.role == "worker":
            facts.update(worker_probe(root))
        elif args.role == "web":
            facts.update(web_probe(root))
    facts.update(role=args.role, python=sys.executable, forbidden_absent=list(forbidden[args.role]))
    args.output.write_text(json.dumps(facts, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
