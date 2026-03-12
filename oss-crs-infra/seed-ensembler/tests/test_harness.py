"""Tests for harness data model."""

from pathlib import Path

from seed_ensembler.harness import Harness


class TestHarness:
    def test_creation(self):
        h = Harness(
            name="my_fuzzer",
            path_in_out_dir=Path("/out/my_fuzzer"),
        )
        assert h.name == "my_fuzzer"
        assert h.path_in_out_dir == Path("/out/my_fuzzer")
        assert h.scorable_timeout_duration is None

    def test_with_timeout(self):
        h = Harness(
            name="my_fuzzer",
            path_in_out_dir=Path("/out/my_fuzzer"),
            scorable_timeout_duration=65,
        )
        assert h.scorable_timeout_duration == 65

    def test_to_dict(self):
        h = Harness(
            name="my_fuzzer",
            path_in_out_dir=Path("/out/my_fuzzer"),
            scorable_timeout_duration=65,
        )
        d = h.to_dict()
        assert d == {
            "name": "my_fuzzer",
            "path_in_out_dir": "/out/my_fuzzer",
            "scorable_timeout_duration": 65,
        }

    def test_from_dict(self):
        d = {
            "name": "my_fuzzer",
            "path_in_out_dir": "/out/my_fuzzer",
            "scorable_timeout_duration": 65,
        }
        h = Harness.from_dict(d)
        assert h.name == "my_fuzzer"
        assert h.path_in_out_dir == Path("/out/my_fuzzer")
        assert h.scorable_timeout_duration == 65

    def test_from_dict_without_timeout(self):
        d = {
            "name": "test",
            "path_in_out_dir": "/out/test",
        }
        h = Harness.from_dict(d)
        assert h.scorable_timeout_duration is None

    def test_roundtrip(self):
        h = Harness(
            name="test",
            path_in_out_dir=Path("/out/test"),
            scorable_timeout_duration=25,
        )
        assert Harness.from_dict(h.to_dict()) == h

    def test_frozen(self):
        h = Harness(
            name="test", path_in_out_dir=Path("/out/test")
        )
        try:
            h.name = "other"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass
