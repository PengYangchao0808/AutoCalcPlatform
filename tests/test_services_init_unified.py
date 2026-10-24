"""Unified ``acp init`` step-flow tests (B07-2 / plan todo 36).

Covers the acceptance criteria:

* the step flow (usage → tier → resources → components → params/secrets →
  preview/apply → doctor) with refusal semantics — an unverified tier
  (Hosted) and a tier with missing component/resource dependencies are
  refused, never silently downgraded;
* secret *references* only — a raw secret value can never enter a plan and
  never appears in the rendered plan/log;
* the non-interactive answer-file path (Standalone config → doctor) plus the
  failure paths (refused tier, plaintext secret, version mismatch);
* single config source — no second defaults / no second ``resolve_run_root``
  anywhere under ``src/acp/init_wizard/`` (grep guard).

Prompts are injected through the ``PromptBundle`` seam; no TTY is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import acp.init_wizard.capabilities as capabilities_module
from acp.cli import build_parser
from acp.init_wizard import (
    COMPONENT_SPECS,
    build_plan,
    collect_capability_report,
    load_answers,
    plan_from_answers,
    run_init,
    tier_enablement,
)
from acp.init_wizard.answers import ANSWER_SCHEMA_VERSION
from acp.init_wizard.capabilities import CheckState
from acp.init_wizard.options import (
    ComponentCategory,
    default_component_selection,
    tier_spec_for_key,
)
from acp.init_wizard.persist import InitAbort
from acp.init_wizard.plan import (
    PLAN_VERSION,
    apply_plan,
    digest_of,
    make_secret_ref,
    plan_to_dict,
    render_plan,
)

_INIT_WIZARD_DIR = Path(__file__).resolve().parents[1] / "src" / "acp" / "init_wizard"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hermetic HOME + cwd so no real ``~/.cccp.yaml`` or run_root is touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_real_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the doctor report deterministic (no PATH probing in unit tests)."""
    monkeypatch.setattr(capabilities_module, "resolve_executable", lambda name, path=None: None)


