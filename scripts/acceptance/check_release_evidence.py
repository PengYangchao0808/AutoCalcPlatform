#!/usr/bin/env python3
"""Check that every digest recorded in B25 release evidence matches the real file.

The original B25a evidence recorded artifact paths under ``release/`` and
``root-build/``.  Those trees were rebuilt by B25b and again by B-fix-4, so the
B25a summary now points at the preserved ``release-b25a-original/`` and
``root-build-b25a-original/`` copies.  This checker therefore accepts both
evidence shapes instead of assuming the B25a layout:

* a summary with an ``artifacts`` mapping plus a role ``release-manifest.json``
  (B25a), or an acceptance summary without artifacts (B25b);
* ``install-results.json`` as a ``{"roles": {...}}`` mapping (B25a) or as a list
  of role rows (B25b).

Every path is resolved relative to ``--evidence`` unless it is absolute or
already exists from the current working directory.  The manifest is discovered
from ``role_release_manifest`` when the summary provides it, otherwise from
``<release-dir>/release-manifest.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

RELEASE_SUFFIXES = (".whl", ".gz")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(value: str | Path, evidence: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    from_cwd = Path.cwd() / path
    if from_cwd.exists():
        return from_cwd
    return evidence / path


def _check(path: Path, expected: str, label: str, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing_artifact:{label}:{path}")
        return
    actual = _sha256(path)
    if actual != expected:
        errors.append(f"digest_mismatch:{label}:expected={expected}:actual={actual}")


def _release_dir(evidence: Path, summary: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    manifest_ref = summary.get("role_release_manifest")
    if isinstance(manifest_ref, str) and manifest_ref:
        return _resolve(manifest_ref, evidence).parent
    return evidence / "release"


def _manifest_path(evidence: Path, summary: dict[str, Any], release: Path) -> Path:
    manifest_ref = summary.get("role_release_manifest")
    if isinstance(manifest_ref, str) and manifest_ref:
        return _resolve(manifest_ref, evidence)
    return release / "release-manifest.json"


def _check_manifest(
    release: Path,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    errors: list[str],
    checked: dict[str, str],
) -> dict[str, str]:
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        errors.append("invalid_manifest:artifacts_not_a_mapping")
        return {}

    if not release.is_dir():
        errors.append(f"missing_release_dir:{release}")
        return artifacts

    on_disk = {
        path.name: _sha256(path)
        for path in sorted(release.iterdir())
        if path.is_file() and path.suffix in RELEASE_SUFFIXES
    }
    if set(artifacts) != set(on_disk):
        errors.append(f"artifact_set_mismatch:manifest={sorted(artifacts)}:disk={sorted(on_disk)}")
    for name, expected in artifacts.items():
        _check(release / name, expected, f"release-manifest:{name}", errors)
        checked[f"release-manifest:{name}"] = expected

    for name, actual in on_disk.items():
        if checked.get(f"release-manifest:{name}") == actual:
            continue
        errors.append(f"disk_digest_not_manifest:{name}:actual={actual}")
    return artifacts


def _check_summary_artifacts(
    evidence: Path,
    summary: dict[str, Any],
    manifest_artifacts: dict[str, str],
    errors: list[str],
    checked: dict[str, str],
) -> None:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        # Acceptance summaries (B25b) carry no artifact map.
        return
    for key, facts in artifacts.items():
        if not isinstance(facts, dict) or "path" not in facts or "sha256" not in facts:
            errors.append(f"invalid_summary_artifact:{key}")
            continue
        expected = facts["sha256"]
        path = evidence / facts["path"]
        _check(path, expected, f"summary:{key}", errors)
        checked[f"summary:{key}"] = expected
        name = Path(facts["path"]).name
        if name in manifest_artifacts and manifest_artifacts[name] != expected:
            errors.append(f"manifest_summary_disagree:{key}:{name}")


def _check_install_roles(
    evidence: Path,
    release: Path,
    install: Any,
    manifest_artifacts: dict[str, str],
    errors: list[str],
    checked: dict[str, str],
) -> None:
    if isinstance(install, dict) and isinstance(install.get("roles"), dict):
        for role, facts in install["roles"].items():
            if not isinstance(facts, dict):
                errors.append(f"invalid_install_result:{role}")
                continue
            for kind in ("wheel", "sdist"):
                name = facts.get(kind)
                expected = facts.get(f"{kind}_sha256")
                if not name or not expected:
                    errors.append(f"install_missing_digest:{role}:{kind}")
                    continue
                _check(release / name, expected, f"install-results:{role}:{kind}", errors)
                checked[f"install-results:{role}:{kind}"] = expected
                if manifest_artifacts.get(name) != expected:
                    errors.append(f"install_vs_manifest:{role}:{kind}:{name}")
        return

    if isinstance(install, list):
        for row in install:
            if not isinstance(row, dict) or "wheel" not in row:
                errors.append("invalid_install_result:list_row")
                continue
            role = str(row.get("role") or "unknown")
            wheel = _resolve(row["wheel"], evidence)
            name = wheel.name
            if not wheel.is_file():
                errors.append(f"missing_artifact:install-results:{role}:{wheel}")
                continue
            actual = _sha256(wheel)
            expected = manifest_artifacts.get(name)
            if expected is None:
                # Supplemental root build; recorded by digest elsewhere.
                continue
            if actual != expected:
                errors.append(
                    f"digest_mismatch:install-results:{role}:expected={expected}:actual={actual}"
                )
                continue
            checked[f"install-results:{role}:wheel"] = expected
            if row.get("install_exit") not in (None, 0, "0"):
                errors.append(f"install_exit_nonzero:{role}:{row.get('install_exit')}")
        return

    errors.append("invalid_install_results:unsupported_shape")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Evidence summary (default: <evidence>/summary.json).",
    )
    parser.add_argument(
        "--install-results",
        type=Path,
        default=None,
        help=(
            "Install results (default: summary.install_results or <evidence>/install-results.json)."
        ),
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=None,
        help=(
            "Directory holding the release artifacts "
            "(default: manifest parent or <evidence>/release)."
        ),
    )
    args = parser.parse_args()

    evidence = args.evidence.resolve()
    summary_path = (
        _resolve(args.summary, evidence) if args.summary is not None else evidence / "summary.json"
    )
    summary = _load_json(summary_path) if summary_path.is_file() else {}
    if not isinstance(summary, dict):
        summary = {}

    release_override = (
        _resolve(args.release_dir, evidence) if args.release_dir is not None else None
    )
    release = _release_dir(evidence, summary, release_override)
    manifest_path = _manifest_path(evidence, summary, release)
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    if not isinstance(manifest, dict):
        manifest = {}

    install_path: Path | None
    if args.install_results is not None:
        install_path = _resolve(args.install_results, evidence)
    elif isinstance(summary.get("install_results"), str):
        install_path = _resolve(summary["install_results"], evidence)
    else:
        install_path = evidence / "install-results.json"
    install = _load_json(install_path) if install_path.is_file() else []

    errors: list[str] = []
    checked: dict[str, str] = {}

    manifest_artifacts = _check_manifest(release, manifest, summary, errors, checked)
    _check_summary_artifacts(evidence, summary, manifest_artifacts, errors, checked)
    _check_install_roles(evidence, release, install, manifest_artifacts, errors, checked)

    expected_manifest_sha = summary.get("role_release_manifest_sha256")
    if isinstance(expected_manifest_sha, str) and expected_manifest_sha:
        _check(
            manifest_path,
            expected_manifest_sha,
            "release-manifest:self",
            errors,
        )

    result = {
        "schema": "acp2.b25.digest_consistency.v2",
        "evidence": str(evidence),
        "summary": str(summary_path),
        "install_results": str(install_path),
        "release_dir": str(release),
        "release_manifest": str(manifest_path),
        "checked": checked,
        "artifact_count": len(checked),
        "errors": sorted(errors),
        "result": "PASS" if not errors else "FAIL",
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
