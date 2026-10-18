"""CCCP NMR analysis science — model-asset locator.

This package currently carries the single model-asset locator for the DP5/FCHL
artifacts (see :mod:`cccp.analysis.nmr.assets`).  The NMR science modules
themselves are migrated here by the C05 waves (plan T25–T30); this ``__init__``
holds re-exports only (repo ANTI #5) and must not grow implementation.
"""

from __future__ import annotations

from cccp.analysis.nmr.assets import MODEL_ASSETS_DIRNAME, asset_dir, resolve_asset

__all__ = ["MODEL_ASSETS_DIRNAME", "asset_dir", "resolve_asset"]
