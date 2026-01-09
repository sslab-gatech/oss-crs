"""Main compose rendering functions."""

import hashlib
import logging
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template

from bug_finding.src.render_compose.config import (
    ComposeEnvironment,
    clone_crs_if_needed,
    get_crs_for_worker,
    get_project_language,
    load_config,
)
from bug_finding.src.render_compose.helpers import (
    check_image_exists,
    expand_volume_vars,
    get_dockerfile_workdir,
    get_dot_env_vars,
    merge_env_vars,
)

# Use parent package for template directory
TEMPLATE_DIR = files("bug_finding.src").joinpath("..").joinpath("templates")
KEY_PROVISIONER_DIR = files("bug_finding.src").joinpath("..").joinpath("key_provisioner")
SEED_WATCHER_DIR = files("bug_finding.src").joinpath("..").joinpath("seed_watcher")

logger = logging.getLogger(__name__)


def _setup_compose_environment(
    config_dir: Path,
    build_dir: Path,
    oss_fuzz_path: Path,
    registry_dir: Path,
    env_file: Path | None = None,
    mode: str = "build",
) -> ComposeEnvironment:
    """
    Common setup for render_build_compose and render_run_compose.

    Args:
        config_dir: Directory containing CRS configuration files
        build_dir: Path to build directory
        oss_fuzz_path: Path to oss-fuzz directory
        registry_dir: Path to local oss-crs-registry directory
        env_file: Optional path to environment file
        mode: Either 'build' or 'run'

    Returns:
        ComposeEnvironment dataclass containing all environment setup data
    """
    template_path = Path(str(TEMPLATE_DIR)) / "compose.yaml.j2"
    litellm_template_path = Path(str(TEMPLATE_DIR)) / "compose-litellm.yaml.j2"

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
    if not registry_dir.exists():
        raise FileNotFoundError(
            f"Registry directory does not exist: {registry_dir}"
        )
    oss_crs_registry_path = registry_dir
    logging.info(f"Using crs_registry at: {oss_crs_registry_path}")

    # Copy env file to output directory as .env if provided
    if env_file:
        if not env_file.exists():
            raise FileNotFoundError(f"Environment file not found: {env_file}")
        dest_env = output_dir / ".env"
        shutil.copy2(env_file, dest_env)

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
    if not env_file:
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
    crs_list: list[dict[str, Any]],
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
    worker_name: str | None,
    crs_list: list[dict[str, Any]],
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
    fuzzer_command: list[str] | None = None,
    source_path: Path | None = None,
    harness_source: str | None = None,
    diff_path: Path | None = None,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
    ensemble_dir: Path | None = None,
    harness_name: str | None = None,
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
            str(source_path).encode() + project.encode()
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
        seed_watcher_path=str(SEED_WATCHER_DIR),
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
        source_path=str(source_path) if source_path else None,
        source_tag=source_tag,
        source_workdir=source_workdir,
        harness_source=harness_source,
        harness_name=harness_name,
        diff_path=str(diff_path) if diff_path else None,
        parent_image_prefix=project_image_prefix,
        external_litellm=external_litellm,
        ensemble_dir=str(ensemble_dir) if ensemble_dir else None,
    )

    return rendered


def render_build_compose(
    config_dir: Path,
    build_dir: Path,
    oss_fuzz_dir: Path,
    project: str,
    engine: str,
    sanitizer: str,
    architecture: str,
    registry_dir: Path,
    source_path: Path | None = None,
    env_file: Path | None = None,
    project_image_prefix: str = "gcr.io/oss-fuzz",
    external_litellm: bool = False,
) -> tuple[list[str], str, Path, list[dict[str, Any]]]:
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

    return all_build_profiles, config_hash, crs_build_dir, all_crs_list


def render_run_compose(
    config_dir: Path,
    build_dir: Path,
    oss_fuzz_dir: Path,
    project: str,
    engine: str,
    sanitizer: str,
    architecture: str,
    registry_dir: Path,
    worker: str,
    fuzzer_command: list[str],
    source_path: Path | None = None,
    env_file: Path | None = None,
    harness_source: str | None = None,
    diff_path: Path | None = None,
    external_litellm: bool = False,
    ensemble_dir: Path | None = None,
) -> tuple[str, Path]:
    """
    Programmatic interface for run mode.

    Args:
        ensemble_dir: Optional base directory for shared seeds between CRS instances

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
            str(source_path).encode() + project.encode()
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
        ensemble_dir=ensemble_dir,
        harness_name=harness_name,
    )

    output_file = output_dir / f"compose-{worker}.yaml"
    output_file.write_text(rendered)

    return config_hash, crs_build_dir
