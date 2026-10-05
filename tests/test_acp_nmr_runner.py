"""Tests for the NMR job materialisation in the scheduler runner.

Covers ``JobRunner._materialize_bruker_asset`` (asset resolution + zip
extraction), ``_build_nmr_cmd`` with a ``mode: "bruker"`` experiment
payload, the G06 emitted-argv regressions (local runner + remote
script_gen builders must produce argv that ``build_parser()`` accepts —
no ``--name``), the ``run_nmr_analysis`` → ``NmrConfig`` forwarding
of ``solvent_model`` / ``max_conformers``, and the T20 local ⇄ remote
parity + GUI closed loop (payload → argv → effective config → ORCA input).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

from acp.scheduler import jobs as scheduler_jobs
from acp.scheduler.jobs import JobSpec
from acp.scheduler.runner import JobRunner


def _make_runner() -> JobRunner:
    """Construct a JobRunner without running its full __init__."""
    runner = JobRunner.__new__(JobRunner)
    runner.python = "/usr/bin/python"
    return runner


def _write_bruker_zip(dest: Path, experiments: dict[str, dict]) -> None:
    """Create a zip containing Bruker experiment subdirs.

    Args:
        experiments: ``{"Proton": {...}}`` — each value is written as
            ``fid`` (int32) + ``acqus`` text inside the subdir.
    """
    with zipfile.ZipFile(dest, "w") as zf:
        for name, files in experiments.items():
            zf.writestr(f"{name}/acqus", files["acqus"])
            raw = files["fid"].astype("<i4")
            zf.writestr(f"{name}/fid", raw.tobytes())


def _acqus_text(nucleus: str, bf1: float, td: int = 32768) -> str:
    return (
        f"##$TD= {td}\n##$SFO1= {bf1}\n##$BF1= {bf1}\n"
        f"##$O1= {5.0 * bf1}\n##$SW_h= {10.0 * bf1}\n##$SW= 10.0\n"
        f"##$NUC1= <{nucleus}>\n##$BYTORDA= 0\n##$DTYPA= 0\n"
        "##$AQ_mod= 1\n##$DECIM= 1\n##$DSPFVS= 0\n##$GRPDLY= 0.0\n##END=\n"
    )


def test_materialize_bruker_asset_extracts_zip(tmp_path: Path) -> None:
    # Simulate the run_root layout: <run_root>/<proj>/uploads/<id>/original/f.zip
    run_root = tmp_path
    proj = "myproj"
    upload_id = "up_abc"
    asset_dir = run_root / proj / "uploads" / upload_id / "original"
    asset_dir.mkdir(parents=True)
    zip_path = asset_dir / "nmr.zip"

    fid = np.zeros(32768, dtype=np.int32)
    _write_bruker_zip(
        zip_path,
        {"Proton": {"acqus": _acqus_text("1H", 500.13), "fid": fid}},
    )

    # work_dir = run_root / "jobs" / "job1"  →  parent.parent = run_root
    work_dir = run_root / "jobs" / "job1"
    inputs_dir = work_dir / "inputs"

    extracted = JobRunner._materialize_bruker_asset(
        experiment={
            "mode": "bruker",
            "spectrum_asset_id": upload_id,
            "filename": "nmr.zip",
            "project_id": proj,
        },
        inputs_dir=inputs_dir,
        work_dir=work_dir,
    )
    assert extracted is not None
    assert (extracted / "Proton" / "fid").is_file()
    assert (extracted / "Proton" / "acqus").is_file()


def test_materialize_bruker_asset_rejects_traversal(tmp_path: Path) -> None:
    run_root = tmp_path
    asset_dir = run_root / "p" / "uploads" / "id" / "original"
    asset_dir.mkdir(parents=True)
    evil = asset_dir / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "boom")
    work_dir = run_root / "jobs" / "j1"
    import pytest

    with pytest.raises(ValueError, match="Unsafe path"):
        JobRunner._materialize_bruker_asset(
            experiment={"mode": "bruker", "spectrum_asset_id": "id",
                        "filename": "evil.zip", "project_id": "p"},
            inputs_dir=work_dir / "inputs",
            work_dir=work_dir,
        )


def test_materialize_bruker_asset_missing_returns_none(tmp_path: Path) -> None:
    result = JobRunner._materialize_bruker_asset(
        experiment=None,
        inputs_dir=tmp_path / "inputs",
        work_dir=tmp_path,
    )
    assert result is None

    result = JobRunner._materialize_bruker_asset(
        experiment={"mode": "bruker"},
        inputs_dir=tmp_path / "inputs",
        work_dir=tmp_path,
    )
    assert result is None


def test_build_nmr_cmd_bruer_mode(tmp_path: Path) -> None:
    """_build_nmr_cmd emits --bruker when experiment.mode == 'bruker'."""
    runner = _make_runner()
    work_dir = tmp_path / "work"

    # Set up the asset
    run_root = work_dir.parent.parent if work_dir.parent.parent.exists() else tmp_path
    # work_dir.parent.parent may not resolve correctly; build explicitly
    run_root = tmp_path
    jobs_dir = run_root / "jobs"
    job_dir = jobs_dir / "job1"
    asset_dir = run_root / "p" / "uploads" / "id" / "original"
    asset_dir.mkdir(parents=True)
    zip_path = asset_dir / "nmr.zip"
    fid = np.zeros(32768, dtype=np.int32)
    _write_bruker_zip(
        zip_path,
        {"Proton": {"acqus": _acqus_text("1H", 500.13), "fid": fid}},
    )

    spec = JobSpec(
        workflow="nmr",
        name="nmr_bruker_test",
        input={
            "source_type": "candidates",
            "candidates": [{"source_type": "smiles", "source": "CCO"}],
            "experiment": {
                "mode": "bruker",
                "spectrum_asset_id": "id",
                "filename": "nmr.zip",
                "project_id": "p",
            },
        },
        method={},
        resources={},
    )

    cmd = runner._build_nmr_cmd(spec, job_dir)
    assert "--bruker" in cmd
    bruker_idx = cmd.index("--bruker")
    assert "bruker" in cmd[bruker_idx + 1]
    # --spectrum must NOT be present in bruker mode
    assert "--spectrum" not in cmd


def _nmr_spec(name: str) -> JobSpec:
    """Scheduler-shaped nmr spec — manager.py always sets a non-empty name."""
    return JobSpec(
        workflow="nmr",
        name=name,
        input={
            "source_type": "candidates",
            "candidates": [{"source_type": "smiles", "source": "CCO"}],
            "experiment": {"mode": "assigned", "content": "C: 40.0(C1)"},
        },
        method={},
        resources={},
    )


def test_build_nmr_cmd_parses_via_cli_parser(tmp_path: Path) -> None:
    """G06 regression: the local runner's nmr argv parses — no ``--name``."""
    from acp.cli import build_parser

    runner = _make_runner()
    work_dir = tmp_path / "20261005_001_nmr_task"
    spec = _nmr_spec(work_dir.name)  # manager sets spec.name = work_dir.name

    cmd = runner._build_nmr_cmd(spec, work_dir)

    assert "--name" not in cmd
    assert cmd[3:5] == ["run", "nmr"]
    parsed = build_parser().parse_args(cmd[3:])  # drop '<python> -m acp.cli'
    assert parsed.workflow == "nmr"


