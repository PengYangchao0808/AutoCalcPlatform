"""ACP Services machine entrypoints (thin adapters over the Service use cases).

B08 (plan ``.omo/plans/acp-parallel-plan-2-services.md``, todos 42—43) owns the
*machine* surfaces — the JSON/exit-code/error contract (:mod:`machine_output`)
and the MCP thin entry — that sit in front of the Service use cases.  These
adapters must stay import-safe and dependency-clean: like the rest of
``acp_services`` they never import ``acp`` (see
``tests/baseline/services-evidence/public_allowlist.md``); the CLI/MCP layers
depend on *them*, not the other way around.

Author: ACP Services (B lane, todo 42).
"""

from __future__ import annotations

__all__: list[str] = []
