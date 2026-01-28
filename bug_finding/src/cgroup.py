"""Cgroup v2 management for resource control.

This module provides functions to create and manage cgroup v2 hierarchies
for Docker container resource management using cgroup-parent.
"""

import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def get_user_cgroup_base() -> Path:
    """Get the base cgroup path for the current user.

    Returns:
        Path to /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/oss-crs
    """
    uid = os.getuid()
    return Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/oss-crs")


def ensure_controllers_enabled(cgroup_path: Path, controllers: list[str]) -> None:
    """Enable controllers in cgroup.subtree_control if not already enabled.

    Args:
        cgroup_path: Path to cgroup directory
        controllers: List of controller names (e.g., ["cpuset", "memory"])

    Raises:
        OSError: If unable to write to subtree_control
    """
    subtree_control = cgroup_path / "cgroup.subtree_control"

    # Read current enabled controllers
    try:
        current = set(subtree_control.read_text().strip().split())
    except FileNotFoundError:
        current = set()

    # Determine what needs to be added
    to_add = set(controllers) - current
    if not to_add:
        return

    # Enable missing controllers
    add_str = " ".join(f"+{c}" for c in sorted(to_add))
    subtree_control.write_text(add_str)
    logger.info(f"Enabled controllers {to_add} in {subtree_control}")


def setup_cgroup_hierarchy(base_path: Path) -> None:
    """Set up cgroup hierarchy with required controllers.

    Creates oss-crs directory and enables cpuset+memory controllers at:
    1. user@<uid>.service level (parent of oss-crs)
    2. oss-crs level (for worker cgroups)

    Args:
        base_path: Path to oss-crs directory

    Raises:
        OSError: If setup fails
    """
    required_controllers = ["cpuset", "memory"]

    # Enable controllers at user@<uid>.service level
    parent_path = base_path.parent  # user@<uid>.service
    ensure_controllers_enabled(parent_path, required_controllers)

    # Create oss-crs directory
    base_path.mkdir(parents=True, exist_ok=True)

    # Enable controllers at oss-crs level
    ensure_controllers_enabled(base_path, required_controllers)


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


def compute_cgroup_resources_from_crs_list(crs_list: list[dict]) -> tuple[str, int]:
    """Compute parent cgroup resources as union of all CRS resources.

    Args:
        crs_list: List of CRS dicts with 'cpuset' and 'memory_limit'

    Returns:
        Tuple of (cpuset_str, total_memory_mb)
    """
    from bug_finding.src.render_compose.config import (
        format_cpu_list,
        parse_cpu_range,
        parse_memory_mb,
    )

    all_cpus = set()
    total_memory_mb = 0
    for crs in crs_list:
        cpus = parse_cpu_range(crs["cpuset"])
        all_cpus.update(cpus)
        total_memory_mb += parse_memory_mb(crs["memory_limit"])

    return format_cpu_list(sorted(all_cpus)), total_memory_mb


def create_crs_cgroups(
    base_path: Path,
    worker_cgroup_name: str,
    worker_cpuset: str,
    worker_memory_mb: int,
    crs_list: list[dict],
) -> Path:
    """Create worker cgroup with per-CRS sub-cgroups.

    Creates a two-level hierarchy:
    1. Worker cgroup with total resources for all CRS combined
    2. Per-CRS sub-cgroups with individual CRS resource limits

    Args:
        base_path: Base cgroup directory (e.g., /sys/fs/cgroup/.../oss-crs)
        worker_cgroup_name: Worker cgroup name (e.g., <run_id>-<phase>-<worker>)
        worker_cpuset: Total cpuset for worker (e.g., "0-15")
        worker_memory_mb: Total memory for worker in MB
        crs_list: List of CRS dicts with 'name', 'cpuset', 'memory_limit'

    Returns:
        Path to worker cgroup (sub-cgroups are <path>/<crs_name>)

    Raises:
        OSError: If cgroup creation or configuration fails
    """
    # Import parse_memory_mb from render_compose.config
    from bug_finding.src.render_compose.config import parse_memory_mb

    # Create worker cgroup with total resources
    worker_cgroup_path = base_path / worker_cgroup_name
    worker_cgroup_path.mkdir(parents=True, exist_ok=True)

    # Set worker-level cpuset and memory
    worker_cpuset_file = worker_cgroup_path / "cpuset.cpus"
    worker_cpuset_file.write_text(worker_cpuset)

    worker_memory_bytes = worker_memory_mb * 1024 * 1024
    worker_memory_file = worker_cgroup_path / "memory.max"
    worker_memory_file.write_text(str(worker_memory_bytes))

    logger.info(
        f"Created worker cgroup: {worker_cgroup_path} "
        f"(cpuset={worker_cpuset}, memory={worker_memory_mb}MB)"
    )

    # Enable controllers for sub-cgroups
    subtree_control = worker_cgroup_path / "cgroup.subtree_control"
    subtree_control.write_text("+cpuset +memory")

    # Create per-CRS sub-cgroups
    for crs in crs_list:
        crs_name = crs["name"]
        crs_cpuset = crs["cpuset"]
        crs_memory_limit = crs["memory_limit"]  # e.g., "4G" or "512M"

        # Parse memory limit to MB, then to bytes
        crs_memory_mb = parse_memory_mb(crs_memory_limit)
        crs_memory_bytes = crs_memory_mb * 1024 * 1024

        # Create CRS sub-cgroup
        crs_cgroup_path = worker_cgroup_path / crs_name
        crs_cgroup_path.mkdir(parents=True, exist_ok=True)

        # Set CRS-specific cpuset and memory
        crs_cpuset_file = crs_cgroup_path / "cpuset.cpus"
        crs_cpuset_file.write_text(crs_cpuset)

        crs_memory_file = crs_cgroup_path / "memory.max"
        crs_memory_file.write_text(str(crs_memory_bytes))

        logger.info(
            f"Created CRS sub-cgroup: {crs_cgroup_path} "
            f"(cpuset={crs_cpuset}, memory={crs_memory_limit})"
        )

    return worker_cgroup_path


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
