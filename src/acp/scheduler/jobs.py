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
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from acp.mechanism.layout import resolve_study_layout
from acp.scheduler.nodes import ExecutionMode


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


#: Exit code a mechanism-study subprocess returns when it pauses at a manual
#: review gate (a StudyOrchestrator decision point). The poller translates
#: this into :attr:`JobStatus.WAITING_REVIEW` instead of marking the job
#: FAILED. Chosen outside the 0-2 conventional range and distinct from 130
#: (KeyboardInterrupt) and 1 (generic failure).
EXIT_WAITING_REVIEW = 77


def _derive_supported_workflows() -> tuple[str, ...]:
    """Derive the scheduler's supported workflow set from WORKFLOW_CATALOG.

    R14/D5: previously this was a hand-maintained tuple that drifted out
    of sync with ``acp.catalog.WORKFLOW_CATALOG``. It now derives from the
    catalog's ``status == "active"`` entries, plus the synthetic ``fake``
    workflow that exists only for scheduler tests (kept here, not in the
    public catalog — see test 3.12 which excludes ``fake`` from the
    equality assertion against the catalog's active set).

    Falls back to a static list when ``acp.catalog`` cannot be imported
    (e.g. during early bootstrap or standalone cccp use).
    """
    try:
        from acp.catalog import WORKFLOW_CATALOG
    except ImportError:
        return (
            "ensemble",
            "energy",
            "mechanism",
            "singlepoint",
            "optimize",
            "frequency",
            "optfreq",
            "optfreqsp",
            "fake",
        )
    active = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")
    # ``fake`` is a synthetic scheduler-only workflow (no catalog entry);
    # append it so test 3.12's set difference `SUPPORTED_WORKFLOWS - {"fake"}`
    # equals the catalog's active id set exactly.
    return active + ("fake",)


SUPPORTED_WORKFLOWS: tuple[str, ...] = _derive_supported_workflows()

_CENSO_PRESETS: tuple[str, ...] = ("censo-light", "censo-default", "censo-zero")
MECHANISM_CONFIG_FILENAME = "mechanism_config.json"


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

    Mechanism jobs carry those fields on the nested ``reactant`` role,
    whereas the other workflows keep them at the top level. This helper keeps
    the runner and remote script generator in parity.
    """
    chemistry = inp
    if inp.get("source_type") == "mechanism":
        reactant = inp.get("reactant")
        if isinstance(reactant, dict):
            chemistry = reactant

    flags: list[str] = []
    if chemistry.get("charge") is not None:
        flags += ["--charge", str(chemistry["charge"])]
    if chemistry.get("multiplicity") is not None:
        flags += ["--multiplicity", str(chemistry["multiplicity"])]
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


# ── nmr flag emission (E7: runner ⇄ script_gen parity) ──────────────────
# NMR workflow scalar knobs that flow method → CLI. Nuclei is emitted as
# a comma-joined string. Solvent/ewin go through the shared resolvers
# (censo_solvent_from_method / censo_ewin_from_method), not here.
_NMR_SCALAR_FLAGS: dict[str, str] = {
    "boltzmann_temp": "--boltzmann-temp",
    "tms_shielding_h": "--tms-1h",
    "tms_shielding_c": "--tms-13c",
    "error_model": "--error-model",
    "nmr_method": "--nmr-method",
    "nmr_basis": "--nmr-basis",
}


def nmr_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the NMR CLI flag group from a job's method dict (E7 parity).

    Nuclei (a list) is emitted as a comma-joined ``--nuclei`` value when
    present. Solvent and ewin are resolved through the shared
    :func:`censo_solvent_from_method` / :func:`censo_ewin_from_method`
    helpers (caller-side), not here, so the NMR and energy/ensemble
    branches stay consistent.
    """
    flags: list[str] = []
    nuclei = method.get("nuclei")
    if isinstance(nuclei, (list, tuple)) and nuclei:
        flags += ["--nuclei", ",".join(str(n) for n in nuclei)]
    for key, flag in _NMR_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is None or value == "":
            continue
        flags += [flag, str(value)]
    return flags


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


def pessearch_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the PESsearch CLI flag group (strategy select; plan via input)."""
    flags: list[str] = []
    if method.get("strategy"):
        flags += ["--strategy", str(method["strategy"])]
    flags += _select_flag(method)
    return flags


def lowconfirm_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the Lowconfirm CLI flag group."""
    flags = _select_flag(method)
    if _as_bool(method.get("no_irc")) is True:
        flags.append("--no-irc")
    return flags


