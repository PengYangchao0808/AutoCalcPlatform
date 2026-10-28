"""M0-03/R10 — required observations, live/offline separation, live ports.

The matrix is the criterion, not the test count:

* every C live scenario declares present/absent observations, and the
  declaration is complete/valid (no scenario silently defaults to live);
* a live PASS is rejected when observations are missing, mismatched, belong to
  an undeclared scenario, or contradict the model/denial facts;
* ``model=absent`` with exactly zero calls plus a real untriggered/denial fact
  is a valid live negative (a budget denial must carry no TaskRef);
* an outer live row may not embed ``offline_fixture`` content;
* the approval card's "no transport" layering rule no longer means PASS: the
  injected ``ACPClient.research`` adapter/wiring must exist;
* ``research-loop-live`` rejects no live row on hardcoded delivery booleans and
  reports ``NOT_VERIFIED(implementation_missing)`` with executable port probes.

No model, QC, LSF, Controller or DSH process is started: every subprocess is
faked and the port probe is static.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = REPO_ROOT / "scripts/acceptance/acp2_runtime.py"
HARNESS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience.py"
SCENARIOS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience_scenarios.py"
ACCEPTANCE_DIR = REPO_ROOT / "scripts/acceptance"
SAMPLES = REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/samples/acceptance"
LIVE_SAMPLE = SAMPLES / "positive/c-dsh-local-singlepoint-live.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runtime():
    module = _load_module("acp2_runtime_m003b", RUNTIME_PATH)
    assert hasattr(module, "required_observations")
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_module("acp2_experience_m003b", HARNESS_PATH)


@pytest.fixture(scope="module")
def scenario_modules():
    _load_module("acp2_experience_scenarios_m003b", SCENARIOS_PATH)
    return {
        "approval": sys.modules["acp2_experience_scenario_approval_budget_live"],
        "research_loop": sys.modules["acp2_experience_scenario_research_loop_live"],
    }


def _sample() -> dict:
    return json.loads(LIVE_SAMPLE.read_text(encoding="utf-8"))


def _denial_receipt() -> dict:
    """A live negative row: zero model calls, real budget denial, no TaskRef."""

    document = copy.deepcopy(_sample())
    document["scenario"] = "experience:fault-boundaries-live"
    document["phase"] = "experience:live"
    document["extensions"] = {
        "acceptance.operation_class": "control",
        "experience.observations": {
            "schema": "acp2.experience.observations.v1",
            "observed": {
                "transport": "present",
                "controller": "present",
                "model": "absent",
                "qc": "absent",
                "lsf": "absent",
                "browser": "absent",
                "artifact": "absent",
                "denial": "present",
            },
            "model_calls": 0,
            "model_evidence": "no model client configured; budget denial before dispatch",
            "denial_evidence": "budget_exceeded: denied before dispatch",
            "denial_kind": "budget",
        },
    }
    for key in (
        "job_id",
        "attempt",
        "release_id",
        "submission_id",
        "operation_id",
        "manifest_generation",
        "negotiated_protocol",
    ):
        document[key] = None
    return document


# ---------------------------------------------------------------------------
# Declared observations
# ---------------------------------------------------------------------------


def _identity(harness, scenario: str, mode: str | None = None):
    args = SimpleNamespace(
        scenario=scenario,
        out=Path("/tmp/m003b") / scenario / "run-0000000",
        phase=None,
        mode=mode,
        subcase=None,
    )
    return harness._resolve_identity(args)


def test_every_live_scenario_declares_complete_observations(runtime, harness):
    declared = runtime.EXPERIENCE_REQUIRED_OBSERVATIONS
    # Every scenario that can resolve to evidence_kind=live must be declared,
    # and a declaration without such a scenario is dead contract.
    live_defaulting = {
        scenario
        for scenario in harness.SCENARIO_IDS
        if _identity(harness, scenario)[0].evidence_kind == "live"
    }
    live_capable = set()
    for scenario in harness.LIVE_ALLOWED:
        identity, error = _identity(harness, scenario, mode="live")
        if error is None and identity.evidence_kind == "live":
            live_capable.add(scenario)
    # approval-budget-live is fixture-only today but keeps its declaration for
    # the live row that B16/B17 will unlock (harness-contract section 4).
    assert live_capable | {"approval-budget-live"} <= set(declared)
    assert set(declared) == live_defaulting | live_capable | {"approval-budget-live"}
    for scenario_key, directions in declared.items():
        assert set(directions) == set(runtime.OBSERVATION_NAMES), scenario_key
        assert set(directions.values()) <= set(runtime.OBSERVATION_DIRECTIONS)
        assert runtime.required_observations(scenario_key) == directions
    assert runtime.required_observations("baseline") is None


def test_declaration_registry_rejects_unknown_entries(runtime):
    with pytest.raises(ValueError):
        runtime._observations(gpu="present")
    with pytest.raises(ValueError):
        runtime._observations(model="sometimes")


def test_harness_fails_closed_for_undeclared_live_scenario(harness, runtime):
    declared_identity = SimpleNamespace(
        evidence_kind="live",
        scenario_key="research-loop-live",
    )
    assert harness._observation_contract_error(declared_identity, runtime) is None
    undeclared = SimpleNamespace(evidence_kind="live", scenario_key="not-a-live-scenario")
    assert (
        harness._observation_contract_error(undeclared, runtime)
        == "experience_observations_undeclared:not-a-live-scenario"
    )
    fixture = SimpleNamespace(evidence_kind="fixture", scenario_key="baseline")
    assert harness._observation_contract_error(fixture, runtime) is None
    stripped = SimpleNamespace(evidence_kind="live", scenario_key="research-loop-live")
    empty = SimpleNamespace(EXPERIENCE_REQUIRED_OBSERVATIONS={})
    assert (
        harness._observation_contract_error(stripped, empty)
        == "experience_observations_registry_missing"
    )


# ---------------------------------------------------------------------------
# Validator: live PASS must be grounded in observations
# ---------------------------------------------------------------------------


def test_live_sample_with_declared_observations_is_accepted(runtime):
    assert runtime.validate_receipt(_sample()) == []


def test_live_pass_without_observations_is_rejected(runtime):
    document = _sample()
    document["extensions"].pop("experience.observations")
    assert runtime.validate_receipt(document)[0] == "experience_observations_missing"


def test_live_observation_mismatch_is_rejected(runtime):
    document = _sample()
    document["extensions"]["experience.observations"]["observed"]["qc"] = "absent"
    assert runtime.validate_receipt(document)[0] == "experience_observation_mismatch:qc"


def test_fixture_row_with_all_absent_observations_is_accepted(runtime):
    # Mirrors the approval-budget-live fixture row: no live fact was observed.
    document = _sample()
    document["scenario"] = "experience:approval-budget-live"
    document["evidence_kind"] = "fixture"
    document["phase"] = "experience:fixture"
    for key in ("job_id", "attempt", "release_id", "negotiated_protocol"):
        document[key] = None
    document["extensions"]["experience.observations"] = {
        "schema": "acp2.experience.observations.v1",
        "observed": {name: "absent" for name in runtime.OBSERVATION_NAMES},
        "model_calls": 0,
        "model_evidence": "fixture row: Node module tests only (0 model calls)",
    }
    assert runtime.validate_receipt(document) == []


def test_live_undeclared_scenario_is_a_contract_error(runtime):
    document = _sample()
    document["scenario"] = "experience:not-a-live-scenario"
    document["extensions"] = {
        "acceptance.operation_class": "discover",
        "experience.observations": {
            "schema": "acp2.experience.observations.v1",
            "observed": {
                name: "absent" for name in runtime.OBSERVATION_NAMES if name != "transport"
            }
            | {"transport": "present"},
            "model_calls": 0,
            "model_evidence": "no model client configured",
        },
    }
    for key in (
        "job_id",
        "attempt",
        "release_id",
        "submission_id",
        "operation_id",
        "manifest_generation",
        "negotiated_protocol",
    ):
        document[key] = None
    assert (
        runtime.validate_receipt(document)[0]
        == "experience_observations_undeclared:not-a-live-scenario"
    )


def test_live_declaration_drift_is_rejected(runtime):
    document = _sample()
    document["extensions"]["experience.declared_observations"] = {
        "transport": "present",
        "controller": "present",
        "model": "present",
        "qc": "present",
        "lsf": "present",
        "browser": "present",
        "artifact": "present",
        "denial": "present",
    }
    assert runtime.validate_receipt(document)[0] == ("experience_observations_declaration_drift")


def test_unknown_observation_name_is_rejected(runtime):
    document = _sample()
    document["extensions"]["experience.observations"]["observed"]["gpu"] = "present"
    assert runtime.validate_receipt(document)[0] == ("experience_observation_unknown:gpu")


# ---------------------------------------------------------------------------
# Validator: model present/absent facts
# ---------------------------------------------------------------------------


def test_model_present_requires_a_real_call(runtime):
    document = _sample()
    document["extensions"]["experience.observations"]["model_calls"] = 0
    assert runtime.validate_receipt(document)[0] == "live_model_present_without_calls"


def test_model_present_requires_a_gateway_receipt(runtime):
    document = _sample()
    document["extensions"]["experience.observations"]["model_evidence"] = ""
    assert runtime.validate_receipt(document)[0] == "live_model_present_without_evidence"


def test_model_absent_with_calls_is_rejected(runtime):
    document = _denial_receipt()
    document["extensions"]["experience.observations"]["model_calls"] = 1
    assert runtime.validate_receipt(document)[0] == "live_model_absent_with_calls"


def test_model_absent_needs_an_untriggered_or_denial_fact(runtime):
    document = _denial_receipt()
    document["extensions"]["experience.observations"].pop("model_evidence")
    assert runtime.validate_receipt(document)[0] == "live_model_absent_without_fact"


def test_zero_model_live_denial_is_a_valid_live_negative(runtime):
    # transport/controller present, model absent with 0 calls, real denial fact,
    # and no TaskRef for the denied budget row.
    assert runtime.validate_receipt(_denial_receipt()) == []


def test_budget_denial_must_not_carry_a_task_ref(runtime):
    document = _denial_receipt()
    document["job_id"] = "job-forbidden"
    assert runtime.validate_receipt(document)[0] == "live_budget_denial_task_ref:job_id"


# ---------------------------------------------------------------------------
# Validator: live/offline cross contradictions
# ---------------------------------------------------------------------------


def test_outer_live_with_nested_offline_fixture_is_rejected(runtime):
    document = _sample()
    document["extensions"]["experience.diagnostic"] = {
        "offline_proof": {"evidence_kind": "offline_fixture", "all_passed": True}
    }
    assert runtime.validate_receipt(document)[0] == (
        "live_nested_evidence_kind:offline_fixture:"
        "extensions.experience.diagnostic.offline_proof.evidence_kind"
    )


def test_outer_live_with_offline_fixture_content_flag_is_rejected(runtime):
    document = _sample()
    document["extensions"]["experience.observations"]["offline_fixture_content"] = True
    assert runtime.validate_receipt(document)[0] == "live_inner_offline_fixture"


def test_fixture_row_may_not_claim_a_present_live_observation(runtime):
    document = _sample()
    document["evidence_kind"] = "fixture"
    document["phase"] = "experience:fixture"
    for key in ("job_id", "attempt", "release_id"):
        document[key] = None
    reasons = runtime.validate_receipt(document)
    assert any(reason.startswith("fixture_observation_present:") for reason in reasons), reasons


# ---------------------------------------------------------------------------
# approval-budget-live: adapter/wiring, not "no transport"
# ---------------------------------------------------------------------------


def _approval_source(tmp_path: Path, *, with_client_adapter: bool) -> Path:
    source = tmp_path / "c-source"
    for relative in (
        "frontend/js/workbench/features/research-approval.js",
        "frontend/js/module_tests/acp2_approval.test.js",
        "frontend/js/module_tests/run_all.js",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    plan = source / "docs/reference/next-change/acp2-contracts/experience/plan-identity.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# plan identity\n", encoding="utf-8")
    if with_client_adapter:
        client = source / "frontend/js/client/research-query.js"
        client.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / "frontend/js/client/research-query.js", client)
    return source


def _approval_context(out_dir: Path):
    return SimpleNamespace(
        identity=SimpleNamespace(
            scenario="experience:approval-budget-live",
            scenario_key="approval-budget-live",
            phase="experience:fixture",
            requested_phase="approval",
            evidence_kind="fixture",
            mode="fixture",
            subcase=None,
        ),
        run_id="approval-budget-live-r1",
        out_dir=out_dir,
        timeout=60.0,
        assertions=[],
        artifacts=[],
        extensions={},
        refs={},
        usage=None,
        reason=None,
    )


def _node_log(module) -> str:
    phrases = "\n".join(module.REQUIRED_TEST_PHRASES.values())
    return f"{phrases}\n12 passed, 0 failed\n"


def test_approval_wiring_is_required_not_absence_of_transport(scenario_modules, tmp_path):
    module = scenario_modules["approval"]
    wiring = module._adapter_wiring(REPO_ROOT)
    assert wiring["present"] is True
    assert wiring["missing"] == []

    source = _approval_source(tmp_path, with_client_adapter=False)
    broken = module._adapter_wiring(source)
    assert broken["present"] is False
    assert "client_research_namespace" in broken["missing"]

    context = _approval_context(tmp_path / "out-missing-adapter")
    with (
        mock.patch.object(module, "_env_path", return_value=source),
        mock.patch.object(module, "_node", return_value="node"),
        mock.patch.object(
            module,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, _node_log(module), ""),
        ),
    ):
        code = module.scenario_approval_budget_live(context)

    assert code == 1
    statuses = {item["name"]: item["status"] for item in context.assertions}
    # The feature still owns no transport ...
    assert statuses["approval.feature_no_direct_transport"] == "PASS"
    # ... but that no longer makes the row PASS: the adapter is missing.
    assert statuses["approval.injected_adapter_wiring_present"] == "FAIL"
    assert "approval.fixture_content_no_platform_transport" not in statuses


def test_approval_row_publishes_fixture_observations(scenario_modules, tmp_path):
    module = scenario_modules["approval"]
    source = _approval_source(tmp_path, with_client_adapter=True)
    context = _approval_context(tmp_path / "out-adapter")
    with (
        mock.patch.object(module, "_env_path", return_value=source),
        mock.patch.object(module, "_node", return_value="node"),
        mock.patch.object(
            module,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, _node_log(module), ""),
        ),
    ):
        code = module.scenario_approval_budget_live(context)

    assert code == 0
    statuses = {item["name"]: item["status"] for item in context.assertions}
    assert statuses["approval.injected_adapter_wiring_present"] == "PASS"
    assert statuses["approval.public_evidence_kind_fixture"] == "PASS"
    observations = context.extensions["experience.observations"]
    assert observations["model_calls"] == 0
    assert set(observations["observed"].values()) == {"absent"}
    assert observations["offline_fixture_content"] is False


# ---------------------------------------------------------------------------
# research-loop-live: live ports instead of hardcoded delivery booleans
# ---------------------------------------------------------------------------


def test_research_loop_probes_every_declared_live_port(scenario_modules):
    module = scenario_modules["research_loop"]
    ports = module._probe_live_ports(REPO_ROOT)
    assert [port["name"] for port in ports] == [entry["name"] for entry in module.LIVE_PORTS]
    for port in ports:
        assert port["path"] and port["anchor"]
        assert port["probe"].startswith("grep -Fn -- ")
        assert isinstance(port["present"], bool)
    present = {port["name"] for port in ports if port["present"]}
    assert {"supervisor_skill", "controller_research_read"} <= present


def test_research_loop_has_no_hardcoded_delivery_booleans(scenario_modules):
    module = scenario_modules["research_loop"]
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "model_delivered" not in text
    assert "controller_delivered" not in text
    assert '"offline_fixture"' not in text


def _research_loop_source(tmp_path: Path) -> Path:
    source = tmp_path / "c-source"
    (source / "rph").mkdir(parents=True, exist_ok=True)
    (source / "rph/package.json").write_text("{}\n", encoding="utf-8")
    for relative in (
        *(
            f"rph/packages/dsh-bundle/skills/{name}.ts"
            for name in (
                "interpreter",
                "synthesizer",
                "reviewer",
                "planner",
                "supervisor",
                "schema",
                "models",
                "index",
            )
        ),
        "rph/test/research-loop.test.ts",
        "rph/scripts/research-loop-offline-proof.mjs",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// stub\n", encoding="utf-8")
    return source


def test_research_loop_missing_implementation_is_not_verified_not_pass(scenario_modules, tmp_path):
    module = scenario_modules["research_loop"]
    source = _research_loop_source(tmp_path)
    context = SimpleNamespace(
        identity=SimpleNamespace(
            scenario="experience:research-loop-live",
            scenario_key="research-loop-live",
            phase="experience:control",
            subcase="control",
            evidence_kind="live",
            mode="live",
        ),
        run_id="research-loop-live-r1",
        out_dir=tmp_path / "out",
        timeout=60.0,
        assertions=[],
        artifacts=[],
        extensions={},
        refs={},
        usage=None,
        reason=None,
    )
    with (
        mock.patch.object(module, "_env_path", return_value=source),
        mock.patch.object(module, "_pnpm", return_value=None),
        mock.patch.object(module, "_node", return_value=None),
    ):
        code = module.scenario_research_loop_live(context)

    assert code == 2
    assert context.reason is not None
    assert context.reason.startswith("implementation_missing:")
    assert "live_loop_runner" in context.reason

    report = json.loads((tmp_path / "out/control-report.json").read_text())
    assert report["content_kind"] == "live_port_probe"
    assert report["diagnostic"]["content_kind"] == "offline_diagnostic"
    assert report["diagnostic"]["live_model_calls"] == 0
    assert report["live"]["missing_ports"]
    assert report["live"]["executable_probes"]
    assert "evidence_kind" not in json.dumps(report)
    text = json.dumps(report)
    assert "model_delivered" not in text
    assert "controller_delivered" not in text

    observations = context.extensions["experience.observations"]
    assert observations["observed"]["model"] == "absent"
    assert observations["model_calls"] == 0
    assert observations["offline_fixture_content"] is False
    statuses = {item["name"]: item["status"] for item in context.assertions}
    assert statuses["research_loop.live_port_probe_executed"] == "PASS"


def test_research_loop_offline_proof_never_turns_the_live_row_into_pass(scenario_modules, tmp_path):
    module = scenario_modules["research_loop"]
    source = _research_loop_source(tmp_path)
    out = tmp_path / "out-proof"
    context = SimpleNamespace(
        identity=SimpleNamespace(
            scenario="experience:research-loop-live",
            scenario_key="research-loop-live",
            phase="experience:control",
            subcase="control",
            evidence_kind="live",
            mode="live",
        ),
        run_id="research-loop-live-r2",
        out_dir=out,
        timeout=60.0,
        assertions=[],
        artifacts=[],
        extensions={},
        refs={},
        usage=None,
        reason=None,
    )

    def fake_run(argv, *, cwd=None, timeout=60.0, env=None):
        if "--out" in argv:
            proof_dir = Path(argv[argv.index("--out") + 1])
            proof_dir.mkdir(parents=True, exist_ok=True)
            (proof_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "schema": "acp2.experience.research-loop.offline-proof.v1",
                        "all_passed": True,
                        "cases": [{"id": index} for index in range(7)],
                        "assertions": len(module.REQUIRED_PROOF_ASSERTIONS),
                        "live_model_calls": 0,
                    }
                ),
                encoding="utf-8",
            )
            (proof_dir / "assertion-table.json").write_text(
                json.dumps(
                    [{"name": name, "status": "PASS"} for name in module.REQUIRED_PROOF_ASSERTIONS]
                ),
                encoding="utf-8",
            )
            (proof_dir / "run-log.json").write_text("[]\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with (
        mock.patch.object(module, "_env_path", return_value=source),
        mock.patch.object(module, "_pnpm", return_value="pnpm"),
        mock.patch.object(module, "_node", return_value="node"),
        mock.patch.object(module, "_run", side_effect=fake_run),
    ):
        code = module.scenario_research_loop_live(context)

    # A fully passing offline diagnostic is still not a live delivery.
    assert code == 2
    assert context.reason is not None
    assert context.reason.startswith("implementation_missing:")
    report = json.loads((out / "control-report.json").read_text())
    assert report["diagnostic"]["status"] == "PASS"
    assert report["diagnostic"]["all_passed"] is True
    assert report["live"]["result"].startswith("NOT_VERIFIED(implementation_missing:")
    statuses = {item["name"]: item["status"] for item in context.assertions}
    assert statuses["research_loop.offline_proof_all_passed"] == "PASS"
    assert statuses["research_loop.required_offline_assertions"] == "PASS"


def test_research_loop_live_row_end_to_end_is_not_verified(tmp_path: Path):
    out = tmp_path / "research-loop-live/run-r10-0001/main/live/control"
    env = dict(os.environ)
    env.update(
        {
            "ACP2_RUNTIME_PATH": str(RUNTIME_PATH),
            "ACP2_PROJECT_ROOT": str(REPO_ROOT),
            "ACP2_C_SOURCE": str(REPO_ROOT),
        }
    )
    env.pop("ACP2_RUN_ID", None)
    # Keep the offline Node diagnostic out of this test: it is a separate
    # (pnpm/node) gate and must not build the workspace from a pytest run.
    env.pop("ACP2_NODE_BIN", None)
    env.pop("ACP2_PNPM_BIN", None)
    env["PATH"] = "/nonexistent-m003b-bin"
    completed = subprocess.run(
        [
            sys.executable,
            str(HARNESS_PATH),
            "--scenario",
            "research-loop-live",
            "--out",
            str(out),
            "--timeout",
            "120",
            "--subcase",
            "control",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert completed.returncode == 2, completed.stderr + completed.stdout
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in out.glob("*.json")
        if path.name.endswith(".json")
    ]
    receipts = [item for item in receipts if item.get("schema") == "acp2.acceptance.v1"]
    assert len(receipts) == 1, receipts
    receipt = receipts[0]
    assert receipt["result"] == "NOT_VERIFIED"
    assert receipt["evidence_kind"] == "live"
    assert receipt["reason"].startswith("implementation_missing:")
    assert receipt["extensions"]["experience.observations"]["model_calls"] == 0
    assert receipt["extensions"]["experience.live_ports"]["executable_probes"]
    assert "model_delivered" not in json.dumps(receipt)
