"""Round-9 M4b: ``vibration.analyze`` consumes the published frequency product.

The external agent prepared ``RESULT/frequencies/<item>__normal_modes.json`` and
called ``acp_analyze(analysis_id="vibration.analyze")``; the handler forced the
ORCA-output parser, produced zero frequencies and the capability's preflight
refused with a not-ready error — even though the platform had already computed
and registered the modes.

These tests pin the round-9 contract:

* a ``normal_modes_v1`` prepared artifact is projected into the same calculator
  view the ORCA parser returns and reaches the **same**
  ``bind_capability(...).preflight()/execute()`` path (no science copied into
  ``acp``, no second set of checks);
* frequencies, imaginary modes, mode vectors (direction data) and IR
  intensities survive the projection; the report is byte-deterministic;
* an empty product keeps the typed not-ready refusal (409 with the failed
  check), a malformed document is an input error — never a fabricated report;
* both declared input formats are advertised and the legacy ORCA path is
  unchanged;
* ``vibration.compute`` stays registered-but-not-executable.

The artifact read is faked at the module boundary (``resolve_artifact_read``);
the real prepare/read lifecycle is covered by ``test_acp2_analysis_chain.py``.
No QC runs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from acp.calculations.batch._items import BatchCalculationItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.services_analysis_bootstrap import (
    NORMAL_MODES_FORMAT,
    ORCA_OUTPUT_FORMAT,
    AnalysisInputError,
    VibrationAnalyzeHandler,
    build_analysis_registry,
)
from acp.storage.manifest import ResultManifest
from acp_services.data.artifacts import ArtifactRead, ArtifactRef
from acp_services.data.contracts import Availability

_JOB_ID = "20261102_120000_001_frequency"
_ITEM_ID = "int_001"
#: The manifest product id the batch writer registers for the mode product.
_MODES_PRODUCT_ID = f"batch_{_ITEM_ID}_normal_modes"

_NORMAL_MODES = {
    "schema_version": "normal_modes_v1",
    "units": {
        "frequency": "cm-1",
        "displacement": "dimensionless_orca_normal_mode",
    },
    "atom_count": 3,
    "geometry_product_id": "batch_int_001",
    "modes": [
        {
            "mode_index": 0,
            "frequency_cm1": -412.8,
            "imaginary": True,
            "vectors": [
                [0.0, 0.0, 0.31],
                [0.0, 0.0, -0.22],
                [0.0, 0.0, -0.09],
            ],
        },
        {
            "mode_index": 6,
            "frequency_cm1": 1180.4,
            "imaginary": False,
            "vectors": [
                [0.11, 0.0, 0.0],
                [-0.08, 0.0, 0.0],
                [-0.03, 0.0, 0.0],
            ],
            "ir_intensity": 41.2,
        },
        {
            "mode_index": 7,
            "frequency_cm1": 3010.5,
            "imaginary": False,
            "vectors": [
                [0.0, 0.0, 0.52],
                [0.0, 0.0, 0.40],
                [0.0, 0.0, 0.12],
            ],
            "ir_intensity": 2.5,
        },
    ],
    "warnings": [],
}


def _normal_modes_product(tmp_path: Path) -> Path:
    """Publish the product through the BatchOptimize writer (the real producer)."""
    result_root = tmp_path / "task" / "RESULT"
    source = (
        tmp_path
        / "task"
        / "WORK"
        / "03_OPT"
        / "batch"
        / _ITEM_ID
        / "frequency"
        / "normal_modes.json"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(_NORMAL_MODES, indent=2), encoding="utf-8")
    engine = BatchOptimizeEngine(work_root=tmp_path / "task" / "WORK", result_root=result_root)
    manifest = ResultManifest(task_id=_JOB_ID, workflow="BatchOptimize", status="completed")
    engine._materialize_normal_modes(
        BatchCalculationItem(item_id=_ITEM_ID, candidate_id=_ITEM_ID, name="INT", tag="INT"),
        manifest,
    )
    manifest.write(result_root)
    product = result_root / "frequencies" / f"{_ITEM_ID}__normal_modes.json"
    assert product.is_file()
    return product


class _PreparedArtifacts:
    """Fake module-boundary read: the handler still passes the full identity."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.bodies: dict[str, bytes] = {}
        monkeypatch.setattr("acp.services_analysis_bootstrap.resolve_artifact_read", self._read)

    def add(self, resource_id: str, body: bytes) -> dict[str, Any]:
        self.bodies[resource_id] = body
        return {
            "task_id": _JOB_ID,
            "attempt": 1,
            "artifact_id": resource_id,
            "generation": 1,
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        }

    def _read(self, service: Any, store: Any, **kwargs: Any) -> ArtifactRead:
        self.calls.append(dict(kwargs))
        body = self.bodies[str(kwargs["resource_id"])]
        ref = ArtifactRef(
            job_id=str(kwargs["job_id"]),
            attempt=int(kwargs["attempt"]),
            resource_id=str(kwargs["resource_id"]),
            generation=int(kwargs["generation"]),
            digest="sha256:" + hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            availability=Availability.READY,
            media_type="application/json",
        )
        return ArtifactRead(ref=ref, body=body)


