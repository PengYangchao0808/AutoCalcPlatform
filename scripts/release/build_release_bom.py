#!/usr/bin/env python3
"""Build every ACP 2.0 release artifact into the durable store and write the BOM.

M5-01 owns the single bill of materials: the three Python roles plus the root
``acp`` distribution, the RPH tarball set *with its self-contained offline
install surface*, the packaged Workbench static assets, the third-party plugin
wheels M4-05 exercised, the contract corpus, the NMR model assets and the
release documents. Every component is copied into ``--store`` and referenced
from ``bom.current.json`` by URI + sha256 + size, so a clean host can install
from the store alone (``scripts/acceptance/acp2_m501_install.py``).

Nothing here runs QC or a model: scientific software is only version-probed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
for candidate in (str(SCRIPTS), str(REPO / "src")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import build_release  # noqa: E402  (sibling build tool: version + git facts)
import schema_validate  # noqa: E402  (scripts/release/schema_validate.py)

BOM_SCHEMA = "acp2.release.bom.v1"
RECEIPT_SCHEMA = "acp2.m5-01.build-receipt.v1"
CONTRACTS = REPO / "docs" / "reference" / "next-change" / "acp2-contracts"
EXPERIENCE = CONTRACTS / "experience"
BOM_SCHEMA_PATH = CONTRACTS / "release" / "bom.schema.json"
BOM_OUT = CONTRACTS / "release" / "bom.current.json"
STATIC_ASSETS_OUT = EXPERIENCE / "static-assets.manifest.json"
EXPERIENCE_MANIFEST_OUT = EXPERIENCE / "release-manifest.json"
COVERAGE = CONTRACTS / "release" / "evidence-coverage.current.json"
CONTRACT_MANIFEST = CONTRACTS / "manifest.json"
DSH_COMPATIBILITY = EXPERIENCE / "dsh-compatibility.json"
RPH = REPO / "rph"
RPH_DIST = RPH / "dist-packages"

PLUGIN_FIXTURES = (
    ("external", REPO / "tests/fixtures/acp2/science/external_plugin"),
    ("upgrade-v1", REPO / "tests/fixtures/acp2/science/plugin_upgrade/v1"),
    ("upgrade-v2", REPO / "tests/fixtures/acp2/science/plugin_upgrade/v2"),
)
SCIENCE_ASSETS = REPO / "src/cccp/analysis/nmr/model_assets"
CRITICAL_SCIENCE_ASSETS = ("NOTICE.md", "LICENSE-DP5", "atomic_reps.gz", "frag_reps.gz")
RPH_SURFACE_FILES = (
    "rph-install.package.json",
    "package.json",
    "pnpm-workspace.yaml",
    "pnpm-lock.yaml",
    "rph-packages.json",
    "SHA256SUMS",
    "README.md",
    "verify-install.mjs",
)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str], *, cwd: Path, log: Path | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, env=_clean_env(), check=False
    )
    if log is not None:
        log.write_text(
            "$ "
            + " ".join(argv)
            + "\n\n"
            + (completed.stdout or "")
            + ("\n[stderr]\n" + completed.stderr if completed.stderr else "")
            + f"\n[exit {completed.returncode}]\n",
            encoding="utf-8",
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{completed.stderr[-4000:]}"
        )
    return completed


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    return env


def _probe(argv: list[str], *, cwd: Path) -> str:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def _version_line(text: str, *patterns: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is not None:
            return match.group(1).strip()
    return None


def _tar_bundle(target: Path, members: list[tuple[Path, str]]) -> Path:
    """Deterministic .tar.gz (sorted members, zeroed metadata, gzip mtime 0)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(members, key=lambda item: item[1])
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", mtime=0, fileobj=raw) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                for path, arcname in ordered:
                    info = archive.gettarinfo(str(path), arcname=arcname)
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
    return target


def _tree_members(root: Path, prefix: str) -> list[tuple[Path, str]]:
    return [
        (path, f"{prefix}/{path.relative_to(root).as_posix()}")
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]


def _artifact(
    *,
    artifact_id: str,
    kind: str,
    name: str,
    version: str,
    path: Path,
    built_at: str,
    build_script: Path,
    notes: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"artifact disappeared before the BOM was written: {path}")
    return {
        "id": artifact_id,
        "kind": kind,
        "name": name,
        "version": version,
        "uri": str(path),
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
        "retrievable": True,
        "built_at": built_at,
        "build_script_sha256": sha256(build_script),
        "notes": notes,
    }


