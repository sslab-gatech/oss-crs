from pathlib import Path
from importlib.resources import files

# Package data paths (bundled with package)
# files(__package__) = "bug_fixing.src.oss_patch" → .parent.parent = "bug_fixing"
_PACKAGE_ROOT = files(__package__).parent.parent  # bug_fixing/
OSS_PATCH_BASE_IMAGES_PATH = _PACKAGE_ROOT / "base_images"
OSS_PATCH_CACHE_BUILDER_DATA_PATH = OSS_PATCH_BASE_IMAGES_PATH / "docker_cache_builder"
OSS_PATCH_RUNNER_DATA_PATH = OSS_PATCH_BASE_IMAGES_PATH / "oss_patch_runner"

# Runtime paths (not bundled - use current working directory)
OSS_PATCH_BUILD_CONTEXT_DIR = Path.cwd() / ".build_context"
OSS_PATCH_WORK_DIR = Path.cwd() / ".oss-patch-work"

# Default registry path
# files(__package__) = "bug_fixing.src.oss_patch" → .parent.parent.parent = oss-crs root
OSS_CRS_PATH = files(__package__).parent.parent.parent # NOTE: will be site-packages after pip install
OSS_PATCH_DIR = OSS_CRS_PATH / "bug_fixing"
OSS_CRS_REGISTRY_PATH = OSS_CRS_PATH / "crs_registry"

# Docker image names (constants)
OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE = "gcr.io/oss-patch/docker-data-manager"
OSS_PATCH_CRS_SYSTEM_IMAGES = "crs-images-volume"
OSS_PATCH_DOCKER_IMAGES_FOR_CRS = "images-for-crs-volume"
DEFAULT_DOCKER_ROOT_DIR = "/var/lib/docker"
BASE_RUNNER_IMAGE = "gcr.io/oss-fuzz-base/base-runner"
DEFAULT_PROJECT_SOURCE_PATH = Path("/tmp/project-src")
