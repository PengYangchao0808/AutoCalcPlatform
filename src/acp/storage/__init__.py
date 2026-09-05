"""v2 task-storage layout objects (design doc §14 Phase 1)."""

from __future__ import annotations

import logging

from acp.storage.backend import (
    LocalStorageBackend,
    NodeAgentStorageBackend,
    SftpStorageBackend,
    StorageEntry,
    StorageError,
    StorageNotFoundError,
    TaskStorageBackend,
    open_storage,
)
from acp.storage.layout import (
    TASK_DIR_NAME_MAX_LEN,
    TaskLayout,
    TaskStorage,
    is_v2_task_dir,
    sanitize_task_dir_name,
)
from acp.storage.manifest import MANIFEST_FILENAME, Product, ProductKind, ResultManifest
from acp.storage.mapping import NodePathMapping
from acp.storage.record import TaskRecord

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_FILENAME",
    "TASK_DIR_NAME_MAX_LEN",
    "LocalStorageBackend",
    "NodeAgentStorageBackend",
    "NodePathMapping",
    "Product",
    "ProductKind",
    "ResultManifest",
    "SftpStorageBackend",
    "StorageEntry",
    "StorageError",
    "StorageNotFoundError",
    "TaskLayout",
    "TaskRecord",
    "TaskStorage",
    "TaskStorageBackend",
    "is_v2_task_dir",
    "open_storage",
    "sanitize_task_dir_name",
]
