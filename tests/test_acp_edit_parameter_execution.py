"""Edited values must reach executable input, rather than stale UI mirrors."""

from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from acp.api.v1_routes import _build_edited_spec
from acp.api.v1_schemas import V1EditPreviewRequest
from acp.catalog import METHOD_SCHEMAS
from acp.cli import _build_simple_method_kwargs, build_parser
from acp.scheduler.edit_parameters import apply_edited_parameters, method_projection_for_edit
from acp.scheduler.job_edit import EditValidationError, build_edit_draft
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus, batchoptimize_method_flags
from acp.scheduler.remote.script_gen import build_remote_cli_command
from acp.scheduler.runner import JobRunner

XYZ = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"


def record(spec: JobSpec, root: Path) -> JobRecord:
    return JobRecord(id="edit_test", spec=spec, status=JobStatus.FAILED, work_dir=str(root))


def edit(spec: JobSpec, root: Path, **patch: object) -> JobSpec:
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace())})
    manager = SimpleNamespace(run_root=root)
    edited, _ = _build_edited_spec(
        record(spec, root), V1EditPreviewRequest(mode="in_place", **patch), request, manager
    )
    return edited


@pytest.mark.parametrize(
    "workflow,schema",
    [
        ("singlepoint", "dft_singlepoint"),
        ("optimize", "dft_optimize"),
        ("frequency", "dft_frequency"),
        ("scan", "dft_scan"),
    ],
)
def test_simple_catalog_profile_command_parses_and_carries_scf_grid(
    tmp_path: Path,
    workflow: str,
    schema: str,
) -> None:
    profiles = METHOD_SCHEMAS[schema]["profiles"]
    levels = deepcopy(profiles[0]["levels"] if profiles else {workflow: {"engine": "orca"}})
    level = next(iter(levels.values()))
    level.update(grid="DEFGRID3", scf_convergence="VeryTight")
    spec = JobSpec(
        workflow=workflow,
        input={
            "source": XYZ,
            "source_type": "xyz_text",
            "charge": -1,
            "multiplicity": 2,
            "scan_coordinates": ["0,1,1,2"],
        },
        method={"levels": levels},
    )
    local = JobRunner()._build_cmd(spec, tmp_path, "input.xyz")
    remote = build_remote_cli_command(spec, input_path="input.xyz")
    for command in (local, remote):
        args = build_parser().parse_args(command[3:])
        kwargs = _build_simple_method_kwargs(args)
        assert kwargs["scf_convergence"] == "VeryTight"
        assert kwargs["grid"] == "DEFGRID3"
        assert (args.charge, args.multiplicity) == (-1, 2)


def test_unmodified_optimize_default_profile_no_longer_exits_two(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="optimize",
        input={"source": XYZ},
        method={
            "levels": deepcopy(METHOD_SCHEMAS["dft_optimize"]["profiles"][0]["levels"]),
        },
    )
    command = JobRunner()._build_cmd(spec, tmp_path, "input.xyz")
    args = build_parser().parse_args(command[3:])
    assert _build_simple_method_kwargs(args)["scf_convergence"] == "Tight"


def test_simple_cli_scf_grid_reach_qc_backend(fake_backend, tmp_path: Path) -> None:
    from acp.workflows.simple import run_singlepoint

    source = tmp_path / "water.xyz"
    source.write_text(XYZ)
    spec = JobSpec(
        workflow="singlepoint",
        method={
            "levels": {
                "single_point": {
                    "functional": "B3LYP",
                    "basis": "def2-SVP",
                    "grid": "DEFGRID3",
                    "scf_convergence": "VeryTight",
                }
            }
        },
    )
    command = JobRunner()._build_cmd(spec, tmp_path, str(source))
    args = build_parser().parse_args(command[3:])
    result = run_singlepoint(
        str(source), output_dir=tmp_path / "sp", method_kwargs=_build_simple_method_kwargs(args)
    )
    assert result.status == "completed"
    assert fake_backend.calls[0].kwargs["grid"] == "DEFGRID3"
    assert fake_backend.calls[0].kwargs["scf_convergence"] == "VeryTight"


