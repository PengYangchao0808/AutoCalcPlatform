"""CI workflow contract guards for the M0-02a CI/package/frontend/contract fix.

The 2026-10-26 delivery-gap audit found the pipeline structurally unable to
prove what it claimed:

* the ``rph`` job uploaded only ``rph/dist-packages/*.tgz`` without ever running
  ``pnpm package``, so the artifact step could only fail (or ship stale
  tarballs from an earlier run);
* ``frontend/js/module_tests/run_all.js`` (zero-dependency Node harness with a
  dedicated "0 tests executed" rejection path) was not wired into CI at all;
* no lane could ever run ``pytest -m contract`` (Python 3.11 + Node 22.19),
  and the ``contract`` / ``nightly`` / ``pre_release`` markers were not
  registered — which also made ``--strict-markers`` impossible;
* job wall-clock was never recorded, so "over 20 minutes means split, not
  skip" could not even be observed.

This module pins those structural facts by parsing ``.github/workflows/ci.yml``
and ``pyproject.toml`` as data. It deliberately does NOT assert that the
conformance gap is closed: the ``mcp-conformance`` job is a skeleton running a
non-empty placeholder selection until the M0-02b contract suite lands.
:func:`test_conformance_placeholder_is_never_vacuous` rejects both an empty
selector and a ``-m contract`` selector while no test actually carries that
marker, and :func:`test_conformance_lane_declares_itself_open` pins the
``NOT_CLOSED`` / ``TODO(M0-02b)`` markers so the lane cannot be cited as
evidence of closure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # py3.10 CI — tomllib is 3.11+ and tomli is not a dep
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = ROOT / "pyproject.toml"
FRONTEND_MODULE_TESTS = ROOT / "frontend" / "js" / "module_tests"

#: Markers pytest itself provides — never registered in ``pyproject.toml``.
_BUILTIN_MARKERS = frozenset(
    {
        "parametrize",
        "skip",
        "skipif",
        "xfail",
        "usefixtures",
        "filterwarnings",
        "tryfirst",
        "trylast",
    }
)

#: ``pytest.mark.<name>`` uses anywhere in the suite (decorator or helper).
_MARKER_USE_RE = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")
#: A real contract-marked test: decorator or module-level ``pytestmark``
#: assignment at line start. Prose mentions of the marker must not count.
_CONTRACT_MARKER_RE = re.compile(
    r"^\s*(?:@pytest\.mark\.contract\b|pytestmark\s*=\s*\[?pytest\.mark\.contract\b)",
    re.MULTILINE,
)
#: ``-m contract`` / ``-m "not slow and contract"`` — a POSITIVE selection.
_CONTRACT_SELECTION_RE = re.compile(r"-m\s+['\"]?(?:not slow and )?contract\b")
#: Explicit test file targets used by placeholder selections.
_TEST_TARGET_RE = re.compile(r"tests/[\w./-]+\.py")
#: `[tool.pytest.ini_options]` body (py3.10 fallback when tomllib is absent).
_PYTEST_SECTION_RE = re.compile(
    r"^\[tool\.pytest\.ini_options\]\s*$(?P<body>.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL
)
_ADDOPTS_RE = re.compile(r'^addopts\s*=\s*"(?P<value>[^"]*)"\s*$', re.MULTILINE)
_MARKER_DECL_RE = re.compile(r'^\s*"(?P<name>[A-Za-z_][A-Za-z0-9_]*):', re.MULTILINE)


def _ci() -> dict[str, Any]:
    data = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ci.yml must parse to a mapping"
    return data


def _job(name: str) -> dict[str, Any]:
    jobs = _ci().get("jobs")
    assert isinstance(jobs, dict) and jobs, "ci.yml declares no jobs"
    assert name in jobs, f"ci.yml is missing the {name!r} job"
    job = jobs[name]
    assert isinstance(job, dict), f"{name!r} job must be a mapping"
    return job


def _steps(name: str) -> list[dict[str, Any]]:
    steps = _job(name).get("steps")
    assert isinstance(steps, list) and steps, f"{name!r} job declares no steps"
    return [step for step in steps if isinstance(step, dict)]


def _flat(value: Any) -> str:
    return " ".join(str(value).split())


def _runs(name: str) -> list[str]:
    return [_flat(step.get("run", "")) for step in _steps(name)]


def _run_index(name: str, needle: str) -> int:
    hits = [index for index, text in enumerate(_runs(name)) if needle in text]
    assert hits, f"{name!r} job has no step running {needle!r}"
    return hits[0]


def _uses_index(name: str, prefix: str) -> int:
    for index, step in enumerate(_steps(name)):
        if str(step.get("uses", "")).startswith(prefix):
            return index
    raise AssertionError(f"{name!r} job has no step using {prefix!r}")


def _pytest_section_text() -> str:
    match = _PYTEST_SECTION_RE.search(PYPROJECT_PATH.read_text(encoding="utf-8"))
    assert match, "pyproject.toml has no [tool.pytest.ini_options] section"
    return match.group("body")


def _parse_pytest_section(section: str) -> tuple[str, list[str]]:
    addopts = _ADDOPTS_RE.search(section)
    return (
        addopts.group("value") if addopts else "",
        _MARKER_DECL_RE.findall(section),
    )


def _ini_addopts_and_markers() -> tuple[str, list[str]]:
    """Return ``(addopts, marker names)`` from ``[tool.pytest.ini_options]``."""
    if tomllib is not None:
        ini = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["tool"]["pytest"][
            "ini_options"
        ]
        return str(ini.get("addopts", "")), [
            str(entry).split(":", 1)[0].strip() for entry in ini["markers"]
        ]
    # py3.10 matrix leg: tomllib is 3.11+ and tomli is not installed in CI.
    return _parse_pytest_section(_pytest_section_text())


def _registered_markers() -> set[str]:
    _, markers = _ini_addopts_and_markers()
    return set(markers)


def _contract_marked_test_files() -> list[Path]:
    return [
        path
        for path in sorted((ROOT / "tests").rglob("*.py"))
        if _CONTRACT_MARKER_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    ]


def _conformance_pytest_step() -> str:
    runs = [text for text in _runs("mcp-conformance") if "pytest " in text]
    assert len(runs) == 1, f"expected exactly one pytest invocation, got {runs!r}"
    return runs[0]


def test_workflow_declares_expected_jobs() -> None:
    jobs = _ci()["jobs"]
    expected = {"test", "frontend-module-tests", "rph-detect", "rph", "mcp-conformance"}
    assert expected <= set(jobs), sorted(expected - set(jobs))


def test_python_matrix_keeps_history_checkout_and_compile() -> None:
    assert _job("test")["strategy"]["matrix"]["python-version"] == ["3.10", "3.11", "3.12"]
    assert any("python -m compileall src tests" in text for text in _runs("test"))
    checkouts = [
        step for step in _steps("test") if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert checkouts, "the matrix job must check the repository out"
    assert checkouts[0].get("with", {}).get("fetch-depth") == 0, (
        "git-history audits need the full history (fetch-depth: 0)"
    )


@pytest.mark.parametrize("job_name", ["test", "mcp-conformance"])
def test_python_jobs_install_full_extras_and_nmrglue(job_name: str) -> None:
    runs = _runs(job_name)
    assert any("pip install -e '.[dev,api,remote]'" in text for text in runs), (
        f"{job_name!r} must install the full [dev,api,remote] extras set"
    )
    assert any("nmrglue" in text for text in runs), f"{job_name!r} must install nmrglue"


def test_python_matrix_excludes_contract_marker() -> None:
    selectors = [text for text in _runs("test") if "pytest tests/ -m" in text]
    assert selectors, "the matrix job lost its pytest selection step"
    for selector in selectors:
        assert "not slow" in selector, selector
        assert "not contract" in selector, (
            "the ordinary matrix must exclude `contract`; it runs in mcp-conformance"
        )


def test_rph_job_packages_before_uploading_artifacts() -> None:
    assert _job("rph")["needs"] == "rph-detect"
    install = _run_index("rph", "pnpm install --frozen-lockfile")
    build = _run_index("rph", "pnpm build")
    test = _run_index("rph", "pnpm test")
    check = _run_index("rph", "pnpm check")
    package = _run_index("rph", "pnpm package")
    upload = _uses_index("rph", "actions/upload-artifact")
    assert install < build < test < check, "RPH steps must keep install→build→test→check order"
    assert package < upload, "`pnpm package` must run BEFORE the artifact upload"
    upload_step = _steps("rph")[upload]
    upload_with = upload_step.get("with", {})
    assert upload_with.get("path") == "rph/dist-packages/*", (
        "CI must archive the full offline install surface (manifests, lockfile, checksums and "
        "verify script), not only the tarballs"
    )
    assert upload_with.get("name") == "rph-dist-packages"
    assert upload_with.get("if-no-files-found") == "error", (
        "a missing tarball must fail the job, not upload nothing silently"
    )


def test_frontend_module_job_is_mandatory_and_non_empty() -> None:
    job = _job("frontend-module-tests")
    assert "if" not in job, "the frontend module job must not be conditionally skipped"
    runs = _runs("frontend-module-tests")
    harness = [
        index
        for index, text in enumerate(runs)
        if "node frontend/js/module_tests/run_all.js" in text
    ]
    assert harness, "frontend module tests must run `node frontend/js/module_tests/run_all.js`"
    guards = [
        index
        for index, text in enumerate(runs)
        if "frontend/js/module_tests" in text and "*.test.js" in text and "exit 1" in text
    ]
    assert guards, "the job must fail when the module test directory is empty"
    assert min(guards) < min(harness), "the non-empty guard must run before the harness"
    node_steps = [
        step
        for step in _steps("frontend-module-tests")
        if str(step.get("uses", "")).startswith("actions/setup-node")
    ]
    assert node_steps, "the frontend module job needs an explicit Node toolchain"
    for step in node_steps:
        assert str(step.get("with", {}).get("node-version", "")).startswith("22")

    # The harness itself must reject captured-but-vacuous runs, not only failures.
    source = (FRONTEND_MODULE_TESTS / "run_all.js").read_text(encoding="utf-8")
    assert "process.exit(1)" in source, "a failing module test must exit 1"
    assert "process.exit(2)" in source, "a zero-test run must exit 2"
    assert "REJECTED: no tests executed" in source
    assert sorted(FRONTEND_MODULE_TESTS.glob("*.test.js")), "no module test files on disk"


def test_mcp_conformance_job_installs_full_toolchain() -> None:
    job = _job("mcp-conformance")
    assert "if" not in job, "the conformance lane must run unconditionally"
    steps = _steps("mcp-conformance")

    python_versions = [
        str(step.get("with", {}).get("python-version", ""))
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python")
    ]
    assert python_versions == ["3.11"], python_versions
    node_versions = [
        str(step.get("with", {}).get("node-version", ""))
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-node")
    ]
    assert node_versions and node_versions[0].startswith("22.19"), node_versions
    pnpm_versions = [
        str(step.get("with", {}).get("version", ""))
        for step in steps
        if str(step.get("uses", "")).startswith("pnpm/action-setup")
    ]
    assert pnpm_versions == ["11.28.5"], pnpm_versions

    runs = _runs("mcp-conformance")
    assert any("pip install -e '.[dev,api,remote]' nmrglue" in text for text in runs)
    install = _run_index("mcp-conformance", "pnpm --dir rph install --frozen-lockfile")
    build = _run_index("mcp-conformance", "pnpm --dir rph build")
    assert install <= build, "the RPH build needs a frozen install first"
    if install == build:
        install_step = runs[install]
        assert install_step.index("install --frozen-lockfile") < install_step.index("build")
    dist_guards = [
        index for index, text in enumerate(runs) if "rph/packages/" in text and "dist" in text
    ]
    assert dist_guards, "missing RPH dist output must fail the lane"
    assert build <= min(dist_guards)


def test_conformance_placeholder_is_never_vacuous() -> None:
    step = _conformance_pytest_step()
    assert "--strict-markers" in step
    if _CONTRACT_SELECTION_RE.search(step):
        # `-m contract` only becomes a real gate once a contract-marked test exists.
        assert _contract_marked_test_files(), (
            "mcp-conformance selects `-m contract` while no test carries the marker"
        )
        return
    targets = _TEST_TARGET_RE.findall(step)
    assert targets, "the placeholder selection must name explicit test files"
    for target in targets:
        assert (ROOT / target).is_file(), f"placeholder selector target is missing: {target}"


def test_contract_selection_is_owned_by_the_conformance_lane() -> None:
    owners = [
        name
        for name in _ci()["jobs"]
        if any(_CONTRACT_SELECTION_RE.search(text) for text in _runs(name))
    ]
    assert owners in ([], ["mcp-conformance"]), (
        f"`-m contract` must be selected by mcp-conformance only, got {owners!r}"
    )


def test_conformance_lane_declares_itself_open() -> None:
    raw = CI_PATH.read_text(encoding="utf-8")
    assert "TODO(M0-02b)" in raw, "the skeleton must carry an explicit contract-suite TODO"
    runs = " ".join(_runs("mcp-conformance"))
    assert "NOT_CLOSED" in runs, "the lane must print its open status on every run"
    assert "gap closed" not in runs.lower(), "the skeleton must never claim the gap is closed"


def test_contract_nightly_pre_release_markers_are_registered() -> None:
    addopts, _ = _ini_addopts_and_markers()
    assert "--strict-markers" in addopts.split(), (
        "pyproject addopts must enable --strict-markers by default"
    )
    missing = {"contract", "nightly", "pre_release"} - _registered_markers()
    assert not missing, f"unregistered pytest markers: {sorted(missing)}"


def test_ini_reader_falls_back_to_text_without_tomllib(monkeypatch: pytest.MonkeyPatch) -> None:
    # The py3.10 matrix leg has no tomllib (and no tomli), so the text fallback
    # must stay in lockstep with the TOML reader — otherwise marker/strictness
    # checks would silently skip on that leg.
    expected = _ini_addopts_and_markers()
    monkeypatch.setattr(sys.modules[__name__], "tomllib", None)
    assert _ini_addopts_and_markers() == expected


def test_every_marker_used_by_the_suite_is_registered() -> None:
    used: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        used.update(_MARKER_USE_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    missing = sorted(used - _registered_markers() - _BUILTIN_MARKERS)
    assert not missing, (
        "markers used by the suite but not registered in pyproject.toml; "
        f"--strict-markers would fail collection: {missing}"
    )


@pytest.mark.parametrize("job_name", ["test", "mcp-conformance"])
def test_job_wall_clock_is_recorded(job_name: str) -> None:
    runs = _runs(job_name)
    assert any("ACP2_JOB_START_EPOCH" in text and "date -u +%s" in text for text in runs), (
        f"{job_name!r} must record its start epoch"
    )
    reports = [
        step
        for step in _steps(job_name)
        if "ACP2_JOB_WALL_CLOCK_SECONDS" in _flat(step.get("run", ""))
    ]
    assert reports, f"{job_name!r} must report its wall-clock"
    for step in reports:
        assert str(step.get("if", "")).startswith("always"), (
            f"{job_name!r} wall-clock report must run with `if: always()`"
        )
