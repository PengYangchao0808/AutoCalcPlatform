"""B18a — bind A's published science contracts without owning science.

This module is the **contract conversion** half of B18.  It does three things
and deliberately refuses to do a fourth:

1. Read A's *published* schema documents (``recipe.schema.json`` and friends)
   and expose their schema id / version / byte digest so the platform binding
   can name them without copying a single type or parser.
2. Convert a **C P2 minimal form document** (the model-free manual research
   form values) into a ``cccp.calculation.requests.TaskRequest`` through A's
   task-binding descriptor.  The conversion is a field mapping; it never fills
   a scientific default, never parses a structure and never guesses a mapping.
3. Register A's ``EvidenceReport`` + native files + provenance/limitations as
   a manifest that B12's :class:`~acp_services.data.artifacts.ArtifactStore`
   stores and reads verbatim.  Claim statuses are preserved literally and are
   never derived from execution state.

The fourth thing — deciding what a scientific claim means — belongs to A and
is not implemented here.  ``supported`` / ``refuted`` / ``inconclusive`` /
``not_evaluated`` are opaque strings to this module; ``completed`` execution is
only ``task_execution_completed`` and never ``claim_supported``.

Scientific boundary (frozen in the B18 plan): B owns JSON-structure and
platform binding validation; CCCP/A own scientific semantics.  The atom-mapping
check here is therefore an **identity** check (the form's declared mapping
digest must equal the reaction document's mapping digest), not a re-implemented
mapping algorithm.

Author: ACP Services (B lane, B18a).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp_services.data.artifacts import ArtifactAuthorization, ArtifactRef, ArtifactStore
from acp_services.data.transfer import sha256_bytes
from acp_services.runtime.content_digest import document_digest as content_document_digest
from cccp.calculation.requests import (
    TASK_REQUEST_SCHEMA_VERSION,
    MethodSpec,
    SinglePointOptions,
    StructureInput,
    TaskInputError,
    TaskKind,
    TaskRequest,
    TaskResources,
    validate_request,
)

__all__ = [
    "CLAIM_STATUSES",
    "EVIDENCE_STATUS_NOT_EVALUATED",
    "EXECUTION_TERMINAL_STATES",
    "SCIENCE_BINDING_SCHEMA",
    "SCIENCE_CONTRACT_SET",
    "SCIENCE_SCHEMA_FILES",
    "AtomMappingMismatchError",
    "CapabilityDiscovery",
    "EvidenceArtifactManifest",
    "EvidenceReportError",
    "ScienceBindingError",
    "ScienceContractRef",
    "ScientificPreconditionError",
    "assert_atom_mapping",
    "build_task_request",
    "discover_capability",
    "document_digest",
    "load_science_contract_refs",
    "reaction_mapping_digest",
    "register_evidence_report",
    "validate_evidence_report",
    "validate_science_binding",
]

#: Schema id of the frozen B18 science binding document.
SCIENCE_BINDING_SCHEMA = "acp2.runtime.science-binding.v1"
#: Contract-set label A publishes.
SCIENCE_CONTRACT_SET = "acp2.science"

#: Schema id -> published file name under A's ``science/`` directory.
#: The ids are the frozen B03 reservation names; a renamed A schema is a
#: versioned reference update, never a silent rename.
SCIENCE_SCHEMA_FILES: Mapping[str, str] = {
    "acp2.science.capability.v1": "capability.schema.json",
    "acp2.science.recipe.v1": "recipe.schema.json",
    "acp2.science.evidence-report.v1": "evidence.schema.json",
    "acp2.science.reaction-spec.v1": "reaction.schema.json",
    "acp2.science.provenance.v1": "provenance.schema.json",
    "acp2.science.task-binding.v1": "task-binding.schema.json",
    "acp2.science.question.v1": "question.schema.json",
    "acp2.science.errors.v1": "errors.schema.json",
}

#: Documents that pin the semantic rules rather than a JSON Schema.
SCIENCE_DOCUMENT_FILES: Mapping[str, str] = {
    "acp2.science.semantic-rules.v1": "semantic-rules.md",
}

#: The four frozen claim statuses.  No fifth value (``confirmed``/``passed``)
#: is accepted, and execution state is never a member.
CLAIM_STATUSES: tuple[str, ...] = (
    "supported",
    "refuted",
    "inconclusive",
    "not_evaluated",
)
EVIDENCE_STATUS_NOT_EVALUATED = "not_evaluated"

#: Execution terminal states live only on ``report.execution``.
EXECUTION_TERMINAL_STATES: tuple[str, ...] = (
    "completed",
    "failed",
    "cancelled",
    "not_started",
)


# ---------------------------------------------------------------------------
# Typed refusals (named; never a silent coercion)
# ---------------------------------------------------------------------------


class ScienceBindingError(ValueError):
    """A binding/shape violation with a stable code and layer."""

    def __init__(self, code: str, detail: str, *, layer: str = "contract") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.layer = layer


class ScientificPreconditionError(ScienceBindingError):
    """A scientific precondition failed *before* an expensive submission."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, layer="scientific")


