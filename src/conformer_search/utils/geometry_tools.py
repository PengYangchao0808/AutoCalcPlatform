"""
Geometry Tools - Universal Geometric Operations
===============================================

Provides various geometric computation and coordinate manipulation utilities.
Non-business logic, extracted from RPH.

Author: QCcalc Team
"""

import numpy as np
from typing import Tuple, List, Optional, Dict, Any
import logging
from pathlib import Path
import re

from conformer_search.utils.file_io import read_xyz
from conformer_search.utils.constants import ELEMENT_MASS, ATOMIC_NUMBER

logger = logging.getLogger(__name__)


class GeometryUtils:
    """
    Universal geometry utility class.
    
    Provides various geometric calculations and transformations.
    These are low-level utilities without specific chemical reaction logic.
    """

    @staticmethod
    def calculate_distance(coords: np.ndarray, atom_i: int, atom_j: int) -> float:
        """
        Calculate distance between two atoms.

        Args:
            coords: Coordinate array (N, 3)
            atom_i: First atom index
            atom_j: Second atom index

        Returns:
            Distance in Angstrom
        """
        if atom_i >= len(coords) or atom_j >= len(coords):
            raise IndexError(f"Atom index out of range: {atom_i}, {atom_j}")

        vec = coords[atom_i] - coords[atom_j]
        return float(np.linalg.norm(vec))

    @staticmethod
    def calculate_angle(coords: np.ndarray, atom_i: int, atom_j: int, atom_k: int) -> float:
        """
        Calculate angle between three atoms (i-j-k).

        Args:
            coords: Coordinate array (N, 3)
            atom_i: First atom index
            atom_j: Center atom index
            atom_k: Third atom index

        Returns:
            Angle in degrees
        """
        vec_i = coords[atom_i] - coords[atom_j]
        vec_k = coords[atom_k] - coords[atom_j]

        cos_angle = np.dot(vec_i, vec_k) / (np.linalg.norm(vec_i) * np.linalg.norm(vec_k))
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        angle = np.arccos(cos_angle)
        return float(np.degrees(angle))

    @staticmethod
    def calculate_dihedral(coords: np.ndarray, atom_i: int, atom_j: int, 
                          atom_k: int, atom_l: int) -> float:
        """
        Calculate dihedral angle (torsion) between four atoms (i-j-k-l).

        Args:
            coords: Coordinate array (N, 3)
            atom_i: First atom index
            atom_j: Second atom index (center)
            atom_k: Third atom index (center)
            atom_l: Fourth atom index

        Returns:
            Dihedral angle in degrees
        """
        b1 = coords[atom_j] - coords[atom_i]
        b2 = coords[atom_k] - coords[atom_j]
        b3 = coords[atom_l] - coords[atom_k]

        b2_norm = b2 / np.linalg.norm(b2)

        v = b1 - np.dot(b1, b2_norm) * b2_norm
        w = b3 - np.dot(b3, b2_norm) * b2_norm

        x = np.dot(v, w)
        y = np.dot(np.cross(b2_norm, v), w)

        angle = np.arctan2(y, x)
        return float(np.degrees(angle))

    @staticmethod
    def center_of_mass(coords: np.ndarray, symbols: List[str]) -> np.ndarray:
        """
        Calculate center of mass.

        Args:
            coords: Coordinate array (N, 3)
            symbols: List of element symbols

        Returns:
            Center of mass coordinates (3,)
        """
        total_mass = 0.0
        com = np.zeros(3)
        
        for i, symbol in enumerate(symbols):
            mass = ELEMENT_MASS.get(symbol, 1.0)
            com += mass * coords[i]
            total_mass += mass
        
        if total_mass > 0:
            com /= total_mass
        
        return com

    @staticmethod
    def moment_of_inertia(coords: np.ndarray, symbols: List[str]) -> np.ndarray:
        """
        Calculate moment of inertia tensor.

        Args:
            coords: Coordinate array (N, 3)
            symbols: List of element symbols

        Returns:
            3x3 moment of inertia tensor
        """
        com = GeometryUtils.center_of_mass(coords, symbols)
        coords_shifted = coords - com

        I = np.zeros((3, 3))
        for i, symbol in enumerate(symbols):
            mass = ELEMENT_MASS.get(symbol, 1.0)
            x, y, z = coords_shifted[i]
            I[0, 0] += mass * (y*y + z*z)
            I[1, 1] += mass * (x*x + z*z)
            I[2, 2] += mass * (x*x + y*y)
            I[0, 1] -= mass * x * y
            I[0, 2] -= mass * x * z
            I[1, 2] -= mass * y * z

        I[1, 0] = I[0, 1]
        I[2, 0] = I[0, 2]
        I[2, 1] = I[1, 2]

        return I

    @staticmethod
    def rmsd(coords1: np.ndarray, coords2: np.ndarray) -> float:
        """
        Calculate RMSD between two coordinate sets.

        Args:
            coords1: First coordinate array (N, 3)
            coords2: Second coordinate array (N, 3)

        Returns:
            RMSD value
        """
        if coords1.shape != coords2.shape:
            raise ValueError(f"Coordinate arrays must have same shape: {coords1.shape} vs {coords2.shape}")
        
        diff = coords1 - coords2
        return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))

    @staticmethod
    def align_structures(coords_ref: np.ndarray, coords_mobile: np.ndarray) -> np.ndarray:
        """
        Align mobile structure to reference using Kabsch algorithm.

        Args:
            coords_ref: Reference coordinates (N, 3)
            coords_mobile: Mobile coordinates to align (N, 3)

        Returns:
            Aligned coordinates
        """
        com_ref = np.mean(coords_ref, axis=0)
        com_mobile = np.mean(coords_mobile, axis=0)

        P = coords_ref - com_ref
        Q = coords_mobile - com_mobile

        H = Q.T @ P
        U, S, Vt = np.linalg.svd(H)
        R = Vt.T @ U.T

        if np.linalg.det(R) < 0:
            Vt[-1, :] *= -1
            R = Vt.T @ U.T

        Q_aligned = Q @ R + com_ref
        return Q_aligned

    @staticmethod
    def rotation_matrix(axis: np.ndarray, angle_deg: float) -> np.ndarray:
        """
        Create rotation matrix around axis.

        Args:
            axis: Rotation axis vector (3,)
            angle_deg: Rotation angle in degrees

        Returns:
            3x3 rotation matrix
        """
        axis = axis / np.linalg.norm(axis)
        angle_rad = np.radians(angle_deg)
        c = np.cos(angle_rad)
        s = np.sin(angle_rad)
        t = 1 - c

        x, y, z = axis
        return np.array([
            [t*x*x + c, t*x*y - s*z, t*x*z + s*y],
            [t*x*y + s*z, t*y*y + c, t*y*z - s*x],
            [t*x*z - s*y, t*y*z + s*x, t*z*z + c]
        ])


