#!/usr/bin/env python3
"""Shared utilities for CRS build and run operations."""

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml
from dotenv import dotenv_values

from bug_finding.src.utils import check_rsync_available, run_git, run_rsync

logger = logging.getLogger(__name__)


def fix_build_dir_permissions(build_dir: Path):
    """Fix permission issues when containers run as root.

    FIXME: Bandaid solution for permission issues when runner executes as root.
    Changes ownership of build_dir to current user.

    Args:
        build_dir: Path to build directory
    """
    uid = os.getuid()
    gid = os.getgid()
    logger.info(f"Changing ownership of {build_dir} to {uid}:{gid}")
    chown_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{build_dir}:/target",
        "alpine",
        "chown",
        "-R",
        f"{uid}:{gid}",
        "/target",
    ]
    subprocess.run(chown_cmd)


def get_command_string(command):
    """Returns a shell escaped command string."""
    return " ".join(shlex.quote(part) for part in command)


def verify_external_litellm(config_dir: Path) -> bool:
    """Verifies LiteLLM environment variables."""

    def keys_in_dict(keys, dict_):
        return all(key in dict_ for key in keys)

    keys = ["LITELLM_URL", "LITELLM_KEY"]
    if keys_in_dict(keys, os.environ):
        return True

    dotenv_path = config_dir / ".env"
    if dotenv_path.is_file():
        dotenv_dict = dotenv_values(str(dotenv_path))
        if keys_in_dict(keys, dotenv_dict):
            return True

    return False


def validate_oss_fuzz_structure(
    source_dir: Path, project_name: str | None = None
) -> bool:
    """Validate OSS-Fuzz directory structure.

    Args:
        source_dir: Source OSS-Fuzz directory to validate
        project_name: Optional project name to validate exists

    Returns:
        bool: True if valid, False otherwise
    """
    # Check source exists
    if not source_dir.exists():
        logging.error(f"Source OSS-Fuzz directory does not exist: {source_dir}")
        return False

    # Check projects/ directory exists (required for valid OSS-Fuzz structure)
    projects_dir = source_dir / "projects"
    if not projects_dir.exists():
        logging.error(
            f"Invalid OSS-Fuzz directory structure. "
            f"'projects/' directory not found in: {source_dir}"
        )
        return False

    # If project_name provided, check it exists
    if project_name:
        project_path = projects_dir / project_name
        if not project_path.exists():
            logging.error(
                f"Project '{project_name}' not found in source OSS-Fuzz directory. "
                f"Expected path: {project_path}"
            )
            return False

    return True


