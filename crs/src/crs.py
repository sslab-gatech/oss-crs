from pathlib import Path
from .config.crs import CRSConfig
from .config.crs_compose import CRSEntry

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

    def prepare(self, publish: bool = False, docker_registry: str = None) -> bool:
        # TODO
        return True
