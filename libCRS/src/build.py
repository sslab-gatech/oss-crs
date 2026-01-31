from pathlib import Path

from .common import OSS_CRS_BUILD_OUT_DIR, is_local_run_env, rsync_copy


def submit_build_output(src_path: str, dst_path: str) -> None:
    """Submit build output from src_path to dst_path.

    Args:
        src_path: Source path (file or directory)
        dst_path: Destination path relative to OSS_CRS_BUILD_OUT_DIR
    """

    if is_local_run_env():
        src = Path(src_path)
        dst = OSS_CRS_BUILD_OUT_DIR / dst_path
        rsync_copy(src, dst)

    else:
        raise NotImplementedError("TODO: submit_build_output other run environments")


def skip_build_output(dst_path: str) -> None:
    raise NotImplementedError("TODO: skip_build_output is not yet implemented")


def download_build_output(src_path: str, dst_path: str) -> None:
    if is_local_run_env():
        src = OSS_CRS_BUILD_OUT_DIR / src_path
        dst = Path(dst_path)
        rsync_copy(src, dst)
    else:
        raise NotImplementedError("TODO: download_build_output other run environments")