class LogParser:
    """
    Log file parser for extracting coordinates from Gaussian/ORCA outputs.

    For ``.log`` files the parser no longer assumes Gaussian by default: ORCA
    parsing is attempted first and falls back to the Gaussian parser only when
    the ORCA parser produces no geometry. Historical Gaussian ``.log`` files
    remain readable via this fallback path (legacy read-only support).
    """

    ELEMENT_MAP = {
        1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O',
        9: 'F', 10: 'Ne', 11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P',
        16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K', 20: 'Ca', 26: 'Fe', 29: 'Cu',
        30: 'Zn', 35: 'Br', 53: 'I'
    }

    @staticmethod
    def extract_last_converged_coords(
        log_file: Path,
        engine_type: str = 'auto'
    ) -> Tuple[Optional[np.ndarray], Optional[List[str]], Optional[str]]:
        """
        Extract last converged geometry from log file.

        Args:
            log_file: Path to log file
            engine_type: 'gaussian', 'orca', 'auto' (default)

        Returns:
            Tuple of (coordinates, symbols, error_message)
            - coordinates: (N, 3) numpy array or None
            - symbols: List of element symbols or None
            - error_message: Error description if failed, else None
        """
        if not log_file.exists():
            return None, None, f"Log file not found: {log_file}"

        if engine_type == 'auto':
            suffix = log_file.suffix.lower()
            if suffix == '.out':
                engine_type = 'orca'
            else:
                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(4096)
                    if 'Gaussian' in content and 'ORCA' not in content:
                        engine_type = 'gaussian'
                    else:
                        engine_type = 'orca'

        try:
            if engine_type == 'gaussian':
                return LogParser._parse_gaussian_log(log_file)
            if engine_type == 'orca':
                coords, symbols, err = LogParser._parse_orca_out(log_file)
                if coords is not None:
                    return coords, symbols, err
                # Fallback to Gaussian parser for legacy .log files that ORCA
                # cannot parse (e.g. historical Gaussian GIAO logs).
                return LogParser._parse_gaussian_log(log_file)
            return None, None, f"Unknown engine type: {engine_type}"
        except Exception as e:
            return None, None, f"Parse error: {str(e)}"

    @staticmethod
    def _parse_gaussian_log(log_file: Path) -> Tuple[Optional[np.ndarray], Optional[List[str]], Optional[str]]:
        """Parse Gaussian log file for last converged geometry."""
        coords_blocks = []
        symbols = None

        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        standard_orient_pattern = r'Standard orientation:.*?Coordinates \(Angstroms\)(.*?)(\n\s+-+\n)(?=\s+Rotational)'
        matches = re.findall(standard_orient_pattern, content, re.DOTALL)

        for match in matches:
            all_lines = match[0].strip().split('\n')
            coords = []
            for line in all_lines:
                parts = line.split()
                if len(parts) >= 6 and parts[0].isdigit():
                    try:
                        atomic_num = int(parts[1])
                        x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
                        symbol = LogParser.ELEMENT_MAP.get(atomic_num, f'X{atomic_num}')
                        coords.append((symbol, x, y, z))
                    except (ValueError, IndexError):
                        continue

            if coords:
                coords_blocks.append(coords)

        if not coords_blocks:
            input_orient_pattern = r'Input orientation:.*?Coordinates \(Angstroms\)(.*?)(\n\s+-+\n)(?=\s+Rotational)'
            matches = re.findall(input_orient_pattern, content, re.DOTALL)
            for match in matches:
                all_lines = match[0].strip().split('\n')
                coords = []
                for line in all_lines:
                    parts = line.split()
                    if len(parts) >= 6 and parts[0].isdigit():
                        try:
                            atomic_num = int(parts[1])
                            x, y, z = float(parts[3]), float(parts[4]), float(parts[5])
                            symbol = LogParser.ELEMENT_MAP.get(atomic_num, f'X{atomic_num}')
                            coords.append((symbol, x, y, z))
                        except (ValueError, IndexError):
                            continue
                if coords:
                    coords_blocks.append(coords)

        if not coords_blocks:
            return None, None, "No coordinates found in Gaussian log"

        last_coords = coords_blocks[-1]
        symbols = [c[0] for c in last_coords]
        coordinates = np.array([[c[1], c[2], c[3]] for c in last_coords])

        return coordinates, symbols, None

    @staticmethod
    def _parse_orca_out(out_file: Path) -> Tuple[Optional[np.ndarray], Optional[List[str]], Optional[str]]:
        """Parse ORCA output file for last geometry."""
        coords_blocks = []
        symbols = None

        with open(out_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        cartesian_pattern = r'CARTESIAN COORDINATES \(ANGSTROEM\)\s+-{3,}(.*?)-{3,}'
        matches = re.findall(cartesian_pattern, content, re.DOTALL)

        for match in matches:
            lines = match.strip().split('\n')
            coords = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 4 and parts[0][0].isalpha():
                    try:
                        symbol = parts[0]
                        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                        coords.append((symbol, x, y, z))
                    except ValueError:
                        continue
            if coords:
                coords_blocks.append(coords)

        if not coords_blocks:
            cartesian_au_pattern = r'CARTESIAN COORDINATES \(A\.U\.\)\s+-{3,}(.*?)-{3,}'
            matches = re.findall(cartesian_au_pattern, content, re.DOTALL)
            for match in matches:
                lines = match.strip().split('\n')
                coords = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 5 and parts[1].isdigit():
                        try:
                            symbol = parts[2]
                            x, y, z = float(parts[3]) * 0.529177, float(parts[4]) * 0.529177, float(parts[5]) * 0.529177
                            coords.append((symbol, x, y, z))
                        except ValueError:
                            continue
                if coords:
                    coords_blocks.append(coords)

        if not coords_blocks:
            xyz_pattern = r'\* xyz (\d+) (\d+)(.*?)\*'
            matches = re.findall(xyz_pattern, content, re.DOTALL)
            for match in matches:
                lines = match[2].strip().split('\n')
                coords = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            symbol = parts[0]
                            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                            coords.append((symbol, x, y, z))
                        except ValueError:
                            continue
                if coords:
                    coords_blocks.append(coords)

        if not coords_blocks:
            return None, None, "No coordinates found in ORCA output"

        last_coords = coords_blocks[-1]
        symbols = [c[0] for c in last_coords]
        coordinates = np.array([[c[1], c[2], c[3]] for c in last_coords])

        return coordinates, symbols, None

    @staticmethod
    def extract_energy(log_file: Path, engine_type: str = 'auto') -> Optional[float]:
        """
        Extract final energy from log file.

        Args:
            log_file: Path to log file
            engine_type: 'gaussian', 'orca', 'auto'

        Returns:
            Energy in Hartree, or None if not found
        """
        if not log_file.exists():
            return None

        if engine_type == 'auto':
            suffix = log_file.suffix.lower()
            engine_type = 'orca' if suffix == '.out' else 'auto_content'

        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        if engine_type == 'gaussian':
            scf_pattern = r'SCF Done:.*E\(.*\)\s*=\s*([-+]?\d+\.\d+)'
            matches = re.findall(scf_pattern, content)
            if matches:
                return float(matches[-1])

        # ORCA first; fall back to Gaussian SCF for legacy .log files.
        final_energy_pattern = r'FINAL SINGLE POINT ENERGY\s+([-+]?\d+\.\d+)'
        matches = re.findall(final_energy_pattern, content)
        if matches:
            return float(matches[-1])
        scf_pattern = r'SCF Done:.*E\(.*\)\s*=\s*([-+]?\d+\.\d+)'
        matches = re.findall(scf_pattern, content)
        if matches:
            return float(matches[-1])

        return None
