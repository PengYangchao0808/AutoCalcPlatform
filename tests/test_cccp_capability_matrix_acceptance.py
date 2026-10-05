"""A6 acceptance: capability declaration ⇄ selection ⇄ implementation matrix.

Cross-checks every ``backend × capability`` pair declared in
:mod:`cccp.backends.matrix` against the *actual* method implemented by the
registered backend class (never a structural ``isinstance`` Protocol match —
declaration is the authority, per plan todo 38):

* declared ``AVAILABLE`` ⇔ the backend has a real callable for that
  capability (no ``NotImplementedError`` stub), and vice versa;
* declared ``STUBBED`` methods exist only to raise ``NotImplementedError``;
* declared ``NOT_IMPLEMENTED`` is never backed by a real implementation;
* ``supports`` / ``list_backends`` / ``list_capabilities`` answer strictly
  from the declaration (``BACKEND_CAPABILITY_STATUS``), never from runtime
  binary probes (``MISSING_BINARY`` is runtime-only and never appears in the
  static matrix).

It then proves the three concrete outcome states are distinguishable:

1. unsupported capability (including a stub or an unknown name) →
   ``UnsupportedCapabilityError``;
2. declared-but-missing binary → ``BackendUnavailableError``;
3. computation failure → a structured ``TaskResult`` with
   ``status="failed"`` and a closed ``ErrorKind``; a backend method that is
   absent never escapes as ``AttributeError`` (the ``call_capability`` guard
   in ``cccp/calculation/_common.py``).

Pure cccp package — never imports ``acp``.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any

import pytest

from cccp.backends.capabilities import (
    list_backends,
    list_capabilities,
    supports,
)
from cccp.backends.matrix import (
    CAPABILITY_ALIASES,
    CAPABILITY_BACKEND_PRIORITY,
    CAPABILITY_MATRIX,
    TASK_CAPABILITY_MAP,
    BackendCapabilityStatus,
)
from cccp.backends.registry import backend_registry, require_backend
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import (
    BackendUnavailableError,
    UnsupportedCapabilityError,
)
from cccp.calculation.requests import (
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, TaskResult
from cccp.calculation.selection import (
    precheck_runtime,
    select_backend,
    select_capability,
    select_semantic,
)
from cccp.calculation.tasks.singlepoint import run_singlepoint

# ── declaration vocabulary → the method(s) that implement each capability ──
#
# ``AVAILABLE`` means "this method exists and really runs"; ``STUBBED`` means
# "this method exists only to raise NotImplementedError"; ``NOT_IMPLEMENTED``
# means "no real method".  Capabilities realised through the same callable
# (nevpt2 via casscf, constrained_relaxed_scan via relaxed_scan) list that
# callable explicitly so the introspection stays faithful.
CAPABILITY_METHODS: dict[str, tuple[str, ...]] = {
    "geometry_optimization": ("optimize",),
    "constrained_optimization": ("constrained_optimize",),
    "single_point": ("single_point",),
    "frequency": ("frequency",),
    "conformer_search": ("run_conformer_search", "search"),
    "clustering": ("cluster",),
    "thermochemistry": ("thermochemistry", "batch_thermochemistry"),
    "mrrho_thermochemistry": ("enso_thermo",),
    "nmr_shielding": ("nmr_shielding",),
    "relaxed_scan": ("relaxed_scan",),
    "transition_state": ("transition_state_opt",),
    "irc": ("irc",),
    "constrained_relaxed_scan": ("relaxed_scan",),
    "rigid_scan": ("rigid_scan",),
    "casscf": ("casscf",),
    "nevpt2": ("casscf",),
    "md_sampling": ("run_md",),
    "censo_refine": ("refine_ensemble",),
    "xtb_path_search": ("path_search",),
    "orca_gradient": ("single_point_gradient",),
}

MISSING, STUB, REAL = "missing", "stub", "real"


def _matrix_keys() -> list[str]:
    keys: set[str] = set()
    for row in CAPABILITY_MATRIX.values():
        keys.update(row)
    return sorted(keys)


_MATRIX_KEYS = _matrix_keys()
_ALL_PAIRS: list[tuple[str, str]] = [
    (backend, capability) for backend in sorted(CAPABILITY_MATRIX) for capability in _MATRIX_KEYS
]
_AVAILABLE_PAIRS = [
    pair
    for pair in _ALL_PAIRS
    if CAPABILITY_MATRIX[pair[0]][pair[1]] is BackendCapabilityStatus.AVAILABLE
]
_NON_AVAILABLE_PAIRS = [pair for pair in _ALL_PAIRS if pair not in set(_AVAILABLE_PAIRS)]
_CAPABILITIES_WITH_IMPLEMENTATION = sorted({capability for _, capability in _AVAILABLE_PAIRS})


def _raises_notimplemented(func: Any) -> bool:
    """True when *func*'s whole body is a ``raise NotImplementedError``.

    A stub is detected statically (no execution) so probing the matrix can
    never launch a subprocess; a method with any other behaviour (including a
    conditional raise) counts as a real implementation.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):  # pragma: no cover - editable installs expose source
        return False
    tree = ast.parse(source)
    function = tree.body[0]
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    raised = body[0].exc
    if isinstance(raised, ast.Call):
        raised = raised.func
    if isinstance(raised, ast.Name):
        name = raised.id
    elif isinstance(raised, ast.Attribute):
        name = raised.attr
    else:
        name = None
    return name == "NotImplementedError"