def _copy_file(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------


def build_python(
    source: Path, store: Path, log_dir: Path, *, clean: bool, build_python: Path
) -> dict[str, Any]:
    """Three disjoint roles plus the mutually exclusive root distribution."""
    python_dir = store / "python"
    roles_dir = python_dir / "roles"
    root_dir = python_dir / "root"
    for directory in (roles_dir, root_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    allow = [] if clean else ["--allow-dirty"]
    facts: dict[str, Any] = {}
    for role, output in (("all", roles_dir), ("root", root_dir)):
        _run(
            [
                str(build_python),
                str(SCRIPTS / "build_release.py"),
                "--source",
                str(source),
                "--out",
                str(output),
                "--role",
                role,
                *allow,
            ],
            cwd=source,
            log=log_dir / f"build-python-{role}.log",
        )
        manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
        facts[role] = {
            "directory": str(output),
            "roles": sorted(manifest["roles"]),
            "artifacts": manifest["artifacts"],
            "manifest_sha256": sha256(output / "release-manifest.json"),
            "source_clean": manifest["source"].get("clean"),
        }
    return facts


def ensure_build_python(store: Path, log_dir: Path) -> Path:
    """A python that can run ``setuptools.build_meta.build_wheel``."""
    probe = subprocess.run(
        [sys.executable, "-c", "import setuptools, wheel"],
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return Path(sys.executable)
    venv = store / "tooling-build-venv"
    python = venv / "bin" / "python"
    if not python.is_file():
        _run([sys.executable, "-m", "venv", str(venv)], cwd=store, log=log_dir / "build-venv.log")
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "setuptools>=65",
                "wheel",
            ],
            cwd=store,
            log=log_dir / "build-venv-install.log",
        )
    probe = subprocess.run(
        [str(python), "-c", "import setuptools, wheel"], capture_output=True, check=False
    )
    if probe.returncode != 0:
        raise RuntimeError(f"could not provision a build python in {venv}")
    return python


def build_plugins(store: Path, log_dir: Path, build_python: Path) -> dict[str, Any]:
    out = store / "plugins"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    facts: dict[str, Any] = {}
    for label, fixture in PLUGIN_FIXTURES:
        before = {path.name for path in out.glob("*.whl")}
        # The fixtures ship their own standard-library-only PEP 517 backend
        # (``build-system.requires = []``), so no ``build`` frontend is needed.
        _run(
            [
                str(build_python),
                "-c",
                "import sys; from build_backend import build_wheel; "
                "print('built ' + build_wheel(sys.argv[1]))",
                str(out),
            ],
            cwd=fixture,
            log=log_dir / f"build-plugin-{label}.log",
        )
        produced = sorted(path for path in out.glob("*.whl") if path.name not in before)
        if len(produced) != 1:
            raise RuntimeError(f"plugin fixture {label} produced {[p.name for p in produced]}")
        facts[label] = {"fixture": str(fixture), "wheel": produced[0].name}
    return facts


def package_rph(store: Path, log_dir: Path, *, repackage: bool) -> dict[str, Any]:
    """Regenerate (optionally) and copy the RPH tarball set + install surface."""
    surface = store / "rph" / "install-surface"
    if surface.exists():
        shutil.rmtree(surface)
    if repackage:
        _run(
            ["pnpm", "--dir", str(RPH), "package"],
            cwd=REPO,
            log=log_dir / "rph-package.log",
        )
    if not RPH_DIST.is_dir():
        raise RuntimeError(f"{RPH_DIST} is missing; run `pnpm --dir rph package` first")
    surface.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RPH_DIST, surface)
    missing = [name for name in RPH_SURFACE_FILES if not (surface / name).is_file()]
    if missing:
        raise RuntimeError(f"RPH install surface is missing {missing}")
    checksums: dict[str, str] = {}
    for line in (surface / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    shipped = {
        path.name for path in surface.iterdir() if path.is_file() and path.name != "SHA256SUMS"
    }
    if shipped != set(checksums):
        raise RuntimeError(
            f"SHA256SUMS does not cover the surface: {sorted(shipped ^ set(checksums))}"
        )
    for name, digest in checksums.items():
        if sha256(surface / name) != digest:
            raise RuntimeError(f"SHA256SUMS mismatch for {name}")
    index = json.loads((surface / "rph-packages.json").read_text(encoding="utf-8"))
    return {
        "directory": str(surface),
        "repackage": repackage,
        "index_schema": index["schema"],
        "packages": index["packages"],
        "checksums": checksums,
    }


def build_static_assets(
    store: Path, web_wheel: Path, *, version: str
) -> tuple[dict[str, Any], Path]:
    """Rebuild the manifest from the *built web wheel* and compare the embedded one."""
    from acp.api import static_assets

    out = store / "static-assets"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="acp-web-assets-") as directory:
        target = Path(directory)
        with zipfile.ZipFile(web_wheel) as archive:
            names = [name for name in archive.namelist() if name.startswith("acp_web_assets/")]
            archive.extractall(target, members=names)
        assets = target / "acp_web_assets"
        manifest = static_assets.build_manifest(
            assets,
            release_id=build_release.static_release_id(version),
            package_version=version,
        )
        embedded_path = assets / static_assets.MANIFEST_NAME
        if not embedded_path.is_file():
            raise RuntimeError(f"{web_wheel.name} does not embed {static_assets.MANIFEST_NAME}")
        embedded = json.loads(embedded_path.read_text(encoding="utf-8"))
        if embedded != manifest:
            raise RuntimeError("embedded static-asset manifest differs from a fresh rebuild")
        digest = manifest["digest"]
        target_manifest = out / static_assets.MANIFEST_NAME
        target_manifest.write_text(static_assets.manifest_text(manifest), encoding="utf-8")
        verified = static_assets.verify_manifest(assets)
        facts = {
            "release_id": manifest["release_id"],
            "digest": digest,
            "files": manifest["counts"]["files"],
            "bytes": manifest["counts"]["bytes"],
            "entry_points": manifest["entry_points"],
            "embedded_matches_rebuild": True,
            "verified_from_wheel": verified["digest"] == digest,
            "web_wheel": web_wheel.name,
        }
    shutil.copy2(out / static_assets.MANIFEST_NAME, STATIC_ASSETS_OUT)
    return facts, target_manifest