class AtomMappingMismatchError(ScientificPreconditionError):
    """The form's atom-mapping identity disagrees with the reaction document."""

    def __init__(self, expected: str, supplied: str) -> None:
        super().__init__(
            "atom_mapping_mismatch",
            f"form mapping digest {supplied!r} != reaction mapping digest {expected!r}",
        )
        self.expected = expected
        self.supplied = supplied


class EvidenceReportError(ScienceBindingError):
    """An EvidenceReport cannot be registered as-is."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, layer="contract")


# ---------------------------------------------------------------------------
# Content digests
# ---------------------------------------------------------------------------


def _sha256_prefixed(data: bytes) -> str:
    return "sha256:" + sha256_bytes(data)


#: Identity forwarder: the single implementation lives in the dependency-free
#: ``content_digest`` module so the worker import closure never pulls the Data
#: artifact layer (and therefore never sqlite) just to digest a document.
document_digest = content_document_digest


# ---------------------------------------------------------------------------
# A contract refs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScienceContractRef:
    """One A-published schema/document reference (id + version + digest)."""

    schema_id: str
    path: str
    version: str
    digest: str
    schema_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "path": self.path,
            "version": self.version,
            "digest": self.digest,
            "schema_version": self.schema_version,
        }


def _schema_version(document: Mapping[str, Any]) -> int | None:
    raw = document.get("schema_version")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    properties = document.get("properties")
    if isinstance(properties, Mapping):
        nested = properties.get("schema_version")
        if isinstance(nested, Mapping):
            const = nested.get("const")
            if isinstance(const, int) and not isinstance(const, bool):
                return const
    return None


def _version_for(schema_version: int | None) -> str:
    if schema_version is None:
        return "1.0.0"
    return f"{schema_version}.0.0"


def load_science_contract_refs(science_root: str | Path) -> dict[str, ScienceContractRef]:
    """Read A's published contract files and return ``schema_id -> ref``.

    Only JSON/bytes are read; nothing is imported and no A Python parser is
    copied.  A missing file is a ``missing_contract`` refusal — a reserved
    digest is never treated as delivered.
    """
    root = Path(science_root)
    refs: dict[str, ScienceContractRef] = {}
    for schema_id, name in SCIENCE_SCHEMA_FILES.items():
        path = root / name
        if not path.is_file():
            raise ScienceBindingError("missing_contract", f"{schema_id} not found at {path}")
        raw = path.read_bytes()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScienceBindingError("invalid_contract", f"{schema_id}: {exc}") from exc
        if not isinstance(document, Mapping):
            raise ScienceBindingError("invalid_contract", f"{schema_id} is not a JSON object")
        schema_version = _schema_version(document)
        if schema_version is not None and schema_version < 1:
            raise ScienceBindingError("invalid_contract", f"{schema_id} has invalid version")
        refs[schema_id] = ScienceContractRef(
            schema_id=schema_id,
            path=str(path),
            version=_version_for(schema_version),
            digest=_sha256_prefixed(raw),
            schema_version=schema_version,
        )
    for schema_id, name in SCIENCE_DOCUMENT_FILES.items():
        path = root / name
        if not path.is_file():
            raise ScienceBindingError("missing_contract", f"{schema_id} not found at {path}")
        refs[schema_id] = ScienceContractRef(
            schema_id=schema_id,
            path=str(path),
            version="1.0.0",
            digest=_sha256_prefixed(path.read_bytes()),
            schema_version=None,
        )
    return refs


def validate_science_binding(binding: Mapping[str, Any]) -> list[str]:
    """Return structural errors for the B18 science-binding document.

    The binding must carry a delivered reference for every required A schema
    with a real digest; ``PENDING`` is never executable.
    """
    errors: list[str] = []
    if binding.get("schema") != SCIENCE_BINDING_SCHEMA:
        errors.append("science_binding.invalid_schema")
    science = binding.get("science_contracts")
    if not isinstance(science, Mapping):
        errors.append("science_binding.missing_science_contracts")
        return errors
    entries = science.get("entries")
    if not isinstance(entries, list):
        errors.append("science_binding.missing_entries")
        return errors
    by_id = {
        entry.get("schema_id"): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("schema_id"), str)
    }
    for schema_id in SCIENCE_SCHEMA_FILES:
        entry = by_id.get(schema_id)
        if entry is None:
            errors.append(f"science_binding.missing_contract:{schema_id}")
            continue
        if entry.get("status") != "delivered":
            errors.append(f"science_binding.not_delivered:{schema_id}")
        digest = entry.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            errors.append(f"science_binding.invalid_digest:{schema_id}")
        if entry.get("types_copied") is not False:
            errors.append(f"science_binding.types_copied:{schema_id}")
    return errors


# ---------------------------------------------------------------------------
# Discovery: A capability descriptor -> the fields a C form may supply
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityDiscovery:
    """What B learns from A's descriptor + task binding (no defaults invented)."""

    capability_id: str
    task_kind: str
    implementation_id: str
    delegation_target: str
    request_schema_version: int
    result_schema_version: int
    required_form_fields: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    validation_owner: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "task_kind": self.task_kind,
            "implementation_id": self.implementation_id,
            "delegation_target": self.delegation_target,
            "request_schema_version": self.request_schema_version,
            "result_schema_version": self.result_schema_version,
            "required_form_fields": list(self.required_form_fields),
            "limitations": list(self.limitations),
            "validation_owner": dict(self.validation_owner),
        }


