"""Tests for acp.io — StructureReader, StructureWriter, InputFormat."""

from __future__ import annotations

import numpy as np

from acp.io import InputFormat, StructureReader, StructureWriter
from acp.core.models import Structure


class TestInputFormat:
    def test_input_format_values(self):
        """InputFormat enum has expected members."""
        assert InputFormat.SMILES is not None
        assert InputFormat.XYZ is not None
        assert InputFormat.GJF is not None
        assert InputFormat.LOG is not None
        assert InputFormat.OUT is not None
        assert InputFormat.UNKNOWN is not None


class TestStructureReader:
    def test_detect_format_smiles(self):
        """detect_format identifies SMILES strings."""
        reader = StructureReader()
        assert reader.detect_format("CCO") == InputFormat.SMILES

    def test_detect_format_xyz(self, tmp_path):
        """detect_format identifies .xyz files."""
        xyz_path = tmp_path / "test.xyz"
        xyz_path.write_text("1\n\nH 0.0 0.0 0.0\n")
        reader = StructureReader()
        assert reader.detect_format(str(xyz_path)) == InputFormat.XYZ

    def test_read_xyz_file(self, tmp_path):
        """StructureReader.read() parses a valid XYZ file."""
        xyz_path = tmp_path / "h2.xyz"
        xyz_path.write_text("2\n\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n")

        reader = StructureReader()
        structure = reader.read(xyz_path)

        assert isinstance(structure, Structure)
        assert structure.symbols == ["H", "H"]
        assert structure.coordinates is not None
        assert np.asarray(structure.coordinates).shape == (2, 3)

    def test_read_xyz_file_with_charge_and_multiplicity_overrides(self, tmp_path):
        """StructureReader.read() accepts charge/multiplicity overrides."""
        xyz_path = tmp_path / "h2_override.xyz"
        xyz_path.write_text("2\n\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n")

        reader = StructureReader()
        structure = reader.read(xyz_path, charge=1, multiplicity=2)

        assert structure.charge == 1
        assert structure.multiplicity == 2


class TestStructureWriter:
    def test_write_xyz(self, tmp_path):
        """StructureWriter.write_xyz produces a valid XYZ file."""
        structure = Structure(
            id="h2_test",
            symbols=["H", "H"],
            coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]]),
        )
        out_path = tmp_path / "output.xyz"

        result = StructureWriter.write_xyz(structure, out_path)

        assert result == out_path
        assert out_path.exists()
        content = out_path.read_text()
        assert "2" in content
        assert "H" in content

    def test_write_json(self, tmp_path):
        """StructureWriter.write_json produces valid JSON."""
        structure = Structure(
            id="json_test",
            symbols=["C"],
            coordinates=np.array([[0.0, 0.0, 0.0]]),
            metadata={"source": "test"},
        )
        out_path = tmp_path / "output.json"

        result = StructureWriter.write_json(structure, out_path)

        assert result == out_path
        assert out_path.exists()

        import json

        data = json.loads(out_path.read_text())
        assert data["id"] == "json_test"
        assert data["symbols"] == ["C"]
        assert data["metadata"]["source"] == "test"
