"""Molecular structure readers and writers.

Delegates to conformer_search.io.input_handler for actual parsing.
This module provides the new public API as a thin wrapper.
"""

from __future__ import annotations

import json
from enum import Enum, auto
from pathlib import Path

import numpy as np

from acp.core.models import Structure


class InputFormat(Enum):
    """Supported input formats for molecular structures."""

    SMILES = auto()
    XYZ = auto()
    GJF = auto()
    LOG = auto()
    OUT = auto()
    UNKNOWN = auto()


class StructureReader:
    """Read molecular structures from SMILES strings or structure files.

    Delegates parsing to the existing MolecularInputHandler from
    conformer_search for format detection and coordinate extraction.
    """

    def read(
        self,
        source: str | Path,
        charge: int | None = None,
        multiplicity: int | None = None,
        name: str | None = None,
    ) -> Structure:
        """Auto-detect format and return a Structure.

        Args:
            source: SMILES string or path to a structure file.
            charge: Molecular charge (overrides auto-detected value).
            multiplicity: Spin multiplicity (overrides auto-detected value).
            name: Optional molecule name forwarded to the parser; when omitted
                the parser derives it from the file stem.

        Returns:
            Structure instance with parsed coordinates and metadata.
        """
        from conformer_search.io.input_handler import MolecularInputHandler

        result = MolecularInputHandler.from_source(
            source,
            name=name,
            charge=charge,
            multiplicity=multiplicity,
        )

        return Structure(
            id=result.name,
            charge=result.charge,
            multiplicity=result.multiplicity,
            symbols=list(result.symbols),
            coordinates=(
                result.coordinates.copy()
                if result.coordinates is not None
                else None
            ),
            metadata={
                "source_format": str(result.source_format),
                "source_path": (
                    str(result.source_path) if result.source_path else None
                ),
                **result.metadata,
            },
        )

    def read_to_ensemble(self, sources: list[str | Path]) -> list[Structure]:
        """Read multiple sources into a list of Structures.

        Args:
            sources: List of SMILES strings or file paths.

        Returns:
            List of Structure instances.
        """
        return [self.read(src) for src in sources]

    def detect_format(self, source: str | Path) -> InputFormat:
        """Detect input format without fully parsing the file.

        Args:
            source: SMILES string or file path.

        Returns:
            Detected InputFormat enum value.
        """
        from conformer_search.io.input_handler import (
            InputFormat as OldInputFormat,
            MolecularInputHandler,
        )

        fmt = MolecularInputHandler.detect_format(source)
        mapping = {
            OldInputFormat.SMILES: InputFormat.SMILES,
            OldInputFormat.XYZ: InputFormat.XYZ,
            OldInputFormat.GJF: InputFormat.GJF,
            OldInputFormat.LOG: InputFormat.LOG,
            OldInputFormat.OUT: InputFormat.OUT,
        }
        return mapping.get(fmt, InputFormat.UNKNOWN)

    @staticmethod
    def _build_smiles_heuristic():
        """Placeholder; heuristic logic delegated to MolecularInputHandler."""
        pass


class StructureWriter:
    """Write structures to various output formats."""

    @staticmethod
    def write_xyz(structure: Structure, path: str | Path) -> Path:
        """Write structure as XYZ file.

        Args:
            structure: Structure to write.
            path: Output file path.

        Returns:
            The resolved Path that was written to.
        """
        from conformer_search.utils.file_io import write_xyz

        if structure.coordinates is None:
            raise ValueError("Cannot write XYZ without coordinates")

        path = Path(path)
        coordinates = np.asarray(structure.coordinates, dtype=float)
        write_xyz(
            path,
            coordinates,
            structure.symbols,
            title=structure.id,
        )
        return path

    @staticmethod
    def write_json(structure: Structure, path: str | Path) -> Path:
        """Write structure metadata as JSON.

        Args:
            structure: Structure to serialize.
            path: Output file path.

        Returns:
            The resolved Path that was written to.
        """
        path = Path(path)
        coordinates = None
        if structure.coordinates is not None:
            coordinates = np.asarray(structure.coordinates, dtype=float).tolist()

        data = {
            "id": structure.id,
            "charge": structure.charge,
            "multiplicity": structure.multiplicity,
            "symbols": structure.symbols,
            "coordinates": coordinates,
            "metadata": structure.metadata,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path
