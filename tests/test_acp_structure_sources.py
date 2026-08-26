"""Tests for acp.scheduler.structure_sources (StructureSourceService)."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.store import JobStore
from acp.scheduler.structure_sources import StructureSourceService

_XYZ_ETHANOL = """\
2
charge=1 mult=2
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
"""

_XYZ_PLAIN = """\
3
water
O 0.000000 0.000000 0.000000
H 0.950000 0.000000 0.000000
H -0.950000 0.000000 0.000000
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_summary(mol_dir: Path, products: list[dict[str, Any]], workflow: str = "energy") -> None:
    _write(
        mol_dir / "result_summary.json",
        json.dumps({"version": 1, "workflow": workflow, "products": products}),
    )


def _make_record(
    job_id: str,
    *,
    workflow: str = "energy",
    name: str = "demo",
    status: JobStatus = JobStatus.COMPLETED,
    completed_at: str | None = "2026-08-22T10:00:00+00:00",
    project_id: str | None = "uncategorized",
    work_dir: Path,
    input: dict[str, Any] | None = None,
    remote_job_id: str | None = None,
    result: dict[str, Any] | None = None,
    molecule_name: str = "",
) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=name,
            input=input or {},
            project_id=project_id,
            molecule_name=molecule_name,
        ),
        status=status,
        work_dir=str(work_dir),
        created_at="2026-08-22T09:00:00+00:00",
        updated_at="2026-08-22T09:00:00+00:00",
        completed_at=completed_at,
        project_id=project_id,
        remote_job_id=remote_job_id,
        result=result,
    )


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "acp_jobs.db")


@pytest.fixture()
def service(store: JobStore, tmp_path: Path) -> StructureSourceService:
    return StructureSourceService(store, tmp_path)


class FakeFetcher:
    """Duck-typed RemoteResultFetcher stand-in backed by an in-memory tree."""

    def __init__(self, files: dict[str, bytes], *, fail: bool = False) -> None:
        self.files = files
        self.fail = fail

    def walk_remote_files(self, record, include=None, exclude=None):
        if self.fail:
            raise RuntimeError("node unreachable")
        patterns = include or ["*"]
        for rel in sorted(self.files):
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                yield rel, None

    def read_file(self, record, filename: str) -> bytes:
        if self.fail:
            raise RuntimeError("node unreachable")
        if filename not in self.files:
            raise FileNotFoundError(filename)
        return self.files[filename]

    def file_exists(self, record, filename: str) -> bool:
        if self.fail:
            raise RuntimeError("node unreachable")
        return filename in self.files


# ---------------------------------------------------------------------------
# JobStore.list_recent_completed
# ---------------------------------------------------------------------------


def test_list_recent_completed_orders_by_completed_at_desc(store: JobStore, tmp_path: Path) -> None:
    for job_id, completed in (
        ("job_a", "2026-08-20T10:00:00+00:00"),
        ("job_b", "2026-08-22T10:00:00+00:00"),
        ("job_c", "2026-08-21T10:00:00+00:00"),
    ):
        store.create(_make_record(job_id, completed_at=completed, work_dir=tmp_path / job_id))
    records = store.list_recent_completed()
    assert [r.id for r in records] == ["job_b", "job_c", "job_a"]