def discover_capability(
    capability: Mapping[str, Any],
    task_binding: Mapping[str, Any],
) -> CapabilityDiscovery:
    """Validate A's descriptor/task binding and project the form contract.

    The projection lists the values a form must supply.  It never contains a
    method/charge/spin default: A's descriptor carries limitations and
    preconditions, and the platform is forbidden from inventing science.
    """
    if capability.get("schema") != "acp2.science.capability":
        raise ScienceBindingError(
            "unsupported_capability", "descriptor schema is not capability.v1"
        )
    if capability.get("schema_version") != 1:
        raise ScienceBindingError(
            "unknown_major_version",
            f"capability schema_version {capability.get('schema_version')!r} is not executable",
        )
    if task_binding.get("schema") != "acp2.science.task_binding":
        raise ScienceBindingError(
            "unsupported_capability", "task binding schema is not task_binding.v1"
        )
    if task_binding.get("schema_version") != 1:
        raise ScienceBindingError(
            "unknown_major_version",
            f"binding schema_version {task_binding.get('schema_version')!r} is not executable",
        )
    capability_id = capability.get("capability_id")
    selection = task_binding.get("selection")
    metadata = (task_binding.get("binding_output") or {}).get("binding_metadata") or {}
    if not isinstance(capability_id, str) or not capability_id:
        raise ScienceBindingError("invalid_capability", "capability_id must be a non-empty string")
    if not isinstance(selection, Mapping):
        raise ScienceBindingError("invalid_capability", "task binding has no selection")
    selected_id = selection.get("capability_id")
    if selected_id is not None and selected_id != capability_id:
        raise ScienceBindingError(
            "capability_not_in_registry",
            f"binding selects {selected_id!r}, descriptor is {capability_id!r}",
        )
    task_kind = metadata.get("resolved_task_kind")
    if not isinstance(task_kind, str) or task_kind not in {kind.value for kind in TaskKind}:
        raise ScienceBindingError(
            "unknown_implementation", f"resolved_task_kind {task_kind!r} is not a TaskKind"
        )
    request_version = metadata.get("request_schema_version")
    result_version = metadata.get("result_schema_version")
    if request_version != TASK_REQUEST_SCHEMA_VERSION:
        raise ScienceBindingError(
            "unsupported_capability",
            f"request schema version {request_version!r} != {TASK_REQUEST_SCHEMA_VERSION}",
        )
    limitations = tuple(
        str(item.get("text"))
        for item in capability.get("limitations") or []
        if isinstance(item, Mapping) and item.get("text")
    )
    layers = task_binding.get("validation_layers") or {}
    owners = {
        str(name): str((layer or {}).get("owner"))
        for name, layer in layers.items()
        if isinstance(layer, Mapping)
    }
    return CapabilityDiscovery(
        capability_id=capability_id,
        task_kind=task_kind,
        implementation_id=str(metadata.get("implementation_id") or ""),
        delegation_target=str(metadata.get("delegation_target") or ""),
        request_schema_version=int(request_version),
        result_schema_version=int(result_version or 0),
        required_form_fields=(
            "workflow",
            "structure",
            "method",
            "charge",
            "multiplicity",
            "mapping",
            "resources",
        ),
        limitations=limitations,
        validation_owner=owners,
    )


