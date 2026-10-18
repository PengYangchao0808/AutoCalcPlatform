"""Compatibility alias — NMR ``probability`` science now lives in
``cccp.analysis.nmr.probability`` (plan C05-s2 / T26).

The legacy ``acp.nmr.probability`` path is aliased to the identical module
object so existing imports, ``isinstance``/identity checks and
``unittest.mock.patch`` targets keep operating on the single live
implementation. Isolated optional-research seams remain outside this path.
"""

from __future__ import annotations

import sys

from cccp.analysis.nmr import probability as _impl

sys.modules[__name__] = _impl
