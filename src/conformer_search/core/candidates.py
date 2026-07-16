"""
Candidates
=========

Conformer candidate representation and operations.

Author: QCcalc Team (adapted from RPH)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import numpy as np


@dataclass
class ConformerCandidate:
    """
    Single conformer candidate.
    
    Attributes:
        index: Candidate index
        coordinates: Atomic coordinates (N, 3)
        symbols: Element symbols
        energy: ORCA single-point energy (Hartree)
        weight: Boltzmann weight at specified temperature
        source_file: Source XYZ file path
        rank: Current ranking
        metadata: Additional metadata
        gibbs_energy: Gibbs free energy = g_conc or g_sum (Hartree)
        gibbs_correction: Shermo thermal correction to G (g_sum - sp_energy) (Hartree)
        h_correction: Enthalpy correction from Shermo (Hartree)
        u_correction: Internal energy correction from Shermo (Hartree)
        s_total: Total entropy from Shermo (a.u.)
        g_conc: Gibbs free energy at specified concentration (Hartree)
    """
    index: int
    coordinates: np.ndarray
    symbols: List[str]
    energy: float = 0.0
    weight: float = 0.0
    source_file: Optional[Path] = None
    rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    gibbs_energy: Optional[float] = None
    gibbs_correction: Optional[float] = None
    h_correction: Optional[float] = None
    u_correction: Optional[float] = None
    s_total: Optional[float] = None
    g_conc: Optional[float] = None
    screening_energy: Optional[float] = None
    xtb_energy: Optional[float] = None
    xtb_free_energy: Optional[float] = None

    @property
    def g_used(self) -> Optional[float]:
        """Return Gibbs energy for Boltzmann weighting (g_conc if available, else gibbs_energy)."""
        return self.g_conc if self.g_conc is not None else self.gibbs_energy

    @property
    def n_atoms(self) -> int:
        """Number of atoms."""
        return len(self.symbols)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'index': self.index,
            'energy': self.energy,
            'weight': self.weight,
            'source_file': str(self.source_file) if self.source_file else None,
            'rank': self.rank,
            'metadata': self.metadata,
            'gibbs_energy': self.gibbs_energy,
            'gibbs_correction': self.gibbs_correction,
            'h_correction': self.h_correction,
            'u_correction': self.u_correction,
            's_total': self.s_total,
            'g_conc': self.g_conc,
            'screening_energy': self.screening_energy,
            'xtb_energy': self.xtb_energy,
            'xtb_free_energy': self.xtb_free_energy,
        }


@dataclass
class CandidateSet:
    """
    Collection of conformer candidates.
    """
    candidates: List[ConformerCandidate] = field(default_factory=list)
    reference_energy: Optional[float] = None
    temperature: float = 298.15
    ranking_basis: str = "xtb_energy"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self):
        return iter(self.candidates)

    def __getitem__(self, index):
        return self.candidates[index]

    def add(self, candidate: ConformerCandidate):
        """Add a candidate to the set."""
        self.candidates.append(candidate)

    def sort_by_energy(self):
        """Sort candidates by energy (lowest first)."""
        self.candidates.sort(key=lambda c: c.energy if c.energy is not None else float('inf'))

    def calculate_boltzmann_weights(self, temperature: float = 298.15):
        """
        Calculate Boltzmann weights using ORCA single-point energy.
        
        Args:
            temperature: Temperature in Kelvin
        """
        if not self.candidates:
            return
        
        self.temperature = temperature
        
        energies = np.array([c.energy for c in self.candidates])
        min_energy = energies.min()
        relative_energies = energies - min_energy
        
        kB = 0.001987204  # kcal/(mol·K)
        boltz_factors = np.exp(-relative_energies / (kB * temperature))
        total = boltz_factors.sum()
        
        for i, candidate in enumerate(self.candidates):
            candidate.weight = boltz_factors[i] / total if total > 0 else 0.0

    def calculate_boltzmann_weights_gibbs(self, temperature_k: float = 298.15):
        """
        Calculate Boltzmann weights using Gibbs free energy from Shermo.
        
        Args:
            temperature_k: Temperature in Kelvin
            
        Note:
            Uses Hartree unit consistently:
            - Gibbs energy in Hartree
            - R = 8.314462618 / 2625500 Hartree/(mol·K)
        """
        if not self.candidates:
            return
        
        self.temperature = temperature_k
        
        valid_candidates = [c for c in self.candidates if c.gibbs_energy is not None]
        if not valid_candidates:
            return
        
        energies = np.array([c.g_used for c in valid_candidates])
        min_energy = energies.min()
        relative_energies = energies - min_energy
        
        R_HARTREE = 8.314462618 / 2625500  # Hartree/(mol·K)
        boltz_factors = np.exp(-relative_energies / (R_HARTREE * temperature_k))
        total = boltz_factors.sum()
        
        for c, bf in zip(valid_candidates, boltz_factors):
            c.weight = bf / total if total > 0 else 0.0
        
        for c in self.candidates:
            if c.gibbs_energy is None:
                c.weight = 0.0

    def select_by_window(self, window_kcal: float = 3.0) -> 'CandidateSet':
        """
        Select candidates within energy window.
        
        Args:
            window_kcal: Energy window in kcal/mol
            
        Returns:
            New CandidateSet with selected candidates
        """
        if not self.candidates:
            return CandidateSet()
        
        min_energy = min(c.energy for c in self.candidates)
        selected = [
            c for c in self.candidates
            if c.energy - min_energy <= window_kcal
        ]
        
        result = CandidateSet(candidates=selected, temperature=self.temperature)
        result.reference_energy = min_energy
        return result

    def select_top_n(self, n: int) -> 'CandidateSet':
        """
        Select top N lowest energy candidates.
        
        Args:
            n: Number of candidates to select
            
        Returns:
            New CandidateSet with top N candidates
        """
        self.sort_by_energy()
        selected = self.candidates[:min(n, len(self.candidates))]
        return CandidateSet(candidates=selected, temperature=self.temperature)

    def select_by_boltzmann_cutoff(self, cutoff: float = 0.90) -> 'CandidateSet':
        """
        Select candidates whose cumulative weight exceeds cutoff.
        
        Args:
            cutoff: Cumulative weight threshold
            
        Returns:
            New CandidateSet with selected candidates
        """
        if not self.candidates:
            return CandidateSet()
        
        self.sort_by_energy()
        self.calculate_boltzmann_weights(self.temperature)
        
        cumulative = 0.0
        selected = []
        for candidate in self.candidates:
            cumulative += candidate.weight
            selected.append(candidate)
            if cumulative >= cutoff:
                break
        
        result = CandidateSet(candidates=selected, temperature=self.temperature)
        return result

    def update_ranks(self):
        """Update ranking based on current energy order."""
        self.sort_by_energy()
        for i, candidate in enumerate(self.candidates):
            candidate.rank = i + 1

    def get_lowest_energy(self) -> Optional[ConformerCandidate]:
        """Get lowest energy candidate based on SP energy."""
        if not self.candidates:
            return None
        self.sort_by_energy()
        return self.candidates[0]

    def get_lowest_gibbs(self) -> Optional[ConformerCandidate]:
        """Get lowest Gibbs free energy candidate."""
        if not self.candidates:
            return None
        valid = [c for c in self.candidates if c.gibbs_energy is not None]
        if not valid:
            return self.get_lowest_energy()
        return min(valid, key=lambda c: c.g_used)


def candidate_set_from_paths(
    xyz_paths: List[Path],
    energies: Optional[List[float]] = None,
    reference_energy: Optional[float] = None
) -> CandidateSet:
    """
    Create CandidateSet from list of XYZ files.
    
    Args:
        xyz_paths: List of XYZ file paths
        energies: List of energies (uses file comments if None)
        reference_energy: Reference energy for relative calculations
        
    Returns:
        CandidateSet instance
    """
    from conformer_search.utils.file_io import read_xyz, read_xyz_with_energy
    
    candidates = []
    
    for i, path in enumerate(xyz_paths):
        try:
            coords, symbols = read_xyz(path)
            _, _, file_energy = read_xyz_with_energy(path)
            
            energy = energies[i] if energies and i < len(energies) else (file_energy or 0.0)
            
            candidate = ConformerCandidate(
                index=i,
                coordinates=coords,
                symbols=symbols,
                energy=energy,
                source_file=path,
                xtb_energy=energies[i] if energies and i < len(energies) else None,
            )
            candidates.append(candidate)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to read {path}: {e}")
            continue
    
    result = CandidateSet(candidates=candidates)
    result.reference_energy = reference_energy
    
    if candidates and reference_energy is not None:
        for c in result.candidates:
            c.energy = c.energy - reference_energy
    
    return result


def clone_candidate_set(candidate_set: CandidateSet) -> CandidateSet:
    """
    Create a deep copy of CandidateSet.
    
    Args:
        candidate_set: Source candidate set
        
    Returns:
        Cloned CandidateSet
    """
    new_candidates = []
    for c in candidate_set.candidates:
        new_candidates.append(ConformerCandidate(
            index=c.index,
            coordinates=c.coordinates.copy() if c.coordinates is not None else None,
            symbols=c.symbols.copy() if c.symbols is not None else [],
            energy=c.energy,
            weight=c.weight,
            source_file=c.source_file,
            rank=c.rank,
            metadata=c.metadata.copy() if c.metadata else {},
            screening_energy=c.screening_energy,
            xtb_energy=c.xtb_energy,
            xtb_free_energy=c.xtb_free_energy,
            gibbs_energy=c.gibbs_energy,
            gibbs_correction=c.gibbs_correction,
            h_correction=c.h_correction,
            u_correction=c.u_correction,
            s_total=c.s_total,
            g_conc=c.g_conc,
        ))
    
    return CandidateSet(
        candidates=new_candidates,
        reference_energy=candidate_set.reference_energy,
        temperature=candidate_set.temperature
    )
