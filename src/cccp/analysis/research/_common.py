"""Stdlib-only helpers shared by the ACP2 research science types.

This module carries no scientific model of its own: it only normalises
mappings, validates required/forbidden keys and produces the frozen canonical
JSON encoding.  It deliberately imports nothing outside the standard library so
that ``cccp.analysis.research`` stays independent of task execution and QC
interfaces.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

__all__ = [
    "FORBIDDEN_PLATFORM_FIELDS",
    "ForbiddenPlatformFieldError",
    "ScienceContractError",
    "SchemaViolationError",
    "canonical_json",
    "is_formula",
    "read_schema_version",
    "reject_forbidden_keys",
    "reject_unknown_keys",
    "require_int",
    "require_keys",
    "require_mapping",
    "require_number",
    "require_sequence",
    "require_string",
]

#: Names that would turn a science document into a platform/model envelope.
#: Mirrors ``propertyNames.not.enum`` in ``reaction.schema.json`` and
#: ``question.schema.json`` (semantic rule R-08).
FORBIDDEN_PLATFORM_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "role",
        "model_role",
        "cost",
        "price",
        "job_status",
        "queue",
        "account",
    }
)

_FORMULA_PATTERN: Final = re.compile(r"^([A-Z][a-z]?[0-9]*)+$")


class ScienceContractError(ValueError):
    """Base class for a rejected ACP2 science contract payload."""


class SchemaViolationError(ScienceContractError):
    """A payload does not have the structure required by its contract."""


class ForbiddenPlatformFieldError(ScienceContractError):
    """A payload carries a platform/model field that science types forbid."""


def canonical_json(payload: Any) -> str:
    """Return the frozen canonical encoding of *payload*.

    Canonical form is UTF-8-safe JSON with sorted object keys, two-space
    indentation and exactly one trailing newline, matching the frozen contract
    examples under ``docs/reference/next-change/acp2-contracts/science``.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def require_mapping(value: Any, where: str) -> dict[str, Any]:
    """Return *value* as a plain ``dict`` or raise :class:`SchemaViolationError`."""
    if not isinstance(value, Mapping):
        raise SchemaViolationError(f"{where}: expected a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def require_sequence(value: Any, where: str) -> list[Any]:
    """Return *value* as a list (strings and mappings are rejected)."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SchemaViolationError(f"{where}: expected a JSON array, got {type(value).__name__}")
    return list(value)


def require_string(value: Any, where: str, *, allow_empty: bool = False) -> str:
    """Return a non-empty string unless ``allow_empty`` is set."""
    if not isinstance(value, str):
        raise SchemaViolationError(f"{where}: expected a string, got {type(value).__name__}")
    if not allow_empty and not value:
        raise SchemaViolationError(f"{where}: string must not be empty")
    return value


def require_int(value: Any, where: str, *, minimum: int | None = None) -> int:
    """Return an ``int`` (``bool`` excluded) respecting an optional minimum."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaViolationError(f"{where}: expected an integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise SchemaViolationError(f"{where}: {value} is below the minimum {minimum}")
    return value


def require_number(value: Any, where: str, *, minimum: float | None = None) -> int | float:
    """Return an ``int``/``float`` (``bool`` excluded) respecting a bound."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaViolationError(f"{where}: expected a number, got {type(value).__name__}")
    if minimum is not None and value <= minimum:
        raise SchemaViolationError(f"{where}: {value} must be greater than {minimum}")
    return value


def require_keys(mapping: Mapping[str, Any], required: Iterable[str], where: str) -> None:
    """Raise when any required key is missing."""
    missing = sorted(set(required) - set(mapping))
    if missing:
        raise SchemaViolationError(f"{where}: missing required field(s) {missing}")


def reject_unknown_keys(mapping: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    """Raise when a canonical payload carries an undeclared field."""
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise SchemaViolationError(
            f"{where}: unknown field(s) {unknown}; canonical v1 payloads are closed"
        )


def reject_forbidden_keys(value: Any, where: str = "document") -> None:
    """Raise when any nested object key is a forbidden platform/model field."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_PLATFORM_FIELDS:
                raise ForbiddenPlatformFieldError(
                    f"{where}: forbidden platform/model field {key!r}"
                )
            reject_forbidden_keys(item, f"{where}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            reject_forbidden_keys(item, f"{where}[{index}]")


def read_schema_version(mapping: Mapping[str, Any], where: str) -> int:
    """Return the declared major version, requiring a non-bool integer."""
    if "schema_version" not in mapping:
        raise SchemaViolationError(f"{where}: missing required field 'schema_version'")
    version = mapping["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise SchemaViolationError(
            f"{where}: schema_version must be an integer, got {type(version).__name__}"
        )
    return version


def is_formula(value: str) -> bool:
    """Whether *value* matches the Hill-order elemental formula grammar."""
    return bool(_FORMULA_PATTERN.fullmatch(value))
