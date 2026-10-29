"""Private input validation for the thermochemistry primitive (compat shim).

The implementation lives in ``cccp.qc.thermo_normalize`` (pure normalization)
and ``cccp.qc.shermo_adapter`` (request validation) since plan todo 14; this
module keeps the historical private import surface.
"""

from __future__ import annotations

from cccp.qc.shermo_adapter import validate_request
from cccp.qc.thermo_normalize import (
    ThermochemistryInputError,
    ThermochemistryRequest,
    ValidatedRequest,
    standard_state_correction_kcal,
)
from cccp.qc.thermo_normalize import (
    normalize_standard_state as _normalize_standard_state,
)

__all__ = [
    "ThermochemistryInputError",
    "ThermochemistryRequest",
    "ValidatedRequest",
    "_normalize_standard_state",
    "standard_state_correction_kcal",
    "validate_request",
]
