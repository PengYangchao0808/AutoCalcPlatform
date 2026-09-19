"""Job Edit & Recalculate (docs/ACP_Edit_And_Recalculate_Plan.md).

Draft restoration, workflow edit-coverage registry, editable-spec diffing,
preview fingerprints, idempotency operation records, and last-valid-structure
resolution.  Execution stays in :mod:`acp.scheduler.manager`; this module owns
everything editable-spec shaped so the manager does not keep growing.

Key contracts (plan §6/§9):

* The server-side ``JobRecord.spec`` is the authoritative source for the
  original submission parameters; the draft is a projection of it.
* ``source_revision`` binds the draft to ``{spec semantics, attempt, input
  hash}`` so ordinary progress updates never fabricate edit conflicts.
* ``request_id`` + payload hash give exactly-once submission semantics that
  survive network retries and service restarts (SQLite-backed operation log).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord, JobSpec

logger = logging.getLogger(__name__)

__all__ = [
    "EDIT_ACTIVE_WORKFLOWS",
    "EditConflictError",
    "EditValidationError",
    "JobEditOperationStore",
    "attempt_number",
    "audit_workflow_edit_coverage",
    "build_edit_draft",
    "compute_payload_hash",
    "compute_preview_fingerprint",
    "compute_source_revision",
    "diff_editable_specs",
    "editable_spec_from_parts",
    "editable_spec_from_record",
    "effective_config_info",
    "normalize_for_compare",
    "resolve_last_structure",
    "workflow_edit_status",
]


class EditConflictError(RuntimeError):
    """409-class conflict: stale source_revision / replayed request_id payload."""


class EditValidationError(ValueError):
    """422-class error: the submitted editable spec is not usable."""


# --- workflow edit coverage registry (plan §1/§5) --------------------------
# Every catalog entry with status "active" MUST appear here.  The coverage
# audit (and its test) fails when a newly added active workflow forgets to
# register edit support — the entry point must never silently go missing.
EDIT_ACTIVE_WORKFLOWS: frozenset[str] = frozenset(
    {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "casscf",
        "xtb_optimize",
        "nmr",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
    }
)

# Retired/planned workflows never re-open their execution entry; the draft
# only offers read-only parameter viewing plus a migration hint (plan §5.1).
RETIRED_MIGRATION_HINTS: dict[str, str] = {
    "optfreq": "BatchOptimize --profile opt_freq（或 optimize + frequency）",
    "optfreqsp": "BatchOptimize --profile opt_freq_sp",
    "Lowconfirm": "BatchOptimize --profile opt_freq",
    "Highconfirm": "BatchOptimize --profile opt_freq_sp_thermo",
    "ensemble": "Confsearch --protocol censo-crest --refinement-policy screen",
    "energy": "Confsearch --protocol censo-crest --refinement-policy rank1/cumulative-99",
    "xtbmd_censo_energy": "Confsearch --protocol xtbmd-censo",
    "conformer": "Confsearch",
    "benchmark": "—（基准执行器已退役，可提取兼容输入另建有效任务）",
    "mechanism": "PESsearch → BatchOptimize → irc 分阶段入口",
    "mech-conf": "BatchOptimize",
    "mech-step": "PESsearch / BatchOptimize / irc",
    "mech-confirm": "BatchOptimize",
    "mech-chain": "irc",
}


def _catalog_status_map() -> dict[str, str]:
    try:
        from acp.catalog import WORKFLOW_CATALOG
    except ImportError:  # pragma: no cover - catalog is always importable
        return {}
    return {str(w.get("id")): str(w.get("status") or "unknown") for w in WORKFLOW_CATALOG}


def audit_workflow_edit_coverage() -> dict[str, list[str]]:
    """Return ``{"missing": [...], "stale": [...]}`` coverage audit.

    ``missing``: catalog-active workflows without edit registration (the plan
    requires the entry point to fail loudly, not silently).  ``stale``:
    registered workflows that are no longer catalog-active.
    """
    statuses = _catalog_status_map()
    active = {wid for wid, status in statuses.items() if status == "active"}
    missing = sorted(active - EDIT_ACTIVE_WORKFLOWS)
    stale = sorted(EDIT_ACTIVE_WORKFLOWS - active)
    return {"missing": missing, "stale": stale}


def workflow_edit_status(workflow_id: str) -> dict[str, Any]:
    """Edit capability projection for one workflow id."""
    status = _catalog_status_map().get(workflow_id, "unknown")
    editable = workflow_id in EDIT_ACTIVE_WORKFLOWS and status == "active"
    return {
        "status": status,
        "editable": editable,
        "migration_hint": None if editable else RETIRED_MIGRATION_HINTS.get(workflow_id),
    }


# --- canonicalisation / revision fingerprints -------------------------------


def _canonicalize(value: Any) -> Any:
    """Recursively normalise a JSON-ish value for order-insensitive compare."""
    if isinstance(value, dict):
        return {str(k): _canonicalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def normalize_for_compare(*sections: Any) -> str:
    """Canonical JSON string over the given spec sections (sorted keys)."""
    payload = [_canonicalize(section) for section in sections]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def attempt_number(record: JobRecord) -> int:
    """1-based attempt number tracked in ``record.result['attempts']``."""
    return int((record.result or {}).get("attempts") or 1)


def compute_source_revision(record: JobRecord) -> str:
    """Version the *configuration identity* of a job for optimistic editing.

    Covers the spec's computational semantics (workflow/input/method/
    resources), the current attempt number, and the recorded input hash.
    Runtime-only fields (status, progress, pid, timestamps, result payload)
    are deliberately excluded so normal progress updates cannot fabricate a
    conflict (plan §9).
    """
    spec = record.spec
    payload = {
        "workflow": spec.workflow,
        "input": spec.input,
        "method": spec.method,
        "resources": spec.resources,
        "attempt": attempt_number(record),
        "input_hash": record.input_hash or "",
    }
    canonical = normalize_for_compare(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sr_{digest[:24]}"


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Hash a submit request payload for request_id idempotency comparison."""
    canonical = normalize_for_compare(payload)
    return "ph_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def compute_preview_fingerprint(
    job_id: str,
    source_revision: str,
    workflow: str,
    input_spec: dict,
    method: dict,
    resources: dict,
) -> str:
    """Bind a preview to the server-normalised configuration/input version."""
    canonical = normalize_for_compare(
        {
            "job_id": job_id,
            "source_revision": source_revision,
            "workflow": workflow,
            "input": input_spec,
            "method": method,
            "resources": resources,
        }
    )
    return "pf_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# --- editable spec projection -------------------------------------------------


