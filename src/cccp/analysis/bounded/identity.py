"""Derived identity for bounded products (plan todo 32, C06-s2).

The identity of a bounded (chunked / subsampled / multi-resolution) product is
**input digest + algorithm/version + parameters + range + resolution + schema**
and deliberately never contains platform job/account identity (registry §4.6;
HN-02a §4).  It is expressed with the C02 primitives — a
:class:`~cccp.calculation.products.contracts.ProductIdentity` whose
``product_kind`` is the underlying data kind and whose ``params_digest`` is the
canonical digest of the bounded parameters — so there is exactly one identity
stack, not a second one.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.calculation.contracts import JsonObject, parse_int_strict, parse_str_strict
from cccp.calculation.products.contracts import (
    ProductIdentity,
    compute_input_digest,
)

__all__ = [
    "BOUNDED_SCHEMA_VERSION",
    "GENERATOR_CHUNK",
    "GENERATOR_MULTIRESOLUTION",
    "GENERATOR_SUBSAMPLE",
    "GENERATOR_TRAJECTORY_CHUNK",
    "METHOD_CHUNK",
    "METHOD_MULTIRESOLUTION",
    "METHOD_STRIDE",
    "BoundedSpec",
    "bounded_identity",
]

#: On-wire schema version of the bounded *manifest* (the blocks themselves use
#: the C02 ``PRODUCT_SCHEMA_VERSION``).  Part of the derived identity payload so
#: a bounded-contract change cannot silently reuse an old identity.
BOUNDED_SCHEMA_VERSION = 1

#: Bounded operation names (identity ``algorithm`` field).
METHOD_CHUNK = "chunk"
METHOD_STRIDE = "stride"
METHOD_MULTIRESOLUTION = "multiresolution"

#: Fully-qualified names of the real bounded generating functions.
GENERATOR_CHUNK = "cccp.analysis.bounded.chunking.chunk_curve_product"
GENERATOR_TRAJECTORY_CHUNK = "cccp.analysis.bounded.chunking.chunk_trajectory_product"
GENERATOR_SUBSAMPLE = "cccp.analysis.bounded.subsampling.subsample_curve_product"
GENERATOR_MULTIRESOLUTION = "cccp.analysis.bounded.multiresolution.build_multiresolution_product"


@dataclass(frozen=True, slots=True)
class BoundedSpec:
    """Bounded-operation parameters whose digest is part of the identity.

    ``start``/``end`` are the half-open original native-index range covered by
    the bounded product; ``resolution`` is the primary block size (chunk) or
    stride (subsample); extra operation parameters (e.g. the multi-resolution
    level list) live in ``params``.
    """

    method: str
    resolution: int
    start: int
    end: int
    algorithm_version: str = "1"
    params: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            message = "spec.method must be a non-empty string"
            raise BoundedSchemaError(message)
        for label, value in (
            ("resolution", self.resolution),
            ("start", self.start),
            ("end", self.end),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                message = f"spec.{label} must be an int"
                raise BoundedSchemaError(message)
        if self.resolution < 1:
            message = "spec.resolution must be >= 1"
            raise BoundedSchemaError(message)
        if self.start < 0 or self.end < self.start:
            message = "spec requires 0 <= start <= end"
            raise BoundedSchemaError(message)
        object.__setattr__(self, "algorithm_version", str(self.algorithm_version))
        object.__setattr__(self, "params", tuple((str(name), value) for name, value in self.params))

    def params_payload(self) -> JsonObject:
        """Canonical JSON payload digested into ``identity.params_digest``."""
        return {
            "bounded_schema_version": BOUNDED_SCHEMA_VERSION,
            "method": self.method,
            "algorithm_version": self.algorithm_version,
            "resolution": self.resolution,
            "start": self.start,
            "end": self.end,
            "params": {str(name): value for name, value in self.params},
        }

    def params_digest(self) -> str:
        """``sha256:<hex>`` over the canonical bounded parameters."""
        return compute_input_digest(self.params_payload())

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (params as an ordered name/value list)."""
        return {
            "method": self.method,
            "algorithm_version": self.algorithm_version,
            "resolution": self.resolution,
            "start": self.start,
            "end": self.end,
            "params": [{"name": name, "value": value} for name, value in self.params],
        }

    @classmethod
    def from_dict(cls, payload: object | None) -> BoundedSpec:
        """Parse strictly; a malformed spec raises :class:`BoundedSchemaError`."""
        if not isinstance(payload, Mapping):
            message = "spec must be a mapping"
            raise BoundedSchemaError(message)
        method = parse_str_strict(payload, "method")
        if method is None:
            message = "spec.method is required"
            raise BoundedSchemaError(message)
        resolution = parse_int_strict(payload, "resolution")
        start = parse_int_strict(payload, "start")
        end = parse_int_strict(payload, "end")
        if resolution is None or start is None or end is None:
            message = "spec requires integer resolution/start/end"
            raise BoundedSchemaError(message)
        raw_params = payload.get("params")
        params: list[tuple[str, object]] = []
        if raw_params is not None:
            if not isinstance(raw_params, list):
                message = "spec.params must be a list"
                raise BoundedSchemaError(message)
            for index, entry in enumerate(raw_params):
                if not isinstance(entry, Mapping):
                    message = f"spec.params[{index}] must be a mapping"
                    raise BoundedSchemaError(message)
                name = parse_str_strict(entry, "name")
                if name is None:
                    message = f"spec.params[{index}].name is required"
                    raise BoundedSchemaError(message)
                params.append((name, entry.get("value")))
        return cls(
            method=method,
            resolution=resolution,
            start=start,
            end=end,
            algorithm_version=parse_str_strict(payload, "algorithm_version") or "1",
            params=tuple(params),
        )


def bounded_identity(
    base_identity: ProductIdentity,
    spec: BoundedSpec,
    *,
    generator: str,
) -> ProductIdentity:
    """Derive the bounded identity from a base product identity and a spec.

    ``input_digest`` is inherited from the base product (the bounded product is
    a projection of the *same* input), while ``algorithm``/``algorithm_version``
    and ``params_digest`` (range + resolution + params + schema) make it stable
    for identical input+parameters and distinct across any parameter change.
    Platform job/account identity is never consulted.
    """
    if not isinstance(base_identity, ProductIdentity):
        message = "base_identity must be a ProductIdentity"
        raise BoundedSchemaError(message)
    if not isinstance(spec, BoundedSpec):
        message = "spec must be a BoundedSpec"
        raise BoundedSchemaError(message)
    return ProductIdentity(
        product_kind=base_identity.product_kind,
        generator=generator,
        input_digest=base_identity.input_digest,
        algorithm=spec.method,
        algorithm_version=spec.algorithm_version,
        params_digest=spec.params_digest(),
    )
