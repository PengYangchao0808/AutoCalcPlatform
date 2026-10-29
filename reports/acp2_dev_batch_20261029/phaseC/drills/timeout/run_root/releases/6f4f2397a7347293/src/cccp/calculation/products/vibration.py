"""Vibration product builder (plan todo 12).

The builder is the *real generating function* registered for
:data:`~cccp.calculation.products.contracts.ProductKind.VIBRATION`.  It is a
pure data transform over an already-parsed
:class:`~cccp.calculation.results.FrequencyAnalysis`-shaped input (the single
scientific parse lives in :mod:`cccp.calculation.frequency_parse`): it consumes
the indexed mode maps and produces a versioned ``ScienceProduct``.

Native mode numbering is preserved verbatim — the mode index is the key from
the source maps, never compacted or renumbered, and zero modes are retained.
A mode without a frequency (or with misaligned displacements) becomes an
explicit :class:`MissingReason` rather than a fabricated value; an input with
no frequency section yields an ``UNAVAILABLE`` product, never an empty success.

Generation timing (see also ``registry``): after a frequency task completes,
at result-publication time, from the parsed ``FrequencyAnalysis`` — the
platform publishes; cccp owns the science contract.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cccp.calculation.products.contracts import (
    DISPLACEMENT_NORMALIZATION,
    DISPLACEMENT_UNITS,
    FrequencyUnits,
    MissingReason,
    ProductIdentity,
    ProductKind,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    VibrationMode,
    VibrationPayload,
    compute_input_digest,
)

__all__ = [
    "GENERATING_FUNCTION",
    "build_vibration_product",
]

#: Fully-qualified name of the real generating function (stable identity field).
GENERATING_FUNCTION = "cccp.calculation.products.vibration.build_vibration_product"


def build_vibration_product(
    *,
    mode_frequencies: Mapping[int, float],
    mode_vectors: Mapping[int, Sequence[Sequence[float]]],
    atom_order: Sequence[str],
    frequency_units: FrequencyUnits = FrequencyUnits.CM1,
    displacement_units: str = DISPLACEMENT_UNITS,
    displacement_normalization: str = DISPLACEMENT_NORMALIZATION,
    mode_ir_intensities: Mapping[int, float] | None = None,
    topology_source: str = "",
    reference_frame: ReferenceFrame | None = None,
    raw_input: object | None = None,
    input_digest: str | None = None,
    algorithm: str = "vibration",
    algorithm_version: str = "1",
    params_digest: str = "",
) -> ScienceProduct:
    """Build a vibration :class:`ScienceProduct` from indexed mode maps.

    Args:
        mode_frequencies: ORCA native mode index -> frequency (zero modes kept).
        mode_vectors: native mode index -> ``(n_atoms, 3)`` displacement rows.
        atom_order: Stable ``atom_uid`` order that displacement rows align to.
        frequency_units: Explicit frequency unit (defaults to cm-1).
        displacement_units: Explicit displacement unit string.
        displacement_normalization: Normalization convention of the vectors.
        mode_ir_intensities: Optional native mode index -> IR intensity.
        topology_source: Where the atom identity came from.
        reference_frame: Axes/origin default.
        raw_input: Raw input object digested into ``identity.input_digest``.
        input_digest: Precomputed digest; overrides ``raw_input`` when given.
        algorithm: Stable algorithm name for the identity.
        algorithm_version: Algorithm version for the identity.
        params_digest: Optional digest of the generation parameters.

    Returns:
        An ``AVAILABLE`` product (partial when a mode carries a reason); an
        explicit ``UNAVAILABLE`` product when the frequency section is absent
        (``missing_frequency``) or the atom identity is absent
        (``missing_atom_uid``) — never an empty success.
    """
    frequencies = {int(index): float(freq) for index, freq in mode_frequencies.items()}
    vectors = {
        int(index): tuple((float(row[0]), float(row[1]), float(row[2])) for row in rows)
        for index, rows in mode_vectors.items()
    }
    intensities = (
        None
        if mode_ir_intensities is None
        else {int(index): float(value) for index, value in mode_ir_intensities.items()}
    )
    atom_uids = tuple(str(uid) for uid in atom_order)

    identity = ProductIdentity(
        product_kind=ProductKind.VIBRATION,
        generator=GENERATING_FUNCTION,
        input_digest=input_digest
        or (
            compute_input_digest(raw_input)
            if raw_input is not None
            else compute_input_digest(
                {
                    "mode_frequencies": frequencies,
                    "mode_vectors": {
                        index: [list(row) for row in rows] for index, rows in vectors.items()
                    },
                }
            )
        ),
        algorithm=algorithm,
        algorithm_version=algorithm_version,
        params_digest=params_digest,
    )
    if not frequencies:
        return ScienceProduct.make_unavailable(
            identity=identity,
            missing_reason=MissingReason.MISSING_FREQUENCY,
            state=ProductState.UNAVAILABLE,
        )
    if not atom_uids:
        return ScienceProduct.make_unavailable(
            identity=identity,
            missing_reason=MissingReason.MISSING_ATOM_UID,
            state=ProductState.UNAVAILABLE,
        )

    modes: list[VibrationMode] = []
    for mode_index in sorted(set(frequencies) | set(vectors)):
        frequency = frequencies.get(mode_index)
        rows = vectors.get(mode_index)
        if rows is not None and len(rows) != len(atom_uids):
            rows = None
        if frequency is None:
            reason = MissingReason.MISSING_FREQUENCY
        elif rows is None:
            reason = MissingReason.MISSING_DISPLACEMENTS
        else:
            reason = None
        modes.append(
            VibrationMode(
                mode_index=mode_index,
                frequency=frequency,
                displacements=rows or (),
                ir_intensity=intensities.get(mode_index) if intensities is not None else None,
                missing_reason=reason,
            )
        )

    payload = VibrationPayload(
        frequency_units=frequency_units,
        displacement_units=displacement_units,
        displacement_normalization=displacement_normalization,
        atom_order=atom_uids,
        modes=tuple(modes),
        topology_source=topology_source,
        reference_frame=reference_frame if reference_frame is not None else ReferenceFrame(),
    )
    return ScienceProduct.make_available(identity=identity, payload=payload)
