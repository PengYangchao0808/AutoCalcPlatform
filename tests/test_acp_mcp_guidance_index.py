"""Round-6 #8: acp_get_guidance without an id returns the discovery index."""

from __future__ import annotations


def test_controller_guidance_without_id_returns_the_index() -> None:
    from acp.services_mcp_bootstrap import _ControllerGuidance

    class _Client:
        def guidance_index(self) -> dict:
            return {
                "format": "acp2.guidance.v1",
                "guides": [{"guide_id": "topic.artifact_locating", "version": "1.1.0"}],
            }

    payload = _ControllerGuidance(_Client()).guidance({}, workspace_id="")
    assert payload["guides"][0]["guide_id"] == "topic.artifact_locating"
