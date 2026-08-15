# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""TS / INT rescue matrix (declarative, mirroring RPH v4.0.1).

A declarative mapping from ``(failure_type, structure_kind)`` to an ordered
list of rescue strategies. The workflow consults the matrix after a failed
optimization, applies the strategies in order, and re-runs the failing stage
until one succeeds or the list is exhausted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

FRESH_HESSIAN_RESTART = "fresh_hessian_restart"
FRESH_HESSIAN_MODE_MONITOR = "fresh_hessian_mode_monitor"
TS_MODE_DIRECTED = "ts_mode_directed"
MODE_DISPLACEMENT = "mode_displacement"
SADDLE_BREAK = "saddle_break"
CALCALL_OPT = "calcall_opt"
TIGHT_OPT_CALCHESS = "tight_opt_calchess"
IRC_MIDPOINT_RECOVERY = "irc_midpoint_recovery"

RescueStrategy = Literal[
    "fresh_hessian_restart",
    "fresh_hessian_mode_monitor",
    "ts_mode_directed",
    "mode_displacement",
    "saddle_break",
    "calcall_opt",
    "tight_opt_calchess",
    "irc_midpoint_recovery",
]

FailureType = Literal[
    "geometry_not_converged",
    "higher_order_saddle",
    "ts_no_imaginary",
    "minimum_with_imaginary",
    "scf_failure",
    "crash_timeout",
    "collapsed_to_product",
]

StructureKind = Literal["ts", "intermediate", "minimum", "precursor", "product"]

_RESCUE_MATRIX: dict[tuple[FailureType, StructureKind], list[str]] = {
    ("geometry_not_converged", "ts"): [
        FRESH_HESSIAN_RESTART,
        TS_MODE_DIRECTED,
        CALCALL_OPT,
    ],
    ("geometry_not_converged", "intermediate"): [
        FRESH_HESSIAN_RESTART,
        CALCALL_OPT,
    ],
    ("geometry_not_converged", "minimum"): [
        FRESH_HESSIAN_RESTART,
        CALCALL_OPT,
    ],
    ("higher_order_saddle", "ts"): [
        SADDLE_BREAK,
        TS_MODE_DIRECTED,
        CALCALL_OPT,
    ],
    ("ts_no_imaginary", "ts"): [
        FRESH_HESSIAN_MODE_MONITOR,
        TS_MODE_DIRECTED,
        CALCALL_OPT,
    ],
    ("minimum_with_imaginary", "intermediate"): [
        MODE_DISPLACEMENT,
    ],
    ("minimum_with_imaginary", "minimum"): [
        MODE_DISPLACEMENT,
    ],
    ("collapsed_to_product", "intermediate"): [
        IRC_MIDPOINT_RECOVERY,
    ],
    ("scf_failure", "ts"): [],
    ("scf_failure", "intermediate"): [],
    ("scf_failure", "minimum"): [],
    ("scf_failure", "precursor"): [],
    ("scf_failure", "product"): [],
    ("crash_timeout", "ts"): [],
    ("crash_timeout", "intermediate"): [],
    ("crash_timeout", "minimum"): [],
    ("crash_timeout", "precursor"): [],
    ("crash_timeout", "product"): [],
}

_RESCUE_DESCRIPTIONS: dict[str, str] = {
    FRESH_HESSIAN_RESTART: "restart with CalcHess + RecalcHess=5",
    FRESH_HESSIAN_MODE_MONITOR: "restart with fresh Hessian while monitoring the target mode",
    TS_MODE_DIRECTED: "re-run with TS_Mode targeting the lowest imaginary mode",
    CALCALL_OPT: "re-run with RecalcHess=1 (CalcAll semantics)",
    SADDLE_BREAK: "displace along the second imaginary mode to break the saddle",
    MODE_DISPLACEMENT: "displace ±0.30 Å along the imaginary mode",
    TIGHT_OPT_CALCHESS: "tight optimization with calculated Hessian",
    IRC_MIDPOINT_RECOVERY: "re-seed from the IRC midpoint (collapsed INT recovery)",
}

FAILURE_EXIT: set[FailureType] = {"scf_failure", "crash_timeout"}


