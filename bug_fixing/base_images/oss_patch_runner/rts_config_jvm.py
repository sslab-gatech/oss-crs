#!/usr/bin/env python3
"""
RTS (Regression Test Selection) dynamic configuration script.

This script performs dynamic configuration for RTS tools before each test run:
- Generates list of modified Java files (using git diff HEAD for uncommitted changes)
- Creates JcgEks configuration files
- Updates RTS tool configurations based on current patch state

Assumes patch was applied via `git apply` or `patch` command (uncommitted).

Usage:
    python rts_config.py [project_path] [--tool ekstazi|jcgeks|openclover]  # project_path defaults to current directory

Environment variables:
    RTS_ON: If set to "1" or "true", RTS configuration will be applied
    RTS_TOOL: RTS tool to use (ekstazi, jcgeks, or openclover), default: jcgeks
"""

import os
import sys
import argparse
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional, Set


def execute_cmd_get_output(cmd: str, cwd: Optional[str] = None, timeout: int = 3600) -> Optional[str]:
    """Execute a shell command and return output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def find_target_dirs(project_path: str, target_name: str) -> List[str]:
    """Find all directories with the given name in the project."""
    dirs = []
    for root, dirnames, _ in os.walk(project_path):
        if target_name in dirnames:
            dirs.append(os.path.join(root, target_name))
    return dirs


def find_class_files(project_path: str) -> List[str]:
    """Find all .class files in the project."""
    class_files = []
    for root, _, files in os.walk(project_path):
        for file in files:
            if file.endswith(".class"):
                class_files.append(os.path.join(root, file))
    return class_files


def get_modified_java_files(project_path: str) -> List[str]:
    """
    Get list of modified Java files in working directory (uncommitted changes).

    Assumes patch was applied via `git apply` or `patch` command without commit.
    Uses `git diff HEAD` to detect uncommitted changes.

    Returns list of class paths (e.g., "com/example/MyClass")
    """
    modified_files = []

    # git diff HEAD shows all uncommitted changes (staged + unstaged)
    # This works for patches applied via `git apply` or `patch` command
    cmd = "git diff HEAD --name-only"
    output = execute_cmd_get_output(cmd, cwd=project_path)

    if output:
        for line in output.split("\n"):
            line = line.strip()
            if line.endswith(".java"):
                # Convert file path to class path
                # e.g., src/main/java/com/example/MyClass.java -> com/example/MyClass
                if "/java/" in line:
                    class_path = line.split("/java/")[-1]
                    class_path = class_path.rsplit(".", 1)[0]  # Remove .java
                    modified_files.append(class_path)

    return modified_files


def get_packages_from_classes(project_path: str) -> Set[str]:
    """Extract package paths from compiled class files."""
    packages = set()

    for class_file in find_class_files(project_path):
        # Extract package from target/classes or target/test-classes paths
        if "target/test-classes/" in class_file:
            parts = class_file.split("target/test-classes/")
            if len(parts) > 1:
                package_path = os.path.dirname(parts[1])
                if package_path:
                    packages.add(package_path + "/")
                else:
                    # Root package (default package) handling
                    packages.add("/")
        elif "target/classes/" in class_file:
            parts = class_file.split("target/classes/")
            if len(parts) > 1:
                package_path = os.path.dirname(parts[1])
                if package_path:
                    packages.add(package_path + "/")
                else:
                    # Root package (default package) handling
                    packages.add("/")

    return packages


class JcgEksConfig:
    """JcgEks configuration file generator."""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.project_name = os.path.basename(self.project_path.rstrip("/"))
        self.config_dir = os.path.join(self.project_path, "jcg_config")

    def ensure_config_dir(self) -> None:
        """Create jcg_config directory, removing existing one if present."""
        if os.path.exists(self.config_dir):
            print(f"[INFO] Removing existing jcg_config directory: {self.config_dir}")
            shutil.rmtree(self.config_dir)
        os.makedirs(self.config_dir)

    def create_config_properties(self) -> None:
        """Create config.properties file for JcgEks."""
        config_path = os.path.join(self.config_dir, "config.properties")

        lines = [
            "parse.method.call.type.value=true",
            "first.parse.init.method.type=true",
            "continue.when.error=true",
            "debug.print=false",
            f"output.root.path={self.project_path}",
            "output.file.ext=",
        ]

        with open(config_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        print(f"[INFO] Created: {config_path}")

    def create_jar_dir_properties(self) -> None:
        """Create jar_dir.properties file listing classes and test-classes directories."""
        jar_dir_path = os.path.join(self.config_dir, "jar_dir.properties")

        class_dirs = []
        test_class_dirs = []

        # Find target/classes directories
        for classes_dir in find_target_dirs(self.project_path, "classes"):
            if "target/classes" in classes_dir and "test-classes" not in classes_dir:
                class_dirs.append(classes_dir)

        # Find target/test-classes directories
        for test_dir in find_target_dirs(self.project_path, "test-classes"):
            if "target/test-classes" in test_dir:
                test_class_dirs.append(test_dir)

        with open(jar_dir_path, "w") as f:
            for dir_path in class_dirs + test_class_dirs:
                f.write(dir_path + "\n")

        print(f"[INFO] Created: {jar_dir_path}")

    def create_packages_properties(self) -> None:
        """Create packages.properties file (usually empty for default behavior)."""
        pack_path = os.path.join(self.config_dir, "packages.properties")

        with open(pack_path, "w") as f:
            f.write("\n")

        print(f"[INFO] Created: {pack_path}")

    def create_package_list(self) -> None:
        """Create package_list.txt with all packages in the project."""
        package_list_path = os.path.join(self.config_dir, "package_list.txt")

        packages = get_packages_from_classes(self.project_path)

        with open(package_list_path, "w") as f:
            for package in sorted(packages):
                f.write(package + "\n")

        print(f"[INFO] Created: {package_list_path} ({len(packages)} packages)")

    def create_modified_java_list(self) -> None:
        """Create java_modify.txt with list of modified Java files."""
        modify_path = os.path.join(self.config_dir, "java_modify.txt")

        modified_files = get_modified_java_files(self.project_path)

        with open(modify_path, "w") as f:
            for class_path in modified_files:
                f.write(class_path + "\n")

        print(f"[INFO] Created: {modify_path} ({len(modified_files)} modified files)")

    def cleanup_stale_excludes(self) -> None:
        """Clean up stale JcgEks excludes file from previous runs."""
        excludes_path = f"/tmp/{self.project_name}_jcgeksExcludes"
        if os.path.exists(excludes_path):
            try:
                os.remove(excludes_path)
                print(f"[INFO] Removed stale excludes: {excludes_path}")
            except OSError as e:
                print(f"[WARNING] Failed to remove excludes file: {e}")

    def generate_all(self) -> None:
        """Generate all JcgEks configuration files."""
        print(f"[INFO] Generating JcgEks configuration for: {self.project_name}")

        self.cleanup_stale_excludes()
        self.ensure_config_dir()
        self.create_config_properties()
        self.create_jar_dir_properties()
        self.create_packages_properties()
        self.create_package_list()
        self.create_modified_java_list()

        print("[INFO] JcgEks configuration completed!")


class EkstaziConfig:
    """Ekstazi configuration handler."""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.project_name = os.path.basename(self.project_path.rstrip("/"))

    def prepare(self) -> None:
        """
        Prepare Ekstazi configuration.

        Ekstazi doesn't require explicit configuration files like JcgEks.
        It automatically tracks dependencies and selects affected tests.
        This method ensures the environment is ready for Ekstazi.
        """
        print(f"[INFO] Preparing Ekstazi for: {self.project_name}")

        # Clean up stale excludes file
        excludes_path = f"/tmp/{self.project_name}_ekstaziExcludes"
        if os.path.exists(excludes_path):
            try:
                os.remove(excludes_path)
                print(f"[INFO] Removed stale excludes: {excludes_path}")
            except OSError:
                pass

        print("[INFO] Ekstazi preparation completed!")


class OpenCloverConfig:
    """OpenClover configuration handler."""

    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)
        self.project_name = os.path.basename(self.project_path.rstrip("/"))

    def prepare(self) -> None:
        """
        Prepare OpenClover configuration.

        OpenClover handles test optimization internally using its snapshot mechanism.
        No explicit configuration files are needed before each test run.
        The plugin reads from ${user.home}/.clover/clover.snapshot automatically.
        """
        print(f"[INFO] Preparing OpenClover for: {self.project_name}")
        print("[INFO] OpenClover requires no dynamic configuration - test optimization is handled internally via snapshot mechanism")
        print("[INFO] OpenClover preparation completed!")


def configure_rts(project_path: str, tool_name: str) -> bool:
    """
    Configure RTS tool for the current test run.

    Assumes patch was applied via `git apply` or `patch` command (uncommitted).

    Args:
        project_path: Path to the Java project root
        tool_name: RTS tool to use (ekstazi, jcgeks, or openclover)

    Returns:
        True if configuration succeeded, False otherwise
    """
    project_path = os.path.abspath(project_path)

    if not os.path.isdir(project_path):
        print(f"[ERROR] Project path does not exist: {project_path}")
        return False

    print(f"[INFO] Configuring RTS ({tool_name}) for: {project_path}")

    try:
        if tool_name == "jcgeks":
            config = JcgEksConfig(project_path)
            config.generate_all()
        elif tool_name == "ekstazi":
            config = EkstaziConfig(project_path)
            config.prepare()
        elif tool_name == "openclover":
            config = OpenCloverConfig(project_path)
            config.prepare()
        else:
            print(f"[ERROR] Unknown RTS tool: {tool_name}")
            return False

        return True

    except Exception as e:
        print(f"[ERROR] RTS configuration failed: {e}")
        return False


def is_rts_enabled() -> bool:
    """Check if RTS is enabled via environment variable."""
    rts_on = os.environ.get("RTS_ON", "").lower()
    return rts_on in ("1", "true", "yes", "on")


def main():
    parser = argparse.ArgumentParser(
        description="Configure RTS (Regression Test Selection) before test run"
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Path to the Java project root (default: current directory)",
    )
    parser.add_argument(
        "--tool",
        choices=["ekstazi", "jcgeks", "openclover"],
        default=os.environ.get("RTS_TOOL", "jcgeks"),
        help="RTS tool to use: ekstazi, jcgeks, or openclover (default: jcgeks or RTS_TOOL env var)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if RTS_ON is not set",
    )

    args = parser.parse_args()

    # Check if RTS is enabled
    if not args.force and not is_rts_enabled():
        print("[INFO] RTS is disabled (RTS_ON not set). Skipping configuration.")
        sys.exit(0)

    success = configure_rts(args.project_path, args.tool)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
