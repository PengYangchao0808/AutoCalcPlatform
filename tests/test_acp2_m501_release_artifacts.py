"""M5-01 release-artifact guards: one BOM, one install entry, no hidden wheels.

Fast layer (always runs):

* ``bom.current.json`` validates against the frozen ``bom.schema.json`` and
  covers every component class the release must ship (four Python
  distributions, the RPH tarball set *with* its offline install surface, the
  packaged Workbench assets, the M4-05 plugin wheels, contracts, model assets
  and docs);
* the BOM references the contract manifest digest, the evidence-coverage digest
  and every artifact by URI + sha256 + size — no secret, no fifth version;
* the committed ``static-assets.manifest.json`` is exactly what the product
  helper builds from ``frontend/``, and the helper fails closed on tamper;
* ``check_release_artifacts.py`` traverses the BOM instead of hardcoding three
  wheels, and the install entry is store-only, offline for RPH and enforces the
  root/roles mutual exclusion.

Slow layer (``--run-slow``): the clean-room end-to-end install receipt.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "reference" / "next-change" / "acp2-contracts"
BOM_PATH = CONTRACTS / "release" / "bom.current.json"
BOM_SCHEMA = CONTRACTS / "release" / "bom.schema.json"
COVERAGE_PATH = CONTRACTS / "release" / "evidence-coverage.current.json"
CONTRACT_MANIFEST = CONTRACTS / "manifest.json"
STATIC_MANIFEST = CONTRACTS / "experience" / "static-assets.manifest.json"
EXPERIENCE_MANIFEST = CONTRACTS / "experience" / "release-manifest.json"


def _store() -> Path:
    """The durable store the live BOM points at (single source: the BOM)."""
    bom = _bom()
    uri = next(entry["uri"] for entry in bom["artifacts"] if entry["kind"] == "python-wheel")
    return Path(uri).parents[2]


CHECKER = ROOT / "scripts" / "acceptance" / "check_release_artifacts.py"
INSTALLER = ROOT / "scripts" / "acceptance" / "acp2_m501_install.py"
BUILDER = ROOT / "scripts" / "release" / "build_release_bom.py"
BUILD_RELEASE = ROOT / "scripts" / "build_release.py"
SERVER = ROOT / "src" / "acp" / "api" / "server.py"
PUBLIC_TOOLS = (
    "acp_analyze",
    "acp_cancel",
    "acp_capabilities",
    "acp_get_guidance",
    "acp_preflight",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_status",
    "acp_submit",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bom() -> dict[str, Any]:
    assert BOM_PATH.is_file(), (
        "missing docs/reference/next-change/acp2-contracts/release/bom.current.json; "
        "build it with scripts/release/build_release_bom.py"
    )
    return _load(BOM_PATH)


def _store_artifact(artifact_id: str) -> tuple[dict[str, Any], Path]:
    """Resolve one BOM artifact that is generated outside the source checkout.

    The BOM is the single authority for the store path.  Missing *BOM entries*
    are a real release defect, so they still fail.  A generated artifact that
    is absent from the current host is reported as an explicit NOT_VERIFIED
    skip; when the store is mounted, every assertion below still executes.
    """
    bom = _bom()
    entry = next((item for item in bom["artifacts"] if item["id"] == artifact_id), None)
    assert entry is not None, f"the BOM must ship artifact {artifact_id!r}"
    path = Path(entry["uri"])
    if not path.is_file():
        pytest.skip(
            f"NOT_VERIFIED: BOM artifact {artifact_id!r} is built by the release "
            f"pipeline and is not available at {path} in this checkout; mount the "
            "M5-03 artifact store to run the positive store assertions"
        )
    return entry, path


def _release_scripts() -> Path:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return scripts


def _validator():
    _release_scripts()
    spec = importlib.util.spec_from_file_location(
        "m501_schema_validate", ROOT / "scripts" / "release" / "schema_validate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _static_assets():
    """Load the product helper by path (never a second checkout's ``acp``)."""
    path = ROOT / "src" / "acp" / "api" / "static_assets.py"
    spec = importlib.util.spec_from_file_location("m501_static_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fast: the BOM is the single authority
# ---------------------------------------------------------------------------


def test_bom_validates_against_the_frozen_schema() -> None:
    bom = _bom()
    errors = _validator().validate(bom, _load(BOM_SCHEMA))
    assert errors == [], "BOM violates bom.schema.json:\n" + "\n".join(errors)
    assert bom["schema"] == "acp2.release.bom.v1"
    assert bom["task_id"] == "M5-02"
    assert bom["owner"] == "REL"
    assert bom["source"]["clean"] is True


def test_bom_covers_every_required_component_class() -> None:
    bom = _bom()
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for entry in bom["artifacts"]:
        by_kind.setdefault(entry["kind"], []).append(entry)
    wheels = {entry["name"] for entry in by_kind["python-wheel"]}
    sdists = {entry["name"] for entry in by_kind["python-sdist"]}
    assert any(name.startswith("cccp-") for name in wheels)
    assert any(name.startswith("acp_worker-") for name in wheels)
    assert any(name.startswith("acp_web-") for name in wheels)
    assert any(name.startswith("acp-") and not name.startswith("acp_") for name in wheels)
    assert len(wheels) == len(sdists) == 4, "four distributions, wheel + sdist each"
    rph = by_kind["rph-package"]
    tarballs = [entry for entry in rph if entry["name"].endswith(".tgz")]
    assert len(tarballs) == 4, "RPH ships exactly its four workspace packages"
    surface = {entry["name"] for entry in rph if not entry["name"].endswith(".tgz")}
    assert {
        "rph-install.package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "SHA256SUMS",
        "rph-packages.json",
        "verify-install.mjs",
    } <= surface
    assert len(by_kind["static-asset-bundle"]) == 1
    assert len(by_kind["plugin-wheel"]) >= 1
    assert len(by_kind["contract-bundle"]) == 1
    assert len(by_kind["science-asset"]) == 1
    documents = {entry["id"] for entry in by_kind["document"]}
    # M5-03 F1/F7: the store also ships the offline dependency wheelhouse and
    # every coverage receipt, all as document components (the BOM schema's
    # frozen kind set is not extended).
    assert {"docs-release", "python-wheelhouse", "evidence-receipts"} <= documents
    toolchain = bom["toolchain"]
    assert toolchain["python"] and toolchain["node"] and toolchain["pnpm"] and toolchain["ruff"]
    assert toolchain["dsh"]["lock_sha256"]
    assert {lock["name"] for lock in toolchain["dependency_locks"]} >= {
        "rph/pnpm-lock.yaml",
        "pyproject.toml",
    }
    software = {entry["name"]: entry for entry in bom["scientific_software"]}
    assert {"ORCA", "xTB", "Shermo"} <= set(software)
    for entry in bom["scientific_software"]:
        assert entry["verified"] in ("PASS", "FAIL", "NOT_VERIFIED")


def test_bom_references_the_live_contract_and_coverage_digests() -> None:
    bom = _bom()
    manifest = _load(CONTRACT_MANIFEST)
    coverage = _load(COVERAGE_PATH)
    evidence = bom["evidence"]
    assert evidence["contract_version"] == manifest["contract_version"]
    assert evidence["contract_digest"] == manifest["contract_digest"]
    assert evidence["coverage_sha256"] == coverage["coverage_sha256"]
    assert evidence["coverage_gap_rows"] == len(coverage["gaps"])
    assert evidence["coverage_scope_rows"] == len(coverage["tasks"])
    assert evidence["reuse_rule"] in {"exact", "reused", "rerun_required", "not_verified"}


def test_bom_carries_no_secret() -> None:
    text = (
        BOM_PATH.read_text(encoding="utf-8")
        + EXPERIENCE_MANIFEST.read_text(encoding="utf-8")
        + STATIC_MANIFEST.read_text(encoding="utf-8")
    )
    lowered = text.lower()
    for marker in ("api_key", "secret", "password", "token:", "bearer ", "-----begin"):
        assert marker not in lowered, f"release documents must not contain {marker!r}"
    for pattern in (r"\bsk-[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}"):
        assert re.search(pattern, text) is None, f"release documents look like they leak {pattern}"
    real_secret = os.environ.get("ACP2_MODEL_API_KEY", "").strip()
    if real_secret:
        assert real_secret not in text, "the model credential leaked into a release document"


def test_bom_artifact_uris_are_retrievable_from_the_store() -> None:
    bom = _bom()
    store = _store()
    if not store.is_dir():
        pytest.skip(f"artifact store {store} is not present on this host")
    seen: set[str] = set()
    for entry in bom["artifacts"]:
        assert entry["id"] not in seen, f"duplicate artifact id {entry['id']}"
        seen.add(entry["id"])
        path = Path(entry["uri"])
        assert path.is_file(), f"{entry['id']} is not retrievable at {path}"
        assert path.resolve().is_relative_to(store.resolve()), (
            f"{entry['id']} points outside the durable store: {path}"
        )
        assert _sha256(path) == entry["sha256"], f"digest drift for {entry['id']}"
        assert path.stat().st_size == entry["size_bytes"], f"size drift for {entry['id']}"
        assert entry["retrievable"] is True


def test_bom_build_script_is_recorded_by_digest() -> None:
    bom = _bom()
    assert _sha256(BUILD_RELEASE) != ""
    assert _sha256(BUILDER) != ""
    recorded = {
        entry["build_script_sha256"]
        for entry in bom["artifacts"]
        if entry["kind"] in ("python-wheel", "python-sdist")
    }
    for entry in bom["artifacts"]:
        assert len(entry["build_script_sha256"]) == 64
    if _store().is_dir():
        assert _sha256(BUILD_RELEASE) in recorded, (
            "every Python artifact must record the build script digest of the build "
            "that produced it; rebuild the BOM after touching scripts/build_release.py"
        )


# ---------------------------------------------------------------------------
# Fast: Workbench static assets (product helper is the single rule)
# ---------------------------------------------------------------------------


def test_committed_static_manifest_equals_a_fresh_frontend_build() -> None:
    helper = _static_assets()
    committed = _load(STATIC_MANIFEST)
    rebuilt = helper.build_manifest(
        ROOT / "frontend",
        release_id=committed["release_id"],
        package_version=committed["package_version"],
    )
    assert rebuilt == committed, "static-assets.manifest.json is stale against frontend/"
    assert committed["schema"] == "acp2.experience.static-assets.v1"
    assert committed["counts"]["files"] == len(committed["files"]) == 76  # module_tests excluded (Node-only doubles)
    assert committed["entry_points"]["index"] == "ACP_Workbench_v2.html"


def test_static_manifest_detects_tampering_and_unlisted_files() -> None:
    helper = _static_assets()
    with tempfile.TemporaryDirectory(prefix="m501-static-") as directory:
        target = Path(directory)
        (target / "js").mkdir()
        (target / "js" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
        (target / "ACP_Workbench_v2.html").write_text("<html></html>\n", encoding="utf-8")
        (target / "ACP_Workbench.html").write_text("<html></html>\n", encoding="utf-8")
        manifest = helper.build_manifest(
            target, release_id="acp2-experience-test", package_version="0.0.1"
        )
        helper.write_manifest(target, manifest)
        assert helper.verify_manifest(target)["digest"] == manifest["digest"]
        (target / "js" / "app.js").write_text("console.log(2);\n", encoding="utf-8")
        with pytest.raises(helper.StaticAssetDigestError):
            helper.verify_manifest(target)
        (target / "js" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
        assert helper.verify_manifest(target)["digest"] == manifest["digest"]
        (target / "js" / "unlisted.js").write_text("// sneaky\n", encoding="utf-8")
        with pytest.raises(helper.StaticAssetDigestError):
            helper.verify_manifest(target)


def test_server_verifies_packaged_assets_at_startup() -> None:
    source = SERVER.read_text(encoding="utf-8")
    assert "_verify_packaged_static_assets()" in source
    assert "verify_manifest" in source
    assert "failed digest verification" in source
    assert source.index("_verify_packaged_static_assets()") < source.index("create_app")


def test_build_release_embeds_the_manifest_and_keeps_root_out_of_all() -> None:
    source = BUILD_RELEASE.read_text(encoding="utf-8")
    assert 'ROOT_ROLE = "root"' in source
    assert "def stage_root(" in source
    assert "write_manifest(" in source
    assert "build_manifest(" in source
    assert "``all`` stays the three disjoint roles" in source
    assert "ROOT_PACKAGES" in source


# ---------------------------------------------------------------------------
# Fast: RPH experience manifest + install entry
# ---------------------------------------------------------------------------


def test_experience_manifest_inventories_rph_and_web_assets() -> None:
    manifest = _load(EXPERIENCE_MANIFEST)
    static = _load(STATIC_MANIFEST)
    assert manifest["schema"] == "acp2.experience.release-manifest.v1"
    packages = {entry["name"]: entry for entry in manifest["packages"]}
    assert set(packages) == {"@rph/research", "@rph/bridge", "@rph/dsh-bundle", "@rph/cli"}
    for entry in packages.values():
        assert entry["license"] == "MIT"
        assert entry["size"] > 0 and len(entry["sha256"]) == 64
    assert packages["@rph/cli"]["bin"] == "rph"
    assert manifest["cli"]["bin"] == "rph"
    assert manifest["workspace"]["private"] is True
    assert manifest["workspace"]["root_package_json"] is False
    surface = manifest["install_surface"]
    assert surface["lockfile"] == "pnpm-lock.yaml"
    assert len(surface["files"]) >= 8
    assert "rph-cli-0.0.0.tgz" in surface["files"]
    assert manifest["static_assets"]["digest"] == static["digest"]
    assert manifest["static_assets"]["release_id"] == static["release_id"]
    assert manifest["dsh"]["vendored"] is False


def test_rph_tarballs_ship_license_notice_bin_and_profiles() -> None:
    index_path = ROOT / "rph" / "dist-packages" / "rph-packages.json"
    if not index_path.is_file():
        pytest.skip("rph/dist-packages is not built (run `pnpm --dir rph package`)")
    index = _load(index_path)
    for entry in index["packages"]:
        assert entry["license"] == "MIT"
    cli = next(entry for entry in index["packages"] if entry["name"] == "cli")
    assert cli["bin"] == "rph"
    facade = (ROOT / "rph" / "scripts" / "package.mjs").read_text(encoding="utf-8")
    assert "assertPackedContents" in facade
    assert "package/NOTICE" in facade
    assert "package/LICENSE" in facade
    for name in ("research", "bridge", "dsh-bundle", "cli"):
        assert (ROOT / "rph" / "packages" / name / "LICENSE").is_file()
        assert (ROOT / "rph" / "packages" / name / "NOTICE").is_file()


def test_check_release_artifacts_walks_the_bom_instead_of_three_wheels() -> None:
    source = CHECKER.read_text(encoding="utf-8")
    assert "--bom" in source
    assert "_verify_bom_artifacts" in source
    assert "len(wheels) == 3" not in source
    assert "assert len(wheels) == 3" not in source
    for kind in ("rph-package", "static-asset-bundle", "plugin-wheel", "contract-bundle"):
        assert kind in source
    assert "legacy_check" in source, "the pre-M5-01 directory mode stays available"
    assert "python-sdist" in source


def test_install_entry_is_store_only_and_enforces_mutual_exclusion() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    assert "ACP2_ARTIFACT_STORE" in source
    for marker in (
        "--offline",
        "--frozen-lockfile",
        "verify-install.mjs",
        "assert_exclusive",
        "DISTRIBUTION_GROUPS",
        "static_assets",
        "verify_manifest",
        '"mcp"',
        '"serve"',
        # M5-03 F1/F2/F3/F4: offline wheelhouse, real runtime refusal, --version
        # and the no-[remote] doctor row are part of the install entry.
        "--no-index",
        "wheelhouse",
        "verify_wheelhouse",
        "offline_pip_env",
        "mixed_install_probe",
        "no_remote_doctor_probe",
        "MUTUAL_EXCLUSION_CODE",
    ):
        assert marker in source, f"install entry must cover {marker}"
    assert "PYTHONPATH" in source, "the entry must prove the source tree is off sys.path"
    assert "install-receipt.json" in source


def test_install_guard_refuses_a_mixed_root_and_roles_install(monkeypatch, capsys) -> None:
    """F2: the real product refuses coexistence at all three entry points."""
    from argparse import Namespace

    import acp.install_guard as guard
    from acp.api.server import create_app
    from acp.cli import _dispatch_mcp, main

    mixed = {"acp", "cccp", "acp-worker", "acp-web"}
    monkeypatch.setattr(guard, "installed_distribution_names", lambda: set(mixed))

    refusal = guard.check_install()
    assert isinstance(refusal, guard.MutuallyExclusiveDistributions)
    assert refusal.code == "mutually_exclusive_distributions"
    assert refusal.exit_code == 78
    assert "mutually_exclusive_distributions" in str(refusal)
    assert "upgrade path" in str(refusal), "the message explains the maintainability break"
    with pytest.raises(guard.MutuallyExclusiveDistributions):
        guard.ensure_exclusive_install()

    assert guard.cli_guard() == 78
    assert "mutually_exclusive_distributions" in capsys.readouterr().err

    # CLI entry: refuses before argparse/--version.
    assert main(["--version"]) == 78
    assert "mutually_exclusive_distributions" in capsys.readouterr().err

    # MCP stdio entry: refuses before requiring --controller.
    assert _dispatch_mcp(Namespace(mcp_command="serve")) == 78
    assert "mutually_exclusive_distributions" in capsys.readouterr().err

    # ASGI entry: create_app raises the typed refusal before touching run_root.
    with pytest.raises(guard.MutuallyExclusiveDistributions):
        create_app()


def test_install_guard_allows_each_release_set_alone(monkeypatch) -> None:
    import acp.install_guard as guard

    for names in ({"acp"}, {"cccp", "acp-worker", "acp-web"}):
        monkeypatch.setattr(guard, "installed_distribution_names", lambda n=names: set(n))
        assert guard.check_install() is None
        assert guard.cli_guard() is None


def test_cli_exposes_version_and_exits_zero(capsys) -> None:
    """F3: ``acp --version`` prints the four-location version and exits 0."""
    from acp.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "acp 0.3.0" in capsys.readouterr().out


def test_doctor_reports_missing_paramiko_instead_of_crashing(monkeypatch, tmp_path, capsys) -> None:
    """F4: a root install without ``[remote]`` reports NOT_VERIFIED, no traceback."""
    from argparse import Namespace

    import acp.cli as cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_paramiko_available", lambda: False)

    exit_code = cli._handle_doctor(Namespace(node=None, log_level="INFO"))
    output = capsys.readouterr().out
    assert exit_code in (0, 1)
    assert "NOT_VERIFIED" in output, output
    assert "paramiko" in output, output
    assert "ModuleNotFoundError" not in output
    assert "Traceback" not in output


def test_sftp_module_imports_without_paramiko() -> None:
    """F4: the SFTP module's paramiko import is controlled, not unconditional."""
    code = """
import sys, importlib.abc

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "paramiko":
            raise ModuleNotFoundError("No module named 'paramiko'")
        return None

sys.meta_path.insert(0, Block())
import acp.scheduler.remote as remote
import acp.scheduler.remote.sftp as sftp
assert sftp.paramiko is None
assert "RemoteExecutionConfig" in remote.__all__
assert "FileStager" not in remote.__all__
print("ok")
"""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH",)}
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "ok" in completed.stdout


def test_wheelhouse_ships_both_platforms_and_every_requirement() -> None:
    _entry, manifest_path = _store_artifact("python-wheelhouse")
    manifest = _load(manifest_path)
    assert manifest["schema"] == "acp2.release.python-wheelhouse.v1"
    assert {"manylinux2014_x86_64", "manylinux2014_aarch64"} <= set(manifest["platforms"])
    for tag in manifest["platforms"]:
        covered = manifest["coverage"][tag]["direct_requirements"]
        assert covered == len(manifest["requirements"]), (tag, manifest["coverage"][tag])
    assert any("aarch64" in name for name in (item["name"] for item in manifest["wheels"]))
    assert any("x86_64" in name for name in (item["name"] for item in manifest["wheels"]))


def test_coverage_receipts_are_shipped_in_the_store() -> None:
    _entry, bundle = _store_artifact("evidence-receipts")
    _manifest_entry, manifest_path = _store_artifact("evidence-receipts-manifest")
    receipts = bundle.parent / "receipts"
    if not receipts.is_dir():
        pytest.skip(
            "NOT_VERIFIED: the external M5-03 evidence store has the receipt "
            f"bundle at {bundle} but has not extracted its receipts to {receipts}; "
            "run the release-store extraction to exercise the receipt digests"
        )
    manifest = _load(manifest_path)
    assert len(manifest["receipts"]) >= 39
    for item in manifest["receipts"]:
        path = receipts / item["path"]
        assert path.is_file(), item["path"]
        assert _sha256(path) == item["sha256"]


def test_public_tool_allowlist_matches_the_receipt_constant() -> None:
    from acp_services.entrypoints.mcp import TOOL_ALLOWLIST

    source = INSTALLER.read_text(encoding="utf-8")
    for tool in TOOL_ALLOWLIST:
        assert tool in source
        assert tool in PUBLIC_TOOLS


# ---------------------------------------------------------------------------
# Slow: clean-room install from the store
# ---------------------------------------------------------------------------


def _node_available() -> bool:
    tools = os.environ.get("ACP2_TOOLS", "")
    candidates = [shutil.which("node")]
    if tools:
        candidates.append(str(Path(tools) / "node-v22.19.0-linux-x64" / "bin" / "node"))
    return any(candidate and Path(candidate).is_file() for candidate in candidates)


@pytest.mark.slow
def test_clean_room_install_from_the_store(tmp_path: Path) -> None:
    """Install every component from the durable store and prove it starts."""
    store = _store()
    if not store.is_dir():
        pytest.skip(f"artifact store {store} is not present on this host")
    if shutil.which("pnpm") is None and not _node_available():
        pytest.skip("pnpm/node are not available for the RPH offline install")
    work = tmp_path / "install"
    out = tmp_path / "evidence"
    completed = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--store",
            str(store),
            "--work",
            str(work),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=2400,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-4000:]
    receipt = _load(out / "install-receipt.json")
    assert receipt["schema"] == "acp2.m5-01.install-receipt.v1"
    assert receipt["result"] == "PASS"
    assert receipt["matrix"]["mixed"]["status"] == "REFUSED"
    assert receipt["rph"]["doctor_schema"] == "acp2.rph.doctor.v1"
    assert receipt["mcp"]["protocol_version"] == "2025-06-18"
    assert receipt["mcp"]["tools"] == sorted(PUBLIC_TOOLS)
    assert receipt["workbench"]["digest"]["tamper_rejected"] is True
