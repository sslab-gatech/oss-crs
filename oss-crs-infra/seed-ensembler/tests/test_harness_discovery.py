"""Tests for harness discovery."""

import os
import stat
from pathlib import Path

import pytest

from seed_ensembler.harness_discovery import (
    _is_harness_candidate,
    discover_harnesses,
)


class TestIsHarnessCandidate:
    def test_elf_binary(self, tmp_path):
        f = tmp_path / "my_fuzzer"
        # ELF magic + enough bytes
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)
        assert _is_harness_candidate(f) is True

    def test_shell_script(self, tmp_path):
        f = tmp_path / "JazzerFuzzer"
        f.write_text("#!/bin/bash\nexec jazzer_driver $@\n")
        f.chmod(0o755)
        assert _is_harness_candidate(f) is True

    def test_non_executable(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o644)
        assert _is_harness_candidate(f) is False

    def test_jar_file(self, tmp_path):
        f = tmp_path / "lib.jar"
        f.write_bytes(b"PK\x03\x04")
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_options_file(self, tmp_path):
        f = tmp_path / "fuzzer.options"
        f.write_text("[libfuzzer]\ntimeout=10\n")
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_jazzer_driver_skipped(self, tmp_path):
        f = tmp_path / "jazzer_driver"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_hidden_file(self, tmp_path):
        f = tmp_path / ".hidden"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_directory(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        assert _is_harness_candidate(d) is False

    def test_seed_corpus_zip(self, tmp_path):
        f = tmp_path / "fuzzer_seed_corpus.zip"
        f.write_bytes(b"PK\x03\x04")
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_shared_lib(self, tmp_path):
        f = tmp_path / "libstuff.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)
        assert _is_harness_candidate(f) is False

    def test_class_file(self, tmp_path):
        f = tmp_path / "MyFuzzer.class"
        f.write_bytes(b"\xca\xfe\xba\xbe")
        f.chmod(0o644)
        assert _is_harness_candidate(f) is False


class TestDiscoverHarnesses:
    def test_finds_elf_in_build_subdir(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        f = build_dir / "my_fuzzer"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)

        harnesses = discover_harnesses(tmp_path)
        assert len(harnesses) == 1
        assert harnesses[0].name == "my_fuzzer"
        assert harnesses[0].path_in_out_dir == f

    def test_finds_shell_script_harness(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        f = build_dir / "CompressTarFuzzer"
        f.write_text("#!/bin/bash\nexec $this_dir/jazzer_driver $@\n")
        f.chmod(0o755)
        # jazzer_driver should be skipped
        jd = build_dir / "jazzer_driver"
        jd.write_bytes(b"\x7fELF" + b"\x00" * 100)
        jd.chmod(0o755)

        harnesses = discover_harnesses(tmp_path)
        names = {h.name for h in harnesses}
        assert "CompressTarFuzzer" in names
        assert "jazzer_driver" not in names

    def test_options_file_timeout(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        f = build_dir / "fuzzer"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        f.chmod(0o755)
        opts = build_dir / "fuzzer.options"
        opts.write_text("[libfuzzer]\ntimeout_exitcode=0\n")

        harnesses = discover_harnesses(tmp_path)
        assert len(harnesses) == 1
        # timeout_exitcode=0 means timeouts are NOT scorable
        assert harnesses[0].scorable_timeout_duration is None

    def test_empty_dir(self, tmp_path):
        harnesses = discover_harnesses(tmp_path)
        assert harnesses == []

    def test_nonexistent_dir(self, tmp_path):
        harnesses = discover_harnesses(tmp_path / "nope")
        assert harnesses == []

    def test_skips_non_harness_files(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "data.jar").write_bytes(b"PK")
        (build_dir / "fuzzer.options").write_text("[libfuzzer]\n")
        (build_dir / "corpus.zip").write_bytes(b"PK")

        harnesses = discover_harnesses(tmp_path)
        assert harnesses == []
