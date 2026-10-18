"""ACP compatibility shim for Confsearch Boltzmann helpers (plan todo 21).

Implementation moved to :mod:`cccp.analysis.confsearch.boltzmann` (T5
write-registry §4.4 ``C直写`` 移交).  This module re-exports the **same
objects** so existing ``from acp.confsearch.shared.boltzmann import ...``
imports and ``acp.confsearch.shared.boltzmann.<attr>`` access keep working
unchanged (RT-SAME; constants keep their original precision).
"""

from __future__ import annotations

from cccp.analysis.confsearch.boltzmann import (
    HARTREE_TO_KCAL,
    _KCAL_PER_HARTREE,  # noqa: F401  (prior module attribute, private)
    _RGAS_KCAL_MOL_K,  # noqa: F401  (prior module attribute, private)
    _boltzmann_weights_none,  # noqa: F401  (prior module attribute, private)
    boltzmann_weights,
    relative_energies_kcal,
)

__all__ = ["HARTREE_TO_KCAL", "boltzmann_weights", "relative_energies_kcal"]
