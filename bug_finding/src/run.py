#!/usr/bin/env python3
"""CRS run operations."""

import atexit
import logging
import signal
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Optional

from bug_finding.src.render import compose as render_compose
from bug_finding.src.crs_utils import (
    clone_oss_fuzz_if_needed,
    fix_build_dir_permissions,
    get_worker_crs_count,
    validate_crs_modes,
    verify_external_litellm,
)

logger = logging.getLogger(__name__)

# Default registry path
DEFAULT_REGISTRY_DIR = files(__package__).parent.parent / "crs_registry"


def run_crs(
    config_dir: Path,
    project_name: str,
    fuzzer_name: str,
    fuzzer_args: list,
    oss_fuzz_dir: Path,
    build_dir: Path,
    worker: str = "local",
    engine: str = "libfuzzer",
    sanitizer: str = "address",
    architecture: str = "x86_64",
    check_project_fn=None,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
    hints_dir: Optional[Path] = None,
    harness_source: Optional[Path] = None,
    diff_path: Optional[Path] = None,
    external_litellm: bool = False,
    source_oss_fuzz_dir: Optional[Path] = None,
    ensemble_dir: Optional[Path] = None,
    disable_ensemble: bool = False,
    corpus_dir: Optional[Path] = None,
):
    """
    Run CRS using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files (Path, already resolved)
        project_name: Name of the OSS-Fuzz project
        fuzzer_name: Name of the fuzzer to run
        fuzzer_args: Arguments to pass to the fuzzer
        oss_fuzz_dir: Path to OSS-Fuzz root directory (Path, already resolved)
        build_dir: Path to build directory (Path, already resolved)
        worker: Worker name to run CRS on (default: local)
        engine: Fuzzing engine (default: libfuzzer)
        sanitizer: Sanitizer to use (default: address)
        architecture: Architecture (default: x86_64)
        check_project_fn: Optional function to check if project exists
        registry_dir: Optional path to local oss-crs-registry directory (Path, already resolved)
        hints_dir: Optional directory containing hints (SARIF and corpus) (Path, already resolved)
        harness_source: Optional path to harness source file (Path, already resolved)
        diff_path: Optional path to diff file (Path, already resolved)
        external_litellm: Use external LiteLLM instance (default: False)
        source_oss_fuzz_dir: Optional source OSS-Fuzz directory to copy from (Path, already resolved)
        ensemble_dir: Optional base directory for ensemble sharing (Path, already resolved)
        disable_ensemble: Disable automatic ensemble directory for multi-CRS mode
        corpus_dir: Optional directory containing initial corpus files to copy to ensemble corpus

    Returns:
        bool: True if successful, False otherwise
    """
    # Validate project exists if checker provided TODO (don't remove todo)
    if check_project_fn and not check_project_fn():
        return False

    # Check if litellm keys are provided
    if external_litellm and not verify_external_litellm(config_dir):
        logger.error("LITELLM_URL or LITELLM_KEY is not provided in the environment")
        return False

    if not clone_oss_fuzz_if_needed(oss_fuzz_dir, source_oss_fuzz_dir, project_name):
        return False

    # Validate CRS modes against diff_path
    if not validate_crs_modes(config_dir, worker, registry_dir, diff_path):
        return False

    # Determine ensemble_dir for multi-CRS mode
    # Structure: build/ensemble/<config>/<project>/<harness>/{corpus,povs,crs-data}
    final_ensemble_dir = None
    config_name = config_dir.name
    if not disable_ensemble:
        if ensemble_dir:
            # User-provided path - append harness name
            final_ensemble_dir = ensemble_dir / fuzzer_name
        else:
            worker_crs_count = get_worker_crs_count(config_dir, worker)
            if worker_crs_count > 1:
                final_ensemble_dir = (
                    build_dir / "ensemble" / config_name / project_name / fuzzer_name
                )
                logger.info(
                    f"Ensemble mode detected ({worker_crs_count} CRS on worker {worker}). "
                    f"Ensemble directory: {final_ensemble_dir}"
                )

    # Create ensemble subdirectories (seed_watcher service will populate corpus and povs)
    if final_ensemble_dir:
        for subdir in ["corpus", "povs", "crs-data"]:
            (final_ensemble_dir / subdir).mkdir(parents=True, exist_ok=True)
        logger.info(f"Created ensemble directory: {final_ensemble_dir}")

    # Copy corpus files to ensemble corpus directory if provided
    if corpus_dir and final_ensemble_dir:
        if not corpus_dir.is_dir():
            logger.error(f"Corpus directory does not exist: {corpus_dir}")
            return False
        # Import seed_utils for hash-based deduplication
        from bug_finding.seed_watcher.seed_utils import copy_corpus_to_shared
        ensemble_corpus_dir = final_ensemble_dir / "corpus"
        copied_count = copy_corpus_to_shared(corpus_dir, ensemble_corpus_dir)
        if copied_count > 0:
            logger.info(
                f"Copied {copied_count} corpus files from {corpus_dir} "
                f"to {ensemble_corpus_dir} (hash-based naming)"
            )
        else:
            logger.warning(f"No new corpus files copied from {corpus_dir}")

    # Generate compose files using render_compose module
    logger.info("Generating compose-%s.yaml", worker)
    fuzzer_command = [fuzzer_name] + fuzzer_args
    try:
        config_hash, crs_build_dir = render_compose.render_run_compose(
            config_dir=str(config_dir),
            build_dir=str(build_dir),
            oss_fuzz_dir=str(oss_fuzz_dir),
            project=project_name,
            engine=engine,
            sanitizer=sanitizer,
            architecture=architecture,
            registry_dir=str(registry_dir),
            worker=worker,
            fuzzer_command=fuzzer_command,
            harness_source=str(harness_source) if harness_source else None,
            diff_path=str(diff_path) if diff_path else None,
            external_litellm=external_litellm,
            ensemble_dir=str(final_ensemble_dir)
            if final_ensemble_dir
            else None,
        )
        crs_build_dir = Path(crs_build_dir)
    except Exception as e:
        logger.error("Failed to generate compose file: %s", e)
        return False

    # Look for compose files
    compose_file = crs_build_dir / f"compose-{worker}.yaml"

    if not compose_file.exists():
        logger.error("compose-%s.yaml was not generated", worker)
        return False

    # Project names for separate compose projects
    litellm_project = f"crs-litellm-{config_hash}"
    run_project = f"crs-run-{config_hash}-{worker}"

    # Start LiteLLM services in detached mode as separate project (unless using external)
    if not external_litellm:
        litellm_compose_file = crs_build_dir / "compose-litellm.yaml"
        if not litellm_compose_file.exists():
            logger.error("compose-litellm.yaml was not generated")
            return False

        logger.info("Starting LiteLLM services (project: %s)", litellm_project)
        litellm_up_cmd = [
            "docker",
            "compose",
            "-p",
            litellm_project,
            "-f",
            str(litellm_compose_file),
            "up",
            "-d",
        ]
        try:
            subprocess.check_call(litellm_up_cmd)
        except subprocess.CalledProcessError:
            logger.error("Failed to start LiteLLM services")
            return False
    else:
        logger.info("Using external LiteLLM instance")

    logger.info("Starting runner services from: %s", compose_file)
    # Commands for cleanup - only affect run project
    compose_down_cmd = [
        "docker",
        "compose",
        "-p",
        run_project,
        "-f",
        str(compose_file),
        "down",
        "--remove-orphans",
    ]

    def cleanup():
        """Cleanup function for compose files"""
        logger.info("cleanup")
        subprocess.run(compose_down_cmd)
        if not external_litellm:
            litellm_compose_file = crs_build_dir / "compose-litellm.yaml"
            litellm_stop_cmd = [
                "docker",
                "compose",
                "-p",
                litellm_project,
                "-f",
                str(litellm_compose_file),
                "stop",
            ]
            subprocess.run(litellm_stop_cmd)
        fix_build_dir_permissions(build_dir)

    def signal_handler(signum, frame):
        """Handle termination signals"""
        logging.warning(f"\nReceived signal {signum}")
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl-C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination

    # Register cleanup on normal exit
    atexit.register(cleanup)

    # Only pass the run compose file (litellm is in separate project)
    # --build ensures image is rebuilt if source files (run.sh, docker-compose.yml) changed
    compose_cmd = [
        "docker",
        "compose",
        "-p",
        run_project,
        "-f",
        str(compose_file),
        "up",
        "--build",
        "--abort-on-container-exit",
    ]
    try:
        subprocess.check_call(compose_cmd)
    except subprocess.CalledProcessError:
        logger.error("Docker compose failed for: %s", compose_file)
        return False
    finally:
        cleanup()

    return True