def _actual_implementation(backend: str, capability: str) -> str:
    """Classify the registered backend's real support for *capability*."""
    backend_cls = backend_registry.get(backend)
    implementations: list[str] = []
    for method_name in CAPABILITY_METHODS[capability]:
        method = getattr(backend_cls, method_name, None)
        if method is None:
            continue
        implementations.append(STUB if _raises_notimplemented(method) else REAL)
    if not implementations:
        return MISSING
    return REAL if REAL in implementations else STUB


# ── 1. declaration vocabulary is closed and fully mapped ────────────────


def test_capability_methods_cover_the_matrix_vocabulary() -> None:
    assert set(CAPABILITY_METHODS) == set(_MATRIX_KEYS)


def test_every_capability_alias_folds_onto_a_matrix_key() -> None:
    assert set(CAPABILITY_ALIASES.values()) == set(_MATRIX_KEYS)


def test_task_capability_map_only_names_declared_capabilities() -> None:
    for task, capabilities in TASK_CAPABILITY_MAP.items():
        assert capabilities, task
        for capability in capabilities:
            assert capability in CAPABILITY_METHODS, (task, capability)


def test_static_matrix_never_encodes_runtime_binary_absence() -> None:
    """``MISSING_BINARY`` is a runtime probe verdict, not a declaration."""
    for backend, row in CAPABILITY_MATRIX.items():
        for capability, status in row.items():
            assert status is not BackendCapabilityStatus.MISSING_BINARY, (
                backend,
                capability,
            )


# ── 2. declared status ⇔ actual implementation ──────────────────────────


@pytest.mark.parametrize(("backend", "capability"), _ALL_PAIRS, ids=lambda v: str(v))
def test_declared_available_has_a_real_implementation(backend: str, capability: str) -> None:
    status = CAPABILITY_MATRIX[backend][capability]
    actual = _actual_implementation(backend, capability)
    if status is BackendCapabilityStatus.AVAILABLE:
        assert actual == REAL, f"{backend}.{capability} declared AVAILABLE but is {actual}"
    else:
        assert actual != REAL, (
            f"{backend}.{capability} declared {status.value} but has a real implementation"
        )


@pytest.mark.parametrize(("backend", "capability"), _ALL_PAIRS, ids=lambda v: str(v))
def test_real_implementation_implies_declared_available(backend: str, capability: str) -> None:
    if _actual_implementation(backend, capability) == REAL:
        assert CAPABILITY_MATRIX[backend][capability] is BackendCapabilityStatus.AVAILABLE


def test_available_and_real_pairs_are_the_same_set() -> None:
    real = {pair for pair in _ALL_PAIRS if _actual_implementation(*pair) == REAL}
    assert real == set(_AVAILABLE_PAIRS)


