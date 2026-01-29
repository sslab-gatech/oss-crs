from pathlib import Path
from typing import Optional
import hashlib
import os
import tempfile

from jinja2 import Template
from rich.console import Console

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry
from .ui import MultiTaskProgress
from .target import Target

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
        # TODO: implement other source types
        raise NotImplementedError("Only local_path source is implemented yet.")

    def __init__(self, name: str, crs_path: Path, work_dir: Path):
        self.name = name
        self.crs_path = crs_path.expanduser().resolve()
        self.config = CRSConfig.from_yaml_file(self.crs_path / CRS_YAML_PATH)
        self.work_dir = work_dir / "crs" / self.name
        self.work_dir.mkdir(parents=True, exist_ok=True)

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

        if standalone:
            # TODO
            pass
        else:
            return multi_task_progress.run_command_with_streaming_output(
                cmd=cmd, cwd=self.crs_path, env=env, info_text=info_text
            )

    def build_target(
        self, target: Target, target_base_image: str, progress: MultiTaskProgress
    ) -> bool:
        build_out_dir = (
            self.work_dir / (target_base_image.replace(":", "_")) / "BUILD_OUT_DIR"
        )
        for build_name, build_config in self.config.target_build_phase.builds.items():
            progress.add_task(
                build_name,
                lambda p,
                build_name=build_name,
                build_config=build_config: self.__build_target_one(
                    target,
                    target_base_image,
                    build_name,
                    build_config,
                    build_out_dir,
                    p,
                ),
            )
        return progress.run_added_tasks()

    def __build_target_one(
        self,
        target,
        target_base_image: str,
        build_name: str,
        build_config,
        build_out_dir: Path,
        progress: MultiTaskProgress,
    ) -> bool:
        build_out_dir = build_out_dir / build_name
        build_out_dir.mkdir(parents=True, exist_ok=True)

        def check_outputs(progress=None) -> bool:
            output_paths = []
            for output in build_config.outputs:
                output_path = build_out_dir / output.replace("$BUILD_OUT_DIR/", "")
                output_paths.append(output_path)
            if progress:
                for output_path in output_paths:
                    progress.add_task(
                        f"{output_path}",
                        lambda p, o=output_path: o.exists(),
                    )
                return progress.run_added_tasks()
            else:
                all_exist = all(p.exists() for p in output_paths)
                return all_exist

        def prepare_docker_compose_file(
            progress, tmp_docker_compose_path: Path
        ) -> bool:
            template_path = (
                Path(__file__).parent
                / "templates"
                / "build-target-docker-compose.yaml.j2"
            )
            template = Template(template_path.read_text())
            target_env = target.get_target_env()
            target_env["image"] = target_base_image

            rendered = template.render(
                crs={
                    "name": self.name,
                    "path": str(self.crs_path),
                    "builder_dockerfile": str(self.crs_path / build_config.dockerfile),
                    "version": self.config.version,
                },
                target=target_env,
                build_out_dir=str(build_out_dir),
            )

            tmp_docker_compose_path.write_text(rendered)
            return True

        def run_docker_compose(progress, tmp_docker_compose_path) -> bool:
            raw_name = f"{target_base_image}_{self.name}_{build_name}"
            name_hash = hashlib.sha256(raw_name.encode()).hexdigest()[:12]
            project_name = f"crs_{name_hash}"
            cmd = [
                "docker",
                "compose",
                "-p",
                project_name,
                "-f",
                str(tmp_docker_compose_path),
                "run",
                "target_builder",
            ]
            if progress.run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.crs_path,
            ):
                return True
            return False

        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".docker-compose", delete=True
        ) as tmp_docker_compose_file:
            tmp_docker_compose_path = Path(tmp_docker_compose_file.name)
            progress.add_task(
                "Prepare docker compose file",
                lambda p: prepare_docker_compose_file(p, tmp_docker_compose_path),
            )
            progress.add_task(
                "Build target by executing the docker compose",
                lambda p: run_docker_compose(p, tmp_docker_compose_path),
            )
            progress.add_task("Check outputs", check_outputs)

            if progress.run_added_tasks():
                return True
            # docker_compose_contents = tmp_docker_compose_path.read_text()
            # progress.add_note(
            #     f"Docker compose file contents:\n---\n{docker_compose_contents}\n---"
            # )
            return False
