# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""ACP2 science capability/task-binding contract test (Plan A todo 4 / A-01b).

Standard library only for schema assertion: ``json`` parsing plus a **restricted**
draft-2020-12 assertion helper. ``jsonschema`` is deliberately absent and must
never be imported here.

The helper declares exactly which constraint keywords it can enforce and raises
:class:`UnsupportedSchemaKeywordError` on anything else. It never silently skips
a declared constraint, so a schema that grows an unsupported keyword fails the
run instead of passing vacuously. The supported set is the one frozen by plan
A-01a plus the keyword set named by this task (``propertyNames`` included).

Named contract outcomes exercised here:

* ``capability_unknown_version``          -> ``unknown_major_version``
* ``capability_duplicate_id``             -> ``duplicate_capability_id``
* ``capability_unsupported_provider``     -> ``unknown_implementation``
* ``capability_unknown_execution_field``  -> rejected for execution
* ``historical_read_unknown_field``       -> readable, never executable/recoverable

The fixtures use the canonical capability/task-binding/error contracts from
``docs/reference/next-change/acp2-contracts/science``. Registry lookups and the
historical-read adapter are contract-level harness functions, not a second
production registry; A-04/A-05 own the real implementations.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
EXAMPLES = SCIENCE / "examples"
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "contracts"

CAPABILITY_SCHEMA_PATH = SCIENCE / "capability.schema.json"
TASK_BINDING_SCHEMA_PATH = SCIENCE / "task-binding.schema.json"
ERRORS_SCHEMA_PATH = SCIENCE / "errors.schema.json"
HANDOFF_PATH = SCIENCE / "handoff-H1.json"

SUPPORTED_SCHEMA_VERSION = 1
FROZEN_ERROR_CODES = frozenset(
    {
        "unknown_major_version",
        "duplicate_capability_id",
        "unknown_implementation",
        "disabled_provider",
        "unavailable_binary",
        "invalid_scientific_input",
    }
)
TASK_KINDS = frozenset(
    {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "casscf",
        "thermochemistry",
        "conformer_search",
        "md_sampling",
        "clustering",
        "censo_refine",
        "nmr_shielding",
        "xtb_path_search",
        "orca_gradient",
        "xtb_constrained_opt",
    }
)
KNOWN_IMPLEMENTATIONS = {
    "electronic.energy": "cccp.calculation.tasks.singlepoint",
}

CONTRACT_FILE_NAMES = (
    "capability.schema.json",
    "task-binding.schema.json",
    "errors.schema.json",
    "reaction.schema.json",
    "question.schema.json",
    "evidence.schema.json",
    "provenance.schema.json",
    "recipe.schema.json",
    "semantic-rules.md",
    "evidence-semantics.md",
    "examples/capability-energy.json",
    "examples/task-binding-singlepoint.json",
)


# ── restricted draft-2020-12 assertion helper ────────────────────────────


class UnsupportedSchemaKeywordError(AssertionError):
    """Raised when a schema uses a keyword the restricted helper cannot enforce."""


