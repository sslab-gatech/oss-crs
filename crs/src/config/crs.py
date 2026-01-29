from pathlib import Path
from enum import Enum
from typing import Optional, Set

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .target import TargetLangauge, TargetSanitizer, TargetArch


class PreparePhase(BaseModel):
    """Configuration for the prepare phase."""

    hcl: str

    @field_validator("hcl")
    @classmethod
    def validate_hcl(cls, v: str) -> str:
        if not v.endswith(".hcl"):
            raise ValueError("hcl file must have .hcl extension")
        return v


class BuildConfig(BaseModel):
    """Configuration for a single build step."""

    dockerfile: str
    outputs: list[str]
    additional_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("dockerfile")
    @classmethod
    def validate_dockerfile(cls, v: str) -> str:
        if not v.endswith(".Dockerfile") and "Dockerfile" not in v:
            raise ValueError("must be a valid Dockerfile path")
        return v


class TargetBuildPhase(BaseModel):
    """Configuration for the target build phase."""

    builds: dict[str, BuildConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def parse_builds(cls, data: dict) -> dict:
        """Parse builds from raw dictionary data."""
        return {"builds": {k: v for k, v in data.items()}}


class CRSRunPhase(BaseModel):
    """Configuration for the CRS run phase."""

    docker_compose: str
    additional_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("docker_compose")
    @classmethod
    def validate_docker_compose(cls, v: str) -> str:
        if not v.endswith((".yaml", ".yml")):
            raise ValueError("docker_compose must be a yaml file")
        return v

    @field_validator("additional_env")
    @classmethod
    def validate_additional_env(cls, v: dict[str, str]) -> dict[str, str]:
        # TODO: check for valid env var names/values if needed (do not allow pre-defined vars)
        return v


class TargetMode(Enum):
    FULL = "full"
    DELTA = "delta"


class SupportedTarget(BaseModel):
    """Configuration for supported targets."""

    mode: Set[TargetMode]
    language: Set[TargetLangauge]
    sanitizer: Set[TargetSanitizer]
    architecture: Set[TargetArch]


class CRSType(Enum):
    BUG_FINDING = "bug-finding"
    BUG_FIXING = "bug-fixing"


class CRSConfig(BaseModel):
    """Root configuration for a CRS."""

    name: str
    type: Set[CRSType]
    version: str
    docker_registry: str
    prepare_phase: PreparePhase
    target_build_phase: TargetBuildPhase

    crs_run_phase: CRSRunPhase
    supported_target: SupportedTarget

    allowed_llms: Optional[list[str]] = Field(default=None)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        # TODO: Improve version validation if needed
        v = v.strip()  # Remove leading/trailing whitespace
        if not v:
            raise ValueError("version cannot be empty")
        return v

    @field_validator("docker_registry")
    @classmethod
    def validate_docker_registry(cls, v: str) -> str:
        # TODO: Improve docker_registry validation if needed
        v = v.strip()  # Remove leading/trailing whitespace
        if not v:
            raise ValueError("docker_registry cannot be empty")
        return v

    @field_validator("allowed_llms")
    @classmethod
    def validate_allowed_llms(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        # TODO: Add specific validation for LLM names if needed
        if v is None:
            return v
        return list(set(v))

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "CRSConfig":
        """Parse CRS config from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: Path) -> "CRSConfig":
        """Parse CRS config from YAML file."""
        with open(filepath.resolve(), "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "CRSConfig":
        """Parse CRS config from dictionary."""
        return cls.model_validate(data)


if __name__ == "__main__":
    import sys

    config = CRSConfig.from_yaml_file(sys.argv[1])
    print(config)
