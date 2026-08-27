"""P5 acceptance-gap tests: full --levels consumption, template injection,
keep-all semantics, frontend method-dict mapping, and censo-zero passthrough.

Covers the fixes registered in dev-doc v14 (P4-5 / P4-6 / gate 10 chain):
* energy._resolve_levels consumes the full §10.1 field sets
* ORCAInterface renders route_extras / geom_maxiter
* CensoBackend keep_all parameterization + part template injection (§6.4)
* scheduler mapping helpers (profile_id → preset, levels → solvent)
* runner/script_gen CLI construction for UI-submitted jobs
* ensemble censo-zero CREST passthrough (no CENSO invocation)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from acp.backends.censo_backend import CensoBackend
from acp.scheduler.jobs import (
    JobSpec,
    censo_preset_from_method,
    censo_solvent_from_method,
)
from acp.workflows.energy import _resolve_levels
from cccp.qc.interfaces.censo import CensoInterface
from cccp.qc.interfaces.orca import ORCAInterface


@pytest.fixture(autouse=True)
def _stub_censo_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the centralized resolver so CENSO tests run without the binary."""

    def _resolve(name: str, configured_path: str | Path | None = None) -> Path | None:
        return Path("/usr/bin/censo") if name == "censo" else None

    monkeypatch.setattr("cccp.qc.interfaces.censo.resolve_executable", _resolve)


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "executables": {
            "censo": {"path": "censo"},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    }
    config.update(overrides)
    return config


def _mock_orca_backend_cls(orca: MagicMock) -> MagicMock:
    """Return a fake ``get_backend("orca")`` class that yields *orca*."""
    backend_cls = MagicMock()
    backend_cls.return_value = orca
    return backend_cls


# ---------------------------------------------------------------------------
# §10.1 full field consumption (P4-5)
# ---------------------------------------------------------------------------


def test_resolve_levels_full_refinement_sp_fields() -> None:
    resolved = _resolve_levels(
        _make_config(),
        {
            "refinement_sp": {
                "functional": "DLPNO-CCSD(T)",
                "basis": "def2-TZVPP",
                "aux_j_basis": "def2/J",
                "aux_c_basis": "def2-TZVPP/C",
                "dispersion": "D4",
                "ri_approximation": "RIJCOSX",
                "grid": "UltraFine",
                "scf_convergence": "VeryTight",
            },
        },
    )
    assert resolved["sp_method"] == "DLPNO-CCSD(T)"
    extras = resolved["sp_route_extras"]
    assert "D4" in extras
    assert "RIJCOSX" in extras
    assert "def2/J" in extras
    assert "def2-TZVPP/C" in extras
    assert "DEFGRID3" in extras
    assert "VeryTightSCF" in extras
    # CENSO-side template line mirrors the same whitelist
    assert resolved["refinement_template_lines"] == [
        "! " + " ".join(extras)
    ]


def test_resolve_levels_full_dft_opt_fields() -> None:
    resolved = _resolve_levels(
        _make_config(),
        {
            "dft_opt": {
                "functional": "PBE0",
                "basis": "def2-TZVP",
                "dispersion": "D3BJ",
                "grid": "SG1",
                "scf_convergence": "Tight",
                "opt_convergence": "Tight",
                "max_steps": 300,
            },
        },
    )
    assert resolved["opt_method"] == "PBE0"
    assert resolved["opt_basis"] == "def2-TZVP"
    assert "D3BJ" in resolved["opt_route_extras"]
    assert "DEFGRID1" in resolved["opt_route_extras"]
    assert "TightSCF" in resolved["opt_route_extras"]
    assert "TightOpt" in resolved["opt_route_extras"]
    assert resolved["opt_geom_maxiter"] == 300
    # freq must NOT inherit the opt-convergence keyword (v7 rule: same
    # method/basis, but TightOpt is opt-only)
    assert "TightOpt" not in resolved["opt_freq_route_extras"]
    assert "TightSCF" in resolved["opt_freq_route_extras"]


def test_resolve_levels_defaults_produce_no_extras() -> None:
    resolved = _resolve_levels(_make_config(), None)
    assert resolved["opt_route_extras"] == []
    assert resolved["sp_route_extras"] == []
    assert resolved["opt_geom_maxiter"] is None
    assert resolved["screening_template_lines"] == []
    assert resolved["refinement_template_lines"] == []


def test_resolve_levels_solvent_chain() -> None:
    resolved = _resolve_levels(
        _make_config(),
        {
            "refinement_sp": {"solvent_model": "SMD", "solvent": "water"},
            "dft_opt": {"solvent_model": "CPCM", "solvent": "dcm"},
        },
    )
    assert resolved["sp_solvent"] == "water"
    assert resolved["sp_solvent_model"] == "smd"
    assert resolved["opt_solvent"] == "dcm"
    assert resolved["opt_solvent_model"] == "cpcm"
    # refinement_sp wins the workflow-global fallback
    assert resolved["levels_solvent"] == "water"


