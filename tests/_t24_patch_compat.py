"""T24 relocation patch-compat helper (acp ↔ cccp dual target).

The retired-engine process adapters were relocated into ``cccp`` by plan todo
24 (``refactor(cccp): migrate retired-engine active science closures``).  The
six ``acp.workflows`` source files are **B-side (patch) files**: in the
un-patched tree the adapter functions still live in ``acp``; once the T24
patches are integrated the implementations live in ``cccp`` and the ``acp``
names become re-exports / (for the process seams) unused legacy imports.

The existing tests mock the *task cores* the adapters call.  To keep those
tests valid **both before and after** the patch integration this module
patches the SAME mock object onto the legacy ``acp`` namespace and the new
``cccp`` home.  Assertions and expectations are unchanged; only the mock
installation target is dual-homed.

This is a migration bridge; once T24 is integrated everywhere the tests can
drop the legacy target and import ``patch`` from ``unittest.mock`` again.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock
from unittest.mock import patch as _orig_patch

_MISSING = object()

#: legacy target → new (cccp) home for relocated internal names.
_RELOCATED: dict[str, str] = {
    "acp.workflows.energy_shared.run_censo_refine": (
        "cccp.calculation.energy_adapters.run_censo_refine"
    ),
    "acp.workflows.energy_shared.run_optimize": (
        "cccp.calculation.energy_adapters.run_optimize"
    ),
    "acp.workflows.energy_shared.run_frequency": (
        "cccp.calculation.energy_adapters.run_frequency"
    ),
    "acp.workflows.energy_shared.run_singlepoint": (
        "cccp.calculation.energy_adapters.run_singlepoint"
    ),
    "acp.workflows.energy_shared.run_thermochemistry": (
        "cccp.calculation.energy_adapters.run_thermochemistry"
    ),
    "acp.workflows.energy_shared.run_conformer_search": (
        "cccp.calculation.energy_adapters.run_conformer_search"
    ),
    "acp.workflows.xtbmd_md.MolclusBackend": (
        "cccp.calculation.xtbmd_adapters.MolclusBackend"
    ),
    "acp.workflows.xtbmd_censo_energy.XTBBackend": (
        "cccp.calculation.xtbmd_adapters.XTBBackend"
    ),
    "acp.workflows.xtbmd_censo_energy.IsostatBackend": (
        "cccp.calculation.xtbmd_adapters.IsostatBackend"
    ),
    # ``patch.object(<acp xtbmd module>, "_aligned_rmsd")``: the consuming
    # ``_geometric_precheck`` now lives in cccp.calculation.xtbmd_adapters.
    "acp.workflows.xtbmd_censo_energy._aligned_rmsd": (
        "cccp.calculation.xtbmd_adapters._aligned_rmsd"
    ),
}


def _resolve(dotted: str):
    module_name, _, attr = dotted.rpartition(".")
    return importlib.import_module(module_name), attr


class _DualPatch:
    """Install one mock on both the legacy and relocated module attributes."""

    def __init__(self, legacy: str, relocated: str, new, kwargs: dict) -> None:
        self._legacy = legacy
        self._relocated = relocated
        self._mock = new if new is not None else MagicMock(**kwargs)
        self._saved: list[tuple[object, str, object]] = []

    def __enter__(self):
        for dotted in (self._legacy, self._relocated):
            module, attr = _resolve(dotted)
            self._saved.append((module, attr, module.__dict__.get(attr, _MISSING)))
            setattr(module, attr, self._mock)
        return self._mock

    def __exit__(self, *exc):
        for module, attr, old in reversed(self._saved):
            if old is _MISSING:
                try:
                    delattr(module, attr)
                except AttributeError:  # pragma: no cover - defensive
                    pass
            else:
                setattr(module, attr, old)
        return False


def patch(target, *args, **kwargs):
    """``unittest.mock.patch`` with a dual acp/cccp target for relocated names.

    Targets not in :data:`_RELOCATED` fall through to the stdlib ``patch``.
    """
    relocated = _RELOCATED.get(target)
    if relocated is None:
        return _orig_patch(target, *args, **kwargs)
    if len(args) == 1 and not kwargs:
        return _DualPatch(target, relocated, args[0], {})
    if args:
        return _orig_patch(target, *args, **kwargs)
    new = kwargs.pop("new", None)
    return _DualPatch(target, relocated, new, kwargs)


def _patch_object(target, attribute, *args, **kwargs):
    dotted = f"{getattr(target, '__name__', '')}.{attribute}"
    relocated = _RELOCATED.get(dotted)
    if relocated is None:
        return _orig_patch.object(target, attribute, *args, **kwargs)
    if len(args) == 1 and not kwargs:
        return _DualPatch(dotted, relocated, args[0], {})
    new = kwargs.pop("new", None)
    return _DualPatch(dotted, relocated, new, kwargs)


patch.object = _patch_object
patch.dict = _orig_patch.dict
patch.multiple = _orig_patch.multiple
patch.stopall = _orig_patch.stopall
patch.TEST_PREFIX = _orig_patch.TEST_PREFIX


__all__ = ["patch"]
