"""Versioned science-product contracts (C02-s1 / C02-s2).

Public surface re-export only — implementation lives in the sibling modules
(:mod:`~cccp.calculation.products.contracts`,
:mod:`~cccp.calculation.products.serialize`,
:mod:`~cccp.calculation.products.structure`,
:mod:`~cccp.calculation.products.curve`,
:mod:`~cccp.calculation.products.vibration`,
:mod:`~cccp.calculation.products.derived`,
:mod:`~cccp.calculation.products.registry`).  Importing this package pulls in
no task-execution modules and never imports ``acp``.
"""

from __future__ import annotations

from cccp.calculation.products.contracts import (
    DISPLACEMENT_NORMALIZATION as DISPLACEMENT_NORMALIZATION,
)
from cccp.calculation.products.contracts import (
    DISPLACEMENT_UNITS as DISPLACEMENT_UNITS,
)
from cccp.calculation.products.contracts import (
    LEGACY_PRODUCT_SCHEMA_VERSION as LEGACY_PRODUCT_SCHEMA_VERSION,
)
from cccp.calculation.products.contracts import (
    PRODUCT_PAYLOAD_TYPES as PRODUCT_PAYLOAD_TYPES,
)
from cccp.calculation.products.contracts import (
    PRODUCT_SCHEMA_VERSION as PRODUCT_SCHEMA_VERSION,
)
from cccp.calculation.products.contracts import (
    CoordinateAtom as CoordinateAtom,
)
from cccp.calculation.products.contracts import (
    CoordinateUnits as CoordinateUnits,
)
from cccp.calculation.products.contracts import (
    CurveScatterPayload as CurveScatterPayload,
)
from cccp.calculation.products.contracts import (
    CurveSeries as CurveSeries,
)
from cccp.calculation.products.contracts import (
    DerivedPayload as DerivedPayload,
)
from cccp.calculation.products.contracts import (
    EnergyUnits as EnergyUnits,
)
from cccp.calculation.products.contracts import (
    FrameRecord as FrameRecord,
)
from cccp.calculation.products.contracts import (
    FrequencyUnits as FrequencyUnits,
)
from cccp.calculation.products.contracts import (
    MissingReason as MissingReason,
)
from cccp.calculation.products.contracts import (
    PointRecord as PointRecord,
)
from cccp.calculation.products.contracts import (
    ProductIdentity as ProductIdentity,
)
from cccp.calculation.products.contracts import (
    ProductIODatum as ProductIODatum,
)
from cccp.calculation.products.contracts import (
    ProductKind as ProductKind,
)
from cccp.calculation.products.contracts import (
    ProductParameter as ProductParameter,
)
from cccp.calculation.products.contracts import (
    ProductPayload as ProductPayload,
)
from cccp.calculation.products.contracts import (
    ProductSchemaError as ProductSchemaError,
)
from cccp.calculation.products.contracts import (
    ProductState as ProductState,
)
from cccp.calculation.products.contracts import (
    ReferenceFrame as ReferenceFrame,
)
from cccp.calculation.products.contracts import (
    ScienceProduct as ScienceProduct,
)
from cccp.calculation.products.contracts import (
    SeriesRef as SeriesRef,
)
from cccp.calculation.products.contracts import (
    SourceRef as SourceRef,
)
from cccp.calculation.products.contracts import (
    StructureTrajectoryPayload as StructureTrajectoryPayload,
)
from cccp.calculation.products.contracts import (
    VibrationMode as VibrationMode,
)
from cccp.calculation.products.contracts import (
    VibrationPayload as VibrationPayload,
)
from cccp.calculation.products.contracts import (
    compute_input_digest as compute_input_digest,
)
from cccp.calculation.products.contracts import (
    frame_stable_id as frame_stable_id,
)
from cccp.calculation.products.contracts import (
    point_stable_id as point_stable_id,
)
from cccp.calculation.products.contracts import (
    series_stable_id as series_stable_id,
)
from cccp.calculation.products.curve import (
    build_curve_scatter_product as build_curve_scatter_product,
)
from cccp.calculation.products.derived import (
    build_derived_product as build_derived_product,
)
from cccp.calculation.products.registry import (
    PRODUCT_SPECS as PRODUCT_SPECS,
)
from cccp.calculation.products.registry import (
    ProductSpec as ProductSpec,
)
from cccp.calculation.products.registry import (
    product_spec as product_spec,
)
from cccp.calculation.products.registry import (
    product_specs as product_specs,
)
from cccp.calculation.products.serialize import (
    migrate_v0_to_v1 as migrate_v0_to_v1,
)
from cccp.calculation.products.serialize import (
    product_from_dict as product_from_dict,
)
from cccp.calculation.products.serialize import (
    product_to_dict as product_to_dict,
)
from cccp.calculation.products.structure import (
    GENERATING_FUNCTION as GENERATING_FUNCTION,
)
from cccp.calculation.products.structure import (
    build_structure_trajectory_product as build_structure_trajectory_product,
)
from cccp.calculation.products.vibration import (
    build_vibration_product as build_vibration_product,
)

__all__ = [
    "DISPLACEMENT_NORMALIZATION",
    "DISPLACEMENT_UNITS",
    "GENERATING_FUNCTION",
    "LEGACY_PRODUCT_SCHEMA_VERSION",
    "PRODUCT_PAYLOAD_TYPES",
    "PRODUCT_SCHEMA_VERSION",
    "PRODUCT_SPECS",
    "CoordinateAtom",
    "CoordinateUnits",
    "CurveScatterPayload",
    "CurveSeries",
    "DerivedPayload",
    "EnergyUnits",
    "FrameRecord",
    "FrequencyUnits",
    "MissingReason",
    "PointRecord",
    "ProductIODatum",
    "ProductIdentity",
    "ProductKind",
    "ProductParameter",
    "ProductPayload",
    "ProductSchemaError",
    "ProductSpec",
    "ProductState",
    "ReferenceFrame",
    "ScienceProduct",
    "SeriesRef",
    "SourceRef",
    "StructureTrajectoryPayload",
    "VibrationMode",
    "VibrationPayload",
    "build_curve_scatter_product",
    "build_derived_product",
    "build_structure_trajectory_product",
    "build_vibration_product",
    "compute_input_digest",
    "frame_stable_id",
    "migrate_v0_to_v1",
    "point_stable_id",
    "product_from_dict",
    "product_spec",
    "product_specs",
    "product_to_dict",
    "series_stable_id",
]
