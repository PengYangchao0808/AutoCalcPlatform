"""Tests that _handle_pessearch always writes a terminal state.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from acp.core.workflow import WorkflowResult


def _make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """Build a minimal Namespace for _handle_pessearch."""
    defaults = {
        "log_level": "INFO",
        "output": str(tmp_path),
        "config": None,
        "nproc": None,
        "mem": None,
        "reaction": None,
        "input_xyz": None,
        "from_artifact": None,
        "from_job": None,
        "from_manifest": None,
        "mode": "bond_length_scan",
        "coordinates": None,
        "strategy": None,
        "charge": 0,
        "multiplicity": 1,
        "scan_config": None,
        "source_type": None,
        "xyz_text": None,
        "asset_path": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _read_state(tmp_path: Path) -> dict[str, Any]:
    state_path = tmp_path / "state.json"
    assert state_path.is_file(), f"state.json not written at {state_path}"
    return json.loads(state_path.read_text(encoding="utf-8"))


def _manifest_args(tmp_path: Path) -> tuple[argparse.Namespace, Path]:
    """Create args that hit the from_manifest branch (needs a real file)."""
    manifest = tmp_path / "confsearch_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    args = _make_args(tmp_path, from_manifest=str(manifest))
    return args, manifest


class TestFinalizeSuccess:
    def test_state_completed_and_all_stages_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: WorkflowResult(
                status="completed",
                metadata={
                    "ts_candidates": 2,
                    "int_candidates": 1,
                    "manifest_path": "/fake/manifest.json",
                },
            ),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 0
        state = _read_state(tmp_path)
        assert state["status"] == "completed"
        for name, info in state["stages"].items():
            assert info["status"] == "completed", f"stage {name!r} not completed"

    def test_finalize_stage_is_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: WorkflowResult(
                status="completed",
                metadata={"manifest_path": "/x"},
            ),
        )

        from acp.cli import _handle_pessearch

        _handle_pessearch(args)

        state = _read_state(tmp_path)
        assert state["stages"]["finalize"]["status"] == "completed"


class TestFinalizeFailure:
    def test_state_failed_on_workflow_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: WorkflowResult(
                status="failed",
                error="PES_E_COORD: bad coordinate",
            ),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 2
        state = _read_state(tmp_path)
        assert state["status"] == "failed"

    def test_state_failed_on_generic_workflow_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: WorkflowResult(status="failed", error="something broke"),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 1
        state = _read_state(tmp_path)
        assert state["status"] == "failed"

    def test_state_failed_on_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 1
        state = _read_state(tmp_path)
        assert state["status"] == "failed"

    def test_state_failed_on_pes_e_manifest_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("PES_E_MANIFEST: missing")),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 2
        state = _read_state(tmp_path)
        assert state["status"] == "failed"


class TestFinalizeEarlyValidation:
    def test_state_failed_on_missing_input_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _make_args(
            tmp_path,
            input_xyz=str(tmp_path / "nonexistent.xyz"),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 2
        state = _read_state(tmp_path)
        assert state["status"] == "failed"

    def test_state_failed_on_missing_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _make_args(
            tmp_path,
            from_manifest=str(tmp_path / "nonexistent_manifest.json"),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 2
        state = _read_state(tmp_path)
        assert state["status"] == "failed"

    def test_state_failed_on_no_input_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = _make_args(tmp_path)

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 2
        state = _read_state(tmp_path)
        assert state["status"] == "failed"


class TestFinalizeInterrupt:
    def test_state_failed_on_keyboard_interrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args, _ = _manifest_args(tmp_path)
        monkeypatch.setattr(
            "acp.workflows.pes_search.run_pes_search",
            lambda **kw: (_ for _ in ()).throw(KeyboardInterrupt),
        )

        from acp.cli import _handle_pessearch

        rc = _handle_pessearch(args)

        assert rc == 130
        state = _read_state(tmp_path)
        assert state["status"] == "failed"
