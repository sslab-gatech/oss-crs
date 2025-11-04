#!/usr/bin/env python3
"""Main CRS implementation for build and run operations."""

import os
import shutil
import subprocess
import sys
import atexit
import hashlib
import logging
import shlex
import signal
import uuid
import yaml
from pathlib import Path

from dotenv import dotenv_values

from . import render_compose

logger = logging.getLogger(__name__)


def _get_absolute_path(path):
    """Returns absolute path with user expansion."""
    return str(Path(path).expanduser().resolve())


def _get_command_string(command):
    """Returns a shell escaped command string."""
    return ' '.join(shlex.quote(part) for part in command)


def _verify_external_litellm(config_dir):
    """Verifies LiteLLM environment variables."""
    def keys_in_dict(keys, dict_):
        return all(key in dict_ for key in keys)

    keys = ["LITELLM_URL", "LITELLM_KEY"]
    if keys_in_dict(keys, os.environ):
        return True

    dotenv_path = Path(config_dir) / ".env"
    if dotenv_path.is_file():
        dotenv_dict = dotenv_values(str(dotenv_path))
        if keys_in_dict(keys, dotenv_dict):
            return True

    return False


def _build_project_image(project_name, oss_fuzz_dir, architecture):
    build_image_cmd = [
        'python3', 'infra/helper.py',
        'build_image',
        '--no-pull',
        '--architecture', architecture,
        project_name
    ]
    try:
        subprocess.check_call(build_image_cmd, cwd=oss_fuzz_dir)
    except subprocess.CalledProcessError:
        logging.error(f"Failed to build image for {project_name}")
        return False
    return True


def _clone_oss_fuzz_if_needed(oss_fuzz_dir):
    if not Path(oss_fuzz_dir).exists():
        logging.info(f"Cloning oss-fuzz to: {oss_fuzz_dir}")
        try:
            subprocess.check_call([
                'git', 'clone', 'https://github.com/google/oss-fuzz',
                str(oss_fuzz_dir)
            ])
        except subprocess.CalledProcessError:
            logging.error("Failed to clone oss-fuzz repository")
            return False
    return True


def _clone_project_source(project_name, oss_fuzz_dir, build_dir):
    """
    Clone project source based on main_repo in project.yaml.

    Args:
        project_name: Name of the project
        oss_fuzz_dir: Path to OSS-Fuzz root directory
        build_dir: Path to build directory

    Returns:
        bool: True if successful, False otherwise
    """
    project_yaml_path = Path(oss_fuzz_dir) / 'projects' / project_name / 'project.yaml'

    # Validate project.yaml exists
    if not project_yaml_path.exists():
        logger.error(f"project.yaml not found: {project_yaml_path}")
        return False

    # Read main_repo from project.yaml
    try:
        with open(project_yaml_path) as f:
            project_config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to parse project.yaml: {e}")
        return False

    main_repo = project_config.get('main_repo')
    if not main_repo:
        logger.error(f"main_repo not found in {project_yaml_path}")
        return False

    # Clone to build/src/
    clone_dest = Path(build_dir) / 'src'

    if clone_dest.exists():
        logger.info(f"Source already exists at {clone_dest}, skipping clone")
        return True

    # Create parent directory if needed
    clone_dest.parent.mkdir(parents=True, exist_ok=True)

    # Clone with depth 1 and recursive submodules
    logger.info(f"Cloning {main_repo} to {clone_dest}")
    try:
        subprocess.check_call([
            'git', 'clone',
            '--depth', '1',
            '--recursive',
            main_repo,
            str(clone_dest)
        ])
        logger.info(f"Successfully cloned source to {clone_dest}")
        return True
    except subprocess.CalledProcessError:
        logger.error(f"Failed to clone repository: {main_repo}")
        return False


