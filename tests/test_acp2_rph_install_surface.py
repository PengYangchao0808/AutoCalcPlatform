"""Guard the RPH package set against resolving private packages from npm.

The 2026-10-26 clean-host probe (``reports/acp2_remediation_20261026/
rph-package-install-probe.json``) failed with ``ERR_PNPM_FETCH_404`` because the
packed tarballs keep ``@rph/*`` dependencies as plain versions and no install
manifest/override redirected them to the local artifacts.

These guards pin the fix from the Python side (the behavioural packaging test
lives in ``rph/test/install-surface.test.mjs`` and runs in the RPH CI job):

* workspace manifests must keep internal dependencies on ``workspace:*`` —
  anything else is packed verbatim and resolves on the public registry;
* ``rph/scripts/package.mjs`` must generate the offline install surface from
  ``rph/scripts/install-surface.mjs`` and verify it before finishing;
* when ``rph/dist-packages`` has been built locally, the generated manifest,
  overrides, lockfile and checksums must be registry-free and digest-clean.

This closes only the standalone install defect; it does not close M5-01.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RPH = ROOT / "rph"
DIST = RPH / "dist-packages"
PACKAGE_NAMES = ("research", "bridge", "dsh-bundle", "cli")
INSTALL_MANIFEST = "rph-install.package.json"
INSTALL_PACKAGE = "package.json"
INSTALL_WORKSPACE = "pnpm-workspace.yaml"
INSTALL_LOCKFILE = "pnpm-lock.yaml"
PACKAGE_INDEX = "rph-packages.json"
CHECKSUMS = "SHA256SUMS"

REGISTRY_MARKERS = (
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "registry.npmmirror.com",
)

RPH_LOCK_ENTRY = re.compile(r"^ {2}'(@rph/[^']+)':$", re.MULTILINE)
WORKSPACE_OVERRIDE = re.compile(r'^ {2}"(@rph/[^"]+)": "(file:[^"]+)"$', re.MULTILINE)
CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64}) {2}(.+)$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_manifest() -> dict[str, object]:
    path = DIST / INSTALL_MANIFEST
    if not path.is_file():
        pytest.skip(
            f"NOT_VERIFIED: generated RPH install surface is absent at {DIST} "
            "(run `pnpm --dir rph package` to build it); static guards still apply, "
            "and the generated-artifact assertions run whenever it is present"
        )
    return json.loads(_read(path))


def test_workspace_manifests_keep_internal_dependencies_workspace_protocol() -> None:
    for name in PACKAGE_NAMES:
        manifest = json.loads(_read(RPH / "packages" / name / "package.json"))
        assert manifest["private"] is True, f"@rph/{name} must stay private"
        dependencies = manifest.get("dependencies", {})
        for dependency, specifier in dependencies.items():
            assert dependency.startswith("@rph/"), (
                f"@rph/{name} has a third-party dependency {dependency}; "
                "the private package set must remain dependency-free"
            )
            assert specifier == "workspace:*", (
                f"@rph/{name}: {dependency}={specifier!r} — only workspace:* is "
                "rewritten locally by pnpm pack; any other specifier makes the "
                "tarball resolve @rph/* on registry.npmjs.org"
            )


def test_package_script_generates_and_verifies_the_offline_install_surface() -> None:
    script = _read(RPH / "scripts" / "package.mjs")
    for marker in (
        "writeInstallSurface",
        "writeChecksums",
        "verifyInstallSurface",
        "assertNoRegistryResolution",
        "install-surface.mjs",
        "--lockfile-only",
        "--frozen-lockfile",
        "--offline",
        "VERIFY_SCRIPT_NAME",
    ):
        assert marker in script, f"rph/scripts/package.mjs must reference {marker}"


def test_install_surface_module_pins_pnpm_settings_to_the_workspace_file() -> None:
    module = _read(RPH / "scripts" / "install-surface.mjs")
    for marker in (
        INSTALL_MANIFEST,
        INSTALL_WORKSPACE,
        INSTALL_LOCKFILE,
        "overrides:",
        "file:./",
        "registry.npmjs.org",
    ):
        assert marker in module, f"install-surface.mjs must reference {marker}"
    # pnpm 11 ignores the package.json `pnpm` field; overrides must live in
    # pnpm-workspace.yaml or the transitive @rph/* deps fall back to the registry.
    assert "no longer reads" in module


def test_generated_install_surface_maps_every_package_to_a_local_tarball() -> None:
    manifest = _install_manifest()
    assert manifest["private"] is True
    assert "pnpm" not in manifest, (
        "pnpm 11 ignores package.json#pnpm; keep overrides in pnpm-workspace.yaml"
    )
    dependencies = manifest["dependencies"]
    assert isinstance(dependencies, dict)
    assert sorted(dependencies) == sorted(f"@rph/{name}" for name in PACKAGE_NAMES)
    for name, specifier in dependencies.items():
        assert isinstance(specifier, str) and specifier.startswith("file:./"), (
            f"{name} would be resolved from a registry: {specifier!r}"
        )
        assert specifier.endswith(".tgz")
        assert (DIST / specifier.removeprefix("file:./")).is_file()
    assert (DIST / INSTALL_PACKAGE).read_bytes() == (DIST / INSTALL_MANIFEST).read_bytes(), (
        "package.json and rph-install.package.json must be byte-identical"
    )


def test_generated_workspace_overrides_cover_transitive_private_packages() -> None:
    manifest = _install_manifest()
    overrides = dict(WORKSPACE_OVERRIDE.findall(_read(DIST / INSTALL_WORKSPACE)))
    assert sorted(overrides) == sorted(f"@rph/{name}" for name in PACKAGE_NAMES)
    for name, specifier in overrides.items():
        assert specifier.startswith("file:./"), f"{name} override is not local: {specifier!r}"
        assert specifier.endswith(".tgz")
    for name in manifest["dependencies"]:  # type: ignore[union-attr]
        assert name in overrides, f"no pnpm-workspace.yaml override for {name}"


def test_generated_lockfile_has_no_registry_resolution() -> None:
    _install_manifest()
    lock = _read(DIST / INSTALL_LOCKFILE)
    for marker in REGISTRY_MARKERS:
        assert marker not in lock, f"lockfile references {marker}"
    keys = RPH_LOCK_ENTRY.findall(lock)
    assert keys, "lockfile has no @rph/* entries"
    for key in keys:
        assert "@file:" in key, f"lockfile entry {key} is not pinned to a local tarball"
    for name in PACKAGE_NAMES:
        assert any(key.startswith(f"@rph/{name}@file:") for key in keys), (
            f"lockfile is missing a file: entry for @rph/{name}"
        )


def test_generated_checksums_and_artifact_index_match_the_tarballs() -> None:
    # Require the generated install surface first so a clean checkout takes the
    # explicit NOT_VERIFIED path instead of raising FileNotFoundError.
    _install_manifest()
    from_rph_scope = {"@rph/" + name for name in PACKAGE_NAMES}
    index = json.loads(_read(DIST / PACKAGE_INDEX))
    assert index["schema"] == "acp2.rph.packages.v1"
    entries = {entry["package"]: entry for entry in index["packages"]}
    assert set(entries) == from_rph_scope
    for package, entry in entries.items():
        tarball = DIST / entry["file"]
        assert tarball.is_file(), f"missing tarball for {package}"
        assert tarball.stat().st_size == entry["size"]
        assert _sha256(tarball) == entry["sha256"], f"sha256 mismatch for {package}"

    sums = {}
    for line in _read(DIST / CHECKSUMS).splitlines():
        match = CHECKSUM_LINE.match(line)
        assert match is not None, f"malformed checksum line: {line}"
        sums[match.group(2)] = match.group(1)
    surface = {path.name for path in DIST.iterdir() if path.is_file() and path.name != CHECKSUMS}
    assert surface == set(sums), "SHA256SUMS must cover every shipped file"
    for name, digest in sums.items():
        assert _sha256(DIST / name) == digest, f"sha256 mismatch for {name}"


def test_install_probe_records_the_registry_failure_as_superseded() -> None:
    """The new probe must point at the original failure without rewriting it."""
    original = ROOT / "reports" / "acp2_remediation_20261026" / "rph-package-install-probe.json"
    retest = (
        ROOT
        / "reports"
        / "acp2_remediation_20261026"
        / "rph-package-install-probe-retest-20261026.json"
    )
    assert original.is_file(), "the original failing probe must stay on disk"
    assert json.loads(_read(original))["probe"]["result"] == "FAIL"
    assert retest.is_file(), "missing the superseding retest probe"
    probe = json.loads(_read(retest))
    assert probe["schema"] == "acp2.remediation.package_install_probe.v2"
    assert probe["supersedes"] == str(original.relative_to(ROOT))
    assert probe["probe"]["result"] in {"PASS", "PASS_LOCAL_ISOLATED_SIMULATION"}
    assert "M5-01" in probe["claim_scope"], "the retest must not claim M5-01 closure"
