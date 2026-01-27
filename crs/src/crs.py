from pathlib import Path
from typing import Optional
import os

from rich.console import Console

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry
from .utils import run_command_with_streaming_output, MultiTaskProgress, TaskStatus
from .target_repo import TargetRepo

CRS_YAML_PATH = "oss-crs/crs.yaml"


class CRS:
    @classmethod
    def from_yaml_file(cls, crs_path: Path, work_dir: Path) -> "CRS":
        config = CRSConfig.from_yaml_file(crs_path / CRS_YAML_PATH)
        return cls(config.name, crs_path, work_dir)

    @classmethod
    def from_crs_compose_entry(
        cls, name: str, entry: CRSEntry, work_dir: Path
    ) -> "CRS":
        if entry.source.local_path:
            return cls(name, Path(entry.source.local_path), work_dir)
        raise NotImplementedError("Only local_path source is implemented yet.")

    def __init__(self, name: str, crs_path: Path, work_dir: Path):
        self.name = name
        self.crs_path = crs_path.expanduser().resolve()
        self.config = CRSConfig.from_yaml_file(self.crs_path / CRS_YAML_PATH)
        self.work_dir = work_dir

    def prepare(
        self,
        publish: bool = False,
        docker_registry: Optional[str] = None,
        multi_task_progress: Optional[MultiTaskProgress] = None,
    ) -> bool:
        """
        Run docker buildx bake to prepare CRS images.

        Args:
            publish: If True, push baked images to the docker registry.
            docker_registry: Override registry for push/cache. If set, overrides config.
            multi_task_progress: Optional progress tracker. If not provided, creates one.

        Returns:
            True if bake succeeded, False otherwise.
        """
        # Create a single-task progress if not provided
        standalone = multi_task_progress is None
        if standalone:
            multi_task_progress = MultiTaskProgress(
                task_names=[self.name],
                title=f"Preparing CRS: {self.name}",
            )

        # Determine the registry to use (parameter overrides config)
        registry = docker_registry if docker_registry else self.config.docker_registry
        version = self.config.version

        # Build HCL file path (relative to crs_path)
        hcl_path = self.crs_path / self.config.prepare_phase.hcl

        # Build the base command
        cmd = ["docker", "buildx", "bake", "-f", str(hcl_path)]

        # Add cache-from options (buildx silently ignores unavailable sources)
        if registry:
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-from=type=registry,ref={cache_ref_version}",
                    f"--set=*.cache-from=type=registry,ref={cache_ref_latest}",
                ]
            )

        # Add push and cache-to options if publishing
        if publish:
            if not registry:
                Console().print(
                    "[bold red]Error:[/bold red] Cannot publish without a docker registry. "
                    "Provide docker_registry parameter or set it in config."
                )
                return False

            cmd.append("--push")
            cache_ref_version = f"{registry}/{self.name}:{version}"
            cache_ref_latest = f"{registry}/{self.name}:latest"
            cmd.extend(
                [
                    f"--set=*.cache-to=type=registry,ref={cache_ref_version},mode=max",
                    f"--set=*.cache-to=type=registry,ref={cache_ref_latest},mode=max",
                ]
            )

        # Set up environment with VERSION
        env = os.environ.copy()
        env["VERSION"] = version

        # Display command info
        info_text = (
            f"HCL: {hcl_path}\n"
            f"Version: {version}\n"
            f"Registry: {registry or 'N/A'}\n"
            f"Publish: {publish}"
        )

        multi_task_progress.set_task_info(self.name, info_text)
        multi_task_progress.set_cmd_info(self.name, " ".join(cmd), str(self.crs_path))

        # Run the bake command with streaming output
        def run_prepare() -> bool:
            multi_task_progress.set_status(self.name, TaskStatus.IN_PROGRESS)
            result = run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.crs_path,
                env=env,
                multi_task_progress=multi_task_progress,
                task_name=self.name,
            )
            multi_task_progress.set_status(
                self.name, TaskStatus.SUCCESS if result else TaskStatus.FAILED
            )
            return result

        if standalone:
            with multi_task_progress:
                return run_prepare()
        else:
            return run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.crs_path,
                env=env,
                multi_task_progress=multi_task_progress,
                task_name=self.name,
            )

    def build_target(self, target: TargetRepo) -> bool:
        # TODO
        return True