class FakePrompts:
    """Scripted PromptBundle replacement recording every call."""

    def __init__(self, menus: list[int] | None = None, asks: list[str] | None = None) -> None:
        self.menus = list(menus or [])
        self.asks = list(asks or [])
        self.menu_calls: list[tuple[str, list[str], bool]] = []
        self.ask_calls: list[str] = []

    def menu(self, title: str, options: list[str], allow_q: bool = True) -> int:
        self.menu_calls.append((title, list(options), allow_q))
        assert self.menus, f"unexpected menu prompt: {title}"
        return self.menus.pop(0)

    def ask(
        self,
        prompt: str,
        default: str | None = None,
        validate: Any = None,
        allow_empty: bool = False,
    ) -> str:
        self.ask_calls.append(prompt)
        if self.asks:
            raw = self.asks.pop(0)
            return raw
        return default if default is not None else ""

    def ask_secret(self, prompt: str) -> str:  # pragma: no cover - never used
        raise AssertionError("the unified wizard must never read a secret value")

    def ask_local_path(self, prompt: str) -> Path:  # pragma: no cover - never used
        raise AssertionError("unexpected ask_local_path")

    def ask_remote_dir(self, prompt: str, default: str) -> str:  # pragma: no cover
        raise AssertionError("unexpected ask_remote_dir")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_answers(tmp_path: Path, payload: dict[str, Any], name: str = "answers.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Step 2 — tier verification / refusal (no silent downgrade)
# --------------------------------------------------------------------------- #


def test_hosted_tier_is_unverified_and_refused() -> None:
    hosted = tier_spec_for_key("hosted")
    assert hosted is not None
    verdict = tier_enablement(hosted, default_component_selection(), ())
    assert verdict.enabled is False
    assert verdict.error_code == "tier_unverified"
    assert "不可启用" in verdict.detail


def test_default_components_exclude_payment_object_store_external_iam() -> None:
    excluded = {
        ComponentCategory.PAYMENT,
        ComponentCategory.OBJECT_STORE,
        ComponentCategory.EXTERNAL_IAM,
    }
    specs = [spec for spec in COMPONENT_SPECS if spec.category in excluded]
    assert specs  # the categories exist in the manifest
    selection = set(default_component_selection())
    for spec in specs:
        assert spec.default_enabled is False
        assert spec.name not in selection


def test_lab_tier_refused_without_capability_components() -> None:
    lab = tier_spec_for_key("lab")
    assert lab is not None
    verdict = tier_enablement(lab, default_component_selection(), {"database"})
    assert verdict.enabled is False
    assert verdict.error_code == "tier_capability_missing"
    assert "不静默降级" in verdict.detail


def test_lab_tier_refused_when_required_resource_missing() -> None:
    lab = tier_spec_for_key("lab")
    assert lab is not None
    components = (*default_component_selection(), "web-ui", "authentication", "isolation")
    verdict = tier_enablement(lab, components, ())
    assert verdict.enabled is False
    assert verdict.error_code == "tier_resources_missing"


def test_lab_tier_enabled_when_requirements_met() -> None:
    lab = tier_spec_for_key("lab")
    assert lab is not None
    components = (*default_component_selection(), "web-ui", "authentication", "isolation")
    verdict = tier_enablement(lab, components, {"database"})
    assert verdict.enabled is True
    assert verdict.detail == ""


# --------------------------------------------------------------------------- #
# Secrets — references only, never values
# --------------------------------------------------------------------------- #


def test_make_secret_ref_rejects_raw_value() -> None:
    with pytest.raises(InitAbort):
        make_secret_ref("db_password", "hunter2")


def test_render_plan_shows_reference_only(tmp_path: Path) -> None:
    ref = make_secret_ref("db_password", "env:ACP_DB_PASSWORD")
    plan = build_plan(
        tmp_path / "cfg.yaml",
        {},
        usage="local",
        tier="local-single",
        components=default_component_selection(),
        params={"run_root": str(tmp_path / "runs")},
        secrets=(ref,),
    )
    rendered = render_plan(plan)
    assert "db_password <- env:ACP_DB_PASSWORD" in rendered
    assert "hunter2" not in rendered
    public = plan_to_dict(plan)
    assert public["secrets"] == [{"name": "db_password", "source": "env", "ref": "ACP_DB_PASSWORD"}]
    assert "ACP_DB_PASSWORD" not in str(public["params"])


# --------------------------------------------------------------------------- #
# Plan build / apply (idempotent + concurrent-modification guard)
# --------------------------------------------------------------------------- #


def test_plan_is_versioned_with_digest(tmp_path: Path) -> None:
    base = {"executables": {"orca": {"path": "/opt/orca"}}}
    plan = build_plan(
        tmp_path / "cfg.yaml",
        base,
        usage="local",
        tier="local-single",
        components=default_component_selection(),
    )
    assert plan.version == PLAN_VERSION
    assert plan.digest == digest_of(base)
    assert plan.enabled is True
    assert any(change.path == "deployment.usage" for change in plan.changes)


def test_apply_plan_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    plan = build_plan(
        target,
        {},
        usage="local",
        tier="local-single",
        components=default_component_selection(),
        params={"run_root": str(tmp_path / "runs")},
    )
    first = apply_plan(plan)
    assert first.changed is True
    assert _load(target)["deployment"]["tier"] == "local-single"
    second = apply_plan(plan)
    assert second.applied is True
    assert second.changed is False


def test_apply_plan_refuses_concurrent_modification(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    target.write_text("nproc: 4\n", encoding="utf-8")
    plan = build_plan(
        target,
        _load(target),
        usage="local",
        tier="local-single",
        components=default_component_selection(),
    )
    target.write_text("nproc: 8\n", encoding="utf-8")
    with pytest.raises(InitAbort):
        apply_plan(plan)


# --------------------------------------------------------------------------- #
# Step 7 — doctor capability report (three states split)
# --------------------------------------------------------------------------- #


def test_capability_report_splits_three_states(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path / "cfg.yaml",
        {},
        usage="local",
        tier="local-single",
        components=(*default_component_selection(), "authentication"),
    )
    report = collect_capability_report(plan)
    assert report.by_state(CheckState.FAIL) == ()
    assert any(check.name == "component:science-core" for check in report.by_state(CheckState.PASS))
    assert any(
        check.name == "component:authentication"
        for check in report.by_state(CheckState.NOT_VERIFIED)
    )
    assert any(
        check.name.startswith("software:") for check in report.by_state(CheckState.NOT_VERIFIED)
    )
    assert report.ok is True


def test_capability_report_fails_on_configured_missing_binary(tmp_path: Path) -> None:
    plan = build_plan(
        tmp_path / "cfg.yaml",
        {},
        usage="local",
        tier="local-single",
        components=default_component_selection(),
        resources={"software": {"orca": str(tmp_path / "missing-orca")}},
    )
    report = collect_capability_report(plan)
    fails = [check for check in report.by_state(CheckState.FAIL) if check.name == "software:orca"]
    assert fails
    assert report.ok is False


def test_capability_report_flags_refused_tier(tmp_path: Path) -> None:
    lab = tier_spec_for_key("lab")
    assert lab is not None
    plan = build_plan(
        tmp_path / "cfg.yaml",
        {},
        usage="local",
        tier=lab.tier,
        components=default_component_selection(),
    )
    report = collect_capability_report(plan)
    assert any(check.name == "deployment-tier" for check in report.by_state(CheckState.FAIL))


# --------------------------------------------------------------------------- #
# Non-interactive answer-file path
# --------------------------------------------------------------------------- #


def test_answer_file_standalone_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    answers = _write_answers(
        tmp_path,
        {
            "version": ANSWER_SCHEMA_VERSION,
            "usage": "local",
            "tier": "standalone",
            "params": {"run_root": str(tmp_path / "runs")},
            "resources": {"cluster": {"type": "local"}},
        },
    )
    assert run_init(target, answers_path=answers) == 0
    config = _load(target)
    assert config["deployment"]["usage"] == "local"
    assert config["deployment"]["tier"] == "local-single"
    assert "science-core" in config["deployment"]["components"]
    out = capsys.readouterr().out
    assert "PASS" in out and "NOT_VERIFIED" in out and "FAIL" in out


def test_answer_file_lab_refused_without_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    answers = _write_answers(
        tmp_path,
        {"version": ANSWER_SCHEMA_VERSION, "usage": "local", "tier": "lab"},
    )
    assert run_init(target, answers_path=answers) == 1
    assert not target.exists()  # refused: nothing written, no silent downgrade
    assert "拒绝" in capsys.readouterr().out


def test_answer_file_rejects_plaintext_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    answers = _write_answers(
        tmp_path,
        {
            "version": ANSWER_SCHEMA_VERSION,
            "usage": "local",
            "tier": "standalone",
            "secrets": [{"name": "db_password", "value": "hunter2"}],
        },
    )
    assert run_init(target, answers_path=answers) == 1
    assert not target.exists()
    assert "明文" in capsys.readouterr().out


def test_answer_file_version_mismatch(tmp_path: Path) -> None:
    answers = _write_answers(
        tmp_path, {"version": ANSWER_SCHEMA_VERSION + 1, "usage": "local", "tier": "standalone"}
    )
    with pytest.raises(InitAbort):
        load_answers(answers)


def test_answer_file_connect_mode_writes_service_only(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    answers = _write_answers(
        tmp_path,
        {
            "version": ANSWER_SCHEMA_VERSION,
            "usage": "connect",
            "service": {
                "url": "https://acp.example.org",
                "project": "demo",
                "credential_ref": "env:ACP_TOKEN",
            },
        },
    )
    assert run_init(target, answers_path=answers) == 0
    config = _load(target)
    assert config["deployment"]["usage"] == "connect"
    assert config["deployment"]["service"]["url"] == "https://acp.example.org"
    assert "tier" not in config["deployment"]
    assert "cluster" not in config  # no local cluster/db created


# --------------------------------------------------------------------------- #
# Interactive step flow (scripted PromptBundle seam)
# --------------------------------------------------------------------------- #


def test_interactive_local_standalone_applies_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(menus=[1, 1, 0, 1, 0])
    assert run_init(target, prompts=prompts) == 0
    config = _load(target)
    assert config["deployment"]["tier"] == "local-single"
    assert config["deployment"]["usage"] == "local"
    titles = [call[0] for call in prompts.menu_calls]
    assert "选择使用方式" in titles
    assert "选择部署档位" in titles
    assert "doctor 能力报告" in capsys.readouterr().out


@pytest.mark.parametrize("origin", ["https://new.example:9443", "http://192.0.2.10:8765", "-"])
def test_interactive_public_origin_saved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    origin: str,
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(
        menus=[1, 1, 0, 1, 0],
        asks=["", str(tmp_path / "runs"), origin, ""],
    )
    assert run_init(target, prompts=prompts) == 0
    assert _load(target)["deployment"]["params"]["public_origin"] == (
        "" if origin == "-" else origin
    )
    assert any("外部访问地址" in prompt for prompt in prompts.ask_calls)
    assert "需重启后生效" in capsys.readouterr().out


def test_interactive_public_origin_preserved_on_reentry(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "deployment": {
                    "params": {
                        "public_origin": "https://existing.example",
                        "custom_setting": 42,
                    }
                }
            }
        )
    )
    prompts = FakePrompts(menus=[1, 1, 0, 1, 1, 1, 0, 1, 0])
    assert run_init(target, prompts=prompts) == 0
    params = _load(target)["deployment"]["params"]
    assert params["public_origin"] == "https://existing.example"
    assert params["custom_setting"] == 42


@pytest.mark.parametrize("origin", ["https://acp.example/path", "null", 123, None])
def test_answer_file_invalid_public_origin_does_not_write(
    tmp_path: Path,
    origin: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "cfg.yaml"
    original = "unrelated: keep\n"
    target.write_text(original)
    answers = _write_answers(
        tmp_path,
        {
            "version": ANSWER_SCHEMA_VERSION,
            "usage": "local",
            "tier": "standalone",
            "params": {"public_origin": origin},
        },
    )
    assert run_init(target, answers_path=answers) == 1
    assert target.read_text() == original
    assert not list(tmp_path.glob("cfg.yaml.bak-*"))
    assert "public_origin" in capsys.readouterr().out


def test_answer_file_preview_public_origin_does_not_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "cfg.yaml"
    answers = _write_answers(
        tmp_path,
        {
            "version": ANSWER_SCHEMA_VERSION,
            "usage": "local",
            "tier": "standalone",
            "params": {"public_origin": "https://preview.example"},
        },
    )
    assert run_init(target, answers_path=answers, plan_only=True) == 0
    assert not target.exists()
    assert "https://preview.example" in capsys.readouterr().out


def test_answer_file_partial_params_preserve_public_origin(tmp_path: Path) -> None:
    target = tmp_path / "cfg.yaml"
    base = {"deployment": {"params": {"public_origin": "https://existing.example"}}}
    plan = plan_from_answers(
        target,
        base,
        {
            "usage": "local",
            "tier": "standalone",
            "params": {"run_root": str(tmp_path / "runs")},
        },
    )
    assert plan.planned_data["deployment"]["params"]["public_origin"] == "https://existing.example"


def test_interactive_hosted_selection_is_refused_then_standalone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(menus=[1, 3, 1, 0, 1, 0])
    assert run_init(target, prompts=prompts) == 0
    assert "不可启用" in capsys.readouterr().out
    assert _load(target)["deployment"]["tier"] == "local-single"


def test_interactive_lab_without_components_refused_no_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(menus=[1, 2, 0, 0])
    assert run_init(target, prompts=prompts) == 0
    assert not target.exists()
    assert "档位启用被拒绝" in capsys.readouterr().out


def test_interactive_connect_usage_configures_service(
    tmp_path: Path,
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(
        menus=[2, 1, 0],
        asks=["https://acp.example.org", "demo", "env:ACP_TOKEN"],
    )
    assert run_init(target, prompts=prompts) == 0
    config = _load(target)
    assert config["deployment"]["usage"] == "connect"
    assert config["deployment"]["service"]["credential_ref"] == "env:ACP_TOKEN"


def test_interactive_plan_only_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "cfg.yaml"
    prompts = FakePrompts(menus=[1, 1, 0, 0])
    assert run_init(target, prompts=prompts, plan_only=True) == 0
    assert not target.exists()
    assert "--plan" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_parser_accepts_answers_and_plan(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "init",
            "--config",
            str(tmp_path / "c.yaml"),
            "--answers",
            str(tmp_path / "a.yaml"),
            "--plan",
        ]
    )
    assert args.answers == tmp_path / "a.yaml"
    assert args.plan is True


# --------------------------------------------------------------------------- #
# Single config source — no second defaults anywhere in the wizard
# --------------------------------------------------------------------------- #


def test_no_second_config_defaults_in_init_wizard() -> None:
    forbidden = (
        "def _get_default_config",
        "def resolve_run_root",
        "/var/lib/acp/runs",
        ".local/share",
        "XDG_DATA_HOME",
    )
    offenders: list[str] = []
    for path in sorted(_INIT_WIZARD_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


def test_component_manifest_carries_no_config_defaults() -> None:
    options_text = (_INIT_WIZARD_DIR / "options.py").read_text(encoding="utf-8")
    for needle in ("nproc", "maxcore", "temperature_k", "gfn_level"):
        assert needle not in options_text


def test_run_init_signature_stays_single_positional() -> None:
    """The CLI must be able to keep calling ``run_init(config_path)``."""
    import inspect

    params = inspect.signature(run_init).parameters
    positional = [
        name
        for name, param in params.items()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    assert positional == ["config_path"]


def test_plan_from_answers_unknown_component_rejected(tmp_path: Path) -> None:
    answers = {
        "version": ANSWER_SCHEMA_VERSION,
        "usage": "local",
        "tier": "standalone",
        "components": ["science-core", "not-a-component"],
    }
    with pytest.raises(InitAbort):
        plan_from_answers(tmp_path / "cfg.yaml", {}, answers)
