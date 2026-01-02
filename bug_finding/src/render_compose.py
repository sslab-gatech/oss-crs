"""
Render Docker Compose files per worker from configuration directory.

This module provides programmatic functions to generate Docker Compose files
for CRS (Cyber Reasoning System) builds and runs.

For CLI usage, use: oss-crs build/run
See infra/crs/__main__.py for the CLI entry point.
"""

import hashlib
import logging
import os.path
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import dotenv_values
from jinja2 import Template

from .utils import run_git

TEMPLATE_DIR = files(__package__).parent / "templates"

logger = logging.getLogger(__name__)
KEY_PROVISIONER_DIR = files(__package__).parent / "key_provisioner"

# Configure logging (INFO level won't show by default)
logging.basicConfig(level=logging.WARNING, format="%(message)s")


def check_image_exists(image_name: str, check_any_tag: bool = False) -> bool:
    """Check if Docker image exists locally.

    Args:
        image_name: Full Docker image name (e.g., 'json-c_crs-multilang_builder:abc123')
        check_any_tag: If True and image_name has no tag, check if any image with that name exists

    Returns:
        True if image exists locally, False otherwise
    """
    # First try exact match
    result = subprocess.run(
        ["docker", "image", "inspect", image_name], capture_output=True
    )
    if result.returncode == 0:
        return True

    # If check_any_tag is True and no tag specified, check for any tag
    if check_any_tag and ":" not in image_name:
        result = subprocess.run(
            ["docker", "images", "-q", image_name], capture_output=True, text=True
        )
        return bool(result.stdout.strip())

    return False


WORKDIR_REGEX = re.compile(r"\s*WORKDIR\s*([^\s]+)")


def workdir_from_lines(lines: list, default: str = "/src") -> str:
    """Gets the WORKDIR from the given Dockerfile lines.

    Ported from oss-fuzz/infra/helper.py.

    Args:
        lines: Lines from a Dockerfile
        default: Default workdir if none found

    Returns:
        The workdir path (e.g., "/src/json-c")
    """
    for line in reversed(lines):  # reversed to get last WORKDIR
        match = re.match(WORKDIR_REGEX, line)
        if match:
            workdir = match.group(1)
            workdir = workdir.replace("$SRC", "/src")

            if not os.path.isabs(workdir):
                workdir = os.path.join("/src", workdir)

            return os.path.normpath(workdir)

    return default


def get_dockerfile_workdir(project_path: Path) -> str:
    """Extract the WORKDIR from a project's Dockerfile.

    Args:
        project_path: Path to the OSS-Fuzz project directory containing Dockerfile

    Returns:
        The workdir path (e.g., "/src/json-c"), defaults to "/src"
    """
    dockerfile_path = project_path / "Dockerfile"
    if not dockerfile_path.exists():
        return "/src"

    try:
        with open(dockerfile_path, "r") as f:
            return workdir_from_lines(f.readlines())
    except (OSError, IOError):
        return "/src"


def get_crs_env_vars(config_dir: Path) -> List[str]:
    """Extract CRS_* prefixed variable names from .env file.

    Args:
        config_dir: Directory containing the .env file

    Returns:
        Sorted list of environment variable names starting with 'CRS_'
    """
    env_file = config_dir / ".env"
    if not env_file.exists():
        return []
    env_vars = dotenv_values(str(env_file))
    return sorted([k for k in env_vars.keys() if k.startswith("CRS_")])


def get_dot_env_vars(config_dir: Path) -> Dict[str, str]:
    """Load all environment variables from .env file.

    Args:
        config_dir: Directory containing the .env file

    Returns:
        Dictionary of environment variable name -> value
    """
    env_file = config_dir / ".env"
    if not env_file.exists():
        return {}
    return dict(dotenv_values(str(env_file)))


def merge_env_vars(
    registry_env: Dict[str, str], dot_env: Dict[str, str], crs_name: str, phase: str
) -> Dict[str, str]:
    """Merge registry env vars with .env vars.

    .env wins on conflict, with warning logged.

    Args:
        registry_env: Environment variables from config-crs.yaml (run_env or build_env)
        dot_env: Environment variables from .env file
        crs_name: Name of the CRS (for logging)
        phase: 'build' or 'run' (for logging)

    Returns:
        Merged dictionary with .env values taking precedence
    """
    merged = dict(registry_env)

    for key, value in dot_env.items():
        if key in merged and merged[key] != value:
            logger.warning(
                f"[{crs_name}] {phase}_env: '{key}' overridden by .env: "
                f"'{merged[key]}' -> '{value}'"
            )
        merged[key] = value

    return merged


