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
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from acp.catalog import method_levels_to_cli_flags
from acp.scheduler.jobs import (
    BATCH_CONFIG_FILENAME,
    MECHANISM_CONFIG_FILENAME,
    SCAN_CONFIG_FILENAME,
    JobSpec,
    batchoptimize_method_flags,
    censo_ewin_from_method,
    censo_preset_from_method,
    censo_solvent_from_method,
    confsearch_method_flags,
    highconfirm_method_flags,
    input_chemistry_flags,
    lowconfirm_method_flags,
    nmr_method_flags,
    pessearch_method_flags,
    scan_method_flags,
    stage_batch_request,
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


def _mechanism_role_source(
    inp: dict[str, Any],
    role: str,
    materialized_role_paths: dict[str, str] | None = None,
) -> str | None:
    if materialized_role_paths and role in materialized_role_paths:
        return materialized_role_paths[role]

    legacy = inp.get(f"{role}_source")
    if legacy:
        return str(legacy)

    role_value = inp.get(role)
    if isinstance(role_value, dict):
        nested_source = (
            role_value.get("source") or role_value.get("input") or role_value.get("smiles")
        )
        if nested_source and role_value.get("source_type") != "xyz_text":
            return str(nested_source)
        return None

    if role_value:
        return str(role_value)
    return None


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

_DEFAULT_NPROC = 8
_DEFAULT_MEM_MB_PER_CORE = 2000
_DEFAULT_QUEUE = "normal"
# No walltime by default — LSF jobs run to completion unless an operator
# explicitly configures `cluster.walltime`.  An empty value omits the
# ``#BSUB -W`` directive entirely (task: drop the default 24h timeout).
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
    extra_flags: str = ""


def build_remote_cli_command(
    spec: JobSpec,
    input_path: str = "inputs/input.xyz",
    python_executable: str = "python",
    config_path: str | None = None,
    materialized_role_paths: dict[str, str] | None = None,
    mechanism_config_path: str | None = None,
) -> list[str]:
    """Build the ``python -m acp.cli ...`` argv for remote execution.

    The mapping rules are identical to :meth:`JobRunner._build_cmd`:

    * mechanism / ensemble / energy / simple workflows → ``acp.cli run <wf>``

    The ``--output`` target is ``"."`` (the remote job dir, after ``cd``).

    Args:
        python_executable: Interpreter to use on the node (from
            :attr:`RemoteNode.python_executable`).  Defaults to ``"python"``.
        config_path: Optional path to a job-level YAML config on the remote
            node. When provided, ``--config`` is added to the CLI.
    """
    py = python_executable or "python"
    wf = spec.workflow
    if wf not in (
        "mechanism",
        "mech-conf",
        "mech-step",
        "mech-confirm",
        "mech-chain",
        "Confsearch",
        "PESsearch",
        "Lowconfirm",
        "Highconfirm",
        "BatchOptimize",
        "ensemble",
        "energy",
        "nmr",
        "xtbmd_censo_energy",
        "singlepoint",
        "optimize",
        "frequency",
        "optfreq",
        "optfreqsp",
        "scan",
        "irc",
        "xtb_optimize",
    ):
        raise ValueError(f"No remote subprocess mapping for workflow: {wf}")

    cmd: list[str] = [py, "-m", "acp.cli", "run", wf]

    inp = spec.input
    method = spec.method
    res = spec.resources

    # Stage workflows consume a validated artifact reference, not a
    # structure source (plan §8) — the handoff payload is expected to be
    # staged into the remote job dir as WORK/01_PREPARE/handoff/ by the
    # submit path (remote stage jobs assume the source artifact ships with
    # the job).
    if wf in ("PESsearch", "Lowconfirm", "Highconfirm"):
        cmd += build_remote_stage_cmd_tail(spec)
        return cmd

    # NMR has a distinct multi-candidate + spectrum payload shape.
    if wf == "nmr":
        cmd += build_remote_nmr_cmd_tail(spec, input_path)
        return cmd

    source = ""
    if wf == "BatchOptimize":
        artifact = inp.get("from_artifact") or inp.get("source")
        items_file = inp.get("items_file")
        if artifact:
            source = str(artifact)
            cmd += ["--from-artifact", str(artifact), "--output", "."]
        elif items_file:
            source = str(items_file)
            cmd += ["--items-file", str(items_file), "--output", "."]
        else:
            raise ValueError("BatchOptimize job requires input.from_artifact or input.items_file")
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
    elif wf == "mechanism":
        cmd += ["--input", str(source), "--output", "."]
        cmd += ["--mechanism-config", mechanism_config_path or MECHANISM_CONFIG_FILENAME]
        if spec.name:
            cmd += ["--name", spec.name]
        product = _mechanism_role_source(inp, "product", materialized_role_paths)
        if product:
            cmd += ["--product", str(product)]
        ts_guess = _mechanism_role_source(inp, "ts_guess", materialized_role_paths)
        if ts_guess:
            cmd += ["--ts-guess", str(ts_guess)]
        routes = inp.get("routes")
        if routes:
            cmd += ["--routes", json.dumps(routes)]
    elif wf == "mech-conf":
        cmd += ["--input", str(source), "--output", "."]
        if method.get("mode"):
            cmd += ["--mode", str(method["mode"])]
        if spec.name:
            cmd += ["--name", spec.name]
    elif wf == "mech-step":
        cmd += ["--source", str(source), "--output", "."]
        target = inp.get("target") or method.get("target")
        if target:
            cmd += ["--target", str(target)]
        plan = inp.get("coordinate_plan") or method.get("coordinate_plan")
        if plan is not None:
            cmd += ["--plan", json.dumps(plan)]
        if method.get("strategy"):
            cmd += ["--strategy", str(method["strategy"])]
        if method.get("fidelity"):
            cmd += ["--fidelity", str(method["fidelity"])]
    elif wf == "mech-confirm":
        step_manifest = inp.get("from") or inp.get("step_manifest") or source
        cmd += ["--from", str(step_manifest), "--output", "."]
        if method.get("select"):
            cmd += ["--select", str(method["select"])]
        if method.get("fidelity"):
            cmd += ["--fidelity", str(method["fidelity"])]
    elif wf == "mech-chain":
        chain_config = inp.get("config") or inp.get("chain_config") or source
        cmd += ["--config", str(chain_config), "--output", "."]
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
            # CLI defaults to rank1-only; an explicit opt-out must be
            # forwarded so the full-ensemble path is restored.
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
        # Shared flag builder (E7): identical to JobRunner._build_cmd so
        # local and remote execution can never drift (DevDoc §10.2).
        cmd += xtbmd_method_flags(method)
        solvent = censo_solvent_from_method(method)
        if solvent:
            cmd += ["--solvent", solvent]
        ewin = censo_ewin_from_method(method)
        if ewin is not None:
            cmd += ["--ewin", str(ewin)]
    elif wf == "irc":
        cmd += ["--input", str(source), "--output", "."]
        input_role = inp.get("input_role")
        if input_role:
            cmd += ["--input-role", str(input_role)]
        directions = inp.get("directions") or ["both"]
        direction_names = {str(direction).strip().lower() for direction in directions}
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
    elif wf in ("singlepoint", "optimize", "frequency", "scan", "optfreq", "optfreqsp"):
        cmd += ["--input", str(source), "--output", "."]
        if spec.name:
            cmd += ["--name", spec.name]
        levels = method.get("levels", {})
        if levels:
            if wf == "optfreqsp":
                prefix_map = {"optfreq": "", "single_point": "sp-", "thermo": ""}
                cmd += method_levels_to_cli_flags(levels, prefix_map)
            else:
                cmd += method_levels_to_cli_flags(levels)
        if wf == "scan":
            cmd += scan_method_flags(method, inp)
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

    # Resources and input chemistry (applies to all workflows).
    if config_path and wf != "mech-chain":
        cmd += ["--config", config_path]
    if res.get("nproc") is not None:
        cmd += ["--nproc", str(res["nproc"])]
    if res.get("mem"):
        cmd += ["--mem", str(res["mem"])]
    cmd += input_chemistry_flags(inp)

    return cmd


def build_remote_scan_config_payload(spec: JobSpec) -> dict[str, Any] | None:
    """Bond-scan scan_request payload staged as ``scan_config.json``.

    Returns ``None`` for non-bond-scan jobs.  For ``task_artifact`` sources
    the artifact path is rewritten to the staged handoff manifest location
    (``WORK/01_PREPARE/handoff/``) inside the remote job dir.
    """
    wf = spec.workflow
    if not (wf == "PESsearch" and str(spec.method.get("mode") or "") == "bond_length_scan"):
        return None
    scan_request = dict(spec.input.get("scan_request") or spec.method.get("scan_request") or {})
    src = dict(scan_request.get("source") or {})
    if src.get("source_type") == "task_artifact":
        src["artifact_path"] = str(
            PurePosixPath("WORK") / "01_PREPARE" / "handoff" / _stage_manifest_name(wf)
        )
        scan_request["source"] = src
    return scan_request


def build_remote_stage_cmd_tail(spec: JobSpec) -> list[str]:
    """E7 parity helper: append the stage-workflow argv for remote execution.

    Mirrors :meth:`JobRunner._build_stage_cmd`. The handoff payload
    (manifest + referenced geometry dirs) must already be staged under
    ``WORK/01_PREPARE/handoff/`` in the remote job dir; the flag points the
    CLI at the staged manifest.  Bond-scan jobs read ``scan_config.json``
    from the remote job dir root (staged by the submit path).
    """
    wf = spec.workflow
    inp = spec.input
    method = spec.method
    flags: list[str] = ["--output", "."]
    bond_scan_mode = wf == "PESsearch" and str(method.get("mode") or "") == "bond_length_scan"
    batch_mode = wf in ("Lowconfirm", "Highconfirm") and stage_batch_request(spec) is not None
    if bond_scan_mode:
        flags += ["--mode", "bond_length_scan"]
        flags += ["--scan-config", SCAN_CONFIG_FILENAME]
        return flags + input_chemistry_flags(inp)
    if batch_mode:
        flags += ["--batch-config", BATCH_CONFIG_FILENAME]
        if wf == "Lowconfirm":
            flags += lowconfirm_method_flags(method)
        else:
            flags += highconfirm_method_flags(method)
        return flags + input_chemistry_flags(inp)
    from_manifest = inp.get("from")
    if from_manifest:
        flags += ["--from", str(from_manifest)]
    else:
        flags += [
            "--from",
            str(PurePosixPath("WORK") / "01_PREPARE" / "handoff" / _stage_manifest_name(wf)),
        ]
    if wf == "PESsearch":
        flags += pessearch_method_flags(method)
        plan = inp.get("coordinate_plan") or method.get("coordinate_plan")
        if plan is not None:
            flags += ["--plan", json.dumps(plan)]
        product = inp.get("product")
        if product:
            flags += ["--product", str(product)]
        ts_guess = inp.get("ts_guess")
        if ts_guess:
            flags += ["--ts-guess", str(ts_guess)]
    elif wf == "Lowconfirm":
        flags += lowconfirm_method_flags(method)
    elif wf == "Highconfirm":
        flags += highconfirm_method_flags(method)
    flags += input_chemistry_flags(inp)
    return flags


def _stage_manifest_name(workflow: str) -> str:
    if workflow == "PESsearch":
        return "confsearch_manifest.json"
    if workflow == "Lowconfirm":
        return "s2_path_manifest.json"
    return "s3_lowconfirm_manifest.json"


def build_remote_nmr_cmd_tail(
    spec: JobSpec,
    input_path: str = "inputs/input.xyz",
) -> list[str]:
    """E7 parity helper: append the NMR-specific argv for remote execution.

    Mirrors :meth:`JobRunner._build_nmr_cmd`. The remote ``inputs/``
    directory is expected to contain one ``input_<i>.xyz`` per candidate
    (synced by the remote code-sync layer) plus ``experiment.txt``.

    Single-candidate fallback: when ``spec.input`` has no ``candidates``
    list, the synced ``input_path`` is reused as the lone candidate.
    """
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
                # Enumerate needs bond information (stereochemistry is
                # topological): pass the SMILES verbatim. The synced
                # input_<i>.xyz has no bond table and cannot be enumerated.
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
        # P3: the extracted Bruker tree is expected at inputs/bruker
        # (mirrors JobRunner._materialize_bruker_asset; the remote input
        # staging layer must sync it alongside the candidate inputs).
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

    # P2: diastereomer enumeration (single-candidate payload only). Mirrors
    # JobRunner._build_nmr_cmd — the backend expands the one candidate.
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
    """Derive LSF resource parameters from a :class:`JobSpec`.

    Returns:
        ``(nproc, mem_mb_per_core, queue, walltime, extra_flags)``.
    """
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
    materialized_role_paths: dict[str, str] | None = None,
    remote_dir_name: str | None = None,
) -> tuple[LSFScriptSpec, list[str]]:
    """Build both the CLI command and :class:`LSFScriptSpec` for a job.

    Args:
        spec: The scheduler job specification.
        job_id: The ACP job identifier (used for the BSUB job name).
        node: The target remote compute node.
        queue: LSF queue name.
        walltime: LSF wall-clock limit.
        extra_flags: Additional BSUB flags.
        input_path: Relative path to the uploaded input file (default
            ``inputs/input.xyz``).
        config_path: Optional path to a job-level YAML config on the remote
            node (e.g. ``cccp.yaml`` in the job directory).
        remote_dir_name: v2 task directory name for the remote working
            directory leaf (defaults to *job_id* for backward compatibility).

    Returns:
        ``(lsf_spec, cli_command)``.
    """
    dir_leaf = remote_dir_name or job_id
    remote_job_dir = posixpath.join(node.remote_work_dir, dir_leaf)
    cli_command = build_remote_cli_command(
        spec,
        input_path=input_path,
        python_executable=node.python_executable,
        config_path=config_path,
        materialized_role_paths=materialized_role_paths,
        mechanism_config_path=(
            posixpath.join(remote_job_dir, MECHANISM_CONFIG_FILENAME)
            if spec.workflow == "mechanism"
            else None
        ),
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
        extra_flags=extra_flags,
    )
    return lsf_spec, cli_command


