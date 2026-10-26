"""Canonical parameter normalisation shared by every application entry.

B06 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 7).  MCP, HTTP and
the Controller client must produce **the same parameters for the same logical
action**.  This module is the single normaliser: the MCP composition root
(:mod:`acp.services_mcp_bootstrap`) and the additive v1 routes both call these
functions, so a field is never interpreted differently per entry.

The module is stdlib-only and imports nothing from :mod:`acp`.  It performs
*shape* validation only — scientific/workflow validation stays in the ACP
catalog and the Controller route.  A normalisation failure raises
:class:`ApplicationParamError` carrying the frozen ``machine_output`` code
(``input_invalid``) so both the HTTP layer and the MCP layer can map it without
inventing a second vocabulary.

M0-06 (G23): exhaustive field policy
------------------------------------
:data:`SUBMIT_FIELD_POLICY` / :data:`PREFLIGHT_FIELD_POLICY` /
:data:`PREPARE_FIELD_POLICY` are the **single authority** for which request
field means what, at which entry, in which request kind.  Every declared field
of the corresponding v1 DTO must appear there exactly once (anti-drift guard:
``tests/test_acp2_request_field_equivalence.py`` compares the tables against the
Pydantic models and against the registered
``runtime/application-api.v1.json`` contract fields).

Field-policy vocabulary (frozen for v1):

``job_spec``
    Mapped onto the effective :class:`JobSpec` field named by
    ``FieldPolicy.job_spec_field`` by every entry.
``request_control``
    Request identity/versioning (``schema_version``, ``idempotency_key``,
    ``preflight_fingerprint``).  Never part of the scientific plan.
``research``
    B-req-01 research launch fields (approval/plan/budget binding).
``server_path_rejected``
    A server filesystem path.  A client may not choose where the Controller
    writes (``output_dir``) or which host config it loads (``config_path``);
    the field is *declared* (so ``extra="forbid"`` reports the truthful
    ``unsupported_server_path`` rule instead of pretending it never existed)
    and rejected at ``http``/``mcp``.
``legacy_allowlist``
    Accepted for a named set of still-registered consumers, never used for
    execution, and scheduled for removal (``removal_version`` +
    ``exit_condition``).  Empty in v1: the mechanical inventory (frontend,
    RPH bridge, CLI, ``scripts/acceptance/**``, registered SDK contract) found
    no undeclared field in use — see :data:`LEGACY_REQUEST_FIELD_ALLOWLIST`.
    There is deliberately **no** "ignore unknown fields and warn" mode.

Server path fields are rejected with ``params={"field": ..., "rule":
"unsupported_server_path"}``; an undeclared field is rejected with
``params={"field": ..., "rule": "unknown_field"}``.  Both surface as the
``input_invalid`` application error at HTTP (422), MCP and CLI.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "APPLICATION_SCHEMA_VERSION",
    "ApplicationParamError",
    "ENTRIES",
    "ENTRY_CLI",
    "ENTRY_HTTP",
    "ENTRY_MCP",
    "FIELD_POLICY",
    "FieldPolicy",
    "LEGACY_REQUEST_FIELD_ALLOWLIST",
    "NODE_TAGS_MAX_COUNT",
    "NODE_TAGS_MAX_LENGTH",
    "PREFLIGHT_FIELD_POLICY",
    "PREPARE_FIELD_POLICY",
    "REQUEST_PREFLIGHT",
    "REQUEST_PREPARE",
    "REQUEST_SUBMIT",
    "SUBMIT_FIELD_POLICY",
    "canonical_submit_request",
    "field_policy_gaps",
    "normalize_analyze",
    "normalize_node_tags",
    "normalize_preflight",
    "normalize_prepare",
    "normalize_read",
    "normalize_submit",
    "validate_request_fields",
]

#: Contract major version accepted by every application command.
APPLICATION_SCHEMA_VERSION = 1

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_BUDGET_UNITS = frozenset({"task_count", "core_hours", "walltime_s"})

#: Application entries the field policy is frozen for.  ``legacy-http`` (the
#: unversioned ``/api/jobs`` surface retained for F0 display compatibility) and
#: ``acp run`` (a local operator that builds a JobSpec directly, never an
#: application request payload) are deliberately **not** application entries:
#: they are not part of the ACP 2.0 request contract.
ENTRY_HTTP = "http"
ENTRY_MCP = "mcp"
ENTRY_CLI = "cli"
ENTRIES: frozenset[str] = frozenset({ENTRY_HTTP, ENTRY_MCP, ENTRY_CLI})

#: Request kinds with a frozen field policy.
REQUEST_SUBMIT = "submit"
REQUEST_PREFLIGHT = "preflight"
REQUEST_PREPARE = "prepare"

#: Field dispositions of the policy table.
DISPOSITION_JOB_SPEC = "job_spec"
DISPOSITION_REQUEST_CONTROL = "request_control"
DISPOSITION_RESEARCH = "research"
DISPOSITION_SERVER_PATH_REJECTED = "server_path_rejected"
DISPOSITION_LEGACY_ALLOWLIST = "legacy_allowlist"

#: Frozen ``node_tags`` limits (moved here from ``acp.api.v1_schemas`` so the
#: DTO, the CLI and the canonical request share one implementation).
NODE_TAGS_MAX_COUNT = 16
NODE_TAGS_MAX_LENGTH = 64


class ApplicationParamError(ValueError):
    """A parameter violates the frozen application contract.

    ``code`` is always a :class:`acp_services.entrypoints.machine_output.
    ErrorCode` value; ``params`` is structured context (never prose parsing).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "input_invalid",
        params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.params = dict(params) if params else {}


