from pathlib import Path
import logging
import shutil
import yaml
import subprocess
from bug_fixing.src.oss_patch.functions import (
    get_runner_image_name,
    run_command,
    docker_image_exists,
    docker_image_exists_in_dir,
    load_docker_images_to_dir,
    get_builder_image_name,
    get_base_runner_image_name,
    get_incremental_build_image_name,
    get_git_commit_hash,
    copy_git_repo,
    pull_project_source,
    is_git_repository,
    remove_directory_with_docker,
    retag_docker_image_in_dir,
)
from bug_fixing.src.oss_patch.project_builder import OSSPatchProjectBuilder
from bug_fixing.src.oss_patch.globals import (
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_BUILD_CONTEXT_DIR,
    OSS_PATCH_RUNNER_DATA_PATH,
    OSS_PATCH_DIR,
)
from tempfile import TemporaryDirectory

logger = logging.getLogger()


def _copy_clean_oss_fuzz(src_oss_fuzz: Path, dst_oss_fuzz: Path) -> None:
    """
    Copy OSS-Fuzz directory excluding build artifacts and unnecessary files.

    - Keeps .git directory for version tracking (needed by get_git_commit_hash)
    - Creates empty build/ directory (excludes all contents)

    Args:
        src_oss_fuzz: Source OSS-Fuzz directory path
        dst_oss_fuzz: Destination OSS-Fuzz directory path
    """
    def ignore_build_artifacts(directory, contents):
        ignored = []
        dir_path = Path(directory)

        # At OSS-Fuzz root level, exclude projects directory only
        if directory == str(src_oss_fuzz):
            ignored.append("projects")  # Will copy only needed project separately

        # Inside build/ directory, exclude ALL contents
        if dir_path == src_oss_fuzz / "build":
            return contents  # Ignore everything inside build/

        return ignored

    logger.info(f"Copying clean OSS-Fuzz from {src_oss_fuzz} to {dst_oss_fuzz}...")
    shutil.copytree(src_oss_fuzz, dst_oss_fuzz, ignore=ignore_build_artifacts)
    logger.info("Clean OSS-Fuzz copied successfully.")


def _disable_force_build_in_oss_fuzz(oss_fuzz_path: Path):
    helper_path = oss_fuzz_path / "infra/helper.py"

    helper_script = helper_path.read_text()

    helper_path.write_text(
        helper_script.replace("build_project_image=True", "build_project_image=False")
    )


def _remove_all_projects_in_oss_fuzz(
    oss_fuzz_path: Path, except_project: str | None = None
):
    if except_project:
        except_project_path = oss_fuzz_path / "projects" / except_project
        assert except_project_path.exists()

        # @TODO: better way to do this deletions
        with TemporaryDirectory() as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            tmp_project_path = tmp_dir_path / "target-project"

            shutil.copytree(except_project_path, tmp_project_path)

            shutil.rmtree(oss_fuzz_path / "projects")

            Path.mkdir(except_project_path.parent, parents=True)
            shutil.move(tmp_project_path, except_project_path)

        assert len([d for d in (oss_fuzz_path / "projects").iterdir()]) == 1

        return

    shutil.rmtree(oss_fuzz_path / "projects")


