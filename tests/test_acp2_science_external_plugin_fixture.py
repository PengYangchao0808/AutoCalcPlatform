"""A-22a — external ACP2 science plugin distribution fixture.

The suite pins the fixture as a *distribution*, not as a core feature:

* the wheel builds offline with the platform build tool and declares the
  stable ``cccp.science_plugins`` entry-point group (version 1) plus an MIT
  licence inside the ``.dist-info`` metadata;
* ``cccp_science_plugin.json`` lives in the installed metadata directory and
  the checked-in digests match the sources;
* the plugin imports no ``cccp.calculation.tasks`` module, no platform
  application module and no subprocess/socket module;
* the fixed native input and expected output are shipped in the wheel and the
  source-tree implementation reproduces the expected result;
* a manifest without a licence fails closed with ``PluginLicenseError``.

The wheel is installed and invoked in a separate virtual environment by the
A-22b suite; this module only proves the distributable exists and is sound.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from cccp.calculation.capabilities.contracts import CapabilityKind
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.plugin import (
    SCIENCE_PLUGIN_ENTRY_POINT_GROUP,
    SCIENCE_PLUGIN_ENTRY_POINT_VERSION,
    PluginLicenseError,
    PluginMetadata,
    PluginTrustLevel,
)
from tests._acp2_external_plugin_harness import (
    CAPABILITY_NAME,
    DISTRIBUTION_NAME,
    ENTRY_POINT_GROUP,
    ENTRY_POINT_NAME,
    EXPECTED_OUTPUT,
    FIXED_INPUT,
    FIXTURE_ROOT,
    MANIFEST,
    MODULE_NAME,
    SOURCE_ROOT,
    WHEEL_FILENAME,
    build_fixture_wheel,
)

DIST_INFO = "acp2_external_science_validator-1.0.0.dist-info"
ALLOWED_CCCP_IMPORTS = frozenset(
    {
        "cccp.calculation.context",
        "cccp.calculation.contracts",
        "cccp.calculation.errors",
        "cccp.calculation.requests",
        "cccp.calculation.results",
        "cccp.plugin.errors",
        "cccp.plugin.sdk",
    }
)
FORBIDDEN_MODULE_PREFIXES = (
    "acp.",
    "acp_services",
    "cccp.calculation.tasks",
    "cccp.workflows",
    "cccp.backends",
    "cccp.plugin.discovery",
    "cccp.plugin.host",
    "cccp.plugin.metadata",
    "subprocess",
    "multiprocessing",
    "socket",
    "ctypes",
)


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    outdir = tmp_path_factory.mktemp("a22a-wheel")
    wheel, argv, log = build_fixture_wheel(outdir)
    return {"wheel": wheel, "argv": argv, "log": log}


@pytest.fixture()
def fixture_plugin() -> object:
    """Import the fixture plugin from the source tree (no installation)."""
    source_dir = str(FIXTURE_ROOT / "src")
    sys.path.insert(0, source_dir)
    try:
        module = importlib.import_module(f"{MODULE_NAME}.plugin")
        yield module
    finally:
        sys.path.remove(source_dir)
        for name in list(sys.modules):
            if name == MODULE_NAME or name.startswith(f"{MODULE_NAME}."):
                sys.modules.pop(name, None)


# ── manifest contract ──────────────────────────────────────────────────


def test_manifest_declares_stable_entry_point_group_and_license() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    metadata = PluginMetadata.from_dict(payload)

    assert metadata.distribution == DISTRIBUTION_NAME
    assert metadata.version == "1.0.0"
    assert metadata.license == "MIT"
    assert metadata.entry_point_group_version == SCIENCE_PLUGIN_ENTRY_POINT_VERSION
    assert metadata.science_version_range == ">=1,<2"
    assert metadata.default_enabled is False
    assert metadata.trust_level is PluginTrustLevel.UNTRUSTED
    assert metadata.is_trusted is False
    assert metadata.has_verified_fixture_evidence is True
    assert metadata.resources.requires_subprocess is False
    assert metadata.resources.requires_network is False
    assert metadata.dependencies == ()

    assert len(metadata.capabilities) == 1
    capability = metadata.capabilities[0]
    assert capability.name == CAPABILITY_NAME
    assert capability.kind is CapabilityKind.PURE_ANALYSIS
    assert capability.schema == "acp2.science.external.native_singlepoint"
    assert capability.schema_version == 1
    assert capability.delegates_to_task_kind is None
    assert capability.validator_refs[0].validator_id == "check.acp2_native_singlepoint_schema"


def test_manifest_digests_match_the_fixture_sources() -> None:
    integrity = _load_integrity()
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["distribution_digest"] == integrity.distribution_digest(
        FIXTURE_ROOT / "src" / MODULE_NAME
    )
    assert payload["fixture_evidence"][0]["digest"] == integrity.fixture_evidence_digest(
        FIXTURE_ROOT / "fixtures"
    )


def test_manifest_without_license_fails_closed_and_restores() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    without_license = dict(payload)
    without_license.pop("license")

    with pytest.raises(PluginLicenseError):
        PluginMetadata.from_dict(without_license)

    restored = PluginMetadata.from_dict(payload)
    assert restored.license == "MIT"


# ── wheel build and metadata ───────────────────────────────────────────


def test_fixture_builds_with_the_platform_build_tool(built_wheel: dict[str, object]) -> None:
    wheel = built_wheel["wheel"]
    argv = built_wheel["argv"]
    assert isinstance(wheel, Path) and isinstance(argv, list)
    assert wheel.is_file()
    assert wheel.name == WHEEL_FILENAME
    assert "--no-isolation" in argv
    assert "build" in argv or "--no-index" in argv


def test_wheel_metadata_declares_entry_point_group_and_license(
    built_wheel: dict[str, object],
) -> None:
    wheel = built_wheel["wheel"]
    assert isinstance(wheel, Path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert f"{MODULE_NAME}/plugin.py" in names
        assert f"{MODULE_NAME}/data/{FIXED_INPUT.name}" in names
        assert f"{MODULE_NAME}/data/{EXPECTED_OUTPUT.name}" in names
        assert f"{DIST_INFO}/cccp_science_plugin.json" in names
        assert f"{DIST_INFO}/LICENSE" in names

        entry_points = archive.read(f"{DIST_INFO}/entry_points.txt").decode("utf-8")
        assert f"[{SCIENCE_PLUGIN_ENTRY_POINT_GROUP}]" in entry_points
        assert f"{ENTRY_POINT_NAME} = {MODULE_NAME}.plugin:PLUGIN" in entry_points

        metadata_lines = archive.read(f"{DIST_INFO}/METADATA").decode("utf-8").splitlines()
        assert "Name: acp2-external-science-validator" in metadata_lines
        assert "Version: 1.0.0" in metadata_lines
        assert "License: MIT" in metadata_lines

        manifest = json.loads(archive.read(f"{DIST_INFO}/cccp_science_plugin.json"))
        wheel_metadata = PluginMetadata.from_dict(manifest)
        assert wheel_metadata.license == "MIT"
        assert wheel_metadata.entry_point_group_version == SCIENCE_PLUGIN_ENTRY_POINT_VERSION
        assert (
            manifest["distribution_digest"]
            == json.loads(MANIFEST.read_text(encoding="utf-8"))["distribution_digest"]
        )

        _assert_record_matches(archive, names, DIST_INFO)
        assert ENTRY_POINT_GROUP == SCIENCE_PLUGIN_ENTRY_POINT_GROUP


def _assert_record_matches(archive: zipfile.ZipFile, names: set[str], dist_info: str) -> None:
    record_name = f"{dist_info}/RECORD"
    recorded: dict[str, tuple[str, str]] = {}
    for line in archive.read(record_name).decode("utf-8").splitlines():
        path, digest, size = line.rsplit(",", 2)
        recorded[path] = (digest, size)
    assert set(recorded) == names
    for name in names:
        if name == record_name:
            assert recorded[name] == ("", "")
            continue
        data = archive.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        expected = "sha256=" + encoded.rstrip(b"=").decode("ascii")
        assert recorded[name] == (expected, str(len(data)))


# ── source boundary ────────────────────────────────────────────────────


def test_plugin_source_imports_no_task_layer_or_platform_module() -> None:
    package_dir = FIXTURE_ROOT / "src" / MODULE_NAME
    modules: list[str] = []
    for path in sorted(package_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "importlib.import_module" not in text
        assert "__import__(" not in text
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)

    offenders = [
        module
        for module in modules
        if any(
            module == prefix or module.startswith(f"{prefix}.") or module.startswith(prefix)
            for prefix in FORBIDDEN_MODULE_PREFIXES
        )
    ]
    assert offenders == []

    cccp_imports = {module for module in modules if module == "cccp" or module.startswith("cccp.")}
    assert cccp_imports <= ALLOWED_CCCP_IMPORTS


def test_plugin_implementation_is_not_copied_into_core() -> None:
    markers = (MODULE_NAME, "NativeSinglePointValidator", CAPABILITY_NAME)
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(
            f"{path.relative_to(SOURCE_ROOT)}:{marker}" for marker in markers if marker in text
        )
    assert offenders == []


# ── fixed fixture behaviour (source tree) ──────────────────────────────


def test_fixed_native_input_matches_expected_output(fixture_plugin: object) -> None:
    plugin = fixture_plugin
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=FIXED_INPUT),
    )
    result = plugin.PLUGIN.invoke(request)
    expected = json.loads(EXPECTED_OUTPUT.read_text(encoding="utf-8"))

    observed = {
        "capability": result.metadata["capability"],
        "capability_schema": result.metadata["capability_schema"],
        "capability_schema_version": result.metadata["capability_schema_version"],
        "task": result.task.value,
        "status": result.status,
        "complete": result.complete,
        "symbols": list(result.symbols or ()),
        "coordinates": [list(row) for row in (result.coordinates or ())],
        "atom_order": list(result.metadata["atom_order"]),
        "energy_hartree": result.energy_hartree,
        "units": dict(result.metadata["units"]),
        "native_schema": result.metadata["native_schema"],
        "declared_atom_count": result.metadata["declared_atom_count"],
        "charge": result.metadata["charge"],
        "multiplicity": result.metadata["multiplicity"],
        "input_sha256": result.provenance.input_signature if result.provenance else None,
    }
    assert observed == expected
    assert result.provenance is not None
    assert result.provenance.backend == "acp2_external_fixture"


def test_malformed_native_input_is_rejected(fixture_plugin: object, tmp_path: Path) -> None:
    plugin = fixture_plugin
    malformed = tmp_path / "truncated.acp2n"
    malformed.write_text(
        "\n".join(
            line
            for line in FIXED_INPUT.read_text(encoding="utf-8").splitlines()
            if "atom H" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=malformed),
    )
    with pytest.raises(TaskInputError, match="declares 3 atom"):
        plugin.PLUGIN.invoke(request)


def test_fixed_input_and_expected_output_are_committed() -> None:
    assert FIXED_INPUT.is_file()
    assert EXPECTED_OUTPUT.is_file()
    expected = json.loads(EXPECTED_OUTPUT.read_text(encoding="utf-8"))
    assert expected["atom_order"] == expected["symbols"]
    assert expected["units"] == {"coordinates": "angstrom", "energy": "hartree"}
    assert expected["input_sha256"] == hashlib.sha256(FIXED_INPUT.read_bytes()).hexdigest()


def _load_integrity() -> object:
    module_path = FIXTURE_ROOT / "src" / MODULE_NAME / "integrity.py"
    spec = importlib.util.spec_from_file_location("_a22_fixture_integrity", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