@pytest.mark.parametrize(
    "workflow", ["singlepoint", "optimize", "frequency", "casscf", "xtb_optimize"]
)
def test_partial_charge_edit_preserves_method_and_execution_state(
    tmp_path: Path, workflow: str
) -> None:
    levels = (
        {"casscf": {"cas_active_electrons": 2, "cas_active_orbitals": 2}}
        if workflow == "casscf"
        else {}
    )
    spec = JobSpec(
        workflow=workflow,
        input={"source_type": "xyz_text", "source": XYZ, "charge": 0, "multiplicity": 1},
        method={"levels": levels},
        resources={"nproc": 8, "mem": "16GB"},
        remark="keep",
        tags=["chemistry"],
        node_tags=["orca"],
    )
    edited = edit(spec, tmp_path, input={"charge": -1, "multiplicity": 2})
    assert edited.input["source"] == XYZ
    assert edited.method == spec.method
    assert edited.resources == spec.resources
    assert edited.remark == "keep" and edited.tags == ["chemistry"] and edited.node_tags == ["orca"]
    args = build_parser().parse_args(JobRunner()._build_cmd(edited, tmp_path, "input.xyz")[3:])
    assert (args.charge, args.multiplicity) == (-1, 2)


def test_scan_actual_payload_hydrates_then_edit_overwrites_execution_aliases(
    tmp_path: Path,
) -> None:
    spec = JobSpec(
        workflow="scan",
        input={"source_type": "xyz_text", "source": XYZ, "scan_coordinates": ["0,1,1.2,2.8"]},
        method={
            "scan_points": 7,
            "levels": {
                "scan": {
                    "engine": "orca",
                    "functional": "r2SCAN-3c",
                    "scan_coordinate_start": 1,
                    "scan_coordinate_end": 3,
                    "scan_coordinate_points": 21,
                }
            },
        },
    )
    projection = method_projection_for_edit(spec)
    level = projection["levels"]["scan"]
    assert (
        level["scan_coordinate_start"],
        level["scan_coordinate_end"],
        level["scan_coordinate_points"],
    ) == (1.2, 2.8, 7)
    assert level["scan_coordinate_atoms"] == [1, 2]
    level.update(
        scan_coordinate_atoms=[2, 3],
        scan_coordinate_start=1.5,
        scan_coordinate_end=2.5,
        scan_coordinate_points=11,
        scan_use_scants=True,
    )
    edited = edit(spec, tmp_path, method=projection)
    for command in (
        JobRunner()._build_cmd(edited, tmp_path, "input.xyz"),
        build_remote_cli_command(edited, input_path="input.xyz"),
    ):
        args = build_parser().parse_args(command[3:])
        assert args.coordinate == ["1,2,1.5,2.5"]
        assert args.scan_points == 11 and args.scants is True


def test_scan_rejects_unimplemented_pes_controls(tmp_path: Path) -> None:
    spec = JobSpec(workflow="scan", input={"source": XYZ, "scan_coordinates": ["0,1,1,2"]})
    with pytest.raises(EditValidationError, match="PES"):
        edit(spec, tmp_path, method={"levels": {"scan": {"scan_retry_count": 8}}})


def test_partial_scan_edit_does_not_reapply_stale_unedited_mirrors(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="scan",
        input={"source_type": "xyz_text", "source": XYZ, "scan_coordinates": ["0,1,1.2,2.8"]},
        method={
            "scan_points": 7,
            "levels": {
                "scan": {
                    "scan_coordinate_start": 1,
                    "scan_coordinate_end": 3,
                    "scan_coordinate_points": 21,
                }
            },
        },
    )
    edited = edit(spec, tmp_path, method={"levels": {"scan": {"scan_coordinate_end": 2.5}}})
    args = build_parser().parse_args(JobRunner()._build_cmd(edited, tmp_path, "input.xyz")[3:])
    assert args.coordinate == ["0,1,1.2,2.5"] and args.scan_points == 7