def test_resolve_levels_solvent_model_none_is_gas() -> None:
    resolved = _resolve_levels(
        _make_config(),
        {"refinement_sp": {"solvent_model": "none", "solvent": "water"}},
    )
    assert resolved["sp_solvent"] is None
    assert resolved["levels_solvent"] is None


# ---------------------------------------------------------------------------
# ORCA input rendering
# ---------------------------------------------------------------------------


def test_orca_route_extras_rendered() -> None:
    orca = ORCAInterface(_make_config(), method="wB97M-V", basis="def2-TZVPP")
    blocks, _ = orca._build_input_blocks(
        "sp",
        route_extras=["RIJCOSX", "def2-TZVPP/C", "VeryTightSCF", "DEFGRID3"],
    )
    route_line = blocks.splitlines()[0]
    assert route_line.startswith("! wB97M-V def2-TZVPP SP")
    for kw in ("RIJCOSX", "VeryTightSCF", "DEFGRID3"):
        assert kw in route_line
    assert "def2-TZVPP/C" not in route_line
    assert 'auxC  "def2-TZVPP/C"' in blocks


def test_orca_geom_maxiter_rendered() -> None:
    orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
    blocks, _ = orca._build_input_blocks("opt", geom_maxiter=250, symbols=["C", "H"])
    assert "MaxIter 250" in blocks


def test_orca_default_blocks_unchanged() -> None:
    orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
    blocks, _ = orca._build_input_blocks("opt", symbols=["C", "H"])
    assert "MaxIter" not in blocks
    assert blocks.splitlines()[0] == "! r2SCAN-3c Opt"


# ---------------------------------------------------------------------------
# CensoBackend: keep_all + template injection (§6.4)
# ---------------------------------------------------------------------------


