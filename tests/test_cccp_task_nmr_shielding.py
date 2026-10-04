"""cccp nmr_shielding task tests (plan todo 43 — P2 execution B).

Independent cccp calls (no ACP imports) with a fake ORCA backend; the
``atom → {symbol, isotropic}`` key shape and JSON integer-key restore are
pinned against ``tests/baseline/cccp_calculation_goldens/nmr_shielding.json``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from cccp.backends.base import QCResult
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    MethodSpec,
    NmrShieldingOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, NmrShieldingPayload
from cccp.calculation.tasks.nmr_shielding import run_nmr_shielding
from cccp.qc.interfaces.orca import NmrShieldingParser

GOLDEN = Path(__file__).parent / "baseline" / "cccp_calculation_goldens" / "nmr_shielding.json"

_GEOMETRY = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.089))
_SYMBOLS = ("C", "H")


class FakeOrcaBackend:
    name = "orca"

    def __init__(self, result: QCResult | None = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def nmr_shielding(
        self,
        coordinates: Any,
        symbols: Any,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "coordinates": coordinates,
                "symbols": list(symbols),
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                **kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _parsed(tmp_path: Path, golden: dict[str, Any], key: str, name: str) -> dict[int, dict[str, Any]]:
    log = tmp_path / name
    log.write_text(golden["inputs"][key], encoding="utf-8")
    return NmrShieldingParser.parse(log, expected_symbols=list(_SYMBOLS))


def _request(options: NmrShieldingOptions | None = None, **level: Any) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=StructureInput(coordinates=_GEOMETRY, symbols=_SYMBOLS),
        level=MethodSpec(**level),
        options=options if options is not None else NmrShieldingOptions(),
    )


def _context(backend: FakeOrcaBackend, tmp_path: Path, **kwargs: Any) -> TaskContext:
    return TaskContext(backend=backend, input_base=tmp_path, **kwargs)


# ── success: shielding key shape vs goldens ───────────────────────────


def test_tensor_block_shielding_key_shape_matches_golden(
    tmp_path: Path, golden: dict[str, Any]
) -> None:
    parsed = _parsed(tmp_path, golden, "tensor_log", "nmr_tensor.out")
    backend = FakeOrcaBackend(
        QCResult(success=True, energy=-40.5, coordinates=_GEOMETRY, symbols=_SYMBOLS, metadata={"shieldings": parsed})
    )
    result = run_nmr_shielding(_request(), context=_context(backend, tmp_path))

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, NmrShieldingPayload)

    expected = golden["tensor_block_parse"]
    assert set(payload.shieldings) == {int(key) for key in expected}
    for key, entry in payload.shieldings.items():
        assert entry.symbol == expected[str(key)]["symbol"]
        assert entry.isotropic == pytest.approx(expected[str(key)]["isotropic"])

    wire = payload.to_dict()["shieldings"]
    assert set(wire) == {"0", "1"}
    assert set(wire["0"]) == {"symbol", "isotropic"}
    restored = NmrShieldingPayload.from_dict(payload.to_dict())
    assert restored == payload


def test_summary_block_shielding_key_shape_matches_golden(
    tmp_path: Path, golden: dict[str, Any]
) -> None:
    parsed = _parsed(tmp_path, golden, "summary_log", "nmr_summary.out")
    backend = FakeOrcaBackend(
        QCResult(success=True, energy=-40.5, coordinates=_GEOMETRY, symbols=_SYMBOLS, metadata={"shieldings": parsed})
    )
    result = run_nmr_shielding(_request(), context=_context(backend, tmp_path))

    assert result.status == "completed"
    payload = result.payload
    assert isinstance(payload, NmrShieldingPayload)
    expected = golden["summary_block_parse"]
    assert set(payload.shieldings) == {int(key) for key in expected}
    for key, entry in payload.shieldings.items():
        assert entry.symbol == expected[str(key)]["symbol"]
        assert entry.isotropic == pytest.approx(expected[str(key)]["isotropic"])


def test_atom_index_base_shifts_keys_and_filters_requested_atoms(
    tmp_path: Path, golden: dict[str, Any]
) -> None:
    parsed = _parsed(tmp_path, golden, "tensor_log", "nmr_tensor.out")
    backend = FakeOrcaBackend(
        QCResult(success=True, energy=-40.5, coordinates=_GEOMETRY, symbols=_SYMBOLS, metadata={"shieldings": parsed})
    )
    options = NmrShieldingOptions(atom_indices=(1,), atom_index_base=1)
    result = run_nmr_shielding(_request(options), context=_context(backend, tmp_path))

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, NmrShieldingPayload)
    assert set(payload.shieldings) == {1}
    assert payload.shieldings[1].symbol == "C"
    assert result.metadata["atom_index_base"] == 1


# ── three-state semantics ─────────────────────────────────────────────


def test_partial_failure_keeps_subset_at_original_keys(
    tmp_path: Path, golden: dict[str, Any]
) -> None:
    parsed = _parsed(tmp_path, golden, "tensor_log", "nmr_tensor.out")
    backend = FakeOrcaBackend(
        QCResult(success=True, energy=-40.5, coordinates=_GEOMETRY, symbols=_SYMBOLS, metadata={"shieldings": parsed})
    )
    options = NmrShieldingOptions(atom_indices=(0, 5))
    result = run_nmr_shielding(_request(options), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.PARSE_FAILURE
    payload = result.payload
    assert isinstance(payload, NmrShieldingPayload)
    assert set(payload.shieldings) == {0}
    assert payload.shieldings[0].symbol == "C"
    assert any("5" in error for error in result.errors)


def test_empty_shielding_table_is_parse_failure(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(
        QCResult(success=True, energy=-40.5, coordinates=_GEOMETRY, symbols=_SYMBOLS, metadata={"shieldings": {}})
    )
    result = run_nmr_shielding(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.PARSE_FAILURE
    assert result.payload is not None
    assert result.payload.shieldings == {}


def test_backend_failure_is_structured(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(
        QCResult(success=False, error_message="ORCA NMR calculation failed", output_file=None, log_file=None)
    )
    result = run_nmr_shielding(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.complete is False
    assert "ORCA NMR calculation failed" in result.errors[0]


def test_backend_exception_is_structured_failure(tmp_path: Path) -> None:
    backend = FakeOrcaBackend(error=RuntimeError("orca missing"))
    result = run_nmr_shielding(_request(), context=_context(backend, tmp_path))

    assert result.status == "failed"
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert "orca missing" in result.errors[0]


def test_wrong_options_type_is_rejected(tmp_path: Path) -> None:
    request = TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=StructureInput(coordinates=_GEOMETRY, symbols=_SYMBOLS),
    )
    object.__setattr__(request, "options", object())
    with pytest.raises(TaskInputError):
        run_nmr_shielding(request, context=_context(FakeOrcaBackend(), tmp_path))


# ── task module independence (no ACP) ─────────────────────────────────


def test_task_module_never_imports_acp() -> None:
    source = (Path(__file__).parent.parent / "src/cccp/calculation/tasks/nmr_shielding.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("acp") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("acp")
