#!/usr/bin/env python3
"""
Render Docker Compose files per worker from configuration directory.

This script reads configuration files (config-resource.yaml, config-worker.yaml,
config-crs.yaml) from a directory and generates a compose-<worker>.yaml file
for each worker defined in the configuration.

Example usage:
  python render_compose.py --config-dir ./example_configs --output-dir ./output
"""

import argparse
import logging
import shutil
import subprocess
import sys
import yaml
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any, List

SCRIPT_DIR = Path(__file__).parent

# Configure logging (INFO level won't show by default)
logging.basicConfig(level=logging.WARNING, format='%(message)s')

def load_config(config_dir: Path) -> Dict[str, Any]:
  """Load all configuration files from the config directory."""
  config = {}

  # Load resource configuration
  resource_config_path = config_dir / "config-resource.yaml"
  if not resource_config_path.exists():
    raise FileNotFoundError(f"Required file not found: {resource_config_path}")
  with open(resource_config_path) as f:
    config['resource'] = yaml.safe_load(f)

  # Load worker configuration (optional)
  worker_config_path = config_dir / "config-worker.yaml"
  if worker_config_path.exists():
    with open(worker_config_path) as f:
      config['worker'] = yaml.safe_load(f)
  else:
    config['worker'] = {}

  # Load CRS configuration (optional)
  crs_config_path = config_dir / "config-crs.yaml"
  if crs_config_path.exists():
    with open(crs_config_path) as f:
      config['crs'] = yaml.safe_load(f)
  else:
    config['crs'] = {}

  return config


def parse_cpu_range(cpu_spec: str) -> List[int]:
  """
  Parse CPU specification in format 'm-n' and return list of CPU cores.

  Args:
    cpu_spec: CPU specification string (e.g., '0-7', '4-11')

  Returns:
    List of CPU core numbers
  """
  if '-' in cpu_spec:
    start, end = cpu_spec.split('-', 1)
    return list(range(int(start), int(end) + 1))
  else:
    # Single core specified
    return [int(cpu_spec)]


def format_cpu_list(cpu_list: List[int]) -> str:
  """
  Format a list of CPU cores as comma-separated string.

  Args:
    cpu_list: List of CPU core numbers

  Returns:
    Comma-separated string (e.g., '0,1,2,3')
  """
  return ','.join(map(str, cpu_list))


def parse_memory_mb(memory_spec: str) -> int:
  """
  Parse memory specification and return value in MB.

  Args:
    memory_spec: Memory specification (e.g., '4G', '512M', '1024')

  Returns:
    Memory in megabytes
  """
  memory_spec = memory_spec.strip().upper()
  if memory_spec.endswith('G'):
    return int(memory_spec[:-1]) * 1024
  elif memory_spec.endswith('M'):
    return int(memory_spec[:-1])
  else:
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
  else:
    return f"{memory_mb}M"


def clone_crs_if_needed(crs_name: str, registry_parent_dir: Path) -> bool:
  """
  Clone a CRS repository if it doesn't already exist.

  Args:
    crs_name: Name of the CRS to clone
    registry_parent_dir: Parent directory containing oss-crs-registry and CRS repos

  Returns:
    True if successful, False otherwise
  """
  crs_path = registry_parent_dir / crs_name

  if crs_path.exists():
    logging.info(f"CRS '{crs_name}' already exists at {crs_path}")
    return True

  # Parse oss-crs-registry to get CRS URL and ref
  oss_crs_registry_path = registry_parent_dir / "oss-crs-registry"
  crs_meta_path = oss_crs_registry_path / "crs" / crs_name / "pkg.yaml"

  if not crs_meta_path.exists():
    print(f"ERROR: CRS metadata not found for '{crs_name}' at {crs_meta_path}")
    return False

  # Parse pkg.yaml to get URL and ref
  import re
  url_regex = re.compile(r'\s*url\s*:\s*([^\s]+)')
  ref_regex = re.compile(r'\s*ref\s*:\s*([^\s]+)')

  crs_url = None
  crs_ref = None

  with open(crs_meta_path) as f:
    for line in f:
      url_match = url_regex.match(line)
      if url_match:
        crs_url = url_match.group(1)
      ref_match = ref_regex.match(line)
      if ref_match:
        crs_ref = ref_match.group(1)

  if not crs_url:
    print(f"ERROR: Could not parse CRS URL from {crs_meta_path}")
    return False

  # Clone the CRS repository
  logging.info(f"Cloning CRS '{crs_name}' from {crs_url}")
  try:
    subprocess.check_call(['git', 'clone', crs_url, str(crs_path)], stdout=subprocess.DEVNULL)

    if crs_ref:
      subprocess.check_call(['git', '-C', str(crs_path), 'checkout', crs_ref], stdout=subprocess.DEVNULL)
      subprocess.check_call(['git', '-C', str(crs_path), 'submodule', 'update',
                             '--init', '--recursive', '--depth', '1'], stdout=subprocess.DEVNULL)

    logging.info(f"Successfully cloned CRS '{crs_name}' to {crs_path}")
    return True

  except subprocess.CalledProcessError as e:
    print(f"ERROR: Failed to clone CRS '{crs_name}': {e}")
    return False