def _scientific_software() -> list[dict[str, Any]]:
    """Record resolved binaries + versions; never launch a science run."""
    from cccp.software import resolve_executable_with_source

    probes = (
        ("ORCA", "orca", ("Program Version ([0-9][\\w.-]+)",)),
        ("xTB", "xtb", ("Version\\s+([0-9][\\w.-]+)",)),
        ("CREST", "crest", ("Version\\s+([0-9][\\w.-]+)",)),
        ("Shermo", "shermo", ("Version\\s+([0-9][\\w.-]+)",)),
    )
    entries: list[dict[str, Any]] = []
    for name, binary, patterns in probes:
        resolved, source = resolve_executable_with_source(binary)
        if resolved is None or not Path(resolved).is_file():
            entries.append(
                {
                    "name": name,
                    "version": "unavailable",
                    "path": binary,
                    "digest": "unavailable",
                    "verified": "NOT_VERIFIED",
                }
            )
            continue
        text = _probe([str(resolved), "--version"], cwd=REPO) or _probe(
            [str(resolved), "-h"], cwd=REPO
        )
        version = _version_line(text, *patterns)
        entries.append(
            {
                "name": name,
                "version": version or "unreported",
                "path": f"{resolved} (source={source})",
                "digest": f"sha256:{sha256(Path(resolved))}",
                "verified": "PASS" if version else "NOT_VERIFIED",
            }
        )
    return entries


def _chromium_facts() -> str | None:
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = [Path(cache)] if cache else [Path.home() / ".cache" / "ms-playwright"]
    for root in roots:
        if not root.is_dir():
            continue
        builds = sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and path.name.startswith("chromium-")
        )
        if builds:
            return f"{builds[-1]} (playwright browser cache: {root})"
    return None


