"""Trusted usage collection — ``UsageRecord`` bound to attempt/submission/evidence/policy.

(B10-2 / todo 54; plan §20.4 / PR-06 / MU-10a.)

This module turns *trusted execution observations* into an auditable
:class:`UsageRecord`.  It is the collection half of frozen metering: the sibling
:mod:`acp_services.runtime.metering_policy` (todo 53) decides *how* core hours are
computed; this module decides *what* was observed, *where it came from*, and
*whether it is confirmable*.

Trusted collection only
-----------------------

Named collection sources are the only allowed evidence:

* :data:`CollectionSource.LSF_JOB_REPORT` — the external scheduler level: fields
  from ``bjobs`` / the LSF job report, mapped to the job's **actual allocation**
  (allocated cores, run seconds, cumulative CPU seconds).
* :data:`CollectionSource.LOCAL_PROCESS_GROUP` — the in-process level: process
  group / OS accounting (e.g. ``getrusage`` user+sys CPU seconds).
* :data:`CollectionSource.LOCAL_POLICY_ALLOCATED` — the local allocation level:
  core-hours the policy allowed for the local run.

Progress reporting and client/browser timers are **never** a usage basis (plan
§20.2/§20.3); they have no vocabulary here at all.

Parent/child are never summed
-----------------------------

An execution nests levels: a scheduler/allocation level (parent) and an
in-process level (child).  Plan §20.3 freezes that *exactly one* non-overlapping
level may be metered — "LSF 作业核时 XOR 内部 ORCA 进程核时".  This is enforced
structurally: :func:`select_metering_level` returns **one**
:class:`CollectedUsage`, requires an explicit choice when both are observed, and
the module exposes no summing primitive at all.  :func:`build_usage_record`
accepts a single :class:`CollectedUsage`, so the two levels cannot be added.

Missing evidence reconciles; it is never zero-filled
----------------------------------------------------

When the evidence source/version is absent, the collection is incomplete, or the
frozen policy's chosen basis lacks its required measurement, the record is
:data:`ReconciliationStatus.PENDING_RECONCILIATION` ("待对账") and carries the
pending intervals explicitly.  The record's ``core_hours`` then covers only the
metered intervals; it is *not* a confirmed zero and must not be settled (plan
§20.4/§20.6).

Not scientific identity
-----------------------

A :class:`UsageRecord` is a platform execution/usage fact.  It must never be
written into ``TaskRequest``, scientific options, CCCP content identity, or the
ACV data model — see :data:`USAGE_NOT_SCIENTIFIC_IDENTITY_NOTE`.  It also carries
no price/rate/Credit vocabulary (pricing is PR-05b and stays gated).

Dependency direction
--------------------

``src/acp_services/**`` must never import ``acp`` (nor ``cccp``).  This module is
stdlib plus the sibling metering policy contract.

Author: ACP Services (B lane, todo 54).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from acp_services.runtime.metering_policy import (
    ChargeDecision,
    IntervalKind,
    IntervalVerdict,
    MeteringPolicy,
    UsageInterval,
    classify_interval,
    measure,
)

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies
    "CollectionSource",
    "ResourceLevel",
    "ResourceClass",
    "ReconciliationStatus",
    # notes
    "USAGE_NOT_SCIENTIFIC_IDENTITY_NOTE",
    # value objects
    "UsageEvidence",
    "LsfJobReport",
    "LocalProcessSample",
    "CollectedUsage",
    "UsageIdentity",
    "UsageRecord",
    # errors
    "UsageError",
    "UsageValidationError",
    "UsageCollectionError",
    # functions
    "usage_id_for",
    "lsf_job_report_from_mapping",
    "local_process_sample_from_rusage",
    "collect_lsf_job_usage",
    "collect_local_process_usage",
    "collect_local_allocated_usage",
    "select_metering_level",
    "build_usage_record",
    "USAGE_UNIT_FIELDS",
    "USAGE_UNIT_ALIASES",
    "USAGE_NOT_UNIFIED_BILLING_NOTE",
    "UsageUnitCounts",
    "normalize_usage_unit",
]

from acp_services.runtime.usage_units import (  # noqa: E402 - compatibility re-export
    USAGE_NOT_UNIFIED_BILLING_NOTE,
    USAGE_UNIT_ALIASES,
    USAGE_UNIT_FIELDS,
    UsageUnitCounts,
    normalize_usage_unit,
)

#: Executable statement that a usage record is a platform fact, not a scientific
#: identity input.  Referenced verbatim by the acceptance tests.
USAGE_NOT_SCIENTIFIC_IDENTITY_NOTE = (
    "A UsageRecord is a platform execution/usage fact and is NOT part of "
    "scientific identity: it must never be written into TaskRequest, scientific "
    "options, CCCP content identity, or the ACV data model."
)


# ---------------------------------------------------------------------------
# Vocabularies (str-valued so they compare/round-trip as plain strings)
# ---------------------------------------------------------------------------


class CollectionSource(str):
    """A trusted source of raw usage observation.

    ``PROGRESS`` / ``BROWSER`` are deliberately absent — progress reporting and
    client timers can never be a usage basis (plan §20.2/§20.3).
    """

    LOCAL_PROCESS_GROUP = "local-process-group"
    LOCAL_POLICY_ALLOCATED = "local-policy-allocated"
    LSF_JOB_REPORT = "lsf-job-report"
    ALL = frozenset({LOCAL_PROCESS_GROUP, LOCAL_POLICY_ALLOCATED, LSF_JOB_REPORT})


class ResourceLevel(str):
    """Which nested execution level a measurement describes.

    ``PARENT`` = allocation/scheduler level (an LSF job, or a local policy-allowed
    allocation); ``CHILD`` = in-process level (the ORCA process group).  Exactly
    one level may be metered per execution; the two are never summed.
    """

    PARENT = "parent"
    CHILD = "child"
    ALL = frozenset({PARENT, CHILD})


class ResourceClass(str):
    """Resource dimension being metered.

    Only ``CPU`` is enabled in this version; GPU (and any other priced dimension)
    requires a new, user-visible policy before it may be metered (plan §20.1).
    """

    CPU = "cpu"
    ALL = frozenset({CPU})


class ReconciliationStatus(str):
    """Whether a usage record's evidence is complete.

    ``PENDING_RECONCILIATION`` mirrors the interval-level
    :data:`metering_policy.ChargeDecision.PENDING_RECONCILIATION`: the record is
    retained and auditable but must be reconciled, never settled — and never
    treated as a confirmed zero.
    """

    COMPLETE = "complete"
    PENDING_RECONCILIATION = "pending_reconciliation"
    ALL = frozenset({COMPLETE, PENDING_RECONCILIATION})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UsageError(RuntimeError):
    """Base error for trusted usage collection."""


class UsageValidationError(UsageError):
    """A usage value is structurally invalid (fail closed)."""


class UsageCollectionError(UsageError):
    """No trusted observation is available to record."""


# ---------------------------------------------------------------------------
# Small validation / coercion helpers
# ---------------------------------------------------------------------------


def _validate_vocab(value: Any, allowed: frozenset[str], name: str) -> str:
    text = str(value)
    if text not in allowed:
        raise UsageValidationError(f"{name}={value!r} is not one of {sorted(allowed)}")
    return text


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise UsageValidationError(f"{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise UsageValidationError(f"{name} must be timezone-aware")
    return value


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce an observation field to :class:`Decimal` (``None`` stays unknown)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise UsageValidationError(f"cannot interpret {value!r} as a decimal") from exc


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise UsageValidationError("boolean is not a valid integral observation")
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise UsageValidationError(f"cannot interpret {value!r} as an integer") from exc


def _pick(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None and payload[key] != "":
            return payload[key]
    return None


# ---------------------------------------------------------------------------
# Evidence + raw observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageEvidence:
    """Where an observation came from and which evidence version produced it.

    Both ``source`` and ``version`` must be present for the evidence to count as
    present; a missing version is *not* a zero usage, it reconciles.  ``source``
    must be a named :class:`CollectionSource` (empty = unknown/missing).
    """

    source: str = ""
    version: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        text = str(self.source).strip()
        if text and text not in CollectionSource.ALL:
            raise UsageValidationError(
                f"evidence source {self.source!r} is not a trusted CollectionSource"
            )

    @property
    def present(self) -> bool:
        return bool(str(self.source).strip() and str(self.version).strip())

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "version": self.version, "note": self.note}


@dataclass(frozen=True)
class LsfJobReport:
    """The material observed from ``bjobs`` / the LSF job report.

    Field names mirror the vocabulary a scheduler report can expose.  ``None``
    fields are *unknown*, never zero: the collector preserves that distinction so
    the record reconciles instead of silently billing zero.
    """

    lsf_job_id: str
    state: str = ""
    node: str = ""
    allocated_cores: int | None = None
    run_seconds: Decimal | None = None
    cpu_seconds: Decimal | None = None
    evidence_version: str = ""

    def __post_init__(self) -> None:
        if not str(self.lsf_job_id).strip():
            raise UsageValidationError("lsf_job_id is required")
        if self.allocated_cores is not None and int(self.allocated_cores) < 0:
            raise UsageValidationError("allocated_cores must be non-negative")
        if self.run_seconds is not None and Decimal(self.run_seconds) < 0:
            raise UsageValidationError("run_seconds must be non-negative")
        if self.cpu_seconds is not None and Decimal(self.cpu_seconds) < 0:
            raise UsageValidationError("cpu_seconds must be non-negative")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> LsfJobReport:
        """Build a report from a raw ``bjobs``/job-report field mapping.

        Accepts the common aliases a scheduler report uses and coerces string
        fields to numbers.  A missing job id is a hard error (no anonymous usage).
        """
        job_id = _pick(payload, "lsf_job_id", "job_id", "id")
        if job_id is None:
            raise UsageValidationError("job report mapping has no lsf_job_id/job_id/id")
        return cls(
            lsf_job_id=str(job_id),
            state=str(_pick(payload, "state", "stat") or ""),
            node=str(_pick(payload, "node", "exec_host", "host") or ""),
            allocated_cores=_to_int(
                _pick(payload, "allocated_cores", "cores", "ncores", "num_processors")
            ),
            run_seconds=_to_decimal(
                _pick(payload, "run_seconds", "runtime", "run_time", "wall_seconds")
            ),
            cpu_seconds=_to_decimal(_pick(payload, "cpu_seconds", "cpu_time", "cpu")),
            evidence_version=str(_pick(payload, "evidence_version", "version") or ""),
        )

    def observation_available(self) -> bool:
        """True when at least one measurable dimension was observed."""
        return self.allocated_cores is not None or self.cpu_seconds is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsf_job_id": self.lsf_job_id,
            "state": self.state,
            "node": self.node,
            "allocated_cores": self.allocated_cores,
            "run_seconds": None if self.run_seconds is None else str(self.run_seconds),
            "cpu_seconds": None if self.cpu_seconds is None else str(self.cpu_seconds),
            "evidence_version": self.evidence_version,
        }


@dataclass(frozen=True)
class LocalProcessSample:
    """OS/process-group accounting for the in-process (child) level."""

    cpu_seconds: Decimal | None = None
    allocated_cores: int | None = None
    pgid: int | None = None
    resource_id: str = ""
    evidence_version: str = ""

    def __post_init__(self) -> None:
        if self.cpu_seconds is not None and Decimal(self.cpu_seconds) < 0:
            raise UsageValidationError("cpu_seconds must be non-negative")
        if self.allocated_cores is not None and int(self.allocated_cores) < 0:
            raise UsageValidationError("allocated_cores must be non-negative")

    def observation_available(self) -> bool:
        return self.cpu_seconds is not None or self.allocated_cores is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_seconds": None if self.cpu_seconds is None else str(self.cpu_seconds),
            "allocated_cores": self.allocated_cores,
            "pgid": self.pgid,
            "resource_id": self.resource_id,
            "evidence_version": self.evidence_version,
        }


def local_process_sample_from_rusage(
    rusage: Mapping[str, Any],
    *,
    pgid: int | None = None,
    resource_id: str = "",
    evidence_version: str = "",
) -> LocalProcessSample:
    """Map an OS ``getrusage``-style mapping to a :class:`LocalProcessSample`.

    ``ru_utime`` + ``ru_stime`` (seconds) are summed into ``cpu_seconds``; a
    pre-summed ``cpu_seconds``/``cpu_time`` key is accepted as-is.  Absent fields
    stay ``None`` (unknown), so a missing reading reconciles rather than zeroes.
    """
    cpu = _to_decimal(_pick(rusage, "cpu_seconds", "cpu_time"))
    if cpu is None:
        user = _to_decimal(rusage.get("ru_utime"))
        system = _to_decimal(rusage.get("ru_stime"))
        if user is not None or system is not None:
            cpu = (user or Decimal(0)) + (system or Decimal(0))
    return LocalProcessSample(
        cpu_seconds=cpu,
        allocated_cores=_to_int(_pick(rusage, "allocated_cores", "cores")),
        pgid=pgid,
        resource_id=resource_id,
        evidence_version=evidence_version,
    )


def lsf_job_report_from_mapping(payload: Mapping[str, Any]) -> LsfJobReport:
    """Functional entry point for :meth:`LsfJobReport.from_mapping`."""
    return LsfJobReport.from_mapping(payload)


@dataclass(frozen=True)
class CollectedUsage:
    """One trusted observation at a single :class:`ResourceLevel`.

    Carries the raw intervals (which the frozen policy classifies), the evidence
    identity, and whether the collection itself is complete.  ``complete=False``
    or missing evidence forces a pending-reconciliation record.
    """

    level: str
    source: str
    intervals: tuple[UsageInterval, ...] = ()
    evidence: UsageEvidence = field(default_factory=UsageEvidence)
    external_job_id: str | None = None
    allocated_cores: int | None = None
    complete: bool = True

    def __post_init__(self) -> None:
        _validate_vocab(self.level, ResourceLevel.ALL, "level")
        _validate_vocab(self.source, CollectionSource.ALL, "source")
        if self.allocated_cores is not None and int(self.allocated_cores) < 0:
            raise UsageValidationError("allocated_cores must be non-negative")

    @property
    def evidence_present(self) -> bool:
        return self.evidence.present

    @property
    def interval_count(self) -> int:
        return len(self.intervals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "source": self.source,
            "intervals": [item.to_dict() for item in self.intervals],
            "evidence": self.evidence.to_dict(),
            "external_job_id": self.external_job_id,
            "allocated_cores": self.allocated_cores,
            "complete": self.complete,
            "evidence_present": self.evidence_present,
        }


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def collect_lsf_job_usage(
    report: LsfJobReport | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> CollectedUsage:
    """Collect the **parent** (LSF job) level from a job report.

    A missing report yields an incomplete, evidence-less observation so the
    resulting record reconciles instead of being guessed.  When both an allocated
    core count and cumulative CPU seconds are present, both are carried as
    evidence; the frozen policy picks exactly one basis and never sums them.
    """
    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    if window_end < window_start:
        raise UsageValidationError("window_end must not precede window_start")

    if report is None:
        return CollectedUsage(
            level=ResourceLevel.PARENT,
            source=CollectionSource.LSF_JOB_REPORT,
            intervals=(),
            evidence=UsageEvidence(),
            complete=False,
        )

    evidence = UsageEvidence(
        source=CollectionSource.LSF_JOB_REPORT,
        version=report.evidence_version,
        note=f"lsf:{report.state}" if report.state else "",
    )
    interval = UsageInterval(
        start=window_start,
        end=window_end,
        kind=IntervalKind.RUNNING,
        allocated_cores=report.allocated_cores,
        cpu_seconds=report.cpu_seconds,
        evidence_source=evidence.source if evidence.present else "",
        evidence_version=evidence.version if evidence.present else "",
    )
    return CollectedUsage(
        level=ResourceLevel.PARENT,
        source=CollectionSource.LSF_JOB_REPORT,
        intervals=(interval,),
        evidence=evidence,
        external_job_id=report.lsf_job_id,
        allocated_cores=report.allocated_cores,
        complete=report.observation_available(),
    )


def collect_local_process_usage(
    sample: LocalProcessSample | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> CollectedUsage:
    """Collect the **child** (in-process / process group) level.

    The interval carries OS-accounted CPU seconds; when the policy instead meters
    allocated core-hours the ``allocated_cores`` field (if the sample has one) is
    used.  A missing sample reconciles.
    """
    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    if window_end < window_start:
        raise UsageValidationError("window_end must not precede window_start")

    if sample is None:
        return CollectedUsage(
            level=ResourceLevel.CHILD,
            source=CollectionSource.LOCAL_PROCESS_GROUP,
            intervals=(),
            evidence=UsageEvidence(),
            complete=False,
        )

    evidence = UsageEvidence(
        source=CollectionSource.LOCAL_PROCESS_GROUP,
        version=sample.evidence_version,
        note=f"pgid:{sample.pgid}" if sample.pgid is not None else "",
    )
    interval = UsageInterval(
        start=window_start,
        end=window_end,
        kind=IntervalKind.RUNNING,
        allocated_cores=sample.allocated_cores,
        cpu_seconds=sample.cpu_seconds,
        evidence_source=evidence.source if evidence.present else "",
        evidence_version=evidence.version if evidence.present else "",
    )
    return CollectedUsage(
        level=ResourceLevel.CHILD,
        source=CollectionSource.LOCAL_PROCESS_GROUP,
        intervals=(interval,),
        evidence=evidence,
        allocated_cores=sample.allocated_cores,
        complete=sample.observation_available(),
    )


def collect_local_allocated_usage(
    *,
    allocated_cores: int | None,
    window_start: datetime,
    window_end: datetime,
    evidence_version: str,
    resource_id: str = "",
) -> CollectedUsage:
    """Collect the local **allocation** (parent) level from a policy-allowed bound.

    Used when a local run has no external scheduler and the policy measures
    allocated core-hours.  A ``None`` core count is unknown (reconciles), never a
    zero.
    """
    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    if window_end < window_start:
        raise UsageValidationError("window_end must not precede window_start")
    if allocated_cores is not None and int(allocated_cores) < 0:
        raise UsageValidationError("allocated_cores must be non-negative")

    evidence = UsageEvidence(
        source=CollectionSource.LOCAL_POLICY_ALLOCATED,
        version=evidence_version,
        note=f"resource:{resource_id}" if resource_id else "",
    )
    interval = UsageInterval(
        start=window_start,
        end=window_end,
        kind=IntervalKind.RUNNING,
        allocated_cores=allocated_cores,
        evidence_source=evidence.source if evidence.present else "",
        evidence_version=evidence.version if evidence.present else "",
    )
    return CollectedUsage(
        level=ResourceLevel.PARENT,
        source=CollectionSource.LOCAL_POLICY_ALLOCATED,
        intervals=(interval,),
        evidence=evidence,
        allocated_cores=allocated_cores,
        complete=allocated_cores is not None,
    )


def select_metering_level(
    parent: CollectedUsage | None,
    child: CollectedUsage | None,
    *,
    preferred: str | None = None,
) -> CollectedUsage:
    """Choose exactly one resource level to meter — parent XOR child.

    An explicit ``preferred`` (:data:`ResourceLevel.PARENT` or
    :data:`ResourceLevel.CHILD`) is authoritative: it must match an observed
    level or the selection is refused.  Without a preference the single observed
    level is returned, while observing both is ambiguous and refused.  There is
    deliberately no code path that combines the two: the returned value is one
    :class:`CollectedUsage`, so "never sum parent and child" holds by
    construction (plan §20.3).
    """
    for observation in (parent, child):
        if observation is not None and observation.level not in ResourceLevel.ALL:
            raise UsageValidationError(f"unknown resource level {observation.level!r}")

    present = [item for item in (parent, child) if item is not None]
    if not present:
        raise UsageCollectionError("no usage observation to record")
    if preferred is not None:
        _validate_vocab(preferred, ResourceLevel.ALL, "preferred")
        for item in present:
            if item.level == preferred:
                return item
        raise UsageValidationError(f"preferred level {preferred!r} was not observed")
    if len(present) == 1:
        return present[0]
    raise UsageValidationError(
        "ambiguous resource level: parent and child are both observed; choose "
        "one explicitly (levels are never summed)"
    )


# ---------------------------------------------------------------------------
# Identity + record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageIdentity:
    """The authoritative execution identity a usage record is bound to.

    ``job_id`` + ``attempt`` (+ ``submission_id``) identify the execution;
    ``usage_id`` is derived from them when left blank, so re-collecting the same
    attempt yields the same id (idempotent settlement keys).  ``resource_id``
    names the execution resource (node/host).
    """

    job_id: str
    attempt: int = 1
    project_id: str | None = None
    account: str | None = None
    submission_id: str | None = None
    resource_id: str = ""
    usage_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.job_id).strip():
            raise UsageValidationError("job_id is required")
        if int(self.attempt) < 1:
            raise UsageValidationError("attempt must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "attempt": self.attempt,
            "project_id": self.project_id,
            "account": self.account,
            "submission_id": self.submission_id,
            "resource_id": self.resource_id,
            "usage_id": self.usage_id,
        }


@dataclass(frozen=True)
class UsageRecord:
    """An auditable, attempt/submission-bound usage record (plan §20.4).

    Every platform-side field required by §20.4 is present: ``usage_id``;
    project/account; job/attempt/submission; execution resource + external job
    identity; basis/interval/cores; evidence source+version; final flag; the
    excluded and pending intervals; resource class + policy version/digest; the
    core-hour result; and an evidence digest.

    ``final`` and ``reconciliation_status`` are independent: an execution may be
    terminally final while remote usage is still unconfirmed (§20.6), in which
    case the record stays :data:`ReconciliationStatus.PENDING_RECONCILIATION`.
    """

    usage_id: str
    job_id: str
    attempt: int
    project_id: str | None
    account: str | None
    submission_id: str | None
    resource_id: str
    external_job_id: str | None
    collection_source: str
    resource_level: str
    resource_class: str
    basis: str
    policy_version: str
    policy_digest: str
    interval_start: datetime
    interval_end: datetime
    allocated_cores: int | None
    core_hours: Decimal
    raw_core_hours: Decimal
    evidence_source: str
    evidence_version: str
    evidence_digest: str
    final: bool
    reconciliation_status: str
    metered_interval_count: int
    excluded_interval_count: int
    pending_interval_count: int
    excluded_intervals: tuple[IntervalVerdict, ...] = ()
    pending_intervals: tuple[IntervalVerdict, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.usage_id).strip():
            raise UsageValidationError("usage_id is required")
        _validate_vocab(self.collection_source, CollectionSource.ALL, "collection_source")
        _validate_vocab(self.resource_level, ResourceLevel.ALL, "resource_level")
        _validate_vocab(self.resource_class, ResourceClass.ALL, "resource_class")
        _validate_vocab(
            self.reconciliation_status, ReconciliationStatus.ALL, "reconciliation_status"
        )
        _require_aware(self.interval_start, "interval_start")
        _require_aware(self.interval_end, "interval_end")

    @property
    def pending_reconciliation(self) -> bool:
        """True when the evidence is incomplete and the record must reconcile."""
        return self.reconciliation_status == ReconciliationStatus.PENDING_RECONCILIATION

    @property
    def confirmable(self) -> bool:
        """True only when the record is both terminal and evidence-complete."""
        return self.final and not self.pending_reconciliation

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "project_id": self.project_id,
            "account": self.account,
            "submission_id": self.submission_id,
            "resource_id": self.resource_id,
            "external_job_id": self.external_job_id,
            "collection_source": self.collection_source,
            "resource_level": self.resource_level,
            "resource_class": self.resource_class,
            "basis": self.basis,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "interval_start": self.interval_start.isoformat(),
            "interval_end": self.interval_end.isoformat(),
            "allocated_cores": self.allocated_cores,
            "core_hours": str(self.core_hours),
            "raw_core_hours": str(self.raw_core_hours),
            "evidence_source": self.evidence_source,
            "evidence_version": self.evidence_version,
            "evidence_digest": self.evidence_digest,
            "final": self.final,
            "reconciliation_status": self.reconciliation_status,
            "pending_reconciliation": self.pending_reconciliation,
            "confirmable": self.confirmable,
            "metered_interval_count": self.metered_interval_count,
            "excluded_interval_count": self.excluded_interval_count,
            "pending_interval_count": self.pending_interval_count,
            "excluded_intervals": [item.to_dict() for item in self.excluded_intervals],
            "pending_intervals": [item.to_dict() for item in self.pending_intervals],
        }

    def to_json(self) -> str:
        """Canonical JSON (sorted keys) for durable persistence downstream."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)


