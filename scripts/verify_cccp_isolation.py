#!/usr/bin/env python3
"""A1/A2 isolation acceptance: the cccp task layer runs without ``acp``.

Plan todo 35 (``.omo/plans/acp-cccp-architecture-remediation.md`` lines
424-430).  This script proves that ``cccp.calculation`` — the single task
execution station — is importable **and callable** in an environment where
the ``acp`` package is genuinely absent.

How the isolation is enforced (mirrors the proven technique of
``tests/test_cccp_isolation.py``):

* The driver copies only the ``src/cccp`` sources into a private temp tree
  and runs a fresh child interpreter with ``PYTHONPATH`` pointing at that
  tree.
* The child prunes every ambient ``sys.path`` entry that could expose an
  ``acp``/``cccp`` package (editable installs / user-site ``.pth`` entries),
  proves ``importlib.util.find_spec("acp") is None``, and only then installs
  an ``importlib.abc.MetaPathFinder`` that raises ``ModuleNotFoundError`` for
  ``acp`` and ``acp.*`` as a backstop — before any ``cccp`` module is
  imported.
* The child imports all fourteen published task cores and **calls** each one
  with a minimal synthetic request.  A ``TaskContext.backend`` stub with no
  subprocess/QC dependency makes the calls deterministic: request
  validation, capability selection, the translation layer, capability
  dispatch and result normalization all execute, while every capability
  method raises ``RuntimeError`` so the task returns a structured failed
  ``TaskResult``.  No external binary is ever launched and no ACP object is
  injected (the stub lives entirely in the child, built from cccp types).
* ``thermochemistry`` — the one task that does not take a backend — has its
  shared ``cccp.qc.shermo_adapter.execute_shermo`` seam patched with a local
  synthetic run record; the task core and its payload normalization still
  execute.

A line ``::cccp-task:: {...}`` is printed per task; ``TaskResult`` and typed
``CalculationError`` subclasses both count as *structured*.  Any other
exception (``ModuleNotFoundError``/``ImportError`` from ``acp`` included) is
unstructured and fails the run.  Exit code is 0 iff all fourteen tasks are
structured and no ``acp`` module leaked into ``sys.modules``.

Usage::

    python3.11 scripts/verify_cccp_isolation.py --no-acp
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CCCP_SRC = REPO_ROOT / "src" / "cccp"

TASK_MARKER = "::cccp-task::"
SUMMARY_MARKER = "::cccp-summary::"

#: The fourteen published task cores (7 core + 7 P2), in plan order.
EXPECTED_TASKS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
    "thermochemistry",
    "conformer_search",
    "md_sampling",
    "clustering",
    "censo_refine",
    "nmr_shielding",
    "xtb_path_search",
    "orca_gradient",
)


_CHILD_SCRIPT = r'''
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

TASK_MARKER = "::cccp-task::"
SUMMARY_MARKER = "::cccp-summary::"


class BlockACP(importlib.abc.MetaPathFinder):
    """Backstop finder: importing acp/acp.* raises ModuleNotFoundError."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "acp" or fullname.startswith("acp."):
            raise ModuleNotFoundError("audit blocked " + fullname, name=fullname)


variant = sys.argv[1]
allowed_root = sys.argv[2]
workdir = Path(sys.argv[3])


def _emit_summary(ok, **extra):
    payload = {"ok": bool(ok)}
    payload.update(extra)
    print(SUMMARY_MARKER + json.dumps(payload, sort_keys=True))


if variant != "cccp-only":
    _emit_summary(False, reason="unknown variant " + variant)
    sys.exit(2)

# ── build a genuine cccp-only sys.path ──────────────────────────────────
kept = []
for entry in sys.path:
    if entry == allowed_root or entry == "":
        kept.append(entry)
        continue
    base = Path(entry or ".")
    try:
        exposes = (
            (base / "acp").exists()
            or (base / "acp.py").exists()
            or (base / "cccp").exists()
            or (base / "cccp.py").exists()
        )
    except OSError:
        exposes = False
    if exposes:
        continue
    kept.append(entry)
sys.path[:] = kept
if allowed_root not in sys.path:
    sys.path.insert(0, allowed_root)

if importlib.util.find_spec("acp") is not None:
    _emit_summary(False, reason="acp still importable in the cccp-only environment")
    sys.exit(2)

sys.meta_path.insert(0, BlockACP())
if any(name == "acp" or name.startswith("acp.") for name in sys.modules):
    _emit_summary(False, reason="acp preloaded before the blocker was installed")
    sys.exit(2)