@dataclass(frozen=True)
class FieldPolicy:
    """One row of the authoritative request-field policy.

    Attributes:
        name: Wire field name.
        disposition: One of the ``DISPOSITION_*`` values.
        entries: Application entries allowed to send the field.
        request_kinds: Request kinds that declare the field.
        job_spec_field: Effective :class:`JobSpec` attribute for a
            ``job_spec`` field (``None`` otherwise).
        removal_version: Release that removes an allowlisted field (required
            for ``legacy_allowlist``).
        exit_condition: What must be true before the allowlist row is removed
            (required for ``legacy_allowlist``).
        note: Why the field exists / what a client may expect.
    """

    name: str
    disposition: str
    entries: frozenset[str]
    request_kinds: frozenset[str]
    job_spec_field: str | None = None
    removal_version: str | None = None
    exit_condition: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.disposition not in {
            DISPOSITION_JOB_SPEC,
            DISPOSITION_REQUEST_CONTROL,
            DISPOSITION_RESEARCH,
            DISPOSITION_SERVER_PATH_REJECTED,
            DISPOSITION_LEGACY_ALLOWLIST,
        }:
            raise ValueError(f"unknown field disposition {self.disposition!r}")
        if self.disposition == DISPOSITION_JOB_SPEC and not self.job_spec_field:
            raise ValueError(f"{self.name}: a job_spec field must name its target attribute")
        if self.disposition == DISPOSITION_LEGACY_ALLOWLIST and not (
            self.removal_version and self.exit_condition
        ):
            raise ValueError(
                f"{self.name}: a legacy allowlist row requires removal_version and exit_condition"
            )


def _entry(
    name: str,
    disposition: str,
    *,
    job_spec_field: str | None = None,
    request_kinds: Iterable[str] = (REQUEST_SUBMIT,),
    entries: Iterable[str] = (ENTRY_HTTP, ENTRY_MCP, ENTRY_CLI),
    note: str = "",
) -> FieldPolicy:
    return FieldPolicy(
        name=name,
        disposition=disposition,
        entries=frozenset(entries),
        request_kinds=frozenset(request_kinds),
        job_spec_field=job_spec_field,
        note=note,
    )


