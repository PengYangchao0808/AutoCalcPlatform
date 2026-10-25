"""``research-loop-live`` — single Supervisor research loop (T-29 / C20a).

Two subcases are frozen for this row:

* ``--subcase control``: no live model and no Controller are delivered yet, so
  the product row is **NOT_VERIFIED(upstream_not_delivered)** (exit 2), never
  PASS. The offline Supervisor proof still runs as a diagnostic: the fixture
  driven state machine must stop on no-evidence, duplicate terminal events,
  round/budget caps and preflight failure. That offline proof is written to
  ``$ACP2_EVIDENCE/C/research-loop-live/offline/<run_id>/``.
* ``--subcase search``: A-req-04 (candidate-search contract) and B-req-15
  (execution proof) are not ``integration_ready``, so the row is
  **NOT_VERIFIED(search_capability)** (exit 2). No search PASS is claimed.

Aggregate is handled by the shared harness: ``experience:control`` and
``experience:search`` are the required children. Today both children are
NOT_VERIFIED, so the parent is NOT_VERIFIED (exit 2).

The body never starts a model, DSH, Controller, QC or browser process. Missing
Node/pnpm/workspace is NOT_VERIFIED; an executed gate with a wrong result is
FAIL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    EXIT_FAIL,
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _pnpm,
    _run,
    _tool_env,
    _write_json,
    register,
)

SCENARIO_ID = "research-loop-live"

SKILL_FILES = (
    "rph/packages/dsh-bundle/skills/interpreter.ts",
    "rph/packages/dsh-bundle/skills/synthesizer.ts",
    "rph/packages/dsh-bundle/skills/reviewer.ts",
    "rph/packages/dsh-bundle/skills/planner.ts",
    "rph/packages/dsh-bundle/skills/supervisor.ts",
    "rph/packages/dsh-bundle/skills/schema.ts",
    "rph/packages/dsh-bundle/skills/models.ts",
    "rph/packages/dsh-bundle/skills/index.ts",
)
TEST_FILE = "rph/test/research-loop.test.ts"
PROOF_SCRIPT = "rph/scripts/research-loop-offline-proof.mjs"
SEARCH_A_CONTRACT = "docs/reference/next-change/acp2-contracts/experience/requests/A-req-04.md"
SEARCH_B_CONTRACT = "docs/reference/next-change/acp2-contracts/experience/requests/B-req-15.md"

REQUIRED_PROOF_ASSERTIONS = (
    "happy_path.stop_reason",
    "no_evidence.no_second_submit",
    "duplicate_terminal.no_extra_submit",
    "round_cap.cap_submits",
    "preflight_failed.no_submit",
    "advice_cannot_override_refuted.advice_recorded",
    "wait_never_calls_model.no_skill_during_wait",
)


def _proof_root(out: Path, run_id: str) -> Path:
    evidence = _env_path("ACP2_EVIDENCE")
    if evidence is not None:
        return evidence / "C" / "research-loop-live" / "offline" / run_id
    return out / "offline" / run_id


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _control(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    workspace = source / "rph"
    if not (workspace / "package.json").is_file():
        return _not_verified(
            context, f"missing_prerequisite:rph_workspace_absent:{workspace}"
        )
    missing = [
        relative
        for relative in (*SKILL_FILES, TEST_FILE, PROOF_SCRIPT)
        if not (source / relative).is_file()
    ]
    if missing:
        return _not_verified(
            context,
            "missing_prerequisite:research_loop_module_absent:" + ",".join(missing),
        )
    pnpm = _pnpm()
    if pnpm is None:
        return _not_verified(context, "missing_prerequisite:pnpm")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node, pnpm)
    run_id = str(context.run_id)
    proof_dir = _proof_root(out, run_id)
    proof_dir.mkdir(parents=True, exist_ok=True)

    build = _run(
        [pnpm, "--dir", str(workspace), "build"],
        cwd=source,
        timeout=timeout,
        env=env,
    )
    _assertion(
        context,
        "research_loop.build",
        build.returncode == 0,
        f"rc={build.returncode} {(build.stderr or build.stdout).strip()[-400:]}",
    )
    if build.returncode != 0:
        return _finish(context, "research_loop_build_failed")

    proof = _run(
        [
            node,
            str(source / PROOF_SCRIPT),
            "--out",
            str(proof_dir),
            "--run-id",
            run_id,
        ],
        cwd=source,
        timeout=timeout,
        env=env,
    )
    _assertion(
        context,
        "research_loop.offline_proof_process",
        proof.returncode == 0,
        f"rc={proof.returncode} {(proof.stderr or proof.stdout).strip()[-800:]}",
    )
    summary_path = proof_dir / "summary.json"
    assertions_path = proof_dir / "assertion-table.json"
    run_log_path = proof_dir / "run-log.json"
    summary: dict[str, Any] = {}
    proof_assertions: list[dict[str, Any]] = []
    if summary_path.is_file():
        summary = _load_json(summary_path)
    if assertions_path.is_file():
        proof_assertions = _load_json(assertions_path)

    for path in (summary_path, assertions_path, run_log_path):
        if path.is_file():
            _add_artifact(context, str(path.resolve()))

    _assertion(
        context,
        "research_loop.offline_proof_all_passed",
        bool(summary.get("all_passed")) and summary.get("live_model_calls") == 0,
        json.dumps(
            {
                "all_passed": summary.get("all_passed"),
                "cases": summary.get("cases"),
                "assertions": summary.get("assertions"),
                "live_model_calls": summary.get("live_model_calls"),
            },
            sort_keys=True,
        )[:800],
    )
    case_count = len(summary.get("cases") or [])
    _assertion(
        context,
        "research_loop.offline_cases_ge_7",
        case_count >= 7,
        f"cases={case_count}",
    )

    by_name = {
        item.get("name"): item.get("status")
        for item in proof_assertions
        if isinstance(item, dict)
    }
    missing_assertions = [
        name for name in REQUIRED_PROOF_ASSERTIONS if by_name.get(name) != "PASS"
    ]
    _assertion(
        context,
        "research_loop.required_offline_assertions",
        not missing_assertions,
        ",".join(missing_assertions) or "all required assertions PASS",
    )

    report = {
        "schema": "acp2.experience.research-loop.control.v1",
        "evidence_kind": "offline_fixture",
        "run_id": run_id,
        "offline_proof": summary,
        "live": {
            "model_delivered": False,
            "controller_delivered": False,
            "result": "NOT_VERIFIED(upstream_not_delivered)",
        },
        "live_model_calls": 0,
    }
    _write_json(out, "control-report.json", report)
    _add_artifact(context, "control-report.json")

    if proof.returncode != 0:
        return _finish(context, "research_loop_offline_proof_failed")
    return _not_verified(
        context,
        "upstream_not_delivered:live_model_and_controller_absent",
    )


def _search(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    out = _out_dir(context)
    contracts = []
    for relative in (SEARCH_A_CONTRACT, SEARCH_B_CONTRACT):
        path = source / relative
        if not path.is_file():
            return _not_verified(
                context, f"missing_prerequisite:search_contract_absent:{relative}"
            )
        text = path.read_text(encoding="utf-8")
        contracts.append(
            {
                "path": relative,
                "status": "open" if "- **Status:** open" in text else "not_open",
                "contains_not_verified_rule": "NOT_VERIFIED(search_capability)" in text,
            }
        )
    _assertion(
        context,
        "research_loop.search_inputs_not_integration_ready",
        all(item["status"] == "open" for item in contracts)
        and all(item["contains_not_verified_rule"] for item in contracts),
        json.dumps(contracts, sort_keys=True),
    )
    report = {
        "schema": "acp2.experience.research-loop.search.v1",
        "evidence_kind": "not_verified",
        "run_id": str(context.run_id),
        "scenario_key": "experience:research-loop-live/search",
        "result": "NOT_VERIFIED(search_capability)",
        "contracts": contracts,
        "search_capability": "NOT_VERIFIED",
    }
    _write_json(out, "search-report.json", report)
    _add_artifact(context, "search-report.json")
    return _not_verified(
        context,
        "search_capability:A-req-04_and_B-req-15_not_integration_ready",
    )


def scenario_research_loop_live(context: Any) -> int:
    subcase = getattr(context.identity, "subcase", None)
    if subcase == "control":
        return _control(context)
    if subcase == "search":
        return _search(context)
    return _not_verified(
        context,
        "subcase_required:control_or_search",
    )


register(SCENARIO_ID, scenario_research_loop_live)
