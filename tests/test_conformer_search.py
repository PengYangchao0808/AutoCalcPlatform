"""
Tests for ConformerSearch
"""

import pytest
from pathlib import Path
import tempfile
import numpy as np

from conformer_search.io import MolecularInput, MolecularInputHandler, InputFormat, load_batch_inputs
from conformer_search.utils.file_io import read_xyz, read_xyz_multiframe, write_xyz_multiframe


class TestMolecularInputHandler:
    """Test molecular input handling."""

    def test_detect_smiles(self):
        """Test SMILES detection."""
        assert MolecularInputHandler.detect_format("CCO") == InputFormat.SMILES
        assert MolecularInputHandler.detect_format("c1ccccc1") == InputFormat.SMILES

    def test_detect_xyz(self):
        """Test XYZ file detection."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("3\nTest\nC 0 0 0\nH 1 0 0\nH 0 1 0\n")
            xyz_path = f.name
        
        try:
            assert MolecularInputHandler.detect_format(xyz_path) == InputFormat.XYZ
        finally:
            Path(xyz_path).unlink()

    def test_parse_smiles(self):
        """Test SMILES parsing."""
        handler = MolecularInputHandler()
        mol_input = handler.parse_smiles("CCO", name="ethanol")
        
        assert mol_input.name == "ethanol"
        assert mol_input.n_atoms > 0
        assert len(mol_input.symbols) == mol_input.n_atoms
        assert mol_input.source_format == InputFormat.SMILES
        assert mol_input.metadata['smiles'] == "CCO"

    def test_parse_xyz(self):
        """Test XYZ file parsing."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("3\nTest molecule\nC 0.0 0.0 0.0\nH 1.0 0.0 0.0\nO -0.5 0.866 0.0\n")
            xyz_path = f.name
        
        try:
            handler = MolecularInputHandler()
            mol_input = handler.parse_xyz(Path(xyz_path), name="test_mol")
            
            assert mol_input.name == "test_mol"
            assert mol_input.n_atoms == 3
            assert 'C' in mol_input.symbols
            assert 'H' in mol_input.symbols
            assert 'O' in mol_input.symbols
            assert mol_input.source_format == InputFormat.XYZ
        finally:
            Path(xyz_path).unlink()

    def test_from_source_smiles(self):
        """Test from_source with SMILES."""
        handler = MolecularInputHandler()
        mol_input = handler.from_source("CCO")
        
        assert mol_input.source_format == InputFormat.SMILES

    def test_batch_loading(self):
        """Test batch input loading."""
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w') as f:
            f.write("CCO\n")
            f.write("c1ccccc1\n")
            f.write("# This is a comment\n")
            f.write("\n")
            f.write("C=O\n")
            batch_path = f.name
        
        try:
            inputs = load_batch_inputs(Path(batch_path))
            
            assert len(inputs) == 3
            assert all(isinstance(m, MolecularInput) for m in inputs)
        finally:
            Path(batch_path).unlink()


class TestCandidateSet:
    """Test candidate set operations."""

    def test_boltzmann_weights(self):
        """Test Boltzmann weight calculation."""
        from conformer_search.core import CandidateSet, ConformerCandidate
        
        candidates = [
            ConformerCandidate(index=0, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=0.0),
            ConformerCandidate(index=1, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=1.0),
            ConformerCandidate(index=2, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=2.0),
        ]
        
        cs = CandidateSet(candidates=candidates)
        cs.calculate_boltzmann_weights(temperature=298.15)
        
        assert abs(cs[0].weight - cs[1].weight) > 0.01
        assert cs[0].weight > cs[1].weight
        assert cs[1].weight > cs[2].weight
        assert abs(cs[0].weight + cs[1].weight + cs[2].weight - 1.0) < 0.001

    def test_window_selection(self):
        """Test energy window selection."""
        from conformer_search.core import CandidateSet, ConformerCandidate
        
        candidates = [
            ConformerCandidate(index=0, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=0.0),
            ConformerCandidate(index=1, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=1.0),
            ConformerCandidate(index=2, coordinates=np.zeros((3, 3)), symbols=['C', 'H', 'H'], energy=5.0),
        ]
        
        cs = CandidateSet(candidates=candidates)
        selected = cs.select_by_window(window_kcal=3.0)
        
        assert len(selected) == 2
        assert selected[0].energy == 0.0
        assert selected[1].energy == 1.0