def test_coupled_scan_draft_shows_actual_first_coordinate_and_rejects_partial_range(
    tmp_path: Path,
) -> None:
    spec = JobSpec(
        workflow="scan",
        input={
            "source_type": "xyz_text",
            "source": XYZ,
            "scan_coordinates": ["0,1,1.2,2.8", "0,2,1.3,2.6"],
        },
        method={"levels": {"scan": {"scan_coordinate_start": 1.0}}},
    )
    draft = build_edit_draft(record(spec, tmp_path))
    level = draft["method_projection"]["levels"]["scan"]
    assert level["scan_coordinate_start"] == 1.2
    assert level["scan_coordinate_end"] == 2.8
    assert level["scan_coordinate_atoms"] == [1, 2]
    assert any("2 个联动扫描坐标" in note for note in draft["notes"])
    with pytest.raises(EditValidationError, match="耦合 scan"):
        edit(
            spec,
            tmp_path,
            method={"levels": {"scan": {"scan_coordinate_start": 1.0}}},
            method_projection_changed=True,
        )


def test_scan_explicit_edit_to_stale_mirror_value_updates_actual_input(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="scan",
        input={
            "source_type": "xyz_text",
            "source": XYZ,
            "scan_coordinates": ["0,1,1.2,2.8"],
        },
        method={"levels": {"scan": {"scan_coordinate_start": 1.0}}},
    )
    unchanged = edit(spec, tmp_path, method=deepcopy(spec.method))
    assert unchanged.input == spec.input
    edited = edit(
        spec,
        tmp_path,
        method=deepcopy(spec.method),
        method_projection_changed=True,
    )
    assert edited.input["scan_coordinates"][0]["start"] == 1.0
    assert edited.input["scan_coordinates"][0]["end"] == 2.8


def test_explicit_empty_edit_sections_clear_but_missing_sections_preserve(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="singlepoint",
        input={"source_type": "xyz_text", "source": XYZ},
        method={"levels": {"single_point": {"functional": "r2SCAN-3c"}}},
        resources={"nproc": 8, "mem": "16GB"},
        tags=["tag"],
        node_tags=["orca"],
        remark="old",
    )
    edited = edit(spec, tmp_path, resources={}, method={}, tags=[], node_tags=[], remark="")
    assert edited.input == spec.input
    assert edited.method == {} and edited.resources == {}
    assert edited.tags == [] and edited.node_tags == [] and edited.remark == ""


def test_replacing_geometry_does_not_merge_the_previous_source_reference(tmp_path: Path) -> None:
    original = JobSpec(
        workflow="singlepoint",
        input={
            "source_type": "xyz_text",
            "source": XYZ,
            "source_job_id": "old_job",
            "from_artifact": "RESULT/old.xyz",
            "source_ref": {"source_id": "job_old_job:RESULT/old.xyz"},
        },
    )
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace())})
    manager = SimpleNamespace(run_root=tmp_path)
    spec, _ = _build_edited_spec(
        record(original, tmp_path),
        V1EditPreviewRequest(
            mode="new_job",
            input={"source_type": "xyz_text", "source": XYZ.replace("H 0 1 0", "H 0 1.2 0")},
        ),
        request,
        manager,
    )
    assert "source_ref" not in spec.input and "source_job_id" not in spec.input
    assert "from_artifact" not in spec.input


def test_pes_edits_rebuild_serialized_scan_request(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="PESsearch",
        input={
            "source": {"source_type": "xyz_text", "xyz_text": XYZ, "charge": 0, "multiplicity": 1},
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.4,
                "n_points": 9,
            },
            "protocol": {
                "scan_optimizer": {"method": "GFN2-xTB"},
                "single_point": {"method": "B97-3c", "enabled": True},
            },
        },
        method={"mode": "bond_length_scan", "profile_id": "default"},
    )
    projected = method_projection_for_edit(spec)
    assert projected["levels"]["scan_coordinate"]["scan_coordinate_points"] == 9
    projected["levels"]["scan_coordinate"].update(
        scan_coordinate_end=2.9, scan_coordinate_points=13
    )
    projected["levels"]["scan_optimizer"].update(
        scan_optimizer_method="B3LYP",
        scan_optimizer_basis="def2-SVP",
        scan_optimizer_dispersion="D3BJ",
        scan_optimizer_scf_convergence="Tight",
    )
    projected["levels"]["single_point"].update(functional="PBE0", basis="def2-TZVP")
    edited = edit(spec, tmp_path, method=projected, input={"charge": 1, "multiplicity": 2})
    JobRunner()._build_cmd(edited, tmp_path, "input.xyz")
    payload = json.loads((tmp_path / "scan_config.json").read_text())
    assert payload["coordinate"]["n_points"] == 13 and payload["coordinate"]["end"] == 2.9
    assert payload["protocol"]["scan_optimizer"]["method"] == "B3LYP"
    assert payload["protocol"]["scan_optimizer"]["basis"] == "def2-SVP"
    assert payload["protocol"]["single_point"]["method"] == "PBE0"
    assert payload["source"]["charge"] == 1 and payload["source"]["multiplicity"] == 2
    assert payload["protocol"]["single_point"]["charge"] == 1


