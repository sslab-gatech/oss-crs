from pathlib import Path
import shutil
import logging
import yaml
import subprocess
import tempfile
import re
import os

from bug_fixing.src.oss_patch.functions import (
    create_docker_volume,
    docker_image_exists_in_volume,
    docker_image_exists,
    get_base_runner_image_name,
    get_builder_image_name,
    get_runner_image_name,
    run_command,
)
from bug_fixing.src.oss_patch.globals import (
    OSS_PATCH_CRS_DOCKER_ASSETS,
    OSS_PATCH_CRS_SYSTEM_IMAGES,
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
    OSS_PATCH_RUNNER_DATA_PATH,
    OSS_PATCH_CACHE_BUILDER_DATA_PATH,
)

WORKDIR_REGEX = re.compile(r"\s*WORKDIR\s*([^\s]+)")

PATCH_SNIPPET_FOR_COMPILE = """
#################### OSS-PATCH: script for patched run ####################
# `/built-src/{proj-src}` to `/src/{proj-src}`
export MOUNTED_SRC_DIR=$(echo $PWD | sed 's/built-src/src/')
pushd $MOUNTED_SRC_DIR 

# Now in /src/{proj-src}
git config --global --add safe.directory $MOUNTED_SRC_DIR 
git diff HEAD > /tmp/patch.diff

popd
# Now returned to `/built-src/{proj-src}`
if [ -s /tmp/patch.diff ]; then
    git apply /tmp/patch.diff
else
    echo "No patch file found at /tmp/patch.diff or it is empty. Skipping git apply."
fi
#################### OSS-PATCH: script for patched run ####################
"""


logger = logging.getLogger(__name__)


def _clone_project_repo(proj_yaml_path: Path, dst_path: Path) -> bool:
    if not proj_yaml_path.exists():
        logger.error(f'Target project "{proj_yaml_path}" not found')
        return False

    with open(proj_yaml_path) as f:
        yaml_data = yaml.safe_load(f)

    if not "main_repo" in yaml_data.keys():
        logger.error(f"Invalid project.yaml file: {proj_yaml_path}")
        return False

    logger.info(
        f'Cloning the target project repository from "{yaml_data["main_repo"]}"...'
    )

    clone_command = f"git clone {yaml_data['main_repo']} --depth 1 --shallow-submodules --recurse-submodules {dst_path}"
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


def _read_lang_from_project_yaml(proj_yaml_path: Path):
    with open(proj_yaml_path, "r") as f:
        proj_yaml = yaml.safe_load(f)

    return proj_yaml["language"]


def _workdir_from_lines(lines, default="/src"):
    """Gets the WORKDIR from the given lines."""
    for line in reversed(lines):  # reversed to get last WORKDIR.
        match = re.match(WORKDIR_REGEX, line)
        if match:
            workdir = match.group(1)
            workdir = workdir.replace("$SRC", "/src")

            if not os.path.isabs(workdir):
                workdir = os.path.join("/src", workdir)

            return os.path.normpath(workdir)

    return default


def _workdir_from_dockerfile(project_path: Path, proj_name: str):
    dockerfile_path = project_path / "Dockerfile"

    """Parses WORKDIR from the Dockerfile for the given project."""
    with open(dockerfile_path) as file_handle:
        lines = file_handle.readlines()

    return _workdir_from_lines(lines, default=os.path.join("/src", proj_name))


