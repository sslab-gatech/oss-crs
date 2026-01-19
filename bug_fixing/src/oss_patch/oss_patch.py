from pathlib import Path
from .crs_builder import OSSPatchCRSBuilder
from .crs_context import OSSPatchCRSContext
from .crs_runner import OSSPatchCRSRunner
from .inc_build_checker import IncrementalBuildChecker, IncrementalSnapshotMaker
from .functions import (
    prepare_docker_cache_builder,
    get_project_rts_config,
    resolve_rts_config,
)
from .globals import (
    OSS_PATCH_BASE_WORK_DIR_NAME,
    DEFAULT_PROJECT_SOURCE_NAME,
)
import logging

logger = logging.getLogger()


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

    def _build_crs(
        self,
        crs_name: str,
        local_crs: Path | None = None,
        registry_path: Path | None = None,
        use_gitcache: bool = False,
        force_rebuild: bool = False,
    ) -> bool:
        # TODO: better dectection to skip building
        assert crs_name

        crs_builder = OSSPatchCRSBuilder(
            crs_name,
            local_crs=local_crs,
            registry_path=registry_path,
            use_gitcache=use_gitcache,
            force_rebuild=force_rebuild,
        )

        return crs_builder.build()

    def _build_project(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
        force_rebuild: bool = False,
        inc_build_enabled: bool = True,
    ) -> bool:
        logger.info(f'Preparing project "{self.project_name}"')

        crs_context = OSSPatchCRSContext(self.project_name, self.project_work_dir)
        if not crs_context.build(
            oss_fuzz_path,
            custom_project_path=custom_project_path,
            custom_source_path=custom_source_path,
            force_rebuild=force_rebuild,
            inc_build_enabled=inc_build_enabled,
        ):
            return False

        return True

    def build(
        self,
        oss_fuzz_path: Path,
        custom_project_path: Path | None = None,
        custom_source_path: Path | None = None,
        local_crs: Path | None = None,
        registry_path: Path | None = None,
        use_gitcache: bool = False,
        force_rebuild: bool = False,
        inc_build_enabled: bool = True,
    ) -> bool:
        if not prepare_docker_cache_builder():
            return False

        assert self.crs_name

        if not self._build_crs(
            self.crs_name,
            local_crs=local_crs,
            registry_path=registry_path,
            use_gitcache=use_gitcache,
            force_rebuild=force_rebuild,
        ):
            return False

        if not self._build_project(
            oss_fuzz_path,
            custom_project_path=custom_project_path,
            custom_source_path=custom_source_path,
            force_rebuild=force_rebuild,
            inc_build_enabled=inc_build_enabled,
        ):
            return False

        logger.info(f"CRS building successfully done!")

        return True

    def run_crs(
        self,
        harness_name: str,
        povs_dir: Path,
        litellm_api_key: str,
        litellm_api_base: str,
        diff_path: Path | None,
        out_dir: Path,
        log_dir: Path | None = None,
        cpuset: str | None = None,
        memory: str | None = None,
    ) -> bool:
        assert self.crs_name

        # Default log_dir to out_dir/logs if not specified
        effective_log_dir = log_dir if log_dir else out_dir / "logs"

        crs_context = OSSPatchCRSContext(self.project_name, self.project_work_dir)

        crs_runner = OSSPatchCRSRunner(
            self.project_name,
            crs_context,
            self.project_work_dir,
            log_dir=effective_log_dir,
        )

        return crs_runner.run_crs_against_povs(
            self.crs_name,
            harness_name,
            povs_dir,
            out_dir,
            litellm_api_key,
            litellm_api_base,
            diff_path,
            cpuset=cpuset,
            memory=memory,
        )

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
        push: str | None = None,
        force_rebuild: bool = True,
        source_path: Path | None = None,
        log_file: Path | None = None,
        skip_clone: bool = False,
        force_push: bool = False,
    ) -> bool:
        """Create incremental build snapshot and optionally push to registry.

        This assumes test-inc-build has already been run successfully.
        RTS mode is determined solely from project.yaml 'rts_mode' field.

        Args:
            oss_fuzz_path: Path to OSS-Fuzz repository
            push: Push mode - 'base' (base image only), 'inc' (incremental only),
                  'both' (base and incremental), or None (no push)
            force_rebuild: Force rebuild even if image exists (default: True)
            source_path: Path to source code (optional)
            log_file: Path to log file (optional)
            skip_clone: Skip source code cloning
            force_push: Force push even if images already exist in remote registry
        """
        oss_fuzz_path = oss_fuzz_path.resolve()
        if source_path:
            source_path = source_path.resolve()

        project_path = oss_fuzz_path / "projects" / self.project_name

        # Load project configuration from project.yaml
        project_config = get_project_rts_config(project_path)
        logger.info(f"Project config from project.yaml: {project_config}")

        # Resolve final configuration from project.yaml only (no CLI override)
        _, effective_rts_mode = resolve_rts_config(False, None, project_config)
        rts_enabled = effective_rts_mode != "none"

        logger.info(
            f"Final config: rts_enabled={rts_enabled}, rts_mode={effective_rts_mode}"
        )

        maker = IncrementalSnapshotMaker(
            oss_fuzz_path, self.project_name, self.project_work_dir, log_file=log_file
        )

        return maker.make_snapshot(
            rts_tool=effective_rts_mode if rts_enabled else None,
            push=push,
            force_rebuild=force_rebuild,
            skip_clone=skip_clone,
            force_push=force_push,
        )
