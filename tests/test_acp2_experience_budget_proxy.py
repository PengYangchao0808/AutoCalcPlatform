"""Guards for the T-13a model budget proxy and the T-13b receipt usage contract.

These tests are fast and stdlib-only: the proxy's controlled upstream lives in
its own ``selftest`` (see ``scripts/acceptance/acp2_experience_budget_proxy.py``),
which is exercised by the ``model-live`` acceptance scenario.  Here we pin the
ledger invariants that must never regress and the harness redaction rule that
keeps numeric token counts in ``usage``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts/acceptance"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def proxy() -> ModuleType:
    return _load("acp2_budget_proxy_under_test", "acp2_experience_budget_proxy.py")


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load("acp2_experience_harness_under_test", "acp2_experience.py")


def _store(proxy: ModuleType, root: Path, budget_id: str = "unit") -> object:
    store = proxy.BudgetStore(root, budget_id, None, ["stub-ok"])
    store.ensure()
    return store


def _settle_one(proxy: ModuleType, store: object, *, usage: tuple[int | None, int | None]) -> None:
    reservation = store.reserve(
        run_id="run-a",
        model="stub-ok",
        stream=False,
        estimated_input=10,
        requested_max_tokens=16,
    )
    store.settle(reservation, outcome="completed", usage=usage)


def test_estimate_is_positive_and_conservative(proxy: ModuleType) -> None:
    estimate = proxy.estimate_input_tokens({"messages": [{"role": "user", "content": "x" * 300}]})
    assert estimate >= 100


def test_twelve_allowed_thirteenth_refused(proxy: ModuleType, tmp_path: Path) -> None:
    store = _store(proxy, tmp_path)
    for _ in range(12):
        _settle_one(proxy, store, usage=(11, 7))
    with pytest.raises(proxy.BudgetRefusalError) as caught:
        store.reserve(
            run_id="run-a",
            model="stub-ok",
            stream=False,
            estimated_input=10,
            requested_max_tokens=16,
        )
    assert caught.value.code == "model_requests"
    snapshot = store.snapshot()
    assert snapshot["usage"]["model_calls"] == 12
    assert snapshot["remaining"]["model_requests"] == 0
    assert snapshot["refused"] == {"model_requests": 1}


def test_restart_keeps_the_batch_and_remaining_allowance(proxy: ModuleType, tmp_path: Path) -> None:
    first = _store(proxy, tmp_path, "restart")
    _settle_one(proxy, first, usage=(11, 7))
    _settle_one(proxy, first, usage=(11, 7))
    batch_id = first.snapshot()["batch_id"]

    second = _store(proxy, tmp_path, "restart")
    snapshot = second.snapshot()
    assert snapshot["batch_id"] == batch_id
    assert snapshot["usage"]["model_calls"] == 2
    assert snapshot["remaining"]["model_requests"] == 10


def test_run_id_does_not_reset_the_budget(proxy: ModuleType, tmp_path: Path) -> None:
    store = _store(proxy, tmp_path, "run-id")
    _settle_one(proxy, store, usage=(11, 7))
    reservation = store.reserve(
        run_id="run-b",
        model="stub-ok",
        stream=False,
        estimated_input=10,
        requested_max_tokens=16,
    )
    store.settle(reservation, outcome="completed", usage=(11, 7))
    snapshot = store.snapshot()
    assert snapshot["usage"]["model_calls"] == 2
    assert snapshot["run_ids"] == ["run-a", "run-b"]


def test_missing_usage_is_unknown_not_fabricated(proxy: ModuleType, tmp_path: Path) -> None:
    store = _store(proxy, tmp_path, "unknown")
    _settle_one(proxy, store, usage=None)
    snapshot = store.snapshot()
    usage = snapshot["usage"]
    assert usage["output_tokens_reported"] == 0
    assert usage["output_tokens_unknown_events"] == 1
    assert usage["input_tokens_estimated"] > 0
    assert usage["total_tokens_complete"] is False


def test_caps_cannot_be_changed_on_restart(proxy: ModuleType, tmp_path: Path) -> None:
    _store(proxy, tmp_path, "caps")
    changed = proxy.BudgetStore(
        tmp_path,
        "caps",
        {"model_requests": 20, "input_tokens": 150_000, "output_tokens": 30_000},
        ["stub-ok"],
    )
    with pytest.raises(proxy.CapsMismatchError):
        changed.ensure()


def test_unapproved_model_is_refused_before_forwarding(proxy: ModuleType, tmp_path: Path) -> None:
    store = _store(proxy, tmp_path, "unapproved")
    snapshot = store.snapshot()
    assert snapshot["models"] == ["stub-ok"]
    store.record_refusal(code="unapproved_model", message="not approved", model="other")
    assert store.snapshot()["refused"] == {"unapproved_model": 1}


def test_per_request_output_cap_is_refused(proxy: ModuleType, tmp_path: Path) -> None:
    store = _store(proxy, tmp_path, "per-request")
    with pytest.raises(proxy.BudgetRefusalError) as caught:
        store.reserve(
            run_id="run-a",
            model="stub-ok",
            stream=False,
            estimated_input=10,
            requested_max_tokens=proxy.PER_REQUEST_OUTPUT_CAP + 1,
        )
    assert caught.value.code == "per_request_output_cap"
    assert store.snapshot()["usage"]["model_calls"] == 0


def test_usage_token_counts_survive_receipt_redaction(harness: ModuleType) -> None:
    cleaned = harness.redact(
        {
            "usage": {"total_tokens": 18, "input_tokens": 11, "batch_id": "batch-x"},
            "api_key": "raw-secret",
        }
    )
    assert cleaned["usage"]["total_tokens"] == 18
    assert cleaned["usage"]["input_tokens"] == 11
    assert cleaned["api_key"] == "***"
    assert "raw-secret" not in json.dumps(cleaned)
