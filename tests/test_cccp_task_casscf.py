"""cccp CASSCF task core tests (plan todo 22 — independent of ACP)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from cccp.backends.base import QCResult
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import casscf_spec_from_dict
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    CasscfOptions,
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import CasscfPayload, casscf_payload_from_multireference
from cccp.calculation.tasks.casscf import run_casscf

_COORDS = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.4))
_SYMBOLS = ("C", "C")

_MULTIREF_METADATA_KEYS = {
    "active_electrons",
    "active_orbitals",
    "multiplicity",
    "nroots",
    "state_weights",
    "active_orbital_indices",
    "orbital_source",
    "dynamic_correlation",
    "active_space_signature",
    "casscf_energy_hartree",
    "nevpt2_correction_hartree",
    "correlated_energy_hartree",
    "natural_occupations",
    "nevpt2_roots",
    "converged",
}

_NEVPT2_ROOTS = [
    {
        "root": 0,
        "multiplicity": 1,
        "casscf_energy_hartree": -108.9812345678,
        "nevpt2_correction_hartree": -0.123456789,
        "correlated_energy_hartree": -109.1046913568,
    },
    {
        "root": 1,
        "multiplicity": 1,
        "casscf_energy_hartree": -108.8123456789,
        "nevpt2_correction_hartree": -0.1111111111,
        "correlated_energy_hartree": -108.92345679,
    },
]


class _RecordingBackend:
    name = "orca"

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def casscf(
        self,
        coordinates: Any,
        symbols: Any,
        *,
        charge: int,
        multiplicity: int,
        output_dir: Path | None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _spec_options(**overrides: Any) -> CasscfOptions:
    payload: dict[str, Any] = {
        "active_electrons": 2,
        "active_orbitals": 2,
        "dynamic_correlation": "sc_nevpt2",
    }
    payload.update(overrides)
    return CasscfOptions(spec=casscf_spec_from_dict(payload))


def _request(
    *,
    options: CasscfOptions | None = None,
    output_dir: Path | None = None,
    backend: str | None = "orca",
    task: TaskKind = TaskKind.CASSCF,
) -> TaskRequest:
    return TaskRequest(
        task=task,
        structure=StructureInput(coordinates=_COORDS, symbols=_SYMBOLS),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="casscf"),
        backend=backend,
        options=options,
        output_dir=output_dir,
    )


def _casscf_response(**parsed: Any) -> QCResult:
    body: dict[str, Any] = {
        "casscf_energy_hartree": -108.9888864,
        "nevpt2_correction_hartree": -0.15802909,
        "correlated_energy_hartree": -109.14691549,
        "natural_occupations": [1.98123, 1.95211, 0.04789, 0.01877],
        "nevpt2_roots": [],
        "converged": True,
    }
    body.update(parsed)
    return QCResult(
        success=True,
        energy=-109.14691549,
        symbols=list(_SYMBOLS),
        converged=True,
        metadata={"casscf": body},
    )


# ── independent cccp call ──────────────────────────────────────────────


def test_independent_cccp_call_returns_payload_and_legacy_metadata(tmp_path: Path) -> None:
    backend = _RecordingBackend(_casscf_response(nevpt2_roots=_NEVPT2_ROOTS))
    result = run_casscf(
        _request(options=_spec_options(), output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )
    assert result.status == "completed"
    assert result.complete is True
    assert result.energy_hartree == pytest.approx(-109.14691549)

    call = backend.calls[-1]
    assert call["active_electrons"] == 2
    assert call["active_orbitals"] == 2
    assert call["dynamic_correlation"] == "sc_nevpt2"
    assert call["nroots"] == 1
    assert call["frozen_core"] is True

    multiref = result.metadata["multireference"]
    assert set(multiref) == _MULTIREF_METADATA_KEYS
    assert multiref["active_electrons"] == 2
    assert multiref["nevpt2_correction_hartree"] == pytest.approx(-0.15802909)

    payload = result.payload
    assert isinstance(payload, CasscfPayload)
    assert payload.root_energies == pytest.approx(
        (-108.9812345678, -108.8123456789)
    )
    assert payload.nevpt2_energies == pytest.approx((-109.1046913568, -108.92345679))
    assert payload.natural_occupations == pytest.approx(
        (1.98123, 1.95211, 0.04789, 0.01877)
    )
    assert payload.active_space == casscf_spec_from_dict(
        {"active_electrons": 2, "active_orbitals": 2, "dynamic_correlation": "sc_nevpt2"}
    ).active_space_signature()

    active_space_file = tmp_path / "active_space.json"
    assert active_space_file.is_file()
    recorded = json.loads(active_space_file.read_text(encoding="utf-8"))
    assert recorded["nevpt2_correction_hartree"] == pytest.approx(-0.15802909)
    assert (tmp_path / "natural_occupations.json").is_file()


def test_scalar_fallback_payload_without_roots(tmp_path: Path) -> None:
    backend = _RecordingBackend(_casscf_response())
    result = run_casscf(
        _request(options=_spec_options(), output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )
    payload = result.payload
    assert isinstance(payload, CasscfPayload)
    assert payload.root_energies == pytest.approx((-108.9888864,))
    assert payload.nevpt2_energies == pytest.approx((-109.14691549,))


# ── payload mapping (both recorded metadata shapes) ────────────────────


def test_payload_mapping_production_shape() -> None:
    multiref = {
        "casscf_energy_hartree": -108.9888864,
        "correlated_energy_hartree": -109.14691549,
        "natural_occupations": [1.99, 0.01],
        "nevpt2_roots": _NEVPT2_ROOTS,
        "active_space_signature": '{"nel":2}',
    }
    payload = casscf_payload_from_multireference(multiref)
    assert payload.root_energies == pytest.approx((-108.9812345678, -108.8123456789))
    assert payload.nevpt2_energies == pytest.approx((-109.1046913568, -108.92345679))
    assert payload.natural_occupations == pytest.approx((1.99, 0.01))
    assert payload.active_space == '{"nel":2}'

    scalar_only = casscf_payload_from_multireference(
        {"casscf_energy_hartree": -1.5, "correlated_energy_hartree": -1.6}
    )
    assert scalar_only.root_energies == (-1.5,)
    assert scalar_only.nevpt2_energies == (-1.6,)
    assert scalar_only.active_space == ""

    empty = casscf_payload_from_multireference({})
    assert empty == CasscfPayload()


def test_payload_mapping_rebuild_shape_wins() -> None:
    multiref = {
        "root_energies": [-1.0, -2.0],
        "natural_occupations": [1.5, 0.5],
        "nevpt2_energies": [-1.1, -2.1],
        "active_space": "sig",
        "nevpt2_roots": _NEVPT2_ROOTS,
        "casscf_energy_hartree": -99.0,
    }
    payload = casscf_payload_from_multireference(multiref)
    assert payload.root_energies == (-1.0, -2.0)
    assert payload.nevpt2_energies == (-1.1, -2.1)
    assert payload.natural_occupations == (1.5, 0.5)
    assert payload.active_space == "sig"


# ── orbital_selection (T11 D5: CASSCFSpec owns the field) ──────────────


def test_orbital_selection_property_and_round_trip() -> None:
    options = _spec_options(orbital_selection="energy_window")
    assert options.orbital_selection == "energy_window"
    assert options.spec.orbital_selection == "energy_window"
    round_tripped = CasscfOptions.from_dict(options.to_dict())
    assert round_tripped == options
    assert round_tripped.orbital_selection == "energy_window"
    assert _spec_options().orbital_selection == "manual"


# ── envelope + structured failures ─────────────────────────────────────


def test_missing_options_raises_task_input_error_with_active_message() -> None:
    with pytest.raises(TaskInputError, match="active"):
        run_casscf(_request(options=None), context=TaskContext(backend=_RecordingBackend(None)))


def test_wrong_task_and_wrong_options_rejected() -> None:
    with pytest.raises(TaskInputError):
        run_casscf(
            _request(options=_spec_options(), task=TaskKind.SINGLEPOINT),
            context=TaskContext(backend=_RecordingBackend(None)),
        )


def test_non_orca_backend_returns_structured_failure() -> None:
    result = run_casscf(
        _request(options=_spec_options(), backend="xtb"),
        context=TaskContext(backend=_RecordingBackend(None)),
    )
    assert result.status == "failed"
    assert result.payload is None
    assert any("only supported on the ORCA backend" in e for e in result.errors)
    assert any("xtb" in e for e in result.errors)


def test_spec_validation_errors_return_structured_failure() -> None:
    backend = _RecordingBackend(_casscf_response())
    result = run_casscf(
        _request(options=_spec_options(active_electrons=6, active_orbitals=2)),
        context=TaskContext(backend=backend),
    )
    assert result.status == "failed"
    assert result.errors
    assert any("cannot exceed" in e for e in result.errors)
    assert backend.calls == []


def test_no_energy_returns_failed() -> None:
    response = QCResult(success=True, energy=None, symbols=list(_SYMBOLS), metadata={"casscf": {}})
    result = run_casscf(
        _request(options=_spec_options()),
        context=TaskContext(backend=_RecordingBackend(response)),
    )
    assert result.status == "failed"
    assert any("no energy" in e for e in result.errors)


def test_backend_exception_returns_structured_failure() -> None:
    result = run_casscf(
        _request(options=_spec_options()),
        context=TaskContext(backend=_RecordingBackend(OSError("boom"))),
    )
    assert result.status == "failed"
    assert result.errors == ("boom",)
    assert result.error_kind is not None


# ── isolation: no acp imports in the cccp stations ─────────────────────


def test_cccp_casscf_stations_never_import_the_acp_package() -> None:
    root = Path(__file__).resolve().parent.parent / "src"
    for relative in (
        "cccp/calculation/tasks/casscf.py",
        "cccp/calculation/tasks/thermochemistry.py",
        "cccp/calculation/results.py",
        "cccp/calculation/requests.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("acp"), f"{relative}:{node.lineno}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("acp"), f"{relative}:{node.lineno}"
