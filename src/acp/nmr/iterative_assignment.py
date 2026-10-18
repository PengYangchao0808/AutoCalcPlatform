"""Compatibility alias — NMR ``iterative_assignment`` science now lives in
``cccp.analysis.nmr.iterative_assignment`` (plan C05-s2 / T26).

The legacy ``acp.nmr.iterative_assignment`` path is aliased to the identical
module object so existing imports, ``isinstance``/identity checks and
``unittest.mock.patch`` targets keep operating on the single live
implementation. Isolated optional-research seams remain outside this path.
"""

from __future__ import annotations

import sys

from cccp.analysis.nmr import iterative_assignment as _impl

sys.modules[__name__] = _impl