# ---------------------------------------------------------------------------
# Atom-mapping identity (not a mapping algorithm)
# ---------------------------------------------------------------------------


def reaction_mapping_digest(reaction: Mapping[str, Any]) -> str:
    """Identity digest of a reaction document's ``atom_mapping`` block.

    A owns the mapping semantics.  B only needs a stable identity to bind the
    form to the reaction; re-deriving or repairing the mapping here would be a
    second implementation of A's science.
    """
    if reaction.get("schema_version") != 1:
        raise ScienceBindingError(
            "unknown_major_version",
            f"reaction schema_version {reaction.get('schema_version')!r} is not executable",
        )
    mapping = reaction.get("atom_mapping")
    if not isinstance(mapping, Mapping):
        raise ScienceBindingError("missing_atom_mapping", "reaction has no atom_mapping block")
    for key in ("left_to_right", "right_to_left", "unmapped_left", "unmapped_right"):
        if key not in mapping:
            raise ScienceBindingError("missing_atom_mapping", f"atom_mapping.{key} is absent")
    return document_digest(mapping)


def assert_atom_mapping(form_mapping_digest: str, reaction: Mapping[str, Any]) -> str:
    """Reject a form whose mapping identity differs from the reaction.

    A mismatched mapping is never "adopted" or silently repaired: the caller
    must re-discover and re-render the form.
    """
    expected = reaction_mapping_digest(reaction)
    supplied = str(form_mapping_digest or "")
    if supplied != expected:
        raise AtomMappingMismatchError(expected, supplied)
    return expected


# ---------------------------------------------------------------------------
# C P2 minimal form -> TaskRequest
# ---------------------------------------------------------------------------

_FORM_FIELDS = frozenset(
    {
        "question_id",
        "workflow",
        "structure",
        "method",
        "charge",
        "multiplicity",
        "mapping",
        "mapping_digest",
        "resources",
        "options",
        "backend",
        "electronic_state",
        "structure_role",
    }
)
_METHOD_FIELDS = frozenset(
    {
        "method",
        "basis",
        "dispersion",
        "solvent",
        "solvent_model",
        "integration_grid",
        "scf",
        "ri_approximation",
    }
)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScientificPreconditionError("invalid_scientific_input", f"{label} must be an object")
    return value


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScientificPreconditionError(
            "invalid_scientific_input", f"{label} must be an explicit integer"
        )
    return value


def _structure_input(structure: Mapping[str, Any], *, role: str) -> StructureInput:
    """Build a cccp structure reference from a C form value (no parsing)."""
    if not isinstance(structure.get("path"), str) and not (
        isinstance(structure.get("coordinates"), list)
        and isinstance(structure.get("symbols"), list)
    ):
        raise ScientificPreconditionError(
            "structure_supplied",
            "form.structure needs a relative path or C-parsed coordinates+symbols",
        )
    payload: dict[str, Any] = {
        "role": role,
        "source": str(structure.get("source") or "form"),
    }
    if isinstance(structure.get("path"), str):
        payload["path"] = structure["path"]
    if isinstance(structure.get("coordinates"), list):
        payload["coordinates"] = structure["coordinates"]
    if isinstance(structure.get("symbols"), list):
        payload["symbols"] = structure["symbols"]
    if isinstance(structure.get("elements"), list):
        payload["elements"] = structure["elements"]
    try:
        parsed = StructureInput.from_dict(payload)
    except (ValueError, TaskInputError) as exc:
        raise ScientificPreconditionError("structure_supplied", str(exc)) from exc
    if parsed is None:
        raise ScientificPreconditionError("structure_supplied", "structure resolved to empty")
    return parsed


