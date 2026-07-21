"""Tests for composite/automatic method guard filtering (Phase 5.4).

Verifies that RI keywords and auxiliary basis sets are correctly filtered
from route_extras for composite (3c) and automatic (DLPNO) methods.
"""

from __future__ import annotations

import pytest

from conformer_search.qc.interfaces.orca import ORCAInterface


@pytest.fixture
def orca_config():
    return {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }


def test_composite_strips_ri_keywords(orca_config) -> None:
    """3c composite methods should filter RI keywords from route_extras."""
    iface = ORCAInterface(orca_config, method="r2SCAN-3c", basis="def2-mTZVPP")
    blocks_str = iface._build_input_blocks(
        calc_type="opt",
        method="r2SCAN-3c",
        basis="def2-mTZVPP",
        route_extras=["RIJCOSX", "def2/J", "VeryTightSCF"],
    )
    assert "RIJCOSX" not in blocks_str
    assert "def2/J" not in blocks_str
    assert "VeryTightSCF" in blocks_str  # Unrelated extras preserved


def test_dlpno_strips_ri_but_keeps_auxc(orca_config) -> None:
    """DLPNO should filter RI keywords but preserve auxC override path."""
    iface = ORCAInterface(orca_config, method="DLPNO-CCSD(T)", basis="def2-TZVPP")
    blocks_str = iface._build_input_blocks(
        calc_type="sp",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
        route_extras=["RIJCOSX"],
        aux_c_basis="cc-pVTZ/C",
    )
    assert "RIJCOSX" not in blocks_str
    assert 'auxC  "cc-pVTZ/C"' in blocks_str


def test_composite_strips_aux_basis_from_route_extras(orca_config) -> None:
    """3c composite methods should filter auxiliary basis from route_extras."""
    iface = ORCAInterface(orca_config, method="r2SCAN-3c", basis="def2-mTZVPP")
    blocks_str = iface._build_input_blocks(
        calc_type="opt",
        method="r2SCAN-3c",
        basis="def2-mTZVPP",
        route_extras=["RIJCOSX", "def2/J", "def2-TZVPP/C"],
    )
    assert "def2/J" not in blocks_str
    assert "def2-TZVPP/C" not in blocks_str


def test_regex_anchor_prevents_false_positives(orca_config) -> None:
    """Regex anchor should prevent false positives like SMD/JK (mid-token)."""
    iface = ORCAInterface(orca_config, method="r2SCAN-3c", basis="def2-mTZVPP")
    blocks_str = iface._build_input_blocks(
        calc_type="opt",
        method="r2SCAN-3c",
        basis="def2-mTZVPP",
        route_extras=["GridX/C", "SMD/JK"],
    )
    # SMD/JK should NOT be filtered (/J followed by K, not at end of token)
    assert "SMD/JK" in blocks_str
    # GridX/C SHOULD be filtered (/C at end of token, same format as real aux basis like def2-TZVPP/C)
    assert "GridX/C" not in blocks_str
