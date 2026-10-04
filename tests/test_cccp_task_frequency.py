"""cccp frequency task core — plan todo 19 (frequency science in cccp).

Pure-cccp tests (never import ``acp``): an independent call returns
frequencies / vibration vectors / IR intensities on the typed payload with
no ACP interpretation, ``n_imaginary`` is derived from the returned
frequency list, log artifacts are referenced, and
``FrequencyOptions.numerical`` (unconfirmed backend support) does not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    FrequencyOptions,
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, FrequencyPayload
from cccp.calculation.tasks.frequency import run_frequency

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "structure_viewer"
FULL_MODES_FIXTURE = FIXTURES / "orca_freq_modes_full.txt"


class _RecordingBackend:
    """Minimal frequency capability fake recording call kwargs."""

    name = "orca"

    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def frequency(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        from cccp.qc.interfaces.base import QCResult

        self.calls.append(
            {
                "coordinates": np.asarray(coordinates, dtype=float),
                "symbols": list(symbols),
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                "kwargs": dict(kwargs),
            }
        )
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return QCResult(
            success=True,
            energy=-76.4,
            symbols=list(symbols),
            converged=True,
            frequencies=[100.0, 200.0],
            has_frequencies=True,
        )


def _request(
    *,
    backend: str | None = "orca",
    level: MethodSpec | None = None,
    options: FrequencyOptions | None = None,
    n_atoms: int = 3,
) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.FREQUENCY,
        structure=StructureInput(
            coordinates=tuple((0.0, float(i), 0.0) for i in range(n_atoms)),
            symbols=tuple(("O", "H", "H")[:n_atoms]),
        ),
        charge=0,
        multiplicity=1,
        level=level or MethodSpec(method="r2SCAN-3c"),
        backend=backend,
        options=options,
    )


def _log_qc_result(tmp_path: Path, text: str) -> Any:
    from cccp.qc.interfaces.base import QCResult

    log_path = tmp_path / "freq.log"
    log_path.write_text(text, encoding="utf-8")
    return QCResult(
        success=True,
        energy=-76.4,
        symbols=["O", "H", "H"],
        converged=True,
        frequencies=[-797.72, -791.36, 1411.55],
        has_frequencies=True,
        freq_log_file=log_path,
    )


# ── happy path: independent cccp call gets the science directly ────────


def test_independent_cccp_call_returns_frequencies_vectors_and_ir(tmp_path: Path) -> None:
    """Frequencies / vibration vectors / IR intensities with no ACP help."""
    text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
    backend = _RecordingBackend([_log_qc_result(tmp_path, text)])
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    assert result.status == "completed"
    assert result.frequencies == (-797.72, -791.36, 1411.55)
    assert isinstance(result.payload, FrequencyPayload)

    analysis = result.payload.analysis
    assert analysis is not None
    # vibration vectors: 3 atoms × 3 components, physically nonzero displacements
    vectors = analysis.mode_vectors[6]
    assert len(vectors) == 3
    for atom_vector in vectors:
        assert len(atom_vector) == 3
        assert any(component != 0.0 for component in atom_vector)
    # IR intensities (km/mol) keyed by ORCA mode index
    assert analysis.mode_ir_intensities is not None
    assert analysis.mode_ir_intensities[6] == pytest.approx(66.542)
    assert analysis.mode_ir_intensities[8] == pytest.approx(81.914)
    # indexed frequency map keeps ORCA native indices incl. zero modes
    assert analysis.mode_frequencies[6] == pytest.approx(-797.72)
    assert analysis.mode_frequencies[0] == pytest.approx(0.0)
    assert set(analysis.mode_vectors) == set(analysis.mode_frequencies)
    # the log artifact is referenced (scientific artifact, not a platform product)
    assert result.payload.freq_log_ref is not None
    assert result.payload.freq_log_ref.type == "frequency_log"
    assert result.payload.freq_log_ref.path.name == "freq.log"
    # no platform product is written by the task
    assert not (tmp_path / "normal_modes.json").exists()


def test_n_imaginary_counted_from_returned_frequencies() -> None:
    """n_imaginary derives from the authoritative frequency list."""
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend()
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    assert result.status == "completed"
    assert result.frequencies == (100.0, 200.0)
    assert isinstance(result.payload, FrequencyPayload)
    assert result.payload.n_imaginary == 0

    negative = _RecordingBackend(
        [
            QCResult(
                success=True,
                energy=-76.4,
                symbols=["O", "H", "H"],
                converged=True,
                frequencies=[-120.0, 350.0],
                has_frequencies=True,
            )
        ]
    )
    negative_result = run_frequency(_request(), context=TaskContext(backend=negative))
    assert isinstance(negative_result.payload, FrequencyPayload)
    assert negative_result.payload.n_imaginary == 1


def test_missing_log_returns_frequencies_without_analysis(tmp_path: Path) -> None:
    """No readable log → frequencies kept, analysis None, step completed."""
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend(
        [
            QCResult(
                success=True,
                energy=-76.4,
                symbols=["O", "H", "H"],
                converged=True,
                frequencies=[100.0, 200.0],
                has_frequencies=True,
                freq_log_file=tmp_path / "nonexistent.log",
            )
        ]
    )
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    assert result.status == "completed"
    assert result.frequencies == (100.0, 200.0)
    assert isinstance(result.payload, FrequencyPayload)
    assert result.payload.analysis is None
    assert not (tmp_path / "normal_modes.json").exists()


def test_ir_intensities_none_without_ir_section(tmp_path: Path) -> None:
    """No IR SPECTRUM section → ir_intensities and mode_ir_intensities are None."""
    text = (
        "VIBRATIONAL FREQUENCIES\n"
        "-----------------------\n"
        "   0:      100.00 cm**-1\n"
        "   1:     -500.00 cm**-1\n"
    )
    backend = _RecordingBackend([_log_qc_result(tmp_path, text)])
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    analysis = result.payload.analysis  # type: ignore[union-attr]
    assert analysis is not None
    assert analysis.frequencies == (100.0, -500.0)
    assert analysis.imaginary_frequencies == (-500.0,)
    assert analysis.ir_intensities is None
    assert analysis.mode_ir_intensities is None
    assert analysis.mode_frequencies == {0: 100.0, 1: -500.0}


# ── failure semantics ──────────────────────────────────────────────────


def test_backend_exception_returns_structured_failure() -> None:
    backend = _RecordingBackend([RuntimeError("frequency exploded")])
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors == ("frequency exploded",)


def test_unsuccessful_qc_result_returns_failed_with_message() -> None:
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend(
        [QCResult(success=False, error_message="ORCA frequency calculation failed")]
    )
    result = run_frequency(_request(), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors == ("ORCA frequency calculation failed",)


# ── contract validation ────────────────────────────────────────────────


def test_request_validation_rejects_wrong_task() -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
        level=MethodSpec(method="HF"),
    )
    with pytest.raises(TaskInputError, match="run_frequency requires task 'frequency'"):
        run_frequency(request, context=TaskContext(backend=_RecordingBackend()))


def test_request_validation_rejects_wrong_options() -> None:
    from cccp.calculation.requests import SinglePointOptions

    request = TaskRequest(
        task=TaskKind.FREQUENCY,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
        level=MethodSpec(method="HF"),
        options=SinglePointOptions(),
    )
    with pytest.raises(TaskInputError, match="expected FrequencyOptions"):
        run_frequency(request, context=TaskContext(backend=_RecordingBackend()))


def test_frequency_options_carry_no_numerical_field() -> None:
    """``FrequencyOptions.numerical`` is removed (unconfirmed backend support)."""
    options = FrequencyOptions()
    assert not hasattr(options, "numerical")
    assert options.to_dict() == {}
    restored = FrequencyOptions.from_dict({"numerical": True})
    assert restored == options
