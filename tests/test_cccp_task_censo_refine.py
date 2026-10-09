"""cccp censo_refine task tests (plan todo 43 — P2 execution B).

Independent cccp calls (no ACP imports) with a fake CENSO backend; record
identity / free-energy / weight shapes are pinned against
``tests/baseline/cccp_calculation_goldens/censo_records.json``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    CensoLevelOverride,
    CensoRefineOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import CensoRefinePayload, ErrorKind
from cccp.calculation.tasks.censo_refine import run_censo_refine
from cccp.qc.interfaces.censo import CensoConformerRecord, CensoInterface, CensoRunResult, part_index
from cccp.qc.translation import render_censo_template_lines

GOLDEN = Path(__file__).parent / "baseline" / "cccp_calculation_goldens" / "censo_records.json"

_ENSEMBLE_NAME = f"{part_index('screening')}_SCREENING.xyz"


class FakeCensoBackend:
    name = "censo"

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def refine_ensemble(self, ensemble_xyz: Path, output_dir: Path, **kwargs: Any) -> Any:
        self.calls.append({"ensemble_xyz": Path(ensemble_xyz), "output_dir": Path(output_dir), **kwargs})
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def golden(tmp_path: Path) -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture()
def run_result(tmp_path: Path, golden: dict[str, Any]) -> CensoRunResult:
    json_path = tmp_path / "censo.json"
    xyz_path = tmp_path / "censo.xyz"
    json_path.write_text(json.dumps(golden["inputs"]["json"]), encoding="utf-8")
    xyz_path.write_text(golden["inputs"]["xyz"], encoding="utf-8")
    records = CensoInterface({}).parse_censo_json(json_path, xyz_path)
    result = CensoRunResult(
        preset="screening",
        records=records,
        final_part="screening",
        work_dir=tmp_path,
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


def _ensemble_file(tmp_path: Path) -> Path:
    ensemble = tmp_path / "ensemble.xyz"
    ensemble.write_text("3\nCONF1\nC 0 0 0\nH 0 0 1.089\nH 1.026719 0 -0.362999\n", encoding="utf-8")
    return ensemble


def _request(tmp_path: Path, options: CensoRefineOptions | None = None) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.CENSO_REFINE,
        structure=StructureInput(path=_ensemble_file(tmp_path)),
        options=options if options is not None else CensoRefineOptions(),
        output_dir=tmp_path / "out",
    )


def _context(tmp_path: Path, backend: FakeCensoBackend, **kwargs: Any) -> TaskContext:
    return TaskContext(backend=backend, input_base=tmp_path, **kwargs)


# ── success: record identity + free energy + weights vs goldens ────────


def test_success_maps_record_identity_free_energies_and_weights(
    tmp_path: Path, golden: dict[str, Any], run_result: CensoRunResult
) -> None:
    (tmp_path / _ENSEMBLE_NAME).write_text("refined ensemble", encoding="utf-8")
    backend = FakeCensoBackend(result=run_result)
    options = CensoRefineOptions(
        preset="censo-light",
        level_overrides=(
            CensoLevelOverride(part="refinement", func="dlpno-ccsd(t)", basis="def2-TZVPP", threshold=0.99),
        ),
        temperature_k=298.15,
    )
    result = run_censo_refine(_request(tmp_path, options), context=_context(tmp_path, backend))

    assert result.status == "completed"
    assert result.complete is True
    assert result.error_kind is None
    payload = result.payload
    assert isinstance(payload, CensoRefinePayload)

    expected = {row["conf_id"]: row for row in golden["records"]}
    assert len(payload.records) == len(expected)
    for record in payload.records:
        row = expected[record.conf_id]
        assert record.frame_index == row["frame_index"]
        assert record.energy_hartree == pytest.approx(row["energy"])
        assert record.free_energy_hartree == pytest.approx(row["gtot"])
        assert record.weight == pytest.approx(golden["boltzmann_weights"][record.conf_id])

    assert payload.refined_ensemble_ref is not None
    assert payload.refined_ensemble_ref.path == tmp_path / _ENSEMBLE_NAME
    assert payload.refined_ensemble_ref.type == "refined_ensemble"

    call = backend.calls[0]
    assert call["preset"] == "censo-light"
    assert call["temperature"] == 298.15
    assert call["part_overrides"] == {
        "refinement": {"func": "dlpno-ccsd(t)", "basis": "def2-TZVPP", "threshold": 0.99}
    }
    assert call["charge"] == 0
    assert call["multiplicity"] == 1


# ── template text: translation layer only ─────────────────────────────


def test_template_lines_are_rendered_by_the_translation_layer(
    tmp_path: Path, run_result: CensoRunResult
) -> None:
    (tmp_path / _ENSEMBLE_NAME).write_text("refined ensemble", encoding="utf-8")
    backend = FakeCensoBackend(result=run_result)
    context = _context(
        tmp_path,
        backend,
        capability_extras={
            "part_template_extras": {"refinement": ["RIJCOSX", "def2-TZVPP/C", "VeryTightSCF"]},
            "include_refinement": True,
        },
    )
    result = run_censo_refine(_request(tmp_path), context=context)

    assert result.status == "completed"
    call = backend.calls[0]
    assert call["part_templates"] == {
        "refinement": render_censo_template_lines(["RIJCOSX", "def2-TZVPP/C", "VeryTightSCF"])
    }
    assert call["part_templates"]["refinement"] == ["! RIJCOSX def2-TZVPP/C VeryTightSCF"]
    assert call["include_refinement"] is True


def test_preassembled_template_text_is_rejected(tmp_path: Path) -> None:
    backend = FakeCensoBackend(result=CensoRunResult(preset="screening", records=[]))
    context = _context(
        tmp_path,
        backend,
        capability_extras={"part_templates": {"refinement": ["! RIJCOSX"]}},
    )
    with pytest.raises(TaskInputError, match="translation layer"):
        run_censo_refine(_request(tmp_path), context=context)
    assert backend.calls == []


# ── three-state semantics ─────────────────────────────────────────────


def test_partial_failure_keeps_valid_rows_at_original_frame_index(
    tmp_path: Path, run_result: CensoRunResult
) -> None:
    (tmp_path / _ENSEMBLE_NAME).write_text("refined ensemble", encoding="utf-8")
    run_result.records.append(
        CensoConformerRecord(
            conf_id="CONF_UNMAPPED",
            frame_index=-1,
            energy=0.0,
            gsolv=0.0,
            grrho=0.0,
            gtot=0.0,
            coordinates=np.zeros((0, 3)),
            symbols=[],
        )
    )
    backend = FakeCensoBackend(result=run_result)
    result = run_censo_refine(_request(tmp_path), context=_context(tmp_path, backend))

    assert result.status == "failed"
    assert result.complete is False
    payload = result.payload
    assert isinstance(payload, CensoRefinePayload)
    assert [r.conf_id for r in payload.records] == ["CONF1", "CONF2"]
    assert [r.frame_index for r in payload.records] == [0, 1]
    assert any("CONF_UNMAPPED" in error for error in result.errors)


def test_partial_failure_when_refined_ensemble_missing(
    tmp_path: Path, run_result: CensoRunResult
) -> None:
    backend = FakeCensoBackend(result=run_result)
    result = run_censo_refine(_request(tmp_path), context=_context(tmp_path, backend))

    assert result.status == "failed"
    assert result.complete is False
    payload = result.payload
    assert isinstance(payload, CensoRefinePayload)
    assert payload.refined_ensemble_ref is None
    assert {r.frame_index for r in payload.records} == {0, 1}
    assert any("refined ensemble" in error for error in result.errors)


def test_empty_result_is_backend_failure(tmp_path: Path) -> None:
    empty = CensoRunResult(preset="screening", records=[], final_part="screening", work_dir=tmp_path)
    backend = FakeCensoBackend(result=empty)
    result = run_censo_refine(_request(tmp_path), context=_context(tmp_path, backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.payload is None


def test_backend_exception_is_structured_failure(tmp_path: Path) -> None:
    backend = FakeCensoBackend(error=RuntimeError("censo exploded"))
    result = run_censo_refine(_request(tmp_path), context=_context(tmp_path, backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert "censo exploded" in result.errors[0]


def test_wrong_task_kind_is_rejected(tmp_path: Path) -> None:
    request = TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("H",)),
    )
    with pytest.raises(TaskInputError, match="censo_refine"):
        run_censo_refine(request, context=_context(tmp_path, FakeCensoBackend()))


# ── task module independence (no ACP) ─────────────────────────────────


def test_task_module_never_imports_acp() -> None:
    source = (Path(__file__).parent.parent / "src/cccp/calculation/tasks/censo_refine.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("acp") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("acp")
