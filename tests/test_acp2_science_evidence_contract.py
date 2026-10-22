# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""ACP2 evidence/provenance contract test (Plan A todo 8 / A-03b).

Standard library only for schema assertion: ``json`` plus a **restricted**
draft-2020-12 assertion helper. ``jsonschema`` is deliberately absent and must
never be imported here. The helper declares its supported keyword set up front
and raises :class:`UnsupportedEvidenceSchemaKeywordError` on anything else; it
never silently skips a declared constraint.

Named contract outcomes exercised here:

* ``missing_source_for_supported_claim``  -> ``supported`` needs a digested source
* ``digest_mismatch``                     -> the declared digest is checked on disk
* ``partial_output_cannot_support``       -> a blocking partial-output check blocks support
* ``unknown_claim_status``                -> only the frozen four statuses are legal
* ``model_text_is_not_a_validator``       -> free prose / ``llm:`` is never a validator

Positive cases: a properly digested ``supported`` claim, the canonical
``completed-but-inconclusive`` report, and a historical v0 payload that the
adapter reads while preserving unknown fields without granting execution or
recovery eligibility.

The digest check reads the synthetic artifacts under ``evidence/artifacts/``
and compares the bare lowercase sha256, exactly as
``cccp.analysis.identity.content_sha256`` would. These files are contract
fixtures, never calculation products.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
EVIDENCE_SCHEMA_PATH = SCIENCE / "evidence.schema.json"
PROVENANCE_SCHEMA_PATH = SCIENCE / "provenance.schema.json"
EVIDENCE_FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "contracts" / "evidence"

CLAIM_STATUSES = ("supported", "refuted", "inconclusive", "not_evaluated")
VALIDATOR_KIND_NAMESPACE = {
    "deterministic": "check.",
    "software": "software.",
    "human_review": "human.",
}
VALIDATOR_ID_PATTERN = re.compile(r"^(?:check|software|human)\.[a-z0-9][a-z0-9_.-]*$")
VALIDATOR_VERSION_PATTERN = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


# ── restricted draft-2020-12 assertion helper ────────────────────────────


class UnsupportedEvidenceSchemaKeywordError(AssertionError):
    """Raised when a schema uses a keyword the restricted helper cannot enforce."""


