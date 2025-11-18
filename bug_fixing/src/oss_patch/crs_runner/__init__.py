from pathlib import Path
import logging
import shutil
import yaml
import base64
import subprocess
from bug_fixing.src.oss_patch.functions import get_runner_image_name, run_command
from bug_fixing.src.oss_patch.globals import (
    OSS_PATCH_CRS_SYSTEM_IMAGES,
    OSS_PATCH_CRS_DOCKER_ASSETS,
    DEFAULT_DOCKER_ROOT_DIR,
)
from bug_fixing.src.oss_patch.models import CRSMode

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


class OSSPatchCRSRunner:
    def __init__(self, crs_name: str, project_name: str, work_dir: Path, out_dir: Path):
        self.crs_name = crs_name
        self.project_name = project_name
        self.work_dir = work_dir
        self.out_dir = out_dir

        if not out_dir.exists():
            out_dir.mkdir()

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
        self, litellm_api_key: str, litellm_api_base: str
    ) -> bool:
        cmd_parts = [
            "docker",
            "run --rm --privileged",
            "--net=host",  # @NOTE: LiteLLM does not work properly without this option.
            f"-v {OSS_PATCH_CRS_DOCKER_ASSETS}:/crs-docker",
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
                f"-e CRS_NAME={self.crs_name}",
                get_runner_image_name(self.project_name),
                "launch_crs.sh",
            ]
        )

        command = " ".join(cmd_parts)

        try:
            # @TODO: ensure the clean-up of existing docker processes
            subprocess.check_call(command, shell=True)
            # run_command(command, n=10)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"CRS failed: {e}")
            return False

    def _prepare_hints(self, hints_path: Path):
        shutil.copytree(hints_path, self.work_dir / "hints")

    def run_crs_against_povs(
        self,
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

        logger.info(f'Now launching "{self.crs_name}"')
        if not self._run_crs_against_povs(litellm_api_key, litellm_api_base):
            return False

        logger.info(
            f'The CRS "{self.crs_name}" has run successfully. Check the "{self.out_dir}" for its outputs.'
        )
        return True
