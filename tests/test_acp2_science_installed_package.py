# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""Installed-package verification for the ACP2 science distribution (A-27b).

Two independent groups of tests live here:

1. **Corpus drift** (no wheel): the in-package copy
   ``src/cccp/analysis/research/contracts/`` must equal the authoritative docs
   corpus exactly — same fourteen names, byte-identical content — and must
   match ``packaging-audit.json::contract_corpus`` (path, size, sha256).
2. **Installed probe** (clean interpreter): build or load a ``cccp`` wheel,
   extract it into a freshly created ``venv --without-pip`` interpreter, and
   run a probe from a directory outside the repository.  The probe asserts
   that ``cccp.__file__`` comes from the extract, that no source-tree path is
   on ``sys.path``, that ``acp``/``acp_services`` are absent, that science
   imports, capability discovery and the packaged contract accessor work, that
   fixtures parse from a temporary copy, and that execution without ORCA
   raises :class:`cccp.calculation.errors.BackendUnavailableError`.

The test runner may be ``$ACP2_A_PYTHON``, but the probe always uses the
separately recorded clean interpreter printed by this module.  When
``$ACP2_CCCP_WHEEL`` is unset a **local candidate** wheel is constructed from
``src/cccp`` without network access; it is never labelled as B25a.  The
platform release-candidate artifact therefore remains ``NOT_VERIFIED`` until
B25a publishes it.

``/usr/bin/orca`` on this host is the GNOME screen reader, not ORCA.  The
typed-unavailable probe therefore pins ``executables.orca.path`` to an explicit
nonexistent file: the strict config pin is authoritative, while a bare
``resolve_executable`` call can return the misleading system binary.
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
import sysconfig
import textwrap
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
AUDIT_PATH = SCIENCE / "requests" / "packaging-audit.json"
CONTRACTS_PKG = SRC / "cccp" / "analysis" / "research" / "contracts"
AUDIT_SOURCE_PREFIX = Path("docs") / "reference" / "next-change" / "acp2-contracts" / "science"

#: Absolute source root that must never leak into the probe's ``sys.path``.
SOURCE_TREE = str(SRC.resolve())

#: Third-party distributions the science closure resolves from the runner.
_DEPENDENCY_MODULES = ("numpy", "scipy", "rdkit")

#: Fixtures copied under the probe work directory (no repo-relative read).
_FIXTURE_FILES = ("task-binding-singlepoint.json", "reaction-da.json")

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


# ═══════════════════════════════════════════════════════════════════════
# Corpus drift (no wheel, no subprocess)
# ═══════════════════════════════════════════════════════════════════════


def _load_audit() -> dict[str, Any]:
    payload = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "packaging-audit.json root must be an object"
    return payload


def _audit_entries() -> list[dict[str, Any]]:
    payload = _load_audit()
    entries = payload["contract_corpus"]
    assert isinstance(entries, list), "contract_corpus must be an array"
    typed: list[dict[str, Any]] = []
    for entry in entries:
        assert isinstance(entry, dict), "each contract_corpus entry must be an object"
        typed.append(entry)
    return typed


def _docs_names() -> list[str]:
    return sorted(
        Path(entry["source_path"]).relative_to(AUDIT_SOURCE_PREFIX).as_posix()
        for entry in _audit_entries()
    )


def _package_names() -> list[str]:
    return sorted(
        path.relative_to(CONTRACTS_PKG).as_posix()
        for path in CONTRACTS_PKG.rglob("*")
        if path.is_file()
    )


def test_in_package_corpus_matches_docs_corpus_exactly() -> None:
    """The packaged set and the docs set are the same fourteen files."""
    names = _docs_names()
    assert len(names) == 14, f"packaging audit must name 14 contract files, got {len(names)}"
    assert _package_names() == names, "in-package corpus set drifted from the docs corpus"

    from cccp.analysis.research import contracts

    assert contracts.contract_root().resolve() == CONTRACTS_PKG.resolve()
    assert sorted(contracts.CONTRACT_FILE_NAMES) == names, "accessor manifest drifted"
    assert list(contracts.list_contracts()) == names, "list_contracts drifted"


