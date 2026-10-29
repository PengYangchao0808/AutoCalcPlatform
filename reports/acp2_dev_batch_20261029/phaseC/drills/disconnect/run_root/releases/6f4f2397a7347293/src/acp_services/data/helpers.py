"""Data-owner pure helpers (B05-5, CQ-02).

The Data owner (manifest / cache / transfer / prepare / published artifacts)
needs one crash-safe text writer: write to a temp file **in the same directory**
and ``os.replace`` it into place so a reader never observes a half-written
file.  The canonical definition lived in
``acp.results.frame_candidate_store.atomic_write_text``; this module is the
**single** Data-owner definition and that module now delegates to it while
keeping its public API.

Scope discipline (CQ-02, plan §29.2)
------------------------------------
* **Owner-scoped, not universal.** This is *not* a universal ``utils.py``.  The
  Runtime owner keeps its own atomic writers (e.g. receipt write in
  ``acp_services.runtime.worker``); they are *not* merged across owners.
* **No numeric constants / no time helper here.** Data's ``_utc_now_iso`` copies
  are **not** merged: ``data.operations`` / ``data.transfer`` use a *different*
  format (``time.strftime(...)+"Z"``, no microseconds) and are therefore *not*
  equivalent to the Runtime helper — merging them would silently change stored
  timestamps (equivalence must be proven before merge; it is not here).
* **No cross-owner private-table access.** This module imports nothing from
  ``acp`` and opens no database.

Author: ACP Services (B lane, todo 29).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write_text"]


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write *text* to *path* via a temp file + ``os.replace``.

    Byte-for-byte the former ``acp.results.frame_candidate_store.atomic_write_text``
    body: the temp file is created in *path*'s own directory (same filesystem, so
    the replace is atomic), the parent directory is created on demand, and on any
    failure the temp file is unlinked before the exception propagates.  File mode
    is the ``NamedTemporaryFile`` default (``0600``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
        newline="",
    )
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