def generate_lsf_script(s: LSFScriptSpec) -> str:
    """Render a complete ``bsub`` submission script.

    The script sets ``PYTHONPATH`` to include the synced source tree,
    ``cd``\\ s into the remote job directory, runs the CLI command, and
    writes the exit code to ``.exit_code`` for reliable status detection.

    A termination-signal trap ensures ``.exit_code`` is recorded even when
    LSF kills the job (e.g. a configured ``-W`` walltime / ``RUNLIMIT``
    sends ``SIGUSR2`` to the whole process group) \u2014 without it the
    trailing ``echo $? > .exit_code`` never runs and the scheduler cannot
    tell a walltime-killed job from one that is still running.
    """
    cli_str = " ".join(shlex.quote(arg) for arg in s.cli_command)
    lines: list[str] = [
        "#!/bin/bash",
        f"#BSUB -J {s.job_name}",
        f"#BSUB -q {s.queue}",
        f"#BSUB -n {s.nproc}",
        f"#BSUB -M {int(s.mem_mb_per_core * s.nproc * 1024 * 1.05)}",
    ]
    # Only emit a walltime directive when one is explicitly configured;
    # an empty walltime means "no LSF run-time limit" (default).
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
        "# Record the workflow exit code even if LSF terminates the job by",
        "# signal (walltime/RUNLIMIT).  Forward such signals to a normal",
        "# shell exit so the EXIT trap fires and writes .exit_code.",
        '_acp_record_exit() { [ -f .exit_code ] || echo "$?" > .exit_code; }',
        "trap 'exit $?' USR2 TERM INT HUP",
        "trap _acp_record_exit EXIT",
        f'export PYTHONPATH="{s.remote_code_dir}/src:$PYTHONPATH"',
        f'cd "{s.remote_job_dir}"',
        cli_str,
        "echo $? > .exit_code",
        "",
    ]
    return "\n".join(lines)


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
    """Parse a memory specification into megabytes.

    Accepts plain numbers (treated as MB), or strings with suffixes
    like ``"16GB"``, ``"16000MB"``, ``"2TB"`` (case-insensitive).
    """
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
