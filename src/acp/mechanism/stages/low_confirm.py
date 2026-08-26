"""Lowconfirm (S3) — coarse optimization + frequency + preliminary IRC.

Reads ``s2_path_manifest.json`` (or the user-confirmed sibling
``s2_candidate_manifest.json``), refines the selected candidates at the s3
fidelity through the shared batch engine (:class:`BatchConfirmEngine`,
profile ``s3``), and writes ``s3_lowconfirm_manifest.json`` plus the
unified ``batch_calculation_manifest.json``. IRC runs by default on the
canonical TS (plan §6.3 — PESsearch discovers, Lowconfirm confirms coarsely,
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
from ..batch_models import BatchCalculationItem, BatchStructureItem, load_batch_request
from .confirm import LowConfirmProfile

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
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S2 manifest is not a JSON object: {path}")
    if (
        payload.get("schema_version") not in _S2_ACCEPTED_SCHEMAS
        or payload.get("workflow") != "PESsearch"
    ):
        raise ValueError(f"Not a PESsearch s2_path manifest: {path}")
    return payload


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
    from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

    from ..batch_models import load_items_from_s2_path_manifest

    out_root = Path(output_dir).resolve()
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)

    items: list[BatchStructureItem] = []
    plan: ReactionCoordinatePlan | None = None
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
        plan_payload = (payload_in.get("route") or {}).get("coordinate_plan") or {}
        plan = ReactionCoordinatePlan.from_dict(plan_payload) if plan_payload else None
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

    profile = LowConfirmProfile(run_irc=run_irc)
    from ..batch_confirm import BatchConfirmEngine

    engine = BatchConfirmEngine(
        config=config,
        work_root=out_root / "WORK" / "03_OPT",
        profile=profile,
        refinement_provider=refinement_provider,
        endpoint_provider=endpoint_provider,
    )
    structures_for_engine = structures if structures is not None else items
    outcome = engine.run(
        structures_for_engine,
        charge=resolved_charge,
        multiplicity=resolved_multiplicity,
        coordinate_plan=plan,
        workflow="Lowconfirm",
    )

    candidates_out: list[dict[str, Any]] = []
    if outcome.confirm is not None:
        for candidate in outcome.confirm.candidates:
            row = _result_row(result_dir, candidate)
            candidates_out.append(row)
    for record in outcome.manifest.items:
        if record.status != "skipped":
            continue
        candidates_out.append(_carried_row(out_root, record))

    confirmed = [row for row in candidates_out if row["status"] == "confirmed"]
    ts_rows = [row for row in confirmed if row["kind"] == "ts"]
    irc_block = outcome.confirm.irc if outcome.confirm is not None else None
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

    payload = {
        "schema_version": S3_SCHEMA_VERSION,
        "workflow": "Lowconfirm",
        "stage": "S3",
        "created_at": utc_now_iso(),
        "source": source_block,
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
        "batch_manifest": "batch_calculation_manifest.json",
        "irc": irc_block,
        "gates": gates,
        "errors": list(outcome.errors),
        "provenance": {
            "engine": "acp-lowconfirm",
            "fingerprint": fingerprint(
                {
                    "source_manifest": str(from_manifest) if from_manifest else "",
                    "select": selected_ids,
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
    manifest_path: Path | None = None,
    *,
    copy_sp: bool = False,
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
    if copy_sp:
        sp_output = evidence.get("sp_output")
        if sp_output and Path(str(sp_output)).is_file():
            sp_dir = result_dir / "single_points"
            sp_dir.mkdir(parents=True, exist_ok=True)
            target = sp_dir / f"{candidate.candidate_id}{Path(str(sp_output)).suffix}"
            shutil.copy2(sp_output, target)
            row.setdefault("outputs", {})["sp"] = f"single_points/{target.name}"
    if row["status"] == "failed" and not row.get("input_xyz"):
        row["input_xyz"] = ""
    row.pop("evidence", None)
    row["source_manifest"] = str(manifest_path) if manifest_path is not None else ""
    return row


def _carried_row(out_root: Path, record: BatchCalculationItem) -> dict[str, Any]:
    """Rebuild a stage candidate row from a carried (skipped) batch record.

    ``optimized_xyz`` is stored RESULT-relative in the batch manifest; the
    stage manifest keeps paths relative to ``RESULT/mechanism`` (like every
    other row), so a ``../structures/...`` pointer is emitted.
    """
    input_abs = (out_root / record.input_xyz).resolve() if record.input_xyz else Path("")
    optimized_rel = ""
    if record.optimized_xyz:
        optimized_abs = out_root / "RESULT" / record.optimized_xyz
        try:
            optimized_rel = (
                optimized_abs.resolve()
                .relative_to((out_root / "RESULT" / "mechanism").resolve())
                .as_posix()
            )
        except ValueError:
            optimized_rel = f"../{record.optimized_xyz}"
    sp_energy = record.single_point.get("energy_hartree")
    gibbs = record.thermochemistry.get("gibbs_hartree")
    return {
        "id": record.candidate_id,
        "kind": record.kind,
        "role": "transition_state" if record.kind == "ts" else "intermediate",
        "status": "confirmed" if record.status in {"completed", "skipped"} else record.status,
        "input_xyz": str(input_abs) if str(input_abs) else "",
        "optimized_xyz": optimized_rel,
        "opt_converged": record.status in {"completed", "skipped"},
        "frequency": dict(record.frequency),
        "sp_energy_hartree": sp_energy,
        "gibbs_hartree": gibbs,
        "source_manifest": "",
        "resumed_from_previous_run": True,
    }


__all__ = [
    "S3_MANIFEST_NAME",
    "S3_SCHEMA_VERSION",
    "read_s2_manifest",
    "run_low_confirm",
]
