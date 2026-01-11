from pathlib import Path
import logging
import shutil
import yaml
import base64
import subprocess
import secrets
from bug_fixing.src.oss_patch.functions import (
    get_runner_image_name,
    run_command,
    docker_image_exists,
    docker_image_exists_in_dir,
    load_docker_images_to_dir,
    get_builder_image_name,
    get_incremental_build_image_name,
    get_base_runner_image_name,
    get_git_commit_hash,
    get_crs_image_name,
    change_ownership_with_docker,
    copy_git_repo,
    pull_project_source,
    is_git_repository,
    copy_directory_with_docker,
    remove_directory_with_docker,
)
from bug_fixing.src.oss_patch.project_builder import OSSPatchProjectBuilder
from bug_fixing.src.oss_patch.globals import (
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_BUILD_CONTEXT_DIR,
    OSS_PATCH_RUNNER_DATA_PATH,
    OSS_PATCH_DIR,
)
from bug_fixing.src.oss_patch.models import CRSMode
from tempfile import TemporaryDirectory

logger = logging.getLogger()


def _check_povs(povs_path: Path) -> bool:
    if not povs_path.exists():
        logger.error(f"Invalid `--povs` option: the directory {povs_path} not found")
        return False

    if not povs_path.is_dir():
        logger.error(f"Invalid `--povs` option: {povs_path} is not a directory")
        return False

    return True


def _cleanup_dir(target_dir: Path):
    if not target_dir.exists():
        return
    for item in target_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


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


def _check_force_build_in_oss_fuzz(oss_fuzz_path: Path) -> bool:
    helper_path = oss_fuzz_path / "infra/helper.py"

    if "build_project_image=True" in helper_path.read_text():
        return False

    return True


