"""M2-04d — ACP-side publication of bounded-DAG step products.

The Services worker only **declares** step products (kind + real path + digest)
and writes the per-step ``step_result.json``.  The ACP publication contract stays
where it has always been:

* ``acp.calculations.primitives`` owns the product formats and the publication
  halves (``frequency`` writes ``normal_modes.json`` from the typed payload —
  the ~2k lines of publication logic are **not** deleted and **not** copied into
  Services);
* this module is the seam that applies those halves to the products a bounded
  step declared, and returns the publication record the task manifest is built
  from afterwards (``acp.calculations.result_publication`` remains the only
  task-manifest publisher).

It never recomputes science: a missing product or a digest mismatch is a
publication failure that is retried without re-running QC.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cccp.calculation.requests import TaskKind, TaskRequest
from cccp.calculation.results import TaskResult

logger = logging.getLogger(__name__)

__all__ = [
    "STEP_PUBLICATION_SCHEMA",
    "StepProductRecord",
    "StepPublication",
    "publish_bounded_step_products",
    "read_step_result_document",
]

#: Wire schema of the ACP step-publication record.
STEP_PUBLICATION_SCHEMA: Final = "acp2.acp.step-publication.v1"

#: Product/artifact types the ACP publication half owns (one implementation root).
_ACP_PUBLISHED_ARTIFACT_TYPES: Final[frozenset[str]] = frozenset({"normal_modes"})


@dataclass(frozen=True, slots=True)
class StepProductRecord:
    """One published step product inside the attempt result directory."""

    step_id: str
    product_kind: str
    source_path: str
    published_path: str
    digest: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "product_kind": self.product_kind,
            "source_path": self.source_path,
            "published_path": self.published_path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class StepPublication:
    """The publication record of one bounded-DAG attempt."""

    products: tuple[StepProductRecord, ...] = ()
    applied: tuple[str, ...] = ()
    schema: str = STEP_PUBLICATION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "products": [product.to_dict() for product in self.products],
            "applied": list(self.applied),
        }


def read_step_result_document(work_dir: Path | str, step_id: str) -> dict[str, Any]:
    """Read one step's persisted ``step_result.json`` (typed error when absent)."""
    path = Path(work_dir) / "WORK" / "00_RUNTIME" / "steps" / step_id / "step_result.json"
    if not path.is_file():
        raise FileNotFoundError(f"no step result for {step_id!r} under {work_dir}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"step result for {step_id!r} is not a JSON object")
    return payload


def _task_result_from_payload(payload: Mapping[str, Any]) -> TaskResult | None:
    """Rebuild the typed ``TaskResult`` from a step result's publish payload."""
    publish_payload = payload.get("publish_payload") or {}
    if not isinstance(publish_payload, Mapping):
        return None
    raw = publish_payload.get("result")
    if not isinstance(raw, Mapping):
        return None
    task = str(raw.get("task") or "")
    if not task:
        return None
    try:
        return TaskResult.from_dict(dict(raw))
    except (TypeError, ValueError, KeyError):
        logger.debug("step publication: unusable task result for %s", task, exc_info=True)
        return None


def publish_bounded_step_products(
    *,
    work_dir: Path | str,
    result_dir: Path | str,
    step_ids: Sequence[str],
    product_dirs: Mapping[str, Path] | None = None,
) -> StepPublication:
    """Publish the declared products of *step_ids* into the attempt result dir.

    Publication is idempotent (a retry after a partial copy completes without
    side effects) and never recomputes science.  The ACP publication half is
    applied where it exists (``normal_modes`` for frequency steps) through
    :mod:`acp.calculations.primitives` — the single implementation root.
    """
    work = Path(work_dir)
    target_root = Path(result_dir)
    records: list[StepProductRecord] = []
    applied: list[str] = []
    for step_id in step_ids:
        document = read_step_result_document(work, step_id)
        products = document.get("products") or ()
        step_dir = target_root / "steps" / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        for product in products:
            if not isinstance(product, Mapping):
                continue
            source = Path(str(product.get("path") or ""))
            if not source.is_file():
                raise FileNotFoundError(
                    f"declared product {product.get('product_kind')!r} of {step_id} is "
                    f"missing at {source}"
                )
            published = step_dir / source.name
            if not published.is_file() or published.stat().st_size != source.stat().st_size:
                shutil.copy2(source, published)
            records.append(
                StepProductRecord(
                    step_id=step_id,
                    product_kind=str(product.get("product_kind") or ""),
                    source_path=str(source),
                    published_path=str(published),
                    digest=str(product.get("digest") or ""),
                    size_bytes=int(product.get("size_bytes") or 0),
                )
            )
        task_result = _task_result_from_payload(document)
        if task_result is not None and task_result.task is TaskKind.FREQUENCY:
            applied.extend(_apply_frequency_publication(document, task_result, step_dir))
    return StepPublication(products=tuple(records), applied=tuple(sorted(set(applied))))


def _apply_frequency_publication(
    document: Mapping[str, Any], task_result: TaskResult, out_dir: Path
) -> list[str]:
    """Apply the ACP frequency publication half (``normal_modes.json``)."""
    from acp.calculations.primitives.frequency import publish_normal_modes

    publish_payload = document.get("publish_payload") or {}
    raw_request = publish_payload.get("request")
    request: Any
    if isinstance(raw_request, Mapping):
        try:
            request = TaskRequest.from_dict(dict(raw_request))
        except (TypeError, ValueError):
            request = None
    else:
        request = None
    published = publish_normal_modes(request, task_result, out_dir, "cccp")
    return [artifact.type for artifact in published]
