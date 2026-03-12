"""Tests for libfuzzer result parsing."""

from pathlib import Path

from seed_ensembler.libfuzzer_result import (
    LibfuzzerFailure,
    LibfuzzerMergeResult,
    LibfuzzerResult,
    LibfuzzerSingleExecResult,
    Sanitizer,
    align_int_lists_by_checkpoints,
)


# ---------------------------------------------------------------------------
# align_int_lists_by_checkpoints
# ---------------------------------------------------------------------------


class TestAlignIntListsByCheckpoints:
    def test_empty(self):
        assert align_int_lists_by_checkpoints([], [[], []]) == [[], []]

    def test_no_checkpoints(self):
        result = align_int_lists_by_checkpoints([], [[3], [1, 5]])
        assert result == [[3, None], [1, 5]]

    def test_single_checkpoint(self):
        result = align_int_lists_by_checkpoints([2], [[3], [1, 5]])
        assert result == [[None, 3], [1, 5]]

    def test_one_empty_list(self):
        result = align_int_lists_by_checkpoints(
            [], [[1, 2, 3, 4], []]
        )
        assert result == [[1, 2, 3, 4], [None, None, None, None]]

    def test_full_example(self):
        result = align_int_lists_by_checkpoints(
            [5, 10, 30, 45],
            [[2, 7, 20, 33, 36, 50], [3, 6, 22, 26, 35, 37, 52]],
        )
        assert result == [
            [2, 7, 20, None, 33, 36, 50],
            [3, 6, 22, 26, 35, 37, 52],
        ]

    def test_symmetric(self):
        """Swapping list order should swap output order."""
        cp = [5, 10, 30, 45]
        a = [2, 7, 20, 33, 36, 50]
        b = [3, 6, 22, 26, 35, 37, 52]
        r1 = align_int_lists_by_checkpoints(cp, [a, b])
        r2 = align_int_lists_by_checkpoints(cp, [b, a])
        assert r1 == [r2[1], r2[0]]


# ---------------------------------------------------------------------------
# find_crash_sanitizer / find_crash_summary
# ---------------------------------------------------------------------------


class TestFindCrashSanitizer:
    def test_address(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"AddressSanitizer: SEGV"
        )
        assert s == Sanitizer.ADDRESS

    def test_memory(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"MemorySanitizer: use-of-uninitialized"
        )
        assert s == Sanitizer.MEMORY

    def test_undefined(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"UndefinedBehaviorSanitizer: signed-integer-overflow"
        )
        assert s == Sanitizer.UNDEFINED

    def test_timeout(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"libFuzzer: timeout in foo"
        )
        assert s == Sanitizer.TIMEOUT

    def test_exited(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"libFuzzer: fuzz target exited"
        )
        assert s == Sanitizer.EXITED

    def test_multiple_returns_none(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"AddressSanitizer and MemorySanitizer"
        )
        assert s is None

    def test_none_found(self):
        s = LibfuzzerResult.find_crash_sanitizer(
            b"no sanitizer here"
        )
        assert s is None

    def test_bounded_region(self):
        data = b"xxAddressSanitizerxx"
        assert (
            LibfuzzerResult.find_crash_sanitizer(data, 0, 5) is None
        )
        assert (
            LibfuzzerResult.find_crash_sanitizer(data, 0, 20)
            == Sanitizer.ADDRESS
        )


class TestFindCrashSummary:
    def test_extracts_summary(self):
        data = b"stuff\nSUMMARY: AddressSanitizer: SEGV foo.c:42\nmore"
        assert (
            LibfuzzerResult.find_crash_summary(data)
            == b"AddressSanitizer: SEGV foo.c:42"
        )

    def test_no_summary(self):
        assert LibfuzzerResult.find_crash_summary(b"no summary") is None

    def test_bounded_region(self):
        data = b"AAA\nSUMMARY: X\nBBB\nSUMMARY: Y\nCCC"
        assert LibfuzzerResult.find_crash_summary(data, 15) == b"Y"


