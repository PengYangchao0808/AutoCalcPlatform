"""
Input Handler
=============

Unified input handler for multiple molecular input formats.
Supports SMILES, XYZ, GJF, Gaussian/ORCA log files.

Author: QCcalc Team
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Union, List, Optional, Dict, Any, Tuple
import logging
import re
import numpy as np

logger = logging.getLogger(__name__)


class InputFormat(Enum):
    """Supported input formats."""
    SMILES = "smiles"
    XYZ = "xyz"
    GJF = "gjf"
    LOG = "log"
    OUT = "out"
    UNKNOWN = "unknown"


@dataclass
class MolecularInput:
    """
    Standardized molecular input representation.
    
    Attributes:
        name: Molecule name/identifier
        coordinates: Atomic coordinates (N, 3) in Angstrom
        symbols: Element symbols
        charge: Molecular charge (default 0)
        multiplicity: Spin multiplicity (default 1)
        source_format: Original input format
        source_path: Original file path if applicable
        metadata: Additional metadata
    """
    name: str
    coordinates: np.ndarray
    symbols: List[str]
    charge: int = 0
    multiplicity: int = 1
    source_format: InputFormat = InputFormat.UNKNOWN
    source_path: Optional[Path] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate and normalize input."""
        if self.charge > 10 or self.charge < -10:
            logger.warning(f"Unusual charge value: {self.charge}, please verify")
        
        if self.multiplicity < 1 or self.multiplicity > 10:
            raise ValueError(f"Invalid multiplicity: {self.multiplicity}")
        
        if len(self.coordinates) != len(self.symbols):
            raise ValueError(
                f"Coordinate/symbol count mismatch: {len(self.coordinates)} coords, "
                f"{len(self.symbols)} symbols"
            )

    @property
    def n_atoms(self) -> int:
        """Number of atoms."""
        return len(self.symbols)


