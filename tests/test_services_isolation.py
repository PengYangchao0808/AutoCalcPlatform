"""B09-3 (todo 50) — execution isolation adapters + capability matrix (MU-06).

Exercises ``acp_services.runtime.isolation``:

1. **Pseudo-isolation is never isolation.** Same-UID per-directory separation,
   ``OMP_NUM_THREADS``-only and a login page resolve to ``PSEUDO`` and can never
   satisfy a tier requirement.
2. **Refuse, never downgrade.** A tier whose required identity / resource /
   minimal-mount capability is absent is refused with a typed reason; the
   decision reports the *requested* tier and grants no substitute tier.
3. **Local resource limits bind the envelope process group** (cgroup v2, one
   cgroup per ``submission_id``), and the cgroup capability is measured where
   possible else reported NOT_VERIFIED/UNAVAILABLE with its method.
4. **Cross-user read/write negative.** A same-UID directory layout never
   certifies isolation; distinct UIDs still require a restrictive mode.
5. **LSF stop scope is limited to the job's own identity.** ``bkill`` / ``bstop``
   / ``bresume`` may address only this job's numeric id + own account; the live
   monitor's commands satisfy that predicate.
6. **The capability matrix file matches the code** and the module imports no
   ``acp``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from acp_services.contracts.envelope import ExecutionEnvelope, ResourceBudget, StorageBinding
from acp_services.runtime import isolation as iso

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ISOLATION_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "isolation.py"
_MATRIX_PATH = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "isolation_matrix.md"

M = iso.IsolationMechanism
REAL = {
    M.OS_IDENTITY,
    M.SANDBOX,
    M.CGROUP_V2,
    M.LSF_IDENTITY,
    M.MINIMAL_MOUNTS,
}
HOSTED_FULL = {
    M.OS_IDENTITY,
    M.CGROUP_V2,
    M.MINIMAL_MOUNTS,
}


def _envelope(submission_id: str = "sub_deadbeef") -> ExecutionEnvelope:
    return ExecutionEnvelope(
        envelope_version=1,
        job_id="job-1",
        attempt=1,
        submission_id=submission_id,
        release_id="release-1",
        owner="alice",
        fencing_token="fence-1",
        plan_digest="plan-digest",
        plan_identity="plan-id",
        resource_budget=ResourceBudget(nproc=4, mem_mb=2048),
    )


def _local_config(**overrides) -> iso.IsolationConfig:
    base: dict = {"execution_mode": "local", "mechanisms": ()}
    base.update(overrides)
    return iso.IsolationConfig(**base)


# --------------------------------------------------------------------------- #
# 1. Pseudo-isolation is never isolation
# --------------------------------------------------------------------------- #


def test_pseudo_mechanisms_never_raise_level() -> None:
    pseudo = {M.SAME_UID_DIRECTORY, M.OMP_NUM_THREADS, M.LOGIN_PAGE}
    assert iso.level_for_mechanisms(pseudo) == iso.IsolationLevel.PSEUDO
    assert iso.is_isolated_level(iso.IsolationLevel.PSEUDO) is False
    assert iso.is_pseudo_only(pseudo) is True

    # A real mechanism mixed in is what raises the level; pseudo adds nothing.
    with_real = pseudo | {M.CGROUP_V2}
    assert iso.level_for_mechanisms(with_real) == iso.IsolationLevel.RESOURCE_LIMITED
    assert iso.is_isolated_level(iso.IsolationLevel.RESOURCE_LIMITED) is False


def test_same_uid_directory_only_config_fails_isolation_check() -> None:
    adapter = iso.LocalIsolationAdapter(
        _local_config(mechanisms=(M.SAME_UID_DIRECTORY, M.OMP_NUM_THREADS, M.LOGIN_PAGE))
    )
    assert adapter.level() == iso.IsolationLevel.PSEUDO
    with pytest.raises(iso.IsolationUnavailableError):
        adapter.bind(
            _envelope(),
            principal_id="alice",
            workdir="/srv/acp/alice",
            code_release="/opt/acp/releases/r1",
            require_isolation=True,
        )


# --------------------------------------------------------------------------- #
# 2. Gate refuses; never downgrades
# --------------------------------------------------------------------------- #


def test_gate_refuses_lab_and_hosted_with_pseudo_only() -> None:
    gate = iso.IsolationGate()
    for tier in ("lab", "hosted"):
        decision = gate.evaluate(tier, {M.SAME_UID_DIRECTORY})
        assert decision.enabled is False
        assert decision.reason_code == iso.IsolationReason.PSEUDO_ISOLATION_REJECTED


def test_gate_refuses_hosted_without_identity_even_with_cgroup() -> None:
    gate = iso.IsolationGate()
    decision = gate.evaluate("hosted", {M.CGROUP_V2, M.MINIMAL_MOUNTS})
    assert decision.enabled is False
    assert decision.reason_code == iso.IsolationReason.IDENTITY_ISOLATION_REQUIRED
    assert set(decision.missing) == {M.OS_IDENTITY, M.LSF_IDENTITY}


def test_gate_refuses_hosted_without_resource_limit() -> None:
    gate = iso.IsolationGate()
    decision = gate.evaluate("hosted", {M.OS_IDENTITY, M.MINIMAL_MOUNTS})
    assert decision.enabled is False
    assert decision.reason_code == iso.IsolationReason.RESOURCE_LIMIT_REQUIRED


def test_gate_enables_tiers_when_requirements_satisfied() -> None:
    gate = iso.IsolationGate()
    local = gate.evaluate("local-single", set())
    assert local.enabled is True
    assert local.reason_code == iso.IsolationReason.NO_ISOLATION_REQUIRED

    lab = gate.evaluate("lab", {M.OS_IDENTITY})
    assert lab.enabled is True

    hosted = gate.evaluate("hosted", HOSTED_FULL)
    assert hosted.enabled is True
    assert hosted.level == iso.IsolationLevel.IDENTITY_ISOLATED


def test_gate_unknown_tier_refused() -> None:
    decision = iso.IsolationGate().evaluate("bogus-tier", HOSTED_FULL)
    assert decision.enabled is False
    assert decision.reason_code == iso.IsolationReason.UNKNOWN_TIER


def test_no_silent_downgrade_decision_has_no_substitute_tier() -> None:
    decision = iso.IsolationGate().evaluate("hosted", {M.CGROUP_V2})
    assert decision.tier == "hosted"
    payload = decision.to_dict()
    assert payload["tier"] == "hosted"
    for forbidden in ("granted_tier", "effective_tier", "fallback_tier", "downgraded_to"):
        assert forbidden not in payload
        assert not hasattr(decision, forbidden)


def test_assert_enablement_raises_with_decision() -> None:
    gate = iso.IsolationGate()
    with pytest.raises(iso.IsolationRefusedError) as excinfo:
        gate.assert_enablement("hosted", {M.CGROUP_V2})
    assert excinfo.value.decision.reason_code == iso.IsolationReason.IDENTITY_ISOLATION_REQUIRED
    assert gate.assert_enablement("lab", {M.LSF_IDENTITY}).enabled is True


# --------------------------------------------------------------------------- #
# 3. Local: identity / cgroup binding
# --------------------------------------------------------------------------- #


def test_local_identity_measured_when_mapped() -> None:
    identity = iso.OsIdentity(username="acp_alice", uid=6101, gid=6101)
    adapter = iso.LocalIsolationAdapter(
        _local_config(
            mechanisms=(M.OS_IDENTITY, M.MINIMAL_MOUNTS),
            identity_map={"alice": identity},
        )
    )
    caps = {cap.mechanism: cap for cap in adapter.capabilities()}
    assert caps[M.OS_IDENTITY].status == iso.CapabilityStatus.MEASURED
    assert M.OS_IDENTITY in adapter.verified_mechanisms()
    assert adapter.level() == iso.IsolationLevel.IDENTITY_ISOLATED

    binding = adapter.bind(
        _envelope(),
        principal_id="alice",
        workdir="/srv/acp/alice",
        code_release="/opt/acp/releases/r1",
        require_isolation=True,
    )
    assert binding.uid == 6101 and binding.gid == 6101
    assert binding.process_group.envelope_key == "sub_deadbeef"


def test_local_identity_refused_without_mapping() -> None:
    adapter = iso.LocalIsolationAdapter(_local_config(mechanisms=(M.OS_IDENTITY, M.MINIMAL_MOUNTS)))
    caps = {cap.mechanism: cap for cap in adapter.capabilities()}
    assert caps[M.OS_IDENTITY].status == iso.CapabilityStatus.NOT_VERIFIED
    assert M.OS_IDENTITY not in adapter.verified_mechanisms()
    with pytest.raises(iso.IdentityNotMappedError):
        adapter.resolve_identity("alice")


def test_local_cgroup_plan_binds_envelope_process_group() -> None:
    adapter = iso.LocalIsolationAdapter(_local_config(mechanisms=(M.CGROUP_V2,)))
    envelope = _envelope(submission_id="sub_cafef00d")
    process_group = iso.EnvelopeProcessGroup(
        envelope_key=envelope.submission_id, job_id="job-1", attempt=1, pgid=4242
    )
    plan = adapter.plan_cgroup_v2(envelope, process_group)
    assert plan.envelope_key == "sub_cafef00d"
    assert plan.cgroup_path.endswith("/sub_cafef00d")
    assert plan.procs_file.endswith("/cgroup.procs")

    writes = dict(plan.placement_writes(4242))
    assert writes[plan.procs_file] == "4242"
    assert plan.cpu_max == "400000 100000"
    assert plan.memory_max_bytes == 2048 * 1024 * 1024
    assert plan.pids_max is not None and plan.pids_max >= 16


def test_local_cgroup_measured_or_not_verified_with_method(tmp_path: Path) -> None:
    fake_root = tmp_path / "cg"
    fake_root.mkdir()
    (fake_root / "cgroup.controllers").write_text("cpu memory pids\n", encoding="utf-8")
    measured = iso.LocalIsolationAdapter(
        _local_config(mechanisms=(M.CGROUP_V2,), cgroup_root=str(fake_root))
    )
    cap = {c.mechanism: c for c in measured.capabilities()}[M.CGROUP_V2]
    assert cap.status == iso.CapabilityStatus.MEASURED
    assert cap.method and "writable" in cap.method

    absent = iso.LocalIsolationAdapter(
        _local_config(mechanisms=(M.CGROUP_V2,), cgroup_root=str(tmp_path / "missing"))
    )
    cap_absent = {c.mechanism: c for c in absent.capabilities()}[M.CGROUP_V2]
    assert cap_absent.status in {
        iso.CapabilityStatus.NOT_VERIFIED,
        iso.CapabilityStatus.UNAVAILABLE,
    }
    assert cap_absent.method


def test_local_bind_refuses_non_isolation_binding() -> None:
    adapter = iso.LocalIsolationAdapter(
        _local_config(mechanisms=(M.SAME_UID_DIRECTORY, M.OMP_NUM_THREADS))
    )
    with pytest.raises(iso.IsolationUnavailableError):
        adapter.bind(
            _envelope(),
            principal_id="alice",
            workdir="/w",
            code_release="/c",
            require_isolation=True,
        )


# --------------------------------------------------------------------------- #
# 4. Cross-user read/write negative
# --------------------------------------------------------------------------- #


def test_cross_user_same_uid_directory_never_isolated(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    audit = iso.audit_cross_user_access(shared, owner_uid=1000, other_uid=1000)
    assert audit.same_uid is True
    assert audit.isolated is False
    assert "not isolation" in audit.detail


def test_cross_user_distinct_uid_requires_restrictive_mode(tmp_path: Path) -> None:
    open_dir = tmp_path / "open"
    open_dir.mkdir(mode=0o777)
    open_dir.chmod(0o777)
    real_uid = open_dir.stat().st_uid
    assert (
        iso.audit_cross_user_access(
            open_dir, owner_uid=real_uid, other_uid=real_uid + 1
        ).isolated
        is False
    )

    closed_dir = tmp_path / "closed"
    closed_dir.mkdir(mode=0o700)
    closed_dir.chmod(0o700)
    assert (
        iso.audit_cross_user_access(
            closed_dir, owner_uid=real_uid, other_uid=real_uid + 1
        ).isolated
        is True
    )


def test_minimal_mounts_reject_broad_and_secret_sources() -> None:
    for bad in ("/", "/home", "/root", "~", "/srv/acp/acp_jobs.db", "/root/.ssh"):
        with pytest.raises(iso.IsolationError):
            iso.assert_minimal_mounts([iso.MountSpec(source=bad, target="/x", mode="ro")])

    with pytest.raises(iso.IsolationError):
        iso.assert_minimal_mounts(
            [iso.MountSpec(source="/opt/acp/code", target="/x", mode="rw", role="code_release")]
        )

    good = iso.minimal_mount_policy(
        workdir="/srv/acp/alice/jobs/j1",
        code_release="/opt/acp/releases/r1",
        data_bindings=("/srv/acp/shared/input.xyz",),
    )
    iso.assert_minimal_mounts(good)
    assert {mount.role for mount in good} == {"workdir", "code_release", "tmp", "data_binding"}


# --------------------------------------------------------------------------- #
# 5. LSF identity mapping + stop scope
# --------------------------------------------------------------------------- #


def _lsf_account(principal: str, account: str) -> iso.LsfExecutionAccount:
    return iso.LsfExecutionAccount(
        principal_id=principal, account=account, queue="normal", resource_policy="rp-1"
    )


def test_lsf_stop_scope_limited_to_own_job() -> None:
    scope = iso.StopScope(lsf_job_id="12345", execution_account="acp_alice", submission_id="sub_x")
    assert scope.permits_job("12345", "acp_alice") is True
    assert scope.permits_job("12345", "acp_bob") is False
    assert scope.permits_job("123456", "acp_alice") is False

    assert scope.permits_command(scope.kill_command()) is True
    assert scope.permits_command(scope.suspend_command()) is True
    assert scope.permits_command(scope.resume_command()) is True
    # superstring id and foreign id/account must be rejected
    assert scope.permits_command(("bkill", "123456")) is False
    assert scope.permits_command(("bstop", "999")) is False
    assert scope.permits_command(("bkill", "-u", "acp_bob", "12345")) is False
    assert scope.permits_command(("bkill", "-u", "acp_alice", "12345")) is True
    # a path-based id is never acceptable
    assert scope.permits_command(("bkill", "/scratch/alice/jobs/12345")) is False

    pattern = scope.pkill_fallback_pattern()
    assert "12345" in pattern and "(?<![0-9])" in pattern
    # the fallback pattern is anchored, so a superstring id cannot match
    assert re_search_span(pattern, "acp.cli run --x 123456") is None
    assert re_search_span(pattern, "acp.cli run --x 12345") is not None


def re_search_span(pattern: str, text: str):
    import re

    return re.search(pattern, text)


def test_non_numeric_stop_scope_rejected() -> None:
    with pytest.raises(iso.IsolationError):
        iso.StopScope(lsf_job_id="j-1", execution_account="acp_alice")


def test_lsf_binding_uses_persistent_mapping_not_client_choice() -> None:
    adapter = iso.LsfIsolationAdapter(accounts={"alice": _lsf_account("alice", "acp_alice")})
    assert adapter.resolve_account("alice").account == "acp_alice"
    with pytest.raises(iso.IdentityNotMappedError):
        adapter.resolve_account("bob")

    binding = adapter.bind(
        _envelope(),
        principal_id="alice",
        lsf_job_id="777",
        workdir="/srv/acp/alice/jobs/j1",
        code_release="/opt/acp/releases/r1",
    )
    assert binding.execution_account == "acp_alice"
    assert binding.stop_scope.permits_command(("bkill", "777")) is True
    assert binding.stop_scope.permits_command(("bkill", "778")) is False
    iso.assert_minimal_mounts(binding.minimal_mounts)

    caps = {cap.mechanism: cap for cap in adapter.capabilities()}
    assert caps[M.LSF_IDENTITY].status == iso.CapabilityStatus.CONFIGURED
    # No live cluster here: cluster enforcement is not verified.
    assert adapter.verified_mechanisms() == frozenset()


# --------------------------------------------------------------------------- #
# 6. Live monitor stop commands satisfy the stop scope
# --------------------------------------------------------------------------- #


def test_monitor_bkill_and_bstop_target_only_own_job() -> None:
    pytest.importorskip("paramiko")
    from acp.scheduler.remote.config import RemoteNode
    from acp.scheduler.remote.monitor import RemoteJobMonitor

    class _FakeSSH:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def execute(self, node, cmd, timeout=30):  # noqa: ANN001
            self.commands.append(cmd)
            return (0, "", "")

    fake = _FakeSSH()
    monitor = RemoteJobMonitor(fake, None)  # type: ignore[arg-type]
    node = RemoteNode(
        name="n1", host="h", username="acp_alice", remote_work_dir="/w", remote_code_dir="/c"
    )
    scope = iso.StopScope(lsf_job_id="12345", execution_account="acp_alice")

    assert monitor.cancel_job(node, "12345") is True
    assert monitor.bstop_job(node, "12345") is True

    assert scope.permits_shell_command(fake.commands[0]) is True
    assert scope.permits_shell_command(fake.commands[1]) is True
    # a foreign job id would not be permitted by the same scope
    assert scope.permits_shell_command("bkill 123456 2>&1") is False


# --------------------------------------------------------------------------- #
# 7. Matrix file + module isolation
# --------------------------------------------------------------------------- #


def _all_adapter_capabilities() -> list[iso.TierCapability]:
    local = iso.LocalIsolationAdapter(_local_config(mechanisms=(M.OS_IDENTITY, M.CGROUP_V2)))
    lsf = iso.LsfIsolationAdapter(accounts={"alice": _lsf_account("alice", "acp_alice")})
    caps: list[iso.TierCapability] = []
    for row in iso.capability_matrix((local, lsf)):
        caps.extend(row.capabilities)
    return caps


def _parse_matrix_file() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    in_table = False
    for line in _MATRIX_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if not in_table:
            if cells and cells[0].lower() == "adapter":
                in_table = True
            continue
        if all(cell and set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)  # type: ignore[arg-type]
    return rows


def test_capability_matrix_file_matches_code() -> None:
    assert _MATRIX_PATH.is_file(), f"missing tracked matrix {_MATRIX_PATH}"
    rows = _parse_matrix_file()
    assert rows, "no capability rows parsed from isolation_matrix.md"
    for cells in rows:
        assert len(cells) == 4, f"matrix row must have 4 columns: {cells}"
        status = cells[2].lower().replace("_", "-")
        assert status in iso.CapabilityStatus.ALL, f"bad status {cells[2]!r}"

    file_pairs = {(cells[0], cells[1]) for cells in rows}
    code_pairs = {(cap.adapter, cap.mechanism) for cap in _all_adapter_capabilities()}
    assert file_pairs == code_pairs, (
        f"matrix drift: file-only={file_pairs - code_pairs} code-only={code_pairs - file_pairs}"
    )

    text = _MATRIX_PATH.read_text(encoding="utf-8")
    assert "NOT_VERIFIED" in text
    assert "same-UID" in text or "same-uid" in text


def test_isolation_module_has_no_acp_import() -> None:
    tree = ast.parse(_ISOLATION_SRC.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
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
    assert not offenders, f"acp import detected: {offenders}"


def test_local_default_config_helper() -> None:
    assert iso.DEFAULT_LOCAL_CONFIG.execution_mode == "local"
    binding = iso.minimal_mount_policy(workdir="/w", code_release="/c")
    assert all(binding)


def test_storage_binding_dataclass_still_constructible() -> None:
    # guards that the module's envelope import stays usable alongside StorageBinding
    binding = StorageBinding(binding_id="b1", tenant_id="t", project_id="p")
    assert binding.to_dict()["binding_id"] == "b1"
