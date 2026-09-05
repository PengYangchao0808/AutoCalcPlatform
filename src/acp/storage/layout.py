"""v2 task-storage layout constants and path resolution (design doc §3–§5, §7)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from acp.storage.record import TaskRecord

logger = logging.getLogger(__name__)

__all__ = [
    "TASK_DIR_NAME_MAX_LEN",
    "TaskLayout",
    "TaskStorage",
    "is_v2_task_dir",
    "runtime_file",
    "sanitize_task_dir_name",
]

TASK_DIR_NAME_MAX_LEN = 100

_FORBIDDEN_CHARS = re.compile(r'[/\\:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")
_REPEAT_UNDERSCORE = re.compile(r"_+")


@dataclass(frozen=True)
class TaskLayout:
    """Immutable v2 task-layout constants (design doc §3, §5, §7)."""

    stage_runtime: str = "00_RUNTIME"
    stage_prepare: str = "01_PREPARE"
    stage_search: str = "02_SEARCH"
    stage_opt: str = "03_OPT"
    stage_freq: str = "04_FREQ"
    stage_sp: str = "05_SP"
    stage_thermo: str = "06_THERMO"
    stage_path: str = "07_PATH"
    stage_analysis: str = "08_ANALYSIS"

    category_structures: str = "structures"
    category_energies: str = "energies"
    category_frequencies: str = "frequencies"
    category_trajectories: str = "trajectories"
    category_ensembles: str = "ensembles"
    category_mechanism: str = "mechanism"
    category_reports: str = "reports"

    WORK_DIR_NAME: ClassVar[str] = "WORK"
    RESULT_DIR_NAME: ClassVar[str] = "RESULT"
    TASK_JSON_NAME: ClassVar[str] = "task.json"
    INPUT_XYZ_NAME: ClassVar[str] = "input.xyz"
    INPUT_SOURCE_JSON_NAME: ClassVar[str] = "input_source.json"
    RESULT_MANIFEST_NAME: ClassVar[str] = "result_manifest.json"

    WORK_STAGES: ClassVar[tuple[str, ...]] = (
        "00_RUNTIME",
        "01_PREPARE",
        "02_SEARCH",
        "03_OPT",
        "04_FREQ",
        "05_SP",
        "06_THERMO",
        "07_PATH",
        "08_ANALYSIS",
    )
    RESULT_CATEGORIES: ClassVar[tuple[str, ...]] = (
        "structures",
        "energies",
        "frequencies",
        "trajectories",
        "ensembles",
        "mechanism",
        "reports",
    )


def _sanitize_component(text: str) -> str:
    """Normalise one name component per §4.3 (whitespace→_, forbidden chars→_)."""
    cleaned = _WHITESPACE.sub("_", text.strip())
    cleaned = _FORBIDDEN_CHARS.sub("_", cleaned)
    cleaned = _REPEAT_UNDERSCORE.sub("_", cleaned).strip("_")
    return cleaned


def sanitize_task_dir_name(molecule_name: str, task_name: str, remark: str = "") -> str:
    """Build a v2 task directory name ``<molecule>_<task>_<remark>`` (design doc §4.3).

    Args:
        molecule_name: Molecule name component (required).
        task_name: Calculation task name component (required).
        remark: Optional remark component; omitted from the name when empty.

    Returns:
        Sanitised directory name, truncated to ``TASK_DIR_NAME_MAX_LEN``.

    Raises:
        ValueError: If molecule or task name is empty after sanitisation.
    """
    molecule = _sanitize_component(molecule_name)
    task = _sanitize_component(task_name)
    if not molecule:
        raise ValueError(f"molecule_name is empty after sanitisation: {molecule_name!r}")
    if not task:
        raise ValueError(f"task_name is empty after sanitisation: {task_name!r}")
    parts = [molecule, task]
    cleaned_remark = _sanitize_component(remark) if remark else ""
    if cleaned_remark:
        parts.append(cleaned_remark)
    name = "_".join(parts)
    if len(name) > TASK_DIR_NAME_MAX_LEN:
        name = name[:TASK_DIR_NAME_MAX_LEN].rstrip("_")
    return name


def is_v2_task_dir(path: Path | str) -> bool:
    """Return True if *path* looks like a v2 task dir (has ``WORK/`` or v2 ``task.json``)."""
    root = Path(path)
    if not root.is_dir():
        return False
    if (root / TaskLayout.WORK_DIR_NAME).is_dir():
        return True
    task_json = root / TaskLayout.TASK_JSON_NAME
    if task_json.is_file():
        try:
            payload = json.loads(task_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("is_v2_task_dir: unreadable task.json at %s: %s", task_json, exc)
            return False
        return isinstance(payload, dict) and payload.get("layout_version") == 2
    return False


def runtime_file(work_dir: Path | str, name: str) -> Path:
    """Resolve a scheduler runtime file (stdout.log / events.jsonl / ...) with dual-layout fallback.

    v2 task dirs (``WORK/`` present) serve runtime files from
    ``WORK/00_RUNTIME``; legacy dirs keep them at the task root, so readers
    work unchanged for pre-migration jobs (design doc §6 / Phase 6 compat).
    """
    root = Path(work_dir)
    if (root / TaskLayout.WORK_DIR_NAME).is_dir():
        return root / TaskLayout.WORK_DIR_NAME / TaskLayout().stage_runtime / name
    return root / name


def _write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via a sibling tmp file + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class TaskStorage:
    """Path resolver and scaffolder bound to one v2 task directory (design doc §5)."""

    def __init__(self, task_dir: Path | str) -> None:
        self._root = Path(task_dir)

    @property
    def root(self) -> Path:
        """Task directory root."""
        return self._root

    def work_dir(self) -> Path:
        """``WORK/`` — real computation workspace."""
        return self._root / TaskLayout.WORK_DIR_NAME

    def result_dir(self) -> Path:
        """``RESULT/`` — final results and visualisation data."""
        return self._root / TaskLayout.RESULT_DIR_NAME

    def runtime_dir(self) -> Path:
        """``WORK/00_RUNTIME`` — stdout/stderr/events/runtime metadata."""
        return self.work_dir() / TaskLayout().stage_runtime

    def stage_dir(self, stage: str, engine: str | None = None) -> Path:
        """``WORK/<stage>`` or ``WORK/<stage>/<engine>``.

        Args:
            stage: One of :data:`TaskLayout.WORK_STAGES`.
            engine: Optional engine/sub-area subdir (``ORCA``, ``xTB``, ``TS``,
                ``route01``, ...); free-form, sanitised for path safety.

        Raises:
            ValueError: On unknown stage or unsafe engine name.
        """
        if stage not in TaskLayout.WORK_STAGES:
            raise ValueError(
                f"unknown WORK stage {stage!r}; expected one of {TaskLayout.WORK_STAGES}"
            )
        base = self.work_dir() / stage
        if engine is None:
            return base
        if not engine or Path(engine).name != engine or engine in {".", ".."}:
            raise ValueError(f"unsafe engine subdir name: {engine!r}")
        return base / engine

    def result_category_dir(self, category: str) -> Path:
        """``RESULT/<category>`` for one of :data:`TaskLayout.RESULT_CATEGORIES`."""
        if category not in TaskLayout.RESULT_CATEGORIES:
            raise ValueError(
                f"unknown RESULT category {category!r}; "
                f"expected one of {TaskLayout.RESULT_CATEGORIES}"
            )
        return self.result_dir() / category

    def input_xyz(self) -> Path:
        """Task-root ``input.xyz``."""
        return self._root / TaskLayout.INPUT_XYZ_NAME

    def input_source_json(self) -> Path:
        """Task-root ``input_source.json`` (SMILES provenance)."""
        return self._root / TaskLayout.INPUT_SOURCE_JSON_NAME

    def task_json(self) -> Path:
        """Task-root ``task.json``."""
        return self._root / TaskLayout.TASK_JSON_NAME

    def result_manifest_json(self) -> Path:
        """``RESULT/result_manifest.json``."""
        return self.result_dir() / TaskLayout.RESULT_MANIFEST_NAME

    def ensure_layout(
        self,
        stages: list[str] | tuple[str, ...] | None = None,
        categories: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Create the task scaffold: root, ``WORK/``, ``WORK/00_RUNTIME``, ``RESULT/``.

        Only the explicitly requested stage/category dirs are created (design doc
        §6: "只创建任务实际使用的阶段目录").
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self.runtime_dir().mkdir(parents=True, exist_ok=True)
        self.result_dir().mkdir(parents=True, exist_ok=True)
        for stage in stages or ():
            self.stage_dir(stage).mkdir(parents=True, exist_ok=True)
        for category in categories or ():
            self.result_category_dir(category).mkdir(parents=True, exist_ok=True)

    def write_input_xyz(self, text: str) -> Path:
        """Atomically write the task-root ``input.xyz``."""
        path = self.input_xyz()
        _write_text_atomic(path, text)
        return path

    def write_input_source_json(self, payload: dict[str, Any]) -> Path:
        """Atomically write ``input_source.json`` (SMILES/input provenance)."""
        path = self.input_source_json()
        _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True, default=str))
        return path

    def write_task_json(self, record: TaskRecord) -> Path:
        """Atomically write ``task.json`` from a :class:`TaskRecord`."""
        path = self.task_json()
        payload = json.dumps(record.to_dict(), indent=2, sort_keys=True, default=str)
        _write_text_atomic(path, payload)
        return path
