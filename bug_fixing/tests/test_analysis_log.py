"""Test analysis_log return structure."""

import pytest
from pathlib import Path
import tempfile

from bug_fixing.src.oss_patch.inc_build_checker.rts_checker import analysis_log


class TestAnalysisLogStructure:
    """Test analysis_log return value structure."""

    def test_return_structure_empty_file(self):
        """Test return structure with empty/nonexistent file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("")
            temp_path = Path(f.name)

        result = analysis_log(temp_path)

        # Expected structure: [test_run, total_time, run_classes_list, output_class_set, [failure, error, skip]]
        assert len(result) == 5, f"Expected 5 elements, got {len(result)}: {result}"

        # Check types
        assert isinstance(result[0], int), (
            f"result[0] (test_run) should be int, got {type(result[0])}"
        )
        assert isinstance(result[1], (int, float)), (
            f"result[1] (total_time) should be number, got {type(result[1])}"
        )
        assert isinstance(result[2], list), (
            f"result[2] (run_classes_list) should be list, got {type(result[2])}"
        )
        assert isinstance(result[3], set), (
            f"result[3] (output_class_set) should be set, got {type(result[3])}"
        )
        assert isinstance(result[4], list), (
            f"result[4] (failures/errors/skips) should be list, got {type(result[4])}"
        )
        assert len(result[4]) == 3, (
            f"result[4] should have 3 elements, got {len(result[4])}"
        )

        temp_path.unlink()

    def test_return_structure_with_maven_output(self):
        """Test return structure with typical Maven test output."""
        maven_log = """
[INFO] Running com.example.TestClass1
[INFO] Tests run: 10, Failures: 1, Errors: 0, Skipped: 2
[INFO] Running com.example.TestClass2
[INFO] Tests run: 5, Failures: 0, Errors: 1, Skipped: 0
[INFO] Results :
[INFO]
[INFO] Tests run: 15, Failures: 1, Errors: 1, Skipped: 2
[INFO] Total time: 45.123 s
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(maven_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path)

        # Check structure
        assert len(result) == 5, f"Expected 5 elements, got {len(result)}: {result}"

        # Check indices
        test_run = result[0]
        total_time = result[1]
        run_classes = result[2]
        output_classes = result[3]
        failures_errors_skips = result[4]

        print(f"test_run: {test_run}")
        print(f"total_time: {total_time}")
        print(f"run_classes: {run_classes}")
        print(f"output_classes: {output_classes}")
        print(f"failures_errors_skips: {failures_errors_skips}")

        # Verify types
        assert isinstance(test_run, int)
        assert isinstance(total_time, (int, float))
        assert isinstance(run_classes, list)
        assert isinstance(output_classes, set)
        assert isinstance(failures_errors_skips, list)
        assert len(failures_errors_skips) == 3

        # Can call len() on run_classes
        assert len(run_classes) >= 0

        temp_path.unlink()

    def test_index_access_patterns(self):
        """Test all index access patterns used in incremental_build_checker.py"""
        # Empty result for testing
        result = [0, 0, [], set(), [0, 0, 0]]

        # These are the access patterns used in _print_test_summary and _calculate_test_averages
        # result[0] - test_run count
        assert result[0] == 0

        # result[1] - total_time
        assert result[1] == 0

        # result[2] - run_classes_list (should support len())
        assert len(result[2]) == 0

        # result[3] - output_class_set (should support len())
        assert len(result[3]) == 0

        # result[4] - [failures, errors, skips]
        failures, errors, skips = result[4]
        assert failures == 0
        assert errors == 0
        assert skips == 0

        # This should NOT work (old index)
        with pytest.raises(IndexError):
            _ = result[5]


class TestGoogleTestParser:
    """Test GoogleTest log parsing."""

    def test_googletest_basic_output(self):
        """Test parsing basic GoogleTest output."""
        gtest_log = """
[==========] Running 5 tests from 2 test suites.
[----------] Global test environment set-up.
[----------] 3 tests from FooTest
[ RUN      ] FooTest.TestOne
[       OK ] FooTest.TestOne (10 ms)
[ RUN      ] FooTest.TestTwo
[       OK ] FooTest.TestTwo (5 ms)
[ RUN      ] FooTest.TestThree
[  FAILED  ] FooTest.TestThree (2 ms)
[----------] 3 tests from FooTest (17 ms total)
[----------] 2 tests from BarTest
[ RUN      ] BarTest.TestA
[       OK ] BarTest.TestA (3 ms)
[ RUN      ] BarTest.TestB
[       OK ] BarTest.TestB (1 ms)
[----------] 2 tests from BarTest (4 ms total)
[----------] Global test environment tear-down
[==========] 5 tests from 2 test suites ran. (25 ms total)
[  PASSED  ] 4 tests.
[  FAILED  ] 1 test, listed below:
[  FAILED  ] FooTest.TestThree

 1 FAILED TEST
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(gtest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="googletest")

        # Check structure
        assert len(result) == 5
        test_run, total_time, run_tests, rts_selected, failures_errors_skips = result

        # Check values
        assert test_run == 5, f"Expected 5 tests run, got {test_run}"
        assert abs(total_time - 0.025) < 0.001, f"Expected ~0.025s, got {total_time}"
        assert len(run_tests) == 5
        assert "FooTest.TestOne" in run_tests
        assert "BarTest.TestB" in run_tests
        assert failures_errors_skips[0] == 1, (
            f"Expected 1 failure, got {failures_errors_skips[0]}"
        )

        temp_path.unlink()

    def test_googletest_all_passing(self):
        """Test GoogleTest output with all tests passing."""
        gtest_log = """
