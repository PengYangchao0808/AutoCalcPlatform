"""Tests for ORCA %basis block generation (Phase 5.3).

Verifies that auxiliary basis sets are correctly rendered in %basis blocks
rather than being appended to the ! route line.
"""

from __future__ import annotations

import pytest

from cccp.qc.interfaces.orca import ORCAInterface


@pytest.fixture
def orca_config():
    return {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }


def test_normal_dft_generates_basis_block(orca_config) -> None:
    """Normal DFT should generate independent %basis block, not append to ! line."""
    iface = ORCAInterface(orca_config, method="B3LYP", basis="def2-TZVPP")
    blocks_str, _ = iface._build_input_blocks(
        calc_type="opt",
        method="B3LYP",
        basis="def2-TZVPP",
        route_extras=["RIJCOSX"],
        aux_j_basis="def2/J",
        symbols=["C", "H"],  # light molecule → auto Hessian resolves to interval=0
    )
    assert "! B3LYP def2-TZVPP Opt RIJCOSX" in blocks_str
    assert "%basis" in blocks_str
    assert 'auxJ  "def2/J"' in blocks_str
    # Ensure def2/J does not appear in ! line
    route_line = [l for l in blocks_str.split("\n") if l.startswith("!")][0]
    assert "def2/J" not in route_line


def test_double_hybrid_generates_auxj_and_auxc(orca_config) -> None:
    """Double-hybrid should generate both auxJ and auxC."""
    iface = ORCAInterface(orca_config, method="PWPB95", basis="def2-TZVPP")
    blocks_str, _ = iface._build_input_blocks(
        calc_type="sp",
        method="PWPB95",
        basis="def2-TZVPP",
        route_extras=["RIJCOSX"],
        aux_j_basis="def2/J",
        aux_c_basis="def2-TZVPP/C",
    )
    assert 'auxJ  "def2/J"' in blocks_str
    assert 'auxC  "def2-TZVPP/C"' in blocks_str


def test_dlpno_uses_unified_basis_block_after_merge(orca_config) -> None:
    """DLPNO should use unified %basis rendering, not read basis_block template."""
    iface = ORCAInterface(orca_config, method="DLPNO-CCSD(T)", basis="def2-TZVPP")
    blocks_str, _ = iface._build_input_blocks(
        calc_type="sp",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
    )
    # Default auxJ/auxC from METHOD_META default_aux_j/default_aux_c
    assert 'auxJ  "def2/J"' in blocks_str
    assert 'auxC  "def2-TZVPP/C"' in blocks_str
    # Explicit override
    blocks_str2, _ = iface._build_input_blocks(
        calc_type="sp",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
        aux_c_basis="cc-pVTZ/C",
    )
    assert 'auxC  "cc-pVTZ/C"' in blocks_str2


def test_no_aux_basis_no_basis_block(orca_config) -> None:
    """Without aux basis, no %basis block should be generated for normal DFT."""
    iface = ORCAInterface(orca_config, method="B3LYP", basis="def2-TZVPP")
    blocks_str, _ = iface._build_input_blocks(
        calc_type="sp",
        method="B3LYP",
        basis="def2-TZVPP",
    )
    assert "%basis" not in blocks_str
