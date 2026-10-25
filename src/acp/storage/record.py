"""Task metadata record — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.store.record`; this
module re-exports the same object. Do not re-add an implementation here.
"""

from __future__ import annotations

from acp_services.store.record import TaskRecord

__all__ = ["TaskRecord"]
