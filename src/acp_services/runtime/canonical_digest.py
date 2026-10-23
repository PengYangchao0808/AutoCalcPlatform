"""The single canonical-digest implementation for ACP 2.0 (B16).

B03 froze the rules and the byte-for-byte vectors
(``runtime/application-api.v1.json#canonical_digest`` and
``runtime/canonical-digest-vectors.v1.json``); this module is the one runtime
implementation.  **B18 imports this helper and never re-implements it.**

Rules implemented here (all from the frozen contract):

* the root value is an object; anything else is ``root_not_object``;
* ``drop_fields`` removes top-level keys *before* any content check, so volatile
  approval state may carry a null or a float and still be excluded;
* JSON ``null`` anywhere is ``null_in_digest_input``; JSON floats anywhere are
  ``non_canonical_number`` (never silently rounded);
* an object whose keys are exactly ``{value, unit}`` is a quantity: it is
  converted to the dimension base unit with ``Decimal`` precision 28 /
  ``ROUND_HALF_EVEN`` and emitted as ``{unit, value}`` with a canonical decimal
  string (no exponent, no leading zeros, trailing zeros removed);
* a quantity value is an integer or a plain-decimal string (no exponent, no
  leading zeros; trailing zeros allowed on input);
* object keys match ``^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)*$`` (``non_canonical_key``);
* strings are normalized to Unicode NFC before serialization;
* arrays are order-significant unless the parent key is listed in
  ``set_fields``, where elements are sorted by their canonical JSON UTF-8 bytes;
* serialization is UTF-8 without BOM, ``sort_keys=True``,
  ``separators=(',', ':')``, ``ensure_ascii=False``, no trailing newline;
* ``digest = 'sha256:' + hex(sha256(canonical UTF-8 bytes))``.

The helper version is recorded in :data:`CANONICAL_DIGEST_VERSION` and in the
returned :class:`CanonicalProjection.meta`.  It is also surfaced by the
approval API so an approver can see which canonicalization produced the digest.

Author: ACP Services (B lane, B16).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

__all__ = [
    "CANONICAL_DIGEST_IMPLEMENTATION",
    "CANONICAL_DIGEST_SPEC_ID",
    "CANONICAL_DIGEST_VERSION",
    "CanonicalDigestError",
    "CanonicalProjection",
    "PLAN_DEFINITION_FIELDS",
    "PLAN_IGNORED_FIELDS",
    "PROPOSAL_IGNORED_FIELDS",
    "PROPOSAL_PROJECTION_FIELDS",
    "canonical_bytes",
    "canonical_digest",
    "canonical_json",
    "frozen_plan_definition",
    "plan_digest",
    "proposal_digest",
]

#: Frozen spec id from B03.
CANONICAL_DIGEST_SPEC_ID = "acp2.canonical-digest.v1"

#: This implementation's version.  Changing canonical bytes MUST bump it and
#: update the frozen vectors; an approval records the version it was created
#: with so a future canonicalization change cannot silently re-interpret a
#: previously approved digest.
CANONICAL_DIGEST_VERSION = "1.0.0"

CANONICAL_DIGEST_IMPLEMENTATION = "src/acp_services/runtime/canonical_digest.py"

_DIGEST_PREFIX = "sha256:"
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
# Input may carry trailing zeros (they are normalized away); exponent and
# leading zeros are not accepted.
_INPUT_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_OUTPUT_DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_^\-]*$")
_DECIMAL_PRECISION = 28

#: dimension -> (base symbol, {symbol: multiplier-to-base})
_UNIT_TABLE: dict[str, tuple[str, dict[str, str]]] = {
    "energy": ("hartree", {"hartree": "1", "kcal_per_mol": "0.0015936010974213599"}),
    "length": ("angstrom", {"angstrom": "1", "bohr": "0.529177210903"}),
    "angle": ("degree", {"degree": "1", "radian": "57.29577951308232087679815481"}),
    "temperature": ("kelvin", {"kelvin": "1"}),
    "time": ("second", {"second": "1", "minute": "60", "hour": "3600"}),
    "frequency": ("cm^-1", {"cm^-1": "1"}),
    "mass": ("dalton", {"dalton": "1"}),
    "charge": ("elementary_charge", {"elementary_charge": "1"}),
    "count": (
        "count",
        {
            "count": "1",
            "core": "1",
            "byte": "1",
            "kilobyte": "1000",
            "megabyte": "1000000",
            "gigabyte": "1000000000",
        },
    ),
    "pressure": ("pascal", {"pascal": "1", "bar": "100000"}),
}

_UNIT_TO_DIMENSION: dict[str, str] = {
    symbol: dimension for dimension, (_base, symbols) in _UNIT_TABLE.items() for symbol in symbols
}


class CanonicalDigestError(ValueError):
    """A digest input violates the frozen canonical rules.

    ``rule`` is one of the frozen rule names
    (``root_not_object``/``null_in_digest_input``/``non_canonical_number``/
    ``non_canonical_key``/``unknown_unit``/``non_canonical_decimal_output``) or
    a projection rule added by this module
    (``unknown_science_field``/``missing_science_field``/
    ``non_canonical_digest``).
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.detail = message

    def params(self) -> dict[str, Any]:
        return {"rule": self.rule}


