"""``mech-chain``: declarative composition of standalone mechanism modules (M4)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .modules.module_confirm import run_confirm_module
from .modules.module_conformer import run_conformer_module
from .modules.module_step import run_step_module
from .modules.schema import (
    ELEMENTARY_STEP_FILENAME,
    MANIFEST_FILENAME,
    ElementaryStepManifest,
    ModuleManifest,
)

logger = logging.getLogger(__name__)

_MODULE_RUNNERS = {
    "mech-conf": run_conformer_module,
    "mech-step": run_step_module,
    "mech-confirm": run_confirm_module,
}

_INTERPOLATION_RE = re.compile(r"\$\{([^}]+)\}")


def _step_output(
    manifest: ModuleManifest | ElementaryStepManifest,
    manifest_path: Path,
) -> dict[str, Any]:
    if isinstance(manifest, ModuleManifest):
        return dict(manifest.output)
    output: dict[str, Any] = {"step_manifest": str(manifest_path)}
    if manifest.transition_state:
        output["transition_state"] = {"xyz": manifest.transition_state.get("xyz")}
    sink_xyz = _sink_xyz(manifest)
    if sink_xyz:
        output["sink_xyz"] = sink_xyz
    return output


def _sink_xyz(manifest: ElementaryStepManifest) -> str | None:
    endpoints = (manifest.irc or {}).get("endpoints") or {}
    for direction in ("forward", "reverse"):
        data = endpoints.get(direction)
        if isinstance(data, dict) and data.get("role") == "sink":
            raw = data.get("raw_geometry") or {}
            if isinstance(raw, dict) and raw.get("path"):
                return str(raw["path"])
    return None


def _lookup(expr: str, context: dict[str, Any]) -> Any:
    root, _, rest = expr.partition(".")
    match = re.fullmatch(r"steps\[(\d+)\]", root)
    if match:
        value: Any = context["steps"][int(match.group(1))]
    elif root == "prev":
        value = context["prev"]
    else:
        raise ValueError(f"Unknown interpolation root in ${{{expr}}}")
    for token in rest.split(".") if rest else []:
        if not isinstance(value, dict) or token not in value:
            raise ValueError(f"Cannot resolve ${{{expr}}}: missing key {token!r}")
        value = value[token]
    return value


def _interpolate(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        matches = list(_INTERPOLATION_RE.finditer(value))
        if not matches:
            return value
        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            return _lookup(matches[0].group(1), context)

        def _sub(match: re.Match[str]) -> str:
            return str(_lookup(match.group(1), context))

        return _INTERPOLATION_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {key: _interpolate(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, context) for item in value]
    return value


def run_chain(
    chain_config: dict[str, Any],
    *,
    providers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the declared module sequence; return one record per step."""
    providers = providers or {}
    records: list[dict[str, Any]] = []
    step_contexts: list[dict[str, Any]] = []
    for step in chain_config.get("steps") or []:
        module = str(step.get("module") or "")
        runner = _MODULE_RUNNERS.get(module)
        if runner is None:
            raise ValueError(f"Unknown chain module: {module!r}")
        context = {
            "prev": step_contexts[-1] if step_contexts else {},
            "steps": step_contexts,
        }
        args = _interpolate(dict(step.get("args") or {}), context)
        if "output_dir" in args:
            args["output_dir"] = Path(str(args["output_dir"]))
        if module == "mech-conf" and "ensemble_provider" in providers:
            args["ensemble_provider"] = providers["ensemble_provider"]
        elif module == "mech-step" and "step_providers" in providers:
            args["providers"] = providers["step_providers"]
        elif module == "mech-confirm" and "refinement_provider" in providers:
            args["refinement_provider"] = providers["refinement_provider"]
        manifest: ModuleManifest | ElementaryStepManifest = runner(**args)
        filename = (
            ELEMENTARY_STEP_FILENAME
            if isinstance(manifest, ElementaryStepManifest)
            else MANIFEST_FILENAME
        )
        manifest_path = Path(str(args.get("output_dir") or ".")) / filename
        record = {
            "module": module,
            "status": manifest.status,
            "manifest_path": str(manifest_path),
        }
        records.append(record)
        step_contexts.append(
            {
                "manifest": str(manifest_path),
                "output": _step_output(manifest, manifest_path),
            }
        )
        logger.info("mech-chain step %s -> %s", module, manifest.status)
    return records


def run_chain_from_yaml(
    path: Path | str,
    *,
    providers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load a chain config from YAML and run it."""
    import yaml

    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Chain config must be a mapping: {path}")
    return run_chain(config, providers=providers)


__all__ = ["run_chain", "run_chain_from_yaml"]