#: The submit request policy.  Must be exhaustive over
#: ``acp.api.v1_schemas.V1JobCreateRequest.model_fields`` (anti-drift test).
SUBMIT_FIELD_POLICY: dict[str, FieldPolicy] = {
    field.name: field
    for field in (
        _entry(
            "schema_version",
            DISPOSITION_REQUEST_CONTROL,
            note="Contract major version; recorded in the request digest.",
        ),
        _entry("workflow", DISPOSITION_JOB_SPEC, job_spec_field="workflow"),
        _entry("name", DISPOSITION_JOB_SPEC, job_spec_field="name"),
        _entry("input", DISPOSITION_JOB_SPEC, job_spec_field="input"),
        _entry("method", DISPOSITION_JOB_SPEC, job_spec_field="method"),
        _entry("resources", DISPOSITION_JOB_SPEC, job_spec_field="resources"),
        _entry("tags", DISPOSITION_JOB_SPEC, job_spec_field="tags"),
        _entry("node_tags", DISPOSITION_JOB_SPEC, job_spec_field="node_tags"),
        _entry("project_id", DISPOSITION_JOB_SPEC, job_spec_field="project_id"),
        _entry("execution_mode", DISPOSITION_JOB_SPEC, job_spec_field="execution_mode"),
        _entry("target_node", DISPOSITION_JOB_SPEC, job_spec_field="target_node"),
        _entry("molecule_name", DISPOSITION_JOB_SPEC, job_spec_field="molecule_name"),
        _entry("task_name", DISPOSITION_JOB_SPEC, job_spec_field="task_name"),
        _entry("remark", DISPOSITION_JOB_SPEC, job_spec_field="remark"),
        _entry(
            "output_dir",
            DISPOSITION_SERVER_PATH_REJECTED,
            entries=(),
            note="Server filesystem path: the task directory is allocated by the Controller.",
        ),
        _entry(
            "config_path",
            DISPOSITION_SERVER_PATH_REJECTED,
            entries=(),
            note="Server filesystem path: the Controller owns its configuration sources.",
        ),
        _entry(
            "idempotency_key",
            DISPOSITION_REQUEST_CONTROL,
            note="Raw client retry key; excluded from the request digest.",
        ),
        _entry(
            "preflight_fingerprint",
            DISPOSITION_REQUEST_CONTROL,
            note="Optional binding to the plan digest a preflight returned (B1-3).",
        ),
        _entry(
            "extensions",
            DISPOSITION_REQUEST_CONTROL,
            note=(
                "Namespaced extension metadata (unknown_field_policy U4); "
                "opaque to the platform and never used for execution."
            ),
        ),
        _entry("plan_digest", DISPOSITION_RESEARCH),
        _entry("proposal_ref", DISPOSITION_RESEARCH),
        _entry("proposal_digest", DISPOSITION_RESEARCH),
        _entry("budget", DISPOSITION_RESEARCH),
    )
}

#: The zero-side-effect preflight policy.  Preflight is a *dry run of the same
#: submit request* (the frontend builds both from one normalised body), so it
#: accepts exactly the submit field set — including the execution-target, naming
#: and research-binding fields — and rejects exactly the same server-path fields.
#: Naming/tags/research fields never enter the science plan digest; they are
#: accepted so a shared request body is not rejected at the preflight step and a
#: target rejection is decided by the *same* check before and after a submit.
PREFLIGHT_FIELD_POLICY: dict[str, FieldPolicy] = {
    name: replace(row, request_kinds=row.request_kinds | {REQUEST_PREFLIGHT})
    for name, row in SUBMIT_FIELD_POLICY.items()
}