def clone_oss_fuzz_if_needed(
    oss_fuzz_dir: Path,
    source_oss_fuzz_dir: Path | None = None,
    project_name: str | None = None,
) -> bool:
    """Clone or copy OSS-Fuzz to the standard location if needed.

    When source_oss_fuzz_dir and project_name are provided, uses rsync for
    optimized selective copy:
    - Excludes build/ directory entirely (FR-001)
    - Copies only the target project from projects/ (FR-002)
    - Copies all other top-level directories (infra/, docs/, etc.) (FR-003)
    - Handles symlinks gracefully (FR-004)
    - Supports nested project names (FR-005)

    Args:
        oss_fuzz_dir: Destination directory for OSS-Fuzz (standard location)
        source_oss_fuzz_dir: Optional source directory to copy from
        project_name: Optional project name for selective copy optimization

    Returns:
        bool: True if successful, False otherwise
    """
    # If source directory provided, copy from it
    if source_oss_fuzz_dir:
        # Validate source structure
        if not validate_oss_fuzz_structure(source_oss_fuzz_dir, project_name):
            return False

        # Use optimized rsync copy if project_name is provided
        if project_name:
            # Check rsync is available (FR-008)
            if not check_rsync_available():
                return False

            logging.info(
                f"Optimized copy of OSS-Fuzz from {source_oss_fuzz_dir} to {oss_fuzz_dir} "
                f"(project: {project_name})"
            )

            # Create destination directory
            oss_fuzz_dir.mkdir(parents=True, exist_ok=True)

            # Phase 1: Copy base structure excluding {build,projects,.git}/
            logging.info("Phase 1: Copying base structure (excluding build/, projects/, and .git/)")
            if not run_rsync(
                source=source_oss_fuzz_dir,
                dest=oss_fuzz_dir,
                exclude=["build/", "projects/", ".git/"],
            ):
                logging.error("Failed to copy base OSS-Fuzz structure")
                return False

            # Phase 2: Copy only the target project from projects/
            logging.info(f"Phase 2: Copying target project: {project_name}")
            source_project = source_oss_fuzz_dir / "projects" / project_name
            dest_project = oss_fuzz_dir / "projects" / project_name

            # Create parent directories for nested project names (e.g., aixcc/c/myproject)
            dest_project.parent.mkdir(parents=True, exist_ok=True)

            if not run_rsync(source=source_project, dest=dest_project):
                logging.error(f"Failed to copy project: {project_name}")
                return False

            # Phase 3: Copy target project's build artifacts if they exist
            # Copy both build/out/ (compiled fuzzers) and build/work/ (intermediate files)
            # This supports incremental builds and pre-built fuzzer reuse
            # Handle nested project names by using hyphenated form for matching
            project_base = project_name.replace("/", "-")

            for build_subdir in ["out", "work"]:
                source_build = source_oss_fuzz_dir / "build" / build_subdir
                if not source_build.exists():
                    continue

                # Find directories matching project name (e.g., json-c, json-c-asan)
                matching_dirs = [
                    d for d in source_build.iterdir()
                    if d.is_dir() and (d.name == project_base or d.name.startswith(f"{project_base}-"))
                ]
                if matching_dirs:
                    logging.info(f"Phase 3: Copying build/{build_subdir}/ for {project_name}")
                    dest_build = oss_fuzz_dir / "build" / build_subdir
                    dest_build.mkdir(parents=True, exist_ok=True)
                    for src_dir in matching_dirs:
                        dest_dir = dest_build / src_dir.name
                        logging.info(f"  Copying {src_dir.name}")
                        if not run_rsync(source=src_dir, dest=dest_dir):
                            logging.warning(f"Failed to copy build artifact: {src_dir.name}")
                            # Continue anyway - build artifacts are optional

            logging.info(f"Successfully copied OSS-Fuzz to {oss_fuzz_dir}")
            return True

        # Fallback to shutil.copytree for full copy (legacy behavior)
        logging.info(f"Copying OSS-Fuzz from {source_oss_fuzz_dir} to {oss_fuzz_dir}")
        try:
            # Create parent directory if needed
            oss_fuzz_dir.parent.mkdir(parents=True, exist_ok=True)
            # Copy with symlink handling to avoid errors from dangling symlinks
            shutil.copytree(
                source_oss_fuzz_dir,
                oss_fuzz_dir,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )
            logging.info(f"Successfully copied OSS-Fuzz to {oss_fuzz_dir}")
            return True
        except Exception as e:
            logging.error(f"Failed to copy OSS-Fuzz directory: {e}")
            return False

    # Otherwise, clone from GitHub (if not already present)
    # Check if directory already exists with valid structure (from previous build)
    if oss_fuzz_dir.exists():
        if project_name:
            # For sparse checkout, verify infra/ and project exist
            if (oss_fuzz_dir / "infra").exists() and (
                oss_fuzz_dir / "projects" / project_name
            ).exists():
                logging.info(
                    f"OSS-Fuzz directory already exists with project {project_name}: {oss_fuzz_dir}"
                )
                return True
        else:
            # For full clone, just check infra/ exists
            if (oss_fuzz_dir / "infra").exists():
                logging.info(f"OSS-Fuzz directory already exists: {oss_fuzz_dir}")
                return True
        # Directory exists but invalid - remove and re-clone
        logging.warning(f"OSS-Fuzz directory exists but invalid, removing: {oss_fuzz_dir}")
        shutil.rmtree(oss_fuzz_dir)

    logging.info(f"Cloning oss-fuzz to: {oss_fuzz_dir}")
    try:
        # Create parent directory if needed
        oss_fuzz_dir.parent.mkdir(parents=True, exist_ok=True)

        if project_name:
            # Use sparse checkout to only fetch infra/ and the target project
            logging.info(f"Using sparse checkout for project: {project_name}")
            run_git([
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--depth", "1",
                "https://github.com/google/oss-fuzz",
                str(oss_fuzz_dir),
            ])
            # Initialize sparse checkout and set paths
            run_git(["sparse-checkout", "init", "--cone"], cwd=oss_fuzz_dir)
            run_git(
                ["sparse-checkout", "set", "infra", f"projects/{project_name}"],
                cwd=oss_fuzz_dir,
            )
            # Checkout the sparse tree
            run_git(["checkout"], cwd=oss_fuzz_dir)
        else:
            # Full shallow clone (legacy behavior)
            run_git([
                "clone",
                "--depth", "1",
                "https://github.com/google/oss-fuzz",
                str(oss_fuzz_dir),
            ])

        logging.info(f"Successfully cloned OSS-Fuzz to {oss_fuzz_dir}")
        return True
    except subprocess.CalledProcessError:
        logging.error("Failed to clone oss-fuzz repository")
        return False


