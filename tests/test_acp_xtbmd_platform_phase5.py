"""Phase 5 platform-integration tests for the xtbmd_censo_energy workflow.

Covers the DevDoc ACP_xTBMD_CENSO_Energy_DevDoc.html Step 5/6 wiring:

* CLI subcommand exists and exposes the full flag set (Step 5);
* catalog carries the WORKFLOW_CATALOG entry, METHOD_SCHEMAS and the
  xtbmd control-group FIELD_DEFINITIONS (Step 6 / §10.1);
* JobRunner._build_cmd and build_remote_cli_command both accept the
  workflow and emit the same flags (Step 6 / §10.2);
* the frontend workbench derives its submitted top-level field set from
  the catalog (E7 — five-way parity: cli / runner / script_gen /
  FIELD_DEFINITIONS / frontend-derived set).

E7 parity is the mechanical guard against the "five hand-maintained
lists drift" failure mode described in DevDoc §10.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_XTBMD_WORKFLOW_ID = "xtbmd_censo_energy"

#: The xtbmd_censo_energy control-group field names — every one of them
#: must exist in FIELD_DEFINITIONS, map to a CLI flag, be emitted by both
#: the local runner and the remote script generator, and be forwarded by
#: the frontend submit branch (derived from the catalog schema).
_XTBMD_CONTROL_FIELDS: tuple[str, ...] = (
    "md_temperature",
    "md_time_ps",
    "md_dump_fs",
    "md_step_fs",
    "md_hmass",
    "md_shake",
    "md_nvt",
    "md_seed",
    "md_seeds",
    "md_method",
    "conv_check",
    "conv_novelty_max",
    "conv_rmsd",
    "max_frames",
    "opt_gfn",
    "opt_level",
    "opt_timeout",
    "edis",
    "gdis",
    "resume",
)

#: Method key → CLI flag for the scalar control group. Booleans are
#: emitted as opt-in/opt-out flags (md_shake False → --md-no-shake, etc.)
#: and are exercised separately.
_XTBMD_FLAG_MAP: dict[str, str] = {
    "md_temperature": "--md-temp",
    "md_time_ps": "--md-time",
    "md_dump_fs": "--md-dump",
    "md_step_fs": "--md-step",
    "md_hmass": "--md-hmass",
    "md_seed": "--md-seed",
    "md_seeds": "--md-seeds",
    "md_method": "--md-method",
    "conv_novelty_max": "--conv-novelty-max",
    "conv_rmsd": "--conv-rmsd",
    "max_frames": "--max-frames",
    "opt_gfn": "--opt-gfn",
    "opt_level": "--opt-level",
    "opt_timeout": "--opt-timeout",
    "edis": "--edis",
    "gdis": "--gdis",
    "threshold": "--threshold",
}

#: Boolean fields: (method key, CLI flag when True, CLI flag when False).
_XTBMD_BOOL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("md_shake", "", "--md-no-shake"),
    ("md_nvt", "", "--no-md-nvt"),
    ("conv_check", "", "--no-conv-check"),
    ("keep_frames", "--keep-frames", ""),
    ("resume", "--resume", ""),
    ("rank1_only", "--rank1-only", ""),
    ("no_opt", "--no-opt", ""),
)


def _full_method_dict() -> dict[str, Any]:
    """Method dict exercising every xtbmd control field."""
    method: dict[str, Any] = {
        "preset": "censo-light",
        "md_temperature": 500.0,
        "md_time_ps": 50.0,
        "md_dump_fs": 200.0,
        "md_step_fs": 0.5,
        "md_hmass": 3.0,
        "md_shake": False,
        "md_nvt": False,
        "md_seed": 7,
        "md_seeds": 3,
        "md_method": "gfn1",
        "conv_check": False,
        "conv_novelty_max": 0.05,
        "conv_rmsd": 0.8,
        "max_frames": 300,
        "opt_gfn": 2,
        "opt_level": "tight",
        "opt_timeout": 600,
        "edis": 1.0,
        "gdis": 0.5,
        "keep_frames": True,
        "resume": True,
        "rank1_only": True,
        "no_opt": False,
        "threshold": 0.95,
        "levels": {"refinement_sp": {"functional": "wB97X-D4"}},
        "solvent": "water",
        "ewin": 8.0,
    }
    return method


# ---------------------------------------------------------------------------
# 1. CLI surface (Step 5)
# ---------------------------------------------------------------------------


def test_cli_has_xtbmd_censo_energy_subcommand_and_all_flags() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "xtbmd_censo_energy", "--input", "CCO"])
    assert args.workflow == _XTBMD_WORKFLOW_ID

    run_parser = None
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices"):
            run_parser = action.choices["run"]
            break
    assert run_parser is not None, "run subparser not found"
    subparsers_action = next(
        a for a in run_parser._actions if getattr(a, "dest", None) == "workflow"
    )
    sub = subparsers_action.choices["xtbmd_censo_energy"]
    assert sub is not None, "CLI subparser xtbmd_censo_energy missing"

    option_strings = {opt for a in sub._actions for opt in a.option_strings}
    for key, flag in _XTBMD_FLAG_MAP.items():
        assert flag in option_strings, f"CLI missing {flag} (method key {key})"
    for _, flag_true, flag_false in _XTBMD_BOOL_FIELDS:
        if flag_true:
            assert flag_true in option_strings, f"CLI missing {flag_true}"
        if flag_false:
            assert flag_false in option_strings, f"CLI missing {flag_false}"
    for base in ("--input", "--batch-file", "--preset", "--no-opt", "--rank1-only",
                 "--threshold", "--levels", "--solvent", "--ewin", "--resume"):
        assert base in option_strings, f"CLI missing {base}"

    # Dispatch wiring: the handler must exist for the subcommand (cli.py
    # dispatch dict is the real gate — a missed entry rejects the command).
    import inspect

    from acp.cli import main

    src = inspect.getsource(main)
    assert '"xtbmd_censo_energy"' in src or "'xtbmd_censo_energy'" in src


def test_cli_parse_roundtrip_defaults() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "xtbmd_censo_energy", "--input", "CCO"])
    assert args.workflow == _XTBMD_WORKFLOW_ID
    assert args.md_temp == 400.0
    assert args.md_time == 100.0
    assert args.md_dump == 100.0
    assert args.md_step == 1.0
    assert args.md_hmass == 1.0
    assert args.md_no_shake is False
    assert args.md_nvt is True
    assert args.md_seed == 42
    assert args.md_seeds == 1
    assert args.md_method == "gfnff"
    assert args.conv_check is True
    assert args.conv_novelty_max == 0.10
    assert args.conv_rmsd == 0.5
    assert args.max_frames == 500
    assert args.opt_gfn == 1
    assert args.opt_level == "normal"
    assert args.opt_timeout == 300
    assert args.edis == 0.5
    assert args.gdis == 0.25
    # --ewin default is None so config censo.ewin stays reachable (the
    # workflow falls back to levels.censo.ewin → config, default 6.0).
    assert args.ewin is None
    assert args.rank1_only is False
    assert args.resume is False


# ---------------------------------------------------------------------------
# 2. Catalog surface (Step 6 / §10.1)
# ---------------------------------------------------------------------------


def test_catalog_workflow_entry_active_and_visible() -> None:
    from acp.catalog import WORKFLOW_CATALOG

    entry = next((w for w in WORKFLOW_CATALOG if w["id"] == _XTBMD_WORKFLOW_ID), None)
    assert entry is not None
    assert entry["status"] == "active"
    assert entry["visible"] is True
    assert entry["method_schema_id"] == _XTBMD_WORKFLOW_ID
    assert entry["default_backend"] == "censo"
    assert "xtb" in entry["requires_binaries"]
    assert "isostat" in entry["requires_binaries"]
    assert "censo" in entry["requires_binaries"]
    assert "orca" in entry["requires_binaries"]


def test_catalog_schema_levels_and_profiles() -> None:
    from acp.catalog import METHOD_SCHEMAS

    schema = METHOD_SCHEMAS.get(_XTBMD_WORKFLOW_ID)
    assert schema is not None
    level_ids = {lv["level_id"] for lv in schema["method_levels"]}
    assert {"xtb_md", "xtb_opt", "isostat", "censo", "refinement_sp", "thermo"} <= level_ids
    profiles = {p["profile_id"] for p in schema["profiles"]}
    assert {"censo-light", "censo-default", "censo-zero"} <= profiles

    # every level field must exist in FIELD_DEFINITIONS or the frontend
    # rendering breaks (§10.1 "schema fields 引用必须存在")
    from acp.catalog import FIELD_DEFINITIONS

    for lv in schema["method_levels"]:
        for field_name in lv.get("fields", []):
            assert field_name in FIELD_DEFINITIONS, (
                f"schema field {field_name} missing from FIELD_DEFINITIONS"
            )


def test_catalog_field_definitions_visibility() -> None:
    """md_temperature / md_seeds are regular fields; the rest are advanced."""
    from acp.catalog import FIELD_DEFINITIONS

    for key in _XTBMD_CONTROL_FIELDS:
        fd = FIELD_DEFINITIONS.get(key)
        assert fd is not None, f"FIELD_DEFINITIONS missing {key}"
    assert FIELD_DEFINITIONS["md_temperature"].get("advanced") is not True
    assert FIELD_DEFINITIONS["md_seeds"].get("advanced") is not True
    advanced = [k for k in _XTBMD_CONTROL_FIELDS if FIELD_DEFINITIONS[k].get("advanced")]
    assert set(advanced) == set(_XTBMD_CONTROL_FIELDS) - {"md_temperature", "md_seeds"}


def test_catalog_opt_level_xtb_options_corrected_per_v14() -> None:
    """v1.4 audit: xTB legal opt_level set is crude/normal/tight/verytight."""
    from acp.catalog import FIELD_DEFINITIONS

    assert FIELD_DEFINITIONS["opt_level"]["per_backend"]["xtb"] == [
        "crude", "normal", "tight", "verytight",
    ]


def test_supported_workflows_derives_from_catalog() -> None:
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

    assert _XTBMD_WORKFLOW_ID in SUPPORTED_WORKFLOWS


def test_validate_method_accepts_xtbmd_schema() -> None:
    from acp.catalog import get_method_schema, normalize_and_validate_method_config

    schema = get_method_schema(_XTBMD_WORKFLOW_ID)
    method = {
        "levels": {
            "xtb_md": {"engine": "molclus", "md_temperature": 400.0, "md_seeds": 3},
            "xtb_opt": {"engine": "xtb", "opt_gfn": "1", "opt_level": "normal"},
            "isostat": {"engine": "isostat", "edis": 0.5, "gdis": 0.25},
            "censo": {"engine": "censo", "ewin": 6.0},
            "refinement_sp": {"engine": "orca", "functional": "wB97M-V", "basis": "def2-TZVPP"},
            "thermo": {"engine": "shermo", "temperature": 298.15},
        }
    }
    normalized, errors = normalize_and_validate_method_config(method, schema)
    assert not errors, errors
    assert normalized["xtb_md"]["md_seeds"] == 3

    bad = {"levels": {"xtb_opt": {"engine": "xtb", "opt_level": "loose"}}}
    _, errors = normalize_and_validate_method_config(bad, schema)
    assert any("opt_level" in e for e in errors)


def test_validate_method_accepts_numeric_select_values() -> None:
    """opt_gfn options are catalog strings; numeric API values must pass.

    Audit fix (Phase 5 review): the option membership check compares
    str(user_val) against str(options) so int ``1`` and string ``"1"``
    are both accepted, while out-of-range values are still rejected.
    """
    from acp.catalog import get_method_schema, normalize_and_validate_method_config

    schema = get_method_schema(_XTBMD_WORKFLOW_ID)
    for value in (1, "1", 2, "2", 0):
        method = {"levels": {"xtb_opt": {"engine": "xtb", "opt_gfn": value}}}
        normalized, errors = normalize_and_validate_method_config(method, schema)
        assert not errors, f"opt_gfn={value!r} rejected: {errors}"

    method = {"levels": {"xtb_opt": {"engine": "xtb", "opt_gfn": 9}}}
    _, errors = normalize_and_validate_method_config(method, schema)
    assert any("opt_gfn" in e for e in errors)


def test_runner_boolean_string_coercion() -> None:
    """API clients sending ``"false"`` strings must not lose the flag.

    Audit fix (Phase 5 review): ``xtbmd_method_flags`` normalises boolean
    method values through ``_as_bool`` so string/1/0 forms emit the same
    opt-in / opt-out flags as real JSON booleans.
    """
    from acp.scheduler.jobs import xtbmd_method_flags

    method = {
        "md_shake": "false", "md_nvt": "false", "conv_check": "False",
        "resume": "true", "rank1_only": "1", "keep_frames": "on",
        "no_opt": "0",
    }
    flags = xtbmd_method_flags(method)
    assert "--md-no-shake" in flags
    assert "--no-md-nvt" in flags
    assert "--no-conv-check" in flags
    assert "--resume" in flags
    assert "--rank1-only" in flags
    assert "--keep-frames" in flags
    assert "--no-opt" not in flags


def test_runner_forwards_md_timeout() -> None:
    """md_timeout must reach the CLI so API users can override the
    workflow's heuristic per-MD timeout (doc §16 risk 1)."""
    from acp.scheduler.jobs import xtbmd_method_flags

    flags = xtbmd_method_flags({"md_timeout": 7200})
    assert flags == ["--md-timeout", "7200"]


