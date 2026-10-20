"""CCCP bounded science-product layer (chunked / subsampled / multi-resolution).

Public surface re-export only — implementation lives in the sibling modules
(:mod:`~cccp.analysis.bounded.identity`,
:mod:`~cccp.analysis.bounded.manifest`,
:mod:`~cccp.analysis.bounded.chunking`,
:mod:`~cccp.analysis.bounded.subsampling`,
:mod:`~cccp.analysis.bounded.multiresolution`).  Bounded products *reuse* the
C02 versioned science-product skeleton (a real
:class:`~cccp.calculation.products.contracts.ScienceProduct` per block, the
:class:`~cccp.calculation.products.contracts.ProductIdentity` identity and the
single reader/writer); importing this package pulls in no task-execution module
and never imports ``acp``.

Author: QCcalc Team
"""

from __future__ import annotations

from cccp.analysis.bounded.chunking import (
    chunk_curve_product as chunk_curve_product,
)
from cccp.analysis.bounded.chunking import (
    chunk_trajectory_product as chunk_trajectory_product,
)
from cccp.analysis.bounded.errors import BoundedSchemaError as BoundedSchemaError
from cccp.analysis.bounded.identity import (
    BOUNDED_IDENTITY_RULE_VERSION as BOUNDED_IDENTITY_RULE_VERSION,
)
from cccp.analysis.bounded.identity import (
    BOUNDED_SCHEMA_VERSION as BOUNDED_SCHEMA_VERSION,
)
from cccp.analysis.bounded.identity import (
    LEGACY_BOUNDED_IDENTITY_RULE_VERSION as LEGACY_BOUNDED_IDENTITY_RULE_VERSION,
)
from cccp.analysis.bounded.identity import (
    LEGACY_BOUNDED_SCHEMA_VERSION as LEGACY_BOUNDED_SCHEMA_VERSION,
)
from cccp.analysis.bounded.identity import (
    GENERATOR_CHUNK as GENERATOR_CHUNK,
)
from cccp.analysis.bounded.identity import (
    GENERATOR_MULTIRESOLUTION as GENERATOR_MULTIRESOLUTION,
)
from cccp.analysis.bounded.identity import (
    GENERATOR_SUBSAMPLE as GENERATOR_SUBSAMPLE,
)
from cccp.analysis.bounded.identity import (
    GENERATOR_TRAJECTORY_CHUNK as GENERATOR_TRAJECTORY_CHUNK,
)
from cccp.analysis.bounded.identity import (
    METHOD_CHUNK as METHOD_CHUNK,
)
from cccp.analysis.bounded.identity import (
    METHOD_MULTIRESOLUTION as METHOD_MULTIRESOLUTION,
)
from cccp.analysis.bounded.identity import (
    METHOD_STRIDE as METHOD_STRIDE,
)
from cccp.analysis.bounded.identity import (
    BoundedSpec as BoundedSpec,
)
from cccp.analysis.bounded.identity import (
    bounded_identity as bounded_identity,
)
from cccp.analysis.bounded.identity import (
    source_identity_digest as source_identity_digest,
)
from cccp.analysis.bounded.manifest import (
    DISPLAY_SAMPLING_NOTE as DISPLAY_SAMPLING_NOTE,
)
from cccp.analysis.bounded.manifest import (
    BoundedBlock as BoundedBlock,
)
from cccp.analysis.bounded.manifest import (
    BoundedProduct as BoundedProduct,
)
from cccp.analysis.bounded.manifest import (
    KeyPointRetention as KeyPointRetention,
)
from cccp.analysis.bounded.manifest import (
    native_index_of as native_index_of,
)
from cccp.analysis.bounded.manifest import (
    reconstruct_curve_series as reconstruct_curve_series,
)
from cccp.analysis.bounded.manifest import (
    reconstruct_structure_frames as reconstruct_structure_frames,
)
from cccp.analysis.bounded.manifest import (
    selected_point_mapping as selected_point_mapping,
)
from cccp.analysis.bounded.multiresolution import (
    build_multiresolution_product as build_multiresolution_product,
)
from cccp.analysis.bounded.subsampling import (
    DEFAULT_KEY_POINTS as DEFAULT_KEY_POINTS,
)
from cccp.analysis.bounded.subsampling import (
    KEY_ENDPOINTS as KEY_ENDPOINTS,
)
from cccp.analysis.bounded.subsampling import (
    KEY_GLOBAL_MAX as KEY_GLOBAL_MAX,
)
from cccp.analysis.bounded.subsampling import (
    KEY_GLOBAL_MIN as KEY_GLOBAL_MIN,
)
from cccp.analysis.bounded.subsampling import (
    KEY_LOCAL_MIN as KEY_LOCAL_MIN,
)
from cccp.analysis.bounded.subsampling import (
    find_key_point_indices as find_key_point_indices,
)
from cccp.analysis.bounded.subsampling import (
    subsample_curve_product as subsample_curve_product,
)

__all__ = [
    "BOUNDED_IDENTITY_RULE_VERSION",
    "BOUNDED_SCHEMA_VERSION",
    "LEGACY_BOUNDED_IDENTITY_RULE_VERSION",
    "LEGACY_BOUNDED_SCHEMA_VERSION",
    "DEFAULT_KEY_POINTS",
    "DISPLAY_SAMPLING_NOTE",
    "GENERATOR_CHUNK",
    "GENERATOR_MULTIRESOLUTION",
    "GENERATOR_SUBSAMPLE",
    "GENERATOR_TRAJECTORY_CHUNK",
    "KEY_ENDPOINTS",
    "KEY_GLOBAL_MAX",
    "KEY_GLOBAL_MIN",
    "KEY_LOCAL_MIN",
    "METHOD_CHUNK",
    "METHOD_MULTIRESOLUTION",
    "METHOD_STRIDE",
    "BoundedBlock",
    "BoundedProduct",
    "BoundedSchemaError",
    "BoundedSpec",
    "KeyPointRetention",
    "bounded_identity",
    "source_identity_digest",
    "build_multiresolution_product",
    "chunk_curve_product",
    "chunk_trajectory_product",
    "find_key_point_indices",
    "native_index_of",
    "reconstruct_curve_series",
    "reconstruct_structure_frames",
    "selected_point_mapping",
    "subsample_curve_product",
]
