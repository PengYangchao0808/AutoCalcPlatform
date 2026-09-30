"""Shared method and basis options for BatchOptimize.

Provides two configuration styles:

* **Legacy flat** (``minimum_*`` / ``transition_state_*`` prefixed fields on
  :class:`BatchMethodOptions`) — still supported for backward compatibility.
* **New-style per-role** (``batch_roles`` dict with ``int`` / ``ts`` sub-keys
  each containing a complete :class:`RoleMethodConfig`) — the canonical form
  consumed by the frontend and new submissions.

:meth:`BatchMethodOptions.from_method_dict` is the single entry point: it
accepts either style and normalises to the internal representation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from typing_extensions import assert_never

from acp.calculations.contracts import JsonValue, StepKind

# ── role-override constants ───────────────────────────────────────────────
_ROLE_OVERRIDE_FIELDS: Final[tuple[str, ...]] = (
    "opt_max_iter",
    "opt_convergence",
    "opt_trust_radius",
    "opt_initial_hessian",
    "opt_recalc_hess",
    "opt_rescue_policy",
    "opt_max_rescue",
    "scf_max_iter",
    "scf_convergence",
    "scf_strategy",
)
_ROLE_PREFIX: Final[dict[str, str]] = {
    "int": "minimum_",
    "ts": "transition_state_",
}
_INHERIT: Final[dict[str, tuple[object, ...]]] = {
    "opt_max_iter": (None,),
    "opt_convergence": (None, ""),
    "opt_trust_radius": (None, ""),
    "opt_initial_hessian": (None, "", "auto"),
    "opt_recalc_hess": (None, "", "auto"),
    "opt_rescue_policy": (None, ""),
    "opt_max_rescue": (None,),
    "scf_max_iter": (None,),
    "scf_convergence": (None, ""),
    "scf_strategy": (None, ""),
}
#: Base fields the engine consumes ONLY via ``resolve_role_options`` —
#: their common value must flow through the resolved dict.  All other
#: role-resolvable fields are read from the common attribute by the
#: engine directly, so they resolve only when explicitly overridden.
_ROLE_OVERRIDE_PULL_COMMON: Final[frozenset[str]] = frozenset(
    {"opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess"}
)
#: Built-in TS role defaults (INT has none — values are omitted).  Fields
#: not listed here fall through to the shared common value / engine default.
ROLE_DEFAULTS: Final[dict[str, dict[str, object]]] = {
    "int": {},
    "ts": {
        "opt_trust_radius": 0.3,
        "opt_initial_hessian": "calculate",
        "opt_recalc_hess": 5,
    },
}

# ── new-style per-role config ─────────────────────────────────────────────

#: INT auto-detection sentinels — when the old resolution omitted these
#: fields for INT, the new-style config represents them as ``"auto"``
#: (element-based engine detection downstream).
_INT_AUTO_DEFAULTS: Final[dict[str, str]] = {
    "opt_initial_hessian": "auto",
    "opt_recalc_hess": "auto",
}


@dataclass(frozen=True, slots=True)
class RoleMethodConfig:
    """Complete standalone config for one optimization role (INT or TS).

    Every field is explicit — no inheritance, no "omit to inherit".
    ``null`` (``None``) means *engine default* (the kwarg is not passed
    to ORCA and the engine uses its built-in default).
    """

    # Method / basis
    method: str = ""
    basis: str | None = None
    sp_method: str | None = None
    sp_basis: str | None = None

    # Optimization controls
    opt_max_iter: int | None = None
    opt_convergence: str = "tight"
    opt_trust_radius: float | None = None
    opt_initial_hessian: str | None = None  # "model"|"calculate"|"auto"|None
    opt_recalc_hess: str | int | None = None  # int>=0|"auto"|None
    opt_rescue_policy: str = "adaptive"
    opt_max_rescue: int = 2

    # SCF controls
    scf_max_iter: int = 300
    scf_convergence: str = "tight"
    scf_strategy: str = "normal"
    scf_orbital_inherit: bool = True
    scf_damp: bool = False
    scf_damp_fac: float = 0.50
    scf_shift: bool = False
    scf_shift_fac: float = 0.30

    # Thermochemistry
    temperature: float = 298.15
    pressure: float = 1.0
    scale_factor: float = 0.9905


def _role_config_to_dict(cfg: RoleMethodConfig) -> dict[str, Any]:
    """Serialise a :class:`RoleMethodConfig` to a plain dict."""
    return {
        "method": cfg.method,
        "basis": cfg.basis,
        "sp_method": cfg.sp_method,
        "sp_basis": cfg.sp_basis,
        "opt_max_iter": cfg.opt_max_iter,
        "opt_convergence": cfg.opt_convergence,
        "opt_trust_radius": cfg.opt_trust_radius,
        "opt_initial_hessian": cfg.opt_initial_hessian,
        "opt_recalc_hess": cfg.opt_recalc_hess,
        "opt_rescue_policy": cfg.opt_rescue_policy,
        "opt_max_rescue": cfg.opt_max_rescue,
        "scf_max_iter": cfg.scf_max_iter,
        "scf_convergence": cfg.scf_convergence,
        "scf_strategy": cfg.scf_strategy,
        "scf_orbital_inherit": cfg.scf_orbital_inherit,
        "scf_damp": cfg.scf_damp,
        "scf_damp_fac": cfg.scf_damp_fac,
        "scf_shift": cfg.scf_shift,
        "scf_shift_fac": cfg.scf_shift_fac,
        "temperature": cfg.temperature,
        "pressure": cfg.pressure,
        "scale_factor": cfg.scale_factor,
    }


#: Per-role product defaults — the effective config when NO user overrides
#: are present.  Frontend sync tests read this constant to verify that the
#: wizard's default display matches the engine's actual behaviour.
ROLE_PRODUCT_DEFAULTS: Final[dict[str, dict[str, object]]] = {
    "int": _role_config_to_dict(RoleMethodConfig(
        opt_initial_hessian="auto",
        opt_recalc_hess="auto",
    )),
    "ts": _role_config_to_dict(RoleMethodConfig(
        opt_trust_radius=0.3,
        opt_initial_hessian="calculate",
        opt_recalc_hess=5,
    )),
}


# ── legacy migration ──────────────────────────────────────────────────────


def _build_legacy_opts(d: dict[str, Any]) -> BatchMethodOptions:
    """Construct :class:`BatchMethodOptions` from a legacy flat method dict.

    Mirrors the old ``effective_config.build_opts_from_method_dict`` logic
    so callers can migrate without circular imports.
    """
    from acp.chem.composition import normalize_recalc_hess

    kwargs: dict[str, Any] = {}
    if d.get("functional"):
        kwargs["optimization_method"] = d["functional"]
    if d.get("basis"):
        kwargs["optimization_basis"] = d["basis"]
    if d.get("single_point_method"):
        kwargs["single_point_method"] = d["single_point_method"]
    if d.get("single_point_basis"):
        kwargs["single_point_basis"] = d["single_point_basis"]
    if d.get("minimum_method"):
        kwargs["minimum_method"] = d["minimum_method"]
    if d.get("minimum_basis"):
        kwargs["minimum_basis"] = d["minimum_basis"]
    if d.get("transition_state_method"):
        kwargs["transition_state_method"] = d["transition_state_method"]
    if d.get("transition_state_basis"):
        kwargs["transition_state_basis"] = d["transition_state_basis"]
    if d.get("temperature") is not None:
        kwargs["temperature"] = float(d["temperature"])
    if d.get("pressure") is not None:
        kwargs["pressure"] = float(d["pressure"])
    if d.get("scale_factor") is not None:
        kwargs["scale_factor"] = float(d["scale_factor"])
    # Optimization controls
    if d.get("opt_max_iter") is not None:
        kwargs["opt_max_iter"] = int(d["opt_max_iter"])
    if d.get("opt_convergence"):
        kwargs["opt_convergence"] = d["opt_convergence"]
    if d.get("opt_trust_radius") is not None:
        kwargs["opt_trust_radius"] = float(d["opt_trust_radius"])
    if d.get("opt_initial_hessian") is not None:
        kwargs["opt_initial_hessian"] = d["opt_initial_hessian"]
    if d.get("opt_recalc_hess") is not None:
        kwargs["opt_recalc_hess"] = normalize_recalc_hess(d["opt_recalc_hess"])
    if d.get("opt_rescue_policy"):
        kwargs["opt_rescue_policy"] = d["opt_rescue_policy"]
    if d.get("opt_max_rescue") is not None:
        kwargs["opt_max_rescue"] = int(d["opt_max_rescue"])
    # SCF controls
    if d.get("scf_max_iter") is not None:
        kwargs["scf_max_iter"] = int(d["scf_max_iter"])
    if d.get("scf_convergence"):
        kwargs["scf_convergence"] = d["scf_convergence"]
    if d.get("scf_strategy"):
        kwargs["scf_strategy"] = d["scf_strategy"]
    if d.get("scf_orbital_inherit") is not None:
        kwargs["scf_orbital_inherit"] = bool(d["scf_orbital_inherit"])
    if d.get("scf_damp") is not None:
        kwargs["scf_damp"] = bool(d["scf_damp"])
    if d.get("scf_damp_fac") is not None:
        kwargs["scf_damp_fac"] = float(d["scf_damp_fac"])
    if d.get("scf_shift") is not None:
        kwargs["scf_shift"] = bool(d["scf_shift"])
    if d.get("scf_shift_fac") is not None:
        kwargs["scf_shift_fac"] = float(d["scf_shift_fac"])
    # Per-role overrides
    for prefix in ("minimum_", "transition_state_"):
        for name in _ROLE_OVERRIDE_FIELDS:
            field_name = f"{prefix}{name}"
            raw = d.get(field_name)
            if raw is None or raw == "":
                continue
            if name == "opt_recalc_hess":
                kwargs[field_name] = normalize_recalc_hess(raw)
            elif name in ("opt_max_iter", "scf_max_iter", "opt_max_rescue"):
                kwargs[field_name] = int(raw)
            else:
                kwargs[field_name] = raw
    return BatchMethodOptions(**kwargs)


def _build_role_config_from_opts(
    opts: BatchMethodOptions,
    is_ts: bool,
) -> RoleMethodConfig:
    """Derive a complete :class:`RoleMethodConfig` from legacy resolution.

    The effective value for each field is:
    - role_override (if set and not an inherit sentinel) > common > role_default

    For INT, ``opt_initial_hessian`` and ``opt_recalc_hess`` map to
    ``"auto"`` (element-based detection) when the effective value is None.
    """
    method, basis = opts.for_role(is_ts)
    sp_method = opts.single_point_method or method
    sp_basis = opts.single_point_basis or basis
    role_opts = opts.resolve_role_options(is_ts)

    # INT-specific auto defaults: None → "auto" for element-detection fields
    auto_defaults = _INT_AUTO_DEFAULTS if not is_ts else {}

    # Build kwargs for RoleMethodConfig from resolved values
    rc_kwargs: dict[str, Any] = {
        "method": method,
        "basis": basis or None,
        "sp_method": sp_method or None,
        "sp_basis": sp_basis or None,
    }
    for name in _ROLE_OVERRIDE_FIELDS:
        value = role_opts.get(name)
        if value is None:
            value = getattr(opts, name, None)
        if value is None and name in auto_defaults:
            value = auto_defaults[name]
        rc_kwargs[name] = value

    # Non-role-overridable fields from common
    rc_kwargs["scf_orbital_inherit"] = opts.scf_orbital_inherit
    rc_kwargs["scf_damp"] = opts.scf_damp
    rc_kwargs["scf_damp_fac"] = opts.scf_damp_fac
    rc_kwargs["scf_shift"] = opts.scf_shift
    rc_kwargs["scf_shift_fac"] = opts.scf_shift_fac
    rc_kwargs["temperature"] = opts.temperature
    rc_kwargs["pressure"] = opts.pressure
    rc_kwargs["scale_factor"] = opts.scale_factor

    return RoleMethodConfig(**rc_kwargs)


def migrate_legacy_method_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Migrate a legacy flat method dict to new-style ``batch_roles`` structure.

    Builds :class:`BatchMethodOptions` from the legacy dict, resolves each
    role using the **OLD** rules, and emits a complete new-style
    ``batch_roles`` dict whose *effective* values are identical to what the
    old resolution would have produced (zero computational behaviour change).

    Fields the old resolution omitted become ``None``; old common values
    freeze into BOTH roles unless a role override existed; old TS
    ``ROLE_DEFAULTS`` freeze into migrated TS.
    """
    opts = _build_legacy_opts(d)
    roles: dict[str, dict[str, Any]] = {}
    for is_ts, role_key in ((False, "int"), (True, "ts")):
        rc = _build_role_config_from_opts(opts, is_ts)
        roles[role_key] = _role_config_to_dict(rc)
    return {"batch_roles": roles}


