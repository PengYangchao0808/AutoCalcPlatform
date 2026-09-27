"""Verified, immutable TS sources for scheduler IRC submissions."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from acp.calculations.batch.models import parse_tag_comment
from acp.calculations.batch.options import BatchMethodOptions
from acp.results.manifest import load_result_manifest
from acp.scheduler.files import resolve_safe
from acp.scheduler.jobs import JobRecord, JobStatus
from acp.storage.manifest import ResultManifest

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

__all__ = ["VerifiedTsSource", "load_ts_result_manifest", "resolve_verified_ts_source"]


@dataclass(frozen=True)
class VerifiedTsSource:
    """A TS structure and its validated frequency-level provenance."""

    job_id: str
    product_id: str
    xyz: str
    geometry_sha256: str
    method: str
    basis: str
    charge: int
    multiplicity: int
    imaginary_frequency_cm1: float
    frequency_evidence_sha256: str
    source_completed_at: str
    source_workflow: str

    def method_payload(self, *, maxpoints: int = 100, step: float = 0.1) -> dict[str, Any]:
        return {
            "method": self.method,
            "basis": self.basis,
            "maxpoints": maxpoints,
            "step": step,
        }

    def provenance(self) -> dict[str, Any]:
        return {
            "schema": "irc_ts_source_v1",
            "job_id": self.job_id,
            "product_id": self.product_id,
            "source_workflow": self.source_workflow,
            "geometry_sha256": self.geometry_sha256,
            "method": self.method,
            "basis": self.basis,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "imaginary_frequency_cm1": self.imaginary_frequency_cm1,
            "frequency_evidence_sha256": self.frequency_evidence_sha256,
            "source_completed_at": self.source_completed_at,
        }


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"TS evidence missing or unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"TS evidence is not an object: {path.name}")
    return value


def _frequencies(values: object) -> list[float]:
    if not isinstance(values, list) or not values:
        raise ValueError("TS source has no completed frequency values")
    try:
        frequencies = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("TS source frequency values are invalid") from exc
    if not all(math.isfinite(value) for value in frequencies):
        raise ValueError("TS source frequency values are invalid")
    negatives = [value for value in frequencies if value < 0]
    if len(negatives) != 1:
        raise ValueError(
            f"TS source requires exactly one imaginary frequency; found {len(negatives)}"
        )
    return negatives


def _geometry_lines(xyz: str) -> list[str]:
    """Compare XYZ coordinates while allowing the result title to be rewritten."""
    lines = xyz.strip().splitlines()
    if len(lines) < 3:
        raise ValueError("TS optimized geometry is invalid")
    return [lines[0].strip(), *[line.strip() for line in lines[2:]]]


def load_ts_result_manifest(
    record: JobRecord,
    remote_fetcher: RemoteResultFetcher | None = None,
) -> ResultManifest | None:
    """Read the authoritative manifest locally or from its remote node."""
    result = record.result or {}
    if result.get("node") and result.get("remote_dir"):
        if remote_fetcher is None:
            raise ValueError("Remote TS result fetch is unavailable")
        try:
            raw = remote_fetcher.read_file(record, "RESULT/result_manifest.json")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Result manifest root must be an object")
            return ResultManifest.from_dict(payload)
        except (
            OSError, RuntimeError, UnicodeError, ValueError, TypeError,
            AttributeError, KeyError,
        ) as exc:
            raise ValueError("Remote TS result manifest is unavailable or invalid") from exc
    return load_result_manifest(Path(record.work_dir))


def _resolve_remote_ts_source(
    record: JobRecord,
    product_id: str,
    remote_fetcher: RemoteResultFetcher,
) -> VerifiedTsSource:
    """Fetch only the files needed for this TS check into a temporary snapshot."""
    with tempfile.TemporaryDirectory(prefix="acp-irc-ts-") as tmp:
        root = Path(tmp)

        def stage(relative: str) -> Path:
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative:
                raise ValueError("TS evidence path escapes the source task")
            target = root.joinpath(*path.parts)
            try:
                payload = remote_fetcher.read_file(record, relative)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"Remote TS evidence is unavailable: {relative}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            return target

        stage("RESULT/result_manifest.json")
        manifest = load_result_manifest(root)
        product = next(
            (entry for entry in manifest.products if entry.id == product_id), None
        ) if manifest else None
        if product is None:
            raise ValueError("IRC source is not a registered result product")
        stage("RESULT/" + product.path)
        if record.spec.workflow == "BatchOptimize":
            checkpoint_path = stage("WORK/00_RUNTIME/checkpoint.json")
            stage("RESULT/batch_provenance.json")
            checkpoint = _json_object(checkpoint_path)
            states = checkpoint.get("items_state")
            item_id = product_id.removeprefix("batch_")
            item = states.get(item_id) if isinstance(states, dict) else None
            if isinstance(item, dict) and item.get("optimized_xyz"):
                stage(str(item["optimized_xyz"]))
        elif record.spec.workflow == "tsmode":
            stage("RESULT/tsmode/tsmode_report.json")
            stage("INPUT/tsmode/source_bundle.json")
        local_record = replace(record, work_dir=str(root), result={})
        return resolve_verified_ts_source(local_record, product_id)


def resolve_verified_ts_source(
    record: JobRecord,
    product_id: str,
    remote_fetcher: RemoteResultFetcher | None = None,
) -> VerifiedTsSource:
    """Resolve a manifest product to a completed, frequency-verified TS.

    A TS label alone is never evidence. Batch checkpoint item state and
    per-item provenance, or the TS Mode final report, must agree with the
    selected structure product.
    """
    if record.status != JobStatus.COMPLETED:
        raise ValueError("IRC source job must be completed")
    if record.spec.workflow not in {"BatchOptimize", "tsmode"}:
        raise ValueError("IRC source must be a completed TS calculation")
    if not record.work_dir:
        raise ValueError("IRC source job has no result directory")
    result = record.result or {}
    if result.get("node") and result.get("remote_dir"):
        if remote_fetcher is None:
            raise ValueError("Remote TS result fetch is unavailable")
        return _resolve_remote_ts_source(record, product_id, remote_fetcher)
    root = Path(record.work_dir)
    manifest = load_result_manifest(root)
    if manifest is None:
        raise ValueError("IRC source has no result manifest")
    product = next((entry for entry in manifest.products if entry.id == product_id), None)
    if product is None or product.kind.value != "structure":
        raise ValueError("IRC source must name a final structure product")
    geometry = resolve_safe(root / "RESULT", product.path)
    if geometry is None:
        raise ValueError("IRC source structure is missing or outside RESULT")
    xyz = geometry.read_text(encoding="utf-8")
    if record.spec.workflow == "BatchOptimize":
        if not product_id.startswith("batch_"):
            raise ValueError("IRC requires a formal BatchOptimize TS result")
        item_id = product_id.removeprefix("batch_")
        if "__TAG_TS__" not in product.path:
            raise ValueError("IRC source is not a TS result")
        checkpoint = _json_object(root / "WORK" / "00_RUNTIME" / "checkpoint.json")
        states = checkpoint.get("items_state")
        item = states.get(item_id) if isinstance(states, dict) else None
        if not isinstance(item, dict) or item.get("status") not in {"completed", "skipped"}:
            raise ValueError("TS optimization did not complete")
        optimized_rel = str(item.get("optimized_xyz") or "")
        optimized = resolve_safe(root, optimized_rel) if optimized_rel else None
        if item.get("tag") != "TS" or optimized is None:
            raise ValueError("TS result does not match its calculation item")
        if item.get("state_id"):
            raise ValueError("IRC cannot yet reproduce this TS electronic-state configuration")
        if _geometry_lines(optimized.read_text(encoding="utf-8")) != _geometry_lines(xyz):
            raise ValueError("TS result geometry differs from the optimized item")
        freq = item.get("frequency")
        if not isinstance(freq, dict) or freq.get("status") != "completed":
            raise ValueError("TS source has no completed frequency calculation")
        negative = _frequencies(freq.get("frequencies"))[0]
        frequency_evidence = {"item_id": item_id, "frequency": freq}
        provenance = _json_object(root / "RESULT" / "batch_provenance.json")
        records = provenance.get("items")
        evidence = (
            next(
                (
                    entry for entry in records
                    if isinstance(entry, dict) and entry.get("item_id") == item_id
                ),
                None,
            )
            if isinstance(records, list)
            else None
        )
        effective = evidence.get("effective_config") if isinstance(evidence, dict) else None
        if not isinstance(effective, dict) or evidence.get("role") != "ts":
            raise ValueError("TS effective method provenance is unavailable")
        method = str(effective.get("method") or "")
        basis = str(effective.get("basis") or "")
        options = BatchMethodOptions.from_method_dict(record.spec.method)
        if not method or (method, basis) != options.for_role(True):
            raise ValueError("TS method provenance disagrees with its task configuration")
        roles = record.spec.method.get("batch_roles")
        ts_role = roles.get("ts") if isinstance(roles, dict) else None
        if isinstance(ts_role, dict) and ts_role.get("dispersion") not in (None, "", "none"):
            raise ValueError("IRC cannot verify a TS with separate dispersion settings")
        try:
            charge = int(item["charge"])
            multiplicity = int(item["multiplicity"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("TS source charge or multiplicity is unavailable") from exc
    else:
        if product_id != "tsmode_optimized":
            raise ValueError("IRC requires the final TS Mode optimized product")
        report = _json_object(root / "RESULT" / "tsmode" / "tsmode_report.json")
        if (
            report.get("optimization_status") != "completed"
            or report.get("frequency_status") != "completed"
        ):
            raise ValueError("TS Mode optimization and final frequency must complete")
        modes = report.get("imaginary_modes")
        negative = _frequencies(
            [mode.get("frequency_cm1") for mode in modes if isinstance(mode, dict)]
            if isinstance(modes, list) else None
        )[0]
        frequency_evidence = {
            "imaginary_modes": modes,
            "frequency_status": report.get("frequency_status"),
            "validation": report.get("validation"),
        }
        level = report.get("resolved_level")
        if not isinstance(level, dict):
            raise ValueError("TS Mode effective method is unavailable")
        method = str(level.get("method") or "")
        basis = str(level.get("basis") or "")
        if not method:
            raise ValueError("TS Mode effective method is unavailable")
        # TS Mode snapshots the original frequency source's electronic state.
        bundle = _json_object(root / "INPUT" / "tsmode" / "source_bundle.json")
        bundle_level = bundle.get("level")
        if not isinstance(bundle_level, dict) or any(
            str(bundle_level.get(key) or "") != str(level.get(key) or "")
            for key in ("method", "basis", "solvent", "solvent_model", "dispersion", "grid")
        ):
            raise ValueError("TS Mode report and source bundle disagree on the calculation level")
        try:
            charge = int(bundle["charge"])
            multiplicity = int(bundle["multiplicity"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("TS Mode charge or multiplicity is unavailable") from exc
        if any(level.get(key) for key in ("solvent", "solvent_model", "dispersion", "grid")):
            raise ValueError("IRC cannot yet reproduce this TS Mode solvent/dispersion/grid level")
    if charge < -100 or multiplicity < 1:
        raise ValueError("TS charge or multiplicity is invalid")
    xyz_lines = xyz.splitlines()
    tag = parse_tag_comment(xyz_lines[1] if len(xyz_lines) > 1 else "").get("tag")
    if tag not in {"TS", None}:
        raise ValueError("TS structure tag disagrees with source evidence")
    return VerifiedTsSource(
        job_id=record.id,
        product_id=product_id,
        xyz=xyz,
        geometry_sha256=hashlib.sha256(xyz.encode("utf-8")).hexdigest(),
        method=method,
        basis=basis,
        charge=charge,
        multiplicity=multiplicity,
        imaginary_frequency_cm1=negative,
        frequency_evidence_sha256=hashlib.sha256(
            json.dumps(frequency_evidence, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        source_completed_at=record.completed_at or "",
        source_workflow=record.spec.workflow,
    )
