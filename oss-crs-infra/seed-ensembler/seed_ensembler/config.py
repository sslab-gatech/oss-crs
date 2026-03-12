"""Configuration for the seed ensembler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Configuration:
    """Seed ensembler configuration.

    Attributes:
        temp_dir: Working directory for temporary files (ideally on tmpfs).
        worker_pool_size: Number of parallel libfuzzer worker processes.
        runner_image: Docker image used to execute harness binaries.  When
            ``None``, harnesses are executed directly (requires the sidecar
            container to include all necessary runtime libraries).
        verbose: Enable debug-level logging.
    """

    temp_dir: Path
    worker_pool_size: int = 4
    runner_image: str | None = None
    verbose: bool = False
