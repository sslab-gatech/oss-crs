from pathlib import Path
from contextlib import contextmanager
from .crs_builder import OSSPatchCRSBuilder
from .project_builder import OSSPatchProjectBuilder
from .runner import OSSPatchCRSRunner
from .globals import (
    OSS_PATCH_WORK_DIR,
    OSS_PATCH_BUILD_CONTEXT_DIR,
)
import shutil
import logging

logger = logging.getLogger()


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


class OSSPatch:
    def __init__(self, crs_name: str, project_name: str):
        self.crs_name = crs_name
        self.project_name = project_name
        self.work_dir = OSS_PATCH_WORK_DIR / project_name

        if not OSS_PATCH_WORK_DIR.exists():
            OSS_PATCH_WORK_DIR.mkdir()

        if not self.work_dir.exists():
            self.work_dir.mkdir(parents=True)

    def build_crs(
        self,
        oss_fuzz_path: Path,
        project_path: Path | None = None,
        source_path: Path | None = None,
        local_crs: Path | None = None,
    ) -> bool:
        oss_fuzz_path = oss_fuzz_path.resolve()
        project_path = (
            Path(project_path).resolve()
            if project_path
            else oss_fuzz_path / "projects" / self.project_name
        )

        crs_builder = OSSPatchCRSBuilder(
            self.crs_name,
            self.work_dir,
            local_crs=local_crs,
        )

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=project_path,
            source_path=source_path,
        )
        if not project_builder.validate_arguments():
            return False

        with temp_build_context(OSS_PATCH_BUILD_CONTEXT_DIR):
            if not crs_builder.build_crs_image():
                return False

            if not project_builder.prepare_docker_cache_builder():
                return False

            if not project_builder.copy_oss_fuzz_and_sources(
                OSS_PATCH_BUILD_CONTEXT_DIR
            ):
                return False

            if not project_builder.prepare_docker_volumes():
                return False

            if not project_builder.prepare_project_builder_image():
                return False

            if not project_builder.prepare_runner_image(OSS_PATCH_BUILD_CONTEXT_DIR):
                return False

            if not project_builder.take_incremental_build_snapshot():
                return False

        logger.info(f"CRS building successfully done!")
        return True

    def run_crs(
        self,
        harness_name: str,
        povs_dir: Path,
        litellm_api_key: str,
        litellm_api_base: str,
        hints_dir: Path | None,
        out_dir: Path,
    ) -> bool:
        oss_patch_runner = OSSPatchCRSRunner(
            self.crs_name, self.project_name, self.work_dir, out_dir
        )

        oss_patch_runner.run_crs_against_povs(
            harness_name, povs_dir, litellm_api_key, litellm_api_base, hints_dir, "full"
        )
