"""B09-5 (plan todo 52) — Lab init/doctor over validated combos.

Covers the acceptance criteria:

1. **Four-state capability detection.** ``PASS`` / ``FAIL`` / ``NOT_VERIFIED`` /
   ``UNAVAILABLE`` are distinct and never conflated: a ``NOT_VERIFIED`` row is
   never a pass and never counts toward enablement.
2. **Refuse, never downgrade.** A missing dependency disables the capability
   that needs it; an authentication-less Lab refuses anonymous, a quota-less
   storage refuses no-quota operation, an HTTPS-less entry refuses plaintext.
3. **Doctor report schema.** The serialised report has the stable keys a
   consumer parses, with the four states split.
4. **Identity honesty.** OIDC is never marked available; the vocabulary mirrors
   ``ACP_Services_Identity_Support_Scope.md``; the Key-only scope is stated.
5. **Slurm is demand-only and never blocks Lab.**
6. **Module isolation.** ``acp_services.runtime.lab_capabilities`` imports no
   ``acp``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from acp_services.contracts.access import DeploymentTier, ExecutionMode
from acp_services.runtime.isolation import IsolationMechanism, LsfExecutionAccount, OsIdentity
from acp_services.runtime.lab_capabilities import (
    LabCapability,
    LabCapabilityName,
    LabCapabilityRefusedError,
    LabCapabilityReport,
    LabCapabilityState,
    LabEnvironment,
    LabProbes,
    LabRefusalReason,
    build_lab_capability_report,
    reference_target,
    render_lab_capability_report,
    resolve_reference,
)
from acp_services.workspace.boundary import TierCapability
from acp_services.workspace.identity_lifecycle import (
    OIDC_SUPPORTED,
    IdentityMode,
    identity_support_scope,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LAB_MODULE = _REPO_ROOT / "src" / "acp_services" / "runtime" / "lab_capabilities.py"
_IDENTITY_DOC = (
    _REPO_ROOT / "docs" / "reference" / "next-change" / "ACP_Services_Identity_Support_Scope.md"
)

_LAB_CAPS = (TierCapability.AUTHENTICATION, TierCapability.ISOLATION)

#: A real, valid self-signed certificate (CN=acp-lab-test); used only to prove
#: the certificate-parse probe distinguishes a valid PEM from a broken one.
_TEST_CERT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIDDzCCAfegAwIBAgIUZ/aW64XQ/IvBMLe2IiuN78ssLI0wDQYJKoZIhvcNAQEL
BQAwFzEVMBMGA1UEAwwMYWNwLWxhYi10ZXN0MB4XDTI2MTAxOTA1NTExNFoXDTM2
MTAxNjA1NTExNFowFzEVMBMGA1UEAwwMYWNwLWxhYi10ZXN0MIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnOqN+Kp1UxrqRDWIruFMpIeGYb2UXyjECTwm
gkygLsBf7YCJB/JnTItuV9wvXWIPP28jMAH3lRjJfSPUxBxYjoj05YddyQjjhvO7
KcvV9gW7OtxGzfbvb7qh+kQzRmHdWaGV/LQ6f3XvAYL+X8QQZVg7yXZcQh8gwMwx
jAKweYBM8neZkrq5YQR6tVfx0qGRf4gxXluGPCZATy2BAgoiohf5iLOrvSogJjP9
4lXIjso+N0IfC2xixQwR8+GvweFAkIEmQchNc5nNTlDzRi9qmwrRiGROdLqiaxeh
JSay74UFAc/TXdUhrklRUE1RBPj2ZJGilcNxfH913cxIuGdBMQIDAQABo1MwUTAd
BgNVHQ4EFgQUYTLVbx1SNtY8j8heo+kTve79+IMwHwYDVR0jBBgwFoAUYTLVbx1S
NtY8j8heo+kTve79+IMwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOC
AQEAHLjfPxMZDK0mJ/TZl02uv08c8bLMZ1F5cH/Q8yje4Wn0K1OJjqw5iqHwcVut
ujt+v2S8Ipe59Ymehhb7RUJqlLK8622bwV/Gn5hFI+iQd9l0ANh4qoG2LTm7h1Pj
vcSBHi9VuG13+OGOH3QTFzUXl/vWG2nZ3MZtz05uEx748xVU3ImzfzFndbblMjZx
hUJwi0shtoK79CyTBMyfR1RM/y4BmELPvo5gONvWlj6lgz1pm0U8Ek3xGfoUm5Xp
abQAoqMUaxfY0lIx3O98vxJbe3GOmzBXMSzCCjG9SFev7593c9wWBPAB9WQTWqwA
MmMVlbBt6TIVfEeHH1O8RZgVAg==
-----END CERTIFICATE-----
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _happy_env(tmp_path: Path, **overrides) -> LabEnvironment:
    env_file = tmp_path / "secret.txt"
    env_file.write_text("not-a-real-secret", encoding="utf-8")
    cert = tmp_path / "cert.pem"
    cert.write_text(_TEST_CERT_PEM, encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    base: dict = {
        "execution_mode": ExecutionMode.LOCAL,
        "database_ref": "env:ACP_DB_DSN",
        "https_cert_ref": f"file:{cert}",
        "https_requested": True,
        "storage_quota_bytes": 10 * 1024 * 1024,
        "storage_quota_source": "statvfs:/data",
        "quota_requested": True,
        "identity_map": {"alice": OsIdentity(username="acp_alice", uid=6101, gid=6101)},
        "secret_refs": (("db_password", f"file:{env_file}"),),
        "backup_ref": f"file:{backup_dir}",
        "env": {"ACP_DB_DSN": "postgres://acp@db/acp"},
    }
    base.update(overrides)
    return LabEnvironment(**base)


def _happy_probes() -> LabProbes:
    return LabProbes(database=lambda ref: True, backup=lambda ref: True)


def _report(env: LabEnvironment, **kwargs) -> LabCapabilityReport:
    kwargs.setdefault("capabilities", _LAB_CAPS)
    return build_lab_capability_report(env, **kwargs)


# --------------------------------------------------------------------------- #
# 1. Four states are distinct and never conflated
# --------------------------------------------------------------------------- #


def test_capability_states_are_four_distinct_values() -> None:
    assert LabCapabilityState.ALL == {"PASS", "FAIL", "NOT_VERIFIED", "UNAVAILABLE"}
    assert len(LabCapabilityState.ALL) == 4
    # never conflated: each state is its own value
    assert LabCapabilityState.PASS != LabCapabilityState.NOT_VERIFIED
    assert LabCapabilityState.FAIL != LabCapabilityState.UNAVAILABLE


def test_default_environment_is_not_verified_not_available() -> None:
    report = _report(LabEnvironment())
    assert report.ok is False
    assert report.enabled == ()  # nothing is claimed available
    assert LabCapabilityName.ISOLATION in report.unverified
    assert LabCapabilityName.DATABASE in report.unavailable
    assert LabCapabilityName.AUTHENTICATION in report.unverified
    # NOT_VERIFIED must never leak into the enabled set
    assert set(report.unverified).isdisjoint(report.enabled)
    assert set(report.refused) == set(report.unverified) | set(report.unavailable)


def test_fully_verified_environment_passes_required_capabilities(tmp_path: Path) -> None:
    report = _report(_happy_env(tmp_path), probes=_happy_probes())
    assert report.ok is True
    assert report.integrity_ok is True
    assert {
        LabCapabilityName.ISOLATION,
        LabCapabilityName.AUTHENTICATION,
        LabCapabilityName.DATABASE,
        LabCapabilityName.STORAGE_QUOTA,
        LabCapabilityName.HTTPS,
        LabCapabilityName.KEYS,
    } <= set(report.enabled)


def test_declared_but_unprobed_database_is_not_verified_not_pass(tmp_path: Path) -> None:
    env = _happy_env(tmp_path)
    env = LabEnvironment(**{**env.__dict__, "database_ref": "env:ACP_DB_DSN"})
    report = _report(env, probes=None)
    assert report.state_of(LabCapabilityName.DATABASE) == LabCapabilityState.NOT_VERIFIED
    assert report.state_of(LabCapabilityName.DATABASE) != LabCapabilityState.PASS


def test_unresolved_reference_is_fail(tmp_path: Path) -> None:
    env = LabEnvironment(database_ref="env:ACP_MISSING_DSN", env={})
    report = _report(env)
    assert report.state_of(LabCapabilityName.DATABASE) == LabCapabilityState.FAIL
    assert report.integrity_ok is False


# --------------------------------------------------------------------------- #
# 2. Refuse, never downgrade
# --------------------------------------------------------------------------- #


def test_missing_database_refuses_database_capability() -> None:
    report = _report(LabEnvironment())
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.require_enabled(LabCapabilityName.DATABASE)
    assert exc.value.reason_code == LabRefusalReason.CAPABILITY_UNAVAILABLE
    assert exc.value.capability == LabCapabilityName.DATABASE


def test_unverified_isolation_refuses_enablement() -> None:
    report = _report(LabEnvironment())
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.require_enabled(LabCapabilityName.ISOLATION)
    assert exc.value.reason_code == LabRefusalReason.CAPABILITY_NOT_VERIFIED


def test_anonymous_downgrade_refused_without_authentication() -> None:
    report = _report(LabEnvironment())
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.refuse_downgrade("anonymous")
    assert exc.value.reason_code == LabRefusalReason.ANONYMOUS_DOWNGRADE_REFUSED


def test_no_quota_downgrade_refused_when_quota_requested_without_limit() -> None:
    env = LabEnvironment(quota_requested=True, storage_quota_bytes=None)
    report = _report(env)
    assert report.state_of(LabCapabilityName.STORAGE_QUOTA) == LabCapabilityState.FAIL
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.refuse_downgrade("no-quota")
    assert exc.value.reason_code == LabRefusalReason.NO_QUOTA_DOWNGRADE_REFUSED


def test_plaintext_downgrade_refused_when_https_requested_without_cert() -> None:
    report = _report(LabEnvironment(https_requested=True))
    assert report.state_of(LabCapabilityName.HTTPS) == LabCapabilityState.FAIL
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.refuse_downgrade("plaintext")
    assert exc.value.reason_code == LabRefusalReason.PLAINTEXT_DOWNGRADE_REFUSED


def test_refuse_downgrade_passes_when_capability_enabled(tmp_path: Path) -> None:
    report = _report(_happy_env(tmp_path), probes=_happy_probes())
    report.refuse_downgrade("anonymous")
    report.refuse_downgrade("no-quota")
    report.refuse_downgrade("plaintext")


def test_unknown_capability_and_downgrade_refused() -> None:
    report = _report(LabEnvironment())
    with pytest.raises(LabCapabilityRefusedError) as exc:
        report.require_enabled("not-a-capability")
    assert exc.value.reason_code == LabRefusalReason.UNKNOWN_CAPABILITY
    with pytest.raises(LabCapabilityRefusedError) as exc2:
        report.refuse_downgrade("frobnicate")
    assert exc2.value.reason_code == LabRefusalReason.UNKNOWN_DOWNGRADE


def test_pseudo_isolation_is_fail_not_a_pass() -> None:
    env = LabEnvironment(
        identity_map={"alice": OsIdentity(username="acp_alice", uid=6101, gid=6101)},
        isolation_mechanisms=(IsolationMechanism.SAME_UID_DIRECTORY,),
    )
    report = _report(env)
    assert report.state_of(LabCapabilityName.ISOLATION) == LabCapabilityState.FAIL
    assert LabCapabilityName.ISOLATION not in report.enabled


# --------------------------------------------------------------------------- #
# 3. Individual capability detection
# --------------------------------------------------------------------------- #


def test_lsf_mode_without_mapping_fails() -> None:
    env = LabEnvironment(execution_mode=ExecutionMode.LSF, lsf_accounts={})
    report = _report(env)
    assert report.state_of(LabCapabilityName.LSF_MAPPING) == LabCapabilityState.FAIL


def test_lsf_mapping_configured_is_not_verified_without_live_cluster() -> None:
    account = LsfExecutionAccount(
        principal_id="alice", account="acp_alice", queue="normal", resource_policy="rp"
    )
    env = LabEnvironment(execution_mode=ExecutionMode.LSF, lsf_accounts={"alice": account})
    report = _report(env)
    assert report.state_of(LabCapabilityName.LSF_MAPPING) == LabCapabilityState.NOT_VERIFIED
    # a live probe is the only thing that promotes it to PASS
    live = _report(env, probes=LabProbes(lsf=lambda: True))
    assert live.state_of(LabCapabilityName.LSF_MAPPING) == LabCapabilityState.PASS


def test_https_valid_cert_passes_and_invalid_cert_fails(tmp_path: Path) -> None:
    good = tmp_path / "cert.pem"
    good.write_text(_TEST_CERT_PEM, encoding="utf-8")
    assert (
        _report(LabEnvironment(https_cert_ref=f"file:{good}")).state_of(LabCapabilityName.HTTPS)
        == LabCapabilityState.PASS
    )

    broken = tmp_path / "broken.pem"
    broken.write_text("-----BEGIN CERTIFICATE-----\nnonsense\n-----END CERTIFICATE-----\n")
    assert (
        _report(LabEnvironment(https_cert_ref=f"file:{broken}")).state_of(LabCapabilityName.HTTPS)
        == LabCapabilityState.FAIL
    )


def test_keys_unresolved_fails_provider_unverified(tmp_path: Path) -> None:
    unresolved = _report(LabEnvironment(secret_refs=(("key", "env:ACP_NOT_SET"),), env={}))
    assert unresolved.state_of(LabCapabilityName.KEYS) == LabCapabilityState.FAIL

    provider = _report(LabEnvironment(secret_refs=(("key", "provider:vault/k1"),)))
    assert provider.state_of(LabCapabilityName.KEYS) == LabCapabilityState.NOT_VERIFIED


def test_backup_unconfigured_unavailable_and_probe_promotes(tmp_path: Path) -> None:
    unavailable = _report(LabEnvironment())
    assert unavailable.state_of(LabCapabilityName.BACKUP) == LabCapabilityState.UNAVAILABLE

    dest = tmp_path / "backups"
    dest.mkdir()
    env = LabEnvironment(backup_ref=f"file:{dest}")
    assert _report(env).state_of(LabCapabilityName.BACKUP) == LabCapabilityState.NOT_VERIFIED
    probed = _report(env, probes=LabProbes(backup=lambda ref: True))
    assert probed.state_of(LabCapabilityName.BACKUP) == LabCapabilityState.PASS


def test_reference_resolution_helper() -> None:
    assert reference_target("env:ABC") == ("env", "ABC")
    assert reference_target("hunter2") is None
    assert resolve_reference("env:ABC", env={"ABC": "1"})[0] == "resolved"
    assert resolve_reference("env:ABC", env={})[0] == "unresolved"
    assert resolve_reference("provider:key", env={})[0] == "unverified"
    assert resolve_reference("bogus", env={})[0] == "malformed"


def test_unknown_state_or_name_rejected() -> None:
    from acp_services.runtime.lab_capabilities import LabCapabilityError

    with pytest.raises(LabCapabilityError):
        LabCapability(name="database", state="MAYBE")
    with pytest.raises(LabCapabilityError):
        LabCapability(name="not-a-capability", state=LabCapabilityState.PASS)


# --------------------------------------------------------------------------- #
# 4. Doctor report schema
# --------------------------------------------------------------------------- #


def test_doctor_report_schema(tmp_path: Path) -> None:
    report = _report(_happy_env(tmp_path), probes=_happy_probes())
    payload = report.to_dict()
    assert set(payload) == {
        "tier",
        "execution_mode",
        "ok",
        "integrity_ok",
        "capabilities",
        "states",
        "enabled",
        "refused",
        "identity",
        "oidc",
        "slurm",
    }
    assert set(payload["states"]) == {"PASS", "FAIL", "NOT_VERIFIED", "UNAVAILABLE"}
    capability = payload["capabilities"][0]
    assert set(capability) == {
        "name",
        "state",
        "detail",
        "required",
        "reused",
        "dependency",
        "evidence",
    }
    assert payload["identity"]["deployment_tier"] == DeploymentTier.LAB
    assert payload["oidc"]["supported"] is False
    assert payload["slurm"]["required"] is False

    rendered = render_lab_capability_report(report)
    for token in ("PASS", "FAIL", "NOT_VERIFIED", "UNAVAILABLE"):
        assert token in rendered


# --------------------------------------------------------------------------- #
# 5. Identity honesty (mirror the support-scope doc)
# --------------------------------------------------------------------------- #


def _doc_oidc_row() -> str:
    for line in _IDENTITY_DOC.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "OIDC" in stripped:
            return stripped
    raise AssertionError("OIDC row missing from the identity support scope doc")


def test_oidc_doc_says_unavailable_and_code_agrees() -> None:
    row = _doc_oidc_row()
    assert "不可用" in row or "NOT available" in row
    assert OIDC_SUPPORTED is False

    report = _report(LabEnvironment(), capabilities=_LAB_CAPS)
    oidc = report.get(LabCapabilityName.OIDC)
    assert oidc is not None
    assert oidc.state != LabCapabilityState.PASS
    assert oidc.state in {LabCapabilityState.NOT_VERIFIED, LabCapabilityState.UNAVAILABLE}
    assert report.to_dict()["oidc"]["supported"] is False
    assert LabCapabilityName.OIDC not in report.enabled
    # the identity support scope never claims OIDC either
    assert report.identity.oidc_supported is False


def test_key_only_scope_stated_for_local_single() -> None:
    scope = identity_support_scope(DeploymentTier.LOCAL_SINGLE)
    assert scope.mode == IdentityMode.KEY_ONLY
    assert scope.sessions_supported is False
    assert any("Key-only" in line for line in scope.limitations)
    assert any("OIDC" in line for line in scope.limitations)


# --------------------------------------------------------------------------- #
# 6. Slurm is demand-only and never blocks Lab
# --------------------------------------------------------------------------- #


def test_slurm_is_demand_only_and_never_blocks(tmp_path: Path) -> None:
    report = _report(_happy_env(tmp_path), probes=_happy_probes(), slurm_requested=True)
    assert report.slurm["required"] is False
    assert report.slurm["blocks_lab"] is False
    assert report.ok is True  # missing/absent Slurm does not block Lab
    assert "slurm" not in {cap.name for cap in report.capabilities}

    # request Slurm on a bare env: it still never enters the capability rows
    bare = _report(LabEnvironment(), slurm_requested=True)
    assert bare.slurm["required"] is False
    assert bare.slurm["blocks_lab"] is False


# --------------------------------------------------------------------------- #
# 7. Plan bridge + init/doctor wiring
# --------------------------------------------------------------------------- #


def test_plan_bridge_maps_reused_infrastructure(tmp_path: Path) -> None:
    from acp.init_wizard.lab_doctor import lab_environment_from_plan
    from acp.init_wizard.options import default_component_selection
    from acp.init_wizard.plan import build_plan

    cert = tmp_path / "cert.pem"
    cert.write_text(_TEST_CERT_PEM, encoding="utf-8")
    db_ref = tmp_path / "db_dsn.txt"
    db_ref.write_text("postgres://acp@db/acp", encoding="utf-8")
    components = (*default_component_selection(), "web-ui", "authentication", "isolation")
    plan = build_plan(
        tmp_path / "cfg.yaml",
        {},
        usage="local",
        tier="lab",
        components=components,
        resources={
            "external": {
                "database": {"ref": f"file:{db_ref}"},
                "https": {"ref": f"file:{cert}"},
            }
        },
        params={
            "lab": {
                "quota_bytes": 2048,
                "quota_source": "statvfs:/data",
                "quota_requested": True,
            }
        },
    )
    env = lab_environment_from_plan(plan)
    assert env.database_ref == f"file:{db_ref}"
    assert env.https_cert_ref == f"file:{cert}"
    assert env.storage_quota_bytes == 2048
    assert env.quota_requested is True
    assert env.execution_mode == ExecutionMode.LOCAL

    from acp.init_wizard.lab_doctor import collect_lab_capability_report

    report = collect_lab_capability_report(plan, probes=LabProbes(database=lambda ref: True))
    assert report.get(LabCapabilityName.DATABASE) is not None
    assert report.get(LabCapabilityName.DATABASE).reused is True
    assert report.get(LabCapabilityName.DATABASE).state == LabCapabilityState.PASS


def test_init_answer_file_lab_doctor_reports_unverified_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    from acp.init_wizard.answers import ANSWER_SCHEMA_VERSION
    from acp.init_wizard.flows import run_init

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        yaml.dump(
            {
                "version": ANSWER_SCHEMA_VERSION,
                "usage": "local",
                "tier": "lab",
                "components": [
                    *[
                        "science-core",
                        "local-identity",
                        "local-storage",
                        "web-ui",
                        "authentication",
                        "isolation",
                    ]
                ],
                "resources": {"database": {"ref": "env:ACP_DB_DSN"}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACP_DB_DSN", "postgres://acp@db/acp")
    target = tmp_path / "cfg.yaml"
    code = run_init(target, answers_path=answers)
    out = capsys.readouterr().out
    # Lab tier recipe may be applied, but the environment is NOT verified here
    # (no restricted identity map / live LSF), so doctor is honestly not-ok.
    assert code == 1
    assert "Lab 能力报告" in out
    assert "隔离" not in out or "isolation" in out  # capability rows use canonical names


# --------------------------------------------------------------------------- #
# 8. Module isolation
# --------------------------------------------------------------------------- #


def _scan_acp_imports(source: str) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            offenders += [
                alias.name
                for alias in node.names
                if alias.name == "acp" or alias.name.startswith("acp.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "acp" or module.startswith("acp."):
                offenders.append(module)
    return offenders


def test_lab_capabilities_module_has_no_acp_import() -> None:
    assert _scan_acp_imports(_LAB_MODULE.read_text(encoding="utf-8")) == []