@dataclass(frozen=True)
class RescueMethodParams:
    """Resolved per-method rescue parameters mirroring RPH method defaults."""

    method: RescueStrategy
    max_cycles: int = 30
    recalc_hess: int | None = None
    calc_hess: bool = False
    trust: float | None = None
    displacement_step_angstrom: float = 0.30
    displacement_sign: tuple[str, ...] = ("plus", "minus")
    mode_min_overlap: float = 0.35
    mode_min_overlap_margin: float = 0.08
    irc_max_iter: int = 5
    irc_direction: str = "both"
    shoulder_energy_window_kcal_mol: float = 2.0

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "max_cycles": self.max_cycles,
            "recalc_hess": self.recalc_hess,
            "calc_hess": self.calc_hess,
            "trust": self.trust,
            "displacement_step_angstrom": self.displacement_step_angstrom,
            "displacement_sign": list(self.displacement_sign),
            "mode_min_overlap": self.mode_min_overlap,
            "mode_min_overlap_margin": self.mode_min_overlap_margin,
            "irc_max_iter": self.irc_max_iter,
            "irc_direction": self.irc_direction,
            "shoulder_energy_window_kcal_mol": self.shoulder_energy_window_kcal_mol,
        }


@dataclass(frozen=True)
class RescueAction:
    """One rescue step emitted by the matrix.

    Attributes:
        strategy: Rescue strategy id.
        failure_type: The failure that triggered this rescue.
        structure_kind: The structure being rescued.
        description: Human-readable strategy description.
        index: Position in the ordered rescue chain (0-based).
        params: Resolved method-level rescue parameters.
    """

    strategy: str
    failure_type: FailureType
    structure_kind: StructureKind
    description: str
    index: int = 0
    params: RescueMethodParams | None = None


@dataclass(frozen=True)
class RescuePlan:
    """Ordered rescue chain for one failed optimization.

    Attributes:
        failure_type: Classified failure.
        structure_kind: Structure being rescued.
        actions: Ordered rescue actions (empty when the failure is terminal).
        terminal: Whether the failure has no rescue path (SCF crash / timeout).
    """

    failure_type: FailureType
    structure_kind: StructureKind
    actions: list[RescueAction] = field(default_factory=list)
    terminal: bool = False

    @property
    def exhausted(self) -> bool:
        return self.terminal or not self.actions


def build_rescue_plan(
    failure_type: FailureType,
    structure_kind: StructureKind,
    overrides: Mapping[str, Any] | None = None,
) -> RescuePlan:
    """Build a :class:`RescuePlan` for a ``(failure_type, structure_kind)`` pair.

    SCF failures and crashes/timeouts are terminal (no rescue); everything
    else follows the ordered strategies in the matrix (an empty list for the
    pair behaves as terminal).
    """
    terminal = failure_type in FAILURE_EXIT
    strategies = _RESCUE_MATRIX.get((failure_type, structure_kind), [])
    if terminal or not strategies:
        return RescuePlan(
            failure_type=failure_type,
            structure_kind=structure_kind,
            actions=[],
            terminal=terminal or not strategies,
        )
    actions = [
        RescueAction(
            strategy=strategy,
            failure_type=failure_type,
            structure_kind=structure_kind,
            description=_RESCUE_DESCRIPTIONS.get(strategy, strategy),
            index=i,
            params=method_params(strategy, overrides),
        )
        for i, strategy in enumerate(strategies)
    ]
    return RescuePlan(
        failure_type=failure_type,
        structure_kind=structure_kind,
        actions=actions,
        terminal=False,
    )


