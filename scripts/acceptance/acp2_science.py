#!/usr/bin/env python3
"""ACP2 science acceptance harness (A-10a skeleton, A-10b singlepoint scenario).

The harness exposes the scenario/flag contract for the science track, a
path/budget ``--dry-run`` and the project's ``0=PASS, 1=FAIL, 2=NOT_VERIFIED,
3=usage`` exit-code contract.  ``--help`` and ``--dry-run`` never create a work
root, start a job or produce a scenario ``PASS``.

Three scenarios are implemented.  ``science:installed-singlepoint`` calls the
public ``cccp.calculation.run_singlepoint`` task core (the single execution
station) inside an independent native work root and re-parses the native output
locally.  ``science:validate-artifacts`` consumes an already-prepared,
platform-authorized optimize/frequency artifact snapshot: it confirms the
registered ``dsh-optfreq-live`` id against an explicitly supplied platform
registry record, verifies the snapshot content through
``cccp.analysis.research.artifact_manifest`` and runs the frozen
stationary-point validators.  ``science:validate-da-ts`` audits a B20a-exec
Diels-Alder candidate snapshot: it confirms the registered ``da-runtime`` and
``da-research-live`` ids, validates the frozen reactant/product geometry and
small budget, keeps every candidate selection/rejection reason and the bounded
search range, and runs the TS/frequency validators.  ``B20a-ready`` is the
execution interface, never execution evidence.  No scenario launches a second
QC job.

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
from collections import Counter
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
DA_TS_ALIAS = "validate-da-ts"
H2_HANDOFF_RELPATH = "docs/reference/next-change/acp2-contracts/science/handoff-H2.json"
PRODUCTION_RUN_ROOTS = ("/var/lib/acp/runs",)

#: The platform registry is owned by the platform and is not published to A.
#: A supplies only the lookup; it never invents the id or a registry row.
REGISTERED_OPT_FREQ_RUN_ID = "dsh-optfreq-live"
DA_RUNTIME_RUN_ID = "da-runtime"
DA_RESEARCH_LIVE_RUN_ID = "da-research-live"
DA_REQUIRED_REGISTRY_IDS = (DA_RUNTIME_RUN_ID, DA_RESEARCH_LIVE_RUN_ID)
DA_EXECUTION_ARTIFACT_ID = "B20a-exec:da-execution-artifacts"
DA_EXECUTION_SUB_DELIVERABLE = "22b"
DA_EXECUTION_SEMANTIC_CANDIDATE = "B.B20a.exec"
DA_READY_ARTIFACT_ID = "B20a-ready:da-execution-interface-preflight"
DA_CANDIDATE_SCHEMA = "acp2.science.da-candidate-selection"
DA_STARTING_STRUCTURE_SOURCES = frozenset(
    {"user_input", "frozen_known_candidate", "generated_candidate"}
)
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

#: B07's frozen LSF/DA deadline components.  A derives the harness timeout from
#: these values and never substitutes an independent fixed timeout.
FROZEN_DEADLINE_KEYS = (
    "max_queue_wait_seconds",
    "max_run_seconds",
    "max_prepare_seconds",
    "cleanup_seconds",
)
CROSSCHECK_RECORD_SCHEMA = "acp2.science.platform-run-records"
CROSSCHECK_REPORT_SCHEMA = "acp2.science.platform-record-crosscheck"
CROSSCHECK_RUN_KINDS = ("completed_science_run", "control_cancel_run")
CROSSCHECK_COMPLETED = "completed_science_run"
CROSSCHECK_CANCELLED = "control_cancel_run"
CROSSCHECK_SCIENCE_TOLERANCE = {
    "energy_hartree_abs": NATIVE_ENERGY_TOLERANCE_HARTREE,
    "frequency_cm1_abs": NATIVE_FREQUENCY_TOLERANCE_CM1,
}

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
_SOFTWARE_VERSION_PATTERNS = (
    re.compile(r"\bORCA\s+([0-9]+(?:\.[0-9A-Za-z]+)+)"),
    re.compile(r"\bxtb(?:\s+version)?\s+([0-9]+(?:\.[0-9A-Za-z]+)+)", re.IGNORECASE),
)
_VIBRATION_LINE = re.compile(r"^\s*\d+:\s+([-+]?\d+(?:\.\d*)?)\s+cm\*\*-1", re.MULTILINE)
_NORMAL_MODE_ROW = re.compile(r"^\s*\d+\s+[-+]?\d+\.\d+", re.MULTILINE)


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


def _platform_registry_view(
    path: str | None,
    document: Any,
    *,
    required_ids: tuple[str, ...] = (REGISTERED_OPT_FREQ_RUN_ID,),
) -> dict[str, Any]:
    """Confirm the registered run id(s) against an explicitly supplied registry."""
    required = tuple(dict.fromkeys(required_ids))
    if not path:
        gap = PLATFORM_REGISTRY_GAP
        if required != (REGISTERED_OPT_FREQ_RUN_ID,):
            gap = (
                "platform registry not supplied: pass --platform-registry <path> for the "
                "platform-provided registry record so the registered ids "
                f"{', '.join(repr(item) for item in required)} can be confirmed; the platform "
                "registry is not published to track A and the harness never invents the ids or "
                "a row"
            )
        return {
            "status": "MISSING",
            "registered": False,
            "constructed": None,
            "path": None,
            "ids": [],
            "required_ids": list(required),
            "registered_ids": [],
            "reasons": [gap],
        }
    if not isinstance(document, (Mapping, list)):
        return {
            "status": "INVALID",
            "registered": False,
            "constructed": None,
            "path": path,
            "ids": [],
            "required_ids": list(required),
            "registered_ids": [],
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
            "required_ids": list(required),
            "registered_ids": [],
            "reasons": ["platform registry has no entries/runs/records list and no single run id"],
        }
    ids = _registry_entry_ids(entries)
    constructed = bool(
        (isinstance(document, Mapping) and document.get("constructed") is True)
        or any(isinstance(entry, Mapping) and entry.get("constructed") is True for entry in entries)
    )
    registered_ids = [item for item in required if item in ids]
    missing_ids = [item for item in required if item not in ids]
    registered = not missing_ids
    reasons: list[str] = []
    for missing in missing_ids:
        reasons.append(
            f"registered id {missing!r} is absent from the supplied "
            f"platform registry ({path}); ids seen: {sorted(ids)}"
        )
    return {
        "status": "REGISTERED" if registered else "UNREGISTERED",
        "registered": registered,
        "constructed": constructed,
        "path": path,
        "ids": ids,
        "required_ids": list(required),
        "registered_ids": registered_ids,
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


def derive_timeout_from_frozen(
    frozen_deadlines: Any,
    *,
    client_request_seconds: float | None = None,
) -> dict[str, Any]:
    """Derive the harness timeout from B07's frozen deadline components.

    Queue wait, scientific runtime, prepare and cleanup are summed into the
    overall acceptance deadline.  The client request deadline is reported
    separately and is never folded into the QC timeout.  Missing values stay
    ``NOT_VERIFIED`` and malformed values fail; no default is invented.
    """
    report: dict[str, Any] = {
        "status": "NOT_VERIFIED",
        "source": "B07 frozen deadlines",
        "keys": list(FROZEN_DEADLINE_KEYS),
        "components": {},
        "timeout_seconds": None,
        "deadlines": {},
        "gaps": [],
        "malformed": [],
    }
    if not isinstance(frozen_deadlines, Mapping):
        report["gaps"].append(
            "B07 frozen deadlines not supplied; required keys: " + ", ".join(FROZEN_DEADLINE_KEYS)
        )
        for key in FROZEN_DEADLINE_KEYS:
            report["gaps"].append(f"frozen deadline {key} is missing")
        return report
    malformed = False
    for key in FROZEN_DEADLINE_KEYS:
        value = frozen_deadlines.get(key)
        if value is None:
            report["gaps"].append(f"frozen deadline {key} is missing")
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            report["malformed"].append(
                f"frozen deadline {key} must be a non-negative finite number"
            )
            malformed = True
            continue
        report["components"][key] = float(value)
    if malformed:
        report["status"] = "FAIL"
        return report
    if report["gaps"]:
        return report
    total = math.fsum(report["components"][key] for key in FROZEN_DEADLINE_KEYS)
    report["status"] = "OK"
    report["timeout_seconds"] = total
    report["deadlines"] = {
        "queue_wait_seconds": report["components"]["max_queue_wait_seconds"],
        "scientific_runtime_seconds": report["components"]["max_run_seconds"],
        "prepare_seconds": report["components"]["max_prepare_seconds"],
        "cleanup_seconds": report["components"]["cleanup_seconds"],
        "client_request_seconds": client_request_seconds,
        "overall_acceptance_seconds": total,
    }
    return report


def _crosscheck_document(records: Any) -> tuple[list[Any], dict[str, Any]]:
    if isinstance(records, Mapping):
        document = dict(records)
        for key in ("records", "rows", "runs"):
            value = document.get(key)
            if isinstance(value, list):
                return value, document
        if isinstance(document.get("task_ref"), str):
            return [document], document
        return [], document
    if isinstance(records, list):
        return records, {}
    return [], {}


def _crosscheck_integration_status() -> dict[str, Any]:
    source = os.environ.get("ACP2_INTEGRATION_SOURCE")
    interpreter = os.environ.get("ACP2_INTEGRATION_PYTHON")
    a_source = os.environ.get("ACP2_A_SOURCE")
    a_python = os.environ.get("ACP2_A_PYTHON")
    gaps: list[str] = []
    if not source:
        gaps.append("$ACP2_INTEGRATION_SOURCE is not set")
    if not interpreter:
        gaps.append("$ACP2_INTEGRATION_PYTHON is not set")
    if source and a_source and Path(source).resolve() == Path(a_source).resolve():
        gaps.append("$ACP2_INTEGRATION_SOURCE must be distinct from $ACP2_A_SOURCE")
    if interpreter and a_python and Path(interpreter).resolve() == Path(a_python).resolve():
        gaps.append("$ACP2_INTEGRATION_PYTHON must be distinct from $ACP2_A_PYTHON")
    return {
        "status": "OK" if not gaps else "NOT_VERIFIED",
        "available": not gaps,
        "source": source,
        "interpreter": interpreter,
        "a_source": a_source,
        "a_python": a_python,
        "gaps": gaps,
    }


def _crosscheck_environment(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("environment")
    environment = dict(raw) if isinstance(raw, Mapping) else {}
    software = environment.get("software")
    software_mapping = dict(software) if isinstance(software, Mapping) else {}
    return {
        "source": environment.get("source"),
        "interpreter": environment.get("interpreter"),
        "software_name": software_mapping.get("name"),
        "software_version": software_mapping.get("version"),
        "method": environment.get("method"),
        "basis": environment.get("basis"),
        "input_sha256": environment.get("input_sha256"),
    }


def _crosscheck_path(raw: Any, root: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _crosscheck_parse_native(text: str) -> dict[str, Any]:
    energy = _parse_native_energy(text, None)
    software_version = None
    for pattern in _SOFTWARE_VERSION_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            software_version = match.group(1)
            break
    frequencies: list[float] = []
    for raw in _VIBRATION_LINE.findall(text):
        try:
            value = float(raw)
        except ValueError:
            continue
        if value != 0.0:
            frequencies.append(value)
    return {
        "energy_found": bool(energy.get("found")),
        "energy_hartree": energy.get("energy_hartree"),
        "normal_termination": bool(energy.get("normal_termination")),
        "software": energy.get("software"),
        "software_version": software_version,
        "frequencies_cm1": frequencies,
        "normal_modes_section": "NORMAL MODES" in text,
        "normal_mode_rows": len(_NORMAL_MODE_ROW.findall(text)),
    }


def _crosscheck_artifact_rows(
    record: Mapping[str, Any],
    root: Path,
    attempt: int,
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Path], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    contradictions: list[str] = []
    gaps: list[str] = []
    paths_by_role: dict[str, Path] = {}
    texts_by_role: dict[str, str] = {}
    raw_artifacts = record.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        gaps.append("record declares no artifacts")
        return rows, contradictions, gaps, paths_by_role, texts_by_role
    for raw in raw_artifacts:
        if not isinstance(raw, Mapping):
            contradictions.append("artifact entry is not a JSON object")
            continue
        role = raw.get("role")
        declared = raw.get("sha256")
        artifact_attempt = raw.get("attempt")
        path = _crosscheck_path(raw.get("path"), root)
        status = "PASS"
        actual_sha256: str | None = None
        size_bytes: int | None = None
        if not isinstance(role, str) or not role:
            contradictions.append("artifact role is missing")
            status = "FAIL"
        if not isinstance(declared, str) or not _is_digest(declared):
            contradictions.append(f"artifact {role!r} sha256 is not sha256:<64 hex>")
            status = "FAIL"
        if path is None:
            gaps.append(f"artifact {role!r} path is missing")
            status = "NOT_VERIFIED"
        elif not path.is_file():
            gaps.append(f"artifact {role!r} is not a readable file: {path}")
            status = "NOT_VERIFIED"
        else:
            actual_sha256 = _sha256(path)
            size_bytes = path.stat().st_size
            if isinstance(declared, str) and _is_digest(declared):
                expected = declared.removeprefix("sha256:")
                if actual_sha256 != expected:
                    contradictions.append(
                        f"artifact {role!r} SHA-256 mismatch: declared {expected}, "
                        f"actual {actual_sha256} ({path})"
                    )
                    status = "FAIL"
            if isinstance(role, str) and role in ("native_output", "frequency_output"):
                try:
                    texts_by_role[role] = path.read_text(encoding="utf-8", errors="replace")
                    paths_by_role[role] = path
                except OSError as exc:
                    gaps.append(f"artifact {role!r} cannot be read as text: {exc}")
                    status = "NOT_VERIFIED"
        if artifact_attempt is not None:
            try:
                artifact_attempt_int = int(artifact_attempt)
            except (TypeError, ValueError):
                contradictions.append(f"artifact {role!r} attempt is not an integer")
                status = "FAIL"
            else:
                if artifact_attempt_int < attempt:
                    contradictions.append(
                        f"cached artifact {role!r} from an older attempt "
                        f"(artifact attempt {artifact_attempt_int}, record attempt {attempt})"
                    )
                    status = "FAIL"
                elif artifact_attempt_int != attempt:
                    contradictions.append(
                        f"artifact {role!r} attempt {artifact_attempt_int} differs from "
                        f"record attempt {attempt}"
                    )
                    status = "FAIL"
        rows.append(
            {
                "record_id": record.get("record_id"),
                "role": role,
                "path": str(path) if path is not None else raw.get("path"),
                "declared_sha256": declared,
                "actual_sha256": actual_sha256,
                "size_bytes": size_bytes,
                "attempt": artifact_attempt,
                "status": status,
            }
        )
    return rows, contradictions, gaps, paths_by_role, texts_by_role


def _crosscheck_task_results(
    raw_results: Any,
    environment: Mapping[str, Any],
    native: Mapping[str, Any],
    frequency_native: Mapping[str, Any] | None,
    tolerance: Mapping[str, float],
) -> dict[str, Any]:
    from cccp.calculation.results import TaskResult

    checks: list[dict[str, Any]] = []
    contradictions: list[str] = []
    gaps: list[str] = []
    summary: dict[str, Any] = {}
    results: dict[str, Any] = {}
    if not isinstance(raw_results, list) or not raw_results:
        gaps.append("record carries no TaskResult document: full scientific body missing")
        return {
            "checks": checks,
            "contradictions": contradictions,
            "gaps": gaps,
            "results": summary,
            "energy_hartree": None,
        }
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            contradictions.append("TaskResult document is not a JSON object")
            continue
        try:
            result = TaskResult.from_dict(dict(raw))
        except (TypeError, ValueError) as exc:
            contradictions.append(f"invalid TaskResult document: {type(exc).__name__}: {exc}")
            continue
        task = result.task.value
        if task in results:
            contradictions.append(f"duplicate {task} TaskResult in record")
            continue
        results[task] = result
    if not results:
        gaps.append("record carries no parseable TaskResult document")
    energy_tolerance = float(tolerance["energy_hartree_abs"])
    frequency_tolerance = float(tolerance["frequency_cm1_abs"])
    native_energy = native.get("energy_hartree")
    for task, result in results.items():
        summary[task] = {
            "status": result.status,
            "complete": result.complete,
            "method": getattr(result.provenance, "method", None),
            "software_version": getattr(result.provenance, "version", None),
            "energy_hartree": result.energy_hartree,
            "frequencies_cm1": [float(value) for value in result.frequencies],
        }
        if result.status != "completed":
            contradictions.append(f"{task} TaskResult status is {result.status!r}, not completed")
        if not result.complete:
            contradictions.append(f"{task} TaskResult declares an incomplete scientific body")
        if not result.symbols or not result.coordinates:
            gaps.append(f"{task} TaskResult carries no complete coordinates/symbols")
        provenance = result.provenance
        if provenance is None:
            gaps.append(f"{task} TaskResult declares no provenance")
        else:
            if provenance.method != environment.get("method"):
                contradictions.append(
                    f"{task} TaskResult method {provenance.method!r} differs from record "
                    f"method {environment.get('method')!r}"
                )
            if not provenance.version or provenance.version == "unknown":
                gaps.append(f"{task} TaskResult provenance.version is unknown")
            elif provenance.version != environment.get("software_version"):
                contradictions.append(
                    f"{task} TaskResult software version {provenance.version!r} differs from "
                    f"record software version {environment.get('software_version')!r}"
                )
        if result.energy_hartree is None or not math.isfinite(float(result.energy_hartree)):
            gaps.append(f"{task} TaskResult exposes no finite energy")
        elif native_energy is None:
            gaps.append(f"native output exposes no final energy to compare with {task}")
        else:
            delta = abs(float(result.energy_hartree) - float(native_energy))
            if delta > energy_tolerance:
                contradictions.append(
                    f"{task} TaskResult energy differs from the native output by "
                    f"{delta:.3e} Ha (tolerance {energy_tolerance:.3e})"
                )
        if task == "optimize":
            optimization_status = getattr(result.payload, "optimization_status", None)
            if optimization_status is not None and optimization_status != "converged":
                contradictions.append(
                    f"optimize TaskResult reports optimization_status {optimization_status!r}"
                )
        if task == "frequency":
            if not result.frequencies:
                gaps.append("frequency TaskResult carries no vibrational frequencies")
            analysis = getattr(result.payload, "analysis", None)
            if analysis is None:
                gaps.append("frequency payload carries no analysis (auxiliary products missing)")
            else:
                mode_vectors = getattr(analysis, "mode_vectors", None)
                mode_frequencies = getattr(analysis, "mode_frequencies", None)
                ir_intensities = getattr(analysis, "ir_intensities", None)
                if not mode_vectors:
                    gaps.append("frequency payload carries no normal-mode vectors")
                if not mode_frequencies:
                    gaps.append("frequency payload carries no mode-frequency table")
                if ir_intensities is None or len(ir_intensities) != len(result.frequencies):
                    gaps.append("frequency payload IR intensities do not cover every reported mode")
            if frequency_native is None:
                gaps.append("record carries no frequency_output native product")
            else:
                native_frequencies = [
                    float(value) for value in frequency_native.get("frequencies_cm1", [])
                ]
                if not native_frequencies:
                    gaps.append("frequency native output exposes no vibrational frequencies")
                elif len(native_frequencies) != len(result.frequencies):
                    contradictions.append(
                        "frequency native mode count differs from the TaskResult "
                        f"({len(native_frequencies)} vs {len(result.frequencies)})"
                    )
                else:
                    deltas = [
                        abs(left - right)
                        for left, right in zip(native_frequencies, result.frequencies, strict=True)
                    ]
                    if any(delta > frequency_tolerance for delta in deltas):
                        contradictions.append(
                            "frequency native modes differ from the TaskResult beyond "
                            f"{frequency_tolerance:.3e} cm-1"
                        )
                if not frequency_native.get("normal_modes_section"):
                    gaps.append("frequency native output carries no NORMAL MODES section")
                elif int(frequency_native.get("normal_mode_rows") or 0) <= 0:
                    gaps.append("frequency native output carries no normal-mode rows")
    required = ["optimize", "frequency"] if frequency_native is not None else ["optimize"]
    for task in required:
        if task not in results:
            gaps.append(f"record carries no {task} TaskResult")
    checks.append(
        {
            "name": "task_results",
            "status": "FAIL" if contradictions else ("NOT_VERIFIED" if gaps else "PASS"),
            "detail": "; ".join(contradictions or gaps) or None,
        }
    )
    energy = next(
        (
            float(result.energy_hartree)
            for result in results.values()
            if result.energy_hartree is not None
        ),
        None,
    )
    return {
        "checks": checks,
        "contradictions": contradictions,
        "gaps": gaps,
        "results": summary,
        "energy_hartree": energy,
    }


def _crosscheck_completed_science(
    record: Mapping[str, Any],
    *,
    root: Path,
    attempt: int,
    environment: Mapping[str, Any],
    tolerance: Mapping[str, float],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    contradictions: list[str] = []
    gaps: list[str] = []
    artifact_rows, artifact_contradictions, artifact_gaps, paths, texts = _crosscheck_artifact_rows(
        record, root, attempt
    )
    contradictions.extend(artifact_contradictions)
    gaps.extend(artifact_gaps)
    native_text = texts.get("native_output")
    frequency_text = texts.get("frequency_output")
    native: dict[str, Any] = {}
    frequency_native: dict[str, Any] | None = None
    if native_text is None:
        gaps.append("record carries no readable native_output product")
    else:
        native = _crosscheck_parse_native(native_text)
        if not native["energy_found"]:
            gaps.append("native output exposes no independently parsed final energy")
        if not native["normal_termination"]:
            contradictions.append("native output does not report normal termination")
        method = environment.get("method")
        if not isinstance(method, str) or not method:
            gaps.append("record declares no actual method")
        elif method.lower() not in native_text.lower():
            gaps.append(f"native output does not state the actual method {method!r}")
        basis = environment.get("basis")
        if isinstance(basis, str) and basis and basis.lower() not in native_text.lower():
            gaps.append(f"native output does not state the actual basis {basis!r}")
        version = native.get("software_version")
        if not version:
            gaps.append("native output does not state the software version")
        elif version != environment.get("software_version"):
            contradictions.append(
                f"native software version {version!r} differs from record software version "
                f"{environment.get('software_version')!r}"
            )
    if frequency_text is not None:
        frequency_native = _crosscheck_parse_native(frequency_text)
    task_report = _crosscheck_task_results(
        record.get("task_results"), environment, native, frequency_native, tolerance
    )
    checks.extend(task_report["checks"])
    contradictions.extend(task_report["contradictions"])
    gaps.extend(task_report["gaps"])
    checks.insert(
        0,
        {
            "name": "artifact_digests",
            "status": (
                "FAIL"
                if any(row["status"] == "FAIL" for row in artifact_rows)
                else (
                    "NOT_VERIFIED"
                    if any(row["status"] == "NOT_VERIFIED" for row in artifact_rows)
                    else "PASS"
                )
            ),
            "detail": f"{len(artifact_rows)} artifact(s) hashed against declared SHA-256",
        },
    )
    checks.insert(
        1,
        {
            "name": "native_output",
            "status": ("FAIL" if contradictions else ("NOT_VERIFIED" if gaps else "PASS")),
            "detail": "; ".join(contradictions or gaps) or None,
        },
    )
    return {
        "checks": checks,
        "contradictions": contradictions,
        "gaps": gaps,
        "artifact_rows": artifact_rows,
        "paths_by_role": paths,
        "native": native,
        "frequency_native": frequency_native,
        "task_results": task_report,
        "energy_hartree": task_report["energy_hartree"],
    }


def _crosscheck_receipt(platform: Mapping[str, Any]) -> str | None:
    receipt = platform.get("receipt")
    if not isinstance(receipt, Mapping):
        return "receipt is missing"
    if receipt.get("present") is not True:
        return "receipt.present must be true"
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or not _is_digest(digest):
        return "receipt.sha256 must be a sha256:<64 hex> digest"
    return None


def _crosscheck_lsf_evidence(
    record: Mapping[str, Any],
    *,
    constructed: bool,
    timeout: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    platform = record.get("platform")
    platform_mapping = dict(platform) if isinstance(platform, Mapping) else {}
    if constructed:
        reasons.append("real bsub/bjobs/submission/receipt evidence (record is constructed)")
    else:
        commands = platform_mapping.get("commands")
        command_text = ""
        if isinstance(commands, list):
            command_text = " ".join(
                " ".join(str(part) for part in command.get("argv", []))
                for command in commands
                if isinstance(command, Mapping)
            ).lower()
            for command in commands:
                if not isinstance(command, Mapping):
                    reasons.append("platform command record is not a JSON object")
                    continue
                exit_code = command.get("exit_code")
                if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                    reasons.append("platform command record has no integer exit_code")
        else:
            reasons.append("real bsub/bjobs command records are missing")
        if "bsub" not in command_text:
            reasons.append("real bsub command and exit code are missing")
        if "bjobs" not in command_text:
            reasons.append("real bjobs reconciliation command and exit code are missing")
        submission_id = platform_mapping.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id:
            reasons.append("submission_id is missing")
        receipt_gap = _crosscheck_receipt(platform_mapping)
        if receipt_gap is not None:
            reasons.append(receipt_gap)
    if timeout.get("status") != "OK":
        for gap in timeout.get("gaps", []):
            reasons.append(str(gap))
        for malformed in timeout.get("malformed", []):
            reasons.append(str(malformed))
    return reasons


def _crosscheck_compare(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    tolerance: Mapping[str, float],
) -> dict[str, Any]:
    if left.get("comparison_key") != right.get("comparison_key"):
        return {
            "left": left.get("record_id"),
            "right": right.get("record_id"),
            "status": "not_comparable",
            "reason": "different environment/method/version/input",
            "delta_hartree": None,
            "tolerance_hartree": float(tolerance["energy_hartree_abs"]),
        }
    left_energy = left.get("energy_hartree")
    right_energy = right.get("energy_hartree")
    if left_energy is None or right_energy is None:
        return {
            "left": left.get("record_id"),
            "right": right.get("record_id"),
            "status": "not_comparable",
            "reason": "one record exposes no comparable energy",
            "delta_hartree": None,
            "tolerance_hartree": float(tolerance["energy_hartree_abs"]),
        }
    delta = abs(float(left_energy) - float(right_energy))
    return {
        "left": left.get("record_id"),
        "right": right.get("record_id"),
        "status": "equal" if delta <= float(tolerance["energy_hartree_abs"]) else "differing",
        "reason": None,
        "delta_hartree": delta,
        "tolerance_hartree": float(tolerance["energy_hartree_abs"]),
    }


def _crosscheck_mark_failed(row: dict[str, Any], message: str) -> None:
    row["contradictions"].append(message)
    row["status"] = "FAIL"


def crosscheck_local_lsf_records(
    records: Any,
    *,
    frozen_deadlines: Any = None,
    tolerance: Any = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Cross-check platform Local/LSF records against the native science bytes.

    A record set is either a list of record objects or an object with a
    ``records``/``rows``/``runs`` list.  Every real record must carry the
    platform Loc/ LSF evidence, the frozen B07 deadlines and the
    ``$ACP2_INTEGRATION_SOURCE``/``$ACP2_INTEGRATION_PYTHON`` pair; a
    constructed record is labelled ``constructed: true`` and can prove the code
    path but never a real run.  Returned rows are PASS, FAIL, NOT_VERIFIED or
    STALE_REJECTED; a cancelled run is checked for its terminal state, stop
    confirmation and receipt instead of a completed scientific result.
    """
    raw_records, document = _crosscheck_document(records)
    record_root = Path(root) if root is not None else _repo_root()
    constructed_set = document.get("constructed") is True or any(
        isinstance(raw, Mapping) and raw.get("constructed") is True for raw in raw_records
    )
    deadline_source = (
        frozen_deadlines if frozen_deadlines is not None else document.get("frozen_deadlines")
    )
    client_request_seconds = document.get("client_request_seconds")
    if (
        isinstance(client_request_seconds, bool)
        or not isinstance(client_request_seconds, (int, float))
        or not math.isfinite(float(client_request_seconds))
        or float(client_request_seconds) < 0
    ):
        client_request_seconds = None
    timeout = derive_timeout_from_frozen(
        deadline_source, client_request_seconds=client_request_seconds
    )
    tolerance_map = dict(CROSSCHECK_SCIENCE_TOLERANCE)
    raw_tolerance = tolerance if tolerance is not None else document.get("tolerance")
    if isinstance(raw_tolerance, Mapping):
        for key in tolerance_map:
            value = raw_tolerance.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                tolerance_map[key] = float(value)
    integration = _crosscheck_integration_status()
    revisions = {
        "server_revision": document.get("server_revision"),
        "client_revision": document.get("client_revision"),
        "harness_revision": _sha256(SCRIPT_PATH),
        "harness_path": str(SCRIPT_PATH),
    }
    task_ref_kinds: dict[str, set[str]] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            continue
        task_ref = raw.get("task_ref")
        run_kind = raw.get("run_kind")
        if isinstance(task_ref, str) and run_kind in CROSSCHECK_RUN_KINDS:
            task_ref_kinds.setdefault(task_ref, set()).add(str(run_kind))
    conflicting_refs = {task_ref for task_ref, kinds in task_ref_kinds.items() if len(kinds) > 1}
    row_reports: list[dict[str, Any]] = []
    stale_responses: list[dict[str, Any]] = []
    cache: dict[str, dict[str, Any]] = {}
    last_sequence: dict[str, int] = {}
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping):
            row_reports.append(
                {
                    "record_id": f"record-{index}",
                    "status": "FAIL",
                    "gaps": [],
                    "contradictions": ["record is not a JSON object"],
                    "checks": [],
                    "digest_table": [],
                }
            )
            continue
        record = dict(raw)
        record_id = str(record.get("record_id") or f"record-{index}")
        task_ref = record.get("task_ref")
        task_ref_text = task_ref if isinstance(task_ref, str) else ""
        run_kind = record.get("run_kind")
        scheduler = str(record.get("scheduler") or "local").lower()
        attempt_raw = record.get("attempt", 1)
        try:
            attempt = int(attempt_raw)
        except (TypeError, ValueError):
            attempt = 0
        sequence_raw = record.get("sequence", index)
        try:
            sequence = int(sequence_raw)
        except (TypeError, ValueError):
            sequence = index
        constructed = record.get("constructed", constructed_set) is True
        gaps: list[str] = []
        contradictions: list[str] = []
        checks: list[dict[str, Any]] = []
        environment = _crosscheck_environment(record)
        row: dict[str, Any] = {
            "record_id": record_id,
            "task_ref": task_ref_text or None,
            "attempt": attempt,
            "run_kind": run_kind,
            "scheduler": scheduler,
            "constructed": constructed,
            "status": "NOT_VERIFIED",
            "gaps": gaps,
            "contradictions": contradictions,
            "checks": checks,
            "digest_table": [],
            "energy_hartree": None,
            "comparison_key": [
                environment.get("source"),
                environment.get("interpreter"),
                environment.get("software_name"),
                environment.get("software_version"),
                environment.get("method"),
                environment.get("basis"),
                environment.get("input_sha256"),
            ],
        }
        if task_ref_text and task_ref_text in conflicting_refs:
            contradictions.append(
                "completed_science_run and control_cancel_run must use different TaskRefs "
                f"(both use {task_ref_text!r})"
            )
        if attempt <= 0:
            contradictions.append("attempt must be a positive integer")
        if task_ref_text and sequence <= last_sequence.get(task_ref_text, -1):
            row["status"] = "STALE_REJECTED"
            row["reason"] = (
                f"out-of-order/stale response: sequence {sequence} is not newer than the "
                f"last applied sequence {last_sequence[task_ref_text]}"
            )
            stale_responses.append(
                {
                    "record_id": record_id,
                    "task_ref": task_ref_text,
                    "sequence": sequence,
                    "last_applied_sequence": last_sequence[task_ref_text],
                }
            )
            row_reports.append(row)
            continue
        if task_ref_text:
            last_sequence[task_ref_text] = sequence
        if not constructed:
            if not integration["available"]:
                gaps.extend(integration["gaps"])
            if not revisions["server_revision"]:
                gaps.append("server_revision is not recorded")
            if not revisions["client_revision"]:
                gaps.append("client_revision is not recorded")
        cache_spec = record.get("cache")
        cache_mapping = dict(cache_spec) if isinstance(cache_spec, Mapping) else {}
        cache_state = str(cache_mapping.get("state") or "cold")
        previous = cache.get(task_ref_text) if task_ref_text else None
        if cache_state not in ("cold", "warm", "evicted"):
            contradictions.append(f"cache.state {cache_state!r} is not cold/warm/evicted")
        elif cache_state == "evicted":
            cache.pop(task_ref_text, None)
            checks.append(
                {
                    "name": "cache_eviction_rebuild",
                    "status": "PASS",
                    "detail": "cache entry evicted and rebuilt from the current attempt",
                }
            )
        elif cache_state == "warm":
            if previous is None:
                gaps.append("warm cache declared but no prior cached entry exists")
            elif int(previous["attempt"]) < attempt:
                contradictions.append(
                    "cross-attempt replacement must evict the cache: cached artifact from "
                    f"attempt {previous['attempt']} while record attempt is {attempt}"
                )
        if cache_state == "cold":
            checks.append(
                {
                    "name": "cold_cache",
                    "status": "PASS",
                    "detail": "first read for this TaskRef populated the cache",
                }
            )
        restart_of = record.get("controller_restart_of")
        if restart_of is not None:
            reference = next(
                (item for item in row_reports if item.get("record_id") == restart_of), None
            )
            if reference is None:
                gaps.append(f"controller restart references unknown record {restart_of!r}")
            elif reference.get("task_ref") != task_ref_text or reference.get("attempt") != attempt:
                contradictions.append("controller restart must not create a new TaskRef or attempt")
            else:
                checks.append(
                    {
                        "name": "controller_restart_identity",
                        "status": "PASS",
                        "detail": f"restart reuses TaskRef {task_ref_text!r} attempt {attempt}",
                    }
                )
        if run_kind == CROSSCHECK_COMPLETED:
            if record.get("terminal_state") != "COMPLETED":
                contradictions.append("completed_science_run terminal_state must be COMPLETED")
            science = _crosscheck_completed_science(
                record,
                root=record_root,
                attempt=attempt,
                environment=environment,
                tolerance=tolerance_map,
            )
            checks.extend(science["checks"])
            contradictions.extend(science["contradictions"])
            gaps.extend(science["gaps"])
            row["digest_table"] = science["artifact_rows"]
            row["energy_hartree"] = science["energy_hartree"]
            row["science"] = {
                "native": science["native"],
                "frequency_native": science["frequency_native"],
                "task_results": science["task_results"]["results"],
            }
            artifact_digests = {
                str(item.get("role")): item.get("declared_sha256")
                for item in science["artifact_rows"]
            }
            if restart_of is not None:
                reference = next(
                    (item for item in row_reports if item.get("record_id") == restart_of), None
                )
                if reference is not None:
                    reference_digests = {
                        str(item.get("role")): item.get("declared_sha256")
                        for item in reference.get("digest_table", [])
                    }
                    if reference_digests != artifact_digests:
                        contradictions.append(
                            "controller restart returned different artifact digests"
                        )
            if task_ref_text and not contradictions:
                cache[task_ref_text] = {
                    "attempt": attempt,
                    "digests": artifact_digests,
                    "energy_hartree": row["energy_hartree"],
                    "sequence": sequence,
                }
        elif run_kind == CROSSCHECK_CANCELLED:
            if record.get("terminal_state") != "CANCELLED":
                contradictions.append("control_cancel_run terminal_state must be CANCELLED")
            platform = record.get("platform")
            platform_mapping = dict(platform) if isinstance(platform, Mapping) else {}
            stop_confirmation = platform_mapping.get("stop_confirmation")
            if not isinstance(stop_confirmation, Mapping):
                gaps.append("cancelled run has no stop confirmation")
            elif stop_confirmation.get("confirmed") is not True:
                gaps.append("cancelled run stop confirmation is not confirmed")
            elif stop_confirmation.get("observed_state") != "CANCELLED":
                gaps.append("cancelled run stop confirmation does not observe the CANCELLED state")
            receipt_gap = _crosscheck_receipt(platform_mapping)
            if receipt_gap is not None:
                gaps.append(f"cancelled run {receipt_gap}")
            row["control"] = {
                "terminal_state": record.get("terminal_state"),
                "stop_confirmation": stop_confirmation,
                "requires_completed_science": False,
            }
            checks.append(
                {
                    "name": "control_cancel_terminal_state",
                    "status": "PASS" if not gaps else "NOT_VERIFIED",
                    "detail": "cancelled run needs terminal state/stop confirmation/receipt only",
                }
            )
        else:
            contradictions.append(
                f"run_kind {run_kind!r} is not one of {', '.join(CROSSCHECK_RUN_KINDS)}"
            )
        if scheduler in ("lsf", "remote"):
            lsf_gaps = _crosscheck_lsf_evidence(record, constructed=constructed, timeout=timeout)
            gaps.extend(lsf_gaps)
            timing = record.get("timing")
            timing_mapping = dict(timing) if isinstance(timing, Mapping) else {}
            row["timings"] = {
                "queue_wait_seconds": timing_mapping.get("queue_wait_seconds"),
                "scientific_runtime_seconds": timing_mapping.get("run_seconds"),
                "prepare_seconds": timing_mapping.get("prepare_seconds"),
                "cleanup_seconds": timing_mapping.get("cleanup_seconds"),
                "client_request_seconds": timing_mapping.get("client_request_seconds"),
                "overall_acceptance_seconds": timeout.get("timeout_seconds"),
            }
            if timeout.get("status") == "OK":
                components = timeout["components"]
                for label, timing_key, frozen_key in (
                    ("queue wait", "queue_wait_seconds", "max_queue_wait_seconds"),
                    ("scientific runtime", "run_seconds", "max_run_seconds"),
                    ("prepare", "prepare_seconds", "max_prepare_seconds"),
                    ("cleanup", "cleanup_seconds", "cleanup_seconds"),
                ):
                    value = timing_mapping.get(timing_key)
                    if value is None:
                        continue
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or float(value) < 0
                    ):
                        contradictions.append(f"{label} timing is not a non-negative number")
                    elif float(value) > float(components[frozen_key]):
                        contradictions.append(
                            f"{label} {float(value)}s exceeds the frozen "
                            f"{frozen_key} {components[frozen_key]}s"
                        )
            if not lsf_gaps:
                checks.append(
                    {
                        "name": "lsf_submission_evidence",
                        "status": "PASS",
                        "detail": "real bsub/bjobs/submission/receipt and frozen deadlines present",
                    }
                )
        if contradictions:
            row["status"] = "FAIL"
        elif gaps:
            row["status"] = "NOT_VERIFIED"
        else:
            row["status"] = "PASS"
        row_reports.append(row)
    comparisons: list[dict[str, Any]] = []
    by_id = {row["record_id"]: row for row in row_reports}
    for row in row_reports:
        if row["run_kind"] != CROSSCHECK_COMPLETED or row["status"] == "STALE_REJECTED":
            continue
        raw = next(
            (
                item
                for item in raw_records
                if isinstance(item, Mapping)
                and str(item.get("record_id") or "") == row["record_id"]
            ),
            None,
        )
        if raw is None:
            continue
        compare_with = raw.get("compare_with") or raw.get("numeric_equality_with")
        if not isinstance(compare_with, list):
            continue
        for target_id in compare_with:
            target = by_id.get(str(target_id))
            if target is None:
                row["gaps"].append(f"numeric comparison target {target_id!r} is not a record")
                continue
            comparison = _crosscheck_compare(row, target, tolerance_map)
            comparisons.append(comparison)
            if comparison["status"] == "not_comparable":
                _crosscheck_mark_failed(
                    row,
                    "numeric equality across differing methods/versions is refused "
                    f"({row['record_id']} vs {target['record_id']}: {comparison['reason']})",
                )
            elif comparison["status"] == "differing":
                _crosscheck_mark_failed(
                    row,
                    "same-environment records differ beyond the frozen tolerance "
                    f"({comparison['delta_hartree']:.3e} Ha)",
                )
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in row_reports:
        if row["run_kind"] != CROSSCHECK_COMPLETED or row["status"] in (
            "STALE_REJECTED",
            "FAIL",
        ):
            continue
        if row.get("energy_hartree") is None:
            continue
        grouped.setdefault(tuple(row["comparison_key"]), []).append(row)
    for group in grouped.values():
        for left_index, left in enumerate(group):
            for right in group[left_index + 1 :]:
                comparison = _crosscheck_compare(left, right, tolerance_map)
                comparisons.append(comparison)
                if comparison["status"] == "differing":
                    _crosscheck_mark_failed(
                        left,
                        "same-environment records differ beyond the frozen tolerance "
                        f"({comparison['delta_hartree']:.3e} Ha)",
                    )
                    _crosscheck_mark_failed(
                        right,
                        "same-environment records differ beyond the frozen tolerance "
                        f"({comparison['delta_hartree']:.3e} Ha)",
                    )
    contradictions_global: list[str] = []
    if any(row["status"] == "FAIL" for row in row_reports):
        status = "FAIL"
    elif not row_reports:
        status = "NOT_VERIFIED"
        contradictions_global.append(
            "no platform Local/LSF records supplied; nothing can be cross-checked"
        )
    elif any(row["status"] == "NOT_VERIFIED" for row in row_reports):
        status = "NOT_VERIFIED"
    else:
        status = "PASS"
    cache_summary = {
        "states": sorted(
            {
                str((item.get("cache") or {}).get("state") or "cold")
                for item in raw_records
                if isinstance(item, Mapping) and isinstance(item.get("cache"), Mapping)
            }
        ),
        "entries": len(cache),
        "cold": sum(
            1
            for row in row_reports
            if any(check.get("name") == "cold_cache" for check in row.get("checks", []))
        ),
        "evicted": sum(
            1
            for row in row_reports
            if any(check.get("name") == "cache_eviction_rebuild" for check in row.get("checks", []))
        ),
        "restarts": sum(
            1
            for row in row_reports
            if any(
                check.get("name") == "controller_restart_identity"
                for check in row.get("checks", [])
            )
        ),
    }
    return {
        "schema": CROSSCHECK_REPORT_SCHEMA,
        "constructed": constructed_set,
        "status": status,
        "rows": row_reports,
        "digest_table": [entry for row in row_reports for entry in row.get("digest_table", [])],
        "numeric_comparisons": comparisons,
        "stale_responses": stale_responses,
        "cache": cache_summary,
        "timeout": timeout,
        "tolerance": tolerance_map,
        "revisions": revisions,
        "integration": integration,
        "gaps": contradictions_global,
        "contradictions": contradictions_global,
        "real_rows": (
            "NOT_VERIFIED" if not any(not row["constructed"] for row in row_reports) else status
        ),
    }


