# pyright: reportAny=false, reportExplicitAny=false
"""P0 closure: capability selection rejects unsupported combinations pre-launch.

Integrated-path companion to ``tests/test_cccp_isolation.py`` (plan todo 10).
Capability selection lives in ``acp.backends`` and is deliberately exercised
here on the integrated path — the isolation probes block ``acp`` imports on
purpose and must not be forced to cover selection.

Locked expectations (plan todo 8, deltas D1/D4):

* ``require_backend("frequency")`` never selects ``XTBBackend`` (xTB declares
  frequency NOT_IMPLEMENTED): selection returns ``ORCABackend``.
* ``supports("xtb", "frequency")`` is False and ``supports("crest",
  "geometry_optimization")`` is False (STUBBED is not implemented).
* A capability with no implemented backend is rejected **before any backend
  instance is constructed** with a structured ``UnsupportedCapabilityError``
  (a ``CalculationError`` that keeps the historical ``LookupError`` category).
"""

from __future__ import annotations

import pytest

from acp.backends.capabilities import supports
from acp.backends.crest import CrestBackend
from acp.backends.orca import ORCABackend
from acp.backends.registry import BackendRegistry, require_backend
from acp.backends.xtb import XTBBackend
from cccp.calculation.errors import CalculationError, UnsupportedCapabilityError


def test_require_backend_frequency_never_selects_xtb() -> None:
    """Frequency selection must not land on XTBBackend; it selects ORCABackend.

    ``require_backend`` completes before any backend instance is built
    (delta D1); the returned class is the declaration-driven winner.
    """
    selected = require_backend("frequency")
    assert selected is not XTBBackend, "XTBBackend has no frequency implementation"
    assert selected is ORCABackend


def test_unsupported_combinations_are_false_before_launch() -> None:
    """Declaration matrix: xtb+frequency and crest+geometry_optimization unsupported."""
    assert supports("xtb", "frequency") is False
    assert supports("crest", "geometry_optimization") is False


def test_capability_without_implemented_backend_is_rejected_structured() -> None:
    """No AVAILABLE declaration -> structured rejection before construction.

    ``CrestBackend`` is the only registered backend and declares
    ``geometry_optimization`` STUBBED (a NotImplementedError stub), so no
    backend implements the capability: ``require`` must raise the structured
    ``UnsupportedCapabilityError`` instead of returning a stub class.
    """
    registry = BackendRegistry()
    registry.register(CrestBackend)
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        registry.require("geometry_optimization")
    error = excinfo.value
    assert isinstance(error, CalculationError)
    assert isinstance(error, LookupError)  # historical category preserved
    message = str(error)
    assert "geometry_optimization" in message
    assert "crest" in message
