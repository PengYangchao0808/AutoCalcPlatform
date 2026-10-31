"""One local request must derive ONE research identity on every route.

The v1 ``_command_identity`` fallback used the transport label ``http`` while
the model gateway (and the research saga default) label the Controller-process
fallback ``controller``. ``ResearchIdentity.allocate`` folds ``entry_class``
into the digest, so the same unverified request derived two different research
ids on ``/api/v2/model/usage`` and ``/api/v2/research/evidence/read`` and the
evidence read was fenced as a foreign identity. This pins the shared label.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from acp.api.server import create_app
from acp_services.runtime.research_session import ResearchIdentity
from acp_services.workspace.command_identity import EntryClass


@pytest.fixture()
def identity_pair(tmp_path):
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1, poll_interval=60)) as client:
        app = client.app
        server = app.state.server_identity
        assert server is not None

        class _State:
            request_identity = None

        request = SimpleNamespace(app=app, state=_State())
        from acp.api import v1_routes, v2_model_gateway

        yield request, v1_routes, v2_model_gateway


def test_local_fallback_entry_class_is_controller_on_both_routes(identity_pair) -> None:
    request, v1_routes, v2_model_gateway = identity_pair
    project = "proj-consistency"
    v1_identity = v1_routes._command_identity(request, project_id=project)
    gateway_identity = v2_model_gateway._command_identity(request, project)
    assert str(v1_identity.entry_class) == EntryClass.CONTROLLER
    assert str(gateway_identity.entry_class) == EntryClass.CONTROLLER
    assert v1_identity.principal_id == gateway_identity.principal_id


def test_same_request_derives_one_research_identity(identity_pair) -> None:
    request, v1_routes, v2_model_gateway = identity_pair
    project = "proj-consistency"
    seed = "session-seed-1"
    v1_identity = v1_routes._command_identity(request, project_id=project)
    gateway_identity = v2_model_gateway._command_identity(request, project)
    v1_research = ResearchIdentity.allocate(v1_identity, session_seed=seed)
    gateway_research = ResearchIdentity.allocate(gateway_identity, session_seed=seed)
    assert v1_research.research_id == gateway_research.research_id
    assert v1_research.session_id == gateway_research.session_id
