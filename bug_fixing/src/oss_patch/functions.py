import docker
import subprocess
from pathlib import Path
from bug_fixing.src.oss_patch.globals import (
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
    OSS_PATCH_CACHE_BUILDER_DATA_PATH,
    DEFAULT_INC_BUILD_REGISTRY,
    OSS_CRS_PATH,
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


def _get_source_name_from_dockerfile(dockerfile_path: Path) -> str | None:
    """Extract source directory name from Dockerfile WORKDIR directive.

    Parses the last WORKDIR in Dockerfile to determine source directory name.
    Handles common patterns like:
    - WORKDIR $SRC/curl -> curl
    - WORKDIR /src/curl -> curl
    - WORKDIR libtiff -> libtiff

    Args:
        dockerfile_path: Path to Dockerfile

    Returns:
        Source directory name, or None if not found
    """
    import re

    if not dockerfile_path.exists():
        logger.error(f"Dockerfile not found: {dockerfile_path}")
        return None

    lines = dockerfile_path.read_text().splitlines()

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = re.match(r"WORKDIR\s+(.+)", stripped, re.IGNORECASE)
        if match:
            workdir = match.group(1).strip()
            # Normalize: remove common prefixes
            for prefix in ["$SRC/", "${SRC}/", "/src/"]:
                if workdir.startswith(prefix):
                    workdir = workdir[len(prefix) :]
                    break
            # Return last path component
            return Path(workdir).name

    logger.warning(f"No WORKDIR found in {dockerfile_path}")
    return None


def find_main_repo_tarball(benchmarks_dir: Path, project_name: str) -> Path | None:
    """Find the main repo tarball in benchmarks directory.

    Searches for the tarball in:
    - benchmarks_dir/{benchmark_name}/pkgs/{source_name}.tar.gz

    The source_name is extracted from Dockerfile's WORKDIR directive.

    Args:
        benchmarks_dir: Directory containing benchmarks (e.g., crsbench-2/benchmarks)
        project_name: OSS-Fuzz project name (e.g., "afc-curl-delta-01")

    Returns:
        Path to the tarball if found, None otherwise
    """
    # For project names like "aixcc/c/afc-curl-delta-01", extract the benchmark name
    benchmark_name = project_name.split("/")[-1]

    benchmark_dir = benchmarks_dir / benchmark_name
    pkgs_dir = benchmark_dir / "pkgs"

    if not pkgs_dir.exists():
        logger.error(f"pkgs directory not found: {pkgs_dir}")
        return None

    # Get source name from Dockerfile WORKDIR
    dockerfile_path = benchmark_dir / "Dockerfile"
    source_name = _get_source_name_from_dockerfile(dockerfile_path)

    if not source_name:
        logger.error(f"Could not determine source name from Dockerfile: {dockerfile_path}")
        return None

    tarball_path = pkgs_dir / f"{source_name}.tar.gz"

    if not tarball_path.exists():
        logger.error(f"Main repo tarball not found: {tarball_path}")
        return None

    logger.info(f"Found main repo tarball: {tarball_path}")
    return tarball_path


def extract_tarball_to_dir(tarball_path: Path, dst_path: Path) -> bool:
    """Extract a tarball to destination directory.

    The tarball is expected to contain a single top-level directory.
    This directory is extracted and its contents are moved to dst_path.

    Args:
        tarball_path: Path to .tar.gz file
        dst_path: Destination directory for extracted source

    Returns:
        True if successful, False otherwise
    """
    if dst_path.exists():
        logger.info(f"Removing existing destination: {dst_path}")
        shutil.rmtree(dst_path)

    dst_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Extracting {tarball_path.name} to {dst_path}")

    # Extract to a temp directory first to handle nested directory
    with TemporaryDirectory() as tmpdir:
        tmp_extract = Path(tmpdir) / "extracted"
        tmp_extract.mkdir()

        try:
            subprocess.check_call(
                ["tar", "-xzf", str(tarball_path), "-C", str(tmp_extract)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to extract tarball: {e}")
            return False

        # Find the extracted directory (usually only one top-level dir)
        extracted_dirs = list(tmp_extract.iterdir())
        if len(extracted_dirs) == 1 and extracted_dirs[0].is_dir():
            # Move contents of the single directory to dst_path
            for item in extracted_dirs[0].iterdir():
                shutil.move(str(item), str(dst_path / item.name))
        else:
            # Move all extracted content directly
            for item in tmp_extract.iterdir():
                shutil.move(str(item), str(dst_path / item.name))

    logger.info(f"Successfully extracted to {dst_path}")
    return True


def extract_tarball_to_source(tarball_path: Path, dst_path: Path) -> bool:
    """Extract a tarball and initialize as git repository for CRS use.

    This is for direct tarball usage with oss-bugfix-crs build/run commands.
    The tarball should NOT contain .aixcc directory (ground truth).

    Args:
        tarball_path: Path to .tar.gz file
        dst_path: Destination directory for extracted source

    Returns:
        True if successful, False otherwise
    """
    if not tarball_path.exists():
        logger.error(f"Tarball not found: {tarball_path}")
        return False

    if not extract_tarball_to_dir(tarball_path, dst_path):
        return False

    # Verify .aixcc directory does not exist
    aixcc_path = dst_path / ".aixcc"
    if aixcc_path.exists():
        logger.error(
            f"CRITICAL: .aixcc directory found in extracted source at {aixcc_path}. "
            f"Tarballs for CRS should NOT contain .aixcc directory."
        )
        return False

    # Initialize as git repository if not already
    git_dir = dst_path / ".git"
    if not git_dir.exists():
        logger.info("Initializing git repository for extracted source")
        try:
            subprocess.check_call(
                ["git", "init"],
                cwd=dst_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.check_call(
                ["git", "add", "-A"],
                cwd=dst_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.check_call(
                ["git", "commit", "--no-gpg-sign", "-m", "Initial commit"],
                cwd=dst_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to initialize git repository: {e}")
            return False

    logger.info(f"Successfully extracted tarball to {dst_path}")
    return True


def pull_project_source_from_tarball(
    benchmarks_dir: Path, project_name: str, dst_path: Path
) -> bool:
    """Pull project source from bundled tarball instead of git clone.

    This is an alternative to pull_project_source() that uses pre-bundled
    tarballs from crsbench benchmark bundle-all command. This avoids issues
    with .aixcc directories being present in git clones.

    Args:
        benchmarks_dir: Directory containing benchmarks with pkgs/ tarballs
        project_name: OSS-Fuzz project name (e.g., "afc-curl-delta-01")
        dst_path: Destination path for extracted source

    Returns:
        True if successful, False otherwise
    """
    tarball_path = find_main_repo_tarball(benchmarks_dir, project_name)
    if not tarball_path:
        logger.error(
            f"Could not find main repo tarball for {project_name} in {benchmarks_dir}"
        )
        return False

    if not extract_tarball_to_dir(tarball_path, dst_path):
        return False

    # Verify .aixcc directory does not exist in extracted source
    # This is critical: .aixcc contains ground truth that CRS should not access
    aixcc_path = dst_path / ".aixcc"
    assert not aixcc_path.exists(), (
        f"CRITICAL: .aixcc directory found in extracted source at {aixcc_path}. "
        f"Bundled tarballs should NOT contain .aixcc directory. "
        f"Please regenerate tarball using 'crsbench benchmark bundle'."
    )

    return True


def copy_git_repo(src: Path, dst: Path) -> None:
    """Copy a git repository, properly handling submodules.

    Git submodules have a .git file (not directory) that points to the parent's
    .git/modules/ directory. This function copies the repository and converts
    the submodule pointer to a proper .git directory.

    Args:
        src: Source repository path
        dst: Destination path
    """
    # First, do a regular copy
    shutil.copytree(src, dst)

    # Check if .git is a file (submodule pointer) instead of a directory
    git_path = dst / ".git"
    if git_path.is_file():
        # Read the gitdir pointer
        gitdir_content = git_path.read_text().strip()
        if gitdir_content.startswith("gitdir: "):
            gitdir_rel = gitdir_content[len("gitdir: ") :]
            # Resolve relative to the original source location
            gitdir_abs = (src / gitdir_rel).resolve()

            if gitdir_abs.is_dir():
                # Remove the pointer file
                git_path.unlink()
                # Copy the actual .git directory
                shutil.copytree(gitdir_abs, git_path)
                logger.info(f"Converted submodule .git pointer to directory: {dst}")


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
    uid = os.getuid()
    gid = os.getgid()
    command = f"docker run --rm --privileged -v {target_path.resolve()}:/target alpine:latest chown -R {uid}:{gid} /target"

    proc = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def copy_directory_with_docker(src_path: Path, dst_path: Path) -> bool:
    assert src_path.is_dir()
    assert dst_path.parent.exists()

    command = (
        f"docker run --rm --privileged "
        f"-v {src_path.resolve().parent}:/src "
        f"-v {dst_path.resolve().parent}:/dst "
        f"alpine:latest "
        f"cp -r /src/{src_path.name} /dst/{dst_path.name}"
    )

    proc = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        logger.error(f"copy_directory_with_docker failed: {command}")
        logger.error(f"stdout: {proc.stdout.decode()}")
        logger.error(f"stderr: {proc.stderr.decode()}")
        return False


def remove_directory_with_docker(target_path: Path) -> bool:
    assert target_path.is_dir()

    command = (
        f"docker run --rm --privileged "
        f"-v {target_path.resolve().parent}:/target "
        f"alpine:latest "
        f"rm -rf /target/{target_path.name}"
    )

    proc = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def pull_project_source(
    project_path: Path, dst_path: Path, use_gitcache: bool = False
) -> bool:
    assert project_path.exists()

    if not _clone_project_repo(project_path, dst_path, use_gitcache):
        logger.error("Cloning target project source code has failed.")
        return False

    if not _checkout_project_sources(project_path, dst_path):
        logger.error("Checking out project source code has failed.")
        return False

    return True


def _clone_project_repo(
    project_path: Path, dst_path: Path, use_gitcache: bool = False
) -> bool:
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

    git_prefix = "gitcache " if use_gitcache else ""
    clone_command = f"{git_prefix}git clone {yaml_data['main_repo']} --shallow-submodules --recurse-submodules {dst_path}"
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
        "git reset --hard",
        cwd=path,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def load_docker_images_to_dir(
    images: list[str],
    docker_root_path: Path,
    *,
    retag: tuple[str, str] | None = None,
) -> bool:
    """Load Docker images to a docker root directory.

    Args:
        images: List of image names to load
        docker_root_path: Path to docker root directory
        retag: Optional tuple of (src_image, dst_image) to retag after loading
               in the same DinD session

    Returns:
        True if successful, False otherwise
    """
    logger.info(
        f'Loading docker images to "{docker_root_path}". It may take a while...'
    )

    if len(images) == 0 and retag is None:
        logger.warning("No images to load provided")
        return True

    with TemporaryDirectory() as tmp_dir:
        images_path = Path(tmp_dir)

        for image_name in images:
            if not docker_image_exists(image_name):
                logger.error(f'"{image_name}" does not exist in docker daemon')
                return False

            # TODO: skip save if already exists
            subprocess.run(
                f"docker save -o {images_path / image_name.split('/')[-1]}.tar {image_name}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # Build the shell command to run inside DinD
        # First load images, then optionally retag
        inner_cmds = []

        if len(images) > 0:
            inner_cmds.append('for f in /images/*; do docker load -i "$f"; done')

        if retag is not None:
            src_image, dst_image = retag
            inner_cmds.append(f"docker tag {src_image} {dst_image}")
            logger.info(f'Will retag "{src_image}" -> "{dst_image}" after loading')

        if not inner_cmds:
            # Nothing to do
            return True

        combined_cmd = " && ".join(inner_cmds)

        docker_load_cmd = (
            f"docker run --privileged --rm "
            f"-v {docker_root_path}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {images_path}:/images "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
            f"sh -c '{combined_cmd}'"
        )

        proc = subprocess.run(
            docker_load_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if proc.returncode == 0:
            change_ownership_with_docker(docker_root_path)
            if retag is not None:
                logger.info(f'Retagged "{retag[0]}" -> "{retag[1]}" in docker root')
            return True
        else:
            return False

    return True


def docker_image_exists_in_dir(image_name: str, docker_root_path: Path) -> bool:
    # assert _docker_volume_exists(volume_name)

    # command = f"docker run -d --privileged --name {container_name} -v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} {OSS_PATCH_DOCKER_CACHE_BUILDER_IMAGE} sleep infinity"
    command = f"docker run --rm --privileged -v {docker_root_path}:{DEFAULT_DOCKER_ROOT_DIR} {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} /image_checker.sh {image_name}"

    # subprocess.check_call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)

    proc = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
    )

    if proc.returncode == 0:
        return True
    else:
        return False


def retag_docker_image_in_dir(
    src_image: str, dst_image: str, docker_root_path: Path
) -> bool:
    """Retag a Docker image inside a docker root directory.

    Uses dind container to run docker tag. Must wait for dockerd to be ready
    before executing the tag command (similar to image_checker.sh).
    """
    # Wait for dockerd to be ready (up to 30s), then run docker tag
    wait_and_tag_cmd = (
        "sh -c '"
        "ELAPSED=0; "
        "until docker info > /dev/null 2>&1 || [ $ELAPSED -ge 30 ]; do "
        "sleep 1; ELAPSED=$((ELAPSED + 1)); "
        "done; "
        "if [ $ELAPSED -ge 30 ]; then "
        "echo ERROR: dockerd not ready; exit 1; "
        "fi; "
        f"docker tag {src_image} {dst_image}"
        "'"
    )
    command = (
        f"docker run --privileged --rm "
        f"-v {docker_root_path}:{DEFAULT_DOCKER_ROOT_DIR} "
        f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
        f"{wait_and_tag_cmd}"
    )
    logger.debug(f"Retag command: {command}")
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )

    if proc.returncode == 0:
        logger.info(f'Retagged "{src_image}" -> "{dst_image}" in docker root')
        return True
    else:
        logger.error(f'Failed to retag "{src_image}" -> "{dst_image}" in docker root')
        logger.error(f"stdout: {proc.stdout}")
        logger.error(f"stderr: {proc.stderr}")
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


def get_incremental_build_image_name(
    oss_fuzz_path: Path, project_name: str, sanitizer: str = "address"
) -> str:
    return f"{get_builder_image_name(oss_fuzz_path, project_name)}:inc-{sanitizer}"


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
        # Context must be oss-crs root because Dockerfile uses paths like
        # "bug_fixing/base_images/..." which are relative to oss-crs/
        run_command(
            f"docker build --tag {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} --file {OSS_PATCH_CACHE_BUILDER_DATA_PATH / 'Dockerfile'} {OSS_CRS_PATH}"
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
        line_start = full_log.rfind("\n", 0, start_idx)
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


# Valid RTS modes per language
VALID_RTS_MODES = {
    "jvm": ["jcgeks", "openclover", "ekstazi", "none"],
    "c": ["binaryrts", "none"],
    "c++": ["binaryrts", "none"],
}

# Default RTS mode per language (when --with-rts is specified)
DEFAULT_RTS_MODE = {
    "jvm": "jcgeks",
    "c": "binaryrts",
    "c++": "binaryrts",
}


class RTSConfigError(Exception):
    """Raised when RTS configuration is invalid."""

    pass


def get_project_rts_config(project_path: Path) -> dict:
    """Get RTS and incremental build configuration from project.yaml.

    Args:
        project_path: Path to the project directory containing project.yaml

    Returns:
        dict with keys:
            - 'inc_build': bool (default: True)
            - 'rts_mode': str | None (value from project.yaml or None if not specified)
            - 'language': str (jvm, c, c++)
    """
    project_yaml_path = project_path / "project.yaml"

    if not project_yaml_path.exists():
        logger.warning(f"project.yaml not found: {project_yaml_path}")
        return {
            "inc_build": True,
            "rts_mode": None,
            "language": "c",  # default
        }

    with open(project_yaml_path, "r") as f:
        yaml_data = yaml.safe_load(f)

    language = yaml_data.get("language", "c")
    inc_build = yaml_data.get("inc_build", True)
    rts_mode = yaml_data.get("rts_mode", None)

    return {
        "inc_build": inc_build,
        "rts_mode": rts_mode,
        "language": language,
    }


def resolve_rts_config(
    cli_with_rts: bool, cli_rts_tool: str | None, project_config: dict
) -> tuple[bool, str]:
    """Resolve final inc_build and rts_mode values.

    Priority:
    1. CLI --rts-tool explicit value (overrides everything)
    2. project.yaml rts_mode value
    3. Default based on --with-rts flag and language

    Args:
        cli_with_rts: --with-rts flag from CLI
        cli_rts_tool: --rts-tool value from CLI (None if not explicitly provided)
        project_config: dict from get_project_rts_config()

    Returns:
        tuple of (inc_build_enabled, rts_mode)

    Raises:
        RTSConfigError: if rts_mode is invalid for the project language
    """
    inc_build = project_config.get("inc_build", True)
    language = project_config.get("language", "c")
    yaml_rts_mode = project_config.get("rts_mode")

    # Determine effective rts_mode
    if cli_rts_tool is not None:
        # CLI override takes precedence
        effective_rts_mode = cli_rts_tool
    elif yaml_rts_mode is not None:
        # Use project.yaml setting
        effective_rts_mode = yaml_rts_mode
    elif cli_with_rts:
        # Default based on language when --with-rts is specified
        effective_rts_mode = DEFAULT_RTS_MODE.get(language, "none")
    else:
        # No RTS
        effective_rts_mode = "none"

    # Validate rts_mode against language
    valid_modes = VALID_RTS_MODES.get(language, ["none"])
    if effective_rts_mode not in valid_modes:
        raise RTSConfigError(
            f"Invalid rts_mode '{effective_rts_mode}' for language '{language}'. "
            f"Valid modes: {valid_modes}"
        )

    logger.info(
        f"Resolved config: inc_build={inc_build}, rts_mode={effective_rts_mode}, language={language}"
    )

    return (inc_build, effective_rts_mode)


def get_git_commit_hash(repository_path: Path) -> str:
    """Get the git commit hash of a repository.

    Uses subprocess with safe.directory config to handle repositories
    that may have different ownership (e.g., copied repositories).
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository_path),
                "-c",
                "safe.directory=*",
                "rev-parse",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Failed to get git commit hash for {repository_path}: {e}")
        return "unknown"


# =============================================================================
# Inc-build image pull/retag utilities
# =============================================================================


def get_inc_build_remote_image_name(
    project_name: str,
    sanitizer: str = "address",
    registry: str = DEFAULT_INC_BUILD_REGISTRY,
) -> str:
    """Get remote inc-build Docker image name."""
    # Extract just the project name, removing any prefix like "aixcc/c/"
    simple_name = project_name.split("/")[-1]
    return f"{registry}/{simple_name}:inc-{sanitizer}"


def get_ossfuzz_inc_image_name(project_name: str, sanitizer: str = "address") -> str:
    """Get OSS-Fuzz compatible inc-build image name (aixcc-afc/{project}:inc-{sanitizer})."""
    simple_name = project_name.split("/")[-1]
    return f"aixcc-afc/{simple_name}:inc-{sanitizer}"


def _pull_docker_image(image_name: str, timeout: int = 600) -> bool:
    """Pull a Docker image from registry."""
    logger.info(f'Pulling Docker image "{image_name}"...')

    proc = subprocess.run(
        f"docker pull {image_name}",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )

    if proc.returncode == 0:
        logger.info(f'Successfully pulled "{image_name}"')
        return True
    else:
        logger.warning(f'Failed to pull "{image_name}": {proc.stderr.decode()[:500]}')
        return False


def retag_docker_image(src_image: str, dst_image: str) -> bool:
    """Retag a Docker image."""
    proc = subprocess.run(
        f"docker tag {src_image} {dst_image}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if proc.returncode == 0:
        logger.info(f'Retagged "{src_image}" -> "{dst_image}"')
        return True
    else:
        logger.error(f'Failed to retag "{src_image}" -> "{dst_image}"')
        return False


def ensure_inc_build_image(
    project_name: str,
    oss_fuzz_path: Path,
    sanitizer: str = "address",
    registry: str = DEFAULT_INC_BUILD_REGISTRY,
) -> bool:
    """Ensure inc-build image is available locally (pull from registry if needed).

    Tries to:
    1. Check if inc-build image already exists locally
    2. If not, pull from remote registry
    3. Retag to builder image format for OSS-Fuzz compatibility
    """
    builder_image = get_builder_image_name(oss_fuzz_path, project_name)
    builder_inc_image = f"{builder_image}:inc-{sanitizer}"

    # Check if already available in builder format
    if docker_image_exists(builder_inc_image):
        logger.info(f'Inc-build image "{builder_inc_image}" already exists locally.')
        return True

    # Check OSS-Fuzz compatible format (aixcc-afc/{project}:inc-{sanitizer})
    ossfuzz_image = get_ossfuzz_inc_image_name(project_name, sanitizer)
    if docker_image_exists(ossfuzz_image):
        logger.info(f'Found "{ossfuzz_image}", retagging to builder format...')
        return retag_docker_image(ossfuzz_image, builder_inc_image)

    # Try to pull from remote registry
    remote_image = get_inc_build_remote_image_name(project_name, sanitizer, registry)

    # Check if remote image exists locally (maybe already pulled with different tag)
    if docker_image_exists(remote_image):
        logger.info(f'Found "{remote_image}" locally, retagging to builder format...')
        return retag_docker_image(remote_image, builder_inc_image)

    # Pull from remote
    if _pull_docker_image(remote_image):
        return retag_docker_image(remote_image, builder_inc_image)

    logger.info(f"Inc-build image not available from registry, will build locally.")
    return False