def test_in_package_corpus_is_byte_identical_to_docs() -> None:
    """Every packaged file equals its docs source byte for byte (sha256)."""
    from cccp.analysis.research import contracts

    for name in _docs_names():
        docs_bytes = (SCIENCE / name).read_bytes()
        packaged_bytes = (CONTRACTS_PKG / name).read_bytes()
        assert packaged_bytes == docs_bytes, f"{name} is not byte-identical to the docs copy"
        assert contracts.read_contract(name) == docs_bytes, f"{name} accessor bytes drifted"
    print(f"corpus drift: {len(_docs_names())} files byte-identical (sha256 + raw bytes)")


def test_packaging_audit_manifest_matches_real_files() -> None:
    """``in_package_path``/``size_bytes``/``sha256`` are true for every entry."""
    entries = _audit_entries()
    assert len(entries) == 14
    for entry in entries:
        in_package_path = Path(str(entry["in_package_path"]))
        assert in_package_path.parts[:4] == ("cccp", "analysis", "research", "contracts")
        packaged = SRC / in_package_path
        assert packaged.is_file(), f"audit path missing from the tree: {in_package_path}"
        digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
        assert entry["sha256"] == digest, f"sha256 drift for {in_package_path}"
        assert entry["size_bytes"] == packaged.stat().st_size, f"size drift for {in_package_path}"
    print(f"packaging audit: {len(entries)} contract entries match the real files")


def test_contract_accessor_rejects_absolute_and_traversal_names() -> None:
    from cccp.analysis.research.contracts import ContractPathError, read_contract

    for bad in (
        "/etc/passwd",
        "../capability.schema.json",
        "examples/../../capability.schema.json",
    ):
        with pytest.raises(ContractPathError):
            read_contract(bad)
    with pytest.raises(FileNotFoundError):
        read_contract("does-not-exist.json")


# ═══════════════════════════════════════════════════════════════════════
# Local candidate wheel + clean interpreter
# ═══════════════════════════════════════════════════════════════════════


def _cccp_version() -> str:
    match = _VERSION_RE.search((SRC / "cccp" / "version.py").read_text(encoding="utf-8"))
    assert match is not None, "src/cccp/version.py has no __version__"
    return match.group(1)


def _package_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in (SRC / "cccp").rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    )


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _build_local_candidate(destination: Path) -> tuple[Path, str, str]:
    """Construct a deterministic ``cccp`` wheel from ``src/`` without network."""
    version = _cccp_version()
    dist_info = f"cccp-{version}.dist-info"
    wheel_path = destination / f"cccp-{version}-py3-none-any.whl"
    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: cccp\n"
        f"Version: {version}\n"
        "Summary: ACP2 science local candidate wheel (A-27b, not a B25a artifact)\n"
        "Requires-Python: >=3.10\n"
        "License: MIT\n"
    )
    wheel_metadata = (
        "Wheel-Version: 1.0\n"
        "Generator: acp2-a27b-local-candidate\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    )
    records: list[str] = []
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for path in _package_files():
            name = path.relative_to(SRC).as_posix()
            data = path.read_bytes()
            _write_member(archive, name, data)
            records.append(f"{name},sha256={hashlib.sha256(data).hexdigest()},{len(data)}")
        for name, text in (
            (f"{dist_info}/METADATA", metadata),
            (f"{dist_info}/WHEEL", wheel_metadata),
        ):
            data = text.encode("utf-8")
            _write_member(archive, name, data)
            records.append(f"{name},sha256={hashlib.sha256(data).hexdigest()},{len(data)}")
        records.append(f"{dist_info}/RECORD,,")
        _write_member(archive, f"{dist_info}/RECORD", ("\n".join(records) + "\n").encode("utf-8"))
    return wheel_path, "local_candidate", "manual_zip_from_src"


def _resolve_wheel(destination: Path) -> tuple[Path, str, str]:
    configured = os.environ.get("ACP2_CCCP_WHEEL", "").strip()
    if configured:
        platform_wheel = Path(configured)
        assert platform_wheel.is_file(), f"ACP2_CCCP_WHEEL is unreadable: {platform_wheel}"
        return platform_wheel, "platform_b25a", "prebuilt_artifact"
    return _build_local_candidate(destination)


def _clean_venv(venv_dir: Path) -> Path:
    completed = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"venv creation failed: {completed.stderr}"
    python = venv_dir / "bin" / "python"
    assert python.is_file(), f"clean interpreter missing: {python}"
    return python


def _venv_site_packages(venv_dir: Path) -> Path:
    minor = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for relative in (
        Path("lib") / minor / "site-packages",
        Path("lib64") / minor / "site-packages",
    ):
        candidate = venv_dir / relative
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"no site-packages directory under {venv_dir}")