class TestProtocols:
    """Test protocol resolution."""

    def test_default_config(self):
        """Test default configuration loading."""
        from conformer_search.config import _get_default_config
        
        config = _get_default_config()
        
        assert 'executables' in config
        assert 'resources' in config
        assert 'protocols' in config
        assert config['protocols']['default'] == 'censo-lite'

    def test_protocol_spec_resolve(self):
        """Test protocol specification resolution."""
        from conformer_search.core import resolve_protocol_spec
        
        config = {
            'protocols': {
                'ext': {
                    'two_stage_enabled': True,
                    'ngeom_default': 5,
                    'ngeom_max': 10,
                    'funnel': {
                        'search_mode': 'crest_gfn2',
                        'clustering_mode': 'isostat',
                        'prescreen_mode': 'none',
                        'rerank_mode': 'none'
                    },
                    'handoff': {
                        'enabled': True,
                        'mode': 'optimize_rank1',
                        'ranking_after_handoff': 'final_sp_minimum'
                    }
                }
            }
        }
        
        spec = resolve_protocol_spec(config, 'ext')
        
        assert spec.name == 'ext'
        assert spec.two_stage_enabled == True
        assert spec.ngeom_default == 5
        assert spec.ngeom_max == 10


class TestMultiFrameXYZ:
    """Test multi-frame XYZ I/O functions."""

    def test_read_multiframe_three_conformers(self):
        """Read 3-frame XYZ with 3-atom molecule, verify correct parsing."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("3\nFrame 0\nC  0.0  0.0  0.0\nH  1.0  0.0  0.0\nH  0.0  1.0  0.0\n")
            f.write("3\nFrame 1\nC  0.1  0.0  0.0\nH  1.0  0.1  0.0\nH  0.0  1.1  0.0\n")
            f.write("3\nFrame 2\nC  0.2  0.0  0.0\nH  1.0  0.2  0.0\nH  0.0  1.2  0.0\n")
            xyz_path = f.name

        try:
            coords, symbols = read_xyz_multiframe(xyz_path)
            assert len(coords) == 9  # 3 atoms × 3 frames
            assert len(symbols) == 3  # C, H, H
            n_conformers = len(coords) // len(symbols)
            assert n_conformers == 3
            assert symbols == ['C', 'H', 'H']
        finally:
            Path(xyz_path).unlink()

    def test_read_multiframe_single_atom(self):
        """Read 3-frame XYZ with 1-atom molecule, verify no div-by-zero."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("1\nFrame 0\nHe  0.0  0.0  0.0\n")
            f.write("1\nFrame 1\nHe  1.0  0.0  0.0\n")
            f.write("1\nFrame 2\nHe  2.0  0.0  0.0\n")
            xyz_path = f.name

        try:
            coords, symbols = read_xyz_multiframe(xyz_path)
            assert len(coords) == 3  # 3 frames × 1 atom
            assert len(symbols) == 1
            assert symbols == ['He']
            n_conformers = len(coords) // len(symbols)
            assert n_conformers == 3
        finally:
            Path(xyz_path).unlink()

    def test_write_multiframe_roundtrip(self):
        """Write then read back 2-frame data, verify no data loss."""
        symbols = ['C', 'H', 'H']
        coords = np.array([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.1, 0.0, 0.0],
            [1.0, 0.1, 0.0],
            [0.0, 1.1, 0.0],
        ])
        titles = ['Geometry A', 'Geometry B']

        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            write_xyz_multiframe(f.name, coords, symbols, titles=titles)
            xyz_path = f.name

        try:
            coords_back, symbols_back = read_xyz_multiframe(xyz_path)
            assert symbols_back == symbols
            assert np.allclose(coords_back, coords)
            n_conformers = len(coords_back) // len(symbols_back)
            assert n_conformers == 2
        finally:
            Path(xyz_path).unlink()

    def test_read_multiframe_empty(self):
        """Read empty multi-frame XYZ, must not crash."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("0\nempty title\n")
            xyz_path = f.name

        try:
            coords, symbols = read_xyz_multiframe(xyz_path)
            assert len(coords) == 0
            assert len(symbols) == 0
        finally:
            Path(xyz_path).unlink()

    def test_existing_read_xyz_still_single_frame(self):
        """Existing read_xyz returns only the first frame from a multi-frame file."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("3\nFrame 0\nC  0.0  0.0  0.0\nH  1.0  0.0  0.0\nH  0.0  1.0  0.0\n")
            f.write("3\nFrame 1\nC  0.1  0.0  0.0\nH  1.0  0.1  0.0\nH  0.0  1.1  0.0\n")
            f.write("3\nFrame 2\nC  0.2  0.0  0.0\nH  1.0  0.2  0.0\nH  0.0  1.2  0.0\n")
            xyz_path = f.name

        try:
            coords, symbols = read_xyz(xyz_path)
            assert len(coords) == 3  # only first frame for 3-atom molecule
            assert len(symbols) == 3
        finally:
            Path(xyz_path).unlink()

    def test_multiframe_atom_count_mismatch(self):
        """Atom count mismatch across frames raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False, mode='w') as f:
            f.write("3\nFrame 0\nC  0.0  0.0  0.0\nH  1.0  0.0  0.0\nH  0.0  1.0  0.0\n")
            f.write("2\nFrame 1\nC  0.1  0.0  0.0\nH  1.0  0.1  0.0\n")
            xyz_path = f.name

        try:
            with pytest.raises(ValueError):
                read_xyz_multiframe(xyz_path)
        finally:
            Path(xyz_path).unlink()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