class RestrictedEvidenceValidator:
    """Enforce the bounded draft-2020-12 subset actually used by evidence.schema.json.

    ``SUPPORTED_CONSTRAINT_KEYWORDS`` is declared up front. Any other
    constraint keyword raises immediately during construction; nothing is
    ignored. ``schema_version`` at the document root is an annotation (the
    contract file revision), while ``properties.schema_version.const`` remains a
    real constraint on the instance payload.
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
            "exclusiveMinimum",
            "format",
            "if",
            "items",
            "minItems",
            "minLength",
            "minimum",
            "not",
            "pattern",
            "properties",
            "required",
            "then",
            "type",
        }
    )
    ANNOTATION_KEYWORDS = frozenset(
        {
            "$comment",
            "$id",
            "$schema",
            "description",
            "schema_version",
            "title",
        }
    )

    def __init__(self, schema: dict[str, Any], *, name: str = "<schema>") -> None:
        self.root = schema
        self.name = name
        self.assert_supported_keywords(schema, "#")

    def assert_supported_keywords(self, schema: Any, path: str) -> None:
        if not isinstance(schema, dict):
            return
        for keyword, value in schema.items():
            if keyword in self.ANNOTATION_KEYWORDS:
                continue
            if keyword not in self.SUPPORTED_CONSTRAINT_KEYWORDS:
                raise UnsupportedEvidenceSchemaKeywordError(
                    f"{self.name}: unsupported schema keyword {keyword!r} at {path}"
                )
            child = f"{path}/{keyword}"
            if keyword == "$defs":
                if not isinstance(value, dict):
                    raise UnsupportedEvidenceSchemaKeywordError(
                        f"{self.name}: $defs must be an object at {path}"
                    )
                for def_name, subschema in value.items():
                    self.assert_supported_keywords(subschema, f"{child}/{def_name}")
            elif keyword == "properties":
                if not isinstance(value, dict):
                    raise UnsupportedEvidenceSchemaKeywordError(
                        f"{self.name}: properties must be an object at {path}"
                    )
                for prop_name, subschema in value.items():
                    self.assert_supported_keywords(subschema, f"{child}/{prop_name}")
            elif keyword in {"additionalProperties", "items"}:
                if isinstance(value, dict):
                    self.assert_supported_keywords(value, child)
                elif keyword == "items" and not isinstance(value, bool):
                    raise UnsupportedEvidenceSchemaKeywordError(
                        f"{self.name}: tuple-form items are not supported at {path}"
                    )
            elif keyword in {"allOf", "anyOf"}:
                if not isinstance(value, list):
                    raise UnsupportedEvidenceSchemaKeywordError(
                        f"{self.name}: {keyword} must be an array at {path}"
                    )
                for index, subschema in enumerate(value):
                    self.assert_supported_keywords(subschema, f"{child}/{index}")
            elif keyword in {"if", "then", "not"}:
                if isinstance(value, dict):
                    self.assert_supported_keywords(value, child)

    def validate(self, instance: Any) -> list[str]:
        errors: list[str] = []
        self._validate(instance, self.root, "$", errors)
        return errors

    def is_valid(self, instance: Any) -> bool:
        return not self.validate(instance)

    def _resolve(self, ref: str) -> dict[str, Any]:
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise UnsupportedEvidenceSchemaKeywordError(
                f"{self.name}: only local $ref values are supported, got {ref!r}"
            )
        pointer = ref[1:]
        node: Any = self.root
        if pointer in {"", "/"}:
            if not isinstance(node, dict):
                raise UnsupportedEvidenceSchemaKeywordError(
                    f"{self.name}: $ref {ref!r} does not resolve to an object"
                )
            return node
        for token in pointer.lstrip("/").split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise UnsupportedEvidenceSchemaKeywordError(f"{self.name}: unresolved $ref {ref!r}")
            node = node[key]
        if not isinstance(node, dict):
            raise UnsupportedEvidenceSchemaKeywordError(
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
                    f"{path}: expected type {' | '.join(names)}, got {type(instance).__name__}"
                )

        if "const" in schema and not _json_equal(instance, schema["const"]):
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

        if "enum" in schema:
            allowed = schema["enum"]
            if not any(_json_equal(instance, item) for item in allowed):
                errors.append(f"{path}: {instance!r} is not one of {allowed!r}")

        if isinstance(instance, str):
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")
            if "minLength" in schema and len(instance) < schema["minLength"]:
                errors.append(
                    f"{path}: length {len(instance)} is below minLength {schema['minLength']}"
                )
            if schema.get("format") == "date-time" and DATETIME_PATTERN.match(instance) is None:
                errors.append(f"{path}: {instance!r} is not an RFC 3339 date-time")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: {instance!r} is below minimum {schema['minimum']!r}")
            if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
                errors.append(
                    f"{path}: {instance!r} is not above exclusiveMinimum "
                    f"{schema['exclusiveMinimum']!r}"
                )

        if isinstance(instance, dict):
            for key in schema.get("required", []):
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")

            properties = schema.get("properties", {})
            for key, subschema in properties.items():
                if key in instance:
                    self._validate(instance[key], subschema, f"{path}.{key}", errors)

            if schema.get("additionalProperties") is False:
                for key in instance:
                    if key not in properties:
                        errors.append(f"{path}: additional property {key!r} is not allowed")

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                errors.append(
                    f"{path}: {len(instance)} items is below minItems {schema['minItems']}"
                )
            if isinstance(schema.get("items"), dict):
                for index, item in enumerate(instance):
                    self._validate(item, schema["items"], f"{path}[{index}]", errors)

        if "allOf" in schema:
            for index, subschema in enumerate(schema["allOf"]):
                self._validate(instance, subschema, f"{path}<allOf:{index}>", errors)

        if "anyOf" in schema:
            for subschema in schema["anyOf"]:
                local: list[str] = []
                self._validate(instance, subschema, f"{path}<anyOf>", local)
                if not local:
                    break
            else:
                errors.append(f"{path}: no anyOf branch matched")

        if isinstance(schema.get("not"), dict):
            local = []
            self._validate(instance, schema["not"], f"{path}<not>", local)
            if not local:
                errors.append(f"{path}: value must not match the 'not' schema")

        if "if" in schema:
            local: list[str] = []
            self._validate(instance, schema["if"], f"{path}<if>", local)
            if not local and isinstance(schema.get("then"), dict):
                self._validate(instance, schema["then"], path, errors)

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
        raise UnsupportedEvidenceSchemaKeywordError(
            f"{self.name}: unsupported type token {name!r} at {path}"
        )


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality that keeps booleans distinct from 0/1."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


EVIDENCE_SCHEMA = load_json(EVIDENCE_SCHEMA_PATH)
PROVENANCE_SCHEMA = load_json(PROVENANCE_SCHEMA_PATH)
EVIDENCE_VALIDATOR = RestrictedEvidenceValidator(
    EVIDENCE_SCHEMA, name=str(EVIDENCE_SCHEMA_PATH.relative_to(REPO))
)
PROVENANCE_VALIDATOR = RestrictedEvidenceValidator(
    PROVENANCE_SCHEMA, name=str(PROVENANCE_SCHEMA_PATH.relative_to(REPO))
)


# ── scientific evidence semantics (contract-level, not production code) ──


class EvidenceContractError(AssertionError):
    """Named evidence-contract rejection. ``code`` is asserted by the tests."""

    code = "evidence_contract_error"

    def __init__(self, message: str) -> None:
        super().__init__(f"[{self.code}] {message}")


class MissingSourceForSupportedClaimError(EvidenceContractError):
    code = "missing_source_for_supported_claim"


class DigestMismatchError(EvidenceContractError):
    code = "digest_mismatch"


class PartialOutputCannotSupportError(EvidenceContractError):
    code = "partial_output_cannot_support"


class BlockingMissingCheckCannotSupportError(EvidenceContractError):
    code = "blocking_missing_check_cannot_support"


class UnknownClaimStatusError(EvidenceContractError):
    code = "unknown_claim_status"


class ModelTextIsNotValidatorError(EvidenceContractError):
    code = "model_text_is_not_a_validator"


class ValidatorShapeError(EvidenceContractError):
    code = "invalid_validator"


class NotEvaluatedRequiresMissingCheckError(EvidenceContractError):
    code = "not_evaluated_requires_missing_check"


class MissingLimitationReferenceError(EvidenceContractError):
    code = "missing_limitation_reference"


def assert_validator_admissible(validator: Any) -> None:
    """A validator is a namespace-registered check, never narrative model text."""
    if not isinstance(validator, dict):
        raise ModelTextIsNotValidatorError(
            f"validator must be an object, got {type(validator).__name__}"
        )
    validator_id = validator.get("validator_id")
    kind = validator.get("kind")
    version = validator.get("validator_version")
    if not isinstance(validator_id, str) or VALIDATOR_ID_PATTERN.match(validator_id) is None:
        raise ModelTextIsNotValidatorError(
            f"validator_id {validator_id!r} is free prose/model text, not a "
            "check./software./human. identifier"
        )
    if kind not in VALIDATOR_KIND_NAMESPACE:
        raise ModelTextIsNotValidatorError(
            f"validator kind {kind!r} is not deterministic/software/human_review"
        )
    expected_prefix = VALIDATOR_KIND_NAMESPACE[kind]
    if not validator_id.startswith(expected_prefix):
        raise ModelTextIsNotValidatorError(
            f"validator_id {validator_id!r} does not match kind {kind!r} "
            f"(expected {expected_prefix}*)"
        )
    if not isinstance(version, str) or VALIDATOR_VERSION_PATTERN.match(version) is None:
        raise ValidatorShapeError(f"validator_version {version!r} is not a non-empty version token")


def verify_source_digest(fixture_root: Path, source_ref: dict[str, Any]) -> None:
    """Recompute the bare sha256 of the referenced artifact and compare."""
    artifact_path = source_ref.get("artifact_path")
    digest = source_ref.get("digest")
    if not isinstance(artifact_path, str) or not isinstance(digest, dict):
        raise MissingSourceForSupportedClaimError(
            "a source ref needs an artifact_path and a digest object"
        )
    declared = digest.get("value")
    if not isinstance(declared, str) or DIGEST_PATTERN.match(declared) is None:
        raise MissingSourceForSupportedClaimError(
            f"declared digest {declared!r} is missing or malformed"
        )
    root = fixture_root.resolve()
    candidate = (root / artifact_path).resolve()
    if root not in candidate.parents and candidate != root:
        raise MissingSourceForSupportedClaimError(
            f"artifact path {artifact_path!r} escapes the evidence root"
        )
    if not candidate.is_file():
        raise MissingSourceForSupportedClaimError(f"artifact {artifact_path!r} is absent")
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != declared:
        raise DigestMismatchError(f"{artifact_path}: declared {declared}, actual {actual}")


def check_claim(
    claim: dict[str, Any],
    fixture_root: Path,
    limitation_ids: frozenset[str],
) -> None:
    status = claim.get("status")
    if status not in CLAIM_STATUSES:
        raise UnknownClaimStatusError(
            f"claim {claim.get('claim_id')!r} declares status {status!r}; allowed: {CLAIM_STATUSES}"
        )

    if "validator" in claim:
        assert_validator_admissible(claim["validator"])

    source_refs = claim.get("source_refs", [])
    missing_checks = claim.get("missing_checks", [])

    if status in {"supported", "refuted"}:
        if "validator" not in claim:
            raise ModelTextIsNotValidatorError(
                f"claim {claim.get('claim_id')!r} is {status} without a validator"
            )
        if not isinstance(source_refs, list) or not source_refs:
            raise MissingSourceForSupportedClaimError(
                f"claim {claim.get('claim_id')!r} is {status} without a source ref "
                "(missing IRC/frequency evidence)"
            )
        for source_ref in source_refs:
            verify_source_digest(fixture_root, source_ref)
        blocking = [
            check
            for check in missing_checks
            if isinstance(check, dict) and check.get("blocking") is True
        ]
        if any(check.get("check_id") == "partial_output" for check in blocking):
            raise PartialOutputCannotSupportError(
                f"claim {claim.get('claim_id')!r} is {status} while the output is partial"
            )
        if blocking:
            raise BlockingMissingCheckCannotSupportError(
                f"claim {claim.get('claim_id')!r} is {status} with blocking missing "
                f"checks {[check.get('check_id') for check in blocking]}"
            )

    if status == "not_evaluated" and not missing_checks:
        raise NotEvaluatedRequiresMissingCheckError(
            f"claim {claim.get('claim_id')!r} is not_evaluated without listing what was missing"
        )

    for limitation_id in claim.get("limitations", []):
        if limitation_id not in limitation_ids:
            raise MissingLimitationReferenceError(
                f"claim {claim.get('claim_id')!r} references unknown limitation {limitation_id!r}"
            )


def check_evidence_report(report: dict[str, Any], fixture_root: Path) -> None:
    limitation_ids = frozenset(
        limitation["limitation_id"]
        for limitation in report.get("limitations", [])
        if isinstance(limitation, dict) and isinstance(limitation.get("limitation_id"), str)
    )
    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        raise EvidenceContractError("an evidence report needs at least one claim")
    for claim in claims:
        if not isinstance(claim, dict):
            raise EvidenceContractError("a claim must be an object")
        check_claim(claim, fixture_root, limitation_ids)


# ── historical adapter ───────────────────────────────────────────────────


def _resolve_schema(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    current = schema
    while "$ref" in current:
        ref = current["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise UnsupportedEvidenceSchemaKeywordError(
                f"historical adapter only resolves local refs, got {ref!r}"
            )
        node: Any = root
        for token in ref[2:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise UnsupportedEvidenceSchemaKeywordError(f"unresolved $ref {ref!r}")
            node = node[key]
        if not isinstance(node, dict):
            raise UnsupportedEvidenceSchemaKeywordError(
                f"$ref {ref!r} does not resolve to an object"
            )
        current = node
    return current


def collect_unknown_field_paths(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Return every property path not declared by the schema.

    Unknown fields are *preserved read-only*; discovering them is what lets the
    adapter report them without coercing the payload.
    """
    schema = _resolve_schema(schema, root)
    found: list[str] = []
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key not in properties:
                found.append(f"{path}.{key}")
            else:
                found.extend(
                    collect_unknown_field_paths(value, properties[key], root, f"{path}.{key}")
                )
    elif isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                found.extend(collect_unknown_field_paths(item, items, root, f"{path}[{index}]"))
    return found


