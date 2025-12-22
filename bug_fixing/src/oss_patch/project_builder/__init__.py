from pathlib import Path
import shutil
import logging
import yaml
import subprocess
import tempfile
import re
import os
from contextlib import contextmanager

from bug_fixing.src.oss_patch.functions import (
    create_docker_volume,
    docker_image_exists_in_volume,
    docker_image_exists,
    get_base_runner_image_name,
    get_builder_image_name,
    get_runner_image_name,
    run_command,
    is_git_repository,
    change_ownership_with_docker,
)
from bug_fixing.src.oss_patch.globals import (
    OSS_PATCH_DOCKER_IMAGES_FOR_CRS,
    OSS_PATCH_CRS_SYSTEM_IMAGES,
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
    OSS_PATCH_RUNNER_DATA_PATH,
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


@contextmanager
def temp_build_context(path_name="temp_data"):
    temp_path = Path(path_name).resolve()

    try:
        temp_path.mkdir(exist_ok=True)
    except OSError as e:
        raise e

    try:
        yield temp_path
    finally:
        if temp_path.exists():
            try:
                shutil.rmtree(temp_path)
            except OSError:
                pass


def _clone_project_repo(
    proj_yaml_path: Path, dst_path: Path, use_gitcache: bool = False
) -> bool:
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


def _read_lang_from_project_yaml(proj_yaml_path: Path) -> str:
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
        project_path: Path,
        force_rebuild: bool = False,
    ):
        self.work_dir = work_dir
        self.project_name = project_name
        self.oss_fuzz_path = oss_fuzz_path.resolve()
        self.project_path = project_path
        self.force_rebuild = force_rebuild

        assert self.project_path.exists()
        assert (self.project_path / "project.yaml").exists()

        self.project_lang = _read_lang_from_project_yaml(
            self.project_path / "project.yaml"
        )

    def build(self, source_path: Path, inc_build_enabled: bool = True) -> bool:
        if not self._validate_arguments():
            return False

        if not self._prepare_project_builder_image():
            return False

        if inc_build_enabled:
            if not self.take_incremental_build_snapshot(source_path):
                return False

        return True

    def build_fuzzers(
        self,
        source_path: Path | None = None,
    ) -> tuple[bytes, bytes] | None:
        # logger.info(f'Execute `build_fuzzers` command for "{self.project_name}"')

        if source_path:
            command = f"python3 {self.oss_fuzz_path / 'infra/helper.py'} build_fuzzers {self.project_name} {source_path}"
        else:
            command = f"python3 {self.oss_fuzz_path / 'infra/helper.py'} build_fuzzers {self.project_name}"

        proc = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode == 0:
            return None

        return (proc.stdout, proc.stderr)

    def remove_builder_image(
        self, volume_name: str = OSS_PATCH_DOCKER_IMAGES_FOR_CRS
    ) -> bool:
        logger.info(
            f'Removing "{get_builder_image_name(self.oss_fuzz_path, self.project_name)}" from {volume_name}'
        )
        container_command = f"docker rmi {get_builder_image_name(self.oss_fuzz_path, self.project_name)}"

        command = f"docker run --rm --privileged -v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} {OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} {container_command}"

        try:
            subprocess.check_call(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            return False

        return True

    def _validate_arguments(self):
        # Validate OSS-Fuzz path
        if not self.oss_fuzz_path.exists():
            logger.error(f"OSS-Fuzz path does not exist: {self.oss_fuzz_path}")
            return False

        # `project_path` must exist if provided
        if self.project_path:
            if not self.project_path.exists():
                logger.error(f"Project path does not exist: {self.project_path}")
                return False

            if not self.project_path.is_dir():
                logger.error(f"Project path is not a directory: {self.project_path}")
                return False

        # `project.yaml` must exist in `project_path`
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

    def _pull_base_images(self) -> bool:
        base_runner_image_name = get_base_runner_image_name(self.oss_fuzz_path)

        if docker_image_exists(base_runner_image_name):
            logger.info(
                f'Base runner image ("{base_runner_image_name}") already exists. Skip pulling it.'
            )
            return True

        logger.info(f'Pulling OSS-Fuzz base images "{base_runner_image_name}"...')

        # oss_fuzz_image_build_cmd = f"python3 /oss-fuzz/infra/helper.py pull_images"
        pull_cmd = f"docker pull {base_runner_image_name}"

        # command = (
        #     f"docker run --rm --privileged --network=host "
        #     f"-v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} "
        #     f"-v {self.oss_fuzz_path}:/oss-fuzz "
        #     f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} {pull_cmd}"
        # )

        run_command(pull_cmd)

        if not docker_image_exists(base_runner_image_name):
            logger.error(
                f'"{base_runner_image_name}" does not exist in the docker daemon.'
            )
            return False

        return True

    def _prepare_docker_volumes(self) -> bool:
        if not create_docker_volume(OSS_PATCH_DOCKER_IMAGES_FOR_CRS):
            return False
        if not create_docker_volume(OSS_PATCH_CRS_SYSTEM_IMAGES):
            return False
        return True

    def _prepare_builder_image(self) -> bool:
        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )

        # FIXME: move up
        if not self.force_rebuild:
            if docker_image_exists(builder_image_name):
                logger.info(
                    f'The image "{builder_image_name}" already exists. Skip building it.'
                )
                return True

        logger.info(f'Building the image "{builder_image_name}"...')

        oss_fuzz_image_build_cmd = f"python3 {self.oss_fuzz_path / 'infra/helper.py'} build_image --no-pull {self.project_name}"

        run_command(oss_fuzz_image_build_cmd)

        if not docker_image_exists(builder_image_name):
            logger.error(f'"{builder_image_name}" does not exist in the docker daemon.')
            return False

        return True

    def _prepare_project_builder_image(self) -> bool:
        if not self._pull_base_images():
            logger.error(f"Pulling OSS-Fuzz base images has failed...")
            return False

        if not self._prepare_builder_image():
            logger.error(
                f'Preparing builder image for "{self.project_name}" has failed...'
            )
            return False

        return True

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

    def _take_incremental_build_snapshot_for_c(self, source_path: Path) -> bool:
        # if not self._detect_incremental_build(volume_name):
        #     logger.info(
        #         "`replay_build.sh` not detected, incremental build feature disabled."
        #     )
        #     return False
        project_path = self.oss_fuzz_path / "projects" / self.project_name
        sanitizer = "address"

        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )

        new_src_dir = "/built-src"
        new_workdir = _workdir_from_dockerfile(project_path, self.project_name).replace(
            "/src", new_src_dir
        )
        container_name = f"{self.project_name.split('/')[-1]}-origin-{sanitizer}"

        try:
            create_container_command = (
                f"docker create --privileged --network=host "
                f"--env=SANITIZER={sanitizer} "
                f"--env=CCACHE_DIR=/workspace/ccache "
                f"--env=FUZZING_LANGUAGE={self.project_lang} "
                f"--env=CAPTURE_REPLAY_SCRIPT=1 "
                f"--name={container_name} "
                f"-v={self.oss_fuzz_path}/ccaches/{self.project_name}/ccache:/workspace/ccache "
                f"-v={self.oss_fuzz_path}/build/out/{self.project_name}/:/out/ "
                f"-v={source_path}:{_workdir_from_dockerfile(project_path, self.project_name)} "
                f"{builder_image_name} "
                f'/bin/bash -c "export PATH=/ccache/bin:\\$PATH && rsync -av \\$SRC/ {new_src_dir} && export SRC={new_src_dir} && cd {new_workdir} && chmod +x /usr/local/bin/compile && compile && cp -n /usr/local/bin/replay_build.sh \\$SRC/"'
            )

            proc = subprocess.run(
                create_container_command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("docker create command has failed")
                return False

            proc = subprocess.run(
                f"docker cp {OSS_PATCH_RUNNER_DATA_PATH / 'replay_build.sh'} {container_name}:/usr/local/bin/replay_build.sh",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("Installing `replay_build.sh` has failed")
                return False

            proc = subprocess.run(
                f"docker cp {OSS_PATCH_RUNNER_DATA_PATH / 'make_build_replayable.py'} {container_name}:/usr/local/bin/make_build_replayable.py",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("Installing `make_build_replayable.py` failed")
                return False

            with tempfile.TemporaryDirectory() as tmp_dir:
                patched_compile_path = Path(tmp_dir) / "compile"

                patched_compile_txt = self._get_patched_compile_sh()
                if not patched_compile_txt:
                    return False
                patched_compile_path.write_text(patched_compile_txt)

                # Command for patched `compile` in gcr.io/oss-fuzz/<proj-name>
                proc = subprocess.run(
                    f"docker cp {patched_compile_path} {container_name}:/usr/local/bin/compile",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if proc.returncode != 0:
                    logger.error("Installing patched compile script has failed")
                    return False

            proc = subprocess.run(
                f"docker start -a {container_name}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("docker start command has failed")
                return False

            commit_command = (
                f"docker container commit "
                f'-c "ENV REPLAY_ENABLED=1" '
                f'-c "ENV CAPTURE_REPLAY_SCRIPT=" '
                f'-c "ENV SRC={new_src_dir}" '
                f'-c "WORKDIR {new_workdir}" '
                f'-c "CMD [\\"compile\\"]" '
                f"{container_name} {builder_image_name}"
            )

            proc = subprocess.run(
                commit_command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("Committing container has failed")
                return False

            change_ownership_with_docker(source_path)

            return True

        finally:
            subprocess.run(
                "docker stop $(docker ps -a -q)",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                "docker rm $(docker ps -a -q)",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _take_incremental_build_snapshot_for_java(self, source_path: Path) -> bool:
        project_path = self.oss_fuzz_path / "projects" / self.project_name
        sanitizer = "address"

        builder_image_name = get_builder_image_name(
            self.oss_fuzz_path, self.project_name
        )
        new_src_dir = "/built-src"
        new_workdir = _workdir_from_dockerfile(project_path, self.project_name).replace(
            "/src", new_src_dir
        )
        container_name = f"{self.project_name.split('/')[-1]}-origin-{sanitizer}"

        try:
            create_container_command = (
                f"docker create --privileged --network=host "
                f"--env=SANITIZER={sanitizer} "
                f"--env=FUZZING_LANGUAGE={self.project_lang} "
                f"--name={container_name} "
                f"-v={self.oss_fuzz_path}/ccaches/{self.project_name}/ccache:/workspace/ccache "
                f"-v={self.oss_fuzz_path}/build/out/{self.project_name}/:/out/ "
                f"-v={source_path}:{_workdir_from_dockerfile(project_path, self.project_name)} "
                f"{builder_image_name} "
                f'/bin/bash -c "rsync -av \\$SRC/ {new_src_dir} && export SRC={new_src_dir} && cd {new_workdir} && chmod +x /usr/local/bin/compile && compile"'
            )

            proc = subprocess.run(
                create_container_command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("docker create command has failed")
                return False

            with tempfile.TemporaryDirectory() as tmp_dir:
                patched_compile_path = Path(tmp_dir) / "compile"

                patched_compile_txt = self._get_patched_compile_sh()
                if not patched_compile_txt:
                    return False
                patched_compile_path.write_text(patched_compile_txt)

                # Command for patched `compile` in gcr.io/oss-fuzz/<proj-name>
                proc = subprocess.run(
                    f"docker cp {patched_compile_path} {container_name}:/usr/local/bin/compile",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if proc.returncode != 0:
                    logger.error("Installing patched compile script has failed")
                    return False

            proc = subprocess.run(
                f"docker start -a {container_name}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("docker start command has failed")
                return False

            commit_command = (
                f"docker container commit "
                f'-c "ENV SRC={new_src_dir}" '
                f'-c "WORKDIR {new_workdir}" '
                f'-c "CMD [\\"compile\\"]" '
                f"{container_name} {builder_image_name}"
            )

            proc = subprocess.run(
                commit_command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                logger.error("Committing container has failed")
                return False

            change_ownership_with_docker(source_path)

            return True

        finally:
            subprocess.run(
                "docker stop $(docker ps -a -q)",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                "docker rm $(docker ps -a -q)",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def take_incremental_build_snapshot(self, source_path: Path) -> bool:
        logger.info("Taking a snapshot for incremental build...")
        assert self.oss_fuzz_path.exists()
        assert self.project_path

        assert source_path.exists()

        assert is_git_repository(source_path), (
            f'"{source_path}" is not a git repository'
        )

        if not docker_image_exists(
            get_builder_image_name(self.oss_fuzz_path, self.project_name)
        ):
            logger.error(
                f'The project builder image "{get_builder_image_name(self.oss_fuzz_path, self.project_name)}" does not exist.'
            )
            return False

        if self.project_lang in ["c", "c++"]:
            return self._take_incremental_build_snapshot_for_c(source_path)
        elif self.project_lang == "jvm":
            return self._take_incremental_build_snapshot_for_java(source_path)
        else:
            logger.error(
                f'Incremental build for language "{self.project_lang}" is not supported.'
            )
            return False

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
