"""cccp thermochemistry task tests (plan todo 22 — one Shermo launch contract)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

import cccp.qc.shermo_adapter as shermo_adapter
from acp.calculations.primitives.thermochemistry import (
    ThermochemistryCalculator,
)
from acp.calculations.primitives.thermochemistry import (
    run_thermochemistry as acp_run_thermochemistry,
)
from cccp.backends.external_backend import ExternalBackend
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    MethodSpec,
    TaskKind,
    TaskRequest,
    ThermochemistryOptions,
)
from cccp.calculation.results import ThermochemistryPayload
from cccp.calculation.tasks.thermochemistry import run_thermochemistry

_BASELINE_DELTA_HARTREE_298K = 0.003018804534102794


def _freq_log(tmp_path: Path, name: str = "frequency.log") -> Path:
    path = tmp_path / name
    path.write_text("frequency output", encoding="utf-8")
    return path


def _spy(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Patch the shared runner with a recording spy (writes the output file)."""
    state: dict[str, Any] = {"calls": [], "stacks": []}
    payload = values if values is not None else {
        "u_sum": -99.90,
        "h_sum": -99.88,
        "g_sum": -99.95,
        "g_conc": -99.94,
        "s_total": 0.0123,
    }

    def fake_run_shermo(**kwargs: Any) -> dict[str, float]:
        state["calls"].append(kwargs)
        state["stacks"].append(inspect.stack(0))
        output_file = Path(kwargs["output_file"])
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("Shermo summary", encoding="utf-8")
        return dict(payload)

    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)
    return state


def _request(freq_log: Path, **overrides: Any) -> TaskRequest:
    payload: dict[str, Any] = {
        "freq_log_path": freq_log,
        "sp_energy_hartree": -40.5,
        "temperature_k": 298.15,
        "pressure_atm": 1.0,
    }
    payload.update(overrides)
    return TaskRequest(
        task=TaskKind.THERMOCHEMISTRY,
        level=MethodSpec(),
        options=ThermochemistryOptions(**payload),
    )


# ── independent cccp call: units + standard-state contract ─────────────


def test_independent_cccp_call_maps_units_and_standard_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freq_log = _freq_log(tmp_path)
    state = _spy(monkeypatch)
    result = run_thermochemistry(
        _request(freq_log, standard_state="1M", scl_zpe=1.0, ilowfreq=2, imagreal=0, conc=1.0),
        context=TaskContext(),
    )
    assert result.status == "completed"
    assert len(state["calls"]) == 1
    call = state["calls"][0]
    assert call["temperature_k"] == 298.15
    assert call["pressure_atm"] == 1.0
    assert call["scl_zpe"] == 1.0
    assert call["ilowfreq"] == 2
    assert call["imagreal"] == 0
    assert call["conc"] == 1.0

    payload = result.payload
    assert isinstance(payload, ThermochemistryPayload)
    assert payload.enthalpy_hartree == pytest.approx(-99.88)
    assert payload.gibbs_hartree == pytest.approx(-99.94)
    assert payload.entropy_au == pytest.approx(0.0123)
    assert payload.gibbs_source == "g_conc"
    assert payload.standard_state == "1M"

    assert result.metadata["enthalpy_hartree"] == pytest.approx(-99.88)
    assert result.metadata["gibbs_hartree"] == pytest.approx(-99.94)
    assert result.metadata["entropy_au"] == pytest.approx(0.0123)
    assert result.metadata["selected_gibbs_source"] == "g_conc"
    assert result.metadata["standard_state"] == "1M"
    assert result.energy_hartree == pytest.approx(-40.5)
    assert [artifact.type for artifact in result.artifacts] == ["thermochemistry"]
    assert result.artifacts[0].path.is_file()


def test_exactly_one_shermo_launch_per_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _spy(monkeypatch)
    for index in range(2):
        run_thermochemistry(
            _request(_freq_log(tmp_path, f"freq{index}.log")),
            context=TaskContext(),
        )
        assert len(state["calls"]) == index + 1


# ── both entries share the single implementation ───────────────────────


def test_both_entries_share_impl_with_one_launch_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freq_log = _freq_log(tmp_path)
    state = _spy(monkeypatch)
    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)

    backend_result = ExternalBackend({}).thermochemistry(freq_log, output_dir=tmp_path / "b")
    assert backend_result.success is True
    assert len(state["calls"]) == 1

    task_result = run_thermochemistry(_request(freq_log), context=TaskContext())
    assert task_result.status == "completed"
    assert len(state["calls"]) == 2

    calc_result = ThermochemistryCalculator().compute(
        freq_log_path=freq_log,
        sp_energy_hartree=-40.5,
        temperature=298.15,
        pressure=1.0,
        standard_state="1atm",
    )
    assert calc_result.status == "completed"
    assert len(state["calls"]) == 3

    from acp.calculations.primitives import thermochemistry as acp_thermo_module
    from cccp.calculation.tasks import thermochemistry as task_module

    assert task_module.execute_shermo is shermo_adapter.execute_shermo
    assert acp_thermo_module.execute_shermo is shermo_adapter.execute_shermo
    assert ExternalBackend.thermochemistry is not None
    assert acp_run_thermochemistry is not run_thermochemistry


