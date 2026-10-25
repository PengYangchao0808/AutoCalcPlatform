"""v2 task-storage layout — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.store.layout`; this
module re-exports the same objects so ``acp.storage.layout.TaskStorage`` *is*
``acp_services.store.layout.TaskStorage``. Do not re-add an implementation here.
"""

from __future__ import annotations

from acp_services.store.layout import (
    TASK_DIR_NAME_MAX_LEN,
    TaskLayout,
    TaskStorage,
    is_v2_task_dir,
    runtime_file,
    sanitize_existing_task_dir_name,
    sanitize_task_dir_name,
)

__all__ = [
    "TASK_DIR_NAME_MAX_LEN",
    "TaskLayout",
    "TaskStorage",
    "is_v2_task_dir",
    "runtime_file",
    "sanitize_existing_task_dir_name",
    "sanitize_task_dir_name",
]
