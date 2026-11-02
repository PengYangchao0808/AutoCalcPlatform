"""Round-7 #7b: the idempotency key accepts chemistry-shaped names.

External-agent complaint: a retry key such as ``...:sp-ccsd(t)-ts:...`` was
refused with ``idempotency_key is malformed`` because the frozen character class
was ``A-Za-z0-9._:-``.  Chemistry names legitimately carry parentheses (and
occasionally brackets), and the key only feeds the Controller's idempotency
ledger (a TEXT column), logs and the canonical request digest -- never a path or
a shell token.  The shared normaliser
(:mod:`acp_services.entrypoints.application_params`) is the single pattern used
by HTTP *and* MCP, so widening it there fixes both entries at once.

The guard rails that must survive the widening:

* the length bound stays 8-128 characters;
* whitespace, slashes, quotes, backslashes and control characters stay refused;
* the rejection remains ``input_invalid`` + ``params.rule == "pattern"`` with the
  published pattern (a client can correct the value without prose parsing).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.entrypoints.application_params import (
    IDEMPOTENCY_KEY_PATTERN,
    ApplicationParamError,
    canonical_submit_request,
    normalize_analyze,
    normalize_prepare,
    normalize_submit,
)

#: A minimal, otherwise-valid submit payload (no science is ever executed).
_BASE: dict[str, Any] = {"workflow": "singlepoint", "input": {"source": "CCO"}}

#: Keys a chemistry-aware caller actually writes today.
_ACCEPTED = (
    "exp:sp-ccsd(t)-ts:0001",  # the exact complaint shape
    "exp:[t1]:ccsd(t):0002",  # bracket namespace + parenthesis
    "m0:opt-r2scan-3c:ccsd(t):03",
    "r7:sp-b97-3c:ccsd(t)[a]:(b):4",
    "aaaaaaaa",  # exactly 8 characters (lower bound)
    "a" * 128,  # exactly 128 characters (upper bound)
)

#: Characters the pattern must keep rejecting, even though the class widened.
_REFUSED = {
    "space": "foo bar baz",
    "slash": "../x../y../z",
    "path": "run/one/two",
    "double-quote": 'he said "hi" now',
    "single-quote": "it's-a-key-1",
    "backslash": "a\\b\\cdefgh",
    "tab": "tab\tkey-value",
    "newline": "line\nkey-value",
    "semicolon": "key;rm-rf:0001",
    "angle": "<script>alert(1)</script>",
}


def _error(key: str) -> ApplicationParamError:
    with pytest.raises(ApplicationParamError) as excinfo:
        normalize_submit({**_BASE, "idempotency_key": key})
    return excinfo.value


@pytest.mark.parametrize("key", _ACCEPTED)
def test_chemistry_shaped_keys_are_accepted_by_the_shared_normaliser(key: str) -> None:
    assert normalize_submit({**_BASE, "idempotency_key": key})["idempotency_key"] == key
    # The canonical form (the request-digest material) keeps the key verbatim.
    canonical = canonical_submit_request({**_BASE, "idempotency_key": key})
    assert canonical["idempotency_key"] == key


@pytest.mark.parametrize(
    "key",
    ("short77", "b" * 129),
    ids=("too-short", "too-long"),
)
def test_length_bounds_stay_8_to_128(key: str) -> None:
    error = _error(key)
    assert error.code == "input_invalid"
    assert error.params["rule"] == "pattern"
    assert error.params["pattern"] == IDEMPOTENCY_KEY_PATTERN


@pytest.mark.parametrize(
    "key",
    ("foo bar", "../x", 'say "hi"', "it's ok"),
    ids=("space", "slash", "double-quote", "single-quote"),
)
def test_the_lead_named_malformed_examples_are_still_refused(key: str) -> None:
    """The three original rejections must not be traded away for the widening."""
    error = _error(key)
    assert error.code == "input_invalid"
    assert error.params["rule"] == "pattern"
    assert error.params["pattern"] == IDEMPOTENCY_KEY_PATTERN


@pytest.mark.parametrize("key", _REFUSED.values(), ids=tuple(_REFUSED))
def test_unsafe_characters_are_still_refused(key: str) -> None:
    error = _error(key)
    assert error.code == "input_invalid"
    assert error.params == {
        "field": "idempotency_key",
        "rule": "pattern",
        "pattern": IDEMPOTENCY_KEY_PATTERN,
    }
    # The malformed value itself is never echoed back (a key may be sensitive).
    assert key not in error.message
    assert key not in str(error.params)


def test_prepare_and_analyze_share_the_same_widened_pattern() -> None:
    """One authority: prepare/analyze write entries accept the same keys."""
    key = "exp:[t1]:ccsd(t):0001"
    prepared = normalize_prepare(
        {
            "purpose": "artifact",
            "artifact_id": "catalog",
            "task_ref": {"task_id": "j1"},
            "idempotency_key": key,
        }
    )
    assert prepared["idempotency_key"] == key
    analyzed = normalize_analyze(
        {
            "analysis_id": "a1",
            "input_refs": [
                {
                    "task_id": "j1",
                    "attempt": 1,
                    "artifact_id": "catalog",
                    "generation": 1,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
            "explicit": True,
            "idempotency_key": key,
        }
    )
    assert analyzed["idempotency_key"] == key


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


def _row_count(client: TestClient) -> int:
    return sum(dict(client.app.state.job_manager.store.counts()).values())


def test_http_submit_accepts_a_parenthesised_key_and_replays(client: TestClient) -> None:
    """HTTP entry: the key is accepted by the real route, not only the helper."""
    body = {
        "workflow": "fake",  # internal synthetic workflow: no QC is started
        "name": "round7-key",
        "input": {"source": "CCO"},
        "idempotency_key": "exp:sp-ccsd(t)-ts:[v2]0001",
    }
    first = client.post("/api/v1/jobs", json=body)
    assert first.status_code == 201, first.text
    created = first.json()
    assert created["idempotency_key"] == "exp:sp-ccsd(t)-ts:[v2]0001"
    assert created["idempotent_replay"] is False
    assert created["job_id"]

    replay = client.post("/api/v1/jobs", json=body)
    assert replay.status_code == 201, replay.text
    replayed = replay.json()
    # Same key + same payload = one operation, replayed by the ledger.
    assert replayed["job_id"] == created["job_id"]
    assert replayed["idempotent_replay"] is True
    assert _row_count(client) == 1


def test_http_submit_rejects_unsafe_keys_before_any_side_effect(client: TestClient) -> None:
    """HTTP entry: the same refusals, before any task row is created."""
    before = _row_count(client)
    for label, key in _REFUSED.items():
        body = {
            "workflow": "fake",
            "name": "round7-bad-key",
            "input": {"source": "CCO"},
            "idempotency_key": key,
        }
        response = client.post("/api/v1/jobs", json=body)
        # A shape violation that is not a field-policy rule keeps the 400 answer.
        assert response.status_code == 400, (label, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "input_invalid", label
        assert detail["params"] == {
            "field": "idempotency_key",
            "rule": "pattern",
            "pattern": IDEMPOTENCY_KEY_PATTERN,
        }, label
        assert _row_count(client) == before, label


def test_http_accepts_a_key_with_brackets_end_to_end(client: TestClient) -> None:
    """Brackets are part of the same widening (``[t1]``/``[v2]`` namespaces)."""
    body = {
        "workflow": "fake",
        "name": "round7-bracket-key",
        "input": {"source": "CCO"},
        "idempotency_key": "exp:[t1]:sp-ccsd(t):0001",
    }
    response = client.post("/api/v1/jobs", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["idempotency_key"] == "exp:[t1]:sp-ccsd(t):0001"


def test_http_header_only_key_uses_the_same_pattern(client: TestClient) -> None:
    """Round-7 lead follow-up: the Idempotency-Key header is not a loophole."""
    body = {"workflow": "fake", "name": "round7-header", "input": {"source": "CCO"}}
    before = _row_count(client)

    good = client.post(
        "/api/v1/jobs",
        json=body,
        headers={"Idempotency-Key": "exp:header-sp-ccsd(t):0001"},
    )
    assert good.status_code == 201, good.text
    assert good.json()["idempotency_key"] == "exp:header-sp-ccsd(t):0001"

    bad = client.post(
        "/api/v1/jobs",
        json=body,
        headers={"Idempotency-Key": "bad key s p"},
    )
    assert bad.status_code == 400, bad.text
    detail = bad.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"]["field"] == "idempotency_key"
    assert detail["params"]["rule"] == "pattern"
    assert _row_count(client) == before + 1  # only the good header created a row
