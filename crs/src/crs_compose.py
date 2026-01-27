from pathlib import Path
from .config.crs_compose import CRSComposeConfig
from .crs import CRS
from .utils import MultiTaskProgress, TaskStatus


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
        self.__prepare_oss_crs_infra(
            publish=publish, docker_registry=self.config.docker_registry
        )

        # Collect task names (infra + all CRS)
        task_names = ["oss-crs-infra"] + [crs.name for crs in self.crs_list]

        all_success = True
        with MultiTaskProgress(
            task_names=task_names,
            title="CRS Compose Prepare",
        ) as progress:
            # Mark infra as done (TODO: actually implement infra preparation)
            progress.set_status("oss-crs-infra", TaskStatus.SUCCESS)

            # Prepare each CRS
            for crs in self.crs_list:
                progress.set_status(crs.name, TaskStatus.IN_PROGRESS)
                result = crs.prepare(
                    publish=publish,
                    docker_registry=self.config.docker_registry,
                    multi_task_progress=progress,
                )
                progress.set_status(
                    crs.name, TaskStatus.SUCCESS if result else TaskStatus.FAILED
                )
                if not result:
                    all_success = False
                    break  # Stop on first failure

        return all_success
