"""ACP Services workspace — project / member / group owner (B01-1 / todo 6).

Re-exports only (ANTI #5): the single implementation body lives in
:mod:`acp_services.workspace.projects`. The legacy ``acp.scheduler.projects``
path is a same-object forwarder, guarded by
``tests/test_acp_services_isolation.py``.
"""

from __future__ import annotations

from acp_services.workspace.projects import ProjectManager, sanitize_project_dir_name

__all__ = ["ProjectManager", "sanitize_project_dir_name"]
