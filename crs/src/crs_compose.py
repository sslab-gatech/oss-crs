from pathlib import Path
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target
from .templates import renderer
from .utils import TmpDockerCompose


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path, work_dir: Path) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir)

    def __init__(self, config: CRSComposeConfig, work_dir: Path):
        self.config = config
        self.work_dir = work_dir
        self.crs_compose_env = CRSComposeEnv(self.config.run_env)
        self.crs_list = [
            CRS.from_crs_compose_entry(name, crs_cfg, work_dir, self.crs_compose_env)
            for name, crs_cfg in self.config.crs_entries.items()
        ]

    def __prepare_oss_crs_infra(
        self, publish: bool = False, docker_registry: str = None
    ) -> "TaskResult":
        # TODO
        return TaskResult(success=True)

    def prepare(self, publish: bool = False) -> bool:
        # Collect task names (infra + all CRS)
        tasks = [
            (
                "TODO: oss-crs-infra",
                lambda progress: self.__prepare_oss_crs_infra(
                    publish=publish, docker_registry=self.config.docker_registry
                ),
            )
        ]
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.prepare(
                        publish=publish,
                        docker_registry=self.config.docker_registry,
                        multi_task_progress=progress,
                    ),
                )
            )

        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Prepare",
        ) as progress:
            return progress.run_all_tasks()

        return True

    def build_target(self, target: Target) -> bool:
        target_base_image = target.build_docker_image()
        if target_base_image is None:
            return False

        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.build_target(
                        target, target_base_image, progress
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Build Target",
        ) as progress:
            return progress.run_all_tasks()

        return True

    def run(self, target: Target) -> bool:
        if not self.__check_target_built(target):
            if not self.build_target(target):
                return False
        return self.__run(target)

    def __check_target_built(self, target: Target) -> bool:
        target_base_image = target.get_docker_image_name()
        tasks = []
        for crs in self.crs_list:
            tasks.append(
                (
                    crs.name,
                    lambda progress, crs=crs: crs.is_target_built(
                        target, target_base_image, progress
                    ),
                )
            )
        with MultiTaskProgress(
            tasks=tasks,
            title="CRS Compose Check Target Built",
        ) as progress:
            return progress.run_all_tasks()

        return True

    def __run(self, target: Target) -> bool:
        if self.crs_compose_env.run_env == RunEnv.LOCAL:
            return self.__run_local(target)
        else:
            print(f"TODO: Support run env {self.crs_compose_env.run_env}")
            return False

    def __run_local(self, target: Target) -> bool:
        with MultiTaskProgress(
            tasks=[],
            title="CRS Compose Run",
        ) as progress:
            with TmpDockerCompose(progress, "crs_compose") as tmp_docker_compose:
                tmp_docker_compose_path = tmp_docker_compose.path
                project_name = tmp_docker_compose.project_name
                tasks = [
                    (
                        "Prepare Running Environment",
                        lambda progress: self.__prepare_local_running_env(
                            project_name, target, tmp_docker_compose_path, progress
                        ),
                    ),
                    (
                        "Run CRSs!",
                        lambda progress: self.__run_local_running_env(
                            project_name, tmp_docker_compose_path, progress
                        ),
                    ),
                ]
                progress.add_tasks(tasks)
                return progress.run_added_tasks()

        return False

    def __prepare_local_running_env(
        self,
        project_name: str,
        target: Target,
        tmp_docker_compose_path: Path,
        progress: MultiTaskProgress,
    ) -> TaskResult:
        def prepare_docker_compose(progress: MultiTaskProgress) -> TaskResult:
            content = renderer.render_run_crs_compose_docker_compose(
                project_name, self.crs_compose_env, self.crs_list, target
            )
            tmp_docker_compose_path.write_text(content)
            return TaskResult(success=True)

        progress.add_task(
            "Prepare combined docker compose file", prepare_docker_compose
        )
        progress.add_task(
            "Build docker images in the combined docker compose file",
            lambda progress: progress.docker_compose_build(
                project_name, tmp_docker_compose_path
            ),
        )

        return progress.run_added_tasks()

    def __run_local_running_env(
        self,
        project_name: str,
        tmp_docker_compose_path: Path,
        progress: MultiTaskProgress,
    ) -> TaskResult:
        ret = progress.docker_compose_up(project_name, str(tmp_docker_compose_path))
        if ret.success:
            return ret
        ret.error += "\n\n📝 Depending on your Dockerfile, You might need to run `uv run crs-compose prepare` to apply your changes."
        return ret
