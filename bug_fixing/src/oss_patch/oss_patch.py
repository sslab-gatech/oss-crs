from pathlib import Path
from .crs_builder import OSSPatchCRSBuilder
from .project_builder import OSSPatchProjectBuilder
from .crs_runner import OSSPatchCRSRunner
from .globals import (
    OSS_PATCH_WORK_DIR,
)
from .functions import prepare_docker_cache_builder
import logging

logger = logging.getLogger()


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

        if not prepare_docker_cache_builder():
            return False

        crs_builder = OSSPatchCRSBuilder(
            self.crs_name,
            self.work_dir,
            local_crs=local_crs,
        )
        if not crs_builder.build():
            return False

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=project_path,
            source_path=source_path,
        )
        if not project_builder.build():
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

        return oss_patch_runner.run_crs_against_povs(
            harness_name, povs_dir, litellm_api_key, litellm_api_base, hints_dir, "full"
        )

    # Testing purpose function
    def run_pov(self, harness_name: str, pov_path: Path, source_path: Path) -> bool:
        oss_patch_runner = OSSPatchCRSRunner(
            self.crs_name, self.project_name, self.work_dir, Path("/tmp/out")
        )

        oss_patch_runner.run_pov(harness_name, pov_path, source_path)
        return True
