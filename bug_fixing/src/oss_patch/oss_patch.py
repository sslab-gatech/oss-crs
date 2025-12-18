from pathlib import Path
from .crs_builder import OSSPatchCRSBuilder
from .project_builder import OSSPatchProjectBuilder
from .crs_runner import OSSPatchCRSRunner
from .inc_build_checker import IncrementalBuildChecker
from .globals import OSS_PATCH_WORK_DIR, DEFAULT_PROJECT_SOURCE_PATH
from .functions import (
    prepare_docker_cache_builder,
    pull_project_source,
    is_git_repository,
    change_ownership_with_docker,
)
import logging
import shutil

logger = logging.getLogger()


def _copy_oss_fuzz_if_needed(
    dest_oss_fuzz_dir: Path,
    source_oss_fuzz_dir: Path,
    overwrite: bool = False,
) -> bool:
    """Copy OSS-Fuzz to work directory."""
    if dest_oss_fuzz_dir.exists():
        if not overwrite:
            logger.info(
                f"OSS-Fuzz already exists at {dest_oss_fuzz_dir}, skipping copy"
            )
            return True
        logger.info(f"Overwriting existing OSS-Fuzz at {dest_oss_fuzz_dir}")
        shutil.rmtree(dest_oss_fuzz_dir)

    logger.info(f"Copying OSS-Fuzz from {source_oss_fuzz_dir} to {dest_oss_fuzz_dir}")
    dest_oss_fuzz_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_oss_fuzz_dir, dest_oss_fuzz_dir)
    return True


class OSSPatch:
    def __init__(self, project_name: str, crs_name: str | None = None):
        self.crs_name = crs_name
        self.project_name = project_name
        self.work_dir = OSS_PATCH_WORK_DIR / project_name

        if not OSS_PATCH_WORK_DIR.exists():
            OSS_PATCH_WORK_DIR.mkdir()

        if not self.work_dir.exists():
            self.work_dir.mkdir(parents=True)

    def build(
        self,
        oss_fuzz_path: Path,
        project_path: Path | None = None,
        source_path: Path | None = None,
        local_crs: Path | None = None,
        registry_path: Path | None = None,
        overwrite: bool = False,
        use_gitcache: bool = False,
    ) -> bool:
        assert self.crs_name

        if not prepare_docker_cache_builder():
            return False

        crs_builder = OSSPatchCRSBuilder(
            self.crs_name,
            self.work_dir,
            local_crs=local_crs,
            registry_path=registry_path,
            use_gitcache=use_gitcache,
        )
        if not crs_builder.build():
            return False

        # Copy oss-fuzz to work directory
        source_oss_fuzz = oss_fuzz_path.resolve()
        dest_oss_fuzz = OSS_PATCH_WORK_DIR / "oss-fuzz"
        if not _copy_oss_fuzz_if_needed(dest_oss_fuzz, source_oss_fuzz, overwrite):
            logger.error("Failed to copy OSS-Fuzz")
            return False
        oss_fuzz_path = dest_oss_fuzz  # Use copied oss-fuzz from now on

        # Copy project to oss-fuzz/projects/ if project_path provided
        if project_path:
            project_path = Path(project_path).resolve()
            dest_project_path = oss_fuzz_path / "projects" / self.project_name

            if dest_project_path.exists():
                if not overwrite:
                    logger.info(
                        f"Project already exists at {dest_project_path}, skipping copy"
                    )
                else:
                    logger.info(f"Overwriting existing project at {dest_project_path}")
                    shutil.rmtree(dest_project_path)
                    dest_project_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(project_path, dest_project_path)
            else:
                dest_project_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(project_path, dest_project_path)

            project_path = dest_project_path
        else:
            project_path = oss_fuzz_path / "projects" / self.project_name

        if not source_path:
            source_path = DEFAULT_PROJECT_SOURCE_PATH

            if source_path.exists():
                change_ownership_with_docker(source_path)
                shutil.rmtree(source_path)

            pull_project_source(project_path, source_path, use_gitcache)

        assert source_path.exists()
        assert is_git_repository(source_path)

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=project_path,
        )
        if not project_builder.build(source_path):
            return False

        crs_runner = OSSPatchCRSRunner(self.project_name, self.work_dir)
        if not crs_runner.build(oss_fuzz_path, source_path):
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
        assert self.crs_name

        oss_patch_runner = OSSPatchCRSRunner(self.project_name, self.work_dir, out_dir)

        return oss_patch_runner.run_crs_against_povs(
            self.crs_name,
            harness_name,
            povs_dir,
            litellm_api_key,
            litellm_api_base,
            hints_dir,
            "full", # TODO: support delta mode
        )

    # # Testing purpose function
    # def run_pov(
    #     self,
    #     harness_name: str,
    #     pov_path: Path,
    # ) -> tuple[bytes, bytes]:
    #     oss_patch_runner = OSSPatchCRSRunner(
    #         self.project_name, self.work_dir, Path("/tmp/out")
    #     )

    #     return oss_patch_runner.run_pov(harness_name, pov_path)

    # Testing purpose function
    def test_inc_build(self, oss_fuzz_path: Path) -> bool:
        oss_fuzz_path = oss_fuzz_path.resolve()

        return IncrementalBuildChecker(
            oss_fuzz_path, self.project_name, self.work_dir
        ).test()
