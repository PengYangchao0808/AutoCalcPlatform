"""B03 contract conformance: application API, MCP launch, science binding, samples.

Every assertion here reads the delivered contract files. The test does not
implement a runtime canonicalizer (B16 owns that); it carries a test-local
conformance checker so the frozen vectors are verifiable byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields as dataclass_fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs/reference/next-change/acp2-contracts"
RUNTIME = CONTRACTS / "runtime"
SAMPLES = RUNTIME / "samples"

REQUIRED_OPERATIONS = {
    "capabilities",
    "health",
    "preflight",
    "submit",
    "status",
    "cancel",
    "prepare",
    "read",
    "analyze",
}
ERROR_CLASSES = {
    "retryable",
    "authorization",
    "invalid-parameter",
    "conflict",
    "not-prepared",
    "unsupported",
}
MCP_TOOL_NAMES = {
    "acp_capabilities",
    "acp_preflight",
    "acp_submit",
    "acp_status",
    "acp_cancel",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_analyze",
}
DIGEST_FIELDS = {"plan_digest", "proposal_digest", "contract_digest", "artifact_digest"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def api() -> dict[str, Any]:
    return load_json(RUNTIME / "application-api.v1.json")


@pytest.fixture(scope="module")
def launch_schema() -> dict[str, Any]:
    return load_json(RUNTIME / "mcp-launch.schema.json")


@pytest.fixture(scope="module")
def launch_template() -> dict[str, Any]:
    return load_json(RUNTIME / "mcp-launch.template.json")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return load_json(CONTRACTS / "manifest.json")


@pytest.fixture(scope="module")
def dependencies() -> dict[str, Any]:
    return load_json(RUNTIME / "dependencies.json")


@pytest.fixture(scope="module")
def samples() -> dict[str, Any]:
    return load_json(SAMPLES / "manifest.json")


def sample_payloads() -> dict[str, dict[str, Any]]:
    index = load_json(SAMPLES / "manifest.json")
    return {
        entry["path"]: load_json(SAMPLES / entry["path"].removeprefix("samples/"))
        for entry in index["entries"]
    }


# ---------------------------------------------------------------------------
# application-api
# ---------------------------------------------------------------------------


def test_application_api_operations_are_frozen(api: dict[str, Any]) -> None:
    assert api["schema"] == "acp2.runtime.application-api.v1"
    assert api["schema_dialect"] == "https://json-schema.org/draft/2020-12/schema"
    operations = api["operations"]
    assert REQUIRED_OPERATIONS <= set(operations)
    for op_id, operation in operations.items():
        assert operation["id"] == op_id
        assert operation["purpose"]
        assert operation["use_case"]
        assert operation["authorization"]
        assert operation["side_effects"]
        request = operation["request"]
        assert request["additional_properties"] is False
        for required in request["required"]:
            assert required in request["fields"], f"{op_id}.{required} not declared"
        for name, spec in request["fields"].items():
            assert spec["type"], f"{op_id}.{name} has no type"
            assert spec["description"], f"{op_id}.{name} has no description"
        for error_class in operation["errors"]:
            assert error_class in ERROR_CLASSES, f"{op_id} uses {error_class}"
        response = operation["response"]
        for required in response["required"]:
            assert required in response["fields"], f"{op_id} response {required} undeclared"


def test_digest_fields_are_four_distinct_contracts(api: dict[str, Any]) -> None:
    digest_fields = api["digest_fields"]
    assert DIGEST_FIELDS <= set(digest_fields)
    definitions = {digest_fields[name]["definition"] for name in DIGEST_FIELDS}
    assert len(definitions) == len(DIGEST_FIELDS)
    plan = digest_fields["plan_digest"]
    assert "authorization value" in plan["authorization_semantics"]
    assert plan["mismatch_error"]["params"]["rule"] == "plan_digest_mismatch"
    assert "approval_ref" in digest_fields["proposal_digest"]["excluded_fields"]
    assert "status" in digest_fields["proposal_digest"]["excluded_fields"]
    assert "updated_at" in digest_fields["proposal_digest"]["excluded_fields"]
    contract_definition = digest_fields["contract_digest"]["definition"].lower()
    assert "delivered contract set" in contract_definition
    assert digest_fields["contract_digest"]["must_not_be_derived_from"]
    assert "artifact content" in digest_fields["contract_digest"]["must_not_be_derived_from"]
    for name in DIGEST_FIELDS:
        others = DIGEST_FIELDS - {name}
        assert all(other in digest_fields[name]["is_not"] for other in others)
    reuse = digest_fields["forbidden_reuse_matrix"]
    assert reuse["plan_digest_as_artifact_digest"] == "forbidden"
    assert reuse["artifact_digest_as_plan_digest"] == "forbidden"


def test_health_reports_entry_equivalence_fields(api: dict[str, Any]) -> None:
    response = api["operations"]["health"]["response"]
    for name in ("candidate_revision", "contract_version", "contract_digest"):
        assert name in response["required"]
        assert name in response["fields"]
    assert response["fields"]["contract_digest_scope"]["enum"] == ["delivered-contract-set"]
    assert api["operations"]["health"]["side_effects"] == "none"


def test_ui_schema_subset_is_explicit(api: dict[str, Any]) -> None:
    subset = api["ui_schema_subset"]
    supported = set(subset["supported_keywords"])
    unsupported = set(subset["unsupported_keywords"])
    assert supported
    assert not supported & unsupported
    assert {"type", "properties", "required", "enum", "oneOf", "$ref"} <= supported
    assert {"allOf", "anyOf", "multipleOf"} <= unsupported
    assert "local" in subset["ref_policy"]["allowed"]
    assert "remote refs are unsupported" in subset["ref_policy"]["remote_refs"]
    assert subset["one_of_policy"]["ambiguous_match"]
    assert subset["numeric_bound_policy"]["inclusive_only"] is True
    assert subset["numeric_bound_policy"]["keywords"] == ["minimum", "maximum"]
    assert "union type" in subset["nullable_policy"]["representation"]
    assert subset["unknown_keyword_policy"]


def test_unknown_field_policy_has_four_frozen_rules(api: dict[str, Any]) -> None:
    rules = {rule["id"]: rule for rule in api["unknown_field_policy"]["rules"]}
    assert set(rules) == {
        "U1-new-execution-rejects",
        "U2-historical-reads-preserve",
        "U3-unknown-major-no-execute",
        "U4-extensions-namespaced",
    }
    assert "submit" in rules["U1-new-execution-rejects"]["applies_to"]
    assert rules["U2-historical-reads-preserve"]["eligibility_effect"].startswith("none")
    unsupported = rules["U3-unknown-major-no-execute"]["error"]
    assert unsupported["class"] == "unsupported"
    assert unsupported["params"]["rule"] == "unsupported_major_version"
    assert rules["U4-extensions-namespaced"]["key_pattern"]


def test_error_classes_match_the_machine_output_catalog(api: dict[str, Any]) -> None:
    from acp_services.entrypoints import machine_output as mo

    classes = api["error_classes"]["classes"]
    assert set(classes) == ERROR_CLASSES
    mapped: set[str] = set()
    for name, spec in classes.items():
        assert spec["codes"], name
        assert spec["retryable"] is spec["retryable"]
        for code in spec["codes"]:
            assert code in mo.ErrorCode.ALL, f"{name} uses unknown code {code}"
            catalog = mo.ERROR_CATALOG[code]
            assert mo.exit_code_for(code) == spec["exit_code"], code
            assert catalog.retryable == spec["retryable"], (name, code)
            mapped.add(code)
    out_of_scope = set(api["error_classes"]["out_of_scope_categories"]["codes"])
    assert mapped & out_of_scope == set()
    for code in out_of_scope:
        assert code in mo.ErrorCode.ALL


# ---------------------------------------------------------------------------
# entry-call table
# ---------------------------------------------------------------------------


def test_entry_call_table_has_one_command_per_entry(api: dict[str, Any]) -> None:
    table = api["entry_call_table"]
    operations = set(api["operations"])
    entries: set[str] = set()
    gap_ids = {gap["id"] for gap in table["gaps"]}
    for row in table["rows"]:
        assert row["entry"] not in entries, f"duplicate entry {row['entry']}"
        entries.add(row["entry"])
        assert row["application_command"] in operations
        assert row["use_case"]
        assert row["conformance"] in {"conformant", "conformant_local", "gap"}
        if row["conformance"] == "gap":
            assert row["gap_id"] in gap_ids
    assert set(table["gap_ids"]) == gap_ids
    assert table["audit_verdict"] == "PARTIAL"
    assert {row["application_command"] for row in table["rows"]} <= operations
    rendered = (RUNTIME / "entry-call-table.v1.md").read_text(encoding="utf-8")
    for entry in entries:
        assert f"`{entry}`" in rendered, f"{entry} missing from the rendered table"
    for gap in gap_ids:
        assert gap in rendered


# ---------------------------------------------------------------------------
# JSON-Schema subset validator (used by mcp-launch + template)
# ---------------------------------------------------------------------------


def _type_matches(instance: Any, name: str) -> bool:
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "string":
        return isinstance(instance, str)
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "null":
        return instance is None
    raise AssertionError(f"unsupported type keyword {name}")


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    ref = schema["$ref"]
    assert ref.startswith("#/$defs/"), f"non-local $ref {ref}"
    return root["$defs"][ref.split("/")[-1]]


def validate_instance(
    instance: Any, schema: dict[str, Any], root: dict[str, Any]
) -> list[tuple[str, str]]:
    schema = _resolve(schema, root)
    violations: list[tuple[str, str]] = []
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(instance, name) for name in names):
            violations.append(("type", ""))
            return violations
    if "const" in schema and instance != schema["const"]:
        violations.append(("const", ""))
    if "enum" in schema and instance not in schema["enum"]:
        violations.append(("enum", ""))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            violations.append(("min_length", ""))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            violations.append(("max_length", ""))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            violations.append(("pattern", ""))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            violations.append(("minimum", ""))
        if "maximum" in schema and instance > schema["maximum"]:
            violations.append(("maximum", ""))
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                violations.append(("missing_required", name))
        for name, value in instance.items():
            if name in properties:
                for rule, path in validate_instance(value, properties[name], root):
                    violations.append((rule, f"{name}.{path}" if path else name))
            elif schema.get("additionalProperties") is False:
                violations.append(("additional_property", name))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            violations.append(("min_items", ""))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            violations.append(("max_items", ""))
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                for rule, path in validate_instance(value, items, root):
                    violations.append((rule, f"{index}.{path}" if path else str(index)))
    if "oneOf" in schema:
        matches = sum(not validate_instance(instance, branch, root) for branch in schema["oneOf"])
        if matches == 0:
            violations.append(("oneOf_no_match", ""))
        elif matches > 1:
            violations.append(("oneOf_ambiguous", ""))
    return violations


def test_mcp_launch_schema_freezes_protocol_and_tools(launch_schema: dict[str, Any]) -> None:
    assert launch_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert launch_schema["properties"]["protocol_version"]["const"] == "2025-06-18"
    assert launch_schema["properties"]["transport"]["const"] == "stdio"
    tool_inputs = launch_schema["$defs"]["tool_inputs"]
    assert set(tool_inputs) == MCP_TOOL_NAMES
    constraints = launch_schema["$defs"]["constraints"]
    required = set(constraints["required"])
    assert "no_side_effect_discovery" in required
    assert constraints["properties"]["no_side_effect_discovery"]["const"] is True
    assert constraints["properties"]["no_self_controller"]["const"] is True
    assert constraints["properties"]["no_credential_in_descriptor"]["const"] is True
    assert constraints["properties"]["tool_call_state_authority"]["const"] == "none"


def test_mcp_tool_names_match_the_runtime_allowlist() -> None:
    from acp_services.entrypoints.mcp import McpTool

    assert set(McpTool.ALL) == MCP_TOOL_NAMES


def test_mcp_launch_template_is_placeholder_only(
    launch_schema: dict[str, Any], launch_template: dict[str, Any]
) -> None:
    placeholders = set(launch_template["requires_substitution"])
    examples = launch_template["substitution_examples"]
    assert set(examples) == placeholders
    raw = (RUNTIME / "mcp-launch.template.json").read_text(encoding="utf-8")
    found = set(re.findall(r"__[A-Z0-9_]+__", raw))
    assert found <= placeholders, found - placeholders
    assert found
    instance_text = json.dumps(launch_template["instance"]).lower()
    for forbidden in ("/home/", "/var/lib/acp", "password", "bearer ", "secret", "api_key"):
        assert forbidden not in instance_text, forbidden
    replacement = raw
    for name, value in examples.items():
        replacement = replacement.replace(name, value)
    substituted = json.loads(replacement)["instance"]
    violations = validate_instance(substituted, launch_schema, launch_schema)
    assert violations == [], violations
    tool_inputs = launch_schema["$defs"]["tool_inputs"]
    assert [tool["name"] for tool in launch_template["instance"]["tools"]] == [
        "acp_capabilities",
        "acp_preflight",
        "acp_submit",
        "acp_status",
        "acp_cancel",
        "acp_prepare_artifact",
        "acp_read_artifact",
        "acp_analyze",
    ]
    for tool in launch_template["instance"]["tools"]:
        assert tool["inputSchema"] == tool_inputs[tool["name"]]


# ---------------------------------------------------------------------------
# science binding
# ---------------------------------------------------------------------------


def test_science_binding_maps_the_existing_singlepoint_task() -> None:
    from cccp.calculation.requests import TASK_REQUEST_SCHEMA_VERSION, TaskKind, TaskRequest

    binding = load_json(RUNTIME / "science-binding.v1.json")
    single = binding["singlepoint"]
    assert single["task_kind"] in {kind.value for kind in TaskKind}
    assert single["task_request"]["schema_version"] == TASK_REQUEST_SCHEMA_VERSION
    targets = {spec.name for spec in dataclass_fields(TaskRequest)} | {
        f"level.{name}" for name in ("method", "basis", "dispersion", "solvent")
    }
    for row in single["field_map"]:
        target = row["target"]
        assert target == "TaskRequest.structure" or target.startswith("TaskRequest.")
        bare = target.removeprefix("TaskRequest.").split("[")[0].split(".")[0]
        if bare in {"level", "resources", "options"}:
            continue
        assert bare in targets, f"field_map target {target} is not a TaskRequest field"
    assert single["step_requirements"]["singlepoint"]["requires_geometry"] is True
    assert binding["science_contracts"]["entries"]
    for entry in binding["science_contracts"]["entries"]:
        assert entry["owner"] == "A"
        assert entry["types_copied"] is False
        assert entry["status"] == "delivered"
        assert entry["digest"].startswith("sha256:")
        assert len(entry["digest"]) == 71
        assert "properties" not in entry
        assert "fields" not in entry
    # The historical reservation key stays readable and points at the same
    # delivered digests (B18a increments it, it is not a second contract).
    reserved = binding["reserved_science_contracts"]["entries"]
    assert reserved
    for entry in reserved:
        assert entry["status"] == "delivered"
        assert entry["digest_or_pending"].startswith("sha256:")


def test_science_binding_positive_sample_parses_as_a_task_request() -> None:
    from cccp.calculation.requests import TaskRequest

    samples = sample_payloads()
    positive = next(
        data for path, data in samples.items() if path.endswith("singlepoint-request.positive.json")
    )
    request = TaskRequest.from_dict(positive["payload"])
    assert request.to_dict()["task"] == "singlepoint"


# ---------------------------------------------------------------------------
# control-and-data / admission-approval / p2 / fallback
# ---------------------------------------------------------------------------


def test_control_and_data_p1_fields() -> None:
    contract = load_json(RUNTIME / "control-and-data.v1.json")
    assert set(contract["control"]["intents"]) == {
        "pause",
        "unpause",
        "continue",
        "cancel",
        "rerun",
        "edit",
    }
    cancel = contract["control"]["intents"]["cancel"]
    assert cancel["order_local"] == ["SIGCONT", "SIGTERM", "SIGKILL"]
    assert "confirms" in cancel["rule"]
    prepare = contract["artifacts"]["prepare"]
    assert prepare["operation_states"] == [
        "queued",
        "running",
        "succeeded",
        "failed",
        "canceled",
        "expired",
    ]
    assert prepare["availability"] == ["absent", "preparing", "ready", "unavailable"]
    assert contract["artifacts"]["read"]["request_fields"]["artifact_ref"]["required"] is True
    idempotency = contract["idempotency"]
    assert idempotency["window"]["durable"] is True
    assert "never in a process-local dict" in idempotency["window"]["note"]
    budget = contract["budget"]
    assert budget["p1_fields"]["mode"]["enum"] == ["quota-only", "usage-only"]
    assert any("never changes scientific completion" in rule for rule in budget["rules"])


def test_admission_approval_p1_subset_and_p2_extension() -> None:
    contract = load_json(RUNTIME / "admission-approval.v1.json")
    p1 = contract["p1"]
    assert p1["implementation_status"] == "delivered"
    assert p1["admission_request"]["required"] == ["principal_id", "project_id", "job_id"]
    assert "unbounded_request" in p1["admission_decision"]["fields"]["reason_code"]["enum"]
    assert any("never silently admitted" in rule for rule in p1["rules"])
    p2 = contract["p2_additive"]
    assert p2["owners"] == {"approval": "B16", "cumulative budget": "B17"}
    approval = p2["approval"]
    assert approval["request_fields"]["status"]["enum"] == [
        "pending",
        "approved",
        "rejected",
        "revoked",
        "expired",
    ]
    assert any("approver_identity_from_payload" in rule for rule in approval["rules"])
    assert p2["submit_precondition_chain"] == [
        "proposal approved for plan_digest",
        "budget admitted and reserved",
        "submit",
    ]


def test_p2_research_operations_are_complete_and_owned() -> None:
    contract = load_json(RUNTIME / "p2-research-operations.v1.json")
    assert contract["client_path"]["chosen"] == "versioned MCP extension"
    assert contract["client_path"]["rejected_alternative"].startswith("restricted HTTP client")
    assert set(contract["p1_allowlist_unchanged"]["tools"]) == MCP_TOOL_NAMES
    rows = contract["rows"]
    assert len(rows) == 5
    pattern = re.compile(contract["tool_naming"]["pattern"])
    owners = contract["binding_ownership"]
    for row in rows:
        assert row["binding_owner"] in {"B16", "B18"}
        assert row["controller_client_methods"]
        assert row["application_use_case"]
        assert row["authorization"]
        assert row["error_classes"]
        assert row["live_tool_requirement"]
        for error_class in row["error_classes"]:
            assert error_class in ERROR_CLASSES
        for tool in row["tools"]:
            assert pattern.match(tool["name"]), tool["name"]
            assert tool["tool_version"] == "1.0.0"
        assert row["id"] in owners[row["binding_owner"]]
    covered = {row["operation"] for row in rows}
    assert covered == {
        "Proposal/ResearchRecord create/read/CAS",
        "request/query approval",
        "approve/reject/revoke",
        "task/approval/budget waiting",
        "research resume",
    }


def test_fallback_deferral_records_cdxml_scope_change() -> None:
    contract = load_json(RUNTIME / "fallback-deferral.json")
    row = contract["defers"][0]
    assert row["capability"].startswith("CDXML")
    assert row["decision"] == "deferred"
    assert set(row["scope_change_records"]) == {"A", "B", "C"}
    assert row["unsupported_behaviour"]["empty_structure_task_forbidden"] is True
    assert row["reserved_endpoint"]["status"] == "reserved_not_frozen"
    assert len(row["backfill_conditions"]) >= 4
    assert row["not_verified"]["status"] == "NOT_VERIFIED"


def test_ecosystem_round_trip_is_scheduled_not_claimed(api: dict[str, Any]) -> None:
    round_trip = api["ecosystem_round_trip"]
    assert round_trip["status"] == "scheduled"
    assert round_trip["owner"] == "B18"
    assert round_trip["must_not_claim_now"]
    assert api["p2_research_operations"]["client_path"].startswith("versioned MCP extension")
    assert api["cdxml"]["decision"] == "deferred"


# ---------------------------------------------------------------------------
# canonical digest vectors
# ---------------------------------------------------------------------------

DEC_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$")
INPUT_DEC = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")


class CanonicalError(Exception):
    pass


def _canon_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if not DEC_PATTERN.match(text):
        raise CanonicalError("non_canonical_decimal_output")
    return text


def _canonicalize(
    value: Any, units: dict[str, Any], set_fields: tuple[str, ...], drop_fields: tuple[str, ...]
) -> Any:
    if not isinstance(value, dict):
        raise CanonicalError("root_not_object")
    stripped = {key: item for key, item in value.items() if key not in drop_fields}

    def walk(node: Any, parent: str | None) -> Any:
        if node is None:
            raise CanonicalError("null_in_digest_input")
        if isinstance(node, bool):
            return node
        if isinstance(node, float):
            raise CanonicalError("non_canonical_number")
        if isinstance(node, int):
            return node
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if set(node) == {"value", "unit"}:
                unit = node["unit"]
                if unit not in units:
                    raise CanonicalError("unknown_unit")
                spec = units[unit]
                raw = node["value"]
                if isinstance(raw, float) or isinstance(raw, bool):
                    raise CanonicalError("non_canonical_number")
                if isinstance(raw, str):
                    if not INPUT_DEC.match(raw):
                        raise CanonicalError("non_canonical_number")
                    decimal = Decimal(raw)
                else:
                    decimal = Decimal(raw)
                return {
                    "unit": spec["base"],
                    "value": _canon_decimal(decimal * Decimal(spec["multiplier"])),
                }
            result = {}
            for key in sorted(node):
                if not KEY_PATTERN.match(key):
                    raise CanonicalError("non_canonical_key")
                result[key] = walk(node[key], key)
            return result
        if isinstance(node, list):
            items = [walk(item, parent) for item in node]
            if parent in set_fields:
                items.sort(
                    key=lambda item: json.dumps(
                        item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    )
                )
            return items
        raise CanonicalError("unsupported_value")

    return walk(stripped, None)


def _unit_table() -> dict[str, dict[str, str]]:
    vectors = load_json(RUNTIME / "canonical-digest-vectors.v1.json")
    base_by_dimension = vectors["algorithm"]["base_units"]
    table: dict[str, dict[str, str]] = {}
    for unit, spec in vectors["algorithm"]["units"].items():
        table[unit] = {
            "base": base_by_dimension[spec["dimension"]],
            "multiplier": spec["multiplier_to_base"],
        }
    return table


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + sha256_bytes(canonical.encode("utf-8"))


def test_canonical_digest_vectors_reproduce_byte_for_byte() -> None:
    doc = load_json(RUNTIME / "canonical-digest-vectors.v1.json")
    units = _unit_table()
    digests: dict[str, str] = {}
    canonical: dict[str, str] = {}
    for vector in doc["vectors"]:
        if "expected_error" in vector:
            with pytest.raises(CanonicalError) as excinfo:
                _canonicalize(
                    vector["input"],
                    units,
                    tuple(vector["set_fields"]),
                    tuple(vector["drop_fields"]),
                )
            assert str(excinfo.value) == vector["expected_error"]["params"]["rule"]
            assert vector["expected_error"]["class"] == "invalid-parameter"
            continue
        obj = _canonicalize(
            vector["input"], units, tuple(vector["set_fields"]), tuple(vector["drop_fields"])
        )
        text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert text == vector["canonical_json"], vector["id"]
        assert _digest(obj) == vector["digest"], vector["id"]
        assert vector["canonical_bytes_sha256"] == sha256_bytes(text.encode("utf-8"))
        digests[vector["id"]] = vector["digest"]
        canonical[vector["id"]] = text
    assert len(digests) >= 3
    for vector in doc["vectors"]:
        peer = vector.get("equivalent_to")
        if peer:
            assert digests[vector["id"]] == digests[peer]
        distinct = vector.get("expect_distinct_from")
        if distinct:
            assert digests[vector["id"]] != digests[distinct]
    rejected = [vector for vector in doc["vectors"] if "expected_error" in vector]
    assert len(rejected) >= 3


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


def _graph_errors(graph: dict[str, Any]) -> set[str]:
    errors: set[str] = set()
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    external = {node["id"] for node in graph.get("external_nodes", [])}
    for node in nodes.values():
        for dep in node.get("depends_on", []):
            if " or " in str(dep).lower():
                errors.add("free_text_or")
            if dep not in nodes and dep not in external:
                errors.add("dangling_reference")
    colors: dict[str, int] = {}

    def visit(node_id: str) -> None:
        colors[node_id] = 1
        for dep in nodes[node_id].get("depends_on", []):
            if dep not in nodes:
                continue
            if colors.get(dep) == 1:
                errors.add("cycle")
            elif colors.get(dep, 0) == 0:
                visit(dep)
        colors[node_id] = 2

    for node_id in sorted(nodes):
        if colors.get(node_id, 0) == 0:
            visit(node_id)
    for node in nodes.values():
        for dep in node.get("depends_on", []):
            if dep in nodes and nodes[dep]["todo"] > node["todo"]:
                errors.add("acceptance_before_prerequisite")
    for edge in graph.get("external_edges", []):
        if edge.get("consumer") not in nodes:
            errors.add("dangling_reference")
        if edge.get("external_id") not in external:
            errors.add("missing_producer")
        if edge.get("relation") not in {"AND", "OR"}:
            errors.add("free_text_or")
    return errors


def test_dependencies_graph_is_valid(dependencies: dict[str, Any]) -> None:
    assert dependencies["schema"] == "acp2.dependencies.v1"
    assert _graph_errors(dependencies) == set()
    assert dependencies["validation"]["stage_order"]["status"] == "PASS"
    nodes = {node["id"]: node for node in dependencies["nodes"]}
    assert {"B00", "B01", "B02", "B03"} <= set(nodes)
    b03 = nodes["B03"]["artifact"]
    assert b03["readiness"] == "contract_ready"
    assert b03["contract_delivery"] == "delivered"
    assert b03["digest_target"].endswith("application-api.v1.json")
    assert b03["digest_or_pending"].startswith("sha256:")
    for node in dependencies["nodes"]:
        artifact = node["artifact"]
        assert artifact["version"]
        assert artifact["readiness"] in {
            "contract_ready",
            "implementation_ready",
            "integration_ready",
            "verified",
        }
        assert artifact["delivery_status"] in {"planned", "blocked"}


def test_dependencies_merge_c_rows_and_record_the_a_gap(dependencies: dict[str, Any]) -> None:
    sources = {source["track"]: source for source in dependencies["sources"]}
    assert set(sources) == {"A", "B", "C"}
    assert sources["C"]["sha256"]
    assert sources["C"]["merged_rows"] == 5
    a_source = sources["A"]
    assert a_source["sha256"] is None
    assert a_source["status"] == "NOT_VERIFIED"
    assert a_source["reason"] == "blocked_fixture"
    assert a_source["path"].endswith("science/cross-track-dependencies.json")
    rows = dependencies["not_verified"]
    assert rows
    assert rows[0]["status"] == "NOT_VERIFIED"
    assert rows[0]["reason"] == "blocked_fixture"
    assert rows[0]["expected_path"] == a_source["path"]
    edges = dependencies["consumer_edges"]
    assert len(edges) >= 5
    ready_to_c24a = [
        edge for edge in edges if edge["producer"] == "B20a-ready" and "T-34" in edge["consumer"]
    ]
    assert ready_to_c24a, "C T-34/C24a must gate on B20a-ready, not the whole B20a"
    node_ids = {node["id"] for node in dependencies["nodes"]}
    for edge in edges:
        assert edge["producer"] in node_ids
        assert edge["relation"] in {"AND", "OR"}
        assert edge["readiness"] in {
            "contract_ready",
            "implementation_ready",
            "integration_ready",
            "verified",
        }


def test_dependency_negative_samples_trigger_their_rule() -> None:
    samples = sample_payloads()
    for path, data in samples.items():
        if data["contract"] != "dependencies.json":
            continue
        errors = _graph_errors(data["payload"])
        if data["kind"] == "positive":
            assert errors == set(), (path, errors)
        else:
            assert data["expected"]["violation"] in errors, (path, errors)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _contract_digest(manifest: dict[str, Any]) -> str:
    lines = [
        f"{entry['path']} {entry['sha256']}\n"
        for entry in manifest["contracts"]
        if entry["status"] == "delivered" and entry["id"] not in {"manifest", "baseline-lock"}
    ]
    return sha256_bytes("".join(sorted(lines)).encode("utf-8"))


def test_manifest_delivers_every_delivered_contract(manifest: dict[str, Any]) -> None:
    # M0-01 review vector: the release namespace was registered inside the
    # existing delivery set, so the manifest contract version moved to 1.4.0.
    # Kept as an explicit expectation instead of reading the value back.
    assert manifest["contract_version"] == "1.4.0"
    delivered = {entry["id"]: entry for entry in manifest["contracts"]}
    b03_ids = {
        "application-api",
        "entry-call-table",
        "p2-research-operations",
        "mcp-launch-schema",
        "mcp-launch-template",
        "science-binding",
        "control-and-data",
        "admission-approval",
        "fallback-deferral",
        "canonical-digest-vectors",
        "samples",
    }
    assert b03_ids <= set(delivered)
    for contract_id in b03_ids:
        entry = delivered[contract_id]
        assert entry["status"] == "delivered", contract_id
        assert entry["delivered_by"] == "B03"
        target = entry.get("digest_target")
        path = REPO_ROOT / (target or entry["path"])
        assert path.is_file(), contract_id
        assert sha256_bytes(path.read_bytes()) == entry["sha256"], contract_id
    for entry in manifest["contracts"]:
        if entry["status"] == "delivered":
            # M0-07 (2026-10-27): the trusted-usage-receipts contract is the
            # first delivered contract registered under a remediation stage id
            # instead of a B-stage; the set grows only through a new manifest
            # registration with its DELIVERED_IDS_BY_STAGE mapping.
            assert entry["delivered_by"] in {"B00", "B01", "B03", "B04a", "B06", "B19", "M0-07"}
        else:
            assert "sha256" not in entry

    # M0-01b registers the release namespace as planned contracts owned by B;
    # they may only flip to delivered through the recorded M5-02 release stage.
    release_ids = {
        "release-scope",
        "release-evidence-coverage-schema",
        "release-bom-schema",
    }
    assert release_ids <= set(delivered)
    for contract_id in release_ids:
        entry = delivered[contract_id]
        assert entry["status"] == "planned", contract_id
        assert entry["owner"] == "B", contract_id
        assert entry["path"].startswith("docs/reference/next-change/acp2-contracts/release/"), (
            contract_id
        )
        assert (REPO_ROOT / entry["path"]).is_file(), contract_id


def test_cross_track_requests_are_recorded_and_owned(manifest: dict[str, Any]) -> None:
    path = RUNTIME / "cross-track-requests.v1.json"
    contract = load_json(path)
    assert contract["schema"] == "acp2.runtime.cross-track-requests.v1"
    requests = {row["request_id"]: row for row in contract["requests"]}
    assert {"B-req-01", "B-req-06", "B-req-07"} <= set(requests)
    for row in contract["requests"]:
        assert row["from"] in {"A", "B", "C", "integration"}
        assert row["owner"]
        assert row["status"] in {"open", "satisfied", "deferred", "not_verified"}
        assert row["step"]
        assert row["expected"]
        assert row["evidence"]
    assert requests["B-req-01"]["status"] == "satisfied"
    assert requests["B-req-07"]["status"] == "satisfied"
    assert requests["B-req-06"]["status"] == "open"
    delivered = {entry["id"]: entry for entry in manifest["contracts"]}
    assert delivered["cross-track-requests"]["delivered_by"] == "B06"


def test_application_api_v1_1_research_fields_are_additive() -> None:
    api = load_json(RUNTIME / "application-api.v1.json")
    # 1.2.0 (M0-06/G23): additive execution-decision fields (name/tags/node_tags/
    # execution_mode/target_node/molecule_name/task_name/remark/
    # preflight_fingerprint) plus the exhaustive ``submit_field_policy`` block.
    # 1.1.0's research fields stay untouched.
    assert api["contract_version"] == "1.2.0"
    submit = api["operations"]["submit"]
    for name in ("proposal_ref", "plan_digest", "budget"):
        assert name in submit["request"]["fields"], name
    for name in (
        "name",
        "tags",
        "node_tags",
        "execution_mode",
        "target_node",
        "molecule_name",
        "task_name",
        "remark",
        "preflight_fingerprint",
    ):
        assert name in submit["request"]["fields"], name
    assert api["submit_field_policy"]["freeze"].startswith("v1 extra=forbid")
    assert api["submit_field_policy"]["legacy_allowlist"] == {}
    documented = api["submit_field_policy"]["fields"]
    # The policy block also documents the two *rejected* server-path fields, which
    # are deliberately absent from the declared request fields.
    assert set(documented) - {"output_dir", "config_path"} == set(submit["request"]["fields"])
    assert documented["output_dir"]["disposition"] == "server_path_rejected"
    assert documented["config_path"]["entries"] == []
    for name in ("execution_mode", "target_node", "node_tags"):
        assert name in api["operations"]["preflight"]["request"]["fields"], name
    assert submit["request"]["fields"]["budget"]["required"] == ["unit", "timeout_s"]
    for name in ("task_ref", "result", "approval_ref"):
        assert name in submit["response"]["fields"], name
    extension = api["mcp_extensions"]["acp.research.v1"]
    # M0-09/ADR-006 D7: negotiation moved out of initialize into the P1
    # discovery tool; the version is the extension authority (1.0.0), not the
    # application contract version (1.1.0).
    assert extension["negotiated_in"] == "tools/call"
    assert extension["negotiation_tool"] == "acp_capabilities"
    assert extension["negotiation_request_field"] == "research_extension"
    assert extension["initialize_claim"] == "none"
    assert extension["version"] == "1.0.0"
    assert extension["p1_tool_allowlist_unchanged"] is True
    assert extension["implemented"] is False
    assert extension["negotiated_default"] is False


def test_extension_version_authority_is_the_p2_contract() -> None:
    """One authority for the extension id/version, projected everywhere else."""
    api = load_json(RUNTIME / "application-api.v1.json")
    p2 = load_json(RUNTIME / "p2-research-operations.v1.json")
    negotiation = p2["client_path"]["negotiation"]
    assert negotiation["extension_id"] == "acp.research.v1"
    assert negotiation["extension_version"] == "1.0.0"
    assert negotiation["request_carrier"] == "tools/call"
    assert negotiation["request_tool"] == "acp_capabilities"
    assert negotiation["request_field"] == "research_extension"
    assert negotiation["initialize_claim"] == "none"
    assert negotiation["implemented"] is False
    assert negotiation["negotiated_default"] is False
    assert "initialize_param" not in negotiation
    projection = api["mcp_extensions"]["acp.research.v1"]
    assert projection["version"] == negotiation["extension_version"]
    assert projection["negotiated_in"] == negotiation["request_carrier"]
    assert projection["negotiation_tool"] == negotiation["request_tool"]
    # The frozen RPH bridge declares the same pair; the M0-09 server spec must
    # project the same authority (checked in test_acp2_mcp_research_handshake).
    bridge = (REPO_ROOT / "rph/packages/bridge/src/index.ts").read_text(encoding="utf-8")
    assert 'BRIDGE_EXTENSION_ID = "acp.research.v1"' in bridge
    assert f'BRIDGE_EXTENSION_VERSION = "{negotiation["extension_version"]}"' in bridge


def test_manifest_contract_digest_and_self_digest(manifest: dict[str, Any]) -> None:
    assert manifest["contract_digest"] == _contract_digest(manifest)
    stripped = json.loads(json.dumps(manifest))
    recorded = next(entry["sha256"] for entry in stripped["contracts"] if entry["id"] == "manifest")
    for entry in stripped["contracts"]:
        if entry["id"] == "manifest":
            entry["sha256"] = ""
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert recorded == sha256_bytes(canonical.encode("utf-8"))
    assert "delivered" in manifest["digest_rules"]["merged_contract_digest"]


def test_manifest_validator_accepts_the_delivered_set() -> None:
    import subprocess
    import sys

    script = REPO_ROOT / "scripts/acceptance/validate_acp2_contracts.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# samples
# ---------------------------------------------------------------------------


def test_sample_index_is_complete_and_hashed(samples: dict[str, Any]) -> None:
    listed = {entry["path"] for entry in samples["entries"]}
    excluded = tuple(samples["excluded_subtrees"])
    found = {
        str(path.relative_to(RUNTIME)).replace("\\", "/")
        for path in SAMPLES.rglob("*.json")
        if path.name != "manifest.json"
        and not str(path.relative_to(SAMPLES)).replace("\\", "/").startswith(excluded)
    }
    assert listed == found
    counts: dict[str, dict[str, int]] = {}
    for entry in samples["entries"]:
        data = load_json(SAMPLES / entry["path"].removeprefix("samples/"))
        assert (
            sha256_bytes((SAMPLES / entry["path"].removeprefix("samples/")).read_bytes())
            == entry["sha256"]
        )
        assert data["contract"] == entry["contract"]
        assert data["kind"] == entry["kind"]
        counts.setdefault(entry["contract"], {"positive": 0, "negative": 0})
        counts[entry["contract"]][entry["kind"]] += 1
    for contract, kind_counts in counts.items():
        assert kind_counts["positive"] >= 1, f"{contract} has no positive sample"
        assert kind_counts["negative"] >= 1, f"{contract} has no negative sample"
    assert samples["counts"]["total"] == len(samples["entries"])
    assert samples["counts"]["by_contract"] == counts


def _application_api_violations(payload: dict[str, Any], operation: str) -> list[tuple[str, str]]:
    api = load_json(RUNTIME / "application-api.v1.json")
    if payload.get("schema_version", 1) != 1:
        return [("unsupported_major_version", "schema_version")]
    spec = api["operations"][operation]["request"]
    references = api["references"]
    violations: list[tuple[str, str]] = []

    def check(value: Any, field_spec: dict[str, Any], path: str) -> None:
        ref = field_spec.get("ref")
        if ref:
            nested = references[ref]
            if not isinstance(value, dict):
                violations.append(("type", path))
                return
            for name in nested["required"]:
                if name not in value:
                    violations.append(("missing_required", f"{path}.{name}"))
            for name, item in value.items():
                if name in nested["fields"]:
                    check_nested(item, nested["fields"][name], f"{path}.{name}")
            return
        if field_spec.get("type") == "array":
            if not isinstance(value, list):
                violations.append(("type", path))
                return
            items_ref = field_spec.get("items_ref")
            for index, item in enumerate(value):
                if items_ref:
                    check(item, {"ref": items_ref}, f"{path}.{index}")
            return
        check_nested(value, field_spec, path)

    def check_nested(value: Any, field_spec: dict[str, Any], path: str) -> None:
        type_name = field_spec.get("type")
        if isinstance(type_name, str):
            if type_name == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    violations.append(("type", path))
                    return
            elif type_name == "boolean":
                if not isinstance(value, bool):
                    violations.append(("type", path))
                    return
            elif not _type_matches(value, type_name):
                violations.append(("type", path))
                return
        if "enum" in field_spec and value not in field_spec["enum"]:
            violations.append(("enum", path))
        if field_spec.get("const") is not None and value != field_spec["const"]:
            violations.append(("const", path))
        if "pattern" in field_spec and isinstance(value, str):
            if not re.search(field_spec["pattern"], value):
                violations.append(("pattern", path))
        if isinstance(value, int) and not isinstance(value, bool):
            if "minimum" in field_spec and value < field_spec["minimum"]:
                violations.append(("minimum", path))
            if "maximum" in field_spec and value > field_spec["maximum"]:
                violations.append(("maximum", path))

    for name in spec["required"]:
        if name not in payload:
            violations.append(("missing_required", name))
    for name, value in payload.items():
        if name not in spec["fields"]:
            violations.append(("unknown_field", name))
            continue
        check(value, spec["fields"][name], name)
    if operation == "capabilities":
        violations.extend(_extension_request_violations(payload, api))
    return violations


def _extension_request_violations(
    payload: dict[str, Any], api: dict[str, Any]
) -> list[tuple[str, str]]:
    """Semantic checks for the M0-09 handshake request (shape is checked above).

    The request is accepted only for an advertised extension at the supported
    version; a version mismatch or an unknown id is refused, never adopted by
    inference.  The 1.1.0 value is the historical B06 mistake (the application
    contract version) and exists here as a permanent negative.
    """
    extension = payload.get("research_extension")
    if not isinstance(extension, dict):
        return []
    advertised = api.get("mcp_extensions") or {}
    extension_id = extension.get("id")
    if extension_id not in advertised:
        return [("unknown_extension", "research_extension.id")]
    expected = advertised[extension_id]["version"]
    requested = extension.get("version")
    if requested is not None and requested != expected:
        return [("extension_version_mismatch", "research_extension.version")]
    return []


def _launch_violations(payload: dict[str, Any]) -> list[tuple[str, str]]:
    schema = load_json(RUNTIME / "mcp-launch.schema.json")
    return validate_instance(payload, schema, schema)


def _binding_violations(payload: dict[str, Any]) -> list[tuple[str, str]]:
    binding = load_json(RUNTIME / "science-binding.v1.json")
    if payload.get("schema_id"):
        for entry in binding["reserved_science_contracts"]["entries"]:
            if entry["schema_id"] == payload.get("schema_id"):
                if payload.get("digest") == "PENDING":
                    return [("pending_reference", "digest")]
        return [("unknown_schema_id", "schema_id")]
    from cccp.calculation.requests import TaskRequest

    try:
        TaskRequest.from_dict(payload)
    except Exception as err:  # noqa: BLE001 - the adapter raises several typed errors
        message = str(err)
        if "schema_version" in message:
            return [("unsupported_major_version", "schema_version")]
        if "task must be one of" in message:
            return [("unknown_task_kind", "task")]
        return [("invalid_task_request", "")]
    return []


def _required_field_violations(
    payload: dict[str, Any], spec: dict[str, Any]
) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for name, field_spec in spec.items():
        if field_spec.get("required") and name not in payload:
            violations.append(("missing_required", name))
    return violations


def _control_data_violations(
    payload: dict[str, Any], operation: str | None
) -> list[tuple[str, str]]:
    contract = load_json(RUNTIME / "control-and-data.v1.json")
    if operation == "read" and payload.get("served_digest"):
        if payload["served_digest"] != payload["artifact_ref"]["digest"]:
            return [("digest_mismatch", "artifact_ref.digest")]
    if payload.get("idempotency_key") and payload.get("existing_plan_digest"):
        if payload["plan_digest"] != payload["existing_plan_digest"]:
            return [("idempotency_key_reuse", "idempotency_key")]
    if operation == "prepare":
        return _required_field_violations(
            payload, contract["artifacts"]["prepare"]["request_fields"]
        )
    return []


def _admission_violations(payload: dict[str, Any], operation: str | None) -> list[tuple[str, str]]:
    contract = load_json(RUNTIME / "admission-approval.v1.json")
    violations: list[tuple[str, str]] = []
    if payload.get("principal_id") is not None:
        fields = payload
        for name in contract["p1"]["admission_request"]["required"]:
            if name not in fields:
                violations.append(("missing_required", name))
        resource_dims = ("cores", "memory_mb", "duration_s", "storage_bytes")
        if all(not fields.get(name) for name in resource_dims):
            violations.append(("unbounded_request", "cores"))
        return violations
    approval = contract["p2_additive"]["approval"]["request_fields"]
    for name in contract["p2_additive"]["approval"]["request_fields"]:
        if approval[name].get("required") and name not in payload:
            violations.append(("missing_required", name))
    if payload.get("approver_source") == "payload" or str(
        payload.get("approver_ref", "")
    ).startswith("model:"):
        violations.append(("approver_identity_from_payload", "approver_ref"))
    approved = payload.get("approved_plan_digest")
    if approved and approved != payload.get("plan_digest"):
        violations.append(("approved_plan_digest_mismatch", "plan_digest"))
    return violations


def _p2_violations(payload: dict[str, Any], row_id: str | None) -> list[tuple[str, str]]:
    contract = load_json(RUNTIME / "p2-research-operations.v1.json")
    row = next(row for row in contract["rows"] if row["id"] == row_id)
    tool = next(tool for tool in row["tools"] if tool["name"] == payload["tool"])
    arguments = payload["arguments"]
    violations = [
        ("missing_required", name)
        for name in tool["arguments"]["required"]
        if name not in arguments
    ]
    if "approver" in arguments:
        violations.append(("approver_identity_from_payload", "approver"))
    if (
        "max_wait_s" in arguments
        and arguments["max_wait_s"] > contract["waits"]["bounds"]["max_wait_s"]
    ):
        violations.append(("bound_exceeded", "max_wait_s"))
    return violations


def _fallback_violations(payload: dict[str, Any]) -> list[tuple[str, str]]:
    if payload.get("creates_task") and payload.get("structure") is None:
        return [("empty_structure_task_forbidden", "creates_task")]
    if payload["response"]["params"]["rule"] != "unsupported_structure_format":
        return [("wrong_refusal", "response.params.rule")]
    return []


def _manifest_violations(payload: dict[str, Any]) -> list[tuple[str, str]]:
    contracts = payload["contracts"]
    seen: set[str] = set()
    violations: list[tuple[str, str]] = []
    for index, entry in enumerate(contracts):
        if entry["id"] in seen:
            violations.append(("duplicate_contract_id", f"contracts[{index}].id"))
        seen.add(entry["id"])
        if entry.get("owner") not in {"A", "B", "C"}:
            violations.append(("invalid_owner", f"contracts[{index}].owner"))
        if entry["status"] == "delivered" and not entry.get("sha256"):
            violations.append(("delivered_missing_sha", f"contracts[{index}].sha256"))
    return violations


def test_samples_accept_or_reject_exactly_as_declared() -> None:
    samples = sample_payloads()
    violations_by_contract: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for path, data in samples.items():
        contract = data["contract"]
        operation = data.get("operation")
        payload = data["payload"]
        if contract == "application-api.v1.json":
            violations = _application_api_violations(payload, operation)
        elif contract == "mcp-launch.schema.json":
            violations = _launch_violations(payload)
        elif contract == "science-binding.v1.json":
            violations = _binding_violations(payload)
        elif contract == "control-and-data.v1.json":
            violations = _control_data_violations(payload, operation)
        elif contract == "admission-approval.v1.json":
            violations = _admission_violations(payload, operation)
        elif contract == "p2-research-operations.v1.json":
            violations = _p2_violations(payload, operation)
        elif contract == "fallback-deferral.json":
            violations = _fallback_violations(payload)
        elif contract == "manifest.json":
            violations = _manifest_violations(payload)
        elif contract == "dependencies.json":
            errors = _graph_errors(payload)
            violations = [(error, "") for error in sorted(errors)]
        else:
            raise AssertionError(f"no validator for {contract}")
        violations_by_contract.setdefault(contract, {})[path] = violations
        expected = data["expected"]
        if data["kind"] == "positive":
            assert violations == [], (path, violations)
        else:
            rules = {rule for rule, _ in violations}
            assert expected["violation"] in rules, (path, violations)
            assert expected["class"] in ERROR_CLASSES
    assert violations_by_contract


def test_negative_samples_cover_the_required_failure_modes() -> None:
    samples = sample_payloads()
    modes = {
        data["expected"]["violation"] for data in samples.values() if data["kind"] == "negative"
    }
    for required in (
        "unknown_field",
        "unsupported_major_version",
        "missing_required",
        "enum",
        "missing_producer",
        "dangling_reference",
        "cycle",
        "acceptance_before_prerequisite",
    ):
        assert required in modes, required


# ---------------------------------------------------------------------------
# no duplicate implementation
# ---------------------------------------------------------------------------


def test_b03_did_not_add_a_second_canonicalizer_or_science_dtos() -> None:
    # B03 froze the rules only; B16 owns the one implementation.  No other
    # module may define a second canonicalizer.
    canonicalizer = REPO_ROOT / "src/acp_services/runtime/canonical_digest.py"
    assert canonicalizer.is_file()
    api_text = (RUNTIME / "application-api.v1.json").read_text(encoding="utf-8")
    assert "B16 (src/acp_services/runtime/canonical_digest.py)" in api_text
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if path == canonicalizer:
            continue
        text = path.read_text(encoding="utf-8")
        assert "def canonical_digest(" not in text, path
    binding = load_json(RUNTIME / "science-binding.v1.json")
    for entry in binding["reserved_science_contracts"]["entries"]:
        assert set(entry) == {
            "schema_id",
            "owner",
            "producer_task",
            "version",
            "digest_or_pending",
            "expected_path",
            "status",
            "types_copied",
        }
    api_text = (RUNTIME / "application-api.v1.json").read_text(encoding="utf-8")
    assert "class TaskRequest" not in api_text
    assert "proposal_digest_is_plan_digest" not in api_text