def _toolchain() -> dict[str, Any]:
    compatibility = json.loads(DSH_COMPATIBILITY.read_text(encoding="utf-8"))
    python_version = platform.python_version()
    node = _version_line(_probe(["node", "--version"], cwd=REPO), r"(v?[0-9][0-9.]+)") or "absent"
    pnpm = _version_line(_probe(["pnpm", "--version"], cwd=REPO), r"([0-9][0-9.]+)") or "absent"
    ruff = _version_line(
        _probe([sys.executable, "-m", "ruff", "--version"], cwd=REPO), r"([0-9.]+)"
    ) or ("absent")
    dsh = compatibility["dsh"]
    mcp_client = compatibility["official_mcp_client"]
    return {
        "python": python_version,
        "node": node.removeprefix("v"),
        "pnpm": pnpm,
        "ruff": ruff,
        "chromium": _chromium_facts(),
        "dsh": {
            "name": "@deepseek-ai/dsh-mcp-client",
            "version": str(mcp_client["version"]),
            "lock_sha256": dsh["lockfile"]["sha256"],
            "source": (
                f"{dsh['repository']} rev {dsh['revision']} at {dsh['source']} "
                "(external, not vendored)"
            ),
        },
        "dependency_locks": [
            {
                "name": "rph/pnpm-lock.yaml",
                "version": "workspace",
                "lock_sha256": sha256(RPH / "pnpm-lock.yaml"),
                "source": "rph pnpm workspace (private, dependency-free packages)",
            },
            {
                "name": "rph/pnpm-workspace.yaml",
                "version": "workspace",
                "lock_sha256": sha256(RPH / "pnpm-workspace.yaml"),
                "source": "rph workspace settings",
            },
            {
                "name": "requirements-node.txt",
                "version": python_version,
                "lock_sha256": sha256(REPO / "requirements-node.txt"),
                "source": "python >=3.10 compute-node dependencies",
            },
            {
                "name": "pyproject.toml",
                "version": str(build_release.release_version(REPO)),
                "lock_sha256": sha256(REPO / "pyproject.toml"),
                "source": "root acp distribution",
            },
        ],
    }


def build_experience_manifest(
    *,
    version: str,
    rph: dict[str, Any],
    static: dict[str, Any],
    toolchain: dict[str, Any],
) -> dict[str, Any]:
    lockfile = RPH / "pnpm-lock.yaml"
    assets_manifest_sha = sha256(STATIC_ASSETS_OUT)
    return {
        "schema": "acp2.experience.release-manifest.v1",
        "schema_version": "1.0.0",
        "task_id": "M5-01",
        "generated_at": now(),
        "product": {
            "name": "acp",
            "version": version,
            "release_id": static["release_id"],
        },
        "workspace": {
            "root": "rph",
            "private": True,
            "package_manager": "pnpm@11.28.5",
            "node_engine": "^22.19.0 || >=24.0.0",
            "lockfile": "rph/pnpm-lock.yaml",
            "lockfile_sha256": sha256(lockfile),
            "root_package_json": False,
        },
        "packages": [
            {
                "name": entry["package"],
                "version": entry["version"],
                "file": entry["file"],
                "size": entry["size"],
                "sha256": entry["sha256"],
                "license": entry.get("license"),
                "bin": entry.get("bin"),
            }
            for entry in rph["packages"]
        ],
        "cli": {
            "bin": "rph",
            "link": "node_modules/.bin/rph",
            "package": "@rph/cli",
            "version": next(
                entry["version"] for entry in rph["packages"] if entry["package"] == "@rph/cli"
            ),
        },
        "install_surface": {
            "directory": "rph/install-surface",
            "manifest": "rph-install.package.json",
            "lockfile": "pnpm-lock.yaml",
            "checksums": "SHA256SUMS",
            "verify_script": "verify-install.mjs",
            "files": dict(sorted(rph["checksums"].items())),
        },
        "static_assets": {
            "manifest": (
                "docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json"
            ),
            "manifest_sha256": assets_manifest_sha,
            "release_id": static["release_id"],
            "digest": static["digest"],
            "files": static["files"],
            "bytes": static["bytes"],
            "entry_points": static["entry_points"],
            "verified_from": f"acp_web_assets inside {static['web_wheel']}",
        },
        "dsh": {
            "package": toolchain["dsh"]["name"],
            "version": toolchain["dsh"]["version"],
            "lock_sha256": toolchain["dsh"]["lock_sha256"],
            "vendored": False,
        },
        "toolchain": {
            "node": toolchain["node"],
            "pnpm": toolchain["pnpm"],
            "chromium": toolchain["chromium"],
        },
        "limitations": [
            (
                "RPH packages stay private:true and are distributed as tarballs; "
                "nothing is published to a public registry."
            ),
            (
                "DSH lives outside this repository at $ACP2_TOOLS/deepseek-harness; the "
                "BOM records its revision and lock digest instead of vendoring it."
            ),
            (
                "The versioned /experience/<release_id>/ mount is not implemented; the "
                "packaged Workbench /,/js,/css mounts are read-only and digest-verified "
                "at startup."
            ),
        ],
    }


