"""Unified mechanism study layout (v2 design §6.4).

Replaces the legacy ``mechanism_study/<study_id>/`` third naming layer:
study content now lives under the task's unified ``WORK/<stage>/`` tree and
``study_id`` survives only as DB/checkpoint identity — never as a disk path
component. Legacy studies stay readable via :func:`find_study_layout`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from acp.storage.layout import TaskLayout

logger = logging.getLogger(__name__)

__all__ = [
    "MechanismStudyLayout",
    "resolve_study_layout",
    "find_study_layout",
    "find_reaction_json",
    "standalone_layout",
    "LEGACY_FALLBACK_ENABLED",
]

LEGACY_FALLBACK_ENABLED = True
_LEGACY_DIR = "mechanism_study"


@dataclass(frozen=True)
class MechanismStudyLayout:
    """Per-item physical paths for one mechanism study.

    ``analysis_root`` doubles as the checkpoint root: ``study.json``,
    ``network.json``, ``reaction.json``, ``quality_gates.json``, the study
    event log, ``cycles/`` and ``decisions/`` all live there, matching the
    legacy flat layout so checkpoint-relative consumers keep working.
    """

    study_id: str
    task_root: Path
    analysis_root: Path
    prepare_root: Path
    search_root: Path
    path_root: Path
    opt_root: Path
    states_root: Path
    inputs_root: Path
    s1_root: Path
    s1_xtbfast_root: Path
    s2_root: Path
    s2_peb_root: Path
    ts_root: Path
    refinements_root: Path
    endpoint_root: Path
    routes_root: Path

    @property
    def study_json(self) -> Path:
        return self.analysis_root / "study.json"

    @property
    def network_json(self) -> Path:
        return self.analysis_root / "network.json"

    @property
    def reaction_json(self) -> Path:
        return self.analysis_root / "reaction.json"

    @property
    def quality_gates_json(self) -> Path:
        return self.analysis_root / "quality_gates.json"

    @property
    def study_events(self) -> Path:
        return self.analysis_root / "events.jsonl"

    @property
    def legacy(self) -> bool:
        return self.analysis_root.parent.name == _LEGACY_DIR

    def rel(self, path: Path) -> str:
        """POSIX path of *path* relative to the task root (remote mirroring)."""
        return path.resolve().relative_to(self.task_root.resolve()).as_posix()


def resolve_study_layout(task_root: Path | str, study_id: str) -> MechanismStudyLayout:
    """Build the NEW v2 layout for a study being created under *task_root*."""
    root = Path(task_root)
    analysis = root / TaskLayout.WORK_DIR_NAME / TaskLayout.stage_analysis
    prepare = root / TaskLayout.WORK_DIR_NAME / TaskLayout.stage_prepare
    search = root / TaskLayout.WORK_DIR_NAME / TaskLayout.stage_search
    path = root / TaskLayout.WORK_DIR_NAME / TaskLayout.stage_path
    opt = root / TaskLayout.WORK_DIR_NAME / TaskLayout.stage_opt
    return MechanismStudyLayout(
        study_id=study_id,
        task_root=root,
        analysis_root=analysis,
        prepare_root=prepare,
        search_root=search,
        path_root=path,
        opt_root=opt,
        states_root=search / "states",
        inputs_root=prepare / "inputs",
        s1_root=search / "s1",
        s1_xtbfast_root=search / "s1_xtbfast",
        s2_root=path / "s2",
        s2_peb_root=path / "s2_peb",
        ts_root=opt / "TS",
        refinements_root=opt / "refinements",
        endpoint_root=path / "sr",
        routes_root=path / "routes",
    )


def _legacy_layout(task_root: Path, legacy_dir: Path, study_id: str) -> MechanismStudyLayout:
    calc = legacy_dir / "calc"
    return MechanismStudyLayout(
        study_id=study_id,
        task_root=task_root,
        analysis_root=legacy_dir,
        prepare_root=legacy_dir,
        search_root=calc,
        path_root=calc,
        opt_root=legacy_dir,
        states_root=legacy_dir / "states",
        inputs_root=legacy_dir / "inputs",
        s1_root=calc / "s1",
        s1_xtbfast_root=calc / "s1_xtbfast",
        s2_root=calc / "s2",
        s2_peb_root=calc / "s2_peb",
        ts_root=calc / "s3s4",
        refinements_root=legacy_dir / "refinements",
        endpoint_root=legacy_dir / "sr",
        routes_root=legacy_dir / "routes",
    )


def find_reaction_json(task_root: Path | str, study_id: str) -> Path | None:
    """Locate a study's ``reaction.json`` even before any checkpoint exists.

    The scheduler materializes ``reaction.json`` pre-run (before
    ``study.json``), so this probes the file itself: new v2 layout first,
    legacy ``mechanism_study/<study_id>/`` second.
    """
    root = Path(task_root)
    new_path = resolve_study_layout(root, study_id).reaction_json
    if new_path.is_file():
        return new_path
    if LEGACY_FALLBACK_ENABLED:
        legacy_path = root / _LEGACY_DIR / study_id / "reaction.json"
        if legacy_path.is_file():
            return legacy_path
    return None


def standalone_layout(root: Path | str, study_id: str) -> MechanismStudyLayout:
    """Standalone flat layout rooted at *root* (research modules, not scheduler tasks)."""
    base = Path(root)
    return _legacy_layout(base, base, study_id)


def find_study_layout(
    task_root: Path | str, study_id: str | None = None
) -> MechanismStudyLayout | None:
    """Locate a study's layout: NEW tree first, legacy ``mechanism_study/`` second."""
    root = Path(task_root)
    new_layout = resolve_study_layout(root, study_id or "")
    if new_layout.study_json.is_file():
        if study_id is None:
            payload = _study_id_of(new_layout.study_json)
            if payload:
                return resolve_study_layout(root, payload)
        return new_layout

    if not LEGACY_FALLBACK_ENABLED:
        return None
    legacy_root = root / _LEGACY_DIR
    if not legacy_root.is_dir():
        return None
    if study_id is not None:
        legacy_dir = legacy_root / study_id
        if (legacy_dir / "study.json").is_file():
            return _legacy_layout(root, legacy_dir, study_id)
        return None
    candidates = sorted(legacy_root.glob("*/study.json"))
    if len(candidates) == 1:
        sid = _study_id_of(candidates[0]) or candidates[0].parent.name
        return _legacy_layout(root, candidates[0].parent, sid)
    if len(candidates) > 1:
        logger.debug("Ambiguous legacy mechanism study under %s", root)
    return None


def _study_id_of(study_json: Path) -> str | None:
    import json

    try:
        payload = json.loads(study_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("study_id"):
        return str(payload["study_id"])
    return None
