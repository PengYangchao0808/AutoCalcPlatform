#!/usr/bin/env python3
"""Build disjoint CCCP, worker and Web wheels/sdists in temporary staging."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROLES = ("cccp", "worker", "web")
PACKAGES = {"cccp": "cccp", "worker": "acp_services", "web": "acp"}


def release_version(source: Path) -> str:
    """Use the existing authoritative version without a fifth version constant."""
    tree = ast.parse((source / "src/cccp/version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError("cccp/version.py must declare a literal __version__")


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))


def stage_role(source: Path, stage: Path, role: str) -> None:
    """Materialize exactly one package owner plus its declared assets."""
    version = release_version(source)
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / f"packaging/roles/{role}/pyproject.toml", stage / "pyproject.toml")
    for name in ("README.md", "LICENSE"):
        shutil.copy2(source / name, stage / name)
    (stage / "VERSION").write_text(version + "\n", encoding="utf-8")
    (stage / "MANIFEST.in").write_text(
        "include VERSION requirements-role.txt\nrecursive-include src *\n", encoding="utf-8"
    )
    dependencies = []
    if role in ("worker", "web"):
        dependencies.append(f"cccp=={version}")
    if role == "web":
        dependencies.extend(
            [
                f"acp-worker=={version}",
                "fastapi>=0.115",
                "uvicorn>=0.30",
                "python-multipart>=0.0.9",
                "zipstream-ng>=1.9.0",
                "paramiko>=3.0",
            ]
        )
    (stage / "requirements-role.txt").write_text("\n".join(dependencies) + "\n", encoding="utf-8")
    package = PACKAGES[role]
    _copy_tree(source / "src" / package, stage / "src" / package)
    if role == "web":
        assets = stage / "src/acp_web_assets"
        _copy_tree(source / "frontend", assets)
        (assets / "__init__.py").write_text(
            '"""Packaged ACP Workbench assets."""\n', encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--role", choices=(*ROLES, "all"), default="all")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--out must be empty so stale artifacts cannot enter the release manifest")
    manifest = {"version": release_version(source), "roles": {}}
    for role in ROLES if args.role == "all" else (args.role,):
        with tempfile.TemporaryDirectory(prefix=f"acp-build-{role}-") as directory:
            stage = Path(directory)
            stage_role(source, stage, role)
            hashes = {
                path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(stage.rglob("*"))
                if path.is_file()
            }
            for kind in ("build_wheel", "build_sdist"):
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; from setuptools import build_meta as b; "
                        f"b.{kind}(sys.argv[1])",
                        str(output),
                    ],
                    cwd=stage,
                    check=True,
                )
            manifest["roles"][role] = {"source_files": hashes}
    manifest["artifacts"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.iterdir())
        if path.suffix in (".whl", ".gz")
    }
    (output / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
