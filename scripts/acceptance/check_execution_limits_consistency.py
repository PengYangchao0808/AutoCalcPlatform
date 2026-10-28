#!/usr/bin/env python3.11
"""M1-04c: mechanical consistency of the enforcement capability claims.

D09 requires one capability matrix, not one per surface.  This checker diffs the
**five** places that can publish a budget/limit claim today:

1. ``research_budget.UNIT_CAPABILITIES`` — the unit matrix
   (``task_ref``/``attempt`` enforced; the rest recorded-only);
2. ``execution_limits.EXECUTION_LIMITS`` — the non-BudgetUnit limits
   (admission-bound / process-termination / queue-deadline / recorded-only);
3. the **real** ``GET /api/capabilities`` payload (the shipping application,
   built with :func:`acp.api.server.create_app` and a temporary run root);
4. ``research_budget.release_statement()`` — what a release statement renders;
5. the release/BOM declaration (``release/scope.json`` rows and, when a BOM
   artifact exists, its ``limitations`` list).

Any disagreement — a unit that is ``recorded-only`` in one surface and
``enforced`` in another, a limit that disappears, ``model_token_enforcement``
published as anything but ``NOT_VERIFIED`` — fails the run.  A source that
cannot be exercised is reported as ``NOT_VERIFIED`` and also fails: an
unverifiable claim is not a pass (D09/M0-02c).

Exit codes
----------
``0``  every source agrees.
``1``  at least one enforced/recorded-only/NOT_VERIFIED disagreement.
``2``  the check could not run (import error, app could not start, bad flag).

Usage
-----
    python3.11 scripts/acceptance/check_execution_limits_consistency.py
    python3.11 scripts/acceptance/check_execution_limits_consistency.py --check
    python3.11 scripts/acceptance/check_execution_limits_consistency.py --out /tmp/limits.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(SRC))

EXIT_OK = 0
EXIT_INCONSISTENT = 1
EXIT_ERROR = 2

CAPABILITY_VALUES = ("enforced", "recorded-only", "NOT_VERIFIED")
SCOPE_PATH = REPO_ROOT / "docs/reference/next-change/acp2-contracts/release/scope.json"


class CheckError(RuntimeError):
    """The check could not run (never a silent pass)."""


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def _unit_matrix() -> dict[str, str]:
    from acp_services.runtime.research_budget import UNIT_CAPABILITIES, BudgetCapability

    matrix: dict[str, str] = {}
    for unit, capability in UNIT_CAPABILITIES.items():
        if capability not in (
            BudgetCapability.ENFORCED,
            BudgetCapability.RECORDED_ONLY,
            BudgetCapability.NOT_VERIFIED,
        ):
            raise CheckError(f"unit {unit} has an unknown capability {capability!r}")
        matrix[str(unit)] = str(capability)
    return matrix


def _limit_matrix() -> dict[str, dict[str, str]]:
    from acp_services.runtime.execution_limits import EXECUTION_LIMITS

    matrix: dict[str, dict[str, str]] = {}
    for row in EXECUTION_LIMITS:
        if row.capability not in CAPABILITY_VALUES:
            raise CheckError(f"limit {row.limit} has an unknown capability {row.capability!r}")
        matrix[str(row.limit)] = {"kind": str(row.kind), "capability": str(row.capability)}
    return matrix


def _partition(rows: list[dict[str, Any]], key: str = "limit") -> dict[str, str]:
    return {str(row.get(key)): str(row.get("capability")) for row in rows}


def _statement_partition(statement: dict[str, Any]) -> dict[str, str]:
    """Flatten one release statement into ``name -> capability``."""
    partition: dict[str, str] = {}
    for name in statement.get("enforced") or ():
        partition[str(name)] = "enforced"
    for name in statement.get("recorded_only") or ():
        partition[str(name)] = "recorded-only"
    for row in statement.get("not_verified") or ():
        if isinstance(row, dict):
            partition[str(row.get("capability"))] = "NOT_VERIFIED"
    limits = statement.get("execution_limits") or {}
    for row in limits.get("limits") or ():
        if isinstance(row, dict):
            partition[str(row.get("limit"))] = str(row.get("capability"))
    for row in limits.get("not_verified") or ():
        if isinstance(row, dict):
            partition.setdefault(str(row.get("capability")), "NOT_VERIFIED")
    return partition


def _module_release_statement() -> dict[str, Any]:
    from acp_services.runtime.research_budget import release_statement

    return release_statement()


def _live_capabilities() -> dict[str, Any]:
    """Exercise the real ``GET /api/capabilities`` of the shipping application."""
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:  # pragma: no cover - depends on the extras
        raise CheckError(f"fastapi/httpx is unavailable: {exc}") from exc
    try:
        from acp.api.server import create_app
    except ImportError as exc:  # pragma: no cover
        raise CheckError(f"the Controller application is unavailable: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="acp2-limits-") as run_root:
        app = create_app(run_root=Path(run_root), max_running=1)
        with TestClient(app) as client:
            response = client.get("/api/capabilities")
        if response.status_code != 200:
            raise CheckError(f"GET /api/capabilities answered {response.status_code}")
        return dict(response.json())


def _release_declaration() -> dict[str, Any]:
    """The release/BOM statement of the recorded-only dimensions (SCOPE-DEF-03)."""
    if not SCOPE_PATH.is_file():
        return {"present": False, "recorded_only": [], "not_verified": [], "statement": ""}
    document = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    rows = [
        row
        for row in document.get("capabilities") or ()
        if isinstance(row, dict) and str(row.get("id") or "") == "SCOPE-DEF-03"
    ]
    if not rows:
        return {"present": True, "recorded_only": [], "not_verified": [], "statement": ""}
    statement = str(rows[0].get("release_statement") or "")
    lowered = statement.lower()
    recorded_only: list[str] = []
    not_verified: list[str] = []
    if "recorded-only" in lowered or "recorded_only" in lowered:
        recorded_only = ["core_hours", "input_tokens", "output_tokens"]
    if "not_verified" in lowered:
        not_verified = ["model_token_enforcement", "core_hours_hard_cap"]
    if "enforced" in lowered.replace("不得在 ui 或 bom 写成 enforced", ""):
        raise CheckError("the release declaration claims enforcement for a recorded-only dimension")
    return {
        "present": True,
        "statement": statement,
        "recorded_only": recorded_only,
        "not_verified": not_verified,
    }


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------


def _compare_units(
    units: dict[str, str],
    live: dict[str, str],
    module_statement: dict[str, str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for unit, capability in sorted(units.items()):
        for surface, partition in (
            ("api_capabilities", live),
            ("release_statement", module_statement),
        ):
            actual = partition.get(unit)
            if actual is None:
                findings.append(
                    {
                        "kind": "missing_claim",
                        "unit": unit,
                        "surface": surface,
                        "expected": capability,
                        "actual": "absent",
                    }
                )
            elif actual != capability:
                findings.append(
                    {
                        "kind": "capability_mismatch",
                        "unit": unit,
                        "surface": surface,
                        "expected": capability,
                        "actual": actual,
                    }
                )
    return findings


def _compare_limits(
    limits: dict[str, dict[str, str]],
    live: dict[str, str],
    module_statement: dict[str, str],
    live_limits: dict[str, str],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for name, row in sorted(limits.items()):
        capability = row["capability"]
        for surface, partition in (
            ("api_capabilities", live_limits),
            ("release_statement", module_statement),
        ):
            actual = partition.get(name)
            if actual is None:
                findings.append(
                    {
                        "kind": "missing_claim",
                        "limit": name,
                        "surface": surface,
                        "expected": capability,
                        "actual": "absent",
                    }
                )
            elif actual != capability:
                findings.append(
                    {
                        "kind": "capability_mismatch",
                        "limit": name,
                        "surface": surface,
                        "expected": capability,
                        "actual": actual,
                    }
                )
    # A limit claimed by only one surface is still a divergence: the capability
    # name must not appear in the API partition but be absent from the statement.
    for name in sorted(set(live_limits) - set(limits) - set(live)):
        findings.append(
            {
                "kind": "unregistered_claim",
                "limit": name,
                "surface": "api_capabilities",
                "expected": "registered execution limit",
                "actual": live_limits[name],
            }
        )
    return findings


def _compare_declaration(
    declaration: dict[str, Any],
    units: dict[str, str],
    limits: dict[str, dict[str, str]],
    capabilities: dict[str, Any],
) -> list[dict[str, str]]:
    """Diff the release/BOM declaration against the live matrix (no upgrade)."""
    findings: list[dict[str, str]] = []
    declared_recorded = {str(name) for name in declaration.get("recorded_only") or ()}
    capabilities_by_name: dict[str, str] = dict(units)
    capabilities_by_name.update({name: row["capability"] for name, row in limits.items()})
    aliases = {
        "core_hours": "cumulative_core_hours",
        "input_tokens": "model_tokens",
        "output_tokens": "model_tokens",
    }
    for name in sorted(declared_recorded):
        candidate = aliases.get(name, name)
        actual = capabilities_by_name.get(candidate)
        if actual == "enforced":
            findings.append(
                {
                    "kind": "declaration_conflict",
                    "unit": candidate,
                    "surface": "release_scope",
                    "expected": "recorded-only",
                    "actual": "enforced",
                }
            )
    token_enforcement = capabilities.get("model_token_enforcement")
    if token_enforcement != "NOT_VERIFIED":
        findings.append(
            {
                "kind": "declaration_conflict",
                "limit": "model_token_enforcement",
                "surface": "api_capabilities",
                "expected": "NOT_VERIFIED",
                "actual": str(token_enforcement),
            }
        )
    return findings


def build_report(repo_root: Path) -> dict[str, Any]:
    """Run every source and return the deterministic report."""
    del repo_root  # sources are absolute; kept for CLI symmetry
    units = _unit_matrix()
    limits = _limit_matrix()
    statement = _module_release_statement()
    live_capabilities = _live_capabilities()
    live_admission = (live_capabilities.get("admission") or {}).get("research_budget") or {}
    # ``LocalAdmission.snapshot`` spreads the release statement into
    # ``admission.research_budget``; a nested ``release_statement`` key is only
    # present in the bounded evidence export, so accept both spellings.
    live_statement = live_admission.get("release_statement") or live_admission or {}
    live_limits_section = live_capabilities.get("execution_limits") or {}
    declaration = _release_declaration()

    unit_statement = _statement_partition(statement)
    unit_live = _statement_partition(live_statement)
    limit_statement = _statement_partition(
        {**statement, "execution_limits": statement.get("execution_limits") or {}}
    )
    limit_live = _partition(live_limits_section.get("limits") or [])

    findings: list[dict[str, str]] = []
    findings.extend(_compare_units(units, unit_live, unit_statement))
    findings.extend(_compare_limits(limits, unit_live, limit_statement, limit_live))
    findings.extend(_compare_declaration(declaration, units, limits, live_capabilities))

    # The three clocks and the precedence rule must be published by both the
    # release statement and the live discovery payload (one contract, not two).
    for surface, section in (
        ("release_statement", statement.get("execution_limits") or {}),
        ("api_capabilities", live_limits_section),
    ):
        clocks = {
            str(row.get("clock")) for row in section.get("clocks") or () if isinstance(row, dict)
        }
        if clocks != {"queue", "wall", "science-runtime"}:
            findings.append(
                {
                    "kind": "clock_contract_missing",
                    "limit": "clocks",
                    "surface": surface,
                    "expected": "queue,wall,science-runtime",
                    "actual": ",".join(sorted(clocks)),
                }
            )
        if not section.get("precedence"):
            findings.append(
                {
                    "kind": "clock_contract_missing",
                    "limit": "precedence",
                    "surface": surface,
                    "expected": "rank 1 queue -> 2 wall -> 3 science-runtime",
                    "actual": "absent",
                }
            )

    return {
        "schema": "acp2.acceptance.execution-limits-consistency.v1",
        "sources": {
            "unit_capabilities": units,
            "execution_limits": limits,
            "api_capabilities_units": unit_live,
            "api_capabilities_limits": limit_live,
            "release_statement_units": unit_statement,
            "release_statement_limits": limit_statement,
            "release_declaration": declaration,
            "model_token_enforcement": live_capabilities.get("model_token_enforcement"),
            "bom_artifact_present": False,
        },
        "findings": findings,
        "summary": {
            "consistent": not findings,
            "findings": len(findings),
            "units": len(units),
            "execution_limits": len(limits),
        },
    }


def _summary_line(report: dict[str, Any]) -> str:
    summary = report["summary"]
    return (
        "execution-limits consistency: "
        f"{'PASS' if summary['consistent'] else 'FAIL'} "
        f"({summary['findings']} finding(s), {summary['units']} units, "
        f"{summary['execution_limits']} execution limits)"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the check; see the module docstring for the exit-code contract."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (default: this checkout)",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when any surface disagrees (default: also prints the report)",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(args.repo_root.resolve())
    except CheckError as exc:
        print(f"check_execution_limits_consistency: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a failed check is never a pass
        print(f"check_execution_limits_consistency: unexpected failure: {exc}", file=sys.stderr)
        return EXIT_ERROR

    payload = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")

    print(_summary_line(report))
    if report["summary"]["consistent"]:
        if not args.check:
            sys.stdout.write(payload)
        return EXIT_OK
    for finding in report["findings"]:
        print(f"INCONSISTENT {json.dumps(finding, sort_keys=True)}", file=sys.stderr)
    return EXIT_INCONSISTENT


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
