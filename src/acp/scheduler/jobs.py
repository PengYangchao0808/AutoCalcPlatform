# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportExplicitAny=false
"""
Scheduler Job Models
====================

Job-level data structures for the ACP task scheduler. These are deliberately
separate from the lower-level :class:`acp.core.workflow.WorkflowResult` status —
a ``Job`` wraps a workflow invocation with queueing, persistence, and lifecycle
metadata.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.scheduler.nodes import ExecutionMode

if TYPE_CHECKING:
    from acp.storage.record import TaskRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class JobStatus(str, Enum):
    """Lifecycle states for a scheduler job."""

    QUEUED = "queued"
    STARTING = "starting"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    WAITING_REVIEW = "waiting_review"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        active = (
            JobStatus.QUEUED,
            JobStatus.STARTING,
            JobStatus.PENDING,
            JobStatus.RUNNING,
            JobStatus.PAUSED,
            JobStatus.CANCELLING,
            JobStatus.WAITING_REVIEW,
        )
        return self in active


#: Retention gate (D03 plan todo 9): only these terminal statuses may ever
#: have their work/task directories reclaimed by a cleanup sweep.
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

#: Submission states that mean "bsub outcome not settled yet" — the job may
#: still be adopted/launched, so its directory must never be reclaimed.
PENDING_SUBMIT_STATES: frozenset[str] = frozenset({"intent", "unconfirmed"})

#: Cancel states that allow reclamation: no cancellation requested at all
#: (``None``) or a cancellation CONFIRMED by the poll/reconcile chain.
#: Anything else (``requested``/``sent``/``unconfirmed``) keeps the dir.
CONFIRMED_CANCEL_STATES: frozenset[str | None] = frozenset({None, "confirmed"})


def is_deletion_eligible(
    status: str | None,
    submit_state: str | None,
    cancel_state: str | None,
) -> bool:
    """Exact DB-lifecycle predicate gating any work-dir retention deletion.

    ``deletable = is_terminal(status) ∧ submit_state ∉ {intent, unconfirmed}
    ∧ cancel_state ∈ {None, "confirmed"}``.

    Shared by ``LocalCleanup.cleanup_old_work_dirs`` and
    ``RemoteCleanup.cleanup_old_jobs`` so local and remote retention can
    never diverge (r16/r17 P1).  Age (mtime/completed_at) is evaluated
    ONLY after this gate passes — mtime alone is never a deletion
    qualifier.

    Args:
        status: ``JobRecord.status`` value (``None`` = unknown → keep).
        submit_state: ``result["remote"]["submit_state"]`` (``None`` ok).
        cancel_state: ``result["remote"]["cancel_state"]`` (``None`` ok).

    Returns:
        ``True`` when the directory of this job may be considered for
        reclamation (age check still applies afterwards).
    """
    if status is None or status not in TERMINAL_STATUSES:
        return False
    if submit_state in PENDING_SUBMIT_STATES:
        return False
    if cancel_state not in CONFIRMED_CANCEL_STATES:
        return False
    return True


#: Exit code a mechanism-study subprocess returns when it pauses at a manual
#: review gate (a StudyOrchestrator decision point). The poller translates
#: this into :attr:`JobStatus.WAITING_REVIEW` instead of marking the job
#: FAILED. Chosen outside the 0-2 conventional range and distinct from 130
#: (KeyboardInterrupt) and 1 (generic failure).
EXIT_WAITING_REVIEW = 77


#: Catalog ``status == "active"`` workflow ids in catalog order, used only when
#: ``acp.catalog`` cannot be imported (e.g. early bootstrap / standalone cccp).
_FALLBACK_ACTIVE_WORKFLOWS: tuple[str, ...] = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
)

#: Synthetic scheduler-only workflows (no catalog entry). ``fake`` is an
#: in-process no-op used by the scheduler test base and the legacy Workbench
#: demo button; it is accepted internally but never advertised publicly.
_SYNTHETIC_WORKFLOWS: tuple[str, ...] = ("fake",)


def _derive_public_workflows() -> tuple[str, ...]:
    """Derive the public workflow set from ``WORKFLOW_CATALOG``.

    R14/D5: previously a hand-maintained tuple that drifted out of sync with
    ``acp.catalog.WORKFLOW_CATALOG``. It now derives from the catalog's
    ``status == "active"`` entries.  The synthetic ``fake`` workflow is
    deliberately excluded — it belongs only on internal acceptance surfaces.

    Falls back to a static list when ``acp.catalog`` cannot be imported.
    """
    try:
        from acp.catalog import WORKFLOW_CATALOG
    except ImportError:
        return _FALLBACK_ACTIVE_WORKFLOWS
    return tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")


def _derive_all_workflows() -> tuple[str, ...]:
    """Internal acceptance set = public workflows + synthetic ``fake``.

    The scheduler accepts ``fake`` (API/scheduler submission stays 200), but it
    must stay out of :data:`PUBLIC_WORKFLOWS`.
    """
    return _derive_public_workflows() + _SYNTHETIC_WORKFLOWS


#: Public workflow surface (API listings / error strings): catalog active only.
PUBLIC_WORKFLOWS: tuple[str, ...] = _derive_public_workflows()
#: Internal acceptance set: public workflows plus the synthetic ``fake``.
ALL_WORKFLOWS: tuple[str, ...] = _derive_all_workflows()
#: Backward-compatible alias for the internal acceptance set (includes ``fake``).
SUPPORTED_WORKFLOWS: tuple[str, ...] = ALL_WORKFLOWS

_CENSO_PRESETS: tuple[str, ...] = ("censo-light", "censo-default", "censo-zero")
SCAN_CONFIG_FILENAME = "scan_config.json"
BATCH_CONFIG_FILENAME = "batch_config.json"
PATH_CONFIG_FILENAME = "path_config.json"
GRADIENT_CONFIG_FILENAME = "gradient_config.json"


def censo_preset_from_method(method: dict[str, Any]) -> str | None:
    """Resolve the CENSO preset from a job's method dict.

    Priority: ``preset`` > ``profile_id`` > ``protocol``. Unknown values
    (e.g. the wizard's ``__custom__``) resolve to ``None`` so the CLI
    default applies.
    """
    raw = method.get("preset") or method.get("profile_id") or method.get("protocol")
    if not raw:
        return None
    value = str(raw).strip().lower()
    return value if value in _CENSO_PRESETS else None


def censo_solvent_from_method(method: dict[str, Any]) -> str | None:
    """Resolve the workflow-global solvent from a job's method dict.

    Priority: explicit ``method.solvent`` > per-level solvent fields from
    the wizard levels (``refinement_sp`` then ``dft_opt``) when the level's
    solvent_model is not ``none``.
    """
    solvent = method.get("solvent")
    if solvent:
        return str(solvent)
    levels = method.get("levels")
    if isinstance(levels, dict):
        for level_id in ("refinement_sp", "dft_opt"):
            level = levels.get(level_id)
            if not isinstance(level, dict):
                continue
            model = str(level.get("solvent_model") or "").strip().lower()
            value = str(level.get("solvent") or "").strip()
            if value and model not in ("", "none"):
                return value
    return None


def censo_ewin_from_method(method: dict[str, Any]) -> float | None:
    """Resolve the CREST energy window from a job's method dict.

    Priority: explicit ``method.ewin`` > the wizard's ``censo`` level
    ``ewin`` field. Non-numeric or non-positive values resolve to ``None``
    so the workflow/config default applies.
    """
    raw = method.get("ewin")
    if raw is None:
        levels = method.get("levels")
        if isinstance(levels, dict):
            censo_level = levels.get("censo")
            if isinstance(censo_level, dict):
                raw = censo_level.get("ewin")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def input_chemistry_flags(inp: dict[str, Any]) -> list[str]:
    """Emit ``--charge`` / ``--multiplicity`` from the job input payload.

    All current workflows keep chemistry flags at the top level. Historical
    nested role payloads are read only and are intentionally not part of the
    active scheduler contract.
    """
    chemistry = inp

    flags: list[str] = []
    if chemistry.get("charge") is not None:
        flags += ["--charge", str(chemistry["charge"])]
    if chemistry.get("multiplicity") is not None:
        flags += ["--multiplicity", str(chemistry["multiplicity"])]
    return flags


def scan_method_flags(
    method: Mapping[str, Any],
    inp: Mapping[str, Any] | None = None,
) -> list[str]:
    """Emit relaxed-scan coordinate and point flags for local and remote jobs."""
    payload = inp or {}
    raw_coordinates: Any = None
    for source in (payload, method):
        for key in ("scan_coordinates", "coordinate"):
            candidate = source.get(key)
            if candidate is not None:
                raw_coordinates = candidate
                break
        if raw_coordinates is not None:
            break

    if raw_coordinates is None:
        raise ValueError("scan job requires at least one coordinate")
    if isinstance(raw_coordinates, (str, Mapping)):
        coordinates = [raw_coordinates]
    elif isinstance(raw_coordinates, (list, tuple)):
        coordinates = list(raw_coordinates)
    else:
        raise ValueError("scan coordinates must be a string or a sequence")

    flags: list[str] = []
    for coordinate in coordinates:
        if isinstance(coordinate, Mapping):
            atoms = coordinate.get("atoms")
            start = coordinate.get("start")
            end = coordinate.get("end")
            if not isinstance(atoms, (list, tuple)) or len(atoms) != 2:
                raise ValueError("scan coordinate objects require exactly two atoms")
            if start is None or end is None:
                raise ValueError("scan coordinate objects require start and end")
            coordinate = f"{atoms[0]},{atoms[1]},{start},{end}"
        flags += ["--coordinate", str(coordinate)]

    levels = method.get("levels")
    scan_level: Mapping[str, Any] = {}
    if isinstance(levels, Mapping):
        candidate_level = levels.get("scan") or levels.get("scan_coordinate")
        if isinstance(candidate_level, Mapping):
            scan_level = candidate_level
    points = method.get("scan_points")
    if points is None:
        points = scan_level.get("scan_coordinate_points")
    if points is None:
        points = scan_level.get("scan_points")
    if points is not None:
        flags += ["--scan-points", str(points)]

    use_scants = method.get("scan_use_scants")
    if use_scants is None:
        use_scants = scan_level.get("scan_use_scants")
    if use_scants is None:
        use_scants = method.get("use_scants")
    if use_scants is None:
        use_scants = payload.get("scan_use_scants")
    if use_scants is None:
        use_scants = payload.get("use_scants")
    if _as_bool(use_scants) is True:
        flags += ["--scants"]
    return flags


# ── xtbmd_censo_energy flag emission (E7: runner ⇄ script_gen parity) ────
# Single source of truth for the MD / batch-opt / ISOSTAT / conv-check /
# resume flag mapping. Both JobRunner._build_cmd and
# build_remote_cli_command emit through this function so the local and
# remote paths can never drift (DevDoc §10.2 — the "runner / script_gen
# 白名单" parity warning). Key set mirrors catalog.FIELD_DEFINITIONS for
# the xtbmd_censo_energy schema plus the energy-like top-level keys.

_XTBMD_SCALAR_FLAGS: dict[str, str] = {
    "md_temperature": "--md-temp",
    "md_time_ps": "--md-time",
    "md_dump_fs": "--md-dump",
    "md_step_fs": "--md-step",
    "md_hmass": "--md-hmass",
    "md_seed": "--md-seed",
    "md_seeds": "--md-seeds",
    "md_method": "--md-method",
    "md_timeout": "--md-timeout",
    "conv_novelty_max": "--conv-novelty-max",
    "conv_rmsd": "--conv-rmsd",
    "max_frames": "--max-frames",
    "opt_gfn": "--opt-gfn",
    "opt_level": "--opt-level",
    "opt_timeout": "--opt-timeout",
    "edis": "--edis",
    "gdis": "--gdis",
    "threshold": "--threshold",
}

# Boolean method keys → CLI opt-out flag (emitted when the value is False).
_XTBMD_BOOL_OPT_OUT_FLAGS: dict[str, str] = {
    "md_shake": "--md-no-shake",
    "md_nvt": "--no-md-nvt",
    "conv_check": "--no-conv-check",
}

# Boolean method keys → CLI opt-in flag (emitted when the value is True).
_XTBMD_BOOL_OPT_IN_FLAGS: dict[str, str] = {
    "keep_frames": "--keep-frames",
    "resume": "--resume",
    "rank1_only": "--rank1-only",
    "no_opt": "--no-opt",
}


def _as_bool(value: Any) -> bool | None:
    """Coerce a method-dict value to ``True`` / ``False`` (or ``None``).

    The frontend submits real JSON booleans, but API clients may send
    ``"true"`` / ``"false"`` strings (or ``1`` / ``0``); strict ``is
    True`` / ``is False`` checks would silently drop those.  Returns
    ``None`` for unrecognised values so the CLI default applies.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in ("true", "1", "yes", "on"):
            return True
        if norm in ("false", "0", "no", "off"):
            return False
    return None


def xtbmd_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the xtbmd_censo_energy CLI flag group from a job's method dict.

    Scalar fields are forwarded whenever present and non-empty; booleans
    are emitted as explicit opt-in / opt-out flags so the CLI defaults
    (rank1_only=False, resume=False, md_nvt=True, conv_check=True, ...)
    never silently override an explicit user choice.  Boolean values are
    normalised via :func:`_as_bool` (tolerates ``"true"`` strings).

    ``ewin`` is intentionally not emitted here: it follows the shared
    :func:`censo_ewin_from_method` priority (``method.ewin`` then
    ``levels.censo.ewin``) used by the energy workflow.
    """
    flags: list[str] = []
    for key, flag in _XTBMD_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is None or value == "":
            continue
        flags += [flag, str(value)]
    for key, flag in _XTBMD_BOOL_OPT_OUT_FLAGS.items():
        if _as_bool(method.get(key)) is False:
            flags.append(flag)
    for key, flag in _XTBMD_BOOL_OPT_IN_FLAGS.items():
        if _as_bool(method.get(key)) is True:
            flags.append(flag)
    return flags


# ── nmr flag emission (T19/T20: single resolver source; E7 parity) ──────
# NMR CLI flags are rendered from acp.nmr.method_config.resolve_nmr_method
# (G06 single source of truth) — the resolver owns key precedence, so this
# module never reads flat nmr_method/nmr_basis keys. Flag spellings match
# the ``acp run nmr`` parser in cli.py. INVARIANT: both nmr builders
# (runner._build_nmr_cmd, script_gen.build_remote_nmr_cmd_tail) emit ONLY
# nmr_method_flags — caller-side censo_preset/solvent/ewin emission for
# nmr would duplicate --preset/--solvent/--ewin (same values, emitted
# twice) and read levels.censo instead of the nmr wizard's
# levels.conformer.ewin. The censo_* helpers below apply to
# Confsearch/ensemble/energy/xtbmd only.
_NMR_FLAG_FIELDS: tuple[tuple[str, str], ...] = (
    ("nmr_method", "--nmr-method"),
    ("nmr_basis", "--nmr-basis"),
    ("solvent_model", "--solvent-model"),
    ("solvent", "--solvent"),
    ("boltzmann_temp", "--boltzmann-temp"),
    ("tms_1h", "--tms-1h"),
    ("tms_13c", "--tms-13c"),
    ("ewin", "--ewin"),
    ("max_conformers", "--max-conformers"),
    ("error_model", "--error-model"),
    ("conformer_preset", "--preset"),
)


def nmr_method_flags(method: dict[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """Emit the NMR CLI flag group from a job's method payload (E7 parity).

    G06: :func:`acp.nmr.method_config.resolve_nmr_method` is the single
    source — this function only renders the resolved config into argv. A
    flag is emitted only when its resolved value differs from the
    resolver's built-in default (an empty payload resolved against no
    config), so payloads that set nothing emit nothing and the CLI defaults
    apply unchanged. Nuclei is comma-joined; ``None``/empty values are
    never emitted (gas phase resolves ``solvent`` to ``""`` — ``--solvent``
    drops while ``--solvent-model none`` still carries the signal).

    Args:
        method: Job/wizard NMR method payload (flat legacy keys or the
            frontend's ``{schema_id, profile_id, levels}`` shape).
        config: Merged cccp config mapping (``load_config()`` output) or
            ``None`` — forwarded to the resolver as-is.

    Returns:
        Flattened ``[flag, value, ...]`` argv fragment.

    Raises:
        NmrMethodConfigError: The payload cannot become a valid NMR config
            (rejection rules live in the resolver — nothing mismatched
            silently falls back to defaults).
    """
    from acp.nmr.method_config import resolve_nmr_method

    resolved = resolve_nmr_method(method, config)
    builtin = resolve_nmr_method({}, None)
    flags: list[str] = []
    if resolved.nuclei != builtin.nuclei:
        flags += ["--nuclei", ",".join(resolved.nuclei)]
    for attr, flag in _NMR_FLAG_FIELDS:
        value = getattr(resolved, attr)
        if value is None or value == "" or value == getattr(builtin, attr):
            continue
        flags += [flag, str(value)]
    return flags


def nmr_flag_config(config_path: str | None = None) -> dict[str, Any]:
    """Merged config backing NMR flag emission (T20 local ⇄ remote parity).

    Mirrors the CLI's ``_build_config`` full 6-source merge
    (``cccp.config.load_config``) so the emitted argv carries every
    nmr-relevant value that differs from the resolver's built-in
    defaults — the flags then determine the effective config on hosts
    that do not receive the ``--config`` file (remote nodes).
    """
    from cccp.config import load_config

    return load_config(Path(config_path) if config_path else None)


# ── Confsearch / stage-workflow flag emission (E7: runner ⇄ script_gen) ───
# Confsearch method knobs that flow method → CLI. Protocol / profile /
# refinement_policy are the three orthogonal axes (plan §3); MD scalars apply
# to the xtb-md / xtbmd-censo sampling layer. Solvent/ewin go through the
# shared resolvers (censo_solvent_from_method / censo_ewin_from_method).
_CONFSEARCH_SCALAR_FLAGS: dict[str, str] = {
    "md_temperature": "--md-temp",
    "md_time_ps": "--md-time",
    "md_seeds": "--md-seeds",
    "max_frames": "--max-frames",
    "temperature": "--temperature",
    "energy_window": "--ewin",
    "max_conformers": "--max-conformers",
}


def confsearch_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the Confsearch CLI flag group from a job's method dict (E7 parity).

    The wizard's ``profile_id`` doubles as the protocol selector when it
    matches a known Confsearch protocol id (catalog profiles are named after
    the protocols); an explicit ``method.protocol`` wins.
    """
    from acp.confsearch.contracts import PROTOCOLS, REFINEMENT_POLICIES

    flags: list[str] = []
    protocol = str(method.get("protocol") or "").strip()
    if not protocol and str(method.get("profile_id") or "") in PROTOCOLS:
        protocol = str(method["profile_id"])
    if protocol:
        flags += ["--protocol", protocol]
    if method.get("profile"):
        flags += ["--profile", str(method["profile"])]
    policy = method.get("refinement_policy")
    if policy and str(policy) in REFINEMENT_POLICIES:
        flags += ["--refinement-policy", str(policy)]
    if method.get("backend"):
        flags += ["--backend", str(method["backend"])]
    preset = method.get("preset")
    if preset and str(preset) in _CENSO_PRESETS:
        flags += ["--preset", str(preset)]
    for key, flag in _CONFSEARCH_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is None or value == "":
            continue
        flags += [flag, str(value)]
    if method.get("levels") and isinstance(method["levels"], dict):
        flags += ["--levels", json.dumps(method["levels"])]
    return flags


def _select_flag(method: dict[str, Any]) -> list[str]:
    select = method.get("select")
    if isinstance(select, (list, tuple)) and select:
        return ["--select", ",".join(str(item) for item in select)]
    if isinstance(select, str) and select.strip():
        return ["--select", select.strip()]
    return []


# ── BatchOptimize flag emission (E7: runner ⇄ script_gen parity) ──────────
_BATCHOPTIMIZE_SCALAR_FLAGS: dict[str, str] = {
    "minimum_method": "--minimum-method",
    "minimum_basis": "--minimum-basis",
    "transition_state_method": "--transition-state-method",
    "transition_state_basis": "--transition-state-basis",
    "optimization_method": "--method",
    "optimization_basis": "--basis",
    "single_point_method": "--sp-method",
    "single_point_basis": "--sp-basis",
    "temperature": "--temperature",
    "pressure": "--pressure",
    "scale_factor": "--scale-factor",
    "opt_max_iter": "--opt-max-iter",
    "opt_convergence": "--opt-convergence",
    "opt_trust_radius": "--opt-trust-radius",
    "opt_initial_hessian": "--opt-initial-hessian",
    "opt_rescue_policy": "--opt-rescue-policy",
    "opt_max_rescue": "--opt-max-rescue",
    "scf_max_iter": "--scf-max-iter",
    "scf_convergence": "--scf-convergence",
    "scf_strategy": "--scf-strategy",
    "minimum_opt_trust_radius": "--minimum-opt-trust-radius",
    "minimum_opt_initial_hessian": "--minimum-opt-initial-hessian",
    "transition_state_opt_trust_radius": "--transition-state-opt-trust-radius",
    "transition_state_opt_initial_hessian": "--transition-state-opt-initial-hessian",
    "minimum_opt_max_iter": "--minimum-opt-max-iter",
    "minimum_opt_convergence": "--minimum-opt-convergence",
    "minimum_scf_max_iter": "--minimum-scf-max-iter",
    "minimum_scf_convergence": "--minimum-scf-convergence",
    "minimum_scf_strategy": "--minimum-scf-strategy",
    "minimum_opt_rescue_policy": "--minimum-opt-rescue-policy",
    "minimum_opt_max_rescue": "--minimum-opt-max-rescue",
    "transition_state_opt_max_iter": "--transition-state-opt-max-iter",
    "transition_state_opt_convergence": "--transition-state-opt-convergence",
    "transition_state_scf_max_iter": "--transition-state-scf-max-iter",
    "transition_state_scf_convergence": "--transition-state-scf-convergence",
    "transition_state_scf_strategy": "--transition-state-scf-strategy",
    "transition_state_opt_rescue_policy": "--transition-state-opt-rescue-policy",
    "transition_state_opt_max_rescue": "--transition-state-opt-max-rescue",
}
_BATCHOPTIMIZE_PROFILES: frozenset[str] = frozenset(
    {"opt_only", "opt_freq", "opt_freq_sp", "opt_freq_sp_thermo"}
)


def batchoptimize_method_flags(
    method: Mapping[str, Any],
    inp: Mapping[str, Any] | None = None,
) -> list[str]:
    """Emit BatchOptimize profile, shared settings, and override flags."""
    import json as _json

    flags: list[str] = []
    profile = method.get("profile") or method.get("profile_id")
    if profile is not None and str(profile) in _BATCHOPTIMIZE_PROFILES:
        flags += ["--profile", str(profile)]

    selection = method.get("select")
    if selection is None and inp is not None:
        selection = inp.get("select") or inp.get("selected_ids")
    if isinstance(selection, (list, tuple)) and selection:
        flags += ["--select", ",".join(str(value) for value in selection)]
    elif isinstance(selection, str) and selection.strip():
        flags += ["--select", selection.strip()]

    if "batch_roles" in method:
        flags += ["--batch-roles-json", _json.dumps(method["batch_roles"], separators=(",", ":"))]
        batch_level = (method.get("levels") or {}).get("batch", {})
        if isinstance(batch_level, Mapping) and batch_level.get("electronic_state"):
            flags += [
                "--electronic-state-json",
                _json.dumps(batch_level["electronic_state"], separators=(",", ":")),
            ]
        return flags

    for key, flag in _BATCHOPTIMIZE_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is not None and value != "":
            flags += [flag, str(value)]

    opt_recalc = method.get("opt_recalc_hess")
    if opt_recalc is not None and opt_recalc != "":
        flags += ["--opt-recalc-hess", str(opt_recalc)]

    min_opt_recalc = method.get("minimum_opt_recalc_hess")
    if min_opt_recalc is not None and min_opt_recalc != "":
        flags += ["--minimum-opt-recalc-hess", str(min_opt_recalc)]

    ts_opt_recalc = method.get("transition_state_opt_recalc_hess")
    if ts_opt_recalc is not None and ts_opt_recalc != "":
        flags += ["--transition-state-opt-recalc-hess", str(ts_opt_recalc)]

    orbital_inherit = method.get("scf_orbital_inherit")
    if orbital_inherit is False:
        flags += ["--no-scf-orbital-inherit"]

    batch_level = (method.get("levels") or {}).get("batch", {})
    if isinstance(batch_level, Mapping) and batch_level.get("electronic_state"):
        flags += [
            "--electronic-state-json",
            _json.dumps(batch_level["electronic_state"], separators=(",", ":")),
        ]

    return flags


@dataclass(frozen=True)
class JobSpec:
    """Immutable description of what a job should run.

    Attributes:
        workflow: One of :data:`SUPPORTED_WORKFLOWS`.
        name: Canonical user-facing task label; for persisted jobs this equals
            the final physical task-directory leaf.
        input: Input payload (SMILES string, file path, or structured dict).
        method: Method/protocol settings. For conformer workflows this may
            contain ``protocol``, ``profile_id``, and an optional ``levels``
            dict with per-stage method/basis/solvent overrides.
        resources: Resource limits (nproc, mem, ...).
        output_dir: Explicit output directory override (else derived from run root).
        config_path: Optional path to a YAML config file.
        tags: Free-form tags for filtering.
        execution_mode: Resource-type preference (``"local"`` | ``"remote"``).
            ``None`` = follow the server default.  ``"remote"`` means
            "pick a suitable remote node"; use ``target_node`` to pin a
            specific instance (including ``"local"``).
        target_node: Name of a specific execution target to run on
            (``"local"`` or a configured remote node name).  Takes priority
            over ``execution_mode`` and the server default.
        node_tags: User-required node capability tags (AND semantics).
            Only statically declared tags can ever satisfy them
            (design D13); empty = no tag constraint.  Persisted in
            spec_json — deserialization tolerates the missing key
            (older rows predate the field).
    """

    workflow: str
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    method: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = field(default_factory=list)
    project_id: str | None = None
    input_hash: str | None = None
    execution_mode: ExecutionMode | None = None
    target_node: str | None = None
    node_tags: list[str] = field(default_factory=list)
    # v2 task-storage naming (docs/ACP_Project_Task_Storage_Design_v2.md §4):
    # physical task dir name is "<molecule>_<task>_<remark>".  The fields are
    # optional — :meth:`task_dir_name` applies a defaulting chain so every
    # caller (old API clients, CLI invocations) still gets a valid v2 name.
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""
    custom_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def task_dir_name(self) -> str:
        """Return the v2 task directory name ``<molecule>_<task>_<remark>``.

        Defaulting chain (design doc §4.3): the effective task component is
        ``task_name or workflow``; the effective molecule component is
        ``molecule_name or sanitized(name) or "mol"``.  The chain guarantees
        a well-formed name for any spec, so this never raises for callers
        that omit the optional v2 fields.
        """
        from acp.storage.layout import sanitize_task_dir_name

        task = self.task_name or self.workflow
        if self.molecule_name:
            molecule = self.molecule_name
        else:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.name)
            molecule = safe.strip("._") or "mol"
        return sanitize_task_dir_name(molecule, task, self.remark)

    @property
    def uses_v2_naming(self) -> bool:
        """Deprecated shim: naming is always v2 — kept for the remote runner."""
        return True


