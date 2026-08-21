"""Tests for mechanism reaction preview/confirm/plan API routes."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from acp.mechanism.reaction_definition import validate_reaction_json
from acp.scheduler.manager import JobManager
from acp.scheduler.migrations import migrate


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _study_payload(study_id: str) -> dict[str, object]:
    return {
        "study_id": study_id,
        "status": "draft",
        "metadata": {"label": "rxn"},
    }


def _reaction_request(reactant: str, product: str) -> dict[str, object]:
    return {
        "reactant": {"source_type": "smiles", "source": reactant},
        "product": {"source_type": "smiles", "source": product},
        "charge": 0,
        "multiplicity": 1,
        "manual_bond_editing": False,
    }


def _manager(client: TestClient) -> JobManager:
    return cast(JobManager, cast(Starlette, client.app).state.job_manager)


def test_reaction_preview_happy_path_returns_bond_changes(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-preview", "study_json": _study_payload("study-preview")},
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-preview/reaction/preview",
            json=_reaction_request("CC=O", "C=CO"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        assert body["mapping_status"] == "unique"
        assert body["manual_mode"] is False
        assert body["bond_changes"]
        assert body["suggested_plan"] is not None
        assert body["preview_hash"].startswith("sha256:")


def test_reaction_preview_identical_symmetric_molecule_resolves_unique(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={
                "study_id": "study-ambiguous",
                "study_json": _study_payload("study-ambiguous"),
            },
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-ambiguous/reaction/preview",
            json=_reaction_request("C1CC1", "C1CC1"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        assert body["mapping_status"] == "unique"
        assert body["bond_changes"] == []
        assert body["candidates"][0]["mapping"]
        assert body["candidates"][0]["mapping_source"] == "smiles_mcs"
        assert any("minimal-chemical-change tie-break" in warning for warning in body["warnings"])


def test_reaction_confirm_without_selected_candidate_returns_409(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={
                "study_id": "study-confirm-409",
                "study_json": _study_payload("study-confirm-409"),
            },
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-confirm-409/reaction/confirm",
            json=_reaction_request("CCl", "C"),
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["status"] == "confirmation_required"
        assert detail["mapping_status"] == "count_mismatch"
        assert len(detail["candidates"]) >= 1


def test_reaction_confirm_with_selected_candidate_persists_row(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-confirm", "study_json": _study_payload("study-confirm")},
        )

        payload = _reaction_request("C1CC1", "C1CC1")
        payload["selected_candidate"] = 0
        response = client.post(
            "/api/v1/mechanism-studies/study-confirm/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"
        assert body["config_hash"].startswith("sha256:")
        assert body["reaction"]["schema_version"] == 2

        row = _manager(client).store.get_mechanism_study("study-confirm")
        assert row is not None
        assert row["status"] == "reaction_confirmed"
        assert json.loads(str(row["reaction_json"]))["study_id"] == "study-confirm"


def test_reaction_confirm_with_manual_bond_changes_locks_user_records(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-manual", "study_json": _study_payload("study-manual")},
        )

        payload = _reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [
            {"reactant_atoms": [0, 1], "change_type": "break"},
            {"reactant_atoms": [0, 2], "change_type": "form"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"
        assert body["config_hash"].startswith("sha256:")

        reaction = body["reaction"]
        assert reaction["confirmed_by"] == "user_manual"
        assert len(reaction["bond_changes"]) == 2
        break_change, form_change = reaction["bond_changes"]
        assert break_change["reactant_atoms"] == [0, 1]
        assert break_change["change_type"] == "break"
        assert break_change["bond_order_before"] == 1.0
        assert break_change["bond_order_after"] == 0.0
        assert break_change["confidence"] == 1.0
        assert break_change["distance_before"] > 0.0
        assert form_change["reactant_atoms"] == [0, 2]
        assert form_change["change_type"] == "form"
        assert form_change["bond_order_before"] == 0.0
        assert form_change["bond_order_after"] == 1.0
        assert form_change["product_atoms"] is not None

        assert body["suggested_plan"] is not None
        assert body["suggested_plan"]["start_from"] == "reactant"
        drive_roles = [
            coordinate
            for coordinate in body["suggested_plan"]["coordinates"]
            if coordinate["role"] == "drive"
        ]
        assert {tuple(coordinate["atoms"]) for coordinate in drive_roles} == {(0, 1), (0, 2)}

        reaction_path = tmp_path / "reaction.json"
        reaction_path.write_text(
            json.dumps(reaction, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        definition = validate_reaction_json(reaction_path)
        assert definition.content_hash == body["config_hash"]
        assert [list(change.reactant_atoms) for change in definition.bond_changes] == [
            [0, 1],
            [0, 2],
        ]

        row = _manager(client).store.get_mechanism_study("study-manual")
        assert row is not None
        assert row["status"] == "reaction_confirmed"


def test_reaction_confirm_with_invalid_manual_bond_changes_returns_422(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-manual-bad", "study_json": _study_payload("study-m-bad")},
        )

        three_atoms = _reaction_request("CC=O", "C=CO")
        three_atoms["manual_bond_changes"] = [
            {"reactant_atoms": [0, 1, 2], "change_type": "break"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-bad/reaction/confirm",
            json=three_atoms,
        )
        assert response.status_code == 422
        assert "exactly two atom indices" in response.json()["detail"]

        out_of_range = _reaction_request("CC=O", "C=CO")
        out_of_range["manual_bond_changes"] = [
            {"reactant_atoms": [0, 99], "change_type": "form"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-bad/reaction/confirm",
            json=out_of_range,
        )
        assert response.status_code == 422
        assert "out of range" in response.json()["detail"]

        bad_type = _reaction_request("CC=O", "C=CO")
        bad_type["manual_bond_changes"] = [
            {"reactant_atoms": [0, 1], "change_type": "shatter"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-bad/reaction/confirm",
            json=bad_type,
        )
        assert response.status_code == 422
        assert "change_type" in response.json()["detail"]


def _manual_reaction_request(reactant: str, product: str) -> dict[str, object]:
    return {
        "reactant": {"source_type": "smiles", "source": reactant},
        "product": {"source_type": "smiles", "source": product},
        "charge": 0,
        "multiplicity": 1,
    }


def test_reaction_preview_manual_mode_skips_auto_determination(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-manual-preview", "study_json": _study_payload("s-m-preview")},
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-manual-preview/reaction/preview",
            json=_manual_reaction_request("CC=O", "C=CO"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        assert body["mapping_status"] == "manual"
        assert body["manual_mode"] is True
        assert body["candidates"] == []
        assert body["selected_candidate"] is None
        assert body["unmatched_reactant_atoms"] == []
        assert body["unmatched_product_atoms"] == []
        assert body["bond_changes"] == []
        assert body["suggested_plan"] is None
        assert any("自动成键判定已禁用" in warning for warning in body["warnings"])
        assert body["preview_hash"].startswith("sha256:")


def test_reaction_confirm_manual_mode_requires_manual_bond_changes(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-manual-req", "study_json": _study_payload("s-m-req")},
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-manual-req/reaction/confirm",
            json=_manual_reaction_request("CC=O", "C=CO"),
        )
        assert response.status_code == 422
        assert "manual_bond_changes" in response.json()["detail"]

        empty = _manual_reaction_request("CC=O", "C=CO")
        empty["manual_bond_changes"] = []
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-req/reaction/confirm",
            json=empty,
        )
        assert response.status_code == 422
        assert "manual_bond_changes" in response.json()["detail"]


def test_reaction_confirm_manual_mode_locks_user_records_without_mapping(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-manual-mode", "study_json": _study_payload("s-m-mode")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [
            {"reactant_atoms": [0, 1], "change_type": "break"},
            {"reactant_atoms": [0, 2], "change_type": "form"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-mode/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"
        assert body["config_hash"].startswith("sha256:")

        reaction = body["reaction"]
        assert reaction["confirmed_by"] == "user_manual"
        assert reaction["atom_mapping"] == []
        assert len(reaction["bond_changes"]) == 2
        break_change, form_change = reaction["bond_changes"]
        assert break_change["reactant_atoms"] == [0, 1]
        assert break_change["change_type"] == "break"
        assert form_change["reactant_atoms"] == [0, 2]
        assert form_change["change_type"] == "form"
        assert break_change["product_atoms"] is None
        assert form_change["product_atoms"] is None
        assert break_change["distance_before"] > 0.0

        assert body["suggested_plan"] is not None
        assert body["suggested_plan"]["start_from"] == "reactant"
        drive_atoms = {
            tuple(coordinate["atoms"])
            for coordinate in body["suggested_plan"]["coordinates"]
            if coordinate["role"] == "drive"
        }
        assert drive_atoms == {(0, 1), (0, 2)}

        reaction_path = tmp_path / "reaction.json"
        reaction_path.write_text(
            json.dumps(reaction, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        definition = validate_reaction_json(reaction_path)
        assert definition.content_hash == body["config_hash"]
        assert [list(change.reactant_atoms) for change in definition.bond_changes] == [
            [0, 1],
            [0, 2],
        ]

        invalid = _manual_reaction_request("CC=O", "C=CO")
        invalid["manual_bond_changes"] = [{"reactant_atoms": [0, 99], "change_type": "form"}]
        response = client.post(
            "/api/v1/mechanism-studies/study-manual-mode/reaction/confirm",
            json=invalid,
        )
        assert response.status_code == 422
        assert "out of range" in response.json()["detail"]


def test_reaction_confirm_manual_mode_allow_zero_changes_locks_empty_set(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-zero-changes", "study_json": _study_payload("s-zero")},
        )

        payload = _manual_reaction_request("C1CC1", "C1CC1")
        payload["manual_bond_changes"] = []
        payload["allow_zero_changes"] = True
        response = client.post(
            "/api/v1/mechanism-studies/study-zero-changes/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"
        assert body["reaction"]["bond_changes"] == []
        assert body["reaction"]["confirmed_by"] == "user_manual"
        assert body["reaction"]["atom_mapping"] == []
        assert body["suggested_plan"] is None
        assert body["config_hash"].startswith("sha256:")

        reaction_path = tmp_path / "reaction_zero.json"
        reaction_path.write_text(
            json.dumps(body["reaction"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        definition = validate_reaction_json(reaction_path)
        assert definition.bond_changes == ()
        assert definition.atom_mapping == ()


def test_reaction_confirm_manual_product_side_entry_locks_product_space_record(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-product-side", "study_json": _study_payload("s-prod")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [{"product_atoms": [0, 2], "change_type": "form"}]
        response = client.post(
            "/api/v1/mechanism-studies/study-product-side/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"

        reaction = body["reaction"]
        assert reaction["confirmed_by"] == "user_manual"
        assert len(reaction["bond_changes"]) == 1
        change = reaction["bond_changes"][0]
        assert change["product_atoms"] == [0, 2]
        assert change["reactant_atoms"] is None
        assert change["change_type"] == "form"
        assert change["distance_before"] > 0.0
        assert change["distance_after"] is None
        assert change["confidence"] == 1.0

        assert body["suggested_plan"] is not None
        assert body["suggested_plan"]["start_from"] == "product"
        drive_coordinates = [
            coordinate
            for coordinate in body["suggested_plan"]["coordinates"]
            if coordinate["role"] == "drive"
        ]
        assert [tuple(coordinate["atoms"]) for coordinate in drive_coordinates] == [(0, 2)]
        assert drive_coordinates[0]["start"] == pytest.approx(change["distance_before"])
        assert drive_coordinates[0]["end"] == pytest.approx(change["distance_before"] + 2.0)

        reaction_path = tmp_path / "reaction_product.json"
        reaction_path.write_text(
            json.dumps(reaction, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        definition = validate_reaction_json(reaction_path)
        assert definition.content_hash == body["config_hash"]
        assert definition.bond_changes[0].reactant_atoms is None
        assert definition.bond_changes[0].product_atoms == (0, 2)

        mapping_resolved = _reaction_request("CC=O", "C=CO")
        mapping_resolved["manual_bond_changes"] = [{"product_atoms": [0, 2], "change_type": "form"}]
        resolved_response = client.post(
            "/api/v1/mechanism-studies/study-product-side/reaction/confirm",
            json=mapping_resolved,
        )
        assert resolved_response.status_code == 200, resolved_response.text
        resolved_change = resolved_response.json()["reaction"]["bond_changes"][0]
        assert resolved_change["product_atoms"] == [0, 2]
        assert resolved_change["reactant_atoms"] is None
        assert resolved_response.json()["suggested_plan"]["start_from"] == "product"


def test_reaction_confirm_manual_both_sides_entry_locks_record(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-both-sides", "study_json": _study_payload("s-both")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [
            {
                "reactant_atoms": [0, 1],
                "product_atoms": [0, 1],
                "change_type": "break",
            }
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-both-sides/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"

        change = body["reaction"]["bond_changes"][0]
        assert change["reactant_atoms"] == [0, 1]
        assert change["product_atoms"] == [0, 1]
        assert change["distance_before"] > 0.0
        assert change["distance_after"] > 0.0

        assert body["suggested_plan"] is not None
        assert body["suggested_plan"]["start_from"] == "reactant"
        drive_coordinates = [
            coordinate
            for coordinate in body["suggested_plan"]["coordinates"]
            if coordinate["role"] == "drive"
        ]
        assert [tuple(coordinate["atoms"]) for coordinate in drive_coordinates] == [(0, 1)]


def test_reaction_confirm_manual_entry_without_any_side_returns_422(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-no-side", "study_json": _study_payload("s-none")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [{"change_type": "form"}]
        response = client.post(
            "/api/v1/mechanism-studies/study-no-side/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 422
        assert "at least one of" in response.json()["detail"]
        assert "reactant_atoms or product_atoms" in response.json()["detail"]


def test_reaction_confirm_manual_product_atom_out_of_range_returns_422(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-prod-range", "study_json": _study_payload("s-prange")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [{"product_atoms": [0, 99], "change_type": "form"}]
        response = client.post(
            "/api/v1/mechanism-studies/study-prod-range/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 422
        assert "out of range" in response.json()["detail"]
        assert "product_atoms" in response.json()["detail"]


def test_reaction_confirm_legacy_reactant_only_entry_keeps_reactant_start(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-legacy", "study_json": _study_payload("s-legacy")},
        )

        payload = _manual_reaction_request("CC=O", "C=CO")
        payload["manual_bond_changes"] = [
            {"reactant_atoms": [0, 1], "change_type": "break"},
            {"reactant_atoms": [0, 2], "change_type": "form"},
        ]
        response = client.post(
            "/api/v1/mechanism-studies/study-legacy/reaction/confirm",
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "locked"
        assert body["suggested_plan"] is not None
        assert body["suggested_plan"]["start_from"] == "reactant"
        drive_atoms = {
            tuple(coordinate["atoms"])
            for coordinate in body["suggested_plan"]["coordinates"]
            if coordinate["role"] == "drive"
        }
        assert drive_atoms == {(0, 1), (0, 2)}
        for change in body["reaction"]["bond_changes"]:
            assert change["reactant_atoms"] is not None


def test_get_reaction_round_trip(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-get", "study_json": _study_payload("study-get")},
        )
        payload = _reaction_request("C1CC1", "C1CC1")
        payload["selected_candidate"] = 0
        confirm = client.post("/api/v1/mechanism-studies/study-get/reaction/confirm", json=payload)
        assert confirm.status_code == 200, confirm.text

        response = client.get("/api/v1/mechanism-studies/study-get/reaction")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "reaction_confirmed"
        assert body["reaction"]["content_hash"] == confirm.json()["config_hash"]


def test_mechanism_plan_requires_locked_reaction_then_confirms(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-plan", "study_json": _study_payload("study-plan")},
        )
        plan_payload = {
            "plan": {
                "coordinates": [
                    {
                        "id": "rc1",
                        "kind": "distance",
                        "atoms": [0, 1],
                        "role": "drive",
                        "start": 1.3,
                        "end": 2.1,
                    }
                ],
                "points": 21,
                "coupling": "synchronous",
                "start_from": "reactant",
            },
            "strategy": "guided-scan",
            "fidelity": "s3",
        }

        prelock = client.post(
            "/api/v1/mechanism-studies/study-plan/mechanism/plan",
            json=plan_payload,
        )
        assert prelock.status_code == 409

        reaction_payload = _reaction_request("C1CC1", "C1CC1")
        reaction_payload["selected_candidate"] = 0
        confirm = client.post(
            "/api/v1/mechanism-studies/study-plan/reaction/confirm",
            json=reaction_payload,
        )
        assert confirm.status_code == 200, confirm.text

        locked = client.post(
            "/api/v1/mechanism-studies/study-plan/mechanism/plan",
            json=plan_payload,
        )
        assert locked.status_code == 200, locked.text
        body = locked.json()
        assert body["status"] == "plan_confirmed"
        assert body["plan_hash"].startswith("sha256:")


def test_mechanism_study_draft_creation_without_job_id(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-studies",
            json={
                "study_id": "study-draft",
                "study_json": _study_payload("study-draft"),
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["id"] == "study-draft"
        assert body["job_id"] is None
        assert body["status"] == "draft"


def test_migration_007_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(mechanism_studies)").fetchall()
        }
    assert "reaction_json" in columns
    assert "mechanism_plan_json" in columns
    assert "config_hash" in columns
    assert "cycle_index" in columns
    assert "consumed_cycle" in columns
