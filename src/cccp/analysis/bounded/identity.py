"""Derived identity for bounded products (plan todo 32, C06-s2).

The identity of a bounded (chunked / subsampled / multi-resolution) product is
**input digest + source identity + algorithm/version + parameters + range +
resolution + schema** and deliberately never contains platform job/account
identity (registry §4.6; HN-02a §4).  It is expressed with the C02 primitives —
a :class:`~cccp.calculation.products.contracts.ProductIdentity` whose
``product_kind`` is the underlying data kind and whose ``params_digest`` is the
canonical digest of the bounded parameters — so there is exactly one identity
stack, not a second one.

``input_digest`` keeps its source-input meaning (the digest of the raw input
the source product was generated from).  Because that digest alone cannot
distinguish two source products generated from the same input by different
algorithms/parameters, identity rule v2 additionally folds
:func:`source_identity_digest` — the digest of the *full* source
``ProductIdentity.to_dict()`` — into the bounded parameters.  Rule v1 (the
pre-v2 contract) is preserved verbatim so historical manifests keep their old
params digest and remain readable; new products are always written with rule
v2 and ``BOUNDED_SCHEMA_VERSION``.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.calculation.contracts import JsonObject, parse_int_strict, parse_str_strict
from cccp.calculation.products.contracts import (
    ProductIdentity,
    compute_input_digest,
)

__all__ = [
    "BOUNDED_IDENTITY_RULE_VERSION",
    "BOUNDED_SCHEMA_VERSION",
    "GENERATOR_CHUNK",
    "GENERATOR_MULTIRESOLUTION",
    "GENERATOR_SUBSAMPLE",
    "GENERATOR_TRAJECTORY_CHUNK",
    "LEGACY_BOUNDED_IDENTITY_RULE_VERSION",
    "LEGACY_BOUNDED_SCHEMA_VERSION",
    "METHOD_CHUNK",
    "METHOD_MULTIRESOLUTION",
    "METHOD_STRIDE",
    "MULTIRESOLUTION_ALGORITHM_VERSION",
    "BoundedSpec",
    "bounded_identity",
    "expected_block_params_digests",
    "multiresolution_block_spec",
    "source_identity_digest",
]

#: On-wire schema version of the bounded *manifest* (the blocks themselves use
#: the C02 ``PRODUCT_SCHEMA_VERSION``).  v2 folds the full source identity into
#: every derived identity (D10); v1 documents remain readable as historical
#: records but are never adopted as current cache/recovery identities.
BOUNDED_SCHEMA_VERSION = 2

#: The pre-D10 bounded manifest/identity rule (read-only compatibility).
LEGACY_BOUNDED_SCHEMA_VERSION = 1

#: Current bounded *identity rule*: ``source_identity_digest`` participates in
#: the canonical params payload.
BOUNDED_IDENTITY_RULE_VERSION = 2

#: The pre-D10 identity rule: the params payload is byte-for-byte the old one.
LEGACY_BOUNDED_IDENTITY_RULE_VERSION = 1

#: Bounded operation names (identity ``algorithm`` field).
METHOD_CHUNK = "chunk"
METHOD_STRIDE = "stride"
METHOD_MULTIRESOLUTION = "multiresolution"

# Version 2 omitted the key-point policy from each level's identity. Those
# manifests remain readable, but their blocks cannot be adopted as current.
MULTIRESOLUTION_ALGORITHM_VERSION = "3"
_LEGACY_MULTIRESOLUTION_ALGORITHM_VERSION = "2"

#: Fully-qualified names of the real bounded generating functions.
GENERATOR_CHUNK = "cccp.analysis.bounded.chunking.chunk_curve_product"
GENERATOR_TRAJECTORY_CHUNK = "cccp.analysis.bounded.chunking.chunk_trajectory_product"
GENERATOR_SUBSAMPLE = "cccp.analysis.bounded.subsampling.subsample_curve_product"
GENERATOR_MULTIRESOLUTION = "cccp.analysis.bounded.multiresolution.build_multiresolution_product"


def source_identity_digest(base_identity: ProductIdentity) -> str:
    """Return the canonical ``sha256:<hex>`` digest of a full source identity.

    The payload is ``base_identity.to_dict()`` — product kind, schema version,
    generator, source ``input_digest``, algorithm, algorithm version and params
    digest — so no identity field can be dropped when a bounded projection is
    keyed.  Callers may pass the result back into :class:`BoundedSpec`; it is
    then verified against the base identity rather than trusted.

    Raises:
        BoundedSchemaError: When ``base_identity`` is not a
            :class:`~cccp.calculation.products.contracts.ProductIdentity`.
    """
    if not isinstance(base_identity, ProductIdentity):
        message = "base_identity must be a ProductIdentity"
        raise BoundedSchemaError(message)
    return compute_input_digest(base_identity.to_dict())


@dataclass(frozen=True, slots=True)
class BoundedSpec:
    """Bounded-operation parameters whose digest is part of the identity.

    ``start``/``end`` are the half-open original native-index range covered by
    the bounded product; ``resolution`` is the primary block size (chunk) or
    stride (subsample); extra operation parameters (e.g. the multi-resolution
    level list) live in ``params``.

    ``source_identity_digest`` is the digest of the full source product
    identity (see :func:`source_identity_digest`).  Under the current identity
    rule it is required to compute ``params_digest`` and is verified equal to
    the base identity in :func:`bounded_identity`; a caller-supplied value
    that disagrees is rejected.  ``identity_rule_version=1`` reproduces the
    legacy payload exactly (for reading historical manifests).
    """

    method: str
    resolution: int
    start: int
    end: int
    algorithm_version: str = "1"
    params: tuple[tuple[str, object], ...] = ()
    source_identity_digest: str = ""
    identity_rule_version: int = BOUNDED_IDENTITY_RULE_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            message = "spec.method must be a non-empty string"
            raise BoundedSchemaError(message)
        for label, value in (
            ("resolution", self.resolution),
            ("start", self.start),
            ("end", self.end),
            ("identity_rule_version", self.identity_rule_version),
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
        if self.identity_rule_version not in (
            LEGACY_BOUNDED_IDENTITY_RULE_VERSION,
            BOUNDED_IDENTITY_RULE_VERSION,
        ):
            message = (
                "spec.identity_rule_version must be "
                f"{LEGACY_BOUNDED_IDENTITY_RULE_VERSION} or "
                f"{BOUNDED_IDENTITY_RULE_VERSION}; got {self.identity_rule_version}"
            )
            raise BoundedSchemaError(message)
        if not isinstance(self.source_identity_digest, str):
            message = "spec.source_identity_digest must be a string"
            raise BoundedSchemaError(message)
        if (
            self.identity_rule_version == LEGACY_BOUNDED_IDENTITY_RULE_VERSION
            and self.source_identity_digest
        ):
            message = (
                "spec.identity_rule_version 1 never carries a source_identity_digest; "
                "use identity_rule_version 2 for the current identity"
            )
            raise BoundedSchemaError(message)
        object.__setattr__(self, "algorithm_version", str(self.algorithm_version))
        object.__setattr__(self, "source_identity_digest", str(self.source_identity_digest).strip())
        object.__setattr__(self, "params", tuple((str(name), value) for name, value in self.params))

    def params_payload(self) -> JsonObject:
        """Canonical JSON payload digested into ``identity.params_digest``."""
        if self.identity_rule_version == LEGACY_BOUNDED_IDENTITY_RULE_VERSION:
            # Byte-for-byte the pre-D10 payload: historical params digests stay
            # reproducible and historical products keep their old identity.
            return {
                "bounded_schema_version": LEGACY_BOUNDED_SCHEMA_VERSION,
                "method": self.method,
                "algorithm_version": self.algorithm_version,
                "resolution": self.resolution,
                "start": self.start,
                "end": self.end,
                "params": {str(name): value for name, value in self.params},
            }
        if not self.source_identity_digest:
            message = (
                "spec.source_identity_digest is required by identity rule "
                f"{BOUNDED_IDENTITY_RULE_VERSION}"
            )
            raise BoundedSchemaError(message)
        return {
            "bounded_schema_version": BOUNDED_SCHEMA_VERSION,
            "method": self.method,
            "algorithm_version": self.algorithm_version,
            "resolution": self.resolution,
            "start": self.start,
            "end": self.end,
            "source_identity_digest": self.source_identity_digest,
            "params": {str(name): value for name, value in self.params},
        }

    def params_digest(self) -> str:
        """``sha256:<hex>`` over the canonical bounded parameters."""
        return compute_input_digest(self.params_payload())

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (params as an ordered name/value list)."""
        payload: JsonObject = {
            "method": self.method,
            "algorithm_version": self.algorithm_version,
            "resolution": self.resolution,
            "start": self.start,
            "end": self.end,
            "params": [{"name": name, "value": value} for name, value in self.params],
            "identity_rule_version": self.identity_rule_version,
        }
        if self.identity_rule_version == BOUNDED_IDENTITY_RULE_VERSION:
            payload["source_identity_digest"] = self.source_identity_digest
        return payload

    @classmethod
    def from_dict(cls, payload: object | None) -> BoundedSpec:
        """Parse strictly; a malformed spec raises :class:`BoundedSchemaError`.

        ``identity_rule_version``/``source_identity_digest`` are additive
        optional fields: a pre-D10 spec (no rule field) parses as rule 1.
        """
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
        rule = parse_int_strict(payload, "identity_rule_version")
        if rule is None:
            rule = LEGACY_BOUNDED_IDENTITY_RULE_VERSION
        digest = parse_str_strict(payload, "source_identity_digest") or ""
        if rule == BOUNDED_IDENTITY_RULE_VERSION and not digest:
            message = (
                "spec.source_identity_digest is required by identity rule "
                f"{BOUNDED_IDENTITY_RULE_VERSION}"
            )
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
            source_identity_digest=digest,
            identity_rule_version=rule,
        )


