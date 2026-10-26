#!/usr/bin/env python3
"""T-03 / C01b research-contract assertions.

Reproduces the canonicalization frozen in ``experience/plan-identity.md`` and
checks the frozen Proposal / ResearchRecord field sets, the digest stability
matrix and the four negative fixture classes. The script lives in the evidence
run directory (not in the repo) so it stays reproducible without becoming a
sixth product deliverable.

Exit codes:
  0 = every requested check behaved as contracted
  1 = an intended negative fixture was rejected (standalone --only modes)
  2 = an unexpected failure (a positive fixture failed, or a negative was
      accepted)
  3 = usage error / missing input
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCIENCE_FIELDS = (
    "capability_steps",
    "initial_inputs",
    "method",
    "mapping",
    "charge",
    "multiplicity",
    "allowed_substitutions",
)
BUDGET_FIELDS = (
    "model_requests",
    "input_tokens",
    "output_tokens",
    "task_refs",
    "recipe_steps",
    "qc_invocations",
    "wall_seconds",
    "queue_seconds",
)
PROJECTION_PATHS = ("recipe_digest", "science", "budget")
FORBIDDEN_PROPOSAL_FIELDS = (
    "status",
    "state",
    "lifecycle",
    "job_id",
    "attempt",
    "queue_position",
    "running",
    "queued",
)
FORBIDDEN_RECORD_FIELDS = (
    "session_id",
    "chat_id",
    "messages",
    "transcript",
    "conversation",
    "model_role",
)
FORBIDDEN_REQUEST_APPROVER_FIELDS = (
    "approver",
    "approver_ref",
    "role",
    "tenant",
    "principal",
)


class ContractViolation(AssertionError):
    pass


def fail(message: str) -> None:
    raise ContractViolation(message)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        fail(f"{path.name}: root must be a JSON object")
    return value


# ── canonicalization (identical to plan-identity.md §1.3) ───────────────


def _drop_nulls(value):
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value


def _normalize(value):
    if value is None:
        fail("null_in_digest_input")
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        fail("non_canonical_number")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    fail(f"non_canonical_type:{type(value).__name__}")


def plan_definition_payload(proposal: dict) -> dict:
    missing = [key for key in PROJECTION_PATHS if key not in proposal]
    if missing:
        fail(f"projection_field_missing:{','.join(missing)}")
    science = proposal["science"]
    budget = proposal["budget"]
    missing_science = [key for key in SCIENCE_FIELDS if key not in science]
    missing_budget = [key for key in BUDGET_FIELDS if key not in budget]
    if missing_science:
        fail(f"projection_science_field_missing:{','.join(missing_science)}")
    if missing_budget:
        fail(f"projection_budget_field_missing:{','.join(missing_budget)}")
    projection = {
        "recipe_digest": proposal["recipe_digest"],
        "science": {key: science[key] for key in SCIENCE_FIELDS},
        "budget": {key: budget[key] for key in BUDGET_FIELDS},
    }
    return _normalize(_drop_nulls(projection))


def plan_definition_digest(proposal: dict) -> str:
    payload = plan_definition_payload(proposal)
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


# ── minimal frozen validators ───────────────────────────────────────────


def _require_keys(name: str, doc: dict, keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if key not in doc]
    if missing:
        fail(f"{name}: missing required keys {missing}")


def _assert_digest(name: str, value) -> None:
    if not isinstance(value, str) or not DIGEST_RE.match(value):
        fail(f"{name}: not a sha256:<64 hex> digest: {value!r}")


def _assert_int(name: str, value, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{name}: expected integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        fail(f"{name}: {value} < minimum {minimum}")


def validate_proposal(doc: dict) -> None:
    _require_keys(
        "proposal",
        doc,
        (
            "proposal_id",
            "project_id",
            "question_ref",
            "question_revision",
            "reaction_ref",
            "reaction_revision",
            "hypothesis",
            "recipe_digest",
            "plan_definition_digest",
            "science",
            "budget",
            "approval_ref",
            "allowed_change_scope",
            "round",
            "decision",
            "created_at",
            "human_edits",
        ),
    )
    for forbidden in FORBIDDEN_PROPOSAL_FIELDS:
        if forbidden in doc:
            fail(f"proposal: task-state field {forbidden!r} is not allowed")
    _assert_digest("proposal.recipe_digest", doc["recipe_digest"])
    _assert_digest("proposal.plan_definition_digest", doc["plan_definition_digest"])
    _assert_int("proposal.round", doc["round"], minimum=0)
    if doc["decision"] not in {
        "proposed",
        "approved",
        "rejected",
        "superseded",
        "deferred",
    }:
        fail(f"proposal.decision: unknown value {doc['decision']!r}")
    if not isinstance(doc["approval_ref"], str) and doc["approval_ref"] is not None:
        fail("proposal.approval_ref: expected string or null")
    _require_keys(
        "proposal.science",
        doc["science"],
        SCIENCE_FIELDS,
    )
    science = doc["science"]
    _assert_int("proposal.science.charge", science["charge"])
    _assert_int("proposal.science.multiplicity", science["multiplicity"], minimum=1)
    if not isinstance(science["method"], str) or not science["method"]:
        fail("proposal.science.method: expected a non-empty string")
    if not isinstance(science["mapping"], str) or not science["mapping"]:
        fail("proposal.science.mapping: expected a non-empty string")
    if not isinstance(science["capability_steps"], list) or not science["capability_steps"]:
        fail("proposal.science.capability_steps: expected a non-empty array")
    for index, step in enumerate(science["capability_steps"]):
        if not isinstance(step, dict):
            fail(f"proposal.science.capability_steps[{index}]: expected an object")
        _require_keys(
            f"proposal.science.capability_steps[{index}]",
            step,
            ("step_index", "capability", "version"),
        )
        _assert_int(
            f"proposal.science.capability_steps[{index}].step_index",
            step["step_index"],
            minimum=0,
        )
        for key in ("capability", "version"):
            if not isinstance(step[key], str) or not step[key]:
                fail(
                    f"proposal.science.capability_steps[{index}].{key}: "
                    "expected a non-empty string"
                )
    if not isinstance(science["initial_inputs"], list) or not science["initial_inputs"]:
        fail("proposal.science.initial_inputs: expected a non-empty array")
    for index, item in enumerate(science["initial_inputs"]):
        if not isinstance(item, dict):
            fail(f"proposal.science.initial_inputs[{index}]: expected an object")
        _require_keys(
            f"proposal.science.initial_inputs[{index}]",
            item,
            ("input_id", "sha256", "format"),
        )
        _assert_digest(
            f"proposal.science.initial_inputs[{index}].sha256", item["sha256"]
        )
        if not isinstance(item["format"], str) or not item["format"]:
            fail(
                f"proposal.science.initial_inputs[{index}].format: "
                "expected a non-empty string"
            )
    if not isinstance(science["allowed_substitutions"], list):
        fail("proposal.science.allowed_substitutions: expected an array")
    for index, substitution in enumerate(science["allowed_substitutions"]):
        if not isinstance(substitution, dict):
            fail(
                "proposal.science.allowed_substitutions"
                f"[{index}]: expected an object"
            )
        _require_keys(
            f"proposal.science.allowed_substitutions[{index}]",
            substitution,
            ("field_path", "allowed_values"),
        )
    _require_keys("proposal.budget", doc["budget"], BUDGET_FIELDS)
    for key in BUDGET_FIELDS:
        _assert_int(f"proposal.budget.{key}", doc["budget"][key], minimum=0)
    _require_keys(
        "proposal.allowed_change_scope",
        doc["allowed_change_scope"],
        ("mode", "finite_substitutions"),
    )
    if not isinstance(doc["human_edits"], list):
        fail("proposal.human_edits: expected array")
    computed = plan_definition_digest(doc)
    if computed != doc["plan_definition_digest"]:
        fail(
            "proposal.plan_definition_digest mismatch: "
            f"declared {doc['plan_definition_digest']} computed {computed}"
        )


def validate_record(doc: dict) -> None:
    _require_keys(
        "record",
        doc,
        (
            "research_id",
            "project_id",
            "round",
            "decision",
            "plan_definition_digest",
            "step_identity_refs",
            "task_refs",
            "evidence_refs",
            "missing_validation",
            "human_edits",
            "approval_ref",
            "created_at",
            "record_revision",
        ),
    )
    for forbidden in FORBIDDEN_RECORD_FIELDS:
        if forbidden in doc:
            fail(f"record: session/chat field {forbidden!r} is not allowed")
    _assert_int("record.round", doc["round"], minimum=0)
    _assert_int("record.record_revision", doc["record_revision"], minimum=1)
    if doc["decision"] not in {"accept", "revise", "reject", "defer", "escalate"}:
        fail(f"record.decision: unknown value {doc['decision']!r}")
    _assert_digest("record.plan_definition_digest", doc["plan_definition_digest"])
    if not isinstance(doc["step_identity_refs"], list) or not doc["step_identity_refs"]:
        fail("record.step_identity_refs: expected a non-empty array")
    for index, step in enumerate(doc["step_identity_refs"]):
        if not isinstance(step, dict):
            fail(f"record.step_identity_refs[{index}]: expected an object")
        _require_keys(
            f"record.step_identity_refs[{index}]",
            step,
            (
                "step_index",
                "step_identity",
                "implementation_version",
                "upstream_digests",
                "materialized_parameters_digest",
            ),
        )
        _assert_int(f"record.step_identity_refs[{index}].step_index", step["step_index"], 0)
        _assert_digest(
            f"record.step_identity_refs[{index}].materialized_parameters_digest",
            step["materialized_parameters_digest"],
        )
        for upstream in step["upstream_digests"]:
            _assert_digest(
                f"record.step_identity_refs[{index}].upstream_digests[]", upstream
            )
    if not isinstance(doc["task_refs"], list):
        fail("record.task_refs: expected an array of task-ref objects")
    for index, task_ref in enumerate(doc["task_refs"]):
        if isinstance(task_ref, str):
            fail(
                "record.task_refs: a bare string array is not a TaskRef list "
                f"(item {index})"
            )
        if not isinstance(task_ref, dict):
            fail(f"record.task_refs[{index}]: expected an object")
        _require_keys(
            f"record.task_refs[{index}]",
            task_ref,
            ("project_id", "job_id", "attempt", "release_id"),
        )
        _assert_int(f"record.task_refs[{index}].attempt", task_ref["attempt"], 1)
        if task_ref["project_id"] != doc["project_id"]:
            fail(
                "record.task_refs: project mismatch; "
                f"record {doc['project_id']!r} != task_ref {task_ref['project_id']!r}"
            )
    if not isinstance(doc["evidence_refs"], list):
        fail("record.evidence_refs: expected an array")
    if not isinstance(doc["missing_validation"], list):
        fail("record.missing_validation: expected an array")
    if not isinstance(doc["human_edits"], list):
        fail("record.human_edits: expected an array")


# ── B committed allowlist ───────────────────────────────────────────────


def b_allowlist(b_repo: Path, p2_path: str) -> tuple[set[str], dict]:
    blob = subprocess.run(
        ["git", "-C", str(b_repo), "show", f"HEAD:{p2_path}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    contract = json.loads(blob)
    tools: set[str] = set(contract["p1_allowlist_unchanged"]["tools"])
    for row in contract["rows"]:
        for tool in row["tools"]:
            tools.add(tool["name"])
    return tools, contract


# ── checks ──────────────────────────────────────────────────────────────


def check_schemas(schema_dir: Path, fixture_dir: Path) -> None:
    proposal_schema = load_json(schema_dir / "proposal.schema.json")
    record_schema = load_json(schema_dir / "research-record.schema.json")
    for name, schema in (
        ("proposal", proposal_schema),
        ("research-record", record_schema),
    ):
        for key in ("schema", "schema_version", "schema_dialect"):
            if key not in schema:
                fail(f"{name}.schema: missing top-level {key}")
        if schema["schema_dialect"] != "json-schema-2020-12":
            fail(f"{name}.schema: unexpected dialect {schema['schema_dialect']}")

    proposal_required = set(proposal_schema["required"])
    proposal_needed = {
        "proposal_id",
        "project_id",
        "recipe_digest",
        "plan_definition_digest",
        "budget",
        "approval_ref",
    }
    if not proposal_needed <= proposal_required:
        fail(f"proposal.schema: required is missing {proposal_needed - proposal_required}")
    record_required = set(record_schema["required"])
    record_needed = {
        "research_id",
        "project_id",
        "round",
        "decision",
        "plan_definition_digest",
        "step_identity_refs",
        "task_refs",
        "evidence_refs",
    }
    if not record_needed <= record_required:
        fail(f"research-record.schema: required is missing {record_needed - record_required}")

    for path in sorted(fixture_dir.glob("*.json")):
        load_json(path)  # JSON-parse every fixture, fragment or not
        print(f"PARSED fixture {path.name}")

    proposal = load_json(fixture_dir / "proposal.valid.json")
    validate_proposal(proposal)
    print(
        "PASS proposal.valid.json "
        f"(plan_definition_digest={proposal['plan_definition_digest']})"
    )
    record = load_json(fixture_dir / "research-record.valid.json")
    validate_record(record)
    print("PASS research-record.valid.json")


def check_digest(fixture_dir: Path) -> None:
    import copy

    base = load_json(fixture_dir / "proposal.valid.json")
    baseline = plan_definition_digest(base)
    print(f"BASELINE plan_definition_digest={baseline}")

    def mutate(path: list[object], value) -> dict:
        doc = copy.deepcopy(base)
        cursor = doc
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        return doc

    def appended_refs() -> dict:
        doc = copy.deepcopy(base)
        doc["task_refs"] = [
            {
                "project_id": doc["project_id"],
                "job_id": "20261022_999_appended",
                "attempt": 1,
                "release_id": "release-2026.10.22",
            }
        ]
        doc["evidence_refs"] = [
            {
                "evidence_id": "ev-appended",
                "kind": "result_manifest",
                "sha256": "sha256:" + "1" * 64,
            }
        ]
        return doc

    insensitive = [
        ("approval_ref", mutate(["approval_ref"], "approval/reference-002")),
        ("approval_status", mutate(["approval_status"], "approved")),
        ("display_name", mutate(["display_name"], "renamed for display only")),
        ("appended_refs", appended_refs()),
    ]
    sensitive = [
        ("method", mutate(["science", "method"], "wB97X-D4")),
        ("mapping", mutate(["science", "mapping"], "mapping-butadiene-ethene-exo-v1")),
        (
            "initial_input",
            mutate(
                ["science", "initial_inputs", 0, "sha256"], "sha256:" + "0" * 64
            ),
        ),
        ("charge", mutate(["science", "charge"], 1)),
        ("multiplicity", mutate(["science", "multiplicity"], 3)),
        ("budget.model_requests", mutate(["budget", "model_requests"], 25)),
        ("budget.wall_seconds", mutate(["budget", "wall_seconds"], 7200)),
    ]

    failures = []
    for label, doc in insensitive:
        digest = plan_definition_digest(doc)
        status = "UNCHANGED" if digest == baseline else "CHANGED"
        print(f"STABILITY {label:20s} {status:9s} {digest}")
        if digest != baseline:
            failures.append(f"insensitive change {label} altered the digest")
    for label, doc in sensitive:
        digest = plan_definition_digest(doc)
        status = "CHANGED" if digest != baseline else "UNCHANGED"
        print(f"SENSITIVITY {label:20s} {status:9s} {digest}")
        if digest == baseline:
            failures.append(f"sensitive change {label} did not alter the digest")
    if failures:
        fail("; ".join(failures))
    print(
        "PASS digest matrix: 4 insensitive unchanged, "
        "7 sensitive changed"
    )


def check_round_string(fixture_dir: Path) -> None:
    import copy

    doc = copy.deepcopy(load_json(fixture_dir / "proposal.valid.json"))
    doc["round"] = "1"
    try:
        validate_proposal(doc)
    except ContractViolation as exc:
        print(f"REJECTED round-as-string: {exc}")
        return
    fail("proposal with round='1' was wrongly accepted")


def check_taskrefs_bare(fixture_dir: Path) -> None:
    import copy

    doc = copy.deepcopy(load_json(fixture_dir / "research-record.valid.json"))
    doc["task_refs"] = ["20261022_001_PESsearch", "20261022_002_PESsearch"]
    try:
        validate_record(doc)
    except ContractViolation as exc:
        print(f"REJECTED bare-string task_refs: {exc}")
        return
    fail("record with a bare string task_refs array was wrongly accepted")


def check_wrong_project(schema_dir: Path, fixture_dir: Path) -> None:
    view_schema = load_json(schema_dir / "view-model.schema.json")
    required = view_schema["required"]

    def consistency(doc: dict) -> None:
        view_model = doc["view_model"]
        missing = [key for key in required if key not in view_model]
        if missing:
            fail(f"view_model: missing T-02 required fields {missing}")
        vm_task_ref = view_model["task_ref"]
        for key in view_schema["properties"]["task_ref"]["required"]:
            if key not in vm_task_ref:
                fail(f"view_model.task_ref: missing {key}")
        project = view_model["project_id"]
        for index, task_ref in enumerate(doc["task_refs"]):
            if task_ref["project_id"] != project:
                fail(
                    "view-model consistency: "
                    f"view_model.project_id={project!r} != "
                    f"task_refs[{index}].project_id={task_ref['project_id']!r}"
                )

    consistency(load_json(fixture_dir / "wrong-project.valid.json"))
    print("PASS wrong-project.valid.json (project scopes agree)")
    try:
        consistency(load_json(fixture_dir / "wrong-project.json"))
    except ContractViolation as exc:
        print(f"REJECTED wrong-project: {exc}")
        return
    fail("wrong-project.json was wrongly accepted")


def check_unknown_tool(fixture_dir: Path, b_repo: Path, p2_path: str) -> None:
    tools, contract = b_allowlist(b_repo, p2_path)
    print(
        "B allowlist loaded from committed "
        f"{p2_path}: {len(tools)} tools, schema={contract['schema']}"
    )
    positive = load_json(fixture_dir / "unknown-tool.valid.json")
    if positive["tool"] not in tools:
        fail(f"positive tool {positive['tool']!r} is not in the allowlist")
    print(f"PASS unknown-tool.valid.json (tool {positive['tool']} allowed)")
    negative = load_json(fixture_dir / "unknown-tool.json")
    if negative["tool"] in tools:
        fail(f"negative tool {negative['tool']!r} is in the allowlist")
    print(
        "REJECTED unknown-tool: "
        f"tool={negative['tool']!r} is not one of {len(tools)} committed tools"
    )


def check_illegal_request(fixture_dir: Path) -> None:
    def reject_payload_approver(doc: dict) -> None:
        arguments = doc.get("arguments", {})
        present = [
            key for key in FORBIDDEN_REQUEST_APPROVER_FIELDS if key in arguments
        ]
        if present:
            fail(
                "approver identity must come from the authenticated Controller "
                f"channel, not payload fields {present}"
            )

    reject_payload_approver(load_json(fixture_dir / "illegal-request.valid.json"))
    print("PASS illegal-request.valid.json (no payload approver)")
    try:
        reject_payload_approver(load_json(fixture_dir / "illegal-request.json"))
    except ContractViolation as exc:
        print(f"REJECTED illegal-request: {exc}")
        return
    fail("illegal-request.json was wrongly accepted")


def check_stale_reference(fixture_dir: Path) -> None:
    def freshness(doc: dict) -> None:
        proposal = doc["proposal"]
        record = doc["record"]
        validate_proposal(proposal)
        validate_record(record)
        if record["plan_definition_digest"] != proposal["plan_definition_digest"]:
            fail(
                "stale reference: record plan_definition_digest "
                f"{record['plan_definition_digest']} != proposal "
                f"{proposal['plan_definition_digest']}"
            )
        if record["round"] < proposal["round"]:
            fail(
                f"stale reference: record round {record['round']} "
                f"< proposal round {proposal['round']}"
            )

    freshness(load_json(fixture_dir / "stale-reference.valid.json"))
    print("PASS stale-reference.valid.json (digest and round current)")
    try:
        freshness(load_json(fixture_dir / "stale-reference.json"))
    except ContractViolation as exc:
        print(f"REJECTED stale-reference: {exc}")
        return
    fail("stale-reference.json was wrongly accepted")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-dir", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--b-repo", type=Path, default=Path("/ACPServer/AutoCalcPlatform"))
    parser.add_argument(
        "--p2-path",
        default="docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json",
    )
    parser.add_argument(
        "--only",
        choices=(
            "all",
            "schemas",
            "digest",
            "round-string",
            "taskrefs-bare",
            "wrong-project",
            "unknown-tool",
            "illegal-request",
            "stale-reference",
        ),
        default="all",
    )
    args = parser.parse_args(argv)

    try:
        if args.only == "all":
            check_schemas(args.schema_dir, args.fixtures)
            check_digest(args.fixtures)
            # Each negative check prints REJECTED and returns normally when the
            # contract rejects as intended; it raises ContractViolation only if
            # the negative was wrongly accepted.
            check_round_string(args.fixtures)
            check_taskrefs_bare(args.fixtures)
            check_wrong_project(args.schema_dir, args.fixtures)
            check_unknown_tool(args.fixtures, args.b_repo, args.p2_path)
            check_illegal_request(args.fixtures)
            check_stale_reference(args.fixtures)
            print("ALL CHECKS PASS")
            return 0

        if args.only == "schemas":
            check_schemas(args.schema_dir, args.fixtures)
            return 0
        if args.only == "digest":
            check_digest(args.fixtures)
            return 0
        if args.only == "round-string":
            check_round_string(args.fixtures)
            return 1
        if args.only == "taskrefs-bare":
            check_taskrefs_bare(args.fixtures)
            return 1
        if args.only == "wrong-project":
            check_wrong_project(args.schema_dir, args.fixtures)
            return 1
        if args.only == "unknown-tool":
            check_unknown_tool(args.fixtures, args.b_repo, args.p2_path)
            return 1
        if args.only == "illegal-request":
            check_illegal_request(args.fixtures)
            return 1
        if args.only == "stale-reference":
            check_stale_reference(args.fixtures)
            return 1
        parser.error(f"unhandled mode {args.only}")
    except ContractViolation as exc:
        print(f"UNEXPECTED FAILURE: {exc}", file=sys.stderr)
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
