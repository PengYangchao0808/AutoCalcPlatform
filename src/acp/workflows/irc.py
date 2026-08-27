"""Standalone workflow adapter for intrinsic reaction coordinate calculations.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from acp.calculations.batch.models import parse_tag_comment
from acp.calculations.checkpoint import write_checkpoint
from acp.calculations.contracts import Checkpoint, JsonValue, StructureArtifact, StructureRole
from acp.calculations.plans import build_irc_request
from acp.calculations.primitives.irc import run_irc
from acp.core.workflow import WorkflowResult
from acp.storage.layout import TaskStorage
from acp.storage.manifest import ProductKind, ResultManifest


def run_irc_workflow(
    input_artifact: StructureArtifact,
    directions: Sequence[str] = ("forward", "reverse"),
    *,
    output_dir: str | Path = "./irc_output",
    config: Mapping[str, JsonValue] | None = None,
    method: str = "r2SCAN-3c",
    basis: str = "",
    maxpoints: int = 100,
    step: float = 0.1,
    input_role: StructureRole | str | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    profile: str = "default",
    resources: Mapping[str, JsonValue] | None = None,
) -> WorkflowResult:
    """Run an independent IRC request from a transition-state artifact."""
    if maxpoints < 1:
        raise ValueError("IRC maxpoints must be at least 1")
    if step <= 0:
        raise ValueError("IRC step must be positive")

    raw_directions = tuple(str(direction).strip().lower() for direction in directions)
    if raw_directions == ("both",):
        normalized_directions = ("forward", "reverse")
    elif raw_directions and all(
        direction in {"forward", "reverse"} for direction in raw_directions
    ):
        normalized_directions = tuple(
            direction for direction in ("forward", "reverse") if direction in raw_directions
        )
    else:
        raise ValueError("IRC directions must be forward, reverse, or both")

    artifact = input_artifact
    if input_role is not None:
        try:
            resolved_role = StructureRole(input_role)
        except (TypeError, ValueError) as exc:
            raise ValueError("IRC input_role must be 'transition_state'") from exc
    else:
        resolved_role = input_artifact.role
        if resolved_role is StructureRole.MINIMUM:
            try:
                lines = input_artifact.path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                lines = []
            tag_info = parse_tag_comment(lines[1] if len(lines) > 1 else "")
            if tag_info["tag"] == "TS":
                resolved_role = StructureRole.TRANSITION_STATE
                artifact = StructureArtifact(
                    path=input_artifact.path,
                    elements=input_artifact.elements,
                    role=resolved_role,
                    source=input_artifact.source,
                    candidate_id=input_artifact.candidate_id or tag_info["candidate_id"],
                )

    if resolved_role is not StructureRole.TRANSITION_STATE:
        raise ValueError(
            "IRC requires a transition-state artifact; use --input-role transition_state"
        )
    if artifact.role is not StructureRole.TRANSITION_STATE:
        artifact = StructureArtifact(
            path=artifact.path,
            elements=artifact.elements,
            role=resolved_role,
            source=artifact.source,
            candidate_id=artifact.candidate_id,
        )

    irc_request = build_irc_request(artifact, normalized_directions)
    output_root = Path(output_dir).expanduser()
    storage = TaskStorage(output_root)
    storage.ensure_layout(stages=["07_PATH"])
    target_dir = storage.stage_dir("07_PATH", "ORCA")

    request_resources: dict[str, JsonValue] = dict(resources or {})
    request_resources.update(
        {
            "backend": "orca",
            "config": dict(config or {}),
            "output_dir": str(target_dir),
            "result_dir": str(storage.result_dir()),
            "basis": basis,
            "max_iter": maxpoints,
            "step": step,
            "charge": charge,
            "multiplicity": multiplicity,
        }
    )
    checkpoint_fingerprint = _irc_checkpoint_fingerprint(
        artifact=irc_request.input_artifact,
        directions=irc_request.directions,
        method=method,
        basis=basis,
        maxpoints=maxpoints,
        step=step,
        charge=charge,
        multiplicity=multiplicity,
        config=config,
        resources=resources,
    )
    _write_irc_checkpoint(storage.runtime_dir(), checkpoint_fingerprint, "running")
    calculation = run_irc(
        irc_request.input_artifact,
        directions=irc_request.directions,
        method=method,
        resources=request_resources,
        workflow=irc_request.workflow,
        profile=profile,
    )
    _write_irc_checkpoint(
        storage.runtime_dir(),
        checkpoint_fingerprint,
        calculation.status,
        calculation.errors,
    )

    irc_dir = storage.result_dir() / "irc"
    irc_dir.mkdir(parents=True, exist_ok=True)
    report_path = irc_dir / "irc_report.json"
    endpoint_paths: dict[str, JsonValue] = {}
    for direction in ("forward", "reverse"):
        endpoint = calculation.metadata.get(f"{direction}_endpoint")
        if isinstance(endpoint, str):
            endpoint_paths[direction] = endpoint
    report: dict[str, JsonValue] = {
        "workflow": irc_request.workflow,
        "status": calculation.status,
        "input_artifact": str(irc_request.input_artifact.path),
        "input_role": irc_request.input_role.value,
        "directions": list(irc_request.directions),
        "method": method,
        "basis": basis,
        "maxpoints": maxpoints,
        "step": step,
        "endpoints": endpoint_paths,
        "endpoint_count": calculation.metadata.get("endpoint_count", 0),
        "errors": list(calculation.errors),
    }
    report_tmp = report_path.with_name(report_path.name + ".tmp")
    _ = report_tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(report_tmp, report_path)

    try:
        manifest = ResultManifest.read(storage.result_dir())
    except FileNotFoundError:
        manifest = ResultManifest(workflow=irc_request.workflow)
    manifest.workflow = irc_request.workflow
    manifest.status = calculation.status
    _ = manifest.add_product(
        id="irc_report",
        label="IRC report",
        path="irc/irc_report.json",
        kind=ProductKind.REPORT,
    )
    manifest_path = manifest.write(storage.result_dir())

    metadata = dict(calculation.metadata)
    metadata.update(
        {
            "output_dir": str(output_root),
            "report_path": str(report_path),
            "manifest_path": str(manifest_path),
        }
    )
    return WorkflowResult(
        status=calculation.status,
        stages_completed=["irc"] if calculation.status == "completed" else [],
        error="; ".join(calculation.errors) if calculation.errors else None,
        metadata=metadata,
    )


def _irc_checkpoint_fingerprint(
    *,
    artifact: StructureArtifact,
    directions: tuple[str, ...],
    method: str,
    basis: str,
    maxpoints: int,
    step: float,
    charge: int,
    multiplicity: int,
    config: Mapping[str, JsonValue] | None,
    resources: Mapping[str, JsonValue] | None,
) -> str:
    payload = {
        "workflow": "irc",
        "input_artifact": str(artifact.path),
        "input_role": artifact.role.value,
        "directions": list(directions),
        "method": method,
        "basis": basis,
        "maxpoints": maxpoints,
        "step": step,
        "charge": charge,
        "multiplicity": multiplicity,
        "config": dict(config or {}),
        "resources": dict(resources or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_irc_checkpoint(
    runtime_dir: Path,
    fingerprint: str,
    status: str,
    errors: Sequence[str] = (),
) -> None:
    write_checkpoint(
        runtime_dir,
        Checkpoint(
            task_id="irc",
            workflow="irc",
            plan_fingerprint=fingerprint,
            step_states=[
                {
                    "index": 0,
                    "kind": "irc",
                    "status": status,
                    "error": "; ".join(errors) if errors else None,
                }
            ],
            items_state={},
            attempts=0,
        ),
    )


__all__ = ["run_irc_workflow"]
