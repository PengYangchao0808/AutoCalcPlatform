"""Background task-row projections — query side reads prepared snapshots only.

B03-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 19).  This module
owns the **Runtime** half of the task-view query split: a small row-projection
store whose contents are prepared *out of band* and whose read side is a pure
snapshot lookup.

Why this exists
---------------
``acp.scheduler.task_views.query_project_tasks`` already returns the
jobs-authoritative base facts (a single SQL join over the ``tasks`` index and the
``jobs`` authority).  The old v2 task-view then enriched every *active* row by
reading and parsing each job's ``state.json`` inside the request (``v2_routes``
``_enrich_active_rows`` -> ``v1_routes`` ``_enrich_job_snapshot``).  That is an
N+1 synchronous scan/parse on the query path and it is exactly what a query must
not do (plan exit gate: *query must not advance state/transfer/science*).

The split implemented here:

* :class:`TaskProjectionStore` holds a **prepared snapshot** of the
  per-task *enrichment projection* (the live-ish fields the UI shows:
  ``stage_index``/``stage_total``/``stage_detail``/``progress_state``/
  ``display_method``/``live_status``).
* The snapshot is filled by :meth:`TaskProjectionStore.refresh`, which pulls
  rows from an injected :class:`TaskProjectionSourcePort` — the legacy side of
  the seam lives in ``acp`` (the HTTP layer) and already knows how to read a job
  and its ``state.json``.  Refresh may run synchronously (tests, a caller that
  wants determinism) or in the background via :meth:`request_refresh`.
* The **query side** is :meth:`TaskProjectionStore.enrich_rows` and
  :meth:`TaskProjectionStore.snapshot`.  They never call the source, never open
  a file, and never touch a database — they only merge prepared values into the
  caller's row dicts.  A missing entry leaves the row's jobs-authoritative base
  facts untouched (no reverse-scan fallback).

Invalidation hooks
------------------
:meth:`invalidate`, :meth:`invalidate_many`, :meth:`invalidate_all` and
:meth:`clear` drop prepared values so the next background refresh rebuilds them
from the authoritative source.  They only ever mutate this store's own snapshot;
nothing here can write the ``jobs`` authority.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package.  This module is stdlib only; the delegation adapter lives on the
legacy side (``acp.api.v2_routes``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from acp_services.runtime.freshness import (
    OWNER_JOB,
    ProjectionState,
    incoming_is_late,
    resolve_projection_state,
)
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso

logger = logging.getLogger(__name__)

__all__ = [
    "ANNOTATION_FIELDS",
    "PROJECTION_FIELDS",
    "TaskProjectionSourcePort",
    "TaskProjectionStore",
    "TaskRowProjection",
]

#: The availability/freshness/diagnostic fields :meth:`TaskProjectionStore.annotate_rows`
#: may add to a row.  Kept separate from :data:`PROJECTION_FIELDS` (the enrichment
#: vocabulary) so the two never blur: enrichment carries live fact values, these
#: carry the *state* of the prepared projection.
ANNOTATION_FIELDS: tuple[str, ...] = (
    "availability",
    "freshness",
    "basis_revision",
    "observed_at",
    "stale_dimensions",
)


def _override_availability(state: ProjectionState, availability: str) -> ProjectionState:
    """Return *state* with an explicit availability (freshness/basis untouched)."""
    return ProjectionState(
        availability=availability,
        freshness=state.freshness,
        basis_revision=state.basis_revision,
        current_revision=state.current_revision,
        stale_dimensions=state.stale_dimensions,
        observed_at=state.observed_at,
    )


#: The exact enrichment fields the v2 task view may carry on a row.  Frozen here
#: so the projection can only ever produce this vocabulary — a source that
#: returns anything else is a contract violation, and an unlisted key can never
#: leak into a response.
PROJECTION_FIELDS: tuple[str, ...] = (
    "stage_index",
    "stage_total",
    "stage_detail",
    "progress_state",
    "display_method",
    "live_status",
)


@dataclass(frozen=True)
class TaskRowProjection:
    """The prepared enrichment projection of one task row.

    ``fields`` holds only the :data:`PROJECTION_FIELDS` keys that were actually
    observed; ``None`` values are meaningless to the view and are dropped before
    storage so a projection never clobbers a base fact with a null.

    Revision bookkeeping is per owner dimension: ``basis_revisions`` maps each
    owner dimension (job / attempt / name / catalog / organization) to the
    revision the projection was built against.  ``basis_revision`` is the legacy
    single-int job revision, retained for wire compatibility (additive only).
    ``observed_at`` is diagnostic and never decides freshness.
    """

    task_id: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    basis_revision: int = 0
    observed_at: str = ""
    basis_revisions: Mapping[str, int] = field(default_factory=dict)
    availability: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain mapping (enum-free wire shape)."""
        return {
            "task_id": self.task_id,
            "fields": dict(self.fields),
            "basis_revision": int(self.basis_revision),
            "observed_at": self.observed_at,
            "basis_revisions": dict(self.basis_revisions),
            "availability": self.availability,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskRowProjection:
        """Rebuild a projection from :meth:`to_dict` output (defensive)."""
        raw_fields = payload.get("fields")
        fields: dict[str, Any] = {}
        if isinstance(raw_fields, Mapping):
            fields = {k: v for k, v in raw_fields.items() if k in PROJECTION_FIELDS}
        raw_basis = payload.get("basis_revisions")
        basis_revisions: dict[str, int] = {}
        if isinstance(raw_basis, Mapping):
            basis_revisions = {
                str(k): int(v)
                for k, v in raw_basis.items()
                if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            }
        return cls(
            task_id=str(payload.get("task_id") or ""),
            fields=fields,
            basis_revision=int(payload.get("basis_revision") or 0),
            observed_at=str(payload.get("observed_at") or ""),
            basis_revisions=basis_revisions,
            availability=str(payload.get("availability") or ""),
        )


@runtime_checkable
class TaskProjectionSourcePort(Protocol):
    """Collect authoritative enrichment facts for a batch of task ids.

    The legacy-side adapter delegates to the existing task index / task views
    enrichment (``_enrich_job_snapshot``); it is the *only* thing that may read
    ``state.json``.  It must be safe to call from a background thread, and it
    must never mutate the ``jobs`` authority.
    """

    def collect(self, task_ids: Sequence[str]) -> Sequence[TaskRowProjection]:
        """Return one :class:`TaskRowProjection` per readable task id."""
        ...


class TaskProjectionStore:
    """In-memory row-projection snapshot with a background refresh path.

    Args:
        source: The injected collector.  ``None`` makes the store a pure
            pass-through (``enrich_rows`` is a no-op), which keeps a caller that
            has no source fully on the jobs-authoritative base facts.
        clock: Diagnostic timestamp source; tests pin it.
        auto_refresh: When ``True``, :meth:`request_refresh` schedules a single
            background daemon that drains the pending batch.  When ``False``
            (tests / callers that want determinism) :meth:`request_refresh` is a
            no-op and only :meth:`refresh` updates the snapshot.
        max_batch: Upper bound on ids collected per source call (bounded work).
    """

    def __init__(
        self,
        *,
        source: TaskProjectionSourcePort | None = None,
        clock: Callable[[], str] | None = None,
        auto_refresh: bool = False,
        max_batch: int = 200,
    ) -> None:
        self._source = source
        self._clock = clock or _utc_now_iso
        self.auto_refresh = bool(auto_refresh)
        self._max_batch = max(1, int(max_batch))

        self._lock = threading.RLock()
        self._rows: dict[str, TaskRowProjection] = {}
        self._generation = 0

        self._pending_lock = threading.Lock()
        self._pending: set[str] = set()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Background update (write side — never runs on the query path)
    # ------------------------------------------------------------------ #

    def refresh(self, task_ids: Iterable[str]) -> int:
        """Collect and store projections for *task_ids* (bounded, synchronous).

        Returns the number of rows stored.  Safe to call from a background
        thread; an unknown id (no jobs record) is simply absent and will not
        clobber the row's base facts.
        """
        ids = self._bounded_ids(task_ids)
        if self._source is None or not ids:
            return 0
        collected = self._source.collect(ids)
        return self._merge(collected)

    def request_refresh(self, task_ids: Iterable[str]) -> None:
        """Schedule a background refresh; never blocks and never reads a file.

        With ``auto_refresh=False`` this is a no-op so tests can drive
        :meth:`refresh` directly and assert the query path never scans.
        """
        if not self.auto_refresh or self._source is None:
            return
        ids = self._bounded_ids(task_ids)
        if not ids:
            return
        with self._pending_lock:
            self._pending.update(ids)
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._drain,
                name="acp-task-projection",
                daemon=True,
            )
            self._worker.start()

    def _drain(self) -> None:
        while True:
            with self._pending_lock:
                batch = list(self._pending)
                self._pending.clear()
            if not batch:
                return
            try:
                self.refresh(batch)
            except Exception:  # noqa: BLE001 - a background refresh must never crash the app
                logger.warning("task projection refresh failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # Invalidation hooks
    # ------------------------------------------------------------------ #

    def invalidate(self, task_id: str) -> None:
        """Drop the prepared projection for one task (next refresh rebuilds it)."""
        with self._lock:
            if self._rows.pop(task_id, None) is not None:
                self._generation += 1

    def invalidate_many(self, task_ids: Iterable[str]) -> None:
        """Drop the prepared projections for several tasks."""
        with self._lock:
            removed = False
            for task_id in task_ids:
                if self._rows.pop(task_id, None) is not None:
                    removed = True
            if removed:
                self._generation += 1

    def invalidate_all(self) -> None:
        """Drop every prepared projection (query falls back to base facts)."""
        self.clear()

    def clear(self) -> None:
        """Empty the snapshot (used by the "delete projection" failure QA)."""
        with self._lock:
            if self._rows:
                self._rows.clear()
                self._generation += 1

    # ------------------------------------------------------------------ #
    # Query side (read only — snapshot lookups, no source / no file / no DB)
    # ------------------------------------------------------------------ #

    @property
    def generation(self) -> int:
        """Monotonic count of snapshot mutations (diagnostic)."""
        with self._lock:
            return self._generation

    def snapshot(self) -> Mapping[str, TaskRowProjection]:
        """Return an immutable-by-copy view of the prepared projections."""
        with self._lock:
            return dict(self._rows)

    def covers(self, task_id: str) -> bool:
        """Whether the prepared snapshot holds an entry for *task_id*."""
        with self._lock:
            return task_id in self._rows

    def enrichment_for(self, task_id: str) -> Mapping[str, Any]:
        """Return the prepared enrichment fields for *task_id* (``{}`` if absent)."""
        with self._lock:
            projection = self._rows.get(task_id)
            return dict(projection.fields) if projection is not None else {}

    def enrich_rows(self, rows: Sequence[dict[str, Any]]) -> int:
        """Merge prepared enrichment into *rows* in place; return covered count.

        Only the prepared snapshot is consulted: no source call, no ``state.json``
        read, no database access.  A row without a prepared entry keeps its
        jobs-authoritative base facts unchanged (no synchronous scan/parse
        fallback).
        """
        covered = 0
        with self._lock:
            for row in rows:
                task_id = row.get("id")
                if not isinstance(task_id, str):
                    continue
                projection = self._rows.get(task_id)
                if projection is None:
                    continue
                covered += 1
                for key, value in projection.fields.items():
                    if value is not None:
                        row[key] = value
        return covered

    def annotate_rows(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        current_revisions: Mapping[str, Mapping[str, int]]
        | Callable[[dict[str, Any]], Mapping[str, int]]
        | None = None,
        freshly_enriched_ids: Iterable[str] = (),
    ) -> int:
        """Add availability/freshness/basis_revision/observed_at to *rows*.

        Purely additive: it writes only :data:`ANNOTATION_FIELDS` and never
        touches the jobs-authoritative base facts or the prepared enrichment.
        Availability/freshness come from the prepared projection's per-dimension
        basis compared against the caller-supplied **current** owner revisions
        (each dimension independently); with no projection the row keeps its base
        facts and reports ``unavailable``/``unknown``.

        Args:
            current_revisions: Either a ``{task_id: {dimension: revision}}`` map
                or a callable ``row -> {dimension: revision}``.  Missing entries
                compare as "no current" (freshness is decided only on dimensions
                both sides carry).
            freshly_enriched_ids: Ids that were synchronously observed against the
                live source by the retained compatibility path; those report a
                fresh projection whose basis is the current revision.

        Returns:
            The number of rows that carried a prepared projection.
        """
        fresh_ids = set(freshly_enriched_ids)
        covered = 0
        with self._lock:
            pending = set(self._pending)
            for row in rows:
                task_id = row.get("id")
                if not isinstance(task_id, str):
                    continue
                projection = self._rows.get(task_id)
                current = self._current_for(row, task_id, current_revisions)
                if projection is None:
                    state = resolve_projection_state(
                        has_projection=False,
                        current_revisions=current,
                        preparing=task_id in pending,
                        freshly_enriched=task_id in fresh_ids,
                    )
                else:
                    covered += 1
                    state = resolve_projection_state(
                        has_projection=True,
                        basis_revisions=projection.basis_revisions
                        or {OWNER_JOB: projection.basis_revision},
                        current_revisions=current,
                        observed_at=projection.observed_at,
                    )
                    if projection.availability:
                        state = _override_availability(state, projection.availability)
                self._apply_annotation(row, state)
        return covered

    @staticmethod
    def _current_for(
        row: dict[str, Any],
        task_id: str,
        current_revisions: Mapping[str, Mapping[str, int]]
        | Callable[[dict[str, Any]], Mapping[str, int]]
        | None,
    ) -> Mapping[str, int]:
        if current_revisions is None:
            return {}
        if callable(current_revisions):
            resolved = current_revisions(row)
            return resolved if isinstance(resolved, Mapping) else {}
        resolved = current_revisions.get(task_id)
        return resolved if isinstance(resolved, Mapping) else {}

    @staticmethod
    def _apply_annotation(row: dict[str, Any], state: ProjectionState) -> None:
        row["availability"] = state.availability
        row["freshness"] = state.freshness
        row["basis_revision"] = dict(state.basis_revision)
        row["observed_at"] = state.observed_at
        row["stale_dimensions"] = list(state.stale_dimensions)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _bounded_ids(self, task_ids: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for task_id in task_ids:
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            ordered.append(task_id)
            if len(ordered) >= self._max_batch:
                break
        return ordered

    def _merge(self, collected: Sequence[TaskRowProjection]) -> int:
        stored = 0
        observed_at = self._clock()
        with self._lock:
            for raw in collected:
                task_id = raw.task_id
                if not task_id:
                    continue
                fields = {
                    key: value
                    for key, value in raw.fields.items()
                    if key in PROJECTION_FIELDS and value is not None
                }
                incoming_basis = dict(raw.basis_revisions) or {OWNER_JOB: int(raw.basis_revision)}
                existing = self._rows.get(task_id)
                if existing is not None and incoming_is_late(
                    incoming_basis,
                    dict(existing.basis_revisions) or {OWNER_JOB: existing.basis_revision},
                ):
                    continue
                self._rows[task_id] = TaskRowProjection(
                    task_id=task_id,
                    fields=fields,
                    basis_revision=int(raw.basis_revision),
                    observed_at=raw.observed_at or observed_at,
                    basis_revisions=incoming_basis,
                    availability=raw.availability,
                )
                stored += 1
            if stored:
                self._generation += 1
        return stored
