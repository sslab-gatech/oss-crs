import random
import string
from pathlib import Path
from typing import Optional

from .ui import MultiTaskProgress


RAND_CHARS = string.ascii_lowercase + string.digits


def _generate_random_name(length: int = 10) -> str:
    """Generate a random alphanumeric string."""
    return "".join(random.choice(RAND_CHARS) for _ in range(length))


class TmpDockerCompose:
    def __init__(self, progress, project_name_prefix: str = "proj"):
        self.progress = progress
        self._project_name_prefix = project_name_prefix
        self.path: Optional[Path] = None
        self.project_name: Optional[str] = None

    def __enter__(self) -> "TmpDockerCompose":
        # Create a temporary docker-compose YAML file
        name = _generate_random_name(10)
        self.path = Path(f"/tmp/{name}.docker-compose.yaml")
        self.project_name = f"{self._project_name_prefix}_{name}"
        self.path.touch()
        self.progress.add_cleanup_task(
            "Cleanup Docker Compose",
            lambda progress: progress.docker_compose_down(self.project_name, self.path),
        )
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self.path is None or self.project_name is None:
            return
        # Clean up the temporary file
        if self.path is not None and self.path.exists():
            self.path.unlink()
