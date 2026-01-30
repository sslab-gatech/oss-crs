import re
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl
import yaml


class CRSSource(BaseModel):
    """Source configuration for a CRS entry."""

    url: Optional[HttpUrl] = None
    ref: Optional[str] = None
    local_path: Optional[str] = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.local_path is not None:
            if self.url is not None or self.ref is not None:
                raise ValueError("'local_path' cannot be combined with 'url' or 'ref'")
        if self.url is not None:
            if self.local_path is not None:
                raise ValueError("'url' cannot be combined with 'local_path'")
            if self.ref is None:
                raise ValueError("'ref' is required when 'url' is provided")
        if self.url is None and self.local_path is None:
            raise ValueError("Either 'url' or 'local_path' must be provided")

        return self


class ResourceConfig(BaseModel):
    """Base resource configuration with cpuset and memory."""

    cpuset: str
    memory: str
    llm_budget: Optional[int] = Field(default=None, gt=0)

    @field_validator("cpuset")
    @classmethod
    def validate_cpuset(cls, v: str) -> str:
        # Matches patterns like "0-3", "0,1,2,3", "0-3,5,7-9"
        pattern = r"^(\d+(-\d+)?)(,\d+(-\d+)?)*$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid cpuset format: '{v}'. "
                "Expected format like '0-3', '0,1,2,3', or '0-3,5,7-9'"
            )
        return v

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, v: str) -> str:
        # Matches patterns like "8G", "16GB", "1024M", "2048MB"
        pattern = r"^\d+(\.\d+)?\s*(B|K|KB|M|MB|G|GB|T|TB)$"
        if not re.match(pattern, v, re.IGNORECASE):
            raise ValueError(
                f"Invalid memory format: '{v}'. "
                "Expected format like '8G', '16GB', '1024M', '2048MB'"
            )
        return v


class CRSEntry(ResourceConfig):
    """Configuration for a single CRS entry."""

    source: CRSSource


class RunEnv(Enum):
    LOCAL = "local"
    AZURE = "azure"


class CRSComposeConfig(BaseModel):
    """Root configuration for CRS Compose."""

    run_env: RunEnv
    docker_registry: str
    oss_crs_infra: ResourceConfig
    crs_entries: dict[str, CRSEntry] = Field(default_factory=dict)

    @field_validator("docker_registry")
    @classmethod
    def validate_docker_registry(cls, v: str) -> str:
        # TODO: Add more robust validation for docker registry URL if needed
        return v

    @field_validator("crs_entries")
    @classmethod
    def validate_crs_entries_keys(cls, v: dict[str, CRSEntry]) -> dict[str, CRSEntry]:
        uppercase_keys = [key for key in v.keys() if key != key.lower()]
        if uppercase_keys:
            raise ValueError(
                f"CRS entry names must be lowercase. "
                f"Invalid names: {', '.join(uppercase_keys)}"
            )
        return v

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML file."""
        with open(filepath, "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "CRSComposeConfig":
        """Parse CRS Compose config from dictionary."""
        RUN_ENV = "run_env"
        DOCKER_REGISTRY = "docker_registry"
        OSS_CRS_INFRA = "oss_crs_infra"
        run_env = data.get(RUN_ENV)
        docker_registry = data.get(DOCKER_REGISTRY)
        oss_crs_infra = data.get(OSS_CRS_INFRA)

        reserved_keys = {RUN_ENV, DOCKER_REGISTRY, OSS_CRS_INFRA}
        crs_entries = {
            key: value for key, value in data.items() if key not in reserved_keys
        }

        return cls(
            run_env=run_env,  # Pydantic will convert string to enum automatically
            docker_registry=docker_registry,
            oss_crs_infra=oss_crs_infra,
            crs_entries=crs_entries,
        )


class CRSComposeEnv:
    def __init__(self, run_env: RunEnv):
        self.run_env = run_env

    def get_env(self) -> dict[str, str]:
        return {"type": self.run_env.value}


if __name__ == "__main__":
    import sys

    config = CRSComposeConfig.from_yaml_file(sys.argv[1])
    print(config)
