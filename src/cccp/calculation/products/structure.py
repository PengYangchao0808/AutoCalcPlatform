"""Structure/trajectory product builder (first science product, plan todo 11).

The builder is the *real generating function* registered for
:data:`~cccp.calculation.products.contracts.ProductKind.STRUCTURE_TRAJECTORY`.
It is a pure data transform: it consumes already-computed frames + the
raw-input digest and produces a versioned
:class:`~cccp.calculation.products.contracts.ScienceProduct`.  It performs no
I/O, no re-computation and never zero-fills missing geometry.

Generation timing (see also ``registry``): after a trajectory-producing task
completes, at result-publication time, from the task's ``TaskResult`` plus the
frame geometry — the platform publishes; cccp owns the science contract.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence

from cccp.calculation.products.contracts import (
    CoordinateUnits,
    FrameRecord,
    MissingReason,
    ProductIdentity,
    ProductKind,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    StructureTrajectoryPayload,
    compute_input_digest,
)

__all__ = [
    "GENERATING_FUNCTION",
    "build_structure_trajectory_product",
]

#: Fully-qualified name of the real generating function (stable identity field).
GENERATING_FUNCTION = "cccp.calculation.products.structure.build_structure_trajectory_product"


def build_structure_trajectory_product(
    *,
    frames: Sequence[FrameRecord],
    topology_source: str,
    coordinate_units: CoordinateUnits = CoordinateUnits.ANGSTROM,
    reference_frame: ReferenceFrame | None = None,
    atom_order: Sequence[str] = (),
    raw_input: object | None = None,
    input_digest: str | None = None,
    algorithm: str = "structure_trajectory",
    algorithm_version: str = "1",
    params_digest: str = "",
) -> ScienceProduct:
    """Build a structure/trajectory :class:`ScienceProduct`.

    Args:
        frames: Frame records in original order (indices are preserved).
        topology_source: Where the connectivity came from (e.g. ``orca_xyz``).
        coordinate_units: Explicit units of every frame's coordinates.
        reference_frame: Axes/origin (defaults to cartesian/native).
        atom_order: Stable ``atom_uid`` order shared across frames.
        raw_input: Raw input object digested into ``identity.input_digest``.
        input_digest: Precomputed digest; overrides ``raw_input`` when given.
        algorithm: Stable algorithm name for the identity.
        algorithm_version: Algorithm version for the identity.
        params_digest: Optional digest of the generation parameters.

    Returns:
        An ``AVAILABLE`` product when at least one frame is supplied; an
        explicit ``UNAVAILABLE`` product (reason ``source_missing``) otherwise.
    """
    identity = ProductIdentity(
        product_kind=ProductKind.STRUCTURE_TRAJECTORY,
        generator=GENERATING_FUNCTION,
        input_digest=input_digest
        or (
            compute_input_digest(raw_input)
            if raw_input is not None
            else compute_input_digest([frame.to_dict() for frame in frames])
        ),
        algorithm=algorithm,
        algorithm_version=algorithm_version,
        params_digest=params_digest,
    )
    if not frames:
        return ScienceProduct.make_unavailable(
            identity=identity,
            missing_reason=MissingReason.SOURCE_MISSING,
            state=ProductState.UNAVAILABLE,
        )
    payload = StructureTrajectoryPayload(
        coordinate_units=coordinate_units,
        topology_source=topology_source,
        frames=tuple(frames),
        atom_order=tuple(atom_order),
        reference_frame=reference_frame if reference_frame is not None else ReferenceFrame(),
    )
    return ScienceProduct.make_available(identity=identity, payload=payload)
