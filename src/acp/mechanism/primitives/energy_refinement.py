"""Optional single-point refinement for an S2 scan trajectory.

QC execution is injected via ``sp_callable(frame_xyz_path) -> float | None``.
The geometry/SHA256 cache semantics are preserved from RPH.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from _thread import LockType
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, cast

logger = logging.getLogger(__name__)

Record = dict[str, object]
SpCallable = Callable[[Path], float | None]
EventCallback = Callable[[str, dict[str, object]], None]


class QueueItem(TypedDict):
    index: int
    point_id: str
    frame: str
    xyz_sha256: str
    role: str


@dataclass(frozen=True)
class SinglePointSpec:
    """Scientific identity of an injected single-point refinement."""

    engine: str = "orca"
    task: str = "sp"
    method: str = "B97-3c"
    basis: str = ""
    aux_basis: str = ""
    solvent: str = "acetone"
    solvent_model: str = "CPCM"
    route_extras: str = ""
    charge: int = 0
    multiplicity: int = 1
    nproc: int = 1
    memory: str = "2GB"
    maxcore: int | None = None
    timeout: int | None = None


class ScanEnergyRefiner:
    """Run resumable injected SP jobs without changing scan geometries."""

    CACHE_SCHEMA: str = "s2_geometry_sp_cache_v2"

    def __init__(
        self,
        output_dir: Path,
        *,
        sp_callable: SpCallable,
        spec: SinglePointSpec | None = None,
        event_callback: EventCallback | None = None,
        variant: str | None = None,
        enabled: bool = True,
        parallel_jobs: int = 1,
    ) -> None:
        self.output_dir: Path = Path(output_dir)
        self.sp_callable: SpCallable = sp_callable
        self.spec: SinglePointSpec = spec or SinglePointSpec()
        self.event_callback: EventCallback | None = event_callback
        self.variant: str = variant or "product"
        self._enabled: bool = bool(enabled)
        self.parallel_jobs: int = max(1, int(parallel_jobs))
        self._legacy_cache_index: dict[tuple[str, str], Record] = {}
        self._legacy_index_spec_signature: str = ""
        self._progress_lock: LockType = threading.Lock()
        self._progress_done: int = 0
        self._progress_failed: int = 0
        self._progress_total: int = 0
        self._progress_workers: int = 1
        self._batch_started_monotonic: float = 0.0
        self._batch_id: str = f"{self.variant}:b973c_sp"

    def _emit(self, event: str, **fields: object) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event, {"variant": self.variant, **fields})
        except Exception as exc:  # pragma: no cover - UI isolation
            logger.warning("[S2] Ignoring B97-3c UI callback failure for %s: %s", event, exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _signature(spec: SinglePointSpec) -> str:
        payload = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _write_json_atomic(path: Path, payload: Record) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        _ = temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _ = temporary.replace(path)

    def _index_legacy_frame_cache(self, spec_signature: str) -> None:
        if self._legacy_index_spec_signature == spec_signature and self._legacy_cache_index:
            return
        self._legacy_cache_index = {}
        for cache_path in sorted(self.output_dir.glob("frame_*/result.json")):
            try:
                record = cast(Record, json.loads(cache_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
            xyz_hash = record.get("xyz_sha256")
            cached_spec = record.get("spec_sha256")
            if (
                isinstance(xyz_hash, str)
                and cached_spec == spec_signature
                and record.get("status") == "complete"
                and record.get("energy_hartree") is not None
            ):
                record["legacy_cache_path"] = str(cache_path)
                self._legacy_cache_index[(xyz_hash, str(cached_spec))] = record
        self._legacy_index_spec_signature = spec_signature

    def _run_one_impl(
        self,
        item: QueueItem,
        spec: SinglePointSpec,
        spec_signature: str,
    ) -> Record:
        index = item["index"]
        frame = Path(item["frame"])
        xyz_hash = item.get("xyz_sha256") or self._file_hash(frame)
        job_dir = self.output_dir / "cache" / f"{xyz_hash[:20]}_{spec_signature[:12]}"
        cache_path = job_dir / "result.json"
        if cache_path.is_file():
            try:
                cached = cast(Record, json.loads(cache_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                cached = {}
            if (
                cached.get("schema_version") == self.CACHE_SCHEMA
                and cached.get("xyz_sha256") == xyz_hash
                and cached.get("spec_sha256") == spec_signature
                and cached.get("status") == "complete"
                and cached.get("energy_hartree") is not None
            ):
                cached["reused"] = True
                cached["frame_index"] = index
                cached["point_id"] = item.get("point_id")
                cached["input_xyz"] = str(frame)
                return cached

        legacy = self._legacy_cache_index.get((xyz_hash, spec_signature))
        if legacy:
            migrated = dict(legacy)
            migrated.update(
                {
                    "schema_version": self.CACHE_SCHEMA,
                    "frame_index": index,
                    "point_id": item.get("point_id"),
                    "input_xyz": str(frame),
                    "reused": True,
                    "migrated_from": legacy.get("legacy_cache_path"),
                }
            )
            self._write_json_atomic(cache_path, migrated)
            return migrated

        energy = self.sp_callable(frame)
        record: Record = {
            "schema_version": self.CACHE_SCHEMA,
            "frame_index": index,
            "point_id": item.get("point_id"),
            "input_xyz": str(frame),
            "xyz_sha256": xyz_hash,
            "spec_sha256": spec_signature,
            "method": spec.method,
            "solvent": spec.solvent,
            "solvent_model": spec.solvent_model,
            "status": "complete" if energy is not None else "failed",
            "energy_hartree": None if energy is None else float(energy),
            "output_file": None,
            "error": None if energy is not None else "sp_callable_returned_none",
            "reused": False,
        }
        self._write_json_atomic(cache_path, record)
        return record

    def _run_one(
        self,
        item: QueueItem,
        spec: SinglePointSpec,
        spec_signature: str,
    ) -> Record:
        point_id = item.get("point_id") or f"frame_{item['index']:04d}"
        job_id = f"{self.variant}:{point_id}"
        xyz_hash = item.get("xyz_sha256") or self._file_hash(Path(item["frame"]))
        job_dir = self.output_dir / "cache" / f"{xyz_hash[:20]}_{spec_signature[:12]}"
        started = time.monotonic()
        self._emit(
            "batch_job_started",
            batch=self._batch_id,
            job_id=job_id,
            point_id=point_id,
            engine=spec.engine,
            method=spec.method,
            nprocs=int(spec.nproc or 1),
            output=str(job_dir),
            started_at=time.time(),
        )
        try:
            record = self._run_one_impl(item, spec, spec_signature)
        except Exception as exc:
            elapsed = time.monotonic() - started
            self._emit(
                "batch_job_failed",
                batch=self._batch_id,
                job_id=job_id,
                point_id=point_id,
                engine=spec.engine,
                method=spec.method,
                nprocs=int(spec.nproc or 1),
                output=str(job_dir),
                elapsed_seconds=elapsed,
                error=str(exc),
            )
            self._update_progress(point_id=point_id, failed=True)
            raise

        failed = record.get("status") != "complete" or record.get("energy_hartree") is None
        elapsed = time.monotonic() - started
        self._emit(
            "batch_job_failed" if failed else "batch_job_finished",
            batch=self._batch_id,
            job_id=job_id,
            point_id=point_id,
            engine=spec.engine,
            method=spec.method,
            nprocs=int(spec.nproc or 1),
            output=record.get("output_file") or str(job_dir),
            elapsed_seconds=elapsed,
            status="failed" if failed else "cached" if record.get("reused") else "complete",
            energy_hartree=record.get("energy_hartree"),
            error=record.get("error"),
            reused=bool(record.get("reused")),
        )
        self._update_progress(point_id=point_id, failed=failed)
        return record

    def _update_progress(self, *, point_id: str, failed: bool) -> None:
        with self._progress_lock:
            if failed:
                self._progress_failed += 1
            else:
                self._progress_done += 1
            done = self._progress_done
            failed_count = self._progress_failed
            processed = done + failed_count
            total = self._progress_total
            elapsed = max(0.0, time.monotonic() - self._batch_started_monotonic)
            rate = processed * 60.0 / elapsed if elapsed > 0.0 else None
            eta = (
                max(0.0, (total - processed) * elapsed / processed)
                if processed > 0 and processed < total
                else 0.0
            )
            self._emit(
                "batch_progress",
                batch=self._batch_id,
                label="ORCA B97-3c SP refinement",
                phase="b973c_sp",
                total=total,
                done=done,
                failed=failed_count,
                running=max(0, min(total - processed, self._progress_workers)),
                current=point_id,
                elapsed_seconds=elapsed,
                rate_per_minute=rate,
                eta_seconds=eta,
            )

    def refine(
        self,
        frame_paths: Sequence[Path],
        *,
        point_ids: Sequence[str] | None = None,
    ) -> Record:
        if point_ids is not None and len(point_ids) != len(frame_paths):
            raise ValueError("S2 energy refinement requires one point_id per frame")
        if not self.enabled:
            return {
                "enabled": False,
                "status": "not_requested",
                "energies_hartree": [None for _ in frame_paths],
                "records": [],
                "full_coverage": False,
            }

        spec = self.spec
        max_workers = max(1, min(self.parallel_jobs, len(frame_paths) or 1))
        spec_signature = self._signature(spec)
        self._index_legacy_frame_cache(spec_signature)
        items: list[QueueItem] = [
            {
                "index": index,
                "point_id": point_ids[index] if point_ids is not None else f"frame_{index:04d}",
                "frame": str(Path(frame)),
                "xyz_sha256": self._file_hash(Path(frame)),
                "role": "frame",
            }
            for index, frame in enumerate(frame_paths)
        ]
        self._progress_done = 0
        self._progress_failed = 0
        self._progress_total = len(items)
        self._progress_workers = max_workers
        self._batch_started_monotonic = time.monotonic()
        self._emit(
            "batch_started",
            batch=self._batch_id,
            label="ORCA B97-3c SP refinement",
            phase="b973c_sp",
            total=len(items),
            done=0,
            failed=0,
            running=min(max_workers, len(items)),
            started_at=time.time(),
            parallel_jobs=max_workers,
            cores_per_job=int(spec.nproc or 1),
            engine=spec.engine,
            method=spec.method,
        )
        try:
            if max_workers == 1 or len(items) <= 1:
                records = [self._run_one(item, spec, spec_signature) for item in items]
            else:

                def runner(item: QueueItem) -> Record:
                    return self._run_one(item, spec, spec_signature)

                with ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="acp-s2-sp",
                ) as executor:
                    records = list(executor.map(runner, items))
        except Exception as exc:
            self._emit(
                "batch_finished",
                batch=self._batch_id,
                label="ORCA B97-3c SP refinement",
                phase="b973c_sp",
                total=len(items),
                done=self._progress_done,
                failed=max(1, self._progress_failed),
                running=0,
                status="failed",
                elapsed_seconds=time.monotonic() - self._batch_started_monotonic,
                error=str(exc),
            )
            raise

        energies: list[float | None] = [None] * len(frame_paths)
        for record in records:
            energy_hartree = record.get("energy_hartree")
            frame_index = record.get("frame_index")
            if (
                record.get("status") == "complete"
                and isinstance(frame_index, int)
                and isinstance(energy_hartree, (int, float))
            ):
                energies[frame_index] = float(energy_hartree)
        completed = sum(value is not None for value in energies)
        final_status = "complete" if completed == len(frame_paths) else "partial"
        self._emit(
            "batch_finished",
            batch=self._batch_id,
            label="ORCA B97-3c SP refinement",
            phase="b973c_sp",
            total=len(items),
            done=completed,
            failed=len(items) - completed,
            running=0,
            status=final_status,
            elapsed_seconds=time.monotonic() - self._batch_started_monotonic,
            method=spec.method,
        )
        return {
            "enabled": True,
            "status": final_status,
            "method": spec.method,
            "solvent": spec.solvent,
            "solvent_model": spec.solvent_model,
            "completed": completed,
            "total": len(frame_paths),
            "parallel_jobs": max_workers,
            "cores_per_job": int(spec.nproc or 1),
            "energies_hartree": energies,
            "records": records,
            "full_coverage": completed == len(frame_paths),
        }


__all__ = ["ScanEnergyRefiner", "SinglePointSpec"]
