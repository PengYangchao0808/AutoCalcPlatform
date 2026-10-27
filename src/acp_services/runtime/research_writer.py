"""Minimal research-resource writer (M1-06 / first half of M2-01a, G02/G14).

This is the application service the thin vertical slice needs: create / read /
compare-and-swap a C-shaped ``Proposal`` and create / read the ``ResearchRecord``
of one round, with the server owning every identity and every digest.

It deliberately implements **no second store**: the documents live in
:class:`acp_services.data.research_resources.ResearchResourceStore` (B18 Data
authority), approvals live in :class:`acp_services.runtime.approvals.ApprovalService`
(B16), the research identity and its round come from
:mod:`acp_services.runtime.research_session` (M1-02), the submit classification
comes from :mod:`acp_services.runtime.submit_policy` (M1-04b) and every digest
comes from :mod:`acp_services.runtime.canonical_digest` (the one implementation).
The record's evidence binding is admitted through
:mod:`acp_services.runtime.evidence_projection` (M1-03): a decision can never be
published without the digest-bound view and the frozen decision policy.

What this module refuses to do (later M2-01 checkpoints own it):

* no cross-database saga/outbox (b), no round/evidence append saga (c);
* no P2 MCP tool registration (M2-01c) and no direct MCP write;
* no execution fact: it never creates a job row, a ``JobStatus`` or a
  ``WAITING_REVIEW`` task.

Isolation: stdlib plus first-party ``acp_services``; ``acp`` is never imported.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.contracts.events import EventScope, OwnerType
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceAuthorizationError,
    ResearchResourceConflictError,
    ResearchResourceError,
    ResearchResourceNotFoundError,
    ResearchResourceRecord,
    ResearchResourceRequest,
    ResearchResourceRevokedError,
    ResearchResourceStore,
    ResearchResourceValidationError,
    research_resource_key,
)
from acp_services.runtime.approvals import (
    Approval,
    ApprovalError,
    ApprovalService,
)
from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    CanonicalDigestError,
    frozen_plan_definition,
    proposal_plan_definition_digest,
)
from acp_services.runtime.canonical_digest import (
    plan_digest as canonical_plan_digest,
)
from acp_services.runtime.canonical_digest import (
    proposal_digest as canonical_proposal_digest,
)
from acp_services.runtime.evidence_projection import (
    EvidenceProjectionError,
    EvidenceView,
    ResearchDecisionError,
    TaskRef,
    decide_evidence,
    verify_attribution,
)
from acp_services.runtime.local_outbox import ensure_research_outbox, research_outbox
from acp_services.runtime.outbox import OutboxEventSpec
from acp_services.runtime.research_session import (
    DECISION_VALUES,
    ResearchIdentity,
    ResearchSessionConflictError,
    ResearchSessionValidationError,
    SubmitIntent,
    supersede_approval_for_plan_change,
)
from acp_services.runtime.science_inputs import ScienceInputError, normalize_science_inputs
from acp_services.runtime.submit_policy import SubmitClassification, SubmitProbe, classify_submit
from acp_services.workspace.command_identity import CommandIdentity

__all__ = [
    "PROPOSAL_DECISIONS",
    "RECORD_DECISION_VALUES",
    "ResearchWriteConflictError",
    "ResearchWriteDeniedError",
    "ResearchWriteError",
    "ResearchWriter",
    "ProposalWriteResult",
    "RecordWriteResult",
    "proposal_write_digests",
    "record_authority_violations",
]

#: The C ``Proposal.decision`` vocabulary (frozen by ``proposal.schema.json``).
PROPOSAL_DECISIONS: tuple[str, ...] = (
    "proposed",
    "approved",
    "rejected",
    "superseded",
    "deferred",
)

#: The record decision vocabulary is M1-02's (never a task status).
RECORD_DECISION_VALUES: tuple[str, ...] = DECISION_VALUES

#: Fields a Proposal must carry before the platform will bind an approval to it.
#: The *structural* C schema stays C's (``proposal.schema.json`` + the frontend
#: contract gate); the platform only enforces the fields its own binding uses.
_PROPOSAL_REQUIRED: tuple[str, ...] = (
    "proposal_id",
    "project_id",
    "science",
    "budget",
    "recipe_digest",
    "round",
)

_PROPOSAL_ID_RE = re.compile(r"^prop-[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Errors (every refusal is named and carries a stable rule)
# ---------------------------------------------------------------------------


class ResearchWriteError(RuntimeError):
    """Base error of the research writer; never a silent coercion."""

    code = "research_write_error"
    http_status = 400

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "params": {"rule": self.rule}}


class ResearchWriteDeniedError(ResearchWriteError):
    """The verified identity/project/approval does not authorize this write."""

    code = "access_denied"
    http_status = 403


class ResearchWriteConflictError(ResearchWriteError):
    """A stale revision, a changed plan digest or an existing resource."""

    code = "revision_conflict"
    http_status = 409

    def __init__(
        self,
        rule: str,
        message: str,
        *,
        current: ResearchResourceRecord | None = None,
        next_round: int | None = None,
    ) -> None:
        super().__init__(rule, message)
        self.current = current
        self.next_round = next_round

    @property
    def current_revision(self) -> int | None:
        """The observed head revision when the refusal carried one."""
        return None if self.current is None else self.current.revision

    def detail(self) -> dict[str, Any]:
        payload = super().detail()
        params = payload["params"]
        if self.current is not None:
            params["current_revision"] = self.current.revision
            params["current_digest"] = self.current.digest
        if self.next_round is not None:
            params["next_round"] = self.next_round
        return payload


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProposalWriteResult:
    """One accepted proposal write (create or CAS)."""

    identity: ResearchIdentity
    round: int
    record: ResearchResourceRecord
    approval: Approval
    digests: Mapping[str, str]
    superseded_approval: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROPOSAL_SCHEMA,
            "resource_id": self.record.resource_id,
            "revision": self.record.revision,
            "digest": self.record.digest,
            "document": dict(self.record.document),
            "research": {
                "principal_id": self.identity.principal_id,
                "project_id": self.identity.project_id,
                "research_id": self.identity.research_id,
                "session_id": self.identity.session_id,
                "round": self.round,
            },
            "approval": self.approval.to_dict(),
            "digests": dict(self.digests),
            "superseded_approval": self.superseded_approval,
        }


@dataclass(frozen=True, slots=True)
class RecordWriteResult:
    """One accepted research-record write."""

    identity: ResearchIdentity
    round: int
    record: ResearchResourceRecord
    decision: str
    projection_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_RECORD_SCHEMA,
            "resource_id": self.record.resource_id,
            "revision": self.record.revision,
            "digest": self.record.digest,
            "document": dict(self.record.document),
            "research": {
                "principal_id": self.identity.principal_id,
                "project_id": self.identity.project_id,
                "research_id": self.identity.research_id,
                "session_id": self.identity.session_id,
                "round": self.round,
            },
            "decision": self.decision,
            "projection_digest": self.projection_digest,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_digest(value: Any, label: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.match(text) is None:
        raise ResearchWriteError("invalid_digest", f"{label} must be sha256:<64 lowercase hex>")
    return text


def _require_proposal(document: Any) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise ResearchWriteError("invalid_proposal", "the proposal must be an object")
    missing = [name for name in _PROPOSAL_REQUIRED if name not in document]
    if missing:
        raise ResearchWriteError(
            "missing_proposal_field", "the proposal is missing: " + ", ".join(missing)
        )
    proposal_id = str(document.get("proposal_id") or "")
    if _PROPOSAL_ID_RE.match(proposal_id) is None:
        raise ResearchWriteError(
            "invalid_proposal_id", f"proposal_id {proposal_id!r} is not prop-<id>"
        )
    _require_digest(document.get("recipe_digest"), "recipe_digest")
    decision = str(document.get("decision") or "proposed")
    if decision not in PROPOSAL_DECISIONS:
        raise ResearchWriteError(
            "invalid_decision",
            f"proposal decision {decision!r} is not one of {list(PROPOSAL_DECISIONS)}",
        )
    round_index = document.get("round")
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        raise ResearchWriteError("invalid_round", "proposal round must be an integer >= 0")
    if not isinstance(document.get("science"), Mapping):
        raise ResearchWriteError("invalid_science", "proposal.science must be an object")
    if not isinstance(document.get("budget"), Mapping):
        raise ResearchWriteError("invalid_budget", "proposal.budget must be an object")
    return document


def _require_typed_science(science: Mapping[str, Any]) -> None:
    """Reject a science block whose critical values are missing (no 0/1 default)."""
    payload: dict[str, Any] = {
        key: science[key]
        for key in ("method", "mapping", "charge", "multiplicity")
        if key in science
    }
    initial_inputs = science.get("initial_inputs")
    if isinstance(initial_inputs, list):
        payload["structure_provenance"] = [
            {
                "input_id": str(item.get("input_id") or ""),
                "sha256": str(item.get("sha256") or ""),
                "format": str(item.get("format") or ""),
                "role": str(item.get("role") or "minimum"),
            }
            for item in initial_inputs
            if isinstance(item, Mapping)
        ]
    try:
        verdict = normalize_science_inputs(payload)
    except ScienceInputError as exc:
        raise ResearchWriteError(exc.rule, str(exc)) from exc
    if verdict.values is not None:
        return
    if verdict.clarifications:
        raise ResearchWriteError(
            "clarification_required",
            "the proposal science is missing critical value(s): "
            + ", ".join(item.field_name for item in verdict.clarifications),
        )
    first = verdict.issues[0]
    raise ResearchWriteError(first.rule, first.message)


def _execution_plan(plan: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(workflow, plan_digest)`` for a submitted execution plan."""
    if not isinstance(plan, Mapping):
        raise ResearchWriteError("invalid_plan", "the execution plan must be an object")
    workflow = str(plan.get("workflow") or "")
    if not workflow:
        raise ResearchWriteError("invalid_plan", "the execution plan requires a workflow")
    definition = frozen_plan_definition(
        workflow=workflow,
        input=plan.get("input") if isinstance(plan.get("input"), Mapping) else {},
        method=plan.get("method") if isinstance(plan.get("method"), Mapping) else {},
        resources=plan.get("resources") if isinstance(plan.get("resources"), Mapping) else {},
    )
    try:
        return workflow, canonical_plan_digest(definition).digest
    except CanonicalDigestError as exc:
        raise ResearchWriteError(exc.rule, str(exc)) from exc