def get_crs_for_worker(worker_name: str, resource_config: Dict[str, Any],
                       registry_parent_dir: Path) -> List[Dict[str, Any]]:
  """
  Extract CRS configurations for a specific worker.

  Supports three configuration modes:
  1. Fine-grained: Each CRS explicitly specifies resources per worker
  2. Global: CRS specifies global resources applied to all workers
  3. Auto-division: No CRS resources specified, divide worker resources evenly

  Returns a list of CRS configurations with resource constraints applied.
  Each CRS dict includes a 'path' field constructed from registry_parent_dir/crs_name.

  Exits with error if:
  - CPU cores conflict (two CRS trying to use same core)
  - CPU cores out of worker range
  - Not enough cores to give each CRS at least one
  """
  crs_configs = resource_config.get('crs', {})
  workers_config = resource_config.get('workers', {})
  worker_resources = workers_config.get(worker_name, {})

  # Get worker's available resources
  worker_cpus_spec = worker_resources.get('cpuset', '0-3')
  worker_memory_spec = worker_resources.get('memory', '4G')
  worker_all_cpus = set(parse_cpu_range(worker_cpus_spec))
  worker_total_memory_mb = parse_memory_mb(worker_memory_spec)

  # Collect CRS instances for this worker and categorize by config type
  explicit_crs = []  # CRS with explicit resource config for this worker
  auto_divide_crs = []  # CRS without explicit config (needs auto-division)

  for crs_name, crs_config in crs_configs.items():
    # Check if this CRS should run on this worker
    crs_workers = crs_config.get('workers', [])
    if worker_name not in crs_workers:
      continue

    # Check for explicit resource configuration
    resources = crs_config.get('resources', {})

    # Three cases for resources config:
    # 1. resources.{worker_name} exists - per-worker config
    # 2. resources.cpus exists (no worker key) - global config for all workers
    # 3. resources is empty or only has other workers - auto-divide

    if isinstance(resources, dict) and worker_name in resources:
      # Case 1: Per-worker explicit config
      explicit_crs.append((crs_name, resources[worker_name]))
    elif isinstance(resources, dict) and 'cpuset' in resources and worker_name not in resources:
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
    cpus_spec = crs_resources.get('cpuset', '0-3')
    memory_spec = crs_resources.get('memory', '4G')

    crs_cpus_list = parse_cpu_range(cpus_spec)
    crs_cpus_set = set(crs_cpus_list)
    crs_memory_mb = parse_memory_mb(memory_spec)

    # Validation: Check CPUs are within worker range
    if not crs_cpus_set.issubset(worker_all_cpus):
      out_of_range = crs_cpus_set - worker_all_cpus
      print(f"ERROR: CRS '{crs_name}' on worker '{worker_name}' uses CPUs {out_of_range} "
            f"which are outside worker's CPU range {worker_cpus_spec}")
      sys.exit(1)

    # Validation: Check for CPU conflicts
    conflicts = used_cpus & crs_cpus_set
    if conflicts:
      print(f"ERROR: CRS '{crs_name}' on worker '{worker_name}' conflicts with another CRS. "
            f"CPUs {conflicts} are already allocated.")
      sys.exit(1)

    used_cpus.update(crs_cpus_set)
    used_memory_mb += crs_memory_mb

    result.append({
      'name': crs_name,
      'path': str(registry_parent_dir / crs_name),
      'cpuset': format_cpu_list(crs_cpus_list),
      'memory_limit': format_memory(crs_memory_mb),
      'suffix': 'runner'
    })

  # Process auto-divide CRS instances
  if auto_divide_crs:
    # Calculate remaining resources
    remaining_cpus = sorted(worker_all_cpus - used_cpus)
    remaining_memory_mb = worker_total_memory_mb - used_memory_mb

    num_auto = len(auto_divide_crs)

    # Validation: Check we have enough CPUs
    if len(remaining_cpus) < num_auto:
      print(f"ERROR: Not enough CPUs on worker '{worker_name}' for auto-division. "
            f"Need at least {num_auto} cores for {num_auto} CRS instances, "
            f"but only {len(remaining_cpus)} cores remain after explicit allocations.")
      sys.exit(1)

    # Validation: Check we have enough memory
    if remaining_memory_mb < num_auto * 512:  # Minimum 512MB per CRS
      print(f"ERROR: Not enough memory on worker '{worker_name}' for auto-division. "
            f"Only {remaining_memory_mb}MB remain for {num_auto} CRS instances "
            f"(minimum 512MB per CRS required).")
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

      result.append({
        'name': crs_name,
        'path': str(registry_parent_dir / crs_name),
        'cpuset': format_cpu_list(crs_cpus_list),
        'memory_limit': format_memory(crs_memory),
        'suffix': 'runner'
      })

  return result


