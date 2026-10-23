"""B25a worker-only receipt probe: write one receipt and prove no jobs DB exists."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from acp_services.contracts.envelope import ExecutionEnvelope, expected_submission_id
from acp_services.runtime.attempt_freeze import AttemptFreezeSpec, write_frozen_attempt
from acp_services.runtime.execution_chain import frozen_group_dir
from acp_services.runtime.worker import FrozenStep
from acp_services.worker_bootstrap import freeze_frozen_plan

with tempfile.TemporaryDirectory(prefix="b25-worker-receipt-") as directory:
    root = Path(directory)
    plan = freeze_frozen_plan(
        payload={"task": "singlepoint"},
        steps=(FrozenStep(kind="singlepoint", payload={"task": "singlepoint"}),),
        item_symbols=("He",),
        plan_identity="v2:b25-release-receipt",
        step_identities=("v2:b25-release-receipt:step0",),
    )
    envelope = ExecutionEnvelope(
        envelope_version=1,
        job_id="b25-worker-receipt",
        attempt=1,
        submission_id=expected_submission_id("b25-worker-receipt", 1),
        release_id="b25-release-candidate",
        owner="b25",
        fencing_token="b25-fence",
        plan_digest=plan.identity.plan_digest,
        plan_identity=plan.identity.plan_identity,
        step_identities=plan.identity.step_identities,
        entrypoint="worker.bootstrap",
    )
    work = root / "worker-task"
    write_frozen_attempt(work, AttemptFreezeSpec("singlepoint", envelope, plan))
    frozen = frozen_group_dir(work)
    subprocess.run(
        [
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
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads((work / "execution_receipt.json").read_text())
    assert receipt["state"] == "completed", receipt
    assert receipt["submission_id"] == envelope.submission_id, receipt
    databases = [str(path) for path in root.rglob("*.db")]
    assert databases == [], databases
    print(
        json.dumps(
            {
                "schema": "acp2.b25.worker_receipt_check.v1",
                "job_id": envelope.job_id,
                "attempt": envelope.attempt,
                "submission_id": envelope.submission_id,
                "state": receipt["state"],
                "jobs_db": False,
                "databases": databases,
                "result": "PASS",
            },
            indent=2,
            sort_keys=True,
        )
    )