def _digests_for(document: Mapping[str, Any], plan_digest: str) -> dict[str, str]:
    """Proposal, science and execution digests of one write (one implementation)."""
    try:
        science_digest = proposal_plan_definition_digest(document).digest
    except CanonicalDigestError as exc:
        raise ResearchWriteError(exc.rule, str(exc)) from exc
    declared = str(document.get("plan_definition_digest") or "")
    if declared and declared != science_digest:
        raise ResearchWriteError(
            "plan_digest_mismatch",
            "proposal.plan_definition_digest does not match the frozen science projection",
        )
    try:
        proposal_digest = canonical_proposal_digest(
            {
                "proposal_id": str(document.get("proposal_id") or ""),
                "project_id": str(document.get("project_id") or ""),
                "plan_digest": plan_digest,
            }
        ).digest
    except CanonicalDigestError as exc:
        raise ResearchWriteError(exc.rule, str(exc)) from exc
    return {
        "canonical_digest_version": CANONICAL_DIGEST_VERSION,
        "science_digest": science_digest,
        "plan_digest": plan_digest,
        "proposal_digest": proposal_digest,
    }


def proposal_write_digests(
    *, proposal: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, str]:
    """Return the science/plan/proposal digests one proposal write binds.

    Public face of the *one* digest computation :meth:`ResearchWriter.create_proposal`
    uses, so a saga/application service can persist the expected digests before
    the write without re-deriving them (M2-01b).

    Raises:
        ResearchWriteError: the proposal or the plan violates the frozen rules.
    """
    document = dict(_require_proposal(proposal))
    _require_typed_science(document["science"])
    _workflow, plan_digest = _execution_plan(plan)
    return dict(_digests_for(document, plan_digest))


