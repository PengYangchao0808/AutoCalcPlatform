"""Memory budgets reach rendered QC inputs and local/remote launch commands."""

from __future__ import annotations

import argparse
import copy
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import cccp.calculation as calculation
from acp.calculations.contracts import CalculationRequest, StructureArtifact
from acp.calculations.legacy_adapters import to_legacy_request, to_task_request
from acp.calculations.pes.scan import _sp_resource_plan
from acp.calculations.primitives import frequency as legacy_frequency
from acp.calculations.primitives import optimize as legacy_optimize
from acp.cli import _build_config
from acp.scheduler.jobs import JobSpec
from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.script_gen import (
    build_lsf_script_spec,
    build_remote_cli_command,
    generate_lsf_script,
)
from acp.scheduler.resources import with_job_resources
from acp.scheduler.runner import JobRunner
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import StructureRole, casscf_spec_from_dict
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    CasscfOptions,
    MethodSpec,
    OptimizationMode,
    OptimizeOptions,
    ScanCoordinateSpec,
    ScanOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
    validate_request,
)
from cccp.config import _get_default_config, _validate_config
from cccp.qc.interfaces.censo import CensoExecutionError, CensoInterface
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.utils.resource_utils import mem_to_mb, normalize_memory, resolve_orca_maxcore


@pytest.mark.parametrize("value", [0, -1, True, "0GB", "-1MB", "nan", "infGB", float("inf")])
def test_invalid_memory_rejected_at_both_unit_boundaries(value: Any) -> None:
    with pytest.raises(ValueError):
        _validate_config({"resources": {"mem": value}})
    with pytest.raises(TaskInputError):
        validate_request(TaskRequest(task=TaskKind.SINGLEPOINT, resources=TaskResources(mem=value)))


def test_explicit_units_and_legacy_gb_to_task_mb_boundary() -> None:
    assert mem_to_mb(".5 GB") == 512
    assert mem_to_mb("2049MB") == 2049
    assert mem_to_mb(8) == 8192
    assert TaskResources(mem=8).mem_total_mb() == 8
    assert TaskResources(mem="8").mem_total_mb() == 8
    assert normalize_memory(123456.789) == "123456.789GB"
    assert mem_to_mb(normalize_memory(1e6)) == 1024 * 1000000
    legacy = CalculationRequest(
        input_artifact=StructureArtifact(path=Path("input.xyz")),
        method="wB97X-D4",
        resources={"mem": 8},
    )
    request, binding = to_task_request(legacy, "singlepoint")
    assert request.resources.mem_total_mb() == 8192
    assert to_legacy_request(request, binding).resources["mem"] == 8


def test_cli_override_is_validated_after_merge(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("resources:\n  mem: 64GB\n  nproc: 16\n", encoding="utf-8")
    args = argparse.Namespace(config=str(cfg), mem="8", nproc=4)
    merged = _build_config(args)
    assert merged["resources"]["mem"] == "8GB"
    assert merged["resources"]["nproc"] == merged["executables"]["orca"]["nproc"] == 4
    args.mem = "0GB"
    with pytest.raises(ValueError):
        _build_config(args)


@pytest.mark.parametrize("workflow", ["singlepoint", "scan"])
@pytest.mark.parametrize("memory", ["0GB", "abc"])
def test_cli_invalid_memory_has_friendly_error(workflow: str, memory: str, tmp_path: Path) -> None:
    argv = [
        sys.executable,
        "-m",
        "acp.cli",
        "run",
        workflow,
        "--input",
        "CCO",
        "--mem",
        memory,
        "--output",
        str(tmp_path / "output"),
    ]
    if workflow == "scan":
        argv += ["--coordinate", "0,1,1.0,2.0"]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "Invalid configuration:" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "resources",
    [
        {"nproc": 0, "mem": "8GB"},
        {"nproc": -1, "mem": "8GB"},
        {"nproc": True, "mem": "8GB"},
        {"nproc": 1.5, "mem": "8GB"},
        {"nproc": "abc", "mem": "8GB"},
        {"ncores": 0, "memory_gb": 8},
        {"ncores": True, "memory_gb": 8},
        {"ncores": 1.5, "memory_gb": 8},
        {"ncores": "abc", "memory_gb": 8},
        {"nproc": 4, "memory_gb": 0},
        {"nproc": 4, "memory_gb": True},
        {"nproc": 4, "memory_gb": "abc"},
    ],
)
def test_scheduler_rejects_invalid_resources_and_legacy_aliases(
    resources: dict[str, Any],
) -> None:
    spec = JobSpec(workflow="singlepoint", name="invalid", resources=resources)
    with pytest.raises(ValueError):
        with_job_resources(spec)


