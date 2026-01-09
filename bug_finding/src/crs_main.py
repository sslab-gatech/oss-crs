#!/usr/bin/env python3
"""Main CRS implementation for build and run operations."""

import atexit
import hashlib
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Optional

import yaml
from dotenv import dotenv_values

from . import render_compose
from .utils import check_rsync_available, run_git, run_rsync

logger = logging.getLogger(__name__)

# Default registry path
DEFAULT_REGISTRY_DIR = files(__package__).parent.parent / "crs_registry"


def _fix_build_dir_permissions(build_dir: Path):
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


def _get_absolute_path(path):
    """Returns absolute path with user expansion."""
    return str(Path(path).expanduser().resolve())


def _get_command_string(command):
    """Returns a shell escaped command string."""
    return " ".join(shlex.quote(part) for part in command)


def _verify_external_litellm(config_dir: Path) -> bool:
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


def _build_project_image(
    project_name: str, oss_fuzz_dir: Path, architecture: str
) -> bool:
    build_image_cmd = [
        "python3",
        "infra/helper.py",
        "build_image",
        "--no-pull",
        "--cache",
        "--architecture",
        architecture,
        project_name,
    ]
    try:
        subprocess.check_call(build_image_cmd, cwd=oss_fuzz_dir)
    except subprocess.CalledProcessError:
        logging.error(f"Failed to build image for {project_name}")
        return False
    return True


def _save_parent_image_tarballs(
    crs_list: list, project_name: str, build_dir: Path, project_image_prefix: str
):
    """
    Save parent image tarballs for CRS that need dind.

    Args:
        crs_list: List of CRS configurations with 'name' and 'dind' keys
        project_name: Name of the project
        build_dir: Path to build directory (already resolved)
        project_image_prefix: Prefix for project images (e.g., gcr.io/oss-fuzz)

    Returns:
        str: Path to parent image tarball directory, or None if no dind CRS
    """
    # Check if any CRS needs dind
    dind_crs = [crs for crs in crs_list if crs.get("dind", False)]
    if not dind_crs:
        return None

    # Create parent-images directory
    parent_images_dir = build_dir / "crs" / "parent-images"
    parent_images_dir.mkdir(parents=True, exist_ok=True)

    # Parent image name
    parent_image = f"{project_image_prefix}/{project_name}"
    tarball_path = parent_images_dir / f"{project_name}.tar"

    # Save parent image to tarball if not already exists
    if tarball_path.exists() and tarball_path.is_file():
        logger.info(f"Parent image tarball already exists: {tarball_path}")
    else:
        # Remove if it exists as a directory (docker-compose creates directory on failed mounts)
        if tarball_path.exists() and tarball_path.is_dir():
            import shutil
            shutil.rmtree(tarball_path)
        # Create parent directories if needed (project_name may contain slashes)
        tarball_path.parent.mkdir(parents=True, exist_ok=True)
        # Specify :latest tag explicitly to avoid saving all tags in the repository
        logger.info(f"Saving parent image {parent_image}:latest to {tarball_path}")
        save_cmd = ["docker", "save", f"{parent_image}:latest", "-o", str(tarball_path)]
        try:
            subprocess.check_call(save_cmd)
            logger.info(f"Successfully saved parent image to {tarball_path}")
        except subprocess.CalledProcessError:
            logger.error(f"Failed to save parent image {parent_image}")
            return None

    return str(parent_images_dir)


