#!/usr/bin/env python3.11
"""Validate the ACP 2.0 Plan B contract manifest and dependency graph.

Stdlib only. Exit 0 = valid; exit 1 = invalid, with the first named reason on
stderr. This is a structural/reference validator, not a JSON-Schema engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA = "acp2.contracts.manifest.v1"
DEPENDENCY_SCHEMA = "acp2.dependencies.v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
READINESS = {"contract_ready", "implementation_ready", "integration_ready", "verified"}
DELIVERY_STATUS = {"planned", "blocked"}
RESULT = {"PASS", "FAIL", "NOT_VERIFIED"}
SCOPE = {"IN_SCOPE", "DEFERRED", "NOT_APPLICABLE"}
EVIDENCE_KIND = {"fixture", "live", "aggregate"}
PHASE = {
    "main",
    "approval",
    "budget",
    "da-preflight",
    "da-submit",
    "da-evidence",
    "fixture",
    "live",
    "aggregate",
}
B00_DELIVERED_IDS = {
    "manifest",
    "baseline-lock",
    "lint-baseline",
    "dependencies",
    "final-debug-patch",
}
EXPECTED_TODOS = {
    "B00",
    "B01",
    "B02",
    "B03",
    "B04a",
    "B05",
    "B06",
    "B07",
    "B08",
    "B09",
    "B10",
    "B11",
    "B12",
    "B13",
    "B14a",
    "B14b",
    "B15",
    "B16",
    "B17",
    "B18",
    "B19",
    "B20a",
    "B20b",
    "B20c",
    "B21",
    "B22",
    "B23",
    "B24a",
    "B24b",
    "B25a",
    "B25b",
    "B26a",
    "B26b",
}
EXPECTED_SUBNODES = {
    "B02a",
    "B02b",
    "B10a",
    "B10b",
    "B19a",
    "B19b",
    "B20a-ready",
    "B20a-exec",
    "B23a",
    "B23b",
}


class InvalidContractError(Exception):
    pass


def fail(reason: str) -> None:
    raise InvalidContractError(reason)


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        fail(f"{label} missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        fail(f"{label} must be a JSON object")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(data: dict[str, Any], key: str, label: str) -> Any:
    if key not in data or data[key] is None:
        fail(f"{label} missing")
    return data[key]


def validate_manifest(root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != SCHEMA:
        fail(f"manifest schema must be {SCHEMA}")

    baseline = require(manifest, "baseline", "baseline block")
    delivery = require(baseline, "delivery_revision", "baseline.delivery_revision")
    code_candidate = require(
        baseline, "code_candidate_revision", "baseline.code_candidate_revision"
    )
    if not isinstance(delivery, str) or not HEX40.match(delivery):
        fail("baseline.delivery_revision must be a full 40-hex SHA")
    if not isinstance(code_candidate, str) or not HEX40.match(code_candidate):
        fail("baseline.code_candidate_revision must be a full 40-hex SHA")
    require(baseline, "final_debug_candidate", "baseline.final_debug_candidate")
    require(baseline, "pending_fix_bundle", "baseline.pending_fix_bundle")
    report_sha = require(baseline, "report_sha256", "baseline.report_sha256")
    if not isinstance(report_sha, str) or not HEX64.match(report_sha):
        fail("baseline.report_sha256 must be a full 64-hex SHA-256")
    if baseline.get("dirty_tree_is_baseline") is not False:
        fail("baseline.dirty_tree_is_baseline must be false")

    historical = require(manifest, "historical_revisions", "historical_revisions")
    for key in ("delivery_revision", "code_candidate_revision"):
        value = require(historical, key, f"historical_revisions.{key}")
        if not isinstance(value, str) or not HEX40.match(value):
            fail(f"historical_revisions.{key} must be a full 40-hex SHA")

    contracts = require(manifest, "contracts", "contracts registry")
    if not isinstance(contracts, list) or not contracts:
        fail("contracts registry must be a non-empty array")
    seen_ids: set[str] = set()
    for entry in contracts:
        contract_id = require(entry, "id", "contracts[].id")
        if contract_id in seen_ids:
            fail(f"duplicate contract id: {contract_id}")
        seen_ids.add(contract_id)
        path_value = require(entry, "path", f"contracts[{contract_id}].path")
        status = require(entry, "status", f"contracts[{contract_id}].status")
        owner = require(entry, "owner", f"contracts[{contract_id}].owner")
        if owner not in {"A", "B", "C"}:
            fail(f"contracts[{contract_id}].owner must be A, B or C")
        if status not in {"planned", "delivered"}:
            fail(f"contracts[{contract_id}].status must be planned or delivered")
        target = root / path_value
        if status == "delivered":
            if contract_id not in B00_DELIVERED_IDS:
                fail(f"contract {contract_id} may not be delivered in B00")
            if not target.exists():
                fail(f"contracts[{contract_id}].path does not exist: {path_value}")
            digest = require(entry, "sha256", f"contracts[{contract_id}].sha256")
            if not isinstance(digest, str) or not HEX64.match(digest):
                fail(f"contracts[{contract_id}].sha256 must be a full 64-hex SHA-256")
            if contract_id != "manifest":
                digest_target = entry.get("digest_target")
                verify_path = root / digest_target if digest_target else target
                if not verify_path.is_file():
                    fail(f"contracts[{contract_id}] digest target missing: {verify_path}")
                actual = sha256_file(verify_path)
                if actual != digest:
                    fail(
                        f"contracts[{contract_id}].sha256 mismatch: "
                        f"expected {digest}, actual {actual}"
                    )


    owners = require(manifest, "owners", "owners")
    glob_seen: dict[str, str] = {}
    for lane in ("A", "B", "C"):
        lane_data = require(owners, lane, f"owners.{lane}")
        globs = require(lane_data, "globs", f"owners.{lane}.globs")
        if not isinstance(globs, list) or not globs:
            fail(f"owners.{lane}.globs must be a non-empty array")
        for glob in globs:
            if glob in glob_seen:
                fail(f"owner glob is not unique: {glob} ({glob_seen[glob]} and {lane})")
            glob_seen[glob] = lane

    shared = require(manifest, "shared_files", "shared_files")
    if not isinstance(shared, list) or not shared:
        fail("shared_files must be a non-empty array")
    shared_seen: dict[str, str] = {}
    for entry in shared:
        path_value = require(entry, "path", "shared_files[].path")
        owner = require(entry, "owner", f"shared_files[{path_value}].owner")
        if owner not in {"A", "B", "C"}:
            fail(f"shared_files[{path_value}].owner must be A, B or C")
        if path_value in shared_seen:
            fail(f"shared file has more than one owner: {path_value}")
        shared_seen[path_value] = owner

    deferred = require(manifest, "deferred_capabilities", "deferred_capabilities")
    if len(deferred) != 6:
        fail("deferred_capabilities must have exactly six rows")
    status_counts = {"IN_SCOPE": 0, "DEFERRED": 0, "C_OWNED": 0}
    for row in deferred:
        status = row.get("b_scope_status")
        if status not in status_counts:
            fail(f"deferred capability has unknown b_scope_status: {status}")
        status_counts[status] += 1
    if status_counts != {"IN_SCOPE": 4, "DEFERRED": 1, "C_OWNED": 1}:
        fail(
            "deferred capability scope counts must be 4 IN_SCOPE, 1 DEFERRED, "
            f"1 C_OWNED; got {status_counts}"
        )

    enums = require(manifest, "common_enums", "common_enums")
    if set(enums.get("phase", [])) != PHASE:
        fail("common_enums.phase does not match the frozen phase enum")
    if set(enums.get("readiness", [])) != READINESS:
        fail("common_enums.readiness does not match the frozen readiness enum")
    if enums.get("readiness_aliases") != {"deployed": "integration_ready"}:
        fail("common_enums.readiness_aliases must map deployed -> integration_ready")
    if set(enums.get("delivery_status", [])) != DELIVERY_STATUS:
        fail("common_enums.delivery_status does not match planned|blocked")
    if set(enums.get("result", [])) != RESULT:
        fail("common_enums.result does not match PASS|FAIL|NOT_VERIFIED")
    if set(enums.get("scope", [])) != SCOPE:
        fail("common_enums.scope does not match IN_SCOPE|DEFERRED|NOT_APPLICABLE")
    if set(enums.get("evidence_kind", [])) != EVIDENCE_KIND:
        fail("common_enums.evidence_kind does not match fixture|live|aggregate")

    p2 = require(manifest, "p2_entry", "p2_entry")
    transport = require(p2, "transport", "p2_entry.transport")
    if "stdio" not in str(transport) or "JSON-RPC" not in str(transport):
        fail("p2_entry.transport must freeze stdio JSON-RPC over loopback HTTP Controller")


def validate_dependencies(root: Path, manifest_path: Path) -> None:
    dep_path = manifest_path.parent / "runtime" / "dependencies.json"
    deps = load_json(dep_path, "dependencies.json")
    if deps.get("schema") != DEPENDENCY_SCHEMA:
        fail(f"dependencies schema must be {DEPENDENCY_SCHEMA}")

    nodes = deps.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        fail("dependencies.nodes must be a non-empty array")
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = require(node, "id", "dependencies.nodes[].id")
        if node_id in by_id:
            fail(f"duplicate dependency node: {node_id}")
        by_id[node_id] = node
        todo = node.get("todo")
        if not isinstance(todo, int) or not 1 <= todo <= 33:
            fail(f"dependency node {node_id} has invalid todo number")
        relation = node.get("depends_on_relation", "AND")
        if relation not in {"AND", "OR"}:
            fail(f"dependency node {node_id} has invalid depends_on_relation")
        depends_on = node.get("depends_on", [])
        if not isinstance(depends_on, list):
            fail(f"dependency node {node_id}.depends_on must be an array")
        if len(depends_on) > 1:
            if relation not in {"AND", "OR"}:
                fail(f"multi-edge dependency {node_id} lacks explicit AND/OR relation")
            if relation == "OR" and not node.get("any_of"):
                fail(f"OR dependency {node_id} must declare any_of")
        for value in depends_on:
            if isinstance(value, str) and " or " in value.lower():
                fail(f"free-text 'or' is not allowed in depends_on for {node_id}")
        artifact = require(node, "artifact", f"dependency node {node_id}.artifact")
        readiness = require(artifact, "readiness", f"{node_id}.artifact.readiness")
        if readiness not in READINESS:
            fail(f"{node_id}.artifact.readiness is invalid: {readiness}")
        delivery_status = require(
            artifact, "delivery_status", f"{node_id}.artifact.delivery_status"
        )
        if delivery_status not in DELIVERY_STATUS:
            fail(f"{node_id}.artifact.delivery_status is invalid: {delivery_status}")

    missing_todos = EXPECTED_TODOS - set(by_id)
    if missing_todos:
        fail(f"missing B todo node(s): {', '.join(sorted(missing_todos))}")
    missing_subnodes = EXPECTED_SUBNODES - set(by_id)
    if missing_subnodes:
        fail(f"missing B sub-deliverable node(s): {', '.join(sorted(missing_subnodes))}")

    external_nodes = deps.get("external_nodes", [])
    if not isinstance(external_nodes, list):
        fail("dependencies.external_nodes must be an array")
    external_by_id: dict[str, dict[str, Any]] = {}
    for node in external_nodes:
        ext_id = require(node, "id", "external_nodes[].id")
        if ext_id in external_by_id:
            fail(f"duplicate external dependency node: {ext_id}")
        external_by_id[ext_id] = node
        for field in ("artifact", "version", "digest_or_pending", "readiness"):
            require(node, field, f"external_nodes[{ext_id}].{field}")
        if node["readiness"] not in READINESS:
            fail(f"external node {ext_id} has invalid readiness")

    known = set(by_id) | set(external_by_id)
    for node in nodes:
        node_id = node["id"]
        for dep in node.get("depends_on", []):
            if dep not in known:
                fail(f"dangling dependency reference in {node_id}: {dep}")
        for dep in node.get("any_of", []):
            if dep not in known:
                fail(f"dangling any_of reference in {node_id}: {dep}")

    # Cycle detection over internal edges (external nodes are leaves).
    color: dict[str, int] = {}

    def visit(node_id: str) -> None:
        color[node_id] = 1
        for dep in by_id[node_id].get("depends_on", []):
            if dep not in by_id:
                continue
            state = color.get(dep, 0)
            if state == 1:
                fail(f"dependency cycle detected at {node_id} -> {dep}")
            if state == 0:
                visit(dep)
        color[node_id] = 2

    for node_id in sorted(by_id):
        if color.get(node_id, 0) == 0:
            visit(node_id)

    # Acceptance cannot be scheduled before a prerequisite artifact exists.
    for node in nodes:
        node_id = node["id"]
        for dep in node.get("depends_on", []):
            if dep not in by_id:
                continue  # external A/C handoffs are exempt
            if by_id[dep]["todo"] > node["todo"]:
                fail(
                    f"acceptance-before-prerequisite: {node_id} (todo {node['todo']}) "
                    f"depends on {dep} (todo {by_id[dep]['todo']})"
                )

    edges = deps.get("external_edges", [])
    if not isinstance(edges, list):
        fail("dependencies.external_edges must be an array")
    for edge in edges:
        consumer = require(edge, "consumer", "external_edges[].consumer")
        ext_id = require(edge, "external_id", "external_edges[].external_id")
        if consumer not in by_id:
            fail(f"external edge consumer does not exist: {consumer}")
        if ext_id not in external_by_id:
            fail(f"external edge references unknown external node: {ext_id}")
        for field in ("artifact", "version", "digest_or_pending", "readiness"):
            require(edge, field, f"external edge {consumer}->{ext_id}.{field}")
        if edge["readiness"] not in READINESS:
            fail(f"external edge {consumer}->{ext_id} has invalid readiness")
        if edge.get("relation") not in {"AND", "OR"}:
            fail(f"external edge {consumer}->{ext_id} lacks explicit AND/OR relation")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    default_manifest = root / "docs/reference/next-change/acp2-contracts/manifest.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=default_manifest)
    args = parser.parse_args(argv)

    try:
        manifest = load_json(args.manifest, "manifest.json")
        validate_manifest(root, manifest)
        validate_dependencies(root, args.manifest.resolve())
    except InvalidContractError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
