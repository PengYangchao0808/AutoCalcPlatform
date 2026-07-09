"""
Local Cluster Adapter
=====================

Local execution adapter (no cluster, runs directly on current machine).

Author: QCcalc Team
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional

from conformer_search.qc.cluster.base import ClusterAdapterBase, JobStatus

logger = logging.getLogger(__name__)


class LocalClusterAdapter(ClusterAdapterBase):
    """
    Local execution adapter (no cluster, runs directly on current machine).
    This is the default adapter when no cluster is configured.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize local cluster adapter.

        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self.running_processes: Dict[str, subprocess.Popen] = {}

    def submit_job(
        self,
        script_content: str,
        job_name: str,
        resources: Dict[str, Any]
    ) -> str:
        """
        Execute script locally in a subprocess.

        Args:
            script_content: Shell script content
            job_name: Job name (used for logging)
            resources: Resource requirements (ignored for local)

        Returns:
            Process ID as job_id
        """
        script_path = Path(f"/tmp/{job_name}.sh")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write("#!/bin/bash\n")
            f.write(script_content)
        script_path.chmod(0o755)

        log_file = Path(f"/tmp/{job_name}.log")
        
        try:
            process = subprocess.Popen(
                [str(script_path)],
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT,
                shell=True
            )
            
            self.running_processes[str(process.pid)] = process
            
            logger.info(f"Started local job {job_name} with PID {process.pid}")
            
            return str(process.pid)
            
        except Exception as e:
            logger.error(f"Failed to start local job: {e}")
            raise

    def get_status(self, job_id: str) -> JobStatus:
        """
        Get status of local process.

        Args:
            job_id: Process ID

        Returns:
            JobStatus object
        """
        if job_id not in self.running_processes:
            return JobStatus(
                job_id=job_id,
                state="UNKNOWN",
                message="Process not found"
            )

        process = self.running_processes[job_id]
        
        if process.poll() is None:
            return JobStatus(
                job_id=job_id,
                state="RUNNING",
                name=f"PID_{job_id}"
            )
        else:
            exit_code = process.returncode
            return JobStatus(
                job_id=job_id,
                state="DONE" if exit_code == 0 else "FAILED",
                exit_code=exit_code
            )

    def cancel_job(self, job_id: str) -> bool:
        """
        Kill a local process.

        Args:
            job_id: Process ID

        Returns:
            True if killed successfully
        """
        if job_id not in self.running_processes:
            return False

        try:
            process = self.running_processes[job_id]
            process.terminate()
            process.wait(timeout=10)
            del self.running_processes[job_id]
            return True
        except Exception as e:
            logger.error(f"Failed to kill process {job_id}: {e}")
            return False

    def wait_for_completion(
        self,
        job_id: str,
        poll_interval: int = 1,
        max_wait: Optional[int] = None
    ) -> JobStatus:
        """
        Wait for local process to complete.

        Args:
            job_id: Process ID
            poll_interval: Seconds between checks
            max_wait: Maximum seconds to wait

        Returns:
            Final JobStatus
        """
        start_time = time.time()
        
        while True:
            status = self.get_status(job_id)
            
            if status.state in ("DONE", "FAILED", "UNKNOWN"):
                return status
            
            if max_wait and (time.time() - start_time) > max_wait:
                self.cancel_job(job_id)
                return JobStatus(
                    job_id=job_id,
                    state="FAILED",
                    message="Timeout"
                )
            
            time.sleep(poll_interval)
