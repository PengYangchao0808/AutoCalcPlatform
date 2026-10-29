"""Lab-tier init/doctor bridge (B09-5 / plan todo 52).

The pure four-state capability detector lives in
:mod:`acp_services.runtime.lab_capabilities` (stdlib + sibling Services only).
This additive acp-side module is the **only** place that knows the wizard's
:class:`~acp.init_wizard.plan.InitPlan` shape and turns it into a
:class:`~acp_services.runtime.lab_capabilities.LabEnvironment` plus a rendered
Lab doctor report.  It never provisions anything and never executes an external
action — it classifies the reuse of the institution's existing infrastructure.

Plan mapping (all additive, absent keys default to "not provided"):

* ``resources.external.database`` / ``.https`` / ``.storage`` / ``.backup`` — a
  ``source:ref`` reference (``{"ref": "env:..."}`` or a bare string) naming the
  institution's existing resource; never a raw value.
* ``resources.cluster.type`` — ``lsf``/``openlava`` → LSF execution;
  ``slurm``/``podman`` recorded; otherwise local.
* ``params.lab`` — optional Lab settings: ``quota_bytes`` / ``quota_source`` /
  ``quota_requested`` / ``https_requested`` / ``identity_map`` /
  ``lsf_accounts`` / ``isolation_mechanisms`` / ``backup_ref`` /
  ``slurm_requested``.
* ``secrets`` — secret *references*; their resolvability feeds the ``keys`` row.

This module imports no scheduler/remote stack, so ``import acp.init_wizard``
stays paramiko-free (see ``tests/test_acp_init_cli.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp.init_wizard.plan import InitPlan
from acp_services.contracts.access import DeploymentTier, ExecutionMode
from acp_services.runtime.isolation import LsfExecutionAccount, OsIdentity
from acp_services.runtime.lab_capabilities import (
    LabCapabilityReport,
    LabEnvironment,
    LabProbes,
    build_lab_capability_report,
)
from acp_services.runtime.lab_capabilities import (
    render_lab_capability_report as _render_report,
)
from acp_services.workspace.boundary import TierCapability

__all__ = [
    "collect_lab_capability_report",
    "lab_environment_from_plan",
    "render_lab_capability_report",
]

_LSF_CLUSTER_TYPES: frozenset[str] = frozenset({"lsf", "openlava"})


def _external(resources: Mapping[str, Any]) -> Mapping[str, Any]:
    external = resources.get("external")
    return external if isinstance(external, Mapping) else {}


def _lab_params(plan: InitPlan) -> Mapping[str, Any]:
    lab = plan.params.get("lab")
    return lab if isinstance(lab, Mapping) else {}


def _ref(value: object) -> str | None:
    """Extract a ``source:ref`` reference from an external resource entry."""
    if isinstance(value, Mapping):
        ref = value.get("ref")
        if isinstance(ref, str) and ref.strip():
            return ref.strip()
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _execution_mode(resources: Mapping[str, Any]) -> str:
    cluster = resources.get("cluster")
    if not isinstance(cluster, Mapping):
        return ExecutionMode.LOCAL
    cluster_type = str(cluster.get("type") or "local").strip().lower()
    if cluster_type in _LSF_CLUSTER_TYPES:
        return ExecutionMode.LSF
    if cluster_type == "slurm":
        return ExecutionMode.SLURM
    if cluster_type == "podman":
        return ExecutionMode.PODMAN
    return ExecutionMode.LOCAL


def _identity_map(lab: Mapping[str, Any]) -> dict[str, OsIdentity]:
    raw = lab.get("identity_map")
    if not isinstance(raw, Mapping):
        return {}
    identities: dict[str, OsIdentity] = {}
    for principal, entry in raw.items():
        if not isinstance(entry, Mapping):
            continue
        identities[str(principal)] = OsIdentity(
            username=str(entry.get("username", principal)),
            uid=int(entry["uid"]),
            gid=int(entry.get("gid", entry["uid"])),
        )
    return identities


def _lsf_accounts(lab: Mapping[str, Any]) -> dict[str, LsfExecutionAccount]:
    raw = lab.get("lsf_accounts")
    if not isinstance(raw, Mapping):
        return {}
    accounts: dict[str, LsfExecutionAccount] = {}
    for principal, entry in raw.items():
        if not isinstance(entry, Mapping):
            continue
        accounts[str(principal)] = LsfExecutionAccount(
            principal_id=str(principal),
            account=str(entry.get("account", principal)),
            queue=str(entry.get("queue", "normal")),
            resource_policy=str(entry.get("resource_policy", "default")),
        )
    return accounts


def _isolation_mechanisms(lab: Mapping[str, Any]) -> tuple[str, ...]:
    raw = lab.get("isolation_mechanisms")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


def _storage_quota(lab: Mapping[str, Any], storage_ref: str | None) -> tuple[int | None, str]:
    raw = lab.get("quota_bytes")
    if raw is not None:
        try:
            return int(raw), str(lab.get("quota_source", ""))
        except (TypeError, ValueError):
            return None, ""
    return None, ""


def _capabilities(plan: InitPlan) -> tuple[str, ...]:
    selected = set(plan.components)
    available: list[str] = []
    if "authentication" in selected:
        available.append(TierCapability.AUTHENTICATION)
    if "isolation" in selected:
        available.append(TierCapability.ISOLATION)
    return tuple(available)


def lab_environment_from_plan(plan: InitPlan) -> LabEnvironment:
    """Build the observed :class:`LabEnvironment` from an init plan."""
    resources = plan.resources if isinstance(plan.resources, Mapping) else {}
    external = _external(resources)
    lab = _lab_params(plan)

    database_ref = _ref(external.get("database"))
    https_cert_ref = _ref(external.get("https"))
    storage_ref = _ref(external.get("storage"))
    backup_ref = str(lab.get("backup_ref") or "").strip() or _ref(external.get("backup"))
    quota_bytes, quota_source = _storage_quota(lab, storage_ref)

    secret_refs = tuple((secret.name, f"{secret.source}:{secret.ref}") for secret in plan.secrets)

    https_requested = bool(lab.get("https_requested", https_cert_ref is not None))
    quota_requested = bool(lab.get("quota_requested", quota_bytes is not None))

    return LabEnvironment(
        execution_mode=_execution_mode(resources),
        database_ref=database_ref,
        https_cert_ref=https_cert_ref,
        https_requested=https_requested,
        storage_quota_bytes=quota_bytes,
        storage_quota_source=quota_source,
        quota_requested=quota_requested,
        identity_map=_identity_map(lab),
        lsf_accounts=_lsf_accounts(lab),
        secret_refs=secret_refs,
        backup_ref=backup_ref,
        isolation_mechanisms=_isolation_mechanisms(lab),
    )


def collect_lab_capability_report(
    plan: InitPlan,
    *,
    probes: LabProbes | None = None,
) -> LabCapabilityReport:
    """Collect the four-state Lab doctor report for *plan*."""
    env = lab_environment_from_plan(plan)
    slurm_requested = bool(_lab_params(plan).get("slurm_requested", False))
    return build_lab_capability_report(
        env,
        tier=plan.tier or DeploymentTier.LAB,
        capabilities=_capabilities(plan),
        probes=probes,
        slurm_requested=slurm_requested,
    )


def render_lab_capability_report(report: LabCapabilityReport) -> str:
    """Render the Lab report (PASS / FAIL / NOT_VERIFIED / UNAVAILABLE split)."""
    return _render_report(report)
