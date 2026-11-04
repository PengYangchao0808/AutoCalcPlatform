"""Single writer for per-step electronic-energy products.

``acp_energy_product_v1`` is the canonical document a workflow publishes for
the electronic energy one completed step printed (design note: ``RESULT/energy/
<step>.json``).  The optimize path has written it inline since round-8
(``acp.calculations.executor``); BatchOptimize published no such product at all,
so an agent had to add single-point jobs to read energies that had already been
computed (round-9 M5).

This module is the one implementation of the document:

* :func:`build_energy_product_payload` freezes the key set so optimize and
  BatchOptimize cannot drift apart again;
* :func:`publish_energy_product` writes the file atomically and registers the
  ``kind=energy_report`` manifest product through the caller's manifest (the
  batch engine writes one manifest per run — no second disk write);
* :func:`normalize_energy_method` is the single place that turns "no level was
  resolvable" into ``null``.  An empty string is never a value: ``""`` means
  "a method exists and is empty", which is a false fact (round-9 M12).

Nothing here computes, defaults or invents an energy or a level: a missing
``energy_hartree`` publishes nothing.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acp.results.frame_candidate_store import atomic_write_text
from acp.storage.manifest import ProductKind, ResultManifest

logger = logging.getLogger(__name__)

__all__ = [
    "ENERGY_METHOD_UNKNOWN_REASON",
    "ENERGY_PRODUCT_SCHEMA",
    "ENERGY_SOURCE_SCIENTIFIC_RESULT",
    "ENERGY_SUBDIR",
    "build_energy_product_payload",
    "energy_product_relative_path",
    "normalize_energy_method",
    "publish_energy_product",
    "write_energy_product",
]

#: Wire schema id of one step's energy product.
ENERGY_PRODUCT_SCHEMA = "acp_energy_product_v1"
#: ``RESULT/``-relative directory of the products.
ENERGY_SUBDIR = "energy"
#: The step's own ``scientific_result.json`` is the value source (never derived).
ENERGY_SOURCE_SCIENTIFIC_RESULT = "scientific_result.json"
#: Recorded when no step/plan/job level resolved a method for the product.
ENERGY_METHOD_UNKNOWN_REASON = "no_level_in_plan_or_spec"


def normalize_energy_method(value: object) -> str | None:
    """Return a real method/basis id, or ``None`` — never an empty string.

    ``""``/whitespace/non-strings are "unknown", not "a method whose name is
    empty"; writing ``""`` would claim a value that does not exist.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def energy_product_relative_path(step_id: str) -> str:
    """Return the ``RESULT/``-relative path of one energy product."""
    return f"{ENERGY_SUBDIR}/{step_id}.json"


def build_energy_product_payload(
    *,
    step_id: str,
    step_kind: str,
    energy_hartree: float,
    method: object = None,
    source: str = ENERGY_SOURCE_SCIENTIFIC_RESULT,
) -> dict[str, Any]:
    """Build the ``acp_energy_product_v1`` document for one completed step.

    The key set is the optimize writer's shape (round-8 D6/T11).  Callers that
    know more facts (batch item, basis, resolution source) record them in the
    manifest product metadata instead of adding optional JSON keys, so two
    workflows never publish different schemas for the same quantity.
    """
    value = float(energy_hartree)
    return {
        "schema_version": ENERGY_PRODUCT_SCHEMA,
        "step_id": str(step_id),
        "step_kind": str(step_kind),
        "energy": value,
        "energy_hartree": value,
        "unit": "hartree",
        "method": normalize_energy_method(method),
        "source": str(source),
    }


def write_energy_product(
    result_dir: Path | str,
    *,
    step_id: str,
    step_kind: str,
    energy_hartree: float,
    method: object = None,
    source: str = ENERGY_SOURCE_SCIENTIFIC_RESULT,
) -> Path:
    """Atomically write one energy product; returns the file path.

    Raises:
        ValueError: The energy is missing, non-numeric or non-finite.
    """
    if isinstance(energy_hartree, bool) or not isinstance(energy_hartree, (int, float)):
        raise ValueError("energy product requires a numeric energy_hartree")
    if not math.isfinite(float(energy_hartree)):
        raise ValueError("energy product requires a finite energy_hartree")
    payload = build_energy_product_payload(
        step_id=step_id,
        step_kind=step_kind,
        energy_hartree=float(energy_hartree),
        method=method,
        source=source,
    )
    relative = energy_product_relative_path(step_id)
    path = Path(result_dir) / relative
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def publish_energy_product(
    result_dir: Path | str,
    *,
    step_id: str,
    step_kind: str,
    energy_hartree: object,
    method: object,
    manifest: ResultManifest,
    product_id: str,
    label: str,
    source: str = ENERGY_SOURCE_SCIENTIFIC_RESULT,
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Write and register one energy product; returns its product id or ``None``.

    Publication never raises: a missing energy is client-visible "no product"
    (the caller reports it) and an I/O failure is logged.  This mirrors the
    thermochemistry writer's "a product must never fail the workflow" rule.

    The registration always carries ``energy_hartree`` so a manifest reader
    (structure viewer, evidence projection) reads the value without opening
    the JSON; *metadata* adds the caller's provenance facts.
    """
    if isinstance(energy_hartree, bool) or not isinstance(energy_hartree, (int, float)):
        return None
    value = float(energy_hartree)
    if not math.isfinite(value):
        return None
    try:
        relative = energy_product_relative_path(step_id)
        write_energy_product(
            result_dir,
            step_id=step_id,
            step_kind=step_kind,
            energy_hartree=value,
            method=method,
            source=source,
        )
    except (OSError, ValueError):
        logger.warning(
            "could not publish energy product %s for step %s; skipping",
            product_id,
            step_id,
            exc_info=True,
        )
        return None
    product_metadata: dict[str, Any] = {"energy_hartree": value}
    if metadata:
        product_metadata.update(dict(metadata))
    manifest.add_product(
        id=product_id,
        label=label,
        path=relative,
        kind=ProductKind.ENERGY_REPORT,
        metadata=product_metadata,
    )
    return product_id