def bounded_identity(
    base_identity: ProductIdentity,
    spec: BoundedSpec,
    *,
    generator: str,
) -> ProductIdentity:
    """Derive the bounded identity from a base product identity and a spec.

    ``input_digest`` is inherited from the base product (the bounded product is
    a projection of the *same* input), while the full source-identity digest,
    ``algorithm``/``algorithm_version`` and ``params_digest`` (source identity
    + range + resolution + params + schema) make it stable for identical
    input+parameters and distinct across any source or parameter change.
    Platform job/account identity is never consulted.

    A caller-supplied ``spec.source_identity_digest`` must equal the digest
    recomputed from ``base_identity.to_dict()``; a mismatch raises
    :class:`BoundedSchemaError` instead of silently keying on an unverified
    value.
    """
    if not isinstance(base_identity, ProductIdentity):
        message = "base_identity must be a ProductIdentity"
        raise BoundedSchemaError(message)
    if not isinstance(spec, BoundedSpec):
        message = "spec must be a BoundedSpec"
        raise BoundedSchemaError(message)
    effective = spec
    if spec.identity_rule_version == BOUNDED_IDENTITY_RULE_VERSION:
        computed = source_identity_digest(base_identity)
        if spec.source_identity_digest and spec.source_identity_digest != computed:
            message = (
                "spec.source_identity_digest does not match the full base identity "
                f"({spec.source_identity_digest!r} != {computed!r})"
            )
            raise BoundedSchemaError(message)
        effective = replace(spec, source_identity_digest=computed)
    return ProductIdentity(
        product_kind=base_identity.product_kind,
        generator=generator,
        input_digest=base_identity.input_digest,
        algorithm=spec.method,
        algorithm_version=spec.algorithm_version,
        params_digest=effective.params_digest(),
    )