def highconfirm_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the Highconfirm CLI flag group."""
    flags = _select_flag(method)
    if _as_bool(method.get("irc")) is True:
        flags.append("--irc")
    return flags


# ── mechanism flag emission (E7: runner ⇄ script_gen parity) ─────────────
# Mechanism method knobs that flow method → CLI. Strategy/fidelity are the
# two orthogonal preset axes; scan/IRC counts and Hessian policy are the
# per-stage scalars. Routes / reactant / product live in JobSpec.input, NOT
# in the method dict (coordinate plans are the study, not the method).
_MECHANISM_SCALAR_FLAGS: dict[str, str] = {
    "strategy": "--strategy",
    "fidelity": "--fidelity",
    "scan_points": "--scan-points",
    "irc_points": "--irc-points",
    "conformer_mode": "--conformer-mode",
    "max_elementary_steps": "--max-elementary-steps",
    "promotion_policy": "--promotion-policy",
    "study_id": "--study-id",
}

_MECHANISM_BOOL_OPT_IN_FLAGS: dict[str, str] = {
    "int_extension": "--int-extension",
    "auto_converge": "--auto-converge",
}


def _mechanism_preset_ids() -> frozenset[str]:
    """Derive the mechanism preset profile ids from the catalog (single source)."""
    try:
        from acp.catalog import METHOD_SCHEMAS
    except ImportError:
        return frozenset({"rph-s3", "rph-s4"})
    profiles = METHOD_SCHEMAS.get("mechanism", {}).get("profiles")
    return frozenset(
        str(profile.get("profile_id"))
        for profile in profiles
        if isinstance(profile, dict) and profile.get("profile_id")
    )


def mechanism_preset_from_method(method: dict[str, Any]) -> str | None:
    """Resolve the mechanism fidelity preset from a job's method dict.

    Priority: ``preset`` > ``profile_id``. Only catalog profile ids are
    accepted; any other value resolves to ``None`` so the CLI default applies.
    """
    raw = method.get("preset") or method.get("profile_id")
    if not raw:
        return None
    value = str(raw).strip().lower()
    return value if value in _mechanism_preset_ids() else None


def mechanism_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the mechanism CLI flag group from a job's method dict (E7 parity).

    Scalar fields are forwarded whenever present and non-empty. The preset
    (rph-s3 / rph-s4) is emitted via ``--preset`` when set; otherwise the
    per-axis ``--strategy`` / ``--fidelity`` flags are emitted from their
    method keys.
    """
    flags: list[str] = []
    preset = mechanism_preset_from_method(method)
    if preset:
        flags += ["--preset", preset]
    for key, flag in _MECHANISM_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is None or value == "":
            continue
        if key == "strategy" and preset:
            continue
        if key == "fidelity" and preset:
            continue
        flags += [flag, str(value)]
    for key, flag in _MECHANISM_BOOL_OPT_IN_FLAGS.items():
        if _as_bool(method.get(key)) is True:
            flags.append(flag)
    return flags


