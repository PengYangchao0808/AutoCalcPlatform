"""Standard-library-only PEP 517 wheel backend for the A-23 upgrade pair.

The ACP2 build environment provides ``build`` but not the ``wheel`` package,
so each constructed distribution emits a deterministic pure-Python wheel
directly from its source tree.  ``build_meta.json`` carries the distribution
identity; the checked-in ``cccp_science_plugin.json`` carries the manifest and
its integrity digests are recomputed from the sources at build time.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
META = json.loads((ROOT / "build_meta.json").read_text(encoding="utf-8"))
DISTRIBUTION_NAME = str(META["distribution"])
NORMALIZED_NAME = DISTRIBUTION_NAME.replace("-", "_")
MODULE_NAME = str(META["module"])
VERSION = str(META["version"])
SUMMARY = str(META["summary"])
LICENSE_ID = str(META["license"])
REQUIRES_PYTHON = ">=3.10"
ENTRY_POINT_GROUP = str(META["entry_point_group"])
ENTRY_POINT_NAME = str(META["entry_point_name"])
ENTRY_POINT_VALUE = f"{MODULE_NAME}.plugin:PLUGIN"
WHEEL_TAG = "py3-none-any"
WHEEL_FILENAME = f"{NORMALIZED_NAME}-{VERSION}-{WHEEL_TAG}.whl"
DIST_INFO = f"{NORMALIZED_NAME}-{VERSION}.dist-info"
FIXED_DATE_TIME = (2026, 10, 23, 0, 0, 0)

PACKAGE_DIR = ROOT / "src" / MODULE_NAME
FIXTURES_DIR = ROOT / "fixtures"


def _load_integrity() -> object:
    module_path = PACKAGE_DIR / "integrity.py"
    spec = importlib.util.spec_from_file_location("_acp2_upgrade_fixture_integrity", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load integrity helper from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_b64(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _metadata_text() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {DISTRIBUTION_NAME}\n"
        f"Version: {VERSION}\n"
        f"Summary: {SUMMARY}\n"
        f"License: {LICENSE_ID}\n"
        "License-File: LICENSE\n"
        f"Requires-Python: {REQUIRES_PYTHON}\n"
        "Description-Content-Type: text/markdown\n"
        "\n"
        f"{readme}"
    )


def _wheel_text() -> str:
    return (
        "Wheel-Version: 1.0\n"
        "Generator: acp2-upgrade-fixture-build-backend\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {WHEEL_TAG}\n"
    )


def _entry_points_text() -> str:
    return f"[{ENTRY_POINT_GROUP}]\n{ENTRY_POINT_NAME} = {ENTRY_POINT_VALUE}\n"


def _manifest_payload() -> dict[str, object]:
    integrity = _load_integrity()
    manifest_path = ROOT / "cccp_science_plugin.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{manifest_path} must contain a JSON object")
    if payload.get("distribution") != DISTRIBUTION_NAME:
        raise RuntimeError("manifest distribution does not match build_meta.json")
    if payload.get("version") != VERSION:
        raise RuntimeError("manifest version does not match build_meta.json")
    payload["distribution_digest"] = integrity.distribution_digest(
        PACKAGE_DIR, data_dir=FIXTURES_DIR
    )
    evidence = payload.get("fixture_evidence")
    if not isinstance(evidence, list) or len(evidence) != 1:
        raise RuntimeError("manifest must declare exactly one fixture_evidence record")
    record = evidence[0]
    if not isinstance(record, dict):
        raise RuntimeError("fixture_evidence entries must be JSON objects")
    record["digest"] = integrity.fixture_evidence_digest(FIXTURES_DIR)
    return payload


def _manifest_text() -> str:
    return json.dumps(_manifest_payload(), indent=2, sort_keys=True) + "\n"


def _wheel_members() -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        members[f"{MODULE_NAME}/{path.relative_to(PACKAGE_DIR).as_posix()}"] = path.read_bytes()
    for path in sorted(FIXTURES_DIR.iterdir()):
        if path.is_file():
            members[f"{MODULE_NAME}/data/{path.name}"] = path.read_bytes()
    members[f"{DIST_INFO}/METADATA"] = _metadata_text().encode("utf-8")
    members[f"{DIST_INFO}/WHEEL"] = _wheel_text().encode("utf-8")
    members[f"{DIST_INFO}/entry_points.txt"] = _entry_points_text().encode("utf-8")
    members[f"{DIST_INFO}/cccp_science_plugin.json"] = _manifest_text().encode("utf-8")
    members[f"{DIST_INFO}/LICENSE"] = (ROOT / "LICENSE").read_bytes()
    return members


def _record_text(members: dict[str, bytes]) -> str:
    lines = [f"{name},sha256={_sha256_b64(data)},{len(data)}" for name, data in members.items()]
    lines.append(f"{DIST_INFO}/RECORD,,")
    return "\n".join(lines) + "\n"


def _write_wheel(wheel_path: Path, members: dict[str, bytes]) -> None:
    record_name = f"{DIST_INFO}/RECORD"
    record = _record_text(members).encode("utf-8")
    temporary = wheel_path.with_suffix(wheel_path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
        info = zipfile.ZipInfo(record_name, date_time=FIXED_DATE_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o644 << 16
        archive.writestr(info, record)
    os.replace(temporary, wheel_path)


def get_requires_for_build_wheel(config_settings: dict[str, object] | None = None) -> list[str]:
    """Return no build requirements (standard library only)."""
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    """Write the ``.dist-info`` metadata used by ``--no-deps`` installs."""
    target = Path(metadata_directory) / DIST_INFO
    target.mkdir(parents=True, exist_ok=True)
    (target / "METADATA").write_text(_metadata_text(), encoding="utf-8")
    (target / "WHEEL").write_text(_wheel_text(), encoding="utf-8")
    (target / "entry_points.txt").write_text(_entry_points_text(), encoding="utf-8")
    (target / "cccp_science_plugin.json").write_text(_manifest_text(), encoding="utf-8")
    (target / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    return DIST_INFO


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build the deterministic pure-Python wheel and return its filename."""
    del metadata_directory
    destination = Path(wheel_directory)
    destination.mkdir(parents=True, exist_ok=True)
    _write_wheel(destination / WHEEL_FILENAME, _wheel_members())
    return WHEEL_FILENAME