@dataclass(frozen=True)
class CanonicalProjection:
    """A computed digest plus the exact canonical bytes and helper identity."""

    digest: str
    canonical_json: str
    bytes_sha256: str
    spec_id: str = CANONICAL_DIGEST_SPEC_ID
    version: str = CANONICAL_DIGEST_VERSION
    implementation: str = CANONICAL_DIGEST_IMPLEMENTATION

    def meta(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "implementation": self.implementation,
        }


# ── primitive checks ────────────────────────────────────────────────────────


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _reject_null(value: Any, path: str) -> None:
    if value is None:
        raise CanonicalDigestError("null_in_digest_input", f"null at {path}")


def _decimal_string(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise CanonicalDigestError("non_canonical_number", f"boolean quantity at {path}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise CanonicalDigestError(
            "non_canonical_number", f"JSON float at {path}; use a decimal string"
        )
    if not isinstance(value, str):
        raise CanonicalDigestError("non_canonical_number", f"quantity value at {path}")
    text = _nfc(value)
    if not _INPUT_DECIMAL_RE.match(text):
        raise CanonicalDigestError(
            "non_canonical_number",
            f"quantity value {text!r} at {path} is not a plain decimal",
        )
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already constrains it
        raise CanonicalDigestError("non_canonical_number", f"{text!r} at {path}") from exc


def _normalize_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if not _OUTPUT_DECIMAL_RE.match(text):
        raise CanonicalDigestError(
            "non_canonical_decimal_output", f"normalized decimal {text!r} is not canonical"
        )
    return text


def _quantity(key: str, raw: Mapping[str, Any]) -> dict[str, str]:
    unit = raw.get("unit")
    if not isinstance(unit, str) or not _UNIT_RE.match(_nfc(unit)):
        raise CanonicalDigestError("unknown_unit", f"quantity {key!r} has unit {unit!r}")
    unit = _nfc(unit)
    dimension = _UNIT_TO_DIMENSION.get(unit)
    if dimension is None:
        raise CanonicalDigestError("unknown_unit", f"quantity {key!r} unit {unit!r}")
    base, symbols = _UNIT_TABLE[dimension]
    value = _decimal_string(raw.get("value"), f"{key}.value")
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        converted = value * Decimal(symbols[unit])
    return {"unit": base, "value": _normalize_decimal(converted)}


def _check_key(key: Any, parent: str) -> str:
    if not isinstance(key, str):
        raise CanonicalDigestError("non_canonical_key", f"non-string key under {parent!r}")
    text = _nfc(key)
    if not _KEY_RE.match(text):
        raise CanonicalDigestError(
            "non_canonical_key", f"key {text!r} under {parent!r} is not canonical"
        )
    return text


def _canonicalize(value: Any, *, path: str, parent_key: str, set_fields: tuple[str, ...]) -> Any:
    _reject_null(value, path)
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise CanonicalDigestError(
            "non_canonical_number", f"JSON float at {path}; use a decimal string"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, Mapping):
        raw_keys = set(value.keys())
        if raw_keys == {"value", "unit"}:
            return _quantity(path, value)
        result: dict[str, Any] = {}
        for raw_key in value:
            key = _check_key(raw_key, path)
            result[key] = _canonicalize(
                value[raw_key],
                path=f"{path}.{key}" if path else key,
                parent_key=key,
                set_fields=set_fields,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = [
            _canonicalize(
                item,
                path=f"{path}[{index}]",
                parent_key=parent_key,
                set_fields=set_fields,
            )
            for index, item in enumerate(value)
        ]
        if parent_key in set_fields:
            items.sort(key=lambda item: _serialize(item).encode("utf-8"))
        return items
    raise CanonicalDigestError(
        "non_canonical_number", f"unsupported value type {type(value).__name__} at {path}"
    )


def _serialize(value: Any) -> str:
    """Serialize an already-canonicalized value (used for set ordering)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _projection(
    value: Any,
    *,
    set_fields: Sequence[str] = (),
    drop_fields: Sequence[str] = (),
) -> CanonicalProjection:
    if not isinstance(value, Mapping):
        raise CanonicalDigestError("root_not_object", f"root is {type(value).__name__}")
    dropped = {str(name) for name in drop_fields}
    root = {key: item for key, item in value.items() if key not in dropped}
    canonical = _canonicalize(
        root,
        path="",
        parent_key="",
        set_fields=tuple(set_fields),
    )
    document = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    raw = document.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return CanonicalProjection(
        digest=_DIGEST_PREFIX + digest,
        canonical_json=document,
        bytes_sha256=digest,
    )


def canonical_bytes(
    value: Mapping[str, Any],
    *,
    set_fields: Sequence[str] = (),
    drop_fields: Sequence[str] = (),
) -> bytes:
    """Canonical UTF-8 bytes (no BOM, no trailing newline)."""
    return _projection(value, set_fields=set_fields, drop_fields=drop_fields).canonical_json.encode(
        "utf-8"
    )


def canonical_json(
    value: Any,
    *,
    set_fields: Sequence[str] = (),
    drop_fields: Sequence[str] = (),
) -> str:
    """Canonical JSON text for *value* under the frozen rules."""
    return _projection(value, set_fields=set_fields, drop_fields=drop_fields).canonical_json


def canonical_digest(
    value: Mapping[str, Any],
    *,
    set_fields: Sequence[str] = (),
    drop_fields: Sequence[str] = (),
) -> str:
    """Return ``sha256:<hex>`` over the canonical UTF-8 bytes of *value*."""
    return _projection(value, set_fields=set_fields, drop_fields=drop_fields).digest


# ── Proposal / ResearchRecord projection ────────────────────────────────────

#: Fields that are the approved science of a Proposal/ResearchRecord.
PROPOSAL_PROJECTION_FIELDS: tuple[str, ...] = (
    "proposal_id",
    "project_id",
    "title",
    "question",
    "capability_ids",
    "recipe_ref",
    "plan_digest",
    "budget_request",
)

#: Fields that are deliberately excluded from the projection.  Appending task
#: refs, evidence refs, approval state or a display name never changes the
#: approved science; dropping them is the whole point of the semantic
#: projection (B03 v4).
PROPOSAL_IGNORED_FIELDS: tuple[str, ...] = (
    "approval_ref",
    "status",
    "updated_at",
    "revision",
    "etag",
    "task_refs",
    "evidence_refs",
    "tasks",
    "evidence",
    "display_name",
    "created_at",
    "schema_id",
    "schema_version",
)

_PROPOSAL_REQUIRED: tuple[str, ...] = ("proposal_id", "project_id", "plan_digest")

#: Fields of the frozen scientific-plan definition (R3-06).  ``plan_version``
#: is the definition version; execution-time identity (upstream byte digests,
#: materialized parameters, worker/implementation version) is frozen separately
#: per step and is deliberately not part of this digest.
PLAN_DEFINITION_FIELDS: tuple[str, ...] = (
    "workflow",
    "input",
    "method",
    "resources",
    "plan_version",
    "recipe_ref",
    "parameters",
    "dependency_selectors",
    "allowed_bounded_alternatives",
)

#: Presentation/transport fields that never change science and are excluded
#: from a plan digest.  A display-name change must not invalidate an approval.
PLAN_IGNORED_FIELDS: tuple[str, ...] = (
    "display_name",
    "name",
    "task_name",
    "remark",
    "molecule_name",
    "project_id",
    "tags",
    "node_tags",
    "output_dir",
    "config_path",
    "idempotency_key",
    "extensions",
    "execution_mode",
    "target_node",
    "budget",
    "proposal_ref",
    "proposal_digest",
    "plan_digest",
    "schema_version",
)


def _filter_science(
    value: Any,
    *,
    fields: Sequence[str],
    ignored: Sequence[str],
    required: Sequence[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalDigestError(
            "missing_science_field", f"{label} must be an object, got {type(value).__name__}"
        )
    allowed = set(fields)
    ignored_set = set(ignored)
    unknown = sorted(str(key) for key in value if key not in allowed and key not in ignored_set)
    if unknown:
        raise CanonicalDigestError(
            "unknown_science_field",
            f"{label} carries unknown science field(s): {', '.join(unknown)}",
        )
    missing = [name for name in required if name not in value]
    if missing:
        raise CanonicalDigestError(
            "missing_science_field", f"{label} is missing required field(s): {', '.join(missing)}"
        )
    return {name: value[name] for name in fields if name in value}


def proposal_digest(proposal: Mapping[str, Any]) -> CanonicalProjection:
    """Canonical semantic projection of a Proposal/ResearchRecord (v4).

    Excludes approval state, revision/etag/updated_at, display name, task refs
    and evidence refs.  An unknown field that could carry science is rejected
    rather than silently ignored.
    """
    filtered = _filter_science(
        proposal,
        fields=PROPOSAL_PROJECTION_FIELDS,
        ignored=PROPOSAL_IGNORED_FIELDS,
        required=_PROPOSAL_REQUIRED,
        label="proposal",
    )
    return _projection(filtered, set_fields=("capability_ids",))


def frozen_plan_definition(
    *,
    workflow: str,
    input: Mapping[str, Any] | None = None,
    method: Mapping[str, Any] | None = None,
    resources: Mapping[str, Any] | None = None,
    plan_version: int = 1,
    recipe_ref: Mapping[str, Any] | None = None,
    parameters: Mapping[str, Any] | None = None,
    dependency_selectors: Sequence[Any] | None = None,
    allowed_bounded_alternatives: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Build the submit-side scientific plan definition (R3-06 layer 1)."""
    definition: dict[str, Any] = {
        "workflow": workflow,
        "input": dict(input or {}),
        "method": dict(method or {}),
        "resources": dict(resources or {}),
        "plan_version": int(plan_version),
    }
    if recipe_ref:
        definition["recipe_ref"] = dict(recipe_ref)
    if parameters:
        definition["parameters"] = dict(parameters)
    if dependency_selectors:
        definition["dependency_selectors"] = list(dependency_selectors)
    if allowed_bounded_alternatives:
        definition["allowed_bounded_alternatives"] = list(allowed_bounded_alternatives)
    return definition


def plan_digest(plan: Mapping[str, Any]) -> CanonicalProjection:
    """Canonical digest of a frozen scientific plan definition (R3-06 layer 1).

    This is the value submit authorizes against.  It is **not** the proposal
    digest, the contract digest or an artifact digest.
    """
    filtered = _filter_science(
        plan,
        fields=PLAN_DEFINITION_FIELDS,
        ignored=PLAN_IGNORED_FIELDS,
        required=("workflow",),
        label="plan",
    )
    return _projection(filtered)
