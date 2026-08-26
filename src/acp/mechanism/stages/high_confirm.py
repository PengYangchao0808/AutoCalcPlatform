"""Highconfirm (S4) — high-fidelity optimization + frequency + SP + thermo.

Reads ``s3_lowconfirm_manifest.json``, re-confirms the selected candidates
at the s4 fidelity through the same batch engine as Lowconfirm
(:class:`BatchConfirmEngine`, profile ``s4``) and writes
``s4_highconfirm_manifest.json`` plus ``mechanism_profile.json``
(barriers, TS data, S3/S4 consistency).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import source_artifact_ref, utc_now_iso

from .._helpers import fingerprint
from ..batch_models import BatchStructureItem, load_batch_request
from .confirm import HighConfirmProfile
from .low_confirm import _carried_row, _result_row

logger = logging.getLogger(__name__)

S4_SCHEMA_VERSION = "s4_highconfirm_v1"
S4_MANIFEST_NAME = "s4_highconfirm_manifest.json"
MECHANISM_PROFILE_NAME = "mechanism_profile.json"

HARTREE_TO_KCAL = 627.5094740631


def read_s3_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S3 manifest is not a JSON object: {path}")
    if (
        payload.get("schema_version") != "s3_lowconfirm_v1"
        or payload.get("workflow") != "Lowconfirm"
    ):
        raise ValueError(f"Not a Lowconfirm s3_lowconfirm manifest: {path}")
    return payload


def _s3_s4_consistency(s3_row: dict[str, Any], s4_row: dict[str, Any]) -> list[str]:
    """Compare S3 vs S4 identity evidence; empty list = consistent (plan §6.4)."""
    messages: list[str] = []
    if str(s3_row.get("kind")) != str(s4_row.get("kind")):
        messages.append(f"kind changed: s3={s3_row.get('kind')} -> s4={s4_row.get('kind')}")
    s3_freq = s3_row.get("frequency") or {}
    s4_freq = s4_row.get("frequency") or {}
    s3_n = s3_freq.get("n_imaginary")
    s4_n = s4_freq.get("n_imaginary")
    if s3_n is not None and s4_n is not None and int(s3_n) != int(s4_n):
        messages.append(
            f"hessian_index not preserved: s3 n_imaginary={s3_n} -> s4 n_imaginary={s4_n}"
        )
    return messages


def _barrier_blocks(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forward/reverse barriers when a TS and endpoint minima coexist (§6.4)."""
    ts_rows = [
        row for row in candidates if row.get("kind") == "ts" and row["status"] == "confirmed"
    ]
    minima = [
        row for row in candidates if row.get("kind") == "minimum" and row["status"] == "confirmed"
    ]
    barriers: list[dict[str, Any]] = []
    for ts in ts_rows:
        ts_energy = ts.get("sp_energy_hartree")
        if ts_energy is None:
            continue
        block: dict[str, Any] = {
            "ts_id": ts["id"],
            "ts_energy_hartree": ts_energy,
            "forward_barrier_kcal": None,
            "reverse_barrier_kcal": None,
            "forward_from": None,
            "reverse_from": None,
        }
        ranked = sorted(
            (m for m in minima if m.get("sp_energy_hartree") is not None),
            key=lambda m: float(m["sp_energy_hartree"]),  # type: ignore[arg-type]
        )
        if len(ranked) >= 2:
            low, high = ranked[0], ranked[-1]
            block["forward_from"] = low["id"]
            block["forward_barrier_kcal"] = (
                float(ts_energy) - float(low["sp_energy_hartree"])  # type: ignore[arg-type]
            ) * HARTREE_TO_KCAL
            block["reverse_from"] = high["id"]
            block["reverse_barrier_kcal"] = (
                float(ts_energy) - float(high["sp_energy_hartree"])  # type: ignore[arg-type]
            ) * HARTREE_TO_KCAL
        barriers.append(block)
    return barriers


