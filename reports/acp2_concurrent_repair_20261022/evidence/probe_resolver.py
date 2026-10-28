"""Reproduce explicit-pin fallback without running a quantum-chemistry binary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def inspect(root: Path, index: int) -> dict[str, object]:
    path = root / "src/cccp/software.py"
    name = f"resolver_readonly_probe_{index}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    rows = []
    with tempfile.TemporaryDirectory(prefix="acp2-resolver-probe-") as directory:
        scratch = Path(directory)
        replacement = scratch / "path-bin" / "xtb"
        replacement.parent.mkdir()
        replacement.write_text("#!/bin/sh\nexit 99\n")
        replacement.chmod(0o700)
        non_executable = scratch / "non-executable-xtb"
        non_executable.write_text("not executable\n")
        non_executable.chmod(0o600)
        cases = [
            ("missing_absolute_pin", scratch / "missing-xtb", False),
            ("directory_pin", scratch, False),
            ("non_executable_pin", non_executable, False),
            ("bare_name_discovery", "xtb", True),
            ("valid_absolute_pin", replacement, True),
        ]
        with (
            patch.dict(os.environ, {"PATH": str(replacement.parent)}, clear=True),
            patch.object(module.shutil, "which", return_value=str(replacement)),
            patch.object(module.subprocess, "run", side_effect=AssertionError("must not execute")) as launch,
        ):
            for case, configured, should_resolve in cases:
                resolved, source = module.resolve_executable_with_source("xtb", configured)
                rows.append({"case": case, "source": source,
                             "resolved_to_path_fixture": resolved == replacement.resolve(),
                             "target_should_resolve": should_resolve,
                             "matches_target_contract": (resolved is not None) == should_resolve})
            assert launch.call_count == 0
    return {"source_root": str(root), "software_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scope": "resolver behavior only; dummy binary never executed", "cases": rows}


def main() -> None:
    roots = [Path("/ACPServer/AutoCalcPlatform"), Path("/ACPServer/acp2-development/A")]
    result = {"purpose": "planning evidence, not fix acceptance", "observations": [
        inspect(root, index) for index, root in enumerate(roots)
    ]}
    Path(__file__).with_name("resolver-observation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
