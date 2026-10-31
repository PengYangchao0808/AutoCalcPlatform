"""Probe the installed CCCP wheel without importing the source tree."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--xtb", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    forbidden = ("acp", "acp_services", "fastapi", "uvicorn", "paramiko")
    assert all(importlib.util.find_spec(name) is None for name in forbidden)

    import cccp
    import cccp.calculation as calculation
    from cccp.analysis.nmr.probability import normalize_dp4
    from cccp.calculation import (
        MethodSpec, OptimizeOptions, StructureInput, TaskContext, TaskKind,
        TaskRequest, TaskResources, run_optimize,
    )
    from cccp.calculation.products.contracts import ProductKind
    from cccp.plugin.sdk import PLUGIN_SDK_VERSION

    installed_root = Path(cccp.__file__).resolve().parent
    assert installed_root.is_relative_to(Path(sys.prefix).resolve())
    checked = 0
    for source in sorted((args.source / "src/cccp").rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = source.relative_to(args.source / "src/cccp")
        installed = installed_root / relative
        # AGENTS.md is developer guidance, not installed library content.
        if source.name == "AGENTS.md":
            continue
        assert installed.is_file(), str(relative)
        assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(installed.read_bytes()).digest(), str(relative)
        checked += 1
    task_api = []
    for kind in TaskKind:
        name = f"run_{kind.value}"
        module = importlib.import_module(f"cccp.calculation.tasks.{kind.value}")
        assert callable(getattr(module, name))
        task_api.append({"task": kind.value, "module_callable": True,
                         "root_export": callable(getattr(calculation, name, None))})
    distribution = importlib.metadata.distribution("cccp")
    assert not distribution.entry_points
    no_cli = subprocess.run([sys.executable, "-I", "-m", "cccp"], capture_output=True, text=True)
    assert no_cli.returncode != 0 and "__main__" in no_cli.stderr
    dp4 = normalize_dp4([-1.0, -2.0])
    assert abs(sum(dp4) - 1.0) < 1e-12

    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    request = TaskRequest(
        task=TaskKind.OPTIMIZE,
        backend="xtb",
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74)), symbols=("H", "H"),
        ),
        level=MethodSpec(method="GFN2-xTB"),
        options=OptimizeOptions(max_cycles=30),
        resources=TaskResources(nproc=1, timeout_s=60),
        output_dir=work,
    )
    context = TaskContext(
        config={"executables": {"xtb": {"path": str(args.xtb.resolve())}}},
        workdir=work, input_base=work, timeout_s=60,
    )
    result = run_optimize(request, context=context)
    assert result.status == "completed" and result.complete, result.to_dict()
    assert result.payload is not None and result.payload.optimization_status == "converged"
    assert result.energy_hartree is not None
    assert result.coordinates is not None and len(result.coordinates) == 2
    assert result.artifacts
    for artifact in result.artifacts:
        path = Path(artifact.path).resolve()
        assert path.is_relative_to(work) and path.is_file(), str(path)
    leaked = [name for name in sys.modules if name.split(".")[0] in forbidden]
    assert not leaked, leaked
    assert not list(work.rglob("*.db"))
    facts = {
        "result": "PASS", "python": sys.executable, "cccp_version": cccp.__version__,
        "installed_module": cccp.__file__, "source_files_verified": checked,
        "forbidden_absent": list(forbidden), "sys_path": sys.path,
        "requirements": distribution.requires, "task_api": task_api,
        "console_entrypoints": [], "python_m_cccp_error": no_cli.stderr.strip(),
        "dp4_normalization": dp4, "product_kinds": [item.value for item in ProductKind],
        "plugin_sdk_version": PLUGIN_SDK_VERSION,
        "real_xtb_result": result.to_dict(), "leaked_modules": leaked,
    }
    args.output.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"result": "PASS", "energy_hartree": result.energy_hartree,
                      "source_files_verified": checked, "missing_root_exports":
                      [row["task"] for row in task_api if not row["root_export"]]}))


if __name__ == "__main__":
    main()
