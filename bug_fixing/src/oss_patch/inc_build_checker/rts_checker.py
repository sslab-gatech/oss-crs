"""
RTS (Regression Test Selection) Checker Module.

Ported from RTSTool.py with adaptations for oss-crs integration.
Supports multiple test frameworks:
- Maven (JVM)
- GoogleTest (C/C++)
- CTest (CMake, C/C++)
- Autotools (make check, C/C++)
"""

from pathlib import Path
import logging
import os
import re

logger = logging.getLogger(__name__)


def _convert_time_to_seconds(time_str: str) -> float:
    """Convert various time formats to seconds.

    Supports:
    - Maven format: "12.5s", "2min", "2:30min", "1h", "1:30h"
    - GoogleTest format: "123 ms", "1.5 s"
    - Plain numbers: "12.5"
    """
    time_str = time_str.strip()
    time_second = 0

    # GoogleTest format: "123 ms" or "1500 ms"
    if time_str.endswith("ms"):
        try:
            time_second = float(time_str.rstrip("ms").strip()) / 1000.0
            return time_second
        except ValueError:
            pass

    if time_str.endswith("s") or time_str.endswith("S"):
        time_second = float(time_str.rstrip("sS").strip())
    elif time_str.endswith("min"):
        time_pure = time_str.replace("min", "")
        if ":" in time_pure:
            time_min, time_sec = time_pure.split(":")
            time_second = 60 * float(time_min) + float(time_sec)
        else:
            time_second = float(time_pure) * 60
    elif time_str.endswith("h"):
        time_pure = time_str.replace("h", "")
        if ":" in time_pure:
            time_h, time_min = time_pure.split(":")
            time_second = 3600 * float(time_h) + 60 * float(time_min)
        else:
            time_second = float(time_pure) * 3600
    else:
        try:
            time_second = float(time_str)
        except ValueError:
            time_second = 0
    return time_second


def _parse_rts_markers(log_file: Path) -> tuple[int, int, int]:
    """Parse standardized [RTS] markers from a log file.

    See docs/rts-output-standard.md for format specification.

    Returns:
        (rts_total, rts_selected, rts_excluded) - all 0 if no markers found
    """
    rts_total = 0
    rts_selected = 0
    rts_excluded = 0

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.startswith("[RTS]"):
                    continue

                total_match = re.search(r"\[RTS\]\s*Total:\s*(\d+)", line)
                if total_match:
                    rts_total = int(total_match.group(1))
                    continue

                selected_match = re.search(r"\[RTS\]\s*Selected:\s*(\d+)", line)
                if selected_match:
                    rts_selected = int(selected_match.group(1))
                    continue

                excluded_match = re.search(r"\[RTS\]\s*Excluded:\s*(\d+)", line)
                if excluded_match:
                    rts_excluded = int(excluded_match.group(1))
                    continue
    except Exception as e:
        logger.warning(f"Error parsing [RTS] markers: {e}")

    return rts_total, rts_selected, rts_excluded