workdir.mkdir(parents=True, exist_ok=True)
out_dir = workdir / "out"
out_dir.mkdir(parents=True, exist_ok=True)

# ── imports (all cccp; acp is blocked) ──────────────────────────────────
try:
    from cccp.calculation.context import TaskContext
    from cccp.calculation.contracts import CASSCFSpec, StructureRole
    from cccp.calculation.errors import CalculationError
    from cccp.calculation.requests import (
        CasscfOptions,
        CensoRefineOptions,
        ClusteringOptions,
        ConformerSearchOptions,
        FrequencyOptions,
        IrcOptions,
        MdSamplingOptions,
        MethodSpec,
        NmrShieldingOptions,
        OptimizeOptions,
        OrcaGradientOptions,
        ScanCoordinateSpec,
        ScanOptions,
        SinglePointOptions,
        StructureInput,
        TaskKind,
        TaskRequest,
        ThermochemistryOptions,
        XtbPathSearchOptions,
    )
    from cccp.calculation.results import TaskResult
    from cccp.calculation.tasks.casscf import run_casscf
    from cccp.calculation.tasks.censo_refine import run_censo_refine
    from cccp.calculation.tasks.clustering import run_clustering
    from cccp.calculation.tasks.conformer_search import run_conformer_search
    from cccp.calculation.tasks.frequency import run_frequency
    from cccp.calculation.tasks.irc import run_irc
    from cccp.calculation.tasks.md_sampling import run_md_sampling
    from cccp.calculation.tasks.nmr_shielding import run_nmr_shielding
    from cccp.calculation.tasks.optimize import run_optimize
    from cccp.calculation.tasks.orca_gradient import run_orca_gradient
    from cccp.calculation.tasks.scan import run_scan
    from cccp.calculation.tasks.singlepoint import run_singlepoint
    from cccp.calculation.tasks.thermochemistry import run_thermochemistry
    from cccp.calculation.tasks.xtb_path_search import run_xtb_path_search
except BaseException as exc:  # noqa: BLE001 - report any import failure as unstructured
    _emit_summary(
        False,
        reason="cccp import failed",
        error=type(exc).__name__,
        message=str(exc),
    )
    sys.exit(2)


class StubBackend:
    """All-capability fake backend: every capability call raises RuntimeError.

    The task cores catch ``RuntimeError`` as a runtime/scientific failure and
    return a structured failed ``TaskResult``; the parent counts that as a
    structured result.  Nothing is written by the stub and no binary is
    touched.  ``name`` is a plain attribute so ``getattr(backend, "name")``
    keeps working.
    """

    name = "isolation-stub"

    def __getattr__(self, item):
        def _capability(*args, **kwargs):
            raise RuntimeError("isolation stub rejects external execution: " + item)

        return _capability


STUB = StubBackend()
COORDS = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.96), (0.93, 0.0, 0.0))
SYMS = ("O", "H", "H")


def _ctx(with_backend=True):
    return TaskContext(
        backend=STUB if with_backend else None,
        workdir=workdir,
        input_base=workdir,
        config={},
    )


def _inline(role=StructureRole.MINIMUM):
    return StructureInput(coordinates=COORDS, symbols=SYMS, role=role)


def call_singlepoint():
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=_inline(),
        level=MethodSpec(method="HF", basis="def2-SVP"),
        options=SinglePointOptions(),
        output_dir=out_dir,
    )
    return run_singlepoint(request, context=_ctx())


def call_optimize():
    request = TaskRequest(
        task=TaskKind.OPTIMIZE,
        structure=_inline(),
        level=MethodSpec(method="HF", basis="def2-SVP"),
        options=OptimizeOptions(),
        output_dir=out_dir,
    )
    return run_optimize(request, context=_ctx())


def call_frequency():
    request = TaskRequest(
        task=TaskKind.FREQUENCY,
        structure=_inline(),
        level=MethodSpec(method="HF", basis="def2-SVP"),
        options=FrequencyOptions(),
        output_dir=out_dir,
    )
    return run_frequency(request, context=_ctx())


def call_scan():
    options = ScanOptions(
        coordinates=(
            ScanCoordinateSpec(
                atoms=(1, 2), start=0.8, end=1.2, kind="distance", atom_index_base=1
            ),
        ),
        points=3,
    )
    request = TaskRequest(
        task=TaskKind.SCAN,
        structure=_inline(),
        options=options,
        output_dir=out_dir,
    )
    return run_scan(request, context=_ctx())


