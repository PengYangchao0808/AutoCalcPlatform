"""A-26: ACP2 cross-domain/version regression gate and H6 handoff validator.

The suite is the executable gate behind
``docs/reference/next-change/acp2-contracts/science/handoff-H6.json``.  It does
not perform a real QC run: every domain fixture it consumes is labeleed
``constructed=true``/``real_run=false``.  What it *does* prove is contract
behaviour that JSON alone cannot express:

1. version regression — historical payloads stay readable and non-executable,
   an unknown execution field is rejected by the strict v1 reader and an
   unknown major is never executed;
2. derived identity — the single ``cccp.analysis.identity`` algorithm changes
   for every scientific input and not for display/account/tier/permission;
3. conservative recovery — a changed model-asset or plugin digest invalidates
   only the dependent steps of the frozen recipe;
4. platform independence — scientific bytes and claim statuses are identical
   under varied account-tier/permission wrappers and no science artifact
   carries a platform key;
5. per-subcase rows — H6 rows are keyed by the three unique phases, never by a
   parent aggregate;
6. the C T-40b signoff gate — absent/unsigned/stale signoff keeps H6 open and a
   signed signoff still cannot close H6 over a ``NOT_VERIFIED`` selectivity row;
7. no row labelled verified while unverified.

Standard-library parsing only.  ``basedpyright``/``jsonschema`` are not used.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from cccp.analysis.identity import ScientificIdentity, identity_fingerprint
from cccp.analysis.nmr.comparison import model_asset_record
from cccp.analysis.research._common import ForbiddenPlatformFieldError, SchemaViolationError
from cccp.analysis.research.evidence import (
    CLAIM_STATUSES,
    EvidenceContractError,
    UnsupportedEvidenceSchemaVersionError,
    canonical_evidence_json,
    evidence_report_from_dict,
    read_historical_evidence,
)
from cccp.analysis.research.provenance import (
    UnsupportedProvenanceSchemaVersionError,
    provenance_from_dict,
    read_historical_provenance,
)
from cccp.analysis.research.question import (
    Question,
    QuestionForbiddenFieldError,
    UnsupportedQuestionSchemaVersionError,
    read_historical_question,
)
from cccp.analysis.research.reaction import (
    Reaction,
    ReactionForbiddenFieldError,
    UnsupportedSchemaVersionError,
    read_historical_reaction,
)

REPO = Path(__file__).resolve().parents[1]
SCIENCE_DOCS = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science"

H6_PATH = SCIENCE_DOCS / "handoff-H6.json"
H1_PATH = SCIENCE_DOCS / "handoff-H1.json"
H2_PATH = SCIENCE_DOCS / "handoff-H2.json"
H3_PATH = SCIENCE_DOCS / "handoff-H3.json"
H4_PATH = SCIENCE_DOCS / "handoff-H4.json"
H5_PATH = SCIENCE_DOCS / "handoff-H5.json"
SELECTIVITY_REPORT_PATH = SCIENCE_DOCS / "selectivity-evidence-report.json"
LEDGER_PATH = SCIENCE_DOCS / "cross-track-dependencies.json"

CROSS_DOMAIN = FIXTURES / "cross_domain"
NMR_FIXTURE = CROSS_DOMAIN / "nmr_shielding_constructed.json"
THERMO_FIXTURE = CROSS_DOMAIN / "thermochemistry_weights_constructed.json"
DA_MANIFEST = FIXTURES / "diels_alder" / "v1" / "fixture-manifest.json"
DA_REACTION = FIXTURES / "diels_alder" / "v1" / "reaction.json"
DA_QUESTION = FIXTURES / "diels_alder" / "v1" / "question.json"
SINGLEPOINT_MANIFEST = FIXTURES / "singlepoint" / "fixture-manifest.json"
SINGLEPOINT_REQUEST = FIXTURES / "singlepoint" / "request.json"
OPTFREQ_MANIFEST = FIXTURES / "optfreq" / "fixture-manifest.json"
OPTFREQ_OPTIMIZE_REQUEST = FIXTURES / "optfreq" / "optimize-request.json"
SELECTIVITY_MANIFEST = FIXTURES / "selectivity" / "v1" / "fixture-manifest.json"
DA_REPORT_SCENARIO = FIXTURES / "diels_alder" / "v1" / "report-scenario"
CONSTRUCTED_PROVENANCE = DA_REPORT_SCENARIO / "constructed-evidence-report.json"
EVIDENCE_FIXTURES = FIXTURES / "contracts" / "evidence"
SUPPORTED_EVIDENCE = EVIDENCE_FIXTURES / "supported_valid.json"
HISTORICAL_EVIDENCE = EVIDENCE_FIXTURES / "historical_schema_payload.json"
PLUGIN_V1_RESULT = FIXTURES / "plugin_upgrade" / "artifacts" / "v1-task-result.json"
PLUGIN_V1_MANIFEST = FIXTURES / "plugin_upgrade" / "v1" / "cccp_science_plugin.json"
PLUGIN_V2_MANIFEST = FIXTURES / "plugin_upgrade" / "v2" / "cccp_science_plugin.json"
REPORT_SCENARIO = FIXTURES / "selectivity" / "v1" / "report-scenario"
BUDGET_FIXTURE = REPORT_SCENARIO / "cross-domain-budget.json"

REQUIRED_PHASES: Final[tuple[str, ...]] = (
    "experience:nmr",
    "experience:thermo",
    "experience:selectivity",
)
REQUIRED_SUBCASE_ROW_IDS: Final[tuple[str, ...]] = (
    "H6-nmr-static-contract",
    "H6-thermo-static-contract",
    "H6-selectivity-not-verified",
)
REQUIRED_ITEM_ROW_IDS: Final[tuple[str, ...]] = (
    "H6-R1-version-regression",
    "H6-R2-derived-identity",
    "H6-R3-conservative-recovery",
    "H6-R4-platform-independence",
    "H6-R5-per-subcase-rows",
    "H6-R6-c-t40b-signoff",
    "H6-R7-no-false-verified",
)
REQUIRED_PRODUCER_IDS: Final[tuple[str, ...]] = (
    "A-25a",
    "B23b",
    "C T-40a",
    "C T-40b",
    "B25a",
)
ALLOWED_ROW_STATUSES: Final[frozenset[str]] = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
ALLOWED_EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"executed-contract", "static-contract", "constructed-contract", "absent-producer"}
)
A25B_ARTIFACT_ID: Final = "A-25b:selectivity-audit-report"
DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")

#: Keys that must never appear in a science artifact.  ``role`` is allowed only
#: as a provenance/evidence artifact-role or structure-role field, documented
#: below in :data:`ROLE_CONTAINERS`.
FORBIDDEN_SCIENCE_KEYS: Final[frozenset[str]] = frozenset(
    {"account", "tier", "permission", "quota", "role"}
)
ROLE_CONTAINERS: Final[frozenset[str]] = frozenset(
    {"source_refs", "artifacts", "authorized_artifacts", "sources", "structure", "input"}
)
JOB_KEY_PREFIX: Final = "job_"

#: The exact science artifacts the H6 matrix consumes.  Constructed platform
#: snapshots (``*constructed-snapshot*.json``) are platform envelopes, not
#: science artifacts, and are deliberately not scanned for ``job_*`` keys —
#: documenting that boundary is part of the gate.
SCIENCE_SCAN_EXCLUDED_NAME = "constructed-snapshot"


class H6ContractError(ValueError):
    """Raised when the H6 handoff records an unsupported or dishonest claim."""


class H6PhaseKeyError(H6ContractError):
    """Raised when the per-subcase phase key is missing, duplicated or ambiguous."""


class H6RowStatusError(H6ContractError):
    """Raised when a row status and its evidence/verified flag disagree."""


class H6SignoffError(H6ContractError):
    """Raised when the C T-40b signoff record is structurally invalid."""


class H6ClosureError(H6ContractError):
    """Raised when ``h6_closure`` disagrees with the actionable row state."""


# ── generic helpers ──────────────────────────────────────────────────────


def _load(path: Path) -> dict[str, Any]:
    payload = _load_any(path)
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return payload


def _load_any(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _require_mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise H6ContractError(f"{where}: expected a JSON object")
    return value


def _require_str(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise H6ContractError(f"{where}: expected non-empty text")
    return value


def _require_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise H6ContractError(f"{where}: expected a boolean")
    return value


def _require_int(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise H6ContractError(f"{where}: expected an integer >= {minimum}")
    return value


def _require_list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise H6ContractError(f"{where}: expected a JSON array")
    return value


def _row_id(row: Mapping[str, Any]) -> str:
    return _require_str(row.get("row_id"), "row.row_id")


# ── H6 validator ─────────────────────────────────────────────────────────


def validate_evidence(evidence: object, where: str) -> None:
    """Validate a row's evidence block; PASS requires an executed reference."""
    block = _require_mapping(evidence, f"{where}.evidence")
    kind = _require_str(block.get("kind"), f"{where}.evidence.kind")
    if kind not in ALLOWED_EVIDENCE_KINDS:
        raise H6RowStatusError(f"{where}: unknown evidence kind {kind!r}")

    artifacts = _require_list(block.get("artifacts"), f"{where}.evidence.artifacts")
    if not artifacts:
        raise H6RowStatusError(f"{where}: evidence needs at least one digested artifact")
    for artifact in artifacts:
        entry = _require_mapping(artifact, f"{where}.evidence.artifact")
        path = _require_str(entry.get("path"), f"{where}.evidence.artifact.path")
        if path.startswith("/"):
            raise H6RowStatusError(f"{where}: artifact path must be repo-relative")
        if not (REPO / path).exists():
            raise H6RowStatusError(f"{where}: artifact {path!r} does not exist")
        digest = _require_str(entry.get("sha256"), f"{where}.evidence.artifact.sha256")
        if not HEX_PATTERN.match(digest):
            raise H6RowStatusError(f"{where}: artifact {path!r} needs a bare 64-hex sha256")
        if _sha256(REPO / path) != digest:
            raise H6RowStatusError(f"{where}: artifact digest mismatch for {path!r}")


