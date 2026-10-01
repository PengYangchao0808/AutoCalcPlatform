"""Shared calculation-level model (PES scan optimizer / single-point refinement).

This module is the single normalization point for "which method + basis +
dispersion + solvent + grid + SCF settings actually enter the QC input".
Frontend, contracts, CLI, and execution all funnel through
:func:`canonical_level` so a level means the same thing everywhere.

Q7 layered validation (plan cccp-correctness-hardening T13) — two EXPLICIT
entry contexts with a defined order (:func:`resolve_level_for_entry`):

1. the raw config is preserved and user-EXPLICIT conflicting fields are
   detected (:func:`explicit_level_conflicts`);
2. strict submission (:data:`LevelEntryContext.STRICT`, and the
   user-changed fields of an :data:`LevelEntryContext.EDIT` entry) →
   REJECT the conflicts; migration/historical
   (:data:`LevelEntryContext.MIGRATION`, and the unchanged fields of an
   EDIT entry) → normalized config + collectable warnings (never a silent
   rewrite);
3. the FINAL config is validated uniformly before execution
   (:func:`validate_level_for_purpose`).

Capability / dependency / policy (Q8, three distinct levels): ``GFN0-xTB``
is REJECTED on the ORCA path by POLICY (xTB-binary-only) via
``cccp.qc.keyword_registry.method_policy`` even though ORCA 6.1's
external-interface table lists it (capability = supported) and the local
``param_gfn0-xtb.txt`` gap is a DEPENDENCY gap only.  The rejection message
carries all three records.  ``GFN-FF`` on the ORCA path is documented
capability — the catalog offer stands and the GFN family rules apply.

Dependency direction: ``levels.py`` → ``acp.catalog`` +
``cccp.qc.keyword_registry`` (one-way; neither imports this module at
module level, so there is no import cycle).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, replace
from enum import Enum
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
    "gfn0": "GFN0-xTB",
    "b973c": "B97-3c",
    "r2scan3c": "r2SCAN-3c",
    "b3lyp": "B3LYP",
    "pbe0": "PBE0",
}

# GFN semi-empirical methods runnable through ORCA's native xTB keywords.
GFN_METHODS: frozenset[str] = frozenset({"GFN2-xTB", "GFN1-xTB", "GFN-FF"})

# GFN family spellings as classified by the cccp keyword registry
# (``GFN0-xTB`` is family ``gfn`` even though the ORCA path rejects it).
_GFN_FAMILIES: frozenset[str] = frozenset({"gfn", "gfnff"})

# ORCA-GFN solvent models (PLATFORM POLICY — Q1): everything else migrates
# to ``alpb`` on the historical lane and is rejected on the strict lane.
_GFN_SOLVENT_MODELS: frozenset[str] = frozenset({"none", "alpb"})
_GFN_SOLVENT_MODELS_SHOWN = "{none, ALPB}"

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

LEVEL_FIELDS: tuple[str, ...] = (
    "method",
    "basis",
    "dispersion",
    "solvent_model",
    "solvent",
    "grid",
    "scf_convergence",
    "scf_max_iterations",
    "ri_approximation",
    "aux_j_basis",
    "aux_c_basis",
)


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


def _is_gfn_method(method: str) -> bool:
    """Return whether *method* belongs to the GFN family (registry-driven)."""
    from cccp.qc.keyword_registry import method_family

    if method_family(method) in _GFN_FAMILIES:
        return True
    return method in GFN_METHODS


def _gfn_conflicting_fields(raw: CalculationLevel) -> dict[str, str]:
    """Return ``field → raw value`` for fields the GFN family never takes.

    Basis / dispersion / RI / aux are a DFT layer the GFN family does not
    have (built-in); a non-{none, ALPB} solvent model violates the ORCA-GFN
    solvent policy (Q1).  ``grid`` is deliberately not listed: the catalog
    locks the option set and the cccp renderer strips it with a warning.
    """
    conflicts: dict[str, str] = {}
    basis = _none_if_blank(raw.basis)
    if basis:
        conflicts["basis"] = basis
    dispersion = _none_if_blank(raw.dispersion)
    if dispersion and dispersion.strip().lower() != "none":
        conflicts["dispersion"] = dispersion
    ri = str(raw.ri_approximation or "none").strip().lower()
    if ri not in ("", "none"):
        conflicts["ri_approximation"] = str(raw.ri_approximation)
    for field in ("aux_j_basis", "aux_c_basis"):
        value = _none_if_blank(getattr(raw, field))
        if value:
            conflicts[field] = value
    model = str(raw.solvent_model or "none").strip().lower() or "none"
    if model not in _GFN_SOLVENT_MODELS:
        conflicts["solvent_model"] = str(raw.solvent_model)
    return conflicts


_GFN_CONFLICT_WHY = "GFN family rules: no basis/dispersion/RI/aux layer on the ORCA path"


def _gfn_conflict_error(method: str, field: str, value: str) -> str:
    if field == "solvent_model":
        return (
            f"GFN method {method!r} allows solvent models "
            f"{_GFN_SOLVENT_MODELS_SHOWN} on the ORCA path (PLATFORM POLICY); "
            f"explicit solvent_model {value!r} is not allowed"
        )
    return (
        f"GFN method {method!r} carries a built-in {field.replace('_', ' ')}; "
        f"explicit {field} {value!r} is not allowed ({_GFN_CONFLICT_WHY})"
    )


def _gfn_migrate_warning(method: str, field: str, value: str) -> str:
    if field == "solvent_model":
        return (
            f"GFN method {method!r}: historical solvent_model {value!r} migrated "
            f"to 'alpb' (ORCA-GFN solvent models {_GFN_SOLVENT_MODELS_SHOWN}; "
            "PLATFORM POLICY — no silent rewrite)"
        )
    return (
        f"GFN method {method!r}: historical {field} {value!r} cleared "
        f"({_GFN_CONFLICT_WHY}; warned, never silently rewritten)"
    )


def explicit_level_conflicts(level: CalculationLevel) -> list[tuple[str, str]]:
    """Return ``(field, error)`` for user-EXPLICIT conflicting fields (step 1).

    The raw config is preserved: every check inspects the raw field values,
    never the canonicalized ones, so a clear-then-validate composition can
    never hide a conflict.  GFN family rules and composite-3c built-in
    locking are both detected here; the composite-3c checks are unchanged
    (a real basis/dispersion/RI override stays an error in every context).
    """
    from acp.catalog import METHOD_META, _case_insensitive_get

    method = normalize_method_alias(level.method)
    meta = _case_insensitive_get(METHOD_META, method)
    conflicts: list[tuple[str, str]] = []

    if _is_gfn_method(method):
        for field, value in _gfn_conflicting_fields(level).items():
            conflicts.append((field, _gfn_conflict_error(method, field, value)))
        return conflicts

    if meta is not None and str(meta.get("ri_support") or "user") == "composite":
        # Older Workbench builds serialize the composite method's locked
        # built-in basis label (for example, B97-3c's ``mTZVP``) as if it
        # were a user override.  Treat that exact catalog value as a UI
        # echo: canonical_level() clears it before the QC input is built.
        # Keep rejecting any different basis so real overrides remain an
        # error.
        requested_basis = str(level.basis or "").strip()
        builtin_basis = str(meta.get("default_basis") or "").strip()
        if requested_basis and requested_basis.casefold() != builtin_basis.casefold():
            conflicts.append(
                (
                    "basis",
                    f"composite method {method!r} carries a built-in basis; "
                    f"explicit basis {requested_basis!r} conflicts with built-in basis "
                    f"{builtin_basis!r}",
                )
            )
        if level.dispersion and level.dispersion.strip().lower() != "none":
            conflicts.append(
                (
                    "dispersion",
                    f"composite method {method!r} carries a built-in dispersion "
                    "correction; an explicit dispersion is not allowed",
                )
            )
        if str(level.ri_approximation or "none").lower() not in ("", "none"):
            conflicts.append(
                (
                    "ri_approximation",
                    f"composite method {method!r} fixes its RI chain; "
                    "an explicit RI approximation is not allowed",
                )
            )
    return conflicts


def _gfn_method_policy_error(method: str) -> str | None:
    """Return the GFN0-xTB ORCA-path policy error, or ``None`` when allowed.

    Q8 three-level record: capability / dependency / policy are DISTINCT.
    The rejection message carries all three so no caller can mistake the
    missing local ``param_gfn0-xtb.txt`` (dependency) for missing software
    support (capability) — either way POLICY (xTB-binary-only) rejects it.
    """
    from cccp.qc.keyword_registry import KeywordValueError, method_family, method_policy

    if method_family(method) == "unknown":
        return None
    try:
        decision = method_policy(method, engine="orca")
    except KeywordValueError:
        return None
    if decision.allowed:
        return None
    return (
        f"method {method!r} is rejected on the ORCA path: {decision.reason}. "
        f"capability: {decision.capability}; "
        f"dependency: {decision.dependency}; "
        f"policy: {decision.policy}"
    )


def canonical_level(
    level: CalculationLevel, *, warnings: list[str] | None = None
) -> CalculationLevel:
    """Return the canonical form of *level*.

    * Method aliases are normalised (``normalize_method_alias``).
    * Composite (3c) methods lock ``basis``/``dispersion``/RI/aux to the
      built-in values — the fields are cleared so nothing can be stacked on
      top of the composite definition.
    * GFN family methods carry no basis/dispersion/RI/aux layer: the fields
      are cleared AND, when *warnings* is provided, one collectable warning
      per cleared historical value is appended (Q7: warn, never silently
      rewrite).
    * GFN + non-{none, ALPB} solvent model (CPCM/SMD/GBSA) migrates to
      ``"alpb"`` with a warning — the explicit, warned ORCA-GFN migration.
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
        if _is_gfn_method(method):
            basis = None
            dispersion = None
            ri_approximation = "none"
            aux_j_basis = None
            aux_c_basis = None

    if _is_gfn_method(method):
        for field, value in _gfn_conflicting_fields(level).items():
            if warnings is not None and field != "solvent_model":
                warnings.append(_gfn_migrate_warning(method, field, value))
        if solvent_model not in _GFN_SOLVENT_MODELS:
            if warnings is not None:
                warnings.append(_gfn_migrate_warning(method, "solvent_model", solvent_model))
            solvent_model = "alpb"

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

    This is the NEW-SUBMISSION (strict) validator: it inspects the RAW
    level, so user-EXPLICIT conflicts are rejected, never cleared first.

    ``purpose="scan_optimization"`` requires the method to declare the
    ``scan_optimization`` capability (or be a GFN family method);
    ``purpose="single_point"`` accepts any non-empty method.  Both
    purposes enforce: GFN family rules (explicit basis/dispersion/RI/aux
    and non-{none, ALPB} solvent models are rejected — Q1/Q7), composite-3c
    built-in locking, the GFN0-xTB ORCA-path policy gate (Q8, with the
    capability/dependency/policy three-level record), and solvent-name
    consistency.
    """
    errors: list[str] = []
    if purpose not in ("scan_optimization", "single_point"):
        raise ValueError(
            f"unknown calculation level purpose: {purpose!r} "
            "(expected 'scan_optimization' or 'single_point')"
        )
    canonical = canonical_level(level)
    if not canonical.method:
        errors.append("calculation level method is required")
        return errors

    if purpose == "scan_optimization":
        from acp.catalog import METHOD_META, _case_insensitive_get

        meta = _case_insensitive_get(METHOD_META, canonical.method)
        if _is_gfn_method(canonical.method):
            pass
        elif meta is not None and (meta.get("capabilities") or {}).get("scan_optimization"):
            pass
        elif meta is not None:
            errors.append(
                f"method {canonical.method!r} does not declare the scan_optimization capability"
            )
        else:
            errors.append(f"unknown scan optimization method: {canonical.method!r}")

    policy_error = _gfn_method_policy_error(canonical.method)
    if policy_error is not None:
        errors.append(policy_error)

    errors.extend(message for _field, message in explicit_level_conflicts(level))

    if canonical.solvent_model != "none" and not canonical.solvent:
        errors.append(
            f"solvent is required when solvent_model is {canonical.solvent_model!r}"
        )
    return errors


# ── entry contexts (Q7 layered validation) ─────────────────────────────


class LevelEntryContext(str, Enum):
    """Entry-behavior contract (plan Scope) for one calculation level.

    ``STRICT``: new submissions (API / wizard / plain CLI) and the
    user-changed fields of an edit-recalculate — explicit conflicts reject.
    ``MIGRATION``: historical rerun / recompute unchanged fields — explicit
    conflicts normalize with warnings and never reject.
    ``EDIT``: the edit-recalculate entry that mixes the two lanes per field
    (fields in ``changed_fields`` are strict, everything else migrates).
    """

    STRICT = "strict"
    MIGRATION = "migration"
    EDIT = "edit"


@dataclass(frozen=True)
class LevelResolution:
    """Result of one layered level resolution.

    Attributes:
        level: The FINAL config (canonical/normalized) to execute.
        errors: Non-empty → the entry must reject.
        warnings: Collectable migration/normalization warnings (surfaced by
            T14/T24 — never logger-only).
        normalized_fields: Raw fields rewritten by the migration lane.
    """

    level: CalculationLevel
    errors: list[str]
    warnings: list[str]
    normalized_fields: list[str]


def changed_level_fields(
    raw: CalculationLevel, baseline: CalculationLevel | None
) -> set[str]:
    """Return the fields of *raw* the user changed versus *baseline*.

    Value semantics: missing/blank == ``None``; the method compares after
    alias normalization (``"b973c"`` == ``"B97-3c"``).  A field whose final
    value equals the historical one is inherited (migration lane) even when
    the user retyped it.
    """
    if baseline is None:
        return set(LEVEL_FIELDS)
    changed: set[str] = set()
    for field in LEVEL_FIELDS:
        if field == "method":
            old = normalize_method_alias(str(baseline.method or ""))
            new = normalize_method_alias(str(raw.method or ""))
            if old != new:
                changed.add(field)
            continue
        old_value = getattr(baseline, field)
        new_value = getattr(raw, field)
        if isinstance(old_value, str) or isinstance(new_value, str):
            old_norm = _none_if_blank(old_value)
            new_norm = _none_if_blank(new_value)
            if (old_norm or "").casefold() != (new_norm or "").casefold():
                changed.add(field)
        elif old_value != new_value:
            changed.add(field)
    return changed


def resolve_level_for_entry(
    raw: CalculationLevel,
    *,
    purpose: str,
    context: LevelEntryContext | str = LevelEntryContext.STRICT,
    changed_fields: Collection[str] | None = None,
) -> LevelResolution:
    """Run the Q7 layered resolution (steps 1 → 2 → 3) for one entry.

    1. preserve *raw* and detect user-EXPLICIT conflicting fields;
    2. strict lane → reject, migration lane → normalized config + warnings;
    3. validate the FINAL config uniformly before execution.

    Args:
        raw: The raw level exactly as the user/record provides it.
        purpose: ``"scan_optimization"`` or ``"single_point"``.
        context: The entry context (see :class:`LevelEntryContext`).
        changed_fields: For ``EDIT`` entries, the fields the user changed in
            this request; those are strict, the rest migrate.  ``None``
            means "cannot prove inheritance" → everything is strict.

    Returns:
        :class:`LevelResolution` — never raises for level errors; the
        caller decides how to reject (API 4xx / CLI non-zero / ValueError).
    """
    entry = LevelEntryContext(context)
    method = normalize_method_alias(str(raw.method or ""))
    is_gfn = _is_gfn_method(method)
    conflicts = explicit_level_conflicts(raw)
    errors: list[str] = []
    warnings: list[str] = []
    normalized_fields: list[str] = []
    for field, message in conflicts:
        # Composite-3c built-in locking is strict in EVERY context (never
        # loosened); only the GFN family migration whitelist (Q7) may
        # normalize a conflicting field on the historical lane.
        strict_lane = not is_gfn or entry is LevelEntryContext.STRICT or (
            entry is LevelEntryContext.EDIT
            and (changed_fields is None or field in changed_fields)
        )
        if strict_lane:
            errors.append(message)
        else:
            value = str(getattr(raw, field) or "")
            warnings.append(_gfn_migrate_warning(method, field, value))
            normalized_fields.append(field)

    level = canonical_level(raw)
    errors.extend(validate_level_for_purpose(level, purpose))
    return LevelResolution(
        level=level, errors=errors, warnings=warnings, normalized_fields=normalized_fields
    )


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
    "LEVEL_FIELDS",
    "CalculationLevel",
    "LevelEntryContext",
    "LevelResolution",
    "canonical_level",
    "changed_level_fields",
    "engine_for_method",
    "explicit_level_conflicts",
    "level_fingerprint",
    "normalize_method_alias",
    "resolve_level_for_entry",
    "scan_optimization_methods",
    "validate_level_for_purpose",
]
