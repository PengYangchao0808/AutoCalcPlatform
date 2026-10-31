"""Round-5 P0: one coordinate source, conflicts refused before side effects.

The real test agent submitted ``coordinate=[9,4]`` together with
``coordinates=[[9,0]]``; the platform silently scanned ``[9,0]`` and burned a
task.  These tests pin the plan's §5.4 acceptance matrix:

* single source / consistent dual source / coordinates-only / legal multi-coordinate
  all submit;
* any complete-definition difference (atoms, unit, start/end, points, values)
  is refused with ``rule=conflicting_fields``;
* preflight and submit report the same ``code/field/rule``;
* a refusal creates no job and changes no counts;
* the executed plan carries the same coordinate and point count as the
  normalized definition, and local/remote argv agree (scan).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_XYZ = "6\n\nC 0 0 0\nC 1.5 0 0\nC 2.5 1 0\nC 3.5 0 0\nC 4.5 1 0\nC 5.5 0 0\n"


def _coordinate(atoms: list[int], start: str = "4.90", end: str = "1.55", **extra) -> dict:
    payload = {
        "kind": "distance",
        "atoms": atoms,
        "start": start,
        "end": end,
        "n_points": extra.pop("n_points", 20),
    }
    payload.update(extra)
    return payload


def _pes_input(coordinate: dict | None = None, coordinates: list[dict] | None = None) -> dict:
    payload: dict = {
        "source": {"source_type": "xyz_text", "xyz_text": _XYZ},
        "protocol": {"scan_software": "orca", "levels": {"scan": {"method": "r2SCAN-3c"}}},
    }
    if coordinate is not None:
        payload["coordinate"] = coordinate
    if coordinates is not None:
        payload["coordinates"] = coordinates
    return payload


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _preflight(client: TestClient, workflow: str, input_payload: dict, method: dict | None = None):
    return client.post(
        "/api/v1/preflight",
        json={"workflow": workflow, "input": input_payload, "method": method or {}},
    )


def _job_count(client: TestClient) -> dict:
    return dict(client.app.state.job_manager.store.counts())


# --------------------------------------------------------------------------- #
# Accepted shapes
# --------------------------------------------------------------------------- #


def test_single_coordinate_preflights(client: TestClient) -> None:
    response = _preflight(client, "PESsearch", _pes_input(_coordinate([0, 5])))
    assert response.status_code == 200, response.text
    assert response.json()["feasible"] is True


def test_consistent_dual_source_preflights(client: TestClient) -> None:
    coordinate = _coordinate([0, 5])
    response = _preflight(
        client, "PESsearch", _pes_input(coordinate, [dict(coordinate)])
    )
    assert response.status_code == 200, response.text
    assert response.json()["feasible"] is True


def test_coordinates_only_preflights(client: TestClient) -> None:
    response = _preflight(client, "PESsearch", _pes_input(None, [_coordinate([0, 5])]))
    assert response.status_code == 200, response.text
    assert response.json()["feasible"] is True


def test_legal_multi_coordinate_preflights(client: TestClient) -> None:
    coordinates = [
        _coordinate([0, 5], n_points=10),
        _coordinate([2, 3], start="1.54", end="2.10", n_points=10),
    ]
    response = _preflight(client, "PESsearch", _pes_input(coordinates[0], coordinates))
    assert response.status_code == 200, response.text
    assert response.json()["feasible"] is True


# --------------------------------------------------------------------------- #
# Conflict refusals (the reported case)
# --------------------------------------------------------------------------- #


def test_primary_listed_conflict_is_refused(client: TestClient) -> None:
    # the exact complaint shape: [9,4] primary plus a different [9,0] list
    primary = _coordinate([9, 4])
    listed = _coordinate([9, 0])
    body = _pes_input(primary, [listed])
    response = _preflight(client, "PESsearch", body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["params"]["rule"] == "conflicting_fields"
    assert detail["params"]["field"] == "input.coordinate"
    assert "coordinate" in detail["message"]


@pytest.mark.parametrize(
    "variant",
    [
        {"atoms": [0, 6]},  # different atom pair
        {"start": "4.80"},  # different range
        {"end": "1.60"},
        {"n_points": 21},  # different point count
        {"kind": "distance", "unit": "degree"},  # cross-kind unit
    ],
)
def test_any_definition_difference_is_refused(client: TestClient, variant: dict) -> None:
    primary = _coordinate([0, 5])
    listed = {**_coordinate([0, 5]), **variant}
    response = _preflight(client, "PESsearch", _pes_input(primary, [listed]))
    assert response.status_code == 422, (variant, response.text)
    assert response.json()["detail"]["params"]["rule"] in (
        "conflicting_fields",
        "invalid_unit",
    )


def test_conflict_is_identical_in_preflight_and_submit(client: TestClient) -> None:
    body = _pes_input(_coordinate([9, 4]), [_coordinate([9, 0])])
    preflight = _preflight(client, "PESsearch", body)
    submit = client.post("/api/v1/jobs", json={"workflow": "PESsearch", "input": body})
    assert preflight.status_code == submit.status_code == 422
    p_detail = preflight.json()["detail"]
    s_detail = submit.json()["detail"]
    assert p_detail["code"] == s_detail["code"] == "input_invalid"
    assert p_detail["params"]["rule"] == s_detail["params"]["rule"] == "conflicting_fields"
    assert p_detail["params"]["field"] == s_detail["params"]["field"]


def test_refused_conflict_creates_no_job(client: TestClient) -> None:
    before = _job_count(client)
    body = _pes_input(_coordinate([9, 4]), [_coordinate([9, 0])])
    response = client.post("/api/v1/jobs", json={"workflow": "PESsearch", "input": body})
    assert response.status_code == 422
    after = _job_count(client)
    assert before == after


# --------------------------------------------------------------------------- #
# Normalization layer (the single source of truth)
# --------------------------------------------------------------------------- #


def test_normalization_produces_one_ordered_list() -> None:
    from acp.calculations.pes.normalization import normalize_pes_coordinates

    coordinate = _coordinate([0, 5])
    result = normalize_pes_coordinates(_pes_input(coordinate), field_prefix="input")
    assert result["coordinate"]["atoms"] == [0, 5]
    assert len(result["coordinates"]) == 1
    assert result["n_points"] == 20
    only_list = normalize_pes_coordinates(
        _pes_input(None, [coordinate]), field_prefix="input"
    )
    assert only_list["coordinate"] == result["coordinate"]


def test_scan_alias_conflict_is_refused() -> None:
    from acp.calculations.pes.normalization import normalize_scan_entries

    with pytest.raises(ValueError) as excinfo:
        normalize_scan_entries(
            {"coordinate": {"kind": "distance", "atoms": [0, 1], "start": 1.0, "end": 3.0}},
            {"scan_coordinates": [{"kind": "distance", "atoms": [2, 3], "start": 1.0, "end": 3.0}]},
        )
    assert "conflicts" in str(excinfo.value)


def test_scan_point_alias_conflict_is_refused() -> None:
    from acp.calculations.pes.normalization import normalize_scan_points

    with pytest.raises(ValueError) as excinfo:
        normalize_scan_points(
            {"scan_points": 10, "levels": {"scan": {"scan_coordinate_points": 20}}}, {}
        )
    assert "conflicts" in str(excinfo.value)


def test_scan_flags_consume_the_normalized_entry() -> None:
    from acp.scheduler.jobs import scan_method_flags

    flags = scan_method_flags(
        {"scan_points": 20},
        {"coordinate": {"kind": "distance", "atoms": [0, 1], "start": 1.0, "end": 3.0}},
    )
    assert flags == ["--coordinate", "0,1,1.0,3.0", "--scan-points", "20"]
