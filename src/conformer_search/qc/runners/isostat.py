"""
ISOSTAT Clustering Runner
=========================

Run ISOSTAT clustering on conformer ensembles.

Author: QCcalc Team (adapted from RPH)
"""

import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from conformer_search.utils.file_io import read_xyz, write_xyz
from conformer_search.utils import ensure_dir

logger = logging.getLogger(__name__)


def run_isostat(
    ensemble_xyz: Path,
    output_dir: Path,
    config: Dict[str, Any],
    gdis: float = 0.125,
    edis: float = 1.0,
    temperature: float = 298.15,
    threads: int = 8
) -> Tuple[Optional[Path], List[Tuple[np.ndarray, List[str], float]]]:
    """
    Run ISOSTAT clustering on conformer ensemble.

    Args:
        ensemble_xyz: Path to ensemble XYZ file
        output_dir: Output directory
        config: Configuration dictionary
        gdis: Geometry distance cutoff
        edis: Energy distance cutoff
        temperature: Temperature for Boltzmann weighting
        threads: Number of threads

    Returns:
        Tuple of (output_xyz_path, list of (coords, symbols, energy))
    """
    executables = config.get('executables', {})
    isostat_bin = executables.get('isostat', {}).get('path', 'isostat')
    
    output_dir = Path(output_dir)
    ensure_dir(output_dir)
    
    cluster_xyz = output_dir / "cluster.xyz"
    isostat_log = output_dir / "isostat.log"
    
    cmd = [
        str(isostat_bin),
        str(ensemble_xyz),
        "-Gdis", str(gdis),
        "-Edis", str(edis),
        "-T", str(temperature),
        "-nt", str(threads)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        with open(isostat_log, 'w', encoding='utf-8') as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\nSTDERR:\n")
                f.write(result.stderr)
        
        if not cluster_xyz.exists():
            logger.warning(f"ISOSTAT clustering failed, copying ensemble directly")
            import shutil
            shutil.copy(ensemble_xyz, cluster_xyz)
            return cluster_xyz, []
        
        coords_list = []
        with open(cluster_xyz, 'r', encoding='utf-8') as f:
            content = f.read()
        
        atom_count_match = re.match(r'(\d+)', content.strip())
        if atom_count_match:
            n_atoms = int(atom_count_match.group(1))
            
            molecule_blocks = content.strip().split(str(n_atoms))
            for i, block in enumerate(molecule_blocks[1:], 1):
                block = block.strip()
                if not block:
                    continue
                    
                lines = block.split('\n')
                if len(lines) < 2:
                    continue
                    
                coords = []
                symbols = []
                energy = None
                
                title_parts = lines[0].strip().split('|')
                if len(title_parts) >= 2:
                    try:
                        energy = float(title_parts[1].split(':')[1].strip())
                    except (ValueError, IndexError):
                        pass
                
                for line in lines[1:n_atoms+1]:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        symbols.append(parts[0])
                        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
                
                if coords:
                    coords_list.append((np.array(coords), symbols, energy))
        
        return cluster_xyz, coords_list
        
    except Exception as e:
        logger.error(f"ISOSTAT clustering failed: {e}")
        return ensemble_xyz, []