def historical_read_evidence(payload: Any) -> dict[str, Any]:
    """Read a historical evidence payload without granting any eligibility."""
    return {
        "execution_eligible": False,
        "historical": payload.get("schema_version") != 1,
        "payload": deepcopy(payload),
        "readable": True,
        "recovery_eligible": False,
        "schema_version": payload.get("schema_version"),
        "unknown_fields": collect_unknown_field_paths(payload, EVIDENCE_SCHEMA, EVIDENCE_SCHEMA),
    }


# ── helper self-proof ────────────────────────────────────────────────────


def test_evidence_schema_helper_rejects_unsupported_keyword() -> None:
    """An unsupported constraint keyword must fail, not be skipped."""
    assert "uniqueItems" not in RestrictedEvidenceValidator.SUPPORTED_CONSTRAINT_KEYWORDS
    schema = load_json(EVIDENCE_SCHEMA_PATH)
    schema["uniqueItems"] = True
    with pytest.raises(UnsupportedEvidenceSchemaKeywordError):
        RestrictedEvidenceValidator(schema)


# ── positive corpus ──────────────────────────────────────────────────────


def test_supported_claim_with_digested_source_accepted() -> None:
    report = load_json(EVIDENCE_FIXTURES / "supported_valid.json")
    assert EVIDENCE_VALIDATOR.validate(report) == []
    check_evidence_report(report, EVIDENCE_FIXTURES)
    claim = report["claims"][0]
    assert claim["status"] == "supported"
    assert claim["validator"]["validator_id"] == "check.imaginary_mode_count"
    assert (
        claim["source_refs"][0]["digest"]["value"]
        == hashlib.sha256((EVIDENCE_FIXTURES / "artifacts/freq.out").read_bytes()).hexdigest()
    )