def call_irc():
    request = TaskRequest(
        task=TaskKind.IRC,
        structure=_inline(StructureRole.TRANSITION_STATE),
        options=IrcOptions(),
        output_dir=out_dir,
    )
    return run_irc(request, context=_ctx())


def call_casscf():
    options = CasscfOptions(
        spec=CASSCFSpec(active_electrons=2, active_orbitals=2, multiplicity=1)
    )
    request = TaskRequest(
        task=TaskKind.CASSCF,
        structure=_inline(),
        options=options,
        output_dir=out_dir,
    )
    return run_casscf(request, context=_ctx())


def call_thermochemistry():
    freq_log = workdir / "freq.log"
    freq_log.write_text("0\n\n", encoding="utf-8")
    request = TaskRequest(
        task=TaskKind.THERMOCHEMISTRY,
        options=ThermochemistryOptions(freq_log_path=freq_log),
        output_dir=out_dir,
    )

    class _Outcome:
        values = {"h_sum": -1.0, "s_total": 0.0}
        gibbs = -1.0
        gibbs_source = "g_sum"

    class _RunRequest:
        standard_state = "1atm"
        sp_energy_hartree = -1.0

    class _RunContext:
        output_file = out_dir / "freq.sum"

    class _FakeRun:
        success = True
        error = None
        metadata = {"gibbs_hartree": -1.0}
        outcome = _Outcome()
        request = _RunRequest()
        context = _RunContext()

    from cccp.calculation.tasks import thermochemistry as _thermo

    with patch.object(_thermo, "execute_shermo", return_value=_FakeRun()):
        return run_thermochemistry(request, context=_ctx(with_backend=False))


def call_conformer_search():
    request = TaskRequest(
        task=TaskKind.CONFORMER_SEARCH,
        structure=_inline(),
        options=ConformerSearchOptions(),
        output_dir=out_dir,
    )
    return run_conformer_search(request, context=_ctx())


def call_md_sampling():
    request = TaskRequest(
        task=TaskKind.MD_SAMPLING,
        structure=_inline(),
        options=MdSamplingOptions(),
        output_dir=out_dir,
    )
    return run_md_sampling(request, context=_ctx())


def call_clustering():
    # Inline ensemble shape: n_frames * n_atoms stacked; 1 atom, 2 frames.
    structure = StructureInput(
        coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.1)),
        symbols=("C",),
    )
    request = TaskRequest(
        task=TaskKind.CLUSTERING,
        structure=structure,
        options=ClusteringOptions(),
        output_dir=out_dir,
    )
    return run_clustering(request, context=_ctx())


def call_censo_refine():
    ensemble = workdir / "ensemble.xyz"
    ensemble.write_text("1\nframe 0 -1.0\nC 0.0 0.0 0.0\n", encoding="utf-8")
    request = TaskRequest(
        task=TaskKind.CENSO_REFINE,
        structure=StructureInput(path=ensemble),
        options=CensoRefineOptions(),
        output_dir=out_dir,
    )
    return run_censo_refine(request, context=_ctx())


def call_nmr_shielding():
    request = TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=_inline(),
        level=MethodSpec(method="HF", basis="def2-SVP"),
        options=NmrShieldingOptions(),
        output_dir=out_dir,
    )
    return run_nmr_shielding(request, context=_ctx())


def call_xtb_path_search():
    options = XtbPathSearchOptions(
        end_structure=StructureInput(coordinates=COORDS, symbols=SYMS)
    )
    request = TaskRequest(
        task=TaskKind.XTB_PATH_SEARCH,
        structure=_inline(),
        options=options,
        output_dir=out_dir,
    )
    return run_xtb_path_search(request, context=_ctx())


def call_orca_gradient():
    request = TaskRequest(
        task=TaskKind.ORCA_GRADIENT,
        structure=_inline(),
        level=MethodSpec(method="HF", basis="def2-SVP"),
        options=OrcaGradientOptions(),
        output_dir=out_dir,
    )
    return run_orca_gradient(request, context=_ctx())


CALLS = (
    ("singlepoint", call_singlepoint),
    ("optimize", call_optimize),
    ("frequency", call_frequency),
    ("scan", call_scan),
    ("irc", call_irc),
    ("casscf", call_casscf),
    ("thermochemistry", call_thermochemistry),
    ("conformer_search", call_conformer_search),
    ("md_sampling", call_md_sampling),
    ("clustering", call_clustering),
    ("censo_refine", call_censo_refine),
    ("nmr_shielding", call_nmr_shielding),
    ("xtb_path_search", call_xtb_path_search),
    ("orca_gradient", call_orca_gradient),
)