def build_task_request(
    form: Mapping[str, Any],
    discovery: CapabilityDiscovery,
    *,
    reaction: Mapping[str, Any] | None = None,
) -> TaskRequest:
    """Map a C P2 minimal form document onto one cccp ``TaskRequest``.

    The form is *values only* (C's manual research form), never a second job
    payload.  Every scientific value must be explicit in the form; this
    function adds no method, charge, spin or mapping default.
    """
    unknown = sorted(set(form) - _FORM_FIELDS)
    if unknown:
        raise ScienceBindingError(
            "unknown_execution_field", f"form carries unsupported execution fields: {unknown}"
        )
    workflow = form.get("workflow")
    if workflow != discovery.task_kind:
        raise ScientificPreconditionError(
            "unsupported_capability",
            f"form workflow {workflow!r} does not match capability task {discovery.task_kind!r}",
        )
    try:
        task = TaskKind(discovery.task_kind)
    except ValueError as exc:
        raise ScienceBindingError("unknown_implementation", str(exc)) from exc

    method_payload = _require_mapping(form.get("method"), "form.method")
    unknown_method = sorted(set(method_payload) - _METHOD_FIELDS)
    if unknown_method:
        raise ScienceBindingError(
            "unknown_execution_field", f"form.method carries unsupported fields: {unknown_method}"
        )
    method = str(method_payload.get("method") or "").strip()
    if not method:
        # A scientific precondition, checked before any submission.
        raise ScientificPreconditionError(
            "method_identity_missing",
            "form.method.method is required; the platform fills no default",
        )
    try:
        level = MethodSpec(
            method=method,
            basis=str(method_payload.get("basis") or ""),
            dispersion=method_payload.get("dispersion"),
            solvent=method_payload.get("solvent"),
            solvent_model=method_payload.get("solvent_model"),
            integration_grid=method_payload.get("integration_grid"),
            scf=method_payload.get("scf"),
            ri_approximation=method_payload.get("ri_approximation"),
        )
    except (TypeError, ValueError) as exc:
        raise ScientificPreconditionError("method_or_basis", str(exc)) from exc

    charge = _require_int(form.get("charge"), "form.charge")
    multiplicity = _require_int(form.get("multiplicity"), "form.multiplicity")
    if multiplicity < 1:
        raise ScientificPreconditionError("charge_or_multiplicity", "multiplicity must be >= 1")

    mapping_digest = str(form.get("mapping_digest") or "")
    if reaction is not None:
        mapping_digest = assert_atom_mapping(mapping_digest, reaction)
    elif not mapping_digest and form.get("mapping"):
        # A mapping identity string alone is not a digest; refuse to adopt it.
        raise ScientificPreconditionError(
            "missing_atom_mapping",
            "form.mapping requires form.mapping_digest for execution; "
            "an opaque label is not identity",
        )

    structure_payload = _require_mapping(form.get("structure"), "form.structure")
    role = str(form.get("structure_role") or structure_payload.get("role") or "minimum")
    structure = _structure_input(structure_payload, role=role)

    resources_payload = form.get("resources")
    resources = TaskResources()
    if resources_payload is not None:
        if not isinstance(resources_payload, Mapping):
            raise ScientificPreconditionError(
                "invalid_scientific_input", "form.resources must be an object"
            )
        try:
            resources = TaskResources.from_dict(resources_payload)
        except TaskInputError as exc:
            raise ScientificPreconditionError("invalid_scientific_input", str(exc)) from exc

    options = None
    if task is TaskKind.SINGLEPOINT:
        raw_options = form.get("options")
        if raw_options is not None and not isinstance(raw_options, Mapping):
            raise ScientificPreconditionError(
                "invalid_scientific_input", "form.options must be an object"
            )
        try:
            options = SinglePointOptions.from_dict(raw_options)
        except TaskInputError as exc:
            raise ScientificPreconditionError("invalid_scientific_input", str(exc)) from exc

    request = TaskRequest(
        task=task,
        structure=structure,
        charge=charge,
        multiplicity=multiplicity,
        level=level,
        backend=form.get("backend") if isinstance(form.get("backend"), str) else None,
        options=options,
        resources=resources,
    )
    try:
        validate_request(request)
    except TaskInputError as exc:
        # Reject scientific preconditions before an expensive submission.
        raise ScientificPreconditionError("invalid_scientific_input", str(exc)) from exc
    return request