class OSSPatchCRSContext:
    def __init__(
        self,
        project_name: str,
        work_dir: Path,
        out_dir: Path | None = None,
        log_dir: Path | None = None,
        cpuset: str | None = None,
        memory: str | None = None,
    ):
        self.project_name = project_name
        self.work_dir = work_dir.resolve()
        self.run_context_dir = self.work_dir / "run_context"
        self.oss_fuzz_path = self.run_context_dir / "oss-fuzz"
        self.project_path = self.oss_fuzz_path / "projects" / self.project_name
        self.proj_src_path = self.run_context_dir / "proj-src"
        self.docker_root_path = self.work_dir / "docker"

        self.out_dir = out_dir
        self.log_dir = log_dir
        self.cpuset = cpuset
        self.memory = memory

        if out_dir and not out_dir.exists():
            out_dir.mkdir()

        if log_dir and not log_dir.exists():
            log_dir.mkdir(parents=True)

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

    def _construct_run_context(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
    ) -> bool:
        if self.run_context_dir.exists():
            logger.info(
                f'Cleaning up existing run context directory: "{self.run_context_dir}"'
            )
            change_ownership_with_docker(self.run_context_dir)
            shutil.rmtree(self.run_context_dir)

        self.run_context_dir.mkdir()

        self._create_dedicated_docker_root()

        shutil.copytree(oss_fuzz_path, self.oss_fuzz_path)

        # Copy the custom-provided project
        if custom_project_path:
            if self.project_path.exists():
                shutil.rmtree(self.project_path)
            shutil.copytree(custom_project_path, self.project_path)

        _remove_all_projects_in_oss_fuzz(self.oss_fuzz_path, self.project_name)

        assert self.project_path.exists()

        if custom_source_path:
            logger.info(f'Using the provided project source: "{custom_source_path}"')
            shutil.copytree(custom_source_path, self.proj_src_path)
        else:
            pull_project_source(self.project_path, self.proj_src_path)

        _disable_force_build_in_oss_fuzz(self.oss_fuzz_path)

        self._write_runner_metadata()

        assert self.project_path.exists()
        assert self.proj_src_path.exists()

        return True

    def _load_necessary_images_to_docker_root(self, oss_fuzz_path: Path) -> bool:
        # Choose between incremental-build-enabled builder image and original builder image
        if docker_image_exists(
            get_incremental_build_image_name(
                oss_fuzz_path, self.project_name, "address"
            )
        ):
            builder_image_name = get_incremental_build_image_name(
                oss_fuzz_path, self.project_name, "address"
            )
        else:
            builder_image_name = get_builder_image_name(
                oss_fuzz_path, self.project_name
            )

        images_to_load = [
            builder_image_name,
            get_base_runner_image_name(oss_fuzz_path),
        ]
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

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
            force_rebuild=force_rebuild,
        )
        if not project_builder.build(
            self.proj_src_path, inc_build_enabled=inc_build_enabled
        ):
            return False

        assert self.proj_src_path.exists()
        assert is_git_repository(
            self.proj_src_path
        )  # FIXME: should support non-git source

        if not self._load_necessary_images_to_docker_root(oss_fuzz_path):
            return False

        return True

    def _copy_oss_fuzz_and_sources(
        self,
        oss_fuzz_path: Path,
        source_path: Path,
        dst_dir: Path,
    ) -> bool:
        logger.info(f'Copying OSS-Fuzz and target project sources to "{dst_dir}"')

        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.mkdir()

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

    def _prepare_povs_yaml(self, harness_name: str, povs_path: Path, mode: str):
        """
        Prepare {pov_id}.yaml files under the [work_dir]/povs.
        """
        for pov_path in povs_path.iterdir():
            logger.info(f'Creating yaml for "{pov_path.name}"')
            with open((self.work_dir / "povs" / f"{pov_path.name}.yaml"), "w") as f:
                yaml.dump(
                    {
                        "project_name": self.project_name,
                        "harness_name": harness_name,
                        "pov_id": pov_path.name,
                        "blob": base64.b64encode(pov_path.read_bytes()).decode(),
                        "mode": mode,
                    },
                    f,
                    sort_keys=False,
                )

    def _run_crs_against_povs(
        self, crs_name: str, litellm_api_key: str, litellm_api_base: str
    ) -> bool:
        # Run the CRS container
        from datetime import datetime

        assert self.out_dir

        if not _check_force_build_in_oss_fuzz(self.oss_fuzz_path):
            logger.error(
                f"OSS-Fuzz's forciful image build is enabled, which prevents our CRS using incremental build"
            )
            return False

        if not self.oss_fuzz_path.exists():
            logger.error(
                f"OSS-Fuzz does not exist in run_context ({self.run_context_dir}). Run `build` command first."
            )
            return False
        if not self.proj_src_path.exists():
            logger.error(
                f"Target project's source does not exist in run_context ({self.run_context_dir}). Run `build` command first."
            )
            return False

        # Prepare log file if log_dir is set
        log_file = None
        if self.log_dir:
            timestamp = datetime.now().strftime("%y%m%d%H%M%S")
            safe_name = self.project_name.replace("/", "_")
            log_file = self.log_dir / f"crs_run_{safe_name}_{timestamp}.log"
            logger.info(f"CRS execution log will be saved to: {log_file}")

        tmp_docker_root_dir = self.work_dir / f"run-{secrets.token_hex(8)}"
        # @TODO: find a way to optimize this copying routine
        logger.info(f'Copying pre-populated docker root to "{tmp_docker_root_dir}"')
        copy_directory_with_docker(self.docker_root_path, tmp_docker_root_dir)

        with TemporaryDirectory() as tmp_dir:
            crs_run_tmp_dir = Path(tmp_dir)
            try:
                self._copy_oss_fuzz_and_sources(
                    self.oss_fuzz_path, self.proj_src_path, crs_run_tmp_dir
                )

                tmp_oss_fuzz_path = crs_run_tmp_dir / "oss-fuzz"
                tmp_proj_src_path = crs_run_tmp_dir / "proj-src"

                resource_flags = ""
                if self.cpuset:
                    resource_flags += f"--cpuset-cpus={self.cpuset} "
                if self.memory:
                    resource_flags += f"--memory={self.memory} "

                command = (
                    f"docker run --rm --privileged "
                    f"{resource_flags}"
                    f"-v {tmp_docker_root_dir}:/var/lib/docker "
                    f"-v {tmp_oss_fuzz_path}:/oss-fuzz "
                    f"-v {tmp_proj_src_path}:/cp-sources "
                    f"-v {self.work_dir.resolve()}:/work "
                    f"-v {self.out_dir.resolve()}:/artifacts "
                    f"-e LITELLM_API_BASE={litellm_api_base} "
                    f"-e LITELLM_API_KEY={litellm_api_key} "
                    f"-e TARGET_PROJ={self.project_name} "
                    f"-e OSS_FUZZ=/oss-fuzz "
                    f"-e CP_SOURCES=/cp-sources "
                    f"{get_crs_image_name(crs_name)}"
                )

                try:
                    # @TODO: ensure the clean-up of existing docker processes
                    # subprocess.check_call(command, shell=True)
                    run_command(command, n=10, log_file=log_file)
                    return True
                except subprocess.CalledProcessError as e:
                    logger.error(f"CRS failed: {e}")
                    return False

            finally:
                change_ownership_with_docker(crs_run_tmp_dir)
                change_ownership_with_docker(self.out_dir)
                remove_directory_with_docker(tmp_docker_root_dir)

    def _prepare_hints(self, hints_path: Path):
        shutil.copytree(hints_path, self.work_dir / "hints")

    def run_crs_against_povs(
        self,
        crs_name: str,
        harness_name: str,
        povs_path: Path,
        litellm_api_key: str,
        litellm_api_base: str,
        hints_path: Path | None,
        mode: CRSMode,
    ) -> bool:
        if not _check_povs(povs_path):
            return False

        povs_dir = self.work_dir / "povs"
        _cleanup_dir(povs_dir)
        povs_dir.mkdir(exist_ok=True)

        self._prepare_povs_yaml(harness_name, povs_path, mode)

        if hints_path:
            self._prepare_hints(hints_path)

        logger.info(f'Now launching "{crs_name}"')
        if not self._run_crs_against_povs(crs_name, litellm_api_key, litellm_api_base):
            return False

        logger.info(
            f'The CRS "{crs_name}" has run successfully. Check the "{self.out_dir}" for its outputs.'
        )
        return True

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
