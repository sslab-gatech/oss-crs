from typing import Optional

from pydantic import BaseModel, Field

from ..workdir import WorkDir
from ..target import Target


class ExchangeDir(BaseModel):
    """Exchange directory paths shared across all CRSs."""

    base: Optional[str] = None
    pov: Optional[str] = None
    seed: Optional[str] = None
    bug_candidate: Optional[str] = None
    patch: Optional[str] = None
    diff: Optional[str] = None

    @classmethod
    def from_work_dir(
        cls,
        work_dir: WorkDir,
        target: Target,
        run_id: str,
        sanitizer: str,
    ) -> "ExchangeDir":
        """Create ExchangeDir from work_dir paths."""
        base_path = work_dir.get_exchange_dir(target, run_id, sanitizer, create=False)
        return cls(
            base=str(base_path),
            pov=str(base_path / "povs"),
            seed=str(base_path / "seeds"),
            bug_candidate=str(base_path / "bug-candidates"),
            patch=str(base_path / "patches"),
            diff=str(base_path / "diffs"),
        )


class CRSArtifacts(BaseModel):
    """Artifacts for a single CRS."""

    build: Optional[str] = None
    submit_dir: Optional[str] = None
    pov: Optional[str] = None
    seed: Optional[str] = None
    bug_candidate: Optional[str] = None
    patch: Optional[str] = None
    fetch: Optional[str] = None
    shared: Optional[str] = None

    @classmethod
    def from_work_dir(
        cls,
        work_dir: WorkDir,
        crs_name: str,
        target: Target,
        build_id: str | None,
        run_id: str,
        sanitizer: str,
        exchange_dir_base: str | None = None,
    ) -> "CRSArtifacts":
        """Create CRSArtifacts from work_dir paths."""
        artifacts = cls()

        if build_id:
            build_path = work_dir.get_build_output_dir(crs_name, target, build_id, sanitizer, create=False)
            artifacts.build = str(build_path)

        if target.target_harness:
            submit_path = work_dir.get_submit_dir(crs_name, target, run_id, sanitizer, create=False)
            artifacts.submit_dir = str(submit_path)
            artifacts.pov = str(submit_path / "povs")
            artifacts.seed = str(submit_path / "seeds")
            artifacts.bug_candidate = str(submit_path / "bug-candidates")
            artifacts.patch = str(submit_path / "patches")
            artifacts.fetch = exchange_dir_base
            artifacts.shared = str(work_dir.get_shared_dir(crs_name, target, run_id, sanitizer, create=False))

        return artifacts


class ArtifactsOutput(BaseModel):
    """Complete artifacts output structure."""

    build_id: Optional[str] = None
    run_id: str
    sanitizer: Optional[str] = None
    exchange_dir: Optional[ExchangeDir] = None
    crs: dict[str, CRSArtifacts] = Field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(indent=indent, exclude_none=True)
