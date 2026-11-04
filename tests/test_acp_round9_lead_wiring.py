"""Round-9 lead wiring: the cross-batch seams that no executor owned.

Each test pins one seam that only exists because two batches had to meet:
the MCP-side capability map, the descriptor kind pager, the singlepoint default,
the published energy method and the evidence energy picker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.api.artifact_descriptors import describe_job_artifacts
from acp.calculations.attempt_publication import _energy_method
from acp.workflows.simple import _orca_default_method
from acp_services.entrypoints.mcp import _analysis_capability_view


def _manifest(task_dir: Path, products: list[dict]) -> None:
    result = task_dir / "RESULT"
    result.mkdir(parents=True, exist_ok=True)
    (result / "result_manifest.json").write_text(
        json.dumps({"schema_version": 2, "products": products}), encoding="utf-8"
    )


def _product(product_id: str, path: str, kind: str, label: str = "") -> dict:
    return {"id": product_id, "path": path, "kind": kind, "label": label or product_id}


def test_descriptor_kind_filter_pages_frames_without_raising_the_bound(tmp_path: Path) -> None:
    products = [_product("pes_profile", "pes_search/pes_profile.json", "pes_profile")]
    products += [
        _product(f"frame_{index:03d}", f"structures/frame_{index:03d}.xyz", "structure")
        for index in range(120)
    ]
    _manifest(tmp_path, products)

    every, truncated = describe_job_artifacts(tmp_path, attempt=1, job_id="job")
    assert len(every) == 50 and truncated is True

    frames, frames_truncated = describe_job_artifacts(
        tmp_path, attempt=1, job_id="job", kind="structure"
    )
    assert [item["resource_id"] for item in frames] == [
        f"frame_{index:03d}" for index in range(50)
    ]
    assert frames_truncated is True

    profile, profile_truncated = describe_job_artifacts(
        tmp_path, attempt=1, job_id="job", kind="pes_profile"
    )
    assert [item["resource_id"] for item in profile] == ["pes_profile"]
    assert profile_truncated is False


def test_controller_summary_analysis_states_are_forwarded_not_unknown() -> None:
    view = _analysis_capability_view(
        {"vibration.compute": "registered", "vibration.analyze": "executable"},
        summary=True,
    )
    assert view["summary_states"] == {
        "vibration.compute": "registered",
        "vibration.analyze": "executable",
    }
    assert view["detail_tool"]["arguments"] == {"detail": "full"}

    missing = _analysis_capability_view({"something_else": "nonsense"}, summary=True)
    assert missing["status"] == "unknown"


def test_singlepoint_default_follows_the_single_point_section() -> None:
    config = {
        "theory": {
            "optimization": {"method": "r2SCAN-3c"},
            "single_point": {"method": "wB97M-V"},
        }
    }
    # USER CORRECTION (round-8): optimization keeps r2SCAN-3c, single point
    # stays wB97M-V — the implicit default must match the published one.
    assert _orca_default_method(config, workflow="singlepoint") == "wB97M-V"
    assert _orca_default_method(config, workflow="optimize") == "r2SCAN-3c"
    assert _orca_default_method({}, workflow="singlepoint") == "wB97M-V"
    assert _orca_default_method({}, workflow="optimize") == "r2SCAN-3c"


def test_published_energy_method_reads_provenance_not_top_level() -> None:
    body = {"provenance": {"method": "r2SCAN-3c", "basis": "def2-mSVP"}}
    assert _energy_method(body) == "r2SCAN-3c"
    assert _energy_method({"provenance": {"level": {"method": "wB97M-V"}}}) == "wB97M-V"
    assert _energy_method({"method": "B97-3c"}) == "B97-3c"
    assert _energy_method({}) is None
    assert _energy_method({"provenance": {"method": ""}}) is None


def test_evidence_energy_picker_prefers_singlepoint_and_refuses_ambiguity(
    tmp_path: Path,
) -> None:
    from acp_services.runtime.evidence_production import (
        EvidenceProductionError,
        _energy_product,
    )

    _manifest(
        tmp_path,
        [
            _product("batch_int_energy", "energy/int_optimize.json", "energy_report"),
            _product("batch_ts_energy", "energy/ts_singlepoint.json", "energy_report"),
        ],
    )
    (tmp_path / "RESULT" / "energy").mkdir(parents=True, exist_ok=True)
    for name in ("int_optimize", "ts_singlepoint"):
        (tmp_path / "RESULT" / "energy" / f"{name}.json").write_text(
            json.dumps({"method": "r2SCAN-3c"}), encoding="utf-8"
        )
    manifest = json.loads((tmp_path / "RESULT" / "result_manifest.json").read_text())
    manifest["products"][1]["metadata"] = {"energy_step": "singlepoint"}
    (tmp_path / "RESULT" / "result_manifest.json").write_text(json.dumps(manifest))

    path, _payload = _energy_product(tmp_path)
    assert path.name == "ts_singlepoint.json"

    manifest["products"][0]["metadata"] = {"energy_step": "singlepoint"}
    (tmp_path / "RESULT" / "result_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(EvidenceProductionError) as excinfo:
        _energy_product(tmp_path)
    assert excinfo.value.code == "energy_product_ambiguous"


def test_unclassified_prepare_failure_is_not_projected_as_retryable() -> None:
    """An unknown executor failure keeps its code but must not claim a race.

    A retryable verdict on a parameter-class failure makes the client loop
    forever; the projection returns nothing for a token outside the table.
    """
    from acp_services.data.operations import _refusal_projection

    assert _refusal_projection("not_prepared", "OSError: disk went away") is None
    assert _refusal_projection("not_prepared", "Execution failed") is None
    retryable = _refusal_projection("not_prepared", "not_ready: body not materialized")
    assert retryable is not None and retryable.retryable is True
    identity = _refusal_projection("not_prepared", "job_missing: deleted")
    assert identity is not None and identity.rule == "unknown_job"
    assert _refusal_projection("invalid_parameter", "x") is not None


def test_shutdown_signal_is_never_swallowed_by_the_dispatcher() -> None:
    """The signal path must escape ``except Exception`` in the JSON-RPC loop.

    A swallowed shutdown leaves the transport blocked in readline until the test
    (or the operator) times out; the control-flow exception is a BaseException.
    """
    from acp_services.entrypoints.mcp_stdio import _ShutdownRequestedError

    assert not issubclass(_ShutdownRequestedError, Exception)
    assert issubclass(_ShutdownRequestedError, BaseException)

    from acp_services.entrypoints import mcp as mcp_mod

    class _Server(mcp_mod.McpServer):
        def _tools_call(self, request):  # type: ignore[override]
            raise _ShutdownRequestedError(2)

    server = _Server(backends=None)  # type: ignore[arg-type]
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": mcp_mod.McpTool.CAPABILITIES, "arguments": {}},
    }
    with pytest.raises(_ShutdownRequestedError):
        server.handle(request)


def test_shutdown_watchdog_is_armed_for_swallowed_signals() -> None:
    """A recorded shutdown signal must still end the process if swallowed.

    The watchdog thread logs the same line and exits after the grace period; the
    test only asserts it is a daemon thread started with the loop (a real exit
    would kill the test process).
    """
    import os
    import threading

    from acp_services.entrypoints import mcp_stdio

    transport = mcp_stdio.JsonRpcLineTransport(
        object(),  # the watchdog never touches the server
        stdin=open(os.devnull, "rb"),
        stdout=open(os.devnull, "wb"),
    )
    assert transport._shutdown_signal is None
    transport._start_shutdown_watchdog()
    watchdog = next(
        thread
        for thread in threading.enumerate()
        if thread.name == "mcp-shutdown-watchdog"
    )
    assert watchdog.daemon is True
    transport._finished = True