def test_cli_ewin_default_none_preserves_config() -> None:
    """--ewin must default to None so config censo.ewin stays reachable
    (audit fix: a hardcoded 6.0 CLI default made the config dead)."""
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", _XTBMD_WORKFLOW_ID, "--input", "CCO"])
    assert args.ewin is None
    args2 = parser.parse_args(["run", _XTBMD_WORKFLOW_ID, "--input", "CCO", "--ewin", "8.0"])
    assert args2.ewin == 8.0


# ---------------------------------------------------------------------------
# 3. Scheduler / remote wiring (Step 6 / §10.2)
# ---------------------------------------------------------------------------


def _job_spec(method: dict[str, Any]) -> Any:
    from acp.scheduler.jobs import JobSpec

    return JobSpec(
        workflow=_XTBMD_WORKFLOW_ID,
        name="etoh",
        input={"source": "CCO"},
        method=method,
    )


def test_runner_accepts_and_emits_xtbmd_flags() -> None:
    from acp.scheduler.runner import JobRunner

    method = _full_method_dict()
    spec = _job_spec(method)
    cmd = JobRunner()._build_cmd(spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    assert "-m" in cmd and "acp.cli" in cmd and "run" in cmd
    assert "xtbmd_censo_energy" in cmd
    joined = " ".join(cmd)
    for key, flag in _XTBMD_FLAG_MAP.items():
        assert flag in joined, f"runner missing {flag} for {key}"
        if method.get(key) is not None:
            assert str(method[key]) in cmd[cmd.index(flag) + 1], (
                f"runner value mismatch for {flag}"
            )
    for key, flag_true, flag_false in _XTBMD_BOOL_FIELDS:
        if flag_false and method.get(key) is False:
            assert flag_false in joined, f"runner missing {flag_false} (method {key}=False)"
        if flag_true and method.get(key) is True:
            assert flag_true in joined, f"runner missing {flag_true} (method {key}=True)"
    assert "--ewin" in joined and "8.0" in cmd[cmd.index("--ewin") + 1]
    assert "--solvent" in joined
    assert "--preset" in joined and "censo-light" in cmd[cmd.index("--preset") + 1]


def test_runner_rejects_other_workflow_unchanged() -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.runner import JobRunner

    spec = JobSpec(
        workflow="nope",
        input={"source": "CCO"},
        method={"preset": "censo-light"},
    )
    with pytest.raises(ValueError, match="No subprocess mapping"):
        JobRunner()._build_cmd(spec, Path("/tmp/wd"))


def test_script_gen_accepts_and_emits_xtbmd_flags() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    method = _full_method_dict()
    spec = _job_spec(method)
    cmd = build_remote_cli_command(spec, python_executable="python3")
    assert "xtbmd_censo_energy" in cmd
    joined = " ".join(cmd)
    for key, flag in _XTBMD_FLAG_MAP.items():
        assert flag in joined, f"script_gen missing {flag} for {key}"
    for key, flag_true, flag_false in _XTBMD_BOOL_FIELDS:
        if flag_false and method.get(key) is False:
            assert flag_false in joined
        if flag_true and method.get(key) is True:
            assert flag_true in joined
    assert "--resume" in joined


def test_script_gen_parity_with_runner() -> None:
    """Local JobRunner and remote script_gen must emit identical flags."""
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.runner import JobRunner

    method = _full_method_dict()
    spec = _job_spec(method)
    local = JobRunner()._build_cmd(spec, Path("/tmp/wd"), input_path="inputs/input.xyz")
    remote = build_remote_cli_command(spec, python_executable=local[0])
    # Only the output dir differs: /tmp/wd (local) vs "." (remote).
    local_flat = [c if c != "/tmp/wd" else "." for c in local]
    assert remote == local_flat, f"drift: {remote} != {local_flat}"


def test_stage_plan_provider_registered() -> None:
    from acp.scheduler.stage_tasks import get_stage_plan

    spec = _job_spec({"preset": "censo-light"})
    names = [p.stage_name for p in get_stage_plan(spec)]
    assert names == [
        "embed", "xtbmd", "batch_opt", "isostat", "energy_filter",
        "censo", "dft_handoff", "finalize", "conformer_energy",
    ]

    spec_zero = _job_spec({"preset": "censo-zero", "no_opt": True})
    names_zero = [p.stage_name for p in get_stage_plan(spec_zero)]
    assert "censo" not in names_zero
    assert "dft_handoff" not in names_zero


def test_stage_plan_censo_default_keeps_handoff_under_no_opt() -> None:
    """censo-default forces opt_enabled in the workflow even with no_opt,
    so the stage plan must keep dft_handoff (audit fix)."""
    from acp.scheduler.stage_tasks import get_stage_plan

    spec = _job_spec({"preset": "censo-default", "no_opt": True})
    names = [p.stage_name for p in get_stage_plan(spec)]
    assert "censo" in names
    assert "dft_handoff" in names


def test_cli_threshold_merge_preserves_explicit_levels() -> None:
    """An explicit --levels refinement_threshold must survive the CLI
    default (audit fix: handler now merges like the energy handler).

    The canonical position is top-level (the frontend lifts it out of
    the censo level into ``--threshold``; the workflow reads
    ``levels.refinement_threshold`` via resolve_levels)."""
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "run", _XTBMD_WORKFLOW_ID, "--input", "CCO",
        "--levels", '{"refinement_threshold": 0.95}',
    ])
    assert args.threshold == 0.99  # CLI default, not user-explicit

    # Reproduce the handler merge.
    from acp.cli import _parse_levels_json

    levels = _parse_levels_json(args.levels) or {}
    if args.threshold != 0.99:
        levels["refinement_threshold"] = args.threshold
    else:
        levels.setdefault("refinement_threshold", 0.99)
    assert levels["refinement_threshold"] == 0.95

    # Explicit --threshold still wins over the levels value.
    args2 = parser.parse_args([
        "run", _XTBMD_WORKFLOW_ID, "--input", "CCO", "--threshold", "0.9",
        "--levels", '{"refinement_threshold": 0.95}',
    ])
    levels2 = _parse_levels_json(args2.levels) or {}
    if args2.threshold != 0.99:
        levels2["refinement_threshold"] = args2.threshold
    assert levels2["refinement_threshold"] == 0.9


