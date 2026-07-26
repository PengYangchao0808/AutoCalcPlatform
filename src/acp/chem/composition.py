"""
Molecular Composition & Hessian Default Utilities
=================================================

Element classification and ``Recalc_Hess`` policy resolution for ORCA
geometry optimization. Centralises the graded-default heuristic
introduced in the ORCA Hessian Defaults Plan (v1.3) so that CLI, API,
catalog, scheduler, workflows, and the legacy ORCA interface all share
one source of truth.

Author: QCcalc Team
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Element classification -------------------------------------------------
# Light organic elements + common halogens. Molecules containing only
# these are treated as "easy" PES and never compute an exact Hessian.
LIGHT_ELEMENTS: frozenset[str] = frozenset({"C", "H", "O", "N", "F", "Cl", "Br", "I"})

# Common heteroatoms with typically benign PES. Retained for provenance /
# diagnostics (HessianResolution.triggering_elements), even though the
# default interval no longer distinguishes them from other heavy elements.
HETEROATOM_ELEMENTS: frozenset[str] = frozenset({"P", "S", "Si", "B"})

# --- Default interval (two-tier) -------------------------------------------
# Default Recalc_Hess interval for any molecule containing at least one
# non-light element (P/S/Si/B, metals, or anything else). Matches the
# historical fixed default of 10, so non-light molecules see no behaviour
# change versus the pre-auto implementation.
NON_LIGHT_DEFAULT_INTERVAL = 10
# Safety cap (~ effectively never). Also used as the inclusive upper
# bound for user-supplied explicit intervals.
MAX_RECALC_HESS_INTERVAL = 1000

# Sentinel string value indicating "infer from elements".
AUTO_RECALC_HESS = "auto"

_HESSIAN_PREVIEW_REASON_LIGHT = "light_elements"
_HESSIAN_PREVIEW_REASON_HETERO = "heteroatom_only"
_HESSIAN_PREVIEW_REASON_HEAVY = "heavy_elements"


def normalize_recalc_hess(value: object) -> int | str | None:
    """Normalise a raw ``recalc_hess`` value into a canonical form.

    Accepts:

    * ``None`` / empty string → ``None`` (means "not specified, follow config")
    * ``"auto"`` (case-insensitive) → ``"auto"``
    * ``0`` or ``"0"`` → ``0`` (never compute exact Hessian; approximate + BFGS)
    * positive integer ``N`` or numeric string ``"N"`` (1–1000) → ``int(N)``

    Rejects booleans (avoid ``True`` being treated as ``1``), floats
    (avoid silent truncation), negative values, values greater than
    ``MAX_RECALC_HESS_INTERVAL``, and any other type/string.

    Args:
        value: Raw input from CLI/API/catalog/config.

    Returns:
        Canonical ``int``, the string ``"auto"``, or ``None``.

    Raises:
        ValueError: If *value* cannot be interpreted as a valid policy.
    """
    if value is None:
        return None
    # bool is a subclass of int — reject explicitly before the int branch.
    if isinstance(value, bool):
        raise ValueError("recalc_hess must be 'auto', 0, or an integer interval")
    if isinstance(value, int):
        if value < 0 or value > MAX_RECALC_HESS_INTERVAL:
            raise ValueError(
                f"recalc_hess interval must be between 0 and {MAX_RECALC_HESS_INTERVAL}"
            )
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        if text == AUTO_RECALC_HESS:
            return AUTO_RECALC_HESS
        # Reject floats / signs / non-numeric strings; only plain digits allowed.
        if re.fullmatch(r"\d+", text):
            return normalize_recalc_hess(int(text))
        raise ValueError("recalc_hess must be 'auto', 0, or an integer interval")
    raise ValueError("recalc_hess must be 'auto', 0, or an integer interval")


def classify_symbols(
    symbols: list[str] | tuple[str, ...],
) -> tuple[set[str], set[str]]:
    """Split a list of element symbols into heavy and triggering sets.

    Args:
        symbols: Atomic symbols (e.g. ``["C", "H", "H", "O", "Fe"]``).

    Returns:
        A 2-tuple ``(heavy_elements, triggering_elements)`` where

        * ``heavy_elements`` is every element not in :data:`LIGHT_ELEMENTS`
        * ``triggering_elements`` excludes the benign
          :data:`HETEROATOM_ELEMENTS` class (P/S/Si/B). Non-empty
          ``triggering_elements`` ⇒ conservative interval applies.

    Raises:
        ValueError: If *symbols* is empty or contains blank entries.
    """
    if not symbols:
        raise ValueError("symbols list is empty")
    normalized = [str(symbol).strip().capitalize() for symbol in symbols]
    if any(not symbol for symbol in normalized):
        raise ValueError("symbols must not contain empty values")
    heavy = {s for s in normalized if s not in LIGHT_ELEMENTS}
    triggering = heavy - HETEROATOM_ELEMENTS
    return heavy, triggering


def is_light_element_molecule(symbols: list[str] | tuple[str, ...]) -> bool:
    """Return True if every atom in *symbols* is in :data:`LIGHT_ELEMENTS`."""
    heavy, _ = classify_symbols(symbols)
    return not heavy


def default_recalc_hess_for_symbols(
    symbols: list[str] | tuple[str, ...] | None,
) -> int:
    """Return the default ``Recalc_Hess`` interval for *symbols*.

    Two-tier policy:

    * Light-element molecules (CHON + halogens) → ``0`` — no exact
      Hessian is ever computed; ORCA uses its default approximate
      (model) Hessian with BFGS updates.
    * Any molecule containing a non-light element (P/S/Si/B, metals,
      or anything else) → ``NON_LIGHT_DEFAULT_INTERVAL`` (10).

    Args:
        symbols: Atomic symbols. Required for auto inference.

    Returns:
        Concrete integer interval (0 or 10 by default).

    Raises:
        ValueError: If *symbols* is ``None`` or empty.
    """
    if symbols is None:
        raise ValueError("symbols are required when recalc_hess is 'auto'")
    heavy, _ = classify_symbols(symbols)
    if not heavy:
        return 0
    return NON_LIGHT_DEFAULT_INTERVAL


@dataclass(frozen=True)
class HessianResolution:
    """Resolved Hessian policy with full provenance for audit/replay.

    Attributes:
        interval: Concrete integer interval actually emitted to ORCA
            (``0`` means "do not emit ``Recalc_Hess``").
        source: Where the resolution came from — ``"explicit"`` (user
            override) or ``"config"`` (config default or auto fallback).
        reason: Human/machine-readable reason. One of
            ``explicit_interval``, ``explicit_off``, ``auto``.
        heavy_elements: Sorted list of non-light elements present.
        triggering_elements: Sorted subset of ``heavy_elements`` that
            triggered the conservative interval (excludes P/S/Si/B).
    """

    interval: int
    source: str
    reason: str
    heavy_elements: list[str] = field(default_factory=list)
    triggering_elements: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        """True when a ``Recalc_Hess N`` line should be emitted (N > 0)."""
        return self.interval > 0


def resolve_recalc_hess(
    explicit: object = None,
    configured: object = None,
    symbols: list[str] | tuple[str, ...] | None = None,
) -> HessianResolution:
    """Resolve ``recalc_hess`` to a concrete interval + provenance metadata.

    Resolution priority (see plan §5.2):

    1. ``explicit`` (job/API/CLI value) — if non-null, wins outright.
    2. ``configured`` (``optimization_control.recalc_hess``).
    3. Element-graded inference (both null/missing).

    ``"auto"`` at either explicit or configured level triggers element
    inference and ignores any fixed numeric value at the lower-priority
    level. Explicit ``0``/``N`` short-circuits without needing symbols.

    Args:
        explicit: Per-job override (already normalised or raw).
        configured: Config-level value (raw).
        symbols: Atomic symbols of the molecule being optimised. Required
            when the effective policy resolves to ``"auto"``; ignored for
            explicit numeric values.

    Returns:
        A :class:`HessianResolution` describing the concrete behaviour.

    Raises:
        ValueError: If normalisation fails or ``"auto"`` is reached
            without *symbols*.
    """
    interval: int | None = None
    source = "config"
    reason = "auto"

    for candidate, src in ((explicit, "explicit"), (configured, "config")):
        normalized = normalize_recalc_hess(candidate)
        if normalized is None:
            continue
        source = src
        if normalized == AUTO_RECALC_HESS:
            interval = default_recalc_hess_for_symbols(symbols)
            reason = "auto"
        else:
            interval = int(normalized)
            reason = "explicit_interval" if interval > 0 else "explicit_off"
        break

    if interval is None:
        # Both explicit and configured were null/missing → element inference.
        interval = default_recalc_hess_for_symbols(symbols)
        reason = "auto"
        source = "config"

    heavy, triggering = classify_symbols(symbols) if symbols else (set(), set())
    return HessianResolution(
        interval=interval,
        source=source,
        reason=reason,
        heavy_elements=sorted(heavy),
        triggering_elements=sorted(triggering),
    )


__all__ = [
    "AUTO_RECALC_HESS",
    "HETEROATOM_ELEMENTS",
    "HessianResolution",
    "LIGHT_ELEMENTS",
    "MAX_RECALC_HESS_INTERVAL",
    "NON_LIGHT_DEFAULT_INTERVAL",
    "classify_symbols",
    "default_recalc_hess_for_symbols",
    "is_light_element_molecule",
    "normalize_recalc_hess",
    "resolve_recalc_hess",
]
