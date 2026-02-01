"""Utility functions for bug_finding package."""

import logging
import secrets
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


def generate_run_id() -> str:
    """Generate a random run ID for Docker Compose project naming.

    Returns:
        A 16-character hex string.
    """
    return secrets.token_hex(8)


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
    include_only: list[str] | None = None,
    ignore_errors: bool = False,
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
        include_only: List of patterns to include (excludes everything else).
                      Use '*/' to include directories for traversal.
        ignore_errors: Continue despite I/O errors (--ignore-errors). Also treats
                       exit code 23 (partial transfer) as success.

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
    if ignore_errors:
        cmd.append("--ignore-errors")
    if link_dest:
        # --link-dest must be absolute path for rsync to work correctly
        cmd.extend(["--link-dest", str(link_dest.resolve())])

    # Exclude patterns come first (processed top-to-bottom by rsync)
    if exclude:
        for pattern in exclude:
            cmd.extend(["--exclude", pattern])

    # include_only patterns: include specified, then exclude everything else
    if include_only:
        for pattern in include_only:
            cmd.extend(["--include", pattern])
        # Exclude everything not matched by include patterns
        cmd.extend(["--exclude", "*"])

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
        # Exit code 23 = partial transfer due to error (e.g., permission denied on some files)
        # When ignore_errors is True, treat this as success since important files were likely copied
        if ignore_errors and e.returncode == 23:
            logger.warning(
                "rsync completed with partial transfer (exit code 23). "
                "Some files could not be copied due to permission errors."
            )
            return True
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
    sanitizer: str = "address",
) -> bool:
    """Copy docker-data between phases using rsync with hardlinks.

    For each dind CRS:
    1. Checks source docker-data exists
    2. Wipes existing destination docker-data
    3. rsync's source -> dest with hardlinks (except mutable .db files)

    Two-phase rsync is used to handle mutable database files:
    - Phase 1: rsync with --link-dest for all files except *.db (hardlinks for blobs)
    - Phase 2: rsync *.db files without --link-dest (full copies to avoid corruption)

    The containerd metadata database (meta.db) is mutable and must NOT be hardlinked.
    If hardlinked, updates during build phase would corrupt the prepared/ directory,
    causing "blob not found" errors on subsequent builds.

    Directory structure:
        build/docker-data/<crs-name>/
        ├── prepared/                       # From prepare phase (dind-images only)
        ├── build/<project>/<sanitizer>/    # For build phase (dind + project image)
        └── run/<project>/<sanitizer>/      # For run phase (fresh copy from build)

    Args:
        crs_list: List of CRS configurations with 'name' and 'dind' keys
        project_name: Name of the project
        build_dir: Path to build directory (already resolved)
        source_subdir: Source subdirectory ("prepared" or "build")
        dest_subdir: Destination subdirectory ("build" or "run")
        phase_name: Human-readable phase name for error messages
        sanitizer: Sanitizer name (default: "address")

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
        # Source: prepared/ doesn't have project_name/sanitizer, build/ and run/ do
        if source_subdir == "prepared":
            source_path = build_dir / "docker-data" / crs_name / "prepared"
        else:
            source_path = build_dir / "docker-data" / crs_name / source_subdir / project_name / sanitizer

        dest_path = build_dir / "docker-data" / crs_name / dest_subdir / project_name / sanitizer

        # Check source exists - skip if not (some dind CRS pull at runtime)
        if not source_path.exists():
            logger.debug(
                f"{phase_name}: No {source_subdir} docker-data for CRS '{crs_name}', skipping copy"
            )
            continue

        # Wipe existing destination
        if dest_path.exists():
            logger.info(f"Removing existing {dest_subdir} docker-data for CRS '{crs_name}'")
            if not remove_directory_with_docker(dest_path):
                shutil.rmtree(dest_path)

        dest_path.mkdir(parents=True, exist_ok=True)

        # Phase 1: rsync with hardlinks, excluding mutable .db files and overlayfs work dirs
        # ignore_errors=True to handle permission denied on special dirs (e.g., systemd's inaccessible/)
        logger.info(
            f"Copying {source_subdir} docker-data to {dest_subdir}/{project_name} "
            f"for CRS '{crs_name}' (using hardlinks for immutable files)"
        )
        if not run_rsync(
            source_path,
            dest_path,
            hard_links=True,
            link_dest=source_path,
            exclude=["**/work/work", "*.db"],
            ignore_errors=True,
        ):
            logger.error(f"Failed to copy docker-data for '{crs_name}' (phase 1: hardlinks)")
            return False

        # Phase 2: rsync .db files WITHOUT hardlinks (full copies to avoid corruption)
        # These mutable database files must not be hardlinked to preserve source integrity
        logger.info(f"Copying mutable database files for CRS '{crs_name}' (full copies)")
        if not run_rsync(
            source_path,
            dest_path,
            hard_links=False,
            link_dest=None,  # No hardlinks for mutable files
            exclude=["**/work/work"],  # Skip overlayfs work directories
            include_only=["*/", "*.db"],  # Only directories and .db files
            ignore_errors=True,
        ):
            logger.error(f"Failed to copy docker-data for '{crs_name}' (phase 2: db files)")
            return False

        logger.info(f"Successfully set up {dest_subdir} docker-data for CRS '{crs_name}'")

    return True
