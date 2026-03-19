"""Passthrough copy logic for the exchange sidecar.

Implements the same safe-copy semantics as exchange/main.py: O_NOFOLLOW
to prevent TOCTOU symlink races, tmpfile+rename for atomicity, and
dedup-by-filename.  Used for non-seed data types (povs, patches, etc.)
and as a fallback when coverage filtering is not available.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_DATA_TYPES = {"povs", "seeds", "bug-candidates", "patches", "diffs"}


def _is_safe_name(name: str) -> bool:
    """Reject path components that could escape the expected directory."""
    return bool(name) and "/" not in name and name not in ("..", ".")


def safe_copy(src_path: str, dst: Path) -> bool:
    """Atomically copy a regular file to *dst*, skipping symlinks.

    Returns True if the file was copied, False if skipped or failed.
    """
    tmp_path = None
    try:
        fd_src = os.open(src_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            src_stat = os.fstat(fd_src)
            if not stat.S_ISREG(src_stat.st_mode):
                os.close(fd_src)
                return False
            fsrc = os.fdopen(fd_src, "rb")
        except BaseException:
            os.close(fd_src)
            raise

        fd, tmp_path = tempfile.mkstemp(dir=str(dst.parent), prefix=".")
        os.close(fd)
        with fsrc, open(tmp_path, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
        os.rename(tmp_path, str(dst))
        os.chmod(str(dst), 0o644)
        return True
    except OSError as exc:
        log.warning("copy failed %s: %s", src_path, exc)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False


def copy_new_files(
    src_dir: Path,
    dst_dir: Path,
) -> list[str]:
    """Copy new files from *src_dir* to *dst_dir*, dedup by filename.

    Returns list of newly copied filenames.
    """
    copied = []
    for f_entry in os.scandir(src_dir):
        if f_entry.is_symlink():
            continue
        if not f_entry.is_file(follow_symlinks=False):
            continue
        if not _is_safe_name(f_entry.name):
            continue

        dst = dst_dir / f_entry.name
        if dst.exists():
            continue

        if safe_copy(f_entry.path, dst):
            copied.append(f_entry.name)

    return copied


def sync_non_seed_types(
    submit_root: Path,
    exchange_root: Path,
    created_dirs: set[str],
    warned_types: set[str],
) -> None:
    """Copy non-seed data types from SUBMIT_DIR trees to EXCHANGE_DIR.

    Handles povs, bug-candidates, patches, diffs.  Seeds are handled
    separately by the coverage-aware path.
    """
    if not submit_root.is_dir():
        return

    for crs_entry in os.scandir(submit_root):
        if not crs_entry.is_dir(follow_symlinks=False):
            continue
        for type_entry in os.scandir(crs_entry.path):
            if not type_entry.is_dir(follow_symlinks=False):
                continue
            data_type = type_entry.name

            if data_type not in ALLOWED_DATA_TYPES:
                if data_type not in warned_types:
                    log.warning("ignoring unknown data_type: %r", data_type)
                    warned_types.add(data_type)
                continue

            # Seeds handled by scanner, not here
            if data_type == "seeds":
                continue

            dst_dir = exchange_root / data_type
            if data_type not in created_dirs:
                dst_dir.mkdir(parents=True, exist_ok=True)
                created_dirs.add(data_type)

            for name in copy_new_files(Path(type_entry.path), dst_dir):
                log.info("copied %s/%s", data_type, name)


def sync_seeds_passthrough(
    submit_root: Path,
    exchange_root: Path,
    created_dirs: set[str],
) -> None:
    """Copy seeds using simple dedup-by-filename (no coverage filtering).

    Used as fallback when BUILD_OUT_DIR is not available or
    ENSEMBLER_MODE=passthrough.
    """
    if not submit_root.is_dir():
        return

    for crs_entry in os.scandir(submit_root):
        if not crs_entry.is_dir(follow_symlinks=False):
            continue
        seeds_dir = Path(crs_entry.path) / "seeds"
        if not seeds_dir.is_dir():
            continue

        dst_dir = exchange_root / "seeds"
        if "seeds" not in created_dirs:
            dst_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.add("seeds")

        for name in copy_new_files(seeds_dir, dst_dir):
            log.info("copied seeds/%s (passthrough)", name)