def build_bom(
    *,
    store: Path,
    source_facts: dict[str, Any],
    version: str,
    python_facts: dict[str, Any],
    plugin_facts: dict[str, Any],
    rph: dict[str, Any],
    static: dict[str, Any],
    toolchain: dict[str, Any],
    bundles: dict[str, Path],
    generated_at: str,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    roles_dir = Path(python_facts["all"]["directory"])
    root_dir = Path(python_facts["root"]["directory"])
    role_notes = "three disjoint release roles (cccp/acp-worker/acp-web)"
    for path in sorted(roles_dir.glob("*.whl")) + sorted(roles_dir.glob("*.tar.gz")):
        kind = "python-wheel" if path.suffix == ".whl" else "python-sdist"
        artifacts.append(
            _artifact(
                artifact_id=f"python-roles-{path.name}",
                kind=kind,
                name=path.name,
                version=version,
                path=path,
                built_at=generated_at,
                build_script=SCRIPTS / "build_release.py",
                notes=role_notes,
            )
        )
    for path in sorted(root_dir.glob("*.whl")) + sorted(root_dir.glob("*.tar.gz")):
        kind = "python-wheel" if path.suffix == ".whl" else "python-sdist"
        artifacts.append(
            _artifact(
                artifact_id=f"python-root-{path.name}",
                kind=kind,
                name=path.name,
                version=version,
                path=path,
                built_at=generated_at,
                build_script=SCRIPTS / "build_release.py",
                notes="root acp distribution (mutually exclusive with the roles)",
            )
        )
    surface = Path(rph["directory"])
    for entry in rph["packages"]:
        artifacts.append(
            _artifact(
                artifact_id=f"rph-{entry['name']}",
                kind="rph-package",
                name=entry["file"],
                version=entry["version"],
                path=surface / entry["file"],
                built_at=generated_at,
                build_script=RPH / "scripts" / "package.mjs",
                notes=f"{entry['package']} release tarball (private, not published)",
            )
        )
    for name in RPH_SURFACE_FILES:
        artifacts.append(
            _artifact(
                artifact_id=f"rph-surface-{name.replace('.', '-')}",
                kind="rph-package",
                name=name,
                version=next(iter(rph["packages"]))["version"],
                path=surface / name,
                built_at=generated_at,
                build_script=RPH / "scripts" / "install-surface.mjs",
                notes=(
                    "RPH self-contained offline install surface file "
                    "(required alongside the tarballs)"
                ),
            )
        )
    for label, facts in sorted(plugin_facts.items()):
        wheel = Path(store) / "plugins" / facts["wheel"]
        wheel_version = wheel.name.split("-")[1]
        artifacts.append(
            _artifact(
                artifact_id=f"plugin-{label}",
                kind="plugin-wheel",
                name=wheel.name,
                version=wheel_version,
                path=wheel,
                built_at=generated_at,
                build_script=Path(facts["fixture"]) / "pyproject.toml",
                notes=f"M4-05 fixture plugin built from {Path(facts['fixture']).relative_to(REPO)}",
            )
        )
    artifacts.append(
        _artifact(
            artifact_id="static-assets-workbench",
            kind="static-asset-bundle",
            name=STATIC_ASSETS_OUT.name,
            version=version,
            path=Path(store) / "static-assets" / STATIC_ASSETS_OUT.name,
            built_at=generated_at,
            build_script=REPO / "src/acp/api/static_assets.py",
            notes=(
                f"{static['files']} files / {static['bytes']} bytes rebuilt from the "
                f"installed {static['web_wheel']}; embedded manifest equals this copy"
            ),
        )
    )
    contract_manifest = json.loads(CONTRACT_MANIFEST.read_text(encoding="utf-8"))
    artifacts.append(
        _artifact(
            artifact_id="contracts-acp2",
            kind="contract-bundle",
            name=bundles["contracts"].name,
            version=contract_manifest["contract_version"],
            path=bundles["contracts"],
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes="acp2-contracts corpus (manifest, schemas, release/experience/runtime/science)",
        )
    )
    artifacts.append(
        _artifact(
            artifact_id="science-nmr-model-assets",
            kind="science-asset",
            name=bundles["science"].name,
            version=version,
            path=bundles["science"],
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes="DP5/FCHL model assets (also shipped inside cccp/acp wheels)",
        )
    )
    artifacts.append(
        _artifact(
            artifact_id="docs-release",
            kind="document",
            name=bundles["docs"].name,
            version=version,
            path=bundles["docs"],
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes="docs/reference/current + packaging/README.md release documentation",
        )
    )
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    limitations = [
        (
            "Built from a dirty working tree "
            f"({source_facts.get('dirty_count', 0)} paths) at {source_facts['revision']}; "
            "a clean committed candidate is required before M5-02/M5-03 sign-off."
        ),
        (
            "RPH packages remain private:true at version 0.0.0 and are distributed as tarballs; "
            "no public npm publication is claimed."
        ),
        (
            "The versioned /experience/<release_id>/ read-only mount from B-req-06 is not "
            "implemented here; the packaged Workbench /, /js and /css mounts are verified "
            "read-only with startup digest verification (B25 static-assets row stays NOT_VERIFIED)."
        ),
        (
            "BOM evidence reuse_rule is rerun_required: M5-01 proves build+install+serve "
            "from artifacts, while the full G01-G26 walker is M5-02 and the independent "
            "operator run is M5-03."
        ),
        (
            "No QC or model invocation was performed; scientific software entries record "
            "binary version probes only."
        ),
        ("persistent_tag is a proposal only: no tag, commit or push was performed by this task."),
    ]
    return {
        "schema": BOM_SCHEMA,
        "bom_version": 1,
        "generated_at": generated_at,
        "task_id": "M5-01",
        "owner": "REL",
        "source": {
            "repository": source_facts["repository"],
            "revision": source_facts["revision"],
            "tree": source_facts["tree"],
            "clean": source_facts["clean"],
            "persistent_ref": source_facts["persistent_ref"],
            "persistent_tag": source_facts["persistent_tag"],
            "dirty_path_snapshot": source_facts.get("dirty_path_snapshot"),
        },
        "evidence": {
            "contract_version": contract_manifest["contract_version"],
            "contract_digest": contract_manifest["contract_digest"],
            "coverage_path": str(COVERAGE.relative_to(REPO)),
            "coverage_sha256": coverage["coverage_sha256"],
            "coverage_scope_rows": len(coverage["tasks"]),
            "coverage_gap_rows": len(coverage["gaps"]),
            "reuse_rule": "rerun_required",
        },
        "artifacts": artifacts,
        "toolchain": toolchain,
        "scientific_software": _scientific_software(),
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _source_facts(source: Path) -> dict[str, Any]:
    state = build_release._git_state(source)
    if not state.get("git"):
        raise RuntimeError(f"{source} is not a git checkout; the BOM needs revision/tree facts")
    dirty = state.get("dirty_paths") or []
    snapshot = (
        f"{len(dirty)} paths; sha256={hashlib.sha256(chr(10).join(dirty).encode()).hexdigest()}; "
        f"sample={dirty[:8]}"
        if dirty
        else None
    )
    remote = subprocess.run(
        ["git", "-C", str(source), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "repository": remote or str(source),
        "revision": state["revision"],
        "tree": state["tree"],
        "clean": bool(state["clean"]),
        "dirty_count": len(dirty),
        "dirty_path_snapshot": snapshot,
        "persistent_ref": branch or "detached",
        "persistent_tag": f"acp2-remediation-m5-01-candidate (proposed, not created; ref {branch})",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO)
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(os.environ.get("ACP2_ARTIFACT_STORE", "/ACPServer/acp2-artifacts/remediation"))
        / "m5-01",
        help="durable artifact store for M5-01",
    )
    parser.add_argument("--bom-out", type=Path, default=BOM_OUT)
    parser.add_argument(
        "--skip-rph-package",
        action="store_true",
        help="reuse the existing rph/dist-packages instead of running pnpm package",
    )
    parser.add_argument(
        "--skip-python-build",
        action="store_true",
        help="reuse the wheels already present in the store",
    )
    args = parser.parse_args(argv)
    source = args.source.resolve()
    store = args.store.resolve()
    log_dir = store / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    version = build_release.release_version(source)
    source_facts = _source_facts(source)
    generated_at = now()
    build_python_python = ensure_build_python(store, log_dir)

    if args.skip_python_build:
        python_facts = {
            "all": {
                "directory": str(store / "python" / "roles"),
                "roles": ["cccp", "web", "worker"],
            },
            "root": {
                "directory": str(store / "python" / "root"),
                "roles": ["root"],
            },
        }
    else:
        python_facts = build_python(
            source,
            store,
            log_dir,
            clean=bool(source_facts["clean"]),
            build_python=build_python_python,
        )
    plugin_facts = build_plugins(store, log_dir, build_python_python)
    rph = package_rph(store, log_dir, repackage=not args.skip_rph_package)
    web_wheel = next((store / "python" / "roles").glob("acp_web-*.whl"))
    static, _ = build_static_assets(store, web_wheel, version=version)
    toolchain = _toolchain()

    # The BOM instance is generated *after* this bundle and is referenced by
    # URI from the BOM itself; bundling a stale copy would make the contract
    # corpus claim a different BOM than the one it ships with.
    contract_members = [
        item
        for item in _tree_members(CONTRACTS, "acp2-contracts")
        if item[1] != "acp2-contracts/release/bom.current.json"
    ]
    bundles = {
        "contracts": _tar_bundle(
            store / "contracts" / "acp2-contracts.tar.gz",
            contract_members,
        ),
        "science": _tar_bundle(
            store / "science" / "cccp-nmr-model-assets.tar.gz",
            _tree_members(SCIENCE_ASSETS, "cccp/analysis/nmr/model_assets"),
        ),
        "docs": _tar_bundle(
            store / "docs" / "acp-release-docs.tar.gz",
            _tree_members(REPO / "docs" / "reference" / "current", "docs/reference/current")
            + [(REPO / "packaging" / "README.md", "packaging/README.md")],
        ),
    }
    science_files = {path.name for path in SCIENCE_ASSETS.iterdir() if path.is_file()}
    if not set(CRITICAL_SCIENCE_ASSETS) <= science_files:
        raise RuntimeError(
            f"model assets incomplete: {sorted(set(CRITICAL_SCIENCE_ASSETS) - science_files)}"
        )

    experience = build_experience_manifest(
        version=version, rph=rph, static=static, toolchain=toolchain
    )
    EXPERIENCE_MANIFEST_OUT.write_text(
        json.dumps(experience, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    bom = build_bom(
        store=store,
        source_facts=source_facts,
        version=version,
        python_facts=python_facts,
        plugin_facts=plugin_facts,
        rph=rph,
        static=static,
        toolchain=toolchain,
        bundles=bundles,
        generated_at=generated_at,
    )
    errors = schema_validate.validate(bom, schema_validate.load(BOM_SCHEMA_PATH))
    if errors:
        raise RuntimeError("BOM violates bom.schema.json:\n" + "\n".join(errors))
    bom_text = json.dumps(bom, indent=2, sort_keys=True) + "\n"
    args.bom_out.parent.mkdir(parents=True, exist_ok=True)
    args.bom_out.write_text(bom_text, encoding="utf-8")
    stored_bom = _copy_file(args.bom_out, store / "bom" / "bom.current.json")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": generated_at,
        "task_id": "M5-01",
        "store": str(store),
        "bom": str(args.bom_out),
        "bom_sha256": sha256(args.bom_out),
        "stored_bom": str(stored_bom),
        "version": version,
        "source": source_facts,
        "python": python_facts,
        "plugins": plugin_facts,
        "rph": {
            "directory": rph["directory"],
            "packages": rph["packages"],
            "repackage": rph["repackage"],
        },
        "static_assets": static,
        "components": len(bom["artifacts"]),
        "component_kinds": sorted({entry["kind"] for entry in bom["artifacts"]}),
    }
    (store / "bom" / "build-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"BOM written: {args.bom_out}")
    print(f"sha256 {receipt['bom_sha256']}  components {receipt['components']}")
    for kind in receipt["component_kinds"]:
        count = sum(1 for entry in bom["artifacts"] if entry["kind"] == kind)
        print(f"  {kind}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