def test_declared_stubbed_methods_are_notimplementederror_stubs() -> None:
    stubbed = [
        pair
        for pair in _ALL_PAIRS
        if CAPABILITY_MATRIX[pair[0]][pair[1]] is BackendCapabilityStatus.STUBBED
    ]
    assert stubbed, "the matrix must exercise the STUBBED vocabulary"
    for backend, capability in stubbed:
        assert _actual_implementation(backend, capability) == STUB, (backend, capability)


def test_declared_not_implemented_is_never_a_real_method() -> None:
    for backend, capability in _ALL_PAIRS:
        if CAPABILITY_MATRIX[backend][capability] is not BackendCapabilityStatus.NOT_IMPLEMENTED:
            continue
        assert _actual_implementation(backend, capability) in {MISSING, STUB}, (
            backend,
            capability,
        )


# ── 3. supports / listing semantics follow the declaration ──────────────


@pytest.mark.parametrize(("backend", "capability"), _ALL_PAIRS, ids=lambda v: str(v))
def test_supports_returns_true_only_for_available(backend: str, capability: str) -> None:
    expected = CAPABILITY_MATRIX[backend][capability] is BackendCapabilityStatus.AVAILABLE
    assert supports(backend, capability) is expected
    assert list_capabilities(backend)[capability] is CAPABILITY_MATRIX[backend][capability]


def test_supports_folds_caller_facing_aliases_and_backend_aliases() -> None:
    assert supports("ORCA", "sp") is supports("orca", "single_point") is True
    assert supports("CrestBackend", "frequency") is False
    assert supports("xtb", "optimization") is supports("xtb", "geometry_optimization") is True


def test_list_backends_reports_only_declared_available() -> None:
    for capability in _CAPABILITIES_WITH_IMPLEMENTATION:
        expected = sorted(
            backend
            for backend, row in CAPABILITY_MATRIX.items()
            if row[capability] is BackendCapabilityStatus.AVAILABLE
        )
        assert list_backends(capability) == expected


def test_priority_lists_only_declaring_backends() -> None:
    for capability, order in CAPABILITY_BACKEND_PRIORITY.items():
        assert capability in CAPABILITY_METHODS
        available = set(list_backends(capability))
        # rigid_scan has no implementer and deliberately pins no order.
        assert set(order) <= available or not available, (capability, order)


# ── 4. selection never reaches a stub / unimplemented capability ────────


@pytest.mark.parametrize("capability", _CAPABILITIES_WITH_IMPLEMENTATION)
def test_require_backend_returns_a_declaring_backend(capability: str) -> None:
    backend_cls = require_backend(capability)
    canonical = (getattr(backend_cls, "name", "") or "").lower()
    assert canonical in list_backends(capability)
    assert supports(canonical, capability)


def test_require_backend_rejects_capability_without_any_implementer() -> None:
    assert list_backends("rigid_scan") == []
    with pytest.raises(UnsupportedCapabilityError):
        require_backend("rigid_scan")
    # The registry validates the capability *name* first and keeps the
    # historical ValueError contract (locked by test_acp_backends), while the
    # selection choke point wraps it into UnsupportedCapabilityError.
    with pytest.raises(ValueError, match="Unknown capability"):
        require_backend("definitely-not-a-capability")


@pytest.mark.parametrize(("backend", "capability"), _NON_AVAILABLE_PAIRS, ids=lambda v: str(v))
def test_non_available_capability_is_not_selectable(backend: str, capability: str) -> None:
    """Explicit backend requests for STUBBED/NOT_IMPLEMENTED are refused."""
    with pytest.raises(UnsupportedCapabilityError):
        select_capability(capability, backend=backend)


@pytest.mark.parametrize("capability", _CAPABILITIES_WITH_IMPLEMENTATION)
def test_default_selection_never_picks_a_non_available_backend(capability: str) -> None:
    selection = select_capability(capability)
    assert selection.backend in list_backends(capability)
    assert supports(selection.backend, capability)


# ── 5. three-state classification ───────────────────────────────────────


