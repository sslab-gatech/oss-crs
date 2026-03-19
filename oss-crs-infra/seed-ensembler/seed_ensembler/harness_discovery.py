"""Discover harness executables from BUILD_OUT_DIR.

Scans the build output directory for executable files (ELF binaries or
shell scripts) and produces Harness objects for use with the worker pool.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from .harness import Harness
from .util import check_if_timeouts_scorable_in_options_file
from .constants import DEFAULT_SCORABLE_TIMEOUT_DURATION

log = logging.getLogger(__name__)

# Files that are part of the fuzzing infrastructure, not harnesses.
SKIP_NAMES = frozenset({
    "jazzer_driver",
    "jazzer_agent_deploy.jar",
    "jazzer_junit.jar",
    "llvm-symbolizer",
})

# Extensions that are never harness executables.
SKIP_EXTENSIONS = frozenset({
    ".jar", ".class", ".zip", ".dict", ".options", ".txt", ".log",
    ".so", ".a", ".o", ".dylib", ".py", ".sh",
})


def _is_harness_candidate(path: Path) -> bool:
    """Check if a file looks like a harness executable."""
    if path.name in SKIP_NAMES:
        return False
    if path.suffix in SKIP_EXTENSIONS:
        return False
    if path.name.startswith("."):
        return False

    try:
        st = path.stat()
    except OSError:
        return False

    if not stat.S_ISREG(st.st_mode):
        return False
    if not (st.st_mode & stat.S_IXUSR):
        return False

    # Check if it's an ELF binary or a shell script (Jazzer)
    try:
        with open(path, "rb") as f:
            header = f.read(4)
        if header[:4] == b"\x7fELF":
            return True
        if header[:2] == b"#!":
            return True
    except OSError:
        return False

    return False


def discover_harnesses(build_out_dir: Path) -> list[Harness]:
    """Scan BUILD_OUT_DIR for harness executables.

    Looks in ``build_out_dir`` and ``build_out_dir/build/`` (the
    standard oss-crs layout).  Returns a list of Harness objects.
    """
    harnesses = []
    search_dirs = [build_out_dir]

    build_subdir = build_out_dir / "build"
    if build_subdir.is_dir():
        search_dirs.append(build_subdir)

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for entry in os.scandir(search_dir):
            path = Path(entry.path)
            if not _is_harness_candidate(path):
                continue

            options_path = path.with_suffix(".options")
            scorable_timeout = None
            if check_if_timeouts_scorable_in_options_file(options_path):
                scorable_timeout = DEFAULT_SCORABLE_TIMEOUT_DURATION

            harness = Harness(
                name=path.name,
                path_in_out_dir=path,
                scorable_timeout_duration=scorable_timeout,
            )
            harnesses.append(harness)
            log.info("discovered harness: %s", path)

    return harnesses
