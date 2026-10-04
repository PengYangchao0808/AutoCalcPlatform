"""A7 goldens equivalence — accumulated from plan todo 17 onward.

Group ①/③ of the A7 matrix for ``singlepoint``: an independent cccp call's
effective translation parameters must be identical to the **pre-migration**
goldens in ``tests/baseline/cccp_calculation_goldens/`` (frozen record — a
backfilled golden would carry a post-migration commit).  Future todos append
their capability cases here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cccp.calculation._common import level_explicit_fields, render_backend_input, resolve_spec
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
from cccp.calculation.tasks.singlepoint import run_singlepoint

GOLDENS_DIR = Path(__file__).resolve().parent / "baseline" / "cccp_calculation_goldens"

#: Pre-migration generation commit of the frozen goldens (anti-backfill pin).
GOLDENS_SOURCE_COMMIT = "f1c49a148ff85c4fd4ff7167dd7fdeb64e5c434f"


class _CaptureBackend:
    name = "orca"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def single_point(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        from cccp.qc.interfaces.base import QCResult

        self.calls.append({"symbols": list(symbols), "kwargs": dict(kwargs)})
        return QCResult(success=True, energy=-1.0, symbols=list(symbols), converged=True)


def _load(name: str) -> dict[str, Any]:
    return json.loads((GOLDENS_DIR / name).read_text(encoding="utf-8"))


def _extract_fields(text: str) -> dict[str, Any]:
    """Deterministic extraction of the translated fields (mirrors the generator)."""
    lines = text.splitlines()
    route_line = next((ln for ln in lines if ln.startswith("!")), "")

    def _block(name: str) -> list[str]:
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == name)
        except StopIteration:
            return []
        out: list[str] = []
        for ln in lines[start + 1 :]:
            if ln.strip() == "end":
                break
            out.append(ln.strip())
        return out

    basis_block = _block("%basis")
    aux_j = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxJ")), None)
    aux_c = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxC")), None)
    inline_basis = next(
        (ln.split('"')[1] for ln in basis_block if ln.startswith("basis ")), None
    )
    return {
        "route_line": route_line,
        "route_tokens": route_line.lstrip("! ").split(),
        "basis_inline": inline_basis,
        "aux_j": aux_j,
        "aux_c": aux_c,
        "solvent_block": _block("%cpcm"),
        "scf_block": _block("%scf"),
    }


def test_goldens_are_pre_migration_and_non_empty() -> None:
    manifest = _load("manifest.json")
    assert manifest["schema"] == "cccp_calculation_goldens_manifest_v1"
    assert manifest["source_commit"] == GOLDENS_SOURCE_COMMIT
    routes = _load("orca_routes.json")
    assert routes["cases"], "goldens must be non-empty"
    assert any(case["input_params"]["calc_type"] == "sp" for case in routes["cases"])


def test_singlepoint_effective_params_match_goldens() -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    routes = _load("orca_routes.json")
    config: dict[str, Any] = {
        "executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}},
        "resources": {"mem": "8GB", "nproc": 4},
    }
    for case in routes["cases"]:
        params = case["input_params"]
        if params["calc_type"] != "sp":
            continue
        level = MethodSpec(
            method=params["method"],
            basis=params.get("basis") or "",
            dispersion=params.get("dispersion"),
            solvent=params.get("solvent"),
            solvent_model=params.get("solvent_model"),
            integration_grid=params.get("grid"),
            auxiliary_basis_j=params.get("aux_j_basis"),
            auxiliary_basis_c=params.get("aux_c_basis"),
        )
        symbols = list(params["symbols"])
        request = TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(
                coordinates=tuple((0.0, float(i), 0.0) for i in range(len(symbols))),
                symbols=tuple(symbols),
            ),
            level=level,
        )
        backend = _CaptureBackend()
        result = run_singlepoint(request, context=TaskContext(backend=backend))
        assert result.status == "completed", case["id"]
        assert backend.calls, case["id"]

        spec = resolve_spec(level.method or None, explicit=level_explicit_fields(level))
        rendered = render_backend_input(spec, method=level.method or None)
        call_kwargs = dict(rendered)
        interface = ORCAInterface(
            dict(config),
            method=str(call_kwargs.pop("method") or params["method"]),
            basis=str(call_kwargs.pop("basis", None) or "") or params.get("basis") or "",
        )
        text, _resolution = interface._build_input_blocks(
            calc_type="sp",
            symbols=symbols,
            recalc_hess=None,
            **call_kwargs,
        )
        assert text == case["rendered_input"], f"rendered input drifted for {case['id']}"
        extracted = _extract_fields(text)
        for key in ("route_line", "route_tokens", "basis_inline", "aux_j", "aux_c"):
            assert extracted[key] == case["parsed_fields"][key], f"{case['id']}.{key}"
        assert extracted["solvent_block"] == case["parsed_fields"]["solvent_block"]
        assert extracted["scf_block"] == case["parsed_fields"]["scf_block"]

        captured = backend.calls[0]["kwargs"]
        assert captured.get("method") == params["method"], case["id"]


def test_singlepoint_task_request_round_trips_through_serialisation() -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.7)),
            symbols=("H", "H"),
        ),
        level=MethodSpec(method="HF", basis="def2-SVP"),
    )
    restored = TaskRequest.from_dict(request.to_dict())
    assert restored == request