def test_build_cli_no_keep_all_by_default(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH  0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    preset = interface.resolve_preset("censo-light")
    cmd = interface.build_cli(
        input_xyz, rcfile, preset, nproc=4, temperature=298.15, solvent=None,
    )
    assert "--keep-all" not in cmd

    cmd_keep = interface.build_cli(
        input_xyz, rcfile, preset, nproc=4, temperature=298.15, solvent=None,
        keep_all=True,
    )
    assert "--keep-all" in cmd_keep


def test_keep_all_config_default(tmp_path: Path) -> None:
    cfg = _make_config()
    cfg["censo"]["keep_all"] = True
    interface = CensoInterface(cfg)
    assert interface._keep_all is True


def test_write_part_templates(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    home_dir = interface.write_part_templates(
        tmp_path,
        {"refinement": ["! RIJCOSX def2-TZVPP/C VeryTightSCF"]},
    )
    assert home_dir == tmp_path / "home"
    template = home_dir / ".censo2_assets" / "refinement.orca.template"
    assert template.exists()
    body = template.read_text()
    assert body.startswith("{main}\n")
    assert "! RIJCOSX def2-TZVPP/C VeryTightSCF" in body
    assert "{geom}" in body


def test_rcfile_template_flag(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(
        preset, tmp_path, charge=0, multiplicity=1, solvent=None,
        templated_parts={"screening"},
    )
    content = rcfile.read_text()
    screening_section = content.split("[screening]")[1].split("[")[0]
    prescreening_section = content.split("[prescreening]")[1].split("[")[0]
    assert "template = True" in screening_section
    assert "template = False" in prescreening_section
    # CENSO 3.0.8 validates every rcfile section — inactive parts
    # (refinement is not in censo-light's P+S part list) must be omitted
    assert "[refinement]" not in content


def test_rcfile_refinement_written_when_active(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    preset = interface.resolve_preset("censo-light")
    preset["parts"] = [*preset["parts"], "refinement"]
    rcfile = interface.generate_rcfile(
        preset, tmp_path, charge=0, multiplicity=1, solvent=None,
        templated_parts={"refinement"},
    )
    content = rcfile.read_text()
    refinement_section = content.split("[refinement]")[1].split("[")[0]
    assert "template = True" in refinement_section


def test_refine_ensemble_injects_home(tmp_path: Path, monkeypatch: Any) -> None:
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "crest_conformers.xyz"
    input_xyz.write_text("1\n-1.0\nH  0 0 0\n")

    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cmd"] = cmd
        # Fabricate minimal CENSO outputs so parsing succeeds
        out_dir = Path(kwargs["cwd"])
        (out_dir / "1_SCREENING.json").write_text(json.dumps({
            "part_name": "screening",
            "data": {"CONF1": {
                "energy": -1.0, "gsolv": 0.0, "grrho": 0.0, "gtot": -1.0,
            }},
        }))
        (out_dir / "1_SCREENING.xyz").write_text("1\nCONF1\nH  0 0 0\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "cccp.qc.interfaces.censo.subprocess.run", fake_run
    )
    monkeypatch.setattr(CensoInterface, "is_available", lambda self: True)

    result = interface.refine_ensemble(
        input_xyz,
        tmp_path / "censo",
        preset="censo-light",
        part_templates={"screening": ["! VeryTightSCF"]},
    )
    assert result.records
    env = captured["env"]
    assert env is not None
    assert env["HOME"] == str(tmp_path / "censo" / "home")
    # Threads pinned to nproc (config resources.nproc=4) so CENSO's xTB/ORCA
    # children cannot inherit a node-wide OMP_NUM_THREADS.
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "4"
    template = (
        tmp_path / "censo" / "home" / ".censo2_assets" / "screening.orca.template"
    )
    assert template.exists()

def test_refine_ensemble_no_templates_pins_threads(tmp_path: Path, monkeypatch: Any) -> None:
    """CENSO subprocess env must pin BLAS/OpenMP threads even without
    template injection or LD_LIBRARY_PATH (P0: node-wide OMP_NUM_THREADS
    oversubscription fix)."""
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "crest_conformers.xyz"
    input_xyz.write_text("1\n-1.0\nH  0 0 0\n")

    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        out_dir = Path(kwargs["cwd"])
        (out_dir / "1_SCREENING.json").write_text(json.dumps({
            "part_name": "screening",
            "data": {"CONF1": {
                "energy": -1.0, "gsolv": 0.0, "grrho": 0.0, "gtot": -1.0,
            }},
        }))
        (out_dir / "1_SCREENING.xyz").write_text("1\nCONF1\nH  0 0 0\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "cccp.qc.interfaces.censo.subprocess.run", fake_run
    )
    monkeypatch.setattr(CensoInterface, "is_available", lambda self: True)

    interface.refine_ensemble(input_xyz, tmp_path / "censo", preset="censo-light")
    env = captured["env"]
    assert env is not None
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "4"


def test_refine_ensemble_pins_per_child_threads_not_total(tmp_path: Path, monkeypatch: Any) -> None:
    """CENSO subdivides --maxcores across parallel children, so the env
    OMP/MKL/OPENBLAS must be the PER-CHILD share (= --omp-min), not the
    total nproc.  With nproc=16, omp-min=4, CENSO runs ~4 children in
    parallel; pinning env to 16 would make each child spawn 16 threads
    (4 x 16 = 64 -> oversubscription).  Regression guard for the
    compute-01 SmI2 incident (40-core node, 100% CPU on a 16-core job)."""
    interface = CensoInterface(_make_config(resources={"nproc": 16}))
    input_xyz = tmp_path / "crest_conformers.xyz"
    input_xyz.write_text("1\n-1.0\nH  0 0 0\n")

    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cmd"] = cmd
        out_dir = Path(kwargs["cwd"])
        (out_dir / "1_SCREENING.json").write_text(json.dumps({
            "part_name": "screening",
            "data": {"CONF1": {
                "energy": -1.0, "gsolv": 0.0, "grrho": 0.0, "gtot": -1.0,
            }},
        }))
        (out_dir / "1_SCREENING.xyz").write_text("1\nCONF1\nH  0 0 0\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "cccp.qc.interfaces.censo.subprocess.run", fake_run
    )
    monkeypatch.setattr(CensoInterface, "is_available", lambda self: True)

    interface.refine_ensemble(input_xyz, tmp_path / "censo", preset="censo-light")

    env = captured["env"]
    cmd = captured["cmd"]
    # Total budget still passed to CENSO as --maxcores.
    assert "--maxcores" in cmd
    assert cmd[cmd.index("--maxcores") + 1] == "16"
    assert "--omp-min" in cmd
    assert cmd[cmd.index("--omp-min") + 1] == "4"
    # Per-child thread share — must NOT equal the total nproc (16).
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "4"
    assert env["OPENBLAS_NUM_THREADS"] == "4"
    assert env["OMP_NUM_THREADS"] != "16"


def test_censo_preset_from_method() -> None:
    assert censo_preset_from_method({"preset": "censo-zero"}) == "censo-zero"
    assert censo_preset_from_method({"profile_id": "censo-default"}) == "censo-default"
    assert censo_preset_from_method({"preset": "CENSO-Light"}) == "censo-light"
    assert censo_preset_from_method({"profile_id": "__custom__"}) is None
    assert censo_preset_from_method({}) is None
    # explicit preset wins over profile_id
    assert (
        censo_preset_from_method({"preset": "censo-zero", "profile_id": "censo-light"})
        == "censo-zero"
    )


def test_censo_solvent_from_method() -> None:
    assert censo_solvent_from_method({"solvent": "dcm"}) == "dcm"
    assert censo_solvent_from_method({
        "levels": {"refinement_sp": {"solvent_model": "SMD", "solvent": "water"}},
    }) == "water"
    assert censo_solvent_from_method({
        "levels": {"refinement_sp": {"solvent_model": "none", "solvent": "water"}},
    }) is None
    assert censo_solvent_from_method({
        "levels": {"dft_opt": {"solvent_model": "CPCM", "solvent": "thf"}},
    }) == "thf"
    assert censo_solvent_from_method({}) is None


def test_runner_build_cmd_energy_from_ui_method() -> None:
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={
            "schema_id": "censo_energy",
            "profile_id": "censo-zero",
            "no_opt": True,
            "levels": {"thermo": {"scale_factor": 0.98}},
        },
        resources={"nproc": 8},
    )
    stub = SimpleNamespace(python="python")
    cmd = JobRunner._build_cmd(stub, spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--preset" in cmd
    assert cmd[cmd.index("--preset") + 1] == "censo-zero"
    assert "--no-opt" in cmd
    assert "--levels" in cmd
    levels = json.loads(cmd[cmd.index("--levels") + 1])
    assert levels["thermo"]["scale_factor"] == 0.98


def test_runner_build_cmd_ensemble_keep_all() -> None:
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="ensemble",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light", "keep_all": True},
    )
    stub = SimpleNamespace(python="python")
    cmd = JobRunner._build_cmd(stub, spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--keep-all" in cmd
    assert "--preset" in cmd
    # ensemble must never receive --levels / --no-opt
    assert "--levels" not in cmd
    assert "--no-opt" not in cmd


def test_script_gen_parity_with_runner() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={
            "profile_id": "censo-light",
            "levels": {
                "refinement_sp": {"solvent_model": "SMD", "solvent": "water"},
            },
        },
    )
    cmd = build_remote_cli_command(spec, python_executable="python3")
    assert cmd[cmd.index("--preset") + 1] == "censo-light"
    assert "--solvent" in cmd
    assert cmd[cmd.index("--solvent") + 1] == "water"


def test_stage_plan_uses_profile_id() -> None:
    from acp.scheduler.stage_tasks import get_stage_plan

    spec = JobSpec(
        workflow="energy",
        input={"source": "CCO"},
        method={"profile_id": "censo-default"},
    )
    plan = get_stage_plan(spec)
    names = [p.stage_name for p in plan]
    assert "censo_optimization" in names


# ---------------------------------------------------------------------------
# Remote binary probe (acceptance gate 10)
# ---------------------------------------------------------------------------


def _probe_stub(report: dict[str, Any] | Exception) -> SimpleNamespace:
    from acp.scheduler.remote.runner import RemoteJobRunner

    class _FakeSSH:
        def execute(self, node, command, timeout=90):
            if isinstance(report, Exception):
                raise report
            return 0, json.dumps(report), ""

    def _resolve_python(node, job_id=None):
        return "python3.12"

    return SimpleNamespace(
        _ssh=_FakeSSH(),
        _BINARY_PROBE_SCRIPT=RemoteJobRunner._BINARY_PROBE_SCRIPT,
        _resolve_node_python=_resolve_python,
    )


def _fake_node() -> Any:
    from acp.scheduler.remote.config import RemoteNode

    return RemoteNode(
        name="compute-test",
        host="127.0.0.1",
        username="nobody",
        remote_work_dir="/tmp/jobs",
        remote_code_dir="/tmp/code",
    )


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


def test_probe_missing_censo_raises_with_config_hint() -> None:
    from acp.scheduler.remote.runner import (
        RemoteJobRunner,
        RemoteNodeUnavailableError,
    )

    stub = _probe_stub({
        "crest": {"configured": "crest", "resolved": "/usr/bin/crest", "version": None},
        "censo": {"configured": "/opt/censo", "resolved": None, "version": None},
        "orca": {"configured": "orca", "resolved": "/usr/bin/orca", "version": None},
    })
    spec = JobSpec(workflow="energy", input={"source": "CCO"})
    log = _FakeEventLog()

    with pytest.raises(RemoteNodeUnavailableError) as exc_info:
        RemoteJobRunner._probe_required_binaries(stub, _fake_node(), spec, log, "job-x")
    msg = str(exc_info.value)
    assert "censo" in msg.lower()
    assert "~/.cccp.yaml" in msg
    assert "executables.censo.path" in msg
    assert any(e[0] == "remote.binary_probe" for e in log.events)


def test_probe_all_present_passes() -> None:
    from acp.scheduler.remote.runner import RemoteJobRunner

    stub = _probe_stub({
        "crest": {"configured": "crest", "resolved": "/usr/bin/crest", "version": None},
        "censo": {"configured": "censo", "resolved": "/usr/bin/censo", "version": "3.0.8"},
        "orca": {"configured": "orca", "resolved": "/usr/bin/orca", "version": None},
    })
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"})
    log = _FakeEventLog()
    RemoteJobRunner._probe_required_binaries(stub, _fake_node(), spec, log, "job-x")
    probe_events = [e for e in log.events if e[0] == "remote.binary_probe"]
    assert probe_events and probe_events[0][1]["missing"] == []


def test_probe_ssh_failure_is_fail_open() -> None:
    from acp.scheduler.remote.runner import RemoteJobRunner

    stub = _probe_stub(RuntimeError("ssh boom"))
    spec = JobSpec(workflow="energy", input={"source": "CCO"})
    log = _FakeEventLog()
    # must NOT raise
    RemoteJobRunner._probe_required_binaries(stub, _fake_node(), spec, log, "job-x")
    assert any(e[0] == "remote.binary_probe_error" for e in log.events)


def test_probe_skipped_for_workflow_without_binaries() -> None:
    from acp.scheduler.remote.runner import RemoteJobRunner

    class _ExplodingSSH:
        def execute(self, *a: Any, **kw: Any):
            raise AssertionError("probe must not run for workflows without requires_binaries")

    stub = SimpleNamespace(
        _ssh=_ExplodingSSH(),
        _BINARY_PROBE_SCRIPT=RemoteJobRunner._BINARY_PROBE_SCRIPT,
    )
    spec = JobSpec(workflow="fake", input={"source": "x"})
    RemoteJobRunner._probe_required_binaries(stub, _fake_node(), spec, _FakeEventLog(), "job-x")


# ---------------------------------------------------------------------------
# Ensemble censo-zero passthrough (§7: no CENSO invocation)
# ---------------------------------------------------------------------------


def test_xtb_passthrough_sorts_by_title_energy(tmp_path: Path) -> None:
    from acp.workflows.ensemble import _xtb_passthrough_result

    xyz = tmp_path / "crest_conformers.xyz"
    xyz.write_text(
        "1\n"
        "-1.00000000\n"
        "H  0.0 0.0 0.0\n"
        "1\n"
        "-1.50000000\n"
        "H  0.0 0.0 1.0\n"
    )
    result = _xtb_passthrough_result(xyz, 298.15)
    assert result.preset == "censo-zero"
    assert result.final_part == "crest_passthrough"
    assert len(result.records) == 2
    # sorted by gtot: the -1.5 frame (originally second) comes first
    assert result.records[0].gtot == pytest.approx(-1.5)
    assert result.records[0].conf_id == "CONF2"
    assert result.records[0].grrho == 0.0
    weights = result.boltzmann_weights()
    assert weights["CONF2"] > weights["CONF1"]


def test_ensemble_zero_does_not_invoke_censo(tmp_path: Path, monkeypatch: Any) -> None:
    from unittest.mock import MagicMock, patch

    from acp.workflows.ensemble import run_ensemble_generation

    xyz = tmp_path / "ext_ensemble.xyz"
    xyz.write_text(
        "1\n-1.0\nH  0.0 0.0 0.0\n"
        "1\n-1.5\nH  0.0 0.0 1.0\n"
    )

    with patch("acp.workflows.ensemble.CensoBackend") as mock_backend_cls:
        mock_backend_cls.return_value = MagicMock()
        result = run_ensemble_generation(
            input_source=str(xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=_make_config(),
            name="passthrough",
        )

    assert result.status == "completed"
    mock_backend_cls.assert_not_called()
    ensemble_xyz = tmp_path / "out" / "passthrough" / "ensemble" / "ensemble.xyz"
    assert ensemble_xyz.exists()


# ---------------------------------------------------------------------------
# v15: cumulative-Boltzmann ensemble selection (finalDFT logic)
# ---------------------------------------------------------------------------


def _g_record(conf_id: str, frame_index: int, gtot: float) -> Any:
    import numpy as np

    from acp.backends.censo_backend import CensoConformerRecord

    return CensoConformerRecord(
        conf_id=conf_id,
        frame_index=frame_index,
        energy=gtot,
        gsolv=0.0,
        grrho=0.0,
        gtot=gtot,
        coordinates=np.zeros((1, 3)),
        symbols=["H"],
    )


def test_select_cumulative_boltzmann_far_apart_keeps_rank1() -> None:
    from acp.workflows.energy import _select_cumulative_boltzmann

    records = [_g_record("CONF1", 0, -155.00), _g_record("CONF2", 1, -154.95)]
    selected = _select_cumulative_boltzmann(records, 298.15, 0.99)
    assert [r.conf_id for r in selected] == ["CONF1"]


def test_select_cumulative_boltzmann_close_keeps_all() -> None:
    from acp.workflows.energy import _select_cumulative_boltzmann

    # ΔG ≈ 0.31 kcal/mol → weights ~0.63/0.37 → both needed for 99%
    records = [_g_record("CONF2", 1, -154.9995), _g_record("CONF1", 0, -155.0)]
    selected = _select_cumulative_boltzmann(records, 298.15, 0.99)
    assert [r.conf_id for r in selected] == ["CONF1", "CONF2"]


def test_select_cumulative_boltzmann_threshold_crossing_included() -> None:
    from acp.workflows.energy import _select_cumulative_boltzmann

    # Three equal-G conformers: weights 1/3 each; cumsum crosses 0.5 at #2
    records = [_g_record(f"CONF{i}", i - 1, -155.0) for i in (1, 2, 3)]
    selected = _select_cumulative_boltzmann(records, 298.15, 0.5)
    assert len(selected) == 2
    # threshold 1.0 keeps the full set
    assert len(_select_cumulative_boltzmann(records, 298.15, 1.0)) == 3
    # empty input
    assert _select_cumulative_boltzmann([], 298.15, 0.99) == []


def test_resolve_levels_refinement_threshold() -> None:
    from acp.workflows.energy import _resolve_levels

    resolved = _resolve_levels(_make_config(), {"refinement_threshold": 0.9})
    assert resolved["refinement_threshold"] == pytest.approx(0.9)
    # config fallback
    cfg = _make_config()
    cfg["censo"]["refinement_threshold"] = 0.95
    assert _resolve_levels(cfg, None)["refinement_threshold"] == pytest.approx(0.95)
    # invalid values fall back to 0.99
    assert _resolve_levels(_make_config(), {"refinement_threshold": 1.7})[
        "refinement_threshold"
    ] == pytest.approx(0.99)


def test_energy_zero_opt_on_multi_conformer_ensemble(tmp_path: Path) -> None:
    """censo-zero opt-on with near-degenerate xTB energies → 2 handoffs."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from acp.workflows.energy import run_conformer_energy

    xyz = tmp_path / "close.xyz"
    xyz.write_text(
        "3\n-154.80000000\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\n-154.79950000\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    orca = MagicMock()
    opt_result = MagicMock(
        success=True,
        coordinates=np.zeros((3, 3)),
        symbols=["C", "H", "H"],
        energy=-154.9,
        log_file=Path("/tmp/opt.out"),
        error_message=None,
    )
    orca.optimize.return_value = opt_result
    orca.frequency.return_value = MagicMock(
        success=True, log_file=Path("/tmp/freq.out"), error_message=None,
    )
    orca.single_point.return_value = MagicMock(
        success=True, energy=-155.0, log_file=Path("/tmp/sp.out"),
        error_message=None,
    )
    shermo_ok = {"g_sum": -154.95, "g_conc": None, "h_sum": -154.9,
                 "u_sum": -154.91, "s_total": 0.03}

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.get_backend", return_value=_mock_orca_backend_cls(orca)),
        patch("acp.workflows.energy_shared.run_shermo", return_value=dict(shermo_ok)),
    ):
        result = run_conformer_energy(
            input_source=str(xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=_make_config(),
            name="close",
        )

    assert result.status == "completed"
    mock_backend_cls.assert_not_called()
    assert result.metadata["n_conformers"] == 2
    assert orca.optimize.call_count == 2
    # auxiliary xTB ranking table written for the passthrough path
    assert (tmp_path / "out" / "close" / "ensemble" / "screening_ranking.csv").exists()


def test_energy_light_non_rank1_handoff_failure_is_skipped(tmp_path: Path) -> None:
    """A failing non-rank1 conformer is dropped; rank1 failure still raises."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from acp.backends.censo_backend import CensoRunResult
    from acp.workflows.energy import run_conformer_energy

    xyz = tmp_path / "in.xyz"
    xyz.write_text(
        "3\n-154.80000000\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\n-154.79950000\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )
    screening = CensoRunResult(
        preset="censo-light",
        records=[_g_record("CONF1", 0, -154.9995), _g_record("CONF2", 1, -154.9990)],
        final_part="screening",
        temperature=298.15,
    )
    screening.sort_by_gtot()

    orca = MagicMock()
    ok_opt = MagicMock(
        success=True, coordinates=np.zeros((1, 3)), symbols=["H"],
        energy=-154.9, log_file=Path("/tmp/opt.out"), error_message=None,
    )
    bad_opt = MagicMock(success=False, error_message="SCF blew up")
    orca.optimize.side_effect = [ok_opt, bad_opt]
    orca.frequency.return_value = MagicMock(
        success=True, log_file=Path("/tmp/freq.out"), error_message=None,
    )
    orca.single_point.return_value = MagicMock(
        success=True, energy=-155.0, log_file=Path("/tmp/sp.out"),
        error_message=None,
    )
    shermo_ok = {"g_sum": -154.95, "g_conc": None, "h_sum": -154.9,
                 "u_sum": -154.91, "s_total": 0.03}

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.get_backend", return_value=_mock_orca_backend_cls(orca)),
        patch("acp.workflows.energy_shared.run_shermo", return_value=dict(shermo_ok)),
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = screening
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=_make_config(),
            name="skipfail",
        )

    assert result.status == "completed"
    assert result.metadata["n_conformers"] == 1
    assert result.ensemble.records[0].structure.metadata["source"] == "CONF1"


def test_energy_cheap_path_custom_threshold_propagates(tmp_path: Path) -> None:
    """--levels refinement_threshold reaches the CENSO rcfile overrides."""
    from unittest.mock import MagicMock, patch

    from acp.backends.censo_backend import CensoRunResult
    from acp.workflows.energy import run_conformer_energy

    xyz = tmp_path / "in.xyz"
    xyz.write_text(
        "3\n-154.80000000\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\n-154.79950000\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )
    refinement = CensoRunResult(
        preset="censo-light",
        records=[_g_record("CONF1", 0, -154.9995), _g_record("CONF2", 1, -154.9990)],
        final_part="refinement",
        temperature=298.15,
    )
    refinement.sort_by_gtot()

    with patch("acp.workflows.energy.CensoBackend") as mock_backend_cls:
        backend = MagicMock()
        backend.refine_ensemble.return_value = refinement
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=_make_config(),
            name="thr",
            no_opt=True,
            levels={"refinement_threshold": 0.5},
        )

    assert result.status == "completed"
    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["part_overrides"]["refinement"]["threshold"] == pytest.approx(0.5)
    # ΔG ≈ 0.31 kcal/mol → rank1 weight ≈ 0.63 ≥ 0.5 → only rank1 kept
    assert result.metadata["n_conformers"] == 1
    assert result.metadata["refinement_threshold"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# v16: UI ewin → CREST energy_window chain
# ---------------------------------------------------------------------------


def test_censo_ewin_from_method() -> None:
    from acp.scheduler.jobs import censo_ewin_from_method

    assert censo_ewin_from_method({"ewin": 4.5}) == pytest.approx(4.5)
    assert censo_ewin_from_method(
        {"levels": {"censo": {"engine": "censo", "ewin": 5.0}}}
    ) == pytest.approx(5.0)
    # explicit method.ewin wins over levels
    assert censo_ewin_from_method(
        {"ewin": 3.0, "levels": {"censo": {"ewin": 5.0}}}
    ) == pytest.approx(3.0)
    assert censo_ewin_from_method({}) is None
    assert censo_ewin_from_method({"ewin": "abc"}) is None
    assert censo_ewin_from_method({"ewin": 0}) is None
    assert censo_ewin_from_method({"ewin": -2}) is None


def test_resolve_crest_ewin_priority() -> None:
    from acp.workflows.ensemble import _resolve_crest_ewin

    cfg = _make_config()
    assert _resolve_crest_ewin(cfg, 4.5) == pytest.approx(4.5)
    # config fallback
    cfg["censo"]["ewin"] = 3.5
    assert _resolve_crest_ewin(cfg, None) == pytest.approx(3.5)
    # built-in default
    assert _resolve_crest_ewin(_make_config(), None) == pytest.approx(6.0)
    # invalid values fall through
    cfg["censo"]["ewin"] = "bad"
    assert _resolve_crest_ewin(cfg, None) == pytest.approx(6.0)
    cfg["censo"]["ewin"] = -1
    assert _resolve_crest_ewin(cfg, None) == pytest.approx(6.0)


def test_resolve_levels_crest_ewin_level() -> None:
    from acp.workflows.energy import _resolve_levels

    resolved = _resolve_levels(
        _make_config(), {"censo": {"engine": "censo", "ewin": 4.0}},
    )
    assert resolved["crest_ewin_level"] == pytest.approx(4.0)
    assert _resolve_levels(_make_config(), None)["crest_ewin_level"] is None
    assert _resolve_levels(
        _make_config(), {"censo": {"ewin": "bad"}},
    )["crest_ewin_level"] is None


def test_runner_build_cmd_ewin_from_ui_levels() -> None:
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="ensemble",
        name="etoh",
        input={"source": "CCO"},
        method={
            "profile_id": "censo-light",
            "levels": {"censo": {"engine": "censo", "ewin": 4.5}},
        },
    )
    stub = SimpleNamespace(python="python")
    cmd = JobRunner._build_cmd(stub, spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--ewin" in cmd
    assert cmd[cmd.index("--ewin") + 1] == "4.5"


def test_script_gen_ewin_parity() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={
            "profile_id": "censo-light",
            "levels": {"censo": {"engine": "censo", "ewin": 4.5}},
        },
    )
    cmd = build_remote_cli_command(spec, python_executable="python3")
    assert cmd[cmd.index("--ewin") + 1] == "4.5"


def test_cli_energy_accepts_ewin() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "energy", "--input", "CCO", "--ewin", "4.0"])
    assert args.ewin == pytest.approx(4.0)
    args2 = parser.parse_args(["run", "ensemble", "--input", "CCO", "--ewin", "3.0"])
    assert args2.ewin == pytest.approx(3.0)


def test_ensemble_ewin_reaches_crest(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch

    from acp.workflows.ensemble import run_ensemble_generation

    single_xyz = tmp_path / "mol.xyz"
    single_xyz.write_text("1\n-1.0\nH 0 0 0\n")

    def fake_search(**kwargs: Any):
        out_dir = Path(kwargs["output_dir"])
        ensemble = out_dir / "crest_conformers.xyz"
        ensemble.write_text(
            "1\n-1.00000000\nH 0 0 0\n1\n-1.00010000\nH 0 0 1\n"
        )
        return ensemble

    with patch("acp.workflows.ensemble.get_backend") as mock_get_backend:
        backend = MagicMock()
        backend.search.side_effect = fake_search
        mock_get_backend.return_value = MagicMock(return_value=backend)

        result = run_ensemble_generation(
            input_source=str(single_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=_make_config(),
            name="ewin_test",
            ewin=4.5,
        )

    assert result.status == "completed"
    mock_get_backend.assert_called_with("crest")
    assert backend.search.call_args.kwargs["energy_window"] == pytest.approx(4.5)
    assert result.metadata["crest_ewin"] == pytest.approx(4.5)


def test_energy_levels_ewin_reaches_crest(tmp_path: Path) -> None:
    from unittest.mock import MagicMock, patch

    import numpy as np

    from acp.workflows.energy import run_conformer_energy

    single_xyz = tmp_path / "mol.xyz"
    single_xyz.write_text("1\n-1.0\nH 0 0 0\n")

    def fake_search(**kwargs: Any):
        out_dir = Path(kwargs["output_dir"])
        ensemble = out_dir / "crest_conformers.xyz"
        ensemble.write_text("1\n-1.00000000\nH 0 0 0\n")
        return ensemble

    orca = MagicMock()
    orca.optimize.return_value = MagicMock(
        success=True, coordinates=np.zeros((1, 3)), symbols=["H"],
        energy=-1.0, log_file=Path("/tmp/opt.out"), error_message=None,
    )
    orca.frequency.return_value = MagicMock(
        success=True, log_file=Path("/tmp/freq.out"), error_message=None,
    )
    orca.single_point.return_value = MagicMock(
        success=True, energy=-1.1, log_file=Path("/tmp/sp.out"), error_message=None,
    )
    shermo_ok = {"g_sum": -1.05, "g_conc": None, "h_sum": -1.0,
                 "u_sum": -1.01, "s_total": 0.03}

    with (
        patch("acp.workflows.energy.get_backend") as mock_get_backend,
        patch("acp.workflows.energy_shared.get_backend", return_value=_mock_orca_backend_cls(orca)),
        patch("acp.workflows.energy_shared.run_shermo", return_value=dict(shermo_ok)),
    ):
        backend = MagicMock()
        backend.search.side_effect = fake_search
        mock_get_backend.return_value = MagicMock(return_value=backend)

        result = run_conformer_energy(
            input_source=str(single_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=_make_config(),
            name="ewin_lv",
            levels={"censo": {"engine": "censo", "ewin": 4.0}},
        )

    assert result.status == "completed"
    mock_get_backend.assert_called_with("crest")
    assert backend.search.call_args.kwargs["energy_window"] == pytest.approx(4.0)
    assert result.metadata["crest_ewin"] == pytest.approx(4.0)


# =====================================================================
# Phase 5.7: CENSO regression — aux_basis rename does not affect CENSO
# =====================================================================

def test_censo_template_path_not_affected_by_aux_basis_rename() -> None:
    """CENSO backend template injection path should not read aux_j_basis/aux_c_basis.

    CENSO uses its own template injection mechanism (censo_backend.py:589),
    which does not consume aux_j_basis/aux_c_basis fields. This test ensures
    the field rename does not break CENSO's template path.
    """
    from acp.workflows.energy_shared import _base_route_extras

    # CENSO template lines are built from _base_route_extras
    level = {
        "functional": "wB97M-V",
        "basis": "def2-TZVPP",
        "dispersion": "D4",
        "ri_approximation": "RIJCOSX",
        "aux_j_basis": "def2/J",
        "aux_c_basis": "",
        "grid": "UltraFine",
        "scf_convergence": "Tight",
    }
    extras = _base_route_extras(level)
    # CENSO template should include the route keywords
    assert "D4" in extras
    assert "RIJCOSX" in extras
    assert "def2/J" in extras
    assert "DEFGRID3" in extras
    assert "TightSCF" in extras
    # CENSO template line format
    template_line = "! " + " ".join(extras)
    assert template_line.startswith("! ")

def test_runner_build_cmd_energy_rank1_only() -> None:
    from dataclasses import replace

    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light", "rank1_only": True},
        resources={"nproc": 8},
    )
    stub = SimpleNamespace(python="python")
    cmd = JobRunner._build_cmd(stub, spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--rank1-only" in cmd
    # rank1_only must not leak into other workflows' commands
    ens_spec = replace(spec, workflow="ensemble")
    cmd = JobRunner._build_cmd(stub, ens_spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--rank1-only" not in cmd


def test_runner_build_cmd_energy_rank1_only_optout() -> None:
    """CLI defaults to rank1-only; an explicit False must forward --full-ensemble."""
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light", "rank1_only": False},
        resources={"nproc": 8},
    )
    stub = SimpleNamespace(python="python")
    cmd = JobRunner._build_cmd(stub, spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--full-ensemble" in cmd
    assert "--rank1-only" not in cmd
    # missing field → no flag → CLI default (rank1-only) applies
    spec2 = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light"},
        resources={"nproc": 8},
    )
    cmd2 = JobRunner._build_cmd(stub, spec2, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "--full-ensemble" not in cmd2
    assert "--rank1-only" not in cmd2


def test_script_gen_rank1_only_parity() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light", "rank1_only": True},
        resources={"nproc": 8},
    )
    cmd = build_remote_cli_command(
        spec,
        "inputs/input.xyz",
        python_executable="python",
    )
    assert "--rank1-only" in cmd


def test_script_gen_rank1_only_optout_parity() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="energy",
        name="etoh",
        input={"source": "CCO"},
        method={"profile_id": "censo-light", "rank1_only": False},
        resources={"nproc": 8},
    )
    cmd = build_remote_cli_command(
        spec,
        "inputs/input.xyz",
        python_executable="python",
    )
    assert "--full-ensemble" in cmd
    assert "--rank1-only" not in cmd
