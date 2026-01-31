from pathlib import Path


def register_seed_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    print("TODO: Implement register_seed_dir")


def register_shared_seed_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    print("TODO: Implement register_shared_seed_dir")
