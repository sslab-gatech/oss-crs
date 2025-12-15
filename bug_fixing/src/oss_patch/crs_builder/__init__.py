from pathlib import Path
import logging
import yaml
import tempfile


from bug_fixing.src.oss_patch.functions import (
    docker_image_exists_in_volume,
    get_crs_image_name,
    run_command,
)
from bug_fixing.src.oss_patch.globals import (
    OSS_PATCH_CRS_SYSTEM_IMAGES,
    DEFAULT_DOCKER_ROOT_DIR,
    OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE,
    OSS_CRS_REGISTRY_PATH,
)

logger = logging.getLogger(__name__)


def _parse_pkg_yaml(yaml_path: Path) -> tuple[str, str]:
    with open(yaml_path) as f:
        yaml_data = yaml.safe_load(f)

    return (
        yaml_data["source"]["url"],
        yaml_data["source"]["ref"],
    )


class OSSPatchCRSBuilder:
    def __init__(
        self,
        crs_name: str,
        work_dir: Path,
        local_crs: Path | None = None,
        registry_path: Path | None = None,
    ):
        self.crs_name = crs_name
        self.work_dir = work_dir
        self.crs_path = local_crs
        # Default to crs_registry using importlib.resources (same as bug_finding)
        self.registry_path = registry_path if registry_path else OSS_CRS_REGISTRY_PATH

    def build(self, volume_name: str = OSS_PATCH_CRS_SYSTEM_IMAGES) -> bool:
        logger.info(f'Getting CRS metadata for "{self.crs_name}"...')
        result = self._get_crs_yamls()
        if not result:
            logger.error(
                f'Failed to get CRS ("{self.crs_name}") metadata from registry'
            )
            return False

        pkg_yaml_path, config_crs_yaml_path = result

        if self.crs_path:
            # Use the existing local CRS source
            return self._build_crs_image_in_volume(config_crs_yaml_path, volume_name)

        # Pull CRS metadata and source
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            if not self._prepare_crs_source(pkg_yaml_path, tmp_path):
                return False
            return self._build_crs_image_in_volume(config_crs_yaml_path, volume_name)

    def _prepare_crs_source(self, pkg_yaml_path: Path, tmp_path: Path) -> bool:
        # Determine CRS source path
        if not self.crs_path:
            self.crs_path = tmp_path / "crs-source"
            logger.info(f"Pulling {self.crs_name} based on the registry...")
            if not self._pull_crs_sources(pkg_yaml_path):
                logger.error("Failed to pull CRS from the remote repository")
                return False

        if not self.crs_path.exists():
            logger.error(f'CRS path does not exist: "{self.crs_path}"')
            return False

        if not self.crs_path.exists():
            logger.error(f'CRS path does not exist: "{self.crs_path}"')
            return False

        logger.info(f'CRS is now prepared in "{self.crs_path}"')
        return True

    def _get_crs_yamls(self) -> tuple[Path, Path] | None:
        assert self.registry_path.exists()

        crs_registry_path = self.registry_path / self.crs_name

        if not crs_registry_path.exists():
            logger.error(
                f'CRS registry for "{self.crs_name}" does not exist in "{self.registry_path}".'
            )
            return None

        pkg_yaml_path = crs_registry_path / "pkg.yaml"
        config_yaml_path = crs_registry_path / "config-crs.yaml"

        if not pkg_yaml_path.exists():
            logger.error(f'"pkg.yaml" does not exist in "{crs_registry_path}".')
            return None

        if not config_yaml_path.exists():
            logger.error(f'"config-crs.yaml" does not exist in "{crs_registry_path}".')
            return None

        return (pkg_yaml_path, config_yaml_path)

    def _pull_crs_sources(self, pkg_yaml_path: Path) -> bool:
        """Pull CRS repository"""

        crs_url, crs_ref = _parse_pkg_yaml(pkg_yaml_path)
        run_command(f"git clone {crs_url} {self.crs_path}")
        if crs_ref is not None:
            run_command(f"git -C {self.crs_path} checkout {crs_ref}")

        run_command(
            f"git -C {self.crs_path} submodule update --init --recursive --depth 1"
        )

        return True

    def _build_crs_image_in_volume(self, config_yaml: Path, volume_name: str) -> bool:
        assert config_yaml.exists()
        assert self.crs_path and self.crs_path.exists()

        with open(config_yaml) as f:
            yaml_data = yaml.safe_load(f)

        if "build" in yaml_data.keys():
            rel_dockerfile_path = yaml_data["build"]["dockerfile"]
        else:
            rel_dockerfile_path = "builder.Dockerfile"

        assert Path(self.crs_path / rel_dockerfile_path).exists()

        crs_image_name = get_crs_image_name(self.crs_name)

        if docker_image_exists_in_volume(crs_image_name, volume_name):
            logger.info(
                f'CRS image "{crs_image_name}" already exist in the "{volume_name}". Skip buliding the CRS {self.crs_name}.'
            )
            return True

        logger.info("Building CRS Docker image...")

        crs_repo_path_in_container = "/crs-source"

        crs_build_command = f"docker build --network=host --tag {crs_image_name} --file {str(Path(crs_repo_path_in_container, rel_dockerfile_path))} {crs_repo_path_in_container}"

        docker_command = (
            f"docker run --rm --privileged --net=host "
            f"-v {volume_name}:{DEFAULT_DOCKER_ROOT_DIR} "
            f"-v {self.crs_path}:{crs_repo_path_in_container} "
            f"{OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE} "
            f"{crs_build_command}"
        )

        # try:
        #     subprocess.check_call(
        #         docker_command,
        #         stdout=subprocess.DEVNULL,
        #         stderr=subprocess.DEVNULL,
        #         shell=True,
        #     )
        # except subprocess.CalledProcessError as e:
        #     logger.error(
        #         "something went wrong during building the CRS docker image...", e
        #     )
        #     return False

        run_command(docker_command)

        logger.info("=" * 60)
        logger.info("CRS build completed successfully!")
        logger.info(f"CRS image: {get_crs_image_name(self.crs_name)}")
        logger.info("=" * 60)

        return True