def method_params(
    strategy: str,
    overrides: Mapping[str, Any] | None = None,
) -> RescueMethodParams:
    """Resolve method parameters for one rescue strategy.

    ``overrides`` may be either a full ACP/RPH-style config tree containing
    ``refinement.common.rescue.methods`` or a direct ``{strategy: {...}}`` /
    ``{"methods": {strategy: {...}}}`` mapping.
    """
    defaults = _METHOD_PARAMS_DEFAULTS[_as_strategy(strategy)]
    method_cfg = _resolve_method_override(strategy, overrides)
    if not method_cfg:
        return defaults
    recalc_value = method_cfg.get("recalc_hess", method_cfg.get("recalc_hessian"))
    return RescueMethodParams(
        method=defaults.method,
        max_cycles=_as_int(method_cfg.get("max_cycles"), defaults.max_cycles),
        recalc_hess=_as_optional_int(recalc_value, defaults.recalc_hess),
        calc_hess=_as_bool(method_cfg.get("calc_hess"), defaults.calc_hess),
        trust=_as_optional_float(method_cfg.get("trust"), defaults.trust),
        displacement_step_angstrom=_as_float(
            method_cfg.get("displacement_step_angstrom"),
            defaults.displacement_step_angstrom,
        ),
        displacement_sign=_as_displacement_sign(
            method_cfg.get("displacement_sign"),
            defaults.displacement_sign,
        ),
        mode_min_overlap=_as_float(
            method_cfg.get("mode_min_overlap"),
            defaults.mode_min_overlap,
        ),
        mode_min_overlap_margin=_as_float(
            method_cfg.get("mode_min_overlap_margin"),
            defaults.mode_min_overlap_margin,
        ),
        irc_max_iter=_as_int(method_cfg.get("irc_max_iter"), defaults.irc_max_iter),
        irc_direction=str(method_cfg.get("irc_direction", defaults.irc_direction)),
        shoulder_energy_window_kcal_mol=_as_float(
            method_cfg.get("shoulder_energy_window_kcal_mol"),
            defaults.shoulder_energy_window_kcal_mol,
        ),
    )


def apply_rescue_kwargs(
    action: RescueAction,
    base_kwargs: dict[str, object],
    overrides: Mapping[str, Any] | None = None,
    *,
    include_metadata: bool = False,
) -> dict[str, object]:
    """Map a rescue action onto keyword overrides for the next stage run.

    Each strategy translates to ORCA/xTB keyword adjustments (fresh Hessian,
    TS_Mode, RecalcHess, trust radius, displacement). Unknown strategies pass
    *base_kwargs* through unchanged.
    """
    kwargs = dict(base_kwargs)
    strategy = action.strategy
    params = (
        action.params
        if overrides is None and action.params is not None
        else method_params(strategy, overrides)
    )
    metadata = _unsupported_method_metadata(strategy, params)
    if strategy in {FRESH_HESSIAN_RESTART, FRESH_HESSIAN_MODE_MONITOR}:
        kwargs["initial_hessian"] = "calculate"
        if params.recalc_hess is not None:
            kwargs["recalc_hess"] = params.recalc_hess
    elif strategy == TS_MODE_DIRECTED:
        kwargs["ts_mode"] = True
        if params.trust is not None:
            kwargs["trust_radius"] = params.trust
    elif strategy == CALCALL_OPT:
        if params.recalc_hess is not None:
            kwargs["recalc_hess"] = params.recalc_hess
    elif strategy in {SADDLE_BREAK, MODE_DISPLACEMENT}:
        if "mode_vector" in kwargs:
            kwargs["mode_displacement"] = params.displacement_step_angstrom
            if len(params.displacement_sign) == 1:
                kwargs["mode_displacement_sign"] = params.displacement_sign[0]
            else:
                metadata["displacement_sign"] = list(params.displacement_sign)
        else:
            metadata["mode_displacement"] = params.displacement_step_angstrom
            metadata["displacement_sign"] = list(params.displacement_sign)
    elif strategy == TIGHT_OPT_CALCHESS:
        kwargs["opt_level"] = "tight"
        kwargs["initial_hessian"] = "calculate"
    elif strategy == IRC_MIDPOINT_RECOVERY:
        metadata["irc_midpoint_reseed"] = True
    if include_metadata:
        kwargs["rescue_metadata"] = {
            "strategy": strategy,
            "supported_kwargs": _supported_rescue_kwargs(kwargs, base_kwargs),
            "unsupported_kwargs": metadata,
        }
    return kwargs


