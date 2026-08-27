# pyright: reportAny=false
"""Read-only probing for current and historical mechanism layouts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, TypeGuard

from acp.storage.layout import TaskLayout

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

_LEGACY_DIR: Final = "mechanism_study"
_STUDY_JSON: Final = "study.json"
_REACTION_JSON: Final = "reaction.json"

__all__ = [
    "LEGACY_FALLBACK_ENABLED",
    "MechanismStudyLayout",
    "find_reaction_json",
    "find_study_layout",
]

LEGACY_FALLBACK_ENABLED = True


@dataclass(frozen=True, slots=True)
class MechanismStudyLayout:
    """Read-only path projection for one mechanism study."""

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
        """Return the study checkpoint path."""
        return self.analysis_root / _STUDY_JSON

    @property
    def network_json(self) -> Path:
        """Return the reaction-network checkpoint path."""
        return self.analysis_root / "network.json"

    @property
    def reaction_json(self) -> Path:
        """Return the reaction-definition path."""
        return self.analysis_root / _REACTION_JSON

    @property
    def quality_gates_json(self) -> Path:
        """Return the quality-gates checkpoint path."""
        return self.analysis_root / "quality_gates.json"

    @property
    def study_events(self) -> Path:
        """Return the study event-log path."""
        return self.analysis_root / "events.jsonl"

    @property
    def legacy(self) -> bool:
        """Return whether this projection uses the historical directory."""
        return self.analysis_root.parent.name == _LEGACY_DIR

    def rel(self, path: Path) -> str:
        """Return *path* relative to the task root using POSIX separators."""
        return path.resolve().relative_to(self.task_root.resolve()).as_posix()


def _v2_task_root(source: Path) -> Path:
    for candidate in (source, *source.parents):
        if candidate.name == TaskLayout.WORK_DIR_NAME:
            return candidate.parent
    return source


def _legacy_root_for_source(source: Path) -> Path:
    for candidate in (source, *source.parents):
        if candidate.name == _LEGACY_DIR:
            return candidate
    return source / _LEGACY_DIR


def _v2_layout(task_root: Path, study_id: str) -> MechanismStudyLayout:
    work = task_root / TaskLayout.WORK_DIR_NAME
    prepare = work / TaskLayout().stage_prepare
    search = work / TaskLayout().stage_search
    path = work / TaskLayout().stage_path
    opt = work / TaskLayout().stage_opt
    analysis = work / TaskLayout().stage_analysis
    return MechanismStudyLayout(
        study_id=study_id,
        task_root=task_root,
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


def _is_json_object(value: JsonValue) -> TypeGuard[JsonObject]:
    return isinstance(value, dict)


def _study_id_of(study_json: Path) -> str | None:
    try:
        payload: JsonValue = json.loads(study_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not _is_json_object(payload):
        return None
    value = payload.get("study_id")
    return str(value) if value else None


def find_reaction_json(task_root: Path | str, study_id: str) -> Path | None:
    """Locate ``reaction.json`` without creating or rewriting any path."""
    source = Path(task_root)
    new_path = _v2_layout(_v2_task_root(source), study_id).reaction_json
    if new_path.is_file():
        return new_path
    if not LEGACY_FALLBACK_ENABLED:
        return None
    legacy_root = _legacy_root_for_source(source)
    legacy_path = legacy_root / study_id / _REACTION_JSON
    return legacy_path if legacy_path.is_file() else None


def find_study_layout(
    task_root: Path | str, study_id: str | None = None
) -> MechanismStudyLayout | None:
    """Locate a v2 study first, then a historical ``mechanism_study`` study."""
    source = Path(task_root)
    v2_root = _v2_task_root(source)
    new_layout = _v2_layout(v2_root, study_id or "")
    if new_layout.study_json.is_file():
        if study_id is None:
            payload_id = _study_id_of(new_layout.study_json)
            if payload_id:
                return _v2_layout(v2_root, payload_id)
        return new_layout

    if not LEGACY_FALLBACK_ENABLED:
        return None
    legacy_root = _legacy_root_for_source(source)
    if not legacy_root.is_dir():
        return None
    if study_id is not None:
        legacy_dir = legacy_root / study_id
        if (legacy_dir / _STUDY_JSON).is_file():
            return _legacy_layout(legacy_root.parent, legacy_dir, study_id)
        return None
    candidates = sorted(legacy_root.glob(f"*/{_STUDY_JSON}"))
    if len(candidates) == 1:
        candidate = candidates[0]
        resolved_id = _study_id_of(candidate) or candidate.parent.name
        return _legacy_layout(legacy_root.parent, candidate.parent, resolved_id)
    if len(candidates) > 1:
        logger.debug("Ambiguous legacy mechanism study under %s", legacy_root.parent)
    return None