# ---------------------------------------------------------------------------
# EvidenceReport registration (storage is B12; interpretation is A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceArtifactManifest:
    """Registration mapping for A's EvidenceReport and its native files.

    ``claim_statuses`` preserves A's vocabulary verbatim.  There is no
    ``claim_supported`` field by design: a completed execution is only
    ``task_execution_completed``.
    """

    report_id: str
    report_ref: ArtifactRef
    native_files: tuple[ArtifactRef, ...]
    provenance_digest: str
    limitations: tuple[str, ...]
    claim_statuses: tuple[tuple[str, str], ...]
    execution_terminal_state: str
    task_execution_completed: bool
    claim_interpretation: str = "not_interpreted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "acp2.runtime.evidence-manifest.v1",
            "report_id": self.report_id,
            "report": self.report_ref.to_dict(),
            "native_files": [item.to_dict() for item in self.native_files],
            "provenance_digest": self.provenance_digest,
            "limitations": list(self.limitations),
            "claim_statuses": [
                {"claim_id": claim_id, "status": status} for claim_id, status in self.claim_statuses
            ],
            "execution_terminal_state": self.execution_terminal_state,
            "task_execution_completed": self.task_execution_completed,
            "claim_interpretation": self.claim_interpretation,
        }


def _require_status(value: Any) -> str:
    status = str(value or "")
    if status not in CLAIM_STATUSES:
        raise EvidenceReportError(
            "unknown_claim_status",
            f"claim status {value!r} is not one of {list(CLAIM_STATUSES)}",
        )
    return status


def validate_evidence_report(report: Mapping[str, Any]) -> None:
    """Validate the structural contract of A's EvidenceReport.

    This does not decide truth.  It enforces the frozen vocabulary and the
    structural separation between execution facts and claim status.
    """
    if report.get("schema_version") != 1:
        raise EvidenceReportError(
            "unknown_major_version",
            f"evidence schema_version {report.get('schema_version')!r} is not executable",
        )
    if not str(report.get("report_id") or "").strip():
        raise EvidenceReportError("missing_report_id", "report_id is required")
    subject = report.get("subject")
    if not isinstance(subject, Mapping) or not str(subject.get("subject_id") or ""):
        raise EvidenceReportError("missing_subject", "subject.subject_id is required")
    execution = report.get("execution")
    if not isinstance(execution, Mapping):
        raise EvidenceReportError("missing_execution", "execution facts are required")
    terminal = execution.get("terminal_state")
    if terminal not in EXECUTION_TERMINAL_STATES:
        raise EvidenceReportError(
            "invalid_execution_state",
            f"execution.terminal_state {terminal!r} is not a frozen execution state",
        )
    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        raise EvidenceReportError("missing_claims", "at least one claim is required")
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise EvidenceReportError("invalid_claim", f"claims[{index}] is not an object")
        status = _require_status(claim.get("status"))
        if status in {"supported", "refuted"}:
            sources = claim.get("source_refs")
            if not isinstance(sources, list) or not sources:
                raise EvidenceReportError(
                    "missing_source_refs",
                    f"claims[{index}] status {status!r} needs at least one source ref",
                )
    limitations = report.get("limitations")
    if limitations is not None and not isinstance(limitations, list):
        raise EvidenceReportError("invalid_limitations", "limitations must be an array")


