"""Tests for libfuzzer handler command construction and mount mapping."""

from pathlib import Path

import pytest

from seed_ensembler.libfuzzer_handler import (
    LibfuzzerEnvironment,
    LibfuzzerInvocation,
    LibfuzzerMounts,
    Timeouts,
)


@pytest.fixture
def sample_mounts() -> LibfuzzerMounts:
    return LibfuzzerMounts(
        out_dir_host=Path("/host/out"),
        out_dir_guest=Path("/out"),
        work_dir_host=Path("/host/work"),
        work_dir_guest=Path("/work"),
        artifact_prefix_host=Path("/host/artifacts"),
        artifact_prefix_guest=Path("/artifact_prefix"),
        seed_dirs_host=[Path("/host/seeds1"), Path("/host/seeds2")],
        seed_dirs_guest=[Path("/seeds_001"), Path("/seeds_002")],
        other_mount_dirs_host=[Path("/host/corpus")],
        other_mount_dirs_guest=[Path("/other_001")],
    )


def _make_invocation(
    mounts: LibfuzzerMounts,
    *,
    overall: float = 30.0,
    per_seed: float = 1.0,
) -> LibfuzzerInvocation:
    """Create an invocation without actually setting up the environment."""
    env = LibfuzzerEnvironment.__new__(LibfuzzerEnvironment)
    env.runner_image = "builder:v1.0"
    env.verbose = False
    return LibfuzzerInvocation(
        env, Timeouts(overall, per_seed), mounts, "my_fuzzer"
    )


# ---------------------------------------------------------------------------
# LibfuzzerMounts
# ---------------------------------------------------------------------------


class TestLibfuzzerMounts:
    def test_host_to_guest(self, sample_mounts: LibfuzzerMounts):
        assert sample_mounts.host_to_guest(
            Path("/host/out/binary")
        ) == Path("/out/binary")
        assert sample_mounts.host_to_guest(
            Path("/host/seeds1/file.bin")
        ) == Path("/seeds_001/file.bin")
        assert sample_mounts.host_to_guest(
            Path("/host/corpus/test")
        ) == Path("/other_001/test")

    def test_guest_to_host(self, sample_mounts: LibfuzzerMounts):
        assert sample_mounts.guest_to_host(
            Path("/out/binary")
        ) == Path("/host/out/binary")
        assert sample_mounts.guest_to_host(
            Path("/seeds_002/file.bin")
        ) == Path("/host/seeds2/file.bin")

    def test_unmapped_path_returned_as_is(
        self, sample_mounts: LibfuzzerMounts
    ):
        p = Path("/unknown/path")
        assert sample_mounts.host_to_guest(p) == p
        assert sample_mounts.guest_to_host(p) == p

    def test_iter_all_pairs(self, sample_mounts: LibfuzzerMounts):
        pairs = list(sample_mounts.iter_all_pairs())
        # out + work + artifact_prefix + 2 seeds + 1 other = 6
        assert len(pairs) == 6
        assert (Path("/host/out"), Path("/out")) in pairs
        assert (
            Path("/host/seeds1"),
            Path("/seeds_001"),
        ) in pairs


# ---------------------------------------------------------------------------
# LibfuzzerInvocation — argument construction
# ---------------------------------------------------------------------------


class TestLibfuzzerInvocationArgs:
    def test_merge_args(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(sample_mounts)
        args = invoc._merge_args()

        assert "-merge=1" in args
        assert "-artifact_prefix=/artifact_prefix/" in args
        assert "-timeout=1" in args
        assert str(Path("/seeds_001")) in args
        assert str(Path("/seeds_002")) in args

    def test_merge_args_no_timeout(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(
            sample_mounts, per_seed=None  # type: ignore[arg-type]
        )
        invoc.timeouts = Timeouts(overall=30.0, per_seed=None)
        args = invoc._merge_args()
        assert not any(a.startswith("-timeout=") for a in args)

    def test_single_exec_args(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(sample_mounts, per_seed=2.0)
        args = invoc._single_exec_args(Path("/seeds_001/test"))

        assert "-artifact_prefix=/artifact_prefix/" in args
        assert "-timeout=2" in args
        assert str(Path("/seeds_001/test")) in args


# ---------------------------------------------------------------------------
# LibfuzzerInvocation — Docker command
# ---------------------------------------------------------------------------


class TestDockerCmdPrefix:
    def test_basic_structure(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(sample_mounts)
        cmd = invoc._docker_cmd_prefix()

        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd
        assert "--shm-size=2g" in cmd
        assert "builder:v1.0" in cmd
        assert str(Path("/out/my_fuzzer")) in cmd

    def test_volume_mounts(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(sample_mounts)
        cmd = invoc._docker_cmd_prefix()

        # Collect all -v arguments.
        v_args = []
        for i, arg in enumerate(cmd):
            if arg == "-v" and i + 1 < len(cmd):
                v_args.append(cmd[i + 1])

        assert any("/host/out:/out" in v for v in v_args)
        assert any("/host/work:/work" in v for v in v_args)
        assert any("/host/seeds1:/seeds_001" in v for v in v_args)
        assert any("/host/corpus:/other_001" in v for v in v_args)

    def test_user_flag(self, sample_mounts: LibfuzzerMounts):
        invoc = _make_invocation(sample_mounts)
        cmd = invoc._docker_cmd_prefix()

        idx = cmd.index("--user")
        user_arg = cmd[idx + 1]
        # Should be "uid:gid" format.
        assert ":" in user_arg


# ---------------------------------------------------------------------------
# LibfuzzerEnvironment.prepare_invocation
# ---------------------------------------------------------------------------


class TestPrepareInvocation:
    def test_creates_mounts(self, tmp_path: Path):
        root = tmp_path / "root"
        artifacts = tmp_path / "artifacts"

        # Create a fake harness directory.
        out_dir = tmp_path / "build" / "out"
        out_dir.mkdir(parents=True)
        harness = out_dir / "my_fuzzer"
        harness.write_bytes(b"\x7fELF")

        seed_dir = tmp_path / "seeds"
        seed_dir.mkdir()
        (seed_dir / "seed1").write_bytes(b"abc")

        env = LibfuzzerEnvironment(
            root, artifacts, "test:latest", verbose=False
        )
        env.set_up()

        invoc = env.prepare_invocation(
            harness,
            [seed_dir, tmp_path / "empty_seeds"],
            [],
            per_seed_timeout=1.0,
            overall_timeout=10.0,
        )

        assert invoc.harness_filename == "my_fuzzer"
        assert invoc.mounts.out_dir_host == root / "out"
        assert (root / "out" / "my_fuzzer").exists()
        assert (root / "work").is_dir()
