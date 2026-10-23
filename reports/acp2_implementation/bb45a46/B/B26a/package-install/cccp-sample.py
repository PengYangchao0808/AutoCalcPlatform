from __future__ import annotations

import json
import sys


def main() -> int:
    import cccp
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
    from cccp.calculation.tasks.singlepoint import run_singlepoint
    from cccp.qc.interfaces.base import QCResult

    class ControlledBackend:
        name = "orca"

        def single_point(self, coordinates, symbols, **kwargs):
            assert kwargs["method"] == "HF"
            return QCResult(success=True, energy=-1.0, symbols=list(symbols), converged=True)

    result = run_singlepoint(
        TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("He",)),
            level=MethodSpec(method="HF", basis="def2-SVP"),
        ),
        context=TaskContext(backend=ControlledBackend()),
    )
    assert result.complete and result.energy_hartree == -1.0
    assert "acp" not in sys.modules and "acp_services" not in sys.modules
    print(json.dumps({"complete": result.complete, "energy": result.energy_hartree,
                      "cccp": cccp.__file__, "version": cccp.__version__}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
