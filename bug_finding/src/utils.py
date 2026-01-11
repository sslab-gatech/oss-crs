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
    safe_links: bool = True,
) -> bool:
    """Run rsync command with specified options.

    Args:
        source: Source path (will have trailing slash added for directory contents)
        dest: Destination path
        exclude: List of patterns to exclude
        archive: Use archive mode (-a) to preserve permissions, symlinks, etc.
        verbose: Show progress output (-v)
        safe_links: Skip symlinks pointing outside source tree (--safe-links)

    Returns:
        bool: True if rsync succeeded, False otherwise.
    """
    cmd = ["rsync"]

    if archive:
        cmd.append("-a")
    if verbose:
        cmd.append("-v")
    if safe_links:
        cmd.append("--safe-links")

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
