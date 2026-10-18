"""ACP Services data package — typed DataResourcePort over existing writers.

Re-exports only (ANTI #5): the frozen port shape and typed records live in
:mod:`acp_services.data.contracts`.  The delegating adapters that wrap the
existing implementations live on the legacy side of the seam
(``acp.services_data_bootstrap``) so this package imports nothing from there.
"""

from __future__ import annotations

from acp_services.data.contracts import (
    CONTRACT_VERSION,
    Availability,
    AvailabilityQuery,
    BasisRevision,
    CacheEntry,
    CacheLookup,
    CandidateSnapshot,
    CandidateWrite,
    DataAvailability,
    DataResourcePort,
    DataResourceUnavailableError,
    DataRevisionConflictError,
    Freshness,
    InputRecord,
    InputRegistration,
    InputValidation,
    InputValidationRequest,
    ManifestPublication,
    ManifestPublish,
    ManifestRead,
    ManifestSnapshot,
    PrepareOperation,
    RemoteJobRef,
    ResourceKind,
    ResourceRevision,
    ResourceState,
    SourceUpsert,
    TransferDirection,
    TransferDownload,
    TransferReport,
    TransferResult,
    TransferUpload,
)

__all__ = [
    "CONTRACT_VERSION",
    "Availability",
    "AvailabilityQuery",
    "BasisRevision",
    "CacheEntry",
    "CacheLookup",
    "CandidateSnapshot",
    "CandidateWrite",
    "DataAvailability",
    "DataResourcePort",
    "DataResourceUnavailableError",
    "DataRevisionConflictError",
    "Freshness",
    "InputRecord",
    "InputRegistration",
    "InputValidation",
    "InputValidationRequest",
    "ManifestPublish",
    "ManifestPublication",
    "ManifestRead",
    "ManifestSnapshot",
    "PrepareOperation",
    "RemoteJobRef",
    "ResourceKind",
    "ResourceRevision",
    "ResourceState",
    "SourceUpsert",
    "TransferDirection",
    "TransferDownload",
    "TransferReport",
    "TransferResult",
    "TransferUpload",
]
