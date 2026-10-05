"""Single typed resolver: catalog/GUI NMR ``method`` payload → :class:`NmrMethodConfig`.

Plan todo 17 / gap G06: the scheduler and the frontend must stop guessing
NMR parameters from ad-hoc flat keys (``nmr_method_flags`` read
``nmr_method/nmr_basis`` while the wizard submits ``functional/basis`` →
empty argv).  ``resolve_nmr_method`` is THE single source of truth: a pure
function (no I/O, no RDKit, no ``acp.workflows`` import) consumed by the
CLI handler (T18), ``jobs.nmr_method_flags`` (T19), the local/remote argv
builders (T20) and the effective-config record (T21).

Precedence per field — first *present* source wins (present = not ``None``
and not an empty/whitespace string); ``config`` is the merged cccp config
mapping (``load_config()`` output) or ``None``:

================================  ==================================================
Field                             Sources (in order)
================================  ==================================================
nmr_method                        ``method.nmr_method`` → ``method.functional`` →
                                  ``levels.giaoa.functional`` →
                                  ``config theory.nmr.method`` → ``"mPW1PW91"``
nmr_basis                         ``method.nmr_basis`` → ``method.basis`` →
                                  ``levels.giaoa.basis`` →
                                  ``config theory.nmr.basis`` → ``"6-311G(d)"``
                                  (fixed-basis methods clamp to the method's
                                  declared basis instead of the config value)
solvent_model                     ``method.solvent_model`` →
                                  ``levels.giaoa.solvent_model`` →
                                  ``config theory.nmr.solvent_model`` → ``"cpcm"``
solvent                           gas phase (resolved model ``"none"``) → ``""``
                                  always; otherwise ``method.solvent`` →
                                  ``levels.giaoa.solvent`` →
                                  ``config theory.nmr.solvent`` → ``"chloroform"``
nuclei                            ``method.nuclei`` → ``levels.giaoa.nuclei`` →
                                  ``("1H", "13C")``
boltzmann_temp                    ``method.boltzmann_temp`` →
                                  ``levels.giaoa.boltzmann_temp`` →
                                  ``config nmr.temperature_k`` → ``298.15``
tms_1h / tms_13c                  ``method.tms_shielding_h/c`` →
                                  ``levels.giaoa.…`` → ``config
                                  nmr.references.1H/13C`` → ``None`` (the
                                  workflow then does the solvent-aware Goodman
                                  TMSdata lookup)
ewin (kcal/mol)                   ``method.ewin`` → ``levels.conformer.ewin`` →
                                  ``config censo.ewin`` → ``6.0``
max_conformers                    ``method.max_conformers`` →
                                  ``config nmr.max_conformers`` → ``10``
error_model                       ``method.error_model`` → ``"goodman-legacy"``
conformer_preset                  ``method.conformer_preset`` → ``method.preset``
                                  → ``method.profile_id`` (first CENSO-legal
                                  spelling wins, else default) →
                                  ``"censo-light"``
================================  ==================================================

The level keys are read directly so the frontend hoist
(``frontend/ACP_Workbench_v2.html`` ~30776-30785) is NOT required — top-level
keys are only the legacy/hand-built payload surface.

Gas phase: ``solvent_model`` is case-normalised to lowercase and an explicit
``"none"`` is preserved as ``"none"`` — never replaced by the ``cpcm`` /
``chloroform`` defaults; in that case ``solvent`` is forced to ``""``
(catalog ``_normalize_solvent`` semantics) so the effective config stays gas
phase end-to-end.

Rejections — everything below raises :class:`NmrMethodConfigError` (a
``ValueError`` subclass); nothing mismatched silently executes defaults:

1. ``method`` / ``config`` not a mapping; ``levels`` present but not a
   mapping (or ``levels.giaoa`` / ``levels.conformer`` not a mapping).
2. ``schema_id`` present and ≠ ``"nmr"``.
3. Non-empty ``levels`` without a ``giaoa`` level (payload of another
   workflow — the NMR schema always carries the required ``giaoa`` level).
4. **No NMR/GIAO-capable method metadata**: the resolved functional has no
   ``acp.catalog.METHOD_META`` entry — keyed on METHOD_META membership
   (METHOD_META has no ``nmr`` capability key).
5. **Platform NMR policy**: ``cccp.qc.keyword_registry.calculation_policy(
   "nmr", family, implementation)`` returns ``allowed=False`` (keyed on the
   registry family/implementation derived from the functional; flagship
   rule: GFN+NMR is closed while ``GFN_NMR_DEFAULT_ALLOWED`` is ``False``).
6. **functional/basis pair**: an explicit basis outside
   ``METHOD_META[functional]["basis"]`` for a fixed-basis method (e.g.
   ``r2SCAN-3c`` only accepts ``def2-mTZVPP``); an omitted basis for such a
   method is clamped to its ``default_basis`` (catalog
   ``_clamp_to_functional`` semantics); ``basis: ()`` (GFN) accepts none.
7. ``solvent_model`` outside the ORCA options of
   ``FIELD_DEFINITIONS["solvent_model"]`` (``none``/``CPCM``/``SMD``,
   case-folded) — the GIAO level runs on ORCA only.
8. Explicit non-numeric / non-finite / boolean ``boltzmann_temp`` /
   ``tms_*`` / ``ewin`` / ``max_conformers`` (absent falls through;
   non-positive falls through too, mirroring the workflow ``or``-chains and
   the ``> 0`` gate in ``resolve_crest_ewin``).

NOT checked here (deliberately): the Goodman error-model ↔ level binding
(``acp.nmr.error_model.validate_error_model_binding`` runs at workflow
build time) and nuclei membership (unknown nuclei pass through; known ones
are catalog-cased).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp.catalog import FIELD_DEFINITIONS, METHOD_META
from cccp.qc.keyword_registry import (
    KeywordValueError,
    calculation_policy,
    method_family,
    resolve_implementation,
)

logger = logging.getLogger(__name__)

__all__ = ["NmrMethodConfig", "NmrMethodConfigError", "resolve_nmr_method"]

# Defaults — pinned to the workflow's current behaviour
# (workflows/nmr.py::_resolve_config /::_build_nmr_config) and to
# cccp.config._get_default_config() (theory.nmr / nmr / censo sections).
# Single declaration: consumers must not re-derive these values.
_DEFAULT_NMR_METHOD = "mPW1PW91"
_DEFAULT_NMR_BASIS = "6-311G(d)"
_DEFAULT_SOLVENT_MODEL = "cpcm"
_DEFAULT_SOLVENT = "chloroform"
_GAS_PHASE_SOLVENT = ""
_DEFAULT_NUCLEI: tuple[str, ...] = ("1H", "13C")
_DEFAULT_BOLTZMANN_TEMP = 298.15
_DEFAULT_EWIN = 6.0
_DEFAULT_MAX_CONFORMERS = 10
_DEFAULT_ERROR_MODEL = "goodman-legacy"
_DEFAULT_CONFORMER_PRESET = "censo-light"

# CENSO preset vocabulary — keys of cccp.qc.interfaces.censo.CENSO_PRESETS,
# mirrored by acp.scheduler.jobs._CENSO_PRESETS (same three spellings, also
# duplicated in workflows/energy.py::_ENERGY_PRESETS).  Re-declared HERE
# deliberately: importing acp.scheduler.jobs would be circular (T19 makes
# jobs import this module) and importing the subprocess interface layer
# would defeat the "pure resolver" contract.
_CONFORMER_PRESETS: tuple[str, ...] = ("censo-light", "censo-default", "censo-zero")

# Catalog vocabularies (single source: acp.catalog.FIELD_DEFINITIONS).
_SOLVENT_MODEL_OPTIONS: tuple[str, ...] = tuple(
    str(option).strip().lower()
    for option in FIELD_DEFINITIONS["solvent_model"]["per_backend"]["orca"]
)
_NUCLEUS_OPTIONS: tuple[str, ...] = tuple(
    str(option).strip() for option in FIELD_DEFINITIONS["nuclei"]["options"]
)


class NmrMethodConfigError(ValueError):
    """Typed rejection — the method payload cannot become a valid NMR config."""


@dataclass(frozen=True)
class NmrMethodConfig:
    """Effective NMR parameters (G06) — build only via :func:`resolve_nmr_method`."""

    nmr_method: str
    nmr_basis: str
    solvent_model: str
    solvent: str
    nuclei: tuple[str, ...]
    boltzmann_temp: float
    tms_1h: float | None
    tms_13c: float | None
    ewin: float
    max_conformers: int
    error_model: str
    conformer_preset: str

    def __post_init__(self) -> None:
        # Coerce so ``NmrMethodConfig(**cfg.to_dict())`` round-trips.
        object.__setattr__(self, "nuclei", tuple(str(n) for n in self.nuclei))
        object.__setattr__(self, "solvent_model", str(self.solvent_model).strip().lower())
        object.__setattr__(self, "boltzmann_temp", float(self.boltzmann_temp))
        object.__setattr__(self, "ewin", float(self.ewin))
        object.__setattr__(self, "max_conformers", int(self.max_conformers))

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance view of every field (``nuclei`` → list)."""
        return {
            "nmr_method": self.nmr_method,
            "nmr_basis": self.nmr_basis,
            "solvent_model": self.solvent_model,
            "solvent": self.solvent,
            "nuclei": list(self.nuclei),
            "boltzmann_temp": self.boltzmann_temp,
            "tms_1h": self.tms_1h,
            "tms_13c": self.tms_13c,
            "ewin": self.ewin,
            "max_conformers": self.max_conformers,
            "error_model": self.error_model,
            "conformer_preset": self.conformer_preset,
        }


