"""Lowconfirm (S3) — coarse optimization + frequency + preliminary IRC.

Reads ``s2_path_manifest.json`` (or the user-confirmed sibling
``s2_candidate_manifest.json``), refines the selected candidates through the
mechanism-free :class:`BatchOptimizeEngine` ``opt_freq`` profile, and writes
the historical ``s3_lowconfirm_manifest.json`` adapter output. IRC runs by
default on the canonical TS through the temporary endpoint-provider bridge.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from acp.calculations.batch import (
    BatchCalculationItem,
    BatchCalculationManifest,
    BatchOptimizeEngine,
    BatchStructureItem,
    load_batch_request,
)
from acp.calculations.batch.options import BatchMethodOptions
from acp.compat.legacy.batch_loaders import load_items_from_s2_path_manifest
from acp.compat.legacy.manifests import read_s2_path_manifest
from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import source_artifact_ref, utc_now_iso
from acp.mechanism.models import ArtifactRef, StationaryPoint, TsIdentity
from acp.mechanism.presets import FIDELITY_PROFILES, FidelityProfile, resolve_fidelity

from .._helpers import fingerprint

logger = logging.getLogger(__name__)

S3_SCHEMA_VERSION = "s3_lowconfirm_v1"
S3_MANIFEST_NAME = "s3_lowconfirm_manifest.json"

_S2_ACCEPTED_SCHEMAS: frozenset[str] = frozenset({"s2_path_v1", "s2_path_v2"})


def _snapshot_s2_candidate_package(source_manifest: Path, output_root: Path) -> Path:
    source_manifest = source_manifest.resolve()
    target_result = output_root / "RESULT"
    target_mechanism = target_result / "mechanism"
    target_mechanism.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_manifest, target_mechanism / source_manifest.name)
    for filename in ("s2_candidate_manifest.json", "s2_review.json"):
        source = source_manifest.parent / filename
        if source.is_file():
            shutil.copy2(source, target_mechanism / filename)

    # The scheduler may pass either the original RESULT manifest or the
    # handoff copy under WORK/01_PREPARE/handoff.  Probe both layouts so the
    # snapshot remains self-contained in either invocation mode.
    source_result = source_manifest.parent.parent
    source_result_manifest = next(
        (
            candidate
            for candidate in (
                source_result / "result_manifest.json",
                source_manifest.parent / "result_manifest.json",
            )
            if candidate.is_file()
        ),
        None,
    )
    if source_result_manifest is not None:
        shutil.copy2(source_result_manifest, target_result / source_result_manifest.name)

    source_structures = next(
        (
            candidate
            for candidate in (
                source_result / "structures" / "s2_candidates",
                source_manifest.parent / "structures" / "s2_candidates",
            )
            if candidate.is_dir()
        ),
        None,
    )
    if source_structures is not None:
        target_structures = target_result / "structures" / "s2_candidates"
        target_structures.mkdir(parents=True, exist_ok=True)
        for source in source_structures.iterdir():
            if source.is_file():
                shutil.copy2(source, target_structures / source.name)
    return target_mechanism / source_manifest.name


def read_s2_manifest(path: Path) -> dict[str, Any]:
    payload = read_s2_path_manifest(path)
    if (
        payload.get("schema_version") not in _S2_ACCEPTED_SCHEMAS
        or payload.get("workflow") != "PESsearch"
    ):
        raise ValueError(f"Not a PESsearch s2_path manifest: {path}")
    return dict(payload)


def _require_confirmed_review(payload: dict[str, Any]) -> None:
    """Require a PES candidate result to be user-confirmed before refinement."""
    if payload.get("schema_version") != "s2_path_v2":
        return
    review = payload.get("review") or {}
    if str(review.get("status") or "pending") != "confirmed":
        raise ValueError(
            "PES candidates are not yet confirmed by the user — save the TS/INT "
            "selection (POST /jobs/{id}/s2/review) before submitting a batch task"
        )


def run_low_confirm(
    *,
    from_manifest: Path | str | None = None,
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
    structures: list[BatchStructureItem] | None = None,
    batch_request: dict[str, Any] | None = None,
    snapshot_candidates: bool = False,
) -> dict[str, Any]:
    """Run the S3 coarse confirmation; write ``s3_lowconfirm_manifest.json``.

    Structures enter either through a PESsearch manifest (*from_manifest*)
    or directly as batch structures (*structures* / *batch_request*, batch
    plan §3 — upload/paste XYZ or arbitrary task-result structures).
    """
    out_root = Path(output_dir).resolve()
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)

    items: list[BatchStructureItem] = []
    source_block: dict[str, Any]
    if structures is None and batch_request is None:
        if from_manifest is None:
            raise ValueError("Lowconfirm requires from_manifest, structures or batch_request")
        manifest_path = Path(from_manifest).resolve()
        source_manifest_path = manifest_path
        if snapshot_candidates:
            # The copied candidate manifest is the runtime source of truth;
            # later source-job purges cannot remove the S3 input package.
            manifest_path = _snapshot_s2_candidate_package(manifest_path, out_root)
        payload_in = read_s2_manifest(manifest_path)
        _require_confirmed_review(payload_in)
        resolved_charge = charge if charge is not None else int(payload_in.get("charge") or 0)
        resolved_multiplicity = (
            multiplicity if multiplicity is not None else int(payload_in.get("multiplicity") or 1)
        )
        items, _payload = load_items_from_s2_path_manifest(
            manifest_path, [str(s) for s in select or []]
        )
        source_block = source_artifact_ref(
            source_job_id,
            source_relative_path,
            source_manifest_path,
            kind="s2_path_manifest",
            stage="S2",
        )
        selected_ids = [item.candidate_id or item.item_id for item in items]
    else:
        if structures is None:
            request_payload = batch_request
            if request_payload is None:
                raise ValueError("Lowconfirm requires from_manifest, structures or batch_request")
            structures = load_batch_request(request_payload)
        resolved_charge = charge if charge is not None else 0
        resolved_multiplicity = multiplicity if multiplicity is not None else 1
        if select:
            wanted = {str(s) for s in select}
            structures = [
                item for item in structures if item.item_id in wanted or item.candidate_id in wanted
            ]
            if not structures:
                raise ValueError(f"No batch structures match select={sorted(wanted)}")
        source_block = {
            "kind": "batch_structures",
            "stage": "BATCH",
            "source_job_id": source_job_id or "",
            "relative_path": "",
            "path": "",
            "count": len(structures),
        }
        selected_ids = [item.candidate_id or item.item_id for item in structures]

    structures_for_engine = structures if structures is not None else items
    fidelity_name = resolve_fidelity("s3")
    fidelity_profile = FIDELITY_PROFILES[fidelity_name]
    engine = BatchOptimizeEngine(
        config=config,
        work_root=out_root / "WORK",
        result_root=out_root / "RESULT",
        methods=_batch_method_options(fidelity_profile),
    )
    try:
        outcome = engine.run(
            structures_for_engine,
            profile="opt_freq",
            charge=resolved_charge,
            multiplicity=resolved_multiplicity,
            workflow="Lowconfirm",
        )
    except Exception as exc:  # noqa: BLE001 - persist then re-raise engine failures
        error = str(exc) or type(exc).__name__
        failed_records = [
            _failed_record(item, resolved_charge, resolved_multiplicity, error)
            for item in structures_for_engine
        ]
        payload = _build_s3_payload(
            source_block=source_block,
            selected_ids=selected_ids,
            charge=resolved_charge,
            multiplicity=resolved_multiplicity,
            run_irc=run_irc,
            fidelity_profile=fidelity_profile,
            candidates=[_result_row(result_dir, record) for record in failed_records],
            irc_block=None,
            errors=[error],
            status="failed",
        )
        write_json_atomic(result_dir / S3_MANIFEST_NAME, payload)
        raise

    _write_legacy_batch_manifest(outcome.manifest, result_dir)
    candidates_out: list[dict[str, Any]] = []
    for record in outcome.manifest.items:
        candidates_out.append(
            _carried_row(out_root, record)
            if record.status == "skipped"
            else _result_row(result_dir, record)
        )

    confirmed = [row for row in candidates_out if row["status"] == "confirmed"]
    ts_rows = [row for row in confirmed if row["kind"] == "ts"]
    irc_block = _run_irc_bridge(
        out_root,
        result_dir,
        ts_rows,
        fidelity_profile,
        endpoint_provider,
        config,
        run_irc,
    )
    gates: dict[str, Any] = {
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
        "irc_completed": (bool(irc_block and irc_block.get("complete")) if run_irc else None),
    }
    gates["G3"] = "PASS" if gates["optimization_converged"] and gates["frequency_valid"] else "FAIL"

    payload = _build_s3_payload(
        source_block=source_block,
        selected_ids=selected_ids,
        charge=resolved_charge,
        multiplicity=resolved_multiplicity,
        run_irc=run_irc,
        fidelity_profile=fidelity_profile,
        candidates=candidates_out,
        irc_block=irc_block,
        errors=list(outcome.errors),
        gates=gates,
        status="completed",
    )
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
    record: BatchCalculationItem,
    manifest_path: Path | None = None,
    *,
    copy_sp: bool = False,
) -> dict[str, Any]:
    """Project a generic batch record into the historical S3 row shape."""
    result_root = result_dir.parent
    task_root = result_root.parent
    status = "confirmed" if record.status in {"completed", "skipped"} else record.status
    input_path = Path(record.input_xyz) if record.input_xyz else None
    if input_path is not None and not input_path.is_absolute():
        input_path = task_root / input_path
    optimized = Path(record.optimized_xyz) if record.optimized_xyz else None
    if optimized is not None and not optimized.is_absolute():
        optimized = result_root / optimized
    row: dict[str, Any] = {
        "id": record.candidate_id,
        "kind": record.kind,
        "role": "transition_state" if record.kind == "ts" else "intermediate",
        "status": status,
        "input_xyz": str(input_path) if input_path is not None else "",
        "optimized_xyz": record.optimized_xyz,
        "opt_converged": record.status in {"completed", "skipped"},
        "frequency": _frequency_row(record),
        "sp_energy_hartree": record.single_point.get("energy_hartree"),
        "gibbs_hartree": record.thermochemistry.get("gibbs_hartree"),
        "source_manifest": str(manifest_path) if manifest_path is not None else "",
    }
    if optimized is not None and optimized.is_file():
        target_dir = result_dir / "optimized"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{record.candidate_id}.xyz"
        shutil.copy2(optimized, target)
        row["optimized_xyz"] = f"optimized/{record.candidate_id}.xyz"
    if record.error:
        row["error"] = record.error
    return row


def _batch_method_options(profile: FidelityProfile) -> BatchMethodOptions:
    return BatchMethodOptions(
        minimum_method=profile.ts_method,
        minimum_basis=profile.ts_basis,
        transition_state_method=profile.ts_method,
        transition_state_basis=profile.ts_basis,
        frequency_method=profile.freq_method,
        frequency_basis=profile.freq_basis,
        single_point_method=profile.sp_method,
        single_point_basis=profile.sp_basis,
    )


def _carried_row(out_root: Path, record: BatchCalculationItem) -> dict[str, Any]:
    """Project a cache-carried batch record and mark it as resumed."""
    row = _result_row(out_root / "RESULT" / "mechanism", record)
    row["resumed_from_previous_run"] = True
    return row


def _frequency_row(record: BatchCalculationItem) -> dict[str, Any]:
    frequency = dict(record.frequency)
    raw_frequencies = frequency.get("frequencies")
    frequencies = (
        [float(value) for value in raw_frequencies if isinstance(value, (int, float))]
        if isinstance(raw_frequencies, list)
        else []
    )
    if frequency.get("status") == "completed":
        frequency["status"] = "complete"
    if record.kind == "ts" and frequencies:
        imaginary = [value for value in frequencies if value <= -50.0]
        frequency.update(
            {
                "n_imaginary": len(imaginary),
                "imaginary_frequency_cm1": min(imaginary) if imaginary else None,
                "valid_ts_identity": len(imaginary) == 1,
            }
        )
    return frequency


def _failed_record(
    item: BatchStructureItem,
    charge: int,
    multiplicity: int,
    error: str,
) -> BatchCalculationItem:
    record = BatchCalculationItem.from_item(item, charge, multiplicity)
    record.status = "failed"
    record.error = error
    return record


def _legacy_profile_payload(
    fidelity_name: str,
    level: str,
    run_irc: bool,
) -> dict[str, Any]:
    fidelity = resolve_fidelity(fidelity_name)
    profile = FIDELITY_PROFILES[fidelity]
    max_cycles = profile.max_cycles_ts or profile.max_cycles_minimum
    if max_cycles is None:
        max_cycles = {"s3": 60, "s4": 200}[fidelity]
    payload: dict[str, Any] = {
        "level": level,
        "opt_method": profile.ts_method,
        "freq_method": profile.freq_method,
        "sp_method": profile.sp_method,
        "max_cycles": max_cycles,
        "irc": run_irc,
    }
    if level == "high":
        payload.update(
            {
                "opt_basis": profile.ts_basis,
                "freq_basis": profile.freq_basis,
                "sp_basis": profile.sp_basis,
            }
        )
    return payload


def _build_s3_payload(
    *,
    source_block: dict[str, Any],
    selected_ids: list[str],
    charge: int,
    multiplicity: int,
    run_irc: bool,
    fidelity_profile: Any,
    candidates: list[dict[str, Any]],
    irc_block: dict[str, Any] | None,
    errors: list[str],
    status: str,
    gates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del fidelity_profile
    effective_gates = gates or {
        "optimization_converged": False,
        "frequency_valid": False,
        "ts_first_order_saddle": None,
        "irc_completed": bool(irc_block and irc_block.get("complete")) if run_irc else None,
        "G3": "FAIL",
    }
    return {
        "schema_version": S3_SCHEMA_VERSION,
        "workflow": "Lowconfirm",
        "stage": "S3",
        "status": status,
        "created_at": utc_now_iso(),
        "source": source_block,
        "profile": _legacy_profile_payload("s3", "low", run_irc),
        "charge": charge,
        "multiplicity": multiplicity,
        "candidates": candidates,
        "batch_manifest": "batch_calculation_manifest.json",
        "irc": irc_block,
        "gates": effective_gates,
        "errors": list(errors),
        "provenance": {
            "engine": "acp-lowconfirm",
            "fingerprint": fingerprint(
                {"source_manifest": str(source_block.get("path") or ""), "select": selected_ids}
            ),
        },
    }


def _write_legacy_batch_manifest(
    manifest: BatchCalculationManifest,
    result_dir: Path,
) -> None:
    manifest.write(result_dir / "batch_calculation_manifest.json")


def _run_irc_bridge(
    out_root: Path,
    result_dir: Path,
    ts_rows: list[dict[str, Any]],
    fidelity: Any,
    endpoint_provider: Any | None,
    config: dict[str, Any] | None,
    run_irc: bool,
) -> dict[str, Any] | None:
    if not run_irc or not ts_rows:
        return None
    row = ts_rows[0]
    geometry = Path(str(row.get("optimized_xyz") or row.get("input_xyz") or ""))
    if not geometry.is_absolute():
        geometry = result_dir / geometry
    ts = StationaryPoint(
        point_id=str(row["id"]),
        role="transition_state",
        kind="ts",
        geometry=ArtifactRef(path=str(geometry), sha256="", kind="geometry"),
        charge=int(row.get("charge") or 0),
        multiplicity=int(row.get("multiplicity") or 1),
        energy_hartree=row.get("sp_energy_hartree"),
        identity=_ts_identity(row.get("frequency")),
    )
    provider = endpoint_provider
    if provider is None:
        # TEMPORARY Wave 3 bridge; Wave 4 replaces this with standalone IRC.
        from acp.backends import get_backend

        from ..endpoint import DefaultEndpointProvider, EndpointMatchThresholds

        backend_ref = get_backend("orca")
        backend = backend_ref(config or {}) if isinstance(backend_ref, type) else backend_ref
        provider = DefaultEndpointProvider(
            backend=backend,
            thresholds=EndpointMatchThresholds(),
            work_root=out_root / "WORK" / "03_OPT",
        )
    try:
        return _irc_block(provider.run_irc(ts, fidelity))
    except Exception as exc:  # noqa: BLE001 - IRC remains advisory at S3
        logger.warning("IRC validation failed for %s: %s", row["id"], exc)
        return {"enabled": True, "complete": False, "error": str(exc)}


def _ts_identity(frequency: Any) -> TsIdentity | None:
    if not isinstance(frequency, dict) or frequency.get("n_imaginary") is None:
        return None
    return TsIdentity(
        imaginary_count=int(frequency["n_imaginary"]),
        imaginary_frequency_cm1=(
            float(frequency["imaginary_frequency_cm1"])
            if frequency.get("imaginary_frequency_cm1") is not None
            else None
        ),
        valid=bool(frequency.get("valid_ts_identity")),
    )


def _irc_block(irc_result: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "irc_id": getattr(irc_result, "irc_id", ""),
        "ts_id": getattr(irc_result, "ts_id", ""),
        "complete": bool(getattr(irc_result, "complete", False)),
        "endpoints": {},
    }
    for direction in ("forward", "reverse"):
        endpoint = getattr(irc_result, f"{direction}_endpoint", None)
        if endpoint is None:
            continue
        block["endpoints"][direction] = {
            "xyz": getattr(endpoint, "path", ""),
            "sha256": getattr(endpoint, "sha256", ""),
            "kind": getattr(endpoint, "kind", "irc"),
        }
    return block


__all__ = [
    "S3_MANIFEST_NAME",
    "S3_SCHEMA_VERSION",
    "read_s2_manifest",
    "run_low_confirm",
]