def build_crs(config_dir, project_name, oss_fuzz_dir, build_dir,
              engine='libfuzzer', sanitizer='address',
              architecture='x86_64', source_path=None,
              project_path=None, overwrite=False, clone=False,
              check_project_fn=None,
              registry_dir=None, project_image_prefix='gcr.io/oss-fuzz',
              external_litellm=False):
    """
    Build CRS for a project using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files
        project_name: Name of the OSS-Fuzz project
        oss_fuzz_dir: Path to OSS-Fuzz root directory
        build_dir: Path to build directory
        engine: Fuzzing engine (default: libfuzzer)
        sanitizer: Sanitizer to use (default: address)
        architecture: Architecture (default: x86_64)
        source_path: Optional path to local source
        project_path: Optional path to local OSS-compatible project
        overwrite: Overwrite existing project in oss-fuzz/projects/ (default: False)
        clone: Clone project source from main_repo in project.yaml (default: False)
        check_project_fn: Optional function to check if project exists
        registry_dir: Optional path to local oss-crs-registry directory
        project_image_prefix: Project image prefix (default: gcr.io/oss-fuzz)
        external_litellm: Use external LiteLLM instance (default: False)

    Returns:
        bool: True if successful, False otherwise
    """
    # Validate project exists if checker provided TODO (don't remove the todo)
    if check_project_fn and not check_project_fn():
        return False

    # Validate mutually exclusive options
    if clone and source_path:
        logger.error("Cannot use --clone with source_path (mutually exclusive)")
        return False

    # Check if litellm keys are provided
    if external_litellm and not _verify_external_litellm(config_dir):
        logger.error("LITELLM_URL or LITELLM_KEY is not provided in the environment")
        return False

    _clone_oss_fuzz_if_needed(oss_fuzz_dir)

    # Copy project_path to oss-fuzz/projects/{project_name} if provided
    if project_path:
        src_path = Path(project_path).resolve()

        # Validate source project path
        if not src_path.exists():
            logger.error(f"Project path does not exist: {src_path}")
            return False
        if not src_path.is_dir():
            logger.error(f"Project path is not a directory: {src_path}")
            return False
        if not (src_path / 'project.yaml').exists():
            logger.error(f"project.yaml not found in: {src_path}")
            return False

        # Destination path (handles nested names like aixcc/c/project)
        dest_path = Path(oss_fuzz_dir) / 'projects' / project_name

        # Check if destination exists
        if dest_path.exists():
            if not overwrite:
                logger.error(
                    f"Project already exists: {dest_path}. "
                    f"Use --overwrite to replace it."
                )
                return False
            logger.info(f"Overwriting existing project at: {dest_path}")
            shutil.rmtree(dest_path)

        # Create parent directories for nested project names
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy project to oss-fuzz projects directory
        logger.info(f"Copying project from {src_path} to {dest_path}")
        shutil.copytree(src_path, dest_path)
        logger.info(f"Successfully copied project to {dest_path}")

    # Clone project source if requested
    # Note: --clone is for custom projects that don't have source cloning in Dockerfile
    # Standard OSS-Fuzz projects already clone source in their Dockerfile
    if clone:
        if not _clone_project_source(project_name, oss_fuzz_dir, build_dir):
            return False
        # Use cloned source as source_path
        source_path = str(Path(build_dir) / 'src')
        logger.info(f"Using cloned source as source_path: {source_path}")

    # Build project image
    _build_project_image(project_name, oss_fuzz_dir, architecture)

    # Resolve registry_dir if provided
    oss_crs_registry_path = None
    if registry_dir:
        oss_crs_registry_path = str(Path(registry_dir).resolve())

    # Compute source_tag for image versioning if source_path provided
    source_tag = None
    abs_source_path = None
    if source_path:
        abs_source_path = _get_absolute_path(source_path)
        source_tag = hashlib.sha256(abs_source_path.encode()).hexdigest()[:12]
        logger.info('Using source tag for image versioning: %s', source_tag)

    # Generate compose files using render_compose module
    logger.info('Generating compose-build.yaml')
    try:
        build_profiles, config_hash, crs_build_dir = render_compose.render_build_compose(
            config_dir=config_dir,
            build_dir=build_dir,
            oss_fuzz_dir=oss_fuzz_dir,
            project=project_name,
            engine=engine,
            sanitizer=sanitizer,
            architecture=architecture,
            registry_dir=oss_crs_registry_path,
            source_path=abs_source_path,
            project_image_prefix=project_image_prefix,
            external_litellm=external_litellm
        )
        crs_build_dir = Path(crs_build_dir)
    except Exception as e:
        logger.error('Failed to generate compose files: %s', e)
        return False

    if not build_profiles:
        logger.error('No build profiles found')
        return False

    logger.info('Found %d build profiles: %s', len(build_profiles), ', '.join(build_profiles))

    # Look for compose files in the hash directory
    compose_file = crs_build_dir / 'compose-build.yaml'

    if not compose_file.exists():
        logger.error('compose-build.yaml was not generated at: %s', compose_file)
        return False

    # Project names for separate compose projects
    litellm_project = f'crs-litellm-{config_hash}'
    build_project = f'crs-build-{config_hash}'

    # Start LiteLLM services in detached mode as separate project (unless using external)
    if not external_litellm:
        litellm_compose_file = crs_build_dir / 'compose-litellm.yaml'
        if not litellm_compose_file.exists():
            logger.error('compose-litellm.yaml was not generated at: %s', litellm_compose_file)
            return False

        logger.info('Starting LiteLLM services (project: %s)', litellm_project)
        litellm_up_cmd = ['docker', 'compose', '-p', litellm_project,
                          '-f', str(litellm_compose_file), 'up', '-d']
        try:
            subprocess.check_call(litellm_up_cmd)
        except subprocess.CalledProcessError:
            logger.error('Failed to start LiteLLM services')
            return False
    else:
        logger.info('Using external LiteLLM instance')

    # Run docker compose up for each build profile
    completed_profiles = []
    try:
        for profile in build_profiles:
            logger.info('Building profile: %s', profile)

            try:
                # Step 1: Build the containers
                build_cmd = [
                    'docker', 'compose',
                    '-p', build_project,
                    '-f', str(compose_file),
                    '--profile', profile,
                    'build'
                ]
                logger.info('Building containers for profile: %s', profile)
                subprocess.check_call(build_cmd)

                # Step 2: If source_path provided, copy source to workdir
                if source_path:
                    # Extract CRS name from profile (format: {crs_name}_builder)
                    crs_name = profile.replace('_builder', '')
                    service_name = f'{crs_name}_builder'

                    # Generate unique container name for docker commit
                    container_name = f'crs-source-copy-{uuid.uuid4().hex}'
                    # Use tagged image name for version control if source_tag exists
                    image_name = f'{project_name}_{crs_name}_builder'
                    if source_tag:
                        image_name = f'{image_name}:{source_tag}'

                    logger.info('Copying source from /local-source-mount to workdir for: %s', service_name)
                    copy_cmd = [
                        'docker', 'compose',
                        '-p', build_project,
                        '-f', str(compose_file),
                        '--profile', profile,
                        'run', '--no-deps', '--name', container_name,
                        service_name,
                        '/bin/bash', '-c',
                        'workdir=$(pwd) && cd / && rm -rf "$workdir" && cp -r /local-source-mount "$workdir"'
                    ]
                    logger.info('Running copy command: %s', _get_command_string(copy_cmd))
                    subprocess.check_call(copy_cmd)

                    # Extract original image metadata (CMD and ENTRYPOINT) to preserve them
                    logger.info('Extracting metadata from original image: %s', image_name)

                    # Get original CMD
                    cmd_inspect = subprocess.run(
                        ['docker', 'inspect', image_name, '--format', '{{json .Config.Cmd}}'],
                        capture_output=True, text=True, check=True
                    )
                    original_cmd = cmd_inspect.stdout.strip()

                    # Get original ENTRYPOINT
                    entrypoint_inspect = subprocess.run(
                        ['docker', 'inspect', image_name, '--format', '{{json .Config.Entrypoint}}'],
                        capture_output=True, text=True, check=True
                    )
                    original_entrypoint = entrypoint_inspect.stdout.strip()

                    # Commit the container to preserve source changes in the image
                    logger.info('Committing container %s to image %s', container_name, image_name)
                    commit_cmd = ['docker', 'commit']

                    # Add --change flags to restore original metadata if they exist
                    if original_cmd and original_cmd != 'null':
                        commit_cmd.extend(['--change', f'CMD {original_cmd}'])
                    if original_entrypoint and original_entrypoint != 'null':
                        commit_cmd.extend(['--change', f'ENTRYPOINT {original_entrypoint}'])

                    commit_cmd.extend([container_name, image_name])
                    logger.info('Running commit command: %s', _get_command_string(commit_cmd))
                    subprocess.check_call(commit_cmd)

                    # Clean up the container
                    logger.info('Removing container: %s', container_name)
                    cleanup_cmd = ['docker', 'rm', container_name]
                    subprocess.check_call(cleanup_cmd)

                    logger.info('Successfully copied source and committed to image: %s', image_name)

                # Step 3: Run the build
                up_cmd = [
                    'docker', 'compose',
                    '-p', build_project,
                    '-f', str(compose_file),
                    '--profile', profile,
                    'up', '--abort-on-container-exit'
                ]
                logger.info('Running build for profile: %s', profile)
                subprocess.check_call(up_cmd)

                completed_profiles.append(profile)
            except subprocess.CalledProcessError:
                logger.error('Docker compose operation failed for profile: %s', profile)
                return False

            logger.info('Successfully built profile: %s', profile)

        logger.info('All CRS builds completed successfully')
    finally:
        # Clean up: remove all containers from completed profiles
        logger.info('Cleaning up build services')
        if completed_profiles:
            down_cmd = ['docker', 'compose', '-p', build_project, '-f', str(compose_file)]
            for profile in completed_profiles:
                down_cmd.extend(['--profile', profile])
            down_cmd.extend(['down', '--remove-orphans'])
            subprocess.run(down_cmd)
        else:
            subprocess.run(['docker', 'compose',
                          '-p', build_project,
                          '-f', str(compose_file),
                          'down', '--remove-orphans'])

        # Stop LiteLLM services but keep them for reuse (unless using external)
        if not external_litellm:
            logger.info('Stopping LiteLLM services')
            subprocess.run(['docker', 'compose', '-p', litellm_project,
                           '-f', str(litellm_compose_file), 'stop'])

    return True


