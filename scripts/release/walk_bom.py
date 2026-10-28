#!/usr/bin/env python3.11
"""Walk the ACP 2.0 release BOM and prove every referenced byte is retrievable.

The walker deliberately starts from the BOM: it never reads a hardcoded wheel
list or a second coverage file. From ``bom.current.json`` it resolves the
coverage ledger, every artifact URI, every coverage row receipt and the B26
contract registrations, re-hashes each referenced file and prints the rows that
are still open.

Exit codes:
  0  every artifact and receipt is retrievable and the mechanical claims match
  1  a URI/receipt/digest/count/manifest check failed (the reason is named)
  2  the BOM itself is unreadable or violates ``acp2.release.bom.v1``

``--dry-run`` still verifies the coverage rows and receipts (small files) but
only checks artifact existence/size, not the artifact bytes; tests use it on the
fast layer. A sign-off must use ``--verify`` without ``--dry-run``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "release"))

import schema_validate  # noqa: E402  (scripts/release/schema_validate.py)

BOM_SCHEMA_ID = "acp2.release.bom.v1"
COVERAGE_SCHEMA_ID = "acp2.release.evidence-coverage.v1"
RELEASE_STAGE = "B26"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REUSE = {"exact", "reused", "rerun_required"}
OBSERVATIONS = {"present", "absent", "NOT_VERIFIED", "declared_absent", "NOT_APPLICABLE"}
ROW_KINDS = {"fixture", "live", "aggregate"}

#: D-INT-06: Ruff hygiene is an independent stream and never a required row.
NON_REQUIRED_CHECKPOINTS = {"M0-02c"}


class WalkError(Exception):
    """A named, machine-readable failure."""


def fail(reason: str) -> None:
    raise WalkError(reason)


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        fail(f"{label} missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        fail(f"{label} must be a JSON object")
    return payload


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coverage_canonical_digest(document: dict[str, Any]) -> str:
    stripped = json.loads(json.dumps(document))
    stripped["coverage_sha256"] = ""
    payload = json.dumps(
        stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def contract_digest(manifest: dict[str, Any]) -> str:
    lines = sorted(
        f"{entry['path']} {entry['sha256']}\n"
        for entry in manifest["contracts"]
        if entry["status"] == "delivered"
        and entry["id"] not in {"manifest", "baseline-lock", "release-bom-current"}
    )
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def manifest_self_digest(manifest: dict[str, Any]) -> str:
    stripped = json.loads(json.dumps(manifest))
    for entry in stripped["contracts"]:
        if entry["id"] == "manifest":
            entry["sha256"] = ""
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _repo_relative(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty repo-relative path")
    path = Path(value)
    if path.is_absolute():
        fail(f"{label} must be repo-relative, got {value}")
    target = root / path
    if not target.exists():
        fail(f"{label} does not exist: {value}")
    return target


def _check_artifact(
    root: Path,
    entry: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    artifact_id = entry.get("id")
    if not isinstance(artifact_id, str) or not artifact_id:
        fail("artifact id must be a non-empty string")
    if entry.get("kind") not in {
        "python-wheel",
        "python-sdist",
        "rph-package",
        "static-asset-bundle",
        "plugin-wheel",
        "science-asset",
        "contract-bundle",
        "document",
    }:
        fail(f"{artifact_id}: unsupported artifact kind {entry.get('kind')!r}")
    uri = entry.get("uri")
    if not isinstance(uri, str) or not uri:
        fail(f"{artifact_id}: uri must be a non-empty string")
    path = Path(uri)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        fail(f"{artifact_id}: artifact URI is not retrievable: {uri}")
    if entry.get("retrievable") is not True:
        fail(f"{artifact_id}: retrievable must be true for a sign-off BOM")
    size = path.stat().st_size
    if entry.get("size_bytes") != size:
        fail(f"{artifact_id}: size drift (BOM {entry.get('size_bytes')} != {size})")
    if not dry_run:
        actual = sha256(path)
        if entry.get("sha256") != actual:
            fail(f"{artifact_id}: sha256 drift (BOM {entry.get('sha256')} != {actual})")
    if entry["kind"] == "contract-bundle" and not dry_run:
        with tarfile.open(path) as archive:
            names = set(archive.getnames())
        for needed in ("acp2-contracts/manifest.json", "acp2-contracts/release/bom.schema.json"):
            if needed not in names:
                fail(f"{artifact_id}: contract bundle is missing {needed}")
        if "acp2-contracts/release/bom.current.json" in names:
            fail(f"{artifact_id}: contract bundle must not embed a stale BOM instance")
    return {"id": artifact_id, "kind": entry["kind"], "bytes": size, "uri": str(path)}


def _check_coverage_rows(
    root: Path,
    coverage: dict[str, Any],
    *,
    candidate: str,
) -> tuple[dict[str, int], list[str], int]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for kind in ("tasks", "checkpoints", "gaps", "h_rows"):
        for row in coverage.get(kind) or []:
            rows.append((kind, row))
    for row in coverage.get("deferred_rows") or []:
        rows.append(("deferred_rows", row))

    receipts = 0
    open_rows: list[str] = []
    for section, row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            fail(f"{section}: row id must be a non-empty string")
        label = f"coverage.{section}[{row_id}]"
        if row.get("candidate") != candidate:
            fail(f"{label}.candidate {row.get('candidate')!r} != BOM revision {candidate}")
        evidence_kind = row.get("evidence_kind")
        if evidence_kind not in ROW_KINDS:
            fail(f"{label}.evidence_kind must be one of {sorted(ROW_KINDS)}")
        status = row.get("status")
        result = row.get("result")
        if status not in {"OPEN", "CLOSED"}:
            fail(f"{label}.status must be OPEN or CLOSED")
        if result not in {"PASS", "FAIL", "NOT_VERIFIED"}:
            fail(f"{label}.result must be PASS, FAIL or NOT_VERIFIED")
        if status == "OPEN" and result != "NOT_VERIFIED":
            fail(f"{label} is OPEN but claims {result}")
        reuse = row.get("reuse")
        if reuse not in REUSE:
            fail(f"{label}.reuse must be one of {sorted(REUSE)}")
        if reuse == "rerun_required" and result == "PASS":
            fail(f"{label} is rerun_required but claims PASS")
        globs = row.get("impact_globs")
        if not isinstance(globs, list) or not globs:
            fail(f"{label}.impact_globs must be a non-empty array")
        observations = row.get("required_observations")
        if not isinstance(observations, dict) or not observations:
            fail(f"{label}.required_observations must be a non-empty object")
        for name, value in observations.items():
            if value not in OBSERVATIONS:
                fail(f"{label}.required_observations[{name}] has invalid value {value!r}")
        if evidence_kind == "live" and not any(v == "present" for v in observations.values()):
            fail(f"{label} is live but declares no present observation")

        receipt = row.get("receipt")
        if receipt is None:
            if result == "PASS" or reuse != "rerun_required":
                fail(f"{label} has no receipt but is not a rerun_required NOT_VERIFIED row")
        else:
            target = _repo_relative(root, receipt, f"{label}.receipt")
            recorded = row.get("receipt_sha256")
            if not isinstance(recorded, str) or not HEX64.match(recorded):
                fail(f"{label}.receipt_sha256 must be a full 64-hex SHA-256")
            actual = sha256(target)
            if actual != recorded:
                fail(f"{label}.receipt_sha256 mismatch for {receipt}")
            receipts += 1

        if section == "gaps" and status == "CLOSED" and result == "PASS":
            for name, value in observations.items():
                if value in {"absent", "NOT_VERIFIED", "declared_absent"}:
                    fail(f"{label} is PASS while observation {name!r} is {value}")
            tasks = {item["id"]: item for item in coverage["tasks"]}
            conditional = set(row.get("conditional_tasks") or [])
            for task_id in row.get("tasks") or []:
                if task_id in conditional:
                    continue
                task_row = tasks[task_id]
                if task_row["status"] != "CLOSED" or task_row["result"] != "PASS":
                    fail(
                        f"{label} is PASS while closing task {task_id} is "
                        f"{task_row['status']}/{task_row['result']}"
                    )

        if section == "deferred_rows":
            if row.get("scope") != "DEFERRED":
                fail(f"{label}.scope must be DEFERRED")
            if result == "PASS":
                fail(f"{label} is DEFERRED and may never claim PASS")

        if status == "OPEN" or result != "PASS":
            open_rows.append(row_id)

    counts = {
        "tasks": len(coverage.get("tasks") or []),
        "checkpoints": len(coverage.get("checkpoints") or []),
        "gaps": len(coverage.get("gaps") or []),
        "h_rows": len(coverage.get("h_rows") or []),
        "deferred_rows": len(coverage.get("deferred_rows") or []),
    }
    return counts, open_rows, receipts


def _expected_blocking(coverage: dict[str, Any]) -> list[str]:
    return sorted(
        [row["id"] for row in coverage["gaps"] if row["status"] == "OPEN"]
        + [
            row["id"]
            for row in coverage["tasks"]
            if row["status"] == "OPEN" and row["scope"] != "NOT_APPLICABLE"
        ]
        + [row["id"] for row in coverage["h_rows"] if row["status"] == "OPEN"]
    )


def verify(
    bom_path: Path,
    *,
    root: Path = REPO,
    dry_run: bool = False,
    require_closed: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    bom_path = bom_path if bom_path.is_absolute() else root / bom_path
    bom = load_json(bom_path, "bom.current.json")
    coverage_path = bom.get("evidence", {}).get("coverage_path")
    if not isinstance(coverage_path, str) or not coverage_path:
        fail("BOM evidence.coverage_path must be a repo-relative path")
    coverage_file = _repo_relative(root, coverage_path, "BOM evidence.coverage_path")
    manifest_path = coverage_file.parent.parent / "manifest.json"
    manifest = load_json(manifest_path, "contract manifest")

    contracts = {entry.get("id"): entry for entry in manifest.get("contracts", [])}
    schema_entry = contracts.get("release-bom-schema")
    if schema_entry is None:
        fail("manifest has no release-bom-schema registration")
    schema = load_json(root / schema_entry["path"], "bom.schema.json")
    schema_errors = schema_validate.validate(bom, schema)
    if schema_errors:
        fail(f"BOM violates bom.schema.json: {schema_errors[0]}")
    if bom.get("schema") != BOM_SCHEMA_ID:
        fail(f"BOM schema must be {BOM_SCHEMA_ID}")
    if bom.get("task_id") != "M5-02":
        fail("BOM task_id must be M5-02")

    source = bom.get("source")
    if not isinstance(source, dict):
        fail("BOM source must be an object")
    revision = source.get("revision")
    if not isinstance(revision, str) or not HEX40.match(revision):
        fail("BOM source.revision must be a full 40-hex revision")
    tree = source.get("tree")
    if not isinstance(tree, str) or not HEX40.match(tree):
        fail("BOM source.tree must be a full 40-hex tree")
    if source.get("clean") is not True:
        fail("BOM is not built from a clean committed candidate")
    if not str(source.get("persistent_ref") or "").strip():
        fail("BOM source.persistent_ref must be recorded")
    if not str(source.get("persistent_tag") or "").strip():
        fail("BOM source.persistent_tag must record the proposed tag")
    # git facts are checked from the repository, not trusted from the BOM
    import subprocess

    commit = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{revision}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        fail(f"BOM source.revision is not a commit in this repository: {revision}")
    actual_tree = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}^{{tree}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if actual_tree != tree:
        fail(f"BOM source.tree does not match revision {revision}: {actual_tree}")
    for overlay_path in source.get("release_overlay") or []:
        _repo_relative(root, overlay_path, f"BOM source.release_overlay[{overlay_path}]")

    artifacts = bom.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("BOM artifacts must be a non-empty array")
    seen: set[str] = set()
    artifact_facts: list[dict[str, Any]] = []
    for entry in artifacts:
        facts = _check_artifact(root, entry, dry_run=dry_run)
        if facts["id"] in seen:
            fail(f"duplicate artifact id: {facts['id']}")
        seen.add(facts["id"])
        artifact_facts.append(facts)

    coverage = load_json(coverage_file, "evidence-coverage.current.json")
    evidence = bom.get("evidence")
    if not isinstance(evidence, dict):
        fail("BOM evidence must be an object")
    if evidence.get("coverage_sha256") != coverage.get("coverage_sha256"):
        fail("BOM coverage_sha256 does not match the ledger self digest")
    if coverage_canonical_digest(coverage) != coverage.get("coverage_sha256"):
        fail("coverage_sha256 is not the canonical ledger digest")
    if coverage.get("schema") != COVERAGE_SCHEMA_ID:
        fail(f"coverage schema must be {COVERAGE_SCHEMA_ID}")
    coverage_schema_entry = contracts.get("release-evidence-coverage-schema")
    if coverage_schema_entry is None:
        fail("manifest has no release-evidence-coverage-schema registration")
    coverage_schema = load_json(
        root / coverage_schema_entry["path"], "evidence-coverage.schema.json"
    )
    coverage_schema_errors = schema_validate.validate(coverage, coverage_schema)
    if coverage_schema_errors:
        fail(f"coverage violates its schema: {coverage_schema_errors[0]}")

    counts, open_rows, receipts = _check_coverage_rows(root, coverage, candidate=revision)
    if evidence.get("coverage_scope_rows") != counts["tasks"]:
        fail("BOM coverage_scope_rows does not match the ledger")
    if evidence.get("coverage_gap_rows") != counts["gaps"]:
        fail("BOM coverage_gap_rows does not match the ledger")
    summary = coverage.get("summary")
    if not isinstance(summary, dict):
        fail("coverage.summary must be an object")
    expected_summary = {
        "tasks_total": counts["tasks"],
        "tasks_open": sum(1 for row in coverage["tasks"] if row["status"] == "OPEN"),
        "checkpoints_total": counts["checkpoints"],
        "checkpoints_open": sum(1 for row in coverage["checkpoints"] if row["status"] == "OPEN"),
        "gaps_total": counts["gaps"],
        "gaps_open": sum(1 for row in coverage["gaps"] if row["status"] == "OPEN"),
        "h_rows_total": counts["h_rows"],
        "h_rows_open": sum(1 for row in coverage["h_rows"] if row["status"] == "OPEN"),
    }
    for field, expected in expected_summary.items():
        if summary.get(field) != expected:
            fail(f"coverage.summary.{field} must be {expected}")
    if summary.get("full_release_status") not in {"PASS", "FAIL", "NOT_VERIFIED"}:
        fail("coverage.summary.full_release_status is invalid")
    if expected_summary["gaps_open"] or expected_summary["tasks_open"]:
        if summary.get("full_release_status") == "PASS":
            fail("full_release_status cannot be PASS while required rows are open")
    gate = coverage.get("candidate_full")
    if not isinstance(gate, dict):
        fail("coverage.candidate_full must be an object")
    if expected_summary["gaps_open"] or expected_summary["tasks_open"]:
        if gate.get("allowed") is not False:
            fail("candidate_full.allowed must be false while required rows are open")
        if sorted(gate.get("blocking_rows") or []) != _expected_blocking(coverage):
            fail("candidate_full.blocking_rows does not match the open required rows")

    if manifest.get("contract_version") != evidence.get("contract_version"):
        fail("BOM contract_version does not match the manifest")
    if manifest.get("contract_digest") != evidence.get("contract_digest"):
        fail("BOM contract_digest does not match the manifest")
    if contract_digest(manifest) != manifest.get("contract_digest"):
        fail("manifest contract_digest does not match the delivered contract set")
    if manifest_self_digest(manifest) != contracts["manifest"].get("sha256"):
        fail("manifest self digest is stale")
    for contract_id in (
        "release-scope",
        "release-evidence-coverage-schema",
        "release-bom-schema",
        "release-bom-current",
        "release-evidence-coverage-current",
    ):
        entry = contracts.get(contract_id)
        if entry is None:
            fail(f"B26 contract {contract_id} is not registered")
        if entry.get("status") != "delivered" or entry.get("delivered_by") != RELEASE_STAGE:
            fail(f"B26 contract {contract_id} is not delivered by {RELEASE_STAGE}")
        target = root / entry["path"]
        if not target.is_file() or sha256(target) != entry.get("sha256"):
            fail(f"B26 contract {contract_id} digest does not match its file")
    if contracts["release-bom-current"]["sha256"] != sha256(bom_path):
        fail("manifest release-bom-current sha256 does not match bom.current.json")

    required_open = [
        row_id
        for row_id in open_rows
        if row_id not in NON_REQUIRED_CHECKPOINTS
        and row_id != "M0-13"
        and not row_id.startswith("DEF-")
    ]
    if require_closed and required_open:
        fail(f"{len(required_open)} required rows are still open: {required_open}")

    return {
        "result": "PASS",
        "bom": str(bom_path),
        "bom_sha256": sha256(bom_path),
        "revision": revision,
        "tree": tree,
        "clean": source.get("clean"),
        "release_overlay": source.get("release_overlay") or [],
        "artifacts_verified": len(artifact_facts),
        "artifact_bytes": sum(item["bytes"] for item in artifact_facts),
        "artifact_byte_check": "skipped(dry-run)" if dry_run else "sha256",
        "coverage_sha256": evidence["coverage_sha256"],
        "coverage_counts": counts,
        "receipts_verified": receipts,
        "contract_version": manifest.get("contract_version"),
        "contract_digest": manifest.get("contract_digest"),
        "open_rows": open_rows,
        "required_open_rows": required_open,
        "full_release_status": summary.get("full_release_status"),
        "summary": expected_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bom",
        type=Path,
        default=REPO / "docs/reference/next-change/acp2-contracts/release/bom.current.json",
    )
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--verify", action="store_true", help="walk and verify the BOM")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify coverage rows/receipts but only stat artifact bytes",
    )
    parser.add_argument(
        "--require-closed",
        action="store_true",
        help="fail if any required coverage row is still OPEN/NOT_VERIFIED",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.verify and not args.dry_run:
        print(
            "walk_bom: use --verify (add --dry-run for the fast structural layer)",
            file=sys.stderr,
        )
        return 2
    try:
        report = verify(
            args.bom,
            root=args.root,
            dry_run=args.dry_run,
            require_closed=args.require_closed,
        )
    except WalkError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(f"BOM: {report['bom']}")
    print(f"sha256 {report['bom_sha256']}")
    print(
        f"source: {report['revision']} tree {report['tree']} clean={report['clean']} "
        f"overlay={len(report['release_overlay'])} paths"
    )
    print(
        f"artifacts: {report['artifacts_verified']} retrievable "
        f"({report['artifact_bytes']} bytes, byte check {report['artifact_byte_check']})"
    )
    print(
        "coverage: {tasks} tasks / {checkpoints} checkpoints / {gaps} gaps / "
        "{h_rows} H rows / {deferred_rows} deferred rows".format(**report["coverage_counts"])
    )
    print(f"receipts: {report['receipts_verified']} digests verified")
    print(
        f"contracts: {report['contract_version']} digest {report['contract_digest'][:12]} "
        f"(B26 registrations 5)"
    )
    print(
        "claims: tasks_open={tasks_open} checkpoints_open={checkpoints_open} "
        "gaps_open={gaps_open} h_rows_open={h_rows_open} full={full_release_status}".format(
            full_release_status=report["full_release_status"], **report["summary"]
        )
    )
    print("OPEN ROWS: " + (", ".join(report["open_rows"]) if report["open_rows"] else "none"))
    open_note = (
        f"; {len(report['required_open_rows'])} required rows still open"
        if report["required_open_rows"]
        else ""
    )
    print(f"RESULT: PASS (evidence intact{open_note})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