def get_project_language(oss_fuzz_path: Path, project: str) -> str:
  """Get the language for a project from project.yaml."""
  project_yaml_path = oss_fuzz_path / "projects" / project / "project.yaml"

  if not project_yaml_path.exists():
    logging.info(f"No project.yaml found for {project}, assuming c++")
    return "c++"

  import re
  language_regex = re.compile(r'\s*language\s*:\s*([^\s]+)')

  with open(project_yaml_path) as f:
    for line in f:
      match = language_regex.match(line)
      if match:
        return match.group(1)

  logging.info(f"Language not specified in project.yaml for {project}, assuming c++")
  return "c++"


def render_litellm_compose(template_path: Path, config_dir: Path,
                           config_hash: str, crs_list: List[Dict[str, Any]]) -> str:
  """Render the compose-litellm.yaml template."""
  if not template_path.exists():
    raise FileNotFoundError(f"Template file not found: {template_path}")

  template_content = template_path.read_text()
  template = Template(template_content)

  config_dir_resolved = config_dir.resolve()

  rendered = template.render(
    config_hash=config_hash,
    config_dir=str(config_dir_resolved),
    crs_list=crs_list
  )

  return rendered


def render_compose_for_worker(worker_name: str, crs_list: List[Dict[str, Any]],
                              template_path: Path, oss_fuzz_path: Path,
                              project: str, config_dir: Path, engine: str,
                              sanitizer: str, architecture: str, mode: str,
                              config_hash: str, fuzzer_command: List[str] = None,
                              source_path: str = None) -> str:
  """Render the compose template for a specific worker."""
  if not template_path.exists():
    raise FileNotFoundError(f"Template file not found: {template_path}")

  template_content = template_path.read_text()
  template = Template(template_content)

  # Resolve config paths
  config_resource_path = (config_dir / "config-resource.yaml").resolve()
  config_dir_resolved = config_dir.resolve()

  # Get project language
  project_language = get_project_language(oss_fuzz_path, project)

  rendered = template.render(
    crs_list=crs_list,
    worker_name=worker_name,
    oss_fuzz_path=str(oss_fuzz_path),
    project=project,
    project_language=project_language,
    engine=engine,
    sanitizer=sanitizer,
    architecture=architecture,
    fuzzer_command=fuzzer_command or [],
    config_resource_path=str(config_resource_path),
    config_dir=str(config_dir_resolved),
    mode=mode,
    config_hash=config_hash,
    source_path=source_path
  )

  return rendered


