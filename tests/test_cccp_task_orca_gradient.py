"""cccp orca_gradient task tests (plan todo 43 — P2 execution B).

Independent cccp calls (no ACP imports) with a fake ORCA backend carrying
the typed ``SinglePointGradientResult``; the legacy
``pes2ts_orca_gradient_request_v1`` payload is mapped onto this request
shape by the ACP adapter (todo 24) before the call.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.backends.orca import (
    ORCA_GRADIENT_CONVENTION,
    ORCA_GRADIENT_UNIT,
    SinglePointGradientResult,
)
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    FRAGMENT_CONFLICT_RULES,
    BackendInputFragment,
    BackendInputKind,
    MethodSpec,
    OrcaGradientOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, OrcaGradientPayload
from cccp.calculation.tasks.orca_gradient import run_orca_gradient

_GEOMETRY = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.089))
_SYMBOLS = ("C", "H")
_GRADIENT = ((0.1, -0.2, 0.3), (-0.1, 0.2, -0.3))


class FakeOrcaBackend:
    name = "orca"

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def single_point_gradient(
        self,
        coordinates: Any,
        symbols: Any,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "coordinates": coordinates,
                "symbols": list(symbols),
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


def _gradient_result(
    tmp_path: Path,
    *,
    gradient: Any = _GRADIENT,
    success: bool = True,
    error_message: str | None = None,
) -> SinglePointGradientResult:
    output_file = tmp_path / "grad.inp"
    log_file = tmp_path / "grad.out"
    output_file.write_text("input", encoding="utf-8")
    log_file.write_text("log", encoding="utf-8")
    engrad = tmp_path / "grad.engrad"
    engrad.write_text("# engrad\n", encoding="utf-8")
    return SinglePointGradientResult(
        success=success,
        energy=-40.5,
        gradient=None if gradient is None else np.asarray(gradient, dtype=float),
        gradient_unit=ORCA_GRADIENT_UNIT,
        gradient_convention=ORCA_GRADIENT_CONVENTION,
        symbols=list(_SYMBOLS),
        coordinates=np.asarray(_GEOMETRY, dtype=float),
        gradient_source="engrad_file:grad.engrad",
        output_file=output_file,
        log_file=log_file,
        error_message=error_message,
        metadata={"route_extras": ["EnGrad"], "method": "wB97X-D4"},
    )


def _request(options: OrcaGradientOptions | None = None, **level: Any) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.ORCA_GRADIENT,
        structure=StructureInput(coordinates=_GEOMETRY, symbols=_SYMBOLS),
        level=MethodSpec(**level),
        options=options if options is not None else OrcaGradientOptions(),
    )


def _context(backend: FakeOrcaBackend, tmp_path: Path) -> TaskContext:
    return TaskContext(backend=backend, input_base=tmp_path)


def _fragment(kind: BackendInputKind, source: str, content: Any) -> BackendInputFragment:
    return BackendInputFragment(
        kind=kind,
        source=source,
        content=content,
        conflict_rule=FRAGMENT_CONFLICT_RULES[kind],
    )


# ── success: per-atom rows + unit/convention record identity ──────────


def test_success_maps_per_atom_gradient_rows(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(_gradient_result(tmp_path))
    result = run_orca_gradient(_request(), context=_context(backend, tmp_path))

    assert result.status == "completed"
    assert result.complete is True
    assert result.energy_hartree == pytest.approx(-40.5)
    payload = result.payload
    assert isinstance(payload, OrcaGradientPayload)
    assert payload.gradients == _GRADIENT
    assert payload.energy_hartree == pytest.approx(-40.5)
    assert payload.gradient_unit == ORCA_GRADIENT_UNIT
    assert payload.gradient_convention == ORCA_GRADIENT_CONVENTION
    assert [artifact.type for artifact in result.artifacts] == ["output", "log", "engrad"]
    call = backend.calls[0]
    assert call["symbols"] == list(_SYMBOLS)
    assert call["charge"] == 0
    assert call["multiplicity"] == 1


# ── fragment consumption (pes2ts mapping target) ─────────────────────


def test_backend_input_fragments_and_level_map_to_capability_kwargs(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(_gradient_result(tmp_path))
    options = OrcaGradientOptions(
        backend_inputs=(
            _fragment(BackendInputKind.ROUTE_EXTRAS, "route_extras", ("RIJCOSX", "VeryTightSCF")),
            _fragment(BackendInputKind.EXTRA_BLOCKS, "extra_blocks", ("%maxcore 4000",)),
            _fragment(BackendInputKind.OUTPUT_NAME, "output_name", "grad"),
        )
    )
    request = _request(options, method="wB97X-D4", basis="def2-TZVPP", scf="TightSCF")
    result = run_orca_gradient(request, context=_context(backend, tmp_path))

    assert result.status == "completed"
    call = backend.calls[0]
    assert call["route_extras"] == ["RIJCOSX", "VeryTightSCF"]
    assert call["extra_blocks"] == ["%maxcore 4000"]
    assert call["output_name"] == "grad"
    assert call["method"] == "wB97X-D4"
    assert call["basis"] == "def2-TZVPP"
    assert call["scf_convergence"] == "TightSCF"


def test_wrong_fragment_kind_is_rejected() -> None:
    with pytest.raises(TaskInputError, match="route_extras/extra_blocks/output_name"):
        OrcaGradientOptions(
            backend_inputs=(
                _fragment(BackendInputKind.PATH_INP_TEXT, "path_inp_text", "gfn 2"),
            )
        )


# ── three-state semantics: all-or-nothing gradient ────────────────────


def test_missing_gradient_rows_is_parse_failure(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(_gradient_result(tmp_path, gradient=None))
    result = run_orca_gradient(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.PARSE_FAILURE
    assert result.payload is None


def test_misaligned_gradient_rows_are_never_partial(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(_gradient_result(tmp_path, gradient=((0.1, -0.2, 0.3),)))
    result = run_orca_gradient(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.PARSE_FAILURE
    assert result.payload is None
    assert any("1 rows" in error for error in result.errors)


def test_backend_failure_is_structured(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(
        _gradient_result(tmp_path, success=False, error_message="ORCA gradient missing: no bound .engrad")
    )
    result = run_orca_gradient(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert "ORCA gradient missing" in result.errors[0]


def test_backend_exception_is_structured_failure(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(error=RuntimeError("orca crashed"))
    result = run_orca_gradient(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert "orca crashed" in result.errors[0]


def test_wrong_task_kind_is_rejected(tmp_path: Path) -> None:
    request = TaskRequest(
        task=TaskKind.CENSO_REFINE,
        structure=StructureInput(path=tmp_path / "ensemble.xyz"),
    )
    with pytest.raises(TaskInputError, match="orca_gradient"):
        run_orca_gradient(request, context=_context(FakeOrcaBackend(), tmp_path))


# ── task module independence (no ACP) ─────────────────────────────────


def test_task_module_never_imports_acp() -> None:
    source = (Path(__file__).parent.parent / "src/cccp/calculation/tasks/orca_gradient.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("acp") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("acp")
