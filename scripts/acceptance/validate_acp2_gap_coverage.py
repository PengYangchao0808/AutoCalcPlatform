#!/usr/bin/env python3.11
"""Validate the ACP 2.0 release scope, evidence coverage and BOM schema stack.

This is the machine gate for M0-01's ledger: the plan text is the authority, so
every registered row is re-derived from ``EXECUTION_PLAN.md`` instead of trusted:

* the 39 ``### Mx-yy`` tasks, their sizes and their declared ``a/b/c/d``
  sub-checkpoints must match the coverage ledger exactly;
* G01–G26 must exist with the section 10 H mapping and closing-task list, and a
  gap may never claim PASS while any of its closing tasks is open;
* the section 0.2 scope table must be reproduced one-to-one by
  ``release/scope.json``, DEFERRED rows may never claim PASS;
* the H0–H7 rows must match the H7 handoff prerequisites and the recorded
  handoff/execution-index digests;
* the three release contracts must be registered as ``status=planned`` owned by
  B, and every file under ``release/`` must appear in the manifest (a missing
  ``scope.json`` registration is exactly what this check exists for).

The coverage file identifies itself with a canonical-bytes ``coverage_sha256``;
``--print-digest`` prints the digest a hand edit would have to write back.

JSON-Schema handling is a deliberately small structural subset (``type``,
``const``, ``enum``, ``pattern``, ``minLength``, ``minimum``, ``required``,
``properties``, ``additionalProperties``, ``items``, ``minItems``, ``$ref`` to
``#/$defs/*``); it is not a general-purpose evaluator.

Exit 0 = valid, exit 1 = invalid with the first named reason on stderr.
Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_RELATIVE = "docs/reference/next-change/acp2-contracts"
PLAN_RELATIVE = "reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md"
GRAPH_RELATIVE = "reports/acp2_delivery_gap_audit_20261026/plan-graph.json"
COVERAGE_RELATIVE = f"{CONTRACTS_RELATIVE}/release/evidence-coverage.current.json"
SCOPE_RELATIVE = f"{CONTRACTS_RELATIVE}/release/scope.json"
SCHEMA_RELATIVE = f"{CONTRACTS_RELATIVE}/release/evidence-coverage.schema.json"
BOM_SCHEMA_RELATIVE = f"{CONTRACTS_RELATIVE}/release/bom.schema.json"
MANIFEST_RELATIVE = f"{CONTRACTS_RELATIVE}/manifest.json"
H_INDEX_RELATIVE = "docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md"
H7_RELATIVE = f"{CONTRACTS_RELATIVE}/science/handoff-H7.json"

COVERAGE_SCHEMA_ID = "acp2.release.evidence-coverage.v1"
SCOPE_SCHEMA_ID = "acp2.release.scope.v1"
BOM_SCHEMA_ID = "acp2.release.bom.v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TASK_ID = re.compile(r"^M[0-5]-[0-9]{2}$")
H_ID = re.compile(r"^H[0-7]$")
EVIDENCE_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
EVIDENCE_KINDS = ("fixture", "live", "aggregate")
#: A gap's required kind is the strongest layer its closing tasks need.
EVIDENCE_KIND_PRIORITY = ("aggregate", "live", "fixture")
SCOPE_CLASSES = ("IN_SCOPE", "DEFERRED", "OUT_OF_SCOPE")
ROW_SCOPE_CLASSES = ("IN_SCOPE", "DEFERRED", "NOT_APPLICABLE")
ROW_STATUSES = ("OPEN", "CLOSED")
ROW_RESULTS = ("PASS", "FAIL", "NOT_VERIFIED")

#: Release contracts M0-01 registers as planned (owner B).  M5-02 later flips
#: them through a recorded release stage; this gate only proves they are
#: registered, not that they are delivered.
PLANNED_RELEASE_CONTRACTS = (
    "release-scope",
    "release-evidence-coverage-schema",
    "release-bom-schema",
)

#: Ledger instances are not contracts: they are the frozen snapshot a validator
#: checks, so they stay outside the manifest registry.  The allowlist is explicit
#: and verified to exist, so an unregistered contract file cannot hide here.
#: M5-01 adds the generated BOM instance next to the coverage ledger; its schema
#: is the registered ``release-bom-schema`` contract, and manifest registration
#: of the instance itself stays M5-02.
RELEASE_INSTANCE_ALLOWLIST = ("evidence-coverage.current.json", "bom.current.json")

#: Structural anchors M5-02 cannot drop without a schema version bump.
BOM_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "bom_version",
        "generated_at",
        "task_id",
        "owner",
        "source",
        "evidence",
        "artifacts",
        "toolchain",
        "scientific_software",
        "limitations",
    }
)


class CoverageError(Exception):
    """Raised with a single machine-readable reason."""


def fail(reason: str) -> None:
    raise CoverageError(reason)


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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(document: dict[str, Any]) -> str:
    stripped = json.loads(json.dumps(document))
    stripped["coverage_sha256"] = ""
    payload = json.dumps(
        stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_plan_module() -> Any:
    module_path = Path(__file__).resolve().parent / "build_acp2_plan_graph.py"
    if not module_path.is_file():
        fail(f"plan graph generator missing: {module_path}")
    spec = importlib.util.spec_from_file_location("acp2_plan_graph_builder", module_path)
    if spec is None or spec.loader is None:
        fail(f"cannot load plan graph generator: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# structural JSON-Schema subset
# ---------------------------------------------------------------------------


def _json_type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _resolve_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema["$ref"]
    if not reference.startswith("#/"):
        fail(f"unsupported schema reference: {reference}")
    target: Any = root
    for part in reference[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            fail(f"unresolvable schema reference: {reference}")
        target = target[part]
    if not isinstance(target, dict):
        fail(f"schema reference does not point at an object: {reference}")
    return target


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> list[str]:
    """Return every structural violation of ``schema`` for ``instance``."""

    if "$ref" in schema:
        return validate_schema(instance, _resolve_ref(schema, root), root, path)
    errors: list[str] = []
    label = path or "<root>"
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{label}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{label}: {instance!r} is not one of {schema['enum']}")
    expected_types = schema.get("type")
    if expected_types is not None:
        candidates = [expected_types] if isinstance(expected_types, str) else list(expected_types)
        if not any(_json_type_matches(instance, candidate) for candidate in candidates):
            errors.append(f"{label}: expected type {candidates}, got {type(instance).__name__}")
            return errors
    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(instance) < minimum_length:
            errors.append(f"{label}: shorter than minLength {minimum_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            errors.append(f"{label}: {instance!r} does not match {pattern}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{label}: {instance} is below minimum {minimum}")
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{label}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(validate_schema(value, properties[name], root, f"{path}.{name}"))
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{label}: unexpected property {name!r}")
            elif isinstance(additional, dict):
                errors.extend(validate_schema(value, additional, root, f"{path}.{name}"))
    if isinstance(instance, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(instance) < minimum_items:
            errors.append(f"{label}: fewer than minItems {minimum_items}")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, items, root, f"{path}[{index}]"))
    return errors


# ---------------------------------------------------------------------------
# shared assertions
# ---------------------------------------------------------------------------


def require_unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            fail(f"{label}[].{field} must be a non-empty string")
        if value in seen:
            fail(f"{label} has a duplicate {field}: {value}")
        seen[value] = row
    return seen


def require_enum(value: Any, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        fail(f"{label} must be one of {allowed}, got {value!r}")
    return value


def require_repo_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty repo-relative path")
    path = root / value
    if not path.exists():
        fail(f"{label} does not exist: {value}")
    return path


def require_digest(root: Path, value: Any, path_label: str, label: str) -> None:
    if not isinstance(value, str) or not HEX64.match(value):
        fail(f"{label} must be a full 64-hex SHA-256")
    actual = sha256_file(root / path_label)
    if actual != value:
        fail(f"{label} mismatch for {path_label}: expected {value}, actual {actual}")


# ---------------------------------------------------------------------------
# scope.json
# ---------------------------------------------------------------------------


def validate_scope(root: Path, scope: dict[str, Any], model: Any) -> dict[str, Any]:
    if scope.get("schema") != SCOPE_SCHEMA_ID:
        fail(f"scope.schema must be {SCOPE_SCHEMA_ID}")
    if scope.get("task_id") != "M0-01":
        fail("scope.task_id must be M0-01")
    if scope.get("owner") != "B":
        fail("scope.owner must be B (INT/REL are not contract owners)")
    source = scope.get("source")
    if not isinstance(source, dict):
        fail("scope.source must be an object")
    if source.get("plan") != PLAN_RELATIVE:
        fail(f"scope.source.plan must be {PLAN_RELATIVE}")
    if source.get("plan_sha256") != model.plan_sha256:
        fail("scope.source.plan_sha256 does not match the current execution plan")
    require_repo_path(root, source.get("plan_graph"), "scope.source.plan_graph")
    require_repo_path(root, source.get("h_index"), "scope.source.h_index")
    require_repo_path(root, source.get("h7_handoff"), "scope.source.h7_handoff")

    capabilities = scope.get("capabilities")
    if not isinstance(capabilities, list):
        fail("scope.capabilities must be an array")
    by_id = require_unique(capabilities, "id", "scope.capabilities")

    expected_items = model.scope_items
    matched: dict[str, dict[str, str]] = {key: {} for key in expected_items}
    for capability in capabilities:
        scope_class = require_enum(
            capability.get("class"),
            SCOPE_CLASSES,
            f"scope.capabilities[{capability.get('id')}].class",
        )
        statement = capability.get("statement")
        if not isinstance(statement, str) or not statement:
            fail(f"capability {capability.get('id')} must declare a statement")
        if statement not in expected_items[scope_class]:
            fail(
                f"capability {capability.get('id')} statement {statement!r} is not in the "
                f"section 0.2 {scope_class} row"
            )
        matched[scope_class][statement] = capability["id"]
        required = capability.get("required")
        if scope_class == "IN_SCOPE" and required is not True:
            fail(f"capability {capability['id']} is IN_SCOPE but not required")
        if scope_class != "IN_SCOPE" and required is not False:
            fail(f"capability {capability['id']} is {scope_class} but marked required")
        if not str(capability.get("reason") or "").strip():
            fail(f"capability {capability['id']} must record a reason")
        if scope_class in {"DEFERRED", "OUT_OF_SCOPE"}:
            if not str(capability.get("entry_condition") or "").strip():
                fail(f"capability {capability['id']} must record an entry_condition")
            statement_text = str(capability.get("release_statement") or "")
            if not statement_text:
                fail(f"capability {capability['id']} must record a release_statement")
            if "PASS" in statement_text and "NOT" not in statement_text:
                fail(f"capability {capability['id']} may not claim PASS while {scope_class}")
        for task_id in capability.get("tasks", []):
            if not TASK_ID.match(task_id) or task_id not in model.task_ids:
                fail(f"capability {capability['id']} references unknown task {task_id}")

    for scope_class, items in expected_items.items():
        missing = [item for item in items if item not in matched[scope_class]]
        if missing:
            fail(f"section 0.2 {scope_class} items are not covered by scope.json: {missing}")
        extra = set(matched[scope_class]) - set(items)
        if extra:
            fail(f"scope.json invents {scope_class} items: {sorted(extra)}")
        if len(matched[scope_class]) != len(items):
            fail(f"{scope_class} scope rows must be one-to-one with section 0.2")

    gap_index = scope.get("gap_index")
    if not isinstance(gap_index, dict):
        fail("scope.gap_index must be an object")
    if set(gap_index) != {f"G{index:02d}" for index in range(1, 27)}:
        fail("scope.gap_index must contain exactly G01–G26")
    gated_capabilities: set[str] = set()
    for gap_id, row in gap_index.items():
        require_enum(row.get("scope"), ROW_SCOPE_CLASSES, f"scope.gap_index[{gap_id}].scope")
        if row.get("required") is not True:
            fail(f"scope.gap_index[{gap_id}] must be required (all G01–G26 are IN_SCOPE)")
        capabilities_for_gap = row.get("capabilities")
        if not isinstance(capabilities_for_gap, list) or not capabilities_for_gap:
            fail(f"scope.gap_index[{gap_id}] must list at least one capability")
        for capability_id in capabilities_for_gap:
            if capability_id not in by_id:
                fail(f"scope.gap_index[{gap_id}] references unknown capability {capability_id}")
            if by_id[capability_id].get("class") != "IN_SCOPE":
                fail(
                    f"scope.gap_index[{gap_id}] may only be gated by IN_SCOPE rows, "
                    f"not {capability_id}"
                )
            gated_capabilities.add(capability_id)
    for capability in capabilities:
        if capability["class"] == "IN_SCOPE" and capability["id"] not in gated_capabilities:
            fail(f"IN_SCOPE capability {capability['id']} gates no G row in scope.gap_index")

    h_rows = scope.get("h_rows")
    if not isinstance(h_rows, list):
        fail("scope.h_rows must be an array")
    scope_h = require_unique(h_rows, "id", "scope.h_rows")
    if set(scope_h) != {f"H{index}" for index in range(8)}:
        fail("scope.h_rows must contain exactly H0–H7")
    for h_id, row in scope_h.items():
        if row.get("required") is not True:
            fail(f"scope.h_rows[{h_id}] must be required for the full ACP2 release")
        if not str(row.get("title") or "").strip():
            fail(f"scope.h_rows[{h_id}] must record a title")
    mapping_ref = scope.get("h_mapping_ref")
    if not isinstance(mapping_ref, dict):
        fail("scope.h_mapping_ref must point at the single machine G→H mapping")
    if not str(mapping_ref.get("rows") or "").endswith("#h_rows"):
        fail("scope.h_mapping_ref.rows must point at the coverage h_rows array")

    conditional = scope.get("conditional_tasks")
    if not isinstance(conditional, list):
        fail("scope.conditional_tasks must be an array")
    by_conditional = require_unique(conditional, "id", "scope.conditional_tasks")
    for task in model.tasks:
        if task.conditional and task.id not in by_conditional:
            fail(f"conditional task {task.id} is missing from scope.conditional_tasks")
    for task_id, row in by_conditional.items():
        if task_id not in model.task_ids:
            fail(f"scope.conditional_tasks references unknown task {task_id}")
        require_enum(
            row.get("scope"), ROW_SCOPE_CLASSES, f"scope.conditional_tasks[{task_id}].scope"
        )

    gate = scope.get("candidate_full_gate")
    if not isinstance(gate, dict):
        fail("scope.candidate_full_gate must be an object")
    in_scope_ids = sorted(
        capability["id"] for capability in capabilities if capability["class"] == "IN_SCOPE"
    )
    if sorted(gate.get("required_rows", [])) != in_scope_ids:
        fail("scope.candidate_full_gate.required_rows must be exactly the IN_SCOPE rows")
    if sorted(gate.get("deferred_rows", [])) != sorted(
        capability["id"] for capability in capabilities if capability["class"] == "DEFERRED"
    ):
        fail("scope.candidate_full_gate.deferred_rows must be exactly the DEFERRED rows")
    if sorted(gate.get("out_of_scope_rows", [])) != sorted(
        capability["id"] for capability in capabilities if capability["class"] == "OUT_OF_SCOPE"
    ):
        fail("scope.candidate_full_gate.out_of_scope_rows must be exactly the OUT_OF_SCOPE rows")
    if gate.get("deferred_counts_as_delivered") is not False:
        fail("scope.candidate_full_gate.deferred_counts_as_delivered must stay false")
    return gate


# ---------------------------------------------------------------------------
# coverage ledger
# ---------------------------------------------------------------------------


def validate_coverage_against_plan(root: Path, coverage: dict[str, Any], model: Any) -> None:
    tasks = coverage.get("tasks")
    if not isinstance(tasks, list):
        fail("coverage.tasks must be an array")
    by_task = require_unique(tasks, "id", "coverage.tasks")
    if set(by_task) != set(model.task_ids):
        fail(
            "coverage.tasks must cover exactly the plan tasks; missing "
            f"{sorted(set(model.task_ids) - set(by_task))}, extra "
            f"{sorted(set(by_task) - set(model.task_ids))}"
        )
    reverse = {task_id: [] for task_id in model.task_ids}
    for gap in model.gaps:
        for task_id in gap.tasks:
            reverse[task_id].append(gap.id)
    plan_tasks = {task.id: task for task in model.tasks}
    for task_id, row in by_task.items():
        spec = plan_tasks[task_id]
        if row.get("title") != spec.title:
            fail(f"coverage.tasks[{task_id}].title drifts from the plan")
        if row.get("owner") != spec.owner:
            fail(f"coverage.tasks[{task_id}].owner drifts from the plan")
        if row.get("size") != spec.size:
            fail(f"coverage.tasks[{task_id}].size drifts from the plan")
        expected_scope = "NOT_APPLICABLE" if spec.conditional else "IN_SCOPE"
        require_enum(row.get("scope"), ROW_SCOPE_CLASSES, f"coverage.tasks[{task_id}].scope")
        if row.get("scope") != expected_scope:
            fail(
                f"coverage.tasks[{task_id}].scope must be {expected_scope} "
                f"(conditional={spec.conditional})"
            )
        require_enum(row.get("status"), ROW_STATUSES, f"coverage.tasks[{task_id}].status")
        require_enum(row.get("result"), ROW_RESULTS, f"coverage.tasks[{task_id}].result")
        if row.get("status") == "OPEN" and row.get("result") != "NOT_VERIFIED":
            fail(f"coverage.tasks[{task_id}] is OPEN but claims {row.get('result')}")
        require_enum(
            row.get("evidence_level"), EVIDENCE_LEVELS, f"coverage.tasks[{task_id}].evidence_level"
        )
        require_enum(
            row.get("evidence_kind"), EVIDENCE_KINDS, f"coverage.tasks[{task_id}].evidence_kind"
        )
        if sorted(row.get("gaps", [])) != sorted(reverse[task_id]):
            fail(
                f"coverage.tasks[{task_id}].gaps must equal the section 10 reverse index "
                f"{sorted(reverse[task_id])}"
            )
        if list(row.get("checkpoints", [])) != list(spec.checkpoints):
            fail(
                f"coverage.tasks[{task_id}].checkpoints must equal the plan declarations "
                f"{list(spec.checkpoints)}"
            )

    declared = {checkpoint: task.id for task in model.tasks for checkpoint in task.checkpoints}
    checkpoints = coverage.get("checkpoints")
    if not isinstance(checkpoints, list):
        fail("coverage.checkpoints must be an array")
    by_checkpoint = require_unique(checkpoints, "id", "coverage.checkpoints")
    if set(by_checkpoint) != set(declared):
        fail(
            "coverage.checkpoints must cover every declared sub-checkpoint; missing "
            f"{sorted(set(declared) - set(by_checkpoint))}, extra "
            f"{sorted(set(by_checkpoint) - set(declared))}"
        )
    for checkpoint_id, row in by_checkpoint.items():
        parent = declared[checkpoint_id]
        if row.get("parent") != parent:
            fail(f"coverage.checkpoints[{checkpoint_id}].parent must be {parent}")
        if not str(row.get("owner") or "").strip():
            fail(f"coverage.checkpoints[{checkpoint_id}] must record an owner")
        if row.get("evidence_level") != by_task[parent].get("evidence_level"):
            fail(f"coverage.checkpoints[{checkpoint_id}] must inherit {parent} evidence_level")
        if row.get("evidence_kind") != by_task[parent].get("evidence_kind"):
            fail(f"coverage.checkpoints[{checkpoint_id}] must inherit {parent} evidence_kind")
        require_enum(
            row.get("status"), ROW_STATUSES, f"coverage.checkpoints[{checkpoint_id}].status"
        )
        require_enum(
            row.get("result"), ROW_RESULTS, f"coverage.checkpoints[{checkpoint_id}].result"
        )
        if row.get("status") == "OPEN" and row.get("result") != "NOT_VERIFIED":
            fail(f"coverage.checkpoints[{checkpoint_id}] is OPEN but claims {row.get('result')}")

    gaps = coverage.get("gaps")
    if not isinstance(gaps, list):
        fail("coverage.gaps must be an array")
    by_gap = require_unique(gaps, "id", "coverage.gaps")
    plan_gaps = {gap.id: gap for gap in model.gaps}
    if set(by_gap) != set(plan_gaps):
        fail("coverage.gaps must contain exactly G01–G26")
    level_order = list(EVIDENCE_LEVELS)
    for gap_id, row in by_gap.items():
        spec = plan_gaps[gap_id]
        if list(row.get("h_rows", [])) != list(spec.h_rows):
            fail(f"coverage.gaps[{gap_id}].h_rows must equal {list(spec.h_rows)}")
        if list(row.get("tasks", [])) != list(spec.tasks):
            fail(f"coverage.gaps[{gap_id}].tasks must equal the section 10 closing tasks")
        if sorted(row.get("conditional_tasks", [])) != sorted(spec.conditional_tasks):
            fail(f"coverage.gaps[{gap_id}].conditional_tasks must equal {spec.conditional_tasks}")
        require_enum(row.get("scope"), ROW_SCOPE_CLASSES, f"coverage.gaps[{gap_id}].scope")
        require_enum(row.get("status"), ROW_STATUSES, f"coverage.gaps[{gap_id}].status")
        require_enum(row.get("result"), ROW_RESULTS, f"coverage.gaps[{gap_id}].result")
        if row.get("status") == "OPEN" and row.get("result") != "NOT_VERIFIED":
            fail(f"coverage.gaps[{gap_id}] is OPEN but claims {row.get('result')}")
        if row.get("scope") == "DEFERRED" and row.get("result") == "PASS":
            fail(f"coverage.gaps[{gap_id}] is DEFERRED and may never claim PASS")
        if not str(row.get("closing_evidence") or "").strip():
            fail(f"coverage.gaps[{gap_id}] must record the minimum closing evidence")
        expected_level = max(
            (by_task[task_id]["evidence_level"] for task_id in spec.tasks), key=level_order.index
        )
        if row.get("evidence_level") != expected_level:
            fail(
                f"coverage.gaps[{gap_id}].evidence_level must be {expected_level} "
                "(the highest required level of its closing tasks)"
            )
        expected_kind = next(
            (
                kind
                for kind in EVIDENCE_KIND_PRIORITY
                if any(by_task[task_id]["evidence_kind"] == kind for task_id in spec.tasks)
            ),
            "fixture",
        )
        if row.get("evidence_kind") != expected_kind:
            fail(f"coverage.gaps[{gap_id}].evidence_kind must be {expected_kind}")

    for task_id, row in by_task.items():
        if row.get("status") == "CLOSED":
            for gap_id in row.get("gaps", []):
                if by_gap[gap_id].get("status") != "CLOSED":
                    fail(f"task {task_id} is CLOSED while gap {gap_id} is still open")

    h_rows = coverage.get("h_rows")
    if not isinstance(h_rows, list):
        fail("coverage.h_rows must be an array")
    by_h = require_unique(h_rows, "id", "coverage.h_rows")
    if set(by_h) != {f"H{index}" for index in range(8)}:
        fail("coverage.h_rows must contain exactly H0–H7")
    for h_id, row in by_h.items():
        expected_gaps = [gap.id for gap in model.gaps if h_id in gap.h_rows]
        if sorted(row.get("gaps", [])) != sorted(expected_gaps):
            fail(f"coverage.h_rows[{h_id}].gaps must equal the section 10 G→H mapping")
        if row.get("required") is not True:
            fail(f"coverage.h_rows[{h_id}] must be required for the full ACP2 release")
        require_enum(row.get("status"), ROW_STATUSES, f"coverage.h_rows[{h_id}].status")
        require_enum(row.get("result"), ROW_RESULTS, f"coverage.h_rows[{h_id}].result")
        if row.get("status") == "OPEN" and row.get("result") != "NOT_VERIFIED":
            fail(f"coverage.h_rows[{h_id}] is OPEN but claims {row.get('result')}")


def validate_h_index(root: Path, coverage: dict[str, Any]) -> None:
    index = coverage.get("h_index")
    if not isinstance(index, dict):
        fail("coverage.h_index must be an object")
    execution = index.get("execution_index")
    if not isinstance(execution, dict):
        fail("coverage.h_index.execution_index must be an object")
    if execution.get("path") != H_INDEX_RELATIVE:
        fail(f"coverage.h_index.execution_index.path must be {H_INDEX_RELATIVE}")
    require_digest(root, execution.get("sha256"), H_INDEX_RELATIVE, "execution_index.sha256")
    if execution.get("status") not in {"REFERENCED", "NOT_VERIFIED"}:
        fail("coverage.h_index.execution_index.status must be REFERENCED or NOT_VERIFIED")

    handoff_row = index.get("handoff_h7")
    if not isinstance(handoff_row, dict):
        fail("coverage.h_index.handoff_h7 must be an object")
    if handoff_row.get("path") != H7_RELATIVE:
        fail(f"coverage.h_index.handoff_h7.path must be {H7_RELATIVE}")
    require_digest(root, handoff_row.get("sha256"), H7_RELATIVE, "handoff_h7.sha256")
    handoff = load_json(root / H7_RELATIVE, "handoff-H7.json")
    for field in ("handoff_id", "handoff_version", "contract_set", "contract_set_version"):
        if handoff_row.get(field) != handoff.get(field):
            fail(f"coverage.h_index.handoff_h7.{field} drifts from handoff-H7.json")
    if handoff_row.get("release_ready") is not handoff["release_readiness"]["ready"]:
        fail("coverage.h_index.handoff_h7.release_ready drifts from handoff-H7.json")
    if handoff_row.get("handoff_digests_verified") is not True:
        fail("coverage.h_index.handoff_h7.handoff_digests_verified must be true")

    for h_id, recorded in handoff["handoff_digests"].items():
        target = root / recorded["path"]
        if not target.is_file():
            fail(f"handoff digest target missing: {recorded['path']}")
        if sha256_file(target) != recorded["sha256"]:
            fail(f"handoff digest mismatch for {h_id}: {recorded['path']}")

    states = {
        row["artifact_id"]: row["state"]
        for row in handoff["prerequisites"]
        if str(row["artifact_id"]).startswith("H")
    }
    for row in coverage["h_rows"]:
        expected = states.get(row["id"], "NOT_VERIFIED")
        if row.get("science_state") != expected:
            fail(
                f"coverage.h_rows[{row['id']}].science_state must mirror handoff-H7 "
                f"prerequisites ({expected})"
            )
    if states.get("H0") != "PASS":
        fail("handoff-H7 no longer records H0 as PASS; revisit the coverage baseline")


def validate_scope_coverage_alignment(coverage: dict[str, Any], scope: dict[str, Any]) -> None:
    """The ledger and the scope authority must agree on every gap class."""

    gap_index = scope["gap_index"]
    for row in coverage["gaps"]:
        index_row = gap_index[row["id"]]
        if row.get("scope") != index_row.get("scope"):
            fail(
                f"coverage.gaps[{row['id']}].scope ({row.get('scope')}) disagrees with "
                f"scope.gap_index ({index_row.get('scope')})"
            )
        if row.get("required", True) and index_row.get("required") is not True:
            fail(f"coverage.gaps[{row['id']}] is required but scope.gap_index says otherwise")


def validate_summary(coverage: dict[str, Any]) -> None:
    summary = coverage.get("summary")
    if not isinstance(summary, dict):
        fail("coverage.summary must be an object")
    expected = {
        "tasks_total": len(coverage["tasks"]),
        "tasks_open": sum(1 for row in coverage["tasks"] if row["status"] == "OPEN"),
        "checkpoints_total": len(coverage["checkpoints"]),
        "checkpoints_open": sum(1 for row in coverage["checkpoints"] if row["status"] == "OPEN"),
        "gaps_total": len(coverage["gaps"]),
        "gaps_open": sum(1 for row in coverage["gaps"] if row["status"] == "OPEN"),
        "h_rows_total": len(coverage["h_rows"]),
        "h_rows_open": sum(1 for row in coverage["h_rows"] if row["status"] == "OPEN"),
    }
    for field, value in expected.items():
        if summary.get(field) != value:
            fail(f"coverage.summary.{field} must be {value}, got {summary.get(field)!r}")
    open_required = expected["gaps_open"] + expected["tasks_open"] + expected["checkpoints_open"]
    if open_required and summary.get("full_release_status") != "NOT_VERIFIED":
        fail("coverage.summary.full_release_status must stay NOT_VERIFIED while rows are open")

    candidate = coverage.get("candidate_full")
    if not isinstance(candidate, dict):
        fail("coverage.candidate_full must be an object")
    if open_required:
        if candidate.get("allowed") is not False:
            fail("coverage.candidate_full.allowed must be false while required rows are open")
        if not candidate.get("blocking_rows"):
            fail("coverage.candidate_full.blocking_rows must name the open required rows")
    if not str(candidate.get("reason") or "").strip():
        fail("coverage.candidate_full.reason must explain the gate state")


def validate_source(root: Path, coverage: dict[str, Any], model: Any) -> None:
    source = coverage.get("source")
    if not isinstance(source, dict):
        fail("coverage.source must be an object")
    if source.get("plan") != PLAN_RELATIVE:
        fail(f"coverage.source.plan must be {PLAN_RELATIVE}")
    if source.get("plan_sha256") != model.plan_sha256:
        fail("coverage.source.plan_sha256 does not match the current execution plan")
    graph_path = require_repo_path(root, source.get("plan_graph"), "coverage.source.plan_graph")
    require_digest(
        root,
        source.get("plan_graph_sha256"),
        source["plan_graph"],
        "coverage.source.plan_graph_sha256",
    )
    graph = load_json(graph_path, "plan-graph.json")
    if graph.get("source_plan_sha256") != model.plan_sha256:
        fail("plan-graph.json was built from a different execution plan revision")
    require_repo_path(root, source.get("scope"), "coverage.source.scope")
    require_digest(
        root, source.get("scope_sha256"), source["scope"], "coverage.source.scope_sha256"
    )
    if source.get("h_index") != H_INDEX_RELATIVE:
        fail(f"coverage.source.h_index must be {H_INDEX_RELATIVE}")
    if source.get("h7_handoff") != H7_RELATIVE:
        fail(f"coverage.source.h7_handoff must be {H7_RELATIVE}")
    require_digest(
        root, source.get("h7_handoff_sha256"), H7_RELATIVE, "coverage.source.h7_handoff_sha256"
    )
    recorded = coverage.get("coverage_sha256")
    if not isinstance(recorded, str) or not HEX64.match(recorded):
        fail("coverage.coverage_sha256 must be a full 64-hex SHA-256")
    actual = canonical_digest(coverage)
    if recorded != actual:
        fail(
            "coverage.coverage_sha256 does not match the canonical file bytes: "
            f"expected {recorded}, actual {actual} (recompute with --print-digest)"
        )


# ---------------------------------------------------------------------------
# release contract registration
# ---------------------------------------------------------------------------


def validate_release_registration(root: Path, manifest: dict[str, Any], release_dir: Path) -> None:
    contracts = manifest.get("contracts")
    if not isinstance(contracts, list):
        fail("manifest.contracts must be an array")
    by_id = {entry.get("id"): entry for entry in contracts}
    for contract_id in PLANNED_RELEASE_CONTRACTS:
        entry = by_id.get(contract_id)
        if entry is None:
            fail(f"release contract {contract_id} is not registered in the manifest")
        if entry.get("status") != "planned":
            fail(f"release contract {contract_id} must stay status=planned until M5-02")
        if entry.get("owner") != "B":
            fail(f"release contract {contract_id} must be owned by B")
        if "sha256" in entry:
            fail(f"planned release contract {contract_id} must not carry a delivered sha256")
        path = require_repo_path(root, entry.get("path"), f"contracts[{contract_id}].path")
        if not path.is_file():
            fail(f"release contract {contract_id} must point at a file: {entry['path']}")

    registered = set()
    for entry in contracts:
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            continue
        target = root / path_value
        if not target.exists():
            fail(f"manifest contract {entry.get('id')} points at a missing path: {path_value}")
        if target.resolve().is_relative_to(release_dir.resolve()):
            registered.add(target.resolve())
    allowlisted = {release_dir / name for name in RELEASE_INSTANCE_ALLOWLIST}
    for path in allowlisted:
        if not path.is_file():
            fail(f"release instance allowlist entry is missing: {path.name}")
    for path in sorted(release_dir.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in registered or resolved in {item.resolve() for item in allowlisted}:
            continue
        fail(
            f"release file is neither registered in the contract manifest nor an "
            f"allowlisted ledger instance: {path.relative_to(root).as_posix()}"
        )


def validate_bom_schema(schema: dict[str, Any]) -> None:
    if schema.get("$id") != BOM_SCHEMA_ID:
        fail(f"bom.schema $id must be {BOM_SCHEMA_ID}")
    if schema.get("type") != "object":
        fail("bom.schema must describe an object")
    required = schema.get("required")
    if not isinstance(required, list):
        fail("bom.schema.required must be an array")
    missing = BOM_REQUIRED_FIELDS - set(required)
    if missing:
        fail(f"bom.schema drops M5-02 required fields: {sorted(missing)}")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        fail("bom.schema.$defs must be an object")
    for name in ("artifact", "source", "evidence", "toolchain"):
        if name not in definitions:
            fail(f"bom.schema.$defs is missing {name}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(root: Path) -> str:
    plan_module = load_plan_module()
    try:
        model = plan_module.load_model(root / PLAN_RELATIVE)
    except plan_module.PlanParseError as exc:
        fail(f"cannot parse the execution plan: {exc}")

    coverage = load_json(root / COVERAGE_RELATIVE, "evidence-coverage.current.json")
    scope = load_json(root / SCOPE_RELATIVE, "release/scope.json")
    coverage_schema = load_json(root / SCHEMA_RELATIVE, "evidence-coverage.schema.json")
    bom_schema = load_json(root / BOM_SCHEMA_RELATIVE, "release/bom.schema.json")
    manifest = load_json(root / MANIFEST_RELATIVE, "manifest.json")

    if coverage_schema.get("$id") != COVERAGE_SCHEMA_ID:
        fail(f"evidence-coverage.schema.json $id must be {COVERAGE_SCHEMA_ID}")
    schema_errors = validate_schema(coverage, coverage_schema, coverage_schema, "")
    if schema_errors:
        fail(f"coverage violates its schema: {schema_errors[0]}")

    validate_bom_schema(bom_schema)
    validate_source(root, coverage, model)
    gate = validate_scope(root, scope, model)
    validate_coverage_against_plan(root, coverage, model)
    validate_scope_coverage_alignment(coverage, scope)
    validate_h_index(root, coverage)
    validate_summary(coverage)
    validate_release_registration(root, manifest, root / CONTRACTS_RELATIVE / "release")
    if gate.get("allowed") is not False and coverage["candidate_full"]["allowed"] is False:
        fail("scope.candidate_full_gate.allowed disagrees with the coverage ledger")
    summary = coverage["summary"]
    return (
        "VALID: {tasks} tasks / {checkpoints} checkpoints / {gaps} gaps / {h} H rows "
        "({open_gaps} gaps open, full release {status}) | coverage_sha256 {digest}".format(
            tasks=summary["tasks_total"],
            checkpoints=summary["checkpoints_total"],
            gaps=summary["gaps_total"],
            h=summary["h_rows_total"],
            open_gaps=summary["gaps_open"],
            status=summary["full_release_status"],
            digest=coverage["coverage_sha256"][:12],
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--print-digest",
        action="store_true",
        help="print the canonical coverage_sha256 for the current ledger and exit",
    )
    args = parser.parse_args(argv)
    try:
        if args.print_digest:
            coverage = load_json(args.root / COVERAGE_RELATIVE, "evidence-coverage.current.json")
            print(canonical_digest(coverage))
            return 0
        print(run(args.root.resolve()))
    except CoverageError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
