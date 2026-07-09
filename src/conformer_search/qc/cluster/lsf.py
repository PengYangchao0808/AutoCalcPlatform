"""
LSF Cluster Adapter
===================

LSF cluster adapter for job submission and management.

Author: QCcalc Team
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional

from conformer_search.qc.cluster.base import ClusterAdapterBase, JobStatus

logger = logging.getLogger(__name__)


class LSFClusterAdapter(ClusterAdapterBase):
    """
    LSF cluster adapter.
    This is a placeholder implementation - actual LSF support requires
    site-specific configuration.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize LSF cluster adapter.

        Args:
            config: Configuration dictionary with 'cluster' section
        """
        super().__init__(config)
        cluster_config = config.get('cluster', {})
        self.queue = cluster_config.get('queue', 'normal')
        self.walltime = cluster_config.get('walltime', '24:00')
        self.extra_flags = cluster_config.get('extra_flags', '')

    def submit_job(
        self,
        script_content: str,
        job_name: str,
        resources: Dict[str, Any]
    ) -> str:
        """
        Submit job to LSF cluster.

        Args:
            script_content: Shell script content
            job_name: Job name
            resources: Resource requirements (ncores, mem_per_core, etc.)

        Returns:
            LSF job ID
        """
        lsf_script = self._generate_lsf_script(script_content, job_name, resources)
        
        script_path = Path(f"/tmp/{job_name}.lsf")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(lsf_script)
        
        try:
            result = subprocess.run(
                ['bsub', '<', str(script_path)],
                shell=True,
                capture_output=True,
                text=True,
                check=True
            )
            
            import re
            match = re.search(r'Job <(\d+)>', result.stdout)
            if match:
                job_id = match.group(1)
                logger.info(f"Submitted LSF job {job_name} with ID {job_id}")
                return job_id
            else:
                raise RuntimeError(f"Could not parse job ID from bsub output: {result.stdout}")
                
        except subprocess.CalledProcessError as e:
            logger.error(f"LSF submission failed: {e.stderr}")
            raise

    def _generate_lsf_script(
        self,
        script_content: str,
        job_name: str,
        resources: Dict[str, Any]
    ) -> str:
        """Generate LSF submission script."""
        ncores = resources.get('ncores', self.config.get('resources', {}).get('nproc', 16))
        mem_per_core = resources.get('mem_per_core', '2000')
        
        lines = [
            '#!/bin/bash',
            f'#BSUB -J {job_name}',
            f'#BSUB -q {self.queue}',
            f'#BSUB -n {ncores}',
            f'#BSUB -R "rusage[mem={mem_per_core}]"',
            f'#BSUB -W {self.walltime}',
            '#BSUB -o %J.out',
            '#BSUB -e %J.err',
        ]
        
        if self.extra_flags:
            lines.append(f'#BSUB {self.extra_flags}')
        
        lines.append('')
        lines.append('cd $LS_SUBCWD')
        lines.append(script_content)
        
        return '\n'.join(lines)

    def get_status(self, job_id: str) -> JobStatus:
        """
        Get LSF job status.

        Args:
            job_id: LSF job ID

        Returns:
            JobStatus object
        """
        try:
            result = subprocess.run(
                ['bjobs', '-noheader', '-o', 'stat', str(job_id)],
                capture_output=True,
                text=True,
                check=True
            )
            
            state_map = {
                'PEND': 'PENDING',
                'RUN': 'RUNNING',
                'DONE': 'DONE',
                'EXIT': 'FAILED',
                'UNKWN': 'UNKNOWN',
            }
            
            state = result.stdout.strip()
            mapped_state = state_map.get(state, 'UNKNOWN')
            
            return JobStatus(
                job_id=job_id,
                state=mapped_state,
                message=f"LSF state: {state}"
            )
            
        except subprocess.CalledProcessError:
            return JobStatus(
                job_id=job_id,
                state="UNKNOWN",
                message="Could not query job status"
            )

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel LSF job.

        Args:
            job_id: LSF job ID

        Returns:
            True if cancelled successfully
        """
        try:
            subprocess.run(['bkill', str(job_id)], check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def wait_for_completion(
        self,
        job_id: str,
        poll_interval: int = 30,
        max_wait: Optional[int] = None
    ) -> JobStatus:
        """
        Wait for LSF job to complete.

        Args:
            job_id: LSF job ID
            poll_interval: Seconds between checks
            max_wait: Maximum seconds to wait

        Returns:
            Final JobStatus
        """
        start_time = time.time()
        
        while True:
            status = self.get_status(job_id)
            
            if status.state in ('DONE', 'FAILED', 'UNKNOWN'):
                return status
            
            if max_wait and (time.time() - start_time) > max_wait:
                self.cancel_job(job_id)
                return JobStatus(
                    job_id=job_id,
                    state="FAILED",
                    message="Timeout"
                )
            
            time.sleep(poll_interval)
