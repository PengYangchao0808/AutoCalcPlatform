#!/usr/bin/env python3
"""Exercise seven ordinary CLI dispatches at a controlled scientific boundary.

This driver is loaded from the harness revision, while every product import
comes from ``--worktree`` in a fresh interpreter. It calls the real
``acp.cli.main(['run', workflow, ...])`` twice, with complete legal inputs and
controlled completed/failed scientific results. It does not verify real QC,
the scheduler subprocess lifecycle, or scheduler publication.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

LEGACY_WORKFLOWS = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "XtbConstrainedOpt",
    "tsmode",
    "nmr",
)
SCOPE = (
    "ordinary CLI parser/handler/request dispatch + controlled science boundary; "
    "no real QC or scheduler background publication"
)
WATER_XYZ = "3\nwater minimum\nO 0 0 0\nH 0.7586 0 0.5043\nH -0.7586 0 0.5043\n"
SPECTRUM = "H: 4.8(H2), 4.9(H3)\n"


class PrerequisiteMissingError(RuntimeError):
    """The target revision cannot supply a legal fixture/boundary for this check."""


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return _safe(
            {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
        )
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key == "progress_reporter":
                continue
            if key == "config" and isinstance(item, Mapping):
                # Preserve the effective requested resources without copying
                # unrelated machine/user executable configuration into evidence.
                result[key] = {"resources": _safe(item.get("resources", {}))}
            else:
                result[str(key)] = _safe(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return {"type": type(value).__name__}


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _fixtures(worktree: Path, root: Path, workflow: str) -> dict[str, Any]:
    source = _write(root / "inputs" / "water.xyz", WATER_XYZ)
    fixtures: dict[str, Any] = {"xyz": source}
    if workflow == "nmr":
        fixtures["spectrum"] = _write(root / "inputs" / "spectrum.txt", SPECTRUM)
    if workflow == "XtbPathSearch":
        fixtures["payload"] = {
            "schema_version": "pes2ts_xtb_path_request_v1",
            "reaction_id": "acceptance-path",
            "source": {
                "source_type": "xyz_text_pair",
                "start_xyz": WATER_XYZ,
                "end_xyz": WATER_XYZ.replace("0.7586", "0.8586"),
                "charge": 0,
                "multiplicity": 1,
            },
            "recipe": {
                "path_inp_text": "$path\n nrun=1\n npoint=5\n anopt=3\n$end\n",
                "gfn_level": 2,
                "uhf": 0,
                "threads": 2,
                "timeout_seconds": 60,
                "seed": 7,
                "extra_args": [],
            },
            "provenance": {"adapter_version": "ordinary-cli-acceptance"},
        }
    if workflow == "XtbConstrainedOpt":
        fixtures["payload"] = {
            "schema_version": "pes2ts_xtb_constrained_opt_request_v1",
            "reaction_id": "acceptance-constraint",
            "source": {
                "geometry": [[0, 0, 0], [0.7586, 0, 0.5043], [-0.7586, 0, 0.5043]],
                "elements": ["O", "H", "H"],
                "charge": 0,
                "multiplicity": 1,
            },
            "recipe": {
                "gfn_level": 2,
                "constraints": [
                    {"kind": "distance", "atoms": [0, 1], "target": 1.0, "unit": "angstrom"}
                ],
                "force_constant": 10.0,
                "opt_level": "normal",
                "max_cycle": 20,
                "physical_sp": False,
                "threads": 2,
                "timeout_seconds": 60,
            },
            "provenance": {"point_id": "acceptance-point"},
        }
    if "payload" in fixtures:
        fixtures["request_file"] = _write(
            root / "inputs" / "request.json", json.dumps(fixtures["payload"], indent=2)
        )
    if workflow == "tsmode":
        source_dir = worktree / "tests" / "fixtures" / "qc" / "orca61"
        for suffix in ("out", "hess", "xyz"):
            original = source_dir / f"ts_opt.{suffix}"
            if not original.is_file():
                raise PrerequisiteMissingError(f"frequency-source fixture missing: {original}")
            shutil.copyfile(original, root / "inputs" / original.name)
        fixtures["bundle"] = _write(
            root / "inputs" / "bundle.json",
            json.dumps(
                {
                    "schema_version": "tsmode_bundle_v1",
                    "files": {
                        "output": "ts_opt.out",
                        "hessian": "ts_opt.hess",
                        "geometry": "ts_opt.xyz",
                    },
                    "charge": 0,
                    "multiplicity": 1,
                    "origin": {"kind": "files"},
                }
            ),
        )
    return fixtures


def _argv(workflow: str, fixture: dict[str, Any], output: Path) -> list[str]:
    common = ["--output", str(output), "--nproc", "2", "--mem", "2GB"]
    specifics = {
        "Confsearch": [
            "--input",
            str(fixture["xyz"]),
            "--protocol",
            "xtb-crest",
            "--profile",
            "light",
            "--refinement-policy",
            "screen",
            "--charge",
            "0",
            "--multiplicity",
            "1",
            "--temperature",
            "310",
        ],
        "PESsearch": [
            "--input",
            str(fixture["xyz"]),
            "--coordinate",
            "0,1,0.8,1.2",
            "--points",
            "5",
            "--charge",
            "0",
            "--multiplicity",
            "1",
        ],
        "BatchOptimize": [
            "--items-file",
            str(fixture["xyz"]),
            "--profile",
            "opt_only",
            "--method",
            "r2SCAN-3c",
            "--opt-max-iter",
            "9",
            "--charge",
            "0",
            "--multiplicity",
            "1",
        ],
        "XtbPathSearch": ["--path-config", str(fixture.get("request_file", ""))],
        "XtbConstrainedOpt": ["--opt-config", str(fixture.get("request_file", ""))],
        "tsmode": [
            "--source-bundle",
            str(fixture.get("bundle", "")),
            "--source-mode-index",
            "6",
            "--max-steps",
            "9",
            "--recalc-hess",
            "0",
            "--trust-radius",
            "0.2",
            "--allow-unverified-mapping",
        ],
        "nmr": [
            "--input",
            str(fixture["xyz"]),
            "--spectrum",
            str(fixture.get("spectrum", "")),
            "--nuclei",
            "1H",
            "--nmr-method",
            "mPW1PW91",
            "--nmr-basis",
            "6-311G(d)",
            "--max-conformers",
            "2",
            "--boltzmann-temp",
            "310",
            "--charge",
            "0",
            "--multiplicity",
            "1",
        ],
    }
    return ["run", workflow, *specifics[workflow], *common]


def _result(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        error="controlled scientific failure" if status == "failed" else None,
        metadata={},
        conformers=[],
        manifest_path=None,
        quality_gates={},
        protocol="xtb-crest",
        refinement_policy="screen",
    )


def _boundary(workflow: str, fixture: dict[str, Any], status: str, received: list[Any]) -> Any:
    """Patch only the science workflow call, preserving parser and handler code."""
    if workflow == "Confsearch":
        module = importlib.import_module("acp.confsearch")

        def run(engine: Any, request: Any, **kwargs: Any) -> Any:
            received.append({"workflow": workflow, "request": _safe(request)})
            assert Path(request.input_source).read_text() == WATER_XYZ
            assert (request.protocol, request.profile, request.refinement_policy) == (
                "xtb-crest",
                "light",
                "screen",
            )
            assert request.nproc == 2 and request.charge == 0 and request.multiplicity == 1
            assert request.temperature == 310
            return _result(status)

        return patch.object(module.ConfsearchEngine, "run", run)
    if workflow == "tsmode":
        module = importlib.import_module("acp.workflows.tsmode")

        def run(engine: Any, request: Any, bundle: Any, task_root: Any, **kwargs: Any) -> Any:
            received.append(
                {
                    "workflow": workflow,
                    "request": _safe(request),
                    "bundle": {
                        "n_atoms": bundle.n_atoms,
                        "elements": bundle.elements,
                        "hessian_sha256": bundle.hessian_sha256,
                    },
                    "task_root": str(task_root),
                }
            )
            assert request.source_mode_index == 6
            assert request.optimization.max_iterations == 9
            assert request.optimization.recalc_hess == 0
            assert request.resources == {"nproc": 2, "mem": "2GB"}
            assert bundle.mode_by_index(6).is_imaginary
            assert (
                bundle.hessian_sha256
                == hashlib.sha256(
                    (fixture["bundle"].parent / "ts_opt.hess").read_bytes()
                ).hexdigest()
            )
            return SimpleNamespace(workflow_result=_result(status))

        return patch.object(module.TsmodeEngine, "run", run)
    module_name, function = {
        "PESsearch": ("acp.workflows.pes_search", "run_pes_search"),
        "BatchOptimize": ("acp.workflows.batch_optimize", "run_batch_optimize"),
        "XtbPathSearch": ("acp.workflows.xtb_path", "run_xtb_path_search"),
        "XtbConstrainedOpt": ("acp.workflows.xtb_constrained_opt", "run_xtb_constrained_opt"),
        "nmr": ("acp.workflows.nmr", "run_nmr_analysis"),
    }[workflow]
    module = importlib.import_module(module_name)

    def run(*args: Any, **kwargs: Any) -> Any:
        received.append({"workflow": workflow, "args": _safe(args), "kwargs": _safe(kwargs)})
        if workflow == "PESsearch":
            assert Path(kwargs["input_xyz"]).read_text() == WATER_XYZ
            coordinate = kwargs["coordinate"]
            assert coordinate.atoms == (0, 1) and coordinate.n_points == 5
            assert coordinate.start == 0.8 and coordinate.end == 1.2
        elif workflow == "BatchOptimize":
            assert Path(args[0]).read_text() == WATER_XYZ
            assert kwargs["profile"] == "opt_only"
            assert kwargs["methods"].optimization_method == "r2SCAN-3c", (
                "ordinary --method was not forwarded into typed BatchMethodOptions"
            )
            assert kwargs["methods"].opt_max_iter == 9
            models = importlib.import_module("acp.calculations.batch.models")
            assert len(models.load_items_from_xyz_file(args[0])) == 1
        elif workflow == "XtbPathSearch":
            assert args[0] == fixture["payload"]
            assert module._validate_path_request(args[0]).threads == 2
        elif workflow == "XtbConstrainedOpt":
            assert args[0] == fixture["payload"]
            assert module.validate_request(args[0]).physical_sp is False
        elif workflow == "nmr":
            assert kwargs["input_sources"] == [str(fixture["xyz"])]
            assert Path(kwargs["input_sources"][0]).read_text() == WATER_XYZ
            assert Path(kwargs["spectrum"]).read_text() == SPECTRUM
            assert kwargs["nuclei"] == ["1H"] and kwargs["max_conformers"] == 2
            assert kwargs["boltzmann_temp"] == 310
            assert kwargs["nmr_method"].lower() == "mpw1pw91"
            assert kwargs["nmr_basis"] == "6-311G(d)"
        assert kwargs["config"]["resources"]["nproc"] == 2
        return _result(status)

    return patch.object(module, function, run)


def _import_failure(exc: ImportError) -> str:
    """Only an identified missing third-party dependency is unverified."""
    name = getattr(exc, "name", None)
    if name and name.split(".")[0] not in {"acp", "cccp", "acp_services"}:
        return "NOT_VERIFIED"
    return "FAIL"


def _child(worktree: Path, root: Path, workflow: str) -> dict[str, Any]:
    sys.path.insert(0, str(worktree / "src"))
    os.environ["ACP_RUN_ROOT"] = str(root)
    row: dict[str, Any] = {
        "workflow": workflow,
        "scope": SCOPE,
        "ok": False,
        "result": "FAIL",
        "cases": [],
    }
    try:
        cli = importlib.import_module("acp.cli")
        if not Path(cli.__file__).resolve().is_relative_to(worktree / "src"):
            raise AssertionError("CLI was imported from outside the target worktree")
        row["product_cli"] = str(Path(cli.__file__).resolve())
        row["product_cli_sha256"] = hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest()
        fixture = _fixtures(worktree, root, workflow)
        for status in ("completed", "failed"):
            received: list[Any] = []
            stdout, stderr = io.StringIO(), io.StringIO()
            argv = _argv(workflow, fixture, root / status)
            call_error = None
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    with _boundary(workflow, fixture, status, received):
                        exit_code = cli.main(argv)
                except SystemExit as exc:
                    exit_code = int(exc.code or 0)
                except (
                    AssertionError,
                    AttributeError,
                    KeyError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    exit_code = None
                    call_error = f"{type(exc).__name__}: {exc}"
            expected_exit = 0 if status == "completed" else 1
            row["cases"].append(
                {
                    "command": [sys.executable, "-m", "acp.cli", *argv],
                    "execution_entry": (
                        "acp.cli.main(argv), with the documented science-boundary patch"
                    ),
                    "controlled_scientific_status": status,
                    "exit_code": exit_code,
                    "received": received,
                    "stdout": stdout.getvalue(),
                    "stderr": stderr.getvalue(),
                    "error": call_error,
                    "ok": len(received) == 1 and exit_code == expected_exit and call_error is None,
                }
            )
        foreign = [
            name
            for name, module in sys.modules.items()
            if name.split(".")[0] in {"acp", "cccp", "acp_services"}
            and getattr(module, "__file__", None)
            and not Path(module.__file__).resolve().is_relative_to(worktree / "src")
        ]
        assert not foreign, f"product modules outside target worktree: {foreign}"
        row["ok"] = len(row["cases"]) == 2 and all(case["ok"] for case in row["cases"])
        row["result"] = "PASS" if row["ok"] else "FAIL"
        row["real_qc"] = "NOT_VERIFIED"
        row["scheduler_background_publication"] = "NOT_VERIFIED"
    except ImportError as exc:
        row.update(result=_import_failure(exc), reason=f"target import failed: {exc}")
    except PrerequisiteMissingError as exc:
        row.update(result="NOT_VERIFIED", reason=f"target fixture unavailable: {exc}")
    except (
        AssertionError,
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        row.update(result="FAIL", reason=f"{type(exc).__name__}: {exc}")
    return row


def run_legacy_cli_checks(
    worktree: Path,
    run_root: Path,
    workflows: Sequence[str] = LEGACY_WORKFLOWS,
    *,
    timeout: float = 90,
) -> list[dict[str, Any]]:
    """Run this harness helper against the supplied S0/S1 product tree."""
    worktree, run_root = worktree.resolve(), run_root.resolve()
    rows = []
    for workflow in workflows:
        if workflow not in LEGACY_WORKFLOWS:
            raise ValueError(f"not a contracted legacy workflow: {workflow}")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--worktree",
            str(worktree),
            "--workflow",
            workflow,
            "--run-root",
            str(run_root / workflow),
        ]
        env = dict(os.environ)
        env.update(PYTHONPATH=str(worktree / "src"), PYTHONDONTWRITEBYTECODE="1")
        try:
            result = subprocess.run(
                command,
                cwd=worktree,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            row = json.loads(result.stdout)
            row["driver_command"] = command
            row["driver_exit_code"] = result.returncode
            if result.returncode != 0:
                row.update(result="FAIL", ok=False, reason="ordinary CLI helper failed")
        except OSError as exc:
            row = {
                "workflow": workflow,
                "scope": SCOPE,
                "result": "NOT_VERIFIED",
                "ok": False,
                "reason": f"driver could not complete: {exc}",
                "driver_command": command,
            }
        except subprocess.TimeoutExpired as exc:
            row = {
                "workflow": workflow,
                "scope": SCOPE,
                "result": "FAIL",
                "ok": False,
                "reason": f"executed CLI dispatch timed out: {exc}",
                "driver_command": command,
            }
        except json.JSONDecodeError as exc:
            row = {
                "workflow": workflow,
                "scope": SCOPE,
                "result": "FAIL",
                "ok": False,
                "reason": f"executed CLI driver returned invalid evidence: {exc}",
                "driver_command": command,
                "driver_exit_code": result.returncode,
                "driver_stdout": result.stdout,
                "driver_stderr": result.stderr,
            }
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--workflow", choices=LEGACY_WORKFLOWS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        if args.workflow is None:
            parser.error("--child requires --workflow")
        print(json.dumps(_child(args.worktree.resolve(), args.run_root.resolve(), args.workflow)))
        return 0
    rows = run_legacy_cli_checks(
        args.worktree, args.run_root, [args.workflow] if args.workflow else LEGACY_WORKFLOWS
    )
    state = (
        "FAIL"
        if any(row["result"] == "FAIL" for row in rows)
        else ("NOT_VERIFIED" if any(row["result"] == "NOT_VERIFIED" for row in rows) else "PASS")
    )
    print(json.dumps({"scope": SCOPE, "result": state, "checks": rows}, indent=2))
    return {"PASS": 0, "FAIL": 1, "NOT_VERIFIED": 2}[state]


if __name__ == "__main__":
    raise SystemExit(main())