def validate_crs_modes(
    config_dir: Path, worker: str, registry_dir: Path, diff_path: Path | None
) -> bool:
    """
    Validate that CRS mode requirements match the provided diff_path.

    Args:
        config_dir: Directory containing CRS configuration files (already resolved)
        worker: Worker name
        registry_dir: Path to oss-crs-registry directory (already resolved)
        diff_path: Path to diff file (or None)

    Returns:
        bool: True if validation passes, False otherwise
    """
    config_resource_path = config_dir / "config-resource.yaml"
    if not config_resource_path.exists():
        logger.error(f"config-resource.yaml not found: {config_resource_path}")
        return False

    with open(config_resource_path) as f:
        resource_config = yaml.safe_load(f)

    # Get CRS configurations
    crs_configs = resource_config.get("crs", {})
    if not crs_configs:
        logger.error("No CRS defined in config-resource.yaml")
        return False

    # Verify registry exists
    if not registry_dir.exists():
        logger.warning(f"Registry not found at {registry_dir}")
        return False

    # Check each CRS assigned to this worker
    for crs_name, crs_config in crs_configs.items():
        # Check if this CRS is assigned to the worker
        crs_workers = crs_config.get("workers", [])
        if worker not in crs_workers:
            continue

        # Load config-crs.yaml for this CRS
        crs_config_yaml_path = registry_dir / crs_name / "config-crs.yaml"
        if not crs_config_yaml_path.exists():
            logger.warning(
                f"config-crs.yaml not found for CRS '{crs_name}' at {crs_config_yaml_path}, skipping mode validation"
            )
            continue

        try:
            with open(crs_config_yaml_path) as f:
                crs_config_data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.warning(
                f"Failed to parse config-crs.yaml for CRS '{crs_name}': {e}, skipping mode validation"
            )
            continue

        # Extract CRS-specific config
        if not crs_config_data or crs_name not in crs_config_data:
            logger.warning(
                f"CRS '{crs_name}' config not found in config-crs.yaml, skipping mode validation"
            )
            continue

        crs_data = crs_config_data[crs_name]
        # Handle both dict and list formats
        if not isinstance(crs_data, dict):
            # Empty list or other format - treat as both modes supported
            supported_modes = ["full", "delta"]
        else:
            # Get supported modes (default to both if not specified)
            supported_modes = crs_data.get("modes", ["full", "delta"])
        if not isinstance(supported_modes, list):
            supported_modes = [supported_modes]

        # Validate mode requirements
        if "delta" in supported_modes and "full" not in supported_modes:
            # CRS only supports delta mode - diff is required
            if not diff_path:
                logger.error(
                    f"CRS '{crs_name}' only supports delta mode but --diff was not specified. "
                    f"Please provide a diff file with --diff."
                )
                return False
        elif "full" in supported_modes and "delta" not in supported_modes:
            # CRS only supports full mode - diff must not be specified
            if diff_path:
                logger.error(
                    f"CRS '{crs_name}' only supports full mode but --diff was specified. "
                    f"Please run without --diff."
                )
                return False
        # If both modes are supported, no validation needed

    return True


def get_worker_crs_count(config_dir: Path, worker: str) -> int:
    """Get count of CRS instances assigned to a worker.

    Args:
        config_dir: Path to config directory containing config-resource.yaml
        worker: Worker name to check

    Returns:
        Number of CRS instances on the worker (>1 means ensemble mode)
    """
    config_resource_path = config_dir / "config-resource.yaml"
    with open(config_resource_path) as f:
        resource_config = yaml.safe_load(f)
    crs_configs = resource_config.get("crs", {})
    return sum(
        1 for cfg in crs_configs.values() if worker in cfg.get("workers", [])
    )