def _validate_oss_fuzz_structure(
    source_dir: Path, project_name: Optional[str] = None
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


def _clone_oss_fuzz_if_needed(
    oss_fuzz_dir: Path,
    source_oss_fuzz_dir: Optional[Path] = None,
    project_name: Optional[str] = None,
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
        if not _validate_oss_fuzz_structure(source_oss_fuzz_dir, project_name):
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


def _validate_crs_modes(
    config_dir: Path, worker: str, registry_dir: Path, diff_path: Path
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


def _clone_project_source(
    project_name: str, oss_fuzz_dir: Path, build_dir: Path
) -> bool:
    """
    Clone project source based on main_repo in project.yaml.

    Args:
        project_name: Name of the project
        oss_fuzz_dir: Path to OSS-Fuzz root directory (already resolved)
        build_dir: Path to build directory (already resolved)

    Returns:
        bool: True if successful, False otherwise
    """
    project_yaml_path = oss_fuzz_dir / "projects" / project_name / "project.yaml"

    # Validate project.yaml exists
    if not project_yaml_path.exists():
        logger.error(f"project.yaml not found: {project_yaml_path}")
        return False

    # Read main_repo from project.yaml
    try:
        with open(project_yaml_path) as f:
            project_config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to parse project.yaml: {e}")
        return False

    main_repo = project_config.get("main_repo")
    if not main_repo:
        logger.error(f"main_repo not found in {project_yaml_path}")
        return False

    # Clone to build/src/{project_name}
    clone_dest = build_dir / "src" / project_name

    if clone_dest.exists():
        logger.info(f"Source already exists at {clone_dest}, skipping clone")
        return True

    # Create parent directory if needed
    clone_dest.parent.mkdir(parents=True, exist_ok=True)

    # Clone with depth 1 and recursive submodules
    logger.info(f"Cloning {main_repo} to {clone_dest}")
    try:
        run_git(["clone", "--depth", "1", "--recursive", main_repo, str(clone_dest)])
        logger.info(f"Successfully cloned source to {clone_dest}")
        return True
    except subprocess.CalledProcessError:
        logger.error(f"Failed to clone repository: {main_repo}")
        return False


def _get_worker_crs_count(config_dir: Path, worker: str) -> int:
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


def build_crs(
    config_dir: Path,
    project_name: str,
    oss_fuzz_dir: Path,
    build_dir: Path,
    engine: str = "libfuzzer",
    sanitizer: str = "address",
    architecture: str = "x86_64",
    source_path: Path = None,
    project_path: Path = None,
    overwrite: bool = False,
    clone: bool = False,
    check_project_fn=None,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
    source_oss_fuzz_dir: Path = None,
):
    """
    Build CRS for a project using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files (Path, already resolved)
        project_name: Name of the OSS-Fuzz project
        oss_fuzz_dir: Path to OSS-Fuzz root directory (Path, already resolved)
        build_dir: Path to build directory (Path, already resolved)
        engine: Fuzzing engine (default: libfuzzer)
        sanitizer: Sanitizer to use (default: address)
        architecture: Architecture (default: x86_64)
        source_path: Optional path to local source (Path, already resolved)
        project_path: Optional path to local OSS-compatible project (Path, already resolved)
        overwrite: Overwrite existing project in oss-fuzz/projects/ (default: False)
        clone: Clone project source from main_repo in project.yaml (default: False)
        check_project_fn: Optional function to check if project exists
        registry_dir: Optional path to local oss-crs-registry directory (Path, already resolved)
        project_image_prefix: Project image prefix (default: gcr.io/oss-fuzz)
        external_litellm: Use external LiteLLM instance (default: False)
        source_oss_fuzz_dir: Optional source OSS-Fuzz directory to copy from (Path, already resolved)

    Returns:
        bool: True if successful, False otherwise
    """
    # Validate project exists if checker provided TODO (don't remove the todo)
    if check_project_fn and not check_project_fn():
        return False

    # Validate mutually exclusive options
    if clone and source_path:
        logger.error("Cannot use --clone with source_path (mutually exclusive)")
        return False

    # Note: No need to verify external_litellm during build - LiteLLM is only used during run

    if not _clone_oss_fuzz_if_needed(oss_fuzz_dir, source_oss_fuzz_dir, project_name):
        return False

    # Copy project_path to oss-fuzz/projects/{project_name} if provided
    if project_path:
        # Validate source project path (already resolved)
        if not project_path.exists():
            logger.error(f"Project path does not exist: {project_path}")
            return False
        if not project_path.is_dir():
            logger.error(f"Project path is not a directory: {project_path}")
            return False
        if not (project_path / "project.yaml").exists():
            logger.error(f"project.yaml not found in: {project_path}")
            return False

        # Destination path (handles nested names like aixcc/c/project)
        dest_path = oss_fuzz_dir / "projects" / project_name

        # Check if destination exists
        if dest_path.exists():
            if not overwrite:
                logger.warning(
                    f"Project already exists: {dest_path}. "
                    f"Skipping copy. Use --overwrite to replace it."
                )
            else:
                logger.info(f"Overwriting existing project at: {dest_path}")
                shutil.rmtree(dest_path)
                # Create parent directories for nested project names
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                # Copy project to oss-fuzz projects directory
                logger.info(f"Copying project from {project_path} to {dest_path}")
                shutil.copytree(project_path, dest_path)
                logger.info(f"Successfully copied project to {dest_path}")
        else:
            # Create parent directories for nested project names
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            # Copy project to oss-fuzz projects directory
            logger.info(f"Copying project from {project_path} to {dest_path}")
            shutil.copytree(project_path, dest_path)
            logger.info(f"Successfully copied project to {dest_path}")

    # Clone project source if requested
    # Note: --clone is for custom projects that don't have source cloning in Dockerfile
    # Standard OSS-Fuzz projects already clone source in their Dockerfile
    if clone:
        if not _clone_project_source(project_name, oss_fuzz_dir, build_dir):
            return False
        # Use cloned source as source_path
        source_path = build_dir / "src" / project_name
        logger.info(f"Using cloned source as source_path: {source_path}")

    # Build project image
    _build_project_image(project_name, oss_fuzz_dir, architecture)

    # Compute source_tag for image versioning if source_path provided
    source_tag = None
    source_path_str = None
    if source_path:
        source_path_str = str(source_path)
        source_tag = hashlib.sha256(
            source_path_str.encode() + project_name.encode()
        ).hexdigest()[:12]
        logger.info("Using source tag for image versioning: %s", source_tag)

    # Generate compose files using render_compose module
    logger.info("Generating compose-build.yaml")
    try:
        build_profiles, config_hash, crs_build_dir, crs_list = (
            render_compose.render_build_compose(
                config_dir=str(config_dir),
                build_dir=str(build_dir),
                oss_fuzz_dir=str(oss_fuzz_dir),
                project=project_name,
                engine=engine,
                sanitizer=sanitizer,
                architecture=architecture,
                registry_dir=str(registry_dir),
                source_path=source_path_str,
                project_image_prefix=project_image_prefix,
                external_litellm=external_litellm,
            )
        )
        crs_build_dir = Path(crs_build_dir)
    except Exception as e:
        logger.error("Failed to generate compose files: %s", e)
        return False

    # Save parent image tarballs for dind CRS
    parent_images_dir = _save_parent_image_tarballs(
        crs_list, project_name, build_dir, project_image_prefix
    )
    if parent_images_dir:
        logger.info(f"Parent image tarballs saved to: {parent_images_dir}")

    if not build_profiles:
        logger.error("No build profiles found")
        return False

    logger.info(
        "Found %d build profiles: %s", len(build_profiles), ", ".join(build_profiles)
    )

    # Look for compose files in the hash directory
    compose_file = crs_build_dir / "compose-build.yaml"

    if not compose_file.exists():
        logger.error("compose-build.yaml was not generated at: %s", compose_file)
        return False

    # Project name for build compose
    build_project = f"crs-build-{config_hash}"

    # Note: LiteLLM is NOT needed during build - builder doesn't use LLM services
    # LiteLLM is only started during run phase

    # Run docker compose up for each build profile
    completed_profiles = []
    try:
        for profile in build_profiles:
            logger.info("Building profile: %s", profile)

            try:
                # Step 1: Build the containers
                build_cmd = [
                    "docker",
                    "compose",
                    "-p",
                    build_project,
                    "-f",
                    str(compose_file),
                    "--profile",
                    profile,
                    "build",
                ]
                logger.info("Building containers for profile: %s", profile)
                subprocess.check_call(build_cmd)

                # Step 2: If source_path provided, copy source to workdir
                if source_path:
                    # Extract CRS name from profile (format: {crs_name}_builder)
                    crs_name = profile.replace("_builder", "")
                    service_name = f"{crs_name}_builder"

                    # Generate unique container name for docker commit
                    container_name = f"crs-source-copy-{uuid.uuid4().hex}"
                    # Use tagged image name for version control if source_tag exists
                    image_name = f"{project_name}_{crs_name}_builder"
                    if source_tag:
                        image_name = f"{image_name}:{source_tag}"

                    logger.info(
                        "Copying source from /local-source-mount to workdir for: %s",
                        service_name,
                    )
                    copy_cmd = [
                        "docker",
                        "compose",
                        "-p",
                        build_project,
                        "-f",
                        str(compose_file),
                        "--profile",
                        profile,
                        "run",
                        "--no-deps",
                        "--name",
                        container_name,
                        service_name,
                        "/bin/bash",
                        "-c",
                        'workdir=$(pwd) && cd / && rm -rf "$workdir" && cp -r /local-source-mount "$workdir"',
                    ]
                    logger.info(
                        "Running copy command: %s", _get_command_string(copy_cmd)
                    )
                    subprocess.check_call(copy_cmd)

                    # Extract original image metadata (CMD and ENTRYPOINT) to preserve them
                    logger.info(
                        "Extracting metadata from original image: %s", image_name
                    )

                    # Get original CMD
                    cmd_inspect = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            image_name,
                            "--format",
                            "{{json .Config.Cmd}}",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    original_cmd = cmd_inspect.stdout.strip()

                    # Get original ENTRYPOINT
                    entrypoint_inspect = subprocess.run(
                        [
                            "docker",
                            "inspect",
                            image_name,
                            "--format",
                            "{{json .Config.Entrypoint}}",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    original_entrypoint = entrypoint_inspect.stdout.strip()

                    # Commit the container to preserve source changes in the image
                    logger.info(
                        "Committing container %s to image %s",
                        container_name,
                        image_name,
                    )
                    commit_cmd = ["docker", "commit"]

                    # Add --change flags to restore original metadata if they exist
                    if original_cmd and original_cmd != "null":
                        commit_cmd.extend(["--change", f"CMD {original_cmd}"])
                    if original_entrypoint and original_entrypoint != "null":
                        commit_cmd.extend(
                            ["--change", f"ENTRYPOINT {original_entrypoint}"]
                        )

                    commit_cmd.extend([container_name, image_name])
                    logger.info(
                        "Running commit command: %s", _get_command_string(commit_cmd)
                    )
                    subprocess.check_call(commit_cmd)

                    # Clean up the container
                    logger.info("Removing container: %s", container_name)
                    cleanup_cmd = ["docker", "rm", container_name]
                    subprocess.check_call(cleanup_cmd)

                    logger.info(
                        "Successfully copied source and committed to image: %s",
                        image_name,
                    )

                # Step 3: Run the build
                up_cmd = [
                    "docker",
                    "compose",
                    "-p",
                    build_project,
                    "-f",
                    str(compose_file),
                    "--profile",
                    profile,
                    "up",
                    "--abort-on-container-exit",
                ]
                logger.info("Running build for profile: %s", profile)
                subprocess.check_call(up_cmd)

                completed_profiles.append(profile)
            except subprocess.CalledProcessError:
                logger.error("Docker compose operation failed for profile: %s", profile)
                return False

            logger.info("Successfully built profile: %s", profile)

        logger.info("All CRS builds completed successfully")
    finally:
        # Clean up: remove all containers from completed profiles
        logger.info("Cleaning up build services")
        if completed_profiles:
            down_cmd = [
                "docker",
                "compose",
                "-p",
                build_project,
                "-f",
                str(compose_file),
            ]
            for profile in completed_profiles:
                down_cmd.extend(["--profile", profile])
            down_cmd.extend(["down", "--remove-orphans"])
            subprocess.run(down_cmd)
        else:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    build_project,
                    "-f",
                    str(compose_file),
                    "down",
                    "--remove-orphans",
                ]
            )
        _fix_build_dir_permissions(build_dir)

    return True


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
    hints_dir: Path = None,
    harness_source: Path = None,
    diff_path: Path = None,
    external_litellm: bool = False,
    source_oss_fuzz_dir: Path = None,
    ensemble_dir: Path = None,
    disable_ensemble: bool = False,
    corpus_dir: Path = None,
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
    if external_litellm and not _verify_external_litellm(config_dir):
        logger.error("LITELLM_URL or LITELLM_KEY is not provided in the environment")
        return False

    if not _clone_oss_fuzz_if_needed(oss_fuzz_dir, source_oss_fuzz_dir, project_name):
        return False

    # Validate CRS modes against diff_path
    if not _validate_crs_modes(config_dir, worker, registry_dir, diff_path):
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
            worker_crs_count = _get_worker_crs_count(config_dir, worker)
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
        _fix_build_dir_permissions(build_dir)

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
