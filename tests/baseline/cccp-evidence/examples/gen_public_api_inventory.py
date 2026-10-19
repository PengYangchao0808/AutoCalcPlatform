#!/usr/bin/env python3.11
"""T43 (C08-s5) — programmatic public-API inventory for the standalone cccp package.

Enumerates the real public surface of the C08-supported entry modules by
importing each one (offline — no QC binaries) and listing every name in its
``__all__`` (falling back to non-underscore ``dir()`` when ``__all__`` is
absent).  Output is a deterministic markdown table on stdout.

Run from the repo root (offline, no external software):

    PYTHONPATH=src /usr/bin/python3.11 \
        tests/baseline/cccp-evidence/examples/gen_public_api_inventory.py
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

# The C08-declared public entry surface (plan-3 todo 43 + T40a scope statement).
ENTRY_MODULES: tuple[tuple[str, str], ...] = (
    ("cccp", "package root (version marker only; no re-exports)"),
    ("cccp.analysis.structure", "six-format structure intake + RDKit embedding (T16)"),
    ("cccp.analysis.identity", "content-bound request identity v2 fingerprint primitives (T53)"),
    ("cccp.analysis.orca_parser", "ORCA .out read-only aggregate parse (T18)"),
    ("cccp.analysis.frequency_products", "frequency science products from ORCA modes (T19)"),
    ("cccp.analysis.sampling", "xTB-MD sampling-history science (T20)"),
    ("cccp.analysis.confsearch", "Confsearch Boltzmann/selection/result science (T21)"),
    ("cccp.analysis.pes", "PES path/atom-mapping/selection science (T22)"),
    ("cccp.analysis.tsmode", "TS-mode source bundle + mode mapping science (T23)"),
    ("cccp.analysis.nmr", "NMR/DP4/DP5 analysis science + model assets (T25-T28, T55)"),
    ("cccp.analysis.bounded", "bounded chunked/subsampled/multi-resolution products (T31-T33)"),
    ("cccp.calculation.products", "versioned science-product contracts (T11/T12)"),
    ("cccp.plugin", "trusted-plugin pilot surface + SDK (T44/T45)"),
)


def kind_of(obj: object) -> str:
    if isinstance(obj, type):
        return "class"
    if callable(obj) and not isinstance(obj, type):
        return "function"
    return "constant"


def public_names(module: ModuleType) -> tuple[list[str], bool]:
    if hasattr(module, "__all__"):
        return sorted(str(n) for n in module.__all__), True
    names = sorted(n for n in dir(module) if not n.startswith("_"))
    if module.__name__ == "cccp" and hasattr(module, "__version__"):
        names = ["__version__", *names]
    return names, False


def main() -> int:
    print("# cccp public API inventory (programmatic)")
    print()
    print(f"- interpreter: {sys.version.split()[0]} ({sys.executable})")
    print(f"- entries declared: {len(ENTRY_MODULES)}")
    print("- method: import each entry; list ``__all__`` (fallback: non-underscore dir())")
    print()
    total = 0
    failures: list[str] = []
    for name, desc in ENTRY_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - inventory must report, not crash
            failures.append(name)
            print(f"## {name} — IMPORT FAILED: {exc!r}")
            print()
            continue
        names, from_all = public_names(module)
        total += len(names)
        src = "__all__" if from_all else "dir()"
        print(f"## {name}")
        print(f"- purpose: {desc}")
        print(f"- exported names: {len(names)} (source: {src})")
        print()
        print("| name | kind |")
        print("| --- | --- |")
        for n in names:
            print(f"| `{n}` | {kind_of(getattr(module, n))} |")
        print()
    print(f"TOTAL exported names across {len(ENTRY_MODULES)} entries: {total}")
    if failures:
        print(f"IMPORT FAILURES: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
