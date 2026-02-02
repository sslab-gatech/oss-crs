from enum import Enum
import os
import subprocess
from pathlib import Path


class EnvType(str, Enum):
    LOCAL = "local"


def get_env(key: str) -> str:
    # Error if key does not exist in environment
    value = os.environ.get(key)
    if value is None:
        raise KeyError(f"Environment variable '{key}' not found")
    return value


OSS_CRS_RUN_ENV_TYPE = EnvType(get_env("OSS_CRS_RUN_ENV_TYPE"))


def rsync_copy(src: Path, dst: Path) -> None:
    # Create parent directories
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        # Directory: copy everything recursively
        subprocess.run(["rsync", "-a", f"{src}/", f"{dst}/"], check=True)
    else:
        # File: just copy it
        subprocess.run(["rsync", "-a", str(src), str(dst)], check=True)