@pytest.fixture()
def prepared(monkeypatch: pytest.MonkeyPatch) -> _PreparedArtifacts:
    return _PreparedArtifacts(monkeypatch)


def _handler(permission_scope: str = "local-single") -> VibrationAnalyzeHandler:
    return VibrationAnalyzeHandler(
        prepare_operations=object(),  # type: ignore[arg-type]  # the read is faked
        artifact_store=object(),  # type: ignore[arg-type]
        permission_scope=permission_scope,
    )


def _request(ref: dict[str, Any], **parameters: Any) -> dict[str, Any]:
    body = {
        "schema_version": 1,
        "analysis_id": "vibration.analyze",
        "input_refs": [ref],
        "explicit": True,
    }
    if parameters:
        body["parameters"] = dict(parameters)
    return body


# ── the normal_modes_v1 path ────────────────────────────────────────────


def test_normal_modes_product_produces_the_frequency_report(
    tmp_path: Path, prepared: _PreparedArtifacts
) -> None:
    product = _normal_modes_product(tmp_path)
    ref = prepared.add(_MODES_PRODUCT_ID, product.read_bytes())

    result = _handler()(_request(ref))

    report = result["product"]["report"]
    assert report["frequencies"] == [-412.8, 1180.4, 3010.5]
    assert report["imaginary_modes"] == [-412.8]
    assert report["has_imaginary"] is True
    assert report["normal_modes_available"] is True
    modes = report["normal_modes"]["modes"]
    assert [mode["mode_index"] for mode in modes] == [0, 6, 7]
    # Mode direction data survives: one finite 3-vector per atom per mode.
    assert all(len(mode["vectors"]) == 3 for mode in modes)
    assert modes[0]["vectors"][0] == [0.0, 0.0, 0.31]
    assert modes[1]["ir_intensity"] == pytest.approx(41.2)
    assert result["product"]["format"] == NORMAL_MODES_FORMAT
    assert result["product"]["input_digests"] == [ref["digest"]]
    assert result["inputs"][0]["digest"] == ref["digest"]
    assert result["provenance"]["starts_subprocess"] is False
    assert result["provenance"]["starts_task"] is False


def test_handler_reads_the_full_artifact_identity(
    tmp_path: Path, prepared: _PreparedArtifacts
) -> None:
    product = _normal_modes_product(tmp_path)
    ref = prepared.add(_MODES_PRODUCT_ID, product.read_bytes())

    _handler("local-single")(_request(ref))

    call = prepared.calls[-1]
    assert call["job_id"] == _JOB_ID
    assert call["attempt"] == 1
    assert call["resource_id"] == _MODES_PRODUCT_ID
    assert call["generation"] == 1
    assert call["digest"] == ref["digest"]
    assert call["permission_scope"] == "local-single"


def test_analysis_is_deterministic_for_the_same_product(
    tmp_path: Path, prepared: _PreparedArtifacts
) -> None:
    product = _normal_modes_product(tmp_path)
    ref = prepared.add(_MODES_PRODUCT_ID, product.read_bytes())

    first = _handler()(_request(ref))
    second = _handler()(_request(ref))
    assert first == second
    assert first["product"]["digest"] == second["product"]["digest"]


def test_explicit_format_selects_the_product_path(
    tmp_path: Path, prepared: _PreparedArtifacts
) -> None:
    product = _normal_modes_product(tmp_path)
    ref = prepared.add(_MODES_PRODUCT_ID, product.read_bytes())

    result = _handler()(_request(ref, format=NORMAL_MODES_FORMAT))
    assert result["product"]["format"] == NORMAL_MODES_FORMAT
    assert result["product"]["report"]["frequencies"] == [-412.8, 1180.4, 3010.5]


