"""Stage-8 report/manifest path derivation + existence checks (todo 22, gap §12.1).

Before the fix the emission hardcoded ``RESULT/reports/{plot.name}`` for
``result_summary.json`` and ``reports/{plot.name}`` for the manifest, while
``write_all_reports`` actually writes PNGs under ``reports/plots/`` — every
registered plot path pointed at a non-existent file (phantom entries) and a
meaningless ``try/except ValueError`` wrapped a plain f-string append.

Policy under test (documented choice): artifacts missing on disk are NOT
registered and an explicit warning is logged — no phantom entries.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.models import ConformerShielding

SPECTRUM = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"


def _shielding_result(shieldings: Mapping[int, Mapping[str, Any]]) -> Any:
    """Build a completed ``run_nmr_shielding`` TaskResult with canned shieldings."""
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import NmrShielding, NmrShieldingPayload, TaskResult

    payload = NmrShieldingPayload(
        shieldings={
            int(index): NmrShielding(
                symbol=str(values.get("symbol", "")),
                isotropic=float(values.get("isotropic", 0.0)),
            )
            for index, values in shieldings.items()
        }
    )
    return TaskResult(
        task=TaskKind.NMR_SHIELDING,
        status="completed",
        complete=True,
        payload=payload,
    )


def _make_structure() -> Structure:
    return Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H", "H", "H"],
        coordinates=np.array([(0.0, 0.0, 0.0)] * 5, dtype=float),
    )


def _ensemble_with_shieldings(
    structure: Structure, shieldings: Mapping[int, Mapping[str, str | float]]
) -> StructureEnsemble:
    """Build a StructureEnsemble whose .data holds pre-computed shieldings."""
    normalized: dict[int, dict[str, object]] = {i: dict(v) for i, v in shieldings.items()}
    ens = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    ens.data = [ConformerShielding("conf_000", 1.0, normalized)]
    return ens


def _run_two_candidate(tmp_path: Path, extra_plots: tuple[str, ...] = ()) -> Any:
    """Run the full NMR pipeline (stages 0–8) in *tmp_path*.

    ``extra_plots`` injects additional (non-existent) plot paths into the
    stage-8 ``write_all_reports`` result to exercise the existence gate.
    """
    structure = _make_structure()
    sh_a = {
        0: {"symbol": "C", "isotropic": 188.452125 - 40.0},
        1: {"symbol": "H", "isotropic": 32.1243166667 - 4.0},
        2: {"symbol": "H", "isotropic": 32.1243166667 - 3.0},
        3: {"symbol": "H", "isotropic": 32.1243166667 - 1.0},
        4: {"symbol": "H", "isotropic": 32.1243166667 - 0.0},
    }
    sh_b = {
        0: {"symbol": "C", "isotropic": 188.452125 - 30.0},
        1: {"symbol": "H", "isotropic": 32.1243166667 - 9.0},
        2: {"symbol": "H", "isotropic": 32.1243166667 - 8.0},
        3: {"symbol": "H", "isotropic": 32.1243166667 - 6.0},
        4: {"symbol": "H", "isotropic": 32.1243166667 - 5.0},
    }
    ens_a = _ensemble_with_shieldings(structure, sh_a)
    ens_b = _ensemble_with_shieldings(structure, sh_b)

    import acp.workflows.nmr as nmr_workflow

    real_write = nmr_workflow.write_all_reports

    def fake_write(report: Any, output_dir: Path) -> Any:
        paths = real_write(report, output_dir)
        for name in extra_plots:
            paths["plots"].append(Path(output_dir) / "plots" / name)
        return paths

    write_patch = (
        patch.object(nmr_workflow, "write_all_reports", side_effect=fake_write)
        if extra_plots
        else nullcontext()
    )
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            side_effect=[_shielding_result(sh_a), _shielding_result(sh_b)],
        ),
        write_patch,
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        return run_nmr_analysis(
            input_sources=["CCO", "CCO"],
            spectrum=SPECTRUM,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=[ens_a, ens_b],
            error_model="placeholder-student-t",
        )


def test_manifest_products_exist_on_disk(tmp_path: Path) -> None:
    """Every ``RESULT/<path>`` in result_manifest.json must exist (gap §12.1)."""
    result = _run_two_candidate(tmp_path)
    assert result.status == "completed", result.error

    manifest_path = tmp_path / "RESULT" / "result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    products = manifest["products"]
    assert products, "manifest registered no products"

    for product in products:
        rel = product["path"]
        # canonical form: relative to RESULT/, no escape, no absolute path
        assert not rel.startswith("/"), rel
        assert ".." not in Path(rel).parts, rel
        assert (tmp_path / "RESULT" / rel).is_file(), f"phantom manifest entry: RESULT/{rel}"

    ids = {p["id"] for p in products}
    assert "nmr_report" in ids
    report_entry = next(p for p in products if p["id"] == "nmr_report")
    assert report_entry["path"] == "reports/nmr_report.json"

    plots = [p for p in products if p["id"].startswith("plot_")]
    assert plots, "expected at least one plot product"
    for product in plots:
        assert product["path"].startswith("reports/plots/"), product["path"]


def test_result_summary_products_exist_on_disk(tmp_path: Path) -> None:
    """result_summary.json products are RESULT-rooted and point at real files."""
    result = _run_two_candidate(tmp_path)
    assert result.status == "completed", result.error

    summary = json.loads((tmp_path / "result_summary.json").read_text(encoding="utf-8"))
    products = summary["products"]
    assert products, "result_summary registered no products"

    for product in products:
        rel = product["path"]
        assert rel.startswith("RESULT/"), rel
        assert not rel.startswith("//")
        assert ".." not in Path(rel).parts, rel
        assert (tmp_path / rel).is_file(), f"phantom summary entry: {rel}"

    plot_entries = [p for p in products if p["kind"] == "plot"]
    assert plot_entries, "expected plot entries in result_summary"
    for product in plot_entries:
        assert product["path"].startswith("RESULT/reports/plots/"), product["path"]


def test_missing_plot_is_not_registered(tmp_path: Path, caplog) -> None:
    """A plot path with no file on disk is skipped with an explicit warning."""
    with caplog.at_level(logging.WARNING, logger="acp.workflows.nmr"):
        result = _run_two_candidate(tmp_path, extra_plots=("phantom_missing.png",))
    assert result.status == "completed", result.error

    manifest = json.loads(
        (tmp_path / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    manifest_paths = [p["path"] for p in manifest["products"]]
    assert not any("phantom" in p for p in manifest_paths), manifest_paths
    # everything still registered is real
    for rel in manifest_paths:
        assert (tmp_path / "RESULT" / rel).is_file(), rel
    # real plots remain registered with the plots/ prefix
    real_plots = [p for p in manifest["products"] if p["id"].startswith("plot_")]
    assert real_plots
    for product in real_plots:
        assert product["path"].startswith("reports/plots/"), product["path"]

    summary = json.loads((tmp_path / "result_summary.json").read_text(encoding="utf-8"))
    summary_paths = [p["path"] for p in summary["products"]]
    assert not any("phantom" in p for p in summary_paths), summary_paths

    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert any(
        "phantom_missing.png" in message and "not registered" in message for message in warnings
    ), warnings
