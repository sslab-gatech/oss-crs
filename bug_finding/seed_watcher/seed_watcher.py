#!/usr/bin/env python3
"""Ensemble watcher service for CRS ensemble sharing.

Monitors corpus and povs directories from multiple CRS instances and copies
new files to shared directories. Files are deduplicated by content hash -
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
logger = logging.getLogger("ensemble_watcher")


class FileHandler(FileSystemEventHandler):
    """Handles new files by copying to shared directory with hash-based naming."""

    def __init__(self, shared_dir: Path):
        self.shared_dir = shared_dir
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    def _copy_file(self, src_path: Path) -> bool:
        """Copy file to shared directory using content hash as filename.

        Returns True if file was new and copied, False if duplicate.
        """
        return copy_seed(src_path, self.shared_dir)

    def on_created(self, event):
        if isinstance(event, FileCreatedEvent):
            self._copy_file(Path(event.src_path))


def scan_existing_files(watch_dirs: list[Path], handler: FileHandler) -> int:
    """Scan existing files in watch directories.

    Returns count of new files copied.
    """
    copied = 0
    for watch_dir in watch_dirs:
        if not watch_dir.exists():
            continue
        for file_path in watch_dir.iterdir():
            if file_path.is_file():
                if handler._copy_file(file_path):
                    copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(
        description="Watch corpus and povs directories, sync with hash-based deduplication"
    )
    parser.add_argument(
        "--corpus-dirs",
        required=True,
        help="Comma-separated list of corpus directories to watch"
    )
    parser.add_argument(
        "--shared-corpus-dir",
        required=True,
        help="Shared directory to copy corpus seeds to"
    )
    parser.add_argument(
        "--povs-dirs",
        default="",
        help="Comma-separated list of povs directories to watch (optional)"
    )
    parser.add_argument(
        "--shared-povs-dir",
        default="",
        help="Shared directory to copy povs to (optional)"
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
    # Legacy argument for backwards compatibility
    parser.add_argument(
        "--shared-dir",
        default="",
        help="(Deprecated) Use --shared-corpus-dir instead"
    )
    args = parser.parse_args()

    # Handle legacy --shared-dir argument
    shared_corpus_dir = Path(args.shared_corpus_dir or args.shared_dir)

    corpus_dirs = [Path(d.strip()) for d in args.corpus_dirs.split(",") if d.strip()]

    # Setup corpus handler
    corpus_handler = FileHandler(shared_corpus_dir)

    # Setup povs handler if directories provided
    povs_handler = None
    povs_dirs = []
    if args.povs_dirs and args.shared_povs_dir:
        povs_dirs = [Path(d.strip()) for d in args.povs_dirs.split(",") if d.strip()]
        shared_povs_dir = Path(args.shared_povs_dir)
        povs_handler = FileHandler(shared_povs_dir)

    # Initial scan of existing files
    scan_existing_files(corpus_dirs, corpus_handler)
    if povs_handler:
        scan_existing_files(povs_dirs, povs_handler)

    # Setup watchdog observers
    observer_cls = PollingObserver if args.use_polling else Observer
    observer = observer_cls()

    # Watch corpus directories
    for corpus_dir in corpus_dirs:
        corpus_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(corpus_handler, str(corpus_dir), recursive=False)

    # Watch povs directories
    if povs_handler:
        for povs_dir in povs_dirs:
            povs_dir.mkdir(parents=True, exist_ok=True)
            observer.schedule(povs_handler, str(povs_dir), recursive=False)

    observer.start()

    watch_info = f"corpus dirs: {len(corpus_dirs)}"
    if povs_dirs:
        watch_info += f", povs dirs: {len(povs_dirs)}"
    print(f"Ensemble watcher started. Watching {watch_info}")

    try:
        while True:
            # Periodic scan to catch any missed files
            time.sleep(args.scan_interval)
            scan_existing_files(corpus_dirs, corpus_handler)
            if povs_handler:
                scan_existing_files(povs_dirs, povs_handler)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
