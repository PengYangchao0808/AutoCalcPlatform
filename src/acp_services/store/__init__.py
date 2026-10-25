"""ACP Services store package — job persistence + schema migration (single root).

B05-7 (plan todo 77). The job-persistence closure moved **in place** out of
``acp.scheduler`` / ``acp.storage``: the record types (``JobStatus`` /
``JobSpec`` / ``JobRecord`` / ``ExecutionMode``), the SQLite ``JobStore`` and
its CAS API, the versioned migrator, the molecule-group/naming helpers and the
v2 storage layout/record models. The legacy module paths are same-object
forwarders, so ``acp.scheduler.store.JobStore`` *is*
``acp_services.store.store.JobStore`` and identity is guarded by
``tests/test_services_migration_equivalence.py``.

This package holds no implementation itself (ANTI #5); symbols are re-exported
from the defining modules. It imports **no** ``acp`` symbol — dependency
direction is one-way (``acp`` may import ``acp_services``).
"""

from __future__ import annotations

from acp_services.store.migrations import (
    get_schema_version,
    migrate,
)
from acp_services.store.records import (
    ExecutionMode,
    JobRecord,
    JobSpec,
    JobStatus,
)
from acp_services.store.store import (
    CursorSnapshot,
    JobStateConflictError,
    JobStore,
    row_to_record,
)

__all__ = [
    "CursorSnapshot",
    "ExecutionMode",
    "JobRecord",
    "JobSpec",
    "JobStatus",
    "JobStateConflictError",
    "JobStore",
    "get_schema_version",
    "migrate",
    "row_to_record",
]