@pytest.mark.parametrize(
    ("resources", "nproc", "memory"),
    [
        ({"ncores": "4", "memory_gb": 0.5}, 4, "0.5GB"),
        ({"nproc": 4}, 4, "30GB"),
        ({"mem": "8192MB"}, 16, "8192MB"),
        ({"nproc": 4, "ncores": 0, "mem": "8GB", "memory_gb": 0}, 4, "8GB"),
    ],
)
def test_scheduler_aliases_defaults_and_canonical_priority(
    resources: dict[str, Any], nproc: int, memory: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "acp.scheduler.resources.load_config", lambda **kwargs: _get_default_config()
    )
    original = copy.deepcopy(resources)
    spec = JobSpec(workflow="singlepoint", name="aliases", resources=resources)
    resolved = with_job_resources(spec)
    assert resolved.resources["nproc"] == nproc
    assert resolved.resources["mem"] == memory
    assert with_job_resources(resolved) == resolved
    assert spec.resources == original


def test_explicit_maxcore_uses_total_budget_without_auto_rounding() -> None:
    assert resolve_orca_maxcore(4, 4, maxcore=1) == 1
    with pytest.raises(ValueError, match="below 1 MB"):
        resolve_orca_maxcore(4, 4)


@pytest.fixture
def orca_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    executable = tmp_path / "orca"
    executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr("cccp.qc.interfaces.orca.resolve_executable", lambda *a, **k: executable)
    monkeypatch.setattr(ORCAInterface, "_run_orca", lambda *a, **k: False)
    return {
        "resources": {"nproc": 16, "mem": "64GB", "orca_maxcore_safety": 0.8},
        "executables": {"orca": {"path": str(executable), "nproc": 16, "maxcore": 2000}},
    }


@pytest.mark.parametrize(
    "task",
    [
        "singlepoint",
        "optimize",
        "optimize_ts",
        "frequency",
        "scan",
        "irc",
        "casscf",
        "nmr_shielding",
        "orca_gradient",
    ],
)
@pytest.mark.parametrize("maxcore", [None, 800])
def test_task_quota_reaches_actual_orca_input(
    task: str, maxcore: int | None, orca_config: dict[str, Any], tmp_path: Path
) -> None:
    config = copy.deepcopy(orca_config)
    if maxcore is None:
        config["executables"]["orca"].pop("maxcore")
    original = copy.deepcopy(config)
    options = None
    if task == "optimize_ts":
        options = OptimizeOptions(mode=OptimizationMode.TRANSITION_STATE, initial_hessian="model")
    if task == "scan":
        options = ScanOptions(
            coordinates=(ScanCoordinateSpec(atoms=(0, 1), start=0.7, end=1.0, atom_index_base=0),),
            points=2,
        )
    if task == "casscf":
        options = CasscfOptions(
            spec=casscf_spec_from_dict({"active_electrons": 2, "active_orbitals": 2})
        )
    request = TaskRequest(
        task=TaskKind.OPTIMIZE if task == "optimize_ts" else TaskKind(task),
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74)),
            symbols=("H", "H"),
            role=StructureRole.TRANSITION_STATE
            if task in ("irc", "optimize_ts")
            else StructureRole.MINIMUM,
        ),
        level=MethodSpec(method="CASSCF" if task == "casscf" else "wB97X-D4", basis="def2-SVP"),
        resources=TaskResources(nproc=4, mem=4096, maxcore=maxcore),
        options=options,
        output_dir=tmp_path / "run",
    )
    run_dir = tmp_path / "run"
    result = getattr(calculation, f"run_{request.task.value}")(
        request, context=TaskContext(config=config, workdir=run_dir)
    )
    inputs = list(run_dir.rglob("*.inp"))
    assert inputs, f"{task} did not reach ORCA input generation: {result}"
    for path in inputs:
        text = path.read_text(encoding="utf-8")
        assert re.findall(r"(?im)^%maxcore\s+(\d+)", text) == [str(maxcore or 819)]
        assert re.search(r"%pal\s+nprocs\s+4\s+end", text, re.IGNORECASE)
    assert config == original


