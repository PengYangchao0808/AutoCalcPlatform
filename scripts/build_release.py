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
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("cccp", "worker", "web")
PACKAGES = {"cccp": "cccp", "worker": "acp_services", "web": "acp"}
ARTIFACT_PREFIXES = {
    "cccp": ("cccp-",),
    "worker": ("acp_worker-", "acp-worker-"),
    "web": ("acp_web-", "acp-web-"),
}
SCHEMA = "acp2.release.manifest.v1"

#: Python dependency classification for the separated release roles.
#: DSH is a Node/pnpm workspace and is intentionally not a Python dependency;
#: browser tooling stays in the root `browser` extra and never enters `cccp`.
SCIENCE_NODE = {
    "cccp",
    "numpy",
    "rdkit",
    "pyyaml",
    "scipy",
    "matplotlib",
    "openpyxl",
    "typing-extensions",
    "nmrglue",
    "openbabel",
    "qml",
}
CONTROL = {
    "acp-worker",
    "fastapi",
    "uvicorn",
    "python-multipart",
    "zipstream-ng",
    "paramiko",
}
DSH: set[str] = set()
BROWSER = {"playwright"}

REQUIRED_SDIST_FILES = ("VERSION", "requirements-role.txt", "MANIFEST.in", "LICENSE")


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


def _declared_versions(source: Path) -> dict[str, str]:
    """Read all four version locations and refuse a partial bump."""
    versions: dict[str, str] = {}
    for relpath in ("src/cccp/__init__.py", "src/cccp/version.py"):
        tree = ast.parse((source / relpath).read_text(encoding="utf-8"))
        found = [
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
        ]
        if not found:
            raise ValueError(f"{relpath} must declare __version__")
        versions[relpath] = found[0]
    acp_tree = ast.parse((source / "src/acp/__init__.py").read_text(encoding="utf-8"))
    acp_literals = [
        ast.literal_eval(node.value)
        for node in ast.walk(acp_tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if not acp_literals:
        raise ValueError("src/acp/__init__.py must declare a literal fallback __version__")
    versions["src/acp/__init__.py"] = acp_literals[-1]
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - build host Python is 3.11+
        import tomli as tomllib  # type: ignore[no-redef]
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    versions["pyproject.toml"] = project["project"]["version"]
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise ValueError(f"version locations disagree: {versions}")
    return versions


def dependency_class(name: str) -> str:
    normalized = name.lower().replace("_", "-")
    if normalized in SCIENCE_NODE:
        return "science-node"
    if normalized in CONTROL:
        return "control"
    if normalized in DSH:
        return "DSH"
    if normalized in BROWSER:
        return "browser"
    return "unclassified"


def _requirement_names(specs: list[str]) -> list[str]:
    names = []
    for spec in specs:
        token = spec.split(";", 1)[0].strip()
        name = token.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].split("<", 1)[0]
        if name:
            names.append(name.strip())
    return names


def _role_dependencies(requirements: Path, role: str) -> list[str]:
    lines = [
        line.split("#", 1)[0].strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
    ]
    return [line for line in lines if line]


def _role_declared_dependencies(source: Path, role: str, staged: list[str]) -> list[str]:
    if role != "cccp":
        return staged
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - build host Python is 3.11+
        import tomli as tomllib  # type: ignore[no-redef]
    config = tomllib.loads(
        (source / f"packaging/roles/{role}/pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = list(config["project"]["dependencies"])
    for extra in config["project"].get("optional-dependencies", {}).values():
        dependencies.extend(extra)
    return dependencies


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wheel_facts(path: Path, role: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    payload = [name for name in names if not name.endswith("/")]
    assets = [name for name in payload if name.startswith("cccp/analysis/nmr/model_assets/")]
    web_assets = [name for name in payload if name.startswith("acp_web_assets/")]
    return {
        "name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "entries": len(names),
        "files": len(payload),
        "top_level": sorted({name.split("/", 1)[0] for name in payload}),
        "licenses": [name for name in payload if "LICENSE" in name.upper()],
        "notices": [name for name in payload if "NOTICE" in name.upper()],
        "py_typed": any(name.endswith("/py.typed") for name in payload),
        "model_asset_files": len(assets),
        "model_asset_names": sorted(Path(name).name for name in assets),
        "web_asset_files": len(web_assets),
    }


def _sdist_facts(path: Path) -> dict:
    with tarfile.open(path) as archive:
        names = archive.getnames()
    files = [name for name in names if not name.endswith("/")]
    found = {Path(name).name for name in files}
    return {
        "name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "entries": len(names),
        "files": len(files),
        "required_present": sorted(found & set(REQUIRED_SDIST_FILES)),
        "required_missing": sorted(set(REQUIRED_SDIST_FILES) - found),
    }


def _git_state(source: Path) -> dict:
    probe = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return {"git": False}
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return {"git": True, "revision": head, "tree": tree, "clean": not status, "dirty_paths": status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--role", choices=(*ROLES, "all"), default="all")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="build from a dirty checkout (evidence only; the release build must be clean)",
    )
    args = parser.parse_args(argv)
    source = args.source.resolve()
    output = args.out.resolve()
    versions = _declared_versions(source)
    git_state = _git_state(source)
    if not args.allow_dirty and not git_state.get("clean", False):
        parser.error(f"--source must be a clean committed checkout: {git_state.get('dirty_paths')}")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--out must be empty so stale artifacts cannot enter the release manifest")
    manifest = {
        "schema": SCHEMA,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": release_version(source),
        "versions": versions,
        "source": {"path": str(source), **git_state},
        "dependency_classes": {
            "science-node": sorted(SCIENCE_NODE),
            "control": sorted(CONTROL),
            "DSH": sorted(DSH),
            "browser": sorted(BROWSER),
        },
        "roles": {},
    }
    for role in ROLES if args.role == "all" else (args.role,):
        with tempfile.TemporaryDirectory(prefix=f"acp-build-{role}-") as directory:
            stage = Path(directory)
            stage_role(source, stage, role)
            hashes = {
                path.relative_to(stage).as_posix(): _sha256(path)
                for path in sorted(stage.rglob("*"))
                if path.is_file()
            }
            staged_dependencies = _role_dependencies(stage / "requirements-role.txt", role)
            dependencies = _role_declared_dependencies(source, role, staged_dependencies)
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
            prefixes = ARTIFACT_PREFIXES[role]
            wheel = next(
                (path for path in output.glob("*.whl") if path.name.startswith(prefixes)),
                None,
            )
            sdist = next(
                (path for path in output.glob("*.tar.gz") if path.name.startswith(prefixes)),
                None,
            )
            if wheel is None or sdist is None:
                raise RuntimeError(f"missing artifacts for role {role}")
            names = _requirement_names(dependencies)
            manifest["roles"][role] = {
                "source_files": hashes,
                "dependencies": dependencies,
                "dependency_classes": {name: dependency_class(name) for name in names},
                "wheel": _wheel_facts(wheel, role),
                "sdist": _sdist_facts(sdist),
            }
    manifest["artifacts"] = {
        path.name: _sha256(path)
        for path in sorted(output.iterdir())
        if path.suffix in (".whl", ".gz")
    }
    (output / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