# ── build BatchMethodOptions from per-role configs ────────────────────────


def _build_opts_from_batch_roles(
    batch_roles: dict[str, dict[str, Any]],
) -> BatchMethodOptions:
    """Construct :class:`BatchMethodOptions` from per-role config dicts.

    Each role dict must contain at least ``method``; all other keys are
    optional and default to :data:`ROLE_PRODUCT_DEFAULTS`.
    """
    from acp.chem.composition import normalize_recalc_hess

    int_raw = batch_roles.get("int", {})
    ts_raw = batch_roles.get("ts", {})

    # Fill defaults from ROLE_PRODUCT_DEFAULTS
    int_defaults = ROLE_PRODUCT_DEFAULTS["int"]
    ts_defaults = ROLE_PRODUCT_DEFAULTS["ts"]

    def _get(raw: dict[str, Any], defaults: dict[str, Any], key: str) -> Any:
        v = raw.get(key)
        if v is not None:
            return v
        return defaults.get(key)

    kwargs: dict[str, Any] = {}

    # Common method / basis: use INT as the base
    int_method = _get(int_raw, int_defaults, "method") or ""
    int_basis = _get(int_raw, int_defaults, "basis") or ""
    ts_method = _get(ts_raw, ts_defaults, "method") or ""
    ts_basis = _get(ts_raw, ts_defaults, "basis") or ""
    kwargs["optimization_method"] = int_method
    kwargs["optimization_basis"] = int_basis
    kwargs["minimum_method"] = int_method
    kwargs["minimum_basis"] = int_basis
    kwargs["transition_state_method"] = ts_method
    kwargs["transition_state_basis"] = ts_basis

    # SP (per-role)
    sp_method = _get(int_raw, int_defaults, "sp_method")
    sp_basis = _get(int_raw, int_defaults, "sp_basis")
    kwargs["single_point_method"] = sp_method or ""
    kwargs["single_point_basis"] = sp_basis or ""

    # Thermo (per-role — use INT values as common base)
    kwargs["temperature"] = float(_get(int_raw, int_defaults, "temperature"))
    kwargs["pressure"] = float(_get(int_raw, int_defaults, "pressure"))
    kwargs["scale_factor"] = float(_get(int_raw, int_defaults, "scale_factor"))

    # SCF extras (per-role — use INT values as common base)
    kwargs["scf_orbital_inherit"] = bool(_get(int_raw, int_defaults, "scf_orbital_inherit"))
    kwargs["scf_damp"] = bool(_get(int_raw, int_defaults, "scf_damp"))
    kwargs["scf_damp_fac"] = float(_get(int_raw, int_defaults, "scf_damp_fac"))
    kwargs["scf_shift"] = bool(_get(int_raw, int_defaults, "scf_shift"))
    kwargs["scf_shift_fac"] = float(_get(int_raw, int_defaults, "scf_shift_fac"))

    for name in _ROLE_OVERRIDE_FIELDS:
        value = _get(int_raw, int_defaults, name)
        if name == "opt_recalc_hess" and value is not None:
            value = normalize_recalc_hess(value)
        elif name in ("opt_max_iter", "scf_max_iter", "opt_max_rescue") and value is not None:
            value = int(value)
        kwargs[name] = value

    # Per-role override fields → minimum_*/transition_state_* on the dataclass
    for prefix, raw, defaults in (
        ("minimum_", int_raw, int_defaults),
        ("transition_state_", ts_raw, ts_defaults),
    ):
        for name in _ROLE_OVERRIDE_FIELDS:
            value = _get(raw, defaults, name)
            if name == "opt_recalc_hess" and value is not None:
                value = normalize_recalc_hess(value)
            elif name in ("opt_max_iter", "scf_max_iter", "opt_max_rescue") and value is not None:
                value = int(value)
            kwargs[f"{prefix}{name}"] = value

    return BatchMethodOptions(**kwargs)