class OSSPatchProjectBuilder:
    def __init__(
        self,
        work_dir: Path,
        project_name: str,
        oss_fuzz_path: Path,
        project_path: Path | None = None,
        source_path: Path | None = None,
    ):
        self.work_dir = work_dir
        self.project_name = project_name
        self.oss_fuzz_path = oss_fuzz_path.resolve()
        self.project_path = project_path
        self.source_path = source_path

    def validate_arguments(self):
        # Validate OSS-Fuzz path
        if not self.oss_fuzz_path.exists():
            logger.error(f"OSS-Fuzz path does not exist: {self.oss_fuzz_path}")
            return False

        if self.source_path:
            # Validation 1: source_path requires project_path
            # if not project_path:
            #   logger.error(
            #       "ERROR: --source-path requires --project-path\n"
            #       "You must provide project metadata when using pre-cloned source.\n"
            #       "Usage: --project-path /path/to/project --source-path /path/to/source"
            #   )
            #   return False

            if not self.source_path.exists():
                logger.error(f"Source path does not exist: {self.source_path}")
                return False

        # Validation 2: project_path must exist if provided
        if self.project_path:
            if not self.project_path.exists():
                logger.error(f"Project path does not exist: {self.project_path}")
                return False

            if not self.project_path.is_dir():
                logger.error(f"Project path is not a directory: {self.project_path}")
                return False

        # Validation 3: project.yaml must exist in project_path
        if not self.project_path:
            self.project_path = self.oss_fuzz_path / "projects" / self.project_name

        proj_yaml = self.project_path / "project.yaml"
        if not proj_yaml.exists():
            logger.error(
                f"project.yaml not found in {self.project_path}\n"
                "External projects must have OSS-Fuzz compatible structure with project.yaml"
            )
            return False

        return True

    def _cleanup_copy_dir(self, dir_path: Path):
        shutil.rmtree(dir_path)

    def copy_oss_fuzz_and_sources(
        self,
        dst_dir: Path,
    ) -> bool:
        assert self.project_path

        logger.info("Copying OSS-Fuzz and target project's sources")

        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.mkdir()

        # prepare cp sources
        cp_source_path = (dst_dir / "cp-sources").resolve()
        copied_oss_fuzz_path = (dst_dir / "oss-fuzz").resolve()

        if self.source_path:
            # copy existing CP's source
            shutil.copytree(self.source_path, cp_source_path)
        else:
            # CP's source not provided, clone the remote repository
            if not _clone_project_repo(
                self.project_path / "project.yaml", cp_source_path
            ):
                return False

        # copy the provided OSS-Fuzz source
        shutil.copytree(self.oss_fuzz_path, copied_oss_fuzz_path)

        # overwrite the provided project configs
        shutil.copytree(
            self.project_path,
            copied_oss_fuzz_path / "projects" / self.project_name,
            dirs_exist_ok=True,
        )

        # From now, we use the overwritten OSS-Fuzz
        self.oss_fuzz_path = copied_oss_fuzz_path

        return True

    def _pull_base_images_in_volume(self, volume_name: str) -> bool:
        base_runner_image_name = get_base_runner_image_name(self.oss_fuzz_path)

        if docker_image_exists_in_volume(base_runner_image_name, volume_name):
            logger.info(
                f'Base runner image ("{base_runner_image_name}") already exists in the {volume_name}.'
            )
            return True

        logger.info(f"Pulling OSS-Fuzz base images inside the {volume_name}...")

        # oss_fuzz_image_build_cmd = f"python3 /oss-fuzz/infra/helper.py pull_images"
        pull_cmd = f"docker pull {base_runner_image_name}"

        command = (
            f"docker run --rm --privileged --net=host "
            f"-v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {self.oss_fuzz_path}:/oss-fuzz "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} {pull_cmd}"
        )

        run_command(command)
        return True

    def prepare_docker_volumes(self) -> bool:
        if not create_docker_volume(OSS_PATCH_CRS_DOCKER_ASSETS):
            return False
        if not create_docker_volume(OSS_PATCH_CRS_SYSTEM_IMAGES):
            return False
        return True

    def _build_builder_image_in_cache(self, volume_name: str) -> bool:
        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )

        logger.info(
            f'Building the image "{builder_image_name}" inside the cache-builder...'
        )

        oss_fuzz_image_build_cmd = f"python3 /oss-fuzz/infra/helper.py build_image --no-pull {self.project_name}"
        command = f"docker run --rm --privileged --net=host -v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} -v {self.oss_fuzz_path}:/oss-fuzz {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} {oss_fuzz_image_build_cmd}"

        run_command(command)

        if not docker_image_exists_in_volume(builder_image_name, volume_name):
            logger.error(f'Creating builder image "{builder_image_name}" has failed...')
            return False

        return True

    def _prepare_builder_image_in_volume(self, volume_name: str) -> bool:
        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )

        if docker_image_exists_in_volume(builder_image_name, volume_name):
            logger.info(
                f'The image "{builder_image_name}" already exists in docker cache: {volume_name}'
            )
            return True

        # Create a builder image that exists in OSS-Fuzz projects like json-c, nginx, aixcc/c/mock-c.
        if not self._build_builder_image_in_cache(volume_name):
            return False

        return True

    def prepare_project_builder_image(
        self, volume_name: str = OSS_PATCH_CRS_DOCKER_ASSETS
    ) -> bool:
        if not self._pull_base_images_in_volume(volume_name):
            logger.error(f"Pulling OSS-Fuzz base images has failed...")
            return False

        if not self._prepare_builder_image_in_volume(volume_name):
            logger.error(
                f"Preparing builder image for {self.project_name} has failed..."
            )
            return False

        return True

    def prepare_runner_image(self, copy_dir: Path) -> bool:
        assert (copy_dir / "oss-fuzz").exists()
        assert (copy_dir / "cp-sources").exists()

        if docker_image_exists(get_runner_image_name(self.project_name)):
            logger.info(
                f'runner image "{get_runner_image_name(self.project_name)}" already exists. Use the existing image.'
            )
            return True

        runner_container_name = f"oss-patch-{self.project_name.split('/')[-1]}-runner"

        try:
            if not self._build_runner_image():
                logger.error("Building oss-patch runner image failed")
                return False

        finally:
            subprocess.run(
                f"docker stop {runner_container_name}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )
            subprocess.run(
                f"docker rm {runner_container_name}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            )

        return True

    def _build_runner_image(self) -> bool:
        if docker_image_exists(get_runner_image_name(self.project_name)):
            logger.info(
                f'OSS-Patch runner image "{get_runner_image_name(self.project_name)}" already exists, skipping the build process.'
            )
            return True

        logger.info(
            f'Building runner image "{get_runner_image_name(self.project_name)}"...'
        )

        try:
            command = (
                f"docker build --tag {get_runner_image_name(self.project_name)} "
                f"--build-arg target_project={self.project_name} "
                f"--file {OSS_PATCH_RUNNER_DATA_PATH / 'Dockerfile'} "
                f"{str(Path.cwd())}"
            )
            run_command(command)
            return True
        except subprocess.CalledProcessError:
            return False

    def _detect_incremental_build(self, volume_name: str) -> bool:
        # Check if the project_builder image contains `/usr/local/bin/replay_build.sh`
        if not docker_image_exists_in_volume(
            get_builder_image_name(self.oss_fuzz_path, self.project_name), volume_name
        ):
            return False

        command = (
            f"docker run --rm --privileged "
            f"-v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
            f"docker run --rm {get_builder_image_name(self.oss_fuzz_path, self.project_name)} stat /usr/local/bin/replay_build.sh"
        )

        # subprocess.check_call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)

        proc = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
        )

        if proc.returncode == 0:
            return True
        else:
            return False

    def take_incremental_build_snapshot(
        self, volume_name: str = OSS_PATCH_CRS_DOCKER_ASSETS
    ) -> bool:
        if not self._detect_incremental_build(volume_name):
            logger.info(
                "`replay_build.sh` not detected, incremental build feature disabled."
            )
            return True

        project_path = self.oss_fuzz_path / "projects" / self.project_name
        sanitizer = "address"

        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )

        # subprocess.check_call(f"python3 {oss_fuzz_path}/infra/helper.py build_image --no-pull {proj_name}", cwd=oss_fuzz_path, shell=True)

        new_src_dir = "/built-src"
        new_workdir = _workdir_from_dockerfile(project_path, self.project_name).replace(
            "/src", new_src_dir
        )
        container_name = f"{self.project_name.split('/')[-1]}-origin-{sanitizer}"
        build_artifact_command = (
            f"docker run "
            f"--env=SANITIZER={sanitizer} "
            f"--env=CCACHE_DIR=/workspace/ccache "
            f"--env=FUZZING_LANGUAGE={_read_lang_from_project_yaml(project_path / 'project.yaml')} "
            f"--env=CAPTURE_REPLAY_SCRIPT=1 "
            f"--name={container_name} "
            f"-v=/oss-fuzz/ccaches/{self.project_name}/ccache:/workspace/ccache "
            f"-v=/oss-fuzz/build/out/{self.project_name}/:/out/ "
            f"-v=/cp-sources:{_workdir_from_dockerfile(project_path, self.project_name)} "
            f"{builder_image_name} "
            f'/bin/bash -c "export PATH=/ccache/bin:\\$PATH && rsync -av \\$SRC/ {new_src_dir} && export SRC={new_src_dir} && cd {new_workdir} && compile && cp -n /usr/local/bin/replay_build.sh \\$SRC/"'
        )

        # Command for patched `compile` in gcr.io/oss-fuzz/<proj-name>
        patch_compile_command = (
            f"docker cp /work/compile {container_name}:/usr/local/bin/compile"
        )

        commit_command = (
            f"docker container commit "
            f'-c "ENV REPLAY_ENABLED=1" '
            f'-c "ENV CAPTURE_REPLAY_SCRIPT=" '
            f'-c "ENV SRC={new_src_dir}" '
            f'-c "WORKDIR {new_workdir}" '
            f'-c "CMD [\\"compile\\"]" '
            f"{container_name} {builder_image_name}"
        )

        shell_script = f"""
        #!/bin/bash

        set -e -o pipefail
        set -u

        {build_artifact_command}

        # patch /usr/bin/compile
        {patch_compile_command}

        {commit_command}

        docker stop $(docker ps -a -q) && docker rm $(docker ps -a -q)
        """

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            (tmp_path / "prepare_artifacts.sh").write_text(shell_script)

            patched_compile = self._get_patched_compile_sh()
            if not patched_compile:
                return False
            (tmp_path / "compile").write_text(patched_compile)

            runner_command = (
                f"docker run "
                f"--rm --privileged "
                f"-v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} "
                f"-v {tmp_path}:/work "
                f"{get_runner_image_name(self.project_name)} "
                f"/bin/bash -c 'chmod +x /work/* && /work/prepare_artifacts.sh'"
            )

            subprocess.check_call(runner_command, shell=True)

        return True

    def _get_patched_compile_sh(self) -> str | None:
        compile_sh_path = (
            self.oss_fuzz_path / "infra" / "base-images" / "base-builder" / "compile"
        )

        if not compile_sh_path.exists():
            logger.error(f"`compile` script does not exist in `{compile_sh_path}`")
            return None

        original_content = compile_sh_path.read_text()
        echo_pattern = (
            'echo "---------------------------------------------------------------"\n'
        )

        found = original_content.find(echo_pattern)

        if found == -1:
            logger.error(f"Pattern not found in `compile` script.")
            return None

        return (
            original_content[: found + len(echo_pattern)]
            + PATCH_SNIPPET_FOR_COMPILE
            + original_content[found + len(echo_pattern) :]
        )
