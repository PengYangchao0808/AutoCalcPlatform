"""
QC Interfaces
=============

Interfaces for quantum chemistry software.
"""

# DEPRECATED: Prefer acp.backends abstractions for new code.
from conformer_search.qc.interfaces.base import QCInterfaceBase, QCResult
from conformer_search.qc.interfaces.gaussian import GaussianInterface
from conformer_search.qc.interfaces.orca import ORCAInterface
from conformer_search.qc.interfaces.crest import CRESTInterface
from conformer_search.qc.interfaces.xtb import XTBInterface

__all__ = [
    "QCInterfaceBase",
    "QCResult",
    "GaussianInterface",
    "ORCAInterface", 
    "CRESTInterface",
    "XTBInterface",
]