def test_partial_pes_edit_preserves_singlepoint_and_can_rebuild_the_mirror(tmp_path: Path) -> None:
    original = JobSpec(
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        input={
            "source": {"source_type": "xyz_text", "xyz_text": XYZ, "charge": 0, "multiplicity": 1},
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.4,
                "n_points": 9,
            },
            "protocol": {
                "scan_optimizer": {"method": "GFN2-xTB"},
                "single_point": {"enabled": True, "method": "B97-3c"},
            },
        },
    )
    prepared = edit(original, tmp_path)
    edited = edit(
        prepared,
        tmp_path,
        method={
            "levels": {
                "scan_coordinate": {
                    "scan_coordinate_end": 2.9,
                }
            }
        },
        input={"scan_request": None, "coordinates": None},
    )
    assert edited.input["scan_request"]["coordinate"]["end"] == 2.9
    assert edited.input["scan_request"]["protocol"]["single_point"]["enabled"] is True
    assert edited.input["scan_request"]["protocol"]["scan_optimizer"]["method"] == "GFN2-xTB"


def test_confsearch_energy_window_can_be_cleared(tmp_path: Path) -> None:
    from acp.scheduler.jobs import censo_ewin_from_method

    original = JobSpec(
        workflow="Confsearch",
        input={"source_type": "xyz_text", "source": XYZ},
        method={"protocol": "xtb-crest", "ewin": 6},
    )
    edited = edit(original, tmp_path, method={"levels": {"confsearch": {"ewin": ""}}})
    assert edited.method["ewin"] == "" and censo_ewin_from_method(edited.method) is None
    assert "--ewin" not in JobRunner()._build_cmd(edited, tmp_path, "input.xyz")


def test_irc_edit_updates_flat_knobs_before_source_verification() -> None:
    spec = JobSpec(workflow="irc", method={"maxpoints": 45, "step": 0.08})
    projection = method_projection_for_edit(spec)
    assert projection["levels"]["irc"]["maxpoints"] == 45
    projection["levels"]["irc"].update(maxpoints=60, step=0.2)
    method, _ = apply_edited_parameters(spec, projection, {})
    assert method["maxpoints"] == 60 and method["step"] == 0.2


def test_nmr_edit_overwrites_flat_aliases_and_resolver_reads_new_values(tmp_path: Path) -> None:
    from acp.nmr.method_config import resolve_nmr_method

    spec = JobSpec(
        workflow="nmr",
        method={
            "functional": "mPW1PW91",
            "nmr_basis": "6-311G(d)",
            "basis": "6-311G(d)",
            "solvent_model": "cpcm",
            "solvent": "chloroform",
            "nuclei": ["1H", "13C"],
            "boltzmann_temp": 300,
            "levels": {"giaoa": {"engine": "orca", "boltzmann_temp": 298.15}},
        },
    )
    projected = method_projection_for_edit(spec)
    assert projected["levels"]["giaoa"]["boltzmann_temp"] == 300
    projected["levels"]["giaoa"].update(
        boltzmann_temp=310, nuclei=["13C"], solvent_model="none", solvent=""
    )
    edited = edit(spec, tmp_path, method=projected)
    effective = resolve_nmr_method(edited.method, {})
    assert effective.boltzmann_temp == 310 and effective.nuclei == ("13C",)
    assert effective.solvent_model == "none" and effective.solvent == ""


