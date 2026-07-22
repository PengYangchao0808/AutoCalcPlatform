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
from dataclasses import dataclass
from typing import Any

from acp.catalog import method_levels_to_cli_flags
from acp.scheduler.jobs import (
    JobSpec,
    censo_ewin_from_method,
    censo_preset_from_method,
    censo_solvent_from_method,
)
from acp.scheduler.remote.config import RemoteNode

logger = logging.getLogger(__name__)

__all__ = [
    "LSFScriptSpec",
    "build_lsf_script_spec",
    "build_remote_cli_command",
    "derive_lsf_resources",
    "generate_lsf_script",
]

_DEFAULT_NPROC = 8
_DEFAULT_MEM_MB_PER_CORE = 2000
_DEFAULT_QUEUE = "normal"
_DEFAULT_WALLTIME = "24:00"
_MIN_MEM_MB_PER_CORE = 256

_GFN_DISPLAY_TO_INT: dict[str, int] = {
    "GFN0-xTB": 0, "GFN1-xTB": 1, "GFN2-xTB": 2,
    "0": 0, "1": 1, "2": 2,
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
) -> list[str]:
    """Build the ``python -m acp.cli ...`` argv for remote execution.

    The mapping rules are identical to :meth:`JobRunner._build_cmd`:

    * conformer / mechanism / ensemble / energy → ``acp.cli run <wf>``
    * nmr → ``acp.cli run nmr``
    * benchmark → ``acp.cli benchmark``

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
        "conformer", "nmr", "benchmark", "mechanism", "ensemble", "energy",
        "singlepoint", "optimize", "frequency", "optfreq", "optfreqsp",
        "xtb_optimize",
    ):
        raise ValueError(f"No remote subprocess mapping for workflow: {wf}")

    if wf == "benchmark":
        cmd: list[str] = [py, "-m", "acp.cli", "benchmark"]
    else:
        cmd = [py, "-m", "acp.cli", "run", wf]

    inp = spec.input
    method = spec.method
    res = spec.resources

    source = input_path or inp.get("source") or inp.get("input") or inp.get("smiles") or ""
    if not source:
        raise ValueError(f"{wf} job requires a valid input structure")

    if wf in {"conformer", "mechanism"}:
        cmd += ["--input", str(source), "--output", "."]
        if wf == "conformer" and method.get("protocol"):
            cmd += ["--protocol", str(method["protocol"])]
        if spec.name:
            cmd += ["--name", spec.name]
        if wf == "conformer" and method.get("levels"):
            cmd += ["--levels", json.dumps(method["levels"])]
    elif wf in {"ensemble", "energy"}:
        cmd += ["--input", str(source), "--output", "."]
        preset = censo_preset_from_method(method)
        if preset:
            cmd += ["--preset", preset]
        if spec.name:
            cmd += ["--name", spec.name]
        if wf == "energy" and method.get("no_opt"):
            cmd += ["--no-opt"]
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
    elif wf == "nmr":
        cmd += ["--input", str(source), "--output", "."]
        if method.get("protocol"):
            cmd += ["--protocol", str(method["protocol"])]
        if method.get("backend"):
            cmd += ["--backend", str(method["backend"])]
    elif wf in ("singlepoint", "optimize", "frequency", "optfreq", "optfreqsp"):
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
    else:  # benchmark
        cmd += ["--input", str(source), "--output", "."]
        if method.get("benchmark_level"):
            cmd += ["--benchmark-level", str(method["benchmark_level"])]
        if method.get("protocols"):
            cmd += ["--protocols", str(method["protocols"])]

    # Resources and input chemistry (applies to all workflows).
    if config_path:
        cmd += ["--config", config_path]
    if res.get("nproc") is not None:
        cmd += ["--nproc", str(res["nproc"])]
    if res.get("mem"):
        cmd += ["--mem", str(res["mem"])]
    if inp.get("charge") is not None:
        cmd += ["--charge", str(inp["charge"])]
    if inp.get("multiplicity") is not None:
        cmd += ["--multiplicity", str(inp["multiplicity"])]

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
            node (e.g. ``conformer_search.yaml`` in the job directory).

    Returns:
        ``(lsf_spec, cli_command)``.
    """
    cli_command = build_remote_cli_command(
        spec,
        input_path=input_path,
        python_executable=node.python_executable,
        config_path=config_path,
    )
    nproc, mem_mb_per_core, queue, walltime, extra_flags = derive_lsf_resources(
        spec, queue=queue, walltime=walltime, extra_flags=extra_flags
    )
    remote_job_dir = posixpath.join(node.remote_work_dir, job_id)

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
    """
    cli_str = " ".join(shlex.quote(arg) for arg in s.cli_command)
    lines: list[str] = [
        "#!/bin/bash",
        f"#BSUB -J {s.job_name}",
        f"#BSUB -q {s.queue}",
        f"#BSUB -n {s.nproc}",
        f"#BSUB -M {int(s.mem_mb_per_core * s.nproc * 1024 * 1.05)}",
        f"#BSUB -W {s.walltime}",
        f"#BSUB -o {s.remote_job_dir}/stdout.log",
        f"#BSUB -e {s.remote_job_dir}/stderr.log",
    ]
    if s.extra_flags:
        lines.append(f"#BSUB {s.extra_flags}")
    lines += [
        "",
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
