#!/usr/bin/env python3
"""CRS prepare operations - pre-build docker images for dind."""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from bug_fixing.src.oss_patch.functions import (
    change_ownership_with_docker,
    docker_image_exists,
    prepare_docker_cache_builder,
    remove_directory_with_docker,
)
from bug_fixing.src.oss_patch.globals import (
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
)
from bug_finding.src.render_compose.config import clone_crs_if_needed

logger = logging.getLogger(__name__)


def parse_bake_hcl(bake_path: Path) -> dict:
    """Parse docker-bake.hcl using docker buildx bake --print.

    Args:
        bake_path: Path to docker-bake.hcl file

    Returns:
        dict with 'group' and 'target' keys as output by docker buildx bake --print
    """
    try:
        result = subprocess.run(
            ["docker", "buildx", "bake", "--print", "-f", str(bake_path)],
            cwd=bake_path.parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to parse bake file: %s", e.stderr)
        return {"group": {}, "target": {}}

    # Output contains progress lines before JSON, find the JSON part
    output = result.stdout
    json_start = output.find("{")
    if json_start == -1:
        logger.error("No JSON found in bake --print output")
        return {"group": {}, "target": {}}

    try:
        return json.loads(output[json_start:])
    except json.JSONDecodeError as e:
        logger.error("Failed to parse bake JSON: %s", e)
        return {"group": {}, "target": {}}


def get_all_bake_image_tags(bake_path: Path) -> list[str]:
    """Get all image tags from docker-bake.hcl.

    Args:
        bake_path: Path to docker-bake.hcl file

    Returns:
        List of all image tags from all targets
    """
    parsed = parse_bake_hcl(bake_path)
    targets = parsed.get("target", {})

    image_tags = []
    for target_data in targets.values():
        tags = target_data.get("tags", [])
        image_tags.extend(tags)

    return image_tags


def get_dind_image_tags(bake_path: Path) -> list[str]:
    """Get image tags from dind-images group in docker-bake.hcl.

    Args:
        bake_path: Path to docker-bake.hcl file

    Returns:
        List of image tags (e.g., ["internal-runner:latest"])
    """
    parsed = parse_bake_hcl(bake_path)
    groups = parsed.get("group", {})
    targets = parsed.get("target", {})

    dind_group = groups.get("dind-images", {})
    dind_targets = dind_group.get("targets", []) if dind_group else []
    if not dind_targets:
        logger.warning("No dind-images group found in %s", bake_path)
        return []

    image_tags = []
    for target_name in dind_targets:
        if target_name in targets:
            tags = targets[target_name].get("tags", [])
            image_tags.extend(tags)
        else:
            logger.warning("Target '%s' not found in bake file", target_name)

    return image_tags


def check_images_exist(image_tags: list[str]) -> list[str]:
    """Check which images don't exist locally.

    Args:
        image_tags: List of image tags to check

    Returns:
        List of image tags that don't exist locally
    """
    if not image_tags:
        return []

    # Get all local images
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    )
    local_images = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()

    missing = []
    for tag in image_tags:
        if tag not in local_images:
            missing.append(tag)

    return missing


