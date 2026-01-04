from pathlib import Path
from .crs_builder import OSSPatchCRSBuilder
from .project_builder import OSSPatchProjectBuilder
from .crs_runner import OSSPatchCRSRunner
from .inc_build_checker import IncrementalBuildChecker
from .inc_build_checker import IncrementalSnapshotMaker
from .functions import (
    prepare_docker_cache_builder,
    pull_project_source,
    is_git_repository,
    change_ownership_with_docker,
    get_project_rts_config,
    resolve_rts_config,
    copy_git_repo,
)
from .globals import (
    OSS_PATCH_BASE_WORK_DIR_NAME,
    DEFAULT_PROJECT_SOURCE_NAME,
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

    logger.info(
        f'Copying OSS-Fuzz from "{source_oss_fuzz_dir}" to "{dest_oss_fuzz_dir}"'
    )
    dest_oss_fuzz_dir.parent.mkdir(parents=True, exist_ok=True)
    copy_git_repo(source_oss_fuzz_dir, dest_oss_fuzz_dir)
    return True


class OSSPatch:
    def __init__(
        self,
        project_name: str,
        crs_name: str | None = None,
        work_dir: Path | None = None,
    ):
        self.crs_name = crs_name
        self.project_name = project_name

        # Base work directory (default: cwd/.oss-patch-work)
        self.base_work_dir = (
            work_dir / OSS_PATCH_BASE_WORK_DIR_NAME
            if work_dir
            else Path.cwd() / OSS_PATCH_BASE_WORK_DIR_NAME
        )
        self.oss_fuzz_path = self.base_work_dir / "oss-fuzz"
        self.project_path = self.oss_fuzz_path / "projects" / self.project_name
        self.project_work_dir = self.base_work_dir / project_name
        self.source_path = self.project_work_dir / DEFAULT_PROJECT_SOURCE_NAME

        if not self.base_work_dir.exists():
            self.base_work_dir.mkdir(parents=True)

        if not self.project_work_dir.exists():
            self.project_work_dir.mkdir(parents=True)

    def _prepare_oss_fuzz(self, oss_fuzz_path: Path, overwrite: bool = False) -> bool:
        if not _copy_oss_fuzz_if_needed(
            self.oss_fuzz_path, oss_fuzz_path.resolve(), overwrite
        ):
            logger.error("Failed to copy OSS-Fuzz")
            return False

        return True

    def _prepare_project(
        self,
        custom_project_path: Path | None = None,
        overwrite: bool = False,
    ) -> bool:
        assert self.oss_fuzz_path.exists()

        if custom_project_path:
            custom_project_path = Path(custom_project_path).resolve()

            if self.project_path.exists():
                if not overwrite:
                    logger.info(
                        f"Project already exists at {self.project_path}, skipping copy"
                    )
                    return True

                logger.info(f"Overwriting existing project at {self.project_path}")
                shutil.rmtree(self.project_path)

            self.project_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(custom_project_path, self.project_path)

        return True

    def _prepare_environments(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
        overwrite: bool = False,
        use_gitcache: bool = False,
    ) -> bool:
        logger.info("Preparing environments for running bug-fixing CRS...")

        # Copy oss-fuzz to work directory
        if not self._prepare_oss_fuzz(oss_fuzz_path, overwrite):
            logger.error(f'Failed to prepare OSS-Fuzz using "{oss_fuzz_path}"')
            return False

        if not self._prepare_project(custom_project_path, overwrite):
            logger.error(
                f'Failed to prepare project directory for "{self.project_name}"'
            )
            return False

        if self.source_path.exists():
            change_ownership_with_docker(self.source_path)
            shutil.rmtree(self.source_path)

        if custom_source_path:
            shutil.copytree(custom_source_path, self.source_path)
        else:
            pull_project_source(self.project_path, self.source_path, use_gitcache)

        assert self.source_path.exists()
        assert is_git_repository(
            self.source_path
        )  # FIXME: should support non-git source

        return True

    def build(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
        local_crs: Path | None = None,
        registry_path: Path | None = None,
        overwrite: bool = False,
        use_gitcache: bool = False,
        force_rebuild: bool = False,
        inc_build_enabled: bool = True,
    ) -> bool:
        # TODO: better dectection to skip building
        assert self.crs_name

        if not prepare_docker_cache_builder():
            return False

        crs_builder = OSSPatchCRSBuilder(
            self.crs_name,
            self.project_work_dir,
            local_crs=local_crs,
            registry_path=registry_path,
            use_gitcache=use_gitcache,
            force_rebuild=force_rebuild,
        )
        if not crs_builder.build():
            return False

        self._prepare_environments(
            oss_fuzz_path,
            custom_project_path,
            custom_source_path,
            overwrite,
            use_gitcache,
        )

        project_builder = OSSPatchProjectBuilder(
            self.project_work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=self.project_path,
            force_rebuild=force_rebuild,
        )
        if not project_builder.build(
            self.source_path, inc_build_enabled=inc_build_enabled
        ):
            return False

        crs_runner = OSSPatchCRSRunner(self.project_name, self.project_work_dir)
        if not crs_runner.build(oss_fuzz_path, self.source_path):
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

        oss_patch_runner = OSSPatchCRSRunner(
            self.project_name, self.project_work_dir, out_dir
        )

        return oss_patch_runner.run_crs_against_povs(
            self.crs_name,
            harness_name,
            povs_dir,
            litellm_api_key,
            litellm_api_base,
            hints_dir,
            "full",  # TODO: support delta mode
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
    def test_inc_build(
        self,
        oss_fuzz_path: Path,
        with_rts: bool = False,
        rts_tool: str | None = None,
        source_path: Path | None = None,
        log_file: Path | None = None,
        skip_clone: bool = False,
        skip_baseline: bool = False,
        skip_snapshot: bool = False,
    ) -> bool:
        oss_fuzz_path = oss_fuzz_path.resolve()
        if source_path:
            source_path = source_path.resolve()

        project_path = oss_fuzz_path / "projects" / self.project_name

        # Load project configuration from project.yaml
        project_config = get_project_rts_config(project_path)
        logger.info(f"Project config from project.yaml: {project_config}")

        # Resolve final configuration (CLI overrides project.yaml)
        inc_build_enabled, effective_rts_mode = resolve_rts_config(
            with_rts, rts_tool, project_config
        )

        logger.info(
            f"Final config: inc_build={inc_build_enabled}, rts_mode={effective_rts_mode}"
        )

        # Create the checker
        checker = IncrementalBuildChecker(
            oss_fuzz_path, self.project_name, self.project_work_dir, log_file=log_file
        )

        # Choose workflow based on configuration
        if not inc_build_enabled:
            # Workflow A: No incremental build
            logger.warning(
                "Incremental build is DISABLED for this project (inc_build=false in project.yaml). "
                "Running baseline-only workflow without creating snapshots."
            )
            return checker.test_without_inc_build(skip_clone=skip_clone)
        else:
            # Workflow B or C: Incremental build enabled
            effective_with_rts = effective_rts_mode != "none"

            if effective_with_rts:
                logger.info(
                    f"Running incremental build + RTS workflow (rts_mode={effective_rts_mode})"
                )
            else:
                logger.info("Running incremental build workflow (rts_mode=none)")

            return checker.test(
                with_rts=effective_with_rts,
                rts_tool=effective_rts_mode if effective_with_rts else "jcgeks",
                skip_clone=skip_clone,
                skip_baseline=skip_baseline,
                skip_snapshot=skip_snapshot,
            )

    def make_inc_snapshot(
        self,
        oss_fuzz_path: Path,
        with_rts: bool = False,
        rts_tool: str = "jcgeks",
        push: bool = False,
        force_rebuild: bool = True,
        source_path: Path | None = None,
        log_file: Path | None = None,
        skip_clone: bool = False,
    ) -> bool:
        """Create incremental build snapshot and optionally push to registry.

        This assumes test-inc-build has already been run successfully.

        Args:
            oss_fuzz_path: Path to OSS-Fuzz repository
            with_rts: Enable RTS in snapshot
            rts_tool: RTS tool to use (ekstazi, jcgeks, openclover)
            push: Whether to push snapshot to Docker registry
            force_rebuild: Force rebuild even if image exists (default: True)
            source_path: Path to source code (optional)
            log_file: Path to log file (optional)
            skip_clone: Skip source code cloning
        """
        oss_fuzz_path = oss_fuzz_path.resolve()
        if source_path:
            source_path = source_path.resolve()

        maker = IncrementalSnapshotMaker(
            oss_fuzz_path, self.project_name, self.project_work_dir, log_file=log_file
        )

        return maker.make_snapshot(
            with_rts=with_rts,
            rts_tool=rts_tool,
            push=push,
            force_rebuild=force_rebuild,
            skip_clone=skip_clone,
        )
