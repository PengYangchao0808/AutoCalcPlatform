"""
Cluster Adapter Base
====================

Abstract base class and data models for cluster adapters.

Author: QCcalc Team
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class JobStatus:
    """Job status information."""
    job_id: str
    state: str  # PENDING, RUNNING, DONE, FAILED, UNKNOWN
    name: Optional[str] = None
    exit_code: Optional[int] = None
    message: Optional[str] = None


class ClusterAdapterBase(ABC):
    """
    Abstract base class for cluster adapters.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize cluster adapter.

        Args:
            config: Configuration dictionary with cluster settings
        """
        self.config = config

    @abstractmethod
    def submit_job(
        self,
        script_content: str,
        job_name: str,
        resources: Dict[str, Any]
    ) -> str:
        """
        Submit a job to the cluster.

        Args:
            script_content: Shell script content to execute
            job_name: Name for the job
            resources: Resource requirements (nproc, mem, walltime, etc.)

        Returns:
            Job ID string
        """
        pass

    @abstractmethod
    def get_status(self, job_id: str) -> JobStatus:
        """
        Get status of a submitted job.

        Args:
            job_id: Job ID

        Returns:
            JobStatus object
        """
        pass

    @abstractmethod
    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Args:
            job_id: Job ID

        Returns:
            True if cancelled successfully
        """
        pass

    @abstractmethod
    def wait_for_completion(
        self,
        job_id: str,
        poll_interval: int = 30,
        max_wait: Optional[int] = None
    ) -> JobStatus:
        """
        Wait for job to complete.

        Args:
            job_id: Job ID
            poll_interval: Seconds between status checks
            max_wait: Maximum seconds to wait (None = indefinite)

        Returns:
            Final JobStatus
        """
        pass
