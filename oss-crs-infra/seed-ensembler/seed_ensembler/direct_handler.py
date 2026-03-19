"""Direct libfuzzer execution (no Docker).

Runs harness binaries directly via subprocess, for use when the
ensembler sidecar is based on the target image and already has
all necessary runtime dependencies.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from pathlib import Path

from .libfuzzer_result import LibfuzzerMergeResult, LibfuzzerSingleExecResult

log = logging.getLogger(__name__)


class DirectEnvironment:
    """Direct execution environment for libfuzzer harnesses.

    Runs harness binaries as local subprocesses.  The sidecar container
    must be based on the target image (has all shared libs, JVM, etc.).
    """

    def __init__(self, artifact_dir: Path, *, verbose: bool = False):
        self.artifact_dir = artifact_dir
        self.verbose = verbose

    def set_up(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def run_merge(
        self,
        harness_path: Path,
        seed_dirs: list[Path],
        *,
        per_seed_timeout: float | None = None,
        overall_timeout: float | None = None,
    ) -> LibfuzzerMergeResult:
        """Run ``harness -merge=1`` directly and return parsed results."""
        if len(seed_dirs) < 2:
            raise RuntimeError(
                "libfuzzer -merge requires at least two seed directories"
            )

        cmd = [
            str(harness_path),
            "-merge=1",
            f"-artifact_prefix={self.artifact_dir}/",
        ]
        if per_seed_timeout is not None:
            cmd.append(f"-timeout={round(per_seed_timeout)}")
        for d in seed_dirs:
            cmd.append(str(d))

        if self.verbose:
            log.info("cmd: %s", shlex.join(cmd))

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                timeout=overall_timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            elapsed = time.time() - start_time

            if self.verbose:
                log.info("libfuzzer output: %r", proc.stderr[:500])

            result = LibfuzzerMergeResult.from_stderr(
                proc.stderr,
                return_code=proc.returncode,
                execution_time=elapsed,
            )

        except subprocess.TimeoutExpired as e:
            elapsed = time.time() - start_time
            result = LibfuzzerMergeResult.from_stderr(
                e.stderr or b"",
                execution_time=elapsed,
                was_aborted=True,
            )

        result.deduplicate()
        return result

    def run_single_exec(
        self,
        harness_path: Path,
        seed: Path,
        *,
        per_seed_timeout: float | None = None,
        overall_timeout: float | None = None,
    ) -> LibfuzzerSingleExecResult:
        """Run a single seed through the harness directly."""
        cmd = [
            str(harness_path),
            f"-artifact_prefix={self.artifact_dir}/",
        ]
        if per_seed_timeout is not None:
            cmd.append(f"-timeout={round(per_seed_timeout)}")
        cmd.append(str(seed))

        log.info("cmd: %s", shlex.join(cmd))

        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                timeout=overall_timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            elapsed = time.time() - start_time

            result = LibfuzzerSingleExecResult.from_path_and_stderr(
                seed,
                proc.stderr,
                return_code=proc.returncode,
                execution_time=elapsed,
            )

        except subprocess.TimeoutExpired as e:
            elapsed = time.time() - start_time
            result = LibfuzzerSingleExecResult.from_path_and_stderr(
                seed,
                e.stderr or b"",
                execution_time=elapsed,
                was_aborted=True,
            )

        return result
