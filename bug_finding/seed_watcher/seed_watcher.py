#!/usr/bin/env python3
"""Seed watcher service for CRS ensemble seed sharing.

Monitors corpus directories from multiple CRS instances and copies
new seeds to a shared directory. Seeds are deduplicated by content hash -
the filename in the shared directory is the SHA256 hash of the content.
"""

import argparse
import logging
import time
from pathlib import Path

from seed_utils import copy_seed
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("seed_watcher")


class SeedHandler(FileSystemEventHandler):
    """Handles new seed files by copying to shared directory with hash-based naming."""

    def __init__(self, shared_dir: Path):
        self.shared_dir = shared_dir
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    def _copy_seed(self, src_path: Path) -> bool:
        """Copy seed to shared directory using content hash as filename.

        Returns True if seed was new and copied, False if duplicate.
        """
        return copy_seed(src_path, self.shared_dir)

    def on_created(self, event):
        if isinstance(event, FileCreatedEvent):
            self._copy_seed(Path(event.src_path))


def scan_existing_seeds(corpus_dirs: list[Path], handler: SeedHandler) -> int:
    """Scan existing seeds in corpus directories.

    Returns count of new seeds copied.
    """
    copied = 0
    for corpus_dir in corpus_dirs:
        if not corpus_dir.exists():
            continue
        for seed_file in corpus_dir.iterdir():
            if seed_file.is_file():
                if handler._copy_seed(seed_file):
                    copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(
        description="Watch corpus directories and sync seeds with hash-based deduplication"
    )
    parser.add_argument(
        "--corpus-dirs",
        required=True,
        help="Comma-separated list of corpus directories to watch"
    )
    parser.add_argument(
        "--shared-dir",
        required=True,
        help="Shared directory to copy seeds to"
    )
    parser.add_argument(
        "--scan-interval",
        type=float,
        default=5.0,
        help="Interval in seconds for periodic scans (default: 5.0)"
    )
    parser.add_argument(
        "--use-polling",
        action="store_true",
        help="Use polling observer instead of inotify"
    )
    args = parser.parse_args()

    corpus_dirs = [Path(d.strip()) for d in args.corpus_dirs.split(",")]
    shared_dir = Path(args.shared_dir)

    handler = SeedHandler(shared_dir)

    # Initial scan of existing seeds
    scan_existing_seeds(corpus_dirs, handler)

    # Setup watchdog observers for each corpus directory
    observer_cls = PollingObserver if args.use_polling else Observer
    observer = observer_cls()

    for corpus_dir in corpus_dirs:
        corpus_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(handler, str(corpus_dir), recursive=False)

    observer.start()
    print(f"Seed watcher started. Watching {len(corpus_dirs)} corpus dirs -> {shared_dir}")

    try:
        while True:
            # Periodic scan to catch any missed files
            time.sleep(args.scan_interval)
            scan_existing_seeds(corpus_dirs, handler)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
