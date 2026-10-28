"""The one reviewed station for wire identifier constants (M1-01c).

``src/acp_services/**`` must never import ``acp`` (isolation gate).  An *extension
id* is not an import: it is an opaque wire value that happens to live in the
``acp.`` name namespace.  Before M1-01c the only such value
(:data:`NEGOTIATION_EXTENSION_ID`) was assembled from ``chr(97)`` so the isolation
scanner — which treats any ``"acp.*"`` string as an import — would not see it.
That hid a reviewed wire literal inside an obfuscated expression, which is worse
than the problem it worked around.

M1-01c replaces the assembly with this module and an explicit governance rule:

* the reviewed values of :data:`REVIEWED_WIRE_IDENTIFIERS` are the **only**
  ``acp.*`` string literals allowed anywhere in ``acp_services``;
* they are allowed **only** as the constant value declared in this module — the
  scanners (:mod:`tests.test_acp_services_isolation`,
  :mod:`tests.test_services_injection_contracts`) allow the literal for this
  module name and nothing else, so a second copy in any other module fails;
* no review exemption ever covers an import, lazy import, ``importlib`` /
  ``__import__`` load, ``getattr(..., "acp.*")`` or ``sys.modules["acp.*"]``
  lookup; those stay hard failures in every module (including this one);
* adding a value is a contract change: it needs a reviewed constant here, a
  frozen contract that owns the value, and a negative test proving a copy
  outside this module is rejected.

Declaring the values here does **not** make this module the owner of their
semantics.  The research extension id/version authority stays
``p2-research-operations.v1.json::client_path.negotiation`` (executable spelling:
``src/acp/mcp_extension.py``); this module only names the one guidance id that
Services itself publishes under ``capabilities.extensions``.

Author: ACP Services (B lane, M1-01c wire-id de-obfuscation governance).
"""

from __future__ import annotations

__all__ = [
    "NEGOTIATION_EXTENSION_ID",
    "REVIEWED_WIRE_IDENTIFIERS",
    "WIRE_IDENTIFIERS_MODULE",
    "is_reviewed_wire_identifier",
]

#: Dotted module name the isolation scanners use as the single-literal-station
#: allowlist key.  It matches :data:`__name__` by construction.
WIRE_IDENTIFIERS_MODULE = "acp_services.entrypoints.wire_identifiers"

#: Versioned negotiation-guidance extension id published under
#: ``initialize.capabilities.extensions`` (never as a top-level ``initialize``
#: field and never as a ``negotiated`` claim).  Wire value:
#: ``acp.negotiation.v1``.  It is guidance about *where* capability extensions
#: are negotiated (``tools/call acp_capabilities``), not a promise about any
#: extension's implemented/negotiated state (M0-09/ADR-006 D7).
NEGOTIATION_EXTENSION_ID = "acp.negotiation.v1"

#: Every reviewed ``acp.*`` wire literal allowed in ``acp_services``.  The
#: scanners read this tuple as their allowlist and never accept a literal from a
#: module other than :data:`WIRE_IDENTIFIERS_MODULE`.
REVIEWED_WIRE_IDENTIFIERS: tuple[str, ...] = (NEGOTIATION_EXTENSION_ID,)


def is_reviewed_wire_identifier(value: str) -> bool:
    """Whether *value* is one of the reviewed wire identifier constants."""
    return str(value).strip() in REVIEWED_WIRE_IDENTIFIERS