def test_provenance_schemas_parse_and_declare_a_version() -> None:
    assert EVIDENCE_SCHEMA["$id"].startswith("https://acp.local/acp2/science/")
    assert PROVENANCE_SCHEMA["$id"].startswith("https://acp.local/acp2/science/")
    assert EVIDENCE_SCHEMA["schema_version"] == 1
    assert PROVENANCE_SCHEMA["schema_version"] == 1


def test_completed_but_inconclusive_accepted() -> None:
    report = load_json(EVIDENCE_FIXTURES / "completed_but_inconclusive.json")
    assert EVIDENCE_VALIDATOR.validate(report) == []
    check_evidence_report(report, EVIDENCE_FIXTURES)
    assert report["execution"]["terminal_state"] == "completed"
    assert report["execution"]["exit_code"] == 0
    claim = report["claims"][0]
    assert claim["status"] == "inconclusive"
    assert claim["missing_checks"][0]["check_id"] == "missing_irc"
    # Completion is never read as scientific support.
    assert claim["status"] != "supported"


def test_historical_schema_payload_readable_without_eligibility() -> None:
    payload = load_json(EVIDENCE_FIXTURES / "historical_schema_payload.json")
    # Canonical v1 validation is strict: a v0 payload with unknown fields fails.
    assert not EVIDENCE_VALIDATOR.is_valid(payload)
    record = historical_read_evidence(payload)
    assert record["readable"] is True
    assert record["historical"] is True
    assert record["schema_version"] == 0
    # Unknown fields survive byte-for-byte; nothing is filled in.
    assert record["payload"] == payload
    assert record["unknown_fields"] == [
        "$.claims[0].legacy_confidence",
        "$.legacy_review_note",
    ]
    # Historical readability never buys execution or recovery eligibility.
    assert record["execution_eligible"] is False
    assert record["recovery_eligible"] is False


