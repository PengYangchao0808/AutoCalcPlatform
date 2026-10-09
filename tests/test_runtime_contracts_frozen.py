"""Frozen runtime contracts for the ACP runtime-contract remediation (plan todo 1 / R0).

The eight contracts are frozen BEFORE any behavior change lands:

1. TS Mode report JSON — flat ``resolved_level`` + flat ``source_level`` sibling,
   ``schema_version`` unchanged (``tsmode_report_v1``).
2. TS Mode checkpoint v2 credential schema (optimize + frequency) and the
   recompute decision table.
3. jobs/tasks authority + bounded-lag projection rules.
4. Remote read semantics — cache the manifest, fetch files on demand, declared
   tree scope, path safety.
5. Scan validation rules and the single 1-based -> 0-based conversion.
6. Execution-version identity ``(job_id, attempt, revision)`` projected read-only.
7. TS Mode science-vs-publication boundary — expected native mode index set +
   rebuild-on-publish-failure rule.
8. v2 remote-call wrapper rules — preserve ``/api/v2``, 60s budget, shared error
   conversion.

Green tests assert shapes the CURRENT code already satisfies. Future behavior is
frozen either as literal data constants/schema samples or as
``@pytest.mark.xfail(strict=True, ...)`` tests whose reason names the owning todo
removal rule. Nothing here changes production behavior.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from acp.calculations.tsmode.contracts import TsmodeReport

# ── Frozen constants (schema samples; owned by the plan, not by this test) ──

#: ``schema_version`` of ``tsmode_report.json`` must not change (plan todo 1).
FROZEN_TSMODE_REPORT_SCHEMA_VERSION = "tsmode_report_v1"

#: Target report shape (improvement-plan §2 P0-T2 sample): BOTH level blocks are
#: FLAT siblings at the report top level; no nested ``resolved_level.effective_level``.
FROZEN_TSMODE_REPORT_SAMPLE: dict = {
    "schema_version": FROZEN_TSMODE_REPORT_SCHEMA_VERSION,
    "resolved_level": {"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"},
    "source_level": {"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"},
}

#: Frozen TS Mode checkpoint v2 stage credentials + recompute decision table
#: (plan todo 4; public product paths/ids unchanged). The CURRENT checkpoint on
#: disk is ``tsmode_checkpoint_v1`` — v2 is future behavior owned by R5.
FROZEN_CHECKPOINT_V2_SCHEMA: dict = {
    "schema_version": "tsmode_checkpoint_v2",
    "optimize_credential": {
        "binds": [
            "source_content_sha256",
            "target_mode_id",
            "optimizer_mode_index",
            "effective_level",
            "optimization_parameters",
            "optimized_structure_sha256",
            "required_completion_artifacts",
        ],
        "coordinate_validation": (
            "finite N x 3 array; atom count and element order must match the source"
        ),
    },
    "frequency_credential": {
        "binds": [
            "adopted_optimized_structure_sha256",
            "effective_level",
            "frequency_artifact_relpaths_and_sha256",
            "mode_artifact_relpaths_and_sha256",
        ],
        "rules": (
            "validates the ADOPTED optimized structure, never a second copy of the "
            "coordinates; exact artifact paths + digests, no directory globs"
        ),
    },
    "recompute_decision_table": {
        "optimize_credential_invalid": "recompute optimize and invalidate downstream",
        "frequency_credential_only_invalid": "keep validated optimize; recompute frequency",
        "publish_only_failure": (
            "rebuild the canonical publication artifact and retry publication from "
            "validated science with zero QC calls"
        ),
        "old_or_missing_credentials": (
            "conservative recompute; never fabricate identity via directory glob"
        ),
    },
}

#: Frozen jobs/tasks projection rules (plan todo 5 / IS-4). jobs is authoritative;
#: tasks is a repairable projection with bounded eventual consistency.
FROZEN_JOBS_TASKS_RULES: dict = {
    "authority": "jobs",
    "projection": "tasks (repairable)",
    "normal_paths": "project immediately",
    "sync_failure": "non-fatal; bounded lag",
    "stale_snapshot": "never overwrites a newer state",
    "lag_bound": "N drifted rows converge within ceil(N/B) scans (batch size B per scan)",
    "one_truth_reads": "list/filter/archive/stats reads needing one truth join jobs",
    "protected_fields": ("custom_name", "name_revision", "tags", "archived"),
}

#: Frozen remote read semantics (plan todo 6 / IS-5 + todo 15 freshness).
FROZEN_REMOTE_READ_RULES: dict = {
    "manifest": "small indexes served from the manager cache singleton",
    "files": "on-demand cache.fetch(record, rel_path) on miss, then serve from the cache",
    "work_dir": "never written by remote reads",
    "tree_scope": (
        "manifest-registered products (or an explicit remote listing); a partial "
        "cache directory must never masquerade as the full RESULT/WORK tree"
    ),
    "errors": "remote-missing and connection-failure distinguished; bounded, traversal-guarded",
}

#: Frozen scan submission rules (plan todo 9, distance-only first phase).
FROZEN_SCAN_RULES: dict = {
    "coordinates": "non-empty; exactly two distinct integer indices per coordinate",
    "atom_indices": "0-based at submit; within the known atom count of the submitted structure",
    "range_values": "finite start/end; valid distance range",
    "points": "integer points >= 2",
    "conversion": "1-based display -> 0-based submit, applied exactly once",
    "generator": "scan_method_flags is a parameter generator, not the validator",
}

#: Frozen execution-version identity (plan todos 5 + 14; recheck §4).
#: ``name_revision`` is organization naming, NOT execution identity.
FROZEN_EXECUTION_VERSION_FIELDS: tuple[str, ...] = ("job_id", "attempt", "revision")

#: Frozen science-vs-publication boundary (plan todo 4 / recheck §3).
FROZEN_SCIENCE_PUBLICATION_RULES: dict = {
    "expected_mode_set": (
        "the EXPECTED native mode index set comes from the final frequency analysis; "
        "validate SET completeness (mode numbering + finite N x 3 displacements for "
        "every required mode); never assume 3N-6 and never change native numbering"
    ),
    "publication_failure": (
        "canonical normal_modes missing/corrupt while the bound scientific data is "
        "valid -> REBUILD and publish; retry publication from validated science with "
        "zero QC calls; never re-run optimize/frequency"
    ),
    "science_incomplete": (
        "only incomplete scientific mode data enters the frequency-invalid branch"
    ),
}

#: Frozen v2 remote-call wrapper rules (plan todo 6 / recheck §5 / root ANTI #28).
FROZEN_V2_REMOTE_WRAPPER_RULES: dict = {
    "namespace": "/api/v2",
    "remote_budget_ms": 60000,
    "local_default_budget_ms": 8000,
    "helper": (
        "apiV2(path, {timeoutMs: API_REMOTE_TIMEOUT_MS}) or an equivalent version-aware helper"
    ),
    "errors": "shared apiRequest error conversion; never a raw AbortError",
}

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "ACP_Workbench_v2.html"


# ── (1) TS Mode report JSON ────────────────────────────────────────────────


def test_contract_1_report_schema_version_and_flat_resolved_level() -> None:
    """Frozen shape the CURRENT code already satisfies: flat levels, v1 schema."""
    report = TsmodeReport(
        source={"bundle_id": "b1"},
        target={"source_mode_index": 8},
        mapping={"status": "resolved"},
        resolved_level={"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"},
    )
    payload = report.to_dict()
    assert payload["schema_version"] == FROZEN_TSMODE_REPORT_SCHEMA_VERSION
    assert payload["schema_version"] == "tsmode_report_v1"
    # FLAT: IRC reads report["resolved_level"]["method"/"basis"] (irc/source.py:278-292).
    assert payload["resolved_level"] == {"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"}
    assert isinstance(payload["resolved_level"], dict)
    assert "effective_level" not in payload["resolved_level"]
    assert "effective_level" not in payload


def test_contract_1_report_carries_flat_source_level_sibling() -> None:
    report = TsmodeReport(
        source={"bundle_id": "b1"},
        target={"source_mode_index": 8},
        mapping={"status": "resolved"},
        resolved_level=dict(FROZEN_TSMODE_REPORT_SAMPLE["resolved_level"]),
    )
    payload = report.to_dict()
    assert isinstance(payload.get("source_level"), dict), "flat source_level sibling missing"
    assert payload["source_level"] == payload["resolved_level"] or set(
        payload["source_level"]
    ) <= set(FROZEN_TSMODE_REPORT_SAMPLE["source_level"])


# ── (2) checkpoint v2 credentials + decision table ─────────────────────────


def test_contract_2_checkpoint_v2_credential_schema_and_decision_table() -> None:
    """Frozen literal data: v2 stage credentials and the recompute decision table."""
    schema = FROZEN_CHECKPOINT_V2_SCHEMA
    assert schema["schema_version"] == "tsmode_checkpoint_v2"

    optimize = schema["optimize_credential"]
    for field in (
        "source_content_sha256",
        "target_mode_id",
        "optimizer_mode_index",
        "effective_level",
        "optimized_structure_sha256",
        "required_completion_artifacts",
    ):
        assert field in optimize["binds"]
    assert "finite N x 3" in optimize["coordinate_validation"]

    frequency = schema["frequency_credential"]
    assert "adopted_optimized_structure_sha256" in frequency["binds"]
    assert "no directory globs" in frequency["rules"]

    table = schema["recompute_decision_table"]
    assert set(table) == {
        "optimize_credential_invalid",
        "frequency_credential_only_invalid",
        "publish_only_failure",
        "old_or_missing_credentials",
    }
    assert "zero QC calls" in table["publish_only_failure"]
    assert "conservative recompute" in table["old_or_missing_credentials"]
    assert "invalidate downstream" in table["optimize_credential_invalid"]
    # Serialization stays JSON round-trippable (it is written into the checkpoint).
    assert json.loads(json.dumps(schema)) == schema


# ── (3) jobs/tasks authority + bounded lag ─────────────────────────────────


def test_contract_3_jobs_tasks_authority_and_bounded_lag_rules() -> None:
    """Frozen literal data: jobs authoritative, tasks repairable, bounded lag."""
    rules = FROZEN_JOBS_TASKS_RULES
    assert rules["authority"] == "jobs"
    assert rules["projection"].startswith("tasks")
    assert "never overwrites a newer state" in rules["stale_snapshot"]
    assert "ceil(N/B)" in rules["lag_bound"]
    # Organization fields are protected: projection sync must never own them.
    assert set(rules["protected_fields"]) == {"custom_name", "name_revision", "tags", "archived"}


def test_contract_3_projection_sync_preserves_organization_fields() -> None:
    """CURRENT behavior that todo 5 must keep: sync never overwrites org fields."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.migrations import migrate
    from acp.scheduler.tasks import TaskIndex

    with TemporaryDirectory(prefix="acp-r0-contract3-") as td:
        db = Path(td) / "t.db"
        migrate(db)
        idx = TaskIndex(db)
        spec = JobSpec(
            workflow="Confsearch",
            name="CCO_search",
            molecule_name="CCO",
            task_name="search",
            remark="original",
        )
        record = JobRecord(id="j1", spec=spec, status=JobStatus.RUNNING, work_dir="/tmp/x")
        idx.sync_from_job(record)
        idx._run(
            "UPDATE tasks SET remark='user-remark', tags='[\"t\"]', archived=1,"
            " custom_name='renamed' WHERE task_id='j1'"
        )
        idx.sync_job_transition(
            JobRecord(id="j1", spec=spec, status=JobStatus.COMPLETED, work_dir="/tmp/x")
        )
        row = idx.get("j1")
        assert row is not None
        assert row["remark"] == "user-remark"
        assert row["tags"] == '["t"]'
        assert row["archived"] == 1
        assert row["custom_name"] == "renamed"


