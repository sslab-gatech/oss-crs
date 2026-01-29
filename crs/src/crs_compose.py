from pathlib import Path
from .config.crs_compose import CRSComposeConfig
from .crs import CRS
from .ui import MultiTaskProgress
from .target import Target


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path, work_dir: Path) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config, work_dir)

    def __init__(self, config: CRSComposeConfig, work_dir: Path):
        self.config = config
        self.work_dir = work_dir
        self.crs_list = [
            CRS.from_crs_compose_entry(name, crs_cfg, work_dir)
            for name, crs_cfg in self.config.crs_entries.items()
        ]

    def __prepare_oss_crs_infra(
        self, publish: bool = False, docker_registry: str = None
    ) -> bool:
        # TODO
        return True

    def prepare(self, publish: bool = False) -> bool:
        # Collect task names (infra + all CRS)
        tasks = [
            (
                "oss-crs-infra",
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

    def build_target(self, target: Target, checkout: bool) -> bool:
        target_base_image = target.build_docker_image(checkout)
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
