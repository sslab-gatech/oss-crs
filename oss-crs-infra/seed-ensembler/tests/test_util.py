"""Tests for utility functions."""

from pathlib import Path

from seed_ensembler.util import (
    check_if_timeouts_scorable_in_options_file,
    compress_str,
)


class TestCheckIfTimeoutsScorableInOptionsFile:
    def test_missing_file_is_scorable(self, tmp_path: Path):
        assert check_if_timeouts_scorable_in_options_file(
            tmp_path / "nonexistent.options"
        )

    def test_timeout_exitcode_zero_not_scorable(self, tmp_path: Path):
        f = tmp_path / "test.options"
        f.write_text("[libfuzzer]\ntimeout_exitcode=0")
        assert not check_if_timeouts_scorable_in_options_file(f)

    def test_other_section_ignored(self, tmp_path: Path):
        f = tmp_path / "test.options"
        f.write_text("[test]\ntimeout_exitcode=0\n\n[libfuzzer]\n")
        assert check_if_timeouts_scorable_in_options_file(f)

    def test_case_insensitive_section(self, tmp_path: Path):
        f = tmp_path / "test.options"
        f.write_text(
            "[test]\ntimeout_exitcode=0\n\n"
            "[  LiBfUzZeR  ]\nmore\nlines\n"
            "timeout_exitcode  =  0\n\n"
            "[another]\nhi\n"
        )
        assert not check_if_timeouts_scorable_in_options_file(f)

    def test_no_libfuzzer_section(self, tmp_path: Path):
        f = tmp_path / "test.options"
        f.write_text("[other]\ntimeout_exitcode=0\n")
        assert check_if_timeouts_scorable_in_options_file(f)

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "test.options"
        f.write_text("")
        assert check_if_timeouts_scorable_in_options_file(f)


class TestCompressStr:
    def test_str_input(self):
        result = compress_str("hello")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_bytes_input(self):
        result = compress_str(b"hello")
        assert isinstance(result, str)

    def test_roundtrip_consistency(self):
        assert compress_str("abc") == compress_str(b"abc")