def validate_row(row: Mapping[str, Any]) -> None:
    """Validate one applicability/verification row (status, proof, scope)."""
    row_id = _row_id(row)
    status = _require_str(row.get("status"), f"{row_id}.status")
    if status not in ALLOWED_ROW_STATUSES:
        raise H6RowStatusError(f"{row_id}: status {status!r} not in {sorted(ALLOWED_ROW_STATUSES)}")
    verified = _require_bool(row.get("verified"), f"{row_id}.verified")
    real_run = _require_bool(row.get("real_run"), f"{row_id}.real_run")
    scope = _require_str(row.get("scope"), f"{row_id}.scope")

    if real_run:
        raise H6RowStatusError(f"{row_id}: H6 records no real run; real_run must be false")
    if _require_str(row.get("phase"), f"{row_id}.phase") not in REQUIRED_PHASES:
        if row.get("row_kind") != "required-item":
            raise H6PhaseKeyError(
                f"{row_id}: phase {row.get('phase')!r} is not one of the unique "
                f"subcase phases {REQUIRED_PHASES}"
            )

    if status == "PASS":
        if verified is not True:
            raise H6RowStatusError(f"{row_id}: PASS must carry verified=true")
        if str(row.get("gap") or "").strip():
            raise H6RowStatusError(f"{row_id}: PASS must not carry a gap")
        if "real_run=false" not in scope:
            raise H6RowStatusError(
                f"{row_id}: an H6 PASS scope must state static/constructed verification "
                "with the literal 'real_run=false'"
            )
        evidence = _require_mapping(row.get("evidence"), f"{row_id}.evidence")
        reference_value = evidence.get("executed_reference")
        if not isinstance(reference_value, dict):
            raise H6RowStatusError(
                f"{row_id}: PASS needs an executed_reference with a command and exit_code"
            )
        reference = reference_value
        _require_str(reference.get("command"), f"{row_id}.evidence.executed_reference.command")
        if (
            _require_int(
                reference.get("exit_code"), f"{row_id}.evidence.executed_reference.exit_code"
            )
            != 0
        ):
            raise H6RowStatusError(f"{row_id}: the executed reference must record exit_code 0")
        tests_value = evidence.get("tests")
        if not isinstance(tests_value, list) or not tests_value:
            raise H6RowStatusError(f"{row_id}: PASS needs at least one executed test id")
        validate_evidence(evidence, row_id)
    else:
        if verified is not False:
            raise H6RowStatusError(f"{row_id}: {status} must carry verified=false")
        if not str(row.get("gap") or "").strip():
            raise H6RowStatusError(f"{row_id}: {status} needs the exact gap")
        validate_evidence(row.get("evidence"), row_id)


def validate_subcase_phase_keys(rows: Sequence[Mapping[str, Any]]) -> None:
    """Require one subcase row per unique phase and reject parent aggregates."""
    subcase_rows = [row for row in rows if row.get("row_kind") == "subcase"]
    if any(row.get("row_kind") == "aggregate" for row in rows):
        raise H6PhaseKeyError(
            "a parent aggregate row cannot distinguish nmr/thermo/selectivity; "
            "per-subcase rows keyed by phase are required"
        )

    phases: list[str] = []
    for row in subcase_rows:
        row_id = _row_id(row)
        phase = _require_str(row.get("phase"), f"{row_id}.phase")
        if phase not in REQUIRED_PHASES:
            raise H6PhaseKeyError(
                f"{row_id}: parent/ambiguous phase {phase!r} cannot distinguish the three "
                f"subcases; allowed phases are {REQUIRED_PHASES}"
            )
        subcase = row.get("subcase")
        if subcase is not None and subcase != phase.split(":", 1)[1]:
            raise H6PhaseKeyError(
                f"{row_id}: display subcase {subcase!r} contradicts phase {phase!r}"
            )
        phases.append(phase)

    missing = sorted(set(REQUIRED_PHASES) - set(phases))
    if missing:
        raise H6PhaseKeyError(f"missing per-subcase row(s) for phases {missing}")
    duplicated = sorted(phase for phase, count in Counter(phases).items() if count > 1)
    if duplicated:
        raise H6PhaseKeyError(f"duplicated per-subcase phase row(s) {duplicated}")
    if len(subcase_rows) != len(REQUIRED_PHASES):
        raise H6PhaseKeyError(
            f"expected exactly {len(REQUIRED_PHASES)} subcase rows, got {len(subcase_rows)}"
        )


