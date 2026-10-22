#!/usr/bin/env python3
"""ACP2 science acceptance harness (A-10a skeleton, A-10b singlepoint scenario).

The harness exposes the scenario/flag contract for the science track, a
path/budget ``--dry-run`` and the project's ``0=PASS, 1=FAIL, 2=NOT_VERIFIED,
3=usage`` exit-code contract.  ``--help`` and ``--dry-run`` never create a work
root, start a job or produce a scenario ``PASS``.

Two scenarios are implemented.  ``science:installed-singlepoint`` calls the
public ``cccp.calculation.run_singlepoint`` task core (the single execution
station) inside an independent native work root and re-parses the native output
locally.  ``science:validate-artifacts`` consumes an already-prepared,
platform-authorized optimize/frequency artifact snapshot: it confirms the
registered ``dsh-optfreq-live`` id against an explicitly supplied platform
registry record, verifies the snapshot content through
``cccp.analysis.research.artifact_manifest`` and runs the frozen
stationary-point validators.  It never launches a second QC job.

The remaining scenarios record ``NOT_VERIFIED`` for their own todo.  The
harness never starts a controller, scheduler, job or production run root, and
never opens SSH.

The single receipt authority is B01's ``acceptance-result.schema.json``
(``acp2.acceptance.v1``).  While that contract is unpublished the harness
writes a *provisional* A-internal record under ``extensions.science`` and
records ``receipt_protocol_gap = NOT_VERIFIED``.  It does not define a second
receipt schema, copy an unpublished sibling schema, or invent valid/invalid
samples.

Scenarios (canonical ``science:*`` id and short CLI alias are interchangeable):

``science:installed-singlepoint`` / ``installed-singlepoint``
    Standalone singlepoint through the public CCCP API (A-10b).
``science:validate-artifacts`` / ``validate-artifacts``
    Validate a platform-authorized optimize/frequency artifact snapshot
    (A-11b).  Requires ``--input-manifest``; a PASS also requires
    ``--platform-registry`` with the registered ``dsh-optfreq-live`` row.
``science:validate-da-ts`` / ``validate-da-ts``
    Validate Diels-Alder transition-state artifacts.
``science:validate-da-irc`` / ``validate-da-irc``
    Validate Diels-Alder bidirectional IRC artifacts.
``science:validate-da-report`` / ``validate-da-report``
    Validate the Diels-Alder evidence report and its claims.
``science:installed-examples`` / ``installed-examples``
    Run the installed-package examples.

Run ``--help`` for the required flag per scenario.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

RECORD_STATE = "provisional"
CONTRACT_VERSION = 1
RECEIPT_SCHEMA = "acceptance-result.schema.json"
RECEIPT_CONTRACT = "acp2.acceptance.v1"
RECEIPT_DELIVERY_TRIGGER = (
    "Deliver one valid and one invalid A sample immediately when B01 publishes "
    "the draft schema, before B05 or A-17."
)

SCRIPT_PATH = Path(__file__).resolve()

_DIGEST_PATTERN = r"^(?:sha256:)?[0-9a-f]{64}$"

SINGLEPOINT_ALIAS = "installed-singlepoint"
ARTIFACT_ALIAS = "validate-artifacts"
H2_HANDOFF_RELPATH = "docs/reference/next-change/acp2-contracts/science/handoff-H2.json"
PRODUCTION_RUN_ROOTS = ("/var/lib/acp/runs",)

#: The platform registry is owned by the platform and is not published to A.
#: A supplies only the lookup; it never invents the id or a registry row.
REGISTERED_OPT_FREQ_RUN_ID = "dsh-optfreq-live"
PLATFORM_REGISTRY_SCHEMA = "acp2.science.platform-registry"
CONSTRUCTED_SNAPSHOT_SCHEMA = "acp2.science.constructed-snapshot"
FIXTURE_READ_ROOT_TOKEN = "@fixture"
PLATFORM_REGISTRY_GAP = (
    "platform registry not supplied: pass --platform-registry <path> for the "
    "platform-provided registry record so the registered id "
    f"{REGISTERED_OPT_FREQ_RUN_ID!r} can be confirmed; the platform registry is "
    "not published to track A and the harness never invents the id or a row"
)
P1_TEST_CANDIDATE_GAP = (
    "no P1 science/same-task integration test candidate has been published; "
    "the P1 gate is owned by its own later todo"
)
NATIVE_ENERGY_TOLERANCE_HARTREE = 1.0e-6
NATIVE_FREQUENCY_TOLERANCE_CM1 = 1.0e-6
_PLACEHOLDER_RUN_KEYS = frozenset(
    {"", "none", "null", "tbd", "todo", "placeholder", "unapproved", "not-issued"}
)

#: Local, independent re-parsers for the native output.  Deliberately do not
#: import ``cccp.analysis.orca_parser``: the acceptance check must be a second
#: reader of the bytes, not the same parser that produced ``TaskResult``.
_NATIVE_MARKERS: dict[str, tuple[re.Pattern[str], re.Pattern[str] | None]] = {
    "orca": (
        re.compile(r"FINAL SINGLE POINT ENERGY\s+([-+]?\d+\.\d+)"),
        re.compile(r"ORCA TERMINATED NORMALLY"),
    ),
    "xtb": (
        re.compile(r"TOTAL ENERGY\s+([-+]?\d+(?:\.\d*)?(?:[EeDd][-+]?\d+)?)\s+Eh"),
        re.compile(r"normal termination|finished", re.IGNORECASE),
    ),
}


@dataclass(frozen=True)
class ScenarioSpec:
    """One acceptance scenario and its CLI requirements."""

    alias: str
    phase: str
    description: str
    requires_run_spec: bool = False
    requires_input_manifest: bool = False

    @property
    def canonical(self) -> str:
        return f"science:{self.alias}"


SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        alias="installed-singlepoint",
        phase="main",
        description="standalone singlepoint through the public CCCP API",
        requires_run_spec=True,
    ),
    ScenarioSpec(
        alias="validate-artifacts",
        phase="main",
        description="validate authorized optimize/frequency artifacts",
        requires_input_manifest=True,
    ),
    ScenarioSpec(
        alias="validate-da-ts",
        phase="da-evidence",
        description="validate Diels-Alder transition-state artifacts",
        requires_input_manifest=True,
    ),
    ScenarioSpec(
        alias="validate-da-irc",
        phase="da-evidence",
        description="validate Diels-Alder bidirectional IRC artifacts",
        requires_input_manifest=True,
    ),
    ScenarioSpec(
        alias="validate-da-report",
        phase="da-evidence",
        description="validate the Diels-Alder evidence report and claims",
        requires_input_manifest=True,
    ),
    ScenarioSpec(
        alias="installed-examples",
        phase="main",
        description="run the installed-package examples",
    ),
)
SCENARIO_BY_ALIAS = {spec.alias: spec for spec in SCENARIOS}


@dataclass(frozen=True)
class PlatformExportCheck:
    """Harness-only verdict on the platform binding/export envelope.

    The platform's Data service owns the binding, export eligibility and freeze
    decision.  This check never inspects artifact bytes or scientific values;
    it only refuses a snapshot whose platform envelope is absent or not
    exported as an eligible frozen copy.
    """

    status: str
    eligible: bool
    reasons: tuple[str, ...]
    binding: dict[str, Any] | None
    export: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "binding": self.binding,
            "export": self.export,
        }


def _export_string(
    mapping: Mapping[str, Any], key: str, where: str, reasons: list[str]
) -> str | None:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        reasons.append(f"{where}.{key} must be a non-empty string")
        return None
    return value


def check_platform_export(manifest: Any) -> PlatformExportCheck:
    """Validate the platform binding and export envelope of a snapshot.

    Importable without importing ``cccp`` so the harness stays import-light.
    A missing envelope is reported as ``MISSING``; an envelope that exists but
    is not an eligible frozen export is ``INELIGIBLE``.  Both are rejected.
    """
    if not isinstance(manifest, Mapping):
        return PlatformExportCheck(
            status="MISSING",
            eligible=False,
            reasons=("input manifest is not a JSON object",),
            binding=None,
            export=None,
        )
    platform = manifest.get("platform")
    if not isinstance(platform, Mapping):
        return PlatformExportCheck(
            status="MISSING",
            eligible=False,
            reasons=("platform binding is missing from the snapshot",),
            binding=None,
            export=None,
        )

    reasons: list[str] = []
    _export_string(platform, "owner", "platform", reasons)

    binding = platform.get("binding")
    binding_view: dict[str, Any] | None = None
    if not isinstance(binding, Mapping):
        reasons.append("platform.binding must be a JSON object")
    else:
        binding_view = dict(binding)
        _export_string(binding, "task_ref", "platform.binding", reasons)
        _export_string(binding, "job_id", "platform.binding", reasons)
        attempt = binding.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            reasons.append("platform.binding.attempt must be a positive integer")
        for optional in ("release_id", "submission_id"):
            if optional in binding:
                _export_string(binding, optional, "platform.binding", reasons)

    export = platform.get("export")
    export_view: dict[str, Any] | None = None
    if not isinstance(export, Mapping):
        reasons.append("platform.export must be a JSON object")
    else:
        export_view = dict(export)
        for flag in ("export_eligible", "authorized", "frozen"):
            if export.get(flag) is not True:
                reasons.append(f"platform.export.{flag} must be true")
        if export.get("revoked") is not False:
            reasons.append("platform.export.revoked must be false")
        if "snapshot_id" in export:
            _export_string(export, "snapshot_id", "platform.export", reasons)

    eligible = not reasons
    return PlatformExportCheck(
        status="ELIGIBLE" if eligible else "INELIGIBLE",
        eligible=eligible,
        reasons=tuple(reasons),
        binding=binding_view,
        export=export_view,
    )


def _platform_export_assertion(check: PlatformExportCheck) -> dict[str, Any]:
    return {
        "name": "platform_export_eligibility",
        "status": "PASS" if check.eligible else "FAIL",
        "detail": "; ".join(check.reasons) or None,
    }


class _InvalidInputError(Exception):
    """A supplied path or document is syntactically unusable (usage error)."""


class _UsageParser(argparse.ArgumentParser):
    """Argument parser that maps every usage failure to exit code 3."""

    def error(self, message: str) -> None:  # noqa: D401 - argparse contract
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root() -> Path:
    return SCRIPT_PATH.parents[2]


def _harness_relpath() -> str:
    try:
        return str(SCRIPT_PATH.relative_to(_repo_root()))
    except ValueError:
        return str(SCRIPT_PATH)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_entry(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise _InvalidInputError(f"not a readable file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _InvalidInputError(f"cannot parse JSON {path}: {exc}") from exc


def _normalise_scenario(value: str) -> ScenarioSpec | None:
    key = value.strip()
    if key.startswith("science:"):
        key = key[len("science:") :]
    return SCENARIO_BY_ALIAS.get(key)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number of seconds")
    return parsed


def _cap_pair(value: Any) -> tuple[int | None, int | None]:
    """Return ``(limit, used)`` for an int or ``{limit, used}`` cap."""
    if isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        return (value, 0) if value >= 0 else (None, None)
    if isinstance(value, dict):
        limit = value.get("limit")
        used = value.get("used", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            return None, None
        if isinstance(used, bool) or not isinstance(used, int) or used < 0:
            return None, None
        return limit, used
    return None, None


def _budget_report(run_spec: dict[str, Any], *, scenario: str | None = None) -> dict[str, Any]:
    """Validate total/phase/scenario/per-unit caps plus separate unit counts.

    The report is pure: it never reads a filesystem path and never reserves or
    consumes budget.  ``status`` is ``ok``, ``exhausted`` (refuse to submit) or
    ``invalid`` (malformed run spec, a usage error).
    """
    scheduler = str(run_spec.get("scheduler", "local")).lower()
    report: dict[str, Any] = {
        "status": "ok",
        "scheduler": scheduler,
        "caps": {},
        "counts": {},
        "deadlines": {},
        "gaps": [],
        "missing": [],
        "malformed": [],
        "exhausted": [],
    }
    budget = run_spec.get("budget")
    if not isinstance(budget, dict):
        report["status"] = "invalid"
        report["gaps"].append("budget object required")
        report["missing"].append("budget object required")
        return report

    caps = budget.get("caps")
    if not isinstance(caps, dict):
        report["gaps"].append("budget.caps object required")
        report["missing"].append("budget.caps object required")
    else:
        for name in ("total", "phase", "scenario", "per_unit"):
            if name not in caps:
                report["gaps"].append(f"budget.caps.{name} required")
                report["missing"].append(f"budget.caps.{name} required")
                continue
            limit, used = _cap_pair(caps[name])
            if limit is None or used is None:
                gap = f"budget.caps.{name} must be a non-negative int"
                report["gaps"].append(gap)
                report["malformed"].append(gap)
                continue
            report["caps"][name] = {"limit": limit, "used": used}
            if limit <= 0:
                report["exhausted"].append(f"cap {name} disabled (limit={limit})")
            elif used >= limit:
                report["exhausted"].append(f"cap {name} exhausted ({used}/{limit})")

    counts = budget.get("counts")
    required_units = ("task_ref", "qc_invocation", "attempt")
    if scenario != SINGLEPOINT_ALIAS:
        required_units = ("task_ref", "recipe_step", "qc_invocation", "attempt")
    active_units = list(required_units)
    if scheduler in ("lsf", "remote"):
        active_units.append("bsub")
    if not isinstance(counts, dict):
        report["gaps"].append("budget.counts object required")
        report["missing"].append("budget.counts object required")
    else:
        for name in ("task_ref", "recipe_step", "qc_invocation", "attempt", "bsub"):
            if name not in counts:
                gap = f"budget.counts.{name} required"
                report["gaps"].append(gap)
                report["missing"].append(gap)
                continue
            limit, used = _cap_pair(counts[name])
            if limit is None or used is None:
                gap = f"budget.counts.{name} must be a non-negative int"
                report["gaps"].append(gap)
                report["malformed"].append(gap)
                continue
            report["counts"][name] = {"limit": limit, "used": used}
            if limit == 0:
                if name in active_units:
                    report["exhausted"].append(f"count {name} disabled for {scheduler} scenario")
            elif used >= limit:
                report["exhausted"].append(f"count {name} exhausted ({used}/{limit})")

    deadlines = budget.get("deadlines")
    if not isinstance(deadlines, dict):
        report["gaps"].append("budget.deadlines object required")
        report["missing"].append("budget.deadlines object required")
    else:
        for name in ("queue_s", "compute_s", "client_s", "total_s"):
            value = deadlines.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or not math.isfinite(value)
            ):
                gap = f"budget.deadlines.{name} must be a non-negative number"
                report["gaps"].append(gap)
                report["malformed"].append(gap)
                continue
            report["deadlines"][name] = value

    if report["gaps"]:
        report["status"] = "invalid"
    elif report["exhausted"]:
        report["status"] = "exhausted"
    return report


def _validate_run_spec(run_spec: Any) -> list[dict[str, str]]:
    """Return typed issues for a run spec.

    ``missing`` issues are absent scientific/platform prerequisites and are
    reported as ``NOT_VERIFIED`` (exit 2); ``invalid`` issues are malformed
    documents and remain usage errors (exit 3).
    """
    if not isinstance(run_spec, dict):
        return [
            {
                "kind": "invalid",
                "message": "run spec must be a JSON object",
            }
        ]
    issues: list[dict[str, str]] = []

    def missing(message: str) -> None:
        issues.append({"kind": "missing", "message": message})

    def invalid(message: str) -> None:
        issues.append({"kind": "invalid", "message": message})

    for key in ("run_key", "interpreter", "native_work_root"):
        value = run_spec.get(key)
        if not isinstance(value, str) or not value.strip():
            missing(f"{key} must be a non-empty string")
    run_key = run_spec.get("run_key")
    if isinstance(run_key, str) and run_key.strip().lower() in _PLACEHOLDER_RUN_KEYS:
        missing("run_key must be a platform-issued key, not a placeholder")
    interpreter = run_spec.get("interpreter")
    if isinstance(interpreter, str) and interpreter.strip() and not Path(interpreter).is_absolute():
        invalid("interpreter must be an absolute path")
    native_root = run_spec.get("native_work_root")
    if isinstance(native_root, str) and native_root and not Path(native_root).is_absolute():
        invalid("native_work_root must be an absolute path")

    software = run_spec.get("software")
    if not isinstance(software, dict):
        missing("software object required")
    else:
        for key in ("name", "version", "executable"):
            value = software.get(key)
            if not isinstance(value, str) or not value.strip():
                missing(f"software.{key} must be a non-empty string")
        executable = software.get("executable")
        if isinstance(executable, str) and executable.strip():
            if not Path(executable).is_absolute():
                invalid("software.executable must be an absolute path")

    digest = run_spec.get("source_digest") or run_spec.get("wheel_digest")
    if not isinstance(digest, str) or not _is_digest(digest):
        missing("source_digest or wheel_digest must be sha256:<64 lowercase hex>")

    scheduler = run_spec.get("scheduler", "local")
    if scheduler is not None and str(scheduler).lower() not in ("local", "lsf", "remote"):
        invalid("scheduler must be one of: local, lsf, remote")

    if not isinstance(run_spec.get("budget"), dict):
        missing("budget object required")
    return issues


def _is_digest(value: str) -> bool:
    return re.fullmatch(_DIGEST_PATTERN, value) is not None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _approval_status(
    run_spec: Mapping[str, Any], spec: ScenarioSpec, run_id: str
) -> dict[str, Any]:
    """Check the explicit platform approval record bound to this run.

    The harness cannot verify a signature or a registry row; it only refuses a
    run whose approval is absent, false or issued for a different
    ``(scenario, phase, run_id)`` tuple.  Authority stays with B01/B07.
    """
    approval = run_spec.get("run_key_approval")
    reasons: list[str] = []
    record: dict[str, Any] | None = None
    if not isinstance(approval, Mapping):
        reasons.append("run_key_approval object required (platform-approved run key)")
    else:
        record = dict(approval)
        if approval.get("approved") is not True:
            reasons.append("run_key_approval.approved must be true")
        for key in ("authority", "issued_at", "budget_ref"):
            value = approval.get(key)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"run_key_approval.{key} must be a non-empty string")
        expected = {
            "owner": "A",
            "scenario": spec.canonical,
            "phase": spec.phase,
            "run_id": run_id,
        }
        for key, wanted in expected.items():
            actual = approval.get(key)
            if actual != wanted:
                reasons.append(
                    f"run_key_approval.{key} must be {wanted!r} for this run (got {actual!r})"
                )
    return {
        "approved": not reasons,
        "reasons": reasons,
        "record": record,
        "run_key": run_spec.get("run_key"),
    }


def _native_root_independence(
    *,
    run_spec: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    raw = run_spec.get("native_work_root")
    reasons: list[str] = []
    path: Path | None = None
    if not isinstance(raw, str) or not raw.strip():
        reasons.append("native_work_root must be a non-empty absolute path")
    else:
        path = Path(raw)
        resolved = path.resolve()
        if not path.is_absolute():
            reasons.append("native_work_root must be absolute")
        if _is_relative_to(resolved, repo_root):
            reasons.append("native_work_root must be outside the science worktree")
        production = [Path(root) for root in PRODUCTION_RUN_ROOTS]
        env_root = os.environ.get("ACP_RUN_ROOT")
        if env_root:
            production.append(Path(env_root))
        for root in production:
            if _is_relative_to(resolved, root.resolve()):
                reasons.append(f"native_work_root must not be a production run root ({root})")
    return {
        "path": str(path) if path is not None else raw,
        "independent": not reasons,
        "reasons": reasons,
    }


def _software_status(run_spec: Mapping[str, Any]) -> dict[str, Any]:
    software = run_spec.get("software")
    reasons: list[str] = []
    name: str | None = None
    version: str | None = None
    executable: str | None = None
    exists = False
    if not isinstance(software, Mapping):
        reasons.append("software object required")
    else:
        name = software.get("name") if isinstance(software.get("name"), str) else None
        version = software.get("version") if isinstance(software.get("version"), str) else None
        raw = software.get("executable")
        executable = raw if isinstance(raw, str) else None
        if not executable:
            reasons.append("software.executable must be a non-empty absolute path")
        else:
            path = Path(executable)
            if not path.is_absolute():
                reasons.append("software.executable must be absolute")
            if not path.exists():
                reasons.append(f"software executable not found: {executable}")
            else:
                exists = True
                if not path.is_file():
                    reasons.append(f"software executable is not a file: {executable}")
                elif not os.access(path, os.X_OK):
                    reasons.append(f"software executable is not executable: {executable}")
    return {
        "name": name,
        "version": version,
        "executable": executable,
        "exists": exists,
        "available": not reasons,
        "reasons": reasons,
    }


def _candidate_status(repo_root: Path) -> dict[str, Any]:
    """Read H2's published server/client/harness candidate without inventing it."""
    handoff = repo_root / H2_HANDOFF_RELPATH
    candidate: dict[str, Any] = {
        "server_revision": None,
        "client_revision": None,
        "harness_revision": None,
    }
    gaps: dict[str, str] = {
        "server_revision": "no MCP server revision has been published",
        "client_revision": "no MCP client revision has been published",
        "harness_revision": "no integration/DSH harness revision has been published",
        "h2_test_candidate": "no H2 integration test candidate has been published",
    }
    h2_test_candidate: Any = None
    handoff_status = "NOT_VERIFIED"
    handoff_sha256: str | None = None
    if handoff.is_file():
        handoff_sha256 = _sha256(handoff)
        try:
            document = json.loads(handoff.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = None
        external = document.get("external_evidence") if isinstance(document, dict) else None
        if isinstance(external, Mapping):
            for key in candidate:
                candidate[key] = external.get(key)
                gap = external.get(f"{key}_gap")
                if isinstance(gap, str) and gap.strip():
                    gaps[key] = gap
            h2_test_candidate = external.get("h2_test_candidate")
            if isinstance(h2_test_candidate, Mapping) and h2_test_candidate.get(
                "candidate_revision"
            ):
                handoff_status = "PUBLISHED"
            if isinstance(h2_test_candidate, Mapping):
                gap = h2_test_candidate.get("gap")
                if isinstance(gap, str) and gap.strip():
                    gaps["h2_test_candidate"] = gap
    else:
        gaps["handoff"] = f"{H2_HANDOFF_RELPATH} is not present in this worktree"
    published = handoff_status == "PUBLISHED"
    return {
        "status": handoff_status,
        "published": published,
        "candidate": candidate,
        "gaps": gaps,
        "h2_test_candidate": h2_test_candidate,
        "handoff_path": str(handoff),
        "handoff_sha256": handoff_sha256,
    }


def _unpack_snapshot(
    plan: Mapping[str, Any], fixture_dir: Path
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    """Return the artifact-snapshot document and whether it is constructed.

    A platform snapshot is passed through unchanged.  A constructed wrapper is
    unwrapped only when it declares ``constructed: true``; its relative or
    ``@fixture`` read root is resolved against the supplied fixture directory.
    """
    document = plan.get("input_manifest")
    if not isinstance(document, Mapping):
        return None, False, ["input manifest must be a JSON object"]
    if document.get("schema") == CONSTRUCTED_SNAPSHOT_SCHEMA:
        if document.get("constructed") is not True:
            return None, True, ["constructed snapshot wrapper must declare constructed=true"]
        raw_snapshot = document.get("snapshot")
        if not isinstance(raw_snapshot, Mapping):
            return None, True, ["constructed snapshot wrapper has no snapshot object"]
        payload = dict(raw_snapshot)
        raw_root = payload.get("read_root")
        if raw_root == FIXTURE_READ_ROOT_TOKEN:
            payload["read_root"] = str(fixture_dir.resolve())
        elif isinstance(raw_root, str) and raw_root and not Path(raw_root).is_absolute():
            payload["read_root"] = str((fixture_dir / raw_root).resolve())
        return payload, True, []
    payload = dict(document)
    raw_root = payload.get("read_root")
    if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
        return None, False, ["snapshot.read_root must be an absolute path"]
    return payload, False, []


def _registry_entries(document: Any) -> list[Any] | None:
    if isinstance(document, list):
        return document
    if not isinstance(document, Mapping):
        return None
    for key in ("entries", "runs", "records", "registry", "items"):
        value = document.get(key)
        if isinstance(value, list):
            return value
    for key in ("id", "run_id", "run_key"):
        if isinstance(document.get(key), str):
            return [document]
    return None


def _registry_entry_ids(entries: list[Any]) -> list[str]:
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            ids.append(entry)
        elif isinstance(entry, Mapping):
            for key in ("id", "run_id", "run_key", "name"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    ids.append(value)
                    break
    return ids


def _platform_registry_view(path: str | None, document: Any) -> dict[str, Any]:
    """Confirm the registered run id against an explicitly supplied registry."""
    if not path:
        return {
            "status": "MISSING",
            "registered": False,
            "constructed": None,
            "path": None,
            "ids": [],
            "reasons": [PLATFORM_REGISTRY_GAP],
        }
    if not isinstance(document, (Mapping, list)):
        return {
            "status": "INVALID",
            "registered": False,
            "constructed": None,
            "path": path,
            "ids": [],
            "reasons": ["platform registry must be a JSON object or list"],
        }
    entries = _registry_entries(document)
    if entries is None:
        return {
            "status": "INVALID",
            "registered": False,
            "constructed": None,
            "path": path,
            "ids": [],
            "reasons": ["platform registry has no entries/runs/records list and no single run id"],
        }
    ids = _registry_entry_ids(entries)
    constructed = bool(
        (isinstance(document, Mapping) and document.get("constructed") is True)
        or any(isinstance(entry, Mapping) and entry.get("constructed") is True for entry in entries)
    )
    registered = REGISTERED_OPT_FREQ_RUN_ID in ids
    reasons: list[str] = []
    if not registered:
        reasons.append(
            f"registered id {REGISTERED_OPT_FREQ_RUN_ID!r} is absent from the supplied "
            f"platform registry ({path}); ids seen: {sorted(ids)}"
        )
    return {
        "status": "REGISTERED" if registered else "UNREGISTERED",
        "registered": registered,
        "constructed": constructed,
        "path": path,
        "ids": ids,
        "reasons": reasons,
    }


def _single_role_artifact(snapshot: Any, role: str) -> tuple[Any | None, list[str]]:
    matches = [artifact for artifact in snapshot.artifacts if artifact.role == role]
    if not matches:
        return None, [f"snapshot declares no {role!r} artifact"]
    if len(matches) > 1:
        return None, [
            f"snapshot declares {len(matches)} {role!r} artifacts; exactly one is required"
        ]
    return matches[0], []


def _load_task_results(snapshot: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    """Parse exactly one optimize and one frequency TaskResult from provenance."""
    from cccp.calculation.results import TaskResult

    results: dict[str, Any] = {}
    gaps: list[str] = []
    contradictions: list[str] = []
    for artifact in snapshot.artifacts:
        if artifact.role != "provenance":
            continue
        try:
            document = json.loads(artifact.resolved_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            contradictions.append(f"{artifact.relative_path}: unreadable provenance JSON: {exc}")
            continue
        if not isinstance(document, Mapping) or document.get("task") not in (
            "optimize",
            "frequency",
        ):
            continue
        task = str(document["task"])
        try:
            result = TaskResult.from_dict(document)
        except (TypeError, ValueError) as exc:
            contradictions.append(f"{artifact.relative_path}: invalid TaskResult document: {exc}")
            continue
        if task in results:
            contradictions.append(
                f"duplicate {task} TaskResult in provenance ({artifact.relative_path})"
            )
            continue
        results[task] = result
    for task in ("optimize", "frequency"):
        if task not in results:
            gaps.append(f"snapshot carries no {task} TaskResult provenance artifact")
    return results, gaps, contradictions


def _science_identity_consistency(
    snapshot: Any, optimize_result: Any, frequency_result: Any
) -> dict[str, Any]:
    """Check TaskResults against the snapshot's declared method/state."""
    identity = snapshot.science_identity
    contradictions: list[str] = []
    gaps: list[str] = []
    for label, result in (("optimize", optimize_result), ("frequency", frequency_result)):
        provenance = getattr(result, "provenance", None)
        method = getattr(provenance, "method", None)
        if not method:
            gaps.append(f"{label} TaskResult declares no method")
        elif method != identity.method:
            contradictions.append(
                f"{label} method {method!r} differs from snapshot method {identity.method!r}"
            )
        payload = getattr(result, "payload", None)
        state = getattr(payload, "electronic_state", None)
        if not isinstance(state, Mapping):
            gaps.append(f"{label} TaskResult declares no electronic state")
            continue
        state_id = state.get("state_id")
        if state_id is not None and identity.electronic_state.state_id is not None:
            if state_id != identity.electronic_state.state_id:
                contradictions.append(
                    f"{label} state_id {state_id!r} differs from snapshot "
                    f"state_id {identity.electronic_state.state_id!r}"
                )
        multiplicity = state.get("target_multiplicity")
        if multiplicity is not None and int(multiplicity) != identity.electronic_state.multiplicity:
            contradictions.append(
                f"{label} multiplicity {multiplicity!r} differs from snapshot "
                f"multiplicity {identity.electronic_state.multiplicity}"
            )
    return {
        "status": "FAIL" if contradictions else ("NOT_VERIFIED" if gaps else "PASS"),
        "contradictions": contradictions,
        "gaps": gaps,
    }


def _native_evidence(
    optimize_path: Path, frequency_path: Path, optimize_result: Any, frequency_result: Any
) -> dict[str, Any]:
    """Independently re-parse the native optimize/frequency outputs."""
    from cccp.analysis.orca_parser import OrcaOutputParser

    parser = OrcaOutputParser()
    optimize_calc = parser.parse(optimize_path)
    frequency_calc = parser.parse(frequency_path)
    contradictions: list[str] = []
    gaps: list[str] = []

    if optimize_calc.final_energy_hartree is None:
        gaps.append("optimize native output exposes no final single-point energy")
    elif not optimize_calc.converged:
        contradictions.append("optimize native output does not report convergence")
    elif optimize_result.energy_hartree is not None and (
        abs(float(optimize_result.energy_hartree) - optimize_calc.final_energy_hartree)
        > NATIVE_ENERGY_TOLERANCE_HARTREE
    ):
        contradictions.append(
            "optimize native energy differs from the TaskResult beyond "
            f"{NATIVE_ENERGY_TOLERANCE_HARTREE} Ha"
        )

    if not frequency_calc.frequencies:
        gaps.append("frequency native output exposes no vibrational frequencies")
    elif not frequency_calc.mode_vectors:
        gaps.append("frequency native output exposes no normal-mode vectors")
    else:
        task_frequencies = tuple(float(value) for value in frequency_result.frequencies)
        native_frequencies = tuple(float(value) for value in frequency_calc.frequencies)
        if len(task_frequencies) != len(native_frequencies):
            contradictions.append(
                "frequency native mode count differs from the TaskResult "
                f"({len(native_frequencies)} vs {len(task_frequencies)})"
            )
        else:
            deltas = [
                abs(left - right)
                for left, right in zip(native_frequencies, task_frequencies, strict=True)
            ]
            if any(delta > NATIVE_FREQUENCY_TOLERANCE_CM1 for delta in deltas):
                contradictions.append(
                    "frequency native modes differ from the TaskResult beyond "
                    f"{NATIVE_FREQUENCY_TOLERANCE_CM1} cm-1"
                )

    return {
        "status": "FAIL" if contradictions else ("NOT_VERIFIED" if gaps else "PASS"),
        "contradictions": contradictions,
        "gaps": gaps,
        "optimize": {
            "converged": optimize_calc.converged,
            "final_energy_hartree": optimize_calc.final_energy_hartree,
            "normal_termination": optimize_calc.success,
            "opt_cycles": optimize_calc.n_opt_cycles,
        },
        "frequency": {
            "frequencies_cm1": list(frequency_calc.frequencies),
            "imaginary_modes_cm1": list(frequency_calc.imaginary_modes),
            "mode_count": len(frequency_calc.frequencies),
            "mode_vectors": len(frequency_calc.mode_vectors),
        },
    }


def _stationary_point_report(validation: Any, frequencies: tuple[float, ...]) -> dict[str, Any]:
    report = validation.to_dict()
    convergence = validation.claim("c-optimization-converged")
    report["convergence"] = (
        None
        if convergence is None
        else {
            "status": convergence.status,
            "reason": convergence.reason,
            "claim_id": convergence.claim_id,
        }
    )
    report["modes_cm1"] = [float(value) for value in frequencies]
    report["all_modes"] = [float(value) for value in frequencies]
    return report


def _print_stationary_point_report(report: Mapping[str, Any]) -> None:
    convergence = report.get("convergence") or {}
    modes = report.get("all_modes") or []
    print("stationary-point report")
    print(f"  convergence: {convergence.get('status')} ({convergence.get('reason')})")
    print(f"  imaginary-mode count: {report.get('imaginary_mode_count')}")
    print(f"  mode count: {report.get('mode_count')}")
    print("  all modes (cm-1): " + ", ".join(f"{float(value):.2f}" for value in modes))
    print(f"  stationary-point status: {report.get('status')}")


def _parse_native_energy(text: str, software_name: str | None) -> dict[str, Any]:
    """Independently parse an energy from native output bytes."""
    key = (software_name or "").strip().lower()
    parsers: list[tuple[str, re.Pattern[str], re.Pattern[str] | None]] = []
    for name, (energy_re, termination_re) in _NATIVE_MARKERS.items():
        if not key or key == name:
            parsers.append((name, energy_re, termination_re))
    for name, energy_re, termination_re in parsers:
        matches = energy_re.findall(text)
        if not matches:
            continue
        raw = matches[-1].replace("D", "E").replace("d", "e")
        try:
            energy = float(raw)
        except ValueError:
            continue
        if not math.isfinite(energy):
            continue
        return {
            "found": True,
            "software": name,
            "energy_hartree": energy,
            "unit": "hartree",
            "marker": energy_re.pattern,
            "occurrences": len(matches),
            "normal_termination": bool(
                termination_re.search(text) if termination_re is not None else False
            ),
            "termination_marker": termination_re.pattern if termination_re else None,
        }
    return {
        "found": False,
        "software": key or None,
        "energy_hartree": None,
        "unit": None,
        "marker": None,
        "occurrences": 0,
        "normal_termination": False,
        "termination_marker": None,
        "reason": "no recognised native energy marker",
    }


def _compare_energy(
    result_energy: float | None,
    parsed: Mapping[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    """Compare the typed Hartree energy with the independent native parse."""
    if not parsed.get("found") or parsed.get("energy_hartree") is None:
        return {
            "match": False,
            "reason": "independent parser found no energy",
            "delta_hartree": None,
            "tolerance_hartree": tolerance,
        }
    if result_energy is None or not math.isfinite(float(result_energy)):
        return {
            "match": False,
            "reason": "TaskResult.energy_hartree is missing or non-finite",
            "delta_hartree": None,
            "tolerance_hartree": tolerance,
        }
    if parsed.get("unit") != "hartree":
        return {
            "match": False,
            "reason": f"independent parser unit is {parsed.get('unit')!r}, expected 'hartree'",
            "delta_hartree": None,
            "tolerance_hartree": tolerance,
        }
    delta = abs(float(result_energy) - float(parsed["energy_hartree"]))
    return {
        "match": delta <= tolerance,
        "reason": (
            None
            if delta <= tolerance
            else f"energy mismatch: |delta|={delta:.3e} > {tolerance:.3e}"
        ),
        "delta_hartree": delta,
        "tolerance_hartree": tolerance,
    }


def _file_status(label: str, path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "kind": "file",
        "required": True,
        "exists": path.exists(),
        "is_file": path.is_file(),
    }
    if not path.exists():
        info["status"] = "missing"
    elif not path.is_file():
        info["status"] = "wrong_type"
    else:
        info["status"] = "present"
        info["size_bytes"] = path.stat().st_size
    return info


def _directory_status(label: str, path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "kind": "dir",
        "required": True,
        "exists": path.exists(),
        "is_dir": path.is_dir(),
    }
    if not path.exists():
        parent = path.parent
        info["parent"] = str(parent)
        info["parent_exists"] = parent.is_dir()
        info["status"] = "parent_missing" if not info["parent_exists"] else "absent"
    elif not path.is_dir():
        info["status"] = "wrong_type"
    else:
        try:
            entries = list(path.iterdir())
        except OSError:
            entries = []
        info["non_empty"] = bool(entries)
        info["status"] = "present"
        if (path / "result.json").exists():
            info["status"] = "would_overwrite"
        elif entries:
            info["status"] = "would_overwrite"
    return info


def _native_root_status(run_spec: dict[str, Any]) -> dict[str, Any]:
    raw = run_spec.get("native_work_root")
    if not isinstance(raw, str) or not raw:
        return {"label": "native_work_root", "path": raw, "status": "missing"}
    path = Path(raw)
    info: dict[str, Any] = {
        "label": "native_work_root",
        "path": raw,
        "absolute": path.is_absolute(),
        "exists": path.exists(),
        "parent": str(path.parent),
        "parent_exists": path.parent.is_dir(),
        "created": False,
    }
    if not info["absolute"]:
        info["status"] = "invalid"
    elif not info["parent_exists"]:
        info["status"] = "parent_missing"
    else:
        info["status"] = "ok"
    return info


def _executable_status(label: str, raw: str | None) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {"label": label, "path": raw, "status": "missing", "advisory": True}
    path = Path(raw)
    if path.is_absolute() or "/" in raw:
        exists = path.exists()
        resolved = str(path)
    else:
        found = shutil.which(raw)
        exists = found is not None
        resolved = found or raw
    return {
        "label": label,
        "path": resolved,
        "status": "present" if exists else "missing",
        "advisory": True,
    }


def _record_path_error(plan: dict[str, Any], status: dict[str, Any]) -> None:
    kind = status.get("status")
    if kind == "missing":
        plan["errors"].append(
            {
                "kind": "missing",
                "label": status["label"],
                "message": f"{status['label']} not found: {status['path']}",
            }
        )
    elif kind in ("wrong_type", "would_overwrite"):
        plan["errors"].append(
            {
                "kind": "invalid",
                "label": status["label"],
                "message": f"{status['label']} cannot be used ({kind}): {status['path']}",
            }
        )
    elif kind == "parent_missing":
        plan["errors"].append(
            {
                "kind": "missing",
                "label": status["label"],
                "message": f"{status['label']} parent directory is missing: {status['path']}",
            }
        )


def _build_plan(args: argparse.Namespace, spec: ScenarioSpec) -> dict[str, Any]:
    """Collect path and budget facts without creating or executing anything."""
    plan: dict[str, Any] = {
        "paths": [],
        "errors": [],
        "run_spec": None,
        "run_spec_path": None,
        "run_spec_gaps": [],
        "input_manifest": None,
        "input_manifest_path": None,
        "platform_registry": None,
        "platform_registry_path": None,
        "budget": None,
    }

    fixture = _file_status("fixture", Path(args.fixture))
    plan["paths"].append(fixture)
    _record_path_error(plan, fixture)

    out = _directory_status("out", Path(args.out))
    plan["paths"].append(out)
    _record_path_error(plan, out)

    if spec.requires_run_spec:
        run_path = Path(args.run_spec)
        plan["run_spec_path"] = str(run_path)
        run_status = _file_status("run_spec", run_path)
        plan["paths"].append(run_status)
        _record_path_error(plan, run_status)
        if run_status["status"] == "present":
            try:
                document = _load_json(run_path)
            except _InvalidInputError as exc:
                plan["errors"].append({"kind": "invalid", "label": "run_spec", "message": str(exc)})
                document = None
            if isinstance(document, dict):
                plan["run_spec"] = document
                issues = _validate_run_spec(document)
                for issue in issues:
                    if issue["kind"] == "invalid":
                        plan["errors"].append(
                            {
                                "kind": "invalid",
                                "label": "run_spec",
                                "message": issue["message"],
                            }
                        )
                    else:
                        plan["run_spec_gaps"].append(issue["message"])
                if not plan["errors"]:
                    plan["budget"] = _budget_report(document, scenario=spec.alias)
            elif document is not None:
                plan["errors"].append(
                    {
                        "kind": "invalid",
                        "label": "run_spec",
                        "message": "run spec must be a JSON object",
                    }
                )

    if spec.requires_input_manifest:
        raw_manifest = args.input_manifest
        if raw_manifest is None or not str(raw_manifest).strip():
            plan["errors"].append(
                {
                    "kind": "missing",
                    "label": "input_manifest",
                    "message": (
                        "authorized input manifest path is empty: pass --input-manifest "
                        "(env ACP2_ARTIFACT_MANIFEST is unset or empty)"
                    ),
                }
            )
        else:
            manifest_path = Path(raw_manifest)
            plan["input_manifest_path"] = str(manifest_path)
            manifest_status = _file_status("input_manifest", manifest_path)
            plan["paths"].append(manifest_status)
            _record_path_error(plan, manifest_status)
            if manifest_status["status"] == "present":
                try:
                    plan["input_manifest"] = _load_json(manifest_path)
                except _InvalidInputError as exc:
                    plan["errors"].append(
                        {"kind": "invalid", "label": "input_manifest", "message": str(exc)}
                    )

    raw_registry = getattr(args, "platform_registry", None)
    if raw_registry is not None and str(raw_registry).strip():
        registry_path = Path(raw_registry)
        plan["platform_registry_path"] = str(registry_path)
        registry_status = _file_status("platform_registry", registry_path)
        plan["paths"].append(registry_status)
        _record_path_error(plan, registry_status)
        if registry_status["status"] == "present":
            try:
                plan["platform_registry"] = _load_json(registry_path)
            except _InvalidInputError as exc:
                plan["errors"].append(
                    {"kind": "invalid", "label": "platform_registry", "message": str(exc)}
                )

    return plan


def _fixture_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(document, dict):
        for key in ("fixture_id", "fixture_manifest_id", "id"):
            value = document.get(key)
            if isinstance(value, str) and value:
                return value
    return path.stem


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_repo_root()))
    except ValueError:
        return str(path.resolve())


def _receipt_protocol() -> dict[str, Any]:
    """Describe B01's receipt protocol without inventing or copying a schema."""
    root = _repo_root()
    schema_candidates: list[Path] = []
    env_schema = os.environ.get("ACP2_ACCEPTANCE_SCHEMA")
    if env_schema:
        schema_candidates.append(Path(env_schema))
    contract_root = root / "docs/reference/next-change/acp2-contracts"
    schema_candidates.extend(
        [
            contract_root / "acceptance/acceptance-result.schema.json",
            contract_root / "science/acceptance-result.schema.json",
            contract_root / "acceptance-result.schema.json",
            contract_root / "runtime/acceptance-result.schema.json",
        ]
    )
    schema_path = next((path for path in schema_candidates if path.is_file()), None)

    sample_candidates: list[Path] = []
    if schema_path is not None:
        sample_candidates.extend(
            [
                schema_path.parent / "receipt-valid.json",
                schema_path.parent / "receipt-invalid.json",
                schema_path.parent / "examples/receipt-valid.json",
                schema_path.parent / "examples/receipt-invalid.json",
            ]
        )
    sample_candidates.extend(
        [
            contract_root / "science/examples/receipt-valid.json",
            contract_root / "science/examples/receipt-invalid.json",
        ]
    )
    valid_sample = next(
        (
            path
            for path in sample_candidates
            if path.name == "receipt-valid.json" and path.is_file()
        ),
        None,
    )
    invalid_sample = next(
        (
            path
            for path in sample_candidates
            if path.name == "receipt-invalid.json" and path.is_file()
        ),
        None,
    )

    validator_candidates = [
        root / "scripts/acceptance/validate_acceptance_result.py",
        root / "scripts/acceptance/acceptance_result.py",
        contract_root / "acceptance/validator.py",
        contract_root / "acceptance/validate.py",
    ]
    validator_path = next((path for path in validator_candidates if path.is_file()), None)

    missing: list[str] = []
    if schema_path is None:
        missing.append(RECEIPT_SCHEMA)
    if validator_path is None:
        missing.append("shared validator")
    if valid_sample is None:
        missing.append("receipt-valid.json")
    if invalid_sample is None:
        missing.append("receipt-invalid.json")
    status = "AVAILABLE" if not missing else "NOT_VERIFIED"
    if status == "AVAILABLE":
        reason = "B01 receipt schema, shared validator and both samples are present."
    else:
        reason = (
            "B01 receipt protocol missing: "
            + ", ".join(missing)
            + ". A does not define a second receipt schema, copy an unpublished schema, "
            "or invent valid/invalid samples."
        )

    return {
        "status": status,
        "reason": reason,
        "missing": missing,
        "missing_artifact": RECEIPT_SCHEMA if schema_path is None else None,
        "missing_contract": RECEIPT_CONTRACT if schema_path is None else None,
        "published_copy_found": schema_path is not None,
        "schema_path": str(schema_path) if schema_path is not None else None,
        "schema_sha256": _sha256(schema_path) if schema_path is not None else None,
        "shared_validator_present": validator_path is not None,
        "shared_validator_path": str(validator_path) if validator_path is not None else None,
        "valid_sample_present": valid_sample is not None,
        "valid_sample_path": str(valid_sample) if valid_sample is not None else None,
        "invalid_sample_present": invalid_sample is not None,
        "invalid_sample_path": str(invalid_sample) if invalid_sample is not None else None,
        "samples_delivered": bool(valid_sample and invalid_sample),
        "deferred_samples": [
            _relative(contract_root / "science/examples/receipt-valid.json"),
            _relative(contract_root / "science/examples/receipt-invalid.json"),
        ],
        "delivery_trigger": RECEIPT_DELIVERY_TRIGGER,
        "search_roots": [_relative(path) for path in schema_candidates],
    }


def _prerequisites(plan: dict[str, Any], receipt: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for status in plan["paths"]:
        items.append(f"{status['label']}: {status['status']} ({status['path']})")
    if plan["budget"] is not None:
        items.append(f"budget: {plan['budget']['status']}")
    items.append(f"shared_receipt_protocol: {receipt['status']}")
    return items


def _assertions(plan: dict[str, Any], receipt: dict[str, Any]) -> list[dict[str, Any]]:
    invalid = [error for error in plan["errors"] if error["kind"] == "invalid"]
    missing = [error for error in plan["errors"] if error["kind"] == "missing"]
    assertions: list[dict[str, Any]] = [
        {
            "name": "required_flags_present",
            "status": "FAIL" if invalid else "PASS",
            "detail": "; ".join(error["message"] for error in invalid) or None,
        },
        {
            "name": "input_paths_present",
            "status": "NOT_VERIFIED" if missing else "PASS",
            "detail": "; ".join(error["message"] for error in missing) or None,
        },
    ]
    budget = plan["budget"]
    if budget is not None:
        budget_status = {
            "ok": "PASS",
            "exhausted": "NOT_VERIFIED",
            "invalid": "FAIL",
        }[budget["status"]]
        assertions.append(
            {
                "name": "budget_preflight",
                "status": budget_status,
                "detail": "; ".join(budget["gaps"] or budget["exhausted"]) or None,
            }
        )
    assertions.append(
        {
            "name": "shared_validator",
            "status": "PASS" if receipt["status"] == "AVAILABLE" else "NOT_VERIFIED",
            "detail": receipt["reason"],
        }
    )
    assertions.append(
        {
            "name": "scenario_executed",
            "status": "NOT_VERIFIED",
            "detail": "no scenario was executed",
        }
    )
    return assertions


def _write_result(
    out: Path,
    record: dict[str, Any],
    argv_document: dict[str, Any],
    *,
    extra_json: Mapping[str, Any] | None = None,
    extra_bytes: Mapping[str, bytes] | None = None,
) -> None:
    """Write ``result.json`` and ``argv.json`` without overwriting a run."""
    if out.exists():
        if not out.is_dir():
            raise _InvalidInputError(f"--out exists and is not a directory: {out}")
        if any(out.iterdir()):
            raise _InvalidInputError(f"refusing to overwrite a non-empty evidence directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    _atomic_json(out / "result.json", record)
    _atomic_json(out / "argv.json", argv_document)
    for name, payload in (extra_json or {}).items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(target, payload)
    for name, payload in (extra_bytes or {}).items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _finish_scenario(
    *,
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    reason: str,
    started_at: str,
    argv: list[str],
    result: str = "NOT_VERIFIED",
    exit_code: int = EXIT_NOT_VERIFIED,
    platform_export: PlatformExportCheck | None = None,
    assertions: list[dict[str, Any]] | None = None,
    extra: Mapping[str, Any] | None = None,
    extra_json: Mapping[str, Any] | None = None,
    extra_bytes: Mapping[str, bytes] | None = None,
    extra_artifacts: list[dict[str, Any]] | None = None,
) -> int:
    out_status = next((status for status in plan["paths"] if status["label"] == "out"), None)
    if out_status is not None and out_status["status"] in (
        "wrong_type",
        "would_overwrite",
    ):
        print(
            f"usage error: --out cannot be used ({out_status['status']}): {out_status['path']}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if out_status is not None and out_status["status"] == "parent_missing":
        print(
            f"NOT_VERIFIED: cannot write result; --out parent is missing: {out_status['path']}",
            file=sys.stderr,
        )
        return EXIT_NOT_VERIFIED

    fixture_path = Path(args.fixture)
    run_spec = plan["run_spec"] or {}
    software = run_spec.get("software") if isinstance(run_spec, dict) else None
    software_version = software.get("version") if isinstance(software, dict) else None
    artifacts = [
        entry
        for entry in (
            _artifact_entry(fixture_path),
            _artifact_entry(Path(args.run_spec) if args.run_spec else None),
            _artifact_entry(Path(args.input_manifest) if args.input_manifest else None),
        )
        if entry is not None
    ]
    artifacts.extend(extra_artifacts or [])
    if assertions is None:
        assertions = _assertions(plan, receipt)
    if platform_export is not None:
        assertions.append(_platform_export_assertion(platform_export))
    finished_at = _utc_now()
    science_extension: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "harness": _harness_relpath(),
        "harness_sha256": _sha256(SCRIPT_PATH),
        "receipt_protocol_gap": ("NOT_VERIFIED" if receipt["status"] != "AVAILABLE" else None),
        "receipt_protocol": receipt,
        "platform_export": (platform_export.to_dict() if platform_export is not None else None),
        "interpreter": run_spec.get("interpreter") or sys.executable,
        "software": software,
        "software_version": software_version,
        "fixture_id": _fixture_id(fixture_path),
        "run_key": run_spec.get("run_key"),
        "native_work_root": run_spec.get("native_work_root"),
        "budget": plan["budget"],
        "assertions": assertions,
        "prerequisites": _prerequisites(plan, receipt),
        "path_checks": plan["paths"],
        "limitations": [
            "No controller, scheduler, job or production run root is touched.",
            "B01 acceptance-result.schema.json / acp2.acceptance.v1 is not published; "
            "this record is provisional and is not receipt-validated.",
            "No B01 valid/invalid sample exists, so none is copied or invented.",
        ],
    }
    science_extension.update(extra or {})
    record = {
        "record_state": RECORD_STATE,
        "owner": "A",
        "scenario": spec.canonical,
        "scenario_alias": spec.alias,
        "phase": spec.phase,
        "run_id": Path(args.out).name,
        "result": result,
        "scope": "IN_SCOPE",
        "reason": reason,
        "command": _invocation(argv),
        "cwd": str(Path.cwd()),
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "artifacts": artifacts,
        "extensions": {"science": science_extension},
    }
    argv_document = {
        "argv": _invocation(argv),
        "cwd": str(Path.cwd()),
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
    }
    try:
        _write_result(
            Path(args.out),
            record,
            argv_document,
            extra_json=extra_json,
            extra_bytes=extra_bytes,
        )
    except _InvalidInputError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"{result}: {reason}", file=sys.stderr)
    return exit_code


def _invocation(argv: list[str]) -> list[str]:
    return [sys.executable, str(SCRIPT_PATH), *argv]


def _scenario_assertions(
    *,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    approval: Mapping[str, Any],
    native_root: Mapping[str, Any],
    software: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    run_spec_gaps = plan["run_spec_gaps"]
    budget = plan["budget"] or {}
    budget_status = {
        "ok": "PASS",
        "exhausted": "NOT_VERIFIED",
        "invalid": "FAIL",
    }.get(budget.get("status"), "NOT_VERIFIED")
    return [
        {
            "name": "run_spec_complete",
            "status": "NOT_VERIFIED" if run_spec_gaps else "PASS",
            "detail": "; ".join(run_spec_gaps) or None,
        },
        {
            "name": "run_key_approval",
            "status": "PASS" if approval.get("approved") else "NOT_VERIFIED",
            "detail": "; ".join(approval.get("reasons") or []) or None,
        },
        {
            "name": "native_work_root_independent",
            "status": "PASS" if native_root.get("independent") else "NOT_VERIFIED",
            "detail": "; ".join(native_root.get("reasons") or []) or None,
        },
        {
            "name": "software_executable",
            "status": "PASS" if software.get("available") else "NOT_VERIFIED",
            "detail": "; ".join(software.get("reasons") or []) or None,
        },
        {
            "name": "h2_test_candidate",
            "status": "PASS" if candidate.get("published") else "NOT_VERIFIED",
            "detail": (
                None
                if candidate.get("published")
                else candidate.get("gaps", {}).get("h2_test_candidate")
            ),
        },
        {
            "name": "budget_preflight",
            "status": budget_status,
            "detail": "; ".join(budget.get("gaps") or budget.get("exhausted") or []) or None,
        },
        {
            "name": "shared_validator",
            "status": "PASS" if receipt["status"] == "AVAILABLE" else "NOT_VERIFIED",
            "detail": receipt["reason"],
        },
        {
            "name": "scenario_executed",
            "status": "NOT_VERIFIED",
            "detail": "no approved run has executed on this host",
        },
    ]


def _singlepoint_preflight(
    *,
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    receipt = _receipt_protocol()
    run_spec = plan["run_spec"] if isinstance(plan["run_spec"], dict) else {}
    approval = _approval_status(run_spec, spec, Path(args.out).name)
    native_root = _native_root_independence(run_spec=run_spec, repo_root=_repo_root())
    software = _software_status(run_spec)
    candidate = _candidate_status(_repo_root())
    gaps = list(plan["run_spec_gaps"])
    gaps.extend(approval.get("reasons") or [])
    gaps.extend(native_root.get("reasons") or [])
    gaps.extend(software.get("reasons") or [])
    if not candidate.get("published"):
        gaps.append(
            "H2 test candidate not published: "
            + str(candidate.get("gaps", {}).get("h2_test_candidate"))
        )
    extra: dict[str, Any] = {
        "scenario_implementation": "A-10b",
        "run_spec_path": plan["run_spec_path"],
        "run_spec_gaps": plan["run_spec_gaps"],
        "run_key_approval": approval,
        "native_root_check": native_root,
        "software_check": software,
        "candidate": candidate.get("candidate"),
        "candidate_status": candidate.get("status"),
        "candidate_gaps": candidate.get("gaps"),
        "h2_test_candidate": candidate.get("h2_test_candidate"),
        "h2_handoff": {
            "path": candidate.get("handoff_path"),
            "sha256": candidate.get("handoff_sha256"),
        },
        "native_output": None,
        "native_stdout": None,
        "native_stderr": None,
        "native_command_argv": None,
        "native_command_gap": (
            "the public TaskResult does not expose the backend command argv; the "
            "executed harness argv is recorded in argv.json"
        ),
        "native_evidence_gap": None,
        "task_request": None,
        "task_result": None,
        "normalized_energy": None,
    }
    assertions = _scenario_assertions(
        plan=plan,
        receipt=receipt,
        approval=approval,
        native_root=native_root,
        software=software,
        candidate=candidate,
    )
    return extra, gaps, assertions


def _native_output_from_result(result: Any) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for artifact in getattr(result, "artifacts", ()):
        path = Path(getattr(artifact, "path", ""))
        if not path.is_file():
            continue
        kind = str(getattr(artifact, "type", ""))
        priority = 0 if kind == "output" else 1 if kind == "log" else 2
        candidates.append((priority, path))
    if candidates:
        return sorted(candidates, key=lambda item: item[0])[0][1]
    return None


def _execution_timeout(args: argparse.Namespace, run_spec: Mapping[str, Any]) -> float:
    timeout = float(args.timeout)
    deadlines = (run_spec.get("budget") or {}).get("deadlines")
    if isinstance(deadlines, Mapping):
        for key in ("compute_s", "total_s"):
            value = deadlines.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                timeout = min(timeout, float(value))
    return timeout


def _run_native_singlepoint(
    *,
    args: argparse.Namespace,
    plan: dict[str, Any],
) -> dict[str, Any]:
    from cccp.calculation import run_singlepoint
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import TaskRequest

    fixture_path = Path(args.fixture)
    manifest = _load_json(fixture_path)
    references = manifest.get("fixtures") if isinstance(manifest, dict) else None
    if not isinstance(references, Mapping):
        raise _InvalidInputError("fixture manifest has no fixtures mapping")
    fixture_dir = fixture_path.parent
    request_path = fixture_dir / str(references.get("request", "request.json"))
    expected_path = fixture_dir / str(references.get("expected_science", "expected-science.json"))
    request_payload = _load_json(request_path)
    expected = _load_json(expected_path)
    tolerance = float(expected["tolerances"]["reparse_energy_hartree_abs"])

    run_spec = plan["run_spec"]
    run_id = Path(args.out).name
    native_root = Path(run_spec["native_work_root"])
    run_dir = native_root / f"acp2-{run_id}"
    if run_dir.exists() and any(run_dir.iterdir()):
        return {
            "status": "NOT_VERIFIED",
            "reason": f"native run directory is not empty: {run_dir}",
            "extra": {},
            "assertions": [],
            "extra_json": {},
            "extra_bytes": {},
            "artifacts": [],
        }
    run_dir.mkdir(parents=True, exist_ok=True)

    request = replace(TaskRequest.from_dict(request_payload), output_dir=run_dir)
    context = TaskContext(
        workdir=run_dir,
        input_base=fixture_dir,
        timeout_s=_execution_timeout(args, run_spec),
    )
    execution_started = _utc_now()
    result = run_singlepoint(request, context=context)
    execution_finished = _utc_now()

    output_path = _native_output_from_result(result)
    native_text: str | None = None
    native_bytes: dict[str, bytes] = {}
    native_info: dict[str, Any] | None = None
    if output_path is not None:
        native_bytes[output_path.name] = output_path.read_bytes()
        native_text = output_path.read_text(encoding="utf-8", errors="replace")
        native_info = {
            "path": str(output_path.resolve()),
            "sha256": _sha256(output_path),
            "size_bytes": output_path.stat().st_size,
            "stdout_is_native_output": True,
        }

    software = run_spec.get("software") or {}
    parsed = _parse_native_energy(native_text or "", software.get("name"))
    comparison = _compare_energy(result.energy_hartree, parsed, tolerance)
    assertions: list[dict[str, Any]] = [
        {
            "name": "task_completed",
            "status": (
                "PASS" if result.status == "completed" and result.error_kind is None else "FAIL"
            ),
            "detail": f"status={result.status}; error_kind={result.error_kind}",
        },
        {
            "name": "native_output_present",
            "status": "PASS" if native_info is not None else "FAIL",
            "detail": None if native_info is not None else "no native output artifact was exposed",
        },
        {
            "name": "native_normal_termination",
            "status": "PASS" if parsed.get("normal_termination") else "FAIL",
            "detail": (
                None if parsed.get("normal_termination") else "native termination marker not found"
            ),
        },
        {
            "name": "energy_present",
            "status": ("PASS" if result.energy_hartree is not None else "FAIL"),
            "detail": f"energy_hartree={result.energy_hartree!r}",
        },
        {
            "name": "energy_reparse_match",
            "status": "PASS" if comparison["match"] else "FAIL",
            "detail": comparison.get("reason"),
        },
    ]
    passed = all(item["status"] == "PASS" for item in assertions)
    result_state = "PASS" if passed else "FAIL"
    reason = (
        "singlepoint completed and the independent native parse matches within tolerance"
        if passed
        else "singlepoint scenario did not produce a verifiable native energy"
    )
    extra = {
        "native_output": native_info,
        "native_stdout": native_info,
        "native_stderr": {
            "present": False,
            "reason": (
                "the task core exposes one native output stream; no separate stderr "
                "artifact is published by the public TaskResult"
            ),
        },
        "native_command_argv": None,
        "native_command_gap": (
            "the public TaskResult does not expose the backend command argv; the "
            "executed harness argv is recorded in argv.json"
        ),
        "parsed_native_energy": parsed,
        "normalized_energy": comparison,
        "task_request": request.to_dict(),
        "task_result": result.to_dict(),
        "software_version": software.get("version"),
        "execution": {
            "started_at": execution_started,
            "finished_at": execution_finished,
            "native_work_dir": str(run_dir),
        },
    }
    return {
        "status": result_state,
        "reason": reason,
        "extra": extra,
        "assertions": assertions,
        "extra_json": {
            "task-request.json": request.to_dict(),
            "task-result.json": result.to_dict(),
        },
        "extra_bytes": {f"native/{name}": data for name, data in native_bytes.items()},
        "artifacts": [
            {
                "path": str(output_path.resolve()),
                "size_bytes": output_path.stat().st_size,
                "sha256": _sha256(output_path),
            }
        ]
        if output_path is not None
        else [],
    }


def _execute_installed_singlepoint(
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    *,
    started_at: str,
    argv: list[str],
) -> int:
    receipt = _receipt_protocol()
    extra, gaps, assertions = _singlepoint_preflight(args=args, spec=spec, plan=plan)
    if gaps:
        extra["scenario_executed"] = {
            "status": "NOT_VERIFIED",
            "reason": "; ".join(gaps),
        }
        extra["native_evidence_gap"] = (
            "no native output, task request/result or software command exists because "
            "the approved run prerequisites are missing: " + "; ".join(gaps)
        )
        return _finish_scenario(
            args=args,
            spec=spec,
            plan=plan,
            receipt=receipt,
            reason="run refused before submission: " + "; ".join(gaps),
            started_at=started_at,
            argv=argv,
            assertions=assertions,
            extra=extra,
        )

    try:
        execution = _run_native_singlepoint(args=args, plan=plan)
    except (OSError, RuntimeError, ValueError) as exc:
        extra["scenario_executed"] = {
            "status": "FAIL",
            "reason": f"{type(exc).__name__}: {exc}",
        }
        return _finish_scenario(
            args=args,
            spec=spec,
            plan=plan,
            receipt=receipt,
            reason=f"scenario execution raised {type(exc).__name__}: {exc}",
            started_at=started_at,
            argv=argv,
            result="FAIL",
            exit_code=EXIT_FAIL,
            assertions=assertions,
            extra=extra,
        )

    extra.update(execution["extra"])
    extra["scenario_executed"] = {
        "status": "PASS" if execution["status"] == "PASS" else "FAIL",
        "reason": execution["reason"],
    }
    assertions = [item for item in assertions if item["name"] != "scenario_executed"]
    assertions.extend(execution["assertions"])
    passed = execution["status"] == "PASS"
    return _finish_scenario(
        args=args,
        spec=spec,
        plan=plan,
        receipt=receipt,
        reason=execution["reason"],
        started_at=started_at,
        argv=argv,
        result="PASS" if passed else "FAIL",
        exit_code=EXIT_PASS if passed else EXIT_FAIL,
        assertions=assertions,
        extra=extra,
        extra_json=execution["extra_json"],
        extra_bytes=execution["extra_bytes"],
        extra_artifacts=execution["artifacts"],
    )


def _execute_validate_artifacts(
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    *,
    started_at: str,
    argv: list[str],
) -> int:
    """Validate a prepared optimize/frequency snapshot without running a job."""
    receipt = _receipt_protocol()
    fixture_path = Path(args.fixture)
    candidate = _candidate_status(_repo_root())
    p1_test_candidate = {
        "candidate_revision": None,
        "status": "NOT_VERIFIED",
        "gap": P1_TEST_CANDIDATE_GAP,
    }
    extra: dict[str, Any] = {
        "scenario_implementation": "A-11b",
        "fixture_path": str(fixture_path),
        "input_manifest_path": plan.get("input_manifest_path"),
        "platform_registry": None,
        "constructed_fixture": False,
        "real_run": False,
        "candidate": candidate.get("candidate"),
        "candidate_status": candidate.get("status"),
        "candidate_gaps": candidate.get("gaps"),
        "h2_test_candidate": candidate.get("h2_test_candidate"),
        "h2_handoff": {
            "path": candidate.get("handoff_path"),
            "sha256": candidate.get("handoff_sha256"),
        },
        "p1_test_candidate": p1_test_candidate,
        "snapshot": None,
        "task_results": None,
        "science_identity": None,
        "native_evidence": None,
        "native_evidence_gap": None,
        "stationary_point_report": None,
        "modes_cm1": None,
        "imaginary_mode_count": None,
    }
    assertions: list[dict[str, Any]] = []
    export_check: PlatformExportCheck | None = None

    def finish(
        *,
        result: str,
        exit_code: int,
        reason: str,
        extra_json: Mapping[str, Any] | None = None,
        extra_artifacts: list[dict[str, Any]] | None = None,
    ) -> int:
        assertions.append(
            {
                "name": "scenario_executed",
                "status": result if result != "NOT_VERIFIED" else "NOT_VERIFIED",
                "detail": reason,
            }
        )
        return _finish_scenario(
            args=args,
            spec=spec,
            plan=plan,
            receipt=receipt,
            reason=reason,
            started_at=started_at,
            argv=argv,
            result=result,
            exit_code=exit_code,
            assertions=assertions,
            platform_export=export_check,
            extra=extra,
            extra_json=extra_json,
            extra_artifacts=extra_artifacts,
        )

    snapshot_payload, constructed, unpack_errors = _unpack_snapshot(plan, fixture_path.parent)
    if snapshot_payload is None:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="artifact snapshot is unusable: " + "; ".join(unpack_errors),
        )
    extra["constructed_fixture"] = constructed

    export_check = check_platform_export(snapshot_payload)
    if not export_check.eligible:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="platform export rejected: " + "; ".join(export_check.reasons),
        )

    try:
        fixture_document = _load_json(fixture_path)
    except _InvalidInputError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(fixture_document, Mapping) or fixture_document.get("task_kind") != "optfreq":
        print(
            "usage error: validate-artifacts requires the optfreq fixture-manifest.json",
            file=sys.stderr,
        )
        return EXIT_USAGE

    registry = _platform_registry_view(
        plan.get("platform_registry_path"), plan.get("platform_registry")
    )
    extra["platform_registry"] = registry
    extra["constructed_fixture"] = bool(constructed or registry.get("constructed"))
    assertions.append(
        {
            "name": "platform_registry_registration",
            "status": "PASS" if registry.get("registered") else "NOT_VERIFIED",
            "detail": "; ".join(registry.get("reasons") or []) or None,
        }
    )
    if not registry.get("registered"):
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="registered run prerequisite missing: " + "; ".join(registry["reasons"]),
        )

    try:
        from cccp.analysis.research.artifact_manifest import (
            ArtifactSnapshotError,
            load_verified_snapshot,
        )
        from cccp.analysis.research.validators import validate_stationary_point
    except ImportError as exc:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason=f"CCCP science consumer unavailable: {type(exc).__name__}: {exc}",
        )

    try:
        snapshot, digests = load_verified_snapshot(snapshot_payload)
    except ArtifactSnapshotError as exc:
        extra["snapshot_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason=f"snapshot content rejected ({type(exc).__name__}): {exc}",
        )

    digest_table = [row.to_dict() for row in digests]
    extra["snapshot"] = {
        "snapshot_id": snapshot.snapshot_id,
        "generation": snapshot.generation,
        "read_root": str(snapshot.read_root),
        "snapshot_schema": snapshot.snapshot_schema,
        "snapshot_schema_version": snapshot.snapshot_schema_version,
        "constructed": constructed,
        "artifact_digests": digest_table,
        "scientific_identity": snapshot.science_identity.to_dict(),
    }
    extra["real_run"] = not extra["constructed_fixture"]
    assertions.append(
        {
            "name": "snapshot_content_digest",
            "status": "PASS",
            "detail": f"{len(digest_table)} artifacts verified against their declared digests",
        }
    )

    failures: list[str] = []
    gaps: list[str] = []

    optimized_artifact, optimized_gaps = _single_role_artifact(snapshot, "optimized_structure")
    if optimized_gaps:
        gaps.extend(optimized_gaps)
    else:
        identity_digest = snapshot.science_identity.structure_sha256
        if optimized_artifact.declared_sha256 != identity_digest:
            failures.append(
                "snapshot scientific_identity.structure_sha256 does not match the "
                "optimized_structure artifact digest"
            )
        assertions.append(
            {
                "name": "optimized_structure_tie",
                "status": "PASS" if not failures else "FAIL",
                "detail": None if not failures else failures[-1],
            }
        )

    task_results, task_gaps, task_contradictions = _load_task_results(snapshot)
    gaps.extend(task_gaps)
    failures.extend(task_contradictions)
    extra["task_results"] = {
        task: {
            "status": result.status,
            "complete": result.complete,
            "method": getattr(result.provenance, "method", None),
            "energy_hartree": result.energy_hartree,
            "symbols": list(result.symbols) if result.symbols else None,
        }
        for task, result in task_results.items()
    }
    assertions.append(
        {
            "name": "task_results_present",
            "status": "FAIL" if task_contradictions else ("NOT_VERIFIED" if task_gaps else "PASS"),
            "detail": "; ".join(task_contradictions or task_gaps) or None,
        }
    )

    optimize_result = task_results.get("optimize")
    frequency_result = task_results.get("frequency")
    if optimize_result is not None and frequency_result is not None:
        validation = validate_stationary_point(optimize_result, frequency_result)
        frequencies = tuple(float(value) for value in frequency_result.frequencies)
        report = _stationary_point_report(validation, frequencies)
        extra["stationary_point_report"] = report
        extra["modes_cm1"] = list(frequencies)
        extra["imaginary_mode_count"] = report.get("imaginary_mode_count")
        _print_stationary_point_report(report)
        if report.get("status") == "refuted":
            failures.append("stationary-point validation refuted the minimum claim")
        elif report.get("status") in ("inconclusive", "not_evaluated"):
            gaps.append(
                f"stationary-point validation is {report.get('status')}: "
                "the snapshot does not carry complete Hessian evidence"
            )
        assertions.append(
            {
                "name": "stationary_point",
                "status": {
                    "supported": "PASS",
                    "refuted": "FAIL",
                    "inconclusive": "NOT_VERIFIED",
                    "not_evaluated": "NOT_VERIFIED",
                }[str(report.get("status"))],
                "detail": (
                    f"convergence={report.get('convergence')}; "
                    f"imaginary_mode_count={report.get('imaginary_mode_count')}; "
                    f"modes={list(frequencies)}"
                ),
            }
        )

        identity_check = _science_identity_consistency(snapshot, optimize_result, frequency_result)
        extra["science_identity"] = identity_check
        failures.extend(identity_check["contradictions"])
        gaps.extend(identity_check["gaps"])
        assertions.append(
            {
                "name": "science_identity_consistency",
                "status": identity_check["status"],
                "detail": "; ".join(identity_check["contradictions"] or identity_check["gaps"])
                or None,
            }
        )

        optimize_native, native_gaps = _single_role_artifact(snapshot, "native_output")
        frequency_native, frequency_gaps = _single_role_artifact(snapshot, "frequency_output")
        native_gaps = native_gaps + frequency_gaps
        if native_gaps:
            extra["native_evidence"] = {"status": "NOT_VERIFIED", "gaps": native_gaps}
            gaps.extend(native_gaps)
            extra["native_evidence_gap"] = "; ".join(native_gaps)
            assertions.append(
                {
                    "name": "native_output_evidence",
                    "status": "NOT_VERIFIED",
                    "detail": "; ".join(native_gaps),
                }
            )
        else:
            native = _native_evidence(
                optimize_native.resolved_path,
                frequency_native.resolved_path,
                optimize_result,
                frequency_result,
            )
            extra["native_evidence"] = native
            failures.extend(native["contradictions"])
            gaps.extend(native["gaps"])
            if native["gaps"]:
                extra["native_evidence_gap"] = "; ".join(native["gaps"])
            assertions.append(
                {
                    "name": "native_output_evidence",
                    "status": native["status"],
                    "detail": "; ".join(native["contradictions"] or native["gaps"]) or None,
                }
            )
    else:
        assertions.append(
            {
                "name": "stationary_point",
                "status": "NOT_VERIFIED",
                "detail": "one optimize and one frequency TaskResult are required",
            }
        )

    extra_artifacts = [
        entry
        for entry in (
            _artifact_entry(artifact.resolved_path)
            for artifact in snapshot.artifacts
            if artifact.role
            in ("optimized_structure", "native_output", "frequency_output", "provenance")
        )
        if entry is not None
    ]
    report = extra.get("stationary_point_report")
    extra_json = {
        "artifact-digests.json": digest_table,
        **({"stationary-point-report.json": report} if isinstance(report, Mapping) else {}),
    }

    if failures:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="scientific/structural contradiction: " + "; ".join(failures),
            extra_json=extra_json,
            extra_artifacts=extra_artifacts,
        )
    if gaps:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="scientific evidence incomplete: " + "; ".join(gaps),
            extra_json=extra_json,
            extra_artifacts=extra_artifacts,
        )
    return finish(
        result="PASS",
        exit_code=EXIT_PASS,
        reason=(
            "stationary-point validation supported the minimum"
            + (
                " (constructed fixture path proof, not a real run)"
                if extra["constructed_fixture"]
                else ""
            )
        ),
        extra_json=extra_json,
        extra_artifacts=extra_artifacts,
    )


def _execute(
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    *,
    started_at: str,
    argv: list[str],
) -> int:
    invalid = [error for error in plan["errors"] if error["kind"] == "invalid"]
    if invalid:
        print(
            "usage error: " + "; ".join(error["message"] for error in invalid),
            file=sys.stderr,
        )
        return EXIT_USAGE

    missing = [error for error in plan["errors"] if error["kind"] == "missing"]
    if missing:
        return _finish_scenario(
            args=args,
            spec=spec,
            plan=plan,
            receipt=_receipt_protocol(),
            reason="missing prerequisite: " + "; ".join(error["message"] for error in missing),
            started_at=started_at,
            argv=argv,
        )

    budget = plan["budget"]
    if budget is not None and budget["status"] == "invalid":
        if budget.get("missing"):
            missing_fields = list(plan["run_spec_gaps"]) + list(budget["missing"])
            return _finish_scenario(
                args=args,
                spec=spec,
                plan=plan,
                receipt=_receipt_protocol(),
                reason="run spec/budget fields missing: " + "; ".join(missing_fields),
                started_at=started_at,
                argv=argv,
            )
        print(
            "usage error: " + "; ".join(budget["gaps"]),
            file=sys.stderr,
        )
        return EXIT_USAGE
    if budget is not None and budget["status"] == "exhausted":
        return _finish_scenario(
            args=args,
            spec=spec,
            plan=plan,
            receipt=_receipt_protocol(),
            reason="budget refused before submission: " + "; ".join(budget["exhausted"]),
            started_at=started_at,
            argv=argv,
        )

    if spec.alias == SINGLEPOINT_ALIAS:
        return _execute_installed_singlepoint(args, spec, plan, started_at=started_at, argv=argv)
    if spec.alias == ARTIFACT_ALIAS:
        return _execute_validate_artifacts(args, spec, plan, started_at=started_at, argv=argv)

    platform_export: PlatformExportCheck | None = None
    if spec.requires_input_manifest:
        platform_export = check_platform_export(plan.get("input_manifest"))
        if not platform_export.eligible:
            reason = "platform export rejected: " + "; ".join(platform_export.reasons)
            return _finish_scenario(
                args=args,
                spec=spec,
                plan=plan,
                receipt=_receipt_protocol(),
                reason=reason,
                started_at=started_at,
                argv=argv,
                result="FAIL",
                exit_code=EXIT_FAIL,
                platform_export=platform_export,
            )

    receipt = _receipt_protocol()
    return _finish_scenario(
        args=args,
        spec=spec,
        plan=plan,
        receipt=receipt,
        reason=(
            f"scenario '{spec.canonical}' is implemented by its dedicated A todo; "
            "this harness path executes nothing and writes no PASS"
        ),
        started_at=started_at,
        argv=argv,
    )


def _dry_run(
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    receipt: dict[str, Any],
) -> int:
    platform_export = None
    registry_view: dict[str, Any] | None = None
    if spec.requires_input_manifest:
        snapshot_payload, _constructed, _errors = _unpack_snapshot(plan, Path(args.fixture).parent)
        platform_export = check_platform_export(snapshot_payload)
        registry_view = _platform_registry_view(
            plan.get("platform_registry_path"), plan.get("platform_registry")
        )
    document = {
        "dry_run": True,
        "scenario": spec.canonical,
        "scenario_alias": spec.alias,
        "phase": spec.phase,
        "required_flags": _required_flags(spec),
        "paths": plan["paths"],
        "run_spec": {
            "path": plan["run_spec_path"],
            "errors": [error for error in plan["errors"] if error["label"] == "run_spec"]
            + [
                {"kind": "missing", "label": "run_spec", "message": gap}
                for gap in plan["run_spec_gaps"]
            ],
            "gaps": plan["run_spec_gaps"],
        },
        "budget": plan["budget"],
        "platform_export": platform_export.to_dict() if platform_export is not None else None,
        "platform_registry": registry_view,
        "receipt_protocol": {
            "status": receipt["status"],
            "missing_artifact": receipt["missing_artifact"],
            "missing_contract": receipt["missing_contract"],
            "shared_validator_present": receipt["shared_validator_present"],
            "valid_sample_present": receipt["valid_sample_present"],
            "invalid_sample_present": receipt["invalid_sample_present"],
        },
        "would_create_work_root": False,
        "would_create_job": False,
        "would_start_controller": False,
        "would_start_scheduler": False,
        "would_start_qc_subprocess": False,
        "would_write_result": False,
        "result": None,
    }
    print(json.dumps(document, indent=2, sort_keys=True))

    invalid = [error for error in plan["errors"] if error["kind"] == "invalid"]
    if invalid:
        print(
            "usage error: " + "; ".join(error["message"] for error in invalid),
            file=sys.stderr,
        )
        return EXIT_USAGE
    if plan["run_spec_gaps"]:
        print(
            "usage error: " + "; ".join(plan["run_spec_gaps"]),
            file=sys.stderr,
        )
        return EXIT_USAGE
    missing = [error for error in plan["errors"] if error["kind"] == "missing"]
    if missing:
        print(
            "NOT_VERIFIED: " + "; ".join(error["message"] for error in missing),
            file=sys.stderr,
        )
        return EXIT_NOT_VERIFIED
    budget = plan["budget"]
    if budget is not None and budget["status"] == "invalid":
        print("usage error: " + "; ".join(budget["gaps"]), file=sys.stderr)
        return EXIT_USAGE
    if budget is not None and budget["status"] == "exhausted":
        print(
            "NOT_VERIFIED: budget refused: " + "; ".join(budget["exhausted"]),
            file=sys.stderr,
        )
        return EXIT_NOT_VERIFIED
    if platform_export is not None and not platform_export.eligible:
        print(
            "FAIL: platform export rejected: " + "; ".join(platform_export.reasons),
            file=sys.stderr,
        )
        return EXIT_FAIL
    return EXIT_PASS


def _required_flags(spec: ScenarioSpec) -> list[str]:
    flags = ["--scenario", "--fixture", "--out"]
    if spec.requires_run_spec:
        flags.append("--run-spec")
    if spec.requires_input_manifest:
        flags.append("--input-manifest")
    return flags


def _required_flag_gap(spec: ScenarioSpec, args: argparse.Namespace) -> str | None:
    if spec.requires_run_spec and not args.run_spec:
        return f"scenario {spec.canonical} requires --run-spec"
    if spec.requires_input_manifest and args.input_manifest is None:
        return f"scenario {spec.canonical} requires --input-manifest"
    return None


def _help_epilog() -> str:
    lines = ["Scenarios (canonical id | short CLI alias):"]
    for spec in SCENARIOS:
        lines.append(f"  {spec.canonical} | {spec.alias}")
        lines.append(f"    phase: {spec.phase}")
        lines.append(f"    required: {' '.join(_required_flags(spec))}")
        lines.append(f"    purpose: {spec.description}")
    lines.extend(
        [
            "",
            "Global required flags:",
            "  --scenario  canonical science:<name> or short alias",
            "  --fixture   scientific fixture-manifest.json",
            "  --out       evidence directory for result.json and argv.json",
            "Scenario-specific required flags:",
            "  installed-singlepoint  --run-spec",
            "  validate-artifacts     --input-manifest",
            "  validate-da-ts         --input-manifest",
            "  validate-da-irc        --input-manifest",
            "  validate-da-report     --input-manifest",
            "Optional flags:",
            "  --timeout   positive seconds (default 600)",
            "  --platform-registry  platform-provided registry record; a real",
            "                       validate-artifacts PASS needs the registered",
            f"                       id {REGISTERED_OPT_FREQ_RUN_ID!r} there",
            "  --dry-run   check paths and budgets only; never creates a work root or job",
            "",
            "Exit codes: 0=PASS, 1=FAIL, 2=NOT_VERIFIED, 3=usage.",
            "--help and --dry-run may exit 0 but never produce a scenario PASS.",
            "B01 acceptance-result.schema.json / acp2.acceptance.v1 is unpublished;",
            "scenario receipts are provisional and record receipt_protocol_gap=NOT_VERIFIED.",
            "",
            "Examples:",
            "  acp2_science.py --help",
            "  acp2_science.py --scenario installed-singlepoint --fixture fixture-manifest.json"
            " --run-spec run-spec.json --out evidence/ --dry-run",
            "  acp2_science.py --scenario science:validate-artifacts --fixture"
            " fixture-manifest.json --input-manifest authorized-artifacts.json"
            " --platform-registry registry.json --out evidence/",
        ]
    )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = _UsageParser(
        prog="acp2_science.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_help_epilog(),
    )
    parser.add_argument(
        "--scenario",
        metavar="SCENARIO",
        required=True,
        help="scenario canonical id or short alias; see the scenario list below",
    )
    parser.add_argument(
        "--fixture",
        metavar="FIXTURE_MANIFEST",
        required=True,
        help="scientific fixture-manifest.json",
    )
    parser.add_argument(
        "--out",
        metavar="EVIDENCE_DIR",
        required=True,
        help="evidence directory for result.json and argv.json",
    )
    parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=_positive_float,
        default=600.0,
        help="scenario wall-clock timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--run-spec",
        metavar="RUN_SPEC",
        default=None,
        help="budget/software work-root run spec; required for installed-singlepoint",
    )
    parser.add_argument(
        "--input-manifest",
        metavar="AUTHORIZED_ARTIFACTS",
        default=None,
        help="platform-prepared authorized artifact manifest; required for validate-*",
    )
    parser.add_argument(
        "--platform-registry",
        metavar="REGISTRY_RECORD",
        default=None,
        help=(
            "platform-provided registry record used to confirm the registered "
            f"id {REGISTERED_OPT_FREQ_RUN_ID!r}; absent => NOT_VERIFIED"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check paths and budgets only; never creates a work root or job",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the harness and return the project's 0/1/2/3 exit code."""
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(effective_argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_USAGE

    spec = _normalise_scenario(args.scenario)
    if spec is None:
        parser.print_usage(sys.stderr)
        print(
            f"{parser.prog}: error: unknown scenario: {args.scenario}; "
            "use --help for the complete scenario list",
            file=sys.stderr,
        )
        return EXIT_USAGE

    required_gap = _required_flag_gap(spec, args)
    if required_gap is not None:
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: error: {required_gap}", file=sys.stderr)
        return EXIT_USAGE

    started_at = _utc_now()
    plan = _build_plan(args, spec)
    if args.dry_run:
        return _dry_run(args, spec, plan, _receipt_protocol())
    return _execute(args, spec, plan, started_at=started_at, argv=effective_argv)


if __name__ == "__main__":
    raise SystemExit(main())
