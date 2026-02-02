from enum import Enum
from abc import ABC, abstractmethod
from pathlib import Path


class DataType(str, Enum):
    POV = "pov"
    SEED = "seed"
    BUG_CANDIDATE = "bug-candidate"
    PATCH = "patch"

    def __str__(self) -> str:
        return self.value


class CRSUtils(ABC):
    @abstractmethod
    def download_build_output(self, src_path: str, dst_path: Path) -> None:
        """Download build output from src_path (in infra) to dst_path (in local)."""
        pass

    @abstractmethod
    def submit_build_output(self, src_path: str, dst_path: Path) -> None:
        """Submit build output from src_path (in local) to dst_path (in infra)."""
        pass

    @abstractmethod
    def skip_build_output(self, dst_path: str) -> None:
        """Skip build output for dst_path (in infra)."""
        pass

    @abstractmethod
    def register_submit_dir(self, type: DataType, path: Path) -> None:
        """Register a directory for automatic submission to oss-crs-infra."""
        pass

    @abstractmethod
    def register_fetch_dir(self, type: DataType, path: Path) -> None:
        """Register a directory for automatic fetching of shared data from oss-crs-infra."""
        pass

    @abstractmethod
    def submit(self, type: DataType, src: Path) -> None:
        """Submit a local file to oss-crs-infra."""
        pass

    @abstractmethod
    def fetch(self, type: DataType, dst: Path) -> list[str]:
        """Download shared data from oss-crs-infra to a local directory.

        Returns:
            List of downloaded file names
        """
        pass

    # TODO: Add more utility methods as needed