# ── (4) remote read semantics ──────────────────────────────────────────────


class _ContractFetchCounter:
    """Deterministic fake remote: manifest/structure/`.out` + call log."""

    def __init__(self, manifest: bytes) -> None:
        self.manifest = manifest
        self.calls: list[str] = []

    def read_file(self, record: object, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path == "RESULT/result_manifest.json":
            return self.manifest
        if rel_path in ("RESULT/simple/optimized.xyz", "RESULT/simple/opt.out"):
            return b"remote-bytes:" + rel_path.encode()
        raise FileNotFoundError(rel_path)


def _manifest_bytes() -> bytes:
    from acp.storage.manifest import ResultManifest

    manifest = ResultManifest(task_id="contract4", workflow="optimize", status="completed")
    manifest.add_product(
        id="optimized", label="optimized structure", path="simple/optimized.xyz", kind="structure"
    )
    manifest.add_product(id="opt_out", label="orca output", path="simple/opt.out", kind="file")
    return json.dumps(manifest.to_dict()).encode()


def test_contract_4_manifest_cached_files_fetched_on_demand_with_path_safety() -> None:
    """CURRENT cache semantics frozen by IS-5/todo 15: manifest cached, lazy files."""
    from acp.results.remote_structure_cache import RemoteStructureCache

    structure = "RESULT/simple/optimized.xyz"
    out_file = "RESULT/simple/opt.out"
    with TemporaryDirectory(prefix="acp-r0-contract4-") as td:
        fetcher = _ContractFetchCounter(_manifest_bytes())
        cache = RemoteStructureCache(Path(td), fetcher_factory=lambda _job_id: fetcher)
        record = SimpleNamespace(
            id="contract4", attempt=1, result={"node": "n", "remote_dir": "/r"}
        )

        root = cache.fetch_catalog(record, "optimize")
        assert root is not None
        # Frozen: fetch_catalog caches the manifest/small indexes only.
        assert cache.get_cached("contract4", "RESULT/result_manifest.json") is not None
        assert cache.get_cached("contract4", structure) is None
        assert cache.get_cached("contract4", out_file) is None

        # Frozen: on-demand fetch primitive serves the missing file...
        fetched = cache.fetch(record, structure)
        assert fetched is not None
        assert fetched.read_bytes() == b"remote-bytes:" + structure.encode()
        # ...and a repeat read is a cache hit with zero extra remote reads.
        reads_before = fetcher.calls.count(structure)
        again = cache.fetch(record, structure)
        assert again == fetched
        assert fetcher.calls.count(structure) == reads_before

        # Frozen: path safety before/after any SFTP hop (traversal rejected).
        with pytest.raises(ValueError, match="escapes the cache directory"):
            cache.cache_path("contract4", "../escape")
        with pytest.raises(ValueError, match="escapes the cache directory"):
            cache.cache_path("contract4", "/etc/passwd")

        # Frozen: remote-missing degrades to None (bounded error, no crash).
        assert cache.fetch(record, "RESULT/missing.xyz") is None
        # Frozen: no fetcher configured -> bounded None, never a hard failure.
        no_fetcher = RemoteStructureCache(Path(td) / "nofetch")
        assert no_fetcher.fetch(record, structure) is None


def test_contract_4_remote_read_rules_are_frozen_literal_data() -> None:
    rules = FROZEN_REMOTE_READ_RULES
    assert "manager cache singleton" in rules["manifest"]
    assert "cache.fetch(record, rel_path)" in rules["files"]
    assert "never written" in rules["work_dir"]
    assert "never masquerade" in rules["tree_scope"]
    assert "remote-missing and connection-failure distinguished" in rules["errors"]


# ── (5) scan validation rules + 0/1-based conversion ───────────────────────


def _frozen_display_to_submit_coordinate(display_coordinate: str) -> str:
    """1-based display atoms -> 0-based submit string, applied exactly once."""
    a1, a2, start, end = (part.strip() for part in display_coordinate.split(","))
    return f"{int(a1) - 1},{int(a2) - 1},{start},{end}"


def _frozen_validate_scan_submission(
    workflow: str,
    inp: dict,
    method: dict,
    *,
    atom_count: int | None = None,
) -> None:
    """Reference implementation of FROZEN_SCAN_RULES at the submission boundary."""
    if workflow != "scan":
        return
    raw = None
    for source in (inp or {}, method or {}):
        for key in ("scan_coordinates", "coordinate"):
            if source.get(key) is not None:
                raw = source.get(key)
                break
        if raw is not None:
            break
    if raw is None:
        raise ValueError("scan job requires at least one coordinate")
    if isinstance(raw, str):
        coordinates = [raw]
    elif isinstance(raw, (list, tuple)):
        coordinates = list(raw)
    else:
        raise ValueError("scan coordinates must be a string or a sequence")
    if not coordinates:
        raise ValueError("scan coordinates must not be empty")
    points = method.get("scan_points")
    for coordinate in coordinates:
        parts = [part.strip() for part in str(coordinate).split(",")]
        if len(parts) != 4:
            raise ValueError("distance scan coordinate must be atom1,atom2,start,end")
        try:
            a1, a2 = int(parts[0]), int(parts[1])
            start, end = float(parts[2]), float(parts[3])
        except ValueError as exc:
            raise ValueError("scan coordinate indices must be integers and bounds finite") from exc
        if a1 == a2:
            raise ValueError("scan atom indices must be distinct")
        for index in (a1, a2):
            if index < 0:
                raise ValueError("scan atom indices must be non-negative")
            if atom_count is not None and index >= atom_count:
                raise ValueError("scan atom index out of range for the submitted structure")
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError("scan range bounds must be finite")
        if not (start < end):
            raise ValueError("scan range start must be below end")
        if points is None:
            raise ValueError("scan points are required")
        if not isinstance(points, int) or isinstance(points, bool) or points < 2:
            raise ValueError("scan points must be an integer >= 2")


def test_contract_5_scan_validation_rules_reference_implementation() -> None:
    """Frozen literal definition of the shared v1/v2 submission validation."""
    valid = {"coordinate": "0,1,1.0,3.0"}
    _frozen_validate_scan_submission("scan", valid, {"scan_points": 4}, atom_count=6)

    with pytest.raises(ValueError, match="at least one coordinate"):
        _frozen_validate_scan_submission("scan", {}, {"scan_points": 4})
    with pytest.raises(ValueError, match="must not be empty"):
        _frozen_validate_scan_submission("scan", {"coordinate": []}, {"scan_points": 4})
    with pytest.raises(ValueError, match="distinct"):
        _frozen_validate_scan_submission("scan", {"coordinate": "1,1,1.0,3.0"}, {"scan_points": 4})
    with pytest.raises(ValueError, match="non-negative"):
        _frozen_validate_scan_submission("scan", {"coordinate": "-1,0,1.0,3.0"}, {"scan_points": 4})
    with pytest.raises(ValueError, match="out of range"):
        _frozen_validate_scan_submission(
            "scan", {"coordinate": "98,99,1.0,3.0"}, {"scan_points": 4}, atom_count=5
        )
    with pytest.raises(ValueError, match="finite"):
        _frozen_validate_scan_submission("scan", {"coordinate": "0,1,nan,3.0"}, {"scan_points": 4})
    with pytest.raises(ValueError, match="start must be below end"):
        _frozen_validate_scan_submission("scan", {"coordinate": "0,1,3.0,1.0"}, {"scan_points": 4})
    with pytest.raises(ValueError, match="integer >= 2"):
        _frozen_validate_scan_submission("scan", valid, {"scan_points": 1})
    # Non-scan workflows are untouched at this boundary.
    _frozen_validate_scan_submission("optimize", {}, {})


def test_contract_5_scan_display_to_submit_conversion_applied_once() -> None:
    """1-based display -> 0-based submit, exactly once (frozen sample mapping)."""
    assert _frozen_display_to_submit_coordinate("1,2,1.0,3.0") == "0,1,1.0,3.0"
    assert _frozen_display_to_submit_coordinate("3,4,0.5,2.5") == "2,3,0.5,2.5"
    # The rule is "convert exactly once": feeding an already-0-based string again
    # would shift indices again, so callers must not double-apply.
    once = _frozen_display_to_submit_coordinate("1,2,1.0,3.0")
    assert once != _frozen_display_to_submit_coordinate(once)
    assert set(FROZEN_SCAN_RULES) == {
        "coordinates",
        "atom_indices",
        "range_values",
        "points",
        "conversion",
        "generator",
    }
    assert FROZEN_SCAN_RULES["generator"].endswith("not the validator")


def test_contract_5_current_generator_raises_only_for_missing_coordinates() -> None:
    """CURRENT seam: scan_method_flags is a generator (missing -> ValueError)."""
    from acp.scheduler.jobs import scan_method_flags

    with pytest.raises(ValueError, match="at least one coordinate"):
        scan_method_flags({"scan_points": 1}, {})
    flags = scan_method_flags({"scan_points": 4}, {"coordinate": "0,1,1.0,3.0"})
    assert flags == ["--coordinate", "0,1,1.0,3.0", "--scan-points", "4"]


# ── (6) execution-version identity ─────────────────────────────────────────


def test_contract_6_job_record_carries_execution_version_identity() -> None:
    """jobs is the source of the read-only execution-version identity.

    The identity is ``(job_id, attempt, revision)``; on a JobRecord payload the
    job id serializes as ``id`` (the tasks projection exposes it as ``job_id``).
    """
    from acp.scheduler.jobs import JobRecord, JobSpec

    record = JobRecord(id="j1", spec=JobSpec(workflow="fake", name="n"))
    assert record.attempt == 1
    assert record.revision == 0
    payload = record.to_dict()
    assert payload["id"] == "j1"  # job_id leg of the identity
    for field in ("attempt", "revision"):
        assert field in payload, f"{field} missing from jobs-authoritative projection"
    assert FROZEN_EXECUTION_VERSION_FIELDS == ("job_id", "attempt", "revision")
    # name_revision is organization naming, not execution identity.
    assert "name_revision" not in FROZEN_EXECUTION_VERSION_FIELDS


def test_contract_6_api_models_expose_execution_version() -> None:
    from acp.api.v1_schemas import V1JobRecordModel
    from acp.api.v2_schemas import V2TaskRowModel

    for model in (V1JobRecordModel, V2TaskRowModel):
        fields = set(model.model_fields)
        assert {"attempt", "revision"} <= fields, (
            f"{model.__name__} lacks attempt/revision execution identity"
        )


# ── (7) science-vs-publication boundary ────────────────────────────────────


def test_contract_7_publication_failure_does_not_fail_science() -> None:
    """CURRENT+target semantics: publication failure never fails completed science."""
    from acp.calculations.primitives.frequency import _publish_normal_modes
    from cccp.calculation.results import FrequencyAnalysis, FrequencyPayload

    vectors = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    analysis = FrequencyAnalysis(
        mode_frequencies={6: -100.0, 7: 200.0}, mode_vectors={6: vectors, 7: vectors}
    )
    task_result = SimpleNamespace(
        status="completed",
        symbols=("H", "H", "H"),
        payload=FrequencyPayload(analysis=analysis),
    )
    with TemporaryDirectory(prefix="acp-r0-contract7-") as td:
        with patch("pathlib.Path.write_text", side_effect=OSError("simulated publish failure")):
            published = _publish_normal_modes(None, task_result, Path(td) / "out", "orca", [])
    assert published == []
    assert task_result.status == "completed"
    assert "zero QC calls" in FROZEN_SCIENCE_PUBLICATION_RULES["publication_failure"]


def test_contract_7_partial_mode_product_is_flagged_incomplete() -> None:
    from acp.results.frequencies import build_normal_modes_product
    from cccp.calculation.results import FrequencyAnalysis

    vectors = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    partial = FrequencyAnalysis(mode_frequencies={6: -100.0, 7: 200.0}, mode_vectors={6: vectors})
    product = build_normal_modes_product(partial, geometry_product_id=None, atom_count=3)
    emitted = sorted(mode["mode_index"] for mode in product["modes"])
    assert product["warnings"], f"missing required mode not flagged: emitted={emitted} warnings=[]"


def test_contract_7_science_publication_rules_are_frozen_literal_data() -> None:
    rules = FROZEN_SCIENCE_PUBLICATION_RULES
    assert "never assume 3N-6" in rules["expected_mode_set"]
    assert "REBUILD and publish" in rules["publication_failure"]
    assert "never re-run optimize/frequency" in rules["publication_failure"]
    assert "frequency-invalid branch" in rules["science_incomplete"]


# ── (8) v2 remote-call wrapper rules ───────────────────────────────────────


def test_contract_8_frontend_wrapper_rules_present_in_source() -> None:
    """Frozen source pins: /api/v2 namespace, 60s budget, shared error conversion."""
    html = _FRONTEND.read_text(encoding="utf-8")
    rules = FROZEN_V2_REMOTE_WRAPPER_RULES
    assert rules["namespace"] == "/api/v2"
    assert rules["remote_budget_ms"] == 60000
    assert rules["local_default_budget_ms"] == 8000

    # 60s remote budget constant exists (ANTI #28).
    assert "var API_REMOTE_TIMEOUT_MS = 60000;" in html
    # v2 helper preserves the /api/v2 namespace instead of routing through apiRemote.
    assert "async function apiV2(path, opts)" in html
    assert 'apiRequest("/api/v2" + path, opts)' in html
    # Shared error conversion: timeouts become a localized Error, never a raw AbortError.
    assert "Own timeout: never leak" in html
    assert "api.timeout" in html
    # Local-only default budget stays 8s.
    assert "var API_TIMEOUT_MS = 8000;" in html


def test_contract_8_v2_remote_call_sites_pass_remote_budget() -> None:
    html = _FRONTEND.read_text(encoding="utf-8")
    assert "timeoutMs: API_REMOTE_TIMEOUT_MS" in html, (
        "no apiV2 call site passes the 60s remote budget yet"
    )
