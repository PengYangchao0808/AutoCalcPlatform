"""ResolvedCalculationSpec — the single source of parameter-resolution rules.

Moved from ``acp.catalog._clamp_to_functional`` (plan todo 7): the actual
modification semantics for basis/dispersion/RI/aux **and** the one priority
table for "explicit value / task options / run configuration / method
default" live here.  ``acp.catalog`` keeps only historical field migration
(``aux_basis`` → ``aux_j_basis``/``aux_c_basis``) and warning surfacing; the
UI summary, execution input, provenance and cache signature all derive from
the SAME :class:`ResolvedCalculationSpec` (never a second rule).

Boundary (plan todo 7): ACP decides which workflow level/profile applies;
CCCP interprets that level's scientific parameters, fills method-inherent
defaults and renders inputs.  This module reads NOTHING beyond its arguments
plus :mod:`cccp.qc.method_meta` / :mod:`cccp.qc.keyword_registry` — once a
context is passed in explicitly, execution never re-reads global config.

The three mandated tables:

1. :data:`SOURCE_PRIORITY` / :data:`SOURCE_PRIORITY_TABLE` — where a field's
   requested value comes from (highest → lowest).
2. :data:`NULL_SEMANTICS` — what ``omitted`` / ``None`` / ``""`` / ``"none"``
   mean (inherit / inherit / clear / off).
3. :data:`CONFLICT_RULES` — what happens on a conflict (reject / keep the
   clamp / warn).

Every resolution records ``requested`` / ``effective`` / ``source`` /
``adjustment_reason`` so consumers can audit each field.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from cccp.qc.method_meta import (
    AUX_C_BASIS_DEFAULT,
    AUX_J_BASIS_DEFAULT,
    aux_basis_default,
    aux_basis_options,
    functional_options,
    method_meta,
)

__all__ = [
    "AUX_C_BASIS_DEFAULT",
    "AUX_J_BASIS_DEFAULT",
    "CLAMP_FIELDS",
    "CONFLICT_RULES",
    "FieldResolution",
    "NULL_SEMANTICS",
    "RESOLVED_FIELDS",
    "SOURCE_PRIORITY",
    "SOURCE_PRIORITY_TABLE",
    "ResolvedCalculationSpec",
    "clamp_calculation_fields",
    "forced_ri_clears",
    "method_field_default",
    "resolve_calculation_spec",
]

# ── Table ①: source priority ─────────────────────────────────────────────
# "显式值 / 任务 options / 配置默认 / 方法默认" — the ONLY priority order
# (plan wording "请求字段 / 工作流选定 level / 运行配置 / 方法固有规则" maps
# 1:1 onto the same four entries).  A field's requested value is taken from
# the first source that provides one (omitted/None never shadow a lower
# source).
SOURCE_PRIORITY_TABLE: dict[str, str] = {
    "explicit": "request field / 显式值 — provided directly on the request",
    "task_options": "workflow-selected level / 任务 options — chosen by the workflow",
    "run_config": "run configuration / 配置默认 — caller-provided run config defaults",
    "method_default": "method-inherent rules / 方法固有规则 — METHOD_META defaults & forced rules",
}
SOURCE_PRIORITY: tuple[str, ...] = tuple(SOURCE_PRIORITY_TABLE)

# ── Table ②: null semantics ──────────────────────────────────────────────
# omitted/None inherit the next source; "" clears (effective "", never
# inherits); "none" is an explicit OFF token where the domain has one
# (dispersion "none" / solvent_model "none" / RI "none") and a plain value
# elsewhere; "__custom__" is the UI custom placeholder and is never clamped.
NULL_SEMANTICS: dict[str, str] = {
    "omitted": "inherit",
    "None": "inherit",
    '""': "clear",
    '"none"': "off",
    '"__custom__"': "passthrough",
}

# ── Table ③: conflict handling ───────────────────────────────────────────
# 拒绝 (reject) / 保持 clamp (clamp) / 警告 (warn).  The reject/warn entries
# are enforced by ``cccp.qc.keyword_registry.resolve`` (the single keyword
# authority); the clamp entries are enforced here (the single parameter
# modification rule).
CONFLICT_RULES: dict[str, str] = {
    # clamp family — kept semantics of acp.catalog._clamp_to_functional
    "case_mismatch": "clamp",
    "outside_allowed_set": "clamp",
    "empty_allowed_set": "clamp",
    "ri_support_forced_clear": "clamp",
    "aux_outside_allowed_set": "clamp",
    # reject family — cccp.qc.keyword_registry raises KeywordValueError
    "unknown_enum_value": "reject",
    "gfn_solvent_policy_violation": "reject",
    # warn family — registry strips the value with a warning, never emits it
    "family_inapplicable_field": "warn",
}

# Fields whose values the clamp rule may modify.
CLAMP_FIELDS: tuple[str, ...] = (
    "basis",
    "dispersion",
    "ri_approximation",
    "aux_j_basis",
    "aux_c_basis",
)

# Every field ResolvedCalculationSpec can resolve.  solvent/grid/scf are
# included so MethodSpec-level and OptimizeOptions-level values share ONE
# conflict rule (SOURCE_PRIORITY: the request field wins over task options);
# they are never modified here.
RESOLVED_FIELDS: tuple[str, ...] = CLAMP_FIELDS + (
    "solvent",
    "solvent_model",
    "grid",
    "scf_convergence",
    "scf_strategy",
)


def forced_ri_clears(ri_support: str) -> dict[str, str]:
    """Method-owned RI/aux fields whose user values are discarded.

    ``composite`` (3c / GFN) owns RI + auxJ + auxC; ``automatic``
    (DLPNO-CCSD(T)) owns RI + auxJ only — a user ``aux_c_basis`` (e.g. from
    legacy ``aux_basis`` migration) survives the clamp and is honoured at
    render time.  Historical ``_clamp_to_functional`` behaviour, verbatim.
    """
    if ri_support == "composite":
        return {"ri_approximation": "none", "aux_j_basis": "", "aux_c_basis": ""}
    if ri_support == "automatic":
        return {"ri_approximation": "none", "aux_j_basis": ""}
    return {}


def method_field_default(
    field_name: str,
    functional: str | None,
    basis: str | None = None,
) -> str | None:
    """Method-inherent default for *field_name* (``None`` = no opinion).

    Rules (moved from ``acp.catalog._resolve_field_default``'s method-opinion
    branch — single source):

    * ``basis`` / ``dispersion``: METHOD_META ``default_basis`` /
      ``default_dispersion``.
    * ``ri_approximation``: ``"none"`` when the method owns its RI layer
      (composite/automatic), else no opinion (the UI/field default applies).
    * ``aux_j_basis`` / ``aux_c_basis``: cleared for composite/automatic;
      otherwise the basis-derived fitting basis, falling back to
      :data:`AUX_J_BASIS_DEFAULT` / :data:`AUX_C_BASIS_DEFAULT`.
    """
    meta = method_meta(functional)
    if meta is None:
        return None
    ri_support = str(meta.get("ri_support", "user"))
    if ri_support in ("composite", "automatic"):
        if field_name == "ri_approximation":
            return "none"
        if field_name in ("aux_j_basis", "aux_c_basis"):
            return ""
    if field_name == "basis":
        return meta.get("default_basis")
    if field_name == "dispersion":
        return meta.get("default_dispersion")
    if field_name in ("aux_j_basis", "aux_c_basis"):
        derived = aux_basis_default(field_name, functional, basis)
        if derived is not None:
            return derived
        return AUX_J_BASIS_DEFAULT if field_name == "aux_j_basis" else AUX_C_BASIS_DEFAULT
    return None


@dataclass(frozen=True)
class FieldResolution:
    """Resolution record for one field — requested vs effective."""

    field: str
    requested: Any
    effective: Any
    source: str
    adjustment_reason: str | None = None

    @property
    def changed(self) -> bool:
        """True when the effective value differs from the requested one."""
        return self.requested != self.effective

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "requested": self.requested,
            "effective": self.effective,
            "source": self.source,
            "adjustment_reason": self.adjustment_reason,
        }


@dataclass(frozen=True)
class ResolvedCalculationSpec:
    """Resolved scientific parameters for one method, with provenance.

    UI summary / execution input / provenance / cache signature must all be
    derived from the same ``resolutions`` — see :meth:`to_summary`,
    :meth:`effective_values`, :meth:`to_provenance`, :meth:`cache_signature`.
    """

    method: str | None
    resolutions: tuple[FieldResolution, ...] = field(default=())

    def __iter__(self):
        return iter(self.resolutions)

    def __len__(self) -> int:
        return len(self.resolutions)

    def __getitem__(self, field_name: str) -> FieldResolution:
        for res in self.resolutions:
            if res.field == field_name:
                return res
        raise KeyError(field_name)

    def get(self, field_name: str) -> FieldResolution | None:
        for res in self.resolutions:
            if res.field == field_name:
                return res
        return None

    @property
    def warnings(self) -> tuple[str, ...]:
        """Human-readable adjustment notes (ACP surfaces these to users)."""
        out: list[str] = []
        for res in self.resolutions:
            if res.adjustment_reason:
                out.append(
                    f"{res.field}: {res.requested!r} -> {res.effective!r} ({res.adjustment_reason})"
                )
        return tuple(out)

    def effective_values(self) -> dict[str, Any]:
        """Execution input: the effective value of every resolved field."""
        return {res.field: res.effective for res in self.resolutions}

    def to_summary(self) -> dict[str, dict[str, Any]]:
        """UI summary: per-field provenance, same source as execution input."""
        return {res.field: res.to_dict() for res in self.resolutions}

    def to_provenance(self) -> dict[str, Any]:
        """Provenance record: method + every field's source/adjustment."""
        return {
            "method": self.method,
            "source_priority": list(SOURCE_PRIORITY),
            "fields": self.to_summary(),
        }

    def cache_signature(self) -> dict[str, Any]:
        """Cache identity payload derived from the same resolutions."""
        return {
            "method": self.method,
            "effective": self.effective_values(),
        }


