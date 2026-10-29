"""Projection freshness / availability / revision semantics (B03-2, todo 20).

The task-row projection exposes three **orthogonal** semantics, all defined by
the frozen first-slice contract (§2.3 of
``docs/reference/next-change/ACP_Services_First_Slice_Contracts.md``):

* **availability** — is the *resource* prepared and readable
  (``ready`` / ``preparing`` / ``unavailable``; the frozen vocabulary also
  carries ``absent`` for the Data ``prepare`` surface, which the row-projection
  classifier never produces).  Availability says *nothing* about projection
  age.
* **freshness + basis_revision** — has the *projection* kept up with the
  authoritative owner state.  A projection records the owner revision it was
  built against **per owner dimension** (job / attempt / name / catalog /
  organization); freshness compares each dimension **independently** against the
  current revision.  One matching dimension never vouches for another, and a
  different owner's revision is never substituted.
* **observed_at** — a diagnostic timestamp only.  It is **never** the authority
  for "newer vs older"; revisions are.  A clock-skewed observation whose basis
  revision still matches is ``fresh``.

Freshness truth table (revision-driven, never time-driven):

===============  ================================  ===================
availability     freshness                         meaning / action
===============  ================================  ===================
ready            fresh                             full: display normally
ready            stale                             resource readable, projection old:
                                                   display + mark stale, never hide
unavailable      unknown                           no projection: show jobs base facts
preparing        unknown                           projection being built: show base facts
===============  ================================  ===================

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package.  This module is stdlib only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = [
    "AVAILABILITY_ABSENT",
    "AVAILABILITY_PREPARING",
    "AVAILABILITY_READY",
    "AVAILABILITY_UNAVAILABLE",
    "FRESHNESS_FRESH",
    "FRESHNESS_STALE",
    "FRESHNESS_UNKNOWN",
    "OWNER_ATTEMPT",
    "OWNER_CATALOG",
    "OWNER_DIMENSIONS",
    "OWNER_JOB",
    "OWNER_NAME",
    "OWNER_ORGANIZATION",
    "FreshnessVerdict",
    "ProjectionObservationError",
    "ProjectionState",
    "compare_revisions",
    "incoming_is_late",
    "resolve_projection_state",
    "validate_observation_monotonicity",
]

# Availability tokens.  ``absent`` is part of the frozen vocabulary (§2.2) but is
# produced by the Data ``prepare`` surface, not by the task-row projection
# classifier; it is exported so consumers share one vocabulary.
AVAILABILITY_READY = "ready"
AVAILABILITY_PREPARING = "preparing"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_ABSENT = "absent"

AVAILABILITY_VALUES: tuple[str, ...] = (
    AVAILABILITY_READY,
    AVAILABILITY_PREPARING,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_ABSENT,
)

# Freshness tokens.  ``unknown`` = no comparable projection basis (e.g. no
# projection at all); it is explicitly not freshness=stale.
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"

FRESHNESS_VALUES: tuple[str, ...] = (FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_UNKNOWN)

# Owner revision dimensions.  Each is compared independently; the tuple is the
# *documented* vocabulary, not a fixed schema — :func:`compare_revisions` works
# with any supplied subset.
OWNER_JOB = "job"
OWNER_ATTEMPT = "attempt"
OWNER_NAME = "name"
OWNER_CATALOG = "catalog"
OWNER_ORGANIZATION = "organization"

OWNER_DIMENSIONS: tuple[str, ...] = (
    OWNER_JOB,
    OWNER_ATTEMPT,
    OWNER_NAME,
    OWNER_CATALOG,
    OWNER_ORGANIZATION,
)


class ProjectionObservationError(ValueError):
    """An observation timestamp precedes the state it claims to have observed.

    Diagnostic invariant only: it never decides freshness (revisions do).  It
    catches a forged or clock-skewed ``observed_at`` that predates the basis
    revision it is attached to.
    """


@dataclass(frozen=True)
class FreshnessVerdict:
    """Per-dimension freshness verdict for one projection.

    ``freshness`` is ``fresh`` when every dimension present in both the basis
    and the current map agrees, else ``stale``.  ``stale_dimensions`` lists only
    the dimensions that disagree; ``compared_dimensions`` lists the dimensions
    that were actually comparable (present in both maps).
    """

    freshness: str
    stale_dimensions: tuple[str, ...] = ()
    compared_dimensions: tuple[str, ...] = ()

    @property
    def is_fresh(self) -> bool:
        return self.freshness == FRESHNESS_FRESH

    @property
    def is_stale(self) -> bool:
        return self.freshness == FRESHNESS_STALE

    def to_dict(self) -> dict[str, Any]:
        return {
            "freshness": self.freshness,
            "stale_dimensions": list(self.stale_dimensions),
            "compared_dimensions": list(self.compared_dimensions),
        }


@dataclass(frozen=True)
class ProjectionState:
    """Resolved availability/freshness/basis for one task row.

    ``basis_revision`` and ``current_revision`` are per-owner-dimension maps.
    ``observed_at`` is diagnostic and is deliberately *not* an input to
    :attr:`freshness`.
    """

    availability: str
    freshness: str
    basis_revision: Mapping[str, int]
    current_revision: Mapping[str, int]
    stale_dimensions: tuple[str, ...] = ()
    observed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability,
            "freshness": self.freshness,
            "basis_revision": dict(self.basis_revision),
            "current_revision": dict(self.current_revision),
            "stale_dimensions": list(self.stale_dimensions),
            "observed_at": self.observed_at,
        }


def _int_map(raw: Mapping[str, Any] | None) -> dict[str, int]:
    """Coerce a revision mapping to ``{dimension: int}`` (defensive)."""
    result: dict[str, int] = {}
    if not isinstance(raw, Mapping):
        return result
    for dimension, value in raw.items():
        if not isinstance(dimension, str) or not dimension:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            result[dimension] = value
        elif isinstance(value, str) and value.lstrip("-").isdigit():
            result[dimension] = int(value)
    return result


def compare_revisions(
    basis: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> FreshnessVerdict:
    """Compare a projection's per-dimension basis against current revisions.

    Each owner dimension is compared **independently**: only a dimension present
    in *both* maps is compared, and a mismatch on any one of them yields
    ``stale``.  A dimension absent from the basis is not a claim and never
    counts as a mismatch (partial basis); a dimension present only in *current*
    is likewise ignored.  No dimension can stand in for another, and time is not
    consulted.
    """
    basis_map = _int_map(basis)
    current_map = _int_map(current)

    stale: list[str] = []
    compared: list[str] = []
    for dimension, basis_rev in basis_map.items():
        if dimension not in current_map:
            continue
        compared.append(dimension)
        if current_map[dimension] != basis_rev:
            stale.append(dimension)

    freshness = FRESHNESS_STALE if stale else FRESHNESS_FRESH
    return FreshnessVerdict(
        freshness=freshness,
        stale_dimensions=tuple(stale),
        compared_dimensions=tuple(compared),
    )


def incoming_is_late(
    incoming_basis: Mapping[str, Any] | None,
    existing_basis: Mapping[str, Any] | None,
) -> bool:
    """Whether an incoming projection basis is strictly behind an existing one.

    Used to **discard a late/old-attempt projection**: it is late when it is
    behind on at least one shared owner dimension and never ahead on any.  A
    projection that moved forward on some dimension is accepted (the merge keeps
    the newer observation).  With no shared dimension there is no ordering claim,
    so it is not late.  Time is never consulted.
    """
    incoming = _int_map(incoming_basis)
    existing = _int_map(existing_basis)
    if not incoming or not existing:
        return False
    shared = [dim for dim in incoming if dim in existing]
    if not shared:
        return False
    behind = any(incoming[dim] < existing[dim] for dim in shared)
    ahead = any(incoming[dim] > existing[dim] for dim in shared)
    return behind and not ahead


def resolve_projection_state(
    *,
    has_projection: bool,
    basis_revisions: Mapping[str, Any] | None = None,
    current_revisions: Mapping[str, Any] | None = None,
    preparing: bool = False,
    freshly_enriched: bool = False,
    observed_at: str = "",
) -> ProjectionState:
    """Resolve availability/freshness for one task row.

    Args:
        has_projection: Whether a prepared projection entry covers the row.
        basis_revisions: The owner revisions the projection was built against
            (per dimension).  Ignored when there is no projection.
        current_revisions: The current authoritative owner revisions.
        preparing: A background refresh is pending for a row with no stored
            projection yet.
        freshly_enriched: The row was just observed against the live source
            (retained synchronous compatibility path); it is treated as a fresh
            projection whose basis *is* the current revision.
        observed_at: Diagnostic timestamp (never used for the freshness verdict).
    """
    current_map = _int_map(current_revisions)

    if not has_projection:
        if freshly_enriched:
            verdict = compare_revisions(current_map, current_map)
            return ProjectionState(
                availability=AVAILABILITY_READY,
                freshness=verdict.freshness,
                basis_revision=dict(current_map),
                current_revision=dict(current_map),
                stale_dimensions=verdict.stale_dimensions,
                observed_at=observed_at,
            )
        availability = AVAILABILITY_PREPARING if preparing else AVAILABILITY_UNAVAILABLE
        return ProjectionState(
            availability=availability,
            freshness=FRESHNESS_UNKNOWN,
            basis_revision={},
            current_revision=dict(current_map),
            stale_dimensions=(),
            observed_at=observed_at,
        )

    basis_map = _int_map(basis_revisions)
    verdict = compare_revisions(basis_map, current_map)
    return ProjectionState(
        availability=AVAILABILITY_READY,
        freshness=verdict.freshness,
        basis_revision=dict(basis_map),
        current_revision=dict(current_map),
        stale_dimensions=verdict.stale_dimensions,
        observed_at=observed_at,
    )


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; ``None`` when unparseable/naive."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def validate_observation_monotonicity(
    observed_at: str,
    basis_updated_at: str,
) -> None:
    """Reject an observation that predates the state it claims to have observed.

    A projection cannot have been observed *before* the basis revision it was
    built against was written.  This is a diagnostic guard: freshness is decided
    by revisions, never by this timestamp.  Unparseable or naive timestamps are
    left alone (best-effort diagnostic).

    Raises:
        ProjectionObservationError: when *observed_at* is strictly earlier than
            *basis_updated_at*.
    """
    observed = _parse_iso(observed_at)
    updated = _parse_iso(basis_updated_at)
    if observed is None or updated is None:
        return
    if observed < updated:
        raise ProjectionObservationError(
            f"observed_at {observed_at!r} precedes basis revision update {basis_updated_at!r}"
        )