def print_crosscheck_report(report: Mapping[str, Any]) -> None:
    """Print the cross-check verdict, digest table and numeric comparisons."""
    print(f"crosscheck status: {report.get('status')} (constructed={report.get('constructed')})")
    for row in report.get("rows", []):
        print(
            f"  {row.get('record_id')}: {row.get('status')} "
            f"[{row.get('run_kind')}/{row.get('scheduler')}]"
        )
        for entry in row.get("digest_table", []):
            print(
                f"    {entry.get('role')}: {entry.get('actual_sha256')} "
                f"(declared {entry.get('declared_sha256')}, attempt {entry.get('attempt')})"
            )
        for message in row.get("contradictions", []):
            print(f"    contradiction: {message}")
        for message in row.get("gaps", []):
            print(f"    gap: {message}")
    for comparison in report.get("numeric_comparisons", []):
        print(
            f"  compare {comparison.get('left')} vs {comparison.get('right')}: "
            f"{comparison.get('status')}"
        )
    timeout = report.get("timeout") or {}
    print(f"  harness timeout: {timeout.get('timeout_seconds')} (status {timeout.get('status')})")


def _execution_timeout(args: argparse.Namespace, run_spec: Mapping[str, Any]) -> float:
    frozen = run_spec.get("frozen_deadlines")
    budget = run_spec.get("budget")
    if frozen is None and isinstance(budget, Mapping):
        frozen = budget.get("frozen_deadlines")
    if frozen is not None:
        derived = derive_timeout_from_frozen(frozen)
        if derived["status"] == "OK" and derived["timeout_seconds"] is not None:
            return float(derived["timeout_seconds"])
        return 0.0
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