def test_remote_nmr_cmd_parses_via_cli_parser() -> None:
    """G06 regression: the remote script_gen nmr argv parses — no ``--name``."""
    from acp.cli import build_parser
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = _nmr_spec("20261005_001_nmr_task")
    cmd = build_remote_cli_command(spec, input_path="inputs/input_0.xyz")

    assert "--name" not in cmd
    assert cmd[3:5] == ["run", "nmr"]
    parsed = build_parser().parse_args(cmd[3:])  # drop '<python> -m acp.cli'
    assert parsed.workflow == "nmr"


def test_run_nmr_analysis_forwards_solvent_model_and_max_conformers(
    tmp_path: Path, monkeypatch
) -> None:
    """CLI values reach NmrConfig through _build_nmr_config (T21 extends)."""
    import pytest

    from acp.workflows import nmr as nmr_mod

    class _ConfigCapturedError(Exception):
        pass

    built: list = []
    real_build = nmr_mod._build_nmr_config

    def _spy(cfg, **kwargs):
        built.append(real_build(cfg, **kwargs))
        raise _ConfigCapturedError

    monkeypatch.setattr(nmr_mod, "_build_nmr_config", _spy)

    with pytest.raises(_ConfigCapturedError):
        nmr_mod.run_nmr_analysis(
            input_sources=["CCO"],
            spectrum="C: 40.0(C1)",
            output_dir=str(tmp_path / "gas"),
            solvent_model="none",
            max_conformers=5,
        )
    assert built[-1].solvent_model == "none"
    assert built[-1].max_conformers == 5

    with pytest.raises(_ConfigCapturedError):
        nmr_mod.run_nmr_analysis(
            input_sources=["CCO"],
            spectrum="C: 40.0(C1)",
            output_dir=str(tmp_path / "defaults"),
        )
    assert built[-1].solvent_model == "cpcm"
    assert built[-1].max_conformers == 10