def test_orca_config_pin_and_raw_override_obey_total_budget(
    orca_config: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    config = copy.deepcopy(orca_config)
    config["resources"]["mem"] = "1GB"
    with pytest.raises(ValueError, match="cannot cover"):
        ORCAInterface(config)
    config["resources"]["mem"] = "64GB"
    interface = ORCAInterface(config)
    text, _ = interface._build_input_blocks(
        calc_type="sp", extra_blocks=["%maxcore 3000\n%output Print[P_MOs] 1 end"]
    )
    assert re.findall(r"(?im)^%maxcore\s+(\d+)", text) == ["3000"]
    assert "%output Print[P_MOs] 1 end" in text
    assert "Raw %maxcore 3000 MB overrides configured maxcore 2000 MB" in caplog.text
    with pytest.raises(ValueError, match="cannot cover"):
        interface._build_input_blocks(calc_type="sp", extra_blocks=["%maxcore 5000"])
    with pytest.raises(ValueError, match="Conflicting"):
        interface._build_input_blocks(
            calc_type="sp", extra_blocks=["%maxcore 1000", "%maxcore 2000"]
        )


def test_pes_concurrent_children_share_memory_budget(orca_config: dict[str, Any]) -> None:
    config = copy.deepcopy(orca_config)
    config["resources"]["mem"] = "32GB"
    config["executables"]["orca"].pop("maxcore")
    original = copy.deepcopy(config)
    workers, child = _sp_resource_plan(config)
    assert workers == 4
    assert child["resources"]["mem"] == "8192MB"
    interface = ORCAInterface(child)
    assert interface.maxcore == 1638
    assert workers * interface.nproc * interface.maxcore <= 32768 * 0.8
    assert config == original


@pytest.mark.parametrize("task", ["optimize", "frequency"])
def test_acp_wrapper_applies_quota_before_constructing_backend(
    task: str, orca_config: dict[str, Any], tmp_path: Path
) -> None:
    original = copy.deepcopy(orca_config)
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "unused.xyz", elements=["H", "H"]),
        method="wB97X-D4",
        resources={
            "config": orca_config,
            "nproc": 4,
            "mem": 4,
            "maxcore": 800,
            "coordinates": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]],
            "symbols": ["H", "H"],
            "output_dir": str(tmp_path / "run"),
            "opt_rescue_policy": "none",
        },
    )
    module = legacy_optimize if task == "optimize" else legacy_frequency
    getattr(module, f"run_{task}")(request)
    inputs = list((tmp_path / "run").rglob("*.inp"))
    assert inputs
    for path in inputs:
        text = path.read_text(encoding="utf-8")
        assert "%maxcore 800" in text
        assert "%pal nprocs 4 end" in text
    assert orca_config == original


def test_ts_cpu_override_recomputes_memory(orca_config: dict[str, Any], tmp_path: Path) -> None:
    config = copy.deepcopy(orca_config)
    config["resources"]["mem"] = "4GB"
    config["executables"]["orca"]["nproc"] = 4
    config["executables"]["orca"].pop("maxcore")
    interface = ORCAInterface(config)
    interface.transition_state_opt(
        [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)],
        ["H", "H"],
        output_dir=tmp_path / "run",
        initial_hessian="model",
        nproc=8,
    )
    text = next((tmp_path / "run").glob("*.inp")).read_text(encoding="utf-8")
    assert "%maxcore 409" in text
    assert "%pal nprocs 8 end" in text
    with pytest.raises(ValueError, match="cannot cover"):
        interface.transition_state_opt(
            [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74)],
            ["H", "H"],
            output_dir=tmp_path / "overshoot",
            initial_hessian="model",
            nproc=8,
            extra_blocks=["%maxcore 800"],
        )


