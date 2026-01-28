"""Cgroup v2 management for resource control.

This module provides functions to create and manage cgroup v2 hierarchies
for Docker container resource management using cgroup-parent.
"""

import os
import secrets
import time
from pathlib import Path


def get_user_cgroup_base() -> Path:
    """Get the base cgroup path for the current user.

    Returns:
        Path to /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/oss-crs
    """
    uid = os.getuid()
    return Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/oss-crs")


def check_cgroup_delegation(base_path: Path) -> tuple[bool, list[str]]:
    """Verify that required cgroup controllers are delegated.

    Args:
        base_path: Base cgroup directory path

    Returns:
        Tuple of (is_valid, missing_controllers)
        - is_valid: True if all required controllers are delegated
        - missing_controllers: List of missing controller names
    """
    required_controllers = {"cpuset", "memory"}

    if not base_path.exists():
        return False, list(required_controllers)

    # Check parent's cgroup.subtree_control
    parent_path = base_path.parent
    subtree_control_file = parent_path / "cgroup.subtree_control"

    if not subtree_control_file.exists():
        return False, list(required_controllers)

    try:
        enabled_controllers = subtree_control_file.read_text().strip().split()
        enabled_set = set(enabled_controllers)
        missing = required_controllers - enabled_set
        return len(missing) == 0, sorted(missing)
    except (OSError, PermissionError):
        return False, list(required_controllers)


def create_cgroup(
    base_path: Path,
    name: str,
    cpuset: str,
    memory_bytes: int,
) -> Path:
    """Create a cgroup with specified resource limits.

    Args:
        base_path: Base cgroup directory
        name: Cgroup name (should be unique)
        cpuset: CPU set specification (e.g., "0-15")
        memory_bytes: Memory limit in bytes

    Returns:
        Path to the created cgroup directory

    Raises:
        OSError: If cgroup creation or configuration fails
    """
    cgroup_path = base_path / name

    # Create cgroup directory
    cgroup_path.mkdir(parents=True, exist_ok=True)

    # Set cpuset.cpus
    cpuset_file = cgroup_path / "cpuset.cpus"
    cpuset_file.write_text(cpuset)

    # Set memory.max
    memory_file = cgroup_path / "memory.max"
    memory_file.write_text(str(memory_bytes))

    return cgroup_path


def cleanup_cgroup(cgroup_path: Path) -> None:
    """Remove a cgroup directory after containers have exited.

    Args:
        cgroup_path: Path to the cgroup to remove

    Note:
        This will fail if processes are still running in the cgroup.
        The caller should ensure all containers have stopped first.
    """
    if not cgroup_path.exists():
        return

    try:
        cgroup_path.rmdir()
    except OSError as e:
        # Log but don't fail - cgroup may still have processes
        # The system will clean it up when empty
        print(f"Warning: Failed to remove cgroup {cgroup_path}: {e}")


def generate_cgroup_name(run_id: str, phase: str, worker: str) -> str:
    """Generate a unique cgroup name.

    Format: <run_id>-<phase>-<timestamp>-<random>-<worker>

    Args:
        run_id: Experiment run identifier
        phase: "build" or "run"
        worker: Worker identifier (e.g., "localhost")

    Returns:
        Unique cgroup name string
    """
    timestamp = int(time.time())
    random_suffix = secrets.token_hex(4)
    # Sanitize worker name (replace special chars with dash)
    safe_worker = "".join(c if c.isalnum() else "-" for c in worker)
    return f"{run_id}-{phase}-{timestamp}-{random_suffix}-{safe_worker}"


def format_setup_instructions(base_path: Path) -> str:
    """Format instructions for setting up cgroup delegation.

    Args:
        base_path: The base cgroup path that needs setup

    Returns:
        Formatted multi-line instruction string
    """
    uid = os.getuid()
    gid = os.getgid()
    parent_path = base_path.parent

    return f"""
To enable cgroup-parent mode, create the base directory with delegation:
  sudo mkdir -p {base_path}
  sudo chown {uid}:{gid} {base_path}
  echo "+cpuset +memory" | sudo tee {parent_path}/cgroup.subtree_control
"""
