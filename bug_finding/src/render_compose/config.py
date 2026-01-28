"""Configuration loading and resource management for compose rendering."""

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bug_finding.src.utils import run_git

logger = logging.getLogger(__name__)


@dataclass
class ComposeEnvironment:
    """Environment data for compose rendering."""

    config_dir: Path
    build_dir: Path
    template_path: Path
    oss_fuzz_path: Path
    config_name: str  # Name of the config directory (e.g., "ensemble-c")
    crs_build_dir: Path
    output_dir: Path
    oss_crs_registry_path: Path
    config: dict[str, Any]
    resource_config: dict[str, Any]
    crs_paths: dict[str, str]
    crs_pkg_data: dict[str, dict[str, Any]]


def load_config(config_dir: Path) -> dict[str, Any]:
    """Load all configuration files from the config directory."""
    config = {}

    # Load resource configuration
    resource_config_path = config_dir / "config-resource.yaml"
    if not resource_config_path.exists():
        raise FileNotFoundError(f"Required file not found: {resource_config_path}")
    with open(resource_config_path) as f:
        config["resource"] = yaml.safe_load(f)

    # Load worker configuration (optional)
    worker_config_path = config_dir / "config-worker.yaml"
    if worker_config_path.exists():
        with open(worker_config_path) as f:
            config["worker"] = yaml.safe_load(f)
    else:
        config["worker"] = {}

    return config


def parse_cpu_range(cpu_spec: str) -> list[int]:
    """
    Parse CPU specification and return list of CPU cores.

    Supports multiple formats:
    - Range: '0-7' → [0,1,2,3,4,5,6,7]
    - List: '0,2,4,6' → [0,2,4,6]
    - Single: '5' → [5]
    - Mixed: '0-3,8,12-15' → [0,1,2,3,8,12,13,14,15]

    Args:
      cpu_spec: CPU specification string

    Returns:
      List of CPU core numbers in ascending order
    """
    cpu_list = []

    # Split by comma to handle comma-separated values
    parts = cpu_spec.split(",")

    for part in parts:
        part = part.strip()
        if "-" in part:
            # Range format: '0-7'
            start, end = part.split("-", 1)
            cpu_list.extend(range(int(start), int(end) + 1))
        else:
            # Single core: '5'
            cpu_list.append(int(part))

    # Remove duplicates and sort
    return sorted(set(cpu_list))


def format_cpu_list(cpu_list: list[int]) -> str:
    """
    Format a list of CPU cores as comma-separated string.

    Args:
      cpu_list: List of CPU core numbers

    Returns:
      Comma-separated string (e.g., '0,1,2,3')
    """
    return ",".join(map(str, cpu_list))


def parse_memory_mb(memory_spec: str) -> int:
    """
    Parse memory specification and return value in MB.

    Args:
      memory_spec: Memory specification (e.g., '4G', '512M', '1024')

    Returns:
      Memory in megabytes
    """
    memory_spec = memory_spec.strip().upper()
    if memory_spec.endswith("G"):
        return int(memory_spec[:-1]) * 1024
    if memory_spec.endswith("M"):
        return int(memory_spec[:-1])
    # Assume MB if no unit specified
    return int(memory_spec)


def format_memory(memory_mb: int) -> str:
    """
    Format memory in MB back to string with appropriate unit.

    Args:
      memory_mb: Memory in megabytes

    Returns:
      Formatted string (e.g., '4G', '512M')
    """
    if memory_mb >= 1024 and memory_mb % 1024 == 0:
        return f"{memory_mb // 1024}G"
    return f"{memory_mb}M"