# ── T19: nmr_method_flags consumes the single resolver (G06) ────────────


def test_nmr_method_flags_custom_payload_emits_resolver_flags() -> None:
    """{functional, basis, solvent_model} (wizard shape) → non-empty argv."""
    from acp.scheduler.jobs import nmr_method_flags

    flags = nmr_method_flags({"functional": "B3LYP", "basis": "def2-TZVP", "solvent_model": "smd"})
    assert flags  # BEFORE repro: [] (flat nmr_method/nmr_basis keys absent)
    assert flags[flags.index("--nmr-method") + 1] == "B3LYP"
    assert flags[flags.index("--nmr-basis") + 1] == "def2-TZVP"
    assert flags[flags.index("--solvent-model") + 1] == "smd"


def test_nmr_method_flags_empty_payload_omits_everything() -> None:
    """Missing/empty fields → no flags, no error (defaults stay CLI-side)."""
    from acp.scheduler.jobs import nmr_method_flags

    assert nmr_method_flags({}) == []
    # legacy flat keys equal to the resolver defaults stay omitted too —
    # the resolver owns precedence, there is no second emission path
    assert nmr_method_flags({"nmr_method": "mPW1PW91", "nmr_basis": "6-311G(d)"}) == []
    assert nmr_method_flags({"nmr_method": "", "basis": None, "nuclei": []}) == []


def test_nmr_method_flags_joins_nuclei_comma() -> None:
    """Nuclei list → comma-joined ``--nuclei`` value (as today)."""
    from acp.scheduler.jobs import nmr_method_flags

    flags = nmr_method_flags({"nuclei": ["13C", "1H"]})
    assert flags[flags.index("--nuclei") + 1] == "13C,1H"


def test_nmr_method_flags_gas_phase_emits_solvent_model_none() -> None:
    """solvent_model=none emits ``--solvent-model none`` (empty solvent dropped)."""
    from acp.scheduler.jobs import nmr_method_flags

    flags = nmr_method_flags({"solvent_model": "none"})
    assert flags == ["--solvent-model", "none"]
    assert "--solvent" not in flags


def test_nmr_method_flags_legacy_flat_keys_still_win() -> None:
    """Flat nmr_method/nmr_basis keep top precedence through the resolver."""
    from acp.scheduler.jobs import nmr_method_flags

    flags = nmr_method_flags({"nmr_method": "B3LYP", "nmr_basis": "def2-TZVP"})
    assert flags[flags.index("--nmr-method") + 1] == "B3LYP"
    assert flags[flags.index("--nmr-basis") + 1] == "def2-TZVP"


# ── T20: local ⇄ remote nmr argv parity + GUI closed loop (G06) ──────────
# Both nmr builders must emit exactly the resolver-backed flag group
# (``jobs.nmr_method_flags``) — no caller-side censo_* duplication — and
# one GUI payload must yield the same parsed namespace, the same effective
# config, and the same ORCA input on the local and remote paths.

_NMR_FLAG_VOCAB: frozenset[str] = frozenset(
    {"--nuclei"} | {flag for _field, flag in scheduler_jobs._NMR_FLAG_FIELDS}
)
# Host-specific by construction: local materialises absolute paths under
# the task work dir, remote stages ``inputs/...`` + ``--output .``.
_ENV_SPECIFIC_ARGV_KEYS = ("output", "input", "spectrum")
# Kwargs ``_handle_nmr`` forwards into ``run_nmr_analysis`` that
# ``_build_nmr_config`` accepts (→ NmrConfig) — plus ``ewin`` for parity.
_NMR_CONFIG_KWARGS = (
    "nuclei",
    "nmr_method",
    "nmr_basis",
    "solvent",
    "boltzmann_temp",
    "tms_1h",
    "tms_13c",
    "error_model",
    "conformer_preset",
    "solvent_model",
    "max_conformers",
)
_PARITY_KEYS = _NMR_CONFIG_KWARGS + ("ewin",)