class RestrictedSchemaValidator:
    """Enforce a deliberately bounded draft-2020-12 keyword subset.

    ``SUPPORTED_CONSTRAINT_KEYWORDS`` is declared up front. Any other
    constraint keyword raises immediately during construction; nothing is
    ignored. Annotation keys are metadata and cannot affect validation.
    """

    SUPPORTED_CONSTRAINT_KEYWORDS = frozenset(
        {
            "$defs",
            "$ref",
            "additionalProperties",
            "allOf",
            "anyOf",
            "const",
            "enum",
            "items",
            "maximum",
            "minimum",
            "oneOf",
            "pattern",
            "properties",
            "propertyNames",
            "required",
            "type",
        }
    )
    ANNOTATION_KEYWORDS = frozenset(
        {
            "$comment",
            "$id",
            "$schema",
            "description",
            "error_catalog",
            "schema_dialect",
            "schema_version",
            "title",
            "ui_subset_version",
        }
    )

    def __init__(self, schema: dict[str, Any], *, name: str = "<schema>") -> None:
        self.root = schema
        self.name = name
        self.assert_supported_keywords(schema, "#")

    # -- keyword coverage -------------------------------------------------

    def assert_supported_keywords(self, schema: Any, path: str) -> None:
        if not isinstance(schema, dict):
            return
        for keyword, value in schema.items():
            if keyword in self.ANNOTATION_KEYWORDS:
                continue
            if keyword not in self.SUPPORTED_CONSTRAINT_KEYWORDS:
                raise UnsupportedSchemaKeywordError(
                    f"{self.name}: unsupported schema keyword {keyword!r} at {path}"
                )
            child = f"{path}/{keyword}"
            if keyword == "$defs":
                if not isinstance(value, dict):
                    raise UnsupportedSchemaKeywordError(
                        f"{self.name}: $defs must be an object at {path}"
                    )
                for def_name, subschema in value.items():
                    self.assert_supported_keywords(subschema, f"{child}/{def_name}")
            elif keyword == "properties":
                if not isinstance(value, dict):
                    raise UnsupportedSchemaKeywordError(
                        f"{self.name}: properties must be an object at {path}"
                    )
                for prop_name, subschema in value.items():
                    self.assert_supported_keywords(subschema, f"{child}/{prop_name}")
            elif keyword in {"additionalProperties", "items", "propertyNames"}:
                if isinstance(value, dict):
                    self.assert_supported_keywords(value, child)
                elif keyword == "items" and not isinstance(value, bool):
                    raise UnsupportedSchemaKeywordError(
                        f"{self.name}: tuple-form items are not supported at {path}"
                    )
            elif keyword in {"oneOf", "anyOf", "allOf"}:
                if not isinstance(value, list):
                    raise UnsupportedSchemaKeywordError(
                        f"{self.name}: {keyword} must be an array at {path}"
                    )
                for index, subschema in enumerate(value):
                    self.assert_supported_keywords(
                        subschema, f"{child}/{index}"
                    )

    # -- validation -------------------------------------------------------

    def validate(self, instance: Any) -> list[str]:
        errors: list[str] = []
        self._validate(instance, self.root, "$", errors)
        return errors

    def is_valid(self, instance: Any) -> bool:
        return not self.validate(instance)

    def _resolve(self, ref: str) -> dict[str, Any]:
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise UnsupportedSchemaKeywordError(
                f"{self.name}: only local $ref values are supported, got {ref!r}"
            )
        pointer = ref[1:]
        node: Any = self.root
        if pointer in {"", "/"}:
            return node
        for token in pointer.lstrip("/").split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise UnsupportedSchemaKeywordError(
                    f"{self.name}: unresolved $ref {ref!r}"
                )
            node = node[key]
        if not isinstance(node, dict):
            raise UnsupportedSchemaKeywordError(
                f"{self.name}: $ref {ref!r} does not resolve to a schema object"
            )
        return node

    def _validate(
        self,
        instance: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        if "$ref" in schema:
            self._validate(instance, self._resolve(schema["$ref"]), path, errors)

        if "type" in schema:
            declared = schema["type"]
            names = [declared] if isinstance(declared, str) else list(declared)
            if not any(self._matches_type(instance, name, path) for name in names):
                errors.append(
                    f"{path}: expected type {' | '.join(names)}, "
                    f"got {type(instance).__name__}"
                )

        if "const" in schema and not _json_equal(instance, schema["const"]):
            errors.append(
                f"{path}: expected const {schema['const']!r}, got {instance!r}"
            )

        if "enum" in schema:
            allowed = schema["enum"]
            if not any(_json_equal(instance, item) for item in allowed):
                errors.append(f"{path}: {instance!r} is not one of {allowed!r}")

        if (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
            and "minimum" in schema
            and instance < schema["minimum"]
        ):
            errors.append(f"{path}: {instance!r} is below minimum {schema['minimum']!r}")

        if (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
            and "maximum" in schema
            and instance > schema["maximum"]
        ):
            errors.append(f"{path}: {instance!r} is above maximum {schema['maximum']!r}")

        if isinstance(instance, str) and "pattern" in schema:
            if re.search(schema["pattern"], instance) is None:
                errors.append(
                    f"{path}: {instance!r} does not match {schema['pattern']!r}"
                )

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")

            properties = schema.get("properties", {})
            for key, subschema in properties.items():
                if key in instance:
                    self._validate(
                        instance[key], subschema, f"{path}.{key}", errors
                    )

            if schema.get("additionalProperties") is False:
                for key in instance:
                    if key not in properties:
                        errors.append(
                            f"{path}: additional property {key!r} is not allowed"
                        )
            elif isinstance(schema.get("additionalProperties"), dict):
                for key, value in instance.items():
                    if key not in properties:
                        self._validate(
                            value,
                            schema["additionalProperties"],
                            f"{path}.{key}",
                            errors,
                        )

            if isinstance(schema.get("propertyNames"), dict):
                for key in instance:
                    self._validate(
                        key,
                        schema["propertyNames"],
                        f"{path}.<propertyName:{key}>",
                        errors,
                    )

        if isinstance(instance, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                self._validate(
                    item, schema["items"], f"{path}[{index}]", errors
                )

        if "oneOf" in schema:
            matches = 0
            branch_errors: list[str] = []
            for index, subschema in enumerate(schema["oneOf"]):
                local: list[str] = []
                self._validate(instance, subschema, f"{path}<oneOf:{index}>", local)
                if not local:
                    matches += 1
                else:
                    branch_errors.extend(local)
            if matches != 1:
                errors.append(
                    f"{path}: oneOf matched {matches} branches (expected 1); "
                    f"first branch error: {branch_errors[0] if branch_errors else 'none'}"
                )

        if "anyOf" in schema:
            for subschema in schema["anyOf"]:
                local = []
                self._validate(instance, subschema, f"{path}<anyOf>", local)
                if not local:
                    break
            else:
                errors.append(f"{path}: no anyOf branch matched")

        if "allOf" in schema:
            for index, subschema in enumerate(schema["allOf"]):
                self._validate(instance, subschema, f"{path}<allOf:{index}>", errors)

    def _matches_type(self, value: Any, name: str, path: str) -> bool:
        if name == "null":
            return value is None
        if name == "boolean":
            return isinstance(value, bool)
        if name == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if name == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if name == "string":
            return isinstance(value, str)
        if name == "array":
            return isinstance(value, list)
        if name == "object":
            return isinstance(value, dict)
        raise UnsupportedSchemaKeywordError(
            f"{self.name}: unsupported type token {name!r} at {path}"
        )


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality that keeps booleans distinct from 0/1."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return left == right


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_capability_validator(path: Path = CAPABILITY_SCHEMA_PATH) -> RestrictedSchemaValidator:
    try:
        label = str(path.relative_to(REPO))
    except ValueError:
        label = str(path)
    return RestrictedSchemaValidator(load_json(path), name=label)


CAPABILITY_VALIDATOR = load_capability_validator()
TASK_BINDING_VALIDATOR = RestrictedSchemaValidator(
    load_json(TASK_BINDING_SCHEMA_PATH), name=str(TASK_BINDING_SCHEMA_PATH.name)
)
ERRORS_VALIDATOR = RestrictedSchemaValidator(
    load_json(ERRORS_SCHEMA_PATH), name=str(ERRORS_SCHEMA_PATH.name)
)

_CAPABILITY_SCHEMA = load_json(CAPABILITY_SCHEMA_PATH)
CAPABILITY_FIELDS = frozenset(_CAPABILITY_SCHEMA["properties"])
EXECUTION_SEMANTICS_FIELDS = frozenset(
    _CAPABILITY_SCHEMA["$defs"]["executionSemantics"]["properties"]
)


# ── contract-level admission harness (not a production registry) ─────────


@dataclass(frozen=True)
class Admission:
    execution_eligible: bool
    error_code: str | None
    reason: str | None
    unsupported_execution_fields: tuple[str, ...] = ()


def unsupported_execution_fields(payload: Any) -> tuple[str, ...]:
    """Return undeclared keys on a canonical new-execution capability payload."""
    if not isinstance(payload, dict):
        return ()
    found: list[str] = []
    for key in payload:
        if key not in CAPABILITY_FIELDS:
            found.append(key)
    semantics = payload.get("execution_semantics")
    if isinstance(semantics, dict):
        for key in semantics:
            if key not in EXECUTION_SEMANTICS_FIELDS:
                found.append(f"execution_semantics.{key}")
    return tuple(sorted(found))


def assess_capability(payload: Any) -> Admission:
    """Contract-level admission verdict.

    ``execution_eligible`` means "delegable through a registry binding"; it
    says nothing about installation, availability or scientific verification.
    """
    if not isinstance(payload, dict):
        return Admission(False, "invalid_scientific_input", "payload is not an object")
    unknown = unsupported_execution_fields(payload)
    if unknown:
        return Admission(
            False,
            None,
            "unsupported_execution_field:" + ",".join(unknown),
            unknown,
        )
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        return Admission(
            False,
            "unknown_major_version",
            f"declared schema_version {payload.get('schema_version')!r}; "
            "supported versions: [1]",
        )
    errors = CAPABILITY_VALIDATOR.validate(payload)
    if errors:
        return Admission(False, "invalid_scientific_input", errors[0])
    capability_id = payload["capability_id"]
    implementation_id = payload["implementation_id"]
    registered = KNOWN_IMPLEMENTATIONS.get(capability_id)
    if registered != implementation_id:
        return Admission(
            False,
            "unknown_implementation",
            f"no registry entry for capability {capability_id!r} with "
            f"implementation {implementation_id!r}",
        )
    return Admission(True, None, None)


def assess_capability_corpus(descriptors: Any) -> Admission:
    """Validate every descriptor, then report a capability-id collection conflict."""
    if not isinstance(descriptors, list) or not descriptors:
        return Admission(False, "invalid_scientific_input", "corpus must be non-empty")
    for index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict):
            return Admission(
                False, "invalid_scientific_input", f"descriptor[{index}] is not an object"
            )
        if descriptor.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
            return Admission(False, "unknown_major_version", f"descriptor[{index}]")
        errors = CAPABILITY_VALIDATOR.validate(descriptor)
        if errors:
            return Admission(False, "invalid_scientific_input", errors[0])
    seen: dict[str, str] = {}
    for descriptor in descriptors:
        capability_id = descriptor["capability_id"]
        implementation_id = descriptor["implementation_id"]
        if capability_id in seen:
            return Admission(
                False,
                "duplicate_capability_id",
                f"{capability_id!r} declared by {seen[capability_id]!r} and "
                f"{implementation_id!r}",
            )
        seen[capability_id] = implementation_id
    for descriptor in descriptors:
        verdict = assess_capability(descriptor)
        if not verdict.execution_eligible:
            return verdict
    return Admission(True, None, None)


def historical_read(payload: Any) -> dict[str, Any]:
    """Read a historical payload, preserving unknown fields without eligibility."""
    return {
        "execution_eligible": False,
        "payload": deepcopy(payload),
        "readable": True,
        "recovery_eligible": False,
        "unknown_fields": unsupported_execution_fields(payload),
    }


def make_error_record(code: str, detail: dict[str, Any], message: str) -> dict[str, Any]:
    layer = {
        "unknown_major_version": "structure",
        "duplicate_capability_id": "scientific",
        "unknown_implementation": "scientific",
        "disabled_provider": "platform",
        "unavailable_binary": "platform",
        "invalid_scientific_input": "scientific",
    }[code]
    return {
        "code": code,
        "detail": detail,
        "detail_required": True,
        "layer": layer,
        "message": message,
        "path": "$.capability",
        "retryable": False,
    }


# ── restricted-helper self-proof (first-class failure test) ──────────────


def test_capability_schema_unsupported_keyword_is_hard_error(tmp_path: Path) -> None:
    """An unsupported constraint keyword must fail, not be silently skipped."""
    assert "dependencies" not in RestrictedSchemaValidator.SUPPORTED_CONSTRAINT_KEYWORDS
    schema = load_json(CAPABILITY_SCHEMA_PATH)
    schema["dependencies"] = {"capability_id": ["implementation_id"]}
    temporary = tmp_path / "capability.schema.tmp.json"
    temporary.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert temporary.exists()
    try:
        with pytest.raises(UnsupportedSchemaKeywordError):
            RestrictedSchemaValidator(deepcopy(schema))
        with pytest.raises(UnsupportedSchemaKeywordError):
            load_capability_validator(temporary)
    finally:
        temporary.unlink()
    assert not temporary.exists()


# ── positive corpus ──────────────────────────────────────────────────────


def test_capability_valid_fixture_accepted() -> None:
    payload = load_json(FIXTURES / "capability_valid.json")
    assert CAPABILITY_VALIDATOR.validate(payload) == []
    verdict = assess_capability(payload)
    assert verdict.execution_eligible is True
    assert verdict.error_code is None
    # A described capability is not installed, available or verified.
    assert payload["projections"]["described"]["state"] == "present"
    assert payload["projections"]["installed"]["state"] == "unknown"
    assert payload["projections"]["available"]["state"] == "unknown"
    assert payload["projections"]["verified"]["state"] == "not_verified"


def test_capability_example_accepted() -> None:
    payload = load_json(EXAMPLES / "capability-energy.json")
    assert CAPABILITY_VALIDATOR.validate(payload) == []
    assert assess_capability(payload).execution_eligible is True


def test_task_binding_singlepoint_example_valid() -> None:
    payload = load_json(EXAMPLES / "task-binding-singlepoint.json")
    assert TASK_BINDING_VALIDATOR.validate(payload) == []
    metadata = payload["binding_output"]["binding_metadata"]
    assert metadata["selection_mode"] == "capability"
    assert metadata["capability_id"] == "electronic.energy"
    assert metadata["delegation_target"] == "cccp.calculation.run_singlepoint"
    assert metadata["context_serialized"] is False
    assert payload["context_contract"]["serialization_rule"] == "runtime_only_never_json"
    # The context is never a field, at any top level.
    assert "context" not in payload
    assert "TaskContext" not in payload
    assert "task_context" not in payload
    # The serialized request is opaque to this schema but is a real v1 shape.
    request = payload["binding_output"]["request"]
    assert request["schema_version"] == 1
    assert request["task"] == "singlepoint"
    assert payload["request_contract"]["schema_version"] == 1
    assert payload["result_contract"]["schema_version"] == 1


# ── negative corpus: named outcomes ─────────────────────────────────────


def test_capability_unknown_version_is_unknown_major_version() -> None:
    payload = load_json(FIXTURES / "capability_unknown_version.json")
    assert not CAPABILITY_VALIDATOR.is_valid(payload)
    verdict = assess_capability(payload)
    assert verdict.execution_eligible is False
    assert verdict.error_code == "unknown_major_version"
    record = make_error_record(
        "unknown_major_version",
        {
            "contract_id": "capability.schema.json",
            "declared_schema_version": payload["schema_version"],
            "supported_schema_versions": [1],
        },
        "Unsupported capability major version; never executed.",
    )
    assert ERRORS_VALIDATOR.validate(record) == []


def test_capability_duplicate_id_is_duplicate_capability_id() -> None:
    corpus = load_json(FIXTURES / "capability_duplicate_id.json")
    descriptors = corpus["descriptors"]
    assert len(descriptors) == 2
    for descriptor in descriptors:
        assert CAPABILITY_VALIDATOR.validate(descriptor) == []
    assert descriptors[0]["capability_id"] == descriptors[1]["capability_id"]
    verdict = assess_capability_corpus(descriptors)
    assert verdict.execution_eligible is False
    assert verdict.error_code == "duplicate_capability_id"
    record = make_error_record(
        "duplicate_capability_id",
        {
            "capability_id": descriptors[0]["capability_id"],
            "conflicting_implementation_id": descriptors[1]["implementation_id"],
            "existing_implementation_id": descriptors[0]["implementation_id"],
        },
        "Two descriptors declare the same capability id.",
    )
    assert ERRORS_VALIDATOR.validate(record) == []


def test_capability_unsupported_provider_is_unknown_implementation() -> None:
    payload = load_json(FIXTURES / "capability_unsupported_provider.json")
    assert CAPABILITY_VALIDATOR.validate(payload) == []
    verdict = assess_capability(payload)
    assert verdict.execution_eligible is False
    assert verdict.error_code == "unknown_implementation"
    record = make_error_record(
        "unknown_implementation",
        {
            "capability_id": payload["capability_id"],
            "implementation_id": payload["implementation_id"],
        },
        "The declared implementation is not registered.",
    )
    assert ERRORS_VALIDATOR.validate(record) == []


def test_capability_unknown_execution_field_rejected_for_execution() -> None:
    payload = load_json(FIXTURES / "capability_unknown_execution_field.json")
    assert unsupported_execution_fields(payload) == (
        "execution_semantics.retry_policy",
    )
    assert not CAPABILITY_VALIDATOR.is_valid(payload)
    verdict = assess_capability(payload)
    assert verdict.execution_eligible is False
    assert verdict.unsupported_execution_fields == (
        "execution_semantics.retry_policy",
    )
    assert verdict.reason is not None
    assert verdict.reason.startswith(
        "unsupported_execution_field:execution_semantics.retry_policy"
    )


def test_capability_historical_unknown_field_readable_but_not_eligible() -> None:
    payload = load_json(FIXTURES / "historical_read_unknown_field.json")
    # Canonical new-execution validation is strict.
    assert not CAPABILITY_VALIDATOR.is_valid(payload)
    record = historical_read(payload)
    assert record["readable"] is True
    assert record["payload"] == payload
    assert "legacy_notes" in record["unknown_fields"]
    assert "execution_semantics.legacy_runtime_hint" in record["unknown_fields"]
    # Unknown fields buy readability, never execution or recovery.
    assert record["execution_eligible"] is False
    assert record["recovery_eligible"] is False


def test_capability_reserved_task_name_rejected() -> None:
    base = load_json(FIXTURES / "capability_valid.json")
    for task_kind in sorted(TASK_KINDS):
        candidate = deepcopy(base)
        candidate["capability_id"] = task_kind
        assert not CAPABILITY_VALIDATOR.is_valid(candidate), task_kind
        assert assess_capability(candidate).execution_eligible is False, task_kind


# ── errors contract ──────────────────────────────────────────────────────


def test_error_records_validate_against_errors_schema() -> None:
    schema = load_json(ERRORS_SCHEMA_PATH)
    assert set(schema["error_catalog"]) == FROZEN_ERROR_CODES
    assert set(schema["properties"]["code"]["enum"]) == FROZEN_ERROR_CODES
    for code, entry in sorted(schema["error_catalog"].items()):
        assert ERRORS_VALIDATOR.validate(entry["example"]) == [], code
    records = [
        make_error_record(
            "unknown_major_version",
            {
                "contract_id": "capability.schema.json",
                "declared_schema_version": 2,
                "supported_schema_versions": [1],
            },
            "Unsupported major version.",
        ),
        make_error_record(
            "duplicate_capability_id",
            {
                "capability_id": "electronic.energy",
                "conflicting_implementation_id": "example.science.singlepoint",
                "existing_implementation_id": "cccp.calculation.tasks.singlepoint",
            },
            "Duplicate capability id.",
        ),
        make_error_record(
            "unknown_implementation",
            {
                "capability_id": "electronic.energy",
                "implementation_id": "example.science.singlepoint",
            },
            "Unknown implementation.",
        ),
    ]
    for record in records:
        assert ERRORS_VALIDATOR.validate(record) == [], record["code"]
    malformed = deepcopy(records[0])
    malformed["code"] = "not_a_frozen_code"
    assert not ERRORS_VALIDATOR.is_valid(malformed)


# ── fixture verdict table (QA artifact) ──────────────────────────────────


def _fixture_verdict(name: str) -> tuple[str, str]:
    if name == "historical_read_unknown_field.json":
        record = historical_read(load_json(FIXTURES / name))
        readable = record["readable"] and not record["execution_eligible"]
        return ("readable_not_eligible" if readable else "FAIL", "none")
    if name == "capability_duplicate_id.json":
        corpus = load_json(FIXTURES / name)
        verdict = assess_capability_corpus(corpus["descriptors"])
        return ("rejected", verdict.error_code or "none")
    payload = load_json(FIXTURES / name)
    verdict = assess_capability(payload)
    if verdict.execution_eligible:
        return ("accepted", "none")
    code = verdict.error_code
    if code is None and verdict.reason is not None:
        code = verdict.reason.split(":", 1)[0]
    return ("rejected", code or "none")


def test_capability_fixture_verdict_table() -> None:
    expected = {
        "capability_valid.json": ("accepted", "none"),
        "capability_unknown_version.json": ("rejected", "unknown_major_version"),
        "capability_duplicate_id.json": ("rejected", "duplicate_capability_id"),
        "capability_unsupported_provider.json": (
            "rejected",
            "unknown_implementation",
        ),
        "capability_unknown_execution_field.json": (
            "rejected",
            "unsupported_execution_field",
        ),
        "historical_read_unknown_field.json": ("readable_not_eligible", "none"),
    }
    actual = {name: _fixture_verdict(name) for name in expected}
    print("\nACP2 A-01b capability fixture verdicts")
    print(f"{'fixture':44s} {'verdict':24s} error")
    for name, (verdict, code) in actual.items():
        print(f"{name:44s} {verdict:24s} {code}")
    assert actual == expected


# ── handoff H1 ───────────────────────────────────────────────────────────


def test_handoff_h1_lists_every_contract_file_with_digests() -> None:
    handoff = load_json(HANDOFF_PATH)
    assert handoff["handoff_id"] == "H1"
    assert handoff["handoff_version"] == 1
    entries = {entry["path"]: entry for entry in handoff["contract_files"]}
    expected_paths = {
        str((SCIENCE / name).relative_to(REPO)) for name in CONTRACT_FILE_NAMES
    }
    assert set(entries) == expected_paths
    for path, entry in entries.items():
        absolute = REPO / path
        assert absolute.is_file(), path
        assert entry["sha256"] == hashlib.sha256(absolute.read_bytes()).hexdigest(), path
        assert isinstance(entry["version"], int) and entry["version"] >= 1, path
        assert entry["field_owner"], path
        assert isinstance(entry["fields"], dict), path
        for field_name, field in entry["fields"].items():
            assert set(field) >= {
                "required",
                "optional",
                "default",
                "enums",
                "enum_values",
                "unit",
                "rejection_condition",
                "serialization_form",
                "owner",
            }, (path, field_name)
            assert field["required"] is not field["optional"], (path, field_name)
            assert isinstance(field["enums"], list), (path, field_name)
            assert field["enum_values"] == field["enums"], (path, field_name)
            assert field["rejection_condition"], (path, field_name)
            assert field["serialization_form"], (path, field_name)
        if entry["kind"] == "json_schema":
            schema = load_json(absolute)
            assert set(entry["fields"]) == set(schema["properties"]), path
            assert set(schema["required"]) <= set(entry["fields"]), path
        elif entry["kind"] == "example_instance":
            instance = load_json(absolute)
            assert set(entry["fields"]) == set(instance), path
    assert handoff["positive_fixtures"]
    assert handoff["negative_fixtures"]
    assert handoff["compatibility_scope"]["declared_major"] == 1
    assert handoff["compatibility_scope"]["accepted_schema_versions"] == [1]
    assert handoff["unit_declarations"]
    for declaration in handoff["unit_declarations"]:
        assert declaration["unit"], declaration
    for fixture in handoff["positive_fixtures"] + handoff["negative_fixtures"]:
        assert (REPO / fixture["path"]).is_file(), fixture["path"]
        if fixture.get("sha256") is not None:
            assert fixture["sha256"] == hashlib.sha256(
                (REPO / fixture["path"]).read_bytes()
            ).hexdigest(), fixture["path"]


def test_receipt_samples_deferred_until_b01_publishes() -> None:
    handoff = load_json(HANDOFF_PATH)
    gap = handoff["receipt_protocol_gap"]
    assert gap["status"] == "NOT_VERIFIED"
    assert gap["missing_artifact"] == "acceptance-result.schema.json"
    assert gap["missing_contract"] == "acp2.acceptance.v1"
    assert gap["samples_delivered"] is False
    assert not (EXAMPLES / "receipt-valid.json").exists()
    assert not (EXAMPLES / "receipt-invalid.json").exists()