class OSSPatchCRSContext:
    def __init__(
        self,
        project_name: str,
        work_dir: Path,
    ):
        self.project_name = project_name
        self.work_dir = work_dir.resolve()
        self.run_context_dir = self.work_dir / "run_context"
        self.oss_fuzz_path = self.run_context_dir / "oss-fuzz"
        self.project_path = self.oss_fuzz_path / "projects" / self.project_name
        self.proj_src_path = self.run_context_dir / "proj-src"
        self.docker_root_path = self.run_context_dir / "docker"

    def _write_runner_metadata(self):
        oss_fuzz_path = self.run_context_dir / "oss-fuzz"
        proj_src_path = self.run_context_dir / "proj-src"

        config_yaml = {
            "project_name": self.project_name,
            "oss-fuzz": get_git_commit_hash(oss_fuzz_path),
            "proj-src": get_git_commit_hash(proj_src_path),
        }

        with open(self.run_context_dir / "config.yaml", "w") as f:
            f.write(yaml.safe_dump(config_yaml, sort_keys=False))

    def _create_dedicated_docker_root(self):
        assert self.run_context_dir.exists()

        if not self.docker_root_path.exists():
            self.docker_root_path.mkdir()

    def _should_skip(
        self,
        oss_fuzz_path: Path,
        source_path: Path | None,
    ) -> bool:
        if not self.run_context_dir.exists():
            return False

        if not self.oss_fuzz_path.exists():
            return False

        if not self.proj_src_path.exists():
            return False

        if get_git_commit_hash(oss_fuzz_path) != get_git_commit_hash(
            self.oss_fuzz_path
        ):
            return False

        if not source_path:
            return True

        if get_git_commit_hash(source_path) != get_git_commit_hash(
            self.proj_src_path
        ):
            return False

        return True

    def _construct_run_context(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
    ) -> bool:
        if self._should_skip(oss_fuzz_path, custom_source_path):
            return True

        if self.run_context_dir.exists():
            remove_directory_with_docker(self.run_context_dir)

        self.run_context_dir.mkdir()

        self._create_dedicated_docker_root()

        # Copy clean OSS-Fuzz (excludes build/, projects/, .git/)
        _copy_clean_oss_fuzz(oss_fuzz_path, self.oss_fuzz_path)

        # Create projects directory and copy only the needed project
        logger.info(f"Copying project {self.project_name}...")
        (self.oss_fuzz_path / "projects").mkdir(exist_ok=True)

        if custom_project_path:
            logger.info(f"Using custom project from {custom_project_path}...")
            shutil.copytree(custom_project_path, self.project_path)
        else:
            # Copy the project from original oss-fuzz
            shutil.copytree(oss_fuzz_path / "projects" / self.project_name, self.project_path)
        logger.info(f"Project {self.project_name} copied successfully.")

        assert self.project_path.exists()

        if custom_source_path:
            logger.info(f'Using the provided project source: "{custom_source_path}"')
            logger.info("Copying project source...")
            shutil.copytree(custom_source_path, self.proj_src_path)
            logger.info("Project source copied successfully.")
        else:
            logger.info("Pulling project source code...")
            if not pull_project_source(self.project_path, self.proj_src_path):
                logger.error("Failed to pull project source code")
                return False
            logger.info("Project source pulled successfully.")

        _disable_force_build_in_oss_fuzz(self.oss_fuzz_path)

        self._write_runner_metadata()

        assert self.project_path.exists()
        assert self.proj_src_path.exists()

        return True

    def _load_necessary_images_to_docker_root(
        self, *, inc_build_enabled: bool = False
    ) -> bool:
        builder_image = get_builder_image_name(self.oss_fuzz_path, self.project_name)
        base_runner_image = get_base_runner_image_name(self.oss_fuzz_path)

        if inc_build_enabled:
            # Load inc-build image instead of the regular builder image
            inc_image = get_incremental_build_image_name(
                self.oss_fuzz_path, self.project_name
            )
            images_to_load = [inc_image, base_runner_image]
        else:
            images_to_load = [builder_image, base_runner_image]

        # TODO: optimize speed; caching
        if not load_docker_images_to_dir(
            [
                image_name
                for image_name in images_to_load
                if not docker_image_exists_in_dir(image_name, self.docker_root_path)
            ],
            self.docker_root_path,
        ):
            logger.error(f'Image loading to "{self.docker_root_path}" has failed')
            return False

        if inc_build_enabled:
            # Retag inc-build image to :latest inside docker root
            # This is isolated to this run's docker root, not affecting host docker
            inc_image = get_incremental_build_image_name(
                self.oss_fuzz_path, self.project_name
            )
            if not retag_docker_image_in_dir(
                inc_image, f"{builder_image}:latest", self.docker_root_path
            ):
                logger.error("Failed to retag inc-build image to :latest in docker root")
                return False

        return True

    def build(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
        force_rebuild: bool = False,
        inc_build_enabled: bool = True,
    ) -> bool:
        if not self._construct_run_context(
            oss_fuzz_path, custom_project_path, custom_source_path
        ):
            return False

        logger.info("Building project with OSS-Fuzz...")
        project_builder = OSSPatchProjectBuilder(
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
            force_rebuild=force_rebuild,
        )
        if not project_builder.build(
            self.proj_src_path, inc_build_enabled=inc_build_enabled
        ):
            return False
        logger.info("Project build completed successfully.")

        assert self.proj_src_path.exists()
        assert is_git_repository(
            self.proj_src_path
        )  # FIXME: should support non-git source

        logger.info("Loading necessary Docker images...")
        if not self._load_necessary_images_to_docker_root(inc_build_enabled=inc_build_enabled):
            return False
        logger.info("Docker images loaded successfully.")

        return True

    def _copy_oss_fuzz_and_sources(
        self,
        oss_fuzz_path: Path,
        source_path: Path,
        dst_dir: Path,
    ) -> bool:
        logger.info(f'Copying OSS-Fuzz and target project sources to "{dst_dir}"')

        assert dst_dir.exists()

        # prepare cp sources
        proj_src_path = (dst_dir / "proj-src").resolve()
        copied_oss_fuzz_path = (dst_dir / "oss-fuzz").resolve()

        # copy existing CP's source (use copy_git_repo to handle submodules)
        copy_git_repo(source_path, proj_src_path)

        # Ensure clean copy
        if copied_oss_fuzz_path.exists():
            shutil.rmtree(copied_oss_fuzz_path)
        shutil.copytree(oss_fuzz_path, copied_oss_fuzz_path)

        target_project_path = copied_oss_fuzz_path / "projects" / self.project_name

        if (target_project_path / ".aixcc").exists():
            logger.warning(".aixcc directory found. Removing it")
            shutil.rmtree(target_project_path / ".aixcc")

        assert not (target_project_path / ".aixcc").exists()

        # @TODO: better way to do this deletions
        _remove_all_projects_in_oss_fuzz(
            copied_oss_fuzz_path, except_project=self.project_name
        )

        return True

    def _prepare_runner_image(self, copy_dir: Path) -> bool:
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

        # FIXME: should not copy; when we have a dedicated oss_path_runner_base
        # Copy OSS_PATCH_DIR to OSS_PATCH_BUILD_CONTEXT_DIR / {basename}
        build_context = OSS_PATCH_BUILD_CONTEXT_DIR
        build_context.mkdir(parents=True, exist_ok=True)

        dst_patch_dir = build_context / OSS_PATCH_DIR.name  # Uses basename
        if dst_patch_dir.exists():
            shutil.rmtree(dst_patch_dir)
        shutil.copytree(OSS_PATCH_DIR, dst_patch_dir)

        try:
            command = (
                f"docker build --tag {get_runner_image_name(self.project_name)} "
                f"--build-arg target_project={self.project_name} "
                f"--file {OSS_PATCH_RUNNER_DATA_PATH / 'Dockerfile'} "
                f"{str(build_context)}"
            )
            run_command(command)
            return True
        except subprocess.CalledProcessError:
            return False

    def build_fuzzers(self, source_path: Path):
        build_fuzzers_command = f"python3 /oss-fuzz/infra/helper.py build_fuzzers {self.project_name} /cp-sources"

        runner_command = (
            f"docker run --rm --privileged "
            f"-v {self.docker_root_path}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {source_path}:/cp-sources "
            f"{get_runner_image_name(self.project_name)} "
            f"{build_fuzzers_command}"
        )

        try:
            subprocess.check_call(runner_command, shell=True)
            return True
        except subprocess.CalledProcessError as e:
            raise e

    def run_pov(
        self, oss_fuzz_path: Path, harness_name: str, pov_path: Path, source_path: Path
    ) -> tuple[bytes, bytes]:
        # build_fuzzers_command = f"python3 /oss-fuzz/infra/helper.py build_fuzzers {self.project_name} /cp-sources"

        reproduce_command = f"python3 {oss_fuzz_path / 'infra/helper.py'} reproduce {self.project_name} {harness_name} /testcase"

        # runner_command = (
        #     f"docker run --rm --privileged --network=host "
        #     f"-v {OSS_PATCH_CRS_DOCKER_ASSETS}:{DEFAULT_DOCKER_ROOT_DIR} "
        #     f"-v {source_path.resolve()}:/cp-sources "
        #     f"-v {pov_path.resolve()}:/testcase "
        #     f"{get_runner_image_name(self.project_name)} "
        #     f'sh -c "{build_fuzzers_command} && {reproduce_command}"'
        # )

        # print(runner_command)
        proc = subprocess.run(
            reproduce_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        return (proc.stdout, proc.stderr)
