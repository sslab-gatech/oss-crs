"""Parse libfuzzer merge and single-execution stderr output.

Extracts crash information, sanitizer types, and test-case paths from
libfuzzer's stderr stream.  Supports multi-attempt merge operations with
checkpoint-based alignment of crash data.
"""

from __future__ import annotations

import enum
import logging
import os
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iter_bytes_needles(haystack: bytes, needle: bytes) -> Iterator[int]:
    """Iterate over byte-offsets of *needle* occurrences in *haystack*."""
    idx = -1
    while True:
        idx = haystack.find(needle, idx + 1)
        if idx == -1:
            break
        yield idx


def align_int_lists_by_checkpoints(
    checkpoints: list[int],
    lists: list[list[int]],
) -> list[list[int | None]]:
    """Align sorted integer lists by inserting ``None`` at checkpoint boundaries.

    Given parallel lists of sorted integers that *should* be the same length
    but may not be, insert ``None`` values so that elements between the same
    pair of checkpoints stay aligned.

    Example::

        >>> align_int_lists_by_checkpoints(
        ...     [5, 10, 30, 45],
        ...     [[2, 7, 20, 33, 36, 50], [3, 6, 22, 26, 35, 37, 52]],
        ... )
        [[2, 7, 20, None, 33, 36, 50], [3, 6, 22, 26, 35, 37, 52]]
    """
    # Add a sentinel checkpoint larger than all values.
    checkpoints = list(checkpoints)
    max_ = 0
    for L in lists:
        if L:
            max_ = max(max_, max(L))
    checkpoints.append(max_ + 1)

    new_lists: list[list[int | None]] = [[] for _ in range(len(lists))]
    idxs = [0] * len(lists)

    for checkpoint in checkpoints:
        for i, L in enumerate(lists):
            while idxs[i] < len(L) and L[idxs[i]] < checkpoint:
                new_lists[i].append(L[idxs[i]])
                idxs[i] += 1

        target_length = max(len(L) for L in new_lists)
        for L in new_lists:
            while len(L) < target_length:
                L.append(None)

    return new_lists


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Sanitizer(enum.Enum):
    """Sanitizer identifiers found in libfuzzer stderr output."""

    ADDRESS = b"AddressSanitizer"
    MEMORY = b"MemorySanitizer"
    UNDEFINED = b"UndefinedBehaviorSanitizer"
    EXITED = b"libFuzzer: fuzz target exited"
    TIMEOUT = b"libFuzzer: timeout"


@dataclass
class LibfuzzerFailure:
    """A single crash or timeout detected during a libfuzzer run.

    Attributes:
        input_path: Path to the seed that triggered the failure (may be
            ``None`` if the mapping could not be determined).
        output_path: Path to the crash artifact written by libfuzzer.
        sanitizer: Which sanitizer detected the failure.
        summary: The ``SUMMARY:`` line from libfuzzer's stderr.
    """

    input_path: Path | None
    output_path: Path | None
    sanitizer: Sanitizer | None
    summary: bytes | None

    def is_timeout(self) -> bool:
        return self.sanitizer is Sanitizer.TIMEOUT

    def is_exit(self) -> bool:
        return self.sanitizer is Sanitizer.EXITED


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class LibfuzzerResult:
    """Base class providing shared stderr parsing helpers."""

    @staticmethod
    def find_crash_sanitizer(
        stderr: bytes,
        start: int | None = None,
        end: int | None = None,
    ) -> Sanitizer | None:
        """Return the single matching ``Sanitizer`` in *stderr[start:end]*.

        Returns ``None`` when zero or more than one sanitizer matches.
        """
        found: Sanitizer | None = None
        for s in Sanitizer:
            if stderr.find(s.value, start, end) != -1:
                if found is None:
                    found = s
                else:
                    return None
        return found

    @staticmethod
    def find_crash_summary(
        stderr: bytes,
        start: int | None = None,
        end: int | None = None,
    ) -> bytes | None:
        """Extract the ``SUMMARY:`` payload from *stderr[start:end]*."""
        SUMMARY_START = b"\nSUMMARY: "
        SUMMARY_END = b"\n"

        start_of_summary = stderr.rfind(SUMMARY_START, start, end)
        if start_of_summary != -1:
            start_of_summary += len(SUMMARY_START)
            end_of_summary = stderr.find(
                SUMMARY_END, start_of_summary, end
            )
            if end_of_summary != -1:
                return stderr[start_of_summary:end_of_summary]

        return None


@dataclass
class LibfuzzerSingleExecResult(LibfuzzerResult):
    """Result of running a single seed through a harness."""

    failure: LibfuzzerFailure | None
    return_code: int | None
    execution_time: float
    was_aborted: bool

    @classmethod
    def from_path_and_stderr(
        cls,
        path: Path,
        stderr: bytes,
        *,
        return_code: int | None = None,
        execution_time: float,
        was_aborted: bool = False,
    ) -> LibfuzzerSingleExecResult:
        """Parse a single-execution result from *stderr*."""
        sanitizer = cls.find_crash_sanitizer(stderr)
        summary = cls.find_crash_summary(stderr)

        failure: LibfuzzerFailure | None = None
        if sanitizer is not None and summary is not None:
            failure = LibfuzzerFailure(path, None, sanitizer, summary)

        return cls(failure, return_code, execution_time, was_aborted)


