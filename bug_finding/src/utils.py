"""Utility functions for bug_finding package."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Global gitcache setting
USE_GITCACHE = False


def set_gitcache(enabled: bool):
    """Set global gitcache mode."""
    global USE_GITCACHE
    USE_GITCACHE = enabled


def run_git(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run git command, optionally with gitcache prefix."""
    if USE_GITCACHE:
        cmd = f"gitcache git {' '.join(args)}"
        return subprocess.run(cmd, shell=True, check=True, **kwargs)
    return subprocess.run(["git"] + args, check=True, **kwargs)


def check_rsync_available() -> bool:
    """Check if rsync is available on the system.

    Returns:
        bool: True if rsync is available, False otherwise.
    """
    if shutil.which("rsync") is None:
        logger.error(
            "rsync is required but not found. "
            "Install it with: apt-get install rsync (Debian/Ubuntu) "
            "or yum install rsync (CentOS/RHEL)"
        )
        return False
    return True


def run_rsync(
    source: Path,
    dest: Path,
    exclude: list[str] | None = None,
    *,
    archive: bool = True,
    verbose: bool = True,
    delete: bool = False,
    link_dest: Path | None = None,
    hard_links: bool = False,
) -> bool:
    """Run rsync command with specified options.

    Args:
        source: Source path (will have trailing slash added for directory contents)
        dest: Destination path
        exclude: List of patterns to exclude
        archive: Use archive mode (-a) to preserve permissions, symlinks, etc.
        verbose: Show progress output (-v)
        delete: Delete extraneous files from dest (--delete)
        link_dest: Reference directory for creating hardlinks to unchanged files
        hard_links: Preserve existing hard links in source (-H)

    Returns:
        bool: True if rsync succeeded, False otherwise.
    """
    cmd = ["rsync"]

    if archive:
        cmd.append("-a")
    if verbose:
        cmd.append("-v")
    if delete:
        cmd.append("--delete")
    if hard_links:
        cmd.append("-H")
    if link_dest:
        # --link-dest must be absolute path for rsync to work correctly
        cmd.extend(["--link-dest", str(link_dest.resolve())])

    if exclude:
        for pattern in exclude:
            cmd.extend(["--exclude", pattern])

    # Add trailing slash to source to copy contents, not the directory itself
    source_str = str(source)
    if not source_str.endswith("/"):
        source_str += "/"

    cmd.extend([source_str, str(dest)])

    logger.info("Running rsync: %s", " ".join(cmd))

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("rsync failed with exit code %d", e.returncode)
        return False
    except FileNotFoundError:
        logger.error("rsync command not found")
        return False


def copy_docker_data(
    crs_list: list[dict],
    project_name: str,
    build_dir: Path,
    source_subdir: str,
    dest_subdir: str,
    phase_name: str,
) -> bool:
    """Copy docker-data between phases using rsync with hardlinks.

    For each dind CRS:
    1. Checks source docker-data exists
    2. Wipes existing destination docker-data
    3. rsync's source -> dest with hardlinks

    Directory structure:
        build/docker-data/<crs-name>/
        ├── prepared/           # From prepare phase (dind-images only)
        ├── build/<project>/    # For build phase (dind + project image)
        └── run/<project>/      # For run phase (fresh copy from build)

    Args:
        crs_list: List of CRS configurations with 'name' and 'dind' keys
        project_name: Name of the project
        build_dir: Path to build directory (already resolved)
        source_subdir: Source subdirectory ("prepared" or "build")
        dest_subdir: Destination subdirectory ("build" or "run")
        phase_name: Human-readable phase name for error messages

    Returns:
        bool: True if successful, False otherwise
    """
    from bug_fixing.src.oss_patch.functions import remove_directory_with_docker

    dind_crs = [crs for crs in crs_list if crs.get("dind", False)]
    if not dind_crs:
        return True

    for crs in dind_crs:
        crs_name = crs["name"]

        # Build source and dest paths
        # Source: prepared/ doesn't have project_name, build/ and run/ do
        if source_subdir == "prepared":
            source_path = build_dir / "docker-data" / crs_name / "prepared"
        else:
            source_path = build_dir / "docker-data" / crs_name / source_subdir / project_name

        dest_path = build_dir / "docker-data" / crs_name / dest_subdir / project_name

        # Check source exists
        if not source_path.exists():
            logger.error(
                f"{phase_name}: Source docker-data not found for CRS '{crs_name}': {source_path}"
            )
            return False

        # Wipe existing destination
        if dest_path.exists():
            logger.info(f"Removing existing {dest_subdir} docker-data for CRS '{crs_name}'")
            if not remove_directory_with_docker(dest_path):
                shutil.rmtree(dest_path)

        dest_path.mkdir(parents=True, exist_ok=True)

        # rsync with hardlinks, exclude overlayfs work directories
        logger.info(
            f"Copying {source_subdir} docker-data to {dest_subdir}/{project_name} "
            f"for CRS '{crs_name}' (using hardlinks)"
        )
        if not run_rsync(
            source_path,
            dest_path,
            hard_links=True,
            link_dest=source_path,
            exclude=["**/work/work"],
        ):
            logger.error(f"Failed to copy docker-data for '{crs_name}'")
            return False

        logger.info(f"Successfully set up {dest_subdir} docker-data for CRS '{crs_name}'")

    return True
