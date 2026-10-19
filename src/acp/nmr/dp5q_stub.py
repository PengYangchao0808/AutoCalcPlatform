"""Compatibility alias — NMR ``dp5q_stub`` science now lives in
``cccp.analysis.nmr.dp5q_stub`` (plan C05-s5 / T29).

The legacy ``acp.nmr.dp5q_stub`` path is aliased to the identical module
object so existing imports, ``isinstance``/identity checks and
``unittest.mock.patch`` / ``monkeypatch.setattr`` targets keep operating on
the single live implementation. The DP5q stub remains an isolated, optional,
explicitly-selected seam: the default NMR/DP4/DP5 chain never references it
and importing it registers nothing."""

from __future__ import annotations

import sys

from cccp.analysis.nmr import dp5q_stub as _impl

sys.modules[__name__] = _impl
