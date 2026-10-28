"""Standard-library-only PEP 517 wheel backend for the A-22 plugin fixture.

The ACP2 build environment provides ``build`` but not the ``wheel`` package,
and the fixture must build offline with ``--no-isolation``.  This backend
therefore emits a pure-Python wheel directly from the source tree:

* no third-party build dependency (``[build-system].requires = []``);
* deterministic bytes (fixed archive timestamps, sorted member order);
* ``cccp_science_plugin.json`` is written into the wheel's ``.dist-info``
  directory, which is where
  :meth:`importlib.metadata.Distribution.read_text` reads it from;
* both integrity digests are recomputed from the sources at build time, so a
  stale checked-in manifest can never claim a wrong digest.

Hooks implemented: ``get_requires_for_build_wheel``,
``prepare_metadata_for_build_wheel`` and ``build_wheel``.  Source
distributions are intentionally not produced; the platform build command is
``python -m build --wheel --no-isolation`` (or ``pip wheel --no-deps``).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import zipfile
from pathlib import Path

DISTRIBUTION_NAME = "acp2-external-science-validator"
NORMALIZED_NAME = "acp2_external_science_validator"
MODULE_NAME = "acp2_fixture_validator"
VERSION = "1.0.0"
SUMMARY = "Read-only ACP2 external science plugin fixture: native single-point record validator"
LICENSE_ID = "MIT"
REQUIRES_PYTHON = ">=3.10"
ENTRY_POINT_GROUP = "cccp.science_plugins"
ENTRY_POINT_NAME = "acp2_external_validator"
ENTRY_POINT_VALUE = f"{MODULE_NAME}.plugin:PLUGIN"
WHEEL_TAG = "py3-none-any"
WHEEL_FILENAME = f"{NORMALIZED_NAME}-{VERSION}-{WHEEL_TAG}.whl"
DIST_INFO = f"{NORMALIZED_NAME}-{VERSION}.dist-info"

#: Frozen timestamp for reproducible wheels (the ZIP epoch minimum is 1980).
FIXED_DATE_TIME = (2026, 10, 23, 0, 0, 0)

ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = ROOT / "src" / MODULE_NAME
FIXTURES_DIR = ROOT / "fixtures"
#: Declarative UI form shipped in ``.dist-info`` (read metadata-only by the
#: platform: ``Distribution.read_text("cccp_ui_form.json")``).  It is not part
#: of the payload digest, so shipping it never changes the declared
#: distribution digest — only the platform's UI contract decides whether the
#: form is served.
UI_FORM_PATH = ROOT / "cccp_ui_form.json"


def _load_integrity() -> object:
    """Import the package's ``integrity`` module from the source tree."""
    module_path = PACKAGE_DIR / "integrity.py"
    spec = importlib.util.spec_from_file_location("_acp2_external_fixture_integrity", module_path)
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
        "Generator: acp2-fixture-build-backend\n"
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
        raise RuntimeError("manifest distribution does not match the build backend")
    if payload.get("version") != VERSION:
        raise RuntimeError("manifest version does not match the build backend")
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
    """Return wheel member name -> bytes, excluding RECORD."""
    members: dict[str, bytes] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        arcname = f"{MODULE_NAME}/{path.relative_to(PACKAGE_DIR).as_posix()}"
        members[arcname] = path.read_bytes()
    for path in sorted(FIXTURES_DIR.iterdir()):
        if path.is_file():
            members[f"{MODULE_NAME}/data/{path.name}"] = path.read_bytes()
    members[f"{DIST_INFO}/METADATA"] = _metadata_text().encode("utf-8")
    members[f"{DIST_INFO}/WHEEL"] = _wheel_text().encode("utf-8")
    members[f"{DIST_INFO}/entry_points.txt"] = _entry_points_text().encode("utf-8")
    members[f"{DIST_INFO}/cccp_science_plugin.json"] = _manifest_text().encode("utf-8")
    members[f"{DIST_INFO}/cccp_ui_form.json"] = UI_FORM_PATH.read_bytes()
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


# ── PEP 517 hooks ───────────────────────────────────────────────────────


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
    (target / "cccp_ui_form.json").write_bytes(UI_FORM_PATH.read_bytes())
    (target / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
    return DIST_INFO


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build the deterministic pure-Python wheel and return its filename."""
    del metadata_directory  # metadata is regenerated from the same sources
    destination = Path(wheel_directory)
    destination.mkdir(parents=True, exist_ok=True)
    _write_wheel(destination / WHEEL_FILENAME, _wheel_members())
    return WHEEL_FILENAME
