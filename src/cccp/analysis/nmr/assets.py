"""Single CCCP locator for the packaged NMR/DP5 model assets.

The DP5/FCHL model artifacts — ``atomic_reps.gz``, ``frag_reps.gz``, the
three ``*.p`` KDE/residual pickles, ``tms_references.txt`` and the two
license/notice files — live in exactly one place: the ``model_assets/`` data
directory next to this module (a data dir **without** ``__init__.py``).  This
module is the *only* place that derives an asset path; no reader may compute
``Path(__file__).parent / ...`` itself.  The ACP NMR readers
(``acp.nmr.models`` / ``acp.nmr.error_model`` / ``acp.nmr.fchl`` /
``acp.nmr.reference_validation`` and the isolated stub adapter) receive their
asset paths from here, so relocating the assets is a one-line change and the
C05 migration keeps a single source of truth.

The path is resolved from *this* module's ``__file__`` (the packaged location
of the owning ``cccp.analysis.nmr`` package), never from a caller's location —
that is what makes it stable under editable installs and built wheels alike.

``cccp`` must never import ``acp``; this module only imports the stdlib.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = ["MODEL_ASSETS_DIRNAME", "asset_dir", "resolve_asset"]

#: Name of the data directory that holds the NMR/DP5 model artifacts.
MODEL_ASSETS_DIRNAME: Final[str] = "model_assets"


def asset_dir() -> Path:
    """Return the directory that holds the NMR/DP5 model assets.

    The directory is a sibling of this module and intentionally has no
    ``__init__.py`` (it is package *data*, not a sub-package).  The path is
    returned whether or not it exists — callers handle missing assets with
    typed states rather than exceptions (an absent optional parameter file is
    reported as ``unavailable``, never raised).
    """
    return Path(__file__).resolve().parent / MODEL_ASSETS_DIRNAME


def resolve_asset(name: str) -> Path:
    """Return the path to asset *name* inside :func:`asset_dir`.

    Args:
        name: Asset file name (e.g. ``"tms_references.txt"``).  Existence is
            not required — the returned path is a pure join.

    Returns:
        The absolute path ``asset_dir() / name``.
    """
    return asset_dir() / name
