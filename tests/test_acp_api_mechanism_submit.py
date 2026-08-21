"""Tests for mechanism job submission validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _mechanism_payload() -> dict[str, object]:
    return {
        "workflow": "mechanism",
        "name": "mechanism-submit",
        "input": {
            "source_type": "mechanism",
            "reactant": {"source_type": "smiles", "source": "C=O"},
            "product": {"source_type": "smiles", "source": "C[O-]"},
            "routes": [
                {
                    "route_id": "route-1",
                    "path_strategy": "guided-scan",
                    "fidelity": "s3",
                    "coordinate_plan": {
                        "coordinates": [
                            {
                                "id": "rc1",
                                "kind": "distance",
                                "atoms": [0, 1],
                                "role": "drive",
                                "start": 1.2,
                                "end": 2.0,
                            }
                        ],
                        "points": 21,
                        "coupling": "synchronous",
                        "start_from": "reactant",
                    },
                }
            ],
        },
        "method": {"fidelity": "s3"},
    }


def _input_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["input"])


def test_mechanism_submit_valid_payload_is_accepted(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=_mechanism_payload())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["workflow"] == "mechanism"
        assert body["status"]


def test_mechanism_submit_missing_reactant_returns_422(tmp_path: Path) -> None:
    payload = _mechanism_payload()
    del _input_payload(payload)["reactant"]

    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        assert any(error["loc"] == ["reactant"] for error in response.json()["detail"])


def test_mechanism_submit_bad_role_source_type_returns_422(tmp_path: Path) -> None:
    payload = _mechanism_payload()
    reactant = cast(dict[str, object], _input_payload(payload)["reactant"])
    reactant["source_type"] = "garbage"

    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        assert any(
            error["loc"] == ["reactant", "source_type"]
            for error in response.json()["detail"]
        )


def test_mechanism_submit_guided_scan_without_coordinate_plan_returns_422(tmp_path: Path) -> None:
    payload = _mechanism_payload()
    routes = cast(list[dict[str, object]], _input_payload(payload)["routes"])
    del routes[0]["coordinate_plan"]

    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        assert any(
            "coordinate_plan is required" in error["msg"]
            for error in response.json()["detail"]
        )


def test_mechanism_submit_direct_ts_without_ts_guess_id_returns_422(tmp_path: Path) -> None:
    payload = _mechanism_payload()
    _input_payload(payload)["routes"] = [
        {
            "route_id": "route-1",
            "path_strategy": "direct-ts",
        }
    ]

    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        assert any(
            "direct-ts routes require ts_guess_id" in error["msg"]
            for error in response.json()["detail"]
        )


def test_non_mechanism_submit_keeps_free_form_dict_behavior(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "fake",
                "name": "free-form",
                "input": {"source": "CCO", "arbitrary": {"nested": [1, 2, 3]}},
                "method": {"whatever": True, "nested": {"value": "ok"}},
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["workflow"] == "fake"