def test_nmr_candidates_have_distinct_files_and_executable_metadata(tmp_path: Path) -> None:
    from acp.workflows.nmr import _parse_candidates

    second = XYZ.replace("H 0 1 0", "H 0 1.2 0")
    spec = JobSpec(
        workflow="nmr",
        input={
            "candidates": [
                {
                    "source_type": "xyz_text",
                    "source": XYZ,
                    "name": "first",
                    "charge": -1,
                    "multiplicity": 2,
                },
                {
                    "source_type": "xyz_text",
                    "source": second,
                    "name": "second",
                    "charge": 0,
                    "multiplicity": 1,
                },
            ],
            "experiment": {"mode": "assigned", "content": "13C\nC1 20.1"},
        },
    )
    args = build_parser().parse_args(JobRunner()._build_cmd(spec, tmp_path, "input.xyz")[3:])
    assert len(set(args.input)) == 2
    assert Path(args.input[0]).read_text() != Path(args.input[1]).read_text()
    candidates = _parse_candidates(
        args.input,
        args.charge,
        args.multiplicity,
        candidate_metadata=json.loads(args.candidate_metadata_json),
    )
    assert [(c.id, c.charge, c.multiplicity) for c in candidates] == [
        ("first", -1, 2),
        ("second", 0, 1),
    ]


def test_nmr_duplicate_candidate_names_keep_independent_execution_ids(tmp_path: Path) -> None:
    from acp.workflows.nmr import _parse_candidates

    source = tmp_path / "water.xyz"
    source.write_text(XYZ)
    candidates = _parse_candidates(
        [str(source)] * 3,
        0,
        1,
        candidate_metadata=[{"name": name} for name in ("water", "water", "water__02")],
    )
    assert candidates[0].id == "water"
    assert len({candidate.id for candidate in candidates}) == 3
    assert all(candidate.charge == 0 and candidate.multiplicity == 1 for candidate in candidates)


def test_batch_electronic_state_and_keywords_reach_cli(tmp_path: Path) -> None:
    module = {
        "execution_mode": "single",
        "states": [{"state_id": "triplet", "target_multiplicity": 3, "spin_mode": "unrestricted"}],
    }
    spec = JobSpec(
        workflow="BatchOptimize",
        input={"source_type": "xyz_text", "source": XYZ},
        method={
            "batch_roles": {
                "int": {"method": "r2SCAN-3c", "opt_convergence": "tight"},
                "ts": {"method": "r2SCAN-3c", "scf_convergence": "verytight"},
            }
        },
    )
    edited = edit(spec, tmp_path, method={"levels": {"batch": {"electronic_state": module}}})
    assert edited.method["batch_roles"]["int"]["opt_convergence"] == "Tight"
    assert edited.method["batch_roles"]["ts"]["scf_convergence"] == "VeryTight"
    flags = batchoptimize_method_flags(edited.method)
    assert (
        json.loads(flags[flags.index("--electronic-state-json") + 1])["states"][0][
            "target_multiplicity"
        ]
        == 3
    )


@pytest.mark.parametrize(
    "workflow", ["tsmode", "XtbPathSearch", "OrcaGradient", "XtbConstrainedOpt"]
)
def test_frozen_generic_edit_is_explained_and_rejected(tmp_path: Path, workflow: str) -> None:
    spec = JobSpec(workflow=workflow)
    draft = build_edit_draft(record(spec, tmp_path))
    assert draft["capabilities"]["can_edit"] is False
    assert draft["migration_hint"] and draft["capabilities"]["disabled_reasons"]
    with pytest.raises(EditValidationError, match="新任务"):
        edit(spec, tmp_path, input={"charge": 1})