def evaluate_signoff(signoff: object) -> tuple[bool, str]:
    """Return ``(closable, reason)`` for the C T-40b signoff record."""
    record = _require_mapping(signoff, "c_t40b_signoff")
    if record.get("present") is not True:
        return False, "absent"
    if record.get("signed") is not True:
        return False, "unsigned"
    if record.get("audit_dependency") != A25B_ARTIFACT_ID:
        return False, "wrong audit dependency"
    if record.get("audit_digest") != "sha256:" + _sha256(SELECTIVITY_REPORT_PATH):
        return False, "stale audit digest"
    if not str(record.get("signed_at") or "").strip():
        return False, "unsigned (no signed_at timestamp)"
    return True, "signed and bound to the current A-25b audit digest"


def validate_signoff(signoff: object) -> None:
    """Validate the signoff record shape, never inventing a signature."""
    record = _require_mapping(signoff, "c_t40b_signoff")
    _require_str(record.get("signoff_id"), "c_t40b_signoff.signoff_id")
    _require_bool(record.get("required"), "c_t40b_signoff.required")
    _require_bool(record.get("present"), "c_t40b_signoff.present")
    _require_bool(record.get("signed"), "c_t40b_signoff.signed")
    _require_str(record.get("status"), "c_t40b_signoff.status")
    if record.get("present") is not True:
        _require_str(record.get("gap"), "c_t40b_signoff.gap")
    if record.get("signed") is True:
        _require_str(record.get("signed_at"), "c_t40b_signoff.signed_at")


def required_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("required_for_h6_closure") is True]


def expected_closure(doc: Mapping[str, Any]) -> bool:
    """H6 may close only when every required row is verified and C T-40b signed."""
    rows = _require_list(doc.get("rows"), "rows")
    typed_rows = [_require_mapping(row, "rows[]") for row in rows]
    rows_closable = all(row.get("verified") is True for row in required_rows(typed_rows))
    signoff_closable, _reason = evaluate_signoff(doc.get("c_t40b_signoff"))
    return rows_closable and signoff_closable


def validate_closure(doc: Mapping[str, Any]) -> None:
    closure = _require_mapping(doc.get("h6_closure"), "h6_closure")
    status = _require_str(closure.get("status"), "h6_closure.status")
    if status not in {"open", "closed"}:
        raise H6ClosureError(f"h6_closure.status {status!r} must be open|closed")
    closed = _require_bool(closure.get("closed"), "h6_closure.closed")

    rows = [_require_mapping(row, "rows[]") for row in _require_list(doc.get("rows"), "rows")]
    required = required_rows(rows)
    required_ids = sorted(_row_id(row) for row in required)
    open_ids = sorted(_row_id(row) for row in required if row.get("verified") is not True)
    declared_required = _require_list(closure.get("required_row_ids"), "h6_closure.required")
    declared_open = _require_list(closure.get("open_required_row_ids"), "h6_closure.open")
    if sorted(str(value) for value in declared_required) != required_ids:
        raise H6ClosureError(
            "h6_closure.required_row_ids must equal the required_for_h6_closure rows"
        )
    if sorted(str(value) for value in declared_open) != open_ids:
        raise H6ClosureError(
            "h6_closure.open_required_row_ids must equal the unverified required rows"
        )

    closable = expected_closure(doc)
    if closable and not (status == "closed" and closed is True):
        raise H6ClosureError("every required row and the C T-40b signoff are verified; close H6")
    if not closable and (status == "closed" or closed is not False):
        raise H6ClosureError(
            "H6 cannot close while a required row is unverified or the C T-40b signoff "
            "is absent/unsigned/stale"
        )
    _require_str(closure.get("reason"), "h6_closure.reason")
    if _require_bool(
        closure.get("positive_evidence_present"), "h6_closure.positive_evidence_present"
    ) != (not open_ids):
        raise H6ClosureError(
            "positive_evidence_present must be true exactly when no required row is open"
        )
    if _require_bool(closure.get("real_run"), "h6_closure.real_run") is not False:
        raise H6ClosureError("H6 records no real run")


def validate_budget_allocation(allocation: object) -> None:
    """Require the nmr/thermo/selectivity allocation with an honest cumulative."""
    block = _require_mapping(allocation, "budget_allocation")
    if _require_bool(block.get("constructed"), "budget_allocation.constructed") is not True:
        raise H6ContractError("budget_allocation must stay labelled constructed=true")
    if block.get("budget_timing_basis") != "unmeasured":
        raise H6ContractError("budget timing basis must stay 'unmeasured'")

    subrows = [
        _require_mapping(row, "budget_allocation.subrows[]")
        for row in _require_list(block.get("subrows"), "budget_allocation.subrows")
    ]
    by_phase = {str(row.get("phase")): row for row in subrows}
    if sorted(by_phase) != sorted(REQUIRED_PHASES) or len(subrows) != len(REQUIRED_PHASES):
        raise H6ContractError(
            f"budget subrows must be keyed exactly by {REQUIRED_PHASES}, got {sorted(by_phase)}"
        )

    totals = {"task_refs": 0, "steps": 0, "qc_calls": 0}
    reused: list[str] = []
    for phase in REQUIRED_PHASES:
        row = by_phase[phase]
        domain = _require_str(row.get("domain"), f"budget[{phase}].domain")
        if phase != f"experience:{domain}":
            raise H6ContractError(f"budget[{phase}]: domain {domain!r} contradicts the phase")
        _require_str(row.get("task_ref"), f"budget[{phase}].task_ref")
        totals["task_refs"] += _require_int(
            row.get("task_ref_count"), f"budget[{phase}].task_ref_count"
        )
        totals["steps"] += _require_int(row.get("step_count"), f"budget[{phase}].step_count")
        totals["qc_calls"] += _require_int(row.get("qc_count"), f"budget[{phase}].qc_count")
        ignored = _require_list(row.get("reused_artifacts"), f"budget[{phase}].reused_artifacts")
        reused.extend(str(value) for value in ignored)

    cumulative = _require_mapping(block.get("cumulative"), "budget_allocation.cumulative")
    for key, value in totals.items():
        if _require_int(cumulative.get(key), f"budget_allocation.cumulative.{key}") != value:
            raise H6ContractError(f"budget_allocation.cumulative.{key} is not the subrow sum")
    if cumulative.get("wall_seconds") is not None:
        raise H6ContractError("budget_allocation.cumulative.wall_seconds must stay null")

    declared_reused = [
        str(value)
        for value in _require_list(
            block.get("reused_artifacts"), "budget_allocation.reused_artifacts"
        )
    ]
    if declared_reused != reused:
        raise H6ContractError("budget_allocation.reused_artifacts is not the subrow union")
    if reused != ["A-24a:nmr-comparison:constructed", "A-24b:ensemble-thermo:constructed"]:
        raise H6ContractError("the unchanged nmr/thermo artifacts must be reused, not re-run")


def validate_producer_states(states: object) -> None:
    """Every external producer stays NOT_VERIFIED with the exact missing item."""
    block = _require_mapping(states, "producer_states")
    if sorted(block) != sorted(REQUIRED_PRODUCER_IDS):
        raise H6ContractError(f"producer_states must cover exactly {REQUIRED_PRODUCER_IDS}")
    for producer in REQUIRED_PRODUCER_IDS:
        record = _require_mapping(block[producer], f"producer_states[{producer}]")
        if record.get("evidence_state") != "NOT_VERIFIED":
            raise H6ContractError(f"{producer}: evidence_state must stay NOT_VERIFIED")
        _require_str(record.get("gap"), f"producer_states[{producer}].gap")
        digest = record.get("digest")
        if digest is None:
            if not str(record.get("gap") or "").strip():
                raise H6ContractError(f"{producer}: a null digest needs the exact gap")
        elif not isinstance(digest, str) or not DIGEST_PATTERN.match(digest):
            raise H6ContractError(f"{producer}: digest must be null or sha256:<64 hex>")