def clone_crs_if_needed(
    crs_name: str, crs_build_dir: Path, registry_dir: Path
) -> Path | None:
    """
    Clone a CRS repository if needed, or return local path if specified.

    Args:
      crs_name: Name of the CRS to clone
      crs_build_dir: Directory where CRS repositories are cloned
      registry_dir: Path to oss-crs-registry directory

    Returns:
      Path to the CRS directory (either cloned or local), or None on error
    """
    crs_path = crs_build_dir / crs_name

    # Parse crs_registry to get CRS metadata
    crs_meta_path = registry_dir / crs_name / "pkg.yaml"

    if not crs_meta_path.exists():
        print(f"ERROR: CRS metadata not found for '{crs_name}' at {crs_meta_path}")
        return None

    # Parse pkg.yaml using PyYAML
    try:
        with open(crs_meta_path) as f:
            pkg_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"ERROR: Failed to parse {crs_meta_path}: {e}")
        return None

    source_config = pkg_config.get("source", {})
    local_path = source_config.get("local_path")
    crs_url = source_config.get("url")
    crs_ref = source_config.get("ref")

    # Priority: local_path takes precedence over url
    if local_path:
        # Resolve local_path relative to cwd (working directory), with ~ expansion
        local_path_resolved = Path(local_path).expanduser().resolve()

        if not local_path_resolved.exists():
            print(f"ERROR: Local CRS path does not exist: {local_path_resolved}")
            return None

        if not local_path_resolved.is_dir():
            print(f"ERROR: Local CRS path is not a directory: {local_path_resolved}")
            return None

        # Return the local path directly without creating symlinks
        logging.info(f"Using local CRS '{crs_name}' from {local_path_resolved}")
        return local_path_resolved

    if crs_url:
        try:
            if crs_path.exists():
                # Fetch and checkout the ref if directory already exists
                logging.info(f"CRS '{crs_name}' exists at {crs_path}, fetching updates")
                run_git(
                    ["-C", str(crs_path), "fetch", "--all"],
                    stdout=subprocess.DEVNULL,
                )
                if crs_ref:
                    run_git(
                        ["-C", str(crs_path), "checkout", crs_ref],
                        stdout=subprocess.DEVNULL,
                    )
                    run_git(
                        [
                            "-C",
                            str(crs_path),
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        stdout=subprocess.DEVNULL,
                    )
                logging.info(f"CRS '{crs_name}' updated to ref '{crs_ref or 'HEAD'}'")
                return crs_path

            # Clone the CRS repository from URL
            logging.info(f"Cloning CRS '{crs_name}' from {crs_url}")
            run_git(["clone", crs_url, str(crs_path)], stdout=subprocess.DEVNULL)

            if crs_ref:
                run_git(
                    ["-C", str(crs_path), "checkout", crs_ref],
                    stdout=subprocess.DEVNULL,
                )
                run_git(
                    [
                        "-C",
                        str(crs_path),
                        "submodule",
                        "update",
                        "--init",
                        "--recursive",
                    ],
                    stdout=subprocess.DEVNULL,
                )

            logging.info(f"Successfully cloned CRS '{crs_name}' to {crs_path}")
            return crs_path

        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to clone/update CRS '{crs_name}': {e}")
            return None

    else:
        print(f"ERROR: No local_path or url specified in {crs_meta_path}")
        return None


def get_crs_for_worker(
    worker_name: str,
    resource_config: dict[str, Any],
    crs_paths: dict[str, str],
    crs_pkg_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Extract CRS configurations for a specific worker.

    Supports three configuration modes:
    1. Fine-grained: Each CRS explicitly specifies resources per worker
    2. Global: CRS specifies global resources applied to all workers
    3. Auto-division: No CRS resources specified, divide worker resources evenly

    Returns a list of CRS configurations with resource constraints applied.
    Each CRS dict includes a 'path' field from the crs_paths mapping.

    Args:
      worker_name: Name of the worker
      resource_config: Resource configuration dictionary
      crs_paths: Mapping of crs_name -> actual filesystem path
      crs_pkg_data: Mapping of crs_name -> config-crs.yaml data from registry

    Exits with error if:
    - CPU cores conflict (two CRS trying to use same core)
    - CPU cores out of worker range
    - Not enough cores to give each CRS at least one
    """
    crs_configs = resource_config.get("crs", {})
    workers_config = resource_config.get("workers", {})
    worker_resources = workers_config.get(worker_name, {})

    # Get worker's available resources
    worker_cpus_spec = worker_resources.get("cpuset", "0-3")
    worker_memory_spec = worker_resources.get("memory", "4G")
    worker_all_cpus = set(parse_cpu_range(worker_cpus_spec))
    worker_total_memory_mb = parse_memory_mb(worker_memory_spec)

    # Collect CRS instances for this worker and categorize by config type
    explicit_crs = []  # CRS with explicit resource config for this worker
    auto_divide_crs = []  # CRS without explicit config (needs auto-division)

    for crs_name, crs_config in crs_configs.items():
        # Check if this CRS should run on this worker
        crs_workers = crs_config.get("workers", [])
        if worker_name not in crs_workers:
            continue

        # Check for explicit resource configuration
        resources = crs_config.get("resources", {})

        # Three cases for resources config:
        # 1. resources.{worker_name} exists - per-worker config
        # 2. resources.cpus exists (no worker key) - global config for all workers
        # 3. resources is empty or only has other workers - auto-divide

        if isinstance(resources, dict) and worker_name in resources:
            # Case 1: Per-worker explicit config
            explicit_crs.append((crs_name, resources[worker_name]))
        elif (
            isinstance(resources, dict)
            and "cpuset" in resources
            and worker_name not in resources
        ):
            # Case 2: Global config (applies to all workers)
            explicit_crs.append((crs_name, resources))
        else:
            # Case 3: No explicit config for this worker - needs auto-division
            auto_divide_crs.append(crs_name)

    if not explicit_crs and not auto_divide_crs:
        return []

    # Track used CPUs and memory for conflict detection
    used_cpus = set()
    used_memory_mb = 0
    result = []

    # Process explicit configurations first
    for crs_name, crs_resources in explicit_crs:
        cpus_spec = crs_resources.get("cpuset", "0-3")
        memory_spec = crs_resources.get("memory", "4G")

        crs_cpus_list = parse_cpu_range(cpus_spec)
        crs_cpus_set = set(crs_cpus_list)
        crs_memory_mb = parse_memory_mb(memory_spec)

        # Validation: Check CPUs are within worker range
        if not crs_cpus_set.issubset(worker_all_cpus):
            out_of_range = crs_cpus_set - worker_all_cpus
            print(
                f"ERROR: CRS '{crs_name}' on worker '{worker_name}' uses CPUs {out_of_range} "
                f"which are outside worker's CPU range {worker_cpus_spec}"
            )
            sys.exit(1)

        # Validation: Check for CPU conflicts
        conflicts = used_cpus & crs_cpus_set
        if conflicts:
            print(
                f"ERROR: CRS '{crs_name}' on worker '{worker_name}' conflicts with another CRS. "
                f"CPUs {conflicts} are already allocated."
            )
            sys.exit(1)

        used_cpus.update(crs_cpus_set)
        used_memory_mb += crs_memory_mb

        # Get dind and host_docker_builder flags from CRS config-crs.yaml dependencies
        crs_pkg = crs_pkg_data.get(crs_name, {})
        crs_dependencies = crs_pkg.get("dependencies", [])
        crs_dind = "dind" in crs_dependencies if crs_dependencies else False
        crs_host_docker_builder = (
            "host_docker_builder" in crs_dependencies if crs_dependencies else False
        )

        # Get volumes from CRS config-crs.yaml
        crs_volumes = crs_pkg.get("volumes", [])

        # Get run_env and build_env from CRS config-crs.yaml
        crs_run_env = crs_pkg.get("run_env", {})
        crs_build_env = crs_pkg.get("build_env", {})

        # Get run.docker_compose path if specified
        crs_run_docker_compose = crs_pkg.get("run_docker_compose")

        result.append(
            {
                "name": crs_name,
                "path": crs_paths[crs_name],
                "cpuset": format_cpu_list(crs_cpus_list),
                "memory_limit": format_memory(crs_memory_mb),
                "suffix": "runner",
                "dind": crs_dind,
                "host_docker_builder": crs_host_docker_builder,
                "volumes": crs_volumes,
                "run_env": crs_run_env,
                "build_env": crs_build_env,
                "run_docker_compose": crs_run_docker_compose,
            }
        )

    # Process auto-divide CRS instances
    if auto_divide_crs:
        # Calculate remaining resources
        remaining_cpus = sorted(worker_all_cpus - used_cpus)
        remaining_memory_mb = worker_total_memory_mb - used_memory_mb

        num_auto = len(auto_divide_crs)

        # Validation: Check we have enough CPUs
        if len(remaining_cpus) < num_auto:
            print(
                f"ERROR: Not enough CPUs on worker '{worker_name}' for auto-division. "
                f"Need at least {num_auto} cores for {num_auto} CRS instances, "
                f"but only {len(remaining_cpus)} cores remain after explicit allocations."
            )
            sys.exit(1)

        # Validation: Check we have enough memory
        if remaining_memory_mb < num_auto * 512:  # Minimum 512MB per CRS
            print(
                f"ERROR: Not enough memory on worker '{worker_name}' for auto-division. "
                f"Only {remaining_memory_mb}MB remain for {num_auto} CRS instances "
                f"(minimum 512MB per CRS required)."
            )
            sys.exit(1)

        # Divide remaining resources
        cpus_per_crs = len(remaining_cpus) // num_auto
        memory_per_crs = remaining_memory_mb // num_auto

        for idx, crs_name in enumerate(auto_divide_crs):
            # Allocate CPU cores
            start_idx = idx * cpus_per_crs
            end_idx = start_idx + cpus_per_crs
            if idx == num_auto - 1:
                # Last CRS gets remaining cores
                end_idx = len(remaining_cpus)

            crs_cpus_list = remaining_cpus[start_idx:end_idx]

            # Allocate memory
            if idx == num_auto - 1:
                # Last CRS gets remaining memory
                crs_memory = remaining_memory_mb - (memory_per_crs * (num_auto - 1))
            else:
                crs_memory = memory_per_crs

            # Get dind and host_docker_builder flags from CRS config-crs.yaml dependencies
            crs_pkg = crs_pkg_data.get(crs_name, {})
            crs_dependencies = crs_pkg.get("dependencies", [])
            crs_dind = "dind" in crs_dependencies if crs_dependencies else False
            crs_host_docker_builder = (
                "host_docker_builder" in crs_dependencies if crs_dependencies else False
            )

            # Get volumes from CRS config-crs.yaml
            crs_volumes = crs_pkg.get("volumes", [])

            # Get run_env and build_env from CRS config-crs.yaml
            crs_run_env = crs_pkg.get("run_env", {})
            crs_build_env = crs_pkg.get("build_env", {})

            # Get run.docker_compose path if specified
            crs_run_docker_compose = crs_pkg.get("run_docker_compose")

            result.append(
                {
                    "name": crs_name,
                    "path": crs_paths[crs_name],
                    "cpuset": format_cpu_list(crs_cpus_list),
                    "memory_limit": format_memory(crs_memory),
                    "suffix": "runner",
                    "dind": crs_dind,
                    "host_docker_builder": crs_host_docker_builder,
                    "volumes": crs_volumes,
                    "run_env": crs_run_env,
                    "build_env": crs_build_env,
                    "run_docker_compose": crs_run_docker_compose,
                }
            )

    return result


def get_project_language(oss_fuzz_path: Path, project: str) -> str:
    """Get the language for a project from project.yaml."""
    project_yaml_path = oss_fuzz_path / "projects" / project / "project.yaml"

    if not project_yaml_path.exists():
        logging.info(f"No project.yaml found for {project}, assuming c++")
        return "c++"

    try:
        with open(project_yaml_path) as f:
            project_config = yaml.safe_load(f)

        language = project_config.get("language", "c++")
        return language
    except (yaml.YAMLError, AttributeError, TypeError):
        logging.info(
            f"Language not specified in project.yaml for {project}, assuming c++"
        )
        return "c++"
