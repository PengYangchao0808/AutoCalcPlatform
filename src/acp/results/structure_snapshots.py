"""Durable content-addressed geometry snapshots before destructive reruns."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from acp.results.structure_policy import single_geometry
from acp.results.frame_candidate_store import atomic_write_text


def preserve_outputs(run_root: Path, work_dir: Path, outputs: list[dict[str, Any]],
                     *, job_id: str, attempt: int, input_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify all snapshots before recording references; failures abort requeue."""
    objects = Path(run_root) / ".structure_snapshots" / "objects"
    history = Path(work_dir) / ".structure_history"
    objects.mkdir(parents=True, exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)
    refs = []
    # Materialized original input survives even when its source file is replaced.
    input_path = Path(work_dir) / "input.xyz"
    if input_path.is_file():
        input_text = input_path.read_text(encoding="utf-8")
        if single_geometry(input_text) is not None:
            atomic_write_text(history / f"input_{attempt}.xyz", input_text)
    for index, item in enumerate(input_spec.get("items") or []):
        if not isinstance(item, dict):
            continue
        input_text = item.get("xyz") or item.get("xyz_text")
        if input_text and single_geometry(input_text) is not None:
            atomic_write_text(history / f"input_{attempt}_item_{index}.xyz", input_text)
    for output in outputs:
        text = output.get("xyz_text") or ""
        geometry = single_geometry(text)
        if geometry is None:
            raise ValueError("无法保存有效的旧结构快照；已阻断重算")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target = objects / (digest + ".xyz")
        if not target.exists():
            atomic_write_text(target, text)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("结构快照校验失败；已阻断重算")
        # Per-attempt aliases keep both IRC directions and item identities.
        ref_digest = hashlib.sha256(f"{job_id}:{attempt}:{output['entry_id']}:{digest}".encode()).hexdigest()
        alias = history / (ref_digest + ".xyz")
        if not alias.exists():
            atomic_write_text(alias, text)
        refs.append({k: v for k, v in output.items() if k != "xyz_text"})
        refs[-1].update({"path": alias.relative_to(work_dir).as_posix(),
                        "snapshot_checksum": geometry["content_checksum"],
                        "original_path": output["path"], "attempt": attempt})
    from acp.scheduler.structure_source_store import source_uid_for
    for ref in refs:
        ref["original_source_uid"] = ref.get("source_uid")
        uid = source_uid_for(job_id, ref["path"])
        ref["source_uid"] = uid
        ref["source_ref"] = {**ref.get("source_ref", {}), "source_uid": uid,
                             "source_id": f"job_{job_id}:{ref['path']}", "path": ref["path"]}
    record_path = history / "sources.json"
    existing = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else []
    identities = {(r["attempt"], r["entry_id"]) for r in refs}
    existing = [r for r in existing if (r["attempt"], r["entry_id"]) not in identities]
    atomic_write_text(record_path, json.dumps(existing + refs, ensure_ascii=False, indent=2))
    atomic_write_text(history / f"input_{attempt}.json", json.dumps(input_spec, ensure_ascii=False, indent=2))
    return refs

__all__ = ["preserve_outputs"]