def build_bake_images(crs_path: Path) -> bool:
    """Run docker buildx bake to build all images.

    Args:
        crs_path: Path to CRS directory containing docker-bake.hcl

    Returns:
        bool: True if successful, False otherwise
    """
    bake_file = crs_path / "docker-bake.hcl"
    if not bake_file.exists():
        logger.error("docker-bake.hcl not found in %s", crs_path)
        return False

    logger.info("Building images with docker buildx bake in %s", crs_path)
    try:
        subprocess.check_call(
            ["docker", "buildx", "bake", "-f", str(bake_file)],
            cwd=crs_path,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error("docker buildx bake failed: %s", e)
        return False


def build_runner_image(crs_path: Path, crs_name: str) -> bool:
    """Build runner.Dockerfile for the CRS.

    Args:
        crs_path: Path to CRS directory containing runner.Dockerfile
        crs_name: Name of the CRS (used for image tag)

    Returns:
        bool: True if successful, False otherwise
    """
    runner_dockerfile = crs_path / "runner.Dockerfile"
    if not runner_dockerfile.exists():
        logger.warning("runner.Dockerfile not found in %s, skipping", crs_path)
        return True

    image_name = f"{crs_name}_runner"
    logger.info("Building runner image %s from %s", image_name, crs_path)
    try:
        subprocess.check_call(
            [
                "docker",
                "build",
                "-t",
                image_name,
                "-f",
                "runner.Dockerfile",
                ".",
            ],
            cwd=crs_path,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Failed to build runner image: %s", e)
        return False


# TODO (do not remove this comment) consolidate w/ oss-patch
def load_images_to_docker_data(images: list[str], docker_data_path: Path) -> bool:
    """Save images to tarballs and load into docker-data directory via ubuntu-dind.

    This is adapted from bug_fixing/src/oss_patch/functions.py:load_docker_images_to_dir()

    Args:
        images: List of image tags to save and load
        docker_data_path: Path to docker-data directory (mounted as /var/lib/docker)

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("Loading docker images to %s", docker_data_path)

    if not images:
        logger.warning("No images to load")
        return True

    # Ensure docker data manager image exists
    if not prepare_docker_cache_builder():
        logger.error("Failed to prepare docker cache builder image")
        return False

    # Create docker-data directory
    docker_data_path.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as tmp_dir:
        images_path = Path(tmp_dir)

        # Save each image to tarball
        for image_name in images:
            # Ensure :latest tag to avoid saving all tags
            if ":" not in image_name:
                image_name = f"{image_name}:latest"

            if not docker_image_exists(image_name):
                logger.error("Image '%s' does not exist in docker daemon", image_name)
                return False

            # Use image name (with tag) as tarball name
            tarball_name = image_name.replace("/", "_").replace(":", "_") + ".tar"
            tarball_path = images_path / tarball_name

            logger.info("Saving image %s to %s", image_name, tarball_path)
            try:
                subprocess.check_call(
                    ["docker", "save", "-o", str(tarball_path), image_name],
                )
            except subprocess.CalledProcessError as e:
                logger.error("Failed to save image %s: %s", image_name, e)
                return False

        # Load images into docker-data via ubuntu-dind container
        docker_load_cmd = (
            f"docker run --privileged --rm "
            f"-v {docker_data_path}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {images_path}:/images "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
            f"sh -c 'for f in /images/*; do docker load -i \"$f\"; done'"
        )

        logger.info("Loading images into docker-data directory")
        proc = subprocess.run(
            docker_load_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode != 0:
            logger.error(
                "Failed to load images: %s", proc.stderr.decode()[:500]
            )
            return False

        # Fix ownership
        change_ownership_with_docker(docker_data_path)

    logger.info("Successfully loaded %d images to docker-data", len(images))
    return True


def prepare_crs(
    crs_name: str,
    build_dir: Path,
    clone_dir: Path,
    registry_dir: Path,
) -> bool:
    """Prepare a CRS by pre-building docker images for dind.

    1. Clone/locate CRS source (using existing clone_crs_if_needed)
    2. Check if <crs_path>/docker-bake.hcl exists
    3. If yes:
       a. Run `docker buildx bake` to build all images
       b. Parse dind-images group to get target names
       c. Get image tags from those targets
       d. Save images to tarballs
       e. Load into docker-data directory via ubuntu-dind

    Args:
        crs_name: Name of the CRS to prepare (must exist in registry)
        build_dir: Build output directory
        clone_dir: Directory for cloned repositories
        registry_dir: Path to crs_registry

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info("Preparing CRS: %s", crs_name)

    # Create crs clone directory
    crs_clone_dir = clone_dir / "crs"
    crs_clone_dir.mkdir(parents=True, exist_ok=True)

    # Clone/locate CRS source
    crs_path = clone_crs_if_needed(crs_name, crs_clone_dir, registry_dir)
    if crs_path is None:
        logger.error("Failed to locate CRS '%s'", crs_name)
        return False

    # Check if docker-bake.hcl exists
    bake_file = crs_path / "docker-bake.hcl"
    if bake_file.exists():
        # Build images with docker buildx bake
        if not build_bake_images(crs_path):
            logger.error("Failed to build images for CRS '%s'", crs_name)
            return False

        # Get dind-images group tags and load into docker-data
        dind_images = get_dind_image_tags(bake_file)
        if dind_images:
            logger.info("Found %d dind images: %s", len(dind_images), dind_images)

            # Load images into docker-data directory
            # Uses same base path as compose template: build/docker-data/<crs-name>/
            # Note: compose expects build/docker-data/<crs-name>/<project>/ but prepare is project-independent
            docker_data_path = build_dir / "docker-data" / crs_name / "prepared"

            # Wipe existing docker-data to ensure fresh state
            # Use docker to remove since files are owned by root from dind
            if docker_data_path.exists() and docker_data_path.is_dir():
                logger.info("Removing existing docker-data at %s", docker_data_path)
                if not remove_directory_with_docker(docker_data_path):
                    logger.warning(
                        "Failed to remove docker-data with docker, trying shutil"
                    )
                    shutil.rmtree(docker_data_path)

            if not load_images_to_docker_data(dind_images, docker_data_path):
                logger.error("Failed to load images for CRS '%s'", crs_name)
                return False
    else:
        logger.info("No docker-bake.hcl found for CRS '%s', skipping bake", crs_name)

    # Build runner image
    if not build_runner_image(crs_path, crs_name):
        logger.error("Failed to build runner image for CRS '%s'", crs_name)
        return False

    logger.info("Successfully prepared CRS '%s'", crs_name)
    return True