# ── A-17: Diels-Alder transition-state scenario ────────────────────────────


def _parse_xyz(path: Path) -> tuple[list[str], list[tuple[float, float, float]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"{path.name} is empty")
    atom_count = int(lines[0].strip())
    atom_lines = [line for line in lines[2:] if line.strip()]
    if len(atom_lines) != atom_count:
        raise ValueError(f"{path.name}: declared {atom_count} atoms but found {len(atom_lines)}")
    symbols: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in atom_lines:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"{path.name}: malformed atom line {line!r}")
        symbols.append(fields[0])
        values = tuple(float(value) for value in fields[1:])
        if len(values) != 3 or any(not math.isfinite(value) for value in values):
            raise ValueError(f"{path.name}: atom coordinates must be finite 3-vectors")
        coordinates.append((values[0], values[1], values[2]))
    return symbols, coordinates


def _formula_counts(formula: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    position = 0
    for match in re.finditer(r"([A-Z][a-z]?)(\d*)", formula):
        if match.start() != position:
            raise ValueError(f"malformed formula {formula!r}")
        symbol, raw_count = match.group(1), match.group(2)
        counts[symbol] = counts.get(symbol, 0) + (int(raw_count) if raw_count else 1)
        position = match.end()
    if position != len(formula) or not counts:
        raise ValueError(f"malformed formula {formula!r}")
    return counts


def _validate_da_fixture(fixture_path: Path) -> dict[str, Any]:
    """Validate the frozen reactant/product geometry and the small budget.

    This is the first gate of the DA scenario: a contradictory fixture is a
    scenario FAIL, never a scientific inconclusive.  It reuses the public
    reaction/question contracts; it never runs QC and never substitutes a
    different structure.
    """
    from cccp.analysis.research.question import Question
    from cccp.analysis.research.reaction import Reaction
    from cccp.analysis.research.validators import (
        ATOM_COUNT_TOLERANCE,
        COORDINATE_ABS_TOLERANCE_ANGSTROM,
        IMAGINARY_MODE_THRESHOLD_CM1,
        LOW_FREQUENCY_THRESHOLD_CM1,
        VALIDATOR_VERSION,
    )

    checks: list[dict[str, Any]] = []
    contradictions: list[str] = []
    gaps: list[str] = []

    def check(name: str, status: str, detail: str | None = None) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    try:
        manifest = _load_json(fixture_path)
    except _InvalidInputError as exc:
        return {
            "status": "FAIL",
            "checks": [{"name": "fixture_manifest", "status": "FAIL", "detail": str(exc)}],
            "contradictions": [str(exc)],
            "gaps": [],
            "geometry": None,
            "budget": None,
        }
    if not isinstance(manifest, Mapping):
        contradictions.append("fixture manifest must be a JSON object")
        check("fixture_manifest", "FAIL", contradictions[-1])
        return {
            "status": "FAIL",
            "checks": checks,
            "contradictions": contradictions,
            "gaps": gaps,
            "geometry": None,
            "budget": None,
        }
    if manifest.get("schema") != "acp2.science.fixture-manifest":
        contradictions.append("fixture schema must be acp2.science.fixture-manifest")
    if manifest.get("task_kind") != "diels_alder":
        contradictions.append("fixture task_kind must be 'diels_alder'")
    if manifest.get("constructed") is not True:
        contradictions.append("the frozen DA fixture must be labelled constructed=true")
    if manifest.get("real_run") is not False:
        contradictions.append("the frozen DA fixture must record real_run=false")
    check(
        "fixture_identity",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    fixture_files = manifest.get("fixture_files")
    if not isinstance(fixture_files, Mapping):
        contradictions.append("fixture_files must be a JSON object")
        fixture_files = {}
    file_digests = manifest.get("file_digests")
    if not isinstance(file_digests, Mapping):
        contradictions.append("file_digests must be a JSON object")
        file_digests = {}
    base = fixture_path.parent
    file_paths: dict[str, Path] = {}
    for label, relative in fixture_files.items():
        if not isinstance(relative, str) or not relative:
            contradictions.append(f"fixture_files.{label} must be a relative path")
            continue
        path = base / relative
        file_paths[str(label)] = path
        if not path.is_file():
            contradictions.append(f"fixture file {relative!r} is absent")
            continue
        expected = file_digests.get(relative)
        if not isinstance(expected, str):
            contradictions.append(f"file_digests has no digest for {relative!r}")
            continue
        actual = _sha256(path)
        if actual != expected:
            contradictions.append(f"{relative}: digest {actual} differs from frozen {expected}")
    check(
        "fixture_digests",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    reaction = None
    reaction_path = file_paths.get("reaction")
    if reaction_path is not None and reaction_path.is_file():
        try:
            reaction = Reaction.from_dict(_load_json(reaction_path))
        except Exception as exc:  # noqa: BLE001 - contract errors are data
            contradictions.append(f"reaction.json rejected by the v1 contract: {exc}")
    question_path = file_paths.get("question_set")
    if question_path is not None and question_path.is_file():
        try:
            question_document = _load_json(question_path)
            questions = question_document.get("questions")
            if not isinstance(questions, list) or not questions:
                contradictions.append("question.json must carry a non-empty questions list")
            else:
                for index, payload in enumerate(questions):
                    Question.from_dict(payload)
        except Exception as exc:  # noqa: BLE001 - contract errors are data
            contradictions.append(f"question.json rejected by the v1 contract: {exc}")
    check(
        "reaction_question_contracts",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    geometry: dict[str, Any] | None = None
    reactants_path = file_paths.get("reactants")
    product_path = file_paths.get("product")
    if reaction is not None and reactants_path is not None and product_path is not None:
        try:
            left_symbols = [symbol for structure in reaction.left for symbol in structure.elements]
            right_symbols = [
                symbol for structure in reaction.right for symbol in structure.elements
            ]
            reactant_symbols, reactant_coordinates = _parse_xyz(reactants_path)
            product_symbols, product_coordinates = _parse_xyz(product_path)
            if reactant_symbols != left_symbols:
                contradictions.append(
                    "reactants.xyz atom order differs from the declared left elements order"
                )
            if product_symbols != right_symbols:
                contradictions.append(
                    "product.xyz atom order differs from the declared right elements order"
                )
            if len(reactant_coordinates) != len(reactant_symbols):
                contradictions.append("reactants.xyz atom count is inconsistent")
            if len(product_coordinates) != len(product_symbols):
                contradictions.append("product.xyz atom count is inconsistent")
            left_formula: dict[str, int] = {}
            for structure in reaction.left:
                for symbol, count in _formula_counts(structure.formula).items():
                    left_formula[symbol] = left_formula.get(symbol, 0) + count
                if _formula_counts(structure.formula) != _formula_counts(
                    "".join(structure.elements)
                ):
                    contradictions.append(
                        f"{structure.structure_id}: formula disagrees with its elements"
                    )
            right_formula: dict[str, int] = {}
            for structure in reaction.right:
                for symbol, count in _formula_counts(structure.formula).items():
                    right_formula[symbol] = right_formula.get(symbol, 0) + count
                if _formula_counts(structure.formula) != _formula_counts(
                    "".join(structure.elements)
                ):
                    contradictions.append(
                        f"{structure.structure_id}: formula disagrees with its elements"
                    )
            if left_formula != right_formula:
                contradictions.append(
                    f"elements are not conserved: {left_formula} vs {right_formula}"
                )
            left_charge = sum(int(structure.charge) for structure in reaction.left)
            right_charge = sum(int(structure.charge) for structure in reaction.right)
            if left_charge != right_charge:
                contradictions.append(
                    f"net charge is not conserved: left={left_charge} right={right_charge}"
                )
            mapping = reaction.atom_mapping.left_to_right
            if len(set(mapping)) != len(mapping) or len(set(mapping.values())) != len(mapping):
                contradictions.append("the reaction atom mapping is not one-to-one")
            geometry = {
                "reactant_atoms": len(reactant_symbols),
                "product_atoms": len(product_symbols),
                "reactant_elements": dict(Counter(reactant_symbols)),
                "product_elements": dict(Counter(product_symbols)),
                "net_charge": left_charge,
                "atom_order_matches_declared": (
                    reactant_symbols == left_symbols and product_symbols == right_symbols
                ),
            }
        except (OSError, ValueError) as exc:
            contradictions.append(f"reactant/product geometry rejected: {exc}")
    check(
        "reactant_product_geometry",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    budget: dict[str, Any] | None = None
    caps = manifest.get("candidate_cap")
    resource_budget = manifest.get("resource_budget")
    run_spec = manifest.get("run_spec")
    expected_caps = {
        "max_candidate_paths": 1,
        "max_ts_candidates": 1,
        "max_conformers_per_endpoint": 1,
    }
    if not isinstance(caps, Mapping):
        contradictions.append("candidate_cap must be a JSON object")
        caps = {}
    for key, expected in expected_caps.items():
        if caps.get(key) != expected:
            contradictions.append(f"candidate_cap.{key} must be {expected}")
    if not isinstance(resource_budget, Mapping):
        contradictions.append("resource_budget must be a JSON object")
        resource_budget = {}
    step_caps = resource_budget.get("step_caps")
    if not isinstance(step_caps, Mapping):
        contradictions.append("resource_budget.step_caps must be a JSON object")
        step_caps = {}
    expected_step_caps = {
        "max_attempts_per_step": 2,
        "max_bsub": 1,
        "max_candidate_paths": 1,
        "max_conformers": 1,
        "max_qc_invocations": 10,
        "max_task_refs": 10,
        "max_ts_candidates": 1,
    }
    for key, expected in expected_step_caps.items():
        if step_caps.get(key) != expected:
            contradictions.append(f"resource_budget.step_caps.{key} must be {expected}")
    if resource_budget.get("budget_timing_basis") != "unmeasured":
        contradictions.append(
            "resource_budget.budget_timing_basis must remain 'unmeasured' before a real run"
        )
    if not resource_budget.get("h4_open_consequence"):
        contradictions.append("resource_budget.h4_open_consequence must state the H4 consequence")
    if not isinstance(run_spec, Mapping):
        contradictions.append("run_spec must be a JSON object")
        run_spec = {}
    science_seconds = run_spec.get("science_deadline_seconds")
    export_seconds = run_spec.get("export_budget_seconds")
    aggregate_seconds = run_spec.get("aggregate_budget_seconds")
    if (
        not isinstance(science_seconds, int)
        or not isinstance(export_seconds, int)
        or not isinstance(aggregate_seconds, int)
        or aggregate_seconds != science_seconds + export_seconds
    ):
        contradictions.append("run_spec deadlines must satisfy aggregate = science + export")
    if not run_spec.get("started_at"):
        contradictions.append("run_spec.started_at must be frozen")
    queue = run_spec.get("queue_accounting")
    if (
        not isinstance(queue, Mapping)
        or queue.get("queue_wait_counts_toward_total_wall_time") is not True
    ):
        contradictions.append("run_spec.queue_accounting must count queue wait in wall time")
    export_window = run_spec.get("export_window_rule")
    if (
        not isinstance(export_window, Mapping)
        or export_window.get("dispatching_allowed") is not False
    ):
        contradictions.append("run_spec.export_window_rule must forbid new dispatch")
    in_flight = run_spec.get("in_flight_stop_rule")
    if (
        not isinstance(in_flight, Mapping)
        or in_flight.get("requires_stop_confirmation") is not True
    ):
        contradictions.append("run_spec.in_flight_stop_rule must require stop confirmation")
    budget = {
        "candidate_cap": dict(caps),
        "step_caps": dict(step_caps),
        "science_deadline_seconds": science_seconds,
        "export_budget_seconds": export_seconds,
        "aggregate_budget_seconds": aggregate_seconds,
        "started_at": run_spec.get("started_at"),
        "budget_timing_basis": resource_budget.get("budget_timing_basis"),
        "small_budget": (
            step_caps.get("max_qc_invocations") == 10
            and step_caps.get("max_bsub") == 1
            and step_caps.get("max_attempts_per_step") == 2
        ),
    }
    check(
        "frozen_small_budget",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    scientific = manifest.get("science_parameters")
    if not isinstance(scientific, Mapping):
        contradictions.append("science_parameters must be a JSON object")
        scientific = {}
    if scientific.get("method") != "wB97X-D4":
        contradictions.append("science_parameters.method must be wB97X-D4")
    if scientific.get("basis") != "def2-TZVP":
        contradictions.append("science_parameters.basis must be def2-TZVP")
    if scientific.get("charge") != 0 or scientific.get("multiplicity") != 1:
        contradictions.append("science_parameters must freeze charge=0 multiplicity=1")
    state = scientific.get("electronic_state")
    if not isinstance(state, Mapping):
        contradictions.append("science_parameters.electronic_state must be a JSON object")
        state = {}
    if (
        state.get("charge") != 0
        or state.get("target_multiplicity") != 1
        or not state.get("state_id")
    ):
        contradictions.append(
            "science_parameters.electronic_state must declare charge 0, "
            "multiplicity 1 and a state_id"
        )
    if scientific.get("standard_state") != "1_bar_ideal_gas":
        contradictions.append("science_parameters.standard_state must be 1_bar_ideal_gas")
    if scientific.get("temperature_k") != 298.15:
        contradictions.append("science_parameters.temperature_k must be 298.15")
    if scientific.get("pressure_pa") != 100000.0:
        contradictions.append("science_parameters.pressure_pa must be 100000.0")
    tolerances = manifest.get("tolerances")
    if not isinstance(tolerances, Mapping):
        contradictions.append("tolerances must be a JSON object")
        tolerances = {}
    expected_tolerances = {
        "atom_count_tolerance": ATOM_COUNT_TOLERANCE,
        "coordinate_abs_tolerance_angstrom": COORDINATE_ABS_TOLERANCE_ANGSTROM,
        "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
        "low_frequency_threshold_cm1": LOW_FREQUENCY_THRESHOLD_CM1,
        "validator_version": VALIDATOR_VERSION,
    }
    for key, expected in expected_tolerances.items():
        if tolerances.get(key) != expected:
            contradictions.append(f"tolerances.{key} must be {expected!r}")
    check(
        "frozen_science_identity",
        "FAIL" if contradictions else "PASS",
        "; ".join(contradictions) or None,
    )

    return {
        "status": "FAIL" if contradictions else "PASS",
        "checks": checks,
        "contradictions": contradictions,
        "gaps": gaps,
        "geometry": geometry,
        "budget": budget,
        "science_parameters": {
            "method": scientific.get("method"),
            "basis": scientific.get("basis"),
            "charge": scientific.get("charge"),
            "multiplicity": scientific.get("multiplicity"),
            "electronic_state": dict(state),
            "standard_state": scientific.get("standard_state"),
            "temperature_k": scientific.get("temperature_k"),
            "pressure_pa": scientific.get("pressure_pa"),
        },
        "tolerances": dict(tolerances),
    }


def _da_execution_provenance(platform: Any, binding_attempt: Any) -> dict[str, Any]:
    """Check that the snapshot really points at B20a-exec execution evidence.

    ``B20a-ready`` is only the execution interface/preflight.  It is never
    accepted as execution evidence; citing it as such is a structural FAIL, not
    a missing-evidence NOT_VERIFIED.
    """
    reasons: list[str] = []
    contradictions: list[str] = []
    execution = platform.get("execution") if isinstance(platform, Mapping) else None
    if not isinstance(execution, Mapping):
        return {
            "status": "NOT_VERIFIED",
            "reasons": [
                "snapshot.platform.execution is missing: no B20a-exec artifact id or "
                "semantic candidate is published; B20a-ready is not execution evidence"
            ],
            "contradictions": [],
            "producer": None,
            "artifact_id": None,
            "semantic_candidate": None,
            "attempt": None,
            "step_receipts": [],
            "b20a_ready": None,
        }
    producer = execution.get("producer")
    artifact_id = execution.get("artifact_id")
    sub_deliverable = execution.get("sub_deliverable")
    semantic_candidate = execution.get("semantic_candidate")
    attempt = execution.get("attempt")
    ready = execution.get("b20a_ready")
    if ready is not None and not isinstance(ready, Mapping):
        reasons.append("platform.execution.b20a_ready must be a JSON object when present")
        ready = None
    if producer == "B20a-ready":
        contradictions.append("B20a-ready is the execution interface, not execution evidence")
    if isinstance(artifact_id, str) and artifact_id.startswith("B20a-ready"):
        contradictions.append("the execution artifact_id cites B20a-ready, not B20a-exec")
    if isinstance(ready, Mapping) and ready.get("cited_as_execution_evidence") is True:
        contradictions.append(
            "platform.execution.b20a_ready is cited as execution evidence; it is only preflight"
        )
    if producer != "B20a-exec":
        reasons.append("platform.execution.producer must be 'B20a-exec'")
    if artifact_id != DA_EXECUTION_ARTIFACT_ID:
        reasons.append(f"platform.execution.artifact_id must be {DA_EXECUTION_ARTIFACT_ID!r}")
    if semantic_candidate != DA_EXECUTION_SEMANTIC_CANDIDATE:
        reasons.append(
            f"platform.execution.semantic_candidate must be {DA_EXECUTION_SEMANTIC_CANDIDATE!r}"
        )
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        reasons.append("platform.execution.attempt must be an integer")
    elif binding_attempt is not None and attempt != binding_attempt:
        contradictions.append(
            f"B20a-exec attempt {attempt} differs from the current snapshot attempt "
            f"{binding_attempt}; old-attempt artifacts are rejected"
        )
    receipts = execution.get("step_receipts")
    if not isinstance(receipts, list) or not receipts:
        reasons.append("platform.execution.step_receipts must be a non-empty list")
        receipts = []
    normalized_receipts: list[dict[str, Any]] = []
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, Mapping):
            reasons.append(f"platform.execution.step_receipts[{index}] must be a JSON object")
            continue
        step_id = receipt.get("step_id")
        status = receipt.get("status")
        receipt_id = receipt.get("receipt_id")
        if not isinstance(step_id, str) or not step_id:
            reasons.append(f"platform.execution.step_receipts[{index}].step_id is missing")
        if not isinstance(receipt_id, str) or not receipt_id:
            reasons.append(f"platform.execution.step_receipts[{index}].receipt_id is missing")
        if status != "completed":
            contradictions.append(
                f"B20a-exec step receipt {step_id!r} has status {status!r}, not 'completed'"
            )
        normalized_receipts.append(dict(receipt))
    status = "FAIL" if contradictions else "NOT_VERIFIED" if reasons else "PASS"
    return {
        "status": status,
        "reasons": reasons,
        "contradictions": contradictions,
        "producer": producer,
        "artifact_id": artifact_id,
        "sub_deliverable": sub_deliverable,
        "semantic_candidate": semantic_candidate,
        "attempt": attempt,
        "step_receipts": normalized_receipts,
        "b20a_ready": dict(ready) if isinstance(ready, Mapping) else None,
        "b20a_ready_cited_as_execution_evidence": bool(
            isinstance(ready, Mapping) and ready.get("cited_as_execution_evidence") is True
        ),
    }


def _da_candidate_document(
    snapshot: Any,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    documents: list[dict[str, Any]] = []
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
        if isinstance(document, Mapping) and document.get("schema") == DA_CANDIDATE_SCHEMA:
            documents.append(dict(document))
    if not documents:
        gaps.append(
            f"snapshot carries no B20a-exec candidate-selection artifact ({DA_CANDIDATE_SCHEMA!r})"
        )
    elif len(documents) > 1:
        contradictions.append(
            f"snapshot carries {len(documents)} candidate-selection artifacts; "
            "exactly one is required"
        )
    return (documents[0] if len(documents) == 1 else None), gaps, contradictions


def _da_candidate_validation(
    document: Mapping[str, Any],
    manifest: Mapping[str, Any],
    binding_attempt: Any,
) -> dict[str, Any]:
    reasons: list[str] = []
    contradictions: list[str] = []
    attempt = document.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        reasons.append("candidate-selection attempt must be an integer")
    elif binding_attempt is not None and attempt != binding_attempt:
        contradictions.append(
            f"candidate-selection attempt {attempt} differs from the current snapshot "
            f"attempt {binding_attempt}; old-attempt selection records are rejected"
        )

    source = document.get("starting_structure_source")
    if source not in DA_STARTING_STRUCTURE_SOURCES:
        reasons.append(
            f"starting_structure_source must be one of {sorted(DA_STARTING_STRUCTURE_SOURCES)}"
        )
    selection_status = document.get("selection_status")
    selection_reason = document.get("selection_reason")
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        reasons.append("candidate-selection selection_reason must be a non-empty string")

    search_range = document.get("search_range")
    if not isinstance(search_range, Mapping):
        reasons.append("candidate-selection search_range must be a bounded JSON object")
        search_range_view: dict[str, Any] = {}
    else:
        search_range_view = dict(search_range)
        caps = manifest.get("candidate_cap")
        if not isinstance(caps, Mapping):
            caps = {}
        for key in ("max_candidate_paths", "max_ts_candidates", "max_conformers_per_endpoint"):
            value = search_range.get(key)
            cap = caps.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                reasons.append(f"search_range.{key} must be a positive integer")
            elif isinstance(cap, int) and value > cap:
                contradictions.append(f"search_range.{key}={value} exceeds the frozen cap {cap}")
        if search_range.get("max_candidate_paths") != 1:
            reasons.append("search_range.max_candidate_paths must stay at the frozen single path")
        if search_range.get("max_ts_candidates") != 1:
            reasons.append(
                "search_range.max_ts_candidates must stay at the frozen single TS candidate"
            )
        if search_range.get("max_conformers_per_endpoint") != 1:
            reasons.append(
                "search_range.max_conformers_per_endpoint must stay at the frozen single conformer"
            )

    raw_candidates = document.get("candidates")
    if not isinstance(raw_candidates, list):
        reasons.append("candidate-selection candidates must be a list")
        raw_candidates = []
    candidates: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_candidates):
        if not isinstance(candidate, Mapping):
            contradictions.append(f"candidates[{index}] must be a JSON object")
            continue
        candidate_id = candidate.get("candidate_id")
        candidate_source = candidate.get("source")
        candidate_status = candidate.get("status")
        candidate_reason = candidate.get("reason")
        if not isinstance(candidate_id, str) or not candidate_id:
            contradictions.append(f"candidates[{index}].candidate_id is missing")
        if candidate_source not in DA_STARTING_STRUCTURE_SOURCES:
            contradictions.append(
                f"candidates[{index}].source must be one of {sorted(DA_STARTING_STRUCTURE_SOURCES)}"
            )
        if candidate_status not in {"selected", "rejected"}:
            contradictions.append(f"candidates[{index}].status must be 'selected' or 'rejected'")
        if not isinstance(candidate_reason, str) or not candidate_reason.strip():
            contradictions.append(
                f"candidates[{index}].reason must record the selection/rejection reason"
            )
        candidates.append(dict(candidate))
        if candidate_status == "selected":
            selected.append(dict(candidate))
    if len(selected) > 1:
        contradictions.append("more than one candidate is marked selected")
    selected_id = document.get("selected_candidate_id")
    if selected:
        if selected_id != selected[0].get("candidate_id"):
            contradictions.append(
                "selected_candidate_id does not match the selected candidate record"
            )
        if source != selected[0].get("source"):
            contradictions.append(
                "starting_structure_source does not match the selected candidate source"
            )
    elif selected_id is not None:
        contradictions.append("selected_candidate_id is set but no candidate is marked selected")
    if selection_status == "selected" and not selected:
        contradictions.append("selection_status is 'selected' but no candidate is selected")
    if selection_status == "no_candidate" and selected:
        contradictions.append(
            "selection_status is 'no_candidate' but a candidate is marked selected"
        )
    if selection_status not in {"selected", "no_candidate"}:
        if selected:
            contradictions.append(
                f"selection_status {selection_status!r} disagrees with a selected candidate"
            )
        elif selection_status != "no_candidate":
            reasons.append("selection_status must be 'selected' or 'no_candidate'")

    search_claim = document.get("search_capability_claim", "not_claimed")
    if source in {"user_input", "frozen_known_candidate"} and search_claim != "not_claimed":
        contradictions.append(
            "a user-input or frozen-known starting structure must not claim "
            "research search capability"
        )
    if not selected and search_claim == "supported":
        contradictions.append(
            "no selected candidate can support a research search capability claim"
        )

    mapped_mode = document.get("mapped_mode")
    if mapped_mode is not None and not isinstance(mapped_mode, Mapping):
        contradictions.append("mapped_mode must be a JSON object or null")
    provenance: dict[str, Any] = {}
    if selected:
        for key in (
            "ts_optimize_provenance",
            "ts_optimize_provenance_sha256",
            "ts_frequency_provenance",
            "ts_frequency_provenance_sha256",
        ):
            value = document.get(key)
            if not isinstance(value, str) or not value:
                reasons.append(f"selected candidate record requires {key}")
            provenance[key] = value
    status = "FAIL" if contradictions else "NOT_VERIFIED" if reasons else "PASS"
    return {
        "status": status,
        "reasons": reasons,
        "contradictions": contradictions,
        "attempt": attempt,
        "starting_structure_source": source,
        "selection_status": selection_status,
        "selection_reason": selection_reason,
        "search_range": search_range_view,
        "candidates": candidates,
        "selected_candidate_id": selected_id if selected else None,
        "selected_candidate": selected[0] if selected else None,
        "mapped_mode": dict(mapped_mode) if isinstance(mapped_mode, Mapping) else None,
        "search_capability_claim": search_claim,
        "provenance": provenance,
        "raw_document": dict(document),
    }


def _da_named_result(
    snapshot: Any,
    relative_path: Any,
    expected_sha256: Any,
) -> tuple[Any | None, dict[str, Any] | None, list[str], list[str]]:
    from cccp.calculation.results import TaskResult

    gaps: list[str] = []
    contradictions: list[str] = []
    if not isinstance(relative_path, str) or not relative_path:
        return None, None, ["selected candidate carries no provenance path"], contradictions
    artifact = next(
        (item for item in snapshot.artifacts if item.relative_path == relative_path),
        None,
    )
    if artifact is None:
        return (
            None,
            None,
            [f"snapshot declares no artifact at {relative_path!r}"],
            contradictions,
        )
    if isinstance(expected_sha256, str) and expected_sha256:
        normalized = (
            expected_sha256
            if expected_sha256.startswith("sha256:")
            else (f"sha256:{expected_sha256}")
        )
        if normalized != artifact.declared_sha256:
            contradictions.append(
                f"{relative_path}: candidate-selection digest {normalized} differs from the "
                f"snapshot digest {artifact.declared_sha256}"
            )
    try:
        document = json.loads(artifact.resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        contradictions.append(f"{relative_path}: unreadable TaskResult JSON: {exc}")
        return None, artifact, gaps, contradictions
    try:
        result = TaskResult.from_dict(document)
    except (TypeError, ValueError) as exc:
        contradictions.append(f"{relative_path}: invalid TaskResult document: {exc}")
        return None, artifact, gaps, contradictions
    return result, artifact, gaps, contradictions


def _da_optimized_geometry_check(snapshot: Any, optimize_result: Any) -> dict[str, Any]:
    from cccp.analysis.research.validators import COORDINATE_ABS_TOLERANCE_ANGSTROM

    gaps: list[str] = []
    contradictions: list[str] = []
    artifact = next(
        (item for item in snapshot.artifacts if item.role == "optimized_structure"),
        None,
    )
    if artifact is None:
        gaps.append("snapshot declares no optimized_structure artifact")
    elif artifact.declared_sha256 != snapshot.science_identity.structure_sha256:
        contradictions.append(
            "the optimized_structure artifact is not the snapshot scientific_identity structure"
        )
    else:
        try:
            symbols, coordinates = _parse_xyz(artifact.resolved_path)
        except (OSError, ValueError) as exc:
            contradictions.append(f"optimized_structure artifact is unreadable: {exc}")
            symbols, coordinates = [], []
        if not optimize_result.symbols or optimize_result.coordinates is None:
            gaps.append("the TS optimize TaskResult carries no final geometry")
        elif symbols and list(optimize_result.symbols or ()) != symbols:
            contradictions.append(
                "the TS optimize TaskResult atom order differs from the "
                "optimized_structure artifact"
            )
        if coordinates and optimize_result.coordinates is not None:
            worst = 0.0
            for left, right in zip(coordinates, optimize_result.coordinates, strict=False):
                for left_value, right_value in zip(left, right, strict=False):
                    worst = max(worst, abs(float(left_value) - float(right_value)))
            if worst > COORDINATE_ABS_TOLERANCE_ANGSTROM:
                contradictions.append(
                    "the TS optimize TaskResult geometry differs from the optimized_structure "
                    f"artifact by {worst:.6f} A (tolerance {COORDINATE_ABS_TOLERANCE_ANGSTROM})"
                )
    return {
        "status": "FAIL" if contradictions else ("NOT_VERIFIED" if gaps else "PASS"),
        "contradictions": contradictions,
        "gaps": gaps,
        "artifact": artifact.to_dict() if artifact is not None else None,
    }


def _da_h4_readiness(
    *,
    constructed: bool,
    real_run: bool,
    scenario_result: str,
    claim_status: str,
    connectivity_status: str,
    selected_candidate_id: str | None,
) -> dict[str, Any]:
    valid_ts_state = (
        "closed" if real_run and not constructed and claim_status == "supported" else "open"
    )
    valid_ts_reason = (
        "a real valid TS candidate is supported"
        if valid_ts_state == "closed"
        else (
            "constructed fixture: the supported saddle candidate is not a real TS artifact"
            if claim_status == "supported"
            else f"claim status is {claim_status}; H4 needs a real valid TS candidate"
        )
    )
    rows = [
        {
            "row_id": "H4-01-valid-ts",
            "requirement": "A real valid TS: one imaginary mode and a mapped mode.",
            "scenario_result": scenario_result,
            "claim_status": claim_status,
            "state": valid_ts_state,
            "reason": valid_ts_reason,
            "selected_candidate_id": selected_candidate_id,
        },
        {
            "row_id": "H4-02-bidirectional-irc",
            "requirement": "Bidirectional IRC with both specified endpoints reached.",
            "scenario_result": "NOT_VERIFIED",
            "claim_status": "not_evaluated",
            "state": "open",
            "reason": "A-18 owns the bidirectional IRC validation; this scenario does not run IRC.",
        },
        {
            "row_id": "H4-03-endpoint-connectivity",
            "requirement": "Endpoint geometry and bond-topology mapping prove the declared path.",
            "scenario_result": "NOT_VERIFIED",
            "claim_status": connectivity_status,
            "state": "open",
            "reason": "Connectivity is inconclusive without bidirectional IRC endpoint evidence.",
        },
        {
            "row_id": "H4-04-comparable-energy",
            "requirement": "Activation energy is comparable under frozen conditions and units.",
            "scenario_result": "NOT_VERIFIED",
            "claim_status": "not_evaluated",
            "state": "open",
            "reason": (
                "A-19 owns the thermochemistry/comparability validation; no energy is claimed here."
            ),
        },
        {
            "row_id": "H4-05-honest-negatives",
            "requirement": (
                "Honest negative cases are recorded; inconclusive/failed never becomes positive."
            ),
            "scenario_result": scenario_result,
            "claim_status": "not_evaluated",
            "state": "open" if constructed or not real_run else "closed",
            "reason": (
                "the constructed harness recorded every rejection reason and kept the claim "
                "honest; no real negative run exists"
                if constructed or not real_run
                else "the real scenario recorded its negative cases honestly"
            ),
        },
    ]
    positive = valid_ts_state == "closed"
    return {
        "closed": False,
        "status": "open",
        "positive_evidence": positive,
        "real_run": real_run,
        "rows": rows,
        "reason": (
            "H4 requires a real valid TS, bidirectional IRC with both endpoints, comparable "
            "energies and honest negatives. Only the TS scenario was audited here; H4 stays open."
        ),
    }


def _print_da_ts_report(report: Mapping[str, Any]) -> None:
    print("DA transition-state report")
    print(f"  scenario result: {report.get('scenario_result')}")
    print(f"  claim status: {report.get('claim_status')}")
    h4 = report.get("h4") or {}
    print(f"  H4 readiness: {h4.get('status')} (closed={h4.get('closed')})")
    print(f"  selected candidate: {report.get('selected_candidate_id')}")
    print(f"  starting structure: {report.get('starting_structure_source')}")
    claim = report.get("scientific_claim") or {}
    print(f"  saddle claim: {claim.get('claim_id')} = {claim.get('status')}")
    print(f"  connectivity: {report.get('connectivity_status')}")
    for row in h4.get("rows") or []:
        print(
            f"  {row.get('row_id')}: scenario={row.get('scenario_result')} "
            f"claim={row.get('claim_status')} state={row.get('state')}"
        )


def _execute_validate_da_ts(
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    *,
    started_at: str,
    argv: list[str],
) -> int:
    """Validate a B20a-exec Diels-Alder TS candidate without running a job."""
    receipt = _receipt_protocol()
    fixture_path = Path(args.fixture)
    extra: dict[str, Any] = {
        "scenario_implementation": "A-17",
        "fixture_path": str(fixture_path),
        "input_manifest_path": plan.get("input_manifest_path"),
        "platform_registry": None,
        "constructed_fixture": False,
        "real_run": False,
        "execution_provenance": None,
        "fixture_validation": None,
        "candidate_selection": None,
        "candidate_records": None,
        "search_range": None,
        "selected_candidate_id": None,
        "starting_structure_source": None,
        "scientific_claim": None,
        "claims": None,
        "methods_state": None,
        "connectivity_status": None,
        "scenario_result": None,
        "claim_status": None,
        "h4": None,
        "example_ts_substituted": False,
        "second_da_job_started": False,
        "b20a_ready_cited_as_execution_evidence": False,
        "b20a_exec_artifact_id": None,
        "b20a_exec_sub_deliverable": None,
        "b20a_exec_semantic_candidate": None,
        "search_capability_claim": "not_claimed",
        "no_candidate": False,
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
        extra["scenario_result"] = result
        assertions.append(
            {
                "name": "scenario_executed",
                "status": result,
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
    if (
        not isinstance(fixture_document, Mapping)
        or fixture_document.get("task_kind") != "diels_alder"
    ):
        print(
            "usage error: validate-da-ts requires the Diels-Alder v1 fixture-manifest.json",
            file=sys.stderr,
        )
        return EXIT_USAGE

    fixture_validation = _validate_da_fixture(fixture_path)
    extra["fixture_validation"] = fixture_validation
    assertions.append(
        {
            "name": "frozen_fixture_geometry_and_budget",
            "status": fixture_validation["status"],
            "detail": "; ".join(fixture_validation["contradictions"]) or None,
        }
    )
    if fixture_validation["status"] != "PASS":
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason=(
                "the frozen DA fixture is contradictory: "
                + "; ".join(fixture_validation["contradictions"])
            ),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    registry = _platform_registry_view(
        plan.get("platform_registry_path"),
        plan.get("platform_registry"),
        required_ids=DA_REQUIRED_REGISTRY_IDS,
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
            reason="registered DA run prerequisite missing: " + "; ".join(registry["reasons"]),
        )

    platform = snapshot_payload.get("platform")
    binding = platform.get("binding") if isinstance(platform, Mapping) else None
    binding_attempt = binding.get("attempt") if isinstance(binding, Mapping) else None
    execution = _da_execution_provenance(platform, binding_attempt)
    extra["execution_provenance"] = execution
    extra["b20a_exec_artifact_id"] = execution.get("artifact_id")
    extra["b20a_exec_sub_deliverable"] = execution.get("sub_deliverable")
    extra["b20a_exec_semantic_candidate"] = execution.get("semantic_candidate")
    extra["b20a_ready_cited_as_execution_evidence"] = bool(
        execution.get("b20a_ready_cited_as_execution_evidence")
    )
    assertions.append(
        {
            "name": "b20a_exec_execution_evidence",
            "status": execution["status"],
            "detail": "; ".join(execution["contradictions"] or execution["reasons"]) or None,
        }
    )
    if execution["status"] == "FAIL":
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="B20a-exec provenance rejected: " + "; ".join(execution["contradictions"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if execution["status"] != "PASS":
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="B20a-exec execution evidence missing: " + "; ".join(execution["reasons"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    try:
        from cccp.analysis.research.artifact_manifest import (
            ArtifactSnapshotError,
            load_verified_snapshot,
        )
    except ImportError as exc:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason=f"CCCP science consumer unavailable: {type(exc).__name__}: {exc}",
        )
    try:
        snapshot, digests = load_verified_snapshot(snapshot_payload)
    except ArtifactSnapshotError as exc:
        extra["snapshot_error"] = {"type": type(exc).__name__, "message": str(exc)}
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

    fixture_science = fixture_validation.get("science_parameters") or {}
    fixture_state = fixture_science.get("electronic_state") or {}
    identity_contradictions: list[str] = []
    identity = snapshot.science_identity
    if identity.method != fixture_science.get("method"):
        identity_contradictions.append(
            f"snapshot method {identity.method!r} differs from the frozen fixture method "
            f"{fixture_science.get('method')!r}"
        )
    if identity.basis != fixture_science.get("basis"):
        identity_contradictions.append(
            f"snapshot basis {identity.basis!r} differs from the frozen fixture basis "
            f"{fixture_science.get('basis')!r}"
        )
    if identity.electronic_state.charge != fixture_state.get("charge"):
        identity_contradictions.append("snapshot charge differs from the frozen fixture state")
    if identity.electronic_state.multiplicity != fixture_state.get("target_multiplicity"):
        identity_contradictions.append(
            "snapshot multiplicity differs from the frozen fixture state"
        )
    if fixture_state.get("state_id") and (
        identity.electronic_state.state_id != fixture_state.get("state_id")
    ):
        identity_contradictions.append("snapshot state_id differs from the frozen fixture state")
    extra["snapshot_fixture_identity"] = {
        "status": "FAIL" if identity_contradictions else "PASS",
        "contradictions": identity_contradictions,
        "snapshot": identity.to_dict(),
        "fixture": fixture_science,
    }
    assertions.append(
        {
            "name": "snapshot_fixture_identity",
            "status": "FAIL" if identity_contradictions else "PASS",
            "detail": "; ".join(identity_contradictions) or None,
        }
    )
    if identity_contradictions:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="snapshot identity contradicts the frozen fixture: "
            + "; ".join(identity_contradictions),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    candidate_document, candidate_gaps, candidate_contradictions = _da_candidate_document(snapshot)
    if candidate_contradictions:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="candidate-selection artifact rejected: " + "; ".join(candidate_contradictions),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if candidate_document is None:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="candidate-selection evidence missing: " + "; ".join(candidate_gaps),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    candidate = _da_candidate_validation(candidate_document, fixture_document, binding_attempt)
    extra["candidate_selection"] = {
        key: value for key, value in candidate.items() if key != "raw_document"
    }
    extra["candidate_records"] = candidate["candidates"]
    extra["search_range"] = candidate["search_range"]
    extra["selected_candidate_id"] = candidate["selected_candidate_id"]
    extra["starting_structure_source"] = candidate["starting_structure_source"]
    extra["search_capability_claim"] = candidate["search_capability_claim"]
    assertions.append(
        {
            "name": "candidate_selection_reasons",
            "status": candidate["status"],
            "detail": "; ".join(candidate["contradictions"] or candidate["reasons"]) or None,
        }
    )
    if candidate["status"] == "FAIL":
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="candidate selection rejected: " + "; ".join(candidate["contradictions"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if candidate["status"] != "PASS":
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="candidate-selection evidence incomplete: " + "; ".join(candidate["reasons"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    selected = candidate["selected_candidate"]
    if selected is None:
        extra["no_candidate"] = True
        claim = {
            "claim_id": "c-ts-first-order-saddle-candidate",
            "status": "inconclusive",
            "reason": "missing_evidence",
            "statement": (
                "The B20a-exec candidate search/selection produced no qualified "
                "transition-state candidate."
            ),
            "selection_reason": candidate["selection_reason"],
            "candidate_reasons": candidate["candidates"],
            "constructed": constructed,
        }
        h4 = _da_h4_readiness(
            constructed=constructed,
            real_run=bool(extra["real_run"]),
            scenario_result="PASS",
            claim_status="inconclusive",
            connectivity_status="not_evaluated",
            selected_candidate_id=None,
        )
        extra["scientific_claim"] = claim
        extra["claims"] = [claim]
        extra["claim_status"] = "inconclusive"
        extra["connectivity_status"] = "not_evaluated"
        extra["h4"] = h4
        _print_da_ts_report(
            {
                "scenario_result": "PASS",
                "claim_status": "inconclusive",
                "h4": h4,
                "selected_candidate_id": None,
                "starting_structure_source": candidate["starting_structure_source"],
                "scientific_claim": claim,
                "connectivity_status": "not_evaluated",
            }
        )
        return finish(
            result="PASS",
            exit_code=EXIT_PASS,
            reason=(
                "B20a-exec validation completed with no qualified candidate; the honest "
                "scientific claim is inconclusive and H4 remains open"
            ),
            extra_json={
                "fixture-validation.json": fixture_validation,
                "candidate-selection.json": candidate["raw_document"],
                "scientific-claim.json": claim,
                "h4-readiness.json": h4,
                "artifact-digests.json": digest_table,
            },
            extra_artifacts=[
                entry
                for entry in (
                    _artifact_entry(artifact.resolved_path)
                    for artifact in snapshot.artifacts
                    if artifact.role in ("provenance", "optimized_structure")
                )
                if entry is not None
            ],
        )

    optimize_result, optimize_artifact, optimize_gaps, optimize_contradictions = _da_named_result(
        snapshot,
        candidate["provenance"].get("ts_optimize_provenance"),
        candidate["provenance"].get("ts_optimize_provenance_sha256"),
    )
    frequency_result, frequency_artifact, frequency_gaps, frequency_contradictions = (
        _da_named_result(
            snapshot,
            candidate["provenance"].get("ts_frequency_provenance"),
            candidate["provenance"].get("ts_frequency_provenance_sha256"),
        )
    )
    provenance_contradictions = optimize_contradictions + frequency_contradictions
    provenance_gaps = optimize_gaps + frequency_gaps
    assertions.append(
        {
            "name": "ts_provenance_artifacts",
            "status": (
                "FAIL"
                if provenance_contradictions
                else "NOT_VERIFIED"
                if provenance_gaps
                else "PASS"
            ),
            "detail": "; ".join(provenance_contradictions or provenance_gaps) or None,
        }
    )
    if provenance_contradictions:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="TS provenance artifact rejected: " + "; ".join(provenance_contradictions),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if provenance_gaps or optimize_result is None or frequency_result is None:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="TS provenance artifact missing: " + "; ".join(provenance_gaps),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if optimize_result.task is None or optimize_result.task.value != "optimize":
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="the named TS optimization artifact is not an 'optimize' TaskResult",
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if frequency_result.task is None or frequency_result.task.value != "frequency":
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="the named TS frequency artifact is not a 'frequency' TaskResult",
            extra_json={"fixture-validation.json": fixture_validation},
        )

    geometry_check = _da_optimized_geometry_check(snapshot, optimize_result)
    extra["ts_geometry"] = geometry_check
    assertions.append(
        {
            "name": "ts_geometry_identity",
            "status": geometry_check["status"],
            "detail": "; ".join(geometry_check["contradictions"] or geometry_check["gaps"]) or None,
        }
    )
    if geometry_check["contradictions"]:
        return finish(
            result="FAIL",
            exit_code=EXIT_FAIL,
            reason="TS geometry identity rejected: " + "; ".join(geometry_check["contradictions"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )
    if geometry_check["gaps"]:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason="TS geometry identity incomplete: " + "; ".join(geometry_check["gaps"]),
            extra_json={"fixture-validation.json": fixture_validation},
        )

    try:
        from cccp.analysis.research.validators import (
            validate_method_state_consistency,
            validate_transition_state,
            validate_ts_connectivity,
        )
    except ImportError as exc:
        return finish(
            result="NOT_VERIFIED",
            exit_code=EXIT_NOT_VERIFIED,
            reason=f"CCCP science validators unavailable: {type(exc).__name__}: {exc}",
        )

    transition_state = validate_transition_state(
        frequency_result,
        candidate["mapped_mode"],
        optimize_result=optimize_result,
    )
    method_state = validate_method_state_consistency(optimize_result, frequency_result)
    connectivity = validate_ts_connectivity(transition_state)
    identity_check = _science_identity_consistency(snapshot, optimize_result, frequency_result)
    claims = [claim.to_dict() for claim in transition_state.claims]
    claims.extend(claim.to_dict() for claim in method_state.claims)
    claims.append(connectivity.to_dict())
    claim_status = transition_state.status
    if identity_check["contradictions"] or method_state.status == "refuted":
        claim_status = "refuted"
    elif transition_state.status in ("inconclusive", "not_evaluated"):
        claim_status = transition_state.status
    elif method_state.status != "supported" or identity_check["gaps"]:
        claim_status = "inconclusive"
    scientific_claim = {
        "claim_id": transition_state.candidate.claim_id,
        "status": claim_status,
        "reason": transition_state.candidate.reason,
        "statement": transition_state.candidate.statement,
        "classification": transition_state.classification,
        "imaginary_mode_count": transition_state.imaginary_mode_count,
        "mode_count": transition_state.mode_count,
        "mapped_mode_index": transition_state.mapped_mode_index,
        "validator_version": transition_state.to_dict()["validator_version"],
        "candidate_selection_reason": candidate["selection_reason"],
        "method_state_status": method_state.status,
        "connectivity_status": connectivity.status,
        "identity_check": identity_check,
        "constructed": constructed,
    }
    h4 = _da_h4_readiness(
        constructed=constructed,
        real_run=bool(extra["real_run"]),
        scenario_result="PASS",
        claim_status=claim_status,
        connectivity_status=connectivity.status,
        selected_candidate_id=candidate["selected_candidate_id"],
    )
    extra["scientific_claim"] = scientific_claim
    extra["claims"] = claims
    extra["methods_state"] = method_state.to_dict()
    extra["connectivity_status"] = connectivity.status
    extra["claim_status"] = claim_status
    extra["h4"] = h4
    _print_da_ts_report(
        {
            "scenario_result": "PASS",
            "claim_status": claim_status,
            "h4": h4,
            "selected_candidate_id": candidate["selected_candidate_id"],
            "starting_structure_source": candidate["starting_structure_source"],
            "scientific_claim": scientific_claim,
            "connectivity_status": connectivity.status,
        }
    )
    if claim_status == "refuted":
        refutation_details = (
            identity_check["contradictions"]
            or list(method_state.to_dict().get("notes") or ())
            or list(transition_state.candidate.notes)
            or ["the TS candidate evidence is contradictory"]
        )
        reason = "B20a-exec TS validation completed and refuted the candidate: " + "; ".join(
            str(item) for item in refutation_details
        )
    elif claim_status == "supported":
        reason = (
            "B20a-exec TS validation completed: the saddle candidate is supported; "
            "connectivity stays inconclusive without IRC and H4 remains open"
        )
    else:
        reason = (
            f"B20a-exec TS validation completed: the saddle claim is {claim_status}; "
            "H4 remains open"
        )
    return finish(
        result="PASS",
        exit_code=EXIT_PASS,
        reason=reason,
        extra_json={
            "fixture-validation.json": fixture_validation,
            "candidate-selection.json": candidate["raw_document"],
            "ts-validation-report.json": transition_state.to_dict(),
            "scientific-claim.json": scientific_claim,
            "h4-readiness.json": h4,
            "artifact-digests.json": digest_table,
        },
        extra_artifacts=[
            entry
            for entry in (
                _artifact_entry(artifact.resolved_path)
                for artifact in (snapshot.artifacts)
                if artifact.role in ("provenance", "optimized_structure")
            )
            if entry is not None
        ],
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
    if spec.alias == DA_TS_ALIAS:
        return _execute_validate_da_ts(args, spec, plan, started_at=started_at, argv=argv)

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
            "  validate-artifacts     --input-manifest + --platform-registry",
            "                         (dsh-optfreq-live)",
            "  validate-da-ts         --input-manifest + --platform-registry",
            "                         (da-runtime, da-research-live)",
            "  validate-da-irc        --input-manifest",
            "  validate-da-report     --input-manifest",
            "Optional flags:",
            "  --timeout   positive seconds (default 600)",
            "  --platform-registry  platform-provided registry record; a real",
            "                       validate-artifacts PASS needs the registered",
            f"                       id {REGISTERED_OPT_FREQ_RUN_ID!r}; a real",
            "                       validate-da-ts PASS needs the registered ids",
            f"                       {DA_RUNTIME_RUN_ID!r} and {DA_RESEARCH_LIVE_RUN_ID!r}",
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
            "  acp2_science.py --scenario science:validate-da-ts --fixture"
            " diels_alder/v1/fixture-manifest.json --input-manifest b20a-exec.json"
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
            f"ids {REGISTERED_OPT_FREQ_RUN_ID!r}, {DA_RUNTIME_RUN_ID!r} and "
            f"{DA_RESEARCH_LIVE_RUN_ID!r}; absent => NOT_VERIFIED"
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
