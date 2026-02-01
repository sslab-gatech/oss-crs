from pathlib import Path
import logging
from datetime import datetime
import shutil
import yaml
import base64
import secrets
import subprocess
from tempfile import TemporaryDirectory

from bug_fixing.src.oss_patch.crs_context import OSSPatchCRSContext
from bug_fixing.src.oss_patch.functions import (
    run_command,
    get_crs_image_name,
    change_ownership_with_docker,
    copy_directory_with_docker,
    copy_git_repo,
    remove_directory_with_docker
)

logger = logging.getLogger()


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


def _check_povs(povs_path: Path) -> bool:
    if not povs_path.exists():
        logger.error(f"Invalid `--povs` option: the directory {povs_path} not found")
        return False

    if not povs_path.is_dir():
        logger.error(f"Invalid `--povs` option: {povs_path} is not a directory")
        return False

    return True


# def _cleanup_dir(target_dir: Path):
#     if not target_dir.exists():
#         return
#     for item in target_dir.iterdir():
#         if item.is_dir():
#             shutil.rmtree(item)
#         else:
#             item.unlink()


class OSSPatchCRSRunner:
    def __init__(
        self,
        project_name: str,
        crs_context: OSSPatchCRSContext,
        base_path: Path,
        log_dir: Path | None = None,
    ):
        self.project_name = project_name
        self.crs_context = crs_context
        self.oss_fuzz_path = self.crs_context.run_context_dir / "oss-fuzz"
        self.project_path = self.oss_fuzz_path / "projects" / self.project_name
        self.proj_src_path = self.crs_context.run_context_dir / "proj-src"
        self.docker_root_path = self.crs_context.run_context_dir / "docker"
        self.run_base_dir = base_path / f"run-{secrets.token_hex(8)}"
        self.run_base_dir.mkdir()

        self.log_dir = log_dir

        if log_dir and not log_dir.exists():
            log_dir.mkdir(parents=True)

    def _populate_run_base_dir(
        self,
        run_base_dir: Path,
        harness_name: str,
        povs_path: Path,
        diff_path: Path | None,
    ):
        if diff_path:
            mode = "delta"
        else:
            mode = "full"

        povs_dir = run_base_dir / ".crs_inputs" / "povs"
        povs_dir.mkdir(parents=True, exist_ok=True)
        self._prepare_povs_yaml(harness_name, povs_path, mode, povs_dir)

        if diff_path:
            logger.info("diff detected, prepare ref.diff according to the given file.")
            hints_dir = run_base_dir / ".crs_inputs" / "hints"
            hints_dir.mkdir()
            shutil.copy(diff_path, hints_dir / "ref.diff")

        tmp_docker_root_dir = run_base_dir / "docker"
        self._copy_oss_fuzz_and_sources(
            self.oss_fuzz_path, self.proj_src_path, run_base_dir
        )

        logger.info(f'Copying pre-populated docker root to "{tmp_docker_root_dir}"')
        # @TODO: find a way to optimize this copying routine
        if not copy_directory_with_docker(self.docker_root_path, tmp_docker_root_dir):
            logger.error(f'Failed to copy docker root from "{self.docker_root_path}" to "{tmp_docker_root_dir}"')
            raise RuntimeError(f'Failed to copy docker root directory')

    def _prepare_povs_yaml(
        self, harness_name: str, povs_path: Path, mode: str, dst_dir: Path
    ):
        """
        Prepare {pov_id}.yaml files under the [dst_dir]/povs.
        """
        for pov_path in povs_path.iterdir():
            logger.info(f'Creating yaml for "{pov_path.name}"')
            with open((dst_dir / f"{pov_path.name}.yaml"), "w") as f:
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
        self,
        crs_name: str,
        out_dir: Path,
        litellm_api_key: str,
        litellm_api_base: str,
        cpuset: str | None = None,
        memory: str | None = None,
        crs_log_level: str = "info",
    ) -> bool:
        docker_root_path = self.run_base_dir / "docker"
        oss_fuzz_path = self.run_base_dir / "oss-fuzz"
        source_path = self.run_base_dir / "proj-src"
        crs_input_path = self.run_base_dir / ".crs_inputs"

        # Run the CRS container

        if not _check_force_build_in_oss_fuzz(oss_fuzz_path):
            logger.error(
                f"OSS-Fuzz's forciful image build is enabled, which prevents our CRS using incremental build"
            )
            return False

        if not oss_fuzz_path.exists():
            logger.error(
                f"OSS-Fuzz does not exist in run_context ({oss_fuzz_path}). Run `build` command first."
            )
            return False
        if not self.proj_src_path.exists():
            logger.error(
                f"Target project's source does not exist in run_context ({source_path}). Run `build` command first."
            )
            return False

        # Prepare log file if log_dir is set
        log_file = None
        if self.log_dir:
            timestamp = datetime.now().strftime("%y%m%d%H%M%S")
            safe_name = self.project_name.replace("/", "_")
            log_file = self.log_dir / f"crs_run_{safe_name}_{timestamp}.log"
            logger.info(f"CRS execution log will be saved to: {log_file}")

        resource_flags = ""
        if cpuset:
            resource_flags += f"--cpuset-cpus={cpuset} "
        if memory:
            resource_flags += f"--memory={memory} "

        log_level_env = f"-e CRS_LOG_LEVEL={crs_log_level.upper()} "

        command = (
            f"docker run --rm --privileged "
            f"{resource_flags}"
            f"-v {docker_root_path}:/var/lib/docker "
            f"-v {oss_fuzz_path}:/oss-fuzz "
            f"-v {source_path}:/cp-sources "
            f"-v {crs_input_path.resolve()}:/work "
            f"-v {out_dir.resolve()}:/artifacts "
            f"-e LITELLM_API_BASE={litellm_api_base} "
            f"-e LITELLM_API_KEY={litellm_api_key} "
            f"-e TARGET_PROJ={self.project_name} "
            f"-e OSS_FUZZ=/oss-fuzz "
            f"-e PROJECT_SOURCE=/cp-sources "
            f"{log_level_env}"
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

    def run_crs_against_povs(
        self,
        crs_name: str,
        harness_name: str,
        povs_path: Path,
        out_dir: Path,
        litellm_api_key: str,
        litellm_api_base: str,
        diff_path: Path | None,
        cpuset: str | None = None,
        memory: str | None = None,
        crs_log_level: str = "info",
    ) -> bool:
        if not _check_povs(povs_path):
            return False

        try:
            self._populate_run_base_dir(
                self.run_base_dir, harness_name, povs_path, diff_path
            )

            logger.info(f'Now launching "{crs_name}" ("{self.run_base_dir}")')

            if not self._run_crs_against_povs(
                crs_name,
                out_dir,
                litellm_api_key,
                litellm_api_base,
                cpuset=cpuset,
                memory=memory,
                crs_log_level=crs_log_level,
            ):
                return False
        finally:
            change_ownership_with_docker(out_dir)
            remove_directory_with_docker(self.run_base_dir)

        logger.info(
            f'The CRS "{crs_name}" has run successfully. Check the "{out_dir}" for its outputs.'
        )
        return True
