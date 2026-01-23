#!/usr/bin/env python3
"""CRS build operations."""

import logging
import shutil
import subprocess
import uuid
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from bug_finding.src.render_compose import render as render_compose
from bug_finding.src.crs_utils import (
    clone_oss_fuzz_if_needed,
    fix_build_dir_permissions,
    get_command_string,
)
from bug_finding.src.prepare import (
    check_images_exist,
    get_all_bake_image_tags,
    load_images_to_docker_data,
    prepare_crs,
)
from bug_finding.src.utils import copy_docker_data, run_git

logger = logging.getLogger(__name__)

# Default registry path
# __package__ is guaranteed to be set when running as a module
DEFAULT_REGISTRY_DIR = files(__package__).parent.parent / "crs_registry"  # type: ignore[arg-type]


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
        subprocess.check_call(build_image_cmd, cwd=oss_fuzz_dir, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        logging.error(f"Failed to build image for {project_name}")
        return False
    return True


def _setup_build_docker_data(
    crs_list: list[dict[str, Any]],
    project_name: str,
    build_dir: Path,
    project_image_prefix: str,
) -> bool:
    """
    Set up docker-data for build phase with prepared images + project image.

    For each dind CRS:
    1. Copies prepared/ -> build/<project>/ using rsync with hardlinks
    2. Loads project image into build/<project>/

    Args:
        crs_list: List of CRS configurations with 'name' and 'dind' keys
        project_name: Name of the project
        build_dir: Path to build directory (already resolved)
        project_image_prefix: Image prefix (e.g., 'gcr.io/oss-fuzz')

    Returns:
        bool: True if successful, False otherwise
    """
    # Copy prepared -> build using shared helper
    if not copy_docker_data(
        crs_list, project_name, build_dir,
        source_subdir="prepared",
        dest_subdir="build",
        phase_name="Build",
    ):
        return False

    # Load project image into build docker-data
    dind_crs = [crs for crs in crs_list if crs.get("dind", False)]
    if not dind_crs:
        return True

    project_image = f"{project_image_prefix}/{project_name}:latest"

    for crs in dind_crs:
        crs_name = crs["name"]
        build_docker_data = build_dir / "docker-data" / crs_name / "build" / project_name

        logger.info(
            f"Loading project image '{project_image}' into build docker-data for CRS '{crs_name}'"
        )
        if not load_images_to_docker_data([project_image], build_docker_data):
            logger.error(
                f"Failed to load project image into docker-data for CRS '{crs_name}'"
            )
            return False

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


def build_crs(
    config_dir: Path,
    project_name: str,
    oss_fuzz_dir: Path,
    build_dir: Path,
    clone_dir: Path,
    engine: str = "libfuzzer",
    sanitizer: str = "address",
    architecture: str = "x86_64",
    source_path: Path | None = None,
    project_path: Path | None = None,
    overwrite: bool = False,
    clone: bool = False,
    check_project_fn: Callable[[], bool] | None = None,
    registry_dir: Path = DEFAULT_REGISTRY_DIR,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
    source_oss_fuzz_dir: Path | None = None,
    skip_oss_fuzz_clone: bool = False,
    prepare_images: bool = True,
    force_rebuild: bool = False,
) -> bool:
    """
    Build CRS for a project using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files (Path, already resolved)
        project_name: Name of the OSS-Fuzz project
        oss_fuzz_dir: Path to OSS-Fuzz root directory (Path, already resolved)
        build_dir: Path to build directory (Path, already resolved)
        clone_dir: Path to clone directory for CRS repos (Path, already resolved)
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
        skip_oss_fuzz_clone: Skip cloning oss-fuzz (default: False)
        prepare_images: Auto-prepare CRS if bake images are missing (default: True)

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

    if not skip_oss_fuzz_clone:
        if not clone_oss_fuzz_if_needed(oss_fuzz_dir, source_oss_fuzz_dir, project_name):
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

    # Generate compose files using render_compose module
    logger.info("Generating compose-build.yaml")
    try:
        build_profiles, config_hash, crs_build_dir, crs_list = (
            render_compose.render_build_compose(
                config_dir=config_dir,
                build_dir=build_dir,
                clone_dir=clone_dir,
                oss_fuzz_dir=oss_fuzz_dir,
                project=project_name,
                engine=engine,
                sanitizer=sanitizer,
                architecture=architecture,
                registry_dir=registry_dir,
                source_path=source_path,
                project_image_prefix=project_image_prefix,
                external_litellm=external_litellm,
            )
        )
    except Exception as e:
        logger.error("Failed to generate compose files: %s", e)
        return False

    # Auto-prepare CRS if docker-bake.hcl exists and images are missing
    # or if dind CRS has no prepared docker-data
    if prepare_images:
        for crs in crs_list:
            crs_name = crs["name"]
            crs_path = Path(crs["path"])
            bake_file = crs_path / "docker-bake.hcl"

            if bake_file.exists():
                needs_prepare = False
                reason = ""

                # Check if bake images are missing on host
                image_tags = get_all_bake_image_tags(bake_file)
                if image_tags:
                    missing = check_images_exist(image_tags)
                    if missing:
                        needs_prepare = True
                        reason = f"missing bake images: {missing}"

                # For dind CRS, also check if prepared docker-data exists and has content
                if crs.get("dind", False) and not needs_prepare:
                    prepared_path = build_dir / "docker-data" / crs_name / "prepared"
                    prepared_empty = (
                        not prepared_path.exists()
                        or not prepared_path.is_dir()
                        or not any(prepared_path.iterdir())
                    )
                    if prepared_empty:
                        needs_prepare = True
                        reason = "docker-data not prepared for dind"

                if needs_prepare:
                    logger.info(
                        f"CRS '{crs_name}' needs prepare ({reason}). Running prepare..."
                    )
                    if not prepare_crs(crs_name, build_dir, clone_dir, registry_dir):
                        logger.error(f"Failed to auto-prepare CRS '{crs_name}'")
                        return False
                    logger.info(f"Auto-prepare completed for CRS '{crs_name}'")

    # Set up docker-data for build phase (prepared + project image)
    if not _setup_build_docker_data(
        crs_list, project_name, build_dir, project_image_prefix
    ):
        return False

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
                if force_rebuild:
                    build_cmd.append("--no-cache")
                logger.info("Building containers for profile: %s", profile)
                subprocess.check_call(build_cmd, stdin=subprocess.DEVNULL)

                # Step 2: If source_path provided, copy source to workdir
                if source_path:
                    # Extract CRS name from profile (format: {crs_name}_builder)
                    crs_name = profile.replace("_builder", "")
                    service_name = f"{crs_name}_builder"

                    # Generate unique container name for docker commit
                    container_name = f"crs-source-copy-{uuid.uuid4().hex}"
                    image_name = f"{project_name}_{crs_name}_builder"

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
                        "Running copy command: %s", get_command_string(copy_cmd)
                    )
                    subprocess.check_call(copy_cmd, stdin=subprocess.DEVNULL)

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
                        "Running commit command: %s", get_command_string(commit_cmd)
                    )
                    subprocess.check_call(commit_cmd, stdin=subprocess.DEVNULL)

                    # Clean up the container
                    logger.info("Removing container: %s", container_name)
                    cleanup_cmd = ["docker", "rm", container_name]
                    subprocess.check_call(cleanup_cmd, stdin=subprocess.DEVNULL)

                    logger.info(
                        "Successfully copied source and committed to image: %s",
                        image_name,
                    )

                # Step 3: Run the build (single container per profile)
                service_name = profile  # profile name matches service name
                run_cmd = [
                    "docker",
                    "compose",
                    "-p",
                    build_project,
                    "-f",
                    str(compose_file),
                    "--profile",
                    profile,
                    "run",
                    "--rm",
                    service_name,
                ]
                logger.info("Running build for profile: %s", profile)
                subprocess.check_call(run_cmd, stdin=subprocess.DEVNULL)

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
            subprocess.run(down_cmd, stdin=subprocess.DEVNULL)
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
                ],
                stdin=subprocess.DEVNULL,
            )
        fix_build_dir_permissions(build_dir)

    return True
