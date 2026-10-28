#!/usr/bin/env python3
"""Build every ACP 2.0 release artifact into the durable store and write the BOM.

M5-01 owns the single bill of materials: the three Python roles plus the root
``acp`` distribution, the RPH tarball set *with its self-contained offline
install surface*, the packaged Workbench static assets, the third-party plugin
wheels M4-05 exercised, the contract corpus, the NMR model assets, the release
documents, **the offline third-party dependency wheelhouse (x86_64 + ARM64)**
and **every evidence-coverage receipt** (M5-03 findings F1/F7).  Every component
is copied into ``--store`` and referenced from ``bom.current.json`` by URI +
sha256 + size, so a clean host can install from the store alone
(``scripts/acceptance/acp2_m501_install.py``).

Nothing here runs QC or a model: scientific software is only version-probed.
"""

from __future__ import annotations

import argparse
import atexit
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
RECEIPT_SCHEMA = "acp2.release.build-receipt.v1"
TASK_ID = "M5-02"
PERSISTENT_TAG = (
    "acp2-remediation-m5-02-candidate (proposed, not created; ref acp2/integration-remediation)"
)
#: The M5-02 release-ledger overlay: worktree paths that are expected to differ
#: from the clean committed candidate because they ARE the release deliverable
#: being built.  Any other dirty path means the business source changed and the
#: BOM must not claim a clean candidate.
DEFAULT_RELEASE_OVERLAY = (
    "docs/reference/next-change/acp2-contracts/release/",
    "docs/reference/next-change/acp2-contracts/manifest.json",
    "docs/reference/next-change/acp2-contracts/experience/release-manifest.json",
    "docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json",
    "scripts/",
    # M5-03 remediation overlay: the operator-found product defects (F2/F3/F4/
    # F6) are part of the release deliverable built from the frozen coverage
    # candidate, exactly like the BOM/contract files around them.
    "src/acp/install_guard.py",
    "src/acp/cli.py",
    "src/acp/api/server.py",
    "src/acp/scheduler/remote/",
    "frontend/",
    "tests/",
    "reports/acp2_remediation_20261026/m5-02/",
    "reports/acp2_remediation_20261026/m5-03/",
)
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
#: M5-03 F1: the artifact store must install offline on the operator's ARM64
#: Linux host even when the release is built on x86_64.  Both manylinux2014
#: tag sets (glibc >= 2.17) are collected so the same wheelhouse serves both
#: machines; platform-specific wheels are tagged per platform in the manifest.
WHEELHOUSE_PYTHON = "3.11"
WHEELHOUSE_PLATFORMS = ("manylinux2014_x86_64", "manylinux2014_aarch64")
WHEELHOUSE_SCHEMA = "acp2.release.python-wheelhouse.v1"
RECEIPTS_SCHEMA = "acp2.release.coverage-receipts.v1"
#: Dependencies the root/role runtime sets intentionally share; a wheelhouse
#: missing any of them for either platform is a build failure (F1 was exactly
#: this gap: the store had no third-party wheels at all).
_WHEELHOUSE_ACPS = {"acp", "cccp", "acp-worker", "acp-web"}


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


