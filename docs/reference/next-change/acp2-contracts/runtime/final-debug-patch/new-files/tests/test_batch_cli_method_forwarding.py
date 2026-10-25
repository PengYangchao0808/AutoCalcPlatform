"""Ordinary batch CLI common methods survive migration into typed role options."""

from __future__ import annotations

from types import SimpleNamespace

from acp.calculations.batch.options import BatchMethodOptions
from acp.cli import main


def test_canonical_common_fields_win_over_legacy_aliases_and_keep_role_priority() -> None:
    options = BatchMethodOptions.from_method_dict(
        {
            "optimization_method": "PBE0",
            "optimization_basis": "def2-SVP",
            "functional": "B3LYP",
            "basis": "def2-TZVP",
            "minimum_method": "r2SCAN-3c",
            "transition_state_basis": "def2-TZVPP",
        }
    )
    assert options.for_role(False) == ("r2SCAN-3c", "def2-SVP")
    assert options.for_role(True) == ("PBE0", "def2-TZVPP")


def test_ordinary_cli_forwards_common_method_basis_and_role_overrides(
    tmp_path, monkeypatch
) -> None:
    received = []

    def science(source, **kwargs):
        received.append(kwargs["methods"])
        return SimpleNamespace(status="completed", metadata={}, error=None)

    monkeypatch.setattr("acp.workflows.batch_optimize.run_batch_optimize", science)
    source = tmp_path / "water.xyz"
    source.write_text("3\nwater\nO 0 0 0\nH 0.75 0 0.5\nH -0.75 0 0.5\n")
    assert (
        main(
            [
                "run",
                "BatchOptimize",
                "--items-file",
                str(source),
                "--profile",
                "opt_only",
                "--method",
                "PBE0",
                "--basis",
                "def2-SVP",
                "--minimum-method",
                "r2SCAN-3c",
                "--transition-state-basis",
                "def2-TZVPP",
                "--opt-max-iter",
                "9",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    assert len(received) == 1
    assert received[0].optimization_method == "r2SCAN-3c"
    assert received[0].for_role(False) == ("r2SCAN-3c", "def2-SVP")
    assert received[0].for_role(True) == ("PBE0", "def2-TZVPP")
    assert received[0].opt_max_iter == 9