def _native_file_ref(
    *,
    job_id: str,
    attempt: int,
    resource_id: str,
    generation: int,
    relative_path: str,
    role: str,
    digest: str,
    size_bytes: int,
    authorization: Any = None,
) -> ArtifactRef:
    return ArtifactRef(
        job_id=job_id,
        attempt=attempt,
        resource_id=resource_id,
        generation=generation,
        digest=digest,
        size_bytes=size_bytes,
        relative_path=relative_path,
        media_type="application/octet-stream",
        source_schema="",
        source_refs=({"role": role},),
        authorization=authorization
        if isinstance(authorization, ArtifactAuthorization)
        else ArtifactAuthorization(),
    )


def register_evidence_report(
    report: Mapping[str, Any],
    *,
    store: ArtifactStore,
    job_id: str,
    attempt: int,
    native_files: Sequence[Mapping[str, Any]] = (),
    provenance_digest: str = "",
    generation: int = 1,
    resource_id: str = "evidence_report",
    authorization: Any = None,
) -> EvidenceArtifactManifest:
    """Publish A's EvidenceReport + native files through B12's store.

    The report bytes are stored verbatim (canonical JSON of the supplied
    document, the same serialization B12 uses), read back, and compared.  The
    returned manifest preserves every claim status literally.
    """
    validate_evidence_report(report)
    report_bytes = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    report_ref = ArtifactRef(
        job_id=job_id,
        attempt=attempt,
        resource_id=resource_id,
        generation=generation,
        digest="",
        size_bytes=len(report_bytes),
        relative_path=f"{resource_id}.json",
        media_type="application/json",
        source_schema="acp2.science.evidence-report.v1",
        source_refs=tuple(
            {
                "artifact_path": str(item.get("artifact_path") or ""),
                "role": str(item.get("role") or ""),
                "digest": str(item.get("digest") or ""),
            }
            for item in _claim_sources(report)
        ),
        authorization=authorization
        if isinstance(authorization, ArtifactAuthorization)
        else ArtifactAuthorization(),
    )
    published = store.publish(report_ref, report_bytes)
    served = store.read(published)
    if served != report_bytes:
        raise EvidenceReportError(
            "evidence_digest_mismatch", "stored EvidenceReport bytes differ from the input"
        )

    file_refs: list[ArtifactRef] = []
    for index, entry in enumerate(native_files):
        if not isinstance(entry, Mapping):
            raise EvidenceReportError(
                "invalid_native_file", f"native_files[{index}] is not an object"
            )
        body = entry.get("body")
        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, (bytes, bytearray)):
            body_bytes = bytes(body)
        else:
            raise EvidenceReportError(
                "invalid_native_file", f"native_files[{index}].body must be str/bytes"
            )
        relative_path = str(entry.get("relative_path") or f"native/{index}.bin")
        role = str(entry.get("role") or "native_output")
        ref = _native_file_ref(
            job_id=job_id,
            attempt=attempt,
            resource_id=str(entry.get("resource_id") or f"{resource_id}_native_{index}"),
            generation=int(entry.get("generation") or generation),
            relative_path=relative_path,
            role=role,
            digest="",
            size_bytes=len(body_bytes),
            authorization=authorization,
        )
        published_file = store.publish(ref, body_bytes)
        served_file = store.read(published_file)
        if served_file != body_bytes:
            raise EvidenceReportError(
                "native_digest_mismatch", f"native file {relative_path} round-trip mismatch"
            )
        file_refs.append(published_file)

    claims = report.get("claims") or []
    claim_statuses = tuple(
        (str(claim.get("claim_id")), str(claim.get("status")))
        for claim in claims
        if isinstance(claim, Mapping)
    )
    limitations = tuple(
        str(item.get("description"))
        for item in report.get("limitations") or []
        if isinstance(item, Mapping) and item.get("description")
    )
    execution = report.get("execution") if isinstance(report.get("execution"), Mapping) else {}
    terminal = str(execution.get("terminal_state") or "not_started")
    return EvidenceArtifactManifest(
        report_id=str(report.get("report_id")),
        report_ref=published,
        native_files=tuple(file_refs),
        provenance_digest=str(provenance_digest or ""),
        limitations=limitations,
        claim_statuses=claim_statuses,
        execution_terminal_state=terminal,
        task_execution_completed=terminal == "completed",
    )


def _claim_sources(report: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for claim in report.get("claims") or []:
        if not isinstance(claim, Mapping):
            continue
        for source in claim.get("source_refs") or []:
            if isinstance(source, Mapping):
                yield source
