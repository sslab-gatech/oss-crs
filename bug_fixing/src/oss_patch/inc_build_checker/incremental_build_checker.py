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

from bug_fixing.src.oss_patch.inc_build_checker.rts_checker import analysis_log

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
            return (extract_java_exception_report(stdout) is not None) or (extract_sanitizer_report(stdout) is not None)
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
    def __init__(self, oss_fuzz_path: Path, project_name: str, work_dir: Path, log_file: Path | None = None):
        self.oss_fuzz_path = oss_fuzz_path
        self.project_name = project_name
        self.project_path = oss_fuzz_path / "projects" / self.project_name
        self.work_dir = work_dir
        self.log_file = log_file

        self.build_time_without_inc_build: float | None = None
        self.build_time_with_inc_build: float | None = None

        assert self.oss_fuzz_path.exists()
        assert self.project_path.exists()

        self.project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
            log_file=log_file,
        )

    def test(self, with_rts: bool = False, rts_tool: str = "jcgeks") -> bool:
        """Test incremental build (and optionally RTS) for a project.

        Args:
            with_rts: If True, also run RTS benchmark (JVM projects only)
            rts_tool: RTS tool to use (ekstazi, jcgeks, or openclover)
        """
        logger.info(f"Preparing project source code for {self.project_name}")

        proj_src_path = self.work_dir / "project-src"
        if proj_src_path.exists():
            change_ownership_with_docker(proj_src_path)
            shutil.rmtree(proj_src_path)
        pull_project_source(self.project_path, proj_src_path)

        logger.info(
            f'create project builder image: "{get_builder_image_name(self.oss_fuzz_path, self.project_name)}"'
        )

        cur_time = time.time()
        self.project_builder.build(proj_src_path, inc_build_enabled=False)
        image_build_time = time.time() - cur_time
        logger.info(f"Docker image build time: {image_build_time}")

        # Step 3: Measure build time without inc build
        if not self._measure_time_without_inc_build(proj_src_path):
            return False

        # # Step 3a: Measure baseline test time (before snapshot)
        if not self._measure_baseline_test_time(proj_src_path):
            return False

        # Step 4: Take snapshot (with RTS init if enabled)
        logger.info(f"Now taking a snapshot for incremental build")
        if not self.project_builder.take_incremental_build_snapshot(
            proj_src_path, rts_enabled=with_rts, rts_tool=rts_tool
        ):
            logger.error(f"Taking incremental build snapshot has failed")
            return False

        # Step 5: Measure build time with inc build
        if not self._measure_time_with_inc_build(proj_src_path):
            return False

        # Step 6: Check against POVs and measure test time with inc build (+ RTS if enabled)
        if not self._check_against_povs(proj_src_path, with_rts=with_rts):
            return False

        logger.info(f"Incremental build is working correctly for {self.project_name}")

        # Step 7: Print summary
        self._print_test_summary(with_rts=with_rts)

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
        change_ownership_with_docker(self.oss_fuzz_path / "out")

        # measure consumed time
        cur_time = time.time()
        build_fail_logs = self.project_builder.build_fuzzers(source_path)
        self.build_time_without_inc_build = time.time() - cur_time

        if build_fail_logs:
            stdout, stderr = build_fail_logs
            build_log_path = self.work_dir / "build.log"
            logger.error(
                f"`build_fuzzers` failed... check out logs in `{build_log_path}`"
            )

            with open(build_log_path, "w") as f:
                f.write(stdout.decode())
                f.write(stderr.decode())

            return False

        logger.info(
            f"Build time without incremental build: {self.build_time_without_inc_build}"
        )

        change_ownership_with_docker(source_path)
        if not reset_repository(source_path):
            logger.error(f"Reset of {source_path} has failed...")
            return False

        return True

    def _measure_time_with_inc_build(self, source_path) -> bool:
        logger.info("Measuring build time with incremental build")
        change_ownership_with_docker(self.oss_fuzz_path / "out")

        # measure consumed time
        cur_time = time.time()
        build_fail_logs = self.project_builder.build_fuzzers(source_path, use_inc_image=True)
        self.build_time_with_inc_build = time.time() - cur_time

        if build_fail_logs:
            stdout, stderr = build_fail_logs
            build_log_path = self.work_dir / "build.log"
            logger.error(
                f"`build_fuzzers` failed... check out logs in `{build_log_path}`"
            )

            with open(build_log_path, "w") as f:
                f.write(stdout.decode())
                f.write(stderr.decode())

            return False

        logger.info(
            f"Build time with incremental build: {self.build_time_with_inc_build}"
        )

        change_ownership_with_docker(source_path)
        if not reset_repository(source_path):
            logger.error(f"Reset of {source_path} has failed...")
            return False

        return True

    def _check_against_povs(self, source_path, with_rts: bool = False) -> bool:
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

        # Initialize test results list for averaging
        self.test_results = []  # List of (pov_name, test_time, stats)

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
                if self.project_builder.build_fuzzers(source_path, use_inc_image=True):
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
                if self.project_builder.build_fuzzers(source_path, use_inc_image=True):
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

                # Measure test time after patch validation (with or without RTS)
                rts_label = "with RTS" if with_rts else "with inc build"
                log_file = self.work_dir / f"test_inc_{harness_name}_{pov_name}.log"
                logger.info(f"Measuring test time ({rts_label}) for {pov_name}...")

                cur_time = time.time()
                result = self.project_builder.run_tests(
                    source_path, rts_enabled=with_rts, log_file=log_file, use_inc_image=True
                )
                test_time = time.time() - cur_time

                if result:
                    stdout, stderr = result
                    logger.error(f"Test execution failed for {pov_name}")
                    logger.error(f"stdout: {stdout.decode()}")
                    logger.error(f"stderr: {stderr.decode()}")
                    return False

                logger.info(f"Test time ({rts_label}, {pov_name}): {test_time:.2f}s")

                # Analyze log
                stats = None
                if log_file.exists():
                    stats = analysis_log(log_file)
                    logger.info(
                        f"Tests run ({pov_name}): {stats[0]}, Total time: {stats[1]:.2f}s"
                    )

                self.test_results.append((f"{harness_name}/{pov_name}", test_time, stats))

        # Calculate averages for summary
        if self.test_results:
            self._calculate_test_averages()

        return True

    def _measure_baseline_test_time(self, source_path: Path) -> bool:
        """Measure baseline test time (before snapshot, no incremental build)."""
        logger.info("Measuring baseline test time (before snapshot)...")

        log_file = self.work_dir / "test_baseline.log"
        cur_time = time.time()
        result = self.project_builder.run_tests(
            source_path, rts_enabled=False, log_file=log_file
        )
        self.baseline_test_time = time.time() - cur_time

        if result:
            stdout, stderr = result
            logger.error("Baseline test execution failed")
            logger.error(f"stdout: {stdout.decode()}")
            logger.error(f"stderr: {stderr.decode()}")
            return False

        logger.info(f"Baseline test time: {self.baseline_test_time:.2f}s")

        # Analyze log
        if log_file.exists():
            stats = analysis_log(log_file)
            self.baseline_test_stats = stats
            logger.info(f"Baseline tests run: {stats[0]}, Total time: {stats[1]:.2f}s")

        # Reset repository
        change_ownership_with_docker(source_path)
        if not reset_repository(source_path):
            logger.error("Repository reset failed")
            return False

        return True

    def _calculate_test_averages(self):
        """Calculate average stats from all test runs."""
        if not self.test_results:
            return

        # Calculate average test time
        total_time = sum(r[1] for r in self.test_results)
        self.avg_test_time = total_time / len(self.test_results)

        # Calculate average stats
        # stats structure: [test_run, total_time, run_classes_list, output_class_set, [failure, error, skip]]
        valid_stats = [r[2] for r in self.test_results if r[2] is not None]

        if valid_stats:
            avg_test_run = sum(s[0] for s in valid_stats) / len(valid_stats)
            avg_total_time = sum(s[1] for s in valid_stats) / len(valid_stats)
            avg_failures = sum(s[4][0] for s in valid_stats) / len(valid_stats)
            avg_errors = sum(s[4][1] for s in valid_stats) / len(valid_stats)
            avg_skips = sum(s[4][2] for s in valid_stats) / len(valid_stats)

            # Combine all run classes
            all_run_classes = []
            for s in valid_stats:
                all_run_classes.extend(s[2])

            self.avg_test_stats = [
                avg_test_run,
                avg_total_time,
                all_run_classes,
                [avg_failures, avg_errors, avg_skips],
            ]

        logger.info(f"Average test time ({len(self.test_results)} runs): {self.avg_test_time:.2f}s")

    def _print_test_summary(self, with_rts: bool = False):
        """Print test benchmark summary and save to file."""
        mode_label = "with RTS" if with_rts else "with Inc Build"

        # Collect summary lines for both logging and file output
        summary_lines = []

        def log_and_collect(msg: str):
            logger.info(msg)
            summary_lines.append(msg)

        log_and_collect("=" * 60)
        log_and_collect(f"Test Benchmark Results ({mode_label}):")
        log_and_collect("=" * 60)

        # Build time comparison
        log_and_collect("[Build Time Comparison]")
        if self.build_time_without_inc_build is not None:
            log_and_collect(f"  Build time (w/o inc build): {self.build_time_without_inc_build:.2f}s")
        if self.build_time_with_inc_build is not None:
            log_and_collect(f"  Build time (w/ inc build): {self.build_time_with_inc_build:.2f}s")
        if self.build_time_without_inc_build and self.build_time_with_inc_build:
            build_speedup = self.build_time_without_inc_build / self.build_time_with_inc_build
            build_saved = self.build_time_without_inc_build - self.build_time_with_inc_build
            build_reduction = (build_saved / self.build_time_without_inc_build) * 100
            log_and_collect(f"  Build time saved: {build_saved:.2f}s ({build_reduction:.1f}% reduction)")
            log_and_collect(f"  Build speedup: {build_speedup:.2f}x")
        log_and_collect("-" * 60)

        # Print per-POV results first
        if hasattr(self, 'test_results') and self.test_results:
            log_and_collect("[Per-POV Results]")
            for pov_name, test_time, stats in self.test_results:
                if stats:
                    log_and_collect(
                        f"  {pov_name}: time={test_time:.2f}s, tests={stats[0]}, "
                        f"failures={stats[4][0]}, errors={stats[4][1]}"
                    )
                else:
                    log_and_collect(f"  {pov_name}: time={test_time:.2f}s (no stats)")
            log_and_collect("-" * 60)

        num_runs = len(self.test_results) if hasattr(self, 'test_results') else 1

        # Test time comparison (averages)
        log_and_collect(f"[Test Time Comparison] (avg over {num_runs} POV(s))")
        log_and_collect(f"  Baseline (before snapshot): {self.baseline_test_time:.2f}s")
        log_and_collect(f"  {mode_label} (avg after snapshot): {self.avg_test_time:.2f}s")
        if self.baseline_test_time > 0 and self.avg_test_time > 0:
            time_saved = self.baseline_test_time - self.avg_test_time
            speedup = self.baseline_test_time / self.avg_test_time
            reduction_pct = (time_saved / self.baseline_test_time) * 100
            log_and_collect(f"  Avg time saved: {time_saved:.2f}s ({reduction_pct:.1f}% reduction)")
            log_and_collect(f"  Avg speedup: {speedup:.2f}x")

        # Test count comparison (from log analysis) - now using averages
        if hasattr(self, 'baseline_test_stats') and hasattr(self, 'avg_test_stats'):
            log_and_collect("-" * 60)
            log_and_collect(f"[Test Count Comparison] (avg over {num_runs} POV(s))")

            baseline_tests = self.baseline_test_stats[0]
            avg_tests = self.avg_test_stats[0]
            tests_diff = baseline_tests - avg_tests

            log_and_collect(f"  Baseline tests run: {baseline_tests:.1f}")
            log_and_collect(f"  {mode_label} tests run (avg): {avg_tests:.1f}")
            if with_rts and tests_diff > 0:
                log_and_collect(f"  Tests skipped (avg): {tests_diff:.1f}")
                if baseline_tests > 0:
                    selection_pct = (avg_tests / baseline_tests) * 100
                    log_and_collect(f"  Avg test selection rate: {selection_pct:.1f}%")

            # Test class comparison
            baseline_classes = len(self.baseline_test_stats[2])
            avg_classes = len(self.avg_test_stats[2])
            log_and_collect(f"  Baseline test classes: {baseline_classes}")
            log_and_collect(f"  {mode_label} test classes (total unique): {avg_classes}")

            # Failure/Error/Skip comparison
            baseline_failures, baseline_errors, baseline_skips = self.baseline_test_stats[4]
            avg_failures, avg_errors, avg_skips = self.avg_test_stats[3]
            log_and_collect("-" * 60)
            log_and_collect("[Test Results]")
            log_and_collect(f"  Baseline - Total Runs: {baseline_tests:.1f}, Failures: {baseline_failures}, Errors: {baseline_errors}, Skipped: {baseline_skips}")
            log_and_collect(f"  {mode_label} (avg) - Total Runs: {avg_tests:.1f}, Failures: {avg_failures:.1f}, Errors: {avg_errors:.1f}, Skipped: {avg_skips:.1f}")

        log_and_collect("=" * 60)

        # Save summary to file
        summary_file = self.work_dir / "summary.txt"
        with open(summary_file, "w") as f:
            f.write("\n".join(summary_lines) + "\n")
        logger.info(f"Summary saved to: {summary_file}")
