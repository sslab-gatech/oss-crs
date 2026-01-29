from pathlib import Path
from typing import Optional
import hashlib
import os
import tempfile

from jinja2 import Template

from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry, CRSComposeEnv
from .ui import MultiTaskProgress, TaskResult
from .target import Target

CRS_YAML_PATH = "oss-crs/crs.yaml"


class CRS:
    @classmethod
    def from_yaml_file(cls, crs_path: Path, work_dir: Path) -> "CRS":
        config = CRSConfig.from_yaml_file(crs_path / CRS_YAML_PATH)
        return cls(config.name, crs_path, work_dir, None)

    @classmethod
    def from_crs_compose_entry(
        cls,
        name: str,
        entry: CRSEntry,
        work_dir: Path,
        crs_compose_env: CRSComposeEnv,
    ) -> "CRS":
        if entry.source.local_path:
            return cls(name, Path(entry.source.local_path), work_dir, crs_compose_env)
        # TODO: implement other source types
        raise NotImplementedError("Only local_path source is implemented yet.")

    def __init__(
        self,
        name: str,
        crs_path: Path,
        work_dir: Path,
        crs_compose_env: Optional[CRSComposeEnv],
    ):
        self.name = name
        self.crs_path = crs_path.expanduser().resolve()
        self.config = CRSConfig.from_yaml_file(self.crs_path / CRS_YAML_PATH)
        self.work_dir = work_dir / "crs" / self.name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.crs_compose_env = crs_compose_env

    def prepare(
        self,
        publish: bool = False,
        docker_registry: Optional[str] = None,
        multi_task_progress: Optional[MultiTaskProgress] = None,
    ) -> "TaskResult":
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
                error_msg = (
                    "Cannot publish without a docker registry. "
                    "Provide docker_registry parameter or set it in config."
                )
                return TaskResult(success=False, error=error_msg)

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

    def __is_supported_target(self, target: Target) -> bool:
        # TODO: implement proper check based on self.config.supported_target
        return True

    def build_target(
        self, target: Target, target_base_image: str, progress: MultiTaskProgress
    ) -> "TaskResult":
        if not self.__is_supported_target(target):
            # TODO: warn instead of error?
            return TaskResult(
                success=False,
                error=f"Skipping target {target.name} for CRS {self.name} as it is not supported.",
            )
        build_work_dir = self.work_dir / (target_base_image.replace(":", "_"))
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
                    build_work_dir,
                    p,
                ),
            )
        return TaskResult(success=progress.run_added_tasks())

    def __build_target_one(
        self,
        target,
        target_base_image: str,
        build_name: str,
        build_config,
        build_work_dir: Path,
        progress: MultiTaskProgress,
    ) -> "TaskResult":
        build_out_dir = build_work_dir / "BUILD_OUT_DIR" / build_name
        build_out_dir.mkdir(parents=True, exist_ok=True)
        build_cache_path = build_work_dir / f".{build_name}.cache"
        docker_compose_output = ""
        raw_name = f"{target_base_image}_{self.name}_{build_name}"
        name_hash = hashlib.sha256(raw_name.encode()).hexdigest()[:12]
        project_name = f"crs_{name_hash}"

        def check_outputs(progress=None) -> "TaskResult":
            output_paths = []
            for output in build_config.outputs:
                output_path = build_out_dir / output
                output_paths.append(output_path)
            if progress:
                for output_path in output_paths:
                    progress.add_task(
                        f"{output_path}",
                        lambda p, o=output_path: TaskResult(success=o.exists()),
                    )
                return TaskResult(success=progress.run_added_tasks())
            else:
                all_exist = all(p.exists() for p in output_paths)
                return TaskResult(success=all_exist)

        def prepare_docker_compose_file(
            progress, tmp_docker_compose_path: Path
        ) -> "TaskResult":
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
                additional_env=build_config.additional_env,
                target=target_env,
                build_out_dir=str(build_out_dir),
                crs_compose_env=self.crs_compose_env.get_env(),
            )

            tmp_docker_compose_path.write_text(rendered)
            return TaskResult(success=True)

        def build_docker_compose(progress, tmp_docker_compose_path) -> TaskResult:
            nonlocal docker_compose_output
            cmd = [
                "docker",
                "compose",
                "-p",
                project_name,
                "-f",
                str(tmp_docker_compose_path),
                "build",
            ]
            ret = progress.run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.crs_path,
            )
            docker_compose_output = ret.output if ret.success else ret.error
            return ret

        def run_docker_compose(progress, tmp_docker_compose_path) -> "TaskResult":
            nonlocal docker_compose_output
            image_hash = get_image_content_hash(
                f"{project_name}-target_builder", progress
            )
            if image_hash is None:
                return TaskResult(
                    success=False,
                    error="Failed to get target_builder image hash.",
                )

            if build_cache_path.exists():
                if build_cache_path.read_text() == image_hash:
                    progress.add_note(
                        "Build cache is up-to-date. Skipping target build."
                    )
                    return TaskResult(success=True)

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
            ret = progress.run_command_with_streaming_output(
                cmd=cmd,
                cwd=self.crs_path,
            )
            if ret.success:
                docker_compose_output = ret.output
            else:
                docker_compose_output = ret.error
            build_cache_path.write_text(image_hash)
            return ret

        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".docker-compose", delete=True
        ) as tmp_docker_compose_file:
            tmp_docker_compose_path = Path(tmp_docker_compose_file.name)
            progress.add_task(
                "Prepare docker compose file",
                lambda p: prepare_docker_compose_file(p, tmp_docker_compose_path),
            )
            progress.add_task(
                "Prepare docker images defined in docker compose file",
                lambda p: build_docker_compose(p, tmp_docker_compose_path),
            )
            progress.add_task(
                "Build target by executing the docker compose",
                lambda p: run_docker_compose(p, tmp_docker_compose_path),
            )
            progress.add_task("Check outputs", check_outputs)

            success = progress.run_added_tasks()
            if success:
                return TaskResult(success=True)
            docker_compose_contents = tmp_docker_compose_path.read_text()
            error = ""
            if docker_compose_output:
                error += (
                    f"📝 Docker compose output:\n---\n{docker_compose_output}\n---\n"
                )
            error += (
                f"📝 Docker compose file contents:\n---\n{docker_compose_contents}\n---"
            )
            return TaskResult(success=False, error=error)


def get_image_content_hash(
    image_name: str, progress: MultiTaskProgress
) -> Optional[str]:
    cmd = [
        "docker",
        "inspect",
        "--format",
        "{{json .RootFS.Layers}}",
        image_name,
    ]
    ret = progress.run_command_with_streaming_output(
        cmd=cmd,
        cwd=None,
    )
    if not ret.success:
        return None
    layers_json = ret.output.strip()
    image_hash = hashlib.sha256(layers_json.encode()).hexdigest()
    return image_hash