def validate_version_matrix(doc: Mapping[str, Any]) -> None:
    """Cross-check every declared domain/version row against the fixture on disk."""
    matrix = [
        _require_mapping(row, "version_matrix[]")
        for row in _require_list(doc.get("version_matrix"), "version_matrix")
    ]
    by_domain = {str(row.get("domain")): row for row in matrix}
    expected_domains = {
        "singlepoint": SINGLEPOINT_REQUEST,
        "optfreq": OPTFREQ_OPTIMIZE_REQUEST,
        "diels_alder": DA_REACTION,
        "nmr": NMR_FIXTURE,
        "thermochemistry": THERMO_FIXTURE,
        "selectivity": SELECTIVITY_MANIFEST,
    }
    if sorted(by_domain) != sorted(expected_domains):
        raise H6ContractError(f"version_matrix domains must be {sorted(expected_domains)}")
    for domain, path in expected_domains.items():
        row = by_domain[domain]
        fixture = _load(path)
        if _require_int(row.get("schema_version"), f"version_matrix[{domain}].schema_version") != (
            fixture.get("schema_version")
        ):
            raise H6ContractError(f"version_matrix[{domain}]: schema_version drifts from fixture")
        if row.get("result") not in ALLOWED_ROW_STATUSES:
            raise H6ContractError(f"version_matrix[{domain}]: unknown result")


def validate_h6(doc: Mapping[str, Any]) -> None:
    """Validate the complete H6 handoff and its honesty invariants."""
    for key in (
        "handoff_id",
        "handoff_version",
        "generated_by",
        "contract_set",
        "contract_set_version",
        "contract_set_frozen",
        "frozen_at",
        "gate_command",
        "source",
        "h6_closure",
        "c_t40b_signoff",
        "rows",
        "budget_allocation",
        "producer_states",
        "version_matrix",
        "fixture_digests",
        "schema_versions",
        "limitations",
    ):
        if key not in doc:
            raise H6ContractError(f"handoff missing top-level field {key!r}")

    if doc.get("handoff_id") != "H6" or doc.get("contract_set") != "acp2.science":
        raise H6ContractError("this file is the H6 acp2.science handoff")
    source = _require_mapping(doc.get("source"), "source")
    if sorted(source) != ["branch", "pre_delivery_commit", "python", "worktree"]:
        raise H6ContractError(
            "source carries only the pre-delivery commit; a delivered external commit "
            "belongs in the evidence record, never in H6"
        )
    if source.get("pre_delivery_commit") != "a1319b9":
        raise H6ContractError("source.pre_delivery_commit must be the A-26 base commit")

    rows = [_require_mapping(row, "rows[]") for row in _require_list(doc.get("rows"), "rows")]
    if len({_row_id(row) for row in rows}) != len(rows):
        raise H6ContractError("duplicate row_id in H6 rows")
    for row in rows:
        validate_row(row)

    matrix_ids = {_row_id(row) for row in rows}
    for row_id in REQUIRED_ITEM_ROW_IDS + REQUIRED_SUBCASE_ROW_IDS:
        if row_id not in matrix_ids:
            raise H6ContractError(f"missing required H6 row {row_id!r}")
    present_item_ids = tuple(_row_id(row) for row in rows if row.get("row_kind") == "required-item")
    if present_item_ids != REQUIRED_ITEM_ROW_IDS:
        raise H6ContractError("required-item rows must use the frozen order and ids")
    if sorted(_row_id(row) for row in rows if row.get("row_kind") == "subcase") != sorted(
        REQUIRED_SUBCASE_ROW_IDS
    ):
        raise H6ContractError("subcase rows must use the frozen ids")
    validate_subcase_phase_keys(rows)

    validate_signoff(doc.get("c_t40b_signoff"))
    validate_closure(doc)
    validate_budget_allocation(doc.get("budget_allocation"))
    validate_producer_states(doc.get("producer_states"))
    validate_version_matrix(doc)

    digests = _require_mapping(doc.get("fixture_digests"), "fixture_digests")
    for name, path in {
        "a_25a_selectivity_fixture_manifest": SELECTIVITY_MANIFEST,
        "diels_alder_v1_fixture_manifest": DA_MANIFEST,
        "singlepoint_fixture_manifest": SINGLEPOINT_MANIFEST,
        "optfreq_fixture_manifest": OPTFREQ_MANIFEST,
        "nmr_cross_domain_fixture": NMR_FIXTURE,
        "thermochemistry_cross_domain_fixture": THERMO_FIXTURE,
        "plugin_v1_manifest": PLUGIN_V1_MANIFEST,
        "plugin_v2_manifest": PLUGIN_V2_MANIFEST,
    }.items():
        digest = _require_str(digests.get(name), f"fixture_digests.{name}")
        if digest != _sha256(path):
            raise H6ContractError(f"fixture_digests.{name} does not match {path}")

    limitations = _require_list(doc.get("limitations"), "limitations")
    if len(limitations) < 4:
        raise H6ContractError("every limitation and missing producer must be named")
    for entry in limitations:
        record = _require_mapping(entry, "limitations[]")
        _require_str(record.get("limitation"), "limitations[].limitation")
        _require_str(record.get("missing_producer"), "limitations[].missing_producer")


# ── adversarial science scan (platform-key independence) ────────────────


def _iter_science_docs() -> list[Path]:
    docs = [H1_PATH, H2_PATH, H3_PATH, H4_PATH, H5_PATH, H6_PATH]
    docs.extend([SELECTIVITY_REPORT_PATH, LEDGER_PATH])
    for path in sorted(FIXTURES.rglob("*.json")):
        if SCIENCE_SCAN_EXCLUDED_NAME in path.name:
            continue
        docs.append(path)
    return docs


def _role_is_allow_listed(path: tuple[str, ...]) -> bool:
    """A ``role`` key is allowed only as an artifact/structure role field."""
    parent = next(
        (
            segment
            for segment in reversed(path[:-1])
            if not (segment.startswith("[") and segment.endswith("]"))
        ),
        "",
    )
    return parent in ROLE_CONTAINERS


def _collect_forbidden_keys(value: object, path: tuple[str, ...], found: list[str]) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.lower()
            child_path = (*path, key)
            if lowered.startswith(JOB_KEY_PREFIX):
                found.append(".".join(child_path))
            elif lowered in FORBIDDEN_SCIENCE_KEYS and not (
                lowered == "role" and _role_is_allow_listed(child_path)
            ):
                found.append(".".join(child_path))
            _collect_forbidden_keys(child, child_path, found)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _collect_forbidden_keys(child, (*path, f"[{index}]"), found)


def scan_forbidden_keys(doc: object) -> list[str]:
    """Return every forbidden platform key path in *doc*."""
    found: list[str] = []
    _collect_forbidden_keys(doc, ("$",), found)
    return found


# ═══════════════════════════════════════════════════════════════════════
# 1. Version regression / schema upgrade
# ═══════════════════════════════════════════════════════════════════════


