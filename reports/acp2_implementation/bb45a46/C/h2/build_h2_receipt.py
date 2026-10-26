#!/usr/bin/env python3
"""Build the C-side H2 aggregate receipt (experience/handoffs/H2.json).

Every per-scenario row value is copied verbatim from that scenario's latest
authoritative result file under ``$ACP2_EVIDENCE/C/<scenario>/``.  Nothing in
``experience.rows`` is hand-written: the builder reads the source receipts,
derives the aggregate verdict from them, and records the receipts themselves
(plus the env-reuse captures) as artifacts with size and sha256.

The output is a ``acp2.acceptance.v1`` receipt-shaped aggregate:
``owner=C``, ``scenario=experience:h2``, ``phase=aggregate``,
``evidence_kind=aggregate``.  ``result`` is PASS only when all ten P0
scenarios are PASS; otherwise NOT_VERIFIED with the explicit gap list.
H2 total sign-off belongs to B; this file never claims it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

P0_ORDER = [
    "baseline",
    "contracts",
    "workspace",
    "harness-selftest",
    "node-dsh-install",
    "browser-install",
    "bridge-contract",
    "mcp-live",
    "dsh-discovery-live",
    "model-live",
]

RECEIPT_RE = re.compile(r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(source: str) -> str:
    result = subprocess.run(
        ["git", "-C", source, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not RECEIPT_RE.match(value):
        raise SystemExit(f"cannot resolve git HEAD of {source}: {value}")
    return value


def artifact_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def latest_receipt(scenario: str, evidence_root: Path) -> Path:
    """Newest harness-written receipt for ``scenario`` under its evidence dir.

    Only ``experience_*.json`` files directly produced by the harness qualify
    (name pattern ``experience_<scenario>-<phase>-<run_id>.json``).  Ties and
    ordering are decided by file mtime, i.e. the run the harness finished last.
    """

    scenario_dir = evidence_root / "C" / scenario
    candidates: list[tuple[float, Path]] = []
    pattern = re.compile(rf"^experience_{re.escape(scenario)}-")
    for path in scenario_dir.rglob("experience_*.json"):
        if not pattern.match(path.name):
            continue
        candidates.append((path.stat().st_mtime, path))
    if not candidates:
        raise SystemExit(f"no result file found for scenario {scenario!r}")
    candidates.sort()
    return candidates[-1][1]


def main(argv: list[str]) -> int:
    evidence_root = Path(os.environ["ACP2_EVIDENCE"])
    contracts = Path(os.environ["ACP2_CONTRACTS"])
    out_path = contracts / "experience" / "handoffs" / "H2.json"
    h2_evidence = evidence_root / "C" / "h2"

    started_at = utcnow()

    rows: dict[str, Any] = {}
    sub_results: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    receipt_artifacts: list[dict[str, Any]] = []
    gaps: list[str] = []
    all_pass = True

    for scenario in P0_ORDER:
        receipt = latest_receipt(scenario, evidence_root).resolve()
        document = json.loads(receipt.read_text(encoding="utf-8"))
        extensions = document.get("extensions") or {}

        row = {
            "result": document["result"],
            "scope": document["scope"],
            "reason": document.get("reason"),
            "command": document.get("command"),
            "cwd": document.get("cwd"),
            "exit_code": document.get("exit_code"),
            "candidate": document.get("candidate"),
            "started_at": document.get("started_at"),
            "finished_at": document.get("finished_at"),
            "artifacts": document.get("artifacts"),
            "usage": document.get("usage"),
            "interpreter": extensions.get("experience.interpreter"),
            "product_import": extensions.get("experience.product_import"),
            "harness_path": extensions.get("experience.harness_path"),
            "test_revision": extensions.get("experience.test_revision"),
            "run_id": document.get("run_id"),
            "phase": document.get("phase"),
            "receipt_path": str(receipt),
        }
        for key in (
            "result",
            "scope",
            "command",
            "cwd",
            "exit_code",
            "candidate",
            "started_at",
            "finished_at",
            "artifacts",
            "interpreter",
            "product_import",
            "harness_path",
            "test_revision",
        ):
            if row[key] is None:
                raise SystemExit(f"row {scenario}: field {key!r} missing in {receipt}")
        rows[scenario] = row

        sub_results.append(
            {
                "scenario": document.get("scenario"),
                "owner": document.get("owner"),
                "phase": document.get("phase"),
                "receipt": str(receipt),
                "result": document["result"],
                "run_id": document.get("run_id"),
            }
        )
        receipt_artifacts.append(artifact_record(receipt))
        detail = (
            f"run_id={row['run_id']} finished_at={row['finished_at']}"
            + (f" reason={row['reason']}" if row["reason"] else "")
        )
        assertions.append(
            {"name": f"h2.p0.{scenario}", "status": row["result"], "detail": detail}
        )
        if row["result"] != "PASS":
            all_pass = False
            gaps.append(f"{scenario}=NOT_VERIFIED({row['reason']})")

    # Bind the env-reuse captures into the receipt.
    env_variables = h2_evidence / "env-reuse-variables.txt"
    env_newshell = h2_evidence / "env-reuse-newshell-check.txt"
    for capture in (env_variables, env_newshell):
        if not capture.is_file():
            raise SystemExit(f"env reuse capture missing: {capture}")
        receipt_artifacts.append(artifact_record(capture))

    if all_pass:
        result, exit_code, reason = "PASS", 0, None
    else:
        result = "NOT_VERIFIED"
        exit_code = 2
        reason = (
            "h2_c_side_open: "
            + str(len(gaps))
            + " of 10 P0 scenarios not PASS: "
            + "; ".join(gaps)
            + ". H2 C side stays open until every P0 scenario is PASS;"
            " total H2 sign-off belongs to B."
        )
        assertions.append(
            {
                "name": "h2.gap_scenarios",
                "status": "NOT_VERIFIED",
                "detail": ",".join(row.split("=")[0] for row in gaps),
            }
        )

    lock_path = (
        Path(os.environ.get("ACP2_PROJECT_ROOT", "/ACPServer/AutoCalcPlatform"))
        / "docs"
        / "reference"
        / "next-change"
        / "acp2-contracts"
        / "runtime"
        / "baseline-lock.json"
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    server_revision = lock["candidate_id"]
    client_revision = git_head(os.environ["ACP2_C_SOURCE"])
    runtime_path = Path(
        os.environ.get("ACP2_PROJECT_ROOT", "/ACPServer/AutoCalcPlatform")
    ) / "scripts" / "acceptance" / "acp2_runtime.py"
    harness_revision = sha256_file(runtime_path)

    required_phases = sorted({row["phase"] for row in rows.values()})

    document = {
        "schema": "acp2.acceptance.v1",
        "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
        "contract_version": "1.0.0",
        "owner": "C",
        "scenario": "experience:h2",
        "phase": "aggregate",
        "run_id": f"h2-c-closeout-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}",
        "candidate_revision": client_revision,
        "candidate": {
            "server_revision": server_revision,
            "client_revision": client_revision,
            "harness_revision": harness_revision,
        },
        "command": [sys.executable, str(Path(__file__).resolve()), *argv],
        "cwd": os.environ.get("ACP2_PROJECT_ROOT", "/ACPServer/AutoCalcPlatform"),
        "started_at": started_at,
        "finished_at": utcnow(),
        "result": result,
        "scope": "IN_SCOPE",
        "reason": reason,
        "exit_code": exit_code,
        "assertions": assertions,
        "artifacts": receipt_artifacts,
        "evidence_kind": "aggregate",
        "job_id": None,
        "operation_id": None,
        "attempt": None,
        "release_id": None,
        "submission_id": None,
        "manifest_generation": None,
        "negotiated_protocol": None,
        "usage": None,
        "extensions": {
            "experience.scenario_key": "h2",
            "experience.run_kind": "p0-closeout-aggregate",
            "experience.sub_scenarios": list(P0_ORDER),
            "experience.gap_scenarios": [
                gap.split("=")[0] for gap in gaps
            ],
            "experience.h2_c_side_open": not all_pass,
            "experience.h2_total_signoff_owner": "B",
            "experience.env_reuse_variables": str(env_variables),
            "experience.env_reuse_newshell_check": str(env_newshell),
            "experience.rows": rows,
            "acceptance.required_child_phases": required_phases,
        },
        "sub_results": sub_results,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, out_path)

    print(f"wrote {out_path}")
    print(f"result={result} exit_code={exit_code}")
    for scenario in P0_ORDER:
        row = rows[scenario]
        print(
            f"  {scenario:<22} {row['result']:<13} exit={row['exit_code']} "
            f"{row['reason'] or ''}"
        )
    if gaps:
        print(f"gaps: {', '.join(gap.split('=')[0] for gap in gaps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