@dataclass
class LibfuzzerMergeResult(LibfuzzerResult):
    """Result of a ``libfuzzer -merge=1`` operation."""

    failures: list[LibfuzzerFailure]
    return_code: int | None
    execution_time: float
    was_aborted: bool

    @classmethod
    def from_stderr(
        cls,
        stderr: bytes,
        *,
        return_code: int | None = None,
        execution_time: float,
        was_aborted: bool = False,
    ) -> LibfuzzerMergeResult:
        """Parse all failures from a merge operation's *stderr*.

        Handles multi-attempt merges by aligning "Test unit written to",
        "SUMMARY:", and "caused a failure at the previous merge step"
        lines using merge-outer attempt checkpoints.
        """
        failures: list[LibfuzzerFailure] = []

        TEST_UNIT_WRITTEN_TO = b"Test unit written to "
        SUMMARY = b"\nSUMMARY: "
        MERGE_OUTER_ATTEMPT = b"\nMERGE-OUTER: attempt "
        MERGE_INNER = b"MERGE-INNER: '"
        FAILURE_AT_PREVIOUS_MERGE_STEP = (
            b"' caused a failure at the previous merge step"
        )

        all_test_unit_starts = list(
            iter_bytes_needles(stderr, TEST_UNIT_WRITTEN_TO)
        )
        all_summary_starts = list(iter_bytes_needles(stderr, SUMMARY))
        all_merge_outer_attempt_starts = list(
            iter_bytes_needles(stderr, MERGE_OUTER_ATTEMPT)
        )
        all_prev_merge_step_failures = list(
            iter_bytes_needles(stderr, FAILURE_AT_PREVIOUS_MERGE_STEP)
        )

        if len(all_test_unit_starts) != len(all_summary_starts):
            log.warning(
                "Test unit / summary lines mismatch (%d vs. %d): %s",
                len(all_test_unit_starts),
                len(all_summary_starts),
                b64encode(stderr).decode("ascii"),
            )

        (
            all_test_unit_starts,
            all_summary_starts,
            all_prev_merge_step_failures,
        ) = align_int_lists_by_checkpoints(
            all_merge_outer_attempt_starts,
            [
                all_test_unit_starts,
                all_summary_starts,
                all_prev_merge_step_failures,
            ],
        )

        for (
            test_unit_start,
            summary_start,
            prev_merge_step_failure,
        ) in zip(
            all_test_unit_starts,
            all_summary_starts,
            all_prev_merge_step_failures,
        ):
            # A "caused a failure at the previous merge step" message
            # applies to the PREVIOUS failure, not the current one.
            input_path = None
            if prev_merge_step_failure is not None:
                merge_inner_start = stderr.rfind(
                    MERGE_INNER, 0, prev_merge_step_failure
                )
                if merge_inner_start != -1:
                    input_path = stderr[
                        merge_inner_start
                        + len(MERGE_INNER) : prev_merge_step_failure
                    ]
                    input_path = Path(os.fsdecode(input_path))

            if input_path is not None and failures:
                failures[-1].input_path = input_path

            # Extract output path from "Test unit written to" line.
            output_path = None
            if test_unit_start is not None:
                output_path_end = stderr.find(
                    b"\n", test_unit_start + len(TEST_UNIT_WRITTEN_TO)
                )
                if output_path_end != -1:
                    output_path = stderr[
                        test_unit_start
                        + len(TEST_UNIT_WRITTEN_TO) : output_path_end
                    ]
                    output_path = Path(os.fsdecode(output_path))

            # Extract sanitizer + summary from "SUMMARY:" line.
            sanitizer = summary = None
            if summary_start is not None:
                summary_line_end = stderr.find(
                    b"\n", summary_start + len(SUMMARY)
                )
                if summary_line_end != -1:
                    sanitizer = cls.find_crash_sanitizer(
                        stderr, summary_start, summary_line_end + 1
                    )
                    summary = stderr[
                        summary_start + len(SUMMARY) : summary_line_end
                    ]

            if output_path is None:
                continue

            failures.append(
                LibfuzzerFailure(None, output_path, sanitizer, summary)
            )

        return cls(failures, return_code, execution_time, was_aborted)

    def deduplicate(self) -> None:
        """Remove duplicate failures (by sanitizer + summary)."""
        seen: set[tuple[Sanitizer | None, bytes | None]] = set()
        unique: list[LibfuzzerFailure] = []
        for failure in self.failures:
            key = (failure.sanitizer, failure.summary)
            if key not in seen:
                seen.add(key)
                unique.append(failure)
        self.failures = unique

    def split_by_category(
        self,
    ) -> tuple[
        LibfuzzerMergeResult, LibfuzzerMergeResult, LibfuzzerMergeResult
    ]:
        """Split into ``(timeouts, exits, other_crashes)``."""
        timeouts: list[LibfuzzerFailure] = []
        exits: list[LibfuzzerFailure] = []
        others: list[LibfuzzerFailure] = []

        for failure in self.failures:
            if failure.is_timeout():
                timeouts.append(failure)
            elif failure.is_exit():
                exits.append(failure)
            else:
                others.append(failure)

        def _make(
            failures: list[LibfuzzerFailure],
        ) -> LibfuzzerMergeResult:
            return LibfuzzerMergeResult(
                failures,
                self.return_code,
                self.execution_time,
                self.was_aborted,
            )

        return _make(timeouts), _make(exits), _make(others)
