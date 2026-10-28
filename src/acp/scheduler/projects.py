"""Project persistence for the ACP scheduler — compat forwarder (B01-1 / todo 6).

The :class:`ProjectManager` implementation body moved **in place** to
:mod:`acp_services.workspace.projects` (the single implementation root). This
module is a same-object forwarder: it intentionally re-exports the Services
class so ``acp.scheduler.projects.ProjectManager`` *is*
``acp_services.workspace.projects.ProjectManager``. Identity is guarded by
``tests/test_acp_services_isolation.py``.

Do not re-add an implementation here — the single-root rule is enforced by the
isolation test.
"""

from __future__ import annotations

from acp_services.workspace.projects import ProjectManager, sanitize_project_dir_name

__all__ = ["ProjectManager", "sanitize_project_dir_name"]