#: Keys that carry another authority's live row.  A research record stores
#: *references* plus its own necessary projection — never a copy of a job status,
#: a usage row, a reservation or a receipt (M2-01b guard).
RECORD_AUTHORITY_KEYS: frozenset[str] = frozenset(
    {
        "status",
        "state",
        "job_status",
        "task_status",
        "task_state",
        "current_stage",
        "progress",
        "pid",
        "exit_code",
        "host",
        "node_id",
        "remote_job_id",
        "started_at",
        "completed_at",
        "usage",
        "usage_rows",
        "usage_totals",
        "usage_by_job",
        "budget_rows",
        "reservations",
        "receipt",
        "receipts",
        "receipt_rows",
        "job_spec",
        "task_result",
        "result_manifest",
    }
)


def record_authority_violations(document: Any, *, _path: str = "") -> list[str]:
    """Return the dotted paths of authority copies inside a record document.

    The record may cite a TaskRef (``job_id``/``attempt``/``release_id``) and a
    digest-bound evidence reference, but never a live job/usage/budget row: those
    have exactly one authority and a second copy drifts.  An empty result means
    the document is a reference-only projection.
    """
    violations: list[str] = []
    if isinstance(document, Mapping):
        for key, value in document.items():
            name = str(key)
            path = f"{_path}.{name}" if _path else name
            if name in RECORD_AUTHORITY_KEYS:
                violations.append(path)
                continue
            violations.extend(record_authority_violations(value, _path=path))
    elif isinstance(document, list | tuple):
        for index, value in enumerate(document):
            violations.extend(record_authority_violations(value, _path=f"{_path}[{index}]"))
    return violations


def _proposal_scope(command: CommandIdentity, document: Mapping[str, Any]) -> str:
    """Resolve the effective project of one write (server identity wins)."""
    project = str(command.project_id or "")
    claimed = str(document.get("project_id") or "")
    if claimed and claimed != project:
        raise ResearchWriteDeniedError(
            "project_scope_mismatch",
            f"the proposal names project {claimed!r}, the verified project is {project!r}",
        )
    return project


def _store_error(exc: ResearchResourceError) -> ResearchWriteError:
    if isinstance(exc, ResearchResourceConflictError):
        return ResearchWriteConflictError(exc.code, str(exc), current=exc.current)
    if isinstance(exc, ResearchResourceAuthorizationError):
        return ResearchWriteDeniedError(exc.code, str(exc))
    if isinstance(exc, ResearchResourceNotFoundError):
        return ResearchWriteError("research_resource_not_found", str(exc))
    if isinstance(exc, ResearchResourceRevokedError):
        return ResearchWriteConflictError("research_resource_revoked", str(exc))
    if isinstance(exc, ResearchResourceValidationError):
        return ResearchWriteError(exc.code, str(exc))
    return ResearchWriteError(exc.code, str(exc))


