"""Neutral runtime contract errors (D02).

The frozen-worker implementation (:mod:`acp_services.runtime.worker`) and the
controller-side receipt gate (:mod:`acp_services.runtime.receipts`) both need a
shared ``WorkerError`` base. Defining it here — in a module that imports only
the standard library — keeps the execution/decision split acyclic: ``receipts``
no longer imports ``worker`` (the previous reverse dependency) and
:mod:`acp_services.runtime.receipt_identity` imports neither side.

``acp_services.runtime.worker.WorkerError`` and
``acp_services.runtime.receipts.ReceiptRejectedError`` still resolve to the
same public names; the base class is simply re-exported from its original
module (identity preserved, no second implementation).
"""

from __future__ import annotations

__all__ = ["WorkerError"]


class WorkerError(Exception):
    """Base class for frozen-worker / receipt contract violations."""