def run_high_confirm(
    *,
    from_manifest: Path | str | None = None,
    output_dir: Path | str,
    select: list[str] | None = None,
    run_irc: bool = False,
    source_job_id: str | None = None,
    source_relative_path: str = "RESULT/mechanism/s3_lowconfirm_manifest.json",
    charge: int | None = None,
    multiplicity: int | None = None,
    config: dict[str, Any] | None = None,
    refinement_provider: Any | None = None,
    endpoint_provider: Any | None = None,
    structures: list[BatchStructureItem] | None = None,
    batch_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the S4 fine confirmation; write the S4 manifest + mechanism profile.

    Structures enter either through a Lowconfirm manifest (*from_manifest*)
    or directly as batch structures (*structures* / *batch_request*).
    """
    from ..batch_models import load_items_from_s3_manifest

    out_root = Path(output_dir).resolve()
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)

    source_block: dict[str, Any]
    if structures is None and batch_request is None:
        if from_manifest is None:
            raise ValueError("Highconfirm requires from_manifest, structures or batch_request")
        manifest_path = Path(from_manifest).resolve()
        payload_in = read_s3_manifest(manifest_path)
        resolved_charge = charge if charge is not None else int(payload_in.get("charge") or 0)
        resolved_multiplicity = (
            multiplicity
            if multiplicity is not None
            else int(payload_in.get("multiplicity") or 1)
        )
        items, _payload = load_items_from_s3_manifest(manifest_path, [str(s) for s in select or []])
        s3_by_id = {str(row.get("id")): row for row in payload_in.get("candidates") or []}
        source_block = source_artifact_ref(
            source_job_id,
            source_relative_path,
            manifest_path,
            kind="s3_lowconfirm_manifest",
            stage="S3",
        )
    else:
        if structures is None:
            structures = load_batch_request(batch_request)
        resolved_charge = charge if charge is not None else 0
        resolved_multiplicity = multiplicity if multiplicity is not None else 1
        if select:
            wanted = {str(s) for s in select}
            structures = [
                item for item in structures if item.item_id in wanted or item.candidate_id in wanted
            ]
            if not structures:
                raise ValueError(f"No batch structures match select={sorted(wanted)}")
        s3_by_id: dict[str, Any] = {}
        source_block = {
            "kind": "batch_structures",
            "stage": "BATCH",
            "source_job_id": source_job_id or "",
            "relative_path": "",
            "path": "",
            "count": len(structures),
        }

    profile = HighConfirmProfile(run_irc=run_irc)
    from ..batch_confirm import BatchConfirmEngine

    if structures is None:
        structures = items
    engine = BatchConfirmEngine(
        config=config,
        work_root=out_root / "WORK" / "03_OPT",
        profile=profile,
        refinement_provider=refinement_provider,
        endpoint_provider=endpoint_provider,
    )
    outcome = engine.run(
        structures or [],
        charge=resolved_charge,
        multiplicity=resolved_multiplicity,
        workflow="Highconfirm",
    )

    candidates_out: list[dict[str, Any]] = []
    consistency: list[str] = []
    if outcome.confirm is not None:
        for candidate in outcome.confirm.candidates:
            row = _result_row(result_dir, candidate, copy_sp=True)
            s3_row = s3_by_id.get(candidate.candidate_id)
            if s3_row is not None and row["status"] == "confirmed":
                consistency.extend(_s3_s4_consistency(s3_row, row))
            candidates_out.append(row)
    for record in outcome.manifest.items:
        if record.status != "skipped":
            continue
        row = _carried_row(out_root, record)
        s3_row = s3_by_id.get(record.candidate_id)
        if s3_row is not None and row["status"] == "confirmed":
            consistency.extend(_s3_s4_consistency(s3_row, row))
        candidates_out.append(row)

    confirmed = [row for row in candidates_out if row["status"] == "confirmed"]
    gates: dict[str, Any] = {
        "optimization_converged": bool(confirmed)
        and all(row["opt_converged"] for row in confirmed),
        "frequency_successful": any(
            row["frequency"].get("status") == "complete" for row in confirmed
        ),
        "single_point_successful": any(
            row.get("sp_energy_hartree") is not None for row in confirmed
        ),
        "thermo_successful": any(row.get("gibbs_hartree") is not None for row in confirmed),
        "s3_s4_consistent": not consistency,
    }
    gates["G4"] = (
        "PASS" if gates["optimization_converged"] and gates["frequency_successful"] else "FAIL"
    )
    gates["G5"] = (
        "PASS" if gates["s3_s4_consistent"] and gates["single_point_successful"] else "FAIL"
    )

    payload = {
        "schema_version": S4_SCHEMA_VERSION,
        "workflow": "Highconfirm",
        "stage": "S4",
        "created_at": utc_now_iso(),
        "source": source_block,
        "profile": {
            "level": "high",
            "opt_method": profile.opt_method,
            "opt_basis": profile.opt_basis,
            "freq_method": profile.freq_method,
            "freq_basis": profile.freq_basis,
            "sp_method": profile.sp_method,
            "sp_basis": profile.sp_basis,
            "max_cycles": profile.max_cycles,
            "irc": run_irc,
        },
        "charge": resolved_charge,
        "multiplicity": resolved_multiplicity,
        "candidates": candidates_out,
        "batch_manifest": "batch_calculation_manifest.json",
        "irc": outcome.confirm.irc if outcome.confirm is not None else None,
        "s3_s4_consistency": consistency,
        "gates": gates,
        "errors": list(outcome.errors),
        "provenance": {
            "engine": "acp-highconfirm",
            "fingerprint": fingerprint(
                {
                    "source_manifest": str(from_manifest) if from_manifest else "",
                    "select": [item.candidate_id or item.item_id for item in (structures or [])],
                    "run_irc": run_irc,
                }
            ),
        },
    }
    manifest_out = write_json_atomic(result_dir / S4_MANIFEST_NAME, payload)

    mechanism_profile = {
        "schema_version": "mechanism_profile_v1",
        "created_at": utc_now_iso(),
        "s4_manifest": str(manifest_out),
        "transition_states": [
            {
                "id": row["id"],
                "energy_hartree": row.get("sp_energy_hartree"),
                "gibbs_hartree": row.get("gibbs_hartree"),
                "imaginary_frequency_cm1": (row.get("frequency") or {}).get(
                    "imaginary_frequency_cm1"
                ),
                "geometry": row.get("optimized_xyz"),
            }
            for row in confirmed
            if row.get("kind") == "ts"
        ],
        "endpoints": [
            {
                "id": row["id"],
                "energy_hartree": row.get("sp_energy_hartree"),
                "gibbs_hartree": row.get("gibbs_hartree"),
                "geometry": row.get("optimized_xyz"),
            }
            for row in confirmed
            if row.get("kind") == "minimum"
        ],
        "barriers": _barrier_blocks(candidates_out),
        "s3_s4_consistency": consistency,
        "note": (
            "Barriers are reported only between S4-confirmed TS and endpoint minima "
            "at the same fidelity level."
        ),
    }
    profile_out = write_json_atomic(result_dir / MECHANISM_PROFILE_NAME, mechanism_profile)
    logger.info(
        "Highconfirm manifest written: %s (+%s, %d/%d confirmed)",
        manifest_out,
        profile_out,
        len(confirmed),
        len(candidates_out),
    )
    return payload


__all__ = [
    "MECHANISM_PROFILE_NAME",
    "S4_MANIFEST_NAME",
    "S4_SCHEMA_VERSION",
    "read_s3_manifest",
    "run_high_confirm",
]