def test_acp_wrapper_entry_also_launches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.calculations.contracts import CalculationRequest, StructureArtifact

    freq_log = _freq_log(tmp_path)
    state = _spy(monkeypatch)
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "ignored.xyz"),
        method="",
        resources={
            "freq_log_path": str(freq_log),
            "sp_energy_hartree": -40.5,
            "temperature": 300.0,
            "pressure": 1.0,
            "standard_state": "1M",
        },
        workflow="test",
    )
    result = acp_run_thermochemistry(request)
    assert result.status == "completed"
    assert len(state["calls"]) == 1
    assert state["calls"][0]["temperature_k"] == 300.0
    assert result.metadata["standard_state"] == "1M"
    assert result.metadata["entropy_au"] == pytest.approx(0.0123)


# ── no task-entry reentry (call-stack probe + dependency graph) ────────


def test_no_task_entry_reentry_during_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freq_log = _freq_log(tmp_path)
    state = _spy(monkeypatch)
    run_thermochemistry(_request(freq_log), context=TaskContext())
    stack = state["stacks"][0]
    task_entries = [
        frame
        for frame in stack
        if frame.function in {"run_thermochemistry", "execute_thermochemistry"}
    ]
    assert len(task_entries) == 1, "the task entry must not re-enter during the Shermo launch"

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    ExternalBackend({}).thermochemistry(freq_log, output_dir=tmp_path / "b2")
    backend_stack = state["stacks"][1]
    assert not [
        frame
        for frame in backend_stack
        if frame.function in {"run_thermochemistry", "execute_thermochemistry"}
    ], "the backend entry must never route through the task entry"


def test_shared_impl_has_no_task_layer_dependency() -> None:
    root = Path(__file__).resolve().parent.parent / "src"
    for relative in (
        "cccp/qc/shermo_adapter.py",
        "cccp/qc/thermo_normalize.py",
        "cccp/calculation/tasks/thermochemistry.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("acp"), f"{relative}:{node.lineno}"
                    if relative.startswith("cccp/qc/"):
                        assert not alias.name.startswith("cccp.calculation"), (
                            f"{relative}:{node.lineno}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("acp"), f"{relative}:{node.lineno}"
                if relative.startswith("cccp/qc/"):
                    assert not module.startswith("cccp.calculation"), f"{relative}:{node.lineno}"


# ── standard-state normalization + units contract ──────────────────────


def test_standard_state_defaults_and_gibbs_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freq_log = _freq_log(tmp_path)
    state = _spy(monkeypatch, values={"h_sum": -40.25, "g_sum": -40.6, "s_total": 0.01})
    result = run_thermochemistry(
        _request(freq_log, standard_state=None),
        context=TaskContext(),
    )
    payload = result.payload
    assert isinstance(payload, ThermochemistryPayload)
    assert payload.standard_state == "1atm"
    assert payload.gibbs_source == "g_sum"
    assert payload.gibbs_hartree == pytest.approx(-40.6)

    result_1m = run_thermochemistry(
        _request(freq_log, standard_state="1M"),
        context=TaskContext(),
    )
    payload_1m = result_1m.payload
    assert isinstance(payload_1m, ThermochemistryPayload)
    assert payload_1m.standard_state == "1M"
    assert payload_1m.gibbs_source == "g_sum_plus_standard_state"
    assert payload_1m.gibbs_hartree == pytest.approx(-40.6 + _BASELINE_DELTA_HARTREE_298K)
    assert payload_1m.enthalpy_hartree == pytest.approx(-40.25)
    assert payload_1m.entropy_au == pytest.approx(0.01)
    assert len(state["calls"]) == 2


def test_units_are_hartree_and_au_without_scaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cccp.utils.constants import HARTREE_TO_KCAL

    freq_log = _freq_log(tmp_path)
    _spy(monkeypatch)
    result = run_thermochemistry(_request(freq_log), context=TaskContext())
    metadata = result.metadata
    payload = result.payload
    assert isinstance(payload, ThermochemistryPayload)
    assert payload.enthalpy_hartree == pytest.approx(metadata["h_sum"])
    assert payload.entropy_au == pytest.approx(metadata["s_total"])
    assert metadata["free_energy_kcal_mol"] == pytest.approx(
        payload.gibbs_hartree * HARTREE_TO_KCAL
    )


# ── pre-launch input errors ────────────────────────────────────────────


def test_missing_freq_log_raises_task_input_error() -> None:
    request = _request(Path("/nonexistent/frequency.log"))
    with pytest.raises(TaskInputError):
        run_thermochemistry(request, context=TaskContext())


def test_wrong_task_and_wrong_options_rejected(tmp_path: Path) -> None:
    with pytest.raises(TaskInputError):
        run_thermochemistry(
            TaskRequest(task=TaskKind.CASSCF, options=ThermochemistryOptions()),
            context=TaskContext(),
        )
    with pytest.raises(TaskInputError):
        run_thermochemistry(
            TaskRequest(task=TaskKind.THERMOCHEMISTRY, options=None),
            context=TaskContext(),
        )
