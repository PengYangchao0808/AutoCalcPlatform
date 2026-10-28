"""Validate the ACP 2.0 release artifacts by walking the single BOM.

The M0-B25 checker hardcoded "three wheels / three sdists". M5-01 replaces that
with manifest traversal: every component listed in
``docs/reference/next-change/acp2-contracts/release/bom.current.json`` is
fetched from the durable store, digest- and size-checked, and then verified
according to its ``kind``:

* ``python-wheel`` / ``python-sdist`` — role ownership, staged-source equality,
  disjoint role file sets, byte-identical root/role overlap and an
  sdist→wheel rebuild round trip;
* ``rph-package`` — tarball index, ``SHA256SUMS`` coverage, ``file:``-only
  install surface, registry-free lockfile and license/notice/bin/profile
  contents;
* ``static-asset-bundle`` — every listed file equals the byte inside the built
  ``acp-web`` wheel (not the checkout);
* ``plugin-wheel`` — third-party distribution metadata and the
  ``cccp.science_plugins`` entry point;
* ``contract-bundle`` / ``science-asset`` / ``document`` — required members.

Exit code 0 = PASS. ``--artifacts``/``--roundtrip-wheels`` remain as a legacy
directory mode for the pre-M5-01 role build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BOM_REL = "docs/reference/next-change/acp2-contracts/release/bom.current.json"
SCHEMA = "acp2.m5-01.artifact-check.v1"
ROLE_PREFIXES = (
    ("cccp-", "cccp"),
    ("acp_worker-", "worker"),
    ("acp-worker-", "worker"),
    ("acp_web-", "web"),
    ("acp-web-", "web"),
    ("acp-", "root"),
)
REGISTRY_MARKERS = ("registry.npmjs.org", "registry.yarnpkg.com", "registry.npmmirror.com")
RPH_REQUIRED = (
    "rph-install.package.json",
    "package.json",
    "pnpm-workspace.yaml",
    "pnpm-lock.yaml",
    "rph-packages.json",
    "SHA256SUMS",
    "README.md",
    "verify-install.mjs",
)
RPH_TARBALL_MEMBERS = ("package/package.json", "package/LICENSE", "package/NOTICE")
RPH_PACKAGE_PROFILE_MEMBERS = {
    "dsh-bundle": ("package/profiles/web.cordis.yml", "package/profiles/headless.cordis.yml"),
    "cli": ("package/dist/main.js",),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fail(message: str) -> None:
    raise AssertionError(message)


def payload(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            _fail(f"duplicate wheel entries: {wheel}")
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if ".dist-info/" not in name and not name.endswith("/")
        }


def role_of(name: str) -> str:
    for prefix, role in ROLE_PREFIXES:
        if name.startswith(prefix):
            return role
    _fail(f"unrecognised Python artifact name: {name}")
    return ""


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_top_level(role: str) -> set[str]:
    return {
        "cccp": {"cccp"},
        "worker": {"acp_services"},
        "web": {"acp", "acp_web_assets"},
        "root": {"cccp", "acp", "acp_services"},
    }[role]


def _verify_bom_artifacts(bom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Fetch every BOM component by URI and re-check its digest and size."""
    ids: set[str] = set()
    resolved: dict[str, dict[str, Any]] = {}
    for entry in bom["artifacts"]:
        if entry["id"] in ids:
            _fail(f"duplicate BOM artifact id: {entry['id']}")
        ids.add(entry["id"])
        path = Path(entry["uri"])
        if not entry["retrievable"]:
            _fail(f"{entry['id']}: artifact is not marked retrievable")
        if not path.is_file():
            _fail(f"{entry['id']}: missing bytes at {path}")
        actual = sha256(path)
        if actual != entry["sha256"]:
            _fail(f"{entry['id']}: sha256 {actual} != BOM {entry['sha256']}")
        if path.stat().st_size != entry["size_bytes"]:
            _fail(f"{entry['id']}: size {path.stat().st_size} != BOM {entry['size_bytes']}")
        resolved[entry["id"]] = {"entry": entry, "path": path}
    return resolved


def _build_python(store: Path | None) -> Path:
    """A python that can rebuild a wheel from an sdist (needs setuptools+wheel)."""
    candidates = []
    if store is not None:
        candidates.append(store / "tooling-build-venv" / "bin" / "python")
    candidates.append(Path(sys.executable))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import setuptools, wheel"], capture_output=True, check=False
        )
        if probe.returncode == 0:
            return candidate
    _fail(
        "no python with setuptools+wheel to rebuild sdists; the M5-01 store should "
        "carry tooling-build-venv/"
    )
    return Path(sys.executable)


