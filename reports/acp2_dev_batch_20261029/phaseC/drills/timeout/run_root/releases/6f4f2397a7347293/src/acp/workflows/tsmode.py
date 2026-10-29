"""TS Mode workflow adapter (``acp run tsmode``).

Thin adapter per plan §13: parses the CLI/API bundle description
(``bundle.json``), loads + validates the frequency source, and delegates
stage execution to :class:`acp.calculations.tsmode.engine.TsmodeEngine`.
GUI and CLI share this path — the CLI cannot bypass mapping validation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from acp.calculations.tsmode.contracts import (
    FrequencySourceBundle,
    SourceLevelOfTheory,
    TsmodeOptimizationSettings,
    TsmodeRequest,
)
from acp.calculations.tsmode.engine import TsmodeEngine
from acp.calculations.tsmode.source import load_bundle_from_files
from acp.core.workflow import WorkflowResult
from acp.workflows.simple import _calc_subdir, _resolve_output_dir
from cccp.config import load_config

logger = logging.getLogger(__name__)

__all__ = ["build_tsmode_request", "load_source_bundle_description", "run_tsmode"]

_BUNDLE_SCHEMA = "tsmode_bundle_v1"


def load_source_bundle_description(path: str | Path) -> dict[str, Any]:
    """Read and shape-check a ``bundle.json`` source description (plan §8)."""
    bundle_path = Path(path)
    if not bundle_path.is_file():
        raise FileNotFoundError(f"source bundle not found: {bundle_path}")
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"source bundle is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("source bundle must be a JSON object")
    if payload.get("schema_version") not in (None, _BUNDLE_SCHEMA):
        raise ValueError(
            f"unsupported source bundle schema_version {payload.get('schema_version')!r}"
        )
    files = payload.get("files")
    if not isinstance(files, dict) or not isinstance(files.get("hessian"), str):
        raise ValueError("source bundle requires files.hessian")
    if not isinstance(files.get("output"), str):
        raise ValueError("source bundle requires files.output")
    return payload


def build_tsmode_request(
    payload: dict[str, Any],
    source_mode_index: int,
    *,
    resources: dict[str, Any] | None = None,
    request_id: str = "",
) -> TsmodeRequest:
    """Build a :class:`TsmodeRequest` from a bundle description."""
    optimization_payload = payload.get("optimization") or {}
    if not isinstance(optimization_payload, dict):
        optimization_payload = {}
    settings = TsmodeOptimizationSettings(
        max_iterations=_optional_int(optimization_payload, "max_iterations"),
        convergence=_optional_str(optimization_payload, "convergence"),
        recalc_hess=_optional_int(optimization_payload, "recalc_hess"),
        trust_radius=_optional_float(optimization_payload, "trust_radius"),
        retry_limit=_optional_int(optimization_payload, "retry_limit") or 2,
        require_verified_mapping=not bool(optimization_payload.get("allow_unverified_mapping")),
    )
    source = dict(payload.get("source") or {})
    source.setdefault("kind", "bundle_file")
    final_frequency = payload.get("final_frequency")
    if final_frequency is None:
        final_frequency = optimization_payload.get("final_frequency")
    return TsmodeRequest(
        source=source,
        source_mode_index=source_mode_index,
        optimization=settings,
        final_frequency=bool(final_frequency) if final_frequency is not None else True,
        resources=dict(resources or {}),
        request_id=request_id,
    )


def load_bundle_from_description(
    payload: dict[str, Any], *, bundle_dir: str | Path | None = None
) -> FrequencySourceBundle:
    """Load the validated source bundle referenced by a bundle description.

    Relative file references resolve against *bundle_dir* (default: the
    directory of the description file) so remotely synced tasks work
    without absolute local paths.
    """
    files = payload["files"]
    base = Path(bundle_dir) if bundle_dir is not None else Path(payload.get("_dir", "."))

    def _resolve(reference: str) -> str:
        path = Path(reference)
        if path.is_absolute() or path.is_file():
            return str(path)
        return str((base / path).resolve())

    level_payload = payload.get("level") or {}
    level = SourceLevelOfTheory.from_dict(level_payload) if level_payload else None
    return load_bundle_from_files(
        _resolve(files["output"]),
        _resolve(files["hessian"]),
        geometry_path=_resolve(files["geometry"]) if files.get("geometry") else None,
        charge=payload.get("charge") if isinstance(payload.get("charge"), int) else None,
        multiplicity=(
            payload.get("multiplicity") if isinstance(payload.get("multiplicity"), int) else None
        ),
        level=level,
        origin=payload.get("origin"),
    )


def run_tsmode(
    source_bundle: str | Path,
    source_mode_index: int,
    output_dir: str | Path = "./tsmode_output",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    *,
    optimization_overrides: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    progress_reporter: Any = None,
) -> WorkflowResult:
    """Run the complete TS Mode workflow from a bundle description file.

    *optimization_overrides* (CLI/API flags) win over the bundle's
    ``optimization`` section.
    """
    payload = load_source_bundle_description(source_bundle)
    if optimization_overrides:
        merged = dict(payload.get("optimization") or {})
        merged.update(
            {key: value for key, value in optimization_overrides.items() if value is not None}
        )
        payload["optimization"] = merged
    payload["_dir"] = str(Path(source_bundle).resolve().parent)
    request = build_tsmode_request(
        payload,
        source_mode_index,
        resources=resources,
        request_id=str(payload.get("request_id") or ""),
    )
    bundle = load_bundle_from_description(payload)

    cfg = load_config(overrides=config) if config else load_config()
    task_root = _calc_subdir(_resolve_output_dir(output_dir), name, str(source_bundle), "tsmode")
    engine = TsmodeEngine(config=cfg)
    result = engine.run(
        request,
        bundle,
        task_root,
        progress_reporter=progress_reporter,
    )
    return result.workflow_result


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    return None
