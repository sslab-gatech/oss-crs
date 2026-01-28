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

from bug_finding.src.cgroup import (
    cleanup_cgroup,
    create_crs_cgroups,
    format_setup_instructions,
    generate_cgroup_name,
    get_user_cgroup_base,
    setup_cgroup_hierarchy,
)
from bug_finding.src.render_compose import render as render_compose
from bug_finding.src.crs_utils import (
    clone_oss_fuzz_if_needed,
    fix_build_dir_permissions,
    get_worker_crs_count,
    validate_crs_modes,
    verify_external_litellm,
)
from bug_finding.src.utils import copy_docker_data, generate_run_id

logger = logging.getLogger(__name__)

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
    skip_litellm: bool = False,
    source_oss_fuzz_dir: Path | None = None,
    ensemble_dir: Path | None = None,
    disable_ensemble: bool = False,
    corpus_dir: Path | None = None,
    skip_oss_fuzz_clone: bool = False,
    coverage_build_dir: Path | None = None,
    run_id: str | None = None,
    *,
    cgroup_parent: bool = False,
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
        skip_litellm: Skip LiteLLM/Postgres deployment entirely (default: False)
        source_oss_fuzz_dir: Optional source OSS-Fuzz directory to copy from (Path, already resolved)
        ensemble_dir: Optional base directory for ensemble sharing (Path, already resolved)
        disable_ensemble: Disable automatic ensemble directory for multi-CRS mode
        corpus_dir: Optional directory containing initial corpus files to copy to ensemble corpus
        skip_oss_fuzz_clone: Skip cloning oss-fuzz (default: False)
        coverage_build_dir: Optional directory containing coverage-instrumented binaries
        run_id: Custom run ID for Docker Compose project naming (default: random)

    Returns:
        bool: True if successful, False otherwise
    """
    # Validate project exists if checker provided TODO (don't remove todo)
    if check_project_fn and not check_project_fn():
        return False

    # Skip LiteLLM entirely if requested
    if skip_litellm:
        logger.info("Skipping LiteLLM/Postgres deployment (--skip-litellm)")
        external_litellm = True  # Tell render to skip litellm compose/network/volumes

    # Check if litellm keys are provided (only when user explicitly set --external-litellm)
    if external_litellm and not skip_litellm and not verify_external_litellm(config_dir):
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

    # Set up cgroup hierarchy if enabled (but don't create worker cgroup yet - need crs_list first)
    cgroup_config = None
    if cgroup_parent:
        base_path = get_user_cgroup_base()

        # Set up cgroup hierarchy (auto-enable controllers)
        try:
            setup_cgroup_hierarchy(base_path)
        except OSError as e:
            logger.error(f"Failed to set up cgroup hierarchy: {e}")
            logger.error(format_setup_instructions(base_path))
            return False

        # Generate unique cgroup name
        run_run_id_for_cgroup = run_id if run_id else generate_run_id()
        cgroup_name = generate_cgroup_name(run_run_id_for_cgroup, "run", worker)

        # Load config to get worker resources
        from bug_finding.src.render_compose.config import load_config, parse_memory_mb

        config = load_config(config_dir)
        resource_config = config["resource"]
        workers = resource_config.get("workers", {})

        if worker not in workers:
            logger.error(f"Worker '{worker}' not found in config-resource.yaml")
            return False

        worker_resources = workers[worker]
        worker_cpuset = worker_resources.get("cpuset", "0-15")
        worker_memory_spec = worker_resources.get("memory", "16G")
        worker_memory_mb = parse_memory_mb(worker_memory_spec)

        # Store config for cgroup creation after we get crs_list
        cgroup_config = {
            "base_path": base_path,
            "cgroup_name": cgroup_name,
            "worker_cpuset": worker_cpuset,
            "worker_memory_mb": worker_memory_mb,
        }

    # Generate compose files using render_compose module
    # Note: cgroup_parent_path will be set after cgroup creation
    logger.info("Generating compose-%s.yaml", worker)
    fuzzer_command = [fuzzer_name] + fuzzer_args
    try:
        actual_run_id, crs_build_dir, crs_list = render_compose.render_run_compose(
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
            run_id=run_id,
            cgroup_parent_path=None,  # Will be set after cgroup creation
        )
    except Exception as e:
        logger.error("Failed to generate compose file: %s", e)
        return False

    # Create cgroups now that we have crs_list
    cgroup_path = None
    if cgroup_config:
        try:
            cgroup_path = create_crs_cgroups(
                base_path=cgroup_config["base_path"],
                worker_cgroup_name=cgroup_config["cgroup_name"],
                worker_cpuset=cgroup_config["worker_cpuset"],
                worker_memory_mb=cgroup_config["worker_memory_mb"],
                crs_list=crs_list,
            )
            logger.info(f"Created worker cgroup with per-CRS sub-cgroups: {cgroup_path}")

            # Re-render compose file with cgroup_parent_path
            actual_run_id, crs_build_dir, crs_list = render_compose.render_run_compose(
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
                run_id=run_id,
                cgroup_parent_path=str(cgroup_path),
            )
        except OSError as e:
            logger.error(f"Failed to create cgroups: {e}")
            return False

    # Set up docker-data for run phase (fresh copy from build phase)
    if not copy_docker_data(
        crs_list, project_name, build_dir,
        source_subdir="build",
        dest_subdir="run",
        phase_name="Run",
        sanitizer=sanitizer,
    ):
        return False

    # Look for compose file
    compose_file = crs_build_dir / f"compose-{worker}.yaml"

    if not compose_file.exists():
        logger.error("compose-%s.yaml was not generated", worker)
        return False

    run_project = f"crs-run-{actual_run_id}-{worker}"

    logger.info("Starting services from: %s", compose_file)
    # Commands for cleanup
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

    cleanup_done = False

    def cleanup():
        """Cleanup function for compose files (runs at most once)."""
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        logger.info("Cleaning up...")
        subprocess.run(compose_down_cmd, stdin=subprocess.DEVNULL)
        fix_build_dir_permissions(build_dir)

        # Clean up cgroup if created
        if cgroup_path:
            logger.info("Cleaning up cgroup: %s", cgroup_path)
            cleanup_cgroup(cgroup_path)

    def signal_handler(signum, frame):
        """Handle termination signals (first signal cleans up, subsequent ignored)."""
        # Ignore further signals while cleaning up
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl-C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination

    # Register cleanup on normal exit
    atexit.register(cleanup)

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
        proc = subprocess.Popen(
            compose_cmd,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # Filter alarming-but-expected docker compose messages from stderr
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace")
            if "exited with code 0" in text or "Aborting on container exit" in text:
                continue
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()
        proc.wait()
        if proc.returncode != 0:
            logger.error("Docker compose failed for: %s", compose_file)
            return False
    finally:
        cleanup()

    return True
