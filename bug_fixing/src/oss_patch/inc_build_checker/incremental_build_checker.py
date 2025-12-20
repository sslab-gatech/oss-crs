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
    get_cpv_config,
)

from bug_fixing.src.oss_patch.inc_build_checker.rts_checker import analysis_log

logger = logging.getLogger(__name__)


def _detect_crash_report(stdout: str, language: str, error_token: str | None = None) -> bool:
    if error_token:
        if error_token in stdout:
            return True

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
        self.snapshot_sanitizer: str = "address"  # sanitizer used for snapshot
        self.sanitizer_mismatches: list[tuple[str, str]] = []  # [(pov_name, pov_sanitizer), ...]

        assert self.oss_fuzz_path.exists()
        assert self.project_path.exists()

        self.project_builder = OSSPatchProjectBuilder(
            self.work_dir,
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
            log_file=log_file,
        )

    def _collect_pov_sanitizers(self) -> set[str]:
        """Collect all unique sanitizers required by POVs.

        Returns:
            Set of sanitizer names (e.g., {"address"} or {"address", "memory"})
        """
        sanitizers = set()
        aixcc_dir = self.project_path / ".aixcc"
        povs_dir = aixcc_dir / "povs"

        if not povs_dir.exists():
            logger.warning(f"POVs directory not found: {povs_dir}")
            return {"address"}  # default

        for pov_per_harness_dir in povs_dir.iterdir():
            harness_name = pov_per_harness_dir.name

            for pov_path in pov_per_harness_dir.iterdir():
                pov_name = pov_path.name
                cpv_config = get_cpv_config(self.project_path, harness_name, pov_name)
                if cpv_config:
                    sanitizer = cpv_config.get("sanitizer", "address")
                    sanitizers.add(sanitizer)
                else:
                    sanitizers.add("address")

        if not sanitizers:
            sanitizers.add("address")

        return sanitizers

    def _determine_snapshot_sanitizer(self) -> str:
        """Determine which sanitizer to use for the snapshot.

        Returns:
            Single sanitizer if all POVs use the same one, otherwise "address"
        """
        sanitizers = self._collect_pov_sanitizers()
        logger.info(f"Collected sanitizers from POVs: {sanitizers}")

        if len(sanitizers) == 1:
            sanitizer = sanitizers.pop()
            logger.info(f"All POVs use the same sanitizer: {sanitizer}")
            return sanitizer
        else:
            logger.warning(
                f"Multiple sanitizers required by POVs: {sanitizers}. "
                f"Using 'address' for snapshot (default)."
            )
            return "address"

    def test(self, with_rts: bool = False, rts_tool: str = "jcgeks", skip_clone: bool = False) -> bool:
        """Test incremental build (and optionally RTS) for a project.

        Args:
            with_rts: If True, also run RTS benchmark (JVM projects only)
            rts_tool: RTS tool to use (ekstazi, jcgeks, or openclover)
            skip_clone: If True, use existing source code instead of cloning fresh
        """
        proj_src_path = self.work_dir / "project-src"

        if skip_clone:
            logger.info(f"Skipping source code clone, using existing code at {proj_src_path}")
            if not proj_src_path.exists():
                logger.error(f"Source code path does not exist: {proj_src_path}")
                return False
        else:
            logger.info(f"Preparing project source code for {self.project_name}")
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

        # Determine which sanitizer to use for snapshot
        self.snapshot_sanitizer = self._determine_snapshot_sanitizer()

        # Step 3: Measure build time without inc build
        if not self._measure_time_without_inc_build(proj_src_path):
            return False

        # # Step 3a: Measure baseline test time (before snapshot)
        if not self._measure_baseline_test_time(proj_src_path):
            return False

        # Step 4: Take snapshot (with RTS init if enabled)
        logger.info(f"Now taking a snapshot for incremental build (sanitizer={self.snapshot_sanitizer})")
        if not self.project_builder.take_incremental_build_snapshot(
            proj_src_path, rts_enabled=with_rts, rts_tool=rts_tool, sanitizer=self.snapshot_sanitizer
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

        return (proc.stdout, proc.stderr, proc.returncode)

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
                f.write(stdout.decode(errors='replace'))
                f.write(stderr.decode(errors='replace'))

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
                f.write(stdout.decode(errors='replace'))
                f.write(stderr.decode(errors='replace'))

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
        self.sanitizer_mismatches = []  # Reset mismatches list

        for pov_per_harness_dir in povs_dir.iterdir():
            harness_name = pov_per_harness_dir.name

            for pov_path in pov_per_harness_dir.iterdir():
                change_ownership_with_docker(source_path)
                if not reset_repository(source_path):
                    logger.error("Repository reset has failed...")
                    return False

                pov_name = pov_path.name

                # Get CPV-specific config (sanitizer, error_token) from config.yaml
                # pov_name is the same as cpv_name (e.g., "cpv_0")
                sanitizer = "address"  # default
                error_token = None

                cpv_config = get_cpv_config(self.project_path, harness_name, pov_name)
                if cpv_config:
                    sanitizer = cpv_config.get("sanitizer", "address")
                    error_token = cpv_config.get("error_token")
                    logger.info(f'Using CPV config for {pov_name}: sanitizer={sanitizer}, error_token={error_token}')
                else:
                    logger.info(f'No CPV config found for {harness_name}/{pov_name}, using defaults')

                # Warn if POV sanitizer differs from snapshot sanitizer
                if sanitizer != self.snapshot_sanitizer:
                    logger.warning(
                        f'POV "{pov_name}" requires sanitizer "{sanitizer}" but inc build snapshot '
                        f'was built with "{self.snapshot_sanitizer}". Results may be inaccurate.'
                    )
                    self.sanitizer_mismatches.append((f"{harness_name}/{pov_name}", sanitizer))

                logger.info(
                    f'Checking "{pov_name}" for crash with incremental build (sanitizer={sanitizer})...'
                )
                if self.project_builder.build_fuzzers(source_path, use_inc_image=True, sanitizer=sanitizer):
                    return False
                stdout, _, retcode = self._run_pov(harness_name, pov_path)

                if retcode == 0 or not _detect_crash_report(
                    stdout.decode(errors='replace'), self.project_builder.project_lang, error_token
                ):
                    logger.error(f'crash is not detected for "{pov_name}"')
                    print(stdout.decode(errors='replace'))
                    return False

                patch_path = aixcc_dir / "patches" / harness_name / f"{pov_name}.diff"
                assert patch_path.exists(), patch_path

                # apply a patch
                subprocess.check_call(
                    f"git apply {patch_path}", shell=True, cwd=source_path
                )

                _clean_oss_fuzz_out(self.oss_fuzz_path, self.project_name)

                logger.info(f'Building with patch "{patch_path.name}" (sanitizer={sanitizer})')
                cur_time = time.time()
                if self.project_builder.build_fuzzers(source_path, use_inc_image=True, sanitizer=sanitizer):
                    return False

                build_time_with_patch = time.time() - cur_time
                logger.info(
                    f'Build time with incremental build and patch ("{patch_path.name}"): {build_time_with_patch}'
                )

                stdout, _, retcode = self._run_pov(harness_name, pov_path)

                if retcode != 0 and _detect_crash_report(stdout.decode(errors='replace'), self.project_builder.project_lang, error_token):
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
                    logger.error(f"stdout: {stdout.decode(errors='replace')}")
                    logger.error(f"stderr: {stderr.decode(errors='replace')}")
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
            logger.error(f"stdout: {stdout.decode(errors='replace')}")
            logger.error(f"stderr: {stderr.decode(errors='replace')}")
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

        # Sanitizer configuration
        log_and_collect("[Sanitizer Configuration]")
        log_and_collect(f"  Snapshot built with: {self.snapshot_sanitizer}")
        if self.sanitizer_mismatches:
            log_and_collect(f"  POVs with different sanitizers: {len(self.sanitizer_mismatches)}")
            for pov_name, pov_sanitizer in self.sanitizer_mismatches:
                log_and_collect(f"    - {pov_name}: {pov_sanitizer}")
        else:
            log_and_collect(f"  All POVs use: {self.snapshot_sanitizer}")
        log_and_collect("-" * 60)

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

            # Warnings section (RTS-specific and sanitizer mismatches)
            log_and_collect("-" * 60)
            log_and_collect("[Warnings]")
            has_warning = False

            # Warning: Sanitizer mismatches
            if self.sanitizer_mismatches:
                log_and_collect(
                    f"  WARNING: {len(self.sanitizer_mismatches)} POV(s) require different sanitizer than "
                    f"snapshot (built with '{self.snapshot_sanitizer}'). Results may be inaccurate."
                )
                has_warning = True

            # RTS-specific warnings
            if with_rts:
                # Warning: RTS selected zero tests
                if avg_tests == 0:
                    log_and_collect("  WARNING: RTS selected 0 tests - all tests were skipped!")
                    has_warning = True

                # Warning: RTS did not reduce test count (same as baseline)
                elif abs(avg_tests - baseline_tests) < 0.5:  # tolerance for floating point comparison
                    log_and_collect("  WARNING: RTS did not reduce test count - same as baseline!")
                    has_warning = True

            if not has_warning:
                log_and_collect("  No warnings.")

        log_and_collect("=" * 60)

        # Save summary to file
        summary_file = self.work_dir / "summary.txt"
        with open(summary_file, "w") as f:
            f.write("\n".join(summary_lines) + "\n")
        logger.info(f"Summary saved to: {summary_file}")