def multiresolution_block_spec(spec: BoundedSpec, *, level_index: int) -> BoundedSpec:
    """Derive one level's identity parameters for both generation and reading.

    Current levels inherit every manifest parameter, including key-point
    selectors and retention policy, so any option that changes point selection
    also changes the block identity. Version 2 reproduces the historical digest
    only for reading; it must not be reused as a current cache identity.
    """
    if spec.method != METHOD_MULTIRESOLUTION:
        raise BoundedSchemaError("multiresolution block requires a multiresolution spec")
    params = dict(spec.params)
    if not isinstance(params.get("series_id"), str) or not params["series_id"]:
        raise BoundedSchemaError("multiresolution spec requires a series_id")
    resolutions = params.get("resolutions")
    if not isinstance(resolutions, (list, tuple)) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in resolutions
    ):
        raise BoundedSchemaError("multiresolution resolutions must be positive integers")
    if isinstance(level_index, bool) or not isinstance(level_index, int):
        raise BoundedSchemaError("multiresolution level_index must be an integer")
    if not 0 <= level_index < len(resolutions):
        raise BoundedSchemaError("multiresolution level_index is outside resolutions")
    if spec.algorithm_version == _LEGACY_MULTIRESOLUTION_ALGORITHM_VERSION:
        params = {
            "series_id": params["series_id"],
            "level_index": level_index,
            "resolutions": list(resolutions),
        }
    elif spec.algorithm_version == MULTIRESOLUTION_ALGORITHM_VERSION:
        if "key_points" not in params or "retain_key_points" not in params:
            raise BoundedSchemaError("multiresolution spec requires its key-point policy")
        params["level_index"] = level_index
    else:
        raise BoundedSchemaError("unsupported multiresolution algorithm version")
    return replace(
        spec,
        resolution=resolutions[level_index],
        params=tuple(sorted(params.items())),
    )


