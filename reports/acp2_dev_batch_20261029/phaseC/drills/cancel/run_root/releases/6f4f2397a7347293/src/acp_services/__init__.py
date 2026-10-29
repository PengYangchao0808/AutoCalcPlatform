"""ACP Services — service-side workspace/runtime/data layer (incremental).

This package is being built out slice by slice under
``.omo/plans/acp-parallel-plan-2-services.md``. Dependency direction is
one-way: ``acp`` may import ``acp_services``; ``acp_services`` must never
import ``acp`` (see ``contracts/injection.py`` and
``tests/test_services_injection_contracts.py``).

This module intentionally holds no implementation (ANTI #5): the package
surface is re-exported from its submodules.
"""

from __future__ import annotations

__all__: list[str] = []