# ---------------------------------------------------------------------------
# Presence / coercion helpers
# ---------------------------------------------------------------------------


def _present(value: Any) -> bool:
    """True when *value* carries data (``None`` / empty / whitespace = absent)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _first(*values: Any) -> Any:
    """First present value (see :func:`_present`), else ``None``."""
    for value in values:
        if _present(value):
            return value
    return None


def _section(config: Mapping[str, Any] | None, *path: str) -> Mapping[str, Any]:
    """Walk *path* through nested config mappings (missing → ``{}``)."""
    node: Any = config
    for key in path:
        if not isinstance(node, Mapping):
            return {}
        node = node.get(key)
    return node if isinstance(node, Mapping) else {}


def _resolve_float(
    candidates: tuple[Any, ...],
    *,
    default: float,
    positive: bool,
    field: str,
    errors: list[str],
) -> float:
    """First present, coercible candidate; non-positive skips to the next one.

    Explicitly non-numeric / non-finite / boolean values are REJECTED (the
    workflow would crash on ``float(...)`` anyway) — they never silently
    become the default.  Non-positive values fall through, mirroring the
    ``or``-chains in ``_build_nmr_config`` and the ``> 0`` gate in
    ``resolve_crest_ewin``.
    """
    for raw in candidates:
        if not _present(raw):
            continue
        if isinstance(raw, bool):
            errors.append(f"{field}: boolean {raw!r} is not a number")
            return default
        try:
            number = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{field}: {raw!r} is not numeric")
            return default
        if not math.isfinite(number):
            errors.append(f"{field}: {raw!r} is not finite")
            return default
        if positive and number <= 0:
            continue  # fall through to the next source
        return number
    return default


def _resolve_optional_float(
    candidates: tuple[Any, ...],
    *,
    field: str,
    errors: list[str],
) -> float | None:
    """Like :func:`_resolve_float` but no default — ``None`` means table lookup."""
    for raw in candidates:
        if not _present(raw):
            continue
        if isinstance(raw, bool):
            errors.append(f"{field}: boolean {raw!r} is not a number")
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{field}: {raw!r} is not numeric")
            return None
        if not math.isfinite(number):
            errors.append(f"{field}: {raw!r} is not finite")
            return None
        return number
    return None


def _resolve_positive_int(
    candidates: tuple[Any, ...],
    *,
    default: int,
    field: str,
    errors: list[str],
) -> int:
    """First positive, integral-valued candidate (``int()`` truncation like the workflow)."""
    for raw in candidates:
        if not _present(raw):
            continue
        if isinstance(raw, bool):
            errors.append(f"{field}: boolean {raw!r} is not a number")
            return default
        try:
            number = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{field}: {raw!r} is not numeric")
            return default
        if not math.isfinite(number):
            errors.append(f"{field}: {raw!r} is not finite")
            return default
        if number <= 0:
            continue  # ``or``-chain fall-through (``max_conformers or 10``)
        return int(number)
    return default


def _canonical_functional(name: str) -> str | None:
    """METHOD_META key spelling for *name* (case-insensitive), else ``None``."""
    wanted = name.strip().lower()
    for key in METHOD_META:
        if key.lower() == wanted:
            return key
    return None


def _normalize_basis(basis: str) -> str:
    """Case/space-insensitive basis key (same rule as error_model._basis_equal)."""
    return basis.strip().lower().replace(" ", "")


def _resolve_solvent_model(value: Any, errors: list[str]) -> str | None:
    """Lowercase ORCA solvent model, or ``None`` when absent/invalid (error recorded)."""
    if not _present(value):
        return None
    if not isinstance(value, str):
        errors.append(f"solvent_model: expected a string, got {type(value).__name__}")
        return None
    lowered = value.strip().lower()
    if lowered not in _SOLVENT_MODEL_OPTIONS:
        errors.append(
            f"solvent_model: {value!r} is not a valid ORCA solvation model "
            f"(options: {', '.join(_SOLVENT_MODEL_OPTIONS)})"
        )
        return None
    return lowered


def _resolve_nuclei(value: Any, errors: list[str]) -> tuple[str, ...] | None:
    """Normalised nuclei tuple (known options re-cased), ``None`` when absent."""
    if not _present(value):
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
        items = [part for part in items if part]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        errors.append(
            f"nuclei: expected a list or comma-separated string, got {type(value).__name__}"
        )
        return None
    if not items:
        return None
    canon: list[str] = []
    for item in items:
        match = next((opt for opt in _NUCLEUS_OPTIONS if opt.lower() == item.lower()), None)
        # Unknown nuclei pass through unchanged (ORCA target list); known
        # ones are canonicalised to the catalog spelling ("1h" → "1H").
        canon.append(match if match is not None else item)
    return tuple(canon)


def _resolve_conformer_preset(method: Mapping[str, Any]) -> str:
    """First present preset key, validated against the CENSO vocabulary.

    Mirrors ``jobs.censo_preset_from_method`` semantics: unknown spellings
    (e.g. the wizard's ``nmr-goodman`` profile id) resolve to the default so
    the CLI default applies — never a hard failure.
    """
    raw = _first(
        method.get("conformer_preset"),
        method.get("preset"),
        method.get("profile_id"),
    )
    if raw is None:
        return _DEFAULT_CONFORMER_PRESET
    candidate = str(raw).strip().lower()
    if candidate in _CONFORMER_PRESETS:
        return candidate
    logger.debug(
        "resolve_nmr_method: preset %r is not a CENSO preset; using %s",
        raw,
        _DEFAULT_CONFORMER_PRESET,
    )
    return _DEFAULT_CONFORMER_PRESET


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_nmr_method(
    method: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> NmrMethodConfig:
    """Resolve a catalog/GUI NMR *method* payload (+ merged *config*) to typed state.

    Args:
        method: Job/wizard method mapping (may be flat legacy keys, the
            frontend's ``{schema_id, profile_id, levels}`` shape, or both).
        config: Merged cccp config mapping (``load_config()`` output) or ``None``.

    Returns:
        Frozen :class:`NmrMethodConfig` with every effective value resolved.

    Raises:
        NmrMethodConfigError: The payload does not match the NMR model
            requirements (see module docstring §Rejections) or carries
            uncoercible values.  Subclass of ``ValueError``.
    """
    if not isinstance(method, Mapping):
        raise NmrMethodConfigError(f"method must be a mapping, got {type(method).__name__}")
    if config is not None and not isinstance(config, Mapping):
        raise NmrMethodConfigError(f"config must be a mapping or None, got {type(config).__name__}")
    errors: list[str] = []

    # ── structural shape: payload of another workflow rejected ───────────
    schema_id = method.get("schema_id")
    if _present(schema_id) and str(schema_id).strip().lower() != "nmr":
        errors.append(f"schema_id {schema_id!r} does not belong to the nmr workflow")

    levels: Mapping[str, Any] = {}
    levels_raw = method.get("levels")
    if levels_raw is not None:
        if not isinstance(levels_raw, Mapping):
            errors.append(f"levels must be a mapping, got {type(levels_raw).__name__}")
        elif levels_raw:
            levels = levels_raw
            if "giaoa" not in levels:
                errors.append("levels has no 'giaoa' level — not an NMR method payload")

    def _level(level_id: str) -> Mapping[str, Any]:
        raw = levels.get(level_id)
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            errors.append(f"levels.{level_id} must be a mapping, got {type(raw).__name__}")
            return {}
        return raw

    giaoa = _level("giaoa")
    conformer_level = _level("conformer")

    theory_nmr = _section(config, "theory", "nmr")
    nmr_section = _section(config, "nmr")
    refs = _section(config, "nmr", "references")
    censo_section = _section(config, "censo")

    # ── functional → nmr_method (+ capability gates) ─────────────────────
    raw_method = _first(
        method.get("nmr_method"),
        method.get("functional"),
        giaoa.get("functional"),
    )
    if raw_method is None:
        raw_method = theory_nmr.get("method")
    if not _present(raw_method):
        raw_method = _DEFAULT_NMR_METHOD
    if not isinstance(raw_method, str):
        errors.append(f"functional: expected a string, got {type(raw_method).__name__}")
        raw_method = _DEFAULT_NMR_METHOD

    canonical = _canonical_functional(raw_method)
    meta: Mapping[str, Any] | None = None
    if canonical is None:
        errors.append(
            f"functional {raw_method.strip()!r} is not declared in METHOD_META — "
            "no NMR/GIAO-capable method metadata for this method"
        )
        nmr_method = raw_method.strip()
    else:
        nmr_method = canonical
        meta = METHOD_META[canonical]

        # Platform NMR policy (keyword registry) — the NMR/GIAO capability
        # gate; METHOD_META itself carries no ``nmr`` capability key.
        family = method_family(nmr_method)
        try:
            implementation = resolve_implementation(nmr_method, engine="orca")
            decision = calculation_policy("nmr", family=family, implementation=implementation)
        except KeywordValueError as exc:
            errors.append(f"functional {nmr_method!r}: {exc}")
        else:
            if not decision.allowed:
                errors.append(f"functional {nmr_method!r} rejected for NMR: {decision.reason}")

    # ── basis → nmr_basis (METHOD_META basis-pair check) ─────────────────
    explicit_basis = _first(
        method.get("nmr_basis"),
        method.get("basis"),
        giaoa.get("basis"),
    )
    if explicit_basis is not None and not isinstance(explicit_basis, str):
        errors.append(f"basis: expected a string, got {type(explicit_basis).__name__}")
        explicit_basis = None

    basis_spec = meta.get("basis") if meta is not None else None
    if isinstance(basis_spec, tuple):
        allowed = tuple(str(b) for b in basis_spec)
        if not allowed:
            # GFN-style entry (``basis: ()``): the method carries no basis at
            # all and cannot back a GIAO level.
            errors.append(
                f"functional {nmr_method!r} declares no basis set — it cannot back a GIAO level"
            )
            nmr_basis = _DEFAULT_NMR_BASIS
        elif _present(explicit_basis):
            wanted = _normalize_basis(str(explicit_basis))
            if wanted not in {_normalize_basis(b) for b in allowed}:
                errors.append(
                    f"functional/basis pair rejected: {nmr_method} accepts "
                    f"{', '.join(allowed)} (got {explicit_basis!r})"
                )
            nmr_basis = str(explicit_basis).strip()
        else:
            # Fixed-basis method, no explicit basis: clamp to the method's
            # declared basis (catalog _clamp_to_functional semantics) — a
            # config-level basis belongs to a different level of theory.
            nmr_basis = str(meta.get("default_basis") or allowed[0])
            config_basis = theory_nmr.get("basis")
            if _present(config_basis) and _normalize_basis(str(config_basis)) != _normalize_basis(
                nmr_basis
            ):
                logger.debug(
                    "resolve_nmr_method: config theory.nmr.basis=%r inapplicable to "
                    "fixed-basis %s; using %s",
                    config_basis,
                    nmr_method,
                    nmr_basis,
                )
    else:
        # Catalog-reference basis (any basis set) — workflow chain.
        candidate = _first(explicit_basis, theory_nmr.get("basis"))
        if candidate is None:
            nmr_basis = _DEFAULT_NMR_BASIS
        elif not isinstance(candidate, str):
            errors.append(f"basis: expected a string, got {type(candidate).__name__}")
            nmr_basis = _DEFAULT_NMR_BASIS
        else:
            nmr_basis = candidate.strip()

    # ── solvent_model (gas phase preserved verbatim) ─────────────────────
    raw_solvent_model = _first(
        method.get("solvent_model"),
        giaoa.get("solvent_model"),
    )
    if raw_solvent_model is None:
        raw_solvent_model = theory_nmr.get("solvent_model")
    solvent_model = _resolve_solvent_model(raw_solvent_model, errors)
    if solvent_model is None:
        solvent_model = _DEFAULT_SOLVENT_MODEL

    # ── solvent (gas phase never becomes chloroform) ─────────────────────
    if solvent_model == "none":
        # Explicit "none" wins over every solvent default: clear the name
        # (catalog _normalize_solvent semantics) so the TMS lookup keys on
        # gas phase downstream — the config default stays gas phase.
        solvent = _GAS_PHASE_SOLVENT
    else:
        raw_solvent = _first(method.get("solvent"), giaoa.get("solvent"))
        if raw_solvent is None:
            raw_solvent = theory_nmr.get("solvent")
        if not _present(raw_solvent):
            solvent = _DEFAULT_SOLVENT
        elif not isinstance(raw_solvent, str):
            errors.append(f"solvent: expected a string, got {type(raw_solvent).__name__}")
            solvent = _DEFAULT_SOLVENT
        else:
            solvent = raw_solvent.strip()

    # ── nuclei ───────────────────────────────────────────────────────────
    nuclei = _resolve_nuclei(
        _first(method.get("nuclei"), giaoa.get("nuclei")),
        errors,
    )
    if nuclei is None:
        nuclei = _DEFAULT_NUCLEI

    # ── temperature / TMS references / windows / caps ────────────────────
    boltzmann_temp = _resolve_float(
        (
            _first(method.get("boltzmann_temp"), giaoa.get("boltzmann_temp")),
            nmr_section.get("temperature_k"),
        ),
        default=_DEFAULT_BOLTZMANN_TEMP,
        positive=True,
        field="boltzmann_temp",
        errors=errors,
    )

    tms_1h = _resolve_optional_float(
        (
            _first(method.get("tms_shielding_h"), giaoa.get("tms_shielding_h")),
            refs.get("1H"),
        ),
        field="tms_shielding_h",
        errors=errors,
    )
    tms_13c = _resolve_optional_float(
        (
            _first(method.get("tms_shielding_c"), giaoa.get("tms_shielding_c")),
            refs.get("13C"),
        ),
        field="tms_shielding_c",
        errors=errors,
    )

    ewin = _resolve_float(
        (
            _first(method.get("ewin"), conformer_level.get("ewin")),
            censo_section.get("ewin"),
        ),
        default=_DEFAULT_EWIN,
        positive=True,
        field="ewin",
        errors=errors,
    )

    max_conformers = _resolve_positive_int(
        (
            method.get("max_conformers"),
            nmr_section.get("max_conformers"),
        ),
        default=_DEFAULT_MAX_CONFORMERS,
        field="max_conformers",
        errors=errors,
    )

    # ── error model (workflow reads no config key for it) ────────────────
    raw_error_model = method.get("error_model")
    if not _present(raw_error_model):
        error_model = _DEFAULT_ERROR_MODEL
    elif not isinstance(raw_error_model, str):
        errors.append(f"error_model: expected a string, got {type(raw_error_model).__name__}")
        error_model = _DEFAULT_ERROR_MODEL
    else:
        error_model = raw_error_model.strip()

    conformer_preset = _resolve_conformer_preset(method)

    if errors:
        raise NmrMethodConfigError("NMR method resolution failed: " + "; ".join(errors))

    return NmrMethodConfig(
        nmr_method=nmr_method,
        nmr_basis=nmr_basis,
        solvent_model=solvent_model,
        solvent=solvent,
        nuclei=nuclei,
        boltzmann_temp=boltzmann_temp,
        tms_1h=tms_1h,
        tms_13c=tms_13c,
        ewin=ewin,
        max_conformers=max_conformers,
        error_model=error_model,
        conformer_preset=conformer_preset,
    )