def _select_source(
    field_name: str,
    layers: Mapping[str, Mapping[str, Any] | None],
) -> tuple[Any, str]:
    """Apply Table ① + Table ②: pick the requested value and its source."""
    for source in SOURCE_PRIORITY[:-1]:
        layer = layers.get(source)
        if layer is not None and field_name in layer and layer[field_name] is not None:
            return layer[field_name], source
    return None, "absent"


def resolve_calculation_spec(
    method: str | None,
    *,
    explicit: Mapping[str, Any] | None = None,
    task_options: Mapping[str, Any] | None = None,
    run_config: Mapping[str, Any] | None = None,
    fields: Iterable[str] | None = None,
    apply_method_defaults: bool = True,
) -> ResolvedCalculationSpec:
    """Resolve scientific parameters for *method* with full provenance.

    Layers (Table ①): ``explicit`` (request field) beats ``task_options``
    (workflow-selected level) beats ``run_config`` (configuration defaults);
    the ``method_default`` layer (method-inherent rules) applies only when
    *apply_method_defaults* is true and no higher source provided the field.

    Modification rules (Table ③, clamp family — the moved
    ``_clamp_to_functional`` semantics):

    * ``basis`` / ``dispersion``: case-insensitive hit in the method's
      allowed set is canonicalised (``case_mismatch``); a miss clamps to the
      first allowed value (``outside_allowed_set``); an empty allowed set
      forces ``""`` even over ``None`` (``empty_allowed_set``).  ``""`` and
      ``"__custom__"`` pass through untouched (Table ②).
    * RI/aux owned by the method (composite/automatic) are forced to
      ``none``/``""`` (``ri_support_forced_clear``).
    * ``aux_j_basis`` / ``aux_c_basis`` for user-RI methods: a value outside
      the derived option set (case-SENSITIVE membership) is replaced by the
      derived default (``aux_outside_allowed_set``).

    Args:
        method: Method name (case-insensitive); falsy/unknown methods get no
            method rules and no clamp (values pass through).
        explicit: Explicitly requested field values (highest priority).
        task_options: Workflow-selected level values.
        run_config: Caller-provided run-configuration defaults.
        fields: Field scope; defaults to every :data:`RESOLVED_FIELDS` entry.
            Method-owned fields are always in scope.
        apply_method_defaults: Participate in the method-default layer.
            ``False`` reproduces the historical clamp-only behaviour used by
            the ``acp.catalog`` compatibility adapter.

    Returns:
        A :class:`ResolvedCalculationSpec` with per-field
        ``requested``/``effective``/``source``/``adjustment_reason``.
    """
    layers: dict[str, Mapping[str, Any] | None] = {
        "explicit": explicit,
        "task_options": task_options,
        "run_config": run_config,
    }
    scope: set[str] = set(fields) if fields is not None else set(RESOLVED_FIELDS)
    meta = method_meta(method)
    ri_support = str(meta.get("ri_support", "user")) if meta is not None else "user"
    forced = forced_ri_clears(ri_support) if meta is not None else {}
    scope |= set(forced)
    ordered_scope = [f for f in RESOLVED_FIELDS if f in scope]
    options = functional_options(method) if meta is not None else None

    # Effective basis feeds the aux derivation (post basis-clamp, matching
    # the historical ``level.get("basis", "")`` probe).
    if "basis" in ordered_scope:
        basis_eff: Any = _select_source("basis", layers)[0]
    else:
        basis_eff = ""

    resolutions: list[FieldResolution] = []
    for field_name in ordered_scope:
        requested, source = _select_source(field_name, layers)
        if source == "absent" and apply_method_defaults and meta is not None:
            default = method_field_default(field_name, method, _as_str(basis_eff))
            if default is not None:
                requested, source = default, "method_default"
        effective = requested
        reason: str | None = None

        # Method-owned fields: user values are discarded (clamp rule).
        if field_name in forced:
            effective = forced[field_name]
            source = "method_default"
            if effective != requested:
                reason = "ri_support_forced_clear"
        elif field_name in ("basis", "dispersion") and options is not None:
            effective, reason = _clamp_enum_like(requested, options.get(field_name, []))
        elif field_name in ("aux_j_basis", "aux_c_basis") and meta is not None:
            if ri_support == "user" and requested not in (None, ""):
                effective, reason = _clamp_aux(field_name, requested, method, basis_eff)

        if field_name == "basis":
            basis_eff = effective
        resolutions.append(
            FieldResolution(
                field=field_name,
                requested=requested,
                effective=effective,
                source=source,
                adjustment_reason=reason,
            )
        )
    return ResolvedCalculationSpec(method=method, resolutions=tuple(resolutions))


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _clamp_enum_like(requested: Any, allowed: list[str]) -> tuple[Any, str | None]:
    """The moved basis/dispersion clamp rule (Table ③, clamp family)."""
    if not allowed:
        return "", ("empty_allowed_set" if requested != "" else None)
    if not requested or requested == "__custom__":
        return requested, None
    allowed_lower = [str(a).lower() for a in allowed]
    try:
        idx = allowed_lower.index(str(requested).lower())
    except ValueError:
        return allowed[0], "outside_allowed_set"
    canonical = allowed[idx]
    if canonical != requested:
        return canonical, "case_mismatch"
    return canonical, None