def test_list_recent_completed_filters_and_limit(store: JobStore, tmp_path: Path) -> None:
    store.create(
        _make_record(
            "e1",
            workflow="energy",
            completed_at="2026-08-22T10:00:00+00:00",
            work_dir=tmp_path / "e1",
        )
    )
    store.create(
        _make_record(
            "e2",
            workflow="energy",
            project_id="alpha",
            completed_at="2026-08-22T11:00:00+00:00",
            work_dir=tmp_path / "e2",
        )
    )
    store.create(
        _make_record(
            "n1",
            workflow="nmr",
            completed_at="2026-08-22T12:00:00+00:00",
            work_dir=tmp_path / "n1",
        )
    )
    store.create(
        _make_record(
            "r1",
            workflow="energy",
            status=JobStatus.RUNNING,
            completed_at=None,
            work_dir=tmp_path / "r1",
        )
    )

    assert [r.id for r in store.list_recent_completed()] == ["n1", "e2", "e1"]
    assert [r.id for r in store.list_recent_completed(workflow="energy")] == ["e2", "e1"]
    assert [r.id for r in store.list_recent_completed(project_id="alpha")] == ["e2"]
    assert [r.id for r in store.list_recent_completed(limit=1)] == ["n1"]
    assert [
        r.id for r in store.list_recent_completed(completed_after="2026-08-22T10:30:00+00:00")
    ] == ["n1", "e2"]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _energy_job_with_products(
    store: JobStore,
    tmp_path: Path,
    products: list[dict[str, Any]],
    *,
    job_id: str = "20260822_001_energy",
    files: dict[str, str] | None = None,
    **record_kwargs: Any,
) -> JobRecord:
    work_dir = tmp_path / "uncategorized" / job_id
    mol_dir = work_dir / "ethanol"
    _write_summary(mol_dir, products)
    for rel, content in (files or {}).items():
        _write(work_dir / rel, content)
    record = _make_record(job_id, work_dir=work_dir, **record_kwargs)
    store.create(record)
    return record


def test_discover_role_marked_product(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "Global minimum structure",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL},
    )
    entries = service.list_recent()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source_id"] == "job_20260822_001_energy:ethanol/ethanol_global_min.xyz"
    assert entry["job_id"] == "20260822_001_energy"
    assert entry["workflow"] == "energy"
    assert entry["label"] == "Global minimum structure"
    assert entry["formula"] == "CO"
    assert entry["atom_count"] == 2
    assert entry["charge"] == 1
    assert entry["multiplicity"] == 2
    assert entry["has_3d"] is True
    assert entry["remote"] is False
    assert entry["needs_fetch"] is False


def test_role_wins_over_legacy_in_same_summary(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {"label": "Optimized", "path": "optimized.xyz", "kind": "xyz"},
            {
                "label": "Global min",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            },
        ],
        files={
            "ethanol/optimized.xyz": _XYZ_PLAIN,
            "ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL,
        },
    )
    entries = service.list_recent()
    assert [e["path"] for e in entries] == ["ethanol/ethanol_global_min.xyz"]


def test_legacy_filename_fallback(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [{"label": "Global min", "path": "ethanol_global_min.xyz", "kind": "xyz"}],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_PLAIN},
    )
    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["path"] == "ethanol/ethanol_global_min.xyz"


def test_legacy_conformer_fallback_returns_one_minimum(
    service: StructureSourceService, store, tmp_path
):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {"label": "opt", "path": "optimized.xyz", "kind": "xyz"},
            {"label": "confs", "path": "finalDFT/all_conformers.xyz", "kind": "xyz"},
            {"label": "report", "path": "report.json", "kind": "json"},
        ],
        files={
            "ethanol/optimized.xyz": _XYZ_PLAIN,
            "ethanol/finalDFT/all_conformers.xyz": _XYZ_ETHANOL + "\n" + _XYZ_PLAIN,
            "ethanol/report.json": "{}",
        },
    )
    entries = service.list_recent()
    paths = {e["path"] for e in entries}
    assert paths == {"ethanol/optimized.xyz"}


def test_singlepoint_and_frequency_never_yield_sources(
    service: StructureSourceService, store, tmp_path
):
    for job_id, workflow in (("sp1", "singlepoint"), ("f1", "frequency")):
        work_dir = tmp_path / "uncategorized" / job_id
        _write_summary(
            work_dir / "mol",
            [{"label": "out", "path": "optimized.xyz", "kind": "xyz"}],
            workflow=workflow,
        )
        _write(work_dir / "mol" / "optimized.xyz", _XYZ_PLAIN)
        store.create(_make_record(job_id, workflow=workflow, work_dir=work_dir))
    assert service.list_recent() == []
    assert service.list_recent(workflow="singlepoint") == []


