#!/usr/bin/env python3
"""Build, bind and preflight a single-point request with the public CCCP API.

This example is software-free on its documented path: it builds a
:class:`cccp.calculation.requests.TaskRequest`, binds it to the registered
``electronic.energy`` capability, runs the side-effect-free scientific
preflight and prints the resulting plan.  No subprocess, network, plugin load
or artifact write happens.

``--execute`` is the explicit opt-in execution probe.  When no ORCA path is
supplied it pins ``executables.orca.path`` to a nonexistent file, so the
missing runtime is reported as the typed
``cccp.calculation.errors.BackendUnavailableError`` instead of silently
running something else (on some Linux hosts ``/usr/bin/orca`` is the GNOME
screen reader).  Pass ``--orca-path /real/path/to/orca`` to execute against a
real installation; that path is NOT_VERIFIED by this guide.

Usage:
    python singlepoint_public_api.py
    python singlepoint_public_api.py --execute
    python singlepoint_public_api.py --execute --orca-path /opt/orca/orca
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from cccp.calculation.capabilities.task_binding import bind_capability
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import StructureRole
from cccp.calculation.errors import BackendUnavailableError
from cccp.calculation.requests import (
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
)

#: Inline water geometry in angstrom (no input file, no repository path).
WATER_SYMBOLS: tuple[str, ...] = ("O", "H", "H")
WATER_COORDINATES: tuple[tuple[float, float, float], ...] = (
    (0.000000, 0.000000, 0.000000),
    (0.000000, 0.000000, 0.960000),
    (0.930000, 0.000000, -0.250000),
)

#: The deterministic no-software pin used by ``--execute`` without a path.
NO_SOFTWARE_PIN = "/nonexistent/orca"


def build_request() -> TaskRequest:
    """Return the example request: one water single point, in memory."""
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(
            coordinates=WATER_COORDINATES,
            symbols=WATER_SYMBOLS,
            role=StructureRole.MINIMUM,
            source="inline_example",
        ),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="r2SCAN-3c", basis="def2-SVP"),
        resources=TaskResources(nproc=1, mem="1GB"),
    )


def _plan_document(binding: Any) -> dict[str, Any]:
    request = binding.request
    preflight = binding.preflight()
    return {
        "example": "singlepoint_public_api",
        "execute": False,
        "binding_id": binding.binding_id,
        "capability_id": binding.capability_id,
        "implementation_id": binding.implementation_id,
        "delegation_target": binding.delegation_target,
        "dispatchable": binding.dispatchable,
        "request": request.to_dict() if request is not None else None,
        "preflight_outcome": preflight.outcome.value,
        "preflight_ready": preflight.ready,
        "preflight_checks": [check.to_dict() for check in preflight.checks],
    }


def main(argv: list[str] | None = None) -> int:
    """Print the plan; optionally attempt execution.  Return a process code."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a public-API singlepoint request, bind electronic.energy and "
            "print the preflight plan (software-free)."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "attempt execution; without --orca-path the runtime is pinned to "
            f"{NO_SOFTWARE_PIN} so the typed unavailable error is shown"
        ),
    )
    parser.add_argument(
        "--orca-path",
        default=None,
        help="explicit ORCA executable path used with --execute (default: no-software pin)",
    )
    args = parser.parse_args(argv)

    binding = bind_capability("electronic.energy", build_request())
    document = _plan_document(binding)
    if not args.execute:
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0

    orca_path = args.orca_path or NO_SOFTWARE_PIN
    document["execute"] = True
    document["orca_path"] = orca_path
    context = TaskContext(config={"executables": {"orca": {"path": orca_path}}})
    try:
        result = binding.execute(context=context)
    except BackendUnavailableError as exc:
        document["execution"] = {
            "attempted": True,
            "outcome": "unavailable",
            "error_class": type(exc).__name__,
            "error_module": type(exc).__module__,
            "message": str(exc),
        }
        print(json.dumps(document, indent=2, sort_keys=True))
        print(
            f"ERROR: {type(exc).__module__}.{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    document["execution"] = {
        "attempted": True,
        "outcome": "completed",
        "task_status": getattr(result, "status", None),
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
