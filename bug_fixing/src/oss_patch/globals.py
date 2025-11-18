from pathlib import Path

OSS_PATCH_WORK_DIR = Path(__file__).parent.parent.parent / ".work"  # bug_fixing/.work
OSS_PATCH_BUILD_CONTEXT_DIR = Path(".build_context").resolve()
OSS_PATCH_DOCKER_DATA_MANAGER_IMAGE = "gcr.io/oss-patch/docker-data-manager"
OSS_PATCH_CRS_SYSTEM_IMAGES = "crs-systems-volume"
OSS_PATCH_CRS_DOCKER_ASSETS = "crs-assets-volume"
DEFAULT_DOCKER_ROOT_DIR = "/var/lib/docker"
BASE_RUNNER_IMAGE = "gcr.io/oss-fuzz-base/base-runner"

OSS_PATCH_BASE_IMAGES_PATH = Path(__file__).parent.parent.parent / "base_images"
OSS_PATCH_CACHE_BUILDER_DATA_PATH = OSS_PATCH_BASE_IMAGES_PATH / "docker_cache_builder"
OSS_PATCH_RUNNER_DATA_PATH = OSS_PATCH_BASE_IMAGES_PATH / "oss_patch_runner"
OSS_CRS_REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "crs_registry"
