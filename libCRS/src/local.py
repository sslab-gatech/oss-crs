from pathlib import Path

from .base import CRSUtils, DataType
from .common import rsync_copy, get_env

OSS_CRS_BUILD_OUT_DIR = Path(get_env("OSS_CRS_BUILD_OUT_DIR"))


class LocalCRSUtils(CRSUtils):
    def download_build_output(self, src_path: str, dst_path: Path) -> None:
        src = OSS_CRS_BUILD_OUT_DIR / src_path
        dst = Path(dst_path)
        rsync_copy(src, dst)

    def submit_build_output(self, src_path: str, dst_path: Path) -> None:
        src = Path(src_path)
        dst = OSS_CRS_BUILD_OUT_DIR / dst_path
        rsync_copy(src, dst)

    def skip_build_output(self, dst_path: str) -> None:
        raise NotImplementedError("TODO: skip_build_output is not yet implemented")

    def register_submit_dir(self, type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        raise NotImplementedError("TODO: register_submit_dir is not yet implemented")

    def register_fetch_dir(self, type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        raise NotImplementedError("TODO: register_fetch_dir is not yet implemented")

    def submit(self, type: DataType, src: Path) -> None:
        raise NotImplementedError("TODO: submit is not yet implemented")

    def fetch(self, type: DataType, dst: Path) -> list[str]:
        raise NotImplementedError("TODO: fetch is not yet implemented")
