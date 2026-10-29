"""C2 pre-flip rehearsal: ordinary xtb_optimize through the REAL frozen envelope.

The default-envelope set is temporarily widened *in this process only*, so the
unmodified runner freezes the attempt via the registered builder, the worker
executes the frozen plan with real xTB, and the receipt/publication/recovery
chain runs exactly as it will after the permanent flip. Evidence (events,
receipt, manifest, attempt choice) is collected for the support matrix.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE = Path("/tmp/phaseC2/run")
RUN_ROOT = BASE / "run_root"


def main() -> int:
    from acp.scheduler.jobs import JobSpec, JobStatus
    from acp.scheduler.manager import JobManager
    from acp.scheduler.runner import JobRunner
    from acp.scheduler.store import JobStore
    import acp.scheduler.runner as runner_module
    import acp_services.runtime.execution_chain as chain

    if BASE.exists():
        shutil.rmtree(BASE)
    RUN_ROOT.mkdir(parents=True)
    os.environ["ACP_RUN_ROOT"] = str(RUN_ROOT)

    # Pre-flip rehearsal: only this process sees the widened set.
    widened = frozenset(chain.DEFAULT_ENVELOPE_WORKFLOWS) | {"xtb_optimize"}
    chain.DEFAULT_ENVELOPE_WORKFLOWS = widened  # type: ignore[misc]
    runner_module.DEFAULT_ENVELOPE_WORKFLOWS = widened  # type: ignore[misc]
    import acp.scheduler.manager as manager_module
    manager_module.DEFAULT_ENVELOPE_WORKFLOWS = widened  # type: ignore[misc]

    store = JobStore(RUN_ROOT / "acp_jobs.db")
    runner = JobRunner(python_executable=sys.executable)
    manager = JobManager(RUN_ROOT, store=store, runner=runner,
                         envelope_completion=True, poll_interval=5)
    try:
        spec = JobSpec(
            workflow="xtb_optimize",
            name="c2-xtb-opt",
            input={"source_type": "xyz_text",
                   "source": "3\nwater\nO   0.0000000    0.0000000    0.1173000\n"
                             "H   0.0000000    0.7572000   -0.4692000\n"
                             "H   0.0000000   -0.7572000   -0.4692000\n"},
            method={"method": "GFN2-xTB", "charge": 0, "multiplicity": 1},
            resources={"ncores": 1, "memory_gb": 2},
        )
        record = manager.submit(spec)
        print("submitted:", record.id, flush=True)
        deadline = time.time() + 600
        terminal = None
        while time.time() < deadline:
            current = store.get(record.id)
            if current is not None and current.status.is_terminal:
                terminal = current
                break
            manager._poll_job(record.id)
            time.sleep(0.2)
        assert terminal is not None, "the job did not reach a terminal state"
        facts = {"job_id": record.id, "status": str(terminal.status),
                 "attempt": terminal.attempt, "result": terminal.result}
        work = Path(terminal.work_dir or "")
        receipt_path = work / "execution_receipt.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
        choice = chain.read_attempt_choice(work) if work.is_dir() else None
        manifest_path = work / "RESULT" / "result_manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
        events = (work / "WORK" / "00_RUNTIME" / "events.jsonl").read_text() if work.is_dir() else ""
        facts.update({
            "receipt_state": (receipt or {}).get("state"),
            "receipt_release_id": (receipt or {}).get("release_id"),
            "attempt_choice": None if choice is None else {
                "entry": choice.entry, "protocol": choice.protocol},
            "manifest_products": [p.get("kind") for p in (manifest or {}).get("products", [])],
            "events": {
                "worker_entry": "acp_services.worker_bootstrap" in events,
                "cli_entry": "acp.cli" in events,
                "process_starting": events.count('"type": "process.starting"'),
            },
        })
        (BASE / "c2-facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2))
        print(json.dumps(facts, ensure_ascii=False, indent=1)[:1200])
        assert "COMPLETED" in facts["status"], facts["status"]
        assert facts["receipt_state"] == "completed"
        assert facts["attempt_choice"] == {"entry": "envelope", "protocol": "local"}
        assert facts["events"]["worker_entry"] and not facts["events"]["cli_entry"]
        assert any(kind for kind in facts["manifest_products"]), facts["manifest_products"]
        print("C2 ENVELOPE RUN PASS")
        return 0
    finally:
        manager.shutdown()


if __name__ == "__main__":
    sys.exit(main())