def expand_volume_vars(volumes: List[str], env_vars: Dict[str, str]) -> List[str]:
    """Expand ${VAR} in volume strings using env vars.

    Docker Compose uses .env for variable substitution, but we've moved
    some vars to config-crs.yaml. This function expands those variables
    before rendering the template.

    Args:
        volumes: List of volume mount strings (e.g., "${HOST_CACHE_DIR}:/cache:ro")
        env_vars: Environment variables to use for substitution

    Returns:
        List of volume strings with variables expanded
    """
    expanded = []
    for vol in volumes:
        # Match ${VAR} or $VAR patterns
        def replace_var(match: re.Match) -> str:
            var_name = match.group(1) or match.group(2)
            return env_vars.get(var_name, match.group(0))

        expanded_vol = re.sub(r"\$\{(\w+)\}|\$(\w+)", replace_var, vol)
        expanded.append(expanded_vol)
    return expanded


@dataclass
class ComposeEnvironment:
    """Environment data for compose rendering."""

    config_dir: Path
    build_dir: Path
    template_path: Path
    litellm_template_path: Path
    oss_fuzz_path: Path
    config_hash: str
    crs_build_dir: Path
    output_dir: Path
    oss_crs_registry_path: Path
    config: Dict[str, Any]
    resource_config: Dict[str, Any]
    crs_paths: Dict[str, str]
    crs_pkg_data: Dict[str, Dict[str, Any]]


def load_config(config_dir: Path) -> Dict[str, Any]:
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


def parse_cpu_range(cpu_spec: str) -> List[int]:
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


def format_cpu_list(cpu_list: List[int]) -> str:
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
) -> Optional[Path]:
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
        if crs_path.exists():
            logging.info(f"CRS '{crs_name}' already exists at {crs_path}")
            return crs_path

        # Clone the CRS repository from URL
        logging.info(f"Cloning CRS '{crs_name}' from {crs_url}")
        try:
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
                        "--depth",
                        "1",
                    ],
                    stdout=subprocess.DEVNULL,
                )

            logging.info(f"Successfully cloned CRS '{crs_name}' to {crs_path}")
            return crs_path

        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to clone CRS '{crs_name}': {e}")
            return None

    else:
        print(f"ERROR: No local_path or url specified in {crs_meta_path}")
        return None