def test_non_completed_jobs_excluded(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_PLAIN},
        status=JobStatus.FAILED,
    )
    assert service.list_recent() == []


def test_broken_pointer_and_traversal_dropped_silently(
    service: StructureSourceService, store, tmp_path
):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "missing",
                "path": "gone_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
    )
    assert service.list_recent() == []

    work_dir = tmp_path / "uncategorized" / "evil"
    _write_summary(
        work_dir / "mol",
        [
            {
                "label": "evil",
                "path": "../../../../etc/passwd",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
    )
    store.create(_make_record("evil", work_dir=work_dir))
    assert service.list_recent() == []


def test_charge_mult_precedence(service: StructureSourceService, store, tmp_path):
    # comment wins over spec.input
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL},
        job_id="j_comment",
        input={"charge": -1, "multiplicity": 3},
    )
    # spec.input used when the comment carries no charge/mult
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "water_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/water_global_min.xyz": _XYZ_PLAIN},
        job_id="j_spec",
        input={"charge": -1, "multiplicity": 3},
    )
    # defaults (0, 1) when neither source has values
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "plain_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/plain_global_min.xyz": _XYZ_PLAIN},
        job_id="j_default",
    )
    entries = {e["job_id"]: e for e in service.list_recent(limit=10)}
    assert (entries["j_comment"]["charge"], entries["j_comment"]["multiplicity"]) == (1, 2)
    assert (entries["j_spec"]["charge"], entries["j_spec"]["multiplicity"]) == (-1, 3)
    assert (entries["j_default"]["charge"], entries["j_default"]["multiplicity"]) == (0, 1)


def test_source_id_roundtrip(service: StructureSourceService) -> None:
    job_id, rel = StructureSourceService.parse_source_id(
        "job_20260822_001_energy:ethanol/ethanol_global_min.xyz"
    )
    assert job_id == "20260822_001_energy"
    assert rel == "ethanol/ethanol_global_min.xyz"
    with pytest.raises(ValueError, match="Invalid source_id"):
        StructureSourceService.parse_source_id("nonsense")
    with pytest.raises(ValueError, match="Invalid source_id"):
        StructureSourceService.parse_source_id("job_abc:")


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


def test_get_local_returns_asset_and_checksum(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL},
    )
    asset, checksum = service.get("job_20260822_001_energy:ethanol/ethanol_global_min.xyz")
    assert checksum.startswith("sha256:")
    assert asset["formula"] == "CO"
    assert asset["atom_count"] == 2
    assert asset["charge"] == 1
    assert asset["multiplicity"] == 2
    assert asset["source_type"] == "job_artifact"
    assert asset["original_format"] == "xyz"
    assert asset["name"] == "ethanol_global_min.xyz"
    assert asset["xyz"].splitlines()[0] == "2"


def test_get_errors(service: StructureSourceService, store, tmp_path):
    with pytest.raises(ValueError, match="Invalid source_id"):
        service.get("bogus")
    with pytest.raises(ValueError, match="Job not found"):
        service.get("job_missing:mol/x.xyz")

    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL},
        status=JobStatus.RUNNING,
        completed_at=None,
    )
    with pytest.raises(ValueError, match="not completed"):
        service.get("job_20260822_001_energy:ethanol/ethanol_global_min.xyz")


