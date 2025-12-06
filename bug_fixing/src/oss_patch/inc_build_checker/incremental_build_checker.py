from pathlib import Path
import logging
import subprocess
import time
import shutil
from bug_fixing.src.oss_patch.project_builder import OSSPatchProjectBuilder
from bug_fixing.src.oss_patch.functions import (
    extract_sanitizer_report,
    extract_java_exception_report,
    get_builder_image_name,
    reset_repository,
    change_ownership_with_docker,
    pull_project_source,
)
from bug_fixing.src.oss_patch.globals import DEFAULT_PROJECT_SOURCE_PATH

logger = logging.getLogger(__name__)


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


def _clean_oss_fuzz_out(oss_fuzz_path: Path, project_name: str):
    oss_fuzz_out_path = oss_fuzz_path / "build/out" / project_name
    if oss_fuzz_out_path.exists():
        change_ownership_with_docker(oss_fuzz_out_path)
        subprocess.run(
            f"rm -rf {oss_fuzz_out_path}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class IncrementalBuildChecker:
    def __init__(self, oss_fuzz_path: Path, project_name: str, work_dir: Path):
        self.oss_fuzz_path = oss_fuzz_path
        self.project_name = project_name
        self.project_path = oss_fuzz_path / "projects" / self.project_name
        self.work_dir = work_dir

        self.build_time_without_inc_build: float | None = None
        self.build_time_with_inc_build: float | None = None

        assert self.oss_fuzz_path.exists()
        assert self.project_path.exists()

        self.project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
        )

    def test(self) -> bool:
        logger.info(f"Preparing project source code for {self.project_name}")

        proj_src_path = DEFAULT_PROJECT_SOURCE_PATH
        if proj_src_path.exists():
            change_ownership_with_docker(proj_src_path)
            shutil.rmtree(proj_src_path)
        pull_project_source(self.project_path, proj_src_path)

        pull_project_source(
            self.oss_fuzz_path / "projects" / self.project_name, proj_src_path
        )

        logger.info(
            f'create project builder image: "{get_builder_image_name(self.oss_fuzz_path, self.project_name)}"'
        )

        cur_time = time.time()
        self.project_builder.build(proj_src_path, inc_build_enabled=False)
        image_build_time = time.time() - cur_time
        logger.info(f"Docker image build time: {image_build_time}")

        # if not self._measure_time_without_inc_build():
        #     return False

        logger.info(f"Now taking a snapshot for incremental build")
        if not self.project_builder.take_incremental_build_snapshot(proj_src_path):
            logger.error(f"Taking incremental build snapshot has failed")
            return False

        if not self._measure_time_with_inc_build(proj_src_path):
            return False

        if not self._check_against_povs(proj_src_path):
            return False

        logger.info(f"Incremental build is working correctly for {self.project_name}")

        return True

    # Testing purpose function
    def _run_pov(
        self,
        harness_name: str,
        pov_path: Path,
    ) -> tuple[bytes, bytes]:
        reproduce_command = f"python3 {self.oss_fuzz_path / 'infra/helper.py'} reproduce {self.project_name} {harness_name} {pov_path}"

        # print(runner_command)
        proc = subprocess.run(
            reproduce_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        return (proc.stdout, proc.stderr)

    def _measure_time_without_inc_build(self, source_path: Path) -> bool:
        logger.info("Measuring original build time without incremental build")
        # measure consumed time
        cur_time = time.time()
        build_fail_logs = self.project_builder.build_fuzzers(source_path)
        self.build_time_without_inc_build = time.time() - cur_time

        if build_fail_logs:
            stdout, stderr = build_fail_logs
            logger.error(
                f"`build_fuzzers` failed... check out logs in `/tmp/build.log`"
            )

            with open("/tmp/build.log", "w") as f:
                f.write(stdout.decode())
                f.write(stderr.decode())

            return False

        logger.info(
            f"Build time without incremental build: {self.build_time_without_inc_build}"
        )

        if not reset_repository(source_path):
            logger.error(f"Reset of {source_path} has failed...")
            return False

        return True

    def _measure_time_with_inc_build(self, source_path) -> bool:
        logger.info("Measuring build time with incremental build")

        # measure consumed time
        cur_time = time.time()
        build_fail_logs = self.project_builder.build_fuzzers(source_path)
        self.build_time_with_inc_build = time.time() - cur_time

        if build_fail_logs:
            stdout, stderr = build_fail_logs
            logger.error(
                f"`build_fuzzers` failed... check out logs in `/tmp/build.log`"
            )

            with open("/tmp/build.log", "w") as f:
                f.write(stdout.decode())
                f.write(stderr.decode())

            return False

        logger.info(
            f"Build time with incremental build: {self.build_time_with_inc_build}"
        )

        if not reset_repository(source_path):
            logger.error(f"Reset of {source_path} has failed...")
            return False

        return True

    def _check_against_povs(self, source_path) -> bool:
        aixcc_dir = self.oss_fuzz_path / "projects" / self.project_name / ".aixcc"
        if not aixcc_dir.exists():
            logger.error(
                f'".aixcc" directory does not exist in {self.oss_fuzz_path / "projects" / self.project_name}'
            )
            return False

        # clean out directory of OSS-Fuzz
        _clean_oss_fuzz_out(self.oss_fuzz_path, self.project_name)

        povs_dir = aixcc_dir / "povs"
        if not povs_dir.exists():
            logger.error(f'"{povs_dir}" does not exist.')
            return False

        for pov_per_harness_dir in povs_dir.iterdir():
            harness_name = pov_per_harness_dir.name

            for pov_path in pov_per_harness_dir.iterdir():
                if not reset_repository(source_path):
                    logger.error("Repository reset has failed...")
                    return False

                pov_name = pov_path.name
                logger.info(
                    f'Checking "{pov_name}" for crash with incremental build...'
                )
                if self.project_builder.build_fuzzers(source_path):
                    return False
                stdout, _ = self._run_pov(harness_name, pov_path)

                if not _detect_crash_report(
                    stdout.decode(), self.project_builder.project_lang
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

                _clean_oss_fuzz_out(self.oss_fuzz_path, self.project_name)

                logger.info(f'Building with patch "{patch_path.name}"')
                cur_time = time.time()
                if self.project_builder.build_fuzzers(source_path):
                    return False

                build_time_with_patch = time.time() - cur_time
                logger.info(
                    f'Build time with incremental build and patch ("{patch_path.name}"): {build_time_with_patch}'
                )

                stdout, _ = self._run_pov(harness_name, pov_path)

                if _detect_crash_report(str(stdout), self.project_builder.project_lang):
                    logger.error(
                        f'crash is detected for "{pov_name}" with a patch "{patch_path}"'
                    )
                    return False

                logger.info(f'Incremental build for "{pov_name}" has been validated')

        return True
