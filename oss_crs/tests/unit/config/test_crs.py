"""Unit tests for oss_crs.src.config.crs module."""

import pytest
from pydantic import ValidationError
from oss_crs.src.config.crs import (
    PreparePhase,
    BuildConfig,
    CRSRunPhaseModule,
    _validate_dockerfile_value,
)


class TestDockerfileValidation:
    """Tests for dockerfile path validation."""

    def test_accepts_framework_module_reference(self):
        """oss-crs-infra: prefix identifies framework-provided modules."""
        result = _validate_dockerfile_value("oss-crs-infra:default-builder")
        assert result == "oss-crs-infra:default-builder"

    def test_accepts_dockerfile_paths(self):
        """Standard Dockerfile paths should be accepted."""
        assert _validate_dockerfile_value("Dockerfile") == "Dockerfile"
        assert _validate_dockerfile_value("build.Dockerfile") == "build.Dockerfile"

    def test_rejects_non_dockerfile_paths(self):
        """Non-Dockerfile files should be rejected."""
        with pytest.raises(ValueError, match="valid Dockerfile"):
            _validate_dockerfile_value("config.yaml")


class TestPreparePhase:
    """Tests for PreparePhase - HCL file configuration."""

    def test_requires_hcl_extension(self):
        """HCL file must have .hcl extension."""
        phase = PreparePhase(hcl="build.hcl")
        assert phase.hcl == "build.hcl"

        with pytest.raises(ValidationError, match=".hcl extension"):
            PreparePhase(hcl="build.yaml")


class TestBuildConfig:
    """Tests for BuildConfig - build step configuration."""

    def test_outputs_cannot_escape_directory(self):
        """Output paths cannot contain '..' to prevent directory traversal."""
        with pytest.raises(ValidationError, match="cannot contain"):
            BuildConfig(name="build", dockerfile="Dockerfile", outputs=["../escape"])

    def test_snapshot_flag(self):
        """snapshot flag controls whether build creates a snapshot image."""
        normal = BuildConfig(name="build", dockerfile="Dockerfile")
        assert normal.snapshot is False

        snapshot = BuildConfig(name="build", dockerfile="Dockerfile", snapshot=True)
        assert snapshot.snapshot is True


class TestCRSRunPhaseModule:
    """Tests for CRSRunPhaseModule - run phase service configuration."""

    def test_snapshot_module_can_omit_dockerfile(self):
        """Snapshot-based modules don't need their own Dockerfile."""
        module = CRSRunPhaseModule(run_snapshot=True)
        assert module.dockerfile is None

    def test_non_snapshot_requires_dockerfile(self):
        """Non-snapshot modules must specify a Dockerfile."""
        with pytest.raises(ValidationError, match="dockerfile is required"):
            CRSRunPhaseModule(run_snapshot=False)