# Wizard-shaped payload: hoisted preset/solvent + nested levels — BEFORE
# T20 the caller-side censo_* trio duplicated --preset/--solvent/--ewin
# after the resolver group.
_CUSTOM_PAYLOAD: dict = {
    "schema_id": "nmr",
    "profile_id": "nmr-goodman",
    "preset": "censo-default",
    "solvent": "water",
    "ewin": 3.5,
    "levels": {
        "giaoa": {"functional": "B3LYP", "basis": "def2-TZVP", "solvent_model": "smd"},
        "conformer": {"ewin": 3.5},
    },
}


class _AnalysisCapturedError(BaseException):
    """Sentinel escaping ``_handle_nmr``'s broad ``except Exception``."""


def _nmr_gui_spec(method: dict, name: str = "20261006_001_nmr_parity") -> JobSpec:
    """GUI-shaped nmr spec: hoisted charge/multiplicity + wizard payload."""
    return JobSpec(
        workflow="nmr",
        name=name,
        input={
            "source_type": "candidates",
            "candidates": [{"source_type": "smiles", "source": "CCO"}],
            "experiment": {"mode": "assigned", "content": "C: 40.0(C1)"},
            "charge": 0,
            "multiplicity": 1,
        },
        method=method,
        resources={},
    )


def _nmr_argv_pair(method: dict, tmp_path: Path) -> tuple[list[str], list[str]]:
    """Build ``(local_argv, remote_argv)`` from ONE payload via both builders."""
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    runner = _make_runner()
    spec = _nmr_gui_spec(method)
    local = runner._build_nmr_cmd(spec, tmp_path / spec.name)
    remote = build_remote_cli_command(spec, input_path="inputs/input_0.xyz")
    return local, remote


def _nmr_flag_fragment(argv: list[str]) -> list[str]:
    """Extract the resolver-owned flag pairs from a full argv (order kept)."""
    fragment: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _NMR_FLAG_VOCAB:
            fragment += [token, argv[index + 1]]
            index += 2
        else:
            index += 1
    return fragment


def _parsed_namespaces(local: list[str], remote: list[str]) -> tuple[dict, dict]:
    """Parse BOTH full argvs with ``build_parser``; drop host-specific keys."""
    from acp.cli import build_parser

    ns_local = vars(build_parser().parse_args(local[3:]))
    ns_remote = vars(build_parser().parse_args(remote[3:]))
    for key in _ENV_SPECIFIC_ARGV_KEYS:
        ns_local.pop(key, None)
        ns_remote.pop(key, None)
    return ns_local, ns_remote


def test_nmr_local_remote_argv_parity_default_payload(tmp_path: Path) -> None:
    """Default payload: parsed namespaces and resolver fragments are equal."""
    from acp.scheduler.jobs import nmr_method_flags

    local, remote = _nmr_argv_pair({}, tmp_path)
    ns_local, ns_remote = _parsed_namespaces(local, remote)
    assert ns_local == ns_remote
    expected = nmr_method_flags({})
    assert _nmr_flag_fragment(local[3:]) == expected
    assert _nmr_flag_fragment(remote[3:]) == expected


def test_nmr_local_remote_argv_parity_custom_payload(tmp_path: Path) -> None:
    """Custom payload: argv carries EXACTLY the resolver group (no dupes)."""
    from collections import Counter

    from acp.scheduler.jobs import nmr_method_flags

    local, remote = _nmr_argv_pair(dict(_CUSTOM_PAYLOAD), tmp_path)
    ns_local, ns_remote = _parsed_namespaces(local, remote)
    assert ns_local == ns_remote

    expected = nmr_method_flags(dict(_CUSTOM_PAYLOAD))
    assert expected  # wizard payload must emit a non-empty resolver group
    # RED before T20: the caller-side censo_* trio appended a second
    # --preset/--solvent/--ewin after the resolver group.
    assert _nmr_flag_fragment(local[3:]) == expected
    assert _nmr_flag_fragment(remote[3:]) == expected
    for argv in (local[3:], remote[3:]):
        counts = Counter(token for token in argv if token in _NMR_FLAG_VOCAB)
        assert all(count == 1 for count in counts.values()), counts