def test_get_missing_file(service: StructureSourceService, store, tmp_path):
    _energy_job_with_products(
        store,
        tmp_path,
        [
            {
                "label": "g",
                "path": "ethanol_global_min.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        files={"ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL},
    )
    with pytest.raises(ValueError, match="Source file not found"):
        service.get("job_20260822_001_energy:ethanol/other.xyz")


# ---------------------------------------------------------------------------
# Remote jobs
# ---------------------------------------------------------------------------


def _remote_record(
    store: JobStore,
    tmp_path: Path,
    *,
    job_id: str = "rjob1",
    result: dict[str, Any] | None = None,
) -> JobRecord:
    record = _make_record(
        job_id,
        work_dir=tmp_path / "uncategorized" / job_id,
        result=result
        or {
            "node": "node1",
            "remote_dir": "/remote/root/rjob1",
            "lsf_job_id": "9911",
        },
    )
    store.create(record)
    return record


def _remote_files() -> dict[str, bytes]:
    summary = json.dumps(
        {
            "version": 1,
            "workflow": "energy",
            "products": [
                {
                    "label": "Global minimum structure",
                    "path": "ethanol_global_min.xyz",
                    "kind": "xyz",
                    "role": "final_stable_structure",
                }
            ],
        }
    ).encode()
    return {
        "ethanol/result_summary.json": summary,
        "ethanol/ethanol_global_min.xyz": _XYZ_ETHANOL.encode(),
    }


def test_remote_listing_with_fetcher(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path, fetcher=FakeFetcher(_remote_files()))
    entries = service.list_recent()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["remote"] is True
    assert entry["needs_fetch"] is False
    assert entry["source_id"] == "job_rjob1:ethanol/ethanol_global_min.xyz"
    assert entry["formula"] == "CO"


def test_remote_listing_without_fetcher_is_placeholder(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path)
    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["remote"] is True
    assert entries[0]["needs_fetch"] is True


def test_remote_listing_probe_failure_degrades(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    failing = FakeFetcher(_remote_files(), fail=True)
    service = StructureSourceService(store, tmp_path, fetcher=failing)
    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["needs_fetch"] is True


def test_remote_listing_include_remote_false(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path, fetcher=FakeFetcher(_remote_files()))
    assert service.list_recent(include_remote=False) == []


def test_remote_get_via_fetcher(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path, fetcher=FakeFetcher(_remote_files()))
    asset, checksum = service.get("job_rjob1:ethanol/ethanol_global_min.xyz")
    assert checksum.startswith("sha256:")
    assert asset["formula"] == "CO"
    assert asset["charge"] == 1


def test_remote_get_without_fetcher_raises(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path)
    with pytest.raises(ValueError, match="fetcher"):
        service.get("job_rjob1:ethanol/ethanol_global_min.xyz")


def test_remote_get_unreachable_node_raises(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(
        store, tmp_path, fetcher=FakeFetcher(_remote_files(), fail=True)
    )
    with pytest.raises(ValueError, match="unavailable"):
        service.get("job_rjob1:ethanol/ethanol_global_min.xyz")


def test_remote_get_missing_file_raises(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path, fetcher=FakeFetcher(_remote_files()))
    with pytest.raises(ValueError, match="missing on remote"):
        service.get("job_rjob1:ethanol/absent.xyz")


def test_remote_traversal_rejected(store: JobStore, tmp_path: Path) -> None:
    _remote_record(store, tmp_path)
    service = StructureSourceService(store, tmp_path, fetcher=FakeFetcher(_remote_files()))
    with pytest.raises(ValueError, match="Invalid remote path"):
        service.get("job_rjob1:../../etc/passwd")


# ---------------------------------------------------------------------------
# Unified result_manifest.json discovery (batch plan §6)
# ---------------------------------------------------------------------------


def test_confsearch_manifest_returns_only_rank_one_geometry(service, store, tmp_path) -> None:
    work_dir = tmp_path / "uncategorized" / "confsearch_job"
    conf_dir = work_dir / "RESULT" / "confsearch"
    _write(
        conf_dir / "confsearch_manifest.json",
        json.dumps(
            {
                "schema_version": "confsearch_v1",
                "workflow": "Confsearch",
                "conformers": [
                    {
                        "conf_id": "conf_0001",
                        "geometry": "conformers/conf_0001.xyz",
                        "energy_hartree": -10.0,
                        "rank": 1,
                    },
                    {
                        "conf_id": "conf_0002",
                        "geometry": "conformers/conf_0002.xyz",
                        "energy_hartree": -9.0,
                        "rank": 2,
                    },
                ],
            }
        ),
    )
    _write(conf_dir / "conformers" / "conf_0001.xyz", _XYZ_PLAIN)
    _write(conf_dir / "conformers" / "conf_0002.xyz", _XYZ_ETHANOL)
    store.create(
        _make_record(
            "confsearch_job",
            workflow="Confsearch",
            work_dir=work_dir,
            molecule_name="INT_S",
        )
    )

    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["path"] == "RESULT/confsearch/conformers/conf_0001.xyz"
    assert entries[0]["label"] == "Lowest-energy conformer (conf_0001)"
    assert entries[0]["candidate_id"] == ""

_XYZ_TAG_TS = """\
2
TAG: TS | candidate_id=ts_guess_001 | source=PESsearch | frame=006
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
"""

_XYZ_TAG_INT = """\
2
TAG: INT | candidate_id=int_guess_002 | source=PESsearch | frame=009
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
"""


def _write_result_manifest(
    result_dir: Path,
    products: list[dict[str, Any]],
    *,
    workflow: str = "PESsearch",
) -> None:
    _write(
        result_dir / "result_manifest.json",
        json.dumps(
            {
                "version": 2,
                "workflow": workflow,
                "status": "completed",
                "products": products,
            }
        ),
    )


def test_discover_result_manifest_structure_products(service, store, tmp_path) -> None:
    work_dir = tmp_path / "uncategorized" / "20260823_001_PESsearch"
    result_dir = work_dir / "RESULT"
    _write_result_manifest(
        result_dir,
        [
            {
                "id": "s2_candidate_ts_guess_001",
                "label": "S2 candidate ts_guess_001 (TS)",
                "path": "mechanism/ts_guesses/ts_guess_001.xyz",
                "kind": "structure",
            },
            {
                "id": "s2_candidate_int_guess_002",
                "label": "S2 candidate int_guess_002 (INT)",
                "path": "mechanism/intermediate_guesses/int_guess_002.xyz",
                "kind": "structure",
            },
            {
                "id": "s2_path_manifest",
                "label": "S2 path manifest",
                "path": "mechanism/s2_path_manifest.json",
                "kind": "file",
            },
        ],
    )
    _write(work_dir / "RESULT" / "mechanism" / "ts_guesses" / "ts_guess_001.xyz", _XYZ_TAG_TS)
    _write(
        work_dir / "RESULT" / "mechanism" / "intermediate_guesses" / "int_guess_002.xyz",
        _XYZ_TAG_INT,
    )
    _write(work_dir / "RESULT" / "mechanism" / "s2_path_manifest.json", "{}")
    store.create(
        _make_record(
            "20260823_001_PESsearch",
            workflow="PESsearch",
            work_dir=work_dir,
            molecule_name="INT_P_energy_mt5g72",
        )
    )

    entries = service.list_recent()
    assert len(entries) == 2, "kind=file products must be excluded; both structures listed"
    by_label = {entry["label"]: entry for entry in entries}
    ts_entry = by_label["S2 candidate ts_guess_001 (TS)"]
    assert ts_entry["tag"] == "TS"
    assert ts_entry["candidate_id"] == "ts_guess_001"
    assert ts_entry["workflow"] == "PESsearch"
    assert ts_entry["source_id"] == (
        "job_20260823_001_PESsearch:RESULT/mechanism/ts_guesses/ts_guess_001.xyz"
    )
    int_entry = by_label["S2 candidate int_guess_002 (INT)"]
    assert int_entry["tag"] == "", "INT is the default and needs no explicit tag"
    assert ts_entry["molecule_name"] == "INT_P_energy_mt5g72__ts_guess_001"
    assert int_entry["molecule_name"] == "INT_P_energy_mt5g72__int_guess_002"


def test_energy_manifest_prefers_global_minimum_over_conformer_ensemble(
    service, store, tmp_path
) -> None:
    """An energy job contributes one reusable stationary structure, not two cards."""
    work_dir = tmp_path / "uncategorized" / "energy_duplicate"
    result_dir = work_dir / "RESULT"
    _write_result_manifest(
        result_dir,
        [
            {
                "id": "all_conformers",
                "label": "Ranked conformers (XYZ)",
                "path": "structures/all_conformers.xyz",
                "kind": "structure",
            },
            {
                "id": "global_min",
                "label": "Global minimum structure",
                "path": "structures/INT_S_energy_mt5g72_global_min.xyz",
                "kind": "structure",
            },
        ],
        workflow="energy",
    )
    _write(result_dir / "structures" / "all_conformers.xyz", _XYZ_ETHANOL)
    _write(result_dir / "structures" / "INT_S_energy_mt5g72_global_min.xyz", _XYZ_PLAIN)
    store.create(
        _make_record(
            "energy_duplicate",
            workflow="energy",
            work_dir=work_dir,
            molecule_name="INT_S_energy_mt5g72",
        )
    )

    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["path"] == "RESULT/structures/INT_S_energy_mt5g72_global_min.xyz"


def test_result_manifest_wins_over_legacy_summary(service, store, tmp_path) -> None:
    work_dir = tmp_path / "uncategorized" / "job_mixed"
    result_dir = work_dir / "RESULT"
    _write_result_manifest(
        result_dir,
        [
            {
                "id": "batch_item_001",
                "label": "opt_1 (TS, s3)",
                "path": "structures/item_001__TAG_TS__optimized.xyz",
                "kind": "structure",
            }
        ],
        workflow="Lowconfirm",
    )
    # Same physical file also referenced by a legacy summary with a role —
    # the result_manifest entry must win and dedupe.
    _write_summary(
        result_dir,
        [
            {
                "label": "legacy label",
                "path": "structures/item_001__TAG_TS__optimized.xyz",
                "kind": "xyz",
                "role": "final_stable_structure",
            }
        ],
        workflow="Lowconfirm",
    )
    _write(work_dir / "RESULT" / "structures" / "item_001__TAG_TS__optimized.xyz", _XYZ_TAG_TS)
    store.create(
        _make_record("job_mixed", workflow="Lowconfirm", work_dir=work_dir)
    )

    entries = service.list_recent()
    assert len(entries) == 1, "identical product referenced twice must dedupe"
    assert entries[0]["label"] == "opt_1 (TS, s3)"


def test_duplicate_candidate_id_is_listed_once(service, store, tmp_path) -> None:
    """Repeated manifest pointers must not create duplicate candidate rows."""
    work_dir = tmp_path / "uncategorized" / "job_duplicate_candidate"
    result_dir = work_dir / "RESULT"
    _write_result_manifest(
        result_dir,
        [
            {
                "id": "s2_candidate_int_guess_002_a",
                "label": "candidate A",
                "path": "structures/a.xyz",
                "kind": "structure",
            },
            {
                "id": "s2_candidate_int_guess_002_b",
                "label": "candidate B",
                "path": "structures/b.xyz",
                "kind": "structure",
            },
        ],
    )
    _write(result_dir / "structures" / "a.xyz", _XYZ_TAG_INT)
    _write(result_dir / "structures" / "b.xyz", _XYZ_TAG_INT)
    store.create(
        _make_record(
            "job_duplicate_candidate", workflow="PESsearch", work_dir=work_dir
        )
    )

    entries = service.list_recent()
    assert len(entries) == 1
    assert entries[0]["candidate_id"] == "int_guess_002"


def test_remote_probe_reads_result_manifest(store, tmp_path) -> None:
    _remote_record(store, tmp_path)
    files = dict(_remote_files())
    files["RESULT/result_manifest.json"] = json.dumps(
        {
            "version": 2,
            "workflow": "PESsearch",
            "status": "completed",
            "products": [
                {
                    "id": "s2_candidate_ts_001",
                    "label": "S2 candidate ts_001 (TS)",
                    "path": "mechanism/ts_guesses/ts_guess_001.xyz",
                    "kind": "structure",
                }
            ],
        }
    ).encode()
    files["RESULT/mechanism/ts_guesses/ts_guess_001.xyz"] = _XYZ_TAG_TS.encode()
    service = StructureSourceService(
        store, tmp_path, fetcher=FakeFetcher(files)
    )
    entries = service.list_recent()
    assert any(entry["remote"] and entry["tag"] == "TS" for entry in entries)
