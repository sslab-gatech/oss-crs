import subprocess
import random
import string
from pathlib import Path
from typing import Optional


RAND_CHARS = string.ascii_lowercase + string.digits


def generate_random_name(length: int = 10) -> str:
    """Generate a random alphanumeric string."""
    return "".join(random.choice(RAND_CHARS) for _ in range(length))


class TmpDockerCompose:
    def __init__(self, progress, project_name_prefix: str = "proj"):
        self.progress = progress
        self._project_name_prefix = project_name_prefix
        self.dir: Optional[Path] = None
        self.docker_compose: Optional[Path] = None
        self.project_name: Optional[str] = None

    def __enter__(self) -> "TmpDockerCompose":
        # Create a temporary docker-compose YAML file
        name = generate_random_name(10)
        self.dir = Path(f"/tmp/{name}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.docker_compose = self.dir / "docker-compose.yaml"
        self.project_name = f"{self._project_name_prefix}_{name}"
        self.docker_compose.touch()
        self.progress.add_cleanup_task(
            "Cleanup Docker Compose",
            lambda progress: progress.docker_compose_down(
                self.project_name, self.docker_compose
            ),
        )
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self.docker_compose is None or self.project_name is None:
            return
        # Clean up the temporary dir
        if self.dir is not None and self.dir.exists():
            self.dir.rmdir


def rm_with_docker(path: Path) -> None:
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{path.parent}:/data",
                "alpine",
                "rm",
                "-rf",
                f"/data/{path.name}",
            ],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error removing {path} with Docker: {e}")
