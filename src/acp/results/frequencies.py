"""FREQ-viewer JSON projection from an :class:`OrcaCalculation` (design doc §11.2)."""

from __future__ import annotations

import math
from typing import Any

from acp.results.orca_parser import OrcaCalculation

__all__ = ["build_frequency_report", "build_normal_modes_product"]


def build_normal_modes_product(
    calc: Any,
    *,
    geometry_product_id: str | None,
    atom_count: int,
) -> dict[str, Any]:
    """Build a ``normal_modes_v1`` product dict from *calc*.

    *calc* is duck-typed: must expose ``mode_frequencies`` (dict[int, float]),
    ``mode_vectors`` (dict[int, tuple[tuple[float, float, float], ...]]),
    and ``mode_ir_intensities`` (dict[int, float] | None).

    Modes are emitted in ascending ``mode_index`` order using ORCA's native
    indices (never reindexed).  A mode is skipped with a warning when:
    - its frequency is missing from ``mode_frequencies``
    - ``len(vectors) != atom_count``
    - any vector row has != 3 components or contains non-finite values

    Returns:
        Dict conforming to the ``normal_modes_v1`` schema (doc §4.2).
    """
    warnings: list[str] = []
    modes: list[dict[str, Any]] = []

    mode_vectors: dict[int, tuple[tuple[float, float, float], ...]] = getattr(
        calc, "mode_vectors", {}
    )
    mode_frequencies: dict[int, float] = getattr(calc, "mode_frequencies", {})
    mode_ir_intensities: dict[int, float] | None = getattr(
        calc, "mode_ir_intensities", None
    )

    for mode_index in sorted(mode_vectors.keys()):
        vectors = mode_vectors[mode_index]

        freq = mode_frequencies.get(mode_index)
        if freq is None:
            warnings.append(
                f"Mode {mode_index}: frequency missing from mode_frequencies; skipped"
            )
            continue

        if len(vectors) != atom_count:
            warnings.append(
                f"Mode {mode_index}: len(vectors)={len(vectors)} != atom_count={atom_count}; skipped"
            )
            continue

        valid = True
        for row in vectors:
            if len(row) != 3:
                warnings.append(
                    f"Mode {mode_index}: vector row has {len(row)} components (expected 3); skipped"
                )
                valid = False
                break
            for val in row:
                if not math.isfinite(val):
                    warnings.append(
                        f"Mode {mode_index}: non-finite value in vectors; skipped"
                    )
                    valid = False
                    break
            if not valid:
                break

        if not valid:
            continue

        entry: dict[str, Any] = {
            "mode_index": mode_index,
            "frequency_cm1": freq,
            "imaginary": freq < 0,
            "vectors": [list(row) for row in vectors],
        }

        if mode_ir_intensities is not None:
            ir = mode_ir_intensities.get(mode_index)
            if ir is not None:
                entry["ir_intensity"] = ir

        modes.append(entry)

    return {
        "schema_version": "normal_modes_v1",
        "units": {
            "frequency": "cm-1",
            "displacement": "dimensionless_orca_normal_mode",
        },
        "atom_count": atom_count,
        "geometry_product_id": geometry_product_id,
        "modes": modes,
        "warnings": warnings,
    }


def build_frequency_report(calc: OrcaCalculation) -> dict:
    """Build the §11.2 frequency-viewer payload from *calc*.

    When ``calc.mode_vectors`` is non-empty, the report includes
    ``normal_modes_available: True`` and a ``normal_modes`` product dict
    (via :func:`build_normal_modes_product`).  The ``normal_modes_available``
    key is always present for backward compatibility.
    """
    mode_vectors: dict = getattr(calc, "mode_vectors", {})
    has_modes = bool(mode_vectors)

    report: dict[str, Any] = {
        "frequencies": list(calc.frequencies),
        "imaginary_modes": list(calc.imaginary_modes),
        "ir_intensities": list(calc.ir_intensities) if calc.ir_intensities is not None else None,
        "has_imaginary": bool(calc.imaginary_modes),
        "normal_modes_available": has_modes,
    }

    if has_modes:
        first_vectors = next(iter(mode_vectors.values()))
        atom_count = len(first_vectors)
        report["normal_modes"] = build_normal_modes_product(
            calc, geometry_product_id=None, atom_count=atom_count
        )

    return report
