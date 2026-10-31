"""``research-loop-live`` — single Supervisor research loop (T-29 / C20a).

Two subcases are frozen for this row:

* ``--subcase control``: the row is a **live** row (``evidence_kind=live``) but
  the live delivery is not implemented yet.  The body probes the real callable
  ports a live Supervisor loop needs (Controller research read/write, approval
  write, budget reservation, model gateway, live loop runner) and reports
  ``NOT_VERIFIED(implementation_missing)`` with the executable probe list of the
  ports that are missing.  It never hardcodes the delivery booleans
  (model delivered / controller delivered = False) as the final answer and
  never claims PASS.
  The offline Supervisor proof is still run when node/pnpm exist, but it is a
  *diagnostic* artifact labelled ``content_kind=offline_diagnostic``: it is not
  the row's evidence and it can never turn the live row into a live claim.
* ``--subcase search``: A-req-04 (candidate-search contract) and B-req-15
  (execution proof) are not ``integration_ready``, so the row is
  **NOT_VERIFIED(search_capability)** (exit 2).  No search PASS is claimed.

Missing environment is reported as the matching ``missing_prerequisite`` /
``missing_prerequisite:node|pnpm`` reason; a missing live implementation wins
over an environment gap, so an absent toolchain can never masquerade as an
unimplemented port (or the reverse).  The body never starts a model, DSH,
Controller, QC, LSF or browser process.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
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
    publish_observations,
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

# Every callable port a live Supervisor loop must have before this row can
# leave NOT_VERIFIED(implementation_missing).  The probe is deliberately static:
# it reads the anchor from the source and never executes the port, so a source
# probe cannot start a model, DSH, Controller or QC process.
LIVE_PORTS: tuple[dict[str, str], ...] = (
    {
        "name": "supervisor_skill",
        "path": "rph/packages/dsh-bundle/skills/supervisor.ts",
        "anchor": "export async function runSupervisor(",
    },
    {
        "name": "controller_research_read",
        "path": "src/acp/api/v2_research.py",
        "anchor": '@router.get("/research/records/{research_id}")',
    },
    {
        "name": "controller_research_round_write",
        "path": "src/acp/api/v2_research.py",
        "anchor": "@router.post(",
    },
    {
        "name": "controller_approval_write",
        "path": "src/acp/api/v2_approvals.py",
        "anchor": '@router.post("/research/approvals"',
    },
    {
        "name": "budget_reservation_write",
        "path": "src/acp/api/v2_budgets.py",
        "anchor": '@router.post("/research/budgets"',
    },
    {
        "name": "model_gateway_proxy",
        "path": "scripts/acceptance/acp2_experience_budget_proxy.py",
        "anchor": "def main(",
    },
    {
        "name": "live_loop_runner",
        "path": "rph/scripts/research-loop-live.mjs",
        "anchor": "supervisor",
    },
)


def _proof_root(out: Path, run_id: str) -> Path:
    evidence = _env_path("ACP2_EVIDENCE")
    if evidence is not None:
        return evidence / "C" / "research-loop-live" / "offline" / run_id
    return out / "offline" / run_id


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _probe_live_ports(source: Path) -> list[dict[str, Any]]:
    """Statically probe each declared live port; never executes it."""

    ports: list[dict[str, Any]] = []
    for port in LIVE_PORTS:
        path = source / port["path"]
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        present = bool(text) and port["anchor"] in text
        ports.append(
            {
                "name": port["name"],
                "path": port["path"],
                "anchor": port["anchor"],
                "present": present,
                "probe": f"grep -Fn -- {shlex.quote(port['anchor'])} {port['path']}",
            }
        )
    return ports


def _control(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    workspace = source / "rph"
    if not (workspace / "package.json").is_file():
        return _not_verified(context, f"missing_prerequisite:rph_workspace_absent:{workspace}")
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

    out = _out_dir(context)
    run_id = str(context.run_id)
    timeout = max(10.0, float(context.timeout))

    ports = _probe_live_ports(source)
    missing_ports = [port["name"] for port in ports if not port["present"]]
    _assertion(
        context,
        "research_loop.live_port_probe_executed",
        len(ports) == len(LIVE_PORTS) and bool(ports),
        ",".join(f"{port['name']}={'present' if port['present'] else 'absent'}" for port in ports),
    )

    diagnostic: dict[str, Any] = {
        "content_kind": "offline_diagnostic",
        "status": "NOT_VERIFIED",
        "reason": None,
        "all_passed": None,
        "live_model_calls": 0,
        "cases": None,
    }
    summary: dict[str, Any] = {}
    proof_assertions: list[dict[str, Any]] = []
    proof_rc: int | None = None

    pnpm = _pnpm()
    node = _node()
    evidence_root = _proof_root(out, run_id)
    if pnpm is None:
        diagnostic["reason"] = "missing_prerequisite:pnpm"
    elif node is None:
        diagnostic["reason"] = "missing_prerequisite:node"
    else:
        evidence_root.mkdir(parents=True, exist_ok=True)
        env = _tool_env(node, pnpm)
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
            proof_rc = build.returncode
        else:
            proof = _run(
                [
                    node,
                    str(source / PROOF_SCRIPT),
                    "--out",
                    str(evidence_root),
                    "--run-id",
                    run_id,
                ],
                cwd=source,
                timeout=timeout,
                env=env,
            )
            proof_rc = proof.returncode
            _assertion(
                context,
                "research_loop.offline_proof_process",
                proof.returncode == 0,
                f"rc={proof.returncode} {(proof.stderr or proof.stdout).strip()[-800:]}",
            )
            summary_path = evidence_root / "summary.json"
            assertions_path = evidence_root / "assertion-table.json"
            run_log_path = evidence_root / "run-log.json"
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

    diagnostic.update(
        {
            "status": "PASS" if proof_rc == 0 else "NOT_VERIFIED",
            "all_passed": summary.get("all_passed"),
            "live_model_calls": int(summary.get("live_model_calls") or 0),
            "cases": len(summary.get("cases") or []) if summary else None,
        }
    )

    if missing_ports:
        reason = "implementation_missing:" + ",".join(missing_ports)
    elif proof_rc is None:
        # Every live port exists; only the environment (node/pnpm) is missing.
        reason = diagnostic["reason"] or "missing_prerequisite:node|pnpm"
    else:
        reason = "implementation_missing:live_runner_invocation_unwired"

    report = {
        "schema": "acp2.experience.research-loop.control.v1",
        "content_kind": "live_port_probe",
        "run_id": run_id,
        "live": {
            "ports": ports,
            "missing_ports": missing_ports,
            "executable_probes": [port["probe"] for port in ports if not port["present"]],
            "result": (
                f"NOT_VERIFIED({reason})" if proof_rc in (None, 0) else "FAIL(offline_diagnostic)"
            ),
        },
        "diagnostic": diagnostic,
    }
    _write_json(out, "control-report.json", report)
    _add_artifact(context, "control-report.json")
    context.extensions["experience.live_ports"] = {
        "ports": [port["name"] for port in ports],
        "missing_ports": missing_ports,
        "executable_probes": [port["probe"] for port in ports if not port["present"]],
    }
    context.extensions["experience.diagnostic"] = {
        "content_kind": "offline_diagnostic",
        "status": diagnostic["status"],
        "live_model_calls": diagnostic["live_model_calls"],
        "all_passed": diagnostic["all_passed"],
    }
    publish_observations(
        context,
        {
            "transport": "absent",
            "controller": "absent",
            "model": "absent",
            "qc": "absent",
            "lsf": "absent",
            "browser": "absent",
            "artifact": "absent",
            "denial": "absent",
        },
        model_calls=diagnostic["live_model_calls"],
        model_evidence=(
            "no live Controller/model port is wired for this row; only the "
            "offline diagnostic process ran and it makes 0 model calls"
        ),
    )

    if proof_rc != 0 and proof_rc is not None:
        return _finish(context, "research_loop_offline_proof_failed")
    return _not_verified(context, reason)


def _search(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    out = _out_dir(context)
    contracts = []
    for relative in (SEARCH_A_CONTRACT, SEARCH_B_CONTRACT):
        path = source / relative
        if not path.is_file():
            return _not_verified(context, f"missing_prerequisite:search_contract_absent:{relative}")
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
        "content_kind": "contract_gap",
        "run_id": str(context.run_id),
        "scenario_key": "experience:research-loop-live/search",
        "result": "NOT_VERIFIED(search_capability)",
        "contracts": contracts,
        "search_capability": "NOT_VERIFIED",
    }
    _write_json(out, "search-report.json", report)
    _add_artifact(context, "search-report.json")
    publish_observations(
        context,
        {
            "transport": "absent",
            "controller": "absent",
            "model": "absent",
            "qc": "absent",
            "lsf": "absent",
            "browser": "absent",
            "artifact": "absent",
            "denial": "absent",
        },
        model_calls=0,
        model_evidence=(
            "search capability contracts are open; no model client is invoked "
            "for the search subcase (0 calls)"
        ),
    )
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
