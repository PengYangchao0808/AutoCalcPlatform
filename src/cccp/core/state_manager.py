"""
State Manager
=============

Manages conformer search state and checkpointing.

Author: QCcalc Team (adapted from RPH)
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)


class ConformerStateManager:
    """
    Manages conformer search state persistence.
    
    Handles:
    - Run initialization and tracking
    - Checkpoint saving/loading
    - Protocol signature tracking
    - Step completion status
    """

    STATE_FILE = "conformer_state.json"

    def __init__(self, work_dir: Path, molecule_name: str):
        """
        Initialize state manager.

        Args:
            work_dir: Working directory for the molecule
            molecule_name: Name of the molecule
        """
        self.work_dir = Path(work_dir)
        self.molecule_name = molecule_name
        self.state_file = self.work_dir / self.STATE_FILE
        self._state: Dict[str, Any] = {}

    def load_state(self) -> Optional[Dict[str, Any]]:
        """
        Load state from file if it exists.

        Returns:
            State dictionary or None if no state file
        """
        if not self.state_file.exists():
            return None

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                self._state = json.load(f)
            logger.info(f"Loaded state from {self.state_file}")
            return self._state
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            return None

    def save_state(self):
        """Save current state to file."""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def start_run(self, smiles: str, two_stage_enabled: bool):
        """
        Initialize a new run.

        Args:
            smiles: SMILES string
            two_stage_enabled: Whether two-stage search is enabled
        """
        self._state = {
            'version': '1.0',
            'molecule_name': self.molecule_name,
            'smiles': smiles,
            'two_stage_enabled': two_stage_enabled,
            'started_at': datetime.now().isoformat(),
            'stages': {},
            'current_stage': None,
            'completed': False,
        }
        self.save_state()

    def set_stage(self, stage_name: str, status: str = 'running'):
        """
        Set current stage.

        Args:
            stage_name: Name of the stage
            status: Stage status (running, completed, failed)
        """
        if 'stages' not in self._state:
            self._state['stages'] = {}
        
        if stage_name not in self._state['stages']:
            self._state['stages'][stage_name] = {}
        
        self._state['stages'][stage_name]['status'] = status
        self._state['stages'][stage_name]['updated_at'] = datetime.now().isoformat()
        self._state['current_stage'] = stage_name
        
        self.save_state()

    def complete_stage(self, stage_name: str, result: Dict[str, Any]):
        """
        Mark stage as completed with results.

        Args:
            stage_name: Name of the stage
            result: Stage result data
        """
        if 'stages' not in self._state:
            self._state['stages'] = {}
        
        self._state['stages'][stage_name] = {
            'status': 'completed',
            'completed_at': datetime.now().isoformat(),
            'result': result
        }
        
        self.save_state()

    def fail_stage(self, stage_name: str, error: str):
        """
        Mark stage as failed.

        Args:
            stage_name: Name of the stage
            error: Error message
        """
        if 'stages' not in self._state:
            self._state['stages'] = {}
        
        self._state['stages'][stage_name] = {
            'status': 'failed',
            'failed_at': datetime.now().isoformat(),
            'error': error
        }
        
        self.save_state()

    def set_protocol_signature(self, protocol: str, funnel_signature: Dict[str, Any]):
        """
        Set protocol signature for reproducibility.

        Args:
            protocol: Protocol name
            funnel_signature: Funnel configuration signature
        """
        self._state['protocol'] = protocol
        self._state['funnel_signature'] = funnel_signature
        self._state['signature_timestamp'] = datetime.now().isoformat()
        self.save_state()

    def is_stage_completed(self, stage_name: str) -> bool:
        """
        Check if stage is completed.

        Args:
            stage_name: Name of the stage

        Returns:
            True if stage is completed
        """
        if 'stages' not in self._state:
            return False
        
        stage = self._state['stages'].get(stage_name, {})
        return stage.get('status') == 'completed'

    def get_stage_result(self, stage_name: str) -> Optional[Dict[str, Any]]:
        """
        Get result from completed stage.

        Args:
            stage_name: Name of the stage

        Returns:
            Stage result or None
        """
        if not self.is_stage_completed(stage_name):
            return None
        
        return self._state['stages'].get(stage_name, {}).get('result')

    def mark_completed(self):
        """Mark entire run as completed."""
        self._state['completed'] = True
        self._state['completed_at'] = datetime.now().isoformat()
        self.save_state()

    def get_conformer_count(self) -> int:
        """
        Get number of conformers found so far.

        Returns:
            Number of conformers
        """
        crest_result = self.get_stage_result('crest_search')
        if crest_result:
            return crest_result.get('n_conformers', 0)
        return 0

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary of current state.

        Returns:
            Summary dictionary
        """
        return {
            'molecule_name': self.molecule_name,
            'smiles': self._state.get('smiles'),
            'protocol': self._state.get('protocol'),
            'two_stage_enabled': self._state.get('two_stage_enabled'),
            'current_stage': self._state.get('current_stage'),
            'completed': self._state.get('completed', False),
            'stages': list(self._state.get('stages', {}).keys()),
            'n_conformers': self.get_conformer_count(),
            'started_at': self._state.get('started_at'),
            'completed_at': self._state.get('completed_at'),
        }

    def clear(self):
        """Clear all state."""
        self._state = {}
        if self.state_file.exists():
            self.state_file.unlink()

    # -------------------------------------------------------------------------
    # Intermediate / prescreen / screening stage helpers
    # -------------------------------------------------------------------------

    def mark_intermediate_clustering(self, status: str, output_file: str):
        """
        Mark crest_intermediate_clustering stage as completed.

        Args:
            status: Clustering status or summary
            output_file: Path to clustered ensemble file
        """
        self.set_stage('crest_intermediate_clustering')
        self.complete_stage('crest_intermediate_clustering', {
            'status': status,
            'output_file': str(output_file)
        })

    def mark_fastsp_prescreen(self, status: str, output_file: str):
        """
        Mark fastsp_prescreen stage as completed.

        Args:
            status: Prescreen status or summary
            output_file: Path to prescreen results
        """
        self.set_stage('fastsp_prescreen')
        self.complete_stage('fastsp_prescreen', {
            'status': status,
            'output_file': str(output_file)
        })

    def mark_fastsp_screening(self, status: str, output_file: str):
        """
        Mark fastsp_screening stage as completed.

        Args:
            status: Screening status or summary
            output_file: Path to screening results
        """
        self.set_stage('fastsp_screening')
        self.complete_stage('fastsp_screening', {
            'status': status,
            'output_file': str(output_file)
        })
