"""Neutral event-contract public surface (frozen allowlist ``acp_services.events``).

The frozen first-slice allowlist
(``tests/baseline/services-evidence/public_allowlist.md``) names the public
event contract module as ``acp_services.events`` with the symbols
``EventEnvelope`` / ``EventCursor`` / ``EventScope``.  Per the B04-1 task the
implementation is placed in the neutral :mod:`acp_services.contracts.events`
(so Workspace/Data publish their own facts without importing a Runtime private
module); this module is a **pure re-export shim** (ANTI #5, no implementation)
that keeps the frozen public surface real.

Dependency direction: stdlib + ``acp_services`` only — never ``acp``.
"""

from __future__ import annotations

from acp_services.contracts.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    EVENT_ENVELOPE_FIELDS,
    LEGACY_EVENT_SCHEMA_VERSION,
    OWNER_COMPONENT_BY_TYPE,
    EventContractError,
    EventCursor,
    EventCursorError,
    EventEnvelope,
    EventOwner,
    EventScope,
    EventSerializationError,
    EventStreamCorruptError,
    EventVersionError,
    OwnerType,
    migrate_legacy_event,
    validate_event_sequence,
)

__all__ = [
    "CURRENT_EVENT_SCHEMA_VERSION",
    "EVENT_ENVELOPE_FIELDS",
    "LEGACY_EVENT_SCHEMA_VERSION",
    "OWNER_COMPONENT_BY_TYPE",
    "EventContractError",
    "EventCursor",
    "EventCursorError",
    "EventEnvelope",
    "EventOwner",
    "EventScope",
    "EventSerializationError",
    "EventStreamCorruptError",
    "EventVersionError",
    "OwnerType",
    "migrate_legacy_event",
    "validate_event_sequence",
]