class MolecularInputHandler:
    """
    Unified handler for molecular input formats.
    
    Automatically detects input format and converts to standardized
    MolecularInput representation.
    """

    @staticmethod
    def detect_format(source: Union[str, Path]) -> InputFormat:
        """
        Detect input format from source string or path.

        Args:
            source: Input source (SMILES string or file path)

        Returns:
            Detected InputFormat
        """
        source_str = str(source).strip()
        
        if MolecularInputHandler._looks_like_smiles(source_str):
            return InputFormat.SMILES
        
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        
        suffix = path.suffix.lower()
        
        format_map = {
            '.xyz': InputFormat.XYZ,
            '.gjf': InputFormat.GJF,
            '.com': InputFormat.GJF,
            '.log': InputFormat.LOG,
            '.out': InputFormat.OUT,
        }
        
        return format_map.get(suffix, InputFormat.UNKNOWN)

    @staticmethod
    def _looks_like_smiles(s: str) -> bool:
        """
        Heuristic check if string looks like SMILES.
        
        Args:
            s: Input string
            
        Returns:
            True if appears to be SMILES
        """
        if Path(s).exists():
            return False
        
        smiles_chars = set('CNOPSFIHclbro()[]=#-+0987654321/\\@%+.$')
        benzene_indicators = ['c1', 'C1', 'c:', 'C:']
        
        if any(s.startswith(b) for b in benzene_indicators):
            return True
        
        return len(s) < 500 and len(set(s) - smiles_chars) < 3

    @classmethod
    def from_source(
        cls,
        source: Union[str, Path],
        name: Optional[str] = None,
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ) -> MolecularInput:
        """
        Create MolecularInput from any supported source.

        Args:
            source: Input source (SMILES or file path)
            name: Molecule name (auto-generated if None)
            charge: Molecular charge (auto-detected if None)
            multiplicity: Spin multiplicity (auto-detected if None)
            **kwargs: Additional parameters

        Returns:
            MolecularInput instance
        """
        format_type = cls.detect_format(source)
        
        if format_type == InputFormat.SMILES:
            return cls.parse_smiles(source, name, charge, multiplicity)
        elif format_type == InputFormat.XYZ:
            return cls.parse_xyz(source, name, charge, multiplicity)
        elif format_type == InputFormat.GJF:
            return cls.parse_gjf(source, name, charge, multiplicity)
        elif format_type in (InputFormat.LOG, InputFormat.OUT):
            return cls.parse_log(source, name, charge, multiplicity)
        else:
            raise ValueError(f"Unsupported or unknown input format: {source}")

    @staticmethod
    def parse_smiles(
        smiles: str,
        name: Optional[str] = None,
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ) -> MolecularInput:
        """
        Parse SMILES string to 3D structure using RDKit.

        Args:
            smiles: SMILES string
            name: Molecule name
            charge: Molecular charge
            multiplicity: Spin multiplicity

        Returns:
            MolecularInput with 3D coordinates
        """
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem
        except ImportError:
            raise ImportError(
                "RDKit is required for SMILES processing. "
                "Install with: pip install rdkit"
            )
        
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"Invalid SMILES: {smiles}")
        
        mol = Chem.AddHs(mol)
        
        try:
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            result = AllChem.EmbedMolecule(mol, params)
            if result == -1:
                AllChem.EmbedMolecule(mol, randomSeed=42)
            
            AllChem.MMFFSanitizeMolecule(mol)
            AllChem.MMFFOptimizeMolecule(mol)
        except Exception as e:
            logger.warning(f"3D embedding warning: {e}")
        
        conf = mol.GetConformer()
        
        symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
        coordinates = np.array([
            [conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y, conf.GetAtomPosition(i).z]
            for i in range(mol.GetNumAtoms())
        ])
        
        if charge is None:
            try:
                charge = Chem.GetFormalCharge(mol)
            except Exception:
                charge = 0
        
        if multiplicity is None:
            num_radical = sum(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
            multiplicity = num_radical + 1
            if multiplicity < 1:
                multiplicity = 1
        
        name = name or f"mol_{re.sub(r'[^a-zA-Z0-9]', '_', smiles[:20])}_{hash(smiles) % 10000}"
        
        return MolecularInput(
            name=name,
            coordinates=coordinates,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            source_format=InputFormat.SMILES,
            metadata={'smiles': smiles}
        )

    @staticmethod
    def parse_xyz(
        xyz_path: Path,
        name: Optional[str] = None,
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ) -> MolecularInput:
        """
        Parse XYZ file.

        Args:
            xyz_path: Path to XYZ file
            name: Molecule name
            charge: Molecular charge
            multiplicity: Spin multiplicity

        Returns:
            MolecularInput instance
        """
        from cccp.utils.file_io import read_xyz, read_xyz_with_energy

        xyz_path = Path(xyz_path)
        coordinates, symbols = read_xyz(xyz_path)

        coords_with_energy, _, energy = read_xyz_with_energy(xyz_path)
        if energy is not None:
            coordinates = coords_with_energy

        comment_charge: int = 0
        comment_mult: int = 1
        if charge is None or multiplicity is None:
            try:
                with open(xyz_path, encoding="utf-8") as fh:
                    lines = fh.readlines()
                if len(lines) >= 2:
                    comment = lines[1].strip()
                    chg_match = re.search(r"charge\s*=\s*(-?\d+)", comment, re.IGNORECASE)
                    if chg_match:
                        comment_charge = int(chg_match.group(1))
                    mult_match = re.search(r"mult(?:i(?:plicity)?)?\s*=\s*(\d+)", comment, re.IGNORECASE)
                    if mult_match:
                        comment_mult = int(mult_match.group(1))
            except (OSError, UnicodeDecodeError):
                pass

        charge = charge if charge is not None else comment_charge
        multiplicity = multiplicity if multiplicity is not None else comment_mult

        name = name or xyz_path.stem
        
        return MolecularInput(
            name=name,
            coordinates=coordinates,
            symbols=symbols,
            charge=charge,
            multiplicity=multiplicity,
            source_format=InputFormat.XYZ,
            source_path=xyz_path,
            metadata={'source_xyz': str(xyz_path), 'energy': energy}
        )

    @staticmethod
    def parse_gjf(
        gjf_path: Path,
        name: Optional[str] = None,
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ) -> MolecularInput:
        """
        Parse Gaussian input file (.gjf).

        Args:
            gjf_path: Path to GJF file
            name: Molecule name
            charge: Molecular charge (parsed from file if None)
            multiplicity: Spin multiplicity (parsed from file if None)

        Returns:
            MolecularInput instance
        """
        from cccp.utils.file_io import read_gjf

        gjf_path = Path(gjf_path)
        coordinates, symbols, file_charge, file_multiplicity = read_gjf(gjf_path)

        if charge is None:
            charge = file_charge
        if multiplicity is None:
            multiplicity = file_multiplicity

        name = name or gjf_path.stem
        
        return MolecularInput(
            name=name,
            coordinates=coordinates,
            symbols=symbols,
            charge=charge if charge is not None else 0,
            multiplicity=multiplicity if multiplicity is not None else 1,
            source_format=InputFormat.GJF,
            source_path=gjf_path,
            metadata={'source_gjf': str(gjf_path)}
        )

    @staticmethod
    def parse_log(
        log_path: Path,
        name: Optional[str] = None,
        charge: Optional[int] = None,
        multiplicity: Optional[int] = None
    ) -> MolecularInput:
        """
        Parse Gaussian/ORCA log file to extract final geometry.

        Args:
            log_path: Path to log file
            name: Molecule name
            charge: Molecular charge
            multiplicity: Spin multiplicity

        Returns:
            MolecularInput instance
        """
        from cccp.utils.geometry_tools import LogParser

        log_path = Path(log_path)
        coords, symbols, error = LogParser.extract_last_converged_coords(log_path)

        if coords is None:
            raise ValueError(f"Could not parse coordinates from {log_path}: {error}")

        name = name or log_path.stem

        return MolecularInput(
            name=name,
            coordinates=coords,
            symbols=symbols,
            charge=charge if charge is not None else 0,
            multiplicity=multiplicity if multiplicity is not None else 1,
            source_format=InputFormat.LOG if log_path.suffix == '.log' else InputFormat.OUT,
            source_path=log_path,
            metadata={'source_log': str(log_path)}
        )


def load_batch_inputs(
    batch_file: Path,
    name_template: str = "mol_{index}",
    charge: Optional[int] = None,
    multiplicity: Optional[int] = None
) -> List[MolecularInput]:
    """
    Load multiple molecular inputs from a batch file.
    
    Each line can be:
    - SMILES string
    - File path (XYZ, GJF, LOG, OUT)
    - Empty line (skipped)
    - Comment line starting with # (skipped)

    Args:
        batch_file: Path to batch file
        name_template: Template for generating molecule names
        charge: Default charge for all molecules
        multiplicity: Default multiplicity for all molecules

    Returns:
        List of MolecularInput instances
    """
    handler = MolecularInputHandler()
    inputs = []
    
    with open(batch_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        if not line or line.startswith('#'):
            continue
        
        try:
            mol_input = handler.from_source(
                line,
                name=name_template.format(index=i+1),
                charge=charge,
                multiplicity=multiplicity
            )
            inputs.append(mol_input)
        except Exception as e:
            logger.error(f"Failed to parse line {i+1}: {line[:50]}... Error: {e}")
            continue
    
    return inputs
