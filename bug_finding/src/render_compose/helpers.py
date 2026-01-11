"""Helper functions for compose rendering."""

import os
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values


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


def get_crs_env_vars(config_dir: Path) -> list[str]:
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


def get_dot_env_vars(config_dir: Path) -> dict[str, str]:
    """Load all environment variables from .env file.

    Args:
        config_dir: Directory containing the .env file

    Returns:
        Dictionary of environment variable name -> value
    """
    env_file = config_dir / ".env"
    if not env_file.exists():
        return {}
    # Filter out None values from dotenv_values
    return {k: v for k, v in dotenv_values(str(env_file)).items() if v is not None}


def merge_env_vars(
    registry_env: dict[str, str], dot_env: dict[str, str], crs_name: str, phase: str
) -> dict[str, str]:
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
    import logging
    logger = logging.getLogger(__name__)

    merged = dict(registry_env)

    for key, value in dot_env.items():
        if key in merged and merged[key] != value:
            logger.warning(
                f"[{crs_name}] {phase}_env: '{key}' overridden by .env: "
                f"'{merged[key]}' -> '{value}'"
            )
        merged[key] = value

    return merged


def expand_volume_vars(volumes: list[str], env_vars: dict[str, str]) -> list[str]:
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
        def replace_var(match: re.Match[str]) -> str:
            var_name = match.group(1) or match.group(2)
            if var_name is None:
                return match.group(0)
            return env_vars.get(var_name, match.group(0))

        expanded_vol = re.sub(r"\$\{(\w+)\}|\$(\w+)", replace_var, vol)
        expanded.append(expanded_vol)
    return expanded