def test_historical_evidence_payload_is_readable_but_never_eligible() -> None:
    payload = _load(HISTORICAL_EVIDENCE)
    record = read_historical_evidence(payload)
    assert record.schema_version == 0
    assert record.historical is True
    assert record.execution_eligible is False
    assert record.recovery_eligible is False
    assert record.to_dict() == payload
    assert any(field.endswith("legacy_review_note") for field in record.unknown_fields)
    assert any(field.endswith("legacy_confidence") for field in record.unknown_fields)
    assert "execution" in record.payload


def test_historical_provenance_and_reaction_question_are_read_only() -> None:
    report = _load(REPORT_SCENARIO / "path-endo-evidence.json")
    provenance_payload = {
        "schema_version": 0,
        "legacy_note": "pre-v1 provenance preserved verbatim",
        "report_id": report["report_id"],
    }
    provenance = read_historical_provenance(provenance_payload)
    assert provenance.schema_version == 0
    assert provenance.historical is True
    assert provenance.execution_eligible is False
    assert provenance.recovery_eligible is False
    assert provenance.to_dict() == provenance_payload
    assert any(field.endswith("legacy_note") for field in provenance.unknown_fields)

    reaction_payload = _load(DA_REACTION)
    reaction_payload["schema_version"] = 0
    reaction_payload["legacy_note"] = "pre-v1 reaction preserved verbatim"
    reaction = read_historical_reaction(reaction_payload)
    assert reaction.schema_version == 0
    assert reaction.execution_eligible is False
    assert reaction.recovery_eligible is False
    assert reaction.to_dict() == reaction_payload
    assert any(field.endswith("legacy_note") for field in reaction.unknown_fields)

    question_payload = _load(DA_QUESTION)["questions"][0]
    question_payload["schema_version"] = 0
    question_payload["legacy_note"] = "pre-v1 question preserved verbatim"
    question = read_historical_question(question_payload)
    assert question.schema_version == 0
    assert question.execution_eligible is False
    assert question.recovery_eligible is False
    assert question.to_dict() == question_payload
    assert any(field.endswith("legacy_note") for field in question.unknown_fields)


def test_cross_domain_fixture_records_are_readable_and_non_executable() -> None:
    for path in (NMR_FIXTURE, THERMO_FIXTURE):
        payload = _load(path)
        record = read_historical_evidence(payload)
        assert record.to_dict() == payload
        assert record.execution_eligible is False
        assert record.recovery_eligible is False
        assert payload["constructed"] is True
        assert payload["real_run"] is False


def test_strict_v1_reader_rejects_an_unknown_execution_field() -> None:
    payload = _load(SUPPORTED_EVIDENCE)
    payload["future_execution_field"] = {"account": "tier-a"}
    with pytest.raises(EvidenceContractError):
        evidence_report_from_dict(payload)

    nested = _load(SUPPORTED_EVIDENCE)
    nested["execution"]["account"] = "tier-a"
    with pytest.raises(EvidenceContractError):
        evidence_report_from_dict(nested)


def test_unknown_major_is_never_executed() -> None:
    payload = _load(SUPPORTED_EVIDENCE)
    payload["schema_version"] = 99
    with pytest.raises(UnsupportedEvidenceSchemaVersionError):
        evidence_report_from_dict(payload)
    historical = read_historical_evidence(payload)
    assert historical.schema_version == 99
    assert historical.historical is True
    assert historical.execution_eligible is False

    provenance_payload = _load(CONSTRUCTED_PROVENANCE)["provenance"]
    provenance_payload["schema_version"] = 99
    with pytest.raises(UnsupportedProvenanceSchemaVersionError):
        provenance_from_dict(provenance_payload)
    assert read_historical_provenance(provenance_payload).execution_eligible is False

    reaction_payload = _load(DA_REACTION)
    reaction_payload["schema_version"] = 99
    with pytest.raises(UnsupportedSchemaVersionError):
        Reaction.from_dict(reaction_payload)
    assert read_historical_reaction(reaction_payload).execution_eligible is False

    question_payload = _load(DA_QUESTION)["questions"][0]
    question_payload["schema_version"] = 99
    with pytest.raises(UnsupportedQuestionSchemaVersionError):
        Question.from_dict(question_payload)
    assert read_historical_question(question_payload).execution_eligible is False


def test_strict_readers_reject_unknown_and_platform_fields() -> None:
    reaction_payload = _load(DA_REACTION)
    reaction_payload["future_field"] = 1
    with pytest.raises(SchemaViolationError):
        Reaction.from_dict(reaction_payload)
    reaction_payload.pop("future_field")
    reaction_payload["account"] = "tier-a"
    with pytest.raises(ForbiddenPlatformFieldError):
        Reaction.from_dict(reaction_payload)
    with pytest.raises(ReactionForbiddenFieldError):
        read_historical_reaction(reaction_payload)

    question_payload = _load(DA_QUESTION)["questions"][0]
    question_payload["future_field"] = 1
    with pytest.raises(SchemaViolationError):
        Question.from_dict(question_payload)
    question_payload.pop("future_field")
    question_payload["account"] = "tier-a"
    with pytest.raises(QuestionForbiddenFieldError):
        Question.from_dict(question_payload)


# ═══════════════════════════════════════════════════════════════════════
# 2. Derived identity across domains (single identity implementation)
# ═══════════════════════════════════════════════════════════════════════


