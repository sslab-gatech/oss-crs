from pathlib import Path
import logging
import subprocess
import shutil
import yaml
from bug_fixing.src.oss_patch.project_builder import OSSPatchProjectBuilder
from bug_fixing.src.oss_patch.functions import (
    get_builder_image_name,
    change_ownership_with_docker,
    pull_project_source_from_tarball,
)

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY = "ghcr.io/team-atlanta"


def _get_snapshot_image_name(
    project_name: str, sanitizer: str, registry: str = DEFAULT_REGISTRY
) -> str:
    """Generate snapshot image name for incremental build.

    Args:
        project_name: Project name (e.g., "json-c", "aixcc/c/json-c")
        sanitizer: Sanitizer name (e.g., "address")
        registry: Docker registry (default: ghcr.io/team-atlanta)

    Returns:
        Full image name (e.g., "ghcr.io/team-atlanta/crsbench/json-c:inc-address")
    """
    # Extract just the project name, removing any prefix like "aixcc/c/" or "aixcc/jvm/"
    simple_name = project_name.split("/")[-1]
    return f"{registry}/crsbench/{simple_name}:inc-{sanitizer}"


def _get_base_image_name(project_name: str, registry: str = DEFAULT_REGISTRY) -> str:
    """Generate base image name for builder.

    Args:
        project_name: Project name (e.g., "json-c", "aixcc/c/json-c")
        registry: Docker registry (default: ghcr.io/team-atlanta)

    Returns:
        Full image name (e.g., "ghcr.io/team-atlanta/crsbench/json-c:base")
    """
    # Extract just the project name, removing any prefix like "aixcc/c/" or "aixcc/jvm/"
    simple_name = project_name.split("/")[-1]
    return f"{registry}/crsbench/{simple_name}:base"


