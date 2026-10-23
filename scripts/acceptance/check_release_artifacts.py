"""Validate built role ownership, assets and source-distribution round trips."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path


def payload(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), f"duplicate wheel entries: {wheel}"
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if ".dist-info/" not in name and not name.endswith("/")
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--roundtrip-wheels", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.artifacts / "release-manifest.json").read_text())
    owners: dict[str, str] = {}
    facts = {}
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((args.artifacts / name).read_bytes()).hexdigest() == digest
    wheels = sorted(args.artifacts.glob("*.whl"))
    assert len(wheels) == 3
    for wheel in wheels:
        files = payload(wheel)
        role = (
            "cccp"
            if wheel.name.startswith("cccp-")
            else ("worker" if wheel.name.startswith("acp_worker-") else "web")
        )
        staged = {
            name.removeprefix("src/"): digest
            for name, digest in manifest["roles"][role]["source_files"].items()
            if name.startswith("src/")
        }
        assert files == staged, f"wheel omitted or changed staged source/assets: {wheel}"
        expected = (
            {"cccp"}
            if wheel.name.startswith("cccp-")
            else (
                {"acp_services"}
                if wheel.name.startswith("acp_worker-")
                else {"acp", "acp_web_assets"}
            )
        )
        assert {name.split("/")[0] for name in files} == expected
        for name in files:
            assert name not in owners, f"duplicate installed file: {name}"
            owners[name] = wheel.name
        assert files == payload(args.roundtrip_wheels / wheel.name), f"sdist drift: {wheel}"
        for name, digest in files.items():
            if name.startswith("cccp/analysis/nmr/model_assets/"):
                original = args.source / "src" / name
                assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
            elif name.startswith("acp_web_assets/") and not name.endswith("__init__.py"):
                original = args.source / "frontend" / name.split("/", 1)[1]
                assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
        facts[wheel.name] = {
            "owners": sorted(expected),
            "files": len(files),
            "sdist_roundtrip": "PASS",
        }
    sdists = sorted(args.artifacts.glob("*.tar.gz"))
    assert len(sdists) == 3
    for sdist in sdists:
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            for needed in ("VERSION", "requirements-role.txt", "MANIFEST.in", "LICENSE"):
                assert any(name.endswith("/" + needed) for name in names), (
                    f"missing {needed}: {sdist}"
                )
    args.output.write_text(json.dumps({"result": "PASS", "wheels": facts}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