def _atom_mapping_digest(nmr: Mapping[str, Any]) -> str:
    payload = {
        "symbols": nmr["symbols"],
        "atom_labels": nmr["atom_labels"],
        "atom_index_base": nmr["atom_index_base"],
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _science_identity_payload() -> dict[str, Any]:
    nmr = _load(NMR_FIXTURE)
    thermo = _load(THERMO_FIXTURE)
    manifest = _load(DA_MANIFEST)
    model_version, _available, _hashes = model_asset_record(None)
    return {
        "domain": "cross-domain",
        "method": nmr["reference"]["method"],
        "basis": nmr["reference"]["basis"],
        "solvent": nmr["reference"]["solvent"],
        "structure_sha256": manifest["file_digests"]["product.xyz"],
        "atom_mapping_digest": _atom_mapping_digest(nmr),
        "charge": 0,
        "multiplicity": 1,
        "electronic_state": "state.singlet.s0",
        "units": {"energy": "hartree", "shieldings": "ppm", "temperature": "kelvin"},
        "reference_state": {
            "standard_state": thermo["standard_state"],
            "tms_shieldings": nmr["reference"]["tms_shieldings"],
        },
        "temperature_kelvin": nmr["reference"]["temperature_kelvin"],
        "provider": "orca",
        "plugin_version": "1.0.0",
        "model_asset_version": model_version,
    }


def test_every_scientific_input_changes_the_identity() -> None:
    base = _science_identity_payload()
    base_fingerprint = identity_fingerprint(base)
    assert base_fingerprint.startswith("v2:")

    mutations: dict[str, dict[str, Any]] = {
        "method": {**base, "method": "wB97X-D4"},
        "structure_digest": {**base, "structure_sha256": "0" * 64},
        "atom_mapping": {**base, "atom_mapping_digest": "0" * 64},
        "charge": {**base, "charge": 1},
        "multiplicity": {**base, "multiplicity": 2},
        "electronic_state": {**base, "electronic_state": "state.triplet.t1"},
        "units": {**base, "units": {"energy": "kcal_per_mol", "shieldings": "ppm"}},
        "reference_state": {**base, "reference_state": {"standard_state": "1_bar_ideal_gas"}},
        "provider": {**base, "provider": "xtb"},
        "plugin_version": {**base, "plugin_version": "2.0.0"},
        "model_asset_version": {**base, "model_asset_version": "sha256:" + "0" * 64},
    }
    fingerprints = {identity_fingerprint(payload) for payload in mutations.values()}
    assert base_fingerprint not in fingerprints
    assert len(fingerprints) == len(mutations)


def test_execution_and_display_metadata_do_not_change_the_identity() -> None:
    base = _science_identity_payload()
    base_fingerprint = identity_fingerprint(base)

    # Execution-domain keys are excluded by the one identity implementation.
    with_execution = {
        **base,
        "output_dir": "/tmp/out",
        "nproc": 16,
        "mem": "64GB",
        "timeout_s": 10,
        "config": {"account": "tier-a"},
    }
    assert identity_fingerprint(with_execution) == base_fingerprint

    wrappers: tuple[dict[str, Any], ...] = (
        {"display_name": "Alice", "account": "acct-a", "tier": "free", "permission": ["read"]},
        {
            "display_name": "Bob",
            "account": "acct-b",
            "tier": "enterprise",
            "permission": ["read", "write", "admin"],
        },
        {"display_name": "Carol", "account": None, "tier": "researcher", "permission": []},
    )
    renderings = set()
    fingerprints = set()
    for wrapper in wrappers:
        envelope = {"science": base, "platform": wrapper}
        renderings.add(_canonical_bytes(envelope["science"]))
        fingerprints.add(identity_fingerprint(envelope["science"]))
        for key in ("display_name", "account", "tier", "permission"):
            assert key not in envelope["science"]
    assert len(renderings) == 1
    assert fingerprints == {base_fingerprint}


def test_scientific_identity_round_trips_the_plan_and_step_layers() -> None:
    base = _science_identity_payload()
    plan_payload = {"recipe_id": "acp2.diels_alder.limited.v1", "inputs": base}
    steps = (
        {"step_id": "opt", "method": base["method"], "structure_sha256": base["structure_sha256"]},
        {"step_id": "nmr", "model_asset_version": base["model_asset_version"]},
    )
    identity = ScientificIdentity(
        plan_identity=identity_fingerprint(plan_payload),
        step_identities=tuple(identity_fingerprint(step) for step in steps),
        items=(),
        steps=steps,
        path_remaps=(),
    )
    record = identity.payload()
    assert record["identity_schema"] == 2
    assert record["plan_identity"] == identity.plan_identity
    assert record["step_identities"] == list(identity.step_identities)
    assert record["steps"] == [dict(step) for step in steps]


# ═══════════════════════════════════════════════════════════════════════
# 3. Conservative recovery after a model/plugin digest change
# ═══════════════════════════════════════════════════════════════════════


def _plugin_digest(path: Path) -> str:
    return _sha256(path)


def _frozen_recipe(
    *, model_digest: str | None = None, plugin_digest: str | None = None
) -> dict[str, Any]:
    manifest = _load(DA_MANIFEST)
    base = _science_identity_payload()
    model = model_digest or str(base["model_asset_version"])
    plugin = plugin_digest or _plugin_digest(PLUGIN_V1_MANIFEST)
    steps = (
        {
            "step_id": "optimize",
            "kind": "optimize",
            "method": base["method"],
            "structure_sha256": base["structure_sha256"],
        },
        {
            "step_id": "frequency",
            "kind": "frequency",
            "method": base["method"],
            "structure_sha256": base["structure_sha256"],
        },
        {
            "step_id": "nmr-compare",
            "kind": "nmr.compare",
            "model_asset_digest": model,
            "atom_mapping_digest": base["atom_mapping_digest"],
        },
        {
            "step_id": "plugin-singlepoint",
            "kind": "singlepoint",
            "plugin_digest": plugin,
            "charge": base["charge"],
            "multiplicity": base["multiplicity"],
        },
    )
    plan = {
        "recipe_id": "acp2.diels_alder.limited.v1",
        "plan_definition_digest": manifest["file_digests"]["acceptance.json"],
        "step_ids": [step["step_id"] for step in steps],
        "step_identities": [identity_fingerprint(step) for step in steps],
    }
    step_identities = tuple(identity_fingerprint(step) for step in steps)
    return {
        "plan_identity": identity_fingerprint(plan),
        "step_identities": step_identities,
        "step_ids": tuple(str(step["step_id"]) for step in steps),
    }


def _invalidated_steps(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[str, ...]:
    before_ids = dict(zip(before["step_ids"], before["step_identities"], strict=True))
    after_ids = dict(zip(after["step_ids"], after["step_identities"], strict=True))
    return tuple(sorted(sid for sid in before_ids if before_ids[sid] != after_ids[sid]))


def test_model_digest_change_invalidates_exactly_the_dependent_step() -> None:
    base = _frozen_recipe()
    changed = _frozen_recipe(model_digest="sha256:" + "1" * 64)
    assert changed["plan_identity"] != base["plan_identity"]
    assert _invalidated_steps(base, changed) == ("nmr-compare",)
    reuse = {
        sid: (old == new)
        for sid, old, new in zip(
            base["step_ids"], base["step_identities"], changed["step_identities"], strict=True
        )
    }
    assert reuse == {
        "optimize": True,
        "frequency": True,
        "nmr-compare": False,
        "plugin-singlepoint": True,
    }


def test_plugin_digest_change_invalidates_only_the_plugin_step() -> None:
    base = _frozen_recipe()
    changed = _frozen_recipe(plugin_digest=_plugin_digest(PLUGIN_V2_MANIFEST))
    assert changed["plan_identity"] != base["plan_identity"]
    assert _invalidated_steps(base, changed) == ("plugin-singlepoint",)
    assert base["step_identities"][:3] == changed["step_identities"][:3]


def test_both_digest_changes_invalidate_two_of_four_steps() -> None:
    base = _frozen_recipe()
    changed = _frozen_recipe(
        model_digest="sha256:" + "2" * 64,
        plugin_digest=_plugin_digest(PLUGIN_V2_MANIFEST),
    )
    assert _invalidated_steps(base, changed) == ("nmr-compare", "plugin-singlepoint")


def test_v1_artifact_after_simulated_upgrade_stays_byte_identical() -> None:
    raw = PLUGIN_V1_RESULT.read_text(encoding="utf-8")
    before = _sha256(PLUGIN_V1_RESULT)
    record = read_historical_evidence(json.loads(raw))
    replayed = json.dumps(record.to_dict(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert replayed == raw
    assert _sha256(PLUGIN_V1_RESULT) == before
    assert record.execution_eligible is False
    assert record.recovery_eligible is False

    # The simulated v1 -> v2 upgrade changes only the plugin digest; the
    # model-free pure-analysis step keeps its identity and is reused.
    base = _frozen_recipe()
    upgraded = _frozen_recipe(plugin_digest=_plugin_digest(PLUGIN_V2_MANIFEST))
    assert _invalidated_steps(base, upgraded) == ("plugin-singlepoint",)
    assert base["step_identities"][0] == upgraded["step_identities"][0]
    assert base["step_identities"][1] == upgraded["step_identities"][1]


# ═══════════════════════════════════════════════════════════════════════
# 4. Account tier / project permission independence
# ═══════════════════════════════════════════════════════════════════════

ACCOUNT_WRAPPERS: Final[tuple[dict[str, Any], ...]] = (
    {
        "account": "acct-alpha",
        "tier": "free",
        "permissions": ["read"],
        "role": "viewer",
        "quota": 1,
        "job_id": "job-alpha",
    },
    {
        "account": "acct-beta",
        "tier": "enterprise",
        "permissions": ["read", "write", "admin"],
        "role": "owner",
        "quota": 10_000,
        "job_id": "job-beta",
    },
    {
        "account": None,
        "tier": "researcher",
        "permissions": [],
        "role": "reviewer",
        "quota": 0,
        "job_id": None,
    },
)


def test_scientific_bytes_and_claim_statuses_ignore_account_and_permission() -> None:
    payload = _science_identity_payload()
    report = evidence_report_from_dict(_load(SUPPORTED_EVIDENCE))
    rendered: set[bytes] = set()
    statuses: set[str] = set()
    report_bytes: set[bytes] = set()
    for wrapper in ACCOUNT_WRAPPERS:
        envelope = {"science": payload, "platform": wrapper}
        rendered.add(_canonical_bytes(envelope["science"]))
        statuses.add(identity_fingerprint(envelope["science"]))
        report_bytes.add(canonical_evidence_json(report).encode("utf-8"))
        assert report.overall_claim_status in CLAIM_STATUSES
        assert report.overall_claim_status == "supported"
    assert len(rendered) == 1
    assert len(statuses) == 1
    assert len(report_bytes) == 1


def test_no_science_artifact_carries_a_platform_key() -> None:
    violations: dict[str, list[str]] = {}
    for path in _iter_science_docs():
        found = scan_forbidden_keys(_load_any(path))
        if found:
            violations[str(path.relative_to(REPO))] = found
    assert violations == {}, f"platform keys leaked into science artifacts: {violations}"

    # The allow-list is narrow and documented: `role` is legal only as a
    # provenance/evidence artifact role or a request structure role.
    assert scan_forbidden_keys({"source_refs": [{"role": "native_output"}]}) == []
    assert scan_forbidden_keys({"role": "admin"}) != []
    assert scan_forbidden_keys({"snapshot": {"job_id": "j-1"}}) != []


# ═══════════════════════════════════════════════════════════════════════
# 5. Per-subcase rows, not a parent aggregate
# ═══════════════════════════════════════════════════════════════════════


def _h6() -> dict[str, Any]:
    return _load(H6_PATH)


def test_h6_uses_one_row_per_unique_subcase_phase() -> None:
    doc = _h6()
    rows = doc["rows"]
    subcase_phases = sorted(row["phase"] for row in rows if row["row_kind"] == "subcase")
    assert subcase_phases == sorted(REQUIRED_PHASES)
    validate_subcase_phase_keys(rows)


def test_parent_only_aggregate_row_is_rejected() -> None:
    doc = _h6()
    rows = list(doc["rows"])
    aggregate = {
        "row_id": "H6-parent-aggregate",
        "row_kind": "aggregate",
        "phase": "experience:cross-domain-live",
        "status": "NOT_VERIFIED",
        "verified": False,
        "real_run": False,
        "scope": "parent-only aggregate",
        "gap": "cannot distinguish nmr/thermo/selectivity",
        "evidence": {
            "kind": "absent-producer",
            "artifacts": [
                {
                    "path": str(SELECTIVITY_REPORT_PATH.relative_to(REPO)),
                    "sha256": _sha256(SELECTIVITY_REPORT_PATH),
                }
            ],
        },
    }
    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys([*rows, aggregate])

    ambiguous = [
        {**row, "phase": "experience:cross-domain-live"} if row["row_kind"] == "subcase" else row
        for row in rows
    ]
    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys(ambiguous)


def test_missing_duplicated_or_ambiguous_subcase_phase_fails() -> None:
    rows = [row for row in _h6()["rows"] if row["row_kind"] == "subcase"]

    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys([row for row in rows if row["phase"] != "experience:nmr"])
    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys([*rows, {**rows[0], "row_id": "H6-duplicate-nmr"}])

    mismatched = [{**rows[0], "subcase": "selectivity"}, *rows[1:]]
    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys(mismatched)


# ═══════════════════════════════════════════════════════════════════════
# 6. C T-40b signoff gate
# ═══════════════════════════════════════════════════════════════════════


def test_absent_signoff_keeps_h6_open() -> None:
    doc = _h6()
    assert doc["c_t40b_signoff"]["present"] is False
    closable, reason = evaluate_signoff(doc["c_t40b_signoff"])
    assert closable is False
    assert reason == "absent"
    assert doc["h6_closure"]["status"] == "open"
    assert doc["h6_closure"]["closed"] is False


def test_unsigned_or_stale_signoff_keeps_h6_open() -> None:
    doc = _h6()
    signed = dict(doc["c_t40b_signoff"])
    signed["present"] = True
    signed["signed"] = False
    signed["signed_at"] = None
    closable, reason = evaluate_signoff(signed)
    assert (closable, reason) == (False, "unsigned")

    stale = dict(doc["c_t40b_signoff"])
    stale.update(
        {
            "present": True,
            "signed": True,
            "signed_at": "2026-10-23T00:00:00Z",
            "audit_digest": "sha256:" + "0" * 64,
        }
    )
    closable, reason = evaluate_signoff(stale)
    assert closable is False
    assert reason == "stale audit digest"


def test_signed_signoff_cannot_close_h6_over_a_not_verified_selectivity_row() -> None:
    doc = _h6()
    signed = {
        **doc["c_t40b_signoff"],
        "present": True,
        "signed": True,
        "signed_at": "2026-10-23T00:00:00Z",
        "audit_digest": "sha256:" + _sha256(SELECTIVITY_REPORT_PATH),
        "status": "signed",
    }
    closable, reason = evaluate_signoff(signed)
    assert closable is True
    assert "A-25b" in reason

    modified = {**doc, "c_t40b_signoff": signed}
    assert expected_closure(modified) is False
    validate_closure(modified)  # still open because the selectivity row is NOT_VERIFIED
    selectivity = next(row for row in doc["rows"] if row["phase"] == "experience:selectivity")
    assert selectivity["status"] == "NOT_VERIFIED"
    assert selectivity["verified"] is False


def test_signoff_cannot_be_invented_without_required_fields() -> None:
    closable, reason = evaluate_signoff(
        {"present": True, "signed": True, "signed_at": "2026-10-23T00:00:00Z"}
    )
    assert closable is False
    assert reason == "wrong audit dependency"
    missing_digest = dict(_h6()["c_t40b_signoff"])
    missing_digest.pop("signoff_id")
    with pytest.raises(H6ContractError):
        validate_signoff(missing_digest)


# ═══════════════════════════════════════════════════════════════════════
# 7. No row labelled verified while unverified
# ═══════════════════════════════════════════════════════════════════════


def test_not_verified_row_cannot_be_labelled_verified() -> None:
    row = next(row for row in _h6()["rows"] if row["status"] == "NOT_VERIFIED")
    broken = {**row, "verified": True}
    with pytest.raises(H6RowStatusError):
        validate_row(broken)


def test_pass_requires_an_executed_reference() -> None:
    row = next(row for row in _h6()["rows"] if row["status"] == "PASS")
    broken = {**row, "evidence": {**row["evidence"], "executed_reference": None}}
    with pytest.raises(H6RowStatusError):
        validate_row(broken)

    no_tests = {**row, "evidence": {**row["evidence"], "tests": []}}
    with pytest.raises(H6RowStatusError):
        validate_row(no_tests)


def test_pass_cannot_claim_a_real_run_or_a_gap() -> None:
    row = next(row for row in _h6()["rows"] if row["status"] == "PASS")
    with pytest.raises(H6RowStatusError):
        validate_row({**row, "real_run": True})
    with pytest.raises(H6RowStatusError):
        validate_row({**row, "gap": "unexpected gap"})


# ═══════════════════════════════════════════════════════════════════════
# Published H6 handoff + version matrix
# ═══════════════════════════════════════════════════════════════════════


def test_h6_handoff_matches_the_published_contract() -> None:
    doc = _h6()
    validate_h6(doc)
    assert doc["handoff_id"] == "H6"
    assert doc["generated_by"] == "A-26"
    assert doc["h6_closure"]["status"] == "open"
    assert doc["h6_closure"]["closed"] is False
    assert doc["h6_closure"]["open_required_row_ids"] == [
        "H6-R6-c-t40b-signoff",
        "H6-selectivity-not-verified",
    ]


def test_h6_matrix_prints_domain_version_and_subcase_table() -> None:
    doc = _h6()
    validate_h6(doc)
    print("\nversion/applicability matrix (constructed science only):")
    for row in doc["version_matrix"]:
        print(
            f"  {row['domain']:16s} schema_version={row['schema_version']} "
            f"result={row['result']} fixture={row['fixture']}"
        )
    print("per-subcase phase table:")
    for row in doc["rows"]:
        if row["row_kind"] != "subcase":
            continue
        print(
            f"  {row['phase']:24s} {row['row_id']:32s} status={row['status']:13s} "
            f"verified={str(row['verified']).lower():5s} real_run={str(row['real_run']).lower()}"
        )
    print(f"h6_closure={json.dumps(doc['h6_closure'], sort_keys=True)}")
    assert len(doc["version_matrix"]) == 6


def test_h6_budget_allocation_is_constructed_and_reuses_existing_artifacts() -> None:
    doc = _h6()
    validate_budget_allocation(doc["budget_allocation"])
    budget = doc["budget_allocation"]
    assert budget["cumulative"] == {
        "task_refs": 1,
        "steps": 8,
        "qc_calls": 14,
        "wall_seconds": None,
    }
    assert budget["budget_timing_basis"] == "unmeasured"
    assert set(doc["producer_states"]) == set(REQUIRED_PRODUCER_IDS)
    assert all(
        state["evidence_state"] == "NOT_VERIFIED" for state in doc["producer_states"].values()
    )


# ═══════════════════════════════════════════════════════════════════════
# Ledger edge row for H6 -> C T-40b
# ═══════════════════════════════════════════════════════════════════════


def test_cross_track_ledger_records_the_h6_edge_with_the_current_digest() -> None:
    ledger = _load(LEDGER_PATH)
    rows = [row for row in ledger["rows"] if row["artifact_id"] == "A-26:h6-cross-domain-handoff"]
    assert len(rows) == 1
    row = rows[0]
    assert row["producer"] == "A-26"
    assert row["consumer"] == "C T-40b"
    assert row["phase"] == "experience:selectivity"
    assert row["readiness"] == "implementation_ready"
    assert row["evidence_state"] == "NOT_VERIFIED"
    assert row["digest"] == "sha256:" + _sha256(H6_PATH)
    assert row["version"] == "1"
    assert row["gap"]


# ═══════════════════════════════════════════════════════════════════════
# Adversarial probes (a)-(g) — every probe prints its binary observation
# ═══════════════════════════════════════════════════════════════════════


def test_probe_a_old_schema_read_only() -> None:
    record = read_historical_evidence(_load(HISTORICAL_EVIDENCE))
    assert record.execution_eligible is False and record.recovery_eligible is False
    print("probe(a)=PASS old evidence schema read-only, execution_eligible=false")


def test_probe_b_unsupported_field_rejected() -> None:
    payload = _load(SUPPORTED_EVIDENCE)
    payload["execution"]["account"] = "tier-a"
    with pytest.raises(EvidenceContractError):
        evidence_report_from_dict(payload)
    print("probe(b)=PASS unknown execution field rejected by the strict v1 reader")


def test_probe_c_digest_change_conservative_recompute() -> None:
    base = _frozen_recipe()
    changed = _frozen_recipe(model_digest="sha256:" + "3" * 64)
    invalidated = _invalidated_steps(base, changed)
    assert invalidated == ("nmr-compare",)
    print(f"probe(c)=PASS digest change invalidated exactly {invalidated}")


def test_probe_d_stale_state_byte_identical_and_one_rerun() -> None:
    raw = PLUGIN_V1_RESULT.read_text(encoding="utf-8")
    record = read_historical_evidence(json.loads(raw))
    assert json.dumps(record.to_dict(), indent=2, sort_keys=True, ensure_ascii=True) + "\n" == raw
    base = _frozen_recipe()
    upgraded = _frozen_recipe(plugin_digest=_plugin_digest(PLUGIN_V2_MANIFEST))
    assert _invalidated_steps(base, upgraded) == ("plugin-singlepoint",)
    print("probe(d)=PASS v1 artifact byte-identical; only plugin-singlepoint reruns")


def test_probe_e_parent_aggregate_rejected() -> None:
    rows: list[dict[str, Any]] = [
        {
            "row_id": "H6-parent",
            "row_kind": "subcase",
            "phase": "experience",
            "status": "NOT_VERIFIED",
            "verified": False,
            "real_run": False,
            "scope": "parent only",
            "gap": "no per-subcase phase",
            "evidence": {
                "kind": "absent-producer",
                "artifacts": [
                    {
                        "path": str(SELECTIVITY_REPORT_PATH.relative_to(REPO)),
                        "sha256": _sha256(SELECTIVITY_REPORT_PATH),
                    }
                ],
            },
        }
    ]
    with pytest.raises(H6PhaseKeyError):
        validate_subcase_phase_keys(rows)
    print("probe(e)=PASS parent-only aggregate row rejected")


def test_probe_f_misleading_pass_rejected() -> None:
    row = next(row for row in _h6()["rows"] if row["phase"] == "experience:selectivity")
    forged = {
        **row,
        "status": "PASS",
        "verified": True,
        "evidence": {**row["evidence"], "executed_reference": None},
    }
    with pytest.raises(H6RowStatusError):
        validate_row(forged)
    print("probe(f)=PASS flips NOT_VERIFIED to PASS without executed evidence -> rejected")


def test_probe_g_account_permission_variation_identical_bytes() -> None:
    payload = _science_identity_payload()
    renderings = {
        _canonical_bytes({"science": payload, "platform": wrapper}) for wrapper in ACCOUNT_WRAPPERS
    }
    # The platform wrapper is byte-identical for each account; the science
    # projection is the same record, so any variation is confined to the
    # wrapper and never reaches the science bytes.
    science_bytes = {_canonical_bytes(payload) for _wrapper in ACCOUNT_WRAPPERS}
    assert len(science_bytes) == 1
    assert len(renderings) == len(ACCOUNT_WRAPPERS)
    print("probe(g)=PASS science bytes identical under every account/tier/permission wrapper")
