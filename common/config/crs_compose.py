import re
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl
import yaml as yaml_lib


class CRSSource(BaseModel):
    """Source configuration for a CRS entry."""

    url: Optional[HttpUrl] = None
    ref: Optional[str] = None
    local_path: Optional[str] = None
    conf_path: str

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


class CRSComposeType(Enum):
    LOCAL = "local"
    AZURE = "azure"


class CRSComposeConfig(BaseModel):
    """Root configuration for CRS Compose."""

    type: CRSComposeType
    oss_crs_infra: ResourceConfig
    crs_entries: dict[str, CRSEntry] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML string."""
        data = yaml_lib.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML file."""
        with open(filepath, "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "CRSComposeConfig":
        """Parse CRS Compose config from dictionary."""
        TYPE = "type"
        OSS_CRS_INFRA = "oss_crs_infra"
        config_type = data.get(TYPE)
        oss_crs_infra = data.get(OSS_CRS_INFRA)

        reserved_keys = {TYPE, OSS_CRS_INFRA}
        crs_entries = {
            key: value for key, value in data.items() if key not in reserved_keys
        }

        return cls(
            type=config_type,  # Pydantic will convert string to enum automatically
            oss_crs_infra=oss_crs_infra,
            crs_entries=crs_entries,
        )


if __name__ == "__main__":
    import sys

    config = CRSComposeConfig.from_yaml_file(sys.argv[1])
    print(config)
