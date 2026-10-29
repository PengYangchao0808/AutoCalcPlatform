"""Compatibility alias — NMR ``proton_processor`` science now lives in
``cccp.analysis.nmr.proton_processor`` (plan C05-s3 / T27).

The legacy ``acp.nmr.proton_processor`` path is aliased to the identical module object so
existing imports, ``isinstance``/identity checks and ``unittest.mock.patch`` /
``monkeypatch.setattr`` targets keep operating on the single live
implementation. Isolated optional-research seams remain outside this path.
"""

from __future__ import annotations

import sys

from cccp.analysis.nmr import proton_processor as _impl

sys.modules[__name__] = _impl
