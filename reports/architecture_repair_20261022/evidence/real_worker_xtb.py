"""Real installed-worker xTB smoke; no ACP package and no probe executor."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    ScientificClosure,
    expected_submission_id,
)
from acp_services.runtime.attempt_freeze import AttemptFreezeSpec, write_frozen_attempt
from acp_services.runtime.execution_chain import frozen_group_dir
from acp_services.runtime.worker import FrozenStep
from acp_services.worker_bootstrap import freeze_frozen_plan
from cccp.calculation.requests import (
    MethodSpec,
    OptimizeOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
)

assert importlib.util.find_spec("acp") is None
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
os.environ["ACP_RUN_ROOT"] = str(root / "empty-run-root")
request = TaskRequest(
    task=TaskKind.OPTIMIZE,
    backend="xtb",
    structure=StructureInput(coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74)), symbols=("H", "H")),
    level=MethodSpec(method="GFN2-xTB"),
    options=OptimizeOptions(max_cycles=30),
    resources=TaskResources(nproc=1),
)
plan = freeze_frozen_plan(
    payload=request.to_dict(),
    steps=(FrozenStep(kind="optimize", payload=request.to_dict()),),
    item_symbols=("H", "H"),
    plan_identity="v2:real-worker-xtb",
    step_identities=("v2:real-worker-xtb:0",),
)
envelope = ExecutionEnvelope(
    envelope_version=1,
    job_id="real-worker-xtb",
    attempt=1,
    submission_id=expected_submission_id("real-worker-xtb", 1),
    release_id="repair-20261022",
    owner="acceptance-worker",
    fencing_token="acceptance-worker-fence",
    plan_digest=plan.identity.plan_digest,
    plan_identity=plan.identity.plan_identity,
    step_identities=plan.identity.step_identities,
    entrypoint="worker.bootstrap",
    scientific_closure=ScientificClosure(task_kind="optimize"),
)
write_frozen_attempt(root, AttemptFreezeSpec("optimize", envelope, plan))
group = frozen_group_dir(root)
process = subprocess.run(
    [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        str(group / "envelope.json"),
        "--plan",
        str(group / "plan.json"),
        "--work-dir",
        str(root),
        "--require-frozen-group",
        "--attempt-lock",
    ],
    capture_output=True,
    text=True,
)
(root / "worker.stdout.log").write_text(process.stdout)
(root / "worker.stderr.log").write_text(process.stderr)
assert process.returncode == 0, (process.stdout, process.stderr)
receipt = json.loads((root / "execution_receipt.json").read_text())
assert receipt["state"] == "completed", receipt
assert receipt["submission_id"] == envelope.submission_id
assert not list(root.rglob("*.db"))
task_result = json.loads((root / "WORK/00_RUNTIME/task_result.json").read_text())
for artifact in task_result["result"]["artifacts"]:
    assert Path(artifact["path"]).resolve().is_relative_to(root.resolve()), artifact
    assert Path(artifact["path"]).is_file(), artifact
assert task_result["result"]["energy_hartree"] == -0.982686139534

print(
    json.dumps(
        {
            "result": "PASS",
            "scope": "real xTB optimize in installed worker; no ACP or jobs DB",
            "receipt": receipt,
        }
    )
)
