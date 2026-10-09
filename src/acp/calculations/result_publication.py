"""ACP result publication — the single named ACP persistence entry.

This module is the one ACP-side writer of ``RESULT/result_manifest.json`` and
the owner of the publication contract (plan todo 16).  Executor / scan / IRC
product registration routes through :func:`register_result_manifest` (guard:
``tests/test_architecture_invariants.py::test_all_new_results_register_result_manifest``).

Publication contract — persistence order after the calculation completes:

1. the scientific result record (``scientific_result.json``) and its artifact
   references are persisted first;
2. platform view products are materialised and the display manifest
   (``result_manifest.json``) is published;
3. publication is marked complete (``publication_state.json``).

The scientific result record (durable scientific output) and the platform
display manifest (presentation) are separate responsibilities on purpose.
Every step is idempotent keyed by the stable ``result_id``: re-publishing the
same identity after completion is a no-op.

:func:`recover_publication` checks for an existing valid scientific result
BEFORE deciding whether to execute the calculation: when one exists, only
publication is retried and the calculation callable is never invoked.  The
pending-publication state is ACP-internal bookkeeping kept in this module —
deliberately NOT a scheduler job status (``acp.scheduler.jobs.JobStatus`` is
unchanged by publication).

Boundary: a crash between calculation-process exit and step 1 is recovered
from the raw output files or explicitly allowed to recompute; exactly-once
delivery across that window is NOT promised (plan todo 16 boundary).

Responsibility split (todo 16 review fix): adapters only transform data and
never write files; this module only persists; callers orchestrate both.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.storage.manifest import ResultManifest

logger = logging.getLogger(__name__)

__all__ = [
    "PUBLICATION_STATE_FILENAME",
    "SCIENTIFIC_RESULT_FILENAME",
    "ArtifactReference",
    "PublicationOutcome",
    "PublicationState",
    "ScientificResultRecord",
    "load_publication_state",
    "load_scientific_result",
    "mark_publication_complete",
    "publish_result",
    "recover_publication",
    "register_result_manifest",
    "save_scientific_result",
]

SCIENTIFIC_RESULT_FILENAME = "scientific_result.json"
PUBLICATION_STATE_FILENAME = "publication_state.json"


# ── records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Reference to one scientific artifact file (persisted with the record).

    ``path`` is relative to the task ``RESULT/`` directory.
    """

    path: str
    type: str = "file"

    def to_dict(self) -> dict[str, str]:
        """Serialise to a JSON-safe dict."""
        return {"path": self.path, "type": self.type}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArtifactReference:
        """Deserialise from a parsed artifact entry."""
        return cls(path=str(payload.get("path", "")), type=str(payload.get("type", "file")))


@dataclass(frozen=True, slots=True)
class ScientificResultRecord:
    """Durable scientific output of one completed calculation (contract ①).

    ``result_id`` is the stable publication identity — the idempotency key of
    the whole publication sequence.  ``summary`` carries the scientific data
    itself (energies, counts, …); ``artifacts`` are the artifact references
    persisted alongside it.  This record is intentionally NOT the platform
    display manifest.
    """

    result_id: str
    kind: str
    artifacts: tuple[ArtifactReference, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "result_id": self.result_id,
            "kind": self.kind,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "summary": dict(self.summary),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScientificResultRecord:
        """Deserialise from a parsed ``scientific_result.json`` payload."""
        raw_artifacts = payload.get("artifacts")
        artifacts = tuple(
            ArtifactReference.from_dict(item)
            for item in (raw_artifacts if isinstance(raw_artifacts, list) else [])
            if isinstance(item, Mapping)
        )
        raw_summary = payload.get("summary")
        summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {}
        return cls(
            result_id=str(payload.get("result_id", "")),
            kind=str(payload.get("kind", "")),
            artifacts=artifacts,
            summary=summary,
        )


@dataclass(frozen=True, slots=True)
class PublicationState:
    """ACP-internal publication bookkeeping — NOT a scheduler job status.

    Persisted as ``publication_state.json``; ``complete`` flips true only after
    the display manifest is published (contract ③).
    """

    result_id: str
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {"result_id": self.result_id, "complete": self.complete}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PublicationState:
        """Deserialise from a parsed ``publication_state.json`` payload."""
        return cls(
            result_id=str(payload.get("result_id", "")),
            complete=bool(payload.get("complete", False)),
        )


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    """Result of one :func:`recover_publication` / retry round.

    ``qc_executed`` is True when the calculation callable actually ran;
    ``recovered`` is True when an existing valid scientific result was found
    and only publication was retried.
    """

    record: ScientificResultRecord
    manifest: ResultManifest
    qc_executed: bool
    recovered: bool


# ── low-level persistence ────────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def save_scientific_result(
    result_dir: Path | str, record: ScientificResultRecord
) -> ScientificResultRecord:
    """Persist contract ① — the scientific result record + artifact references.

    Idempotent by stable identity: an existing record carrying the same
    ``result_id`` is kept (first write wins) and returned unchanged.
    """
    result_dir = Path(result_dir)
    existing = load_scientific_result(result_dir)
    if existing is not None and existing.result_id == record.result_id:
        return existing
    _atomic_write_json(result_dir / SCIENTIFIC_RESULT_FILENAME, record.to_dict())
    return record