def _module_root(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    assert spec is not None and spec.origin is not None, f"{name} is not installed for the runner"
    return Path(spec.origin).resolve().parents[1]


def _pythonpath_entries() -> tuple[Path, ...]:
    roots: list[Path] = [_module_root(name) for name in _DEPENDENCY_MODULES]
    roots.append(Path(sysconfig.get_path("purelib")).resolve())
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def _write_probe(work: Path) -> Path:
    probe = work / "installed_probe.py"
    probe.write_text(textwrap.dedent(_PROBE_SCRIPT), encoding="utf-8")
    return probe


def _run_probe(
    clean_python: Path,
    probe: Path,
    fixtures: Path,
    pythonpath_entries: tuple[Path, ...],
    cwd: Path,
    *,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(entry) for entry in pythonpath_entries)}
    return subprocess.run(
        [str(clean_python), str(probe), str(fixtures)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="session")
def installed_probe(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Build/load the wheel, create the clean interpreter, run the probe."""
    work = tmp_path_factory.mktemp("a27b-installed-package")
    assert REPO not in work.parents, f"probe work dir must be outside the repo: {work}"

    wheel, wheel_source, build_method = _resolve_wheel(work)
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()

    venv_dir = work / "venv"
    clean_python = _clean_venv(venv_dir)
    site_packages = _venv_site_packages(venv_dir)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site_packages)
    installed = site_packages / "cccp" / "__init__.py"
    assert installed.is_file(), f"wheel extract omitted cccp/__init__.py: {installed}"

    fixtures = work / "fixtures"
    fixtures.mkdir()
    for name in _FIXTURE_FILES:
        shutil.copyfile(SCIENCE / "examples" / name, fixtures / name)

    pythonpath_entries = _pythonpath_entries()
    probe = _write_probe(work)
    completed = _run_probe(clean_python, probe, fixtures, pythonpath_entries, work)

    record: dict[str, Any] = {
        "work_dir": str(work),
        "clean_interpreter": str(clean_python),
        "site_packages": str(site_packages),
        "wheel_path": str(wheel),
        "wheel_sha256": wheel_sha256,
        "wheel_source": wheel_source,
        "wheel_build_method": build_method,
        "platform_b25a_wheel": wheel_source == "platform_b25a",
        "pythonpath_entries": [str(entry) for entry in pythonpath_entries],
        "probe_returncode": completed.returncode,
        "probe_stdout": completed.stdout,
        "probe_stderr": completed.stderr,
        "payload": None,
    }
    assert completed.returncode == 0, (
        "installed probe failed\n"
        f"interpreter: {clean_python}\n"
        f"wheel: {wheel} ({wheel_source}, sha256={wheel_sha256})\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict), "probe must print one JSON object"
    record["payload"] = payload

    print(f"clean interpreter: {clean_python}")
    print(f"wheel: {wheel} ({wheel_source}) sha256={wheel_sha256}")
    print(f"cccp.__file__: {payload['cccp_file']}")
    print(f"sys.path: {payload['sys_path']}")
    print(f"contract root: {payload['contracts']['root']}")
    print(f"contract digests: {payload['contracts']['digests']}")
    print(f"discovery: {payload['discovery']}")
    print(f"typed unavailable: {payload['typed_unavailable']}")
    if not record["platform_b25a_wheel"]:
        print("platform_b25a_wheel=false (local candidate only; B25a stays NOT_VERIFIED)")

    try:
        yield record
    finally:
        shutil.rmtree(work, ignore_errors=True)
        print(f"cleanup receipt: removed {work}")


@pytest.fixture(scope="session")
def probe_payload(installed_probe: dict[str, Any]) -> dict[str, Any]:
    payload = installed_probe["payload"]
    assert isinstance(payload, dict), "probe payload is missing"
    return payload


# ═══════════════════════════════════════════════════════════════════════
# Installed-probe assertions
# ═══════════════════════════════════════════════════════════════════════


def test_clean_interpreter_provenance(
    installed_probe: dict[str, Any], probe_payload: dict[str, Any]
) -> None:
    """``cccp.__file__`` comes from the extract, never from the source tree."""
    site_packages = Path(installed_probe["site_packages"])
    clean_python = Path(installed_probe["clean_interpreter"])
    assert clean_python.is_file(), "clean interpreter disappeared before assertion"
    cccp_file = Path(probe_payload["cccp_file"])
    assert cccp_file.is_relative_to(site_packages), f"cccp imported from {cccp_file}"
    assert SOURCE_TREE not in str(cccp_file), f"cccp.__file__ leaked the source tree: {cccp_file}"
    for entry in probe_payload["sys_path"]:
        assert SOURCE_TREE not in str(entry), f"source tree leaked into sys.path: {entry}"
    assert Path(probe_payload["cwd"]) == Path(installed_probe["work_dir"])


def test_platform_packages_are_absent(probe_payload: dict[str, Any]) -> None:
    for name in ("acp", "acp_services"):
        observed = probe_payload["platform_imports"][name]
        assert observed["absent"] is True, f"{name} was importable in the clean interpreter"
        assert observed["error"] == "ModuleNotFoundError"


def test_science_modules_import_from_the_installed_wheel(
    installed_probe: dict[str, Any], probe_payload: dict[str, Any]
) -> None:
    site_packages = Path(installed_probe["site_packages"])
    imports = probe_payload["science_imports"]
    expected = {
        "cccp.analysis.research",
        "cccp.calculation.capabilities",
        "cccp.analysis.frequency_products",
        "cccp.analysis.nmr.comparison",
        "cccp.analysis.ensemble.science",
    }
    assert set(imports) == expected
    for name, origin in imports.items():
        assert origin, f"{name} has no __file__"
        assert Path(origin).is_relative_to(site_packages), f"{name} came from {origin}"


def test_capability_discovery_without_software(probe_payload: dict[str, Any]) -> None:
    discovery = probe_payload["discovery"]
    assert discovery["builtin_ids"] == [
        "electronic.energy",
        "geometry.optimize",
        "vibration.analyze",
        "vibration.compute",
    ]
    assert set(discovery["builtin_ids"]) <= set(discovery["domain_ids"])
    assert discovery["unknown"]["supported"] is False
    assert discovery["unknown"]["error_code"] == "unknown_implementation"


def test_contract_corpus_ships_in_the_installed_wheel(
    installed_probe: dict[str, Any], probe_payload: dict[str, Any]
) -> None:
    contracts = probe_payload["contracts"]
    root = Path(contracts["root"])
    site_packages = Path(installed_probe["site_packages"])
    assert root == site_packages / "cccp" / "analysis" / "research" / "contracts"

    expected = {
        Path(str(entry["in_package_path"]))
        .relative_to("cccp/analysis/research/contracts")
        .as_posix(): entry
        for entry in _audit_entries()
    }
    assert set(contracts["listed"]) == set(expected), "installed corpus set differs from the audit"
    assert len(contracts["listed"]) == 14
    for name, entry in expected.items():
        assert contracts["digests"][name] == entry["sha256"], f"installed digest drift for {name}"


def test_fixtures_parse_from_a_copy_outside_the_repo(probe_payload: dict[str, Any]) -> None:
    fixtures = probe_payload["fixtures"]
    fixture_dir = Path(fixtures["dir"])
    assert fixture_dir.name == "fixtures"
    assert not fixture_dir.is_relative_to(REPO), "fixtures must be read from a temp copy"
    assert fixtures["request_task"] == "singlepoint"
    assert fixtures["reaction_id"] == "reaction.diels_alder.butadiene_ethene_cyclohexene.v1"
    assert fixtures["reaction_schema_version"] == 1


def test_execution_without_orca_raises_typed_unavailable(probe_payload: dict[str, Any]) -> None:
    unavailable = probe_payload["typed_unavailable"]
    assert unavailable["raised"] is True, "execution without ORCA did not raise"
    assert unavailable["is_typed"] is True, (
        "execution without ORCA raised an untyped error: "
        f"{unavailable['class']} from {unavailable['module']}: {unavailable['message']}"
    )
    assert unavailable["class"] == "BackendUnavailableError"
    assert unavailable["module"] == "cccp.calculation.errors"
    assert unavailable["class"] not in {"ImportError", "ModuleNotFoundError", "FileNotFoundError"}
    assert "/nonexistent/orca" in unavailable["message"]
    print(
        "typed unavailable: "
        f"{unavailable['module']}.{unavailable['class']}: {unavailable['message']}"
    )
    print(f"misleading bare resolution of 'orca': {unavailable['misleading_resolution']}")


def test_wheel_source_is_explicitly_recorded(installed_probe: dict[str, Any]) -> None:
    if installed_probe["wheel_source"] == "platform_b25a":
        assert installed_probe["platform_b25a_wheel"] is True
    else:
        assert installed_probe["wheel_source"] == "local_candidate"
        assert installed_probe["platform_b25a_wheel"] is False
        assert installed_probe["wheel_build_method"] == "manual_zip_from_src"
    assert len(str(installed_probe["wheel_sha256"])) == 64


# ═══════════════════════════════════════════════════════════════════════
# Failure QA: delete a packaged contract from the extracted wheel
# ═══════════════════════════════════════════════════════════════════════


def test_missing_packaged_contract_fails_the_probe(
    installed_probe: dict[str, Any], tmp_path: Path
) -> None:
    """Removing one packaged asset must fail loudly; restoring must go green."""
    site_packages = Path(installed_probe["site_packages"])
    clean_python = Path(installed_probe["clean_interpreter"])
    pythonpath_entries = tuple(Path(entry) for entry in installed_probe["pythonpath_entries"])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(entry) for entry in pythonpath_entries)}
    target = (
        site_packages / "cccp" / "analysis" / "research" / "contracts" / "capability.schema.json"
    )
    backup = tmp_path / "capability.schema.json.bak"
    probe_snippet = (
        "import cccp.analysis.research.contracts as contracts;"
        "print(len(contracts.read_contract('capability.schema.json')))"
    )

    shutil.copy2(target, backup)
    try:
        target.unlink()
        broken = subprocess.run(
            [str(clean_python), "-c", probe_snippet],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert broken.returncode != 0, "removing a packaged contract must fail the probe"
        assert "FileNotFoundError" in broken.stderr, broken.stderr
        assert "contract 'capability.schema.json' is not present" in broken.stderr, broken.stderr
        print(f"failure QA: missing asset detected -> {broken.stderr.strip().splitlines()[-1]}")
    finally:
        shutil.copy2(backup, target)

    restored = subprocess.run(
        [str(clean_python), "-c", probe_snippet],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert restored.returncode == 0, f"restored corpus must pass: {restored.stderr}"
    print(f"failure QA: restored corpus reads {restored.stdout.strip()} bytes")


# ═══════════════════════════════════════════════════════════════════════
# Probe executed by the clean interpreter (stdlib + installed wheel only)
# ═══════════════════════════════════════════════════════════════════════


_PROBE_SCRIPT = """\
import hashlib
import json
import sys
from pathlib import Path

fixture_dir = Path(sys.argv[1])
payload = {}

import cccp

payload["cwd"] = str(Path.cwd())
payload["cccp_file"] = str(Path(cccp.__file__).resolve())
payload["sys_path"] = [str(entry) for entry in sys.path]
payload["executable"] = str(Path(sys.executable).resolve())

platform_imports = {}
for name in ("acp", "acp_services"):
    try:
        module = __import__(name)
    except ModuleNotFoundError as exc:
        platform_imports[name] = {
            "absent": True,
            "error": type(exc).__name__,
            "message": str(exc),
        }
    else:
        platform_imports[name] = {
            "absent": False,
            "error": None,
            "file": getattr(module, "__file__", None),
        }
payload["platform_imports"] = platform_imports

science_imports = {}
for name in (
    "cccp.analysis.research",
    "cccp.calculation.capabilities",
    "cccp.analysis.frequency_products",
    "cccp.analysis.nmr.comparison",
    "cccp.analysis.ensemble.science",
):
    module = __import__(name, fromlist=["__name__"])
    science_imports[name] = str(Path(module.__file__).resolve())
payload["science_imports"] = science_imports

from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.capabilities.domains import domain_registry

builtin = builtin_registry()
domain = domain_registry()
unknown = builtin.query("science.unknown_capability")
payload["discovery"] = {
    "builtin_ids": list(builtin.capability_ids),
    "domain_ids": list(domain.capability_ids),
    "unknown": {
        "supported": unknown.supported,
        "error_code": None if unknown.error is None else unknown.error.code,
    },
}

from cccp.analysis.research import contracts

listed = list(contracts.list_contracts())
payload["contracts"] = {
    "root": str(contracts.contract_root().resolve()),
    "listed": listed,
    "digests": {
        name: hashlib.sha256(contracts.read_contract(name)).hexdigest() for name in listed
    },
}

from cccp.analysis.research.reaction import Reaction
from cccp.calculation.requests import TaskRequest

singlepoint = json.loads(
    (fixture_dir / "task-binding-singlepoint.json").read_text(encoding="utf-8")
)
request = TaskRequest.from_dict(singlepoint["binding_output"]["request"])
reaction = Reaction.from_dict(
    json.loads((fixture_dir / "reaction-da.json").read_text(encoding="utf-8"))
)
payload["fixtures"] = {
    "dir": str(fixture_dir.resolve()),
    "request_task": request.task.value,
    "request_method": request.level.method,
    "reaction_id": reaction.reaction_id,
    "reaction_schema_version": reaction.schema_version,
}

from cccp.calculation.capabilities.task_binding import bind_capability
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import BackendUnavailableError
from cccp.software import resolve_executable

misleading = resolve_executable("orca", configured_path="/nonexistent/orca")
binding = bind_capability("electronic.energy", request)
context = TaskContext(config={"executables": {"orca": {"path": "/nonexistent/orca"}}})
try:
    binding.execute(context=context)
except BackendUnavailableError as exc:
    payload["typed_unavailable"] = {
        "raised": True,
        "is_typed": True,
        "class": type(exc).__name__,
        "module": type(exc).__module__,
        "message": str(exc),
        "misleading_resolution": None if misleading is None else str(misleading),
    }
except Exception as exc:
    payload["typed_unavailable"] = {
        "raised": True,
        "is_typed": False,
        "class": type(exc).__name__,
        "module": type(exc).__module__,
        "message": str(exc),
        "misleading_resolution": None if misleading is None else str(misleading),
    }
else:
    payload["typed_unavailable"] = {
        "raised": False,
        "is_typed": False,
        "class": None,
        "module": None,
        "message": "",
        "misleading_resolution": None if misleading is None else str(misleading),
    }

print(json.dumps(payload, sort_keys=True))
"""


# ═══════════════════════════════════════════════════════════════════════
# Documented examples (A-28): run only from the installed wheel
# ═══════════════════════════════════════════════════════════════════════

DOCUMENTED_EXAMPLE_FILES = (
    "singlepoint_public_api.py",
    "compile_recipe.py",
    "read_report.py",
    "external_plugin.py",
)
DOCUMENTED_EXAMPLE_MARKERS = {
    "singlepoint_public_api.py": '"preflight_outcome": "ready"',
    "compile_recipe.py": '"canonical_json_equal": true',
    "read_report.py": '"overall_claim_status":',
    "external_plugin.py": '"auto_enabled": false',
}
DOCUMENTED_EXAMPLE_INPUTS = {
    "read_report.py": (
        "supported_valid.json",
        "tests/fixtures/acp2/science/contracts/evidence/supported_valid.json",
    ),
    "external_plugin.py": (
        "cccp_science_plugin.json",
        "tests/fixtures/acp2/science/external_plugin/cccp_science_plugin.json",
    ),
}
DOCUMENTED_EXAMPLE_DIR = SCIENCE / "examples"


def _example_env(installed_probe: dict[str, Any]) -> dict[str, str]:
    entries = tuple(Path(entry) for entry in installed_probe["pythonpath_entries"])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = os.pathsep.join(str(entry) for entry in entries)
    return env


def _copy_documented_examples(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for name in DOCUMENTED_EXAMPLE_FILES:
        shutil.copyfile(DOCUMENTED_EXAMPLE_DIR / name, destination / name)
    return destination


def _copy_example_input(name: str, relative: str, destination: Path) -> Path:
    source = REPO / relative
    assert source.is_file(), f"example input is missing: {source}"
    target = destination / name
    shutil.copyfile(source, target)
    return target


def _run_documented_example(
    installed_probe: dict[str, Any],
    work: Path,
    name: str,
    extra_args: list[str],
) -> subprocess.CompletedProcess[str]:
    env = _example_env(installed_probe)
    assert SOURCE_TREE not in env["PYTHONPATH"]
    return subprocess.run(
        [installed_probe["clean_interpreter"], str(work / name), *extra_args],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_documented_examples_have_a_help_path(
    installed_probe: dict[str, Any], tmp_path: Path
) -> None:
    work = _copy_documented_examples(tmp_path / "examples")
    for name in DOCUMENTED_EXAMPLE_FILES:
        completed = _run_documented_example(installed_probe, work, name, ["--help"])
        assert completed.returncode == 0, f"{name} --help failed: {completed.stderr}"
        assert "usage:" in completed.stdout.lower(), f"{name} printed no usage line"


def test_documented_examples_run_from_the_installed_wheel(
    installed_probe: dict[str, Any], tmp_path: Path
) -> None:
    work = _copy_documented_examples(tmp_path / "examples")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    input_paths = {
        local: _copy_example_input(local, relative, inputs)
        for local, relative in DOCUMENTED_EXAMPLE_INPUTS.values()
    }
    invocations = {
        "singlepoint_public_api.py": [],
        "compile_recipe.py": [],
        "read_report.py": ["--report", str(input_paths["supported_valid.json"])],
        "external_plugin.py": ["--manifest", str(input_paths["cccp_science_plugin.json"])],
    }
    for name in DOCUMENTED_EXAMPLE_FILES:
        completed = _run_documented_example(installed_probe, work, name, invocations[name])
        assert completed.returncode == 0, (
            f"{name} failed in the clean interpreter\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        assert DOCUMENTED_EXAMPLE_MARKERS[name] in completed.stdout, (
            f"{name} did not print {DOCUMENTED_EXAMPLE_MARKERS[name]!r}"
        )
        assert "Traceback" not in completed.stderr
        print(f"documented example {name}: exit 0, marker {DOCUMENTED_EXAMPLE_MARKERS[name]}")


def test_documented_examples_source_has_no_repo_or_placeholder_paths() -> None:
    forbidden = ("/ACPServer", "/home/", "/tmp/")
    for name in DOCUMENTED_EXAMPLE_FILES:
        text = (DOCUMENTED_EXAMPLE_DIR / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{name} contains a placeholder/external path: {token}"
        assert "python -m cccp" not in text
        assert "import acp" not in text and "import acp_services" not in text
        print(f"documented example {name}: no placeholder path, no platform import")


def test_documented_examples_fail_when_a_dependency_is_missing(
    installed_probe: dict[str, Any], tmp_path: Path
) -> None:
    work = _copy_documented_examples(tmp_path / "examples")
    originals = {
        name: hashlib.sha256((work / name).read_bytes()).hexdigest()
        for name in DOCUMENTED_EXAMPLE_FILES
    }
    mutated = work / "mutated_example.py"
    mutated.write_text(
        "import not_installed_module\n"
        "from cccp.calculation.capabilities.builtin import builtin_registry\n"
        "print(builtin_registry().capability_ids)\n",
        encoding="utf-8",
    )
    env = _example_env(installed_probe)
    completed = subprocess.run(
        [installed_probe["clean_interpreter"], str(mutated)],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode != 0, "an unimportable dependency must fail the example"
    assert "ModuleNotFoundError" in completed.stderr, completed.stderr
    assert "not_installed_module" in completed.stderr
    print(f"failure QA: mutated example exits {completed.returncode} with ModuleNotFoundError")

    mutated.unlink()
    for name in DOCUMENTED_EXAMPLE_FILES:
        restored = hashlib.sha256((work / name).read_bytes()).hexdigest()
        assert restored == originals[name], f"{name} changed during the failure probe"
    rerun = _run_documented_example(installed_probe, work, "singlepoint_public_api.py", [])
    assert rerun.returncode == 0, f"restored example must pass: {rerun.stderr}"
    print("failure QA: original examples restored byte-identical and green")
