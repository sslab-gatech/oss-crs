from pathlib import Path
from .crs_builder import OSSPatchCRSBuilder
from .project_builder import OSSPatchProjectBuilder
from .crs_runner import OSSPatchCRSRunner
from .globals import (
    OSS_PATCH_WORK_DIR,
)
from .functions import (
    prepare_docker_cache_builder,
    extract_sanitizer_report,
    extract_java_exception_report,
)
import logging
import time
import subprocess
from tempfile import TemporaryDirectory

logger = logging.getLogger()


def _detect_crash_report(stdout: str, language: str) -> bool:
    if language in ["c", "c++"]:
        return extract_sanitizer_report(stdout) is not None
    elif language == "jvm":
        if "ERROR: libFuzzer:" in stdout:
            return True
        elif "FuzzerSecurityIssueLow: Stack overflow" in stdout:
            return True
        else:
            return extract_java_exception_report(stdout) is not None
    else:
        return False


class OSSPatch:
    def __init__(self, project_name: str, crs_name: str | None = None):
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
        assert self.crs_name

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
        assert self.crs_name

        oss_patch_runner = OSSPatchCRSRunner(self.project_name, self.work_dir, out_dir)

        return oss_patch_runner.run_crs_against_povs(
            self.crs_name,
            harness_name,
            povs_dir,
            litellm_api_key,
            litellm_api_base,
            hints_dir,
            "full",
        )

    # Testing purpose function
    def run_pov(
        self,
        harness_name: str,
        pov_path: Path,
        source_path: Path,
    ) -> tuple[bytes, bytes]:
        oss_patch_runner = OSSPatchCRSRunner(
            self.project_name, self.work_dir, Path("/tmp/out")
        )

        return oss_patch_runner.run_pov(harness_name, pov_path, source_path)

    def test_povs(self, oss_fuzz_path: Path) -> bool:
        oss_fuzz_path = oss_fuzz_path.resolve()
        project_path = oss_fuzz_path / "projects" / self.project_name

        if not prepare_docker_cache_builder():
            return False

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=project_path,
        )

        project_builder.remove_builder_image()
        project_builder.build(inc_build_enabled=False)

        with TemporaryDirectory() as tmp_dir:
            source_path = Path(tmp_dir) / "target-sources"
            project_builder.prepare_project_sources(source_path)
            aixcc_dir = oss_fuzz_path / "projects" / self.project_name / ".aixcc"
            if not aixcc_dir.exists():
                logger.error(
                    f'".aixcc" directory does not exist in {oss_fuzz_path / "projects" / self.project_name}'
                )
                return False

            povs_dir = aixcc_dir / "povs"

            for pov_per_harness_dir in povs_dir.iterdir():
                harness_name = pov_per_harness_dir.name

                for pov_path in pov_per_harness_dir.iterdir():
                    pov_name = pov_path.name
                    logger.info(f'Testing "{pov_name}" for harness "{harness_name}"')
                    stdout, _ = self.run_pov(harness_name, pov_path, source_path)

                    if not _detect_crash_report(
                        stdout.decode(), project_builder.project_lang
                    ):
                        logger.error(f'crash is not detected for "{pov_name}"')
                        print(stdout.decode())
                        return False

        return True

    # Testing purpose function
    def test_inc_build(self, oss_fuzz_path: Path) -> bool:
        oss_fuzz_path = oss_fuzz_path.resolve()
        project_path = oss_fuzz_path / "projects" / self.project_name

        if not prepare_docker_cache_builder():
            return False

        project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            oss_fuzz_path,
            project_path=project_path,
        )

        project_builder.remove_builder_image()
        project_builder.build(inc_build_enabled=False)

        # measure consumed time
        cur_time = time.time()
        result = project_builder.build_fuzzers()
        build_time_without_inc_build = time.time() - cur_time

        if result:
            stdout, stderr = result
            logger.error(f"build_fuzzers failed...")
            print(str(stdout))
            print(str(stderr))
            return False

        logger.info(
            f"Build time without incremental build: {build_time_without_inc_build}"
        )

        logger.info(f"Now taking a snapshot for incremental build")
        if not project_builder.take_incremental_build_snapshot():
            logger.error(f"taking incremental build snapshot failed")
            return False

        # measure consumed time
        cur_time = time.time()
        result = project_builder.build_fuzzers()
        build_time_with_inc_build = time.time() - cur_time

        logger.info(f"Build time with incremental build: {build_time_with_inc_build}")

        source_path = Path("/tmp/project-src")

        subprocess.run("rm -rf /tmp/project-src", shell=True)

        project_builder.prepare_project_sources(source_path)
        aixcc_dir = oss_fuzz_path / "projects" / self.project_name / ".aixcc"
        if not aixcc_dir.exists():
            logger.error(
                f'".aixcc" directory does not exist in {oss_fuzz_path / "projects" / self.project_name}'
            )
            return False

        povs_dir = aixcc_dir / "povs"

        for pov_per_harness_dir in povs_dir.iterdir():
            harness_name = pov_per_harness_dir.name

            for pov_path in pov_per_harness_dir.iterdir():
                pov_name = pov_path.name
                logger.info(f'Checking "{pov_name}" for crash...')
                stdout, _ = self.run_pov(harness_name, pov_path, source_path)

                if not _detect_crash_report(
                    stdout.decode(), project_builder.project_lang
                ):
                    logger.error(f'crash is not detected for "{pov_name}"')
                    print(stdout.decode())
                    return False

                patch_path = aixcc_dir / "patches" / harness_name / f"{pov_name}.diff"
                assert patch_path.exists(), patch_path

                # apply a patch
                subprocess.check_call(
                    f"git apply {patch_path}", shell=True, cwd=source_path
                )

                cur_time = time.time()
                project_builder.build_fuzzers(source_path)
                build_time_with_patch = time.time() - cur_time
                logger.info(
                    f'Build time with incremental build and patch ("{patch_path.name}"): {build_time_with_patch}'
                )

                stdout, _ = self.run_pov(harness_name, pov_path, source_path)

                if _detect_crash_report(str(stdout), project_builder.project_lang):
                    logger.error(
                        f'crash is detected for "{pov_name}" with a patch "{patch_path}"'
                    )
                    return False

                logger.info(f'Incremental build for "{pov_name}" has been validated')

                # reset the given source code
                subprocess.check_call(
                    f"git reset --hard",
                    shell=True,
                    cwd=source_path,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        logger.info(f"Incremental build is working correctly for {self.project_name}")

        return True