def _singlepoint_request(**overrides: Any) -> TaskRequest:
    payload: dict[str, Any] = {
        "task": TaskKind.SINGLEPOINT,
        "structure": StructureInput(
            coordinates=((0.0, 0.0, 0.0),),
            symbols=("C",),
        ),
        "charge": 0,
        "multiplicity": 1,
        "level": MethodSpec(method="wB97X-D4", basis="def2-SVP"),
    }
    payload.update(overrides)
    return TaskRequest(**payload)


# State 1 — unsupported capability (stub / unimplemented / unknown).


def test_state1_unsupported_capability_raises_unsupported() -> None:
    with pytest.raises(UnsupportedCapabilityError):
        require_backend("rigid_scan")
    with pytest.raises(UnsupportedCapabilityError):
        select_capability("rigid_scan")
    with pytest.raises(UnsupportedCapabilityError):
        select_capability("no-such-capability")


def test_state1_stub_capability_is_refused_for_its_declaring_backend() -> None:
    # crest declares optimize/single_point/frequency stubs, never AVAILABLE.
    for capability in ("geometry_optimization", "single_point", "frequency"):
        assert supports("crest", capability) is False
        with pytest.raises(UnsupportedCapabilityError):
            select_capability(capability, backend="crest")


# State 2 — declared AVAILABLE but the required binary is absent in THIS
# context (never a silent substitution for another backend).


def test_state2_declared_missing_binary_raises_backend_unavailable(tmp_path: Path) -> None:
    assert CAPABILITY_MATRIX["orca"]["single_point"] is BackendCapabilityStatus.AVAILABLE
    request = _singlepoint_request()
    selection = select_semantic(request)
    assert selection.backend == "orca"
    missing_context = TaskContext(
        config={"executables": {"orca": {"path": str(tmp_path / "missing_orca")}}}
    )
    with pytest.raises(BackendUnavailableError) as excinfo:
        precheck_runtime(selection, missing_context)
    assert not isinstance(excinfo.value, UnsupportedCapabilityError)
    with pytest.raises(BackendUnavailableError):
        select_backend(request, context=missing_context)


def test_state2_environment_absent_binary_raises_backend_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable",
        lambda name, configured_path=None: None,
    )
    with pytest.raises(BackendUnavailableError):
        select_backend(_singlepoint_request())


# State 3 — computation failure becomes a structured TaskResult; a missing
# method is a typed pre-launch rejection, never AttributeError.


class _FailingSinglePointBackend:
    name = "orca"

    def single_point(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        raise RuntimeError("backend crashed")


class _NoCapabilityBackend:
    name = "orca"


def test_state3_computation_failure_returns_structured_task_result() -> None:
    result = run_singlepoint(
        _singlepoint_request(),
        context=TaskContext(backend=_FailingSinglePointBackend()),
    )
    assert isinstance(result, TaskResult)
    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors == ("backend crashed",)


def test_state3_missing_capability_method_never_escapes_as_attribute_error() -> None:
    with pytest.raises(UnsupportedCapabilityError):
        run_singlepoint(
            _singlepoint_request(),
            context=TaskContext(backend=_NoCapabilityBackend()),
        )
    # The dispatch guard rejects the absent method before launch; an
    # AttributeError from a bare ``getattr`` would be a different type.
    from cccp.calculation._common import CalculationInputs, call_capability

    inputs = CalculationInputs(
        coordinates=((0.0, 0.0, 0.0),),
        symbols=("C",),
        charge=0,
        multiplicity=1,
    )
    with pytest.raises(UnsupportedCapabilityError):
        call_capability(
            _NoCapabilityBackend(),
            "single_point",
            inputs,
            Path.cwd(),
            {},
        )


def test_state3_scientific_failure_keeps_closed_error_taxonomy() -> None:
    from cccp.qc.interfaces.base import QCResult

    class _NotConvergedBackend:
        name = "orca"

        def single_point(self, coordinates: Any, symbols: list[str], **kwargs: Any) -> Any:
            return QCResult(success=False, error_message="SCF did not converge")

    result = run_singlepoint(
        _singlepoint_request(),
        context=TaskContext(backend=_NotConvergedBackend()),
    )
    assert result.status == "failed"
    assert result.error_kind is ErrorKind.NOT_CONVERGED
    assert result.complete is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