def analysis_log(log_file: Path, language: str = "jvm", test_mode: str | None = None):
    """Analyze test log file and extract statistics.

    Dispatches to appropriate parser based on language and test_mode.
    Also checks for standardized [RTS] markers that override framework counts.

    Args:
        log_file: Path to the test log file
        language: Project language ("jvm", "c", "c++")
        test_mode: Test framework ("googletest", "ctest", "maven", etc.)
                   If None, inferred from language (jvm -> maven)

    Returns:
        [test_run, total_time, run_tests_list, rts_selected_set, [failure, error, skip]]
    """
    if not os.path.exists(log_file):
        logger.warning(f"Log file does not exist: {log_file}")
        return [0, 0, [], set(), [0, 0, 0]]

    logger.info(
        f"[INFO] analysing log file: {log_file} (language={language}, test_mode={test_mode})"
    )

    # Dispatch to appropriate parser
    if language == "jvm" or test_mode == "maven":
        result = _analysis_log_maven(log_file)
    elif language in ["c", "c++"]:
        if test_mode == "googletest":
            result = _analysis_log_googletest(log_file)
        elif test_mode == "ctest":
            result = _analysis_log_ctest(log_file)
        elif test_mode == "autotools":
            result = _analysis_log_autotools(log_file)
        else:
            # Default: try autotools parser for misc/unknown (most common for C)
            logger.info(
                f"Unknown test_mode '{test_mode}' for C, trying autotools parser"
            )
            result = _analysis_log_autotools(log_file)
    else:
        logger.warning(f"Unknown language '{language}', falling back to Maven parser")
        result = _analysis_log_maven(log_file)

    # Check for [RTS] markers that override framework-specific counts
    # This handles cases where test.sh operates at finer granularity
    rts_total, rts_selected, rts_excluded = _parse_rts_markers(log_file)
    if rts_selected > 0 or rts_total > 0:
        framework_count = result[0]
        # Use rts_selected for RTS runs, rts_total for baseline runs
        if rts_selected > 0:
            result[0] = rts_selected
        elif rts_total > 0:
            result[0] = rts_total
        logger.info(
            f"[RTS] markers found: total={rts_total} selected={rts_selected} "
            f"excluded={rts_excluded} (framework reported {framework_count})"
        )

    return result


def _analysis_log_maven(log_file: Path):
    """Analyze a Maven test log file and extract statistics.

    Ported from RTSTool.py's analysis_log method.

    Returns:
        [test_run, total_time, run_classes_list, output_class_set, [failure, error, skip]]
    """
    # [test_run, Total time, run classes list, output testClass set, [failure, error, skip]]
    analysis_res = [0, 0, [], set(), [0, 0, 0]]

    if not os.path.exists(log_file):
        logger.warning(f"Log file does not exist: {log_file}")
        return analysis_res

    logger.info(f"[INFO] analysing log file: {log_file}")

    with open(log_file, "r", encoding="utf-8", errors="replace") as log_f:
        lines = log_f.readlines()
        for line_count in range(len(lines)):
            line = lines[line_count]

            if "Results :" in line or "Results:" in line:
                curr_idx = line_count + 2
                found_flag = True
                while True:
                    if curr_idx >= len(lines):
                        found_flag = False
                        break
                    curr_line = lines[curr_idx]
                    if "Tests run: " in curr_line:
                        result_list = (
                            curr_line.replace("\n", "").replace(" ", "").split(",")
                        )
                        break
                    elif "[INFO] ---" in curr_line:
                        found_flag = False
                        break
                    else:
                        curr_idx = curr_idx + 1

                if found_flag == False:
                    continue

                # compute tests run
                test_run_num = result_list[0].split(":")[1]
                analysis_res[0] = analysis_res[0] + int(test_run_num)

                # compute failures
                failures_num = result_list[1].split(":")[1]
                analysis_res[4][0] = analysis_res[4][0] + int(failures_num)

                # compute errors
                errors_num = result_list[2].split(":")[1]
                analysis_res[4][1] = analysis_res[4][1] + int(errors_num)

                # compute skipped
                skipped_str = result_list[3].split(":")[1]
                skipped_num = ""
                for ch in skipped_str:
                    if ch.isdigit():
                        skipped_num += ch
                analysis_res[4][2] = analysis_res[4][2] + int(skipped_num)

            elif "Total time:" in line:
                total_time = _convert_time_to_seconds(
                    line.replace("\n", "").split("Total time:")[1].replace(" ", "")
                )
                analysis_res[1] = total_time

            elif "Running " in line:
                run_class = line.split("Running ")[1].replace("\n", "")
                analysis_res[2].append(run_class)

            elif "[RTS CHECK TAG]" in line:
                output_class = line.replace("[RTS CHECK TAG] ", "").split(" -> ")[0]
                if "$" in output_class:
                    output_class = output_class.split("$")[0]
                analysis_res[3].add(output_class)

    return analysis_res