def load_scientific_result(result_dir: Path | str) -> ScientificResultRecord | None:
    """Return the persisted scientific record, or ``None`` when absent.

    An unreadable record is treated as absent (and logged): callers may then
    explicitly recompute per the recovery boundary.
    """
    path = Path(result_dir) / SCIENTIFIC_RESULT_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("scientific result record is not a JSON object")
        return ScientificResultRecord.from_dict(payload)
    except (OSError, ValueError, TypeError):
        logger.warning("unreadable scientific result record at %s; treating as absent", path)
        return None


def register_result_manifest(result_dir: Path | str, manifest: ResultManifest) -> Path:
    """Persist contract ② — the platform display manifest.

    The single named ACP persistence seam for ``RESULT/result_manifest.json``:
    executor / scan / IRC product registration routes through here instead of
    writing the manifest themselves.
    """
    return manifest.write(Path(result_dir))


def mark_publication_complete(result_dir: Path | str, result_id: str) -> PublicationState:
    """Persist contract ③ — mark publication complete for *result_id*."""
    state = PublicationState(result_id=result_id, complete=True)
    _atomic_write_json(Path(result_dir) / PUBLICATION_STATE_FILENAME, state.to_dict())
    return state


def load_publication_state(result_dir: Path | str) -> PublicationState | None:
    """Return the ACP-internal publication state, or ``None`` when absent."""
    path = Path(result_dir) / PUBLICATION_STATE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("publication state is not a JSON object")
        return PublicationState.from_dict(payload)
    except (OSError, ValueError, TypeError):
        logger.warning("unreadable publication state at %s; treating as absent", path)
        return None


# ── publication sequence ─────────────────────────────────────────────────────


def publish_result(
    result_dir: Path | str,
    *,
    record: ScientificResultRecord,
    manifest: ResultManifest,
    copy_products: Callable[[], None] | None = None,
) -> ResultManifest:
    """Run the publication sequence for *record* in contract order ①②③.

    ``copy_products`` (optional) materialises platform view products between ①
    and ②; it must be idempotent so a retry after a partial copy completes
    without side effects.  Publishing an already-completed ``result_id`` is an
    idempotent no-op.
    """
    result_dir = Path(result_dir)
    state = load_publication_state(result_dir)
    if state is not None and state.complete and state.result_id == record.result_id:
        try:
            existing = ResultManifest.read(result_dir)
        except FileNotFoundError:
            logger.warning(
                "publication marked complete for %s but manifest missing; re-publishing",
                record.result_id,
            )
        else:
            logger.info("publication already complete for %s; idempotent no-op", record.result_id)
            return existing
    save_scientific_result(result_dir, record)  # ① scientific result + artifact refs
    if copy_products is not None:
        copy_products()  # ② platform view products
    register_result_manifest(result_dir, manifest)  # ② display manifest
    mark_publication_complete(result_dir, record.result_id)  # ③ done marker
    return manifest


def recover_publication(
    result_dir: Path | str,
    *,
    result_id: str,
    execute_qc: Callable[[], ScientificResultRecord],
    build_manifest: Callable[[ScientificResultRecord], ResultManifest],
    copy_products: Callable[[], None] | None = None,
) -> PublicationOutcome:
    """Recovery entry — check for an existing valid scientific result first.

    * valid record (same ``result_id``) already persisted → the calculation
      callable is NEVER invoked; only publication is retried (products, ②, ③);
    * no valid record → the calculation runs exactly once (recompute is
      explicitly allowed at this boundary) and the full sequence runs.

    The check-before-execute order is the contract: fault-injection tests
    assert the calculation call count does not increase when a valid
    scientific result exists.
    """
    result_dir = Path(result_dir)
    record = load_scientific_result(result_dir)  # check FIRST
    if record is not None and record.result_id == result_id:
        manifest = build_manifest(record)
        publish_result(result_dir, record=record, manifest=manifest, copy_products=copy_products)
        return PublicationOutcome(
            record=record, manifest=manifest, qc_executed=False, recovered=True
        )
    record = execute_qc()
    if record.result_id != result_id:
        raise ValueError(
            f"calculation returned result_id {record.result_id!r}, expected {result_id!r}"
        )
    manifest = build_manifest(record)
    publish_result(result_dir, record=record, manifest=manifest, copy_products=copy_products)
    return PublicationOutcome(record=record, manifest=manifest, qc_executed=True, recovered=False)