#: The explicit prepare policy.  The MCP adapter forwards the request unchanged
#: (M0-06) and this table is the drift guard that says every prepare DTO field
#: is a known one.  Prepare has no execution-field semantics.
#:
#: **Authority (D-IDENT-10, M0-11)**: the live HTTP route
#: ``POST /api/v1/jobs/{job_id}/prepare`` (``src/acp/api/v1_routes.py::
#: prepare_artifact``) is the one authority for *which prepare fields exist and
#: what they mean*: it accepts exactly ``V1PrepareRequest`` (``extra="forbid"``),
#: normalises it once through :func:`normalize_prepare`, and forwards the result
#: into ``acp_services.data.operations.PrepareRequest``.  Every row below names
#: the field the route actually reads; a field the route does not read is not a
#: prepare field.
#:
#: Consequences that are easy to get backwards:
#:
#: * ``resource_id`` is **not** a wire field.  The route derives it
#:   (``artifact_id`` for ``purpose=artifact``, ``structure-input:<relative_path>``
#:   otherwise) and passes it as ``PrepareRequest.resource_id``; a caller that
#:   sends ``resource_id`` is rejected as ``unknown_field`` like any other
#:   undeclared field.  An MCP adapter / SDK bridge argument must be named
#:   ``artifact_id``.
#: * ``purpose`` is required (``artifact`` | ``structure-input``) and
#:   ``artifact_id`` is required when ``purpose=artifact``.
#: * ``extensions`` is a *submit* control field; prepare does not accept it.
PREPARE_FIELD_POLICY: dict[str, FieldPolicy] = {
    field.name: field
    for field in (
        _entry(
            "schema_version",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Contract major version; any value other than 1 executes nothing.",
        ),
        _entry(
            "purpose",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Required. artifact = fetch/commit one artifact revision; "
                "structure-input = local parse+register with no task."
            ),
        ),
        _entry(
            "task_ref",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Alternate task reference ({task_id, attempt, ...}); task_id must equal the "
                "job id in the route path and supplies the default attempt."
            ),
        ),
        _entry(
            "job_id",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "The route path is authoritative; a body job_id that differs is "
                "revision_conflict, never a second target."
            ),
        ),
        _entry(
            "artifact_id",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Required when purpose=artifact. Safe artifact token; becomes "
                "PrepareRequest.resource_id (server-derived, never client-sent)."
            ),
        ),
        _entry(
            "attempt",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "1-based attempt; defaults to the current attempt and may never be newer "
                "than it (attempt_not_current)."
            ),
        ),
        _entry(
            "generation",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Artifact generation; defaults to 1 for artifact / 0 for structure-input "
                "and is re-checked by the completion CAS."
            ),
        ),
        _entry(
            "expected_revision",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Declared and normalised for CAS-style callers; the prepare route does not "
                "act on it (an artifact prepare is deduplicated by content identity)."
            ),
        ),
        _entry(
            "relative_path",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Relative path inside the attempt work dir; traversal is refused, and for "
                "purpose=structure-input it names the parsed structure source."
            ),
        ),
        _entry(
            "expected_digest",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Trusted digest expectation (sha256:); a mismatch is a conflict and the "
                "committed value is always re-derived."
            ),
        ),
        _entry(
            "content_digest",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Fallback spelling of expected_digest (expected_digest wins).",
        ),
        _entry(
            "expected_size_bytes",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Trusted size expectation; the committed size is re-derived.",
        ),
        _entry(
            "schema_ref",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Source schema reference, recorded as the operation payload source_schema.",
        ),
        _entry(
            "source_refs",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Source references recorded verbatim in the operation payload.",
        ),
        _entry(
            "evidence_report",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Evidence report attached to the artifact; when present the relative path is "
                "taken as declared evidence rather than re-validated as a fetch target."
            ),
        ),
        _entry(
            "media_type",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note="Recorded media type of the prepared body.",
        ),
        _entry(
            "idempotency_key",
            DISPOSITION_REQUEST_CONTROL,
            request_kinds=(REQUEST_PREPARE,),
            note=(
                "Raw client retry key; the server derives the versioned key from "
                "(principal, project, client key, epoch)."
            ),
        ),
    )
}

#: Request kind → authoritative policy table.
FIELD_POLICY: dict[str, dict[str, FieldPolicy]] = {
    REQUEST_SUBMIT: SUBMIT_FIELD_POLICY,
    REQUEST_PREFLIGHT: PREFLIGHT_FIELD_POLICY,
    REQUEST_PREPARE: PREPARE_FIELD_POLICY,
}

#: Explicit legacy allowlist.  Empty in v1: the M0-06 mechanical inventory of
#: every registered consumer found no undeclared field in use.
#:
#: Inventory (2026-10-27, ``git grep``/AST scan of the checkouts below):
#:
#: * ``frontend/js/**`` — submit builders send workflow, molecule_name, remark,
#:   input, method, resources, project_id, task_name, target_node, node_tags;
#:   science-question.js adds plan_digest/proposal_ref/proposal_digest/budget and
#:   preflight_fingerprint.
#: * ``rph/packages/bridge/src/index.ts::buildToolArguments`` — workflow, input,
#:   method, project_id, resources, idempotency_key.
#: * ``src/acp/cli.py`` — workflow, input, method, resources, name, tags,
#:   node_tags, project_id, execution_mode, target_node, molecule_name,
#:   task_name, remark, idempotency_key.
#: * ``scripts/acceptance/**`` — every POST /api/v1/jobs body uses declared
#:   fields only (``acp2_runtime.py``, ``four_combinations.py``,
#:   ``acp2_event_recovery.py``, ``acp2_package_install.py``,
#:   ``compatibility_checks.py``, ``real_environment.py``).
#: * registered SDK contract ``runtime/application-api.v1.json`` — see the
#:   additive fields registered in M0-06.
#:
#: A future row MUST carry ``removal_version`` and ``exit_condition``; the
#: dataclass rejects a row without them.
LEGACY_REQUEST_FIELD_ALLOWLIST: dict[str, FieldPolicy] = {}


