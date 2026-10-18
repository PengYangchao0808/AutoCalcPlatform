"""B02-1 (todo 11) — six-domain round-trips for the typed DataResourcePort.

Exercises the real implementations through the legacy-side adapter
(``acp.services_data_bootstrap.build_data_resource_port``):

1. inputs      — register + validate an indexed input source
2. artifacts   — publish/read the v2 manifest (+ legacy-version read compat)
3. sources     — upsert + list + stable ``source_uid_for``
4. candidates  — authoritative ``RESULT/frame_candidates.json`` write/read/list
5. transfers   — cache→remote upload / remote→cache download
6. cache       — local cache lookup + availability/freshness shape

Negative: a write that bypasses the authoritative candidate file formatting
fails the invariant assertion, and the port never reads a decoy file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.services_data_bootstrap import build_data_resource_port
from acp_services.data import contracts as dc

_FRAME_CANDIDATES = "RESULT/frame_candidates.json"


def _port(run_root: Path) -> dc.DataResourcePort:
    return build_data_resource_port(run_root)


def _assert_frame_candidates_authoritative(task_root: Path) -> dict:
    """Guard: the authority file is v2 and byte-identical to ``write_authority``."""
    path = task_root / _FRAME_CANDIDATES
    assert path.is_file(), f"authoritative candidate file missing: {path}"
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload.get("schema_version") == "frame_candidates_v2"
    assert "revision" in payload
    # write_authority serialises with indent=2 + sort_keys=True; a bypass (e.g.
    # json.dump / custom indentation) produces different bytes and fails here.
    assert text == json.dumps(payload, indent=2, sort_keys=True), (
        "frame_candidates.json was not written by the authoritative writer"
    )
    return payload


# ---------------------------------------------------------------------------
# port shape
# ---------------------------------------------------------------------------


def test_adapter_satisfies_data_resource_port(tmp_path: Path) -> None:
    port = _port(tmp_path)
    assert isinstance(port, dc.DataResourcePort)
    # explicit methods only — no generic dispatcher surface.
    assert not hasattr(port, "call")
    assert not hasattr(port, "invoke")


# ---------------------------------------------------------------------------
# 1. inputs
# ---------------------------------------------------------------------------


def test_inputs_register_and_validate_round_trip(tmp_path: Path) -> None:
    port = _port(tmp_path)
    record = port.register_input(
        dc.InputRegistration(
            task_root=str(tmp_path),
            job_id="job_1",
            input_id="input_main",
            relative_path="input.xyz",
            label="Input structure",
            role="input",
        )
    )
    assert record.registered is True
    assert record.source_uid.startswith("ss_")
    assert record.source_uid == port.source_uid("job_1", "input.xyz")

    valid = port.validate_input(
        dc.InputValidationRequest(
            task_root=str(tmp_path), job_id="job_1", relative_path="input.xyz"
        )
    )
    assert valid.valid is True
    assert valid.exists is True
    assert valid.source_uid == record.source_uid
    assert valid.availability == "available"

    missing = port.validate_input(
        dc.InputValidationRequest(task_root=str(tmp_path), job_id="job_1", relative_path="nope.xyz")
    )
    assert missing.valid is False
    assert missing.exists is False
    assert missing.reason == "not_registered"


# ---------------------------------------------------------------------------
# 2. artifacts / manifest
# ---------------------------------------------------------------------------


def test_manifest_publish_read_round_trip(tmp_path: Path) -> None:
    port = _port(tmp_path)
    payload = {
        "version": 2,
        "task_id": "task_1",
        "workflow": "optimize",
        "status": "completed",
        "products": [
            {
                "id": "final_structure",
                "label": "Final structure",
                "path": "structures/final.xyz",
                "kind": "structure",
            }
        ],
    }
    publication = port.publish_manifest(
        dc.ManifestPublish(task_root=str(tmp_path), manifest=payload)
    )
    assert publication.version == 2
    assert publication.product_count == 1
    assert Path(publication.path).is_file()

    snapshot = port.read_manifest(dc.ManifestRead(task_root=str(tmp_path)))
    assert snapshot.found is True
    assert snapshot.version == 2
    assert snapshot.manifest == payload


def test_manifest_read_legacy_v2_compat(tmp_path: Path) -> None:
    port = _port(tmp_path)
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(parents=True)
    legacy = {
        "version": 2,
        "task_id": "legacy_task",
        "workflow": "optfreqsp",
        "status": "completed",
        "products": [
            {
                "id": "energy_report",
                "label": "Energy report",
                "path": "reports/energy.json",
                "kind": "energy_report",
            }
        ],
    }
    (result_dir / "result_manifest.json").write_text(json.dumps(legacy), encoding="utf-8")

    snapshot = port.read_manifest(dc.ManifestRead(task_root=str(tmp_path)))
    assert snapshot.found is True
    assert snapshot.manifest == legacy

    # Missing manifest is reported, never raised.
    empty = port.read_manifest(dc.ManifestRead(task_root=str(tmp_path / "absent")))
    assert empty.found is False
    assert empty.version == 0


# ---------------------------------------------------------------------------
# 3. sources
# ---------------------------------------------------------------------------


def test_sources_upsert_list_and_uid_stability(tmp_path: Path) -> None:
    from acp.scheduler.structure_source_store import source_uid_for

    port = _port(tmp_path)
    uid_a = port.source_uid("job_a", "RESULT/structures/a.xyz")
    assert uid_a.startswith("ss_")
    assert uid_a == source_uid_for("job_a", "RESULT/structures/a.xyz")
    assert uid_a == port.source_uid("job_a", "RESULT/structures/a.xyz")
    assert uid_a != port.source_uid("job_b", "RESULT/structures/a.xyz")

    affected = port.upsert_sources(
        dc.SourceUpsert(
            entries=[
                {
                    "job_id": "job_a",
                    "path": "RESULT/structures/a.xyz",
                    "label": "Candidate A",
                    "source_kind": "candidate",
                }
            ]
        )
    )
    assert affected == 1

    rows = port.list_sources("job_a")
    assert len(rows) == 1
    assert rows[0]["source_uid"] == uid_a
    assert rows[0]["relative_path"] == "RESULT/structures/a.xyz"


# ---------------------------------------------------------------------------
# 4. candidates (authoritative file)
# ---------------------------------------------------------------------------


def test_candidates_round_trip_and_authoritative_invariant(tmp_path: Path) -> None:
    port = _port(tmp_path)

    empty = port.read_candidates(str(tmp_path))
    assert empty.found is False
    assert empty.revision == 0

    saved = port.save_candidates(
        dc.CandidateWrite(task_root=str(tmp_path), payload={"job_id": "job_1", "candidates": []})
    )
    assert saved.found is True
    assert saved.schema_version == "frame_candidates_v2"
    assert saved.revision == 1
    assert saved.candidates == ()
    _assert_frame_candidates_authoritative(tmp_path)

    candidate = {
        "candidate_id": "scan_frame_0000",
        "view_type": "scan",
        "frame_index": 0,
        "role": "TS",
        "role_index": 1,
        "display_label": "TS1",
        "created_seq": 1,
    }
    saved2 = port.save_candidates(
        dc.CandidateWrite(
            task_root=str(tmp_path),
            payload={"job_id": "job_1", "candidates": [candidate]},
            expected_revision=1,
        )
    )
    assert saved2.revision == 2
    assert len(saved2.candidates) == 1
    assert saved2.candidates[0]["candidate_id"] == "scan_frame_0000"
    assert port.list_candidates(str(tmp_path))[0]["candidate_id"] == "scan_frame_0000"
    _assert_frame_candidates_authoritative(tmp_path)

    # Revision conflict is explicit, never a silent overwrite.
    with pytest.raises(dc.DataRevisionConflictError):
        port.save_candidates(
            dc.CandidateWrite(
                task_root=str(tmp_path),
                payload={"job_id": "job_1", "candidates": []},
                expected_revision=999,
            )
        )
    # The conflicting write did not advance the stored revision.
    assert port.read_candidates(str(tmp_path)).revision == 2


def test_negative_direct_write_bypassing_authority_fails_invariant(tmp_path: Path) -> None:
    """A bypass (no write_authority formatting) must fail the guard."""
    path = tmp_path / _FRAME_CANDIDATES
    path.parent.mkdir(parents=True)
    payload = {"schema_version": "frame_candidates_v2", "revision": 1, "candidates": []}
    # Deliberately NOT json.dumps(..., indent=2, sort_keys=True): a direct write.
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_frame_candidates_authoritative(tmp_path)


def test_negative_decoy_file_is_not_read_by_port(tmp_path: Path) -> None:
    """The port reads only the authoritative file, never a decoy location."""
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(parents=True)
    (result_dir / "candidates.json").write_text(
        json.dumps({"schema_version": "frame_candidates_v2", "revision": 7, "candidates": []}),
        encoding="utf-8",
    )
    port = _port(tmp_path)
    assert port.read_candidates(str(tmp_path)).found is False
    assert port.list_candidates(str(tmp_path)) == ()


# ---------------------------------------------------------------------------
# 5. transfers + 6. cache
# ---------------------------------------------------------------------------


class _FakeFetcher:
    """Minimal in-memory remote node backing transfer/cache round-trips."""

    def __init__(self, remote_root: Path) -> None:
        self._remote_root = remote_root

    def read_file(self, record: object, rel_path: str) -> bytes:  # noqa: ARG002
        path = self._remote_root / rel_path
        if not path.is_file():
            raise FileNotFoundError(rel_path)
        return path.read_bytes()

    def write_file(self, record: object, rel_path: str, data: bytes) -> None:  # noqa: ARG002
        path = self._remote_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_transfers_and_cache_round_trip(tmp_path: Path) -> None:
    from acp.results.remote_structure_cache import RemoteStructureCache

    run_root = tmp_path / "runs"
    run_root.mkdir()
    remote = tmp_path / "remote"
    (remote / "RESULT").mkdir(parents=True)
    (remote / "RESULT" / "a.json").write_text('{"x": 1}', encoding="utf-8")

    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: _FakeFetcher(remote))
    port = build_data_resource_port(run_root, cache=cache)

    # download (transfer op)
    result = port.download_file(
        dc.TransferDownload(job=dc.RemoteJobRef("job_1", 1), relative_path="RESULT/a.json")
    )
    assert result.direction is dc.TransferDirection.DOWNLOAD
    assert result.fetched is True
    assert result.cached_path is not None
    assert Path(result.cached_path).is_file()

    # cache availability (local-only lookup)
    entry = port.cache_entry(
        dc.CacheLookup(job=dc.RemoteJobRef("job_1", 1), relative_path="RESULT/a.json")
    )
    assert entry.present is True
    assert entry.cached_path == result.cached_path

    # upload (write-back) — missing cache files are skipped, not failures
    Path(result.cached_path).write_text('{"x": 2}', encoding="utf-8")
    report = port.upload_paths(
        dc.TransferUpload(
            job=dc.RemoteJobRef("job_1", 1),
            relative_paths=["RESULT/a.json", "RESULT/missing.json"],
        )
    )
    assert report.direction is dc.TransferDirection.UPLOAD
    assert report.uploaded == ("RESULT/a.json",)
    assert report.skipped == ("RESULT/missing.json",)
    assert (remote / "RESULT" / "a.json").read_text(encoding="utf-8") == '{"x": 2}'


def test_cache_availability_shape(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    port = build_data_resource_port(run_root)

    # A relative-path miss is availability=absent, freshness=unknown.
    miss = port.resource_availability(
        dc.AvailabilityQuery(
            resource_id="res_missing",
            root=str(run_root),
            relative_path="RESULT/x.json",
            job_id="job_1",
        )
    )
    assert miss.availability is dc.Availability.ABSENT
    assert miss.freshness is dc.Freshness.UNKNOWN
    assert miss.is_readable() is False

    # A ready workflow catalog under root reports availability=ready/current.
    ready_root = tmp_path / "ready"
    (ready_root / "RESULT").mkdir(parents=True)
    (ready_root / "RESULT" / "result_manifest.json").write_text("{}", encoding="utf-8")
    ready = port.resource_availability(
        dc.AvailabilityQuery(resource_id="res_ready", root=str(ready_root), workflow="optimize")
    )
    assert ready.availability is dc.Availability.READY
    assert ready.freshness is dc.Freshness.CURRENT
    assert ready.is_readable() is True


# ---------------------------------------------------------------------------
# contract vocabulary
# ---------------------------------------------------------------------------


def test_resource_revision_rejects_negative() -> None:
    with pytest.raises(ValueError):
        dc.ResourceRevision(revision=-1)
