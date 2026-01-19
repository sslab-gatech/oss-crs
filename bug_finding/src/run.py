#!/usr/bin/env python3
"""CRS run operations."""

import atexit
import logging
import signal
import subprocess
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from typing import Any

from bug_finding.src.render_compose import render as render_compose
from bug_finding.src.crs_utils import (
    clone_oss_fuzz_if_needed,
    fix_build_dir_permissions,
    get_worker_crs_count,
    validate_crs_modes,
    verify_external_litellm,
)
from bug_finding.src.utils import run_rsync
from bug_fixing.src.oss_patch.functions import remove_directory_with_docker

logger = logging.getLogger(__name__)


def _setup_run_docker_data(
    crs_list: list[dict[str, Any]],
    project_name: str,
    build_dir: Path,
) -> bool:
    """
    Set up docker-data for run phase by copying from build phase.

    For each dind CRS:
    1. Copies build/<project>/ -> run/<project>/ using rsync with hardlinks

    This creates a fresh copy of the build-phase docker-data for each run,
    allowing the run to be repeated without contaminating the build state.

    Directory structure:
        build/docker-data/<crs-name>/
        ├── prepared/           # From prepare phase (dind-images only)
        ├── build/<project>/    # From build phase (dind + project image)
        └── run/<project>/      # For run phase (fresh copy from build)

    Args:
        crs_list: List of CRS configurations with 'name' and 'dind' keys
        project_name: Name of the project
        build_dir: Path to build directory (already resolved)

    Returns:
        bool: True if successful, False otherwise
    """
    dind_crs = [crs for crs in crs_list if crs.get("dind", False)]
    if not dind_crs:
        return True

    for crs in dind_crs:
        crs_name = crs["name"]
        build_docker_data = build_dir / "docker-data" / crs_name / "build" / project_name
        run_docker_data = build_dir / "docker-data" / crs_name / "run" / project_name

        # Check build docker-data exists
        if not build_docker_data.exists():
            logger.error(
                f"Build docker-data not found for CRS '{crs_name}': {build_docker_data}. "
                f"Run 'oss-bugfind-crs build' first."
            )
            return False

        # Wipe existing run docker-data for fresh state
        if run_docker_data.exists():
            logger.info(f"Removing existing run docker-data for CRS '{crs_name}'")
            if not remove_directory_with_docker(run_docker_data):
                import shutil
                shutil.rmtree(run_docker_data)

        run_docker_data.mkdir(parents=True, exist_ok=True)

        # rsync build/<project>/ -> run/<project>/ with hardlinks
        # Exclude overlayfs work directories - these are ephemeral kernel structures
        # created with d--------- permissions that rsync can't read. containerd
        # recreates them on startup.
        logger.info(
            f"Copying build docker-data to run/{project_name} for CRS '{crs_name}' (using hardlinks)"
        )
        if not run_rsync(
            build_docker_data,
            run_docker_data,
            hard_links=True,
            link_dest=build_docker_data,
            exclude=["**/work/work"],
        ):
            logger.error(f"Failed to copy build docker-data for '{crs_name}'")
            return False

        logger.info(f"Successfully set up run docker-data for CRS '{crs_name}'")

    return True

# Default registry path
# __package__ is guaranteed to be set when running as a module
DEFAULT_REGISTRY_DIR = files(__package__).parent.parent / "crs_registry"  # type: ignore[arg-type]


def run_crs(
    config_dir: Path,
    project_name: str,
    fuzzer_name: str,
    fuzzer_args: list[str],
    oss_fuzz_dir: Path,
    build_dir: Path,
    clone_dir: Path,
    worker: str = "local",
    engine: str = "libfuzzer",
    sanitizer: str = "address",
    architecture: str = "x86_64",
    check_project_fn: Callable[[], bool] | None = None,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
    hints_dir: Path | None = None,
    harness_source: Path | None = None,
    diff_path: Path | None = None,
    external_litellm: bool = False,
    source_oss_fuzz_dir: Path | None = None,
    ensemble_dir: Path | None = None,
    disable_ensemble: bool = False,
    corpus_dir: Path | None = None,
    skip_oss_fuzz_clone: bool = False,
    coverage_build_dir: Path | None = None,
) -> bool:
    """
    Run CRS using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files (Path, already resolved)
        project_name: Name of the OSS-Fuzz project
        fuzzer_name: Name of the fuzzer to run
        fuzzer_args: Arguments to pass to the fuzzer
        oss_fuzz_dir: Path to OSS-Fuzz root directory (Path, already resolved)
        build_dir: Path to build directory (Path, already resolved)
        clone_dir: Path to clone directory for CRS repos (Path, already resolved)
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
        skip_oss_fuzz_clone: Skip cloning oss-fuzz (default: False)

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

    if not skip_oss_fuzz_clone:
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
        config_hash, crs_build_dir, crs_list = render_compose.render_run_compose(
            config_dir=config_dir,
            build_dir=build_dir,
            clone_dir=clone_dir,
            oss_fuzz_dir=oss_fuzz_dir,
            project=project_name,
            engine=engine,
            sanitizer=sanitizer,
            architecture=architecture,
            registry_dir=registry_dir,
            worker=worker,
            fuzzer_command=fuzzer_command,
            harness_source=str(harness_source) if harness_source else None,
            diff_path=diff_path,
            external_litellm=external_litellm,
            ensemble_dir=final_ensemble_dir,
            coverage_build_dir=coverage_build_dir,
        )
    except Exception as e:
        logger.error("Failed to generate compose file: %s", e)
        return False

    # Set up docker-data for run phase (fresh copy from build phase)
    if not _setup_run_docker_data(crs_list, project_name, build_dir):
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