# ── negative corpus: named outcomes ─────────────────────────────────────


def test_supported_with_missing_source_is_rejected() -> None:
    report = load_json(EVIDENCE_FIXTURES / "missing_irc_source.json")
    # The schema's supported branch requires a non-empty source_refs list.
    assert not EVIDENCE_VALIDATOR.is_valid(report)
    with pytest.raises(MissingSourceForSupportedClaimError) as excinfo:
        check_evidence_report(report, EVIDENCE_FIXTURES)
    assert excinfo.value.code == "missing_source_for_supported_claim"


def test_supported_with_digest_mismatch_is_rejected() -> None:
    report = load_json(EVIDENCE_FIXTURES / "digest_mismatch.json")
    # The structure is valid; only the on-disk digest comparison rejects it.
    assert EVIDENCE_VALIDATOR.validate(report) == []
    with pytest.raises(DigestMismatchError) as excinfo:
        check_evidence_report(report, EVIDENCE_FIXTURES)
    assert excinfo.value.code == "digest_mismatch"


def test_partial_output_cannot_support() -> None:
    report = load_json(EVIDENCE_FIXTURES / "partial_output.json")
    # Structurally valid: the semantic floor is what rejects it.
    assert EVIDENCE_VALIDATOR.validate(report) == []
    with pytest.raises(PartialOutputCannotSupportError) as excinfo:
        check_evidence_report(report, EVIDENCE_FIXTURES)
    assert excinfo.value.code == "partial_output_cannot_support"


