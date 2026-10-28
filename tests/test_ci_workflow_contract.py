"""CI workflow contract guards for the M0-02a/M0-02b CI/package/frontend/contract fix.

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
and ``pyproject.toml`` as data.

M0-02b wires the ``mcp-conformance`` lane to the real cross-stack suite
(``tests/test_acp2_mcp_sdk_contract.py``, ``-m contract``) and this module pins
that wiring: the contract selection has exactly one owner, the lane keeps a
non-contract structural subset so it is never empty, and the contract step
cannot pass vacuously — an empty selector (pytest exit 5) fails, every skip is
accounted for, and the external frozen DSH tree is an explicit workflow
variable (``ACP2_DSH_SOURCE``) whose absence is reported as ``NOT_VERIFIED``
rather than silently green. The retired skeleton markers (``TODO(M0-02b)``,
``NOT_CLOSED``) must not come back.
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
except ModuleNotFoundError:  # Python 3.10 (CI matrix); tomli is installed with pytest
    tomllib = __import__("tomli")  # type: ignore[assignment]

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


def _conformance_step(needle: str) -> dict[str, Any]:
    """Return the single ``mcp-conformance`` step whose name contains *needle*."""
    hits = [step for step in _steps("mcp-conformance") if needle in str(step.get("name", ""))]
    assert len(hits) == 1, (
        f"expected exactly one mcp-conformance step named *{needle}*, "
        f"got {[step.get('name') for step in hits]!r}"
    )
    return hits[0]


def _conformance_pytest_steps() -> list[str]:
    return [text for text in _runs("mcp-conformance") if "pytest " in text]


CONTRACT_SUITE = ROOT / "tests" / "test_acp2_mcp_sdk_contract.py"
STRUCTURAL_SUITES = (
    ROOT / "tests" / "test_acp2_runtime_mcp_process.py",
    ROOT / "tests" / "test_acp2_runtime_mcp_backends.py",
)


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


def test_conformance_structural_subset_is_non_empty_and_explicit() -> None:
    """The lane keeps a real non-contract floor that needs no external tool tree."""
    step = _conformance_step("structural MCP conformance subset")
    text = _flat(step.get("run", ""))
    assert "--strict-markers" in text
    assert '-m "not slow and not contract"' in text, text
    targets = _TEST_TARGET_RE.findall(text)
    assert targets, "the structural subset must name explicit test files"
    for target in targets:
        assert (ROOT / target).is_file(), f"structural selector target is missing: {target}"
    for suite in STRUCTURAL_SUITES:
        assert suite.is_file(), f"missing structural suite {suite}"
    # It must run before the contract gate, so a missing DSH tree can never
    # leave the job with zero executed tests.
    contract = _conformance_step("contract suite")
    runs = _runs("mcp-conformance")
    assert runs.index(_flat(step.get("run", ""))) < runs.index(_flat(contract.get("run", "")))


def test_conformance_contract_gate_runs_the_marked_suite() -> None:
    step = _conformance_step("contract suite")
    text = _flat(step.get("run", ""))
    assert "tests/test_acp2_mcp_sdk_contract.py" in text, text
    assert _CONTRACT_SELECTION_RE.search(text), text
    assert "--strict-markers" in text
    # `-m contract` only selects anything while the suite actually carries the
    # marker: pin the file that does, not just the selector text.
    assert CONTRACT_SUITE.is_file(), f"missing contract suite {CONTRACT_SUITE}"
    assert CONTRACT_SUITE in _contract_marked_test_files(), (
        f"{CONTRACT_SUITE} must carry `pytestmark = pytest.mark.contract`"
    )


def test_conformance_contract_gate_cannot_pass_by_skipping() -> None:
    step = _conformance_step("contract suite")
    text = _flat(step.get("run", ""))
    # An empty selection fails by name (pytest exit 5), never as a green gate.
    assert "-eq 5" in text and "EMPTY_CONTRACT_SELECTION" in text, text
    assert "--junitxml" in text, "the gate needs machine-readable counts, not log scraping"
    for code in ("PARTIAL_CONTRACT_SKIP", "DSH_AVAILABLE_BUT_SKIPPED", "NOT_VERIFIED"):
        assert code in text, f"missing skip-accounting branch {code!r}"
    assert "exit 1" in text
    # The external DSH tree is a declared workflow input, never an implicit
    # local path (the suite would otherwise skip and pass).
    env = step.get("env") or {}
    assert "vars.ACP2_DSH_SOURCE" in str(env.get("ACP2_DSH_SOURCE", "")), env


def test_conformance_resolves_the_frozen_dsh_tree_explicitly() -> None:
    step = _conformance_step("Resolve frozen DSH harness")
    text = _flat(step.get("run", ""))
    assert "_acp2_mcp_live_harness" in text, (
        "the preflight must reuse the suite's own prerequisite resolver"
    )
    assert "prerequisites" in text
    assert "GITHUB_ENV" in text, "availability flags must reach the contract step"
    assert "DSH_DECLARED_BUT_UNRESOLVED" in text, (
        "a declared-but-unusable DSH path must fail by name"
    )
    env = step.get("env") or {}
    assert "vars.ACP2_DSH_SOURCE" in str(env.get("ACP2_DSH_SOURCE", "")), env


def test_contract_selection_is_owned_by_the_conformance_lane() -> None:
    owners = [
        name
        for name in _ci()["jobs"]
        if any(_CONTRACT_SELECTION_RE.search(text) for text in _runs(name))
    ]
    assert owners == ["mcp-conformance"], (
        f"`-m contract` must be selected by mcp-conformance and no other job, got {owners!r}"
    )


def test_conformance_pytest_invocations_are_explicit_and_strict() -> None:
    steps = _conformance_pytest_steps()
    assert len(steps) == 2, f"expected the structural and contract runs, got {steps!r}"
    for text in steps:
        assert "--strict-markers" in text
        assert re.search(r"-m\s+['\"]?(?:not slow and (?:not )?)?contract", text), (
            f"a conformance pytest run without an explicit marker selection: {text}"
        )


def test_conformance_lane_carries_no_placeholder_or_open_banner() -> None:
    raw = CI_PATH.read_text(encoding="utf-8")
    assert "TODO(M0-02b)" not in raw, "the gate is wired; the skeleton TODO must not return"
    assert "NOT_CLOSED" not in raw, "the lane must not print an open-gap banner"
    runs = " ".join(_runs("mcp-conformance")).lower()
    assert "placeholder" not in runs, "the lane must not fall back to a placeholder selection"
    assert "gap closed" not in runs


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