structured_count = 0
for task_name, call in CALLS:
    try:
        result = call()
    except CalculationError as exc:
        line = {
            "task": task_name,
            "structured": True,
            "kind": "typed_error",
            "error": type(exc).__name__,
            "message": str(exc)[:400],
        }
    except BaseException as exc:  # noqa: BLE001 - classify any other failure
        line = {
            "task": task_name,
            "structured": False,
            "kind": "unstructured",
            "error": type(exc).__name__,
            "message": str(exc)[:400],
        }
    else:
        is_result = isinstance(result, TaskResult)
        error_kind = getattr(getattr(result, "error_kind", None), "value", None)
        line = {
            "task": task_name,
            "structured": bool(is_result),
            "kind": "TaskResult" if is_result else "unexpected_return",
            "status": getattr(result, "status", None) if is_result else None,
            "complete": getattr(result, "complete", None) if is_result else None,
            "error_kind": error_kind,
            "return_type": type(result).__name__,
        }
    if line["structured"]:
        structured_count += 1
    print(TASK_MARKER + json.dumps(line, sort_keys=True))

leaked = sorted(
    name for name in sys.modules if name == "acp" or name.startswith("acp.")
)
_emit_summary(
    structured_count == len(CALLS) and not leaked,
    task_count=len(CALLS),
    structured=structured_count,
    leaked_acp_modules=leaked,
)
sys.exit(0 if (structured_count == len(CALLS) and not leaked) else 1)
'''


def _build_child_tree(tmp_root: Path) -> Path:
    """Copy only the cccp sources into ``tmp_root/cccp`` and return tmp_root."""
    shutil.copytree(
        CCCP_SRC,
        tmp_root / "cccp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return tmp_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the cccp task layer runs in a no-ACP environment (A1/A2)."
    )
    parser.add_argument(
        "--no-acp",
        action="store_true",
        default=False,
        help="isolation mode (default); the acp package is blocked in the child",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="child interpreter timeout in seconds (default: 300)",
    )
    args = parser.parse_args(argv)
    # Isolation is the only supported mode: --no-acp is accepted explicitly
    # and is also the default.
    if not args.no_acp:
        print("[verify_cccp_isolation] --no-acp not passed; isolation mode is the default")

    with tempfile.TemporaryDirectory(prefix="cccp_isolation_") as tmp:
        tmp_root = Path(tmp)
        _build_child_tree(tmp_root)
        workdir = tmp_root / "work"
        workdir.mkdir()

        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmp_root)
        env["ACP_DISABLE_MPI_SNIFF"] = "1"
        # Do not let an inherited acp exposing path survive via sitecustomize.
        cmd = [
            sys.executable,
            "-c",
            _CHILD_SCRIPT,
            "cccp-only",
            str(tmp_root),
            str(workdir),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(workdir),
            timeout=args.timeout,
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")

    task_lines: dict[str, dict[str, object]] = {}
    summary: dict[str, object] | None = None
    for line in stdout.splitlines():
        if line.startswith(TASK_MARKER):
            payload = json.loads(line[len(TASK_MARKER) :])
            task_lines[str(payload.get("task"))] = payload
        elif line.startswith(SUMMARY_MARKER):
            summary = json.loads(line[len(SUMMARY_MARKER) :])

    missing = [name for name in EXPECTED_TASKS if name not in task_lines]
    unstructured = [
        name
        for name in EXPECTED_TASKS
        if name in task_lines and not task_lines[name].get("structured")
    ]

    print("=" * 70)
    print("cccp isolation verification summary")
    print(f"  child exit code          : {proc.returncode}")
    print(f"  tasks reported           : {len(task_lines)}/{len(EXPECTED_TASKS)}")
    if missing:
        print(f"  MISSING tasks            : {', '.join(missing)}")
    if unstructured:
        print(f"  UNSTRUCTURED tasks       : {', '.join(unstructured)}")
    if summary is not None:
        leaked = summary.get("leaked_acp_modules") or []
        print(f"  leaked acp modules       : {leaked or 'none'}")
    print("=" * 70)

    ok = (
        proc.returncode == 0
        and not missing
        and not unstructured
        and summary is not None
        and bool(summary.get("ok"))
    )
    if ok:
        print("RESULT: PASS — all 14 cccp tasks returned structured, ACP-free results.")
        return 0
    print("RESULT: FAIL — see the child output above for details.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
