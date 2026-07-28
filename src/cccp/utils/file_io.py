"""
File I/O Utilities
==================

File reading and writing utilities for molecular structures.
Supports XYZ, GJF, and various quantum chemistry output formats.

Author: QCcalc Team (adapted from RPH)
"""

from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
import numpy as np
import logging
import re

logger = logging.getLogger(__name__)


def read_xyz(xyz_file: Path) -> Tuple[np.ndarray, List[str]]:
    """
    Read XYZ file.

    Args:
        xyz_file: Path to XYZ file

    Returns:
        Tuple of (coordinates, symbols)
        - coordinates: (N, 3) numpy array
        - symbols: List of element symbols
    """
    xyz_file = Path(xyz_file)
    if not xyz_file.exists():
        raise FileNotFoundError(f"XYZ file not found: {xyz_file}")

    with open(xyz_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if len(lines) < 2:
        logger.warning(f"Malformed XYZ file (too few lines): {xyz_file}")
        return np.empty((0, 3)), []

    try:
        n_atoms = int(lines[0].strip())
    except ValueError:
        logger.warning(f"Malformed XYZ file (invalid atom count): {xyz_file}")
        return np.empty((0, 3)), []

    title = lines[1].strip()

    coords = []
    symbols = []

    for line in lines[2:2+n_atoms]:
        parts = line.strip().split()
        if len(parts) >= 4:
            symbols.append(parts[0])
            coords.append([float(parts[1]), float(parts[2]), float(parts[3])])

    return np.array(coords), symbols


def write_xyz(xyz_file: Path, coordinates: np.ndarray, symbols: List[str],
              title: str = "", energy: float = None, comment: str = ""):
    """
    Write XYZ file.

    Args:
        xyz_file: Output XYZ file path
        coordinates: (N, 3) coordinate array
        symbols: List of element symbols
        title: Title line (optional)
        energy: Energy value to include in title (optional)
        comment: Additional comment to append to title
    """
    n_atoms = len(symbols)
    
    if energy is not None:
        title_line = f"{title} | Energy: {energy:.10f} | {comment}".strip()
    else:
        title_line = f"{title} | {comment}".strip() if comment else title

    with open(xyz_file, 'w', encoding='utf-8') as f:
        f.write(f"{n_atoms}\n")
        f.write(f"{title_line}\n")
        for i, (symbol, coord) in enumerate(zip(symbols, coordinates)):
            f.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")


def read_gjf(gjf_file: Path) -> Tuple[np.ndarray, List[str], int, int]:
    """
    Read Gaussian input file (.gjf).

    Args:
        gjf_file: Path to Gaussian input file

    Returns:
        Tuple of (coordinates, symbols, charge, multiplicity)
    """
    gjf_file = Path(gjf_file)
    if not gjf_file.exists():
        raise FileNotFoundError(f"GJF file not found: {gjf_file}")

    with open(gjf_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    coords = []
    symbols = []
    charge = 0
    multiplicity = 1

    reading_coords = False
    for line in lines:
        line_stripped = line.strip()
        
        if not line_stripped or line_stripped.startswith('#'):
            continue
            
        if '--' in line_stripped:
            reading_coords = True
            continue
            
        if reading_coords:
            parts = line_stripped.split()
            if len(parts) >= 4:
                try:
                    symbols.append(parts[0])
                    coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
                except ValueError:
                    if len(parts) >= 6:
                        symbols.append(parts[0])
                        coords.append([float(parts[3]), float(parts[4]), float(parts[5])])
            elif len(parts) == 2:
                charge_multiplicity = parts[0].split('.')
                if len(charge_multiplicity) == 2:
                    try:
                        charge = int(charge_multiplicity[0])
                        multiplicity = int(charge_multiplicity[1])
                    except ValueError:
                        pass

    if not coords:
        route_section = False
        for line in lines:
            if line.strip().startswith('#'):
                route_section = True
                continue
            if route_section and line.strip() and not line.startswith('%') and not line.strip().startswith('#'):
                parts = line.strip().split()
                if len(parts) >= 4:
                    try:
                        symbols.append(parts[0])
                        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
                    except ValueError:
                        pass
                elif len(parts) == 2:
                    charge_multiplicity = parts[0].split('.')
                    if len(charge_multiplicity) == 2:
                        try:
                            charge = int(charge_multiplicity[0])
                            multiplicity = int(charge_multiplicity[1])
                        except ValueError:
                            pass

    if not coords:
        raise ValueError(f"No coordinates found in GJF file: {gjf_file}")

    return np.array(coords), symbols, charge, multiplicity


def read_energy_from_gaussian(log_file: Path) -> Optional[float]:
    """
    Extract SCF energy from Gaussian log file.

    Args:
        log_file: Path to Gaussian log file

    Returns:
        SCF energy in Hartree, or None if not found
    """
    log_file = Path(log_file)
    if not log_file.exists():
        return None

    energy = None
    with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if 'SCF Done' in line:
                parts = line.split()
                try:
                    energy = float(parts[4])
                except (ValueError, IndexError):
                    pass

    return energy


def read_xyz_with_energy(xyz_file: Path) -> Tuple[np.ndarray, List[str], Optional[float]]:
    """
    Read XYZ file and extract energy from comment line.

    Args:
        xyz_file: Path to XYZ file

    Returns:
        Tuple of (coordinates, symbols, energy)
        - energy: Energy from comment line if found, else None
    """
    xyz_file = Path(xyz_file)
    coords, symbols = read_xyz(xyz_file)
    
    energy = None
    with open(xyz_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    if len(lines) >= 2:
        title = lines[1].strip()
        energy_match = re.search(r'Energy:\s*([-+]?\d+\.?\d*)', title)
        if energy_match:
            energy = float(energy_match.group(1))
    
    return coords, symbols, energy


def write_json(data: Dict[str, Any], json_file: Path):
    """Write data to JSON file."""
    import json
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def read_json(json_file: Path) -> Dict[str, Any]:
    """Read JSON file."""
    json_file = Path(json_file)
    import json
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def ensure_dir(directory: Path):
    """Ensure directory exists, create if not."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_xyz_multiframe(xyz_file: Path) -> Tuple[np.ndarray, List[str]]:
    """
    Read multi-frame XYZ file containing multiple conformer structures.

    Parses a concatenated multi-frame XYZ file where each frame is a standard
    XYZ block: atom count line, title/comment line, and coordinate lines.
    All frames must have the same atom count. Coordinates are stacked into
    a single (n_frames * n_atoms, 3) array.

    Args:
        xyz_file: Path to multi-frame XYZ file

    Returns:
        Tuple of (all_coordinates, symbols)
        - all_coordinates: (n_frames * n_atoms, 3) stacked numpy array of all
          conformer coordinates
        - symbols: List of element symbols of length n_atoms

    Raises:
        ValueError: If atom count is inconsistent across frames
    """
    xyz_file = Path(xyz_file)
    if not xyz_file.exists():
        raise FileNotFoundError(f"XYZ file not found: {xyz_file}")

    with open(xyz_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if len(lines) == 0:
        return np.empty((0, 3)), []

    all_coords = []
    symbols = []
    n_atoms = None
    offset = 0
    frame_idx = 0

    while offset < len(lines):
        # Parse atom count from header line
        try:
            atom_count = int(lines[offset].strip())
        except (ValueError, IndexError):
            logger.warning(f"Malformed frame header at line {offset}, skipping")
            offset += 1
            continue

        if atom_count == 0:
            # Zero-atom frame: end of meaningful content
            if frame_idx == 0:
                return np.empty((0, 3)), []
            break

        # Check sufficient lines remain for this frame
        end_line = offset + 2 + atom_count
        if end_line > len(lines):
            logger.warning(
                f"Trailing incomplete frame at offset {offset}: "
                f"expected {atom_count} atoms but only "
                f"{len(lines) - offset - 2} lines remain, skipping"
            )
            break

        # Validate consistent atom count
        if n_atoms is None:
            n_atoms = atom_count
        elif atom_count != n_atoms:
            raise ValueError(
                f"Atom count inconsistency in multi-frame XYZ at "
                f"frame {frame_idx}: expected {n_atoms}, got {atom_count}"
            )

        # Parse coordinate lines
        for i in range(atom_count):
            parts = lines[offset + 2 + i].strip().split()
            if len(parts) >= 4:
                if frame_idx == 0:
                    symbols.append(parts[0])
                all_coords.append([float(parts[1]), float(parts[2]), float(parts[3])])

        offset += atom_count + 2
        frame_idx += 1

    return np.array(all_coords), symbols


def write_xyz_multiframe(
    xyz_file: Path,
    all_coords: np.ndarray,
    symbols: List[str],
    titles: List[str] = None,
    energies: List[float] = None,
) -> None:
    """
    Write multi-frame XYZ file from stacked conformer coordinates.

    Writes all conformers into a single concatenated multi-frame XYZ file.
    Each frame is a standard XYZ block: atom count line, title line,
    and coordinate lines.

    Args:
        xyz_file: Output XYZ file path
        all_coords: (n_frames * n_atoms, 3) stacked coordinate array, as
            returned by read_xyz_multiframe
        symbols: List of element symbols (length n_atoms)
        titles: Optional list of title strings per frame (length n_frames).
            Ignored if `energies` is provided.
        energies: Optional list of energy values per frame (length n_frames).
            When provided, title line includes "Frame {i} | Energy: {value}".

    Raises:
        ValueError: If all_coords length is not divisible by n_atoms
    """
    n_atoms = len(symbols)

    if n_atoms == 0:
        with open(xyz_file, 'w', encoding='utf-8') as f:
            f.write("0\n\n")
        return

    if len(all_coords) % n_atoms != 0:
        raise ValueError(
            f"Coordinate array length ({len(all_coords)}) is not "
            f"divisible by number of atoms ({n_atoms})"
        )

    n_frames = len(all_coords) // n_atoms

    if n_frames == 0:
        with open(xyz_file, 'w', encoding='utf-8') as f:
            f.write("0\n\n")
        return

    with open(xyz_file, 'w', encoding='utf-8') as f:
        for i in range(n_frames):
            # Build title line
            if energies is not None and i < len(energies):
                title_line = f"Frame {i} | Energy: {energies[i]:.10f}"
            elif titles is not None and i < len(titles):
                title_line = titles[i]
            else:
                title_line = f"Frame {i}"

            f.write(f"{n_atoms}\n")
            f.write(f"{title_line}\n")

            # Write coordinates for this frame
            start = i * n_atoms
            end = start + n_atoms
            for j in range(start, end):
                symbol = symbols[j - start]
                x, y, z = all_coords[j]
                f.write(
                    f"{symbol:2s} {x:15.10f} {y:15.10f} {z:15.10f}\n"
                )