# ---------------------------------------------------------------------------
# LibfuzzerFailure
# ---------------------------------------------------------------------------


class TestLibfuzzerFailure:
    def test_is_timeout(self):
        f = LibfuzzerFailure(None, None, Sanitizer.TIMEOUT, b"t")
        assert f.is_timeout()
        assert not f.is_exit()

    def test_is_exit(self):
        f = LibfuzzerFailure(None, None, Sanitizer.EXITED, b"e")
        assert f.is_exit()
        assert not f.is_timeout()

    def test_neither(self):
        f = LibfuzzerFailure(None, None, Sanitizer.ADDRESS, b"c")
        assert not f.is_timeout()
        assert not f.is_exit()


# ---------------------------------------------------------------------------
# LibfuzzerMergeResult.from_stderr — real test data
# ---------------------------------------------------------------------------


class TestLibfuzzerMergeResultFromStderr:
    def test_output_01_exits(self, test_data_dir: Path):
        data = (test_data_dir / "libfuzzer_output_01.txt").read_bytes()
        result = LibfuzzerMergeResult.from_stderr(
            data, execution_time=0.0
        )

        assert len(result.failures) == 15
        assert result.failures[0] == LibfuzzerFailure(
            Path("examples/00a7f52ad8faa266"),
            Path(
                "./crash-356a192b7913b04c54574d18c28d46e6395428ab"
            ),
            Sanitizer.EXITED,
            b"libFuzzer: fuzz target exited",
        )
        assert result.failures[-2] == LibfuzzerFailure(
            Path("examples/0a07e6b152870cb0"),
            Path(
                "./crash-fa35e192121eabf3dabf9f5ea6abdbcbc107ac3b"
            ),
            Sanitizer.EXITED,
            b"libFuzzer: fuzz target exited",
        )
        assert result.failures[-1] == LibfuzzerFailure(
            None,
            Path(
                "./crash-f1abd670358e036c31296e66b3b66c382ac00812"
            ),
            Sanitizer.EXITED,
            b"libFuzzer: fuzz target exited",
        )

    def test_output_02_address_sanitizer(self, test_data_dir: Path):
        data = (test_data_dir / "libfuzzer_output_02.txt").read_bytes()
        result = LibfuzzerMergeResult.from_stderr(
            data, execution_time=0.0
        )

        assert len(result.failures) == 9
        assert result.failures[0] == LibfuzzerFailure(
            Path("examples/0720061be1c789aa"),
            Path(
                "./crash-0716d9708d321ffb6a00818614779e779925365c"
            ),
            Sanitizer.ADDRESS,
            b"AddressSanitizer: SEGV /src/nginx/src/http/"
            b"ngx_http_parse.c:1277:14 in "
            b"ngx_http_parse_complex_uri",
        )
        assert result.failures[6] == LibfuzzerFailure(
            Path("examples/03c0a42d2c0ca110"),
            Path(
                "./crash-d435a6cdd786300dff204ee7c2ef942d3e9034e2"
            ),
            Sanitizer.ADDRESS,
            b"AddressSanitizer: SEGV string/../sysdeps/x86_64/"
            b"multiarch/memmove-vec-unaligned-erms.S:342 "
            b"in __memcpy_evex_unaligned_erms",
        )

    def test_output_03_timeout(self, test_data_dir: Path):
        data = (test_data_dir / "libfuzzer_output_03.txt").read_bytes()
        result = LibfuzzerMergeResult.from_stderr(
            data, execution_time=0.0
        )

        assert len(result.failures) == 1
        assert result.failures[0] == LibfuzzerFailure(
            Path("dir2/input4.txt"),
            Path(
                "./timeout-"
                "1fa7bc717eb4c4d0c7243aa2805059c4d358c8d2"
            ),
            Sanitizer.TIMEOUT,
            b"libFuzzer: timeout",
        )

    def test_output_04_heap_buffer_overflow(self, test_data_dir: Path):
        data = (test_data_dir / "libfuzzer_output_04.txt").read_bytes()
        result = LibfuzzerMergeResult.from_stderr(
            data, execution_time=0.0
        )

        assert len(result.failures) == 1
        assert result.failures[0] == LibfuzzerFailure(
            None,
            Path(
                "./crash-"
                "f6e1126cedebf23e1463aee73f9df08783640400"
            ),
            Sanitizer.ADDRESS,
            b"AddressSanitizer: heap-buffer-overflow "
            b"/src/nginx/src/core/ngx_string.c:1330:14 "
            b"in ngx_decode_base64_internal",
        )

    def test_empty_stderr(self):
        result = LibfuzzerMergeResult.from_stderr(
            b"", execution_time=0.0
        )
        assert result.failures == []
        assert not result.was_aborted

    def test_aborted(self):
        result = LibfuzzerMergeResult.from_stderr(
            b"", execution_time=1.0, was_aborted=True
        )
        assert result.was_aborted


