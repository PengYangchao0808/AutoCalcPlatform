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


def test_mechanism_submit_valid_payload_is_rejected_as_retired(tmp_path: Path) -> None:
    """Confsearch v1.0: mechanism submissions are rejected for new runs."""
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/jobs", json=_mechanism_payload())
        assert response.status_code == 400
        assert "Unsupported workflow 'mechanism'" in response.json()["detail"]


def test_mechanism_submit_payload_shape_errors_unreachable_after_retirement(
    tmp_path: Path,
) -> None:
    """The 422 payload-shape checks no longer trigger: retirement wins first."""
    with make_client(tmp_path) as client:
        missing_reactant = _mechanism_payload()
        del _input_payload(missing_reactant)["reactant"]
        assert client.post("/api/v1/jobs", json=missing_reactant).status_code == 400

        bad_role = _mechanism_payload()
        reactant = cast(dict[str, object], _input_payload(bad_role)["reactant"])
        reactant["source_type"] = "garbage"
        assert client.post("/api/v1/jobs", json=bad_role).status_code == 400

        no_plan = _mechanism_payload()
        routes = cast(list[dict[str, object]], _input_payload(no_plan)["routes"])
        del routes[0]["coordinate_plan"]
        assert client.post("/api/v1/jobs", json=no_plan).status_code == 400

        direct_ts = _mechanism_payload()
        _input_payload(direct_ts)["routes"] = [
            {"route_id": "route-1", "path_strategy": "direct-ts"}
        ]
        assert client.post("/api/v1/jobs", json=direct_ts).status_code == 400


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
