"""Calculation plan/result contracts — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.runtime.plan_contracts`;
this module re-exports the same objects (identity preserved). Do not re-add an
implementation here.
"""

from __future__ import annotations

from acp_services.runtime.plan_contracts import *  # noqa: F403
from acp_services.runtime.plan_contracts import __all__ as __all__