def _resolve_method_override(
    strategy: str,
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not overrides:
        return {}
    direct = overrides.get(strategy)
    if isinstance(direct, Mapping):
        return dict(direct)
    methods = overrides.get("methods")
    if isinstance(methods, Mapping):
        direct = methods.get(strategy)
        if isinstance(direct, Mapping):
            return dict(direct)
    refinement = overrides.get("refinement")
    if not isinstance(refinement, Mapping):
        return {}
    common = refinement.get("common")
    if not isinstance(common, Mapping):
        return {}
    rescue = common.get("rescue")
    if not isinstance(rescue, Mapping):
        return {}
    methods = rescue.get("methods")
    if not isinstance(methods, Mapping):
        return {}
    direct = methods.get(strategy)
    return dict(direct) if isinstance(direct, Mapping) else {}


def _unsupported_method_metadata(
    strategy: str,
    params: RescueMethodParams,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "max_cycles": params.max_cycles,
    }
    if strategy == TS_MODE_DIRECTED:
        metadata["mode_min_overlap"] = params.mode_min_overlap
        metadata["mode_min_overlap_margin"] = params.mode_min_overlap_margin
    if strategy == IRC_MIDPOINT_RECOVERY:
        metadata["irc_max_iter"] = params.irc_max_iter
        metadata["irc_direction"] = params.irc_direction
        metadata["shoulder_energy_window_kcal_mol"] = params.shoulder_energy_window_kcal_mol
    return metadata


def _supported_rescue_kwargs(
    resolved_kwargs: Mapping[str, object],
    base_kwargs: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in resolved_kwargs.items()
        if base_kwargs.get(key) != value and key != "rescue_metadata"
    }


def _as_strategy(strategy: str) -> RescueStrategy:
    if strategy not in _METHOD_PARAMS_DEFAULTS:
        raise KeyError(f"Unknown rescue strategy: {strategy}")
    return strategy


def _as_bool(value: object, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _as_int(value: object, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float, str)):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_optional_int(value: object, fallback: int | None) -> int | None:
    if value is None or str(value).strip().lower() in {"", "null", "none"}:
        return fallback
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float, str)):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    if not isinstance(value, (int, float, str)):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_optional_float(value: object, fallback: float | None) -> float | None:
    if value is None or str(value).strip().lower() in {"", "null", "none"}:
        return fallback
    if not isinstance(value, (int, float, str)):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_displacement_sign(
    value: object,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    if value is None:
        return fallback
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return fallback
        if "," in stripped:
            parts = [part.strip() for part in stripped.split(",") if part.strip()]
            return tuple(parts) or fallback
        return (stripped,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = [str(part).strip() for part in value if str(part).strip()]
        return tuple(parts) or fallback
    return fallback


_METHOD_PARAMS_DEFAULTS: dict[RescueStrategy, RescueMethodParams] = {
    FRESH_HESSIAN_RESTART: RescueMethodParams(
        method=FRESH_HESSIAN_RESTART,
        max_cycles=30,
        recalc_hess=5,
        calc_hess=True,
    ),
    FRESH_HESSIAN_MODE_MONITOR: RescueMethodParams(
        method=FRESH_HESSIAN_MODE_MONITOR,
        max_cycles=30,
        recalc_hess=5,
        calc_hess=True,
    ),
    TS_MODE_DIRECTED: RescueMethodParams(
        method=TS_MODE_DIRECTED,
        max_cycles=12,
        trust=0.15,
        mode_min_overlap=0.35,
        mode_min_overlap_margin=0.08,
    ),
    MODE_DISPLACEMENT: RescueMethodParams(
        method=MODE_DISPLACEMENT,
        max_cycles=30,
        displacement_step_angstrom=0.30,
    ),
    SADDLE_BREAK: RescueMethodParams(
        method=SADDLE_BREAK,
        max_cycles=30,
        displacement_step_angstrom=0.30,
    ),
    CALCALL_OPT: RescueMethodParams(
        method=CALCALL_OPT,
        max_cycles=30,
        recalc_hess=1,
    ),
    TIGHT_OPT_CALCHESS: RescueMethodParams(
        method=TIGHT_OPT_CALCHESS,
        max_cycles=30,
        calc_hess=True,
    ),
    IRC_MIDPOINT_RECOVERY: RescueMethodParams(
        method=IRC_MIDPOINT_RECOVERY,
        max_cycles=30,
        irc_max_iter=5,
        irc_direction="both",
        shoulder_energy_window_kcal_mol=2.0,
    ),
}


__all__ = [
    "CALCALL_OPT",
    "FAILURE_EXIT",
    "FRESH_HESSIAN_MODE_MONITOR",
    "FRESH_HESSIAN_RESTART",
    "IRC_MIDPOINT_RECOVERY",
    "MODE_DISPLACEMENT",
    "RescueAction",
    "RescueMethodParams",
    "RescuePlan",
    "SADDLE_BREAK",
    "TIGHT_OPT_CALCHESS",
    "TS_MODE_DIRECTED",
    "apply_rescue_kwargs",
    "build_rescue_plan",
    "method_params",
]
