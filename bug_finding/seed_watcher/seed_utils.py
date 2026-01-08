#!/usr/bin/env python3
"""Utility functions for seed handling with hash-based deduplication.

This module contains the core logic for copying seeds with content-hash
based naming, separated from watchdog dependencies for use in other contexts.
"""

import hashlib
import shutil
from pathlib import Path


def file_hash(path: Path) -> str:
    """Compute SHA256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_seed(src_path: Path, shared_dir: Path) -> bool:
    """Copy seed to shared directory using content hash as filename.

    Args:
        src_path: Source file path
        shared_dir: Destination directory for seeds

    Returns:
        True if seed was new and copied, False if duplicate or error.
    """
    if not src_path.is_file():
        return False

    try:
        content_hash = file_hash(src_path)
    except (OSError, IOError):
        return False

    # Use hash as filename - automatically deduplicates
    dest_path = shared_dir / content_hash

    if dest_path.exists():
        return False

    try:
        shared_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)
        return True
    except (OSError, IOError):
        return False


def copy_corpus_to_shared(corpus_dir: Path, shared_dir: Path) -> int:
    """Copy all files from corpus directory to shared directory with hash-based naming.

    Args:
        corpus_dir: Directory containing corpus files
        shared_dir: Destination directory for seeds

    Returns:
        Number of new seeds copied (excludes duplicates).
    """
    if not corpus_dir.is_dir():
        return 0

    copied = 0
    for src_file in corpus_dir.iterdir():
        if src_file.is_file():
            if copy_seed(src_file, shared_dir):
                copied += 1
    return copied