def editable_spec_from_parts(
    *,
    workflow: str,
    input_spec: dict[str, Any],
    method: dict[str, Any],
    resources: dict[str, Any],
    molecule_name: str = "",
    task_name: str = "",
    remark: str = "",
    tags: list[str] | None = None,
    node_tags: list[str] | None = None,
    project_id: str | None = None,
    execution_mode: str | None = None,
    target_node: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Wire-level editable spec built from raw parts (draft/submit parity)."""
    return {
        "workflow": workflow,
        "input": json.loads(json.dumps(input_spec or {}, default=str)),
        "method": json.loads(json.dumps(method or {}, default=str)),
        "resources": json.loads(json.dumps(resources or {}, default=str)),
        "molecule_name": molecule_name,
        "task_name": task_name,
        "remark": remark,
        "tags": list(tags or []),
        "node_tags": list(node_tags or []),
        "project_id": project_id,
        "execution_mode": execution_mode,
        "target_node": target_node,
        "config_path": config_path,
    }


def editable_spec_from_record(record: JobRecord) -> dict[str, Any]:
    """Project a JobRecord into the wire-level editable spec (plan §9).

    Runtime fields (status/pid/remote_job_id/work_dir/result) are excluded on
    purpose — the client can never write them through an edit submission.
    """
    spec = record.spec
    return editable_spec_from_parts(
        workflow=spec.workflow,
        input_spec=spec.input,
        method=spec.method,
        resources=spec.resources,
        molecule_name=spec.molecule_name,
        task_name=spec.task_name,
        remark=spec.remark,
        tags=spec.tags,
        node_tags=spec.node_tags,
        project_id=record.project_id or spec.project_id,
        execution_mode=getattr(spec.execution_mode, "value", None) or spec.execution_mode,
        target_node=spec.target_node,
        config_path=spec.config_path,
    )


def _input_kind(inp: Any) -> str:
    if not isinstance(inp, dict):
        return "plain"
    if isinstance(inp.get("scan_request"), dict):
        return "scan_request"
    source_type = str(inp.get("source_type") or "")
    if source_type:
        return source_type
    if inp.get("source_job_id") or inp.get("from_artifact"):
        return "stage_artifact"
    return "structured"


def _describe_original_input(record: JobRecord, run_root: Path | None = None) -> dict[str, Any]:
    """Summarise the original input dependencies (plan §6.2)."""
    spec = record.spec
    inp = spec.input
    work_dir = Path(record.work_dir) if record.work_dir else None
    desc: dict[str, Any] = {
        "kind": _input_kind(inp),
        "charge": inp.get("charge") if isinstance(inp, dict) else None,
        "multiplicity": inp.get("multiplicity") if isinstance(inp, dict) else None,
        "materialized_input_xyz": bool(work_dir and (work_dir / "input.xyz").is_file()),
    }
    if isinstance(inp, dict):
        items = inp.get("items")
        if isinstance(items, list):
            desc["items_count"] = len(items)
        source_job_id = inp.get("source_job_id")
        if source_job_id:
            desc["source_job_id"] = str(source_job_id)
        artifact = inp.get("from_artifact")
        if artifact:
            desc["from_artifact"] = str(artifact)
            desc["from_artifact_available"] = bool(
                work_dir and (work_dir / str(artifact)).is_file()
            )
        candidates = inp.get("candidates")
        if isinstance(candidates, list):
            desc["candidates_count"] = len(candidates)
        # SMILES provenance recorded at submit time.
        if work_dir is not None:
            source_json = work_dir / "input_source.json"
            if source_json.is_file():
                try:
                    desc["input_source"] = json.loads(source_json.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    desc["input_source"] = None
    return desc


def _xyz_atom_count(xyz_text: str) -> int | None:
    """Return the declared XYZ atom count when the payload is usable."""
    lines = xyz_text.lstrip("\ufeff \t\r\n").splitlines()
    if not lines:
        return None
    try:
        count = int(lines[0].strip())
    except (TypeError, ValueError):
        return None
    return count if count > 0 and len(lines) >= count + 2 else None


def _structure_source_item(
    record: JobRecord,
    *,
    item_id: str,
    name: str,
    xyz_text: str,
    tag: str = "",
    charge: Any = 0,
    multiplicity: Any = 1,
    source_kind: str = "original_input",
) -> dict[str, Any]:
    """Project inline task geometry to the shared frontend source contract."""
    atom_count = _xyz_atom_count(xyz_text)
    available = atom_count is not None
    return {
        "source_id": f"edit-{source_kind}:{record.id}:{item_id}",
        "source_kind": source_kind,
        "job_id": record.id,
        "item_id": item_id,
        "name": name or item_id,
        "tag": tag if tag in {"INT", "TS"} else "",
        "atom_count": atom_count or 0,
        "charge": charge if charge is not None else 0,
        "multiplicity": multiplicity if multiplicity is not None else 1,
        "geometry_ref": {"kind": "inline_xyz", "item_id": item_id},
        "geometry_status": "available" if available else "missing",
        "geometry_error": None if available else "该来源没有可用几何",
        "xyz_text": xyz_text if available else "",
    }


def project_original_structure_items(record: JobRecord) -> list[dict[str, Any]]:
    """Return every reusable geometry from the submitted task input.

    Stable identifiers prefer persisted ``item_id``/``candidate_id`` values
    and otherwise use the input-order index. Geometry is inline-only here, so
    the draft never exposes an arbitrary filesystem path.
    """
    inp = record.spec.input if isinstance(record.spec.input, dict) else {}
    default_charge = inp.get("charge", 0)
    default_mult = inp.get("multiplicity", 1)
    projected: list[dict[str, Any]] = []

    items = inp.get("items")
    if isinstance(items, list):
        for index, raw in enumerate(items, start=1):
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("item_id") or raw.get("candidate_id") or f"item_{index:03d}")
            projected.append(
                _structure_source_item(
                    record,
                    item_id=item_id,
                    name=str(raw.get("name") or raw.get("candidate_id") or item_id),
                    xyz_text=str(raw.get("xyz") or raw.get("xyz_text") or raw.get("source") or ""),
                    tag=str(raw.get("tag") or ""),
                    charge=raw.get("charge", default_charge),
                    multiplicity=raw.get("multiplicity", default_mult),
                )
            )
        return projected

    candidates = inp.get("candidates")
    if isinstance(candidates, list):
        for index, raw in enumerate(candidates, start=1):
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("item_id") or raw.get("candidate_id") or f"candidate_{index:03d}")
            projected.append(
                _structure_source_item(
                    record,
                    item_id=item_id,
                    name=str(raw.get("name") or raw.get("candidate_id") or item_id),
                    xyz_text=str(raw.get("xyz") or raw.get("xyz_text") or raw.get("source") or ""),
                    tag=str(raw.get("tag") or ""),
                    charge=raw.get("charge", default_charge),
                    multiplicity=raw.get("multiplicity", default_mult),
                )
            )
        return projected

    source = inp
    scan_request = inp.get("scan_request")
    if isinstance(scan_request, dict) and isinstance(scan_request.get("source"), dict):
        source = scan_request["source"]
    xyz_text = str(source.get("xyz_text") or source.get("xyz") or source.get("source") or "")
    if _xyz_atom_count(xyz_text) is None and record.work_dir:
        materialized = Path(record.work_dir) / "input.xyz"
        try:
            xyz_text = materialized.read_text(encoding="utf-8")
        except OSError:
            pass
    projected.append(
        _structure_source_item(
            record,
            item_id="input_001",
            name=str(inp.get("name") or record.spec.molecule_name or "上次运行输入"),
            xyz_text=xyz_text,
            tag=str(inp.get("tag") or inp.get("input_role") or "")
            .upper()
            .replace("TRANSITION_STATE", "TS"),
            charge=source.get("charge", default_charge),
            multiplicity=source.get("multiplicity", default_mult),
        )
    )
    return projected


def resolve_last_structure(work_dir: Path | str) -> dict[str, Any] | None:
    """Resolve the task's best ``kind=structure`` product for re-use as input.

    Returns ``{"available": True, "entry_id", "label", "path", "xyz_text"}``
    or ``None`` when the task has no verifiable geometry product (plan §5:
    workflows without structures simply disable this option).
    """
    from acp.results.manifest import find_products, load_result_manifest

    task_dir = Path(work_dir)
    manifest = load_result_manifest(task_dir)
    if manifest is None:
        return None
    for product in find_products(manifest, "structure"):
        path = task_dir / "RESULT" / product.path
        try:
            xyz_text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not xyz_text.strip():
            continue
        return {
            "available": True,
            "entry_id": product.id,
            "label": product.label,
            "path": f"RESULT/{product.path}",
            "xyz_text": xyz_text,
        }
    return None


def effective_config_info(record: JobRecord) -> dict[str, Any]:
    """Last-effective-config availability for the edit draft.

    Authority order for edit hydration (plan §6.3): ``JobRecord.spec`` is the
    authoritative *submission*; ``effective_config.json`` captures what the
    engine actually resolved last run (inherited defaults included) and is
    display/reference only.  ``status`` tells the frontend which case holds:

    * ``snapshot``   — effective_config.json exists in the work dir.
    * ``recomputed`` — no snapshot; the effective config was recomputed from
      the submitted method dict (BatchOptimize only).
    * ``unavailable``— neither is available (non-batch workflows without a
      snapshot; the draft hydrates from the spec alone).
    """
    work_dir = Path(record.work_dir) if record.work_dir else None
    if work_dir is not None:
        try:
            from acp.calculations.batch.effective_config import (
                read_effective_config,
            )

            snapshot = read_effective_config(work_dir)
        except ImportError:  # pragma: no cover - module is always importable
            snapshot = None
        if snapshot is not None:
            return {
                "status": "snapshot",
                "source": "effective_config.json",
                "config": snapshot,
            }
    if record.spec.workflow == "BatchOptimize":
        try:
            from acp.calculations.batch.effective_config import (
                compute_effective_from_method,
            )

            recomputed = compute_effective_from_method(dict(record.spec.method))
            return {"status": "recomputed", "source": "spec.method", "config": recomputed}
        except Exception:  # noqa: BLE001 - degrade to unavailable, never block the draft
            logger.debug("effective-config recompute failed", exc_info=True)
    return {"status": "unavailable", "source": None, "config": None}


def _missing_fields(record: JobRecord) -> list[str]:
    """Required-but-absent dependencies, surfaced as blocking draft fields."""
    spec = record.spec
    inp = spec.input if isinstance(spec.input, dict) else {}
    method = spec.method if isinstance(spec.method, dict) else {}
    missing: list[str] = []
    work_dir = Path(record.work_dir) if record.work_dir else None

    kind = _input_kind(inp)
    has_source = bool(
        inp.get("input_artifact")
        or inp.get("source")
        or inp.get("input")
        or inp.get("smiles")
        or inp.get("xyz_text")
        or inp.get("items")
        or inp.get("candidates")
        or inp.get("scan_request")
    )
    if not has_source:
        missing.append("input.source")

    if spec.workflow == "casscf":
        casscf = method.get("casscf") if isinstance(method.get("casscf"), dict) else {}
        if not casscf.get("active_electrons"):
            missing.append("method.casscf.active_electrons")
        if not casscf.get("active_orbitals"):
            missing.append("method.casscf.active_orbitals")

    if spec.workflow == "BatchOptimize" and kind == "stage_artifact":
        artifact = str(inp.get("from_artifact") or "")
        if artifact and work_dir is not None and not (work_dir / artifact).is_file():
            missing.append("input.from_artifact")

    if spec.workflow == "nmr":
        experiment = inp.get("experiment")
        if isinstance(experiment, dict):
            path = experiment.get("path") or experiment.get("file")
            if isinstance(path, str) and path and not Path(path).is_file():
                missing.append("input.experiment.path")

    return missing


def build_edit_draft(
    record: JobRecord,
    *,
    run_root: Path | None = None,
) -> dict[str, Any]:
    """Build the full edit-draft payload for GET ``/jobs/{id}/edit-draft``."""
    spec = record.spec
    edit_status = workflow_edit_status(spec.workflow)
    editable = bool(edit_status["editable"])
    terminal = record.status.is_terminal
    reasons: list[str] = []
    if not editable:
        reasons.append(
            f"工作流 {spec.workflow} 为 {edit_status['status']}；原地编辑重算已禁用，"
            "仅支持查看与迁移"
        )
    can_in_place = editable and terminal
    if editable and not terminal:
        reasons.append(f"任务状态 {record.status.value} 非终态；请先取消并等待终态后再原地重算")
    can_new_job = editable

    input_refs: dict[str, Any] = {
        "mode": "original",
        "original": _describe_original_input(record, run_root=run_root),
        "structure_items": project_original_structure_items(record),
        "last_structure": resolve_last_structure(record.work_dir) if editable else None,
    }
    method = spec.method if isinstance(spec.method, dict) else {}
    notes: list[str] = []
    if not editable and edit_status.get("migration_hint"):
        notes.append(f"迁移建议：{edit_status['migration_hint']}")
    return {
        "job_id": record.id,
        "workflow": spec.workflow,
        "workflow_status": edit_status["status"],
        "job_status": record.status.value,
        "attempt": attempt_number(record),
        "source_revision": compute_source_revision(record),
        "editable_spec": editable_spec_from_record(record),
        "input_refs": input_refs,
        "effective_config": effective_config_info(record),
        "capabilities": {
            "can_edit": editable,
            "can_in_place": can_in_place,
            "can_new_job": can_new_job,
            "disabled_reasons": reasons,
        },
        "preserved_fields": sorted(str(k) for k in method.keys()),
        "missing_fields": _missing_fields(record),
        "migration_hint": edit_status.get("migration_hint"),
        "notes": notes,
    }


# --- diffing -------------------------------------------------------------------

_KIND_BY_TOP = {
    "method": "method",
    "resources": "resource",
    "input": "input",
    "tags": "meta",
    "node_tags": "execution",
    "remark": "meta",
    "task_name": "meta",
    "molecule_name": "meta",
    "execution_mode": "execution",
    "target_node": "execution",
    "project_id": "meta",
}


def _diff_values(path: str, old: Any, new: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            _diff_values(f"{path}.{key}" if path else str(key), old.get(key), new.get(key), out)
        return
    if isinstance(old, list) and isinstance(new, list):
        if _canonicalize(old) != _canonicalize(new):
            out.append({"path": path or "(root)", "kind": _kind_for(path), "old": old, "new": new})
        return
    if _canonicalize(old) != _canonicalize(new):
        out.append({"path": path or "(root)", "kind": _kind_for(path), "old": old, "new": new})


def _kind_for(path: str) -> str:
    top = path.split(".", 1)[0] if path else "meta"
    return _KIND_BY_TOP.get(top, "meta")


def diff_editable_specs(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Structural diff between two editable-spec projections.

    Omits-vs-null distinctions are preserved (plan §6.1 rule 4): ``None`` and
    a missing key both normalise to ``None`` on purpose — the wire contract
    treats them identically — while ``false``/``0``/``[]`` compare unequal to
    ``None``.
    """
    out: list[dict[str, Any]] = []
    _diff_values("", old or {}, new or {}, out)
    return out


# --- idempotency operation store (plan §9/§10) --------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobEditOperationStore:
    """SQLite-backed exactly-once operation log for edit-recalculate.

    ``request_id`` is the primary key: the same request_id with the same
    payload hash replays the stored result; the same request_id with a
    different payload is a conflict.  The row also marks the ``prepared``
    phase so an interrupted operation is identifiable and can be re-driven
    with the same request_id (plan §10 recovery contract).
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_edit_operations WHERE request_id = ?", (request_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def insert_prepared(
        self,
        *,
        request_id: str,
        job_id: str,
        mode: str,
        payload_hash: str,
        payload_json: str,
    ) -> bool:
        """Record the prepared phase; ``False`` when request_id already exists."""
        now = _utc_now_iso()
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO job_edit_operations "
                    "(request_id, job_id, operation, mode, status, payload_hash, payload_json, "
                    "created_at, updated_at) VALUES (?, ?, 'edit_recalculate', ?, 'prepared', "
                    "?, ?, ?, ?)",
                    (request_id, job_id, mode, payload_hash, payload_json, now, now),
                )
            except sqlite3.IntegrityError:
                return False
            conn.commit()
        return True

    def complete(self, request_id: str, result_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE job_edit_operations SET status = 'completed', result_json = ?, "
                "updated_at = ? WHERE request_id = ?",
                (result_json, _utc_now_iso(), request_id),
            )
            conn.commit()

    def fail(self, request_id: str, error: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE job_edit_operations SET status = 'failed', error = ?, "
                    "updated_at = ? WHERE request_id = ?",
                    (error[:2000], _utc_now_iso(), request_id),
                )
                conn.commit()
        except sqlite3.Error:  # pragma: no cover - failure logging is best-effort
            logger.warning("Could not record failure for edit operation %s", request_id)


def spec_semantics_hash(spec: JobSpec) -> str:
    """Hash only the computational semantics of a spec (compare helper)."""
    canonical = normalize_for_compare(
        {
            "workflow": spec.workflow,
            "input": spec.input,
            "method": spec.method,
            "resources": spec.resources,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