def run_crs(config_dir, project_name, fuzzer_name, fuzzer_args,
            oss_fuzz_dir, build_dir, worker='local',
            engine='libfuzzer', sanitizer='address',
            architecture='x86_64', check_project_fn=None,
            registry_dir=None,
            hints_dir=None,
            harness_source=None,
            external_litellm=False):
    """
    Run CRS using docker compose.

    Args:
        config_dir: Directory containing CRS configuration files
        project_name: Name of the OSS-Fuzz project
        fuzzer_name: Name of the fuzzer to run
        fuzzer_args: Arguments to pass to the fuzzer
        oss_fuzz_dir: Path to OSS-Fuzz root directory
        build_dir: Path to build directory
        worker: Worker name to run CRS on (default: local)
        engine: Fuzzing engine (default: libfuzzer)
        sanitizer: Sanitizer to use (default: address)
        architecture: Architecture (default: x86_64)
        check_project_fn: Optional function to check if project exists
        registry_dir: Optional path to local oss-crs-registry directory
        hints_dir: Optional directory containing hints (SARIF and corpus)
        harness_source: Optional path to harness source file (will be mounted to container)
        external_litellm: Use external LiteLLM instance (default: False)

    Returns:
        bool: True if successful, False otherwise
    """
    # Validate project exists if checker provided TODO (don't remove todo)
    if check_project_fn and not check_project_fn():
        return False

    # Check if litellm keys are provided
    if external_litellm and not _verify_external_litellm(config_dir):
        logger.error("LITELLM_URL or LITELLM_KEY is not provided in the environment")
        return False

    _clone_oss_fuzz_if_needed(oss_fuzz_dir)

    # Resolve registry_dir if provided
    oss_crs_registry_path = None
    if registry_dir:
        oss_crs_registry_path = str(Path(registry_dir).resolve())

    # Generate compose files using render_compose module
    logger.info('Generating compose-%s.yaml', worker)
    fuzzer_command = [fuzzer_name] + fuzzer_args
    try:
        config_hash, crs_build_dir = render_compose.render_run_compose(
            config_dir=config_dir,
            build_dir=build_dir,
            oss_fuzz_dir=oss_fuzz_dir,
            project=project_name,
            engine=engine,
            sanitizer=sanitizer,
            architecture=architecture,
            registry_dir=oss_crs_registry_path,
            worker=worker,
            fuzzer_command=fuzzer_command,
            harness_source=harness_source,
            external_litellm=external_litellm
        )
        crs_build_dir = Path(crs_build_dir)
    except Exception as e:
        logger.error('Failed to generate compose file: %s', e)
        return False

    # Look for compose files
    compose_file = crs_build_dir / f'compose-{worker}.yaml'

    if not compose_file.exists():
        logger.error('compose-%s.yaml was not generated', worker)
        return False

    # Project names for separate compose projects
    litellm_project = f'crs-litellm-{config_hash}'
    run_project = f'crs-run-{config_hash}-{worker}'

    # Start LiteLLM services in detached mode as separate project (unless using external)
    if not external_litellm:
        litellm_compose_file = crs_build_dir / 'compose-litellm.yaml'
        if not litellm_compose_file.exists():
            logger.error('compose-litellm.yaml was not generated')
            return False

        logger.info('Starting LiteLLM services (project: %s)', litellm_project)
        litellm_up_cmd = ['docker', 'compose', '-p', litellm_project,
                          '-f', str(litellm_compose_file), 'up', '-d']
        try:
            subprocess.check_call(litellm_up_cmd)
        except subprocess.CalledProcessError:
            logger.error('Failed to start LiteLLM services')
            return False
    else:
        logger.info('Using external LiteLLM instance')

    logger.info('Starting runner services from: %s', compose_file)
    # Commands for cleanup - only affect run project
    compose_down_cmd = ['docker', 'compose',
                       '-p', run_project,
                       '-f', str(compose_file),
                       'down', '--remove-orphans']

    def cleanup():
        """Cleanup function for compose files"""
        subprocess.run(compose_down_cmd)
        if not external_litellm:
            litellm_compose_file = crs_build_dir / 'compose-litellm.yaml'
            litellm_stop_cmd = ['docker', 'compose', '-p', litellm_project,
                               '-f', str(litellm_compose_file), 'stop']
            subprocess.run(litellm_stop_cmd)

    def signal_handler(signum, frame):
        """Handle termination signals"""
        print(f"\nReceived signal {signum}")
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)   # Ctrl-C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination

    # Register cleanup on normal exit
    atexit.register(cleanup)

    # Only pass the run compose file (litellm is in separate project)
    compose_cmd = ['docker', 'compose',
                  '-p', run_project,
                  '-f', str(compose_file),
                  'up', '--abort-on-container-exit']
    try:
        subprocess.check_call(compose_cmd)
    except subprocess.CalledProcessError:
        logger.error('Docker compose failed for: %s', compose_file)
        return False
    finally:
        cleanup()

    return True
