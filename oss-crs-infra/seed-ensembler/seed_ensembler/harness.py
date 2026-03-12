"""Harness metadata for seed ensembling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Harness:
    """Metadata for a single fuzz harness binary.

    Attributes:
        name: Unique identifier for the harness (typically the binary filename).
        path_in_out_dir: Path to the harness binary within the build output
            directory (e.g. ``/out/my_fuzzer``).
        scorable_timeout_duration: Seconds a seed must run before being
            considered a scorable timeout, or ``None`` to disable timeout
            scoring.
    """

    name: str
    path_in_out_dir: Path
    scorable_timeout_duration: int | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path_in_out_dir": str(self.path_in_out_dir),
            "scorable_timeout_duration": self.scorable_timeout_duration,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Harness:
        return cls(
            name=data["name"],
            path_in_out_dir=Path(data["path_in_out_dir"]),
            scorable_timeout_duration=data.get("scorable_timeout_duration"),
        )
