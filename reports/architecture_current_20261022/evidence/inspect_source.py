"""Reproducible, read-only source inventory for the architecture report."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ("cccp", "acp_services", "acp")


def main() -> None:
    inventory = {}
    imports = []
    hashes = {}
    for package in PACKAGES:
        files = sorted((ROOT / "src" / package).rglob("*.py"))
        subpackages = Counter()
        lines = 0
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            lines += len(source.splitlines())
            within = path.relative_to(ROOT / "src" / package)
            subpackages[within.parts[0] if len(within.parts) > 1 else "(root)"] += 1
            for node in ast.walk(ast.parse(source)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                for name in names:
                    target = name.split(".")[0]
                    if target in PACKAGES and target != package:
                        imports.append({"from": package, "to": target, "path": relative,
                                        "line": node.lineno, "module": name})
        inventory[package] = {"python_files": len(files), "lines_including_comments": lines,
                              "subpackages": dict(sorted(subpackages.items()))}
    for path in [ROOT / "pyproject.toml", ROOT / "scripts/build_release.py",
                 *sorted((ROOT / "packaging").rglob("*"))]:
        if path.is_file():
            hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    matrix = {f"{a} -> {b}": sum(row["from"] == a and row["to"] == b for row in imports)
              for a in PACKAGES for b in PACKAGES if a != b}
    result = {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "scope": "working tree including uncommitted files; absolute AST import edges including TYPE_CHECKING and lazy imports",
              "inventory": inventory, "import_reference_counts": matrix,
              "cross_package_imports": imports, "source_sha256": hashes}
    destination = Path(__file__).with_name("source-inventory.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"inventory": inventory, "import_reference_counts": matrix}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
