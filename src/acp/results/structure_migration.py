"""Conservative, idempotent backfill of evidence-bound historical OPT outputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from acp.results.frame_candidate_store import atomic_write_text
from acp.results.manifest import load_result_manifest
from acp.results.structure_policy import POLICY_VERSION, single_geometry
from acp.scheduler.files import resolve_safe
from acp.storage.manifest import ResultManifest


def backfill_successful_optimizations(task_root: Path, workflow: str, *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Recover only item-bound, explicitly converged optimization trajectories.

    No inference from task status, XYZ existence, or the last trajectory frame.
    Existing products and diagnostic trajectories remain untouched.
    """
    root = Path(task_root)
    if workflow not in {"BatchOptimize", "optimize", "xtb_optimize", "tsmode", "nmr"}:
        return []
    work = root / "WORK"
    if not work.is_dir():
        return []
    manifest = load_result_manifest(root)
    if manifest is None and (root / "RESULT" / "result_manifest.json").exists():
        return []  # Never replace an unreadable authority file.
    manifest = manifest or ResultManifest(workflow=workflow, status="failed")
    known = {product.id for product in manifest.products}
    recovered = []
    for evidence in sorted(work.rglob("optimization_trajectory.json")):
        try:
            evidence.resolve().relative_to(root.resolve())
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            item_id = str(payload.get("item_id") or "")
            if payload.get("converged") is not True or payload.get("status") != "completed" or not item_id:
                continue
            cycles = payload.get("cycles") or []
            final = cycles[-1] if cycles else {}
            ref = final.get("geometry_ref")
            if not isinstance(ref, str) or not ref:
                continue
            geometry_path = resolve_safe(evidence.parent, ref)
            if geometry_path is None:
                continue
            geometry_path.resolve().relative_to(root.resolve())
            text = geometry_path.read_text(encoding="utf-8")
            if single_geometry(text) is None:
                continue
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if any(product.metadata.get("item_id") == item_id and
               product.metadata.get("optimization_status") == "converged"
               for product in manifest.products):
            continue
        evidence_rel = evidence.relative_to(root).as_posix()
        identity = "historical_opt_" + hashlib.sha256((item_id + ":" + evidence_rel).encode()).hexdigest()[:24]
        if identity in known:
            continue
        path = "structures/" + identity + ".xyz"
        facts = {"item_id": item_id, "optimization_status": "converged", "frequency_status": "unknown",
                 "source_kind": "optimization", "auto_reusable": True, "policy_version": POLICY_VERSION,
                 "migration_evidence": evidence_rel, "stage_id": evidence.parent.relative_to(root).as_posix()}
        recovered.append({"id": identity, "path": path, "metadata": facts})
        if not dry_run:
            atomic_write_text(root / "RESULT" / path, text)
            manifest.add_product(identity, item_id + " · recovered OPT", path, "structure", metadata=facts)
            known.add(identity)
    if recovered and not dry_run:
        manifest.write(root / "RESULT")
    return recovered


__all__ = ["backfill_successful_optimizations"]