# ---------------------------------------------------------------------------
# deduplicate / split_by_category
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_removes_duplicates(self):
        f1 = LibfuzzerFailure(
            None, Path("a"), Sanitizer.ADDRESS, b"crash1"
        )
        f2 = LibfuzzerFailure(
            None, Path("b"), Sanitizer.ADDRESS, b"crash1"
        )
        f3 = LibfuzzerFailure(
            None, Path("c"), Sanitizer.TIMEOUT, b"timeout"
        )
        result = LibfuzzerMergeResult([f1, f2, f3], 0, 1.0, False)
        result.deduplicate()

        assert len(result.failures) == 2
        assert result.failures[0] is f1
        assert result.failures[1] is f3

    def test_no_duplicates_unchanged(self):
        f1 = LibfuzzerFailure(
            None, Path("a"), Sanitizer.ADDRESS, b"a"
        )
        f2 = LibfuzzerFailure(
            None, Path("b"), Sanitizer.TIMEOUT, b"b"
        )
        result = LibfuzzerMergeResult([f1, f2], 0, 1.0, False)
        result.deduplicate()
        assert len(result.failures) == 2


class TestSplitByCategory:
    def test_split(self):
        timeout = LibfuzzerFailure(
            None, Path("a"), Sanitizer.TIMEOUT, b"to"
        )
        exit_ = LibfuzzerFailure(
            None, Path("b"), Sanitizer.EXITED, b"ex"
        )
        crash = LibfuzzerFailure(
            None, Path("c"), Sanitizer.ADDRESS, b"cr"
        )
        result = LibfuzzerMergeResult(
            [timeout, exit_, crash], 0, 1.0, False
        )
        t, e, o = result.split_by_category()

        assert t.failures == [timeout]
        assert e.failures == [exit_]
        assert o.failures == [crash]

    def test_empty_result(self):
        result = LibfuzzerMergeResult([], 0, 0.0, False)
        t, e, o = result.split_by_category()
        assert t.failures == []
        assert e.failures == []
        assert o.failures == []


# ---------------------------------------------------------------------------
# LibfuzzerSingleExecResult
# ---------------------------------------------------------------------------


class TestLibfuzzerSingleExecResult:
    def test_crash_detected(self):
        stderr = (
            b"\nSUMMARY: AddressSanitizer: SEGV foo.c:42\n"
            b"Test unit written to ./crash-abc\n"
        )
        result = LibfuzzerSingleExecResult.from_path_and_stderr(
            Path("seed"), stderr, execution_time=0.5
        )
        assert result.failure is not None
        assert result.failure.sanitizer == Sanitizer.ADDRESS

    def test_no_crash(self):
        result = LibfuzzerSingleExecResult.from_path_and_stderr(
            Path("seed"), b"normal output\n", execution_time=0.1
        )
        assert result.failure is None

    def test_aborted(self):
        result = LibfuzzerSingleExecResult.from_path_and_stderr(
            Path("seed"),
            b"",
            execution_time=5.0,
            was_aborted=True,
        )
        assert result.was_aborted
        assert result.failure is None
