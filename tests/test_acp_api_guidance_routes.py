"""Route-level tests for the read-only guidance router (plan 2026-10-30 §5).

Pins the two endpoints of ``acp.api.v1_guidance`` mounted by ``create_app``:

* the discovery index is compact (cards, no bodies) and read-only;
* a guide read is versioned/digested, sliceable by section and bounded by
  ``max_bytes`` with explicit omissions;
* an unknown id is a typed 404 with allowed ids and a suggestion; an unknown
  section is a typed 422; a stale cached version is a typed 409;
* every GET leaves ``manager.store.counts()`` unchanged (the read-only surface
  contract of the api package).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_INDEX = "/api/v1/guidance"
_READ = "/api/v1/guidance/{guide_id}"


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _counts(client: TestClient) -> dict[str, int]:
    manager = client.app.state.job_manager
    return dict(manager.store.counts())


def test_index_is_compact_and_read_only(client: TestClient) -> None:
    before = _counts(client)
    response = client.get(_INDEX)
    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "acp.guidance.v1"
    cards = {card["guide_id"]: card for card in payload["guides"]}
    assert "workflow.PESsearch" in cards and "shared.execution" in cards
    for card in cards.values():
        assert "sections" in card and not card.get("bodies")
        assert card["digest"].startswith("sha256:")
    assert _counts(client) == before


def test_guide_read_is_versioned_and_sliceable(client: TestClient) -> None:
    before = _counts(client)
    response = client.get(_READ.format(guide_id="workflow.PESsearch"))
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["sections"]) == {"purpose", "steps"}
    assert payload["version"] == "1.0.0"

    section = client.get(_READ.format(guide_id="workflow.PESsearch"), params={"section": "purpose"})
    assert section.status_code == 200
    assert set(section.json()["sections"]) == {"purpose"}
    assert "steps" in section.json()["omitted"]
    assert _counts(client) == before


def test_byte_bound_degrades_to_explicit_omissions(client: TestClient) -> None:
    response = client.get(_READ.format(guide_id="workflow.PESsearch"), params={"max_bytes": 16})
    assert response.status_code == 200
    payload = response.json()
    assert payload["sections"] == {}
    assert payload["omitted_reason"] == "budget"
    assert payload["omitted"] == ["purpose", "steps"]


def test_unknown_guide_is_a_typed_404_with_suggestion(client: TestClient) -> None:
    response = client.get(_READ.format(guide_id="workflow.PESsearchh"))
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "not_found"
    assert "workflow.PESsearch" in detail["params"]["allowed"]
    assert detail["suggestion"] == "workflow.PESsearch"


def test_unknown_section_is_a_typed_422(client: TestClient) -> None:
    response = client.get(
        _READ.format(guide_id="workflow.PESsearch"), params={"section": "nope"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "input_invalid"


def test_stale_cached_version_is_a_typed_409(client: TestClient) -> None:
    response = client.get(
        _READ.format(guide_id="workflow.PESsearch"), params={"version": "0.9.0"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["params"]["current_version"] == "1.0.0"
    # the matching version reads normally
    ok = client.get(_READ.format(guide_id="workflow.PESsearch"), params={"version": "1.0.0"})
    assert ok.status_code == 200
