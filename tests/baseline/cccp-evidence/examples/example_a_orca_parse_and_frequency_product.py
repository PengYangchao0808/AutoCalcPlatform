#!/usr/bin/env python3.11
"""Example A (T43) — ORCA read-only parse + frequency product build.

Offline science sample against two real ORCA output fixtures
(``tests/fixtures/``) — parsing is read-only (path in, data out; no QC
program is launched) and the product build is a pure data transform:

1. ``orca_optfreq_real_sections.txt`` — a truncated real OptFreq excerpt
   (frequency sections only, no FINAL SINGLE POINT ENERGY). Demonstrates
   partial-output semantics: ``success=False``, ``final_energy_hartree=None``
   (never fabricated), the 9 native mode frequencies still exposed with
   ORCA-native indices, and displacement vectors truncated so the product
   build records a typed skip reason per mode instead of fabricating.
2. ``structure_viewer/orca_freq_modes_full.txt`` — full frequency/mode
   sections incl. displacement vectors. ``build_normal_modes_product``
   yields the ``normal_modes_v1`` product: 9 modes, native indices 0..8
   never reindexed, mode 6 imaginary at -797.72 cm-1.

Run from the repo root (offline, no external software):

    PYTHONPATH=src /usr/bin/python3.11 \
        tests/baseline/cccp-evidence/examples/example_a_orca_parse_and_frequency_product.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from cccp.analysis.frequency_products import build_normal_modes_product
from cccp.analysis.orca_parser import OrcaOutputParser

REPO = Path(__file__).resolve().parents[4]
SECTIONS = REPO / "tests" / "fixtures" / "orca_optfreq_real_sections.txt"
FULL_MODES = REPO / "tests" / "fixtures" / "structure_viewer" / "orca_freq_modes_full.txt"
ATOM_COUNT = 3  # water: 9 modes = 3N


def main() -> int:
    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    parser = OrcaOutputParser()

    # 1) read-only parse of the truncated real OptFreq excerpt (partial output)
    print(f"[parse]  fixture=tests/fixtures/{SECTIONS.name}")
    partial = parser.parse(SECTIONS)
    print(
        f"[parse]  success={partial.success} energy_hartree={partial.final_energy_hartree} "
        f"(partial fixture: no FINAL SINGLE POINT ENERGY)"
    )
    print(
        f"[parse]  native mode_frequencies count={len(partial.mode_frequencies)} "
        f"indices={sorted(partial.mode_frequencies)}"
    )
    print(
        f"[parse]  mode_vectors keys={sorted(partial.mode_vectors)} "
        f"(vector rows truncated to {len(next(iter(partial.mode_vectors.values())))} != 3 "
        f"-> product build must warn, not fabricate)"
    )
    partial_product = build_normal_modes_product(
        partial, geometry_product_id="example-water-sections", atom_count=ATOM_COUNT
    )
    print(
        f"[parse]  product modes={len(partial_product['modes'])} "
        f"warnings={len(partial_product['warnings'])} (typed skip reasons, never silent)"
    )
    for warning in partial_product["warnings"]:
        print(f"[parse]   warning: {warning}")

    # 2) full-modes fixture: complete parse -> complete product
    print(f"[parse]  fixture=tests/fixtures/structure_viewer/{FULL_MODES.name}")
    calc = parser.parse(FULL_MODES)
    print(f"[parse]  imaginary_modes={calc.imaginary_modes}")
    print(
        "[parse]  vector rows per mode="
        f"{ {k: len(v) for k, v in sorted(calc.mode_vectors.items())} }"
    )

    product = build_normal_modes_product(
        calc, geometry_product_id="example-water-optfreq", atom_count=ATOM_COUNT
    )
    print(f"[build]  schema_version={product['schema_version']}")
    print(f"[build]  units={product['units']}")
    print(f"[build]  modes={len(product['modes'])} warnings={product['warnings']}")
    for mode in product["modes"]:
        flag = "  <-- imaginary (TS evidence)" if mode["imaginary"] else ""
        print(f"[build]   mode {mode['mode_index']}: {mode['frequency_cm1']:.2f} cm-1{flag}")

    # 3) assertions (offline self-check)
    assert partial.success is False and partial.final_energy_hartree is None
    assert len(partial.mode_frequencies) == 9  # native modes still exposed
    assert len(partial_product["modes"]) == 0  # no complete mode -> none emitted
    assert len(partial_product["warnings"]) == 9  # typed skip reasons, not silent
    assert product["schema_version"] == "normal_modes_v1"
    assert [m["mode_index"] for m in product["modes"]] == list(range(9))
    assert product["modes"][6]["frequency_cm1"] == -797.72
    assert product["modes"][6]["imaginary"] is True
    assert product["warnings"] == []
    print("EXAMPLE A: PASS (partial parse semantics + full product build + assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