class IncrementalSnapshotMaker:
    """Creates incremental build snapshots and pushes them to Docker registry.

    This class is designed to be used after test-inc-build has verified
    that incremental builds work correctly for a project.
    """

    def __init__(
        self,
        oss_fuzz_path: Path,
        project_name: str,
        work_dir: Path,
        log_file: Path | None = None,
        benchmarks_dir: Path | None = None,
    ):
        self.oss_fuzz_path = oss_fuzz_path
        self.project_name = project_name
        self.project_path = oss_fuzz_path / "projects" / self.project_name
        self.work_dir = work_dir
        self.log_file = log_file
        self.benchmarks_dir = benchmarks_dir

        self.required_sanitizers: list[str] = []

        logger.info(f"  project_path.exists(): {self.project_path.exists()}")
        logger.info(f"  project_path: {self.project_path}")
        logger.info(f"  project_name: {self.project_name}")
        logger.info(f"  oss_fuzz_path.exists(): {self.oss_fuzz_path.exists()}")
        logger.info(f"  oss_fuzz_path: {self.oss_fuzz_path}")

        assert self.oss_fuzz_path.exists()
        assert self.project_path.exists()

        self.project_builder = OSSPatchProjectBuilder(
            self.project_name,
            self.oss_fuzz_path,
            project_path=self.project_path,
            log_file=log_file,
        )

    def _get_required_sanitizers(self) -> list[str]:
        """Get sanitizers from project.yaml.

        Returns:
            List of sanitizer names from project.yaml (e.g., ["address", "undefined"])
        """
        project_yaml_path = self.project_path / "project.yaml"

        if not project_yaml_path.exists():
            logger.warning(f"project.yaml not found: {project_yaml_path}")
            return ["address"]  # default

        with open(project_yaml_path, "r") as f:
            project_yaml = yaml.safe_load(f)

        sanitizers = project_yaml.get("sanitizers", ["address"])
        logger.info(f"Sanitizers from project.yaml: {sanitizers}")

        return sanitizers

    def _get_project_language(self) -> str:
        """Get project language from project.yaml.

        Returns:
            Language string (e.g., "c", "c++", "jvm")
        """
        project_yaml_path = self.project_path / "project.yaml"

        if not project_yaml_path.exists():
            logger.warning(f"project.yaml not found: {project_yaml_path}")
            return "c"  # default

        with open(project_yaml_path, "r") as f:
            project_yaml = yaml.safe_load(f)

        language = project_yaml.get("language", "c")
        logger.info(f"Language from project.yaml: {language}")

        return language

    def _check_image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists locally.

        Args:
            image_name: Full image name with tag

        Returns:
            True if image exists locally, False otherwise
        """
        cmd = f"docker image inspect {image_name}"
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0

    def _check_remote_image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists in remote registry.

        Args:
            image_name: Full image name with tag (e.g., ghcr.io/team-atlanta/crsbench/project:tag)

        Returns:
            True if image exists in remote registry, False otherwise
        """
        cmd = f"docker manifest inspect {image_name}"
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0

    def _push_image(self, source_image: str, target_image: str) -> bool:
        """Tag and push Docker image to registry.

        Args:
            source_image: Source image name (e.g., gcr.io/oss-fuzz/project:inc-address)
            target_image: Target image name (e.g., ghcr.io/team-atlanta/crsbench/project:inc-address)

        Returns:
            True if push succeeded, False otherwise
        """
        # Tag the image
        logger.info(f"Tagging image: {source_image} -> {target_image}")
        tag_cmd = f"docker tag {source_image} {target_image}"
        proc = subprocess.run(
            tag_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode != 0:
            logger.error(f"Failed to tag image: {proc.stderr.decode()}")
            return False

        # Push the image (show progress in real-time)
        logger.info(f"Pushing image: {target_image}")
        push_cmd = f"docker push {target_image}"
        proc = subprocess.run(
            push_cmd,
            shell=True,
            # Don't capture stdout/stderr to show progress in real-time
        )

        if proc.returncode != 0:
            logger.error(f"Failed to push image: {target_image}")
            logger.error("Authentication failed? Please run: docker login ghcr.io")
            return False

        logger.info(f"Successfully pushed: {target_image}")
        return True

    def make_snapshot(
        self,
        rts_tool: str | None = None,
        push: str | None = None,
        force_rebuild: bool = True,
        skip_clone: bool = False,
        force_push: bool = False,
    ) -> bool:
        """Create incremental build snapshot and optionally push to registry.

        Args:
            rts_tool: RTS tool to use. JVM: jcgeks, openclover. C/C++: binaryrts.
                      If None, RTS is disabled.
            push: Push mode - 'original' (original image only), 'inc' (incremental only),
                  'both' (original and incremental), or None (no push)
            force_rebuild: Force rebuild even if image exists (default: True)
            skip_clone: Skip source code cloning
            force_push: Force push even if images already exist in remote registry

        Returns:
            True if successful, False otherwise
        """
        # Get required sanitizers from project.yaml
        self.required_sanitizers = self._get_required_sanitizers()
        logger.info(f"Required sanitizers: {self.required_sanitizers}")

        # Get project language
        language = self._get_project_language()
        logger.info(f"Project language: {language}")

        # Validate rts_tool for project language
        rts_enabled = rts_tool is not None
        if rts_enabled:
            jvm_tools = {"jcgeks", "openclover"}
            c_tools = {"binaryrts"}

            if language == "jvm" and rts_tool not in jvm_tools:
                logger.error(
                    f"Invalid RTS tool '{rts_tool}' for JVM project. "
                    f"Valid options: {', '.join(jvm_tools)}"
                )
                return False
            elif language in ("c", "c++") and rts_tool not in c_tools:
                logger.error(
                    f"Invalid RTS tool '{rts_tool}' for C/C++ project. "
                    f"Valid options: {', '.join(c_tools)}"
                )
                return False
            elif language not in ("jvm", "c", "c++"):
                logger.warning(
                    f"Unknown language '{language}', RTS tool validation skipped"
                )

        # Check if images already exist (when not forcing rebuild)
        base_image = get_builder_image_name(self.oss_fuzz_path, self.project_name)
        images_to_process: list[tuple[str, str]] = []  # (source_image, target_image)
        need_rebuild = force_rebuild

        for sanitizer in self.required_sanitizers:
            source_image = f"{base_image}:inc-{sanitizer}"
            target_image = _get_snapshot_image_name(self.project_name, sanitizer)
            images_to_process.append((source_image, target_image))

            if not force_rebuild:
                if self._check_image_exists(source_image):
                    logger.info(f"Image already exists locally: {source_image}")
                else:
                    logger.info(
                        f"Image not found locally: {source_image}, rebuild required"
                    )
                    need_rebuild = True

        # If not forcing rebuild and all images exist, skip build
        if not need_rebuild:
            logger.info(
                "All images exist locally, skipping rebuild (use without --no-rebuild to force)"
            )
        else:
            # Build images
            proj_src_path = self.work_dir / "project-src"

            if skip_clone:
                if not proj_src_path.exists():
                    logger.warning(
                        f"Source code path does not exist: {proj_src_path}, cloning anyway..."
                    )
                    skip_clone = False
                else:
                    logger.info(
                        f"Skipping source code clone, using existing code at {proj_src_path}"
                    )

            if not skip_clone:
                logger.info(f"Preparing project source code for {self.project_name}")
                if proj_src_path.exists():
                    change_ownership_with_docker(proj_src_path)
                    shutil.rmtree(proj_src_path)
                if not self.benchmarks_dir:
                    logger.error(
                        "benchmarks_dir is required. Use --benchmarks-dir to specify "
                        "the directory containing bundled tarballs."
                    )
                    return False
                logger.info(
                    f"Using tarball from benchmarks directory: {self.benchmarks_dir}"
                )
                if not pull_project_source_from_tarball(
                    self.benchmarks_dir, self.project_name, proj_src_path
                ):
                    logger.error("Failed to extract source from tarball")
                    return False

            # Build base project builder image
            logger.info(f'Creating project builder image: "{base_image}"')
            self.project_builder.build(proj_src_path, inc_build_enabled=False)

            # Create snapshot for each required sanitizer
            for sanitizer in self.required_sanitizers:
                logger.info(f"Creating snapshot for sanitizer: {sanitizer}")
                if not self.project_builder.take_incremental_build_snapshot(
                    proj_src_path,
                    rts_enabled=rts_enabled,
                    rts_tool=rts_tool if rts_tool else "none",
                    sanitizer=sanitizer,
                ):
                    logger.error(f"Failed to create snapshot for {sanitizer}")
                    return False

                source_image = f"{base_image}:inc-{sanitizer}"
                logger.info(f"Created snapshot image: {source_image}")

        # Prepare base image info
        base_target_image = _get_base_image_name(self.project_name)

        # Print summary
        logger.info("=" * 60)
        logger.info("Snapshot Summary")
        logger.info("=" * 60)
        logger.info(f"Project: {self.project_name}")
        logger.info(f"Language: {language}")
        logger.info(f"RTS enabled: {rts_enabled}")
        if rts_enabled:
            logger.info(f"RTS tool: {rts_tool}")
        logger.info(f"Sanitizers: {self.required_sanitizers}")
        logger.info(f"Rebuilt: {need_rebuild}")
        logger.info("Local images:")
        logger.info(f"  - {base_image}:original (original)")
        for source_img, _ in images_to_process:
            logger.info(f"  - {source_img}")
        if push:
            logger.info(f"Push mode: {push}")
            logger.info("Target images (remote):")
            if push in ("original", "both"):
                logger.info(f"  - {base_target_image} (original)")
            if push in ("inc", "both"):
                for _, target_img in images_to_process:
                    logger.info(f"  - {target_img}")
        logger.info("=" * 60)

        # Push images if requested
        if push:
            logger.info(f"Pushing images to registry: {DEFAULT_REGISTRY}")

            # Push original image if requested
            if push in ("original", "both"):
                if not force_push and self._check_remote_image_exists(
                    base_target_image
                ):
                    logger.info(
                        f"Skipping push: original image already exists in remote: {base_target_image}"
                    )
                else:
                    logger.info("Pushing original image...")
                    if not self._push_image(f"{base_image}:original", base_target_image):
                        logger.error(f"Failed to push original image: {base_target_image}")
                        return False

            # Push incremental images if requested
            if push in ("inc", "both"):
                logger.info("Pushing incremental images...")
                for source_image, target_image in images_to_process:
                    if not force_push and self._check_remote_image_exists(target_image):
                        logger.info(
                            f"Skipping push: image already exists in remote: {target_image}"
                        )
                        continue
                    if not self._push_image(source_image, target_image):
                        logger.error(f"Failed to push image: {target_image}")
                        return False

            logger.info("All requested images pushed successfully")

        return True
