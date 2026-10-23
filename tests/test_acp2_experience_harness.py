"""T-07 self-tests for the C experience acceptance harness.

The named matrix is the criterion, not the test count:

* ``--help`` has no side effect;
* the three modes are mutually exclusive and the two tool modes need no
  scenario arguments;
* a scenario may carry ``--phase``/``--mode``/``--subcase``;
* dry-run writes ``NOT_VERIFIED`` only and never PASS;
* unknown scenario is 3, a missing prerequisite is 2 and an executed
  assertion failure is 1;
* zero assertions can never PASS;
* ``evidence_kind=live`` carrying a fake transport/backend/model FAILs;
* redaction, total timeout, append-only evidence and receipt verification;
* ``--mode``/``--subcase`` legality and the five P0 scenario bodies.

Everything is local: no network, no model, no DSH, no QC and no Chromium.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience.py"
SCENARIOS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience_scenarios.py"
CONTRACTS_DIR = REPO_ROOT / "docs/reference/next-change/acp2-contracts/experience"
ROUTE_PROBE = REPO_ROOT / "scripts/acceptance/acp2_route_probe.sh"
ACCEPTANCE_ENV = Path(
    os.environ.get("ACP2_ACCEPTANCE_ENV", "<acp2-dev>/data/acceptance-env-c.sh")
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_module("acp2_experience_under_test", HARNESS_PATH)


@pytest.fixture(scope="module")
def scenarios():
    return _load_module("acp2_experience_scenarios_under_test", SCENARIOS_PATH)


@pytest.fixture(scope="module")
def runtime_path():
    candidates = [
        os.environ.get("ACP2_RUNTIME_PATH"),
        str(Path(os.environ.get("ACP2_PROJECT_ROOT", "/nonexistent")) / "scripts/acceptance/acp2_runtime.py"),
        "<repo>/scripts/acceptance/acp2_runtime.py",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    pytest.skip("NOT_VERIFIED: B runtime scripts/acceptance/acp2_runtime.py unavailable")


@pytest.fixture()
def runtime_env(monkeypatch, runtime_path):
    project = runtime_path.parents[2]
    monkeypatch.setenv("ACP2_RUNTIME_PATH", str(runtime_path))
    monkeypatch.setenv("ACP2_PROJECT_ROOT", str(project))
    monkeypatch.setenv("ACP2_CONTRACTS", str(CONTRACTS_DIR))
    monkeypatch.setenv("ACP2_C_SOURCE", str(REPO_ROOT))
    monkeypatch.delenv("ACP2_RUN_ID", raising=False)
    monkeypatch.delenv("ACP2_EXPERIENCE_SELFTEST", raising=False)
    return runtime_path


def _receipts(out_dir: Path) -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    for path in sorted(Path(out_dir).rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("schema") == "acp2.acceptance.v1":
            found.append((path, document))
    return found


def _one_receipt(out_dir: Path) -> tuple[Path, dict]:
    receipts = _receipts(out_dir)
    assert len(receipts) == 1, receipts
    return receipts[0]


def _completed(argv, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _run_id(seed: str) -> str:
    return f"{seed}-20260101T000000Z-test"


def _out(tmp_path: Path, scenario: str, seed: str, suffix: str = "main") -> Path:
    return tmp_path / scenario / _run_id(seed) / suffix


def _fake_context(out_dir: Path, *, evidence_kind: str = "fixture", timeout: float = 60.0):
    identity = SimpleNamespace(
        scenario="experience:baseline",
        scenario_key="baseline",
        phase="experience:fixture",
        evidence_kind=evidence_kind,
        mode="fixture",
        subcase=None,
    )
    return SimpleNamespace(
        identity=identity,
        run_id="baseline-20260101T000000Z-test",
        out_dir=Path(out_dir),
        timeout=timeout,
        manifest=None,
        assertions=[],
        artifacts=[],
        extensions={},
        refs={},
        usage=None,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Mode parsing and --help
# ---------------------------------------------------------------------------


def test_help_has_no_side_effects(harness, tmp_path):
    env = dict(os.environ)
    env["ACP2_PROJECT_ROOT"] = str(tmp_path / "no-project")
    env.pop("ACP2_RUNTIME_PATH", None)
    result = subprocess.run(
        [sys.executable, str(HARNESS_PATH), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "NOT_VERIFIED" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_three_exclusive_modes_parse(harness):
    parser = harness.build_parser()
    scenario = parser.parse_args(["--scenario", "baseline", "--out", "/tmp/x", "--timeout", "60"])
    receipt = parser.parse_args(["--verify-receipt", "/tmp/x.json"])
    placeholder = parser.parse_args(["--check-doc-placeholders", "/tmp/doc.md"])
    assert scenario.scenario == "baseline"
    assert receipt.verify_receipt == Path("/tmp/x.json")
    assert placeholder.check_doc_placeholders == Path("/tmp/doc.md")

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--scenario", "baseline", "--verify-receipt", "/tmp/x.json"])
    assert excinfo.value.code == 3


def test_scenario_mode_accepts_phase_mode_subcase(harness):
    parser = harness.build_parser()
    args = parser.parse_args(
        [
            "--scenario",
            "cross-domain-live",
            "--out",
            "/tmp/x",
            "--timeout",
            "60",
            "--phase",
            "main",
            "--mode",
            "live",
            "--subcase",
            "nmr",
        ]
    )
    assert (args.phase, args.mode, args.subcase) == ("main", "live", "nmr")


def test_tool_modes_need_no_scenario_arguments(harness):
    parser = harness.build_parser()
    receipt = parser.parse_args(["--verify-receipt", "/tmp/receipt.json"])
    placeholder = parser.parse_args(["--check-doc-placeholders", "/tmp/doc.md"])
    assert receipt.out is None and receipt.timeout is None
    assert placeholder.out is None and placeholder.timeout is None


def test_invalid_mode_exits_3(harness):
    parser = harness.build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(
            ["--scenario", "baseline", "--out", "/tmp/x", "--timeout", "60", "--mode", "staging"]
        )
    assert excinfo.value.code == 3


# ---------------------------------------------------------------------------
# Dry-run, unknown scenario, missing prerequisite, executed failure
# ---------------------------------------------------------------------------


def test_dry_run_writes_not_verified_only(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "baseline", "dryrun")
    code = harness.main(
        ["--scenario", "baseline", "--out", str(out), "--timeout", "60", "--dry-run"]
    )
    assert code == 0
    _, document = _one_receipt(out)
    assert document["result"] == "NOT_VERIFIED"
    assert document["reason"] == "dry_run"
    assert document["evidence_kind"] == "fixture"
    assert document["extensions"]["experience.mode"] == "fixture"
    assert all(item["status"] != "PASS" for item in document["assertions"])


def test_unknown_scenario_exit_3(harness, runtime_env, tmp_path):
    code = harness.main(
        [
            "--scenario",
            "does-not-exist",
            "--out",
            str(_out(tmp_path, "unknown", "unknown")),
            "--timeout",
            "60",
        ]
    )
    assert code == 3


def test_missing_precondition_exit_2(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "baseline", "missing-manifest")
    code = harness.main(
        [
            "--scenario",
            "baseline",
            "--out",
            str(out),
            "--timeout",
            "60",
            "--manifest",
            str(tmp_path / "absent-manifest.json"),
        ]
    )
    # A missing prerequisite is exit 2 and writes no scenario receipt: the
    # harness never fabricates an executed result for an input it rejected.
    assert code == 2
    assert _receipts(out) == []


def test_executed_assertion_failure_exit_1(harness, runtime_env, tmp_path, monkeypatch):
    def handler(context):
        context.assertions.append({"name": "boom", "status": "FAIL", "detail": "executed"})
        return 1

    monkeypatch.setattr(harness, "load_scenario_impls", lambda: {"baseline": handler})
    out = _out(tmp_path, "baseline", "executed-fail")
    code = harness.main(["--scenario", "baseline", "--out", str(out), "--timeout", "60"])
    assert code == 1
    _, document = _one_receipt(out)
    assert document["result"] == "FAIL"


def test_zero_assertions_never_pass(harness, runtime_env, tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "load_scenario_impls", lambda: {"baseline": lambda context: 0})
    out = _out(tmp_path, "baseline", "zero-assertions")
    code = harness.main(["--scenario", "baseline", "--out", str(out), "--timeout", "60"])
    assert code == 1
    _, document = _one_receipt(out)
    assert document["result"] == "FAIL"
    assert document["reason"] == "zero_assertions"


# ---------------------------------------------------------------------------
# Live guard, redaction, timeout, append-only
# ---------------------------------------------------------------------------


def test_live_with_fake_transport_fails(harness, scenarios, runtime_env, tmp_path, monkeypatch):
    def handler(context):
        return 0 if scenarios.assert_live_environment(context) else 1

    monkeypatch.setattr(harness, "load_scenario_impls", lambda: {"mcp-live": handler})
    monkeypatch.setenv("ACP2_FAKE_TRANSPORT", "1")
    out = _out(tmp_path, "mcp-live", "fake-live", suffix="main/live")
    code = harness.main(
        ["--scenario", "mcp-live", "--out", str(out), "--timeout", "60", "--mode", "live"]
    )
    assert code == 1
    _, document = _one_receipt(out)
    assert document["result"] == "FAIL"
    assert document["evidence_kind"] == "live"
    assert any(item["name"] == "live.no_fake_transport" for item in document["assertions"])


def test_clean_live_environment_is_accepted(harness, scenarios, runtime_env, tmp_path, monkeypatch):
    def handler(context):
        return 0 if scenarios.assert_live_environment(context) else 1

    monkeypatch.setattr(harness, "load_scenario_impls", lambda: {"mcp-live": handler})
    for key in scenarios._FAKE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    out = _out(tmp_path, "mcp-live", "clean-live", suffix="main/live")
    code = harness.main(
        ["--scenario", "mcp-live", "--out", str(out), "--timeout", "60", "--mode", "live"]
    )
    assert code == 0
    _, document = _one_receipt(out)
    assert document["result"] == "PASS"


def test_redaction_masks_keys_and_bearer_tokens(harness):
    cleaned = harness.redact(
        {
            "api_key": "raw-secret",
            "nested": {"Authorization": "Bearer abc.def"},
            "note": "Bearer zzz.yyy",
            "safe": "value",
        }
    )
    assert cleaned["api_key"] == "***"
    assert cleaned["nested"]["Authorization"] == "***"
    assert "raw-secret" not in json.dumps(cleaned)
    assert "zzz.yyy" not in json.dumps(cleaned)
    assert cleaned["safe"] == "value"


def test_manifest_api_key_is_redacted_in_receipt(harness, runtime_env, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"candidate_id": "a" * 40, "api_key": "top-secret-value"}),
        encoding="utf-8",
    )
    out = _out(tmp_path, "baseline", "redaction")
    code = harness.main(
        [
            "--scenario",
            "baseline",
            "--out",
            str(out),
            "--timeout",
            "60",
            "--dry-run",
            "--manifest",
            str(manifest),
        ]
    )
    assert code == 0
    receipt_path, document = _one_receipt(out)
    assert document["extensions"]["experience.manifest"]["api_key"] == "***"
    assert "top-secret-value" not in receipt_path.read_text(encoding="utf-8")
    redacted = out / "manifest.redacted.json"
    assert redacted.is_file()
    assert "top-secret-value" not in redacted.read_text(encoding="utf-8")


def test_total_timeout_is_not_verified(harness, runtime_env, tmp_path, monkeypatch):
    def slow(context):
        time.sleep(2.0)
        context.assertions.append({"name": "late", "status": "PASS", "detail": "too late"})
        return 0

    monkeypatch.setattr(harness, "load_scenario_impls", lambda: {"baseline": slow})
    out = _out(tmp_path, "baseline", "timeout")
    code = harness.main(["--scenario", "baseline", "--out", str(out), "--timeout", "0.2"])
    assert code == 2
    _, document = _one_receipt(out)
    assert document["result"] == "NOT_VERIFIED"
    assert (out / "timeout-cleanup.json").is_file()


def test_append_only_refuses_second_run(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "baseline", "append")
    args = ["--scenario", "baseline", "--out", str(out), "--timeout", "60", "--dry-run"]
    assert harness.main(args) == 0
    receipt_path, _ = _one_receipt(out)
    before = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert harness.main(args) == 3
    after = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert before == after


# ---------------------------------------------------------------------------
# Receipt verification
# ---------------------------------------------------------------------------


def test_verify_receipt_positive(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "baseline", "verify-positive")
    assert harness.main(["--scenario", "baseline", "--out", str(out), "--timeout", "60", "--dry-run"]) == 0
    receipt_path, _ = _one_receipt(out)
    assert harness.main(["--verify-receipt", str(receipt_path)]) == 0


def test_verify_receipt_rejects_sha_mismatch(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "baseline", "verify-negative")
    assert harness.main(["--scenario", "baseline", "--out", str(out), "--timeout", "60", "--dry-run"]) == 0
    receipt_path, document = _one_receipt(out)
    tampered = json.loads(json.dumps(document))
    tampered["artifacts"][0]["sha256"] = "0" * 64
    tampered_path = out / "tampered-receipt.json"
    tampered_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert harness.main(["--verify-receipt", str(tampered_path)]) == 1


def test_product_evidence_summary_is_not_a_b_receipt(harness, runtime_env, tmp_path):
    """C product-evidence summaries must never masquerade as B receipts.

    T-04/T-05/T-04a write ``acp2.experience.product-evidence.v1`` summaries with
    C-owned fields (``checks``/``negative_checks``/``not_verified``).  The single
    B validator must reject them, so a summary can never be cited as a PASS
    receipt.
    """

    summary = {
        "schema": "acp2.experience.product-evidence.v1",
        "run_id": "20260101T000000Z-test",
        "scenario": "node-dsh-install",
        "result": "PASS",
        "evidence_kind": "fixture",
        "checks": [{"name": "toolchain", "result": "PASS"}],
        "negative_checks": [],
        "not_verified": [],
        "artifacts": [],
    }
    path = tmp_path / "product-evidence-result.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert harness.main(["--verify-receipt", str(path)]) == 1


# ---------------------------------------------------------------------------
# Mode/subcase legality and dry-run routing
# ---------------------------------------------------------------------------


def test_mode_live_is_illegal_for_fixture_scenario(harness, runtime_env, tmp_path):
    code = harness.main(
        [
            "--scenario",
            "baseline",
            "--out",
            str(_out(tmp_path, "baseline", "illegal-live")),
            "--timeout",
            "60",
            "--mode",
            "live",
        ]
    )
    assert code == 3


def test_evidence_browser_live_mode_live_dry_run(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "evidence-browser-live", "explicit-live", suffix="main/live")
    code = harness.main(
        [
            "--scenario",
            "evidence-browser-live",
            "--out",
            str(out),
            "--timeout",
            "60",
            "--mode",
            "live",
            "--dry-run",
        ]
    )
    assert code == 0
    _, document = _one_receipt(out)
    assert document["result"] == "NOT_VERIFIED"
    assert document["evidence_kind"] == "live"


def test_subcase_whitelist_positive(harness, runtime_env, tmp_path):
    nmr_out = _out(tmp_path, "cross-domain-live", "subcase-nmr", suffix="main/live/nmr")
    assert harness.main(
        [
            "--scenario",
            "cross-domain-live",
            "--out",
            str(nmr_out),
            "--timeout",
            "60",
            "--subcase",
            "nmr",
            "--dry-run",
        ]
    ) == 0
    _, document = _one_receipt(nmr_out)
    assert document["phase"] == "experience:nmr"

    loop_out = _out(tmp_path, "research-loop-live", "subcase-control", suffix="main/live/control")
    assert harness.main(
        [
            "--scenario",
            "research-loop-live",
            "--out",
            str(loop_out),
            "--timeout",
            "60",
            "--subcase",
            "control",
            "--dry-run",
        ]
    ) == 0
    _, loop_document = _one_receipt(loop_out)
    assert loop_document["phase"] == "experience:control"


@pytest.mark.parametrize(
    "scenario,subcase",
    [("baseline", "nmr"), ("cross-domain-live", "min"), ("research-loop-live", "free")],
)
def test_subcase_whitelist_negative(harness, runtime_env, tmp_path, scenario, subcase):
    code = harness.main(
        [
            "--scenario",
            scenario,
            "--out",
            str(_out(tmp_path, scenario, f"subcase-{subcase}")),
            "--timeout",
            "60",
            "--subcase",
            subcase,
            "--dry-run",
        ]
    )
    assert code == 3


def test_approval_budget_live_phase_alias_writes_fixture(harness, runtime_env, tmp_path):
    out = _out(tmp_path, "approval-budget-live", "alias-approval", suffix="approval")
    assert (
        harness.main(
            [
                "--scenario",
                "approval-budget-live",
                "--out",
                str(out),
                "--timeout",
                "60",
                "--phase",
                "approval",
                "--dry-run",
            ]
        )
        == 0
    )
    _, document = _one_receipt(out)
    assert document["phase"] == "experience:fixture"
    assert document["evidence_kind"] == "fixture"
    assert document["extensions"]["experience.mode"] == "fixture"
    assert document["result"] == "NOT_VERIFIED"

    budget_out = _out(tmp_path, "approval-budget-live", "alias-budget", suffix="budget")
    assert (
        harness.main(
            [
                "--scenario",
                "approval-budget-live",
                "--out",
                str(budget_out),
                "--timeout",
                "60",
                "--phase",
                "budget",
                "--dry-run",
            ]
        )
        == 0
    )
    _, budget = _one_receipt(budget_out)
    assert budget["phase"] == "budget"
    assert budget["evidence_kind"] == "fixture"
    assert budget["result"] == "NOT_VERIFIED"

    live_out = _out(tmp_path, "approval-budget-live", "alias-live", suffix="live")
    assert (
        harness.main(
            [
                "--scenario",
                "approval-budget-live",
                "--out",
                str(live_out),
                "--timeout",
                "60",
                "--mode",
                "live",
            ]
        )
        == 3
    )


# ---------------------------------------------------------------------------
# Route selection (T-04 probe is the one routing implementation)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not ACCEPTANCE_ENV.is_file(), reason="NOT_VERIFIED: acceptance env absent")
def test_route_probe_missing_value_exits_3():
    result = subprocess.run(
        ["bash", str(ROUTE_PROBE), "mcp-live", "60", "--mode"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 3
    assert "exit=3" in result.stdout


@pytest.mark.skipif(not ACCEPTANCE_ENV.is_file(), reason="NOT_VERIFIED: acceptance env absent")
def test_route_dry_run_live_uses_c_environment():
    result = subprocess.run(
        ["bash", str(ROUTE_PROBE), "mcp-live", "60", "--mode", "live", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    environment: dict[str, str] = {}
    probe = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{ACCEPTANCE_ENV}" >/dev/null; printf "%s\\n" "$ACP2_C_PYTHON" "$ACP2_INTEGRATION_PYTHON"',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode == 0:
        c_python, integration_python = probe.stdout.splitlines()[:2]
        environment = {"c": c_python, "integration": integration_python}
    assert "interpreter=" in result.stdout
    assert "mode=fixture" in result.stdout
    if environment.get("c"):
        assert f"interpreter={environment['c']}" in result.stdout


# ---------------------------------------------------------------------------
# The five P0 scenario bodies
# ---------------------------------------------------------------------------


def test_scenario_registry_contains_the_five_p0_plus_discovered_modules(scenarios):
    p0 = {
        "baseline",
        "contracts",
        "workspace",
        "harness-selftest",
        "node-dsh-install",
    }
    assert set(scenarios.P0_SCENARIOS) == p0
    assert p0.issubset(set(scenarios.SCENARIO_IMPLS))
    # T-08 owns browser-install; later todos add sibling modules discovered
    # without editing the registry.
    assert "browser-install" in scenarios.SCENARIO_IMPLS
    assert "acp2_experience_scenario_browser_install" in scenarios.DISCOVERY_MODULES


def test_scenario_registry_rejects_duplicate_registration(scenarios):
    with pytest.raises(ValueError, match="already registered"):
        scenarios.register("browser-install", lambda _context: 0)


def test_contracts_scenario_passes_on_real_contracts(scenarios, runtime_env, tmp_path):
    context = _fake_context(tmp_path / "contracts-out")
    code = scenarios.scenario_contracts(context)
    assert code == 0, context.assertions
    assert context.assertions
    assert all(item["status"] == "PASS" for item in context.assertions)
    report = tmp_path / "contracts-out" / "contracts-report.json"
    assert report.is_file()
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["negative"]["missing_candidate_id"]["rejected"] is True
    assert document["negative"]["unknown_tool"]["rejected"] is True


def test_contracts_scenario_fails_on_broken_ref(scenarios, runtime_env, tmp_path, monkeypatch):
    copied = tmp_path / "contracts-copy"
    shutil.copytree(CONTRACTS_DIR, copied)
    (copied / "broken.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "schema_dialect": "json-schema-2020-12",
                "$ref": "#/$defs/does-not-exist",
                "$defs": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACP2_CONTRACTS", str(copied))
    context = _fake_context(tmp_path / "contracts-broken-out")
    code = scenarios.scenario_contracts(context)
    assert code == 1
    refs = [item for item in context.assertions if item["name"] == "contracts.refs_resolve"]
    assert refs and refs[0]["status"] == "FAIL"


def test_baseline_scenario_with_fake_commands(scenarios, tmp_path, monkeypatch):
    source = tmp_path / "C"
    source.mkdir()
    monkeypatch.setenv("ACP2_C_SOURCE", str(source))
    monkeypatch.setenv("ACP2_C_PYTHON", sys.executable)
    monkeypatch.setattr(scenarios, "_node", lambda: "/usr/bin/node")

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["git", "-C"]:
            if "rev-parse" in argv:
                return _completed(argv, 0, "a" * 40 + "\n")
            return _completed(argv, 0, "")
        if argv[:2] == [sys.executable, "-c"]:
            return _completed(
                argv,
                0,
                json.dumps(
                    {
                        "acp": str(source / "src/acp/__init__.py"),
                        "cccp": str(source / "src/cccp/__init__.py"),
                        "acp_services": str(source / "src/acp_services/__init__.py"),
                    }
                ),
            )
        return _completed(argv, 0, "329 passed, 0 failed\n")

    monkeypatch.setattr(scenarios, "_run", fake_run)
    context = _fake_context(tmp_path / "baseline-out")
    code = scenarios.scenario_baseline(context)
    assert code == 0, context.assertions
    assert all(item["status"] == "PASS" for item in context.assertions)


def test_baseline_scenario_fails_on_dirty_worktree(scenarios, tmp_path, monkeypatch):
    source = tmp_path / "C-dirty"
    source.mkdir()
    monkeypatch.setenv("ACP2_C_SOURCE", str(source))
    monkeypatch.setenv("ACP2_C_PYTHON", sys.executable)
    monkeypatch.setattr(scenarios, "_node", lambda: "/usr/bin/node")

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["git", "-C"]:
            if "rev-parse" in argv:
                return _completed(argv, 0, "a" * 40 + "\n")
            return _completed(argv, 0, " M src/acp/cli.py\n")
        return _completed(
            argv,
            0,
            json.dumps(
                {
                    "acp": str(source / "src/acp/__init__.py"),
                    "cccp": str(source / "src/cccp/__init__.py"),
                    "acp_services": str(source / "src/acp_services/__init__.py"),
                }
            ),
        )

    monkeypatch.setattr(scenarios, "_run", fake_run)
    context = _fake_context(tmp_path / "baseline-dirty-out")
    assert scenarios.scenario_baseline(context) == 1


def test_workspace_scenario_with_fake_commands(scenarios, tmp_path, monkeypatch):
    source = tmp_path / "C-workspace"
    (source / "rph").mkdir(parents=True)
    (source / "rph/package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ACP2_C_SOURCE", str(source))
    monkeypatch.setattr(scenarios, "_pnpm", lambda: "/usr/bin/pnpm")
    monkeypatch.setattr(scenarios, "_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(scenarios, "_run", lambda argv, **_kwargs: _completed(argv, 0, "ok"))
    context = _fake_context(tmp_path / "workspace-out")
    assert scenarios.scenario_workspace(context) == 0, context.assertions
    assert all(item["status"] == "PASS" for item in context.assertions)


def test_workspace_scenario_fails_on_build_error(scenarios, tmp_path, monkeypatch):
    source = tmp_path / "C-workspace-fail"
    (source / "rph").mkdir(parents=True)
    (source / "rph/package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ACP2_C_SOURCE", str(source))
    monkeypatch.setattr(scenarios, "_pnpm", lambda: "/usr/bin/pnpm")
    monkeypatch.setattr(scenarios, "_node", lambda: "/usr/bin/node")

    calls = {"count": 0}

    def fake_run(argv, **_kwargs):
        calls["count"] += 1
        return _completed(argv, 1 if calls["count"] == 2 else 0, "boom" if calls["count"] == 2 else "ok")

    monkeypatch.setattr(scenarios, "_run", fake_run)
    context = _fake_context(tmp_path / "workspace-fail-out")
    assert scenarios.scenario_workspace(context) == 1


def test_harness_selftest_scenario_with_fake_pytest(scenarios, tmp_path, monkeypatch):
    source = tmp_path / "C-selftest"
    (source / "tests").mkdir(parents=True)
    (source / "tests/test_acp2_experience_harness.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("ACP2_C_SOURCE", str(source))
    monkeypatch.delenv("ACP2_EXPERIENCE_SELFTEST", raising=False)
    monkeypatch.setattr(scenarios, "_run", lambda argv, **_kwargs: _completed(argv, 0, "20 passed in 0.2s\n"))
    context = _fake_context(tmp_path / "selftest-out")
    assert scenarios.scenario_harness_selftest(context) == 0, context.assertions


def test_harness_selftest_recursion_guard(scenarios, tmp_path, monkeypatch):
    monkeypatch.setenv("ACP2_C_SOURCE", str(tmp_path))
    monkeypatch.setenv("ACP2_EXPERIENCE_SELFTEST", "1")
    context = _fake_context(tmp_path / "selftest-guard-out")
    assert scenarios.scenario_harness_selftest(context) == 2
    assert context.assertions[0]["status"] == "NOT_VERIFIED"


def test_node_dsh_install_scenario_with_fake_commands(scenarios, tmp_path, monkeypatch):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    archive = tmp_path / "node.tar.xz"
    archive.write_bytes(b"archive")
    digest = "c0649af" + "0" * 57
    (contracts / "dsh-compatibility.json").write_text(
        json.dumps(
            {
                "node": {
                    "version": "22.19.0",
                    "archive_path": str(archive),
                    "archive_sha256": digest,
                },
                "pnpm": {"version": "11.28.5"},
                "dsh": {"revision": "687f282e8625c6b8d11ff58211a90524be2e4317"},
            }
        ),
        encoding="utf-8",
    )
    dsh_source = tmp_path / "dsh"
    dsh_source.mkdir()
    monkeypatch.setenv("ACP2_CONTRACTS", str(contracts))
    monkeypatch.setenv("ACP2_DSH_SOURCE", str(dsh_source))
    monkeypatch.delenv("ACP2_NODE_VERSION", raising=False)
    monkeypatch.delenv("ACP2_PNPM_VERSION", raising=False)
    monkeypatch.delenv("ACP2_DSH_REV", raising=False)
    monkeypatch.setattr(scenarios, "_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(scenarios, "_pnpm", lambda: "/usr/bin/pnpm")
    monkeypatch.setattr(scenarios, "_sha256_file", lambda path: digest)

    def fake_run(argv, **_kwargs):
        if argv[0] == "/usr/bin/node":
            return _completed(argv, 0, "v22.19.0\n")
        if argv[:2] == ["/usr/bin/pnpm", "--version"]:
            return _completed(argv, 0, "11.28.5\n")
        if argv[:2] == ["git", "-C"]:
            return _completed(argv, 0, "687f282e8625c6b8d11ff58211a90524be2e4317\n")
        return _completed(argv, 0, "0.2.1-rc.2\n")

    monkeypatch.setattr(scenarios, "_run", fake_run)
    context = _fake_context(tmp_path / "node-dsh-out")
    assert scenarios.scenario_node_dsh_install(context) == 0, context.assertions
    assert all(item["status"] == "PASS" for item in context.assertions)