def usage_id_for(job_id: str, attempt: int, submission_id: str | None = None) -> str:
    """Derive the stable usage id for one execution attempt.

    Deterministic in ``(job_id, attempt, submission_id)`` so a repeated
    collection produces the same id — the key that later idempotent settlement
    uses to refuse double charging (plan §20.4).
    """
    material = f"{job_id}:{int(attempt)}:{submission_id or ''}"
    return "usg_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _evidence_digest(
    collected: CollectedUsage,
    identity: UsageIdentity,
    policy: MeteringPolicy,
    *,
    basis: str,
    window_start: datetime,
    window_end: datetime,
) -> str:
    """Stable SHA-256 over the inputs an auditor needs to reproduce the record."""
    payload = {
        "policy_digest": policy.snapshot_digest(),
        "policy_version": policy.policy_version,
        "basis": basis,
        "job_id": identity.job_id,
        "attempt": identity.attempt,
        "submission_id": identity.submission_id,
        "resource_id": identity.resource_id,
        "level": collected.level,
        "source": collected.source,
        "evidence_source": collected.evidence.source,
        "evidence_version": collected.evidence.version,
        "external_job_id": collected.external_job_id,
        "allocated_cores": collected.allocated_cores,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "intervals": [item.to_dict() for item in collected.intervals],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_usage_record(
    collected: CollectedUsage,
    identity: UsageIdentity,
    policy: MeteringPolicy,
    *,
    window_start: datetime,
    window_end: datetime,
    resource_class: str = ResourceClass.CPU,
    final: bool = False,
) -> UsageRecord:
    """Measure one collected level into a bound :class:`UsageRecord`.

    Refuses (raising the metering policy's :class:`MeteringRefusedError`) when the
    policy is not executable, so an unconfigured policy can never produce a
    record.  The excluded intervals are classified by the frozen policy; the
    pending intervals come straight from the measurement.  The record reconciles
    when the collection was incomplete, evidence was missing, or any interval is
    pending — never when a value happens to be zero.
    """
    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    if window_end < window_start:
        raise UsageValidationError("window_end must not precede window_start")
    _validate_vocab(resource_class, ResourceClass.ALL, "resource_class")

    policy.require_measurable()
    result = measure(list(collected.intervals), policy)

    excluded = tuple(
        verdict
        for verdict in (classify_interval(item, policy) for item in collected.intervals)
        if verdict.decision == ChargeDecision.EXCLUDED
    )
    pending = result.pending

    if not collected.complete or not collected.evidence_present or result.pending_intervals > 0:
        status = ReconciliationStatus.PENDING_RECONCILIATION
    else:
        status = ReconciliationStatus.COMPLETE

    usage_id = identity.usage_id.strip() or usage_id_for(
        identity.job_id, identity.attempt, identity.submission_id
    )
    digest = _evidence_digest(
        collected,
        identity,
        policy,
        basis=result.basis,
        window_start=window_start,
        window_end=window_end,
    )

    logger.info(
        "usage recorded: usage_id=%s job=%s attempt=%d level=%s basis=%s core_hours=%s status=%s",
        usage_id,
        identity.job_id,
        identity.attempt,
        collected.level,
        result.basis,
        result.core_hours,
        status,
    )
    return UsageRecord(
        usage_id=usage_id,
        job_id=identity.job_id,
        attempt=identity.attempt,
        project_id=identity.project_id,
        account=identity.account,
        submission_id=identity.submission_id,
        resource_id=identity.resource_id,
        external_job_id=collected.external_job_id,
        collection_source=collected.source,
        resource_level=collected.level,
        resource_class=resource_class,
        basis=result.basis,
        policy_version=result.policy_version,
        policy_digest=policy.snapshot_digest(),
        interval_start=window_start,
        interval_end=window_end,
        allocated_cores=collected.allocated_cores,
        core_hours=result.core_hours,
        raw_core_hours=result.raw_core_hours,
        evidence_source=collected.evidence.source,
        evidence_version=collected.evidence.version,
        evidence_digest=digest,
        final=bool(final),
        reconciliation_status=status,
        metered_interval_count=result.metered_intervals,
        excluded_interval_count=len(excluded),
        pending_interval_count=result.pending_intervals,
        excluded_intervals=excluded,
        pending_intervals=pending,
    )
