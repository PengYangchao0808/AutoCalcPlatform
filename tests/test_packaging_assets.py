"""Packaging sync + NMR model-asset tests (plan todo 32).

Locks three packaging contracts:

1. ``requirements-node.txt`` stays in sync with ``pyproject.toml`` — every
   core dependency mirrored with a VERBATIM pin (no silent drift like the
   pre-todo-32 ``scipy``/``matplotlib`` gap or the stale ``numpy>=1.26``),
   and every node line declared somewhere in pyproject.
2. Capability dependencies are DECLARED: ``openpyxl`` (XLSX report) as a
   core dep, ``nmrglue``/``qml`` (Bruker/FCHL) in the ``nmr`` extra — so no
   feature can silently skip without a capability declaration.
3. ``[tool.setuptools.package-data]`` covers every file under
   ``src/acp/nmr/models/`` so wheels ship the DP5/FCHL artifacts.

The *actual* wheel namelist check (``acp/nmr/models/atomic_reps.gz`` inside
the built wheel) needs build-isolation network access, so it runs as an
evidence step (``pip wheel --no-deps``), not inside pytest; this file locks
the declarations that make that check pass.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # py3.10 CI — tomllib is 3.11+
    tomllib = pytest.importorskip("tomli")  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]

#: node-file deps that come from a pyproject *extra* (not core) by design
_EXTRA_DEPS_ON_NODE = {"nmrglue"}
#: never shipped to nodes: numpy<2-only build / unwired fragmentation path
_NODE_FORBIDDEN = {"qml", "openbabel"}
#: platform NumPy floor — must never be lowered (root AGENTS.md)
_NUMPY_CORE_PIN = ">=2.1.0"
#: critical wheel assets the acceptance check greps for
_CRITICAL_ASSETS = ("atomic_reps.gz", "frag_reps.gz")


def _load_pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirement_map(specs: list[str]) -> dict[str, str]:
    """Map normalized requirement name -> specifier (e.g. ``>=1.10``)."""
    out: dict[str, str] = {}
    for spec in specs:
        match = re.match(r"([A-Za-z0-9._-]+)\s*(.*)", spec.strip())
        assert match, f"unparseable requirement: {spec!r}"
        out[match.group(1).lower().replace("_", "-")] = match.group(2).strip()
    return out


def _core_requirements() -> dict[str, str]:
    return _requirement_map(_load_pyproject()["project"]["dependencies"])


def _nmr_extra_requirements() -> dict[str, str]:
    return _requirement_map(_load_pyproject()["project"]["optional-dependencies"]["nmr"])


def _node_requirements() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / "requirements-node.txt").read_text(encoding="utf-8").splitlines():
        payload = line.split("#", 1)[0].strip()
        if not payload:
            continue
        match = re.match(r"([A-Za-z0-9._-]+)\s*(.*)", payload)
        assert match, f"unparseable requirements-node line: {line!r}"
        out[match.group(1).lower().replace("_", "-")] = match.group(2).strip()
    return out


# ---------------------------------------------------------------------------
# 1. requirements-node.txt ↔ pyproject core deps (verbatim sync)
# ---------------------------------------------------------------------------


def test_requirements_node_mirrors_every_core_dep() -> None:
    """Every core dep must be on nodes with the IDENTICAL pin (todo 32 drift lock)."""
    core = _core_requirements()
    node = _node_requirements()
    for name, spec in core.items():
        assert node.get(name) == spec, (
            f"requirements-node.txt out of sync: core {name}{spec} "
            f"but node has {name}{node.get(name, '<absent>')}"
        )


def test_requirements_node_declares_only_pyproject_deps() -> None:
    """No stray node deps; pins match the pyproject declaration; no forbidden deps."""
    declared = {**_core_requirements(), **_nmr_extra_requirements()}
    node = _node_requirements()
    for name, spec in node.items():
        assert name in declared, (
            f"{name} in requirements-node.txt is not declared in pyproject.toml"
        )
        assert declared[name] == spec, (
            f"pin drift for {name}: node {spec} vs pyproject {declared[name]}"
        )
        assert name not in _NODE_FORBIDDEN, f"{name} must not ship to compute nodes"
    assert _EXTRA_DEPS_ON_NODE <= node.keys(), (
        "nmrglue required on nodes — remote nmr submits --bruker trees "
        "(script_gen.build_remote_nmr_cmd_tail) processed at stage 0a"
    )


def test_platform_numpy_pin_never_lowered() -> None:
    core = _core_requirements()
    assert core["numpy"] == _NUMPY_CORE_PIN
    assert _node_requirements()["numpy"] == _NUMPY_CORE_PIN


# ---------------------------------------------------------------------------
# 2. Capability declarations (XLSX / Bruker / FCHL) — no silent skips
# ---------------------------------------------------------------------------


def test_openpyxl_is_a_core_dependency() -> None:
    """XLSX writer (acp.nmr.report) must have a core-dep capability declaration."""
    core = _core_requirements()
    assert "openpyxl" in core, (
        "openpyxl must be a core dependency so nmr_assignment.xlsx can never "
        "be silently skipped in supported installs"
    )
    for name in ("scipy", "matplotlib"):
        assert name in core, f"{name} is a core runtime dep of the nmr workflows"


def test_bruker_and_fchl_capabilities_are_declared_in_nmr_extra() -> None:
    extra = _nmr_extra_requirements()
    assert "nmrglue" in extra, "Bruker capability (nmrglue) must stay declared"
    assert "qml" in extra, "FCHL capability (qml) must stay declared"


# ---------------------------------------------------------------------------
# 3. package-data coverage for acp/nmr/models/ (wheel asset ship)
# ---------------------------------------------------------------------------


def test_package_data_covers_all_nmr_model_assets() -> None:
    """Every file in the models/ data dir must match a package-data pattern."""
    patterns = _load_pyproject()["tool"]["setuptools"]["package-data"]["acp.nmr"]
    models_dir = ROOT / "src" / "acp" / "nmr" / "models"
    files = sorted(p for p in models_dir.iterdir() if p.is_file())
    assert files, f"expected DP5/FCHL artifacts in {models_dir}"
    for asset in files:
        rel = f"models/{asset.name}"
        assert any(fnmatch.fnmatch(rel, pat) for pat in patterns), (
            f"{rel} is not covered by [tool.setuptools.package-data] — "
            "wheels would silently lose this asset"
        )


def test_critical_nmr_assets_exist() -> None:
    models_dir = ROOT / "src" / "acp" / "nmr" / "models"
    for name in _CRITICAL_ASSETS:
        assert (models_dir / name).is_file(), f"missing NMR model asset: {name}"
