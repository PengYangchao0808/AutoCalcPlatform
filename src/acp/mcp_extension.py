"""The one ACP-side declaration of the research MCP extension (M0-09).

``acp_services`` implements the handshake *semantics* once
(:func:`acp_services.entrypoints.mcp.negotiate_research_extension`) but must not
carry an ``acp.*`` wire literal (the Services isolation gate).  This module is
therefore the **single** place the id/version pair is spelled on the ``acp``
side, and every consumer imports it from here:

* :mod:`acp.services_mcp_bootstrap` — the ``acp mcp serve`` composition root that
  binds the spec into :class:`acp_services.entrypoints.mcp.McpServer`;
* :mod:`acp.api.routes` — the ``GET``/``POST /api/capabilities`` discovery
  command, which answers the same ``extensions[]`` projection.

Authority: ``p2-research-operations.v1.json::client_path.negotiation`` freezes
``acp.research.v1@1.0.0``; ``application-api.v1.json::mcp_extensions`` is the
projection that must agree with it.  ``tests/test_acp2_mcp_research_handshake.py``
asserts both, so this module can never drift into a second authority.

The version is **not** this file's decision: a bump is a contract change
(M0-12 / M1-01) that edits the contract, this constant and the RPH bridge
(``BRIDGE_EXTENSION_VERSION``) together, with a new negative sample.
"""

from __future__ import annotations

from acp_services.entrypoints.mcp import RESEARCH_TOOL_NAMES, ExtensionSpec

__all__ = [
    "RESEARCH_EXTENSION_ID",
    "RESEARCH_EXTENSION_VERSION",
    "RESEARCH_EXTENSION",
    "RESEARCH_EXTENSION_SPEC",
]

#: Wire id of the versioned research extension.
RESEARCH_EXTENSION_ID = "acp.research.v1"
#: The single supported version (contract-frozen, see the module docstring).
RESEARCH_EXTENSION_VERSION = "1.0.0"

#: The spec bound into the MCP server: id, version and the frozen promise set.
#: ``implemented`` is derived from ``promised_tools`` against the registered
#: allowlist, so declaring the promise can never by itself advertise delivery.
RESEARCH_EXTENSION_SPEC = ExtensionSpec(
    extension_id=RESEARCH_EXTENSION_ID,
    version=RESEARCH_EXTENSION_VERSION,
    promised_tools=RESEARCH_TOOL_NAMES,
)

#: Backwards-friendly alias for importers that prefer the shorter name.
RESEARCH_EXTENSION = RESEARCH_EXTENSION_SPEC
