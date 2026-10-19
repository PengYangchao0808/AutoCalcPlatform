"""Typed errors for the bounded (chunked / subsampled / multi-resolution) layer.

The bounded layer is an analysis-layer contract that *reuses* the C02
versioned science-product skeleton (envelope, identity, point/series records,
canonical digest, reader/writer).  A structurally corrupt bounded manifest or
an operation applied to an unsupported product is reported with
:class:`BoundedSchemaError`, a subclass of
:class:`~cccp.calculation.products.contracts.ProductSchemaError` so existing
``except TaskInputError`` / ``except ProductSchemaError`` handling keeps
working.  It is never a second schema stack.

Author: QCcalc Team
"""

from __future__ import annotations

from cccp.calculation.products.contracts import ProductSchemaError

__all__ = ["BoundedSchemaError"]


class BoundedSchemaError(ProductSchemaError):
    """Structurally corrupt bounded manifest or an unsupported bounded operation."""
