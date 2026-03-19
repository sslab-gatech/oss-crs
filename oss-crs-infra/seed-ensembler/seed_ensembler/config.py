"""Configuration for the seed ensembler."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
        submit_root: Root directory containing per-CRS SUBMIT_DIR trees.
        exchange_root: Shared EXCHANGE_DIR for distributing filtered seeds.
        build_out_dir: BUILD_OUT_DIR containing harness binaries.
        mode: ``"coverage"`` for coverage-aware filtering,
            ``"passthrough"`` for MD5-dedup-only (same as old exchange sidecar).
        poll_interval: Seconds between scan cycles.
    """

    temp_dir: Path
    worker_pool_size: int = 4
    runner_image: str | None = None
    verbose: bool = False
    submit_root: Path = field(default_factory=lambda: Path("/submit"))
    exchange_root: Path = field(default_factory=lambda: Path("/OSS_CRS_EXCHANGE_DIR"))
    build_out_dir: Path = field(default_factory=lambda: Path("/OSS_CRS_BUILD_OUT_DIR"))
    mode: str = "coverage"
    poll_interval: float = 2.0

    @classmethod
    def from_env(cls) -> Configuration:
        """Build configuration from environment variables."""
        return cls(
            temp_dir=Path(os.environ.get("ENSEMBLER_TEMP_DIR", "/tmp/ensembler")),
            worker_pool_size=int(os.environ.get("ENSEMBLER_WORKERS", "2")),
            runner_image=os.environ.get("RUNNER_IMAGE"),
            verbose=os.environ.get("ENSEMBLER_VERBOSE", "").lower() in ("1", "true"),
            submit_root=Path(os.environ.get("SUBMIT_ROOT", "/submit")),
            exchange_root=Path(os.environ.get("EXCHANGE_ROOT", "/OSS_CRS_EXCHANGE_DIR")),
            build_out_dir=Path(os.environ.get("BUILD_OUT_DIR", "/OSS_CRS_BUILD_OUT_DIR")),
            mode=os.environ.get("ENSEMBLER_MODE", "coverage"),
            poll_interval=float(os.environ.get("ENSEMBLER_POLL_INTERVAL", "2.0")),
        )