def _analysis_log_googletest(log_file: Path):
    """Analyze a GoogleTest log file and extract statistics.

    Parses GoogleTest output format:
        [==========] Running 30 tests from 14 test suites.
        [----------] 2 tests from FooTest
        [ RUN      ] FooTest.DoesAbc
        [       OK ] FooTest.DoesAbc (5 ms)
        [ RUN      ] FooTest.DoesXyz
        [  FAILED  ] FooTest.DoesXyz (10 ms)
        [==========] 30 tests from 14 test suites ran. (1234 ms total)
        [  PASSED  ] 28 tests.
        [  FAILED  ] 2 tests, listed below:

    Returns:
        [test_run, total_time, run_tests_list, rts_selected_set, [failure, error, skip]]
    """
    analysis_res = [0, 0, [], set(), [0, 0, 0]]

    with open(log_file, "r", encoding="utf-8", errors="replace") as log_f:
        content = log_f.read()
        lines = content.splitlines()

    # Track individual test runs
    run_tests = []
    failed_tests = []

    for line in lines:
        # Match test run: [ RUN      ] TestSuite.TestName
        if "[ RUN      ]" in line:
            match = re.search(r"\[ RUN\s+\]\s+(\S+)", line)
            if match:
                test_name = match.group(1)
                run_tests.append(test_name)

        # Match failed tests during execution: [  FAILED  ] TestSuite.TestName (X ms)
        # Note: Don't match summary lines (no timing info) or "tests, listed below"
        elif (
            "[  FAILED  ]" in line and "tests, listed below" not in line and "(" in line
        ):
            match = re.search(r"\[\s+FAILED\s+\]\s+(\S+)\s+\(", line)
            if match:
                test_name = match.group(1)
                if test_name not in failed_tests:
                    failed_tests.append(test_name)

        # Match summary: [==========] 30 tests from 14 test suites ran. (1234 ms total)
        elif "[==========]" in line and "ran." in line:
            # Extract total time: (1234 ms total) or (1.5 s total)
            time_match = re.search(r"\((\d+(?:\.\d+)?)\s*(ms|s)\s+total\)", line)
            if time_match:
                time_val = float(time_match.group(1))
                time_unit = time_match.group(2)
                if time_unit == "ms":
                    analysis_res[1] = time_val / 1000.0
                else:
                    analysis_res[1] = time_val

        # Match passed count: [  PASSED  ] 28 tests.
        elif "[  PASSED  ]" in line:
            match = re.search(r"\[\s+PASSED\s+\]\s+(\d+)\s+test", line)
            if match:
                passed_count = int(match.group(1))
                # We'll compute test_run from passed + failed

        # Match failed count from summary: [  FAILED  ] 2 tests, listed below:
        elif "[  FAILED  ]" in line and "tests, listed below" in line:
            match = re.search(r"\[\s+FAILED\s+\]\s+(\d+)\s+test", line)
            if match:
                analysis_res[4][0] = int(match.group(1))  # failures

        # Match RTS selection marker (C RTS format - to be defined)
        elif "[RTS SELECTED]" in line:
            match = re.search(r"\[RTS SELECTED\]\s+(\S+)", line)
            if match:
                analysis_res[3].add(match.group(1))

    # Set results
    analysis_res[0] = len(run_tests)  # total tests run
    analysis_res[2] = run_tests  # list of test names

    # If we didn't get failure count from summary, count from individual failures
    if analysis_res[4][0] == 0 and failed_tests:
        analysis_res[4][0] = len(failed_tests)

    logger.info(
        f"GoogleTest: {analysis_res[0]} tests, {analysis_res[4][0]} failures, {analysis_res[1]:.2f}s"
    )

    return analysis_res