@dataclass(frozen=True, slots=True)
class BatchMethodOptions:
    """ORCA settings shared by every BatchOptimize item.

    ``optimization_method`` and ``optimization_basis`` are the canonical
    method pair for both optimization and frequency calculations.  The
    legacy role-specific fields remain available for TS/minimum overrides,
    but frequency-specific fields are deliberately ignored so an old config
    cannot silently make optimization and frequency inconsistent.

    Per-role optimization overrides (``minimum_*`` and ``transition_state_*``
    prefixed) allow TS and minimum structures to receive different trust
    radii, initial-Hessian strategies, Hessian recalculation intervals,
    iteration caps, convergence levels, SCF settings, and rescue policies.
    Resolution priority per plan §5.4::

        role_override > common > role_default > omitted
    """

    # Keep the historical positional order for callers that still construct
    # this dataclass positionally.
    minimum_method: str = ""
    minimum_basis: str = ""
    transition_state_method: str = ""
    transition_state_basis: str = ""
    # Deprecated compatibility fields.  They are retained for old callers,
    # but frequency calculations always use the optimization method pair.
    frequency_method: str = ""
    frequency_basis: str = ""
    optimization_method: str = ""
    optimization_basis: str = ""
    single_point_method: str = ""
    single_point_basis: str = ""
    temperature: float = 298.15
    pressure: float = 1.0
    scale_factor: float = 0.9905

    # Optimization controls
    opt_max_iter: int | None = None
    opt_convergence: str = "tight"
    opt_trust_radius: float | None = None
    opt_initial_hessian: str | None = None
    opt_recalc_hess: str | int | None = None
    opt_rescue_policy: str = "adaptive"
    opt_max_rescue: int = 2

    # SCF controls
    scf_max_iter: int = 300
    scf_convergence: str = "tight"
    scf_strategy: str = "normal"
    scf_orbital_inherit: bool = True
    scf_damp: bool = False
    scf_damp_fac: float = 0.50
    scf_shift: bool = False
    scf_shift_fac: float = 0.30

    # ── per-role optimization overrides ──────────────────────────────────
    # None / "" / "auto" (where applicable) = inherit from the common field.
    minimum_opt_trust_radius: float | None = None
    minimum_opt_initial_hessian: str | None = None
    minimum_opt_recalc_hess: str | int | None = None
    transition_state_opt_trust_radius: float | None = None
    transition_state_opt_initial_hessian: str | None = None
    transition_state_opt_recalc_hess: str | int | None = None

    # ── per-role optimizer / SCF / rescue overrides ──────────────────────
    minimum_opt_max_iter: int | None = None
    minimum_opt_convergence: str | None = None
    minimum_scf_max_iter: int | None = None
    minimum_scf_convergence: str | None = None
    minimum_scf_strategy: str | None = None
    minimum_opt_rescue_policy: str | None = None
    minimum_opt_max_rescue: int | None = None
    transition_state_opt_max_iter: int | None = None
    transition_state_opt_convergence: str | None = None
    transition_state_scf_max_iter: int | None = None
    transition_state_scf_convergence: str | None = None
    transition_state_scf_strategy: str | None = None
    transition_state_opt_rescue_policy: str | None = None
    transition_state_opt_max_rescue: int | None = None

    # ── single entry point ───────────────────────────────────────────────

    @classmethod
    def from_method_dict(cls, d: dict[str, Any]) -> BatchMethodOptions:
        """Construct from a normalised method payload (either style).

        If ``"batch_roles"`` is present → parse new-style directly.
        Otherwise → migrate the legacy flat dict then parse.
        """
        if "batch_roles" in d:
            return _build_opts_from_batch_roles(d["batch_roles"])
        migrated = migrate_legacy_method_dict(d)
        return _build_opts_from_batch_roles(migrated["batch_roles"])

    def for_role(self, is_transition_state: bool) -> tuple[str, str]:
        """Return the method and basis selected for one item role.

        Priority (per plan §3.5):

        * INT: ``minimum_method`` wins over ``optimization_method``.
        * TS: ``transition_state_method`` wins; ``minimum_method`` never
          applies to TS.
        """
        if is_transition_state:
            method = self.transition_state_method or self.optimization_method
            basis = self.transition_state_basis or self.optimization_basis
        else:
            method = self.minimum_method or self.optimization_method
            basis = self.minimum_basis or self.optimization_basis
        return method, basis

    def for_step(self, step: StepKind, is_transition_state: bool) -> tuple[str, str]:
        role_method, role_basis = self.for_role(is_transition_state)
        match step:
            case StepKind.FREQUENCY:
                # Frequency must use exactly the same electronic-structure
                # settings as optimization for a given role.
                return role_method, role_basis
            case StepKind.SINGLEPOINT:
                return (
                    self.single_point_method or role_method,
                    self.single_point_basis or role_basis,
                )
            case StepKind.OPTIMIZE | StepKind.SCAN | StepKind.THERMOCHEMISTRY | StepKind.CASSCF:
                return role_method, role_basis
            case unreachable:
                assert_never(unreachable)

    # ── role-option resolution ───────────────────────────────────────────

    def resolve_role_options(self, is_transition_state: bool) -> dict[str, JsonValue]:
        """Resolve per-role optimization overrides for one item role.

        Resolution chain (plan §5.4):
            role_override > common > role_default > omitted

        Covers every base field in :data:`_ROLE_OVERRIDE_FIELDS`
        (optimizer, Hessian, SCF, and rescue controls).  Returns only the
        keys whose resolved value is not ``None``.
        """
        role = "ts" if is_transition_state else "int"
        prefix = _ROLE_PREFIX[role]
        resolved: dict[str, JsonValue] = {}

        for name in _ROLE_OVERRIDE_FIELDS:
            override = getattr(self, f"{prefix}{name}")
            value: object
            if override not in _INHERIT[name]:
                value = override
            elif name in _ROLE_OVERRIDE_PULL_COMMON:
                value = getattr(self, name)
            else:
                value = None
            if value in _INHERIT[name]:
                value = ROLE_DEFAULTS[role].get(name)
            if value is not None:
                resolved[name] = value  # type: ignore[assignment]

        return resolved

    def resolve_for_step(
        self,
        step: StepKind,
        is_transition_state: bool,
    ) -> dict[str, JsonValue]:
        """Resolve ALL values needed for one calculation step.

        Returns a flat dict with keys matching what the engine's request
        builders consume: ``method``, ``basis``, role-resolved opt kwargs,
        SCF trio, and thermo values.  This is the single entry point the
        engine should use (replacing scattered ``methods.*`` reads).
        """
        role = "ts" if is_transition_state else "int"
        prefix = _ROLE_PREFIX[role]
        role_method, role_basis = self.for_role(is_transition_state)
        role_opts = self.resolve_role_options(is_transition_state)

        result: dict[str, JsonValue] = {}

        match step:
            case StepKind.OPTIMIZE | StepKind.SCAN:
                result["method"] = role_method
                result["basis"] = role_basis
                # Max cycles
                max_cycles = role_opts.get("opt_max_iter")
                if max_cycles is None:
                    max_cycles = self.opt_max_iter if self.opt_max_iter is not None else 200
                result["max_cycles"] = max_cycles
                result["opt_level"] = role_opts.get("opt_convergence") or self.opt_convergence
                result["structure_kind"] = "ts" if is_transition_state else "minimum"
                if "opt_trust_radius" in role_opts:
                    result["trust_radius"] = role_opts["opt_trust_radius"]
                if "opt_initial_hessian" in role_opts:
                    result["initial_hessian"] = role_opts["opt_initial_hessian"]
                if "opt_recalc_hess" in role_opts:
                    result["recalc_hess"] = role_opts["opt_recalc_hess"]
                result["opt_rescue_policy"] = (
                    role_opts.get("opt_rescue_policy") or self.opt_rescue_policy
                )
                max_rescue = role_opts.get("opt_max_rescue")
                result["opt_max_rescue"] = (
                    int(max_rescue) if max_rescue is not None else self.opt_max_rescue
                )
                # SCF trio (per-role)
                scf_maxiter = role_opts.get("scf_max_iter")
                result["scf_maxiter"] = (
                    int(scf_maxiter) if scf_maxiter is not None else self.scf_max_iter
                )
                result["scf_convergence"] = (
                    role_opts.get("scf_convergence") or self.scf_convergence
                )
                result["scf_strategy"] = (
                    role_opts.get("scf_strategy") or self.scf_strategy
                )
                # SCF damp/shift (from common)
                if self.scf_damp:
                    result["scf_damp"] = True
                    result["scf_damp_fac"] = self.scf_damp_fac
                if self.scf_shift:
                    result["scf_shift"] = True
                    result["scf_shift_fac"] = self.scf_shift_fac

            case StepKind.FREQUENCY:
                result["method"] = role_method
                result["basis"] = role_basis
                # SCF trio (per-role)
                scf_maxiter = role_opts.get("scf_max_iter")
                result["scf_maxiter"] = (
                    int(scf_maxiter) if scf_maxiter is not None else self.scf_max_iter
                )
                result["scf_convergence"] = (
                    role_opts.get("scf_convergence") or self.scf_convergence
                )
                result["scf_strategy"] = (
                    role_opts.get("scf_strategy") or self.scf_strategy
                )

            case StepKind.SINGLEPOINT:
                # Per-role SP method/basis falling back to role method/basis
                sp_method = getattr(self, f"{prefix}sp_method", None) or None
                sp_basis = getattr(self, f"{prefix}sp_basis", None) or None
                if sp_method is None:
                    sp_method = self.single_point_method or role_method
                if sp_basis is None:
                    sp_basis = self.single_point_basis or role_basis
                result["method"] = sp_method
                result["basis"] = sp_basis
                # SCF trio (per-role)
                scf_maxiter = role_opts.get("scf_max_iter")
                result["scf_maxiter"] = (
                    int(scf_maxiter) if scf_maxiter is not None else self.scf_max_iter
                )
                result["scf_convergence"] = (
                    role_opts.get("scf_convergence") or self.scf_convergence
                )
                result["scf_strategy"] = (
                    role_opts.get("scf_strategy") or self.scf_strategy
                )

            case StepKind.THERMOCHEMISTRY:
                # Thermo values — per-role if available, else common
                thermo_temp = getattr(self, f"{prefix}temperature", None)
                thermo_press = getattr(self, f"{prefix}pressure", None)
                thermo_scale = getattr(self, f"{prefix}scale_factor", None)
                result["temperature"] = (
                    float(thermo_temp) if thermo_temp is not None else self.temperature
                )
                result["pressure"] = (
                    float(thermo_press) if thermo_press is not None else self.pressure
                )
                result["scale_factor"] = (
                    float(thermo_scale) if thermo_scale is not None else self.scale_factor
                )

            case StepKind.CASSCF:
                result["method"] = role_method
                result["basis"] = role_basis

        return result

    @property
    def cache_key(self) -> str:
        """Return a deterministic representation for checkpoint invalidation."""
        parts: dict[str, object] = {
            "optimization_method": self.optimization_method,
            "optimization_basis": self.optimization_basis,
            "single_point_method": self.single_point_method,
            "single_point_basis": self.single_point_basis,
            "temperature": self.temperature,
            "pressure": self.pressure,
            "scale_factor": self.scale_factor,
            "minimum_method": self.minimum_method,
            "minimum_basis": self.minimum_basis,
            "transition_state_method": self.transition_state_method,
            "transition_state_basis": self.transition_state_basis,
            "frequency_method": self.frequency_method,
            "frequency_basis": self.frequency_basis,
            "opt_max_iter": self.opt_max_iter,
            "opt_convergence": self.opt_convergence,
            "opt_trust_radius": self.opt_trust_radius,
            "opt_initial_hessian": self.opt_initial_hessian,
            "opt_recalc_hess": self.opt_recalc_hess,
            "opt_rescue_policy": self.opt_rescue_policy,
            "opt_max_rescue": self.opt_max_rescue,
            "scf_max_iter": self.scf_max_iter,
            "scf_convergence": self.scf_convergence,
            "scf_strategy": self.scf_strategy,
            "scf_orbital_inherit": self.scf_orbital_inherit,
            "scf_damp": self.scf_damp,
            "scf_damp_fac": self.scf_damp_fac,
            "scf_shift": self.scf_shift,
            "scf_shift_fac": self.scf_shift_fac,
        }
        # Add role-override fields by looping to prevent future omissions.
        for prefix_str in _ROLE_PREFIX.values():
            for name in _ROLE_OVERRIDE_FIELDS:
                parts[f"{prefix_str}{name}"] = getattr(self, f"{prefix_str}{name}")
        return json.dumps(parts, sort_keys=True, separators=(",", ":"))


__all__ = [
    "BatchMethodOptions",
    "ROLE_DEFAULTS",
    "ROLE_PRODUCT_DEFAULTS",
    "RoleMethodConfig",
    "migrate_legacy_method_dict",
]
