import tempfile
from pathlib import Path
from .config.crs_compose import CRSComposeConfig, CRSComposeEnv, RunEnv
from .crs import CRS
from .ui import MultiTaskProgress, TaskResult
from .target import Target


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
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".docker-compose", delete=True
        ) as tmp_docker_compose_file:
            tmp_docker_compose_path = Path(tmp_docker_compose_file.name)
            tasks = [
                (
                    "Prepare Running Environment",
                    lambda progress: self.__prepare_local_running_env(
                        target, tmp_docker_compose_path, progress
                    ),
                ),
                (
                    "Run CRSs!",
                    lambda progress: self.__run_local_running_env(
                        tmp_docker_compose_path, progress
                    ),
                ),
            ]
            with MultiTaskProgress(
                tasks=tasks,
                title="CRS Compose Run",
            ) as progress:
                return progress.run_all_tasks()

        return False

    def __prepare_local_running_env(
        self, target: Target, tmp_docker_compose_path: Path, progress: MultiTaskProgress
    ) -> TaskResult:
        def prepare_docker_compose(progress) -> TaskResult:
            return TaskResult(success=True)

        progress.add_task(
            "TODO: Prepare combined docker compose file", prepare_docker_compose
        )
        # progress.add_task("Prepare combined docker compose file", build_docker_compose)

        return progress.run_added_tasks()

    def __run_local_running_env(
        self, tmp_docker_compose_path: Path, progress: MultiTaskProgress
    ) -> TaskResult:
        # TODO: Implement actual running logic
        cmd = ["/bin/bash", "-c", "ls -als; sleep 10"]
        progress.run_command_with_streaming_output(cmd, info_text="Running CRS Compose")
        return TaskResult(success=False, error="TODO: Implement __run_all_crs")
