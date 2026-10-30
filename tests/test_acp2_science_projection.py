"""Unit tests for the read-only ACP2 science projection primitives (B1-1)."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from acp.api.science_projection import (
    SAFE_ARTIFACT_ID_RE,
    AttemptCacheIdentityUnverified,
    AttemptHistoryUnavailable,
    UnsafeArtifactId,
    ViewProvenanceUnavailable,
    artifact_read,
    attempt_status,
    attempt_view,
    canonical_view_bytes,
    is_safe_artifact_id,
    map_availability,
    question_specs,
    science_capabilities,
    science_preflight,
    view_digest,
)
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.storage.manifest import ResultManifest
from acp_services.data.artifacts import (
    ArtifactNotPreparedError,
    ArtifactRef,
    ArtifactStore,
)
from acp_services.data.contracts import Availability, ResourceKind
from acp_services.data.operations import (
    PrepareCompletion,
    PrepareOperationService,
    PrepareRequest,
    PrepareState,
)
from acp_services.data.read_root import ReadRoot
from acp_services.entrypoints.status_projection import verified_release_facts
from cccp.calculation.capabilities import builtin_catalog, domain_catalog

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXPERIENCE_CONTRACT = _REPO_ROOT / "frontend/js/workbench/contracts/experience_contract.js"


# ---------------------------------------------------------------------------
# Frozen golden vector (digest computed once from the frozen rules above)
# ---------------------------------------------------------------------------

GOLDEN_VIEW = {
    "snapshot_revision": 7,
    "project_id": "proj-alpha",
    "task_ref": {"job_id": "20261024_001_optimize", "attempt": 2, "task_id": None},
    "manifest_generation": 3,
    "artifacts": [
        {
            "artifact_id": "step_0_optimize",
            "role": "optimized_structure",
            "relative_path": "WORK/03_OPT/optimized.xyz",
            "availability": "readable",
            "generation": 4,
            "sha256": "a" * 64,
            "size_bytes": 1234,
            "reason": None,
        },
        {
            "artifact_id": "step_1_frequency",
            "role": "frequency_output",
            "relative_path": None,
            "availability": "unprepared",
            "generation": 0,
            "sha256": None,
            "size_bytes": None,
            "reason": None,
        },
    ],
    "claims": [
        {
            "claim_id": "claim-1",
            "statement": "The optimized geometry is a stationary point.",
            "status": "inconclusive",
            "evidence_refs": ["step_0_optimize"],
            "reason": None,
        }
    ],
    "clarifications": [],
    "provenance": {
        "source": "result_manifest",
        "producer_revision": "b" * 40,
        "produced_at": "2026-10-24T12:00:00Z",
        "digest": "sha256:" + "0" * 64,
    },
    "read_only": True,
    "not_authoritative_for_science": True,
}

#: Frozen literal — never recompute this inside a test.
GOLDEN_DIGEST = "sha256:f3d52a6e8db92c85064e0633ff614cd34ea6b99e4f2bcbcd04aaf46d32697ff8"


def _acp_tools_from_frontend_contract() -> list[str]:
    text = _EXPERIENCE_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"var ACP_TOOLS = \[(.*?)\];", text, re.DOTALL)
    assert match is not None, "ACP_TOOLS array not found in experience_contract.js"
    return re.findall(r'"([^"]+)"', match.group(1))


# ---------------------------------------------------------------------------
# Safe artifact ids
# ---------------------------------------------------------------------------


def test_safe_artifact_id_regex_is_pinned() -> None:
    assert SAFE_ARTIFACT_ID_RE.pattern == r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("step_0_optimize", True),
        ("a/b", False),
        ("../x", False),
        ("a" * 128, True),
        ("a" * 129, False),
        ("", False),
        ("-leading-dash", False),
        (None, False),
        (123, False),
    ],
)
def test_is_safe_artifact_id(value: object, expected: bool) -> None:
    assert is_safe_artifact_id(value) is expected


# ---------------------------------------------------------------------------
# Availability mapping — full matrix
# ---------------------------------------------------------------------------

_UNPREPARED = "unprepared"
_PREPARING = "preparing"
_READABLE = "readable"
_FAILED = "failed"

_AVAILABILITIES: tuple[Availability | None, ...] = (
    None,
    Availability.ABSENT,
    Availability.PREPARING,
    Availability.READY,
    Availability.UNAVAILABLE,
)

#: Explicit expected value for every (state, availability) pair.
_AVAILABILITY_MATRIX: dict[tuple[PrepareState | None, Availability | None], str] = {
    (None, None): _UNPREPARED,
    (None, Availability.ABSENT): _UNPREPARED,
    (None, Availability.PREPARING): _UNPREPARED,
    (None, Availability.READY): _UNPREPARED,
    (None, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.QUEUED, None): _PREPARING,
    (PrepareState.QUEUED, Availability.ABSENT): _PREPARING,
    (PrepareState.QUEUED, Availability.PREPARING): _PREPARING,
    (PrepareState.QUEUED, Availability.READY): _PREPARING,
    (PrepareState.QUEUED, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.RUNNING, None): _PREPARING,
    (PrepareState.RUNNING, Availability.ABSENT): _PREPARING,
    (PrepareState.RUNNING, Availability.PREPARING): _PREPARING,
    (PrepareState.RUNNING, Availability.READY): _PREPARING,
    (PrepareState.RUNNING, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.SUCCEEDED, None): _UNPREPARED,
    (PrepareState.SUCCEEDED, Availability.ABSENT): _UNPREPARED,
    (PrepareState.SUCCEEDED, Availability.PREPARING): _UNPREPARED,
    (PrepareState.SUCCEEDED, Availability.READY): _READABLE,
    (PrepareState.SUCCEEDED, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.FAILED, None): _FAILED,
    (PrepareState.FAILED, Availability.ABSENT): _FAILED,
    (PrepareState.FAILED, Availability.PREPARING): _FAILED,
    (PrepareState.FAILED, Availability.READY): _FAILED,
    (PrepareState.FAILED, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.CANCELED, None): _FAILED,
    (PrepareState.CANCELED, Availability.ABSENT): _FAILED,
    (PrepareState.CANCELED, Availability.PREPARING): _FAILED,
    (PrepareState.CANCELED, Availability.READY): _FAILED,
    (PrepareState.CANCELED, Availability.UNAVAILABLE): _FAILED,
    (PrepareState.EXPIRED, None): _FAILED,
    (PrepareState.EXPIRED, Availability.ABSENT): _FAILED,
    (PrepareState.EXPIRED, Availability.PREPARING): _FAILED,
    (PrepareState.EXPIRED, Availability.READY): _FAILED,
    (PrepareState.EXPIRED, Availability.UNAVAILABLE): _FAILED,
}


def test_map_availability_full_matrix() -> None:
    assert set(PrepareState) == {
        PrepareState.QUEUED,
        PrepareState.RUNNING,
        PrepareState.SUCCEEDED,
        PrepareState.FAILED,
        PrepareState.CANCELED,
        PrepareState.EXPIRED,
    }
    assert set(Availability) == {
        Availability.ABSENT,
        Availability.PREPARING,
        Availability.READY,
        Availability.UNAVAILABLE,
    }

    states: tuple[PrepareState | None, ...] = (None, *PrepareState)
    assert len(_AVAILABILITY_MATRIX) == len(states) * len(_AVAILABILITIES)

    for state in states:
        for availability in _AVAILABILITIES:
            expected = _AVAILABILITY_MATRIX[(state, availability)]
            assert map_availability(state, availability) == expected, (state, availability)
            # String inputs must behave exactly like the enum inputs.
            assert (
                map_availability(
                    None if state is None else state.value,
                    None if availability is None else availability.value,
                )
                == expected
            )


def test_map_availability_unknown_strings_are_unprepared() -> None:
    assert map_availability("bogus-state", "bogus-availability") == "unprepared"
    assert map_availability("  READY  ", None) == "unprepared"


# ---------------------------------------------------------------------------
# Canonical digest
# ---------------------------------------------------------------------------


def test_golden_digest_is_frozen() -> None:
    assert view_digest(GOLDEN_VIEW) == GOLDEN_DIGEST


def test_golden_canonical_bytes_are_compact_and_sorted() -> None:
    payload = canonical_view_bytes(GOLDEN_VIEW).decode("utf-8")
    assert payload.startswith('{"artifacts":[')
    assert "provenance.digest" not in payload
    assert '"digest"' not in payload
    assert ", " not in payload
    assert ": " not in payload


def test_nullable_none_keys_do_not_affect_digest() -> None:
    base = {
        "task_ref": {"job_id": "job-1", "attempt": 1, "task_id": None},
        "artifacts": [
            {
                "artifact_id": "a",
                "role": "r",
                "availability": "unprepared",
                "generation": 0,
                "sha256": None,
                "size_bytes": None,
                "reason": None,
                "relative_path": None,
            }
        ],
        "claims": [
            {
                "claim_id": "c",
                "statement": "s",
                "status": "not_evaluated",
                "evidence_refs": [],
                "reason": None,
            }
        ],
        "provenance": {
            "source": "task_record",
            "producer_revision": "b" * 40,
            "produced_at": "2026-10-24T12:00:00Z",
            "digest": "sha256:" + "0" * 64,
        },
    }
    stripped = deepcopy(base)
    del stripped["task_ref"]["task_id"]
    for key in ("sha256", "size_bytes", "reason", "relative_path"):
        del stripped["artifacts"][0][key]
    del stripped["claims"][0]["reason"]
    # Reorder every remaining mapping by rebuilding it reversed.
    reordered = {key: stripped[key] for key in reversed(list(stripped))}

    assert view_digest(base) == view_digest(stripped) == view_digest(reordered)


def test_required_field_change_changes_digest() -> None:
    baseline = deepcopy(GOLDEN_VIEW)
    changed_attempt = deepcopy(GOLDEN_VIEW)
    changed_attempt["task_ref"]["attempt"] = 3
    changed_role = deepcopy(GOLDEN_VIEW)
    changed_role["artifacts"][0]["role"] = "some_other_role"

    assert view_digest(changed_attempt) != view_digest(baseline)
    assert view_digest(changed_role) != view_digest(baseline)


def test_canonical_view_bytes_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_view_bytes({"value": float("nan")})


def test_canonical_view_bytes_rejects_infinity() -> None:
    with pytest.raises(ValueError):
        canonical_view_bytes({"value": float("inf")})


def test_canonical_view_bytes_rejects_non_json_objects() -> None:
    with pytest.raises(TypeError):
        canonical_view_bytes({"value": object()})


def test_canonical_view_bytes_nfc_normalizes_strings() -> None:
    composed = canonical_view_bytes({"label": "caf\u00e9"})
    decomposed = canonical_view_bytes({"label": "cafe\u0301"})
    assert composed == decomposed


# ---------------------------------------------------------------------------
# Question specs
# ---------------------------------------------------------------------------

_EXPECTED_DEFAULT_KEYS = {
    "method",
    "charge",
    "multiplicity",
    "solvent",
    "temperature",
    "standardState",
}


@pytest.fixture(scope="module")
def descriptors() -> dict:
    return builtin_catalog().to_dict()


@pytest.fixture(scope="module")
def domain() -> dict:
    return domain_catalog().to_dict()


@pytest.fixture(scope="module")
def questions(descriptors: dict) -> list[dict]:
    return question_specs(descriptors=descriptors)


def test_question_specs_exactly_three_in_order(questions: list[dict]) -> None:
    assert [q["question_id"] for q in questions] == [
        "single_point_energy",
        "geometry_optimization",
        "frequency_analysis",
    ]
    assert [q["workflow"] for q in questions] == ["singlepoint", "optimize", "frequency"]
    assert all(q["label"] for q in questions)


def test_question_specs_defaults_shape(questions: list[dict]) -> None:
    for question in questions:
        defaults = question["defaults"]
        assert set(defaults) == _EXPECTED_DEFAULT_KEYS
        for key, entry in defaults.items():
            assert set(entry) == {"value", "source"}, (question["question_id"], key)
            assert isinstance(entry["source"], str) and entry["source"]


def test_question_specs_frequency_has_no_published_method_default(questions: list[dict]) -> None:
    frequency = next(q for q in questions if q["workflow"] == "frequency")
    assert frequency["method_schema_id"] == "dft_frequency"
    assert frequency["level_id"] == "frequency"
    assert frequency["basis"] is None
    assert frequency["defaults"]["method"] == {
        "value": None,
        "source": "no published default",
    }


def test_question_specs_published_method_defaults(questions: list[dict]) -> None:
    singlepoint = next(q for q in questions if q["workflow"] == "singlepoint")
    optimize = next(q for q in questions if q["workflow"] == "optimize")

    assert singlepoint["method_schema_id"] == "dft_singlepoint"
    assert singlepoint["level_id"] == "single_point"
    assert singlepoint["defaults"]["method"]["value"] == "wB97M-V"
    assert singlepoint["engine"] == "orca"
    assert singlepoint["basis"] == "def2-TZVPP"

    assert optimize["method_schema_id"] == "dft_optimize"
    assert optimize["level_id"] == "optimize"
    assert optimize["defaults"]["method"]["value"] == "r2SCAN-3c"
    assert optimize["engine"] == "orca"
    assert optimize["basis"] == ""

    for question in questions:
        value = question["defaults"]["method"]["value"]
        if value is not None:
            assert "/" not in value


def test_question_specs_shared_defaults_sources(questions: list[dict]) -> None:
    for question in questions:
        defaults = question["defaults"]
        assert defaults["charge"] == {"value": 0, "source": "TaskRequest.charge"}
        assert defaults["multiplicity"] == {
            "value": 1,
            "source": "TaskRequest.multiplicity",
        }
        assert defaults["solvent"] == {
            "value": "none",
            "source": "FIELD_DEFINITIONS.solvent.default",
        }
        assert defaults["temperature"] == {
            "value": 298.15,
            "source": "FIELD_DEFINITIONS.temperature.default",
        }
        assert defaults["standardState"] == {
            "value": None,
            "source": "not_applicable",
        }


def test_question_specs_limitations_are_published_text(questions: list[dict]) -> None:
    for question in questions:
        limitations = question["limitations"]
        assert limitations, question["question_id"]
        assert all(isinstance(text, str) and text.strip() for text in limitations)
        for text in limitations:
            assert "演示" not in text
            assert "fixture" not in text.lower()


def test_question_specs_missing_descriptors_yields_empty_limitations() -> None:
    questions = question_specs(descriptors={"capabilities": []})
    assert len(questions) == 3
    assert all(q["limitations"] == [] for q in questions)


# ---------------------------------------------------------------------------
# Capabilities document
# ---------------------------------------------------------------------------


def test_science_capabilities_shape(descriptors: dict, domain: dict) -> None:
    document = science_capabilities(descriptors=descriptors, domain=domain)

    assert set(document) == {"protocol_version", "tools", "capabilities"}
    assert isinstance(document["protocol_version"], str) and document["protocol_version"]
    assert "fixture" not in document

    capabilities = document["capabilities"]
    assert set(capabilities) == {"questions", "descriptors", "domain", "analysis"}
    assert capabilities["descriptors"] is descriptors
    assert capabilities["domain"] is domain
    assert capabilities["analysis"] == []
    assert len(capabilities["questions"]) == 3
    assert "fixture" not in capabilities


def test_science_capabilities_tools_match_frontend_allowlist(
    descriptors: dict, domain: dict
) -> None:
    document = science_capabilities(descriptors=descriptors, domain=domain)
    tools = document["tools"]

    assert tools == sorted(tools)
    assert len(tools) == 10
    assert set(tools) == set(_acp_tools_from_frontend_contract())


# ===========================================================================
# Part 2 — attempt status / view / artifact read (B1-1, B1-4, B1-5, B1-6 core)
# ===========================================================================

_JOB_ID = "20261024_001_optimize"
_PROJECT_ID = "proj-alpha"
_RELEASE_HEX = "a" * 64
_UPDATED_AT = "2026-10-24T12:00:00+00:00"
_EMPTY_DIGEST = "sha256:" + "0" * 64

_EVIDENCE_REPORT: dict[str, Any] = {
    "schema": "acp2.science.evidence.v1",
    "task_ref": {"job_id": _JOB_ID, "attempt": 1},
    "claims": [
        {
            "claim_id": "claim-1",
            "statement": "The optimized geometry is a stationary point.",
            "status": "supported",
            "scope": {"subject": "system", "applicability": "system_specific"},
            "source_refs": [
                {"artifact_path": "RESULT/energy.json", "role": "native_output"},
                {"artifact_path": "RESULT/energy.json", "role": "duplicate"},
                {"artifact_path": "RESULT/opt.xyz", "role": "geometry"},
            ],
            "missing_checks": [],
            "validator": "independent",
        }
    ],
    "limitations": [],
}


def _record(tmp_path: Path, **overrides: Any) -> JobRecord:
    params: dict[str, Any] = {
        "id": _JOB_ID,
        "spec": JobSpec(workflow="optimize", name=_JOB_ID, project_id=_PROJECT_ID),
        "status": JobStatus.COMPLETED,
        "work_dir": str(tmp_path / "stale-work"),
        "project_id": _PROJECT_ID,
        "revision": 1,
        "attempt": 1,
        "updated_at": _UPDATED_AT,
    }
    params.update(overrides)
    return JobRecord(**params)


def _read_root(
    root: Path,
    *,
    attempt: int = 1,
    is_remote: bool = False,
    historical: bool = False,
    found: bool = True,
    reason: str = "",
) -> ReadRoot:
    return ReadRoot(
        job_id=_JOB_ID,
        path=str(root),
        is_remote=is_remote,
        found=found,
        attempt=attempt,
        historical=historical,
        reason=reason,
    )


def _write_manifest(
    root: Path,
    *,
    product_id: str = "step_0_optimize",
    sidecar_attempt: int | None = None,
) -> Path:
    manifest = ResultManifest(task_id=_JOB_ID, workflow="optimize", status="completed")
    manifest.add_product(
        id=product_id,
        label="Optimized structure",
        path="opt.xyz",
        kind="structure",
    )
    path = manifest.write(root / "RESULT")
    if sidecar_attempt is not None:
        path.with_name(path.name + ".attempt").write_text(
            json.dumps({"attempt": sidecar_attempt}), encoding="utf-8"
        )
    return path


def _write_envelope(root: Path, attempt: int, digest: str = _RELEASE_HEX) -> None:
    path = root / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"attempt": attempt, "release_id": "rel-short", "release_digest": digest}),
        encoding="utf-8",
    )


def _write_receipt(
    root: Path,
    attempt: int,
    *,
    state: str = "completed",
    digest: str | None = None,
    revision: int = 3,
    written_at: str = _UPDATED_AT,
) -> None:
    payload: dict[str, Any] = {
        "attempt": attempt,
        "state": state,
        "revision": revision,
        "written_at": written_at,
    }
    if digest is not None:
        payload["release_digest"] = digest
    (root / "execution_receipt.json").write_text(json.dumps(payload), encoding="utf-8")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


@pytest.fixture()
def prepare_env(tmp_path: Path) -> Any:
    service = PrepareOperationService(
        tmp_path / "ops.db",
        retry_backoff_seconds=0.0,
        default_lease_seconds=60.0,
    )
    store = ArtifactStore(tmp_path / "artifact-store")
    try:
        yield service, store
    finally:
        service.close()


def _enqueue(
    service: PrepareOperationService,
    *,
    artifact_id: str = "step_0_optimize",
    attempt: int = 1,
    generation: int = 1,
    payload: dict[str, Any] | None = None,
    resource_kind: ResourceKind = ResourceKind.ARTIFACT,
    resource_id: str | None = None,
    job_id: str = _JOB_ID,
) -> Any:
    return service.enqueue(
        PrepareRequest(
            job_id=job_id,
            attempt=attempt,
            resource_id=resource_id or artifact_id,
            resource_kind=resource_kind,
            resource_digest="content-1",
            permission_scope="local-single",
            resource_generation=generation,
            payload=payload,
        )
    )


def _lease_for(service: PrepareOperationService, operation_id: str) -> Any:
    lease = service.lease_next()
    assert lease is not None
    assert lease.operation_id == operation_id
    return lease


def _publish_and_complete(
    service: PrepareOperationService,
    store: ArtifactStore,
    operation: Any,
    *,
    body: bytes | None = None,
    publish: bool = True,
    commit_digest: bool = True,
) -> bytes:
    body = _canonical_bytes(deepcopy(_EVIDENCE_REPORT)) if body is None else body
    lease = _lease_for(service, operation.operation_id)
    if publish:
        published = store.publish(
            ArtifactRef(
                job_id=operation.job_id,
                attempt=operation.attempt,
                resource_id=operation.resource_id,
                generation=operation.resource_generation,
                availability=Availability.PREPARING,
                relative_path="evidence.json",
            ),
            body,
        )
        sha256 = published.digest.removeprefix("sha256:") if commit_digest else None
        size_bytes = published.size_bytes if commit_digest else None
    else:
        sha256 = "b" * 64 if commit_digest else None
        size_bytes = len(body) if commit_digest else None
    service.complete(
        lease,
        PrepareCompletion(
            availability=Availability.READY,
            result={"purpose": "artifact"},
            size_bytes=size_bytes,
            sha256=sha256,
        ),
    )
    return body


def _claims_view(
    record: JobRecord,
    service: PrepareOperationService,
    store: ArtifactStore,
    root: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return attempt_view(
        record,
        record.attempt,
        _read_root(root, attempt=record.attempt),
        service,
        store,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# attempt_status
# ---------------------------------------------------------------------------


def test_attempt_status_current_filters_internal_facts(tmp_path: Path) -> None:
    record = _record(tmp_path, attempt=2, revision=7)
    status = attempt_status(record, 2, _read_root(tmp_path, attempt=2))

    assert set(status) == {"schema_version", "task_ref", "status", "revision"}
    assert status["schema_version"] == 1
    assert status["task_ref"] == {
        "job_id": _JOB_ID,
        "attempt": 2,
        "task_id": _JOB_ID,
    }
    assert "release" not in status["task_ref"]
    assert status["status"] == "completed"
    assert status["revision"] == 7
    assert "work_dir" not in status
    assert "pid" not in status
    assert "host" not in status
    assert "remote_job_id" not in status


def test_attempt_status_historical_reads_own_receipt(tmp_path: Path) -> None:
    archive = tmp_path / "attempt-1"
    archive.mkdir()
    _write_receipt(archive, 1, state="completed", revision=4)
    record = _record(tmp_path, attempt=2)

    status = attempt_status(record, 1, _read_root(archive, attempt=1, historical=True))

    assert set(status) == {"schema_version", "task_ref", "status", "revision"}
    assert status["task_ref"] == {"job_id": _JOB_ID, "attempt": 1, "task_id": None}
    assert status["status"] == "completed"
    assert status["revision"] == 4


def test_attempt_status_receipt_attempt_mismatch_is_history_error(tmp_path: Path) -> None:
    archive = tmp_path / "attempt-2"
    archive.mkdir()
    _write_receipt(archive, 1)
    record = _record(tmp_path, attempt=1)

    with pytest.raises(AttemptHistoryUnavailable) as excinfo:
        attempt_status(record, 2, _read_root(archive, attempt=2, historical=True))
    assert excinfo.value.code == "attempt_history_unavailable"


def test_attempt_status_missing_historical_root_is_history_error(tmp_path: Path) -> None:
    record = _record(tmp_path, attempt=2)
    with pytest.raises(AttemptHistoryUnavailable):
        attempt_status(
            record,
            1,
            _read_root(
                tmp_path,
                attempt=1,
                historical=True,
                found=False,
                reason="historical_attempt_missing",
            ),
        )


def test_attempt_status_missing_current_root_is_not_found(tmp_path: Path) -> None:
    record = _record(tmp_path)
    with pytest.raises(ValueError, match="attempt_not_found"):
        attempt_status(
            record,
            1,
            _read_root(tmp_path, found=False, reason="attempt_not_current"),
        )


def test_attempt_status_invalid_attempt(tmp_path: Path) -> None:
    record = _record(tmp_path)
    with pytest.raises(ValueError, match="invalid_attempt"):
        attempt_status(record, 0, _read_root(tmp_path))


# ---------------------------------------------------------------------------
# attempt_view — local provenance matrix
# ---------------------------------------------------------------------------


def test_view_local_release_beats_manifest(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    root = tmp_path / "job-root"
    _write_manifest(root)
    _write_envelope(root, 1, _RELEASE_HEX)

    view = attempt_view(_record(tmp_path), 1, _read_root(root), service, store)

    assert view["provenance"]["source"] == "execution_release"
    assert view["provenance"]["producer_revision"] == "sha256:" + _RELEASE_HEX
    assert view["manifest_generation"] == 0
    assert view["manifest_generation"] != 2
    assert "release" not in view["task_ref"]
    assert view["snapshot_revision"] == 1
    assert view["read_only"] is True
    assert view["not_authoritative_for_science"] is True
    assert [a["artifact_id"] for a in view["artifacts"]] == ["step_0_optimize"]


def test_view_local_legacy_manifest_without_release_is_200(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    root = tmp_path / "job-root"
    _write_manifest(root)

    view = attempt_view(_record(tmp_path), 1, _read_root(root), service, store)

    assert view["provenance"]["source"] == "result_manifest"
    manifest = ResultManifest.read(root / "RESULT")
    imported = json.dumps(
        manifest.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert view["provenance"]["producer_revision"] == (
        "sha256:" + hashlib.sha256(imported).hexdigest()
    )
    assert [a["artifact_id"] for a in view["artifacts"]] == ["step_0_optimize"]
    assert re.fullmatch(r"^sha256:[0-9a-f]{64}$", view["provenance"]["digest"])
    assert datetime.fromisoformat(view["provenance"]["produced_at"]).tzinfo is not None


def test_view_local_release_only_has_no_artifacts(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    root = tmp_path / "job-root"
    _write_envelope(root, 1, _RELEASE_HEX)

    view = attempt_view(_record(tmp_path), 1, _read_root(root), service, store)

    assert view["provenance"]["source"] == "execution_release"
    assert view["artifacts"] == []
    assert view["manifest_generation"] == 0


def test_view_local_record_only_is_task_record(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    root = tmp_path / "empty-root"
    root.mkdir()
    # A stale record.work_dir release envelope must never be consulted.
    stale_work_dir = tmp_path / "stale-work"
    _write_envelope(stale_work_dir, 1, _RELEASE_HEX)
    record = _record(tmp_path, work_dir=str(stale_work_dir))

    view = attempt_view(record, 1, _read_root(root), service, store)

    assert view["provenance"]["source"] == "task_record"
    assert view["artifacts"] == []
    assert view["claims"] == []
    assert view["clarifications"] == []
    assert re.fullmatch(r"^sha256:[0-9a-f]{64}$", view["provenance"]["digest"])


def test_view_without_any_timestamp_is_provenance_unavailable(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    root = tmp_path / "empty-root"
    root.mkdir()
    record = _record(tmp_path, updated_at="")

    with pytest.raises(ViewProvenanceUnavailable) as excinfo:
        attempt_view(record, 1, _read_root(root), service, store)
    assert excinfo.value.code == "view_provenance_unavailable"


def test_view_digest_is_stable_and_ignores_optional_nulls(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    root = tmp_path / "job-root"
    _write_manifest(root)
    record = _record(tmp_path)

    first = attempt_view(record, 1, _read_root(root), service, store)
    second = attempt_view(record, 1, _read_root(root), service, store)
    assert first["provenance"]["digest"] == second["provenance"]["digest"]

    stripped = deepcopy(first)
    if stripped["task_ref"].get("task_id") is None:
        stripped["task_ref"].pop("task_id", None)
    for artifact in stripped["artifacts"]:
        for key in ("sha256", "size_bytes", "reason", "relative_path"):
            if artifact.get(key) is None:
                artifact.pop(key, None)
    for claim in stripped["claims"]:
        if claim.get("reason") is None:
            claim.pop("reason", None)

    assert view_digest(first) == view_digest(stripped)
    for artifact in first["artifacts"]:
        assert artifact["sha256"] is None or re.fullmatch(r"^[0-9a-f]{64}$", artifact["sha256"])


def test_view_local_historical_uses_receipt_written_at(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    archive = tmp_path / "attempt-1"
    archive.mkdir()
    _write_receipt(
        archive,
        1,
        digest=_RELEASE_HEX,
        written_at="2026-10-20T08:30:00+00:00",
    )

    view = attempt_view(
        _record(tmp_path, attempt=2),
        1,
        _read_root(archive, attempt=1, historical=True),
        service,
        store,
    )

    assert view["provenance"]["source"] == "execution_release"
    assert view["provenance"]["producer_revision"] == "sha256:" + _RELEASE_HEX
    assert view["provenance"]["produced_at"] == "2026-10-20T08:30:00+00:00"
    assert view["artifacts"] == []


# ---------------------------------------------------------------------------
# attempt_view — remote mixed-cache matrix
# ---------------------------------------------------------------------------


def test_remote_view_drops_stale_manifest_but_keeps_current_release(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    cache = tmp_path / "cache"
    stale = tmp_path / "stale-work"
    _write_manifest(cache, product_id="attempt_one_product", sidecar_attempt=1)
    _write_manifest(stale, product_id="stale_product")
    _write_receipt(cache, 2, digest=_RELEASE_HEX)
    record = _record(tmp_path, attempt=2, work_dir=str(stale))

    view = attempt_view(record, 2, _read_root(cache, attempt=2, is_remote=True), service, store)

    assert view["provenance"]["source"] == "execution_release"
    assert view["artifacts"] == []
    assert "attempt_one_product" not in [a["artifact_id"] for a in view["artifacts"]]
    assert "stale_product" not in [a["artifact_id"] for a in view["artifacts"]]


def test_remote_view_keeps_current_manifest_and_drops_stale_release(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    cache = tmp_path / "cache"
    stale = tmp_path / "stale-work"
    _write_manifest(cache, product_id="current_product", sidecar_attempt=2)
    _write_manifest(stale, product_id="stale_product")
    _write_receipt(cache, 1, digest=_RELEASE_HEX)
    record = _record(tmp_path, attempt=2, work_dir=str(stale))

    assert verified_release_facts(cache, 2) == {}
    view = attempt_view(record, 2, _read_root(cache, attempt=2, is_remote=True), service, store)

    assert view["provenance"]["source"] == "result_manifest"
    assert [a["artifact_id"] for a in view["artifacts"]] == ["current_product"]
    assert "stale_product" not in [a["artifact_id"] for a in view["artifacts"]]


def test_remote_view_uses_current_release_and_drops_stale_manifest(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    cache = tmp_path / "cache"
    stale = tmp_path / "stale-work"
    _write_manifest(cache, product_id="attempt_one_product", sidecar_attempt=1)
    _write_manifest(stale, product_id="stale_product")
    _write_receipt(cache, 2, digest=_RELEASE_HEX)
    record = _record(tmp_path, attempt=2, work_dir=str(stale))

    view = attempt_view(record, 2, _read_root(cache, attempt=2, is_remote=True), service, store)

    assert view["provenance"]["source"] == "execution_release"
    assert view["provenance"]["producer_revision"] == "sha256:" + _RELEASE_HEX
    assert view["artifacts"] == []


def test_remote_view_both_stale_is_cache_identity_unverified(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    cache = tmp_path / "cache"
    _write_manifest(cache, product_id="attempt_one_product", sidecar_attempt=1)
    _write_receipt(cache, 1, digest=_RELEASE_HEX)
    record = _record(tmp_path, attempt=2)

    with pytest.raises(AttemptCacheIdentityUnverified) as excinfo:
        attempt_view(record, 2, _read_root(cache, attempt=2, is_remote=True), service, store)
    assert excinfo.value.code == "attempt_cache_identity_unverified"


def test_remote_historical_unprovable_is_history_error(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    cache = tmp_path / "historical-cache"
    cache.mkdir()
    record = _record(tmp_path, attempt=2)

    with pytest.raises(AttemptHistoryUnavailable):
        attempt_view(
            record,
            1,
            _read_root(cache, attempt=1, is_remote=True, historical=True),
            service,
            store,
        )


# ---------------------------------------------------------------------------
# attempt_view — published EvidenceReport claims gate
# ---------------------------------------------------------------------------


def test_claims_positive_only_from_published_verified_report(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert len(view["claims"]) == 1
    claim = view["claims"][0]
    assert set(claim) == {"claim_id", "statement", "status", "evidence_refs", "reason"}
    assert claim["claim_id"] == "claim-1"
    assert claim["status"] == "supported"
    assert claim["evidence_refs"] == ["RESULT/energy.json", "RESULT/opt.xyz"]
    assert "scope" not in claim
    assert "source_refs" not in claim
    assert "missing_checks" not in claim
    assert "validator" not in claim

    artifact = next(
        item for item in view["artifacts"] if item["artifact_id"] == operation.resource_id
    )
    assert artifact["availability"] == "readable"
    assert artifact["generation"] == 1
    assert re.fullmatch(r"^[0-9a-f]{64}$", artifact["sha256"])


def test_claims_queued_operation_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []
    assert view["artifacts"][0]["availability"] == "preparing"


def test_claims_failed_operation_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    lease = _lease_for(service, operation.operation_id)
    service.fail(lease, error_code="boom", retryable=False)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []
    assert view["artifacts"][0]["availability"] == "failed"


def test_claims_canceled_operation_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    service.cancel(operation.operation_id)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []


def test_claims_expired_operation_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation)
    service.expire(operation.operation_id)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []


def test_claims_evicted_body_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation)
    for body in (tmp_path / "artifact-store").rglob("*.body"):
        body.unlink()

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []


def test_claims_null_sha256_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation, commit_digest=False)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []
    assert view["artifacts"][0]["sha256"] is None


def test_claims_payload_only_without_published_body_contributes_nothing(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation, publish=False)

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []


def test_claims_digest_mismatch_contributes_nothing(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    lease = _lease_for(service, operation.operation_id)
    published = store.publish(
        ArtifactRef(
            job_id=operation.job_id,
            attempt=operation.attempt,
            resource_id=operation.resource_id,
            generation=operation.resource_generation,
            availability=Availability.PREPARING,
        ),
        _canonical_bytes(deepcopy(_EVIDENCE_REPORT)),
    )
    service.complete(
        lease,
        PrepareCompletion(
            availability=Availability.READY,
            size_bytes=published.size_bytes,
            sha256="c" * 64,
        ),
    )

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    assert view["claims"] == []


def test_claims_denied_authorizer_clears_all_claims(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(
        service,
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_and_complete(service, store, operation)

    view = _claims_view(
        _record(tmp_path),
        service,
        store,
        tmp_path / "empty",
        claim_authorizer=lambda _operation: SimpleNamespace(allowed=False),
    )

    assert view["claims"] == []


def test_structure_input_prepare_row_never_appears_in_artifacts(
    tmp_path: Path, prepare_env: Any
) -> None:
    service, store = prepare_env
    _enqueue(service, artifact_id="step_0_optimize")
    _enqueue(
        service,
        artifact_id="structure-input:foo/bar",
        resource_id="structure-input:foo/bar",
        resource_kind=ResourceKind.INPUT,
        payload={"purpose": "structure-input"},
    )

    view = _claims_view(_record(tmp_path), service, store, tmp_path / "empty")

    artifact_ids = [artifact["artifact_id"] for artifact in view["artifacts"]]
    assert artifact_ids == ["step_0_optimize"]
    assert all(not artifact_id.startswith("structure-input") for artifact_id in artifact_ids)


# ---------------------------------------------------------------------------
# artifact_read (generation resolution + canonical delegation)
# ---------------------------------------------------------------------------


def test_artifact_read_resolves_zero_generation(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    operation = _enqueue(service)
    _publish_and_complete(service, store, operation)
    committed = service.find(
        job_id=_JOB_ID,
        resource_id=operation.resource_id,
        attempt=1,
    )
    assert committed is not None

    payload = artifact_read(
        service,
        store,
        job_id=_JOB_ID,
        attempt=1,
        artifact_id=operation.resource_id,
        generation=0,
        digest="sha256:" + str(committed.sha256),
    )

    assert payload["digest"] == "sha256:" + str(committed.sha256)
    assert payload["generation"] == 1
    assert payload["availability"] == "ready"


def test_artifact_read_rejects_unsafe_id_before_lookup(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    with pytest.raises(UnsafeArtifactId) as excinfo:
        artifact_read(
            service,
            store,
            job_id=_JOB_ID,
            attempt=1,
            artifact_id="../escape",
            generation=1,
            digest=_EMPTY_DIGEST,
        )
    assert excinfo.value.code == "path_traversal"


def test_artifact_read_unprepared_uses_canonical_error(tmp_path: Path, prepare_env: Any) -> None:
    service, store = prepare_env
    with pytest.raises(ArtifactNotPreparedError):
        artifact_read(
            service,
            store,
            job_id=_JOB_ID,
            attempt=1,
            artifact_id="never_prepared",
            generation=0,
            digest=_EMPTY_DIGEST,
        )


# ---------------------------------------------------------------------------
# science_preflight (v2 reshape; the fingerprint IS the plan digest)
# ---------------------------------------------------------------------------


def test_science_preflight_reshapes_and_shares_the_plan_digest() -> None:
    projection = {
        "schema_version": 1,
        "feasible": True,
        "reasons": [],
        "plan_digest": "sha256:" + "a" * 64,
        "plan_summary": {"workflow": "singlepoint", "steps": 1, "project_id": "proj-a"},
        "estimate": {"nproc": 1},
        "admission": None,
        "would_create_task": False,
        "would_reserve": False,
    }

    body = science_preflight(projection, resources={"nproc": 8, "mem": "4GB"})

    assert set(body) == {
        "schema_version",
        "executed",
        "metering_consumed",
        "transfers_attempted",
        "plan",
        "preflight_fingerprint",
        "estimate",
    }
    assert body["schema_version"] == 1
    assert body["executed"] is False
    assert body["metering_consumed"] is False
    assert body["transfers_attempted"] is False
    assert body["plan"]["workflow"] == "singlepoint"
    assert body["plan"]["project_id"] == "proj-a"
    assert body["plan"]["plan_digest"] == "sha256:" + "a" * 64
    assert body["preflight_fingerprint"] == body["plan"]["plan_digest"]
    assert body["estimate"] == {"nproc": 8, "mem": "4GB", "feasible": True, "reason": ""}


def test_science_preflight_infeasible_uses_first_reason_message() -> None:
    projection = {
        "feasible": False,
        "reasons": [
            {"code": "execution_mode_conflict", "message": "remote conflicts with local"},
            {"code": "second", "message": "ignored"},
        ],
        "plan_digest": None,
        "plan_summary": {"workflow": "optimize", "steps": 1, "project_id": None},
        "estimate": {"nproc": 2},
    }

    body = science_preflight(projection)

    assert body["plan"]["plan_digest"] is None
    assert body["preflight_fingerprint"] is None
    assert body["estimate"] == {
        "nproc": 2,
        "feasible": False,
        "reason": "remote conflicts with local",
    }


def test_science_preflight_does_not_invent_resources() -> None:
    projection = {"feasible": True, "reasons": [], "plan_digest": "sha256:" + "b" * 64}

    body = science_preflight(projection, resources={})

    assert body["plan"] == {"plan_digest": "sha256:" + "b" * 64}
    assert body["estimate"] == {"feasible": True, "reason": ""}
