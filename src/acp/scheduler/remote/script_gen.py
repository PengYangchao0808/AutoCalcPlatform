"""
LSF Script Generator
====================

Generates OpenLAVA/LSF submission scripts and the equivalent
``python -m acp.cli run <workflow>`` command for remote execution.

The CLI command mapping mirrors :meth:`JobRunner._build_cmd` so that a
remote job runs exactly the same workflow logic as a local subprocess job.
BSUB directive formatting follows :class:`LSFClusterAdapter._generate_lsf_script`.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
import posixpath
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp.catalog import method_levels_to_cli_flags
from acp.scheduler.jobs import (
    SCAN_CONFIG_FILENAME,
    JobSpec,
    batchoptimize_method_flags,
    censo_ewin_from_method,
    censo_preset_from_method,
    censo_solvent_from_method,
    confsearch_method_flags,
    input_chemistry_flags,
    nmr_method_flags,
    scan_method_flags,
    xtbmd_method_flags,
)
from acp.scheduler.remote.config import RemoteNode

logger = logging.getLogger(__name__)


def _looks_like_smiles(s: str) -> bool:
    """Heuristic: is *s* a SMILES token rather than a file path?"""
    stripped = s.strip()
    if not stripped or "\n" in stripped or len(stripped) > 200:
        return False
    if stripped[0].isdigit():
        return False
    return True


__all__ = [
    "LSFScriptSpec",
    "build_lsf_script_spec",
    "build_remote_cli_command",
    "build_remote_nmr_cmd_tail",
    "build_remote_scan_config_payload",
    "build_remote_stage_cmd_tail",
    "derive_lsf_resources",
    "generate_lsf_script",
]

_REMOTE_CHECKPOINT_PATH = "WORK/00_RUNTIME/checkpoint.json"
_REMOTE_RESULT_MANIFEST_PATH = "RESULT/result_manifest.json"

_DEFAULT_NPROC = 8
_DEFAULT_MEM_MB_PER_CORE = 2000
_DEFAULT_QUEUE = "normal"
_DEFAULT_WALLTIME = ""
_MIN_MEM_MB_PER_CORE = 256

_GFN_DISPLAY_TO_INT: dict[str, int] = {
    "GFN0-xTB": 0,
    "GFN1-xTB": 1,
    "GFN2-xTB": 2,
    "0": 0,
    "1": 1,
    "2": 2,
}


@dataclass(frozen=True)
class LSFScriptSpec:
    """Parameters describing a remote LSF submission.

    Attributes:
        job_name: BSUB ``-J`` job name.
        queue: BSUB ``-q`` queue name.
        nproc: BSUB ``-n`` number of CPU cores.
        mem_mb_per_core: Per-core memory in MB; used to compute the per-process
            ``-M`` RLIMIT_AS as ``mem_mb_per_core * nproc * 1.05`` (MB→KB
            included). Chosen over ``rusage[mem=...]`` to avoid OpenLava's
            double-counting of reserved vs. actually-used memory, which
            caused jobs to PEND unnecessarily.
        walltime: BSUB ``-W`` wall-clock limit (e.g. ``"24:00"``).
        remote_code_dir: Directory where ACP source is synced; used to
            build ``PYTHONPATH={remote_code_dir}/src``.
        remote_job_dir: The remote job working directory; the script
            ``cd``\\ s here before launching the CLI.
        cli_command: Argv list (e.g.
            ``["python", "-m", "acp.cli", "run", ...]``).
        pre_cmds: Shell lines injected verbatim into the script body
            before the CLI runs (after the exit-code traps).  Used for
            ``module load`` / environment setup on module-based clusters.
        extra_flags: Additional raw BSUB flags (e.g. ``"-R span[hosts=1]"``).
    """

    job_name: str
    queue: str
    nproc: int
    mem_mb_per_core: int
    walltime: str
    remote_code_dir: str
    remote_job_dir: str
    cli_command: list[str]
    pre_cmds: list[str] = field(default_factory=list)
    extra_flags: str = ""


# ── Allowed remote workflows ────────────────────────────────────────────

_ALLOWED_REMOTE_WORKFLOWS: frozenset[str] = frozenset(
    {
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "ensemble",
        "energy",
        "nmr",
        "xtbmd_censo_energy",
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "xtb_optimize",
    }
)


def build_remote_cli_command(
    spec: JobSpec,
    input_path: str = "inputs/input.xyz",
    python_executable: str = "python",
    config_path: str | None = None,
) -> list[str]:
    """Build the ``python -m acp.cli ...`` argv for remote execution.

    Args:
        python_executable: Interpreter to use on the node.
        config_path: Optional path to a job-level YAML config on the remote
            node.
    """
    py = python_executable or "python"
    wf = spec.workflow
    if wf not in _ALLOWED_REMOTE_WORKFLOWS:
        raise ValueError(f"No remote subprocess mapping for workflow: {wf}")

    cmd: list[str] = [py, "-m", "acp.cli", "run", wf]

    inp = spec.input
    method = spec.method
    res = spec.resources

    # NMR has a distinct multi-candidate + spectrum payload shape.
    if wf == "nmr":
        cmd += build_remote_nmr_cmd_tail(spec, input_path)
        return _append_resources_and_return(cmd, wf, inp, method, res, config_path)

    # Stage workflows (PESsearch) use the stage tail.
    if wf == "PESsearch":
        cmd += build_remote_pessearch_tail(spec)
        return _append_resources_and_return(cmd, wf, inp, method, res, config_path)

    source = ""
    if wf == "BatchOptimize":
        artifact = inp.get("from_artifact")
        items_file = inp.get("items_file")
        if artifact:
            source = str(artifact)
            cmd += ["--from-artifact", str(artifact), "--output", "."]
        elif items_file:
            source = str(items_file)
            cmd += ["--items-file", str(items_file), "--output", "."]
        elif input_path:
            # RemoteJobRunner stages one structure as input.xyz for each
            # independent scheduler job.  Treat that file as a one-item
            # BatchOptimize request rather than as an artifact path, and use
            # the same flat task layout as local scheduler execution.
            source = str(input_path)
            cmd += ["--items-file", str(input_path), "--output", "."]
        else:
            raise ValueError(
                "BatchOptimize job requires a batch artifact, items file, or staged structure"
            )
        # One structure per scheduler task (layout spec §1a): every dispatch
        # shape uses the flat WORK/<stage> layout, matching runner._build_cmd.
        cmd += ["--layout-mode", "single_flat"]
        cmd += batchoptimize_method_flags(method, inp)
    else:
        source = (
            input_path
            or inp.get("input_artifact")
            or inp.get("source")
            or inp.get("input")
            or inp.get("smiles")
            or ""
        )

    if not source:
        raise ValueError(f"{wf} job requires a valid input structure")

    if wf == "Confsearch":
        cmd += ["--input", str(source), "--output", "."]
        if spec.name:
            cmd += ["--name", spec.name]
        cmd += confsearch_method_flags(method)
        solvent = censo_solvent_from_method(method)
        if solvent:
            cmd += ["--solvent", solvent]
        ewin = censo_ewin_from_method(method)
        if ewin is not None:
            cmd += ["--ewin", str(ewin)]
    elif wf in {"ensemble", "energy"}:
        cmd += ["--input", str(source), "--output", "."]
        preset = censo_preset_from_method(method)
        if preset:
            cmd += ["--preset", preset]
        if spec.name:
            cmd += ["--name", spec.name]
        if wf == "energy" and method.get("no_opt"):
            cmd += ["--no-opt"]
        if wf == "energy" and method.get("rank1_only"):
            cmd += ["--rank1-only"]
        if wf == "energy" and method.get("rank1_only") is False:
            cmd += ["--full-ensemble"]
        if wf == "energy" and method.get("threshold") is not None:
            cmd += ["--threshold", str(method["threshold"])]
        if wf == "energy" and method.get("levels"):
            cmd += ["--levels", json.dumps(method["levels"])]
        if wf == "ensemble" and method.get("keep_all"):
            cmd += ["--keep-all"]
        solvent = censo_solvent_from_method(method)
        if solvent:
            cmd += ["--solvent", solvent]
        ewin = censo_ewin_from_method(method)
        if ewin is not None:
            cmd += ["--ewin", str(ewin)]
    elif wf == "xtbmd_censo_energy":
        cmd += ["--input", str(source), "--output", "."]
        preset = censo_preset_from_method(method)
        if preset:
            cmd += ["--preset", preset]
        if spec.name:
            cmd += ["--name", spec.name]
        if method.get("levels"):
            cmd += ["--levels", json.dumps(method["levels"])]
        cmd += xtbmd_method_flags(method)
        solvent = censo_solvent_from_method(method)
        if solvent:
            cmd += ["--solvent", solvent]
        ewin = censo_ewin_from_method(method)
        if ewin is not None:
            cmd += ["--ewin", str(ewin)]
    elif wf in ("singlepoint", "optimize", "frequency", "scan"):
        cmd += ["--input", str(source), "--output", "."]
        if spec.name:
            cmd += ["--name", spec.name]
        levels = method.get("levels", {})
        if levels:
            cmd += method_levels_to_cli_flags(levels)
        if wf == "scan":
            cmd += scan_method_flags(method, inp)
    elif wf == "irc":
        cmd += build_remote_irc_tail(spec, source)
    elif wf == "xtb_optimize":
        cmd += ["--input", str(source), "--output", "."]
        if spec.name:
            cmd += ["--name", spec.name]
        xtb_level = (method.get("levels") or {}).get("xtb_opt", {})
        gfn_val = xtb_level.get("gfn")
        if gfn_val is not None:
            gfn_int = _GFN_DISPLAY_TO_INT.get(str(gfn_val), gfn_val)
            cmd += ["--gfn", str(gfn_int)]
        if xtb_level.get("opt_level"):
            cmd += ["--opt-level", str(xtb_level["opt_level"])]
        if xtb_level.get("max_steps") is not None:
            cmd += ["--max-steps", str(xtb_level["max_steps"])]
        if xtb_level.get("solvent"):
            cmd += ["--solvent", str(xtb_level["solvent"])]
        sm = xtb_level.get("solvent_model")
        if sm and str(sm).lower() not in ("", "none"):
            cmd += ["--solvent-model", str(sm)]

    return _append_resources_and_return(cmd, wf, inp, method, res, config_path)


def _append_resources_and_return(
    cmd: list[str],
    wf: str,
    inp: dict[str, Any],
    method: dict[str, Any],
    res: dict[str, Any],
    config_path: str | None,
) -> list[str]:
    if config_path:
        cmd += ["--config", config_path]
    if res.get("nproc") is not None:
        cmd += ["--nproc", str(res["nproc"])]
    if res.get("mem"):
        cmd += ["--mem", str(res["mem"])]
    cmd += input_chemistry_flags(inp)
    return cmd


# ──⑧ new-workflow remote tail generators ────────────────────────────────


def build_remote_pessearch_tail(spec: JobSpec) -> list[str]:
    """Generate argv tail for PESsearch remote execution."""
    inp = spec.input
    method = spec.method
    flags: list[str] = ["--output", "."]
    bond_scan_mode = str(method.get("mode") or "") == "bond_length_scan"
    if bond_scan_mode:
        flags += ["--mode", "bond_length_scan"]
        flags += ["--scan-config", SCAN_CONFIG_FILENAME]
        return flags + input_chemistry_flags(inp)
    from_manifest = inp.get("from")
    if from_manifest:
        flags += ["--from", str(from_manifest)]
    if method.get("strategy"):
        flags += ["--strategy", str(method["strategy"])]
    select = method.get("select")
    if isinstance(select, (list, tuple)) and select:
        flags += ["--select", ",".join(str(item) for item in select)]
    elif isinstance(select, str) and select.strip():
        flags += ["--select", select.strip()]
    plan = inp.get("coordinate_plan") or method.get("coordinate_plan")
    if plan is not None:
        flags += ["--plan", json.dumps(plan)]
    product = inp.get("product")
    if product:
        flags += ["--product", str(product)]
    ts_guess = inp.get("ts_guess")
    if ts_guess:
        flags += ["--ts-guess", str(ts_guess)]
    flags += input_chemistry_flags(inp)
    return flags


def build_remote_irc_tail(spec: JobSpec, source: str) -> list[str]:
    """Generate argv tail for IRC remote execution."""
    inp = spec.input
    method = spec.method
    cmd: list[str] = ["--input", str(source), "--output", "."]
    if spec.name:
        cmd += ["--name", spec.name]
    input_role = inp.get("input_role")
    if input_role:
        cmd += ["--input-role", str(input_role)]
    directions = inp.get("directions") or ["both"]
    direction_names = {str(d).strip().lower() for d in directions}
    if direction_names == {"forward"}:
        cmd += ["--direction", "forward"]
    elif direction_names == {"reverse"}:
        cmd += ["--direction", "reverse"]
    elif direction_names in ({"forward", "reverse"}, {"both"}):
        cmd += ["--direction", "both"]
    elif direction_names:
        raise ValueError("irc directions must be forward, reverse, or both")
    levels = method.get("levels")
    irc_level = levels.get("irc", {}) if isinstance(levels, Mapping) else {}
    if not isinstance(irc_level, Mapping):
        irc_level = {}
    irc_method = method.get("method") or method.get("functional") or irc_level.get("method")
    if irc_method:
        cmd += ["--method", str(irc_method)]
    irc_basis = method.get("basis") or irc_level.get("basis")
    if irc_basis:
        cmd += ["--basis", str(irc_basis)]
    maxpoints = method.get("maxpoints") or irc_level.get("maxpoints")
    if maxpoints is not None:
        cmd += ["--maxpoints", str(maxpoints)]
    irc_step = method.get("step") or irc_level.get("step")
    if irc_step is not None:
        cmd += ["--step", str(irc_step)]
    return cmd


# ── Stage tail (PESsearch bond-scan only) ───────────────────────────────


def build_remote_scan_config_payload(spec: JobSpec) -> dict[str, Any] | None:
    """Bond-scan scan_request payload staged as ``scan_config.json``."""
    wf = spec.workflow
    if not (wf == "PESsearch" and str(spec.method.get("mode") or "") == "bond_length_scan"):
        return None
    scan_request = dict(spec.input.get("scan_request") or spec.method.get("scan_request") or {})
    return scan_request


def build_remote_stage_cmd_tail(spec: JobSpec) -> list[str]:
    """E7 parity helper: append the stage-workflow argv for remote execution.

    Handles PESsearch bond-scan mode only (ships scan_config.json).
    """
    wf = spec.workflow
    inp = spec.input
    method = spec.method
    flags: list[str] = ["--output", "."]
    bond_scan_mode = wf == "PESsearch" and str(method.get("mode") or "") == "bond_length_scan"
    if bond_scan_mode:
        flags += ["--mode", "bond_length_scan"]
        flags += ["--scan-config", SCAN_CONFIG_FILENAME]
        return flags + input_chemistry_flags(inp)
    from_manifest = inp.get("from")
    if from_manifest:
        flags += ["--from", str(from_manifest)]
    if wf == "PESsearch":
        if method.get("strategy"):
            flags += ["--strategy", str(method["strategy"])]
        select = method.get("select")
        if isinstance(select, (list, tuple)) and select:
            flags += ["--select", ",".join(str(item) for item in select)]
        elif isinstance(select, str) and select.strip():
            flags += ["--select", select.strip()]
        plan = inp.get("coordinate_plan") or method.get("coordinate_plan")
        if plan is not None:
            flags += ["--plan", json.dumps(plan)]
        product = inp.get("product")
        if product:
            flags += ["--product", str(product)]
        ts_guess = inp.get("ts_guess")
        if ts_guess:
            flags += ["--ts-guess", str(ts_guess)]
    flags += input_chemistry_flags(inp)
    return flags


def build_remote_nmr_cmd_tail(
    spec: JobSpec,
    input_path: str = "inputs/input.xyz",
) -> list[str]:
    """E7 parity helper: append the NMR-specific argv for remote execution."""
    cmd: list[str] = ["--output", "."]
    inp = spec.input
    method = spec.method
    enumerate_mode = bool(inp.get("enumerate"))

    candidates = inp.get("candidates") if isinstance(inp.get("candidates"), list) else None
    if candidates:
        for idx, cand in enumerate(candidates):
            cand_source = (
                (cand.get("source") or cand.get("smiles") or cand.get("input"))
                if isinstance(cand, dict)
                else None
            )
            if enumerate_mode and cand_source and _looks_like_smiles(str(cand_source)):
                cmd += ["--input", str(cand_source)]
                continue
            cmd += ["--input", f"inputs/input_{idx}.xyz"]
    else:
        cmd += ["--input", str(input_path)]

    experiment = inp.get("experiment") or method.get("experiment")
    if isinstance(experiment, dict):
        exp_mode = (experiment or {}).get("mode", "assigned")
    else:
        exp_mode = "assigned"
    if exp_mode == "bruker":
        cmd += ["--bruker", "inputs/bruker"]
        refs = experiment.get("references") if isinstance(experiment, dict) else None
        if isinstance(refs, dict):
            for key, value in refs.items():
                cmd += ["--bruker-ref", f"{key}={value}"]
    else:
        exp_content = (experiment or {}).get("content") if isinstance(experiment, dict) else None
        if not exp_content:
            raise ValueError("nmr remote job requires experiment.content (spectrum text)")
        cmd += ["--spectrum", "inputs/experiment.txt"]

    if inp.get("enumerate"):
        cmd += ["--enumerate"]
        stereocenters = inp.get("stereocenters")
        if isinstance(stereocenters, str) and stereocenters.strip():
            cmd += ["--stereocenters", stereocenters.strip()]
        elif isinstance(stereocenters, list) and stereocenters:
            cmd += ["--stereocenters", ",".join(str(s) for s in stereocenters)]

    if spec.name:
        cmd += ["--name", spec.name]
    preset = censo_preset_from_method(method)
    if preset:
        cmd += ["--preset", preset]
    cmd += nmr_method_flags(method)
    solvent = censo_solvent_from_method(method)
    if solvent:
        cmd += ["--solvent", solvent]
    ewin = censo_ewin_from_method(method)
    if ewin is not None:
        cmd += ["--ewin", str(ewin)]
    return cmd


def derive_lsf_resources(
    spec: JobSpec,
    queue: str = _DEFAULT_QUEUE,
    walltime: str = _DEFAULT_WALLTIME,
    extra_flags: str = "",
) -> tuple[int, int, str, str, str]:
    """Derive LSF resource parameters from a :class:`JobSpec`."""
    res = spec.resources
    nproc = _coerce_int(res.get("nproc")) or _DEFAULT_NPROC
    total_mem_mb = _parse_total_mem_mb(res.get("mem"))
    if total_mem_mb is not None and nproc > 0:
        mem_mb_per_core = max(total_mem_mb // nproc, _MIN_MEM_MB_PER_CORE)
    else:
        mem_mb_per_core = _DEFAULT_MEM_MB_PER_CORE
    return nproc, mem_mb_per_core, queue, walltime, extra_flags


def build_lsf_script_spec(
    spec: JobSpec,
    job_id: str,
    node: RemoteNode,
    queue: str = _DEFAULT_QUEUE,
    walltime: str = _DEFAULT_WALLTIME,
    extra_flags: str = "",
    input_path: str = "inputs/input.xyz",
    config_path: str | None = None,
    remote_dir_name: str | None = None,
    python_executable: str | None = None,
    pre_cmds: list[str] | None = None,
) -> tuple[LSFScriptSpec, list[str]]:
    """Build both the CLI command and :class:`LSFScriptSpec` for a job.

    Args:
        spec: The scheduler job specification.
        job_id: The ACP job identifier (used for the BSUB job name and the
            remote working directory).
        node: The target remote compute node.
        queue: LSF queue name.
        walltime: LSF wall-clock limit.
        extra_flags: Additional BSUB flags.
        input_path: Relative path to the uploaded input file (default
            ``inputs/input.xyz``).
        config_path: Optional path to a job-level YAML config on the remote
            node (e.g. ``cccp.yaml`` in the job directory).
        remote_dir_name: Leaf directory name for the remote job dir (v1.2
            task-dir naming); falls back to ``job_id`` when not given.
        python_executable: Interpreter to use on the node.  When given it
            overrides ``node.python_executable`` — callers pass a
            probe-resolved (Python 3.10+) interpreter here so LSF scripts
            never fall back to a too-old default ``python``.
        pre_cmds: Shell lines injected into the generated script before
            the CLI runs (cluster-level environment setup).

    Returns:
        ``(lsf_spec, cli_command)``.
    """
    dir_leaf = remote_dir_name or job_id
    remote_job_dir = posixpath.join(node.remote_work_dir, dir_leaf)
    cli_command = build_remote_cli_command(
        spec,
        input_path=input_path,
        python_executable=python_executable or node.python_executable,
        config_path=config_path,
    )
    nproc, mem_mb_per_core, queue, walltime, extra_flags = derive_lsf_resources(
        spec, queue=queue, walltime=walltime, extra_flags=extra_flags
    )
    lsf_spec = LSFScriptSpec(
        job_name=f"acp_{job_id}",
        queue=queue,
        nproc=nproc,
        mem_mb_per_core=mem_mb_per_core,
        walltime=walltime,
        remote_code_dir=node.remote_code_dir,
        remote_job_dir=remote_job_dir,
        cli_command=cli_command,
        pre_cmds=list(pre_cmds or []),
        extra_flags=extra_flags,
    )
    return lsf_spec, cli_command


def generate_lsf_script(s: LSFScriptSpec) -> str:
    """Render a complete ``bsub`` submission script."""
    cli_str = " ".join(shlex.quote(arg) for arg in s.cli_command)
    lines: list[str] = [
        "#!/bin/bash",
        f"#BSUB -J {s.job_name}",
        f"#BSUB -q {s.queue}",
        f"#BSUB -n {s.nproc}",
        f"#BSUB -M {int(s.mem_mb_per_core * s.nproc * 1024 * 1.05)}",
    ]
    if s.walltime:
        lines.append(f"#BSUB -W {s.walltime}")
    lines += [
        f"#BSUB -o {s.remote_job_dir}/stdout.log",
        f"#BSUB -e {s.remote_job_dir}/stderr.log",
    ]
    if s.extra_flags:
        lines.append(f"#BSUB {s.extra_flags}")
    lines += [
        "",
        '_acp_record_exit() { [ -f .exit_code ] || echo "$?" > .exit_code; }',
        "trap 'exit $?' USR2 TERM INT HUP",
        "trap _acp_record_exit EXIT",
    ]
    # Cluster-level environment setup (module load, spack, ...) — injected
    # verbatim so module-based HPC clusters work without script surgery.
    if s.pre_cmds:
        lines.append("")
        lines.append("# cluster.pre_cmds (module/environment setup)")
        lines.extend(s.pre_cmds)
    lines += [
        "",
        f'export PYTHONPATH="{s.remote_code_dir}/src:$PYTHONPATH"',
        f'cd "{s.remote_job_dir}"',
        cli_str,
        "echo $? > .exit_code",
        "",
    ]
    return "\n".join(lines)


# ── Remote artifact pull helpers ────────────────────────────────────────


def remote_artifact_pull_list() -> list[str]:
    """Standard remote artifact paths to fetch after job completion."""
    return [_REMOTE_RESULT_MANIFEST_PATH, _REMOTE_CHECKPOINT_PATH]


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_total_mem_mb(value: Any) -> int | None:
    """Parse a memory specification into megabytes."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        return None
    units = (
        ("tb", 1024 * 1024),
        ("gb", 1024),
        ("mb", 1),
        ("t", 1024 * 1024),
        ("g", 1024),
        ("m", 1),
    )
    for suffix, factor in units:
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                return int(float(number) * factor)
            except ValueError:
                return None
    try:
        return int(float(text))
    except ValueError:
        return None
