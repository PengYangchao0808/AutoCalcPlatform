"""cccp calculation package (task-layer station root).

Minimal surface on purpose: only the pure type module ``errors`` exists
here for now.  The public export surface is finalized in todo 11; until
then import submodules directly (``cccp.calculation.errors``).  This
package initializer must stay free of imports so it can never create an
import cycle or trigger task/backends registration.
"""

from __future__ import annotations

__all__: list[str] = []