def test_remote_simple_spin_configuration_matches_local(tmp_path: Path) -> None:
    module = {
        "execution_mode": "single",
        "states": [{"state_id": "triplet", "target_multiplicity": 3, "spin_mode": "unrestricted"}],
    }
    spec = JobSpec(
        workflow="singlepoint",
        input={"source": XYZ},
        method={"levels": {"single_point": {"engine": "orca", "electronic_state": module}}},
    )
    local = JobRunner()._build_cmd(spec, tmp_path, "input.xyz")
    remote = build_remote_cli_command(spec, input_path="input.xyz")
    assert local[local.index("--spin-config") + 1] == str(tmp_path / "electronic_state.json")
    assert remote[remote.index("--spin-config") + 1] == "electronic_state.json"
    assert (
        json.loads((tmp_path / "electronic_state.json").read_text())["electronic_state"] == module
    )


def _job_editor_source(frontend: Path) -> str:
    """job_editor.js preceded by its load-order prerequisite.

    FE-06 moved batchRoleStaticDefaults into the shared workbench contract
    module, which the page loads BEFORE js/job_editor.js; node harnesses must
    mirror that order or the IIFE-top alias throws at load time.
    """
    contracts_path = frontend / "js/workbench/contracts/batch_role_defaults.js"
    if not contracts_path.is_file():
        pytest.skip("frontend module layout lands with feat/frontend-modular-slice")
    contracts = contracts_path.read_text()
    return contracts + "\n" + (frontend / "js/job_editor.js").read_text()