def _rebuild_wheel(sdist: Path, workdir: Path, python: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(workdir),
            str(sdist),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        _fail(f"could not rebuild {sdist.name}:\n{completed.stderr[-3000:]}")
    produced = sorted(workdir.glob("*.whl"))
    if len(produced) != 1:
        _fail(f"{sdist.name} rebuilt {[path.name for path in produced]}")
    return produced[0]


def _check_python(
    artifacts: list[dict[str, Any]], workdir: Path, source: Path, python: Path
) -> dict[str, Any]:
    wheels = [entry for entry in artifacts if entry["kind"] == "python-wheel"]
    sdists = [entry for entry in artifacts if entry["kind"] == "python-sdist"]
    if len(wheels) < 4 or len(sdists) < 4:
        _fail("the BOM must carry four wheels and four sdists (3 roles + root acp)")
    by_dir: dict[Path, list[dict[str, Any]]] = {}
    for entry in wheels:
        by_dir.setdefault(Path(entry["uri"]).parent, []).append(entry)
    facts: dict[str, Any] = {}
    role_files: dict[str, dict[str, str]] = {}
    for directory, entries in sorted(by_dir.items()):
        manifest_path = directory / "release-manifest.json"
        if not manifest_path.is_file():
            _fail(f"missing release-manifest.json next to {directory}")
        manifest = _load(manifest_path)
        for entry in sorted(entries, key=lambda item: item["name"]):
            name = entry["name"]
            role = role_of(name)
            if role not in manifest["roles"]:
                _fail(f"{name} has no {role!r} role in {manifest_path}")
            staged = {
                path.removeprefix("src/"): digest
                for path, digest in manifest["roles"][role]["source_files"].items()
                if path.startswith("src/")
            }
            files = payload(Path(entry["uri"]))
            if files != staged:
                _fail(f"wheel omitted or changed staged source: {name}")
            if {path.split("/")[0] for path in files} != _expected_top_level(role):
                _fail(f"{name} does not own exactly {sorted(_expected_top_level(role))}")
            for path, digest in files.items():
                if path.startswith("cccp/analysis/nmr/model_assets/"):
                    original = source / "src" / path
                    if not original.is_file() or sha256(original) != digest:
                        _fail(f"{name}: model asset bytes differ from {original}")
                elif (
                    path.startswith("acp_web_assets/")
                    and path != "acp_web_assets/__init__.py"
                    and path != "acp_web_assets/static-assets.manifest.json"
                ):
                    original = source / "frontend" / path.split("/", 1)[1]
                    if not original.is_file() or sha256(original) != digest:
                        _fail(f"{name}: frontend asset bytes differ from {original}")
            role_files.setdefault(role, {}).update(files)
            facts[name] = {
                "role": role,
                "owners": sorted(_expected_top_level(role)),
                "files": len(files),
                "set": "roles" if role != "root" else "root",
            }
    roles = {role: role_files[role] for role in ("cccp", "worker", "web") if role in role_files}
    duplicates = set()
    seen: dict[str, str] = {}
    for role, files in roles.items():
        for path in files:
            if path in seen:
                duplicates.add(path)
            seen[path] = role
    if duplicates:
        _fail(f"role wheels double-claim installed files: {sorted(duplicates)[:5]}")
    root_files = role_files.get("root", {})
    if not root_files:
        _fail("the BOM is missing the root acp wheel")
    differing = [
        path
        for path, digest in root_files.items()
        if path in seen
        and next(files[path] for role, files in roles.items() if path in files) != digest
    ]
    if differing:
        _fail(f"root and role distributions disagree on shared bytes: {differing[:5]}")
    roundtrip: dict[str, str] = {}
    for sdist in sorted(sdists, key=lambda item: item["name"]):
        with tarfile.open(sdist["uri"]) as archive:
            names = archive.getnames()
        for needed in ("VERSION", "requirements-role.txt", "MANIFEST.in", "LICENSE"):
            if not any(name.endswith("/" + needed) for name in names):
                _fail(f"missing {needed}: {sdist['name']}")
        target_dir = workdir / sdist["name"].replace(".tar.gz", "")
        rebuilt = _rebuild_wheel(Path(sdist["uri"]), target_dir, python)
        wheel_name = f"{rebuilt.name}"
        if wheel_name not in facts:
            _fail(f"rebuilt {wheel_name} from {sdist['name']} has no matching wheel")
        if payload(rebuilt) != payload(
            Path(next(e["uri"] for e in wheels if e["name"] == wheel_name))
        ):
            _fail(f"sdist drift for {sdist['name']}")
        roundtrip[wheel_name] = "PASS"
    return {
        "wheels": facts,
        "disjoint_roles": True,
        "root_role_overlap_identical": True,
        "sdist_roundtrip": roundtrip,
    }


def _check_static(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    static = [entry for entry in artifacts if entry["kind"] == "static-asset-bundle"]
    if len(static) != 1:
        _fail(f"expected exactly one static-asset-bundle, found {len(static)}")
    manifest = _load(Path(static[0]["uri"]))
    if manifest.get("schema") != "acp2.experience.static-assets.v1":
        _fail(f"unexpected static asset schema: {manifest.get('schema')}")
    web_wheel = next(
        (
            entry
            for entry in artifacts
            if entry["kind"] == "python-wheel" and role_of(entry["name"]) == "web"
        ),
        None,
    )
    if web_wheel is None:
        _fail("the BOM has no acp-web wheel to verify the static assets against")
    files = payload(Path(web_wheel["uri"]))
    checked = 0
    for entry in manifest["files"]:
        name = f"acp_web_assets/{entry['path']}"
        if name not in files:
            _fail(f"static asset {entry['path']} is not inside {web_wheel['name']}")
        if files[name] != entry["sha256"]:
            _fail(f"static asset digest mismatch: {entry['path']}")
        if entry["size_bytes"] <= 0 or not entry["media_type"]:
            _fail(f"static asset entry is incomplete: {entry['path']}")
        checked += 1
    payload_assets = {name for name in files if name.startswith("acp_web_assets/")}
    listed = {f"acp_web_assets/{entry['path']}" for entry in manifest["files"]}
    allowed_extra = {"acp_web_assets/__init__.py", "acp_web_assets/static-assets.manifest.json"}
    unlisted = payload_assets - listed - allowed_extra
    if unlisted:
        _fail(f"acp-web wheel ships unlisted static assets: {sorted(unlisted)[:5]}")
    rule = "".join(f"{entry['path']} {entry['sha256']}\n" for entry in manifest["files"])
    if hashlib.sha256(rule.encode()).hexdigest() != manifest["digest"]:
        _fail("static asset manifest digest is not reproducible")
    return {
        "release_id": manifest["release_id"],
        "digest": manifest["digest"],
        "files": checked,
        "bytes": manifest["counts"]["bytes"],
        "verified_against_wheel": web_wheel["name"],
    }


def _check_rph(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    rph = [entry for entry in artifacts if entry["kind"] == "rph-package"]
    if not rph:
        _fail("the BOM carries no rph-package artifacts")
    directories = {Path(entry["uri"]).parent for entry in rph}
    if len(directories) != 1:
        _fail(f"rph artifacts span multiple directories: {sorted(map(str, directories))}")
    directory = directories.pop()
    for name in RPH_REQUIRED:
        if not (directory / name).is_file():
            _fail(f"RPH install surface is missing {name}")
    index = _load(directory / "rph-packages.json")
    if index.get("schema") != "acp2.rph.packages.v1":
        _fail(f"unexpected RPH index schema: {index.get('schema')}")
    if len(index["packages"]) != 4:
        _fail(f"RPH index lists {len(index['packages'])} packages, expected 4")
    tarballs = sorted(path.name for path in directory.glob("*.tgz"))
    if tarballs != sorted(entry["file"] for entry in index["packages"]):
        _fail("RPH tarball set does not match rph-packages.json")
    checksums: dict[str, str] = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([0-9a-f]{64}) {2}(.+)$", line)
        if match is None:
            _fail(f"malformed SHA256SUMS line: {line}")
        checksums[match.group(2)] = match.group(1)
    surface = {
        path.name for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS"
    }
    if surface != set(checksums):
        _fail(f"SHA256SUMS does not cover the surface: {sorted(surface ^ set(checksums))}")
    for name, digest in checksums.items():
        if sha256(directory / name) != digest:
            _fail(f"SHA256SUMS mismatch for {name}")
    manifest = _load(directory / "rph-install.package.json")
    if manifest.get("private") is not True or "pnpm" in manifest:
        _fail("RPH install manifest must stay private without a pnpm field")
    expected_names = sorted(entry["package"] for entry in index["packages"])
    if sorted(manifest["dependencies"]) != expected_names:
        _fail("RPH install manifest does not depend on exactly the four private packages")
    for name, specifier in manifest["dependencies"].items():
        if not (isinstance(specifier, str) and specifier.startswith("file:./")):
            _fail(f"{name} would resolve from a registry: {specifier!r}")
    if (directory / "package.json").read_bytes() != (
        directory / "rph-install.package.json"
    ).read_bytes():
        _fail("package.json and rph-install.package.json must be byte-identical")
    workspace = (directory / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    overrides = re.findall(r'^ {2}"(@rph/[^"]+)": "(file:[^"]+)"$', workspace, re.MULTILINE)
    if sorted(name for name, _ in overrides) != expected_names:
        _fail("pnpm-workspace.yaml overrides do not cover every private package")
    lock = (directory / "pnpm-lock.yaml").read_text(encoding="utf-8")
    for marker in REGISTRY_MARKERS:
        if marker in lock:
            _fail(f"RPH lockfile references the public registry: {marker}")
    entries = re.findall(r"^ {2}'(@rph/[^']+)':$", lock, re.MULTILINE)
    if not entries or any("@file:" not in entry for entry in entries):
        _fail("RPH lockfile is not pinned to local tarballs")
    for entry in index["packages"]:
        if entry.get("license") != "MIT":
            _fail(f"{entry['package']} does not declare its license")
        with tarfile.open(directory / entry["file"]) as archive:
            members = set(archive.getnames())
        needed = list(RPH_TARBALL_MEMBERS) + list(
            RPH_PACKAGE_PROFILE_MEMBERS.get(entry["name"], ())
        )
        missing = [name for name in needed if name not in members]
        if missing:
            _fail(f"{entry['file']} is missing {missing}")
    cli = next(entry for entry in index["packages"] if entry["name"] == "cli")
    if cli.get("bin") != "rph":
        _fail("@rph/cli must publish the rph bin")
    return {
        "directory": str(directory),
        "packages": index["packages"],
        "surface_files": sorted(surface),
        "registry_free": True,
        "contents_complete": True,
    }


def _check_plugins(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    plugins = [entry for entry in artifacts if entry["kind"] == "plugin-wheel"]
    if not plugins:
        _fail("the BOM carries no plugin-wheel artifact")
    facts: dict[str, Any] = {}
    for entry in plugins:
        with zipfile.ZipFile(entry["uri"]) as archive:
            names = archive.namelist()
            metadata = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
            entry_points = next(
                (name for name in names if name.endswith(".dist-info/entry_points.txt")), None
            )
            if metadata is None or entry_points is None:
                _fail(f"{entry['name']} is missing distribution metadata")
            text = archive.read(metadata).decode("utf-8")
            name = re.search(r"^Name: (.+)$", text, re.MULTILINE)
            version = re.search(r"^Version: (.+)$", text, re.MULTILINE)
            groups = archive.read(entry_points).decode("utf-8")
        if name is None or version is None:
            _fail(f"{entry['name']} has no Name/Version metadata")
        if "cccp.science_plugins" not in groups:
            _fail(f"{entry['name']} has no cccp.science_plugins entry point")
        facts[entry["id"]] = {
            "name": name.group(1),
            "version": version.group(1),
            "entry_point_group": "cccp.science_plugins",
        }
    return facts


def _check_bundles(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for entry in artifacts:
        if entry["kind"] == "contract-bundle":
            with tarfile.open(entry["uri"]) as archive:
                names = archive.getnames()
            for needed in (
                "acp2-contracts/manifest.json",
                "acp2-contracts/release/bom.schema.json",
            ):
                if needed not in names:
                    _fail(f"contract bundle is missing {needed}")
            if "acp2-contracts/release/bom.current.json" in names:
                _fail("contract bundle must not embed a stale BOM instance")
            facts["contracts"] = {"members": len(names)}
        elif entry["kind"] == "science-asset":
            with tarfile.open(entry["uri"]) as archive:
                names = {Path(name).name for name in archive.getnames()}
            for needed in ("NOTICE.md", "LICENSE-DP5", "atomic_reps.gz", "frag_reps.gz"):
                if needed not in names:
                    _fail(f"science asset bundle is missing {needed}")
            facts["science"] = {"members": len(names)}
        elif entry["kind"] == "document":
            if Path(entry["uri"]).stat().st_size <= 0:
                _fail(f"document artifact is empty: {entry['name']}")
            facts.setdefault("documents", []).append(entry["name"])
    return facts


def check_bom(bom_path: Path, *, source: Path) -> dict[str, Any]:
    if not bom_path.is_file():
        _fail(
            f"missing BOM: {bom_path} — build it with `python scripts/release/build_release_bom.py`"
        )
    bom = _load(bom_path)
    if bom.get("schema") != "acp2.release.bom.v1":
        _fail(f"unexpected BOM schema: {bom.get('schema')}")
    resolved = _verify_bom_artifacts(bom)
    artifacts = [item["entry"] for item in resolved.values()]
    store = Path(artifacts[0]["uri"]).parents[2]
    python_artifacts = [
        entry for entry in artifacts if entry["kind"] in ("python-wheel", "python-sdist")
    ]
    with tempfile.TemporaryDirectory(prefix="acp-artifact-check-") as directory:
        python_facts = _check_python(
            python_artifacts, Path(directory), source, _build_python(store)
        )
    return {
        "schema": SCHEMA,
        "result": "PASS",
        "bom": str(bom_path),
        "bom_sha256": sha256(bom_path),
        "task_id": bom.get("task_id"),
        "artifacts": len(artifacts),
        "artifact_kinds": sorted({entry["kind"] for entry in artifacts}),
        "python": python_facts,
        "rph": _check_rph(artifacts),
        "static_assets": _check_static(artifacts),
        "plugins": _check_plugins(artifacts),
        "bundles": _check_bundles(artifacts),
    }


def legacy_check(artifacts_dir: Path, roundtrip_dir: Path, source: Path) -> dict[str, Any]:
    """Pre-M5-01 directory mode: three role wheels, no BOM."""
    manifest = _load(artifacts_dir / "release-manifest.json")
    owners: dict[str, str] = {}
    facts: dict[str, Any] = {}
    for name, digest in manifest["artifacts"].items():
        if sha256(artifacts_dir / name) != digest:
            _fail(f"manifest digest mismatch: {name}")
    wheels = sorted(artifacts_dir.glob("*.whl"))
    if len(wheels) != 3:
        _fail(f"legacy mode expects three role wheels, found {len(wheels)}")
    for wheel in wheels:
        role = role_of(wheel.name)
        files = payload(wheel)
        staged = {
            name.removeprefix("src/"): digest
            for name, digest in manifest["roles"][role]["source_files"].items()
            if name.startswith("src/")
        }
        if files != staged:
            _fail(f"wheel omitted or changed staged source/assets: {wheel}")
        if {name.split("/")[0] for name in files} != _expected_top_level(role):
            _fail(f"{wheel.name} owns unexpected packages")
        for name in files:
            if name in owners:
                _fail(f"duplicate installed file: {name}")
            owners[name] = wheel.name
        if files != payload(roundtrip_dir / wheel.name):
            _fail(f"sdist drift: {wheel}")
        facts[wheel.name] = {"owners": sorted(_expected_top_level(role)), "files": len(files)}
    for sdist in sorted(artifacts_dir.glob("*.tar.gz")):
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
        for needed in ("VERSION", "requirements-role.txt", "MANIFEST.in", "LICENSE"):
            if not any(name.endswith("/" + needed) for name in names):
                _fail(f"missing {needed}: {sdist}")
    return {"schema": SCHEMA, "result": "PASS", "mode": "legacy", "wheels": facts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bom", type=Path, default=REPO / BOM_REL)
    parser.add_argument("--source", type=Path, default=REPO)
    parser.add_argument("--artifacts", type=Path, help="legacy three-role artifact directory")
    parser.add_argument(
        "--roundtrip-wheels", type=Path, help="legacy sdist round-trip wheel directory"
    )
    parser.add_argument("--output", type=Path, help="write the JSON receipt here as well")
    args = parser.parse_args(argv)
    if args.artifacts is not None:
        if args.roundtrip_wheels is None:
            parser.error("--artifacts requires --roundtrip-wheels")
        result = legacy_check(args.artifacts, args.roundtrip_wheels, args.source)
    else:
        result = check_bom(args.bom, source=args.source)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