[==========] Running 3 tests from 1 test suite.
[----------] 3 tests from MyTest
[ RUN      ] MyTest.Test1
[       OK ] MyTest.Test1 (100 ms)
[ RUN      ] MyTest.Test2
[       OK ] MyTest.Test2 (50 ms)
[ RUN      ] MyTest.Test3
[       OK ] MyTest.Test3 (25 ms)
[----------] 3 tests from MyTest (175 ms total)
[==========] 3 tests from 1 test suite ran. (180 ms total)
[  PASSED  ] 3 tests.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(gtest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="googletest")

        test_run, total_time, run_tests, _, failures = result

        assert test_run == 3
        assert abs(total_time - 0.180) < 0.001
        assert failures[0] == 0  # no failures

        temp_path.unlink()


class TestCTestParser:
    """Test CTest log parsing."""

    def test_ctest_basic_output(self):
        """Test parsing basic CTest output."""
        ctest_log = """
Test project /path/to/build
    Start  1: TestA
1/3 Test  #1: TestA ......................   Passed    0.05 sec
    Start  2: TestB
2/3 Test  #2: TestB ......................***Failed    0.10 sec
    Start  3: TestC
3/3 Test  #3: TestC ......................   Passed    0.03 sec

67% tests passed, 1 tests failed out of 3

Total Test time (real) =   0.25 sec
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(ctest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="ctest")

        test_run, total_time, run_tests, _, failures = result

        assert test_run == 3
        assert abs(total_time - 0.25) < 0.01
        assert failures[0] == 1  # 1 failure

        temp_path.unlink()

    def test_ctest_timeout(self):
        """Test CTest with timeout status."""
        ctest_log = """
1/3 Test #1: fast_test ........................   Passed    0.01 sec
2/3 Test #2: slow_test ........................***Timeout  60.00 sec
3/3 Test #3: normal_test ......................   Passed    0.05 sec

67% tests passed, 1 tests failed out of 3

Total Test time (real) =  60.10 sec
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(ctest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="ctest")

        assert result[0] == 3  # 3 tests
        assert result[4][0] == 1  # 1 failure (timeout counts as failure)
        assert abs(result[1] - 60.10) < 0.01

        temp_path.unlink()

    def test_ctest_exception(self):
        """Test CTest with exception status including details."""
        ctest_log = """
1/2 Test #1: test_normal ......................   Passed    0.02 sec
2/2 Test #2: test_crash .......................***Exception: SegFault  0.01 sec

50% tests passed, 1 tests failed out of 2

Total Test time (real) =   0.05 sec
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(ctest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="ctest")

        assert result[0] == 2
        assert result[4][0] == 1  # 1 failure

        temp_path.unlink()

    def test_ctest_verbose_mode(self):
        """Test CTest verbose mode output (-V/-VV)."""
        ctest_log = """
UpdateCTestConfiguration from :/path/CMakeFiles/CTestConfiguration.ini
Test project /path/to/build
Constructing a list of tests
Done constructing a list of tests
test 1
    Start 1: MyTest
1: Test command: /path/to/MyTest
1: Test timeout computed to be: 9.99988e+06
1: Running main() from gtest_main.cc
1: [==========] Running 5 tests from 1 test suite.
1: [  PASSED  ] 5 tests.
1/2 Test #1: MyTest ...........................   Passed    0.50 sec
test 2
    Start 2: OtherTest
2: Test command: /path/to/OtherTest
2: Error in test
2/2 Test #2: OtherTest ........................***Failed    0.10 sec

50% tests passed, 1 tests failed out of 2

Total Test time (real) =   0.65 sec
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(ctest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="ctest")

        assert result[0] == 2
        assert result[4][0] == 1
        assert abs(result[1] - 0.65) < 0.01

        temp_path.unlink()

    def test_ctest_not_run_status(self):
        """Test CTest with Not Run status (skipped tests)."""
        ctest_log = """
1/3 Test #1: test1 ............................   Passed    0.01 sec
2/3 Test #2: test2 ............................***Not Run   0.00 sec
3/3 Test #3: test3 ............................   Passed    0.01 sec

67% tests passed, 1 tests failed out of 3

Total Test time (real) =   0.05 sec
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(ctest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="ctest")

        assert result[0] == 3
        assert result[4][0] == 1  # 1 failure from summary
        assert result[4][2] == 1  # 1 skipped (Not Run)

        temp_path.unlink()


class TestLanguageDispatch:
    """Test language-based parser dispatch."""

    def test_jvm_uses_maven_parser(self):
        """Test that JVM language uses Maven parser."""
        maven_log = """
[INFO] Results :
[INFO]
[INFO] Tests run: 10, Failures: 0, Errors: 0, Skipped: 0
[INFO] Total time: 5.0 s
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(maven_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="jvm")

        assert result[0] == 10
        assert result[1] == 5.0

        temp_path.unlink()

    def test_c_with_googletest(self):
        """Test that C language with googletest mode uses GoogleTest parser."""
        gtest_log = """
[==========] Running 2 tests from 1 test suite.
[ RUN      ] Test.One
[       OK ] Test.One (1 ms)
[ RUN      ] Test.Two
[       OK ] Test.Two (1 ms)
[==========] 2 tests from 1 test suite ran. (3 ms total)
[  PASSED  ] 2 tests.
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(gtest_log)
            temp_path = Path(f.name)

        result = analysis_log(temp_path, language="c", test_mode="googletest")

        assert result[0] == 2

        temp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