def field_policy_gaps(
    declared: Iterable[str], *, request_kind: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(unregistered, stale)`` field names for a request kind.

    ``unregistered`` fields are declared by a DTO but absent from the policy
    (the anti-drift failure M0-06 guards); ``stale`` fields are in the policy
    but no longer declared.
    """
    table = FIELD_POLICY[request_kind]
    declared_set = set(declared)
    unregistered = tuple(sorted(declared_set - set(table)))
    stale = tuple(sorted(set(table) - declared_set))
    return unregistered, stale


def normalize_node_tags(values: Iterable[str] | None) -> list[str]:
    """Sanitize a ``node_tags`` list: strip, drop empties, dedupe (order kept).

    Caps the list at :data:`NODE_TAGS_MAX_COUNT` tags of at most
    :data:`NODE_TAGS_MAX_LENGTH` chars.  Over-limit entries are dropped with a
    warning; a list that normalizes to empty imposes no tag constraint (never an
    unsatisfiable filter).  Single implementation shared by the v1 DTO, the CLI
    and :func:`canonical_submit_request`.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ApplicationParamError(
            "node_tags must be an array of strings",
            params={"field": "node_tags", "rule": "type"},
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ApplicationParamError(
                "node_tags entry must be a string",
                params={"field": "node_tags", "rule": "type"},
            )
        tag = raw.strip()
        if not tag or tag in seen:
            continue
        if len(tag) > NODE_TAGS_MAX_LENGTH:
            logger.warning(
                "node_tags entry dropped (exceeds %d chars): %.64s",
                NODE_TAGS_MAX_LENGTH,
                tag,
            )
            continue
        if len(cleaned) >= NODE_TAGS_MAX_COUNT:
            logger.warning(
                "node_tags entry dropped (more than %d tags): %.64s",
                NODE_TAGS_MAX_COUNT,
                tag,
            )
            continue
        cleaned.append(tag)
        seen.add(tag)
    return cleaned


def validate_request_fields(payload: Mapping[str, Any], *, request_kind: str, entry: str) -> None:
    """Reject an undeclared or entry-forbidden request field.

    Raises:
        ApplicationParamError: ``rule="unknown_field"`` for a field that is not
            in the policy at all, ``rule="unsupported_server_path"`` for a
            server filesystem path field at an entry that does not accept it.
    """
    if request_kind not in FIELD_POLICY:
        raise ValueError(f"unknown request kind {request_kind!r}")
    if entry not in ENTRIES:
        raise ValueError(f"unknown application entry {entry!r}")
    table = FIELD_POLICY[request_kind]
    unknown = sorted(str(name) for name in payload if str(name) not in table)
    if unknown:
        raise ApplicationParamError(
            f"{request_kind} request has an undeclared field {unknown[0]!r}",
            params={
                "field": unknown[0],
                "rule": "unknown_field",
                "fields": unknown,
                "request_kind": request_kind,
                "entry": entry,
            },
        )
    rejected = sorted(
        str(name)
        for name in payload
        if table[str(name)].disposition == DISPOSITION_SERVER_PATH_REJECTED
        and entry not in table[str(name)].entries
    )
    if rejected:
        raise ApplicationParamError(
            f"{request_kind} request may not choose the server path field {rejected[0]!r}",
            params={
                "field": rejected[0],
                "rule": "unsupported_server_path",
                "fields": rejected,
                "request_kind": request_kind,
                "entry": entry,
            },
        )


def canonical_submit_request(
    payload: Mapping[str, Any], *, entry: str = ENTRY_HTTP
) -> dict[str, Any]:
    """Return the frozen effective submit request for *entry*.

    This is the **same** object for the same logical request at HTTP, MCP and
    CLI: field policy applied, defaults materialised (``name``/``molecule_name``/
    ``task_name``/``remark`` are always present as strings, ``tags``/``node_tags``
    are always lists), ``node_tags`` normalised, and every research/control
    field validated.  It is both the JobSpec source for an entry that builds its
    own spec (CLI) and the material of the canonical request digest
    (:func:`acp_services.runtime.idempotency.canonical_request_digest`), so two
    entries cannot disagree about what "the same request" means.

    Values are kept verbatim (apart from ``str.strip`` on the identity fields the
    pre-M0-06 normaliser already stripped); the digest rule must not change for a
    request that was already accepted.
    """
    data = _require_mapping(payload, field="request")
    validate_request_fields(data, request_kind=REQUEST_SUBMIT, entry=entry)
    _schema_version(data)
    workflow = _string(data, "workflow")
    if workflow is None:
        raise ApplicationParamError(
            "workflow is required", params={"field": "workflow", "rule": "required"}
        )
    input_value = data.get("input")
    if input_value is None:
        raise ApplicationParamError(
            "input is required", params={"field": "input", "rule": "required"}
        )
    canonical: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "workflow": workflow,
        "name": _verbatim_text(data, "name"),
        "input": _require_mapping(input_value, field="input"),
        "method": _verbatim_mapping(data, "method"),
        "resources": _verbatim_mapping(data, "resources"),
        "tags": _string_list(data, "tags"),
        "node_tags": normalize_node_tags(data.get("node_tags")),
        "molecule_name": _verbatim_text(data, "molecule_name"),
        "task_name": _verbatim_text(data, "task_name"),
        "remark": _verbatim_text(data, "remark"),
    }
    for field in ("project_id", "execution_mode", "target_node"):
        value = _string(data, field)
        if value is not None:
            canonical[field] = value
    plan_digest = _optional_digest(data, "plan_digest")
    if plan_digest is not None:
        canonical["plan_digest"] = plan_digest
    proposal_digest = _optional_digest(data, "proposal_digest")
    if proposal_digest is not None:
        canonical["proposal_digest"] = proposal_digest
    proposal_ref = _string(data, "proposal_ref")
    if proposal_ref is not None:
        canonical["proposal_ref"] = proposal_ref
    budget = _normalize_budget(data.get("budget"))
    if budget is not None:
        canonical["budget"] = budget
    idempotency = _optional_idempotency(data)
    if idempotency is not None:
        canonical["idempotency_key"] = idempotency
    preflight_fingerprint = _string(data, "preflight_fingerprint")
    if preflight_fingerprint is not None:
        canonical["preflight_fingerprint"] = preflight_fingerprint
    extensions = data.get("extensions")
    if extensions is not None:
        canonical["extensions"] = _require_mapping(extensions, field="extensions")
    if proposal_ref is not None and plan_digest is None:
        raise ApplicationParamError(
            "a research launch requires plan_digest",
            params={"field": "plan_digest", "rule": "plan_digest_required"},
        )
    return canonical


def _require_mapping(payload: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ApplicationParamError(
            f"{field} must be an object", params={"field": field, "rule": "type"}
        )
    return dict(payload)


def _schema_version(payload: Mapping[str, Any]) -> int:
    raw = payload.get("schema_version", APPLICATION_SCHEMA_VERSION)
    if raw != APPLICATION_SCHEMA_VERSION:
        raise ApplicationParamError(
            "unsupported application schema version",
            params={
                "field": "schema_version",
                "rule": "unsupported_major_version",
                "expected": APPLICATION_SCHEMA_VERSION,
                "actual": raw,
            },
        )
    return APPLICATION_SCHEMA_VERSION


def _string(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApplicationParamError(
            f"{field} must be a non-empty string",
            params={"field": field, "rule": "type"},
        )
    return value.strip()


def _verbatim_text(payload: Mapping[str, Any], field: str) -> str:
    """Return a display string verbatim (``""`` when absent).

    Naming fields are the task-directory components: they are compared across
    entries byte-for-byte, so they are never silently stripped or defaulted to a
    different value here.
    """
    value = payload.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApplicationParamError(
            f"{field} must be a string", params={"field": field, "rule": "type"}
        )
    return value


def _verbatim_mapping(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if value is None:
        return {}
    return _require_mapping(value, field=field)


def _string_list(payload: Mapping[str, Any], field: str) -> list[str]:
    value = payload.get(field)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ApplicationParamError(
            f"{field} must be an array of strings",
            params={"field": field, "rule": "type"},
        )
    return [str(item) for item in value]


def _optional_int(
    payload: Mapping[str, Any], field: str, *, minimum: int | None = None
) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationParamError(
            f"{field} must be an integer", params={"field": field, "rule": "type"}
        )
    if minimum is not None and value < minimum:
        raise ApplicationParamError(
            f"{field} must be >= {minimum}",
            params={"field": field, "rule": "minimum", "minimum": minimum},
        )
    return value


def _optional_digest(payload: Mapping[str, Any], field: str) -> str | None:
    value = _string(payload, field)
    if value is None:
        return None
    if not _DIGEST.match(value):
        raise ApplicationParamError(
            f"{field} must be a sha256: digest",
            params={"field": field, "rule": "pattern", "pattern": "^sha256:[0-9a-f]{64}$"},
        )
    return value


def _optional_idempotency(payload: Mapping[str, Any]) -> str | None:
    value = _string(payload, "idempotency_key")
    if value is None:
        return None
    if not _IDEMPOTENCY.match(value):
        raise ApplicationParamError(
            "idempotency_key is malformed",
            params={
                "field": "idempotency_key",
                "rule": "pattern",
                "pattern": "^[A-Za-z0-9._:-]{8,128}$",
            },
        )
    return value


#: Fields of the registered v1 submit ``budget`` object (B-req-01).  The D09
#: shape (``wall_seconds``/``queue_seconds``/tokens/``core_hours``) is the
#: approval/proposal budget, not the v1 launch field, so an unknown key here is a
#: 422 ``unknown_field`` instead of being silently dropped by the normalizer.
_BUDGET_KEYS = frozenset({"unit", "count", "timeout_s"})


def _normalize_budget(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    budget = _require_mapping(value, field="budget")
    unknown = sorted(str(key) for key in budget if key not in _BUDGET_KEYS)
    if unknown:
        raise ApplicationParamError(
            "budget carries undeclared field(s): " + ", ".join(unknown),
            params={
                "field": f"budget.{unknown[0]}",
                "rule": "unknown_field",
                "fields": [f"budget.{name}" for name in unknown],
            },
        )
    unit = budget.get("unit")
    if unit not in _BUDGET_UNITS:
        raise ApplicationParamError(
            "budget.unit must be one of task_count/core_hours/walltime_s",
            params={"field": "budget.unit", "rule": "enum", "allowed": sorted(_BUDGET_UNITS)},
        )
    timeout_s = budget.get("timeout_s")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        raise ApplicationParamError(
            "budget.timeout_s must be a positive integer",
            params={"field": "budget.timeout_s", "rule": "minimum", "minimum": 1},
        )
    count = budget.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise ApplicationParamError(
            "budget.count must be an integer >= 1",
            params={"field": "budget.count", "rule": "minimum", "minimum": 1},
        )
    normalized = {"unit": unit, "timeout_s": timeout_s}
    if count is not None:
        normalized["count"] = count
    return normalized


def _common_submit_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    _schema_version(payload)
    workflow = _string(payload, "workflow")
    if workflow is None:
        raise ApplicationParamError(
            "workflow is required", params={"field": "workflow", "rule": "required"}
        )
    input_value = payload.get("input")
    if input_value is None:
        raise ApplicationParamError(
            "input is required", params={"field": "input", "rule": "required"}
        )
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "workflow": workflow,
        "input": _require_mapping(input_value, field="input"),
    }
    for field in ("method", "resources", "extensions"):
        value = payload.get(field)
        if value is not None:
            data[field] = _require_mapping(value, field=field)
    project_id = _string(payload, "project_id")
    if project_id is not None:
        data["project_id"] = project_id
    return data


def normalize_preflight(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a zero-side-effect preflight request."""
    return _common_submit_fields(_require_mapping(payload, field="request"))


def normalize_submit(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a submit request (additive B-req-01 research fields included)."""
    data = _common_submit_fields(_require_mapping(payload, field="request"))
    plan_digest = _optional_digest(payload, "plan_digest")
    if plan_digest is not None:
        data["plan_digest"] = plan_digest
    proposal_digest = _optional_digest(payload, "proposal_digest")
    if proposal_digest is not None:
        data["proposal_digest"] = proposal_digest
    proposal_ref = _string(payload, "proposal_ref")
    if proposal_ref is not None:
        data["proposal_ref"] = proposal_ref
    budget = _normalize_budget(payload.get("budget"))
    if budget is not None:
        data["budget"] = budget
    idempotency = _optional_idempotency(payload)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    preflight_fingerprint = _string(payload, "preflight_fingerprint")
    if preflight_fingerprint is not None:
        data["preflight_fingerprint"] = preflight_fingerprint
    if proposal_ref is not None and plan_digest is None:
        raise ApplicationParamError(
            "a research launch requires plan_digest",
            params={"field": "plan_digest", "rule": "plan_digest_required"},
        )
    return data


def normalize_prepare(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an explicit prepare request."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    purpose = _string(request, "purpose")
    if purpose not in {"artifact", "structure-input"}:
        raise ApplicationParamError(
            "purpose must be artifact or structure-input",
            params={
                "field": "purpose",
                "rule": "enum",
                "allowed": ["artifact", "structure-input"],
            },
        )
    data: dict[str, Any] = {"schema_version": APPLICATION_SCHEMA_VERSION, "purpose": purpose}
    task_ref = request.get("task_ref")
    if task_ref is not None:
        data["task_ref"] = _require_mapping(task_ref, field="task_ref")
    job_id = _string(request, "job_id")
    if job_id is not None:
        data["job_id"] = job_id
    artifact_id = _string(request, "artifact_id")
    if artifact_id is not None:
        data["artifact_id"] = artifact_id
    attempt = _optional_int(request, "attempt", minimum=1)
    if attempt is not None:
        data["attempt"] = attempt
    generation = _optional_int(request, "generation", minimum=1)
    if generation is not None:
        data["generation"] = generation
    expected_revision = _optional_int(request, "expected_revision", minimum=0)
    if expected_revision is not None:
        data["expected_revision"] = expected_revision
    relative_path = _string(request, "relative_path")
    if relative_path is not None:
        data["relative_path"] = relative_path
    # B12 artifact facts: accepted verbatim (the executor re-derives the
    # committed digest/size; a supplied digest/size is a trusted expectation).
    for field in ("expected_digest", "content_digest", "media_type", "schema_ref"):
        value = _string(request, field)
        if value is not None:
            data[field] = value
    expected_size = _optional_int(request, "expected_size_bytes", minimum=0)
    if expected_size is not None:
        data["expected_size_bytes"] = expected_size
    source_refs = request.get("source_refs")
    if source_refs is not None:
        if not isinstance(source_refs, list) or not all(
            isinstance(item, Mapping) for item in source_refs
        ):
            raise ApplicationParamError(
                "source_refs must be an array of objects",
                params={"field": "source_refs", "rule": "type"},
            )
        data["source_refs"] = [dict(item) for item in source_refs]
    evidence_report = request.get("evidence_report")
    if evidence_report is not None:
        if not isinstance(evidence_report, Mapping):
            raise ApplicationParamError(
                "evidence_report must be an object",
                params={"field": "evidence_report", "rule": "type"},
            )
        data["evidence_report"] = dict(evidence_report)
    idempotency = _optional_idempotency(request)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    if purpose == "artifact":
        if "task_ref" not in data and "job_id" not in data:
            raise ApplicationParamError(
                "prepare(purpose=artifact) requires task_ref or job_id",
                params={"field": "task_ref", "rule": "required"},
            )
        if artifact_id is None:
            raise ApplicationParamError(
                "prepare(purpose=artifact) requires artifact_id",
                params={"field": "artifact_id", "rule": "required"},
            )
    return data


def normalize_read(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a cache-only artifact read request."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    artifact_ref = request.get("artifact_ref")
    if artifact_ref is None:
        raise ApplicationParamError(
            "artifact_ref is required", params={"field": "artifact_ref", "rule": "required"}
        )
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "artifact_ref": _require_mapping(artifact_ref, field="artifact_ref"),
    }
    for field in ("max_bytes", "encoding"):
        value = request.get(field)
        if value is not None:
            data[field] = value
    return data


def normalize_analyze(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise an explicit analysis request (``explicit=true`` is mandatory)."""
    request = _require_mapping(payload, field="request")
    _schema_version(request)
    if request.get("explicit") is not True:
        raise ApplicationParamError(
            "analysis requires explicit=true",
            params={"field": "explicit", "rule": "explicit_required"},
        )
    analysis_id = _string(request, "analysis_id")
    if analysis_id is None:
        raise ApplicationParamError(
            "analysis_id is required", params={"field": "analysis_id", "rule": "required"}
        )
    input_refs = request.get("input_refs")
    if not isinstance(input_refs, list) or not input_refs:
        raise ApplicationParamError(
            "input_refs must be a non-empty array",
            params={"field": "input_refs", "rule": "required"},
        )
    normalized_refs = [
        _require_mapping(item, field=f"input_refs[{index}]")
        for index, item in enumerate(input_refs)
    ]
    data: dict[str, Any] = {
        "schema_version": APPLICATION_SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "input_refs": normalized_refs,
        "explicit": True,
    }
    parameters = request.get("parameters")
    if parameters is not None:
        data["parameters"] = _require_mapping(parameters, field="parameters")
    plan_digest = _optional_digest(request, "plan_digest")
    if plan_digest is not None:
        data["plan_digest"] = plan_digest
    idempotency = _optional_idempotency(request)
    if idempotency is not None:
        data["idempotency_key"] = idempotency
    return data