def main():
  parser = argparse.ArgumentParser(
    description='Render Docker Compose files from configuration directory'
  )
  parser.add_argument(
    '--mode',
    type=str,
    required=True,
    choices=['build', 'run'],
    help='Mode: build (generate compose-build.yaml) or run (generate compose-<worker>.yaml)'
  )
  parser.add_argument(
    '--config-hash',
    type=str,
    required=True,
    help='Hash of config-resource.yaml to prefix network and volume names'
  )
  parser.add_argument(
    '--env-file',
    type=str,
    help='Optional path to environment file to include in generated compose files'
  )
  parser.add_argument(
    '--output-dir',
    type=str,
    default='.',
    help='Directory to write compose files (default: current directory)'
  )
  parser.add_argument(
    '--config-dir',
    type=str,
    required=True,
    help='Directory containing config-resource.yaml, config-worker.yaml, config-crs.yaml'
  )
  parser.add_argument(
    '--registry-parent-dir',
    type=str,
    required=True,
    help='Directory containing all CRS repositories (each CRS is in a subdirectory named after itself)'
  )
  parser.add_argument(
    '--project',
    type=str,
    required=True,
    help='OSS-Fuzz project name'
  )
  parser.add_argument(
    '--engine',
    type=str,
    default='libfuzzer',
    help='Fuzzing engine (default: libfuzzer)'
  )
  parser.add_argument(
    '--sanitizer',
    type=str,
    default='address',
    help='Sanitizer (default: address)'
  )
  parser.add_argument(
    '--architecture',
    type=str,
    default='x86_64',
    help='Architecture (default: x86_64)'
  )
  parser.add_argument(
    '--worker',
    type=str,
    help='Worker name (required in run mode, ignored in build mode)'
  )
  parser.add_argument(
    'fuzzer_command',
    nargs=argparse.REMAINDER,
    help='Fuzzer command and arguments (required in run mode, ignored in build mode)'
  )
  parser.add_argument(
    '--source-path',
    type=str,
    help='Optional path to local source to mount at /local-source-mount in CRS builders'
  )

  args = parser.parse_args()

  # Validate mode-specific requirements
  if args.mode == 'run':
    if not args.fuzzer_command:
      parser.error('fuzzer_command is required in run mode')
    if not args.worker:
      parser.error('--worker is required in run mode')

  # Convert to Path objects
  config_dir = Path(args.config_dir)
  output_dir = Path(args.output_dir)
  template_path = SCRIPT_DIR / "compose.yaml.j2"
  litellm_template_path = SCRIPT_DIR / "compose-litellm.yaml.j2"
  registry_parent_dir = Path(args.registry_parent_dir).resolve()

  # Compute OSS_FUZZ_PATH as parent.parent.parent of this script
  oss_fuzz_path = Path(__file__).parent.parent.parent.resolve()

  # Handle optional env file
  env_file = Path(args.env_file).resolve() if args.env_file else None

  # Ensure output directory exists
  output_dir.mkdir(parents=True, exist_ok=True)

  # Copy env file to output directory as .env if provided
  if env_file:
    if not env_file.exists():
      print(f"Error: Environment file not found: {env_file}")
      return 1
    dest_env = output_dir / ".env"
    shutil.copy2(env_file, dest_env)
    logging.info(f"Copied environment file to: {dest_env}")

  # Load configurations
  logging.info(f"Loading configuration from: {config_dir}")
  try:
    config = load_config(config_dir)
  except FileNotFoundError as e:
    print(f"Error: {e}")
    return 1

  resource_config = config['resource']
  workers = resource_config.get('workers', {})

  if not workers:
    print("Error: No workers defined in config-resource.yaml")
    return 1

  # Clone all required CRS repositories
  crs_configs = resource_config.get('crs', {})
  for crs_name in crs_configs.keys():
    logging.info(f"Checking CRS: {crs_name}")
    if not clone_crs_if_needed(crs_name, registry_parent_dir):
      print(f"Error: Failed to prepare CRS '{crs_name}'")
      return 1

  # Check for .env file in config-dir if no explicit env-file was provided
  if not env_file:
    config_env_file = config_dir / ".env"
    if config_env_file.exists():
      dest_env = output_dir / ".env"
      shutil.copy2(config_env_file, dest_env)
      logging.info(f"Copied .env file from config-dir to: {dest_env}")

  # Build mode: Generate single compose-build.yaml with all build profiles
  if args.mode == 'build':
    logging.info(f"Build mode: generating compose-build.yaml")

    # Collect all CRS instances across all workers
    all_crs_list = []
    all_build_profiles = []

    for worker_name in workers.keys():
      logging.info(f"Processing worker: {worker_name}")

      crs_list = get_crs_for_worker(worker_name, resource_config, registry_parent_dir)

      if not crs_list:
        logging.info(f"  No CRS instances for worker '{worker_name}'")
        continue

      logging.info(f"  Found {len(crs_list)} CRS instance(s)")
      all_crs_list.extend(crs_list)
      all_build_profiles.extend([f"{crs['name']}_builder" for crs in crs_list])

    # Render compose-litellm.yaml first
    try:
      litellm_rendered = render_litellm_compose(
        template_path=litellm_template_path,
        config_dir=config_dir,
        config_hash=args.config_hash,
        crs_list=all_crs_list
      )
    except FileNotFoundError as e:
      print(f"Error: {e}")
      return 1

    litellm_output_file = output_dir / "compose-litellm.yaml"
    litellm_output_file.write_text(litellm_rendered)
    logging.info(f"Written: {litellm_output_file}")

    # Render compose-build.yaml with all CRS instances
    try:
      rendered = render_compose_for_worker(
        worker_name=None,  # Not used in build mode
        crs_list=all_crs_list,
        template_path=template_path,
        oss_fuzz_path=oss_fuzz_path,
        project=args.project,
        config_dir=config_dir,
        engine=args.engine,
        sanitizer=args.sanitizer,
        architecture=args.architecture,
        mode='build',
        config_hash=args.config_hash,
        fuzzer_command=None,
        source_path=args.source_path
      )
    except FileNotFoundError as e:
      print(f"Error: {e}")
      return 1

    # Write to compose-build.yaml
    output_file = output_dir / "compose-build.yaml"
    output_file.write_text(rendered)
    logging.info(f"Written: {output_file}")

    # Print build profiles (comma-separated)
    print(','.join(all_build_profiles))

    logging.info(f"Summary:")
    logging.info(f"  Total CRS instances: {len(all_crs_list)}")
    logging.info(f"  Build profiles: {len(all_build_profiles)}")
    logging.info(f"  Output: {output_file}")

    return 0

  # Run mode: Generate compose-<worker>.yaml for specific worker
  elif args.mode == 'run':
    worker_name = args.worker
    logging.info(f"Run mode: generating compose-{worker_name}.yaml")

    if worker_name not in workers:
      print(f"Error: Worker '{worker_name}' not found in config-resource.yaml")
      return 1

    # Get CRS list for this worker
    crs_list = get_crs_for_worker(worker_name, resource_config, registry_parent_dir)

    if not crs_list:
      print(f"Error: No CRS instances configured for worker '{worker_name}'")
      return 1

    logging.info(f"Found {len(crs_list)} CRS instance(s):")
    for crs in crs_list:
      logging.info(f"  - {crs['name']}: CPUs={crs['cpuset']}, Memory={crs['memory_limit']}")

    # Render compose-litellm.yaml first
    try:
      litellm_rendered = render_litellm_compose(
        template_path=litellm_template_path,
        config_dir=config_dir,
        config_hash=args.config_hash,
        crs_list=crs_list
      )
    except FileNotFoundError as e:
      print(f"Error: {e}")
      return 1

    litellm_output_file = output_dir / "compose-litellm.yaml"
    litellm_output_file.write_text(litellm_rendered)
    logging.info(f"Written: {litellm_output_file}")

    # Render compose file
    try:
      rendered = render_compose_for_worker(
        worker_name=worker_name,
        crs_list=crs_list,
        template_path=template_path,
        oss_fuzz_path=oss_fuzz_path,
        project=args.project,
        config_dir=config_dir,
        engine=args.engine,
        sanitizer=args.sanitizer,
        architecture=args.architecture,
        mode='run',
        config_hash=args.config_hash,
        fuzzer_command=args.fuzzer_command,
        source_path=args.source_path
      )
    except FileNotFoundError as e:
      print(f"Error: {e}")
      return 1

    # Write to compose-<worker>.yaml
    output_file = output_dir / f"compose-{worker_name}.yaml"
    output_file.write_text(rendered)
    logging.info(f"Written: {output_file}")

    logging.info(f"Summary:")
    logging.info(f"  Worker: {worker_name}")
    logging.info(f"  CRS instances: {len(crs_list)}")
    logging.info(f"  Output: {output_file}")

    return 0


if __name__ == '__main__':
  exit(main())