def test_unknown_claim_status_rejected() -> None:
    report = load_json(EVIDENCE_FIXTURES / "unknown_status.json")
    assert report["claims"][0]["status"] == "confirmed"
    assert not EVIDENCE_VALIDATOR.is_valid(report)
    with pytest.raises(UnknownClaimStatusError) as excinfo:
        check_evidence_report(report, EVIDENCE_FIXTURES)
    assert excinfo.value.code == "unknown_claim_status"


def test_model_text_cannot_impersonate_validator() -> None:
    report = load_json(EVIDENCE_FIXTURES / "model_text_validator.json")
    assert not EVIDENCE_VALIDATOR.is_valid(report)
    with pytest.raises(ModelTextIsNotValidatorError) as excinfo:
        check_evidence_report(report, EVIDENCE_FIXTURES)
    assert excinfo.value.code == "model_text_is_not_a_validator"

    # Free-form model prose is not a validator identifier, and neither is a
    # model/llm kind (the enum has no such member).
    for validator in (
        {
            "kind": "human_review",
            "validator_id": "the model says the barrier is low",
            "validator_version": "1",
        },
        {
            "kind": "deterministic",
            "validator_id": "llm:consensus",
            "validator_version": "1",
        },
        {"kind": "model", "validator_id": "model.gpt", "validator_version": "1"},
        {
            "kind": "software",
            "validator_id": "check.imaginary_mode_count",
            "validator_version": "1.0.0",
        },
    ):
        with pytest.raises(ModelTextIsNotValidatorError):
            assert_validator_admissible(validator)

    # The named namespace forms remain admissible.
    assert_validator_admissible(
        {
            "kind": "deterministic",
            "validator_id": "check.imaginary_mode_count",
            "validator_version": "1.0.0",
        }
    )
    assert_validator_admissible(
        {
            "kind": "software",
            "validator_id": "software.orca",
            "validator_version": "5.0.4",
        }
    )


