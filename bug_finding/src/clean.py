#!/usr/bin/env python3
"""CRS clean operations - remove build artifacts and cached data."""

import logging
import shutil
from pathlib import Path

from bug_fixing.src.oss_patch.functions import remove_directory_with_docker

logger = logging.getLogger(__name__)


def _remove_dir_docker(path: Path) -> bool:
    """Remove directory using docker (handles root-owned and permission-restricted files).

    Args:
        path: Directory to remove

    Returns:
        bool: True if successful or directory didn't exist
    """
    if not path.exists():
        return True

    logger.info(f"Removing {path}")
    if not remove_directory_with_docker(path):
        logger.warning(f"Docker removal failed for {path}, trying shutil.rmtree")
        try:
            shutil.rmtree(path)
        except PermissionError:
            logger.error(f"Permission denied removing {path}. Try: sudo rm -rf {path}")
            return False
    return True


def _remove_dir_host(path: Path) -> bool:
    """Remove directory using shutil (for host-owned files).

    Args:
        path: Directory to remove

    Returns:
        bool: True if successful or directory didn't exist
    """
    if not path.exists():
        return True

    logger.info(f"Removing {path}")
    try:
        shutil.rmtree(path)
        return True
    except PermissionError:
        logger.error(f"Permission denied removing {path}")
        return False


def clean_all(build_dir: Path, clone_dir: Path) -> bool:
    """Clean all build artifacts and cloned repositories.

    Args:
        build_dir: Path to build directory (default: ./build)
        clone_dir: Path to clone directory (default: ./.oss-bugfind)

    Returns:
        bool: True if all removals successful
    """
    logger.info("Cleaning all build artifacts and cloned repositories")
    success = True

    # Clone dir is host-owned
    if clone_dir.exists():
        if not _remove_dir_host(clone_dir):
            success = False

    # Build dir may have root-owned files from dind
    if build_dir.exists():
        if not _remove_dir_docker(build_dir):
            success = False

    if success:
        logger.info("Clean completed successfully")
    else:
        logger.warning("Clean completed with some errors")

    return success


def clean_crs(crs_name: str, build_dir: Path, clone_dir: Path) -> bool:
    """Clean all artifacts for a specific CRS.

    Removes:
    - .oss-bugfind/crs/<crs>/
    - build/docker-data/<crs>/
    - build/out/<crs>/
    - build/work/<crs>/
    - build/artifacts/<crs>/

    Args:
        crs_name: Name of the CRS to clean
        build_dir: Path to build directory
        clone_dir: Path to clone directory

    Returns:
        bool: True if all removals successful
    """
    logger.info(f"Cleaning all artifacts for CRS: {crs_name}")
    success = True

    # CRS clone directory (host-owned)
    crs_clone = clone_dir / "crs" / crs_name
    if crs_clone.exists():
        if not _remove_dir_host(crs_clone):
            success = False

    # Build directories (may have root-owned files)
    for subdir in ["docker-data", "out", "work", "artifacts"]:
        crs_build_path = build_dir / subdir / crs_name
        if crs_build_path.exists():
            if not _remove_dir_docker(crs_build_path):
                success = False

    if success:
        logger.info(f"Clean completed successfully for CRS: {crs_name}")
    else:
        logger.warning(f"Clean completed with some errors for CRS: {crs_name}")

    return success


def clean_crs_project(crs_name: str, project_name: str, build_dir: Path) -> bool:
    """Clean artifacts for a specific CRS and project combination.

    Removes:
    - build/docker-data/<crs>/build/<project>/
    - build/docker-data/<crs>/run/<project>/
    - build/out/<crs>/<project>/
    - build/work/<crs>/<project>/
    - build/artifacts/<crs>/<project>/

    Args:
        crs_name: Name of the CRS
        project_name: Name of the project
        build_dir: Path to build directory

    Returns:
        bool: True if all removals successful
    """
    logger.info(f"Cleaning artifacts for CRS: {crs_name}, project: {project_name}")
    success = True

    # docker-data has nested structure: docker-data/<crs>/{build,run}/<project>/
    for phase in ["build", "run"]:
        docker_data_path = build_dir / "docker-data" / crs_name / phase / project_name
        if docker_data_path.exists():
            if not _remove_dir_docker(docker_data_path):
                success = False

    # Other directories: <subdir>/<crs>/<project>/
    for subdir in ["out", "work", "artifacts"]:
        project_path = build_dir / subdir / crs_name / project_name
        if project_path.exists():
            if not _remove_dir_docker(project_path):
                success = False

    if success:
        logger.info(f"Clean completed successfully for CRS: {crs_name}, project: {project_name}")
    else:
        logger.warning(f"Clean completed with some errors for CRS: {crs_name}, project: {project_name}")

    return success
