"""Compatibility alias — NMR ``fchl`` science now lives in
``cccp.analysis.nmr.fchl`` (plan C05-s2 / T26).

The legacy ``acp.nmr.fchl`` path is aliased to the identical module object so
existing imports, ``isinstance``/identity checks and ``unittest.mock.patch``
targets keep operating on the single live implementation (FCHL is mock-heavy:
tests patch both consumer lazy imports and internal call sites). Isolated
optional-research seams remain outside this path.
"""

from __future__ import annotations

import sys

from cccp.analysis.nmr import fchl as _impl

sys.modules[__name__] = _impl
