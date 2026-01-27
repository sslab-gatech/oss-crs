from pathlib import Path
from .config.crs_compose import CRSComposeConfig
from .crs import CRS


class CRSCompose:
    @classmethod
    def from_yaml_file(cls, compose_file: Path) -> "CRSCompose":
        config = CRSComposeConfig.from_yaml_file(compose_file)
        return cls(config)

    def __init__(self, config: CRSComposeConfig):
        self.config = config
        self.crs_list = [
            CRS(name, crs_cfg) for name, crs_cfg in self.config.crs_entries.items()
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
        for crs in self.crs_list:
            crs.prepare(publish=publish, docker_registry=self.config.docker_registry)
        return True
