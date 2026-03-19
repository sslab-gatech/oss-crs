"""Tests for passthrough copy logic."""

import os
import stat
from pathlib import Path

import pytest

from seed_ensembler.passthrough import (
    _is_safe_name,
    safe_copy,
    copy_new_files,
    sync_non_seed_types,
    sync_seeds_passthrough,
)


class TestIsSafeName:
    def test_normal_name(self):
        assert _is_safe_name("abc123.bin") is True

    def test_hash_name(self):
        assert _is_safe_name("da39a3ee5e6b4b0d3255bfef95601890afd80709") is True

    def test_empty_string(self):
        assert _is_safe_name("") is False

    def test_dotdot(self):
        assert _is_safe_name("..") is False

    def test_dot(self):
        assert _is_safe_name(".") is False

    def test_slash(self):
        assert _is_safe_name("foo/bar") is False


class TestSafeCopy:
    def test_copies_regular_file(self, tmp_path):
        src = tmp_path / "src" / "file.bin"
        src.parent.mkdir()
        src.write_bytes(b"hello")
        dst = tmp_path / "dst" / "file.bin"
        dst.parent.mkdir()

        assert safe_copy(str(src), dst) is True
        assert dst.read_bytes() == b"hello"
        assert stat.S_IMODE(dst.stat().st_mode) == 0o644

    def test_skips_symlink(self, tmp_path):
        real = tmp_path / "real.bin"
        real.write_bytes(b"data")
        link = tmp_path / "link.bin"
        link.symlink_to(real)
        dst = tmp_path / "dst.bin"

        assert safe_copy(str(link), dst) is False
        assert not dst.exists()

    def test_returns_false_on_missing_src(self, tmp_path):
        dst = tmp_path / "dst.bin"
        assert safe_copy("/nonexistent/file", dst) is False

    def test_atomic_write(self, tmp_path):
        """Destination should appear atomically (no partial writes visible)."""
        src = tmp_path / "src.bin"
        src.write_bytes(b"x" * 1024)
        dst = tmp_path / "dst.bin"

        safe_copy(str(src), dst)

        # If atomic, no temp files left behind
        files = list(tmp_path.iterdir())
        names = {f.name for f in files}
        assert names == {"src.bin", "dst.bin"}


class TestCopyNewFiles:
    def test_copies_new_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.bin").write_bytes(b"aaa")
        (src / "b.bin").write_bytes(b"bbb")

        dst = tmp_path / "dst"
        dst.mkdir()

        copied = copy_new_files(src, dst)
        assert set(copied) == {"a.bin", "b.bin"}
        assert (dst / "a.bin").read_bytes() == b"aaa"

    def test_skips_existing(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.bin").write_bytes(b"new")

        dst = tmp_path / "dst"
        dst.mkdir()
        (dst / "a.bin").write_bytes(b"old")

        copied = copy_new_files(src, dst)
        assert copied == []
        assert (dst / "a.bin").read_bytes() == b"old"

    def test_skips_symlinks(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        real = src / "real.bin"
        real.write_bytes(b"data")
        (src / "link.bin").symlink_to(real)

        dst = tmp_path / "dst"
        dst.mkdir()

        copied = copy_new_files(src, dst)
        assert copied == ["real.bin"]

    def test_skips_directories(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "subdir").mkdir()
        (src / "file.bin").write_bytes(b"ok")

        dst = tmp_path / "dst"
        dst.mkdir()

        copied = copy_new_files(src, dst)
        assert copied == ["file.bin"]

    def test_skips_unsafe_names(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "..").mkdir(exist_ok=True)  # dir, will be skipped anyway
        (src / "good.bin").write_bytes(b"ok")

        dst = tmp_path / "dst"
        dst.mkdir()

        copied = copy_new_files(src, dst)
        assert copied == ["good.bin"]


class TestSyncNonSeedTypes:
    def test_copies_povs_and_patches(self, tmp_path):
        submit = tmp_path / "submit"
        exchange = tmp_path / "exchange"
        exchange.mkdir()

        # CRS A submits a pov and a patch
        (submit / "crsA" / "povs").mkdir(parents=True)
        (submit / "crsA" / "povs" / "crash1.bin").write_bytes(b"pov")
        (submit / "crsA" / "patches").mkdir(parents=True)
        (submit / "crsA" / "patches" / "fix1.diff").write_bytes(b"patch")

        created = set()
        warned = set()
        sync_non_seed_types(submit, exchange, created, warned)

        assert (exchange / "povs" / "crash1.bin").read_bytes() == b"pov"
        assert (exchange / "patches" / "fix1.diff").read_bytes() == b"patch"

    def test_skips_seeds(self, tmp_path):
        submit = tmp_path / "submit"
        exchange = tmp_path / "exchange"
        exchange.mkdir()

        (submit / "crsA" / "seeds").mkdir(parents=True)
        (submit / "crsA" / "seeds" / "seed1.bin").write_bytes(b"seed")

        created = set()
        warned = set()
        sync_non_seed_types(submit, exchange, created, warned)

        assert not (exchange / "seeds").exists()

    def test_warns_unknown_types(self, tmp_path):
        submit = tmp_path / "submit"
        exchange = tmp_path / "exchange"
        exchange.mkdir()

        (submit / "crsA" / "garbage").mkdir(parents=True)
        (submit / "crsA" / "garbage" / "file").write_bytes(b"x")

        created = set()
        warned = set()
        sync_non_seed_types(submit, exchange, created, warned)

        assert "garbage" in warned
        assert not (exchange / "garbage").exists()

    def test_handles_missing_submit_root(self, tmp_path):
        exchange = tmp_path / "exchange"
        exchange.mkdir()
        sync_non_seed_types(tmp_path / "nonexistent", exchange, set(), set())
        # should not raise


class TestSyncSeedsPassthrough:
    def test_copies_seeds_from_multiple_crs(self, tmp_path):
        submit = tmp_path / "submit"
        exchange = tmp_path / "exchange"
        exchange.mkdir()

        (submit / "crsA" / "seeds").mkdir(parents=True)
        (submit / "crsA" / "seeds" / "s1").write_bytes(b"a")
        (submit / "crsB" / "seeds").mkdir(parents=True)
        (submit / "crsB" / "seeds" / "s2").write_bytes(b"b")

        created = set()
        sync_seeds_passthrough(submit, exchange, created)

        assert (exchange / "seeds" / "s1").read_bytes() == b"a"
        assert (exchange / "seeds" / "s2").read_bytes() == b"b"

    def test_dedup_by_filename(self, tmp_path):
        submit = tmp_path / "submit"
        exchange = tmp_path / "exchange"
        exchange.mkdir()

        (submit / "crsA" / "seeds").mkdir(parents=True)
        (submit / "crsA" / "seeds" / "same_hash").write_bytes(b"first")
        (submit / "crsB" / "seeds").mkdir(parents=True)
        (submit / "crsB" / "seeds" / "same_hash").write_bytes(b"second")

        created = set()
        sync_seeds_passthrough(submit, exchange, created)

        # First one wins
        assert (exchange / "seeds" / "same_hash").read_bytes() == b"first"

    def test_handles_missing_submit_root(self, tmp_path):
        exchange = tmp_path / "exchange"
        exchange.mkdir()
        sync_seeds_passthrough(tmp_path / "nonexistent", exchange, set())