@dataclass
class JobRecord:
    """Mutable, persistable record tracking one job's lifecycle."""

    id: str
    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    work_dir: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    project_id: str | None = None
    input_hash: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    remote_job_id: str | None = None
    group_id: str | None = None
    node_id: str | None = None
    host: str | None = None
    result: dict[str, Any] | None = None
    revision: int = 0
    attempt: int = 1

    def touch(self) -> None:
        self.updated_at = _utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "work_dir": self.work_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_stage": self.current_stage,
            "progress": self.progress,
            "error": self.error,
            "project_id": self.project_id,
            "input_hash": self.input_hash,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "remote_job_id": self.remote_job_id,
            "group_id": self.group_id,
            "node_id": self.node_id,
            "host": self.host,
            "result": self.result,
            "revision": self.revision,
            "attempt": self.attempt,
        }


def build_task_record(record: JobRecord) -> TaskRecord:
    """Project a scheduler :class:`JobRecord` onto a v2 :class:`TaskRecord`.

    Single source of truth for the ``task.json`` payload: the local runner
    writes it via :meth:`TaskStorage.write_task_json`, and the remote runner
    reuses it to upload scheduler-context markers before ``bsub``. Field
    mapping is frozen: ``result_manifest_path`` / ``layout_version`` stay at
    their dataclass defaults, and ``project_id`` falls back to ``""`` when
    the job has no project.

    The storage import is lazy (same pattern as :meth:`JobSpec.task_dir_name`):
    importing the ``acp.storage`` package pulls the paramiko-coupled remote
    backend, which must not enter this module's import time.
    """
    from acp.storage.record import TaskRecord

    return TaskRecord(
        task_id=record.id,
        project_id=record.project_id or "",
        molecule_name=record.spec.molecule_name,
        task_name=record.spec.task_name,
        remark=record.spec.remark,
        display_name=record.spec.name,
        workflow=record.spec.workflow,
        task_dir_name=Path(record.work_dir).name,
        status=record.status.value,
        node_id=record.node_id,
        node_path=record.work_dir,
        input_hash=record.input_hash,
        current_stage=record.current_stage,
        created_at=record.created_at,
        updated_at=record.updated_at,
        custom_name=getattr(record, "custom_name", None)
        or getattr(record.spec, "custom_name", None),
    )


__all__ = [
    "JobStatus",
    "JobSpec",
    "JobRecord",
    "TERMINAL_STATUSES",
    "PENDING_SUBMIT_STATES",
    "CONFIRMED_CANCEL_STATES",
    "is_deletion_eligible",
    "build_task_record",
    "PUBLIC_WORKFLOWS",
    "ALL_WORKFLOWS",
    "SUPPORTED_WORKFLOWS",
    "censo_preset_from_method",
    "censo_solvent_from_method",
    "censo_ewin_from_method",
    "input_chemistry_flags",
    "scan_method_flags",
    "xtbmd_method_flags",
    "nmr_method_flags",
    "nmr_flag_config",
    "batchoptimize_method_flags",
    "confsearch_method_flags",
    "SCAN_CONFIG_FILENAME",
    "BATCH_CONFIG_FILENAME",
    "PATH_CONFIG_FILENAME",
    "GRADIENT_CONFIG_FILENAME",
]