def _normalized_distribution_name(name: str) -> str:
    """PEP 503 normalisation (``python_multipart`` == ``python-multipart``)."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _requirement_name(requirement: str) -> str:
    """The distribution name of one ``Requires-Dist``/requirements line."""
    token = requirement.split(";", 1)[0].strip()
    name = re.split(r"[<>=!~\[ (]", token, maxsplit=1)[0]
    return _normalized_distribution_name(name)


def _wheel_distribution_name(filename: str) -> str:
    """Distribution name from a wheel filename (``numpy-2.4.6-cp311-...whl``)."""
    return _normalized_distribution_name(filename.split("-", 1)[0])


def _wheel_requirements(wheel: Path, extras: tuple[str, ...] = ()) -> list[str]:
    """Runtime ``Requires-Dist`` entries of one built wheel, minus ACP dists.

    The wheelhouse is derived from the wheels that are actually shipped, so the
    dependency set has one authority (the wheel metadata) and no second list to
    drift from ``pyproject.toml``/``requirements-role.txt``.
    """
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        lines = archive.read(metadata).decode("utf-8").splitlines()
    requirements: list[str] = []
    for line in lines:
        if not line.startswith("Requires-Dist:"):
            continue
        specification = line.split(":", 1)[1].strip()
        requirement, _, marker = specification.partition(";")
        marker = marker.strip()
        if marker:
            extra = re.search(r'extra\s*==\s*["\']([^"\']+)["\']', marker)
            if extra is None or extra.group(1) not in extras:
                continue
        name = _requirement_name(requirement)
        if name in _WHEELHOUSE_ACPS:
            continue
        requirements.append(requirement.strip())
    return requirements


def _wheelhouse_requirements(root_wheel: Path, role_wheels: list[Path]) -> list[str]:
    """The union of the runtime requirements of the four release distributions."""
    requirements: list[str] = []
    for wheel in [root_wheel, *role_wheels]:
        extras: tuple[str, ...] = ("api", "remote") if wheel.name.startswith("acp-") else ()
        for requirement in _wheel_requirements(wheel, extras):
            if requirement not in requirements:
                requirements.append(requirement)
    if not requirements:
        raise RuntimeError("the built wheels declared no runtime requirements")
    return requirements


def build_wheelhouse(
    store: Path,
    *,
    root_wheel: Path,
    role_wheels: list[Path],
    build_python: Path,
    log_dir: Path,
) -> dict[str, Any]:
    """Download every runtime dependency for x86_64 **and** aarch64 (M5-03 F1).

    The operator host is ARM64 Linux while the release is built on x86_64, so a
    single-platform wheelhouse would leave the offline install broken exactly
    as the operator found it.  Both manylinux2014 tag sets are collected (glibc
    >= 2.17) and every direct requirement must resolve on both platforms; the
    manifest records per-file platform coverage and SHA-256 so the installer can
    verify the directory before installing with ``--no-index``.
    """
    requirements = _wheelhouse_requirements(root_wheel, role_wheels)
    wheelhouse = store / "python" / "wheelhouse"
    if wheelhouse.exists():
        shutil.rmtree(wheelhouse)
    wheelhouse.mkdir(parents=True)
    platform_files: dict[str, set[str]] = {tag: set() for tag in WHEELHOUSE_PLATFORMS}
    covered: dict[str, set[str]] = {tag: set() for tag in WHEELHOUSE_PLATFORMS}
    staged: dict[str, Path] = {}
    abi = f"cp{WHEELHOUSE_PYTHON.replace('.', '')}"
    for platform_tag in WHEELHOUSE_PLATFORMS:
        staging = store / "python" / f".wheelhouse-{platform_tag}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        _run(
            [
                str(build_python),
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-input",
                "--dest",
                str(staging),
                "--only-binary=:all:",
                "--python-version",
                WHEELHOUSE_PYTHON.replace(".", ""),
                "--implementation",
                "cp",
                "--abi",
                abi,
                "--platform",
                platform_tag,
                *requirements,
            ],
            cwd=store,
            log=log_dir / f"wheelhouse-{platform_tag}.log",
        )
        downloaded = sorted(staging.glob("*.whl"))
        if not downloaded:
            raise RuntimeError(f"pip download produced no wheels for {platform_tag}")
        for path in downloaded:
            target = wheelhouse / path.name
            if target.is_file():
                if sha256(target) != sha256(path):
                    raise RuntimeError(
                        f"wheelhouse name collision with different bytes: {path.name}"
                    )
            else:
                shutil.copy2(path, target)
            staged.setdefault(path.name, target)
            platform_files[platform_tag].add(path.name)
            covered[platform_tag].add(_wheel_distribution_name(path.name))
        missing = sorted(
            requirement
            for requirement in requirements
            if _requirement_name(requirement) not in covered[platform_tag]
        )
        if missing:
            raise RuntimeError(
                f"wheelhouse cannot satisfy {platform_tag}: missing wheels for {missing}"
            )
        shutil.rmtree(staging)
    records = [
        {
            "name": name,
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
            "platforms": sorted(tag for tag in WHEELHOUSE_PLATFORMS if name in platform_files[tag]),
        }
        for name, path in sorted(staged.items())
    ]
    checksum_lines = "".join(f"{record['sha256']}  {record['name']}\n" for record in records)
    (wheelhouse / "SHA256SUMS").write_text(checksum_lines, encoding="utf-8")
    manifest = {
        "schema": WHEELHOUSE_SCHEMA,
        "generated_at": now(),
        "python": WHEELHOUSE_PYTHON,
        "abi": abi,
        "platforms": list(WHEELHOUSE_PLATFORMS),
        "directory": str(wheelhouse),
        "requirements": requirements,
        "wheels": records,
        "coverage": {
            tag: {
                "files": len(platform_files[tag]),
                "direct_requirements": len(
                    [r for r in requirements if _requirement_name(r) in covered[tag]]
                ),
            }
            for tag in WHEELHOUSE_PLATFORMS
        },
        "install_argv": "--no-index --find-links <store>/python/wheelhouse",
        "notes": (
            "Third-party runtime dependency wheelhouse for the root acp and role "
            "distributions. Both manylinux2014 (glibc >= 2.17) tag sets are "
            "collected so the same store installs offline on x86_64 and ARM64."
        ),
    }
    manifest_path = store / "python" / "wheelhouse-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "directory": str(wheelhouse),
        "wheels": len(records),
        "bytes": sum(record["size_bytes"] for record in records),
        "platforms": list(WHEELHOUSE_PLATFORMS),
        "platform_wheels": {tag: len(platform_files[tag]) for tag in WHEELHOUSE_PLATFORMS},
        "requirements": requirements,
        "checksums_sha256": sha256(wheelhouse / "SHA256SUMS"),
    }


def _coverage_receipt_paths(coverage: dict[str, Any]) -> list[str]:
    """Every receipt file referenced by the evidence-coverage ledger."""
    paths = {
        str(row["receipt"])
        for section in ("tasks", "checkpoints", "gaps", "h_rows", "deferred_rows")
        for row in coverage.get(section) or []
        if row.get("receipt")
    }
    return sorted(paths)


def build_receipts(store: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    """Ship every coverage receipt inside the store (M5-03 finding F7).

    The PASS rows of the coverage ledger reference ``reports/...`` files that
    used to live only in the implementation checkout, so an independent
    operator could not verify a digest from the delivered store.  The bundle
    keeps the repository-relative layout, ships a deterministic tarball plus an
    extracted directory, and records every digest in a manifest.
    """
    receipts = _coverage_receipt_paths(coverage)
    if not receipts:
        raise RuntimeError("the coverage ledger references no receipts")
    pairs = [
        (section, row)
        for section in ("tasks", "checkpoints", "gaps", "h_rows", "deferred_rows")
        for row in coverage.get(section) or []
    ]
    recorded = {
        str(row["receipt"]): row["receipt_sha256"] for _section, row in pairs if row.get("receipt")
    }
    members: list[tuple[Path, str]] = []
    files: list[dict[str, Any]] = []
    for relative in receipts:
        source = REPO / relative
        if not source.is_file():
            raise RuntimeError(f"coverage receipt is missing from the worktree: {relative}")
        digest = sha256(source)
        if recorded.get(relative) != digest:
            raise RuntimeError(f"coverage receipt sha256 mismatch: {relative}")
        members.append((source, relative))
        files.append({"path": relative, "sha256": digest, "size_bytes": source.stat().st_size})
    out = store / "evidence"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    tar_path = _tar_bundle(out / "coverage-receipts.tar.gz", members)
    extracted = out / "receipts"
    for source, relative in members:
        target = extracted / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {
        "schema": RECEIPTS_SCHEMA,
        "generated_at": now(),
        "coverage_sha256": coverage["coverage_sha256"],
        "layout": "repository-relative paths under receipts/",
        "receipts": files,
        "total_bytes": sum(entry["size_bytes"] for entry in files),
    }
    manifest_path = out / "coverage-receipts-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "tar": str(tar_path),
        "directory": str(extracted),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "receipts": len(files),
        "bytes": manifest["total_bytes"],
        "tar_sha256": sha256(tar_path),
    }


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------


def build_python(
    source: Path,
    store: Path,
    log_dir: Path,
    *,
    clean: bool,
    build_python: Path,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Three disjoint roles plus the mutually exclusive root distribution."""
    python_dir = store / "python"
    roles_dir = python_dir / "roles"
    root_dir = python_dir / "root"
    for directory in (roles_dir, root_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    # A materialized candidate worktree is physically dirty with exactly the
    # declared release overlay; the BOM separately records that overlay, so the
    # build tool must accept it while a non-overlay dirty tree stays refused.
    allow = [] if (clean and not allow_dirty) else ["--allow-dirty"]
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
    wheelhouse: dict[str, Any],
    receipts: dict[str, Any],
    toolchain: dict[str, Any],
    bundles: dict[str, Path],
    generated_at: str,
    supersedes: list[str],
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
    artifacts.append(
        _artifact(
            artifact_id="python-wheelhouse",
            kind="document",
            name=Path(wheelhouse["manifest"]).name,
            version=version,
            path=Path(wheelhouse["manifest"]),
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes=(
                "Offline third-party dependency wheelhouse index (M5-03 F1): "
                f"{wheelhouse['wheels']} wheels / {wheelhouse['bytes']} bytes under "
                f"{wheelhouse['directory']}; platforms "
                f"{', '.join(wheelhouse['platforms'])}; install with "
                "'--no-index --find-links <store>/python/wheelhouse'"
            ),
        )
    )
    artifacts.append(
        _artifact(
            artifact_id="python-wheelhouse-checksums",
            kind="document",
            name="wheelhouse-SHA256SUMS",
            version=version,
            path=Path(wheelhouse["directory"]) / "SHA256SUMS",
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes="Per-wheel SHA-256 list for the offline dependency wheelhouse",
        )
    )
    artifacts.append(
        _artifact(
            artifact_id="evidence-receipts",
            kind="document",
            name=Path(receipts["tar"]).name,
            version=version,
            path=Path(receipts["tar"]),
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes=(
                "Every receipt referenced by the evidence-coverage ledger, laid out "
                f"by repository-relative path ({receipts['receipts']} files / "
                f"{receipts['bytes']} bytes); extracted copy under "
                f"{receipts['directory']}"
            ),
        )
    )
    artifacts.append(
        _artifact(
            artifact_id="evidence-receipts-manifest",
            kind="document",
            name=Path(receipts["manifest"]).name,
            version=version,
            path=Path(receipts["manifest"]),
            built_at=generated_at,
            build_script=Path(__file__).resolve(),
            notes="SHA-256 manifest of the coverage receipts shipped in the store",
        )
    )
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    limitations = [
        (
            "Built from the clean committed candidate "
            f"{source_facts['revision']} (tree {source_facts['tree']}). The only worktree "
            "differences are the M5-02 release-ledger overlay recorded in "
            "source.release_overlay; no business source changed."
            if source_facts.get("clean")
            else (
                "Built from a dirty working tree "
                f"({source_facts.get('dirty_count', 0)} non-overlay paths) at "
                f"{source_facts['revision']}; a clean committed candidate is required "
                "before M5-03 sign-off."
            )
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
            "BOM evidence reuse_rule is exact for this M5-02 rebuild: every artifact is "
            "rebuilt from the clean candidate and every URI/receipt is re-fetched by "
            "scripts/release/walk_bom.py --verify. Rows whose receipts predate the "
            "candidate use coverage row reuse=reused or rerun_required; they are never "
            "silently upgraded."
        ),
        (
            "The contract bundle is a build-time snapshot of acp2-contracts and "
            "deliberately excludes release/bom.current.json; the manifest snapshot inside "
            "the bundle predates the final BOM sha registration. The live manifest and "
            "this BOM are the authoritative pair."
        ),
        (
            "No QC or model invocation was performed by the BOM build; scientific software "
            "entries record binary version probes only. M4 receipts remain the scientific "
            "evidence."
        ),
        ("persistent_tag is a proposal only: no tag, commit or push was performed by this task."),
        (
            "The offline dependency wheelhouse targets CPython 3.11 on manylinux2014 "
            "(glibc >= 2.17) x86_64 and aarch64. Other interpreters/older glibc need "
            "a wheelhouse rebuild; the manifest records the covered platforms."
        ),
        (
            "Coverage receipts ship both as coverage-receipts.tar.gz and as an "
            "extracted receipts/ tree; walk_bom --verify hashes the extracted files "
            "against the ledger and checks tar membership, so neither copy can drift."
        ),
    ]
    return {
        "schema": BOM_SCHEMA,
        "bom_version": 1,
        "generated_at": generated_at,
        "task_id": TASK_ID,
        "owner": "REL",
        "source": {
            "repository": source_facts["repository"],
            "revision": source_facts["revision"],
            "tree": source_facts["tree"],
            "clean": source_facts["clean"],
            "persistent_ref": source_facts["persistent_ref"],
            "persistent_tag": source_facts["persistent_tag"],
            "dirty_path_snapshot": source_facts.get("dirty_path_snapshot"),
            "release_overlay": source_facts.get("release_overlay", []),
        },
        "evidence": {
            "contract_version": contract_manifest["contract_version"],
            "contract_digest": contract_manifest["contract_digest"],
            "coverage_path": str(COVERAGE.relative_to(REPO)),
            "coverage_sha256": coverage["coverage_sha256"],
            "coverage_scope_rows": len(coverage["tasks"]),
            "coverage_gap_rows": len(coverage["gaps"]),
            "reuse_rule": "exact",
            "supersedes": supersedes,
            "reused_from": [
                "reports/acp2_remediation_20261026/m5-01/summary.json",
                "reports/acp2_remediation_20261026/m5-01/bom-summary.json",
            ],
        },
        "artifacts": artifacts,
        "toolchain": toolchain,
        "scientific_software": _scientific_software(),
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _porcelain_path(line: str) -> str:
    """Extract the path from one ``git status --porcelain`` v1 line."""
    path = line[3:] if len(line) > 3 else line
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def _source_facts(
    source: Path,
    overlay: tuple[str, ...],
    *,
    allow_dirty: bool = False,
    overlay_source: Path | None = None,
) -> dict[str, Any]:
    state = build_release._git_state(source)
    if not state.get("git"):
        raise RuntimeError(f"{source} is not a git checkout; the BOM needs revision/tree facts")
    dirty = build_release._git_state(overlay_source or source).get("dirty_paths") or []
    overlay_paths: list[str] = []
    unexpected: list[str] = []
    for line in dirty:
        path = _porcelain_path(line)
        if any(path == prefix or path.startswith(prefix) for prefix in overlay):
            overlay_paths.append(path)
        else:
            unexpected.append(path)
    if unexpected and not allow_dirty:
        raise RuntimeError(
            "the worktree changed outside the declared M5-02 release overlay; a clean "
            "candidate cannot be claimed: " + ", ".join(sorted(unexpected)[:12])
        )
    snapshot = (
        f"{len(unexpected)} paths; sha256="
        f"{hashlib.sha256(chr(10).join(unexpected).encode()).hexdigest()}; "
        f"sample={unexpected[:8]}"
        if unexpected
        else None
    )
    repo_source = overlay_source or source
    remote = subprocess.run(
        ["git", "-C", str(repo_source), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(repo_source), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "repository": remote or str(source),
        "revision": state["revision"],
        "tree": state["tree"],
        "clean": not unexpected,
        "dirty_count": len(unexpected),
        "dirty_path_snapshot": snapshot,
        "release_overlay": sorted(set(overlay_paths)),
        "persistent_ref": branch or "detached",
        "persistent_tag": PERSISTENT_TAG,
    }


def _temporary_clean_worktree(
    source: Path, revision: str, overlay_paths: list[str]
) -> tuple[Path, Any]:
    """Materialize the committed candidate + the declared release overlay.

    The overlay is the release deliverable itself (BOM/contracts/report files
    *and*, for an M5-03 remediation build, the fixed product sources).  Build
    from the committed candidate and copy exactly those paths in, so the
    wheels contain the overlay bytes while unrelated worktree dirt can never
    leak into the release.
    """

    root = Path(tempfile.mkdtemp(prefix="acp2-bom-clean-", dir="/tmp/opencode"))
    directory = root / "worktree"
    completed = subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "--detach", str(directory), revision],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(
            "could not create a clean worktree for the Python build: "
            + (completed.stderr or completed.stdout)[-2000:]
        )

    for relative in overlay_paths:
        origin = source / relative
        target = directory / relative
        if origin.is_dir():
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                origin,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
            )
        elif origin.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
        else:  # deleted in the release worktree: the candidate must not keep it
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    def cleanup() -> None:
        subprocess.run(
            ["git", "-C", str(source), "worktree", "remove", "--force", str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
        shutil.rmtree(root, ignore_errors=True)

    return directory, cleanup


def _coverage_candidate() -> str:
    """The single committed candidate the coverage ledger judges.

    The BOM must be built from this revision, not merely from ``HEAD``: the
    ledger rows freeze ``candidate`` and ``walk_bom --verify`` refuses drift.
    Later documentation-only commits are therefore part of the declared
    release overlay, never a new candidate.
    """
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    candidates = {
        row["candidate"]
        for section in ("tasks", "checkpoints", "gaps", "h_rows", "deferred_rows")
        for row in coverage.get(section) or []
        if row.get("candidate")
    }
    if len(candidates) != 1:
        raise RuntimeError(
            f"evidence-coverage must reference exactly one candidate, got {sorted(candidates)}"
        )
    return candidates.pop()


def _overlay_drift(source: Path, candidate: str, head: str, overlay: tuple[str, ...]) -> list[str]:
    """Paths changed after the candidate that are *not* declared overlay."""
    if candidate == head:
        return []
    completed = subprocess.run(
        ["git", "-C", str(source), "diff", "--name-only", f"{candidate}..{head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"cannot compare coverage candidate {candidate} with HEAD {head}: "
            + (completed.stderr or "")[-1000:]
        )
    changed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return [
        path
        for path in changed
        if not any(path == prefix or path.startswith(prefix) for prefix in overlay)
    ]


def _git_head(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{path} is not a git checkout: " + (completed.stderr or "")[-1000:])
    return completed.stdout.strip()


def _register_bom_digest(bom_path: Path) -> dict[str, Any]:
    """Refresh the B26 ``release-bom-current`` registration for a new BOM.

    Rebuilding the BOM necessarily changes its digest; ``walk_bom --verify``
    refuses a stale registration, so the builder refreshes the mechanical
    digest entries (BOM sha + manifest self sha).  No contract status, version
    or coverage judgment is touched.  The stored contract bundle still predates
    this update by design (documented in the BOM limitations).
    """
    import walk_bom  # sibling release tool; single implementation of the rule

    manifest = json.loads(CONTRACT_MANIFEST.read_text(encoding="utf-8"))
    bom_sha = sha256(bom_path)
    changed = False
    for entry in manifest["contracts"]:
        if entry["id"] == "release-bom-current" and entry.get("sha256") != bom_sha:
            entry["sha256"] = bom_sha
            changed = True
    self_sha = walk_bom.manifest_self_digest(manifest)
    for entry in manifest["contracts"]:
        if entry["id"] == "manifest" and entry.get("sha256") != self_sha:
            entry["sha256"] = self_sha
            changed = True
    if changed:
        CONTRACT_MANIFEST.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "bom_sha256": bom_sha,
        "manifest_self_sha256": self_sha,
        "changed": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO)
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(os.environ.get("ACP2_ARTIFACT_STORE", "/ACPServer/acp2-artifacts/remediation"))
        / "m5-02",
        help="durable artifact store for the M5-02 clean-candidate rebuild",
    )
    parser.add_argument("--bom-out", type=Path, default=BOM_OUT)
    parser.add_argument(
        "--python-source",
        type=Path,
        default=None,
        help=(
            "clean committed worktree used to build the Python/RPH artifacts; the "
            "release-ledger overlay is read from --source. Defaults to --source."
        ),
    )
    parser.add_argument(
        "--release-overlay",
        default=",".join(DEFAULT_RELEASE_OVERLAY),
        help="comma-separated worktree paths that are the M5-02 release deliverable",
    )
    parser.add_argument(
        "--supersedes",
        default=None,
        help="comma-separated history references recorded in the BOM; defaults to the M5-01 BOM",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="record clean=false instead of failing when non-overlay paths are dirty",
    )
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
    overlay = tuple(item.strip() for item in args.release_overlay.split(",") if item.strip())
    candidate = _coverage_candidate()
    head = _git_head(source)
    if candidate != head:
        ancestor = subprocess.run(
            ["git", "-C", str(source), "merge-base", "--is-ancestor", candidate, head],
            capture_output=True,
            text=True,
            check=False,
        )
        if ancestor.returncode != 0:
            raise RuntimeError(
                f"evidence-coverage candidate {candidate} is not an ancestor of HEAD {head}"
            )
        drift = _overlay_drift(source, candidate, head, overlay)
        if drift:
            raise RuntimeError(
                "commits after the coverage candidate change non-overlay paths; the BOM "
                "cannot claim that candidate: " + ", ".join(sorted(drift)[:12])
            )
    cleanup = None
    if args.python_source is not None:
        python_source = args.python_source.resolve()
    else:
        preliminary = _source_facts(
            source, overlay, allow_dirty=args.allow_dirty, overlay_source=source
        )
        if candidate != head or preliminary["release_overlay"]:
            # The coverage ledger freezes the committed candidate and the
            # release overlay (BOM/contracts/report files plus, for M5-03, the
            # fixed product sources) carries the deliverable bytes; build from
            # a throwaway worktree at that candidate instead of weakening the
            # clean check.
            python_source, cleanup = _temporary_clean_worktree(
                source, candidate, preliminary["release_overlay"]
            )
            atexit.register(cleanup)
        else:
            python_source = source
    store = args.store.resolve()
    log_dir = store / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if _git_head(python_source) != candidate:
        raise RuntimeError(
            f"--python-source must be at the coverage candidate {candidate}, "
            f"got {_git_head(python_source)}"
        )
    version = build_release.release_version(python_source)
    source_facts = _source_facts(
        python_source, overlay, allow_dirty=args.allow_dirty, overlay_source=source
    )
    if args.supersedes:
        supersedes = [item.strip() for item in args.supersedes.split(",") if item.strip()]
    else:
        history_bom = store.parent / "m5-01" / "bom" / "bom.current.json"
        previous_bom = (
            history_bom
            if history_bom.is_file()
            else (args.bom_out if args.bom_out.is_file() else None)
        )
        supersedes = (
            [f"{previous_bom.relative_to(REPO).as_posix()} sha256:{sha256(previous_bom)}"]
            if previous_bom is not None and previous_bom.is_relative_to(REPO)
            else ([f"{previous_bom} sha256:{sha256(previous_bom)}"] if previous_bom else [])
        )
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
            python_source,
            store,
            log_dir,
            clean=bool(source_facts["clean"]),
            build_python=build_python_python,
            allow_dirty=python_source != source,
        )
    plugin_facts = build_plugins(store, log_dir, build_python_python)
    rph = package_rph(store, log_dir, repackage=not args.skip_rph_package)
    root_wheel = next((store / "python" / "root").glob("acp-*.whl"))
    role_wheels = sorted((store / "python" / "roles").glob("*.whl"))
    wheelhouse = build_wheelhouse(
        store,
        root_wheel=root_wheel,
        role_wheels=role_wheels,
        build_python=build_python_python,
        log_dir=log_dir,
    )
    coverage_ledger = json.loads(COVERAGE.read_text(encoding="utf-8"))
    receipts = build_receipts(store, coverage_ledger)
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
        wheelhouse=wheelhouse,
        receipts=receipts,
        toolchain=toolchain,
        bundles=bundles,
        generated_at=generated_at,
        supersedes=supersedes,
    )
    errors = schema_validate.validate(bom, schema_validate.load(BOM_SCHEMA_PATH))
    if errors:
        raise RuntimeError("BOM violates bom.schema.json:\n" + "\n".join(errors))
    bom_text = json.dumps(bom, indent=2, sort_keys=True) + "\n"
    args.bom_out.parent.mkdir(parents=True, exist_ok=True)
    args.bom_out.write_text(bom_text, encoding="utf-8")
    registration = _register_bom_digest(args.bom_out)
    stored_bom = _copy_file(args.bom_out, store / "bom" / "bom.current.json")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": generated_at,
        "task_id": TASK_ID,
        "store": str(store),
        "bom": str(args.bom_out),
        "bom_sha256": sha256(args.bom_out),
        "stored_bom": str(stored_bom),
        "bom_registration": registration,
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
        "wheelhouse": wheelhouse,
        "receipts": receipts,
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
    if cleanup is not None:
        cleanup()
        atexit.unregister(cleanup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