def test_explicit_legacy_format_never_reinterprets_json_as_orca(
    prepared: _PreparedArtifacts,
) -> None:
    ref = prepared.add(_MODES_PRODUCT_ID, json.dumps(_NORMAL_MODES).encode("utf-8"))

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref, format=ORCA_OUTPUT_FORMAT))
    # An ORCA parse of JSON yields no products: the typed not-ready refusal.
    assert excinfo.value.http_status == 409
    assert excinfo.value.params["rule"] == "invalid_scientific_input"


# ── empty / malformed products ──────────────────────────────────────────


def test_empty_product_keeps_the_typed_not_ready_error(
    prepared: _PreparedArtifacts,
) -> None:
    body = json.dumps({"schema_version": "normal_modes_v1", "atom_count": 3, "modes": []}).encode(
        "utf-8"
    )
    ref = prepared.add("frequency_modes_empty", body)

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref))
    error = excinfo.value
    assert error.http_status == 409
    assert error.params["rule"] == "invalid_scientific_input"
    checks = error.params["checks"]
    assert [check["id"] for check in checks] == ["frequency_products_available"]
    assert checks[0]["satisfied"] is False


def test_non_normal_modes_json_is_an_input_error(prepared: _PreparedArtifacts) -> None:
    ref = prepared.add("other_json", b'{"schema_version": "something_else"}')

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref, format=NORMAL_MODES_FORMAT))
    assert excinfo.value.http_status == 400
    assert excinfo.value.params["rule"] == "unsupported_format"
    assert excinfo.value.params["reason"] == "not_normal_modes_v1"


def test_malformed_json_is_an_input_error(prepared: _PreparedArtifacts) -> None:
    ref = prepared.add("broken", b"{not json")

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref, format=NORMAL_MODES_FORMAT))
    assert excinfo.value.http_status == 400
    assert excinfo.value.params["reason"] == "not_normal_modes_json"


def test_non_utf8_body_is_refused(prepared: _PreparedArtifacts) -> None:
    ref = prepared.add("binary", b"\xff\xfe\x00")

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref))
    assert excinfo.value.params["rule"] == "unsupported_format"
    assert excinfo.value.params["reason"] == "not_text"


def test_unknown_format_lists_the_accepted_values(prepared: _PreparedArtifacts) -> None:
    ref = prepared.add("x", b"{}")

    with pytest.raises(AnalysisInputError) as excinfo:
        _handler()(_request(ref, format="gaussian-output"))
    assert excinfo.value.http_status == 400
    assert excinfo.value.params["allowed"] == [ORCA_OUTPUT_FORMAT, NORMAL_MODES_FORMAT]


def test_product_is_manifest_registered_and_discoverable(tmp_path: Path) -> None:
    """The analyze input is the registered ``frequency_modes`` product itself."""
    _normal_modes_product(tmp_path)

    from acp.api import artifact_descriptors as descriptors

    items, truncated = descriptors.describe_job_artifacts(tmp_path / "task", attempt=1)
    assert truncated is False
    descriptor = next(row for row in items if row["resource_id"] == _MODES_PRODUCT_ID)
    assert descriptor["kind"] == "frequency_modes"
    assert descriptor["relative_path"] == f"RESULT/frequencies/{_ITEM_ID}__normal_modes.json"
    assert descriptor["prepare"]["arguments"]["artifact_id"] == _MODES_PRODUCT_ID


# ── capability surface ──────────────────────────────────────────────────


def test_registry_advertises_both_formats_and_compute_stays_inert(tmp_path: Path) -> None:
    store_root = str(tmp_path / "artifacts")
    registry = build_analysis_registry(
        prepare_operations=None,  # type: ignore[arg-type]  # declared-only registry
        artifact_store_root=store_root,
    )
    analyze = registry.get("vibration.analyze")
    assert analyze is not None
    assert analyze.handler is None  # no prepare authority -> not executable
    compute = registry.get("vibration.compute")
    assert compute is not None and compute.handler is None

    executable = build_analysis_registry(
        prepare_operations=object(),  # type: ignore[arg-type]
        artifact_store_root=store_root,
    )
    capability = executable.get("vibration.analyze")
    assert capability is not None and capability.handler is not None
    assert capability.metadata["input_formats"] == [ORCA_OUTPUT_FORMAT, NORMAL_MODES_FORMAT]
    assert "parameters.format overrides" in capability.metadata["input_format_detection"]
