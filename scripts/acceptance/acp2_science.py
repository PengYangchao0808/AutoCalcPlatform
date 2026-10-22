#!/usr/bin/env python3
"""ACP2 science acceptance harness skeleton (A-10a).

This is **not** a production CLI and it never starts a controller, a
scheduler, a job, a native work root or a QC subprocess.  It exposes the
scenario/flag contract for the science track, a path/budget ``--dry-run`` and
the project's ``0=PASS, 1=FAIL, 2=NOT_VERIFIED, 3=usage`` exit-code contract.

The single receipt authority is B01's ``acceptance-result.schema.json``
(``acp2.acceptance.v1``).  While that contract is unpublished the harness
writes a *provisional* A-internal record under ``extensions.science`` and
records ``receipt_protocol_gap = NOT_VERIFIED``.  It does not define a second
receipt schema, copy an unpublished sibling schema, or invent valid/invalid
samples.  ``--help`` and ``--dry-run`` exit zero for their own success but
never produce a scenario ``PASS``.

Scenarios (canonical ``science:*`` id and short CLI alias are interchangeable):

``science:installed-singlepoint`` / ``installed-singlepoint``
    Standalone singlepoint through the public CCCP API (execution is A-10b).
``science:validate-artifacts`` / ``validate-artifacts``
    Validate authorized optimize/frequency artifacts from a prepared manifest.
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
from dataclasses import dataclass
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


def _budget_report(run_spec: dict[str, Any]) -> dict[str, Any]:
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
        "exhausted": [],
    }
    budget = run_spec.get("budget")
    if not isinstance(budget, dict):
        report["status"] = "invalid"
        report["gaps"].append("budget object required")
        return report

    caps = budget.get("caps")
    if not isinstance(caps, dict):
        report["gaps"].append("budget.caps object required")
    else:
        for name in ("total", "phase", "scenario", "per_unit"):
            if name not in caps:
                report["gaps"].append(f"budget.caps.{name} required")
                continue
            limit, used = _cap_pair(caps[name])
            if limit is None or used is None:
                report["gaps"].append(f"budget.caps.{name} must be a non-negative int")
                continue
            report["caps"][name] = {"limit": limit, "used": used}
            if limit <= 0:
                report["exhausted"].append(f"cap {name} disabled (limit={limit})")
            elif used >= limit:
                report["exhausted"].append(f"cap {name} exhausted ({used}/{limit})")

    counts = budget.get("counts")
    required_units = ("task_ref", "recipe_step", "qc_invocation", "attempt")
    active_units = list(required_units)
    if scheduler in ("lsf", "remote"):
        active_units.append("bsub")
    if not isinstance(counts, dict):
        report["gaps"].append("budget.counts object required")
    else:
        for name in ("task_ref", "recipe_step", "qc_invocation", "attempt", "bsub"):
            if name not in counts:
                report["gaps"].append(f"budget.counts.{name} required")
                continue
            limit, used = _cap_pair(counts[name])
            if limit is None or used is None:
                report["gaps"].append(f"budget.counts.{name} must be a non-negative int")
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
    else:
        for name in ("queue_s", "compute_s", "client_s", "total_s"):
            value = deadlines.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or not math.isfinite(value)
            ):
                report["gaps"].append(f"budget.deadlines.{name} must be a non-negative number")
                continue
            report["deadlines"][name] = value

    if report["gaps"]:
        report["status"] = "invalid"
    elif report["exhausted"]:
        report["status"] = "exhausted"
    return report


def _validate_run_spec(run_spec: Any) -> list[str]:
    if not isinstance(run_spec, dict):
        return ["run spec must be a JSON object"]
    gaps: list[str] = []
    for key in ("run_key", "interpreter", "native_work_root"):
        value = run_spec.get(key)
        if not isinstance(value, str) or not value.strip():
            gaps.append(f"{key} must be a non-empty string")
    native_root = run_spec.get("native_work_root")
    if isinstance(native_root, str) and native_root and not Path(native_root).is_absolute():
        gaps.append("native_work_root must be an absolute path")
    software = run_spec.get("software")
    if not isinstance(software, dict):
        gaps.append("software object required")
    else:
        for key in ("name", "version", "executable"):
            value = software.get(key)
            if not isinstance(value, str) or not value.strip():
                gaps.append(f"software.{key} must be a non-empty string")
    digest = run_spec.get("source_digest") or run_spec.get("wheel_digest")
    if not isinstance(digest, str) or not _is_digest(digest):
        gaps.append("source_digest or wheel_digest must be sha256:<64 lowercase hex>")
    if not isinstance(run_spec.get("budget"), dict):
        gaps.append("budget object required")
    return gaps


def _is_digest(value: str) -> bool:
    return re.fullmatch(_DIGEST_PATTERN, value) is not None


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
        "input_manifest": None,
        "input_manifest_path": None,
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
                for gap in _validate_run_spec(document):
                    plan["errors"].append({"kind": "invalid", "label": "run_spec", "message": gap})
                if not plan["errors"]:
                    plan["budget"] = _budget_report(document)
            elif document is not None:
                plan["errors"].append(
                    {
                        "kind": "invalid",
                        "label": "run_spec",
                        "message": "run spec must be a JSON object",
                    }
                )

    if spec.requires_input_manifest:
        manifest_path = Path(args.input_manifest)
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
            "detail": "A-10a is a harness skeleton; no scenario is executed.",
        }
    )
    return assertions


def _write_result(out: Path, record: dict[str, Any], argv_document: dict[str, Any]) -> None:
    """Write ``result.json`` and ``argv.json`` without overwriting a run."""
    if out.exists():
        if not out.is_dir():
            raise _InvalidInputError(f"--out exists and is not a directory: {out}")
        if any(out.iterdir()):
            raise _InvalidInputError(f"refusing to overwrite a non-empty evidence directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    _atomic_json(out / "result.json", record)
    _atomic_json(out / "argv.json", argv_document)


def _finish_not_verified(
    *,
    args: argparse.Namespace,
    spec: ScenarioSpec,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    reason: str,
    started_at: str,
    argv: list[str],
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
    assertions = _assertions(plan, receipt)
    finished_at = _utc_now()
    extensions = {
        "science": {
            "contract_version": CONTRACT_VERSION,
            "harness": _harness_relpath(),
            "harness_sha256": _sha256(SCRIPT_PATH),
            "receipt_protocol_gap": ("NOT_VERIFIED" if receipt["status"] != "AVAILABLE" else None),
            "receipt_protocol": receipt,
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
                "A-10a is a harness skeleton: no scenario is executed and no PASS is written.",
                "No controller, scheduler, job, native work root or QC subprocess is started.",
                "B01 acceptance-result.schema.json / acp2.acceptance.v1 is not published; "
                "this record is provisional and is not receipt-validated.",
                "No B01 valid/invalid sample exists, so none is copied or invented.",
            ],
        }
    }
    record = {
        "record_state": RECORD_STATE,
        "owner": "A",
        "scenario": spec.canonical,
        "scenario_alias": spec.alias,
        "phase": spec.phase,
        "run_id": Path(args.out).name,
        "result": "NOT_VERIFIED",
        "scope": "IN_SCOPE",
        "reason": reason,
        "command": _invocation(argv),
        "cwd": str(Path.cwd()),
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": EXIT_NOT_VERIFIED,
        "artifacts": artifacts,
        "extensions": extensions,
    }
    argv_document = {
        "argv": _invocation(argv),
        "cwd": str(Path.cwd()),
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": EXIT_NOT_VERIFIED,
    }
    try:
        _write_result(Path(args.out), record, argv_document)
    except _InvalidInputError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"NOT_VERIFIED: {reason}", file=sys.stderr)
    return EXIT_NOT_VERIFIED


def _invocation(argv: list[str]) -> list[str]:
    return [sys.executable, str(SCRIPT_PATH), *argv]


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
        return _finish_not_verified(
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
        print(
            "usage error: " + "; ".join(budget["gaps"]),
            file=sys.stderr,
        )
        return EXIT_USAGE
    if budget is not None and budget["status"] == "exhausted":
        return _finish_not_verified(
            args=args,
            spec=spec,
            plan=plan,
            receipt=_receipt_protocol(),
            reason="budget refused before submission: " + "; ".join(budget["exhausted"]),
            started_at=started_at,
            argv=argv,
        )

    receipt = _receipt_protocol()
    if receipt["status"] != "AVAILABLE":
        reason = (
            f"{receipt['reason']} Scenario '{spec.canonical}' requires the shared "
            "receipt; execution is deferred to A-10b (todo 21) and no PASS is written."
        )
        return _finish_not_verified(
            args=args,
            spec=spec,
            plan=plan,
            receipt=receipt,
            reason=reason,
            started_at=started_at,
            argv=argv,
        )

    return _finish_not_verified(
        args=args,
        spec=spec,
        plan=plan,
        receipt=receipt,
        reason=(
            "scenario implementation is delivered by A-10b (todo 21); "
            "A-10a does not execute QC and writes no PASS"
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
    document = {
        "dry_run": True,
        "scenario": spec.canonical,
        "scenario_alias": spec.alias,
        "phase": spec.phase,
        "required_flags": _required_flags(spec),
        "paths": plan["paths"],
        "run_spec": {
            "path": plan["run_spec_path"],
            "errors": [error for error in plan["errors"] if error["label"] == "run_spec"],
        },
        "budget": plan["budget"],
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
    if spec.requires_input_manifest and not args.input_manifest:
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
            " fixture-manifest.json --input-manifest authorized-artifacts.json --out evidence/",
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
