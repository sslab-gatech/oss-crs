from pathlib import Path
from .config.crs import CRSConfig


class CRS:
    @classmethod
    def from_yaml_file(cls, crs_file: Path) -> "CRS":
        config = CRSConfig.from_yaml_file(crs_file)
        return cls(config.name, config)

    def __init__(self, name, config):
        self.name = name
        self.config = config

    def prepare(self, publish: bool = False, docker_registry: str = None) -> bool:
        # TODO
        return True
