"""Tests for pool utility functions."""

from pathlib import Path

from seed_ensembler.libfuzzer_result import (
    LibfuzzerFailure,
    LibfuzzerMergeResult,
    Sanitizer,
)
from seed_ensembler.pool import make_flat_symlink_tree, map_failures_to_inputs


# ---------------------------------------------------------------------------
# make_flat_symlink_tree
# ---------------------------------------------------------------------------


class TestMakeFlatSymlinkTree:
    def test_creates_symlinks(self, tmp_path: Path):
        original = tmp_path / "original"
        original.mkdir()
        (original / "file1.txt").write_text("a")
        (original / "file2.txt").write_text("b")
        (original / "subdir").mkdir()

        new = tmp_path / "new"
        make_flat_symlink_tree(original, new)

        assert (new / "file1.txt").is_symlink()
        assert (new / "file2.txt").is_symlink()
        assert (new / "subdir").is_symlink()
        assert (new / "file1.txt").read_text() == "a"

    def test_as_if_rewriting(self, tmp_path: Path):
        original = tmp_path / "original"
        original.mkdir()
        (original / "file.txt").write_text("content")

        new = tmp_path / "new"
        make_flat_symlink_tree(
            original, new, as_if=Path("/container/seeds")
        )

        link = new / "file.txt"
        assert link.is_symlink()
        target = link.readlink()
        assert str(target).startswith("/container/seeds")

    def test_empty_directory(self, tmp_path: Path):
        original = tmp_path / "original"
        original.mkdir()

        new = tmp_path / "new"
        make_flat_symlink_tree(original, new)

        assert new.is_dir()
        assert list(new.iterdir()) == []

    def test_creates_new_dir(self, tmp_path: Path):
        original = tmp_path / "original"
        original.mkdir()
        (original / "a").write_text("x")

        new = tmp_path / "does_not_exist_yet"
        make_flat_symlink_tree(original, new)
        assert new.is_dir()


# ---------------------------------------------------------------------------
# map_failures_to_inputs
# ---------------------------------------------------------------------------


class TestMapFailuresToInputs:
    def test_input_path_already_set(self, tmp_path: Path):
        batch = tmp_path / "batch"
        batch.mkdir()
        seed = batch / "seed1"
        seed.write_bytes(b"data")

        failure = LibfuzzerFailure(
            input_path=seed,
            output_path=Path("./crash-abc"),
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        result = LibfuzzerMergeResult(
            [failure], return_code=0, execution_time=1.0, was_aborted=False
        )

        mapped = list(map_failures_to_inputs(batch, result))
        assert len(mapped) == 1
        assert mapped[0].input_path == seed

    def test_single_seed_heuristic(self, tmp_path: Path):
        batch = tmp_path / "batch"
        batch.mkdir()
        seed = batch / "seed1"
        seed.write_bytes(b"data")

        failure = LibfuzzerFailure(
            input_path=None,
            output_path=Path("./crash-abc"),
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        result = LibfuzzerMergeResult(
            [failure], return_code=0, execution_time=1.0, was_aborted=False
        )

        mapped = list(map_failures_to_inputs(batch, result))
        assert len(mapped) == 1
        assert mapped[0].input_path == seed

    def test_discards_empty_seed_hash(self, tmp_path: Path):
        batch = tmp_path / "batch"
        batch.mkdir()
        (batch / "seed1").write_bytes(b"a")
        (batch / "seed2").write_bytes(b"b")

        failure = LibfuzzerFailure(
            input_path=None,
            output_path=Path(
                "./crash-da39a3ee5e6b4b0d3255bfef95601890afd80709"
            ),
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        result = LibfuzzerMergeResult(
            [failure], return_code=0, execution_time=1.0, was_aborted=False
        )

        mapped = list(map_failures_to_inputs(batch, result))
        assert len(mapped) == 0

    def test_no_output_path_discarded(self, tmp_path: Path):
        batch = tmp_path / "batch"
        batch.mkdir()
        (batch / "seed1").write_bytes(b"a")
        (batch / "seed2").write_bytes(b"b")

        failure = LibfuzzerFailure(
            input_path=None,
            output_path=None,
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        result = LibfuzzerMergeResult(
            [failure], return_code=0, execution_time=1.0, was_aborted=False
        )

        mapped = list(map_failures_to_inputs(batch, result))
        assert len(mapped) == 0

    def test_sha1_matching(self, tmp_path: Path):
        import hashlib

        batch = tmp_path / "batch"
        batch.mkdir()
        seed_data = b"unique seed content"
        seed = batch / "seed1"
        seed.write_bytes(seed_data)
        (batch / "seed2").write_bytes(b"other")

        sha1 = hashlib.sha1(seed_data).hexdigest()

        # Simulate the output file that libfuzzer wrote.
        output_file = tmp_path / f"crash-{sha1}"
        output_file.write_bytes(seed_data)

        failure = LibfuzzerFailure(
            input_path=None,
            output_path=output_file,
            sanitizer=Sanitizer.ADDRESS,
            summary=b"crash",
        )
        result = LibfuzzerMergeResult(
            [failure], return_code=0, execution_time=1.0, was_aborted=False
        )

        mapped = list(map_failures_to_inputs(batch, result))
        assert len(mapped) == 1
        assert mapped[0].input_path == seed
