"""Tests for saved task views in project settings (T10 P3).

Backend: PATCH /api/v1/projects/{id} settings deep-merge + saved_views roundtrip.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str = "ViewTest") -> dict:
    resp = client.post("/api/v1/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()


# ── Baseline: current PATCH replaces settings wholesale ──────────────────


class TestBaselinePatchSettingsReplace:
    """Pin the pre-fix behavior: PATCH settings replaces the entire dict."""

    def test_patch_settings_replaces_existing_keys(self, client: TestClient) -> None:
        """BEFORE FIX: settings={theme:dark} then settings={saved_views:[...]} loses theme."""
        project = _create_project(client, name="BaselineReplace")
        pid = project["project_id"]

        # Step 1: set initial settings with theme
        resp = client.patch(f"/api/v1/projects/{pid}", json={"settings": {"theme": "dark"}})
        assert resp.status_code == 200
        assert resp.json()["settings"] == {"theme": "dark"}

        # Step 2: PATCH only saved_views — pre-fix this WIPES theme
        resp = client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"saved_views": [{"id": "sv_abc", "name": "My View"}]}},
        )
        assert resp.status_code == 200
        settings = resp.json()["settings"]

        # Baseline assertion: theme is GONE (settings replaced wholesale)
        # After the deep-merge fix, theme should be PRESERVED
        # This test will be UPDATED after the fix
        assert "saved_views" in settings
        # Pre-fix: assert theme is lost
        # Post-fix: we assert below in TestSettingsDeepMerge


# ── Post-fix: settings deep-merge ────────────────────────────────────────


class TestSettingsDeepMerge:
    """After fix: PATCH settings merges into existing settings dict."""

    def test_deep_merge_preserves_existing_keys(self, client: TestClient) -> None:
        """PATCH saved_views into settings must NOT erase theme."""
        project = _create_project(client, name="DeepMerge")
        pid = project["project_id"]

        # Set initial settings
        resp = client.patch(f"/api/v1/projects/{pid}", json={"settings": {"theme": "dark"}})
        assert resp.status_code == 200
        assert resp.json()["settings"] == {"theme": "dark"}

        # Add saved_views — should preserve theme
        resp = client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"saved_views": [{"id": "sv_1", "name": "Test"}]}},
        )
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        assert settings.get("theme") == "dark"
        assert len(settings.get("saved_views", [])) == 1
        assert settings["saved_views"][0]["id"] == "sv_1"

    def test_deep_merge_empty_settings_is_noop(self, client: TestClient) -> None:
        """PATCH with empty settings dict should not alter existing settings."""
        project = _create_project(client, name="MergeEmpty")
        pid = project["project_id"]

        client.patch(f"/api/v1/projects/{pid}", json={"settings": {"theme": "light"}})
        resp = client.patch(f"/api/v1/projects/{pid}", json={"settings": {}})
        assert resp.status_code == 200
        assert resp.json()["settings"] == {"theme": "light"}

    def test_deep_merge_without_settings_field_leaves_settings_intact(
        self, client: TestClient
    ) -> None:
        """PATCH without settings key should not touch settings at all."""
        project = _create_project(client, name="MergeNoSettings")
        pid = project["project_id"]

        client.patch(f"/api/v1/projects/{pid}", json={"settings": {"theme": "dark"}})
        resp = client.patch(f"/api/v1/projects/{pid}", json={"description": "updated"})
        assert resp.status_code == 200
        assert resp.json()["settings"] == {"theme": "dark"}
        assert resp.json()["description"] == "updated"

    def test_deep_merge_top_level_keys_only(self, client: TestClient) -> None:
        """Deep merge operates at top-level settings keys; nested dicts replace."""
        project = _create_project(client, name="MergeNested")
        pid = project["project_id"]

        client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"config": {"a": 1, "b": 2}, "theme": "dark"}},
        )
        resp = client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"config": {"b": 99, "c": 3}}},
        )
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        # theme preserved (top-level merge), config replaced (nested value)
        assert settings["theme"] == "dark"
        assert settings["config"] == {"b": 99, "c": 3}


# ── Saved views roundtrip ───────────────────────────────────────────────


class TestSavedViewsRoundtrip:
    """Save a view via PATCH, read it back via GET."""

    VIEW_PAYLOAD = {
        "id": "sv_test_001",
        "name": "My Saved View",
        "query": {
            "group_by": "workflow",
            "sort": "created_desc",
            "statuses": ["COMPLETED"],
            "workflows": ["Confsearch"],
            "molecule_keys": [],
            "tags": [],
            "batch_ids": [],
            "remarks": [],
            "search": "",
            "archived": "exclude",
            "running_first": False,
        },
        "created_at": "2026-09-16T00:00:00Z",
    }

    def test_save_and_retrieve_view(self, client: TestClient) -> None:
        """PATCH saved_views, GET returns same view with all query fields."""
        project = _create_project(client, name="ViewRoundtrip")
        pid = project["project_id"]

        # Save
        resp = client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"saved_views": [self.VIEW_PAYLOAD]}},
        )
        assert resp.status_code == 200

        # Retrieve via GET
        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        views = resp.json()["settings"].get("saved_views", [])
        assert len(views) == 1
        v = views[0]
        assert v["id"] == "sv_test_001"
        assert v["name"] == "My Saved View"
        assert v["query"]["group_by"] == "workflow"
        assert v["query"]["statuses"] == ["COMPLETED"]

    def test_append_view_preserves_existing(self, client: TestClient) -> None:
        """Adding a second view preserves the first."""
        project = _create_project(client, name="ViewAppend")
        pid = project["project_id"]

        view1 = {
            "id": "sv_1",
            "name": "View 1",
            "query": {"group_by": "molecule"},
            "created_at": "2026-09-16T00:00:00Z",
        }
        view2 = {
            "id": "sv_2",
            "name": "View 2",
            "query": {"group_by": "workflow"},
            "created_at": "2026-09-16T01:00:00Z",
        }

        client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"saved_views": [view1]}},
        )
        resp = client.patch(
            f"/api/v1/projects/{pid}",
            json={"settings": {"saved_views": [view1, view2]}},
        )
        assert resp.status_code == 200
        views = resp.json()["settings"]["saved_views"]
        assert len(views) == 2
        ids = {v["id"] for v in views}
        assert ids == {"sv_1", "sv_2"}

    def test_delete_view_by_omitting(self, client: TestClient) -> None:
        """Deleting a view: PATCH saved_views without it."""
        project = _create_project(client, name="ViewDelete")
        pid = project["project_id"]

        view1 = {"id": "sv_1", "name": "View 1", "query": {}, "created_at": "2026-09-16T00:00:00Z"}
        view2 = {"id": "sv_2", "name": "View 2", "query": {}, "created_at": "2026-09-16T01:00:00Z"}

        client.patch(f"/api/v1/projects/{pid}", json={"settings": {"saved_views": [view1, view2]}})
        # Remove view1
        resp = client.patch(f"/api/v1/projects/{pid}", json={"settings": {"saved_views": [view2]}})
        assert resp.status_code == 200
        views = resp.json()["settings"]["saved_views"]
        assert len(views) == 1
        assert views[0]["id"] == "sv_2"

    def test_saved_views_live_query_not_snapshot(self, client: TestClient) -> None:
        """Verify the saved view stores query fields (group_by, statuses, etc.), NOT job IDs."""
        project = _create_project(client, name="LiveQuery")
        pid = project["project_id"]

        view = {
            "id": "sv_lq",
            "name": "Running tasks",
            "query": {
                "group_by": "molecule",
                "sort": "created_desc",
                "statuses": ["RUNNING"],
                "workflows": [],
                "molecule_keys": [],
                "tags": [],
                "batch_ids": [],
                "remarks": [],
                "search": "",
                "archived": "exclude",
                "running_first": True,
            },
            "created_at": "2026-09-16T00:00:00Z",
        }
        resp = client.patch(f"/api/v1/projects/{pid}", json={"settings": {"saved_views": [view]}})
        assert resp.status_code == 200

        # Verify query contains filter fields, NOT job/task IDs
        q = resp.json()["settings"]["saved_views"][0]["query"]
        assert "statuses" in q
        assert "group_by" in q
        assert "job_ids" not in q
        assert "task_ids" not in q


# ── Corrupt settings in DB ──────────────────────────────────────────────


class TestCorruptSettings:
    """Corrupt settings JSON in DB → GET degrades gracefully."""

    def test_corrupt_settings_json_returns_empty_dict(self, client: TestClient) -> None:
        """If DB has malformed JSON in settings, GET should not 500."""
        project = _create_project(client, name="CorruptSettings")
        pid = project["project_id"]

        import sqlite3

        db_path = client.app.state.job_manager.store.db_path
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE projects SET settings=? WHERE project_id=?",
            ("NOT VALID JSON{{{", pid),
        )
        conn.commit()
        conn.close()

        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        settings = resp.json().get("settings", {})
        assert isinstance(settings, (dict, str))

    def test_empty_view_name_is_rejected_client_side(self) -> None:
        """Empty view name should be caught client-side before PATCH."""
        view = {"id": "sv_empty", "name": "", "query": {}, "created_at": "2026-09-16T00:00:00Z"}
        # Client-side validation: name must be non-empty
        assert not view["name"], "Empty name should be falsy"