# ── per-claim field completeness ────────────────────────────────────────


def test_every_claim_carries_status_validator_sources_and_limitations() -> None:
    for name in (
        "supported_valid.json",
        "completed_but_inconclusive.json",
        "partial_output.json",
    ):
        report = load_json(EVIDENCE_FIXTURES / name)
        limitation_ids = {limitation["limitation_id"] for limitation in report["limitations"]}
        for claim in report["claims"]:
            assert isinstance(claim.get("claim_id"), str) and claim["claim_id"]
            assert isinstance(claim.get("statement"), str) and claim["statement"]
            assert "status" in claim, name
            assert isinstance(claim.get("scope"), dict), name
            assert isinstance(claim.get("source_refs"), list), name
            assert isinstance(claim.get("missing_checks"), list), name
            if claim["status"] in {"supported", "refuted"}:
                validator = claim.get("validator")
                assert isinstance(validator, dict), (name, claim["claim_id"])
                assert validator.get("validator_id"), (name, claim["claim_id"])
                assert validator.get("validator_version"), (name, claim["claim_id"])
                assert claim["source_refs"], (name, claim["claim_id"])
            if claim["status"] == "not_evaluated":
                assert claim["missing_checks"], (name, claim["claim_id"])
            for limitation_id in claim.get("limitations", []):
                assert limitation_id in limitation_ids, (name, limitation_id)


# ── fixture verdict table (QA artifact) ─────────────────────────────────


def _fixture_verdict(name: str) -> tuple[str, str]:
    path = EVIDENCE_FIXTURES / name
    payload = load_json(path)
    if name == "historical_schema_payload.json":
        record = historical_read_evidence(payload)
        accepted = (
            record["readable"]
            and record["payload"] == payload
            and not record["execution_eligible"]
            and not record["recovery_eligible"]
            and bool(record["unknown_fields"])
        )
        return ("readable_not_eligible" if accepted else "FAIL", "none")
    if name in {"supported_valid.json", "completed_but_inconclusive.json"}:
        if EVIDENCE_VALIDATOR.is_valid(payload):
            check_evidence_report(payload, EVIDENCE_FIXTURES)
            return ("accepted", "none")
        return ("FAIL", "invalid_schema")
    try:
        check_evidence_report(payload, EVIDENCE_FIXTURES)
    except EvidenceContractError as exc:
        return ("rejected", exc.code)
    return ("FAIL", "no_named_rejection")


def test_evidence_fixture_verdict_table(capsys: pytest.CaptureFixture[str]) -> None:
    expected = {
        "missing_irc_source.json": (
            "rejected",
            "missing_source_for_supported_claim",
        ),
        "digest_mismatch.json": ("rejected", "digest_mismatch"),
        "partial_output.json": ("rejected", "partial_output_cannot_support"),
        "unknown_status.json": ("rejected", "unknown_claim_status"),
        "model_text_validator.json": (
            "rejected",
            "model_text_is_not_a_validator",
        ),
        "supported_valid.json": ("accepted", "none"),
        "completed_but_inconclusive.json": ("accepted", "none"),
        "historical_schema_payload.json": ("readable_not_eligible", "none"),
    }
    actual = {name: _fixture_verdict(name) for name in expected}
    with capsys.disabled():
        print("\nACP2 A-03b evidence fixture verdicts")
        print(f"{'fixture':38s} {'verdict':24s} reason")
        for name, (verdict, code) in actual.items():
            print(f"{name:38s} {verdict:24s} {code}")
        print(
            "model-text impersonation:",
            actual["model_text_validator.json"][1],
        )
    assert actual == expected
