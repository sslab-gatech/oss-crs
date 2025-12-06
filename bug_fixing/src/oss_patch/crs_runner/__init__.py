from pathlib import Path
import logging
import shutil
import yaml
import base64
import subprocess
from bug_fixing.src.oss_patch.functions import (
    get_runner_image_name,
    run_command,
    create_docker_volume,
    docker_image_exists,
    load_images_to_volume,
    get_builder_image_name,
    get_base_runner_image_name,
)
from bug_fixing.src.oss_patch.globals import (
    OSS_PATCH_CRS_SYSTEM_IMAGES,
    OSS_PATCH_DOCKER_IMAGES_FOR_CRS,
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_BUILD_CONTEXT_DIR,
    OSS_PATCH_RUNNER_DATA_PATH,
)

from bug_fixing.src.oss_patch.models import CRSMode
from contextlib import contextmanager

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
    for item in target_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


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


class OSSPatchCRSRunner:
    def __init__(self, project_name: str, work_dir: Path, out_dir: Path | None = None):
        self.project_name = project_name
        self.work_dir = work_dir
        self.out_dir = out_dir

        if out_dir and not out_dir.exists():
            out_dir.mkdir()

    def build(
        self,
        oss_fuzz_path: Path,
        source_path: Path,
        context_dir: Path = OSS_PATCH_BUILD_CONTEXT_DIR,
    ) -> bool:
        with temp_build_context(str(context_dir)):
            if not self._prepare_docker_volumes():
                return False

            if not self._copy_oss_fuzz_and_sources(
                oss_fuzz_path, source_path, context_dir
            ):
                return False

            if not self._prepare_runner_image(context_dir):
                return False

            if not load_images_to_volume(
                [
                    get_builder_image_name(oss_fuzz_path, self.project_name),
                    get_base_runner_image_name(oss_fuzz_path),
                ],
                OSS_PATCH_DOCKER_IMAGES_FOR_CRS,
            ):
                logger.error(
                    f'Image loading to "{OSS_PATCH_DOCKER_IMAGES_FOR_CRS}" has failed'
                )
                return False

        return True

    def _prepare_docker_volumes(self) -> bool:
        if not create_docker_volume(OSS_PATCH_DOCKER_IMAGES_FOR_CRS):
            return False
        if not create_docker_volume(OSS_PATCH_CRS_SYSTEM_IMAGES):
            return False
        return True

    def _copy_oss_fuzz_and_sources(
        self,
        oss_fuzz_path: Path,
        source_path: Path,
        dst_dir: Path,
    ) -> bool:
        logger.info("Copying OSS-Fuzz and target project's sources")

        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        dst_dir.mkdir()

        # prepare cp sources
        cp_source_path = (dst_dir / "cp-sources").resolve()
        copied_oss_fuzz_path = (dst_dir / "oss-fuzz").resolve()

        # copy existing CP's source
        shutil.copytree(source_path, cp_source_path)

        # copy the provided OSS-Fuzz source
        shutil.copytree(oss_fuzz_path, copied_oss_fuzz_path)

        # # overwrite the provided project configs
        # shutil.copytree(
        #     self.project_path,
        #     copied_oss_fuzz_path / "projects" / self.project_name,
        #     dirs_exist_ok=True,
        # )

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
        cmd_parts = [
            "docker",
            "run --rm --privileged",
            "--net=host",  # @NOTE: LiteLLM does not work properly without this option.
            f"-v {OSS_PATCH_DOCKER_IMAGES_FOR_CRS}:/crs-docker",
            f"-v {OSS_PATCH_CRS_SYSTEM_IMAGES}:{DEFAULT_DOCKER_ROOT_DIR}",
            f"-v {self.work_dir}:/work",
            f"-v {self.out_dir}:/out",
        ]

        # # Mount harness source to predetermined path if provided
        # if harness_source:
        #     cmd_parts.extend(["-v", f"{harness_source}:/harness-source:ro"])

        cmd_parts.extend(
            [
                f"-e LITELLM_API_KEY={litellm_api_key}",
                f"-e LITELLM_API_BASE={litellm_api_base}",
                f"-e CRS_NAME={crs_name}",
                get_runner_image_name(self.project_name),
                "launch_crs.sh",
            ]
        )

        command = " ".join(cmd_parts)

        try:
            # @TODO: ensure the clean-up of existing docker processes
            # subprocess.check_call(command, shell=True)
            run_command(command, n=10)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"CRS failed: {e}")
            return False

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

        _cleanup_dir(self.work_dir)
        (self.work_dir / "povs").mkdir(exist_ok=True)

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

        runner_command = f"docker run --rm --privileged -v {OSS_PATCH_DOCKER_IMAGES_FOR_CRS}:{DEFAULT_DOCKER_ROOT_DIR} -v {source_path}:/cp-sources {get_runner_image_name(self.project_name)} {build_fuzzers_command}"

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
        #     f"docker run --rm --privileged --net=host "
        #     f"-v {OSS_PATCH_CRS_DOCKER_ASSETS}:{DEFAULT_DOCKER_ROOT_DIR} "
        #     f"-v {source_path}:/cp-sources "
        #     f"-v {pov_path}:/testcase "
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
