"""Shared calculation-level model (PES scan optimizer / single-point refinement).

This module is the single normalization point for "which method + basis +
dispersion + solvent + grid + SCF settings actually enter the QC input".
Frontend, contracts, CLI, and execution all funnel through
:func:`canonical_level` so a level means the same thing everywhere.

Dependency direction: ``levels.py`` → ``acp.catalog`` (one-way; the catalog
never imports this module, so there is no import cycle).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

# ── method aliases ─────────────────────────────────────────────────────
# Keys are squashed (no space/underscore/hyphen, lowercase).  Values are
# canonical names: METHOD_META casing for DFT methods, ORCA keyword casing
# for the GFN family.
_METHOD_ALIASES: dict[str, str] = {
    "gfn2": "GFN2-xTB",
    "gfn2xtb": "GFN2-xTB",
    "gfn1": "GFN1-xTB",
    "gfn1xtb": "GFN1-xTB",
    "gfnff": "GFN-FF",
    "gfn0": "GFN-FF",
    "b973c": "B97-3c",
    "r2scan3c": "r2SCAN-3c",
    "b3lyp": "B3LYP",
    "pbe0": "PBE0",
}

# GFN semi-empirical methods runnable through ORCA's native xTB keywords.
GFN_METHODS: frozenset[str] = frozenset({"GFN2-xTB", "GFN1-xTB", "GFN-FF"})

_SQUASH_RE = re.compile(r"[\s_\-]+")


def _squash(value: str) -> str:
    return _SQUASH_RE.sub("", value).lower()


def normalize_method_alias(raw: str) -> str:
    """Normalise free-text method input to the canonical catalog name.

    Examples: ``"b973c" → "B97-3c"``, ``"r2scan-3c" → "r2SCAN-3c"``,
    ``"gfn2" → "GFN2-xTB"``, ``"b3lyp" → "B3LYP"``.  Unknown names are
    returned stripped but otherwise unchanged.
    """
    text = str(raw or "").strip()
    if not text:
        return text
    alias = _METHOD_ALIASES.get(_squash(text))
    if alias is not None:
        return alias
    from acp.catalog import METHOD_META, _case_insensitive_get

    if _case_insensitive_get(METHOD_META, text) is not None:
        for key in METHOD_META:
            if key.lower() == text.lower():
                return key
    for gfn in GFN_METHODS:
        if gfn.lower() == text.lower():
            return gfn
    return text


# ── calculation level ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CalculationLevel:
    """Canonical calculation level shared by scan optimization and SP.

    ``solvent_model == "none"`` explicitly means *no* solvent; ``None`` or a
    missing value is parsed to ``"none"`` and never falls back to any global
    solvent configuration (plan §5 rule 2).
    """

    method: str
    basis: str | None = None
    dispersion: str | None = None
    solvent_model: str = "none"
    solvent: str | None = None
    grid: str | None = None
    scf_convergence: str | None = None
    scf_max_iterations: int | None = None
    ri_approximation: str = "none"
    aux_j_basis: str | None = None
    aux_c_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "basis": self.basis,
            "dispersion": self.dispersion,
            "solvent_model": self.solvent_model,
            "solvent": self.solvent,
            "grid": self.grid,
            "scf_convergence": self.scf_convergence,
            "scf_max_iterations": self.scf_max_iterations,
            "ri_approximation": self.ri_approximation,
            "aux_j_basis": self.aux_j_basis,
            "aux_c_basis": self.aux_c_basis,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> CalculationLevel:
        payload = dict(payload or {})
        raw_maxiter = payload.get("scf_max_iterations")
        try:
            scf_max_iterations = int(raw_maxiter) if raw_maxiter is not None else None
        except (TypeError, ValueError):
            scf_max_iterations = None
        return cls(
            method=str(payload.get("method") or ""),
            basis=_none_if_blank(payload.get("basis")),
            dispersion=_none_if_blank(payload.get("dispersion")),
            solvent_model=str(payload.get("solvent_model") or "none"),
            solvent=_none_if_blank(payload.get("solvent")),
            grid=_none_if_blank(payload.get("grid")),
            scf_convergence=_none_if_blank(payload.get("scf_convergence")),
            scf_max_iterations=scf_max_iterations,
            ri_approximation=str(payload.get("ri_approximation") or "none"),
            aux_j_basis=_none_if_blank(payload.get("aux_j_basis")),
            aux_c_basis=_none_if_blank(payload.get("aux_c_basis")),
        )


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def canonical_level(level: CalculationLevel) -> CalculationLevel:
    """Return the canonical form of *level*.

    * Method aliases are normalised (``normalize_method_alias``).
    * Composite (3c) methods lock ``basis``/``dispersion``/RI/aux to the
      built-in values — the fields are cleared so nothing can be stacked on
      top of the composite definition.
    * ``basis_inline`` methods fall back to the METHOD_META
      ``default_basis`` / ``default_dispersion`` when unset.
    * ``solvent_model`` is lowercased; ``"none"`` clears ``solvent``.
    """
    from acp.catalog import METHOD_META, _case_insensitive_get

    method = normalize_method_alias(level.method)
    meta = _case_insensitive_get(METHOD_META, method)

    solvent_model = str(level.solvent_model or "none").strip().lower() or "none"
    solvent = level.solvent if solvent_model != "none" else None

    basis = level.basis
    dispersion = level.dispersion
    ri_approximation = str(level.ri_approximation or "none")
    aux_j_basis = level.aux_j_basis
    aux_c_basis = level.aux_c_basis

    if meta is not None:
        ri_support = str(meta.get("ri_support") or "user")
        if ri_support == "composite":
            basis = None
            dispersion = None
            ri_approximation = "none"
            aux_j_basis = None
            aux_c_basis = None
        else:
            if basis is None:
                default_basis = meta.get("default_basis")
                basis = str(default_basis) if default_basis else None
            if dispersion is None:
                default_dispersion = meta.get("default_dispersion")
                dispersion = str(default_dispersion) if default_dispersion else None
            if ri_support == "automatic":
                ri_approximation = "none"
    else:
        # GFN family (and unknown methods): no basis/dispersion/RI layer.
        if method in GFN_METHODS:
            basis = None
            dispersion = None
            ri_approximation = "none"
            aux_j_basis = None
            aux_c_basis = None

    if dispersion is not None and dispersion.strip().lower() in ("", "none"):
        dispersion = None if dispersion.strip().lower() == "" else dispersion

    return replace(
        level,
        method=method,
        basis=basis,
        dispersion=dispersion,
        solvent_model=solvent_model,
        solvent=solvent,
        ri_approximation=ri_approximation,
        aux_j_basis=aux_j_basis,
        aux_c_basis=aux_c_basis,
    )


def engine_for_method(method: str) -> str:
    """Return the execution engine for *method* on the PES scan chain.

    PES scans always run through the ORCA subprocess (native relaxed scan or
    per-point constrained optimisation); GFN methods are written as ORCA
    method keywords.  Hence every scan-optimization method maps to
    ``"orca"``.
    """
    _ = normalize_method_alias(method)
    return "orca"


def scan_optimization_methods() -> list[str]:
    """Return the catalog-filtered scan-optimization method list.

    The list is ``capabilities.scan_optimization``-filtered (plan §5 rule
    4): the single-point method catalog is never copied wholesale into the
    scan optimizer.
    """
    from acp.catalog import METHOD_META

    methods = ["GFN2-xTB", "GFN1-xTB", "GFN-FF"]
    for name, meta in METHOD_META.items():
        capabilities = meta.get("capabilities") or {}
        if capabilities.get("scan_optimization"):
            methods.append(name)
    return methods


def validate_level_for_purpose(level: CalculationLevel, purpose: str) -> list[str]:
    """Validate *level* for *purpose*; return a list of error strings.

    ``purpose="scan_optimization"`` requires the method to declare the
    ``scan_optimization`` capability (or be a GFN family method), forbids
    basis/dispersion/RI overrides on composite 3c methods, and requires a
    solvent name whenever a solvent model is active.
    """
    errors: list[str] = []
    canonical = canonical_level(level)
    if not canonical.method:
        errors.append("calculation level method is required")
        return errors

    if purpose == "scan_optimization":
        from acp.catalog import METHOD_META, _case_insensitive_get

        meta = _case_insensitive_get(METHOD_META, canonical.method)
        if canonical.method in GFN_METHODS:
            pass
        elif meta is not None and (meta.get("capabilities") or {}).get("scan_optimization"):
            pass
        elif meta is not None:
            errors.append(
                f"method {canonical.method!r} does not declare the scan_optimization capability"
            )
        else:
            errors.append(f"unknown scan optimization method: {canonical.method!r}")

        if meta is not None and str(meta.get("ri_support") or "user") == "composite":
            # Older Workbench builds serialize the composite method's locked
            # built-in basis label (for example, B97-3c's ``mTZVP``) as if it
            # were a user override.  Treat that exact catalog value as a UI
            # echo: canonical_level() clears it before the QC input is built.
            # Keep rejecting any different basis so real overrides remain an
            # error.
            requested_basis = str(level.basis or "").strip()
            builtin_basis = str(meta.get("default_basis") or "").strip()
            if (
                requested_basis
                and requested_basis.casefold() != builtin_basis.casefold()
            ):
                errors.append(
                    f"composite method {canonical.method!r} carries a built-in basis; "
                    f"explicit basis {requested_basis!r} conflicts with built-in basis "
                    f"{builtin_basis!r}"
                )
            if level.dispersion and level.dispersion.strip().lower() != "none":
                errors.append(
                    f"composite method {canonical.method!r} carries a built-in dispersion "
                    "correction; an explicit dispersion is not allowed"
                )
            if str(level.ri_approximation or "none").lower() not in ("", "none"):
                errors.append(
                    f"composite method {canonical.method!r} fixes its RI chain; "
                    "an explicit RI approximation is not allowed"
                )

    if canonical.solvent_model != "none" and not canonical.solvent:
        errors.append(
            f"solvent is required when solvent_model is {canonical.solvent_model!r}"
        )
    return errors


def level_fingerprint(level: CalculationLevel) -> str:
    """Return a stable 16-char fingerprint of the canonical level.

    Used in manifests, checkpoints, and SP cache scoping so that changing
    the level never reuses results computed at a different level.
    """
    canonical = canonical_level(level)
    payload = json.dumps(canonical.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "GFN_METHODS",
    "CalculationLevel",
    "canonical_level",
    "engine_for_method",
    "level_fingerprint",
    "normalize_method_alias",
    "scan_optimization_methods",
    "validate_level_for_purpose",
]