def test_scheduler_freezes_defaults_and_local_remote_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = _get_default_config()
    defaults["resources"].update(nproc=4, mem="8001MB")
    monkeypatch.setattr(
        "acp.scheduler.resources.load_config", lambda **kwargs: copy.deepcopy(defaults)
    )
    original = JobSpec(workflow="singlepoint", input={"source": "CCO"})
    spec = with_job_resources(original)
    defaults["resources"].update(nproc=64, mem="256GB")
    assert spec.resources == {"nproc": 4, "mem": "8001MB"}
    assert original.resources == {}
    local = JobRunner()._build_cmd(spec, tmp_path, input_path="input.xyz")
    remote = build_remote_cli_command(spec, input_path="input.xyz")
    for command in (local, remote):
        assert command[command.index("--mem") + 1] == "8001MB"
        assert command[command.index("--nproc") + 1] == "4"


@pytest.mark.parametrize("nproc,memory,total", [(3, "1001MB", 1001), (64, "1GB", 1024)])
def test_lsf_exact_total_matches_cli_without_per_core_floor(
    nproc: int, memory: str, total: int
) -> None:
    node = RemoteNode(
        name="test",
        host="localhost",
        username="test",
        remote_work_dir="/scratch",
        remote_code_dir="/code",
    )
    spec = JobSpec(
        workflow="singlepoint", input={"source": "CCO"}, resources={"nproc": nproc, "mem": memory}
    )
    lsf, cmd = build_lsf_script_spec(spec, "job", node)
    assert lsf.mem_total_mb == total
    assert mem_to_mb(cmd[cmd.index("--mem") + 1]) == total
    assert f"#BSUB -M {int(total * 1024 * 1.05)}\n" in generate_lsf_script(lsf)


def test_censo_launch_uses_budgeted_orca_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cccp.qc.interfaces.censo.resolve_executable", lambda *a, **k: Path("/mock/program")
    )
    captured = {}

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(command=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 1, "", "mock stop before calculation")

    monkeypatch.setattr("cccp.qc.interfaces.censo.subprocess.run", run)
    interface = CensoInterface({"resources": {"nproc": 16, "mem": "32GB"}})
    ensemble = tmp_path / "ensemble.xyz"
    ensemble.write_text("2\nH2\nH 0 0 0\nH 0 0 0.74\n", encoding="utf-8")
    extras = {"screening": ["%output Print[P_MOs] 1 end"]}
    with pytest.raises(CensoExecutionError):
        interface.refine_ensemble(
            ensemble, tmp_path / "censo", preset="censo-default", part_templates=extras
        )
    templates = list((Path(captured["env"]["HOME"]) / ".censo2_assets").glob("*.orca.template"))
    assert {p.stem for p in templates} == {
        "prescreening.orca",
        "screening.orca",
        "optimization.orca",
        "refinement.orca",
    }
    for path in templates:
        assert "%maxcore 1638" in path.read_text(encoding="utf-8")
    assert extras == {"screening": ["%output Print[P_MOs] 1 end"]}
    rcfile = tmp_path / "censo" / "censo2rc"
    assert rcfile.read_text(encoding="utf-8").count("template = True") == 4


@pytest.mark.parametrize("block", ["%maxcore 9999", "%output Print[P_MOs] 1 end\n%maxcore 9999"])
def test_censo_rejects_template_memory_override(
    block: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cccp.qc.interfaces.censo.resolve_executable", lambda *a, **k: None)
    interface = CensoInterface({"resources": {"mem": "4GB"}})
    with pytest.raises(ValueError, match="task memory budget"):
        interface._memory_templates({"parts": ["screening"]}, 4, {"screening": [block]})