def _analysis_log_ctest(log_file: Path):
    """Analyze a CTest log file and extract statistics.

    Parses CTest output format (both normal and verbose -V/-VV modes):

    Normal mode:
        Test project /path/to/build
            Start  1: TestName1
        1/10 Test  #1: TestName1 ......................   Passed    0.01 sec
            Start  2: TestName2
        2/10 Test  #2: TestName2 ......................***Failed    0.05 sec
        ...
        100% tests passed, 0 tests failed out of 10
        Total Test time (real) =   1.23 sec

    Verbose mode (-V/-VV) adds:
        1: Test command: /path/to/test
        1: Test timeout computed to be: 9.99988e+06
        1: [test output]

    Note: Standardized [RTS] markers are parsed at the top level in analysis_log()
    and override the counts returned here. See docs/rts-output-standard.md.

    Returns:
        [test_run, total_time, run_tests_list, rts_selected_set, [failure, error, skip]]
    """
    analysis_res = [0, 0, [], set(), [0, 0, 0]]

    with open(log_file, "r", encoding="utf-8", errors="replace") as log_f:
        content = log_f.read()
        lines = content.splitlines()

    run_tests = []
    failed_from_individual = 0
    skipped_from_individual = 0

    for line in lines:
        # Match test execution line with various formats:
        # "1/10 Test  #1: TestName ....   Passed    0.01 sec"
        # "1/10 Test #1: TestName .......   Passed    0.01 sec"
        # "2/2 Test #2: test_crash .....***Exception: SegFault  0.01 sec"
        # Status can be: Passed, Failed, ***Failed, ***Not Run, ***Timeout, ***Exception[:...]
        # The status may include additional text (e.g., "Exception: SegFault")
        test_match = re.search(
            r"^\s*\d+/\d+\s+Test\s+#?\d+:\s+(\S+)\s+\.+"
            r"\s*(Passed|\*{0,3}Failed|\*{0,3}Not Run|\*{0,3}Timeout|\*{0,3}Exception\S*(?:\s+\S+)?)"
            r"\s+([\d.]+)\s*sec",
            line,
        )
        if test_match:
            test_name = test_match.group(1)
            status = test_match.group(2)
            run_tests.append(test_name)

            if "Failed" in status or "Timeout" in status or "Exception" in status:
                failed_from_individual += 1
            elif "Not Run" in status:
                skipped_from_individual += 1
            continue

        # Match summary line: "X% tests passed, Y tests failed out of Z"
        # Also handles: "100% tests passed, 0 tests failed out of 10"
        summary_match = re.search(
            r"(\d+)%\s+tests\s+passed,\s+(\d+)\s+tests?\s+failed\s+out\s+of\s+(\d+)",
            line,
        )
        if summary_match:
            failed = int(summary_match.group(2))
            total = int(summary_match.group(3))
            # Use summary value as authoritative
            analysis_res[4][0] = failed
            continue

        # Match total time: "Total Test time (real) =   1.23 sec"
        time_match = re.search(r"Total Test time\s*\(real\)\s*=\s*([\d.]+)\s*sec", line)
        if time_match:
            analysis_res[1] = float(time_match.group(1))
            continue

        # Match RTS selection marker (for C RTS integration)
        if "[RTS SELECTED]" in line:
            rts_match = re.search(r"\[RTS SELECTED\]\s+(\S+)", line)
            if rts_match:
                analysis_res[3].add(rts_match.group(1))

    # Set test count and list
    analysis_res[0] = len(run_tests)
    analysis_res[2] = run_tests

    logger.info(
        f"CTest: {analysis_res[0]} tests, {analysis_res[4][0]} failures, {analysis_res[1]:.2f}s"
    )

    # If no summary line was found, use individual counts
    if analysis_res[4][0] == 0 and failed_from_individual > 0:
        analysis_res[4][0] = failed_from_individual
    if analysis_res[4][2] == 0 and skipped_from_individual > 0:
        analysis_res[4][2] = skipped_from_individual

    return analysis_res