def _mechanism_raw_scalar(method: Mapping[str, Any], key: str) -> Any:
    value = method.get(key)
    if key in {"int_extension", "auto_converge"}:
        return _as_bool(value)
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def mechanism_resolved_settings(method: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the scheduler-side mechanism config summary from ``method``.

    Priority mirrors the CLI file channel: explicit top-level method values win;
    only ``strategy`` / ``fidelity`` inherit from the catalog preset when the
    method omits them; everything else stays ``None`` so workflow defaults can
    still be applied downstream.
    """

    preset = mechanism_preset_from_method(dict(method))
    preset_strategy: str | None = None
    preset_fidelity: str | None = None
    if preset:
        from acp.mechanism.presets import resolve_preset

        preset_strategy, preset_fidelity = resolve_preset(preset)

    return {
        "preset": preset,
        "strategy": _mechanism_raw_scalar(method, "strategy") or preset_strategy,
        "fidelity": _mechanism_raw_scalar(method, "fidelity") or preset_fidelity,
        "scan_points": _mechanism_raw_scalar(method, "scan_points"),
        "irc_points": _mechanism_raw_scalar(method, "irc_points"),
        "conformer_mode": _mechanism_raw_scalar(method, "conformer_mode"),
        "max_elementary_steps": _mechanism_raw_scalar(method, "max_elementary_steps"),
        "promotion_policy": _mechanism_raw_scalar(method, "promotion_policy"),
        "int_extension": _mechanism_raw_scalar(method, "int_extension"),
        "auto_converge": _mechanism_raw_scalar(method, "auto_converge"),
        "require_sr_review": _mechanism_raw_scalar(method, "require_sr_review"),
        "study_id": _mechanism_raw_scalar(method, "study_id"),
    }


def _mechanism_role_payload(inp: Mapping[str, Any], role: str) -> Any:
    if inp.get("source_type") == "mechanism":
        return inp.get(role)
    if role == "reactant":
        return inp
    return inp.get(role)


def _mechanism_role_source_from_payload(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        source = payload.get("source") or payload.get("input") or payload.get("smiles")
        if source is None:
            return None
        return str(source)
    if payload in (None, ""):
        return None
    return str(payload)


def _mechanism_role_entry(
    inp: Mapping[str, Any],
    role: str,
    role_paths: Mapping[str, str | Path],
) -> dict[str, Any] | None:
    payload = _mechanism_role_payload(inp, role)
    path_value = role_paths.get(role)
    if path_value is None:
        path_value = _mechanism_role_source_from_payload(payload)
        if path_value is None and role == "reactant":
            path_value = _mechanism_role_source_from_payload(inp)

    if path_value is None and role != "reactant":
        return None

    chemistry = payload if isinstance(payload, Mapping) else (inp if role == "reactant" else {})
    charge = chemistry.get("charge") if isinstance(chemistry, Mapping) else None
    multiplicity = chemistry.get("multiplicity") if isinstance(chemistry, Mapping) else None
    return {
        "path": str(path_value) if path_value is not None else None,
        "charge": charge,
        "multiplicity": multiplicity,
    }


def build_mechanism_job_config_payload(
    inp: Mapping[str, Any],
    method: Mapping[str, Any],
    resources: Mapping[str, Any],
    role_paths: Mapping[str, str | Path],
    reaction_definition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the scheduler/CLI handoff payload for mechanism jobs."""

    payload = {
        "version": 1,
        "method": dict(method),
        "resolved": mechanism_resolved_settings(method),
        "roles": {
            "reactant": _mechanism_role_entry(inp, "reactant", role_paths),
            "product": _mechanism_role_entry(inp, "product", role_paths),
            "ts_guess": _mechanism_role_entry(inp, "ts_guess", role_paths),
        },
        "resources": {
            "nproc": resources.get("nproc"),
            "mem": resources.get("mem"),
        },
    }
    if reaction_definition is not None:
        payload["mechanism_schema_version"] = int(reaction_definition.get("schema_version") or 0)
    return payload


def write_mechanism_job_config(
    work_dir: Path,
    inp: Mapping[str, Any],
    method: Mapping[str, Any],
    resources: Mapping[str, Any],
    role_paths: Mapping[str, str | Path],
    reaction_definition: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``mechanism_config.json`` into *work_dir*."""

    config_path = work_dir / MECHANISM_CONFIG_FILENAME
    payload = build_mechanism_job_config_payload(
        inp,
        method,
        resources,
        role_paths,
        reaction_definition=reaction_definition,
    )
    config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return config_path


def write_mechanism_reaction_json(
    work_dir: Path,
    study_id: str,
    reaction_definition: Mapping[str, Any],
) -> Path:
    """Materialize ``reaction.json`` inside the study directory atomically."""

    path = resolve_study_layout(work_dir, study_id).reaction_json
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(dict(reaction_definition), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(temp_path, path)
    return path


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
    mechanism_project_id: str | None = None
    input_hash: str | None = None
    execution_mode: ExecutionMode | None = None
    target_node: str | None = None
    # v2 task-storage naming (docs/ACP_Project_Task_Storage_Design_v2.md §4):
    # physical task dir name is "<molecule>_<task>_<remark>".  The fields are
    # optional — :meth:`task_dir_name` applies a defaulting chain so every
    # caller (old API clients, CLI invocations) still gets a valid v2 name.
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""

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
    result: dict[str, Any] | None = None

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
            "result": self.result,
        }


__all__ = [
    "JobStatus",
    "JobSpec",
    "JobRecord",
    "SUPPORTED_WORKFLOWS",
    "censo_preset_from_method",
    "censo_solvent_from_method",
    "censo_ewin_from_method",
    "input_chemistry_flags",
    "xtbmd_method_flags",
    "nmr_method_flags",
    "mechanism_method_flags",
    "mechanism_preset_from_method",
    "mechanism_resolved_settings",
    "build_mechanism_job_config_payload",
    "write_mechanism_reaction_json",
    "write_mechanism_job_config",
    "MECHANISM_CONFIG_FILENAME",
]
