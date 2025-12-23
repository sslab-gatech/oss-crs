import docker
import subprocess
from pathlib import Path
from bug_fixing.src.oss_patch.globals import (
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
    OSS_PATCH_CACHE_BUILDER_DATA_PATH,
    OSS_PATCH_DOCKER_IMAGES_FOR_CRS,
)
from tempfile import TemporaryDirectory
import os
from typing import Deque
from collections import deque
import sys
import shutil
import logging
import re
import yaml

logger = logging.getLogger()


def _docker_volume_exists(volume_name: str) -> bool:
    client = docker.from_env()
    try:
        # Attempt to retrieve the volume
        client.volumes.get(volume_name)
        return True
    except docker.errors.NotFound:  # pyright: ignore[reportAttributeAccessIssue]
        # The NotFound exception is raised if the volume does not exist
        return False
    except docker.errors.APIError as e:  # pyright: ignore[reportAttributeAccessIssue]
        # Handle other API errors (e.g., connection issue)
        return False


def create_docker_volume(volume_name: str) -> bool:
    # @TODO: Do we need to use the ordinary directory instead of docker volumes??
    if _docker_volume_exists(volume_name):
        # logger.info(f"The volume \"{volume_name}\" already exists. skip creation.")
        return True

    try:
        subprocess.check_call(
            f"docker volume create {volume_name}",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def load_image_to_volume(volume_name: str) -> bool:
    try:
        subprocess.check_call(
            f"docker volume create {volume_name}",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def change_ownership_with_docker(target_path: Path) -> bool:
    command = f"docker run --rm --privileged -v {target_path}:/target {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} chown -R {os.getuid()} /target"

    proc = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def pull_project_source(project_path: Path, dst_path: Path) -> bool:
    assert project_path.exists()

    if not _clone_project_repo(project_path, dst_path):
        logger.error("Cloning target project source code has failed.")
        return False

    if not _checkout_project_sources(project_path, dst_path):
        logger.error("Checking out project source code has failed.")
        return False

    return True


def _clone_project_repo(project_path: Path, dst_path: Path) -> bool:
    proj_yaml_path = project_path / "project.yaml"
    if not proj_yaml_path.exists():
        logger.error(f'Target project "{proj_yaml_path}" not found')
        return False

    with open(proj_yaml_path) as f:
        yaml_data = yaml.safe_load(f)

    if not "main_repo" in yaml_data.keys():
        logger.error(f"Invalid project.yaml file: {proj_yaml_path}")
        return False

    logger.info(
        f'Cloning the target project repository from "{yaml_data["main_repo"]}" to "{dst_path}"'
    )

    clone_command = f"git clone {yaml_data['main_repo']} --shallow-submodules --recurse-submodules {dst_path}"
    # @TODO: how to properly handle `--shallow-submodules --recurse-submodules` options

    try:
        subprocess.check_call(
            clone_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _checkout_project_sources(project_path: Path, dst_path: Path) -> bool:
    assert project_path.exists()

    aixcc_config_yaml_path = project_path / ".aixcc" / "config.yaml"

    if not aixcc_config_yaml_path.exists():
        # Don't have `.aixcc/config.yaml` directory, do nothing.
        return True

    with open(aixcc_config_yaml_path, "r") as f:
        aixcc_config_yaml = yaml.safe_load(f)

    assert aixcc_config_yaml["full_mode"]
    assert aixcc_config_yaml["full_mode"]["base_commit"]

    command = f"git reset --hard {aixcc_config_yaml['full_mode']['base_commit']}"
    try:
        subprocess.check_call(
            command,
            shell=True,
            cwd=dst_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"`{command}` has failed... {e}")
        return False


def is_git_repository(path: Path) -> bool:
    return os.path.isdir(path / ".git")


def reset_repository(path: Path) -> bool:
    proc = subprocess.run(
        f"git reset --hard && git clean -fdx",
        cwd=path,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def load_images_to_volume(images: list[str], volume_name: str) -> bool:
    logger.info(f'Loading docker images to "{volume_name}". It may take a while...')

    if len(images) == 0:
        logger.warning(f"No images to load provided")
        return True

    with TemporaryDirectory() as tmp_dir:
        images_path = Path(tmp_dir)

        for image_name in images:
            if not docker_image_exists(image_name):
                logger.error(f'"{image_name}" does not exist in docker daemon')
                return False

            subprocess.run(
                f"docker save -o {images_path / image_name.split('/')[-1]}.tar {image_name}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        docker_load_cmd = (
            f"docker run --privileged --rm "
            f"-v {OSS_PATCH_DOCKER_IMAGES_FOR_CRS}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {images_path}:/images "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
            f"sh -c 'for f in /images/*; do docker load -i \"$f\"; done'"
        )

        proc = subprocess.run(
            docker_load_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if proc.returncode == 0:
            return True
        else:
            return False

    return True


def docker_image_exists_in_volume(image_name: str, volume_name: str) -> bool:
    # assert _docker_volume_exists(volume_name)

    # command = f"docker run -d --privileged --name {container_name} -v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} {OSS_PATCH_DOCKER_CACHE_BUILDER_IMAGE} sleep infinity"
    command = f"docker run --rm --privileged -v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} /image_checker.sh {image_name}"

    # subprocess.check_call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)

    proc = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def docker_image_exists(image_name: str) -> bool:
    client = docker.from_env()

    try:
        client.images.get(image_name)
        return True
    except docker.errors.ImageNotFound:  # pyright: ignore[reportAttributeAccessIssue]
        return False


def get_base_runner_image_name(oss_fuzz_path: Path) -> str:
    # @TODO: it's a heuristic implementation. Our competition-based OSS-Fuzz has different base runner image.
    # maybe we need to fix this to follow standard OSS-Fuzz's name
    if "aixcc-finals" in (oss_fuzz_path / "infra" / "helper.py").read_text():
        return "ghcr.io/aixcc-finals/base-runner:v1.3.0"
    else:
        return "gcr.io/oss-fuzz-base/base-runner"


def get_builder_image_name(oss_fuzz_path: Path, project_name: str) -> str:
    dockerfile_path = oss_fuzz_path / "projects" / project_name / "Dockerfile"
    assert dockerfile_path.exists(), f"{dockerfile_path} does not exist"

    # @TODO: we need to unify the build image name... (e.g., "ghcr.io/oss-fuzz", "aixcc-finals", "aixcc-afc", ...).
    # There are no standard ways to get the image name given the OSS-Fuzz and project name.
    dockerfile_content = dockerfile_path.read_text()
    if "gcr.io/oss-fuzz-base" in dockerfile_content:
        return f"gcr.io/oss-fuzz/{project_name}"
    elif "aixcc-finals" in dockerfile_content:
        return f"aixcc-afc/{project_name}"
    else:
        assert False


def get_runner_image_name(proj_name: str) -> str:
    return f"gcr.io/oss-patch/{proj_name}/runner"


def get_crs_image_name(crs_name: str) -> str:
    return f"gcr.io/oss-patch/{crs_name}"


def run_command(command: str, n: int = 5, log_file: Path | None = None) -> None:
    """
    Executes a command and dynamically updates the terminal to show
    only the last N lines of output in real-time.

    Args:
        command (str): The command string to execute.
        n (int): The number of recent lines to keep and display. Defaults to 5.
        log_file (Path | None): Optional path to log file to append output.

    Raises:
        subprocess.CalledProcessError: If the command exits with a non-zero status.
    """
    # Use a deque (double-ended queue) with a maximum length of N.
    recent_lines_buffer: Deque[str] = deque(maxlen=n)
    lines_printed_count = (
        0  # Tracks how many lines we previously printed to manage cursor
    )
    first_output = True  # Flag to track if this is the first output

    # Open log file if provided
    log_handle = open(log_file, "a") if log_file else None

    # Get terminal width for calculating wrapped lines
    terminal_width = shutil.get_terminal_size(fallback=(80, 24)).columns

    def count_display_lines(text: str) -> int:
        """Calculate how many terminal lines a string will occupy, accounting for wrapping."""
        if not text:
            return 0
        # Each line in the text may wrap multiple times
        lines = text.split(os.linesep)
        total_display_lines = 0
        for line in lines:
            if len(line) == 0:
                total_display_lines += 1
            else:
                # Calculate how many terminal lines this logical line will occupy
                # Add terminal_width - 1 to ensure we round up
                total_display_lines += (
                    len(line) + terminal_width - 1
                ) // terminal_width
        return total_display_lines

    # We use Popen to start the process non-blockingly and pipe its output
    try:
        # Use Python's built-in stderr=STDOUT for robust output merging
        # start_new_session=True ensures child processes inherit I/O redirections
        # stdin=subprocess.DEVNULL prevents TTY access from nested processes
        process = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line-buffered reading
            start_new_session=True,  # Ensures all child processes inherit I/O
        )

        print(f"--- Executing: '{command}' ---")
        print(
            f"=============================== COMMAND OUTPUT ==============================="
        )

        # Read the output line by line in real-time
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                clean_line = line.strip()
                # Log every line to file (including empty lines for completeness)
                if log_handle:
                    log_handle.write(line)
                    log_handle.flush()

                if clean_line:
                    # 1. Update the rolling buffer
                    recent_lines_buffer.append(clean_line)

                    # 2. Clear previous output (move cursor up and clear lines)
                    # We move the cursor up by the number of lines we last printed.
                    # Only do this if we've already printed output from this function call
                    if lines_printed_count > 0 and not first_output:
                        sys.stdout.write("\033[1A\033[K" * lines_printed_count)

                    # 3. Print the new state of the buffer
                    current_output = os.linesep.join(list(recent_lines_buffer))
                    # Print the current content of the deque, followed by a newline
                    sys.stdout.write(current_output + os.linesep)

                    # Ensure the output is immediately shown on the console
                    sys.stdout.flush()

                    # 4. Update the tracker with actual display line count
                    lines_printed_count = count_display_lines(current_output)
                    first_output = False  # Mark that we've printed at least once

        # Wait for the process to complete and get the return code
        process.wait()

        # Don't clear the rolling display - leave final output visible
        # Just add a newline to separate from subsequent output
        if lines_printed_count > 0:
            sys.stdout.write(os.linesep)
            sys.stdout.flush()

        print(
            f"=============================================================================="
        )

        if process.returncode != 0:
            # Print final error state
            print(f"--- Command FAILED (Exit Code: {process.returncode}) ---")
            print(f"Error executing command: '{command}'")
            print(f"\nLast {n} lines of output/error before exit:")
            if recent_lines_buffer:
                print(os.linesep.join(list(recent_lines_buffer)))
            else:
                print("[No output captured]")
            # Re-raise the exception for caller to handle
            raise subprocess.CalledProcessError(process.returncode, command)

    except subprocess.CalledProcessError:
        # Re-raise CalledProcessError for caller to handle
        raise

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        raise

    finally:
        if log_handle:
            log_handle.close()


def _build_docker_cache_builder_image() -> bool:
    try:
        run_command(
            f"docker build --tag {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} --file {OSS_PATCH_CACHE_BUILDER_DATA_PATH / 'Dockerfile'} {str(Path.cwd())}"
        )

        return True
    except subprocess.CalledProcessError:
        return False


def prepare_docker_cache_builder() -> bool:
    if docker_image_exists(OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE):
        return True

    logger.info(
        f'"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE}" does not exist. Build a new image...'
    )
    if not _build_docker_cache_builder_image():
        logger.error(f'Building "{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE}" has failed.')
        return False

    return True


def extract_sanitizer_report(full_log: str) -> str | None:
    # Detect ASAN (==123==) or UBSAN (runtime error:)
    match = re.search(r"(==\d+==|runtime error:)", full_log)

    if match is None:
        # fail-safe: return the full crash log as it is.
        return None

    start_idx = match.start()
    # For UBSan runtime errors, we try to include the file path at the beginning of the line
    if "runtime error:" in match.group():
        line_start = full_log.rfind('\n', 0, start_idx)
        if line_start != -1:
            start_idx = line_start + 1
        else:
            start_idx = 0

    return full_log[start_idx:]


def extract_java_exception_report(full_log: str) -> str | None:
    match = re.search(r"== Java Exception:", full_log)

    if match is None:
        return None

    return full_log[match.start() :]


def get_cpv_config(project_path: Path, harness_name: str, cpv_name: str) -> dict | None:
    config_path = project_path / ".aixcc" / "config.yaml"

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return None

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"Failed to parse config.yaml: {e}")
        return None

    if not config or "harness_files" not in config:
        return None

    for harness in config.get("harness_files", []):
        if harness.get("name") != harness_name:
            continue

        cpvs = harness.get("cpvs", [])
        for cpv in cpvs:
            if cpv.get("name") == cpv_name:
                return {
                    "sanitizer": cpv.get("sanitizer", "address"),
                    "error_token": cpv.get("error_token"),
                }

        # Harness found but no matching CPV - return None
        return None

    return None


