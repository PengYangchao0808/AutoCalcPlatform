"""Provenance and result schema helpers for scheduler jobs."""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import acp
from acp.scheduler.jobs import JobRecord, JobSpec


class ParserStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class Provenance:
    input_hash: str
    molecule_hash: str | None = None
    acp_version: str = ""
    backend_name: str = ""
    backend_version: str = ""
    method: str = ""
    basis: str | None = None
    solvent: str | None = None
    command_line: str = ""
    hostname: str = ""
    ncores: int | None = None
    memory_gb: float | None = None
    wall_time_seconds: float | None = None
    routine: str | None = None
    creator: str | None = None
    schema_version: str = "1.0"


@dataclass
class ResultSchema:
    success: bool
    exit_status: int = 0
    return_value: float | list[float] | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    error: dict[str, str] | None = None
    schema_name: str = "acp_result"
    schema_version: str = "1.0"


class ParserRegistry:
    """Registry of artifact parsers keyed by artifact type."""

    def __init__(self) -> None:
        self._parsers: dict[str, Callable[[Path], dict[str, Any]]] = {}

    def register(self, artifact_type: str, parser: Callable[[Path], dict[str, Any]]) -> None:
        self._parsers[_normalize_artifact_type(artifact_type)] = parser

    def parse(self, artifact_type: str, filepath: Path) -> dict[str, Any]:
        key = _normalize_artifact_type(artifact_type)
        parser = self._parsers.get(key)
        if parser is None:
            raise KeyError(f"No parser registered for artifact type: {artifact_type}")
        return parser(filepath)

    def has_parser(self, artifact_type: str) -> bool:
        return _normalize_artifact_type(artifact_type) in self._parsers


def compute_input_hash(spec: JobSpec) -> str:
    """Compute a stable SHA256 hash of the input-defining portion of a job spec."""
    canonical = {
        "workflow": spec.workflow,
        "input": _canonicalize(spec.input),
        "method": _canonicalize(spec.method),
        "resources": _canonicalize(spec.resources),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_provenance_for_job(spec: JobSpec, record: JobRecord) -> Provenance:
    """Build a provenance record from scheduler job metadata."""
    result = record.result if isinstance(record.result, dict) else {}
    input_hash = record.input_hash or spec.input_hash or compute_input_hash(spec)
    backend_name = _string_or_none(result.get("backend_name")) or _default_backend_name(spec)
    method = _string_or_none(result.get("method")) or _default_method(spec)
    command_line = _string_or_none(result.get("command_line")) or ""
    if not command_line and spec.workflow == "fake":
        command_line = "in-process fake workflow"

    memory_value = (
        spec.resources.get("memory_gb")
        if spec.resources.get("memory_gb") is not None
        else spec.resources.get("mem")
    )
    return Provenance(
        input_hash=input_hash,
        molecule_hash=_string_or_none(result.get("molecule_hash")),
        acp_version=getattr(acp, "__version__", ""),
        backend_name=backend_name,
        backend_version=_string_or_none(result.get("backend_version")) or "",
        method=method,
        basis=_string_or_none(spec.method.get("basis")),
        solvent=_string_or_none(spec.method.get("solvent")),
        command_line=command_line,
        hostname=socket.gethostname(),
        ncores=_coerce_int(spec.resources.get("nproc") or spec.resources.get("ncores")),
        memory_gb=_coerce_memory_gb(memory_value),
        wall_time_seconds=_compute_wall_time_seconds(record.started_at, record.completed_at),
        routine=_string_or_none(result.get("routine")) or spec.workflow,
        creator=_string_or_none(result.get("creator")),
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


def _normalize_artifact_type(artifact_type: str) -> str:
    return artifact_type.strip().lower()


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_memory_gb(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        return None
    factors = (
        ("tb", 1024.0),
        ("t", 1024.0),
        ("gb", 1.0),
        ("g", 1.0),
        ("mb", 1.0 / 1024.0),
        ("m", 1.0 / 1024.0),
    )
    for suffix, factor in factors:
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                return float(number) * factor
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def _compute_wall_time_seconds(
    started_at: str | None, completed_at: str | None
) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return max((end - start).total_seconds(), 0.0)


def _default_backend_name(spec: JobSpec) -> str:
    if spec.workflow == "fake":
        return "fake"
    return _string_or_none(spec.method.get("backend")) or spec.workflow


def _default_method(spec: JobSpec) -> str:
    if spec.workflow == "fake":
        return "demo"
    return (
        _string_or_none(spec.method.get("protocol"))
        or _string_or_none(spec.method.get("method"))
        or spec.workflow
    )


__all__ = [
    "ParserRegistry",
    "ParserStatus",
    "Provenance",
    "ResultSchema",
    "build_provenance_for_job",
    "compute_input_hash",
]