# ---------------------------------------------------------------------------
# 4. Frontend parity (E7 — five-way)
# ---------------------------------------------------------------------------

_FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"


@pytest.mark.skipif(not _FRONTEND.exists(), reason="frontend not checked out")
def test_frontend_submits_xtbmd_control_group_fields() -> None:
    """The workbench submit branch must forward every control field.

    The frontend derives its top-level field set from the workflow
    schema's control levels (xtb_md / xtb_opt / isostat), which reference
    FIELD_DEFINITIONS — so the E7 frontend-derived set equals the catalog
    control fields. We assert (a) the derivation mechanism exists in the
    page, and (b) the schema's control-level fields — the actual derived
    set the page will submit — equal the catalog control group.
    """
    from acp.catalog import METHOD_SCHEMAS

    html = _FRONTEND.read_text(encoding="utf-8")

    # The submit branch must be wired for the workflow.
    assert "xtbmd_censo_energy" in html
    assert "energyLike" in html

    # The control levels from which the field set is derived must exist
    # in both the page (level_ids) and the schema.
    for level_id in ("xtb_md", "xtb_opt", "isostat"):
        assert f'"{level_id}"' in html, f"frontend missing control level {level_id}"

    # The schema control levels must reference exactly the catalog
    # control group — the frontend reads these names at runtime, so this
    # is the E7 equality between the frontend-derived set and
    # FIELD_DEFINITIONS.
    schema = METHOD_SCHEMAS[_XTBMD_WORKFLOW_ID]
    schema_ctrl: list[str] = []
    for lv in schema["method_levels"]:
        if lv["level_id"] in ("xtb_md", "xtb_opt", "isostat"):
            schema_ctrl.extend(lv["fields"])
    assert set(schema_ctrl) == set(_XTBMD_CONTROL_FIELDS), (
        f"schema control levels {set(schema_ctrl)} != catalog fields {set(_XTBMD_CONTROL_FIELDS)}"
    )


@pytest.mark.skipif(not _FRONTEND.exists(), reason="frontend not checked out")
def test_frontend_rank1_toggle_covers_xtbmd() -> None:
    html = _FRONTEND.read_text(encoding="utf-8")
    toggle_guard = 'wizardState.workflow.id !== "energy"'
    xtbmd_guard = (
        'wizardState.workflow.id !== "energy" '
        '&& wizardState.workflow.id !== "xtbmd_censo_energy"'
    )
    assert toggle_guard not in html.replace(xtbmd_guard, "") or xtbmd_guard in html


@pytest.mark.skipif(not _FRONTEND.exists(), reason="frontend not checked out")
def test_frontend_rank1_default_differs_per_workflow() -> None:
    """energy defaults rank1-only ON; xtbmd defaults to full ensemble."""
    html = _FRONTEND.read_text(encoding="utf-8")
    assert 'wizardState.method.rank1_only = (wizardState.workflow.id === "energy")' in html
