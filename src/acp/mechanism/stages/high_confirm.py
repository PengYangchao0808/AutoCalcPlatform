"""Highconfirm (S4) — high-fidelity optimization + frequency + SP + thermo.

Reads ``s3_lowconfirm_manifest.json``, re-confirms the selected candidates
at the s4 fidelity through the mechanism-free :class:`BatchOptimizeEngine`
``opt_freq_sp_thermo`` profile and writes ``s4_highconfirm_manifest.json``
plus ``mechanism_profile.json``
(barriers, TS data, S3/S4 consistency).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from acp.calculations.batch import (
    BatchOptimizeEngine,
    BatchStructureItem,
    JsonObject,
    load_batch_request,
)
from acp.compat.legacy.batch_loaders import load_items_from_s3_manifest
from acp.compat.legacy.manifests import read_s3_lowconfirm_manifest
from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import source_artifact_ref, utc_now_iso
from acp.mechanism.presets import FIDELITY_PROFILES, resolve_fidelity

from .._helpers import fingerprint
from .low_confirm import (
    _batch_method_options,
    _carried_row,
    _failed_record,
    _legacy_profile_payload,
    _result_row,
    _write_legacy_batch_manifest,
)

logger = logging.getLogger(__name__)

S4_SCHEMA_VERSION = "s4_highconfirm_v1"
S4_MANIFEST_NAME = "s4_highconfirm_manifest.json"
MECHANISM_PROFILE_NAME = "mechanism_profile.json"

HARTREE_TO_KCAL = 627.5094740631


def read_s3_manifest(path: Path) -> dict[str, Any]:
    return dict(read_s3_lowconfirm_manifest(path))


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
    source_job_id: str | None = None,
    source_relative_path: str = "RESULT/mechanism/s3_lowconfirm_manifest.json",
    charge: int | None = None,
    multiplicity: int | None = None,
    config: dict[str, Any] | None = None,
    refinement_provider: Any | None = None,
    structures: list[BatchStructureItem] | None = None,
    batch_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the S4 fine confirmation; write the S4 manifest + mechanism profile.

    Structures enter either through a Lowconfirm manifest (*from_manifest*)
    or directly as batch structures (*structures* / *batch_request*).
    """
    out_root = Path(output_dir).resolve()
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)

    items: list[BatchStructureItem] = []
    source_block: dict[str, Any]
    if structures is None and batch_request is None:
        if from_manifest is None:
            raise ValueError("Highconfirm requires from_manifest, structures or batch_request")
        manifest_path = Path(from_manifest).resolve()
        payload_in = read_s3_manifest(manifest_path)
        resolved_charge = charge if charge is not None else int(payload_in.get("charge") or 0)
        resolved_multiplicity = (
            multiplicity if multiplicity is not None else int(payload_in.get("multiplicity") or 1)
        )
        rows = [
            row for row in payload_in.get("candidates") or [] if row.get("status") == "confirmed"
        ]
        if not rows:
            raise ValueError("S3 manifest has no confirmed candidates to promote")
        if select:
            by_id = {str(row.get("id")): row for row in payload_in.get("candidates") or []}
            missing = [candidate_id for candidate_id in select if str(candidate_id) not in by_id]
            if missing:
                raise ValueError(f"Unknown candidate ids in S3 manifest: {', '.join(missing)}")
            unconfirmed = [
                str(candidate_id)
                for candidate_id in select
                if by_id[str(candidate_id)].get("status") != "confirmed"
            ]
            if unconfirmed:
                raise ValueError(
                    "S3 candidates not confirmed, refuse S4 promotion: " + ", ".join(unconfirmed)
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
            request_payload = batch_request
            if request_payload is None:
                raise ValueError("Highconfirm requires from_manifest, structures or batch_request")
            structures = load_batch_request(cast(JsonObject, request_payload))
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

    structures_for_engine = structures if structures is not None else items
    fidelity_profile = FIDELITY_PROFILES[resolve_fidelity("s4")]
    engine = BatchOptimizeEngine(
        config=config,
        work_root=out_root / "WORK",
        result_root=out_root / "RESULT",
        methods=_batch_method_options(fidelity_profile),
    )
    structures_for_engine = structures_for_engine or []
    try:
        outcome = engine.run(
            structures_for_engine,
            profile="opt_freq_sp_thermo",
            charge=resolved_charge,
            multiplicity=resolved_multiplicity,
            workflow="Highconfirm",
        )
    except Exception as exc:  # noqa: BLE001 - persist then re-raise engine failures
        error = str(exc) or type(exc).__name__
        failed_records = [
            _failed_record(item, resolved_charge, resolved_multiplicity, error)
            for item in structures_for_engine
        ]
        payload = _build_s4_payload(
            source_block=source_block,
            selected_ids=[item.candidate_id or item.item_id for item in structures_for_engine],
            charge=resolved_charge,
            multiplicity=resolved_multiplicity,
            candidates=[_result_row(result_dir, record, copy_sp=True) for record in failed_records],
            consistency=[],
            errors=[error],
            status="failed",
        )
        write_json_atomic(result_dir / S4_MANIFEST_NAME, payload)
        raise

    candidates_out: list[dict[str, Any]] = []
    consistency: list[str] = []
    for record in outcome.manifest.items:
        row = (
            _carried_row(out_root, record)
            if record.status == "skipped"
            else _result_row(result_dir, record, copy_sp=True)
        )
        s3_row = s3_by_id.get(record.candidate_id)
        if s3_row is not None and row["status"] == "confirmed":
            consistency.extend(_s3_s4_consistency(s3_row, row))
        candidates_out.append(row)

    _write_legacy_batch_manifest(outcome.manifest, result_dir)

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

    payload = _build_s4_payload(
        source_block=source_block,
        selected_ids=[item.candidate_id or item.item_id for item in structures_for_engine],
        charge=resolved_charge,
        multiplicity=resolved_multiplicity,
        candidates=candidates_out,
        consistency=consistency,
        errors=list(outcome.errors),
        gates=gates,
        status="completed",
    )
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


def _build_s4_payload(
    *,
    source_block: dict[str, Any],
    selected_ids: list[str],
    charge: int,
    multiplicity: int,
    candidates: list[dict[str, Any]],
    consistency: list[str],
    errors: list[str],
    status: str,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_gates = gates or {
        "optimization_converged": False,
        "frequency_successful": False,
        "single_point_successful": False,
        "thermo_successful": False,
        "s3_s4_consistent": not consistency,
        "G4": "FAIL",
        "G5": "FAIL",
    }
    return {
        "schema_version": S4_SCHEMA_VERSION,
        "workflow": "Highconfirm",
        "stage": "S4",
        "status": status,
        "created_at": utc_now_iso(),
        "source": source_block,
        "profile": _legacy_profile_payload("s4", "high"),
        "charge": charge,
        "multiplicity": multiplicity,
        "candidates": candidates,
        "batch_manifest": "batch_calculation_manifest.json",
        "s3_s4_consistency": list(consistency),
        "gates": effective_gates,
        "errors": list(errors),
        "provenance": {
            "engine": "acp-highconfirm",
            "fingerprint": fingerprint(
                {"source_manifest": str(source_block.get("path") or ""), "select": selected_ids}
            ),
        },
    }


__all__ = [
    "MECHANISM_PROFILE_NAME",
    "S4_MANIFEST_NAME",
    "S4_SCHEMA_VERSION",
    "read_s3_manifest",
    "run_high_confirm",
]
