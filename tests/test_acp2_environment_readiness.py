"""M0-10 environment readiness checker: tri-state, secret redaction, gating.

Every probe is faked through monkeypatched helpers, so the suite never touches a
real ORCA/xTB/CREST/LSF/Node/DSH installation and never contacts a model or SSH
host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/acceptance"))

import check_acp2_environment as readiness


class FakeCommands:
    """Substring-routed replacement for ``_run_command``."""

    def __init__(self) -> None:
        self._rules: list[tuple[str, readiness.CommandResult]] = []
        self.calls: list[tuple[str, ...]] = []

    def add(
        self,
        contains: str,
        stdout: str = "",
        *,
        rc: int = 0,
        stderr: str = "",
        error: str | None = None,
    ) -> None:
        self._rules = [rule for rule in self._rules if rule[0] != contains]
        self._rules.append(
            (contains, readiness.CommandResult((), rc, stdout=stdout, stderr=stderr, error=error))
        )

    def __call__(
        self, argv: tuple[str, ...] | list[str], *, timeout: float | None = None
    ) -> readiness.CommandResult:
        joined = " ".join(str(part) for part in argv)
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        for contains, result in self._rules:
            if contains in joined:
                return readiness.CommandResult(
                    command,
                    result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error=result.error,
                )
        return readiness.CommandResult(command, None, error="not_found")

    def joined_calls(self) -> str:
        return "\n".join(" ".join(call) for call in self.calls)


@pytest.fixture
def probe_state(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    commands = FakeCommands()
    state = SimpleNamespace(
        commands=commands,
        files=set(),
        texts={},
        resolved={},
        chrome=(None, None),
        writable=(False, "not_writable"),
        sha=None,
    )
    monkeypatch.setattr(readiness, "_run_command", commands)
    monkeypatch.setattr(readiness, "_is_file", lambda path: str(path) in state.files)
    monkeypatch.setattr(readiness, "_read_text", lambda path: state.texts.get(str(path)))
    monkeypatch.setattr(readiness, "_which", lambda name, env=None: state.resolved.get(name))
    monkeypatch.setattr(readiness, "_find_chrome", lambda cache: state.chrome)
    monkeypatch.setattr(readiness, "_probe_writable", lambda target: state.writable)
    monkeypatch.setattr(readiness, "_sha256_file", lambda path: state.sha)
    return state


def _path_env() -> dict[str, str]:
    base = {
        "HOME": "/home/tester",
        "ACP2_TOOLS": str(readiness.DEFAULT_TOOLS_ROOT),
        "LSF_ENVDIR": str(readiness.OPENLAVA_ENVDIR),
        "CONFSEARCH_ORCA_PATH": str(readiness.ORCA_PATH),
        "CONFSEARCH_XTB_PATH": str(readiness.XTB_PATH),
        "CONFSEARCH_CREST_PATH": str(readiness.CREST_PATH),
        "PATH": "/usr/bin:/bin",
    }
    entries = [
        readiness._node22_bin(base),
        readiness.XTB_PATH.parent,
        readiness.CREST_PATH.parent,
        readiness.OPENLAVA_ROOT / "bin",
        Path("/usr/bin"),
        Path("/bin"),
    ]
    base["PATH"] = ":".join(str(entry) for entry in entries)
    return base


def _configure_happy(state: SimpleNamespace, *, default_node: str = "v22.19.0") -> dict[str, str]:
    env = _path_env()
    node22 = readiness._node22_bin(env)
    dsh_root = readiness._dsh_root(env)
    state.resolved.update({"python3.11": "/usr/bin/python3.11", "node": str(node22 / "node")})
    state.files.update(
        {
            str(readiness.ORCA_PATH),
            str(readiness.XTB_PATH),
            str(readiness.CREST_PATH),
            str(node22 / "node"),
            str(node22 / "corepack"),
            str(readiness._pnpm_bin(env)),
            str(readiness.OPENLAVA_ROOT / "bin" / "bsub"),
            str(readiness.OPENLAVA_ROOT / "bin" / "bjobs"),
            str(readiness.OPENLAVA_ROOT / "bin" / "bhist"),
            str(readiness.OPENLAVA_ENVDIR / "lsf.conf"),
            str(dsh_root / "apps" / "cli" / "lib" / "bin.js"),
            str(dsh_root / "apps" / "cli" / "package.json"),
            str(dsh_root / "packages" / "mcp" / "mcp-client" / "package.json"),
            str(dsh_root / "pnpm-lock.yaml"),
        }
    )
    state.texts[str(dsh_root / "apps" / "cli" / "package.json")] = json.dumps(
        {"name": "@deepseek-ai/dsh", "version": "0.2.1-rc.2", "bin": {"dsh": "lib/bin.js"}}
    )
    state.texts[str(dsh_root / "packages" / "mcp" / "mcp-client" / "package.json")] = json.dumps(
        {
            "name": "@deepseek-ai/dsh-mcp-client",
            "version": "0.2.1-rc.2",
            "dependencies": {"@modelcontextprotocol/client": "2.2.0"},
        }
    )
    state.chrome = (
        Path("/cache/ms-playwright/chromium-1248/chrome-linux64/chrome"),
        Path("/cache/ms-playwright/chromium_headless_shell-1248/chrome-linux64/headless_shell"),
    )
    state.writable = (True, "probe_ok:/ACPServer")
    state.sha = "a" * 64
    state.commands.add("python3.11 --version", "Python 3.11.13")
    state.commands.add("python3 --version", "Python 3.9.25")
    state.commands.add("import acp, cccp", "0.3.0 0.3.0")
    state.commands.add("orca --version", "40.3")
    state.commands.add("xtb --version", " * xtb version 6.7.1 (edcfbbe) normal termination")
    state.commands.add("crest --version", " Version 3.0.2, Sun Aug 25 20:02:36 UTC 2024")
    state.commands.add("node-v22.19.0-linux-x64/bin/node --version", default_node)
    state.commands.add("node --version", default_node)
    state.commands.add("corepack pnpm --version", "11.28.5")
    state.commands.add("/pnpm --version", "11.28.5")
    return env


def _items(document: dict) -> dict[str, dict]:
    return {item["id"]: item for item in document["items"]}


def test_secret_values_are_redacted_and_never_serialised(probe_state: SimpleNamespace) -> None:
    env = _configure_happy(probe_state)
    env["DEEPSEEK_API_KEY"] = "sk-TOPSECRET1234567890"
    probe_state.commands.add(
        "xtb --version",
        "unexpected banner password=hunter2 sk-LIVE-ABCDEF123456 token=after",
    )

    document = readiness.collect_readiness(env=env)
    payload = json.dumps(document)

    assert "TOPSECRET" not in payload
    assert "hunter2" not in payload
    assert "sk-LIVE" not in payload
    assert readiness.REDACTED in payload
    assert document["secret_free"] is True
    assert all(item["secret_free"] is True for item in document["items"])
    credential = _items(document)["model_gateway_credential"]
    assert credential["result"] == readiness.STATUS_NOT_VERIFIED
    assert credential["secret_reference"] == "env:DEEPSEEK_API_KEY"
    assert "DEEPSEEK_API_KEY" in payload  # a reference, never the value


def test_redact_text_covers_common_credential_shapes() -> None:
    text = "api_key=abc123 password: hunter2 Authorization: Bearer abc.def.ghi sk-ABCDEFGH12345678"
    redacted = readiness._redact_text(text)
    for leaked in ("abc123", "hunter2", "abc.def.ghi", "sk-ABCDEFGH"):
        assert leaked not in redacted
    assert redacted.count(readiness.REDACTED) >= 4


def test_missing_prerequisites_are_not_promoted_to_pass(probe_state: SimpleNamespace) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/home/tester"}

    document = readiness.collect_readiness(env=env)
    items = _items(document)
    results = {key: item["result"] for key, item in items.items()}

    assert results["python311"] == readiness.STATUS_FAIL
    assert results["acp_package"] == readiness.STATUS_NOT_VERIFIED
    assert items["acp_package"]["evidence_kind"] == readiness.EVIDENCE_BLOCKED
    assert results["xtb"] == readiness.STATUS_FAIL
    assert results["lsf_envdir"] == readiness.STATUS_FAIL
    assert results["lsf_command_reachable"] == readiness.STATUS_NOT_VERIFIED
    assert results["lsf_smoke_job_1767"] == readiness.STATUS_NOT_VERIFIED
    assert results["run_root_writable"] == readiness.STATUS_FAIL
    assert results["clean_machine_access"] == readiness.STATUS_PASS
    assert items["clean_machine_access"]["evidence_kind"] == readiness.EVIDENCE_RECORDED
    assert results["model_gateway_credential"] == readiness.STATUS_NOT_VERIFIED
    assert results["independent_reviewer"] == readiness.STATUS_NOT_VERIFIED
    assert results["model_call_smoke"] == readiness.STATUS_PASS
    assert items["model_call_smoke"]["evidence_kind"] == readiness.EVIDENCE_RECORDED
    assert set(results.values()) <= set(readiness.STATUSES)
    assert "bsub" not in probe_state.commands.joined_calls()
    assert "ssh" not in probe_state.commands.joined_calls()
    assert document["summary"]["gate_ready"] is False
    assert all(item["evidence_digest"].startswith("sha256:") for item in document["items"])
    assert document["mode"]["lsf_jobs_submitted"] == 0
    assert document["mode"]["model_calls"] == 0
    assert document["mode"]["qc_jobs_submitted"] == 0


def test_cluster_probe_is_opt_in_and_read_only(probe_state: SimpleNamespace) -> None:
    env = _configure_happy(probe_state)
    probe_state.commands.add("bsub -V", "openlava project 2.0, May 31 2026")
    probe_state.commands.add(
        "bjobs -a",
        "JOBID USER STAT QUEUE FROM_HOST EXEC_HOST JOB_NAME SUBMIT_TIME\n"
        "1767 xiening DONE normal workstation workstation sleep 5 Oct 26 21:57",
    )

    default_document = readiness.collect_readiness(env=env)
    default_items = _items(default_document)
    assert default_items["lsf_command_reachable"]["result"] == readiness.STATUS_NOT_VERIFIED
    assert default_items["lsf_smoke_job_1767"]["result"] == readiness.STATUS_NOT_VERIFIED
    assert "bsub" not in probe_state.commands.joined_calls()

    probed = readiness.collect_readiness(env=env, probe_cluster=True)
    probed_items = _items(probed)
    assert probed_items["lsf_command_reachable"]["result"] == readiness.STATUS_PASS
    assert probed_items["lsf_smoke_job_1767"]["result"] == readiness.STATUS_PASS
    assert "DONE" in probed_items["lsf_smoke_job_1767"]["evidence"]
    command = probed_items["lsf_command_reachable"]["command"]
    assert command[:2] == ["env", f"LSF_ENVDIR={readiness.OPENLAVA_ENVDIR}"]
    assert probed["mode"]["lsf_jobs_submitted"] == 0


def test_lsf_smoke_job_reports_non_done_state_as_fail(probe_state: SimpleNamespace) -> None:
    env = _configure_happy(probe_state)
    probe_state.commands.add("bsub -V", "openlava project 2.0")
    probe_state.commands.add(
        "bjobs -a",
        "JOBID USER STAT QUEUE FROM_HOST EXEC_HOST JOB_NAME SUBMIT_TIME\n"
        "1767 xiening RUN normal workstation workstation sleep 5 Oct 26 21:57",
    )

    document = readiness.collect_readiness(env=env, probe_cluster=True)
    item = _items(document)["lsf_smoke_job_1767"]
    assert item["result"] == readiness.STATUS_FAIL
    assert "RUN" in item["evidence"]


def test_clean_machine_probe_requires_env_and_explicit_opt_in(
    probe_state: SimpleNamespace,
) -> None:
    env = _configure_happy(probe_state)
    env["DEEPSEEK_API_KEY"] = "sk-CLEANHOSTSECRET123456"

    without_host = readiness.collect_readiness(env=env)
    recorded = _items(without_host)["clean_machine_access"]
    assert recorded["result"] == readiness.STATUS_PASS
    assert recorded["evidence_kind"] == readiness.EVIDENCE_RECORDED
    assert recorded["command"] is None
    assert "ssh" not in probe_state.commands.joined_calls()

    env["ACP2_CLEAN_HOST"] = "clean-arm64"
    no_probe = readiness.collect_readiness(env=env, probe_remote=False)
    assert _items(no_probe)["clean_machine_access"]["result"] == readiness.STATUS_NOT_VERIFIED
    assert "ssh" not in probe_state.commands.joined_calls()

    probe_state.commands.add("ssh", "Linux aarch64")
    probed = readiness.collect_readiness(env=env, probe_remote=True)
    item = _items(probed)["clean_machine_access"]
    assert item["result"] == readiness.STATUS_PASS
    assert "aarch64" in item["evidence"]
    command = item["command"]
    assert command[0] == "ssh"
    assert "BatchMode=yes" in command
    assert "UserKnownHostsFile=/dev/null" in command
    assert "clean-arm64" in command
    assert "CLEANHOSTSECRET" not in json.dumps(probed)
    assert probed["secret_free"] is True


def test_clean_machine_failed_probe_is_not_pass(probe_state: SimpleNamespace) -> None:
    env = _configure_happy(probe_state)
    env["ACP2_CLEAN_HOST"] = "clean-arm64"
    probe_state.commands.add("ssh", rc=255, stderr="Permission denied")

    document = readiness.collect_readiness(env=env, probe_remote=True)
    item = _items(document)["clean_machine_access"]
    assert item["result"] == readiness.STATUS_FAIL
    assert "Permission denied" not in item["evidence"]


def test_writable_probe_does_not_create_the_target(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "run_root"

    ok, evidence = readiness._probe_writable(target)

    assert ok is True
    assert evidence.startswith("probe_ok:")
    assert target.exists() is False


def test_configured_environment_rows_pass_when_all_exports_are_present(
    probe_state: SimpleNamespace,
) -> None:
    env = _configure_happy(probe_state)

    document = readiness.collect_readiness(env=env)
    items = _items(document)

    assert items["profile_exports"]["result"] == readiness.STATUS_PASS
    assert items["default_path_node"]["result"] == readiness.STATUS_PASS
    assert items["python311"]["result"] == readiness.STATUS_PASS
    assert items["acp_package"]["result"] == readiness.STATUS_PASS
    assert items["xtb"]["result"] == readiness.STATUS_PASS
    assert items["crest"]["result"] == readiness.STATUS_PASS
    assert items["orca"]["result"] == readiness.STATUS_PASS
    assert items["node22"]["result"] == readiness.STATUS_PASS
    assert items["pnpm"]["result"] == readiness.STATUS_PASS
    assert items["corepack_pnpm"]["result"] == readiness.STATUS_PASS
    assert items["chromium_cache"]["result"] == readiness.STATUS_PASS
    assert items["openlava_install"]["result"] == readiness.STATUS_PASS
    assert items["lsf_envdir"]["result"] == readiness.STATUS_PASS
    assert items["dsh_cli"]["result"] == readiness.STATUS_PASS
    assert items["dsh_client_matrix"]["result"] == readiness.STATUS_PASS
    assert items["run_root_writable"]["result"] == readiness.STATUS_PASS
    assert items["artifact_store_writable"]["result"] == readiness.STATUS_PASS
    assert document["summary"]["fail"] == 0
    assert document["summary"]["gate_ready"] is False
    assert document["summary"]["not_verified"] >= 3


def test_exit_codes_follow_the_tri_state_summary() -> None:
    assert readiness._exit_code({"summary": {"fail": 0, "not_verified": 0}}) == 0
    assert readiness._exit_code({"summary": {"fail": 1, "not_verified": 0}}) == 1
    assert readiness._exit_code({"summary": {"fail": 0, "not_verified": 2}}) == 2
