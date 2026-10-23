#!/usr/bin/env python3
"""Check that every digest recorded in B25 release evidence matches the real file."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(path: Path, expected: str, label: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing_artifact:{label}:{path}")
        return
    actual = _sha256(path)
    if actual != expected:
        errors.append(f"digest_mismatch:{label}:expected={expected}:actual={actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    release = evidence / "release"
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    install = json.loads((evidence / "install-results.json").read_text(encoding="utf-8"))
    summary = json.loads((evidence / "summary.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    checked: dict[str, str] = {}

    manifest_artifacts = manifest["artifacts"]
    on_disk = {
        path.name: _sha256(path)
        for path in sorted(release.iterdir())
        if path.suffix in (".whl", ".gz")
    }
    if set(manifest_artifacts) != set(on_disk):
        errors.append(
            f"artifact_set_mismatch:manifest={sorted(manifest_artifacts)}:disk={sorted(on_disk)}"
        )
    for name, expected in manifest_artifacts.items():
        _check(release / name, expected, f"release-manifest:{name}", errors)
        checked[f"release-manifest:{name}"] = expected
    for name, actual in on_disk.items():
        if checked.get(f"release-manifest:{name}") == actual:
            continue
        errors.append(f"disk_digest_not_manifest:{name}:actual={actual}")

    for role, facts in install["roles"].items():
        for kind in ("wheel", "sdist"):
            name = facts[kind]
            expected = facts[f"{kind}_sha256"]
            _check(release / name, expected, f"install-results:{role}:{kind}", errors)
            checked[f"install-results:{role}:{kind}"] = expected
            if manifest_artifacts.get(name) != expected:
                errors.append(f"install_vs_manifest:{role}:{kind}:{name}")

    for key, facts in summary["artifacts"].items():
        expected = facts["sha256"]
        path = evidence / facts["path"]
        _check(path, expected, f"summary:{key}", errors)
        checked[f"summary:{key}"] = expected

    manifest_digest = manifest_artifacts
    for role, facts in install["roles"].items():
        for kind in ("wheel", "sdist"):
            name = facts[kind]
            if manifest_digest.get(name) != facts[f"{kind}_sha256"]:
                errors.append(f"manifest_install_disagree:{role}:{kind}:{name}")
    for key, facts in summary["artifacts"].items():
        name = Path(facts["path"]).name
        if name in manifest_digest and manifest_digest[name] != facts["sha256"]:
            errors.append(f"manifest_summary_disagree:{key}:{name}")

    expected_manifest_sha = summary.get("role_release_manifest_sha256")
    _check(
        release / "release-manifest.json",
        expected_manifest_sha,
        "release-manifest:self",
        errors,
    )

    result = {
        "schema": "acp2.b25.digest_consistency.v1",
        "evidence": str(evidence),
        "checked": checked,
        "artifact_count": len(checked),
        "errors": sorted(errors),
        "result": "PASS" if not errors else "FAIL",
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