def expected_block_params_digests(spec: BoundedSpec, block: object | None = None) -> list[str]:
    """Recompute the admissible ``params_digest`` values for one block.

    Shared by the generators' read gate (R10/D-recheck): a block spliced from a
    different source (same raw input, different algorithm/version) carries a
    ``params_digest`` derived from the OLD source identity, so verifying the
    block shell alone cannot catch it — the digest itself must be re-derived
    from the manifest's verified source identity and the block's own facts.

    The candidates mirror the real generator block shapes exactly:

    * ``stride``: the single block reuses the manifest spec digest verbatim;
    * ``chunk``: manifest params (minus ``product_kind`` for curve chunks /
      minus ``series_id`` for trajectory chunks) plus ``block_index``;
    * ``multiresolution``: the shared level-spec derivation, including all
      selection-policy parameters under the current algorithm version.

    An empty list means "no known derivation can produce this block" — the
    caller fails closed.
    """
    if not isinstance(spec, BoundedSpec):
        raise BoundedSchemaError("spec must be a BoundedSpec")
    if spec.identity_rule_version != BOUNDED_IDENTITY_RULE_VERSION:
        return []
    base = {str(name): value for name, value in spec.params}
    method = spec.method
    digests: list[str] = []
    if method == METHOD_STRIDE:
        digests.append(spec.params_digest())
        return digests
    block_index = int(getattr(block, "block_index", -1))
    block_start = int(getattr(block, "start", -1))
    block_end = int(getattr(block, "end", -1))
    block_resolution = int(getattr(block, "resolution", -1))
    if block_index < 0 or block_start < 0 or block_end < block_start or block_resolution < 1:
        return []
    if method == METHOD_CHUNK:
        for drop in ("product_kind", "series_id"):
            params = {name: value for name, value in base.items() if name != drop}
            params["block_index"] = block_index
            digests.append(
                replace(
                    spec,
                    resolution=block_resolution,
                    start=block_start,
                    end=block_end,
                    params=tuple(sorted(params.items())),
                ).params_digest()
            )
        return digests
    if method == METHOD_MULTIRESOLUTION:
        if block_start != spec.start or block_end != spec.end:
            return []
        level_spec = multiresolution_block_spec(spec, level_index=block_index)
        return [level_spec.params_digest()] if level_spec.resolution == block_resolution else []
    return []