def _classification_error(classification: SubmitClassification) -> ResearchWriteError:
    detail = classification.detail()
    params = dict(detail.get("params") or {})
    if classification.http_status == 403:
        return ResearchWriteDeniedError(
            str(params.get("rule") or classification.rule), detail["message"]
        )
    return ResearchWriteError(str(params.get("rule") or classification.rule), detail["message"])


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


class ResearchWriter:
    """Proposal/record writer over the Data store + Runtime approval authority."""

    def __init__(
        self,
        *,
        resources: ResearchResourceStore,
        approvals: ApprovalService,
    ) -> None:
        if not isinstance(resources, ResearchResourceStore):
            raise ResearchWriteError("invalid_writer", "a ResearchResourceStore is required")
        if not isinstance(approvals, ApprovalService):
            raise ResearchWriteError("invalid_writer", "an ApprovalService is required")
        self._resources = resources
        self._approvals = approvals
        # The research database's local outbox must exist before the first write
        # so a rolled-back write still leaves a readable (empty) outbox table.
        ensure_research_outbox(resources)

    # -- proposal ----------------------------------------------------------

    def create_proposal(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        proposal: Mapping[str, Any],
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> ProposalWriteResult:
        """Create revision 1 of a proposal and request its approval.

        The identity (principal/project/research/session) is allocated from the
        verified :class:`CommandIdentity` *before* the plan is visible (M1-02);
        the approval is bound to both the scientific (``science_digest``) and the
        execution (``plan_digest``) plan, and the returned digests are what the
        client must send back on submit.
        """
        if not isinstance(command, CommandIdentity):
            raise ResearchWriteDeniedError(
                "invalid_identity", "a verified CommandIdentity is required"
            )
        document = dict(_require_proposal(proposal))
        project = _proposal_scope(command, document)
        document["project_id"] = project
        _require_typed_science(document["science"])
        workflow, plan_digest = _execution_plan(plan)
        digests = _digests_for(document, plan_digest)
        identity = ResearchIdentity.allocate(command, session_seed=session_seed)
        if identity.project_id != project:
            raise ResearchWriteDeniedError(
                "project_scope_mismatch",
                "the allocated research identity belongs to another project",
            )
        round_index = int(document.get("round") or 0)
        if round_index != 0:
            raise ResearchWriteError(
                "invalid_round", "a fresh proposal starts at round 0; open a new round by CAS"
            )

        classification = classify_submit(
            SubmitProbe.from_payload(
                {
                    "workflow": workflow,
                    "proposal_ref": document["proposal_id"],
                    "proposal_digest": digests["proposal_digest"],
                    "plan_digest": plan_digest,
                    "budget": budget,
                }
            ),
            command,
            defer_approval=True,
        )
        if not classification.allowed:
            raise _classification_error(classification)

        approval = self._request_approval(
            document=document,
            project=project,
            requester=command.principal_id,
            digests=digests,
            plan=plan,
            budget=budget,
            expires_at=expires_at,
        )
        document["approval_ref"] = approval.approval_id
        document["plan_definition_digest"] = digests["science_digest"]
        resource_id = research_resource_key(project, "proposal", str(document["proposal_id"]))
        try:
            record = self._resources.create(
                ResearchResourceRequest(
                    resource_id=resource_id,
                    schema=PROPOSAL_SCHEMA,
                    document=document,
                    principal_id=command.principal_id,
                    project_id=project,
                )
            )
        except ResearchResourceError as exc:
            raise _store_error(exc) from exc
        return ProposalWriteResult(
            identity=identity,
            round=round_index,
            record=record,
            approval=approval,
            digests=digests,
        )

    def update_proposal(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        proposal_id: str,
        expected_revision: int,
        proposal: Mapping[str, Any],
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> ProposalWriteResult:
        """CAS one proposal revision; a changed plan digest opens a new round.

        The old approval is revoked (optimistic CAS) and a new approval intent is
        requested for the new digests, exactly like M1-02's plan-change chain; no
        execution fact is written and the client must derive a new submit key.
        """
        if not isinstance(command, CommandIdentity):
            raise ResearchWriteDeniedError(
                "invalid_identity", "a verified CommandIdentity is required"
            )
        project = str(command.project_id or "")
        resource_id = research_resource_key(project, "proposal", str(proposal_id))
        current = self._read(
            project=project, principal=command.principal_id, resource_id=resource_id
        )
        if current.document.get("proposal_id") != proposal_id:
            raise ResearchWriteError(
                "invalid_proposal_id", "proposal id does not address this resource"
            )
        document = dict(_require_proposal(proposal))
        document["project_id"] = _proposal_scope(command, document)
        if document["proposal_id"] != proposal_id:
            raise ResearchWriteError(
                "proposal_id_changed", "a CAS may not rename the proposal identity"
            )
        _require_typed_science(document["science"])
        _workflow, plan_digest = _execution_plan(plan)
        digests = _digests_for(document, plan_digest)
        identity = ResearchIdentity.allocate(command, session_seed=session_seed)
        live = self._live_approval(project, str(proposal_id))
        current_science = str(current.document.get("plan_definition_digest") or "")
        plan_changed = live is not None and live.plan_digest != plan_digest
        science_changed = live is not None and (
            (bool(live.science_digest) and live.science_digest != digests["science_digest"])
            or (bool(current_science) and current_science != digests["science_digest"])
        )
        superseded = ""
        round_index = int(document.get("round") or 0)
        if live is not None and (plan_changed or science_changed):
            if plan_changed:
                approval = self._supersede_plan_change(
                    live,
                    identity=identity,
                    new_digest=plan_digest,
                    document=document,
                    project=project,
                    requester=command.principal_id,
                    proposal_digest=digests["proposal_digest"],
                    plan=plan,
                    budget=budget,
                    expires_at=expires_at,
                )
            else:
                approval = self._supersede_science_change(
                    live,
                    document=document,
                    project=project,
                    requester=command.principal_id,
                    digests=digests,
                    plan=plan,
                    budget=budget,
                    expires_at=expires_at,
                )
            superseded = live.approval_id
            round_index = int(document.get("round") or 0) + 1
        else:
            approval = self._request_approval(
                document=document,
                project=project,
                requester=command.principal_id,
                digests=digests,
                plan=plan,
                budget=budget,
                expires_at=expires_at,
            )
        document["approval_ref"] = approval.approval_id
        document["plan_definition_digest"] = digests["science_digest"]
        document["round"] = round_index
        try:
            record = self._resources.compare_and_swap(
                resource_id,
                expected_revision=expected_revision,
                document=document,
                principal_id=command.principal_id,
                project_id=project,
            )
        except ResearchResourceError as exc:
            raise _store_error(exc) from exc
        return ProposalWriteResult(
            identity=identity,
            round=round_index,
            record=record,
            approval=approval,
            digests=digests,
            superseded_approval=superseded,
        )

    # -- record ------------------------------------------------------------

    def create_record(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        record: Mapping[str, Any],
        evidence_view: Mapping[str, Any] | None = None,
    ) -> RecordWriteResult:
        """Create revision 1 of the round record (evidence refs + decision).

        The optional ``evidence_view`` is the M1-03 projection the client just
        read: it is re-admitted (digest re-verified), attributed to this research
        identity/round and the frozen decision policy is applied.  A decision is
        never published from a missing, tampered or inconclusive view.
        """
        if not isinstance(command, CommandIdentity):
            raise ResearchWriteDeniedError(
                "invalid_identity", "a verified CommandIdentity is required"
            )
        identity, document, decision, projection_digest = self._assemble_record(
            command=command,
            session_seed=session_seed,
            record=record,
            evidence_view=evidence_view,
        )
        resource_id = research_resource_key(document["project_id"], "record", identity.research_id)
        try:
            written = self._resources.create(
                ResearchResourceRequest(
                    resource_id=resource_id,
                    schema=RESEARCH_RECORD_SCHEMA,
                    document=document,
                    principal_id=command.principal_id,
                    project_id=document["project_id"],
                )
            )
        except ResearchResourceError as exc:
            raise _store_error(exc) from exc
        return RecordWriteResult(
            identity=identity,
            round=int(document["round"]),
            record=written,
            decision=decision,
            projection_digest=projection_digest,
        )

    def append_record_round(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        research_id: str,
        expected_revision: int,
        record: Mapping[str, Any],
        evidence_view: Mapping[str, Any] | None = None,
    ) -> RecordWriteResult:
        """Append one new round to an existing record (CAS, one transaction).

        A round is a new *record revision* of the same ``research_id``: the round
        index advances by one, the evidence refs are extended, and the write is an
        optimistic CAS on the observed ``expected_revision``.  The resource
        revision and its local outbox row commit together
        (:func:`acp_services.runtime.local_outbox.research_outbox`), so a crash can
        never leave a round without its event — or an event without its round.
        """
        if not isinstance(command, CommandIdentity):
            raise ResearchWriteDeniedError(
                "invalid_identity", "a verified CommandIdentity is required"
            )
        identity = ResearchIdentity.allocate(command, session_seed=session_seed)
        if str(research_id) != identity.research_id:
            raise ResearchWriteDeniedError(
                "research_identity_mismatch",
                "the addressed record is not the identity derived for this session",
            )
        current = self.read_record(
            principal_id=command.principal_id,
            project_id=str(command.project_id or ""),
            research_id=str(research_id),
        )
        merged = dict(record)
        merged.setdefault("research_id", research_id)
        merged.setdefault("project_id", current.document.get("project_id"))
        merged.setdefault("approval_ref", current.document.get("approval_ref"))
        merged.setdefault("plan_definition_digest", current.document.get("plan_definition_digest"))
        merged["round"] = int(current.document.get("round") or 0) + 1
        merged["created_at"] = str(
            merged.get("created_at") or current.document.get("created_at") or ""
        )
        _identity, document, decision, projection_digest = self._assemble_record(
            command=command,
            session_seed=session_seed,
            record=merged,
            evidence_view=evidence_view,
            record_revision=int(current.document.get("record_revision") or 1) + 1,
        )
        try:
            written = research_outbox(
                self._resources,
                events=[
                    OutboxEventSpec(
                        event_type="research.record.round_appended",
                        payload={
                            "resource_id": current.resource_id,
                            "research_id": str(research_id),
                            "round": int(document["round"]),
                            "decision": decision,
                            "record_revision": int(document["record_revision"]),
                        },
                        scope=EventScope.PROJECT,
                        owner_type=OwnerType.DATA,
                    )
                ],
                owner_id=current.resource_id,
                business=lambda conn: self._resources.compare_and_swap_in_transaction(
                    conn,
                    current.resource_id,
                    expected_revision=int(expected_revision),
                    document=document,
                    principal_id=command.principal_id,
                    project_id=str(command.project_id or ""),
                ),
                revision=int(document["record_revision"]),
            )
        except ResearchResourceError as exc:
            raise _store_error(exc) from exc
        return RecordWriteResult(
            identity=identity,
            round=int(document["round"]),
            record=written,
            decision=decision,
            projection_digest=projection_digest,
        )

    def _assemble_record(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        record: Mapping[str, Any],
        evidence_view: Mapping[str, Any] | None,
        record_revision: int = 1,
    ) -> tuple[ResearchIdentity, dict[str, Any], str, str]:
        """Validate one record payload and build its frozen document.

        The single implementation shared by :meth:`create_record` and
        :meth:`append_record_round`; every refusal keeps its named rule.
        """
        if not isinstance(record, Mapping):
            raise ResearchWriteError("invalid_record", "the record must be an object")
        project = str(command.project_id or "")
        claimed = str(record.get("project_id") or "")
        if claimed and claimed != project:
            raise ResearchWriteDeniedError(
                "project_scope_mismatch",
                f"the record names project {claimed!r}, the verified project is {project!r}",
            )
        identity = ResearchIdentity.allocate(command, session_seed=session_seed)
        claimed_research = str(record.get("research_id") or "")
        if claimed_research and claimed_research != identity.research_id:
            raise ResearchWriteDeniedError(
                "research_identity_mismatch",
                "the record research_id is not the identity derived for this session",
            )
        round_index = record.get("round")
        if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
            raise ResearchWriteError("invalid_round", "record round must be an integer >= 0")
        decision = str(record.get("decision") or "")
        if decision not in RECORD_DECISION_VALUES:
            raise ResearchWriteError(
                "invalid_decision",
                f"record decision {decision!r} is not one of {list(RECORD_DECISION_VALUES)}",
            )
        task_refs = record.get("task_refs")
        if not isinstance(task_refs, list) or len(task_refs) != 1:
            raise ResearchWriteError("invalid_task_ref", "a round record binds exactly one TaskRef")
        binding = task_refs[0]
        if not isinstance(binding, Mapping):
            raise ResearchWriteError("invalid_task_ref", "the TaskRef must be an object")
        if str(binding.get("project_id") or "") != project:
            raise ResearchWriteDeniedError(
                "project_scope_mismatch", "the TaskRef crosses the project"
            )
        evidence_refs = record.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ResearchWriteError(
                "evidence_ref_required", "a record decision must cite digest-bound evidence"
            )
        for index, ref in enumerate(evidence_refs):
            if not isinstance(ref, Mapping):
                raise ResearchWriteError(
                    "invalid_evidence", f"evidence_refs[{index}] is not an object"
                )
            _require_digest(ref.get("sha256"), f"evidence_refs[{index}].sha256")

        projection_digest = ""
        if evidence_view is not None:
            projection_digest = self._verify_view(
                evidence_view,
                identity=identity,
                round_index=round_index,
                decision=decision,
                binding=binding,
                evidence_refs=evidence_refs,
            )

        proposal_ref = str(record.get("proposal_ref") or "")
        approval_ref = str(record.get("approval_ref") or "")
        if approval_ref:
            self._require_approved_record(
                approval_ref=approval_ref,
                project=project,
                principal=command.principal_id,
                plan_definition_digest=str(record.get("plan_definition_digest") or ""),
            )
        elif proposal_ref:
            raise ResearchWriteError(
                "approval_required", "a research record binds the approval of its round"
            )

        document = {
            "research_id": identity.research_id,
            "project_id": project,
            "round": round_index,
            "decision": decision,
            "plan_definition_digest": _require_digest(
                record.get("plan_definition_digest"), "plan_definition_digest"
            ),
            "step_identity_refs": list(record.get("step_identity_refs") or []),
            "task_refs": [
                {
                    "project_id": str(binding.get("project_id") or ""),
                    "job_id": str(binding.get("job_id") or binding.get("task_id") or ""),
                    "attempt": int(binding.get("attempt") or 0),
                    "release_id": str(binding.get("release_id") or ""),
                }
            ],
            "evidence_refs": [
                {
                    "evidence_id": str(ref.get("evidence_id") or ""),
                    "kind": str(ref.get("kind") or ""),
                    "sha256": str(ref.get("sha256") or ""),
                    "relative_path": ref.get("relative_path"),
                }
                for ref in evidence_refs
            ],
            "missing_validation": list(record.get("missing_validation") or []),
            "human_edits": list(record.get("human_edits") or []),
            "approval_ref": record.get("approval_ref"),
            "created_at": str(record.get("created_at") or ""),
            "record_revision": int(record_revision),
        }
        if not document["created_at"]:
            raise ResearchWriteError("missing_created_at", "record.created_at is required")
        violations = record_authority_violations(document)
        if violations:
            # A record cites another authority's *reference*, never its live row.
            raise ResearchWriteError(
                "authority_copy_in_record",
                "the record copies authority field(s): " + ", ".join(sorted(violations)),
            )
        return identity, document, decision, projection_digest

    # -- reads -------------------------------------------------------------

    def read_proposal(
        self, *, principal_id: str, project_id: str, proposal_id: str
    ) -> ResearchResourceRecord:
        """Read the proposal addressed by ``proposal_id`` (no projection)."""
        return self._read(
            project=project_id,
            principal=principal_id,
            resource_id=research_resource_key(project_id, "proposal", proposal_id),
        )

    def read_record(
        self, *, principal_id: str, project_id: str, research_id: str
    ) -> ResearchResourceRecord:
        """Read the round record addressed by ``research_id``."""
        return self._read(
            project=project_id,
            principal=principal_id,
            resource_id=research_resource_key(project_id, "record", research_id),
        )

    # -- internals ---------------------------------------------------------

    def _read(self, *, project: str, principal: str, resource_id: str) -> ResearchResourceRecord:
        try:
            return self._resources.read(resource_id, principal_id=principal, project_id=project)
        except ResearchResourceError as exc:
            raise _store_error(exc) from exc

    def _request_approval(
        self,
        *,
        document: Mapping[str, Any],
        project: str,
        requester: str,
        digests: Mapping[str, str],
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None,
        expires_at: str | None,
    ) -> Approval:
        definition = frozen_plan_definition(
            workflow=str(plan.get("workflow") or ""),
            input=plan.get("input") if isinstance(plan.get("input"), Mapping) else {},
            method=plan.get("method") if isinstance(plan.get("method"), Mapping) else {},
            resources=plan.get("resources") if isinstance(plan.get("resources"), Mapping) else {},
        )
        try:
            approval, _replayed = self._approvals.request(
                proposal_ref=str(document["proposal_id"]),
                proposal_digest=digests["proposal_digest"],
                plan_digest=digests["plan_digest"],
                project_id=project,
                requester_principal=requester,
                plan_definition=definition,
                budget=budget,
                allowed_change_range=document.get("allowed_change_scope")
                if isinstance(document.get("allowed_change_scope"), Mapping)
                else None,
                expires_at=expires_at,
                science_digest=digests["science_digest"],
            )
        except ApprovalError as exc:
            status = getattr(exc, "http_status", 400)
            if status == 403:
                raise ResearchWriteDeniedError(exc.rule, exc.message) from exc
            if status == 409:
                raise ResearchWriteConflictError(exc.rule, exc.message) from exc
            raise ResearchWriteError(exc.rule, exc.message) from exc
        return approval

    def _supersede_plan_change(
        self,
        live: Approval,
        *,
        identity: ResearchIdentity,
        new_digest: str,
        document: Mapping[str, Any],
        project: str,
        requester: str,
        proposal_digest: str,
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None,
        expires_at: str | None,
    ) -> Approval:
        """Revoke the old approval and request the new one (M1-02 chain)."""
        intent = SubmitIntent(
            intent_key=f"writer:{document['proposal_id']}:{live.revision}",
            project_id=project,
            research_id=identity.research_id,
            session_id=identity.session_id,
            round=int(document.get("round") or 0),
            plan_digest=live.plan_digest,
            approval_ref=live.approval_id,
        )
        definition = frozen_plan_definition(
            workflow=str(plan.get("workflow") or ""),
            input=plan.get("input") if isinstance(plan.get("input"), Mapping) else {},
            method=plan.get("method") if isinstance(plan.get("method"), Mapping) else {},
            resources=plan.get("resources") if isinstance(plan.get("resources"), Mapping) else {},
        )
        try:
            outcome = supersede_approval_for_plan_change(
                self._approvals,
                intent=intent,
                new_plan_digest=new_digest,
                requester_principal=requester,
                proposal_ref=str(document["proposal_id"]),
                proposal_digest=proposal_digest,
                actor_principal=requester,
                new_plan_definition=definition,
                budget=budget,
                allowed_change_range=document.get("allowed_change_scope")
                if isinstance(document.get("allowed_change_scope"), Mapping)
                else None,
                expires_at=expires_at,
            )
        except (ResearchSessionConflictError, ResearchSessionValidationError) as exc:
            raise ResearchWriteConflictError(exc.rule, str(exc)) from exc
        except ApprovalError as exc:
            raise ResearchWriteConflictError(exc.rule, str(exc)) from exc
        return outcome.approval

    def _supersede_science_change(
        self,
        live: Approval,
        *,
        document: Mapping[str, Any],
        project: str,
        requester: str,
        digests: Mapping[str, str],
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None,
        expires_at: str | None,
    ) -> Approval:
        """Revoke an approval whose *science* digest changed (same execution plan).

        The M1-02 chain revokes the previous approval, opens a new round and
        requests a new approval intent; only the digest that actually changed
        differs, so the same two optimistic CAS steps are reused (never a local
        "approved" value and no execution fact).
        """
        if live.state == "approved":
            try:
                self._approvals.revoke(
                    live.approval_id,
                    actor_principal=requester,
                    expected_revision=live.revision,
                    reason="science_digest_changed",
                )
            except ApprovalError as exc:
                raise ResearchWriteConflictError(exc.rule, exc.message) from exc
        return self._request_approval(
            document=document,
            project=project,
            requester=requester,
            digests=digests,
            plan=plan,
            budget=budget,
            expires_at=expires_at,
        )

    def _live_approval(self, project: str, proposal_ref: str) -> Approval | None:
        for state in ("approved", "pending", "draft"):
            matches = self._approvals.query(
                project_id=project, proposal_ref=proposal_ref, status=state, limit=1
            )
            if matches:
                return matches[0]
        return None

    def _require_approved_record(
        self,
        *,
        approval_ref: str,
        project: str,
        principal: str,
        plan_definition_digest: str,
    ) -> None:
        """Require the record's approval to be approved, owned and science-bound."""
        approval = self._approvals.get(approval_ref)
        if approval is None:
            raise ResearchWriteDeniedError(
                "approval_required", "the record names an unknown approval"
            )
        if approval.project_id != project:
            raise ResearchWriteDeniedError(
                "project_scope_mismatch", "the approval belongs to another project"
            )
        if approval.requester_principal != principal:
            raise ResearchWriteDeniedError(
                "approval_principal_mismatch", "the approval belongs to another principal"
            )
        if approval.state != "approved":
            raise ResearchWriteDeniedError(
                "approval_not_approved", f"the approval state is {approval.state!r}"
            )
        science = str(approval.science_digest or "")
        if science and _require_digest(plan_definition_digest, "plan_definition_digest") != science:
            raise ResearchWriteConflictError(
                "approval_binding_mismatch",
                "the record science digest differs from the approved plan definition",
            )

    def _verify_view(
        self,
        evidence_view: Mapping[str, Any],
        *,
        identity: ResearchIdentity,
        round_index: int,
        decision: str,
        binding: Mapping[str, Any],
        evidence_refs: list[Any],
    ) -> str:
        """Re-admit the M1-03 view and enforce the frozen decision policy."""
        try:
            view = EvidenceView.from_dict(evidence_view)
            verify_attribution(view, identity, round_index)
            # The C record binding is flat and carries no release digest; M1-03's
            # single normaliser takes the verified digest explicitly, so the view
            # (never an invented value) supplies it.
            task_ref = TaskRef.from_task_binding(
                binding, release_digest=view.task_ref.release_digest
            )
            if view.task_ref.identity() != task_ref.identity():
                raise ResearchWriteDeniedError(
                    "foreign_task_ref", "the evidence view names another task attempt"
                )
            refs = {
                str(ref.get("evidence_id") or ""): str(ref.get("sha256") or "")
                for ref in evidence_refs
                if isinstance(ref, Mapping)
            }
            for ref in view.evidence_refs:
                if refs.get(ref.evidence_id) != ref.sha256:
                    raise ResearchWriteDeniedError(
                        "evidence_digest_mismatch",
                        f"evidence {ref.evidence_id!r} is not cited with the view digest",
                    )
            decide_evidence(view, decision)
        except ResearchDecisionError as exc:
            raise ResearchWriteError(exc.code, str(exc)) from exc
        except EvidenceProjectionError as exc:
            code = exc.code
            if code in {"foreign_project", "foreign_research", "foreign_round", "foreign_task_ref"}:
                raise ResearchWriteDeniedError(code, str(exc)) from exc
            raise ResearchWriteConflictError(code, str(exc)) from exc
        return view.projection_digest
