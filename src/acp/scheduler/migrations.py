"""Schema migration — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.store.migrations`; this
module re-exports the same objects, so ``acp.scheduler.migrations.migrate`` *is*
``acp_services.store.migrations.migrate``. Do not re-add an implementation here.
"""

from __future__ import annotations

from acp_services.store.migrations import (
    _utc_now_iso,  # noqa: F401 - helper alias
    get_schema_version,
    migrate,
)

__all__ = ["get_schema_version", "migrate"]