def _effective_config_from_argv(argv_tail: list[str], monkeypatch, workdir: Path) -> dict:
    """Run the real ``_handle_nmr`` on a parsed argv and capture the kwargs.

    The captured kwargs are exactly what T18's handler forwards into
    ``run_nmr_analysis`` — i.e. the resolved effective config the CLI
    would execute with.
    """
    from acp.cli import _handle_nmr, build_parser
    from acp.workflows import nmr as nmr_workflow

    parsed = build_parser().parse_args(argv_tail)
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        raise _AnalysisCapturedError

    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)  # the remote argv uses ``--output .``
    monkeypatch.setattr(nmr_workflow, "run_nmr_analysis", _capture)
    with pytest.raises(_AnalysisCapturedError):
        _handle_nmr(parsed)
    assert captured, "run_nmr_analysis was never reached"
    return captured


def _nmr_config_from_captured(captured: dict):
    """Reconstruct the workflow ``NmrConfig`` from captured handler kwargs."""
    from acp.workflows.nmr import _build_nmr_config

    return _build_nmr_config(
        captured["config"], **{key: captured[key] for key in _NMR_CONFIG_KWARGS}
    )


def _closed_loop(method: dict, tmp_path: Path, monkeypatch) -> tuple[dict, str]:
    """GUI payload → local+remote argv → effective config → ORCA input.

    Returns the captured local effective config and the ORCA input text
    (asserted identical for the local and remote argv — same payload, one
    effective config, one ORCA input).
    """
    from tests.test_acp_workflows_nmr import _run_giao_capture  # T21 harness

    local, remote = _nmr_argv_pair(method, tmp_path / "argv")
    cap_local = _effective_config_from_argv(local[3:], monkeypatch, tmp_path / "eff_local")
    cap_remote = _effective_config_from_argv(remote[3:], monkeypatch, tmp_path / "eff_remote")
    assert {k: cap_local[k] for k in _PARITY_KEYS} == {k: cap_remote[k] for k in _PARITY_KEYS}
    _, local_input = _run_giao_capture(
        _nmr_config_from_captured(cap_local), tmp_path / "giao_local"
    )
    _, remote_input = _run_giao_capture(
        _nmr_config_from_captured(cap_remote), tmp_path / "giao_remote"
    )
    assert local_input == remote_input  # identical ORCA input on both paths
    return cap_local, local_input


def test_nmr_closed_loop_default_payload(tmp_path: Path, monkeypatch) -> None:
    """Default payload → builtin effective config → default ORCA input."""
    cap, orca_input = _closed_loop({}, tmp_path, monkeypatch)
    assert cap["nmr_method"] == "mPW1PW91"
    assert cap["nmr_basis"] == "6-311G(d)"
    assert cap["solvent_model"] == "cpcm"
    assert cap["solvent"] == "chloroform"
    assert cap["ewin"] == 6.0
    assert "! mPW1PW91 6-311G(d)" in orca_input
    assert "! CPCM(chloroform)" in orca_input


def test_nmr_closed_loop_custom_payload(tmp_path: Path, monkeypatch) -> None:
    """Wizard payload → custom method/basis/solvent in the ORCA input."""
    cap, orca_input = _closed_loop(dict(_CUSTOM_PAYLOAD), tmp_path, monkeypatch)
    assert cap["nmr_method"] == "B3LYP"
    assert cap["nmr_basis"] == "def2-TZVP"
    assert cap["solvent_model"] == "smd"
    assert cap["solvent"] == "water"
    assert cap["ewin"] == 3.5
    assert cap["conformer_preset"] == "censo-default"
    assert "! B3LYP def2-TZVP" in orca_input
    assert "! SMD(Water)" in orca_input


def test_nmr_closed_loop_gas_phase_payload(tmp_path: Path, monkeypatch) -> None:
    """solvent_model=none flows through argv → config → no cpcm in input."""
    method = {
        "schema_id": "nmr",
        "levels": {
            "giaoa": {
                "functional": "B3LYP",
                "basis": "def2-TZVP",
                "solvent_model": "none",
            }
        },
    }
    cap, orca_input = _closed_loop(method, tmp_path, monkeypatch)
    assert cap["solvent_model"] == "none"
    assert cap["solvent"] == ""
    assert "! B3LYP def2-TZVP" in orca_input
    lowered = orca_input.lower()
    assert "cpcm" not in lowered
    assert "smd" not in lowered