def _clamp_aux(
    field_name: str,
    requested: Any,
    method: str | None,
    basis_eff: Any,
) -> tuple[Any, str | None]:
    """The moved aux clamp rule (case-SENSITIVE membership, historical)."""
    basis = _as_str(basis_eff)
    allowed = aux_basis_options(field_name, method, basis)
    if requested in allowed:
        return requested, None
    replacement = method_field_default(field_name, method, basis)
    if replacement is None:
        replacement = AUX_J_BASIS_DEFAULT if field_name == "aux_j_basis" else AUX_C_BASIS_DEFAULT
    return replacement, "aux_outside_allowed_set"


def clamp_calculation_fields(
    method: str | None,
    fields: Mapping[str, Any],
) -> ResolvedCalculationSpec:
    """Clamp-only semantics of ``acp.catalog._clamp_to_functional`` (moved).

    Resolves exactly the fields the historical clamp inspected (the
    :data:`CLAMP_FIELDS` entries present in *fields*, plus the method-owned
    forced fields) WITHOUT filling method defaults — writing every returned
    ``effective`` back reproduces the historical in-place mutation
    byte-for-byte.
    """
    if not method:
        return ResolvedCalculationSpec(method=method, resolutions=())
    scope = [f for f in CLAMP_FIELDS if f in fields]
    return resolve_calculation_spec(
        method,
        explicit=fields,
        fields=scope,
        apply_method_defaults=False,
    )