def _analysis_log_autotools(log_file: Path):
    """Analyze an autotools/automake test harness log file and extract statistics.

    Parses automake parallel test harness output format:

    Per-test results (may include ANSI color codes):
        PASS: test_check
        FAIL: test_stream_flags
        SKIP: test_optional
        XFAIL: test_expected_fail
        XPASS: test_unexpected_pass
        ERROR: test_error

    Summary section:
        # TOTAL: 19
        # PASS:  14
        # SKIP:  0
        # XFAIL: 0
        # FAIL:  5
        # XPASS: 0
        # ERROR: 0

    Returns:
        [test_run, total_time, run_tests_list, rts_selected_set, [failure, error, skip]]
    """
    analysis_res = [0, 0, [], set(), [0, 0, 0]]

    with open(log_file, "r", encoding="utf-8", errors="replace") as log_f:
        content = log_f.read()
        lines = content.splitlines()

    run_tests = []
    failed_tests = []
    skipped_tests = []

    # Strip ANSI color codes for parsing
    # Handles both real ANSI codes (\x1b[..m) and literal bracket sequences ([..m)
    ansi_escape = re.compile(r"(\x1b\[[0-9;]*m|\[[0-9;]*m)")

    for line in lines:
        # Remove ANSI color codes
        clean_line = ansi_escape.sub("", line).strip()

        # Match per-test result lines: "PASS: test_name" or "FAIL: test_name"
        test_match = re.match(
            r"^(PASS|FAIL|SKIP|XFAIL|XPASS|ERROR):\s+(\S+)", clean_line
        )
        if test_match:
            status = test_match.group(1)
            test_name = test_match.group(2)
            run_tests.append(test_name)

            if status in ["FAIL", "ERROR"]:
                failed_tests.append(test_name)
            elif status in ["SKIP", "XFAIL"]:
                skipped_tests.append(test_name)
            # PASS and XPASS are considered successful
            continue

        # Match summary lines: "# TOTAL: 19", "# PASS:  14", etc.
        summary_match = re.match(
            r"^#\s*(TOTAL|PASS|FAIL|SKIP|XFAIL|XPASS|ERROR):\s*(\d+)", clean_line
        )
        if summary_match:
            stat_type = summary_match.group(1)
            count = int(summary_match.group(2))

            if stat_type == "TOTAL":
                # Accumulate TOTAL across multiple test blocks
                analysis_res[0] += count
            elif stat_type == "FAIL":
                analysis_res[4][0] += count  # failures (accumulate across blocks)
            elif stat_type == "ERROR":
                analysis_res[4][1] += count  # errors (accumulate across blocks)
            elif stat_type == "SKIP" or stat_type == "XFAIL":
                analysis_res[4][2] += count  # skipped (combine SKIP and XFAIL)
            continue

        # Match RTS selection marker (for C RTS integration)
        if "[RTS SELECTED]" in line:
            rts_match = re.search(r"\[RTS SELECTED\]\s+(\S+)", line)
            if rts_match:
                analysis_res[3].add(rts_match.group(1))

    # Use per-test list
    analysis_res[2] = run_tests

    # If no summary TOTAL found, use count from per-test lines
    if analysis_res[0] == 0 and run_tests:
        analysis_res[0] = len(run_tests)

    # If no summary failure count, use per-test failures
    if analysis_res[4][0] == 0 and failed_tests:
        analysis_res[4][0] = len(failed_tests)

    # If no summary skip count, use per-test skips
    if analysis_res[4][2] == 0 and skipped_tests:
        analysis_res[4][2] = len(skipped_tests)

    # Subtract skipped from test count - "tests run" should be non-skipped only
    analysis_res[0] = analysis_res[0] - analysis_res[4][2]

    logger.info(
        f"Autotools: {analysis_res[0]} tests run (non-skipped), {analysis_res[4][0]} failures, {analysis_res[4][2]} skipped"
    )

    return analysis_res