def get_crs_for_worker(
    worker_name: str,
    resource_config: Dict[str, Any],
    crs_paths: Dict[str, str],
    crs_pkg_data: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
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


def _setup_compose_environment(
    config_dir: str,
    build_dir: str,
    oss_fuzz_path: str,
    registry_dir: str,
    env_file: str = None,
    mode: str = "build",
) -> ComposeEnvironment:
    """
    Common setup for render_build_compose and render_run_compose.

    Args:
        config_dir: Directory containing CRS configuration files
        build_dir: Path to build directory
        oss_fuzz_path: Path to oss-fuzz directory
        registry_dir: Path to local oss-crs-registry directory (or None)
        env_file: Optional path to environment file
        mode: Either 'build' or 'run'

    Returns:
        ComposeEnvironment dataclass containing all environment setup data
    """
    # Convert strings to Path objects (already resolved from CLI)
    config_dir = Path(config_dir)
    build_dir = Path(build_dir)
    oss_fuzz_path = Path(oss_fuzz_path)
    template_path = TEMPLATE_DIR / "compose.yaml.j2"
    litellm_template_path = TEMPLATE_DIR / "compose-litellm.yaml.j2"
    registry_dir_path = Path(registry_dir) if registry_dir else None
    env_file_path = Path(env_file) if env_file else None

    # Compute config_hash from config-resource.yaml
    config_resource_path = config_dir / "config-resource.yaml"
    if not config_resource_path.exists():
        raise FileNotFoundError(
            f"config-resource.yaml not found in config-dir: {config_dir}"
        )

    with open(config_resource_path, "rb") as f:
        config_content = f.read()
    config_hash = hashlib.sha256(config_content).hexdigest()[:16]

    # Create crs_build_dir (used for compose files and other outputs)
    # Note: For run mode, build validation is done via Docker image existence check
    # in render_run_compose() after we have the project/CRS information
    crs_build_dir = build_dir / "crs" / config_hash
    crs_build_dir.mkdir(parents=True, exist_ok=True)
    if mode == "build":
        logging.info(f"Using CRS build directory: {crs_build_dir}")

    # Output directory is the crs_build_dir
    output_dir = crs_build_dir

    # Verify crs_registry exists
    if not registry_dir_path.exists():
        raise FileNotFoundError(
            f"Registry directory does not exist: {registry_dir_path}"
        )
    oss_crs_registry_path = registry_dir_path
    logging.info(f"Using crs_registry at: {oss_crs_registry_path}")

    # Copy env file to output directory as .env if provided
    if env_file_path:
        if not env_file_path.exists():
            raise FileNotFoundError(f"Environment file not found: {env_file_path}")
        dest_env = output_dir / ".env"
        shutil.copy2(env_file_path, dest_env)

    # Load configurations
    config = load_config(config_dir)
    resource_config = config["resource"]

    # Clone all required CRS repositories and build path mapping
    crs_configs = resource_config.get("crs", {})
    crs_paths = {}
    crs_pkg_data = {}
    for crs_name in crs_configs.keys():
        crs_path = clone_crs_if_needed(crs_name, crs_build_dir, oss_crs_registry_path)
        if crs_path is None:
            raise RuntimeError(f"Failed to prepare CRS '{crs_name}'")
        crs_paths[crs_name] = str(crs_path)

        # Load config-crs.yaml for this CRS from registry
        crs_config_yaml_path = oss_crs_registry_path / crs_name / "config-crs.yaml"
        if crs_config_yaml_path.exists():
            try:
                with open(crs_config_yaml_path) as f:
                    crs_config_data = yaml.safe_load(f) or {}

                # Initialize CRS data
                crs_data = {}

                # Extract run_env and build_env from root level
                crs_data["run_env"] = crs_config_data.get("run_env", {})
                crs_data["build_env"] = crs_config_data.get("build_env", {})

                # Extract CRS-specific config (dependencies, volumes, etc.)
                if crs_name in crs_config_data:
                    crs_specific = crs_config_data[crs_name]
                    if isinstance(crs_specific, dict):
                        crs_data["dependencies"] = crs_specific.get("dependencies", [])
                        crs_data["volumes"] = crs_specific.get("volumes", [])
                    else:
                        crs_data["dependencies"] = []
                        crs_data["volumes"] = []
                else:
                    crs_data["dependencies"] = []
                    crs_data["volumes"] = []

                crs_pkg_data[crs_name] = crs_data

            except yaml.YAMLError as e:
                logging.warning(
                    f"Failed to parse config-crs.yaml for CRS '{crs_name}': {e}"
                )
                crs_pkg_data[crs_name] = {
                    "run_env": {},
                    "build_env": {},
                    "dependencies": [],
                    "volumes": [],
                }
        else:
            logging.warning(
                f"config-crs.yaml not found for CRS '{crs_name}' at {crs_config_yaml_path}"
            )
            crs_pkg_data[crs_name] = {
                "run_env": {},
                "build_env": {},
                "dependencies": [],
                "volumes": [],
            }

    # Check for .env file in config-dir if no explicit env-file was provided
    if not env_file_path:
        config_env_file = config_dir / ".env"
        if config_env_file.exists():
            dest_env = output_dir / ".env"
            shutil.copy2(config_env_file, dest_env)

    return ComposeEnvironment(
        config_dir=config_dir,
        build_dir=build_dir,
        template_path=template_path,
        litellm_template_path=litellm_template_path,
        oss_fuzz_path=oss_fuzz_path,
        config_hash=config_hash,
        crs_build_dir=crs_build_dir,
        output_dir=output_dir,
        oss_crs_registry_path=oss_crs_registry_path,
        config=config,
        resource_config=resource_config,
        crs_paths=crs_paths,
        crs_pkg_data=crs_pkg_data,
    )


def render_litellm_compose(
    template_path: Path,
    config_dir: Path,
    config_hash: str,
    crs_list: List[Dict[str, Any]],
) -> str:
    """Render the compose-litellm.yaml template."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    template_content = template_path.read_text()
    template = Template(template_content)

    rendered = template.render(
        config_hash=config_hash, config_dir=str(config_dir), crs_list=crs_list
    )

    return rendered


def render_compose_for_worker(
    worker_name: str,
    crs_list: List[Dict[str, Any]],
    template_path: Path,
    oss_fuzz_path: Path,
    build_dir: Path,
    project: str,
    config_dir: Path,
    engine: str,
    sanitizer: str,
    architecture: str,
    mode: str,
    config_hash: str,
    fuzzer_command: List[str] = None,
    source_path: str = None,
    harness_source: str = None,
    diff_path: str = None,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
    shared_seed_dir: str = None,
    harness_name: str = None,
) -> str:
    """Render the compose template for a specific worker."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    template_content = template_path.read_text()
    template = Template(template_content)

    # Construct config paths (already resolved from CLI)
    config_resource_path = config_dir / "config-resource.yaml"

    # Get project language
    project_language = get_project_language(oss_fuzz_path, project)

    # Compute source_tag from source_path if provided
    source_tag = None
    if source_path:
        source_tag = hashlib.sha256(
            source_path.encode() + project.encode()
        ).hexdigest()[:12]

    # Load .env vars and merge with each CRS's run_env/build_env
    dot_env = get_dot_env_vars(config_dir)

    # Create copies to avoid mutating the original list
    crs_list = [dict(crs) for crs in crs_list]

    # Merge env vars for each CRS and expand volume variables
    for crs in crs_list:
        crs_name = crs["name"]
        crs["build_env"] = merge_env_vars(
            crs.get("build_env", {}), dot_env, crs_name, "build"
        )
        crs["run_env"] = merge_env_vars(
            crs.get("run_env", {}), dot_env, crs_name, "run"
        )
        # Expand ${VAR} in volumes using merged run_env (for vars like HOST_CACHE_DIR)
        if crs.get("volumes"):
            crs["volumes"] = expand_volume_vars(crs["volumes"], crs["run_env"])

    # Get workdir from project Dockerfile
    project_path = oss_fuzz_path / "projects" / project
    source_workdir = get_dockerfile_workdir(project_path)

    rendered = template.render(
        crs_list=crs_list,
        worker_name=worker_name,
        oss_fuzz_path=str(oss_fuzz_path),
        build_dir=str(build_dir),
        key_provisioner_path=str(KEY_PROVISIONER_DIR),
        project=project,
        project_language=project_language,
        engine=engine,
        sanitizer=sanitizer,
        architecture=architecture,
        fuzzer_command=fuzzer_command or [],
        config_resource_path=str(config_resource_path),
        config_dir=str(config_dir),
        mode=mode,
        config_hash=config_hash,
        source_path=source_path,
        source_tag=source_tag,
        source_workdir=source_workdir,
        harness_source=harness_source,
        harness_name=harness_name,
        diff_path=diff_path,
        parent_image_prefix=project_image_prefix,
        external_litellm=external_litellm,
        shared_seed_dir=shared_seed_dir,
    )

    return rendered


def render_build_compose(
    config_dir: str,
    build_dir: str,
    oss_fuzz_dir: str,
    project: str,
    engine: str,
    sanitizer: str,
    architecture: str,
    registry_dir: str,
    source_path: str = None,
    env_file: str = None,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
) -> Tuple[List[str], str, str, List[Dict]]:
    """
    Programmatic interface for build mode.

    Returns:
      Tuple of (build_profile_names, config_hash, crs_build_dir, crs_list)
    """
    # Common setup
    env = _setup_compose_environment(
        config_dir, build_dir, oss_fuzz_dir, registry_dir, env_file, mode="build"
    )

    config_dir = env.config_dir
    build_dir = env.build_dir
    template_path = env.template_path
    litellm_template_path = env.litellm_template_path
    oss_fuzz_path = env.oss_fuzz_path
    config_hash = env.config_hash
    crs_build_dir = env.crs_build_dir
    output_dir = env.output_dir
    resource_config = env.resource_config
    crs_paths = env.crs_paths
    crs_pkg_data = env.crs_pkg_data

    # Validate workers exist
    workers = resource_config.get("workers", {})
    if not workers:
        raise ValueError("No workers defined in config-resource.yaml")

    # Collect all CRS instances across all workers
    all_crs_list = []
    all_build_profiles = []

    for worker_name in workers.keys():
        crs_list = get_crs_for_worker(
            worker_name, resource_config, crs_paths, crs_pkg_data
        )
        if crs_list:
            all_crs_list.extend(crs_list)
            all_build_profiles.extend([f"{crs['name']}_builder" for crs in crs_list])

    # Render compose-litellm.yaml (unless using external LiteLLM)
    if not external_litellm:
        litellm_rendered = render_litellm_compose(
            template_path=litellm_template_path,
            config_dir=config_dir,
            config_hash=config_hash,
            crs_list=all_crs_list,
        )
        litellm_output_file = output_dir / "compose-litellm.yaml"
        litellm_output_file.write_text(litellm_rendered)

    # Render compose-build.yaml
    rendered = render_compose_for_worker(
        worker_name=None,
        crs_list=all_crs_list,
        template_path=template_path,
        oss_fuzz_path=oss_fuzz_path,
        build_dir=build_dir,
        project=project,
        config_dir=config_dir,
        engine=engine,
        sanitizer=sanitizer,
        architecture=architecture,
        mode="build",
        config_hash=config_hash,
        fuzzer_command=None,
        source_path=source_path,
        project_image_prefix=project_image_prefix,
        external_litellm=external_litellm,
    )

    output_file = output_dir / "compose-build.yaml"
    output_file.write_text(rendered)

    return all_build_profiles, config_hash, str(crs_build_dir), all_crs_list


def render_run_compose(
    config_dir: str,
    build_dir: str,
    oss_fuzz_dir: str,
    project: str,
    engine: str,
    sanitizer: str,
    architecture: str,
    registry_dir: str,
    worker: str,
    fuzzer_command: List[str],
    source_path: str = None,
    env_file: str = None,
    harness_source: str = None,
    diff_path: str = None,
    external_litellm: bool = False,
    shared_seed_dir: str = None,
) -> Tuple[str, str]:
    """
    Programmatic interface for run mode.

    Args:
        shared_seed_dir: Optional base directory for shared seeds between CRS instances

    Returns:
      Tuple of (config_hash, crs_build_dir)
    """
    # Common setup
    env = _setup_compose_environment(
        config_dir, build_dir, oss_fuzz_dir, registry_dir, env_file, mode="run"
    )

    config_dir = env.config_dir
    build_dir = env.build_dir
    template_path = env.template_path
    litellm_template_path = env.litellm_template_path
    oss_fuzz_path = env.oss_fuzz_path
    config_hash = env.config_hash
    crs_build_dir = env.crs_build_dir
    output_dir = env.output_dir
    resource_config = env.resource_config
    crs_paths = env.crs_paths
    crs_pkg_data = env.crs_pkg_data

    # Validate worker exists
    workers = resource_config.get("workers", {})
    if worker not in workers:
        raise ValueError(f"Worker '{worker}' not found in config-resource.yaml")

    # Get CRS list for this worker
    crs_list = get_crs_for_worker(worker, resource_config, crs_paths, crs_pkg_data)

    if not crs_list:
        raise ValueError(f"No CRS instances configured for worker '{worker}'")

    # Validate that CRS builder images exist (validates build was run)
    # Compute source_tag if source_path is provided
    source_tag = None
    if source_path:
        source_tag = hashlib.sha256(
            source_path.encode() + project.encode()
        ).hexdigest()[:12]

    for crs in crs_list:
        crs_name = crs["name"]
        builder_image = f"{project}_{crs_name}_builder"
        if source_tag:
            builder_image += f":{source_tag}"

        # Use check_any_tag=True when no source_tag, to find images built with source_path
        if not check_image_exists(builder_image, check_any_tag=(source_tag is None)):
            raise FileNotFoundError(
                f"CRS builder image not found: {builder_image}. "
                f"Please run 'oss-crs build' first to build the CRS."
            )

    # Render compose-litellm.yaml (unless using external LiteLLM)
    if not external_litellm:
        litellm_rendered = render_litellm_compose(
            template_path=litellm_template_path,
            config_dir=config_dir,
            config_hash=config_hash,
            crs_list=crs_list,
        )
        litellm_output_file = output_dir / "compose-litellm.yaml"
        litellm_output_file.write_text(litellm_rendered)

    # Extract harness_name from fuzzer_command (first element is the fuzzer/harness name)
    harness_name = fuzzer_command[0] if fuzzer_command else None

    # Render compose file
    rendered = render_compose_for_worker(
        worker_name=worker,
        crs_list=crs_list,
        template_path=template_path,
        oss_fuzz_path=oss_fuzz_path,
        build_dir=build_dir,
        project=project,
        config_dir=config_dir,
        engine=engine,
        sanitizer=sanitizer,
        architecture=architecture,
        mode="run",
        config_hash=config_hash,
        fuzzer_command=fuzzer_command,
        source_path=source_path,
        harness_source=harness_source,
        diff_path=diff_path,
        external_litellm=external_litellm,
        shared_seed_dir=shared_seed_dir,
        harness_name=harness_name,
    )

    output_file = output_dir / f"compose-{worker}.yaml"
    output_file.write_text(rendered)

    return config_hash, str(crs_build_dir)


# Note: This module is now used as a library.
# For CLI usage, use: oss-crs build/run
# See infra/crs/__main__.py for the CLI entry point