def test_editor_passive_charge_and_batch_spin_serialization(tmp_path: Path) -> None:
    if not shutil.which("node"):
        pytest.skip("NOT_VERIFIED: node unavailable")
    js = _job_editor_source(Path(__file__).parents[1] / "frontend")
    harness = """
global.window = {};
const fields = {
  'modal-charge': {value: '-1'}, 'modal-mult': {value: '2'},
  'modal-nproc': {value: '4'}, 'modal-task-name': {value: ''}
};
global.document = {getElementById: id => fields[id] || null, removeEventListener: () => {}};
global.ACPMemoryInput = {read: () => '8GB'};
global.wizardState = {method: {profile_id: 'opt_freq', stages: {
  batch_roles: {int: {}, ts: {}}, batch: {electronic_state: {preset_id: 'triplet'}}
}}};
"""
    checks = """
const I = window.ACPJobEditorInternals;
const context = {originalSpec: {workflow: 'optimize', input: {charge: 0, multiplicity: 1}},
  baselineFormState: {charge: '0', mult: '1'}};
const body = I.collectFormOnlyBody(context);
const overlay = I.adapterFor('optimize').patchOriginalInput(
  context, {charge: 0, multiplicity: 1}, body);
if (overlay.charge !== -1 || overlay.multiplicity !== 2) throw Error('passive charge/mult lost');
const adapter = I.adapterFor('BatchOptimize');
const projection = adapter.methodProjection();
if (projection.electronic_state.preset_id !== 'triplet') throw Error('spin omitted from diff');
const method = adapter.buildMethod({originalSpec: {method: {}}, methodBaseline: {
  profile_id: 'opt_freq', batch_roles: {int: {}, ts: {}}
}}, {}, projection);
if (method.levels.batch.electronic_state.preset_id !== 'triplet')
  throw Error('spin omitted from payload');
wizardState.method = {profile_id: 'custom', stages: {scan: {
  engine: 'orca', scan_coordinate_start: 1.2, scan_coordinate_end: 2.5, scf_convergence: 'Tight'
}}};
const scan = I.adapterFor('scan');
const scanContext = {originalSpec: {method: {levels: {scan: {
  engine: 'orca', scan_coordinate_start: 1, scan_coordinate_end: 3
}}}}, methodBaseline: {profile_id: 'custom', stages: {scan: {
  engine: 'orca', scan_coordinate_start: 1.2, scan_coordinate_end: 2.8, scf_convergence: 'Tight'
}}}};
const editedScan = scan.buildMethod(scanContext, {}, scan.methodProjection());
if (editedScan.levels.scan.scan_coordinate_start !== 1.2 ||
    editedScan.levels.scan.scan_coordinate_end !== 2.5)
  throw Error('execution projection reverted to stale form mirror');
if ('scf_convergence' in editedScan.levels.scan)
  throw Error('unmodified display default serialized');
wizardState.method = {profile_id: 'custom', stages: {
  single_point: {engine: 'orca', functional: 'B3LYP'}
}};
const sp = I.adapterFor('singlepoint');
const enabled = sp.buildMethod({originalSpec: {method: {levels: {
  single_point: {engine: 'orca', _disabled: true}
}}}, methodBaseline: {profile_id: 'custom', stages: {}}}, {}, sp.methodProjection());
if (enabled.levels.single_point._disabled !== false) throw Error('stage cannot be re-enabled');
adapter.hydrateMethod({}, {editable_spec: {method: {profile_id: 'opt_freq', levels: {
  batch: {engine: 'orca', max_steps: 42, electronic_state: {preset_id: 'triplet'}}
}}}});
if (wizardState.method.stages.batch.max_steps !== 42) throw Error('stored levels overwritten');
"""
    path = tmp_path / "editor.cjs"
    path.write_text(harness + js + checks)
    result = subprocess.run(["node", str(path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_editor_nmr_candidate_edit_remove_add_and_enumeration(tmp_path: Path) -> None:
    if not shutil.which("node"):
        pytest.skip("NOT_VERIFIED: node unavailable")
    js = _job_editor_source(Path(__file__).parents[1] / "frontend")
    harness = """
global.window = {};
const fields = {'nmr-enumerate-toggle': {checked: true}, 'nmr-enumerate-centers': {value: '2,3'}};
global.document = {getElementById: id => fields[id] || null};
global.wizardState = {method: {profile_id: '', stages: {}}};
global.wizardStructures = [
  {item_id: 'b', name: 'renamed', xyz: 'xyz_b', charge: -1, multiplicity: 2},
  {item_id: 'c', name: 'added', xyz: 'xyz_c', charge: 0, multiplicity: 1}
];
"""
    checks = """
const original = {candidates: [
  {source_type: 'smiles', source: 'CCO', name: 'first', charge: 0, multiplicity: 1},
  {source_type: 'smiles', source: 'COC', name: 'second', charge: 0, multiplicity: 1}
], enumerate: false};
const context = {originalSpec: {input: original}, baselineFormState: {charge: '0', mult: '1',
  nmr_enumerate: false, nmr_stereocenters: '', nmr_candidates: [
    {item_id: 'a', name: 'first', xyz: 'xyz_a', charge: 0, multiplicity: 1},
    {item_id: 'b', name: 'second', xyz: 'xyz_b', charge: 0, multiplicity: 1}
  ]}};
const input = window.ACPJobEditorInternals.adapterFor('nmr').patchOriginalInput(
  context, JSON.parse(JSON.stringify(original)), {input: {charge: 0, multiplicity: 1}});
if (input.candidates.length !== 2 || input.candidates[0].source !== 'COC' ||
    input.candidates[0].name !== 'renamed' || input.candidates[0].charge !== -1 ||
    input.candidates[1].source !== 'xyz_c' || input.candidates[1].source_type !== 'xyz_text')
  throw Error('NMR candidate edits lost or assigned to wrong original candidate');
if (!input.enumerate || input.stereocenters !== '2,3') throw Error('enumeration fields lost');
window.ACPJobEditorInternals.adapterFor('nmr').hydrateInput({}, {editable_spec: {input: {
  enumerate: true, stereocenters: [4,5]
}}});
if (!fields['nmr-enumerate-toggle'].checked || fields['nmr-enumerate-centers'].value !== '4,5')
  throw Error('enumeration fields hydrate incorrectly');
"""
    path = tmp_path / "nmr_editor.cjs"
    path.write_text(harness + js + checks)
    result = subprocess.run(["node", str(path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_editor_serializes_intent_even_when_method_matches_stale_original(tmp_path: Path) -> None:
    if not shutil.which("node"):
        pytest.skip("NOT_VERIFIED: node unavailable")
    editor = (Path(__file__).parents[1] / "frontend/js/job_editor.js").read_text()
    serializer = (
        "function serializeFromCollectedBody(body) {"
        + editor.split("function serializeFromCollectedBody(body) {", 1)[1].split(
            "\n  function serializeMethod", 1
        )[0]
    )
    harness = """
const deepCopy = value => JSON.parse(JSON.stringify(value));
const deepEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const selectedWorkflow = ctx => ctx.originalSpec.workflow;
const workflowChanged = () => false;
const workflowSelectionError = () => '';
const stored = {levels: {scan: {scan_coordinate_start: 1.0}}};
const baseline = {stages: {scan: {scan_coordinate_start: 1.2}}};
let current = {stages: {scan: {scan_coordinate_start: 1.0}}};
const editorContext = {originalSpec: {workflow: 'scan', method: stored},
  executionMode: 'in_place', methodBaseline: baseline,
  adapter: {methodProjection: () => current}, draft: {source_revision: 'sr_test'}};
const serializeMethod = () => deepCopy(stored);
const serializeInput = () => ({});
const serializeResources = () => ({});
const newRequestId = () => 'edit_test';
"""
    checks = """
const edited = serializeFromCollectedBody({});
if (!deepEqual(edited.method, stored) || !edited.method_projection_changed)
  throw Error('Explicit change to stale mirror value lost its edit intent');
current = baseline;
if (serializeFromCollectedBody({}).method_projection_changed)
  throw Error('Unmodified form incorrectly advertised an edit');
"""
    script = tmp_path / "editor_intent.cjs"
    script.write_text(harness + serializer + checks)
    result = subprocess.run(["node", str(script)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("workflow", ["scan", "PESsearch"])
def test_editor_parameter_step_enables_the_real_atom_picker(tmp_path: Path, workflow: str) -> None:
    if not shutil.which("node"):
        pytest.skip("NOT_VERIFIED: node unavailable")
    frontend = Path(__file__).parents[1] / "frontend"
    editor = _job_editor_source(frontend)
    # H2 decomposition moved the picker gates into features/wizard.js; extract
    # from the living production source instead of the HTML shell.
    wizard_path = frontend / "js/workbench/features/wizard.js"
    if not wizard_path.is_file():
        pytest.skip("frontend module layout lands with feat/frontend-modular-slice")
    wizard = wizard_path.read_text()
    # Execute the actual step controller and the actual picker gates with
    # DOM/viewer dependencies replaced, so their shared state is exercised.
    controller = (
        "function currentStep() {"
        + editor.split("function currentStep() {", 1)[1].split(
            "  function renderEditStepCompactSummary", 1
        )[0]
    )
    gates = ""
    for name in ("canPickPESAtoms", "canPickScanAtoms"):
        gates += (
            "function "
            + name
            + "() {"
            + wizard.split("function " + name + "() {", 1)[1].split("\n}\n", 1)[0]
            + "\n}\n"
        )
    harness = """
const modal = {style: {display: 'flex'}, setAttribute: () => {},
  querySelectorAll: () => [], querySelector: () => null};
const document = {getElementById: id => id === 'job-modal' ? modal : null};
let createWizardStep = 1;
const editorContext = {editStep: 1};
const wizardStructures = [{has_3d: true, xyz: '3\\nwater\\nO 0 0 0\\nH 0 0 1\\nH 0 1 0\\n'}];
const wizardState = {workflow: {id: WORKFLOW}};
const previewModel = {};
const previewModelStructureKey = wizardStructures[0].xyz.trim();
const previewViewer = null;
const s2scanState = {atoms: [{}, {}, {}]};
function currentPESStructure() {return wizardStructures[0];}
function scanWizardCurrentStructure() {return wizardStructures[0];}
function isPESsearchTask() {return wizardState.workflow.id === 'PESsearch';}
function renderEditStepCompactSummary() {}
function updateModalSubmitButton() {}
function updateEditorUiState() {}
const enabled = [];
function updatePESSelectionVisibility() {enabled.push(canPickPESAtoms() || canPickScanAtoms());}
""".replace("WORKFLOW", json.dumps(workflow))
    checks = """
if (!setEditStep(2) || enabled[0] !== true) throw Error('editor step 2 cannot pick atoms');
if (!setEditStep(1) || enabled[1] !== false) throw Error('atom picker enabled outside parameters');
"""
    path = tmp_path / "picker.cjs"
    path.write_text(harness + gates + controller + checks)
    result = subprocess.run(["node", str(path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
