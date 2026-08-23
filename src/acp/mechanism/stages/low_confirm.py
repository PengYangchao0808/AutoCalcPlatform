"""Lowconfirm (S3) — coarse optimization + frequency + preliminary IRC.

Reads ``s2_path_manifest.json``, refines the selected candidates at the s3
fidelity through the shared :class:`ConfirmEngine`, and writes
``s3_lowconfirm_manifest.json``. IRC runs by default on the canonical TS
(plan §6.3 — PESsearch discovers, Lowconfirm confirms coarsely,
Highconfirm confirms finally).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import source_artifact_ref, utc_now_iso

from .._helpers import fingerprint
from ..models import ArtifactRef, Provenance, StationaryPointRequest
from .confirm import ConfirmEngine, ConfirmProfile, LowConfirmProfile

logger = logging.getLogger(__name__)

S3_SCHEMA_VERSION = "s3_lowconfirm_v1"
S3_MANIFEST_NAME = "s3_lowconfirm_manifest.json"


def read_s2_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S2 manifest is not a JSON object: {path}")
    if payload.get("schema_version") != "s2_path_v1" or payload.get("workflow") != "PESsearch":
        raise ValueError(f"Not a PESsearch s2_path manifest: {path}")
    return payload


def _resolve_candidate_xyz(manifest_path: Path, ref: str) -> Path:
    candidate = (manifest_path.parent / ref).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"S2 candidate geometry missing: {candidate}")
    return candidate


def _select_candidates(payload: dict[str, Any], select: list[str]) -> list[dict[str, Any]]:
    rows = payload.get("candidates") or []
    if not rows:
        raise ValueError("S2 manifest carries no candidates")
    if not select:
        ts_rows = [row for row in rows if row.get("kind") == "ts_seed"]
        return ts_rows or rows
    by_id = {str(row.get("id")): row for row in rows}
    missing = [cid for cid in select if cid not in by_id]
    if missing:
        raise ValueError(f"Unknown candidate ids in S2 manifest: {', '.join(missing)}")
    return [by_id[cid] for cid in select]


def _build_requests(
    rows: list[dict[str, Any]],
    manifest_path: Path,
    charge: int,
    multiplicity: int,
    coordinate_plan: Any,
) -> list[StationaryPointRequest]:
    requests: list[StationaryPointRequest] = []
    for row in rows:
        xyz_path = _resolve_candidate_xyz(manifest_path, str(row.get("xyz") or ""))
        kind = "ts" if row.get("kind") == "ts_seed" else "minimum"
        role = "transition_state" if kind == "ts" else "intermediate"
        requests.append(
            StationaryPointRequest(
                id=str(row["id"]),
                role=role,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                input_geometry=ArtifactRef(
                    path=str(xyz_path),
                    sha256="",
                    kind="s2_candidate_geometry",
                ),
                coordinate_plan=coordinate_plan,
                fallback_geometries=[],
                source_stage="S2",
                charge=charge,
                multiplicity=multiplicity,
                atom_mapping=None,
                parent_state_id=None,
                route_id=str(payload_route_id(row)),
                ensemble_correction=None,
                provenance=Provenance(
                    provider="acp-lowconfirm",
                    provider_version="1.0",
                    provider_commit="s3",
                    strategy="low-confirmation",
                    strategy_version="1.0",
                    profile_id="s3",
                    schema_version="m0",
                    input_signature=str(row.get("source_seed_id") or row["id"]),
                ),
            )
        )
    return requests


def payload_route_id(row: dict[str, Any]) -> Any:
    return row.get("route_id") or "route_001"


def run_low_confirm(
    *,
    from_manifest: Path | str,
    output_dir: Path | str,
    select: list[str] | None = None,
    run_irc: bool = True,
    source_job_id: str | None = None,
    source_relative_path: str = "RESULT/mechanism/s2_path_manifest.json",
    charge: int | None = None,
    multiplicity: int | None = None,
    config: dict[str, Any] | None = None,
    refinement_provider: Any | None = None,
    endpoint_provider: Any | None = None,
) -> dict[str, Any]:
    """Run the S3 coarse confirmation; write ``s3_lowconfirm_manifest.json``."""
    manifest_path = Path(from_manifest).resolve()
    payload_in = read_s2_manifest(manifest_path)
    resolved_charge = charge if charge is not None else int(payload_in.get("charge") or 0)
    resolved_multiplicity = (
        multiplicity if multiplicity is not None else int(payload_in.get("multiplicity") or 1)
    )

    rows = _select_candidates(payload_in, [str(s) for s in select or []])

    from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

    plan_payload = (payload_in.get("route") or {}).get("coordinate_plan") or {}
    plan = ReactionCoordinatePlan.from_dict(plan_payload) if plan_payload else None

    out_root = Path(output_dir).resolve()
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)

    requests = _build_requests(rows, manifest_path, resolved_charge, resolved_multiplicity, plan)
    profile: ConfirmProfile = LowConfirmProfile(run_irc=run_irc)
    engine = ConfirmEngine(
        config=config,
        work_root=out_root / "WORK" / "03_OPT",
        profile=profile,
        refinement_provider=refinement_provider,
        endpoint_provider=endpoint_provider,
    )
    outcome = engine.confirm(requests)

    candidates_out = []
    for candidate in outcome.candidates:
        row = _result_row(result_dir, candidate, manifest_path)
        candidates_out.append(row)

    confirmed = [row for row in candidates_out if row["status"] == "confirmed"]
    ts_rows = [row for row in confirmed if row["kind"] == "ts"]
    gates = {
        "optimization_converged": bool(confirmed)
        and all(row["opt_converged"] for row in confirmed),
        "frequency_valid": any(row["frequency"].get("status") == "complete" for row in confirmed),
        "ts_first_order_saddle": (
            all(
                (row["frequency"].get("n_imaginary") or 0) == 1
                for row in ts_rows
                if row["frequency"].get("n_imaginary") is not None
            )
            if ts_rows
            else None
        ),
        "irc_completed": (bool(outcome.irc and outcome.irc.get("complete")) if run_irc else None),
    }
    gates["G3"] = "PASS" if gates["optimization_converged"] and gates["frequency_valid"] else "FAIL"

    payload = {
        "schema_version": S3_SCHEMA_VERSION,
        "workflow": "Lowconfirm",
        "stage": "S3",
        "created_at": utc_now_iso(),
        "source": source_artifact_ref(
            source_job_id,
            source_relative_path,
            manifest_path,
            kind="s2_path_manifest",
            stage="S2",
        ),
        "profile": {
            "level": "low",
            "opt_method": profile.opt_method,
            "freq_method": profile.freq_method,
            "sp_method": profile.sp_method,
            "max_cycles": profile.max_cycles,
            "irc": run_irc,
        },
        "charge": resolved_charge,
        "multiplicity": resolved_multiplicity,
        "candidates": candidates_out,
        "irc": outcome.irc,
        "gates": gates,
        "errors": list(outcome.errors),
        "provenance": {
            "engine": "acp-lowconfirm",
            "fingerprint": fingerprint(
                {
                    "source_manifest": str(manifest_path),
                    "select": [r["id"] for r in rows],
                    "run_irc": run_irc,
                }
            ),
        },
    }
    manifest_out = write_json_atomic(result_dir / S3_MANIFEST_NAME, payload)
    logger.info(
        "Lowconfirm manifest written: %s (%d/%d confirmed)",
        manifest_out,
        len(confirmed),
        len(candidates_out),
    )
    return payload


def _result_row(
    result_dir: Path,
    candidate: Any,
    manifest_path: Path,
) -> dict[str, Any]:
    row = candidate.to_dict()
    optimized = Path(row.get("optimized_xyz") or "")
    if optimized.is_file():
        target_dir = result_dir / "optimized"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{candidate.candidate_id}.xyz"
        shutil.copy2(optimized, target)
        row["optimized_xyz"] = f"optimized/{candidate.candidate_id}.xyz"
    evidence = row.get("evidence") or {}
    freq_output = evidence.get("canonical_frequency_output")
    if freq_output and Path(str(freq_output)).is_file():
        freq_dir = result_dir / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        target = freq_dir / f"{candidate.candidate_id}{Path(str(freq_output)).suffix}"
        shutil.copy2(freq_output, target)
        row["frequency"]["output"] = f"frequencies/{target.name}"
    if row["status"] == "failed" and not row.get("input_xyz"):
        row["input_xyz"] = ""
    row.pop("evidence", None)
    row["source_manifest"] = str(manifest_path)
    return row


__all__ = [
    "S3_MANIFEST_NAME",
    "S3_SCHEMA_VERSION",
    "read_s2_manifest",
    "run_low_confirm",
]
