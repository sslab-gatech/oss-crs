#!/usr/bin/env python3
"""
RTS (Regression Test Selection) initialization script.

This script performs one-time setup for RTS tools (Ekstazi, JcgEks, OpenClover) on Java projects:
- Modifies pom.xml files to add surefire and RTS tool plugins
- Configures surefire settings for RTS compatibility
- Parses INCLUDE_TESTS and EXCLUDE_TESTS from $SRC/test.sh and adds to surefire configuration
- Cleans up existing RTS artifacts
- Commits changes to git

Usage:
    python rts_init.py [project_path] [--tool ekstazi|jcgeks|openclover]
    python rts_init.py --install-deps  # Install Ekstazi and JcgEks dependencies only

/$SRC/test.sh format for INCLUDE_TESTS and EXCLUDE_TESTS:
    INCLUDE_TESTS="**/Test1.java,**/Test2.java"
    EXCLUDE_TESTS="**/FailingTest.java"

Environment variables:
    RTS_TOOL: RTS tool to use (ekstazi, jcgeks, or openclover), default: jcgeks
"""

import os
import sys
import argparse
import subprocess
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple
import tempfile
import re

# RTS tool configurations (ekstazi, jcgeks, and openclover supported)
RTS_TOOLS = {
    "ekstazi": {
        "group_id": "org.ekstazi",
        "artifact_id": "ekstazi-maven-plugin",
        "version": "5.3.0",
    },
    "jcgeks": {
        "group_id": "org.jcgeks",
        "artifact_id": "jcgeks-maven-plugin",
        "version": "1.0.0",
    },
    "openclover": {
        "group_id": "org.openclover",
        "artifact_id": "clover-maven-plugin",
        "version": "4.5.2",
    },
}

MAVEN_NAMESPACE = "http://maven.apache.org/POM/4.0.0"
SUREFIRE_VERSION = "2.22.2"

# OpenClover internal class exclude pattern
# OpenClover generates inner classes for each class (test or application code)
# These classes (e.g., TestUtils$__CLR2_6_34a4agh7gevmc) must be excluded from surefire
# See: https://openclover.org/doc/manual/latest/maven--using-with-surefire-and-inner-test-classes.html
OPENCLOVER_EXCLUDE_PATTERN = "**/*$__CLR*"

# JcgEks artifacts download URLs (order matters: parent -> core -> plugin)
JCGEKS_ARTIFACTS = [
    # 1. Parent POM (must be installed first, no jar)
    {
        "jar_url": None,
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.jcgeks.parent-1.0.0.pom",
        "jar_filename": None,
        "pom_filename": "org.jcgeks.parent-1.0.0.pom",
        "artifact_id": "org.jcgeks.parent",
        "packaging": "pom",
    },
    # 2. Core library (depends on parent)
    {
        "jar_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.jcgeks.core-1.0.0.jar",
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.jcgeks.core-1.0.0.pom",
        "jar_filename": "org.jcgeks.core-1.0.0.jar",
        "pom_filename": "org.jcgeks.core-1.0.0.pom",
        "artifact_id": "org.jcgeks.core",
        "packaging": "jar",
    },
    # 3. Maven plugin (depends on parent and core)
    {
        "jar_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/jcgeks-maven-plugin-1.0.0.jar",
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/jcgeks-maven-plugin-1.0.0.pom",
        "jar_filename": "jcgeks-maven-plugin-1.0.0.jar",
        "pom_filename": "jcgeks-maven-plugin-1.0.0.pom",
        "artifact_id": "jcgeks-maven-plugin",
        "packaging": "maven-plugin",
    },
]

# Ekstazi artifacts download URLs (order matters: parent -> core -> plugin)
# Note: Ekstazi 5.3.0 artifacts are hosted under JcgEks release 1.0.0
EKSTAZI_ARTIFACTS = [
    # 1. Parent POM (must be installed first, no jar)
    {
        "jar_url": None,
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.ekstazi.parent-5.3.0.pom",
        "jar_filename": None,
        "pom_filename": "org.ekstazi.parent-5.3.0.pom",
        "artifact_id": "org.ekstazi.parent",
        "packaging": "pom",
    },
    # 2. Core library (depends on parent)
    {
        "jar_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.ekstazi.core-5.3.0.jar",
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/org.ekstazi.core-5.3.0.pom",
        "jar_filename": "org.ekstazi.core-5.3.0.jar",
        "pom_filename": "org.ekstazi.core-5.3.0.pom",
        "artifact_id": "org.ekstazi.core",
        "packaging": "jar",
    },
    # 3. Maven plugin (depends on parent and core)
    {
        "jar_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/ekstazi-maven-plugin-5.3.0.jar",
        "pom_url": "https://github.com/acorn421/JcgEks/releases/download/1.0.0/ekstazi-maven-plugin-5.3.0.pom",
        "jar_filename": "ekstazi-maven-plugin-5.3.0.jar",
        "pom_filename": "ekstazi-maven-plugin-5.3.0.pom",
        "artifact_id": "ekstazi-maven-plugin",
        "packaging": "maven-plugin",
    },
]


def execute_cmd(cmd: str, cwd: Optional[str] = None, timeout: int = 3600) -> bool:
    """Execute a shell command and return success status."""
    try:
        subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[ERROR] Command failed: {cmd}")
        print(f"[ERROR] {e}")
        return False


def ensure_xmllint_installed() -> bool:
    """Ensure xmllint is installed, install via apt if not available."""
    try:
        subprocess.run(
            ["xmllint", "--version"],
            capture_output=True,
            timeout=10,
        )
        return True
    except FileNotFoundError:
        print("[INFO] xmllint not found, installing libxml2-utils...")
        try:
            subprocess.run(
                ["apt-get", "update"],
                capture_output=True,
                timeout=120,
            )
            result = subprocess.run(
                ["apt-get", "install", "-y", "libxml2-utils"],
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0:
                print("[INFO] xmllint installed successfully")
                return True
            else:
                print("[WARNING] Failed to install xmllint")
                return False
        except Exception as e:
            print(f"[WARNING] Failed to install xmllint: {e}")
            return False
    except Exception:
        return False


def format_xml_with_xmllint(file_path: str) -> bool:
    """Format XML file using xmllint."""
    try:
        # Run xmllint --format and capture output
        result = subprocess.run(
            ["xmllint", "--format", file_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            # Write formatted output back to file
            with open(file_path, "wb") as f:
                f.write(result.stdout)
            return True
        else:
            print(f"[WARNING] xmllint failed for {file_path}: {result.stderr.decode()}")
            return False
    except FileNotFoundError:
        print("[WARNING] xmllint not available, skipping formatting")
        return False
    except Exception as e:
        print(f"[WARNING] Failed to format {file_path}: {e}")
        return False


def find_maven_executable() -> Optional[str]:
    """
    Find Maven executable in the following order:
    1. $MVN environment variable
    2. mvn in $SRC directory (using find command)
    3. /usr/bin/mvn

    Returns:
        Path to Maven executable, or None if not found
    """
    # 1. Check $MVN environment variable
    mvn_env = os.environ.get("MVN")
    if mvn_env and os.path.isfile(mvn_env) and os.access(mvn_env, os.X_OK):
        print(f"[INFO] Using Maven from $MVN: {mvn_env}")
        return mvn_env

    # 2. Search for mvn in $SRC directory using find command
    src_dir = os.environ.get("SRC", "/src")
    if os.path.isdir(src_dir):
        try:
            result = subprocess.run(
                ["find", src_dir, "-name", "mvn", "-type", "f"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Get the first result that is executable
                for mvn_path in result.stdout.strip().split("\n"):
                    mvn_path = mvn_path.strip()
                    if mvn_path and os.path.isfile(mvn_path) and os.access(mvn_path, os.X_OK):
                        print(f"[INFO] Using Maven from $SRC: {mvn_path}")
                        return mvn_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 3. Check /usr/bin/mvn
    if os.path.isfile("/usr/bin/mvn") and os.access("/usr/bin/mvn", os.X_OK):
        print("[INFO] Using Maven from /usr/bin/mvn")
        return "/usr/bin/mvn"

    return None


def download_file(url: str, dest_path: str, timeout: int = 120) -> bool:
    """Download a file from URL to destination path."""
    print(f"[INFO] Downloading: {url}")

    # Try wget first
    try:
        result = subprocess.run(
            ["wget", "-q", "-O", dest_path, url],
            timeout=timeout,
            capture_output=True,
        )
        if result.returncode == 0 and os.path.exists(dest_path):
            print(f"[INFO] Downloaded: {dest_path}")
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    print(f"[ERROR] Failed to download: {url}")
    return False


def install_rts_artifacts(artifacts: list, tool_name: str, mvn: str) -> bool:
    """
    Download and install RTS artifacts to Maven local repository.

    Args:
        artifacts: List of artifact configurations to install
        tool_name: Name of the RTS tool (for logging)
        mvn: Path to Maven executable

    Returns:
        True if installation succeeded, False otherwise
    """
    print(f"[INFO] Installing {tool_name} dependencies...")

    # Create temp directory for downloads
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Install artifacts in order (parent -> core -> plugin)
        for artifact in artifacts:
            artifact_id = artifact["artifact_id"]
            print(f"[INFO] Installing {artifact_id}...")

            # Download POM file (required for all)
            pom_path = os.path.join(tmp_dir, artifact["pom_filename"])
            if not download_file(artifact["pom_url"], pom_path):
                print(f"[ERROR] Failed to download POM for {artifact_id}")
                return False

            # Build install command
            if artifact["jar_url"] is None:
                # Parent POM only (no jar)
                install_cmd = [
                    mvn,
                    "install:install-file",
                    f"-Dfile={pom_path}",
                    f"-DpomFile={pom_path}",
                    "-Dpackaging=pom",
                ]
            else:
                # JAR + POM
                jar_path = os.path.join(tmp_dir, artifact["jar_filename"])
                if not download_file(artifact["jar_url"], jar_path):
                    print(f"[ERROR] Failed to download JAR for {artifact_id}")
                    return False

                install_cmd = [
                    mvn,
                    "install:install-file",
                    f"-Dfile={jar_path}",
                    f"-DpomFile={pom_path}",
                    "-DgeneratePom=true",
                ]

            # Execute install command
            try:
                result = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    print(f"[ERROR] Failed to install {artifact_id}")
                    print(f"[ERROR] stdout: {result.stdout}")
                    print(f"[ERROR] stderr: {result.stderr}")
                    return False
                print(f"[INFO] Installed: {artifact_id}")
            except subprocess.TimeoutExpired:
                print(f"[ERROR] Timeout installing {artifact_id}")
                return False

    print(f"[INFO] {tool_name} installation completed!")
    return True


def install_jcgeks_jars() -> bool:
    """Install JcgEks artifacts to Maven local repository."""
    mvn = find_maven_executable()
    if not mvn:
        print("[ERROR] Maven executable not found!")
        print("[ERROR] Checked: $MVN, $SRC/*/mvn, /usr/bin/mvn, PATH")
        return False
    return install_rts_artifacts(JCGEKS_ARTIFACTS, "JcgEks", mvn)


def install_ekstazi_jars() -> bool:
    """Install Ekstazi artifacts to Maven local repository."""
    mvn = find_maven_executable()
    if not mvn:
        print("[ERROR] Maven executable not found!")
        print("[ERROR] Checked: $MVN, $SRC/*/mvn, /usr/bin/mvn, PATH")
        return False
    return install_rts_artifacts(EKSTAZI_ARTIFACTS, "Ekstazi", mvn)


def install_all_rts_deps() -> bool:
    """Install all RTS tool dependencies (Ekstazi and JcgEks)."""
    print("[INFO] Installing all RTS dependencies...")

    # Find Maven executable once
    mvn = find_maven_executable()
    if not mvn:
        print("[ERROR] Maven executable not found!")
        print("[ERROR] Checked: $MVN, $SRC/*/mvn, /usr/bin/mvn, PATH")
        return False

    # Install Ekstazi first
    if not install_rts_artifacts(EKSTAZI_ARTIFACTS, "Ekstazi", mvn):
        return False

    # Install JcgEks
    if not install_rts_artifacts(JCGEKS_ARTIFACTS, "JcgEks", mvn):
        return False

    print("[INFO] All RTS dependencies installed successfully!")
    return True


def find_pom_files(project_path: str) -> List[str]:
    """Find all pom.xml files in the project directory."""
    pom_files = []
    for root, _, files in os.walk(project_path):
        for file in files:
            if file == "pom.xml":
                pom_files.append(os.path.join(root, file))
    return pom_files


def read_patterns_from_file(pattern_file: str) -> List[str]:
    """
    Read patterns from a file (one pattern per line).

    Args:
        pattern_file: Path to the file containing patterns

    Returns:
        List of patterns (empty lines and comments starting with # are ignored)
    """
    patterns = []
    if not os.path.isfile(pattern_file):
        print(f"[WARNING] Pattern file not found: {pattern_file}")
        return patterns

    try:
        with open(pattern_file, "r") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    patterns.append(line)
        print(f"[INFO] Read {len(patterns)} pattern(s) from {pattern_file}")
    except Exception as e:
        print(f"[ERROR] Failed to read pattern file {pattern_file}: {e}")

    return patterns


def parse_test_patterns_from_test_sh(test_sh_path: str) -> tuple:
    """
    Parse INCLUDE_TESTS and EXCLUDE_TESTS variables from test.sh.

    Expected format in test.sh:
        INCLUDE_TESTS="**/Test1.java,**/Test2.java"
        EXCLUDE_TESTS="**/FailingTest.java"

    Args:
        test_sh_path: Path to the test.sh file

    Returns:
        Tuple of (include_patterns, exclude_patterns) as lists
    """
    include_patterns = []
    exclude_patterns = []

    if not os.path.isfile(test_sh_path):
        print(f"[WARNING] test.sh not found: {test_sh_path}")
        return include_patterns, exclude_patterns

    try:
        with open(test_sh_path, "r") as f:
            content = f.read()

        # Parse INCLUDE_TESTS variable
        # Match patterns like: INCLUDE_TESTS="..." or INCLUDE_TESTS='...'
        include_match = re.search(r'INCLUDE_TESTS=["\']([^"\']*)["\']', content)
        if include_match:
            include_value = include_match.group(1).strip()
            if include_value:
                # Split by comma and strip whitespace
                include_patterns = [p.strip() for p in include_value.split(",") if p.strip()]
                print(f"[INFO] Parsed {len(include_patterns)} INCLUDE_TESTS pattern(s) from test.sh")

        # Parse EXCLUDE_TESTS variable
        exclude_match = re.search(r'EXCLUDE_TESTS=["\']([^"\']*)["\']', content)
        if exclude_match:
            exclude_value = exclude_match.group(1).strip()
            if exclude_value:
                # Split by comma and strip whitespace
                exclude_patterns = [p.strip() for p in exclude_value.split(",") if p.strip()]
                print(f"[INFO] Parsed {len(exclude_patterns)} EXCLUDE_TESTS pattern(s) from test.sh")

    except Exception as e:
        print(f"[ERROR] Failed to parse test.sh {test_sh_path}: {e}")

    return include_patterns, exclude_patterns


def get_pom_tree_and_plugins(pom_path: str) -> tuple:
    """Parse pom.xml and return tree and plugins element."""
    tree = ET.parse(pom_path)
    root = tree.getroot()

    ET.register_namespace("", MAVEN_NAMESPACE)
    ns = "{" + MAVEN_NAMESPACE + "}"

    # Find or create build element
    build = root.find(ns + "build")
    if build is None:
        build = ET.Element("build")
        root.append(build)

    # Find or create plugins element
    plugins = build.find(ns + "plugins")
    if plugins is None:
        plugins = ET.Element("plugins")
        build.append(plugins)

    return tree, plugins, ns


def create_plugin_node(group_id: str, artifact_id: str, version: str) -> ET.Element:
    """Create a Maven plugin XML element."""
    plugin = ET.Element("plugin")

    group_elem = ET.SubElement(plugin, "groupId")
    group_elem.text = group_id

    artifact_elem = ET.SubElement(plugin, "artifactId")
    artifact_elem.text = artifact_id

    version_elem = ET.SubElement(plugin, "version")
    version_elem.text = version

    return plugin


def create_surefire_plugin(project_name: str, tool_name: str) -> ET.Element:
    """Create maven-surefire-plugin element with RTS configuration."""
    plugin = create_plugin_node(
        "org.apache.maven.plugins", "maven-surefire-plugin", SUREFIRE_VERSION
    )

    # OpenClover doesn't use excludesFile - it handles test selection internally
    if tool_name not in ("openclover"):
        configuration = ET.SubElement(plugin, "configuration")
        excludes_file = ET.SubElement(configuration, "excludesFile")
        # Use unique exclude file path per project and tool
        prefix_path = "/tmp/" + project_name
        exclude_target = f"_{tool_name}Excludes"
        excludes_file.text = prefix_path + exclude_target

    return plugin


def create_rts_plugin(tool_name: str) -> ET.Element:
    """Create RTS tool plugin element (Ekstazi, JcgEks, or OpenClover)."""
    tool_config = RTS_TOOLS.get(tool_name)
    if not tool_config:
        raise ValueError(f"Unknown RTS tool: {tool_name}")

    plugin = create_plugin_node(
        tool_config["group_id"], tool_config["artifact_id"], tool_config["version"]
    )

    if tool_name == "openclover":
        # OpenClover uses different configuration - snapshot for test optimization
        configuration = ET.SubElement(plugin, "configuration")
        snapshot = ET.SubElement(configuration, "snapshot")
        snapshot.text = "${user.home}/.clover/clover.snapshot"
    else:
        # Ekstazi and JcgEks use select/restore goals
        executions = ET.SubElement(plugin, "executions")
        execution = ET.SubElement(executions, "execution")

        execution_id = ET.SubElement(execution, "id")
        execution_id.text = tool_name

        goals = ET.SubElement(execution, "goals")
        goal_select = ET.SubElement(goals, "goal")
        goal_select.text = "select"
        goal_restore = ET.SubElement(goals, "goal")
        goal_restore.text = "restore"

    return plugin


def delete_surefire_config_element(
    directory: str, target_name: str, replace: Optional[str] = None
) -> None:
    """Delete or replace a surefire configuration element in all pom.xml files."""
    ns = "{" + MAVEN_NAMESPACE + "}"

    for pom_path in find_pom_files(directory):
        try:
            tree = ET.parse(pom_path)
            root = tree.getroot()
            ET.register_namespace("", MAVEN_NAMESPACE)

            modified = False
            plugin_list = root.findall(".//" + ns + "plugin")

            for plugin in plugin_list:
                artifact_id = plugin.find(".//" + ns + "artifactId")
                if artifact_id is not None and artifact_id.text == "maven-surefire-plugin":
                    configuration = plugin.find(".//" + ns + "configuration")
                    if configuration is not None:
                        target = configuration.find(ns + target_name)
                        if target is not None:
                            if replace is None:
                                configuration.remove(target)
                            else:
                                target.text = replace
                            modified = True
                        elif replace is not None:
                            target = ET.SubElement(configuration, target_name)
                            target.text = replace
                            modified = True

            if modified:
                tree.write(pom_path, encoding="utf-8", xml_declaration=True)
                format_xml_with_xmllint(pom_path)

        except ET.ParseError as e:
            print(f"[WARNING] Failed to parse {pom_path}: {e}")


def add_excludes_file_to_surefire(pom_path: str, project_name: str, tool_name: str) -> bool:
    """Add excludesFile configuration to existing surefire plugin."""
    ns = "{" + MAVEN_NAMESPACE + "}"

    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ET.register_namespace("", MAVEN_NAMESPACE)

        # Find existing surefire plugin
        for plugin in root.findall(".//" + ns + "plugin"):
            artifact_id = plugin.find(ns + "artifactId")
            if artifact_id is not None and artifact_id.text == "maven-surefire-plugin":
                # Find or create configuration element
                configuration = plugin.find(ns + "configuration")
                if configuration is None:
                    configuration = ET.SubElement(plugin, "configuration")

                # Add or update excludesFile
                excludes_file = configuration.find(ns + "excludesFile")
                if excludes_file is None:
                    excludes_file = ET.SubElement(configuration, "excludesFile")

                # Set excludesFile path
                prefix_path = "/tmp/" + project_name
                exclude_target = f"_{tool_name}Excludes"
                excludes_file.text = prefix_path + exclude_target

                tree.write(pom_path, encoding="utf-8", xml_declaration=True)
                format_xml_with_xmllint(pom_path)
                return True

        # No existing surefire plugin found
        return False

    except Exception as e:
        print(f"[ERROR] Failed to modify surefire in {pom_path}: {e}")
        return False


def add_excludes_to_surefire(pom_path: str, exclude_patterns: List[str]) -> bool:
    """
    Add exclude patterns to surefire plugin configuration in pom.xml.

    Args:
        pom_path: Path to the pom.xml file
        exclude_patterns: List of exclude patterns (regex or ant-style patterns)

    Returns:
        True if modification succeeded, False otherwise
    """
    if not exclude_patterns:
        return True

    ns = "{" + MAVEN_NAMESPACE + "}"

    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ET.register_namespace("", MAVEN_NAMESPACE)

        # Find build element
        build = root.find(ns + "build")
        if build is None:
            build = root.find("build")
        if build is None:
            print(f"[WARNING] No <build> element found in {pom_path}, skipping excludes")
            return True

        # Find plugins element
        plugins = build.find(ns + "plugins")
        if plugins is None:
            plugins = build.find("plugins")
        if plugins is None:
            print(f"[WARNING] No <build><plugins> element found in {pom_path}, skipping excludes")
            return True

        # Determine namespace used in this pom.xml
        plugin_ns = ns if plugins.find(ns + "plugin") is not None else ""

        # Find surefire plugin
        surefire_plugin = None
        for plugin in plugins:
            if plugin.tag == plugin_ns + "plugin" or plugin.tag == "plugin":
                artifact_id = plugin.find(plugin_ns + "artifactId")
                if artifact_id is None:
                    artifact_id = plugin.find("artifactId")
                if artifact_id is not None and artifact_id.text == "maven-surefire-plugin":
                    surefire_plugin = plugin
                    break

        if surefire_plugin is None:
            # Create new surefire plugin with excludes
            surefire_plugin = ET.Element("plugin")
            group_elem = ET.SubElement(surefire_plugin, "groupId")
            group_elem.text = "org.apache.maven.plugins"
            artifact_elem = ET.SubElement(surefire_plugin, "artifactId")
            artifact_elem.text = "maven-surefire-plugin"
            version_elem = ET.SubElement(surefire_plugin, "version")
            version_elem.text = SUREFIRE_VERSION
            plugins.append(surefire_plugin)
            print(f"[INFO] Created new surefire plugin for excludes")

        # Find or create configuration element
        configuration = surefire_plugin.find(plugin_ns + "configuration")
        if configuration is None:
            configuration = surefire_plugin.find("configuration")
        if configuration is None:
            configuration = ET.SubElement(surefire_plugin, "configuration")

        # Find or create excludes element
        excludes = configuration.find(plugin_ns + "excludes")
        if excludes is None:
            excludes = configuration.find("excludes")
        if excludes is None:
            excludes = ET.SubElement(configuration, "excludes")

        # Get existing exclude patterns to avoid duplicates
        existing_patterns = set()
        for exclude in excludes:
            if exclude.text:
                existing_patterns.add(exclude.text.strip())

        # Add new exclude patterns
        added_count = 0
        for pattern in exclude_patterns:
            if pattern not in existing_patterns:
                exclude_elem = ET.SubElement(excludes, "exclude")
                exclude_elem.text = pattern
                existing_patterns.add(pattern)
                added_count += 1

        if added_count > 0:
            tree.write(pom_path, encoding="utf-8", xml_declaration=True)
            format_xml_with_xmllint(pom_path)
            print(f"[INFO] Added {added_count} exclude pattern(s) to {pom_path}")

        return True

    except Exception as e:
        print(f"[ERROR] Failed to add excludes to {pom_path}: {e}")
        return False


def add_includes_to_surefire(pom_path: str, include_patterns: List[str]) -> bool:
    """
    Add include patterns to surefire plugin configuration in pom.xml.

    Args:
        pom_path: Path to the pom.xml file
        include_patterns: List of include patterns (regex or ant-style patterns)

    Returns:
        True if modification succeeded, False otherwise
    """
    if not include_patterns:
        return True

    ns = "{" + MAVEN_NAMESPACE + "}"

    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ET.register_namespace("", MAVEN_NAMESPACE)

        # Find build element
        build = root.find(ns + "build")
        if build is None:
            build = root.find("build")
        if build is None:
            print(f"[WARNING] No <build> element found in {pom_path}, skipping includes")
            return True

        # Find plugins element
        plugins = build.find(ns + "plugins")
        if plugins is None:
            plugins = build.find("plugins")
        if plugins is None:
            print(f"[WARNING] No <build><plugins> element found in {pom_path}, skipping includes")
            return True

        # Determine namespace used in this pom.xml
        plugin_ns = ns if plugins.find(ns + "plugin") is not None else ""

        # Find surefire plugin
        surefire_plugin = None
        for plugin in plugins:
            if plugin.tag == plugin_ns + "plugin" or plugin.tag == "plugin":
                artifact_id = plugin.find(plugin_ns + "artifactId")
                if artifact_id is None:
                    artifact_id = plugin.find("artifactId")
                if artifact_id is not None and artifact_id.text == "maven-surefire-plugin":
                    surefire_plugin = plugin
                    break

        if surefire_plugin is None:
            # Create new surefire plugin with includes
            surefire_plugin = ET.Element("plugin")
            group_elem = ET.SubElement(surefire_plugin, "groupId")
            group_elem.text = "org.apache.maven.plugins"
            artifact_elem = ET.SubElement(surefire_plugin, "artifactId")
            artifact_elem.text = "maven-surefire-plugin"
            version_elem = ET.SubElement(surefire_plugin, "version")
            version_elem.text = SUREFIRE_VERSION
            plugins.append(surefire_plugin)
            print(f"[INFO] Created new surefire plugin for includes")

        # Find or create configuration element
        configuration = surefire_plugin.find(plugin_ns + "configuration")
        if configuration is None:
            configuration = surefire_plugin.find("configuration")
        if configuration is None:
            configuration = ET.SubElement(surefire_plugin, "configuration")

        # Find or create includes element
        includes = configuration.find(plugin_ns + "includes")
        if includes is None:
            includes = configuration.find("includes")
        if includes is None:
            includes = ET.SubElement(configuration, "includes")

        # Get existing include patterns to avoid duplicates
        existing_patterns = set()
        for include in includes:
            if include.text:
                existing_patterns.add(include.text.strip())

        # Add new include patterns
        added_count = 0
        for pattern in include_patterns:
            if pattern not in existing_patterns:
                include_elem = ET.SubElement(includes, "include")
                include_elem.text = pattern
                existing_patterns.add(pattern)
                added_count += 1

        if added_count > 0:
            tree.write(pom_path, encoding="utf-8", xml_declaration=True)
            format_xml_with_xmllint(pom_path)
            print(f"[INFO] Added {added_count} include pattern(s) to {pom_path}")

        return True

    except Exception as e:
        print(f"[ERROR] Failed to add includes to {pom_path}: {e}")
        return False


def add_rts_plugins_to_pom(pom_path: str, project_name: str, tool_name: str) -> bool:
    """Add RTS tool plugin and configure surefire excludesFile in a pom.xml file."""
    ns = "{" + MAVEN_NAMESPACE + "}"

    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ET.register_namespace("", MAVEN_NAMESPACE)

        # Find build element (must exist)
        build = root.find(ns + "build")
        if build is None:
            # Try without namespace (some pom.xml may not have namespace)
            build = root.find("build")
        if build is None:
            print(f"[WARNING] No <build> element found in {pom_path}, skipping")
            return True

        # Find plugins element - try with and without namespace
        plugins = build.find(ns + "plugins")
        if plugins is None:
            plugins = build.find("plugins")
        if plugins is None:
            print(f"[WARNING] No <build><plugins> element found in {pom_path}, skipping")
            return True

        # Determine namespace used in this pom.xml
        # Check first plugin to see if it has namespace
        first_plugin = plugins.find(ns + "plugin")
        if first_plugin is None:
            first_plugin = plugins.find("plugin")
        plugin_ns = ns if plugins.find(ns + "plugin") is not None else ""

        # Check if surefire plugin already exists
        surefire_plugin = None
        for plugin in plugins:
            if plugin.tag == plugin_ns + "plugin" or plugin.tag == "plugin":
                artifact_id = plugin.find(plugin_ns + "artifactId")
                if artifact_id is None:
                    artifact_id = plugin.find("artifactId")
                if artifact_id is not None and artifact_id.text == "maven-surefire-plugin":
                    surefire_plugin = plugin
                    break

        # Add excludesFile to existing surefire or create new one
        # OpenClover doesn't use excludesFile - it handles test selection internally
        if tool_name != "openclover":
            if surefire_plugin is not None:
                # Add excludesFile to existing surefire
                configuration = surefire_plugin.find(plugin_ns + "configuration")
                if configuration is None:
                    configuration = surefire_plugin.find("configuration")
                if configuration is None:
                    configuration = ET.SubElement(surefire_plugin, "configuration")

                excludes_file = configuration.find(plugin_ns + "excludesFile")
                if excludes_file is None:
                    excludes_file = configuration.find("excludesFile")
                if excludes_file is None:
                    excludes_file = ET.SubElement(configuration, "excludesFile")

                prefix_path = "/tmp/" + project_name
                exclude_target = f"_{tool_name}Excludes"
                excludes_file.text = prefix_path + exclude_target
                print(f"[INFO] Added excludesFile to existing surefire plugin")
            else:
                # Create new surefire plugin
                new_surefire = create_surefire_plugin(project_name, tool_name)
                plugins.append(new_surefire)
                print(f"[INFO] Created new surefire plugin")

        # Check if RTS plugin already exists
        rts_config = RTS_TOOLS.get(tool_name)
        rts_exists = False
        if rts_config:
            for plugin in plugins:
                if plugin.tag == plugin_ns + "plugin" or plugin.tag == "plugin":
                    artifact_id = plugin.find(plugin_ns + "artifactId")
                    if artifact_id is None:
                        artifact_id = plugin.find("artifactId")
                    if artifact_id is not None and artifact_id.text == rts_config["artifact_id"]:
                        rts_exists = True
                        break

        # Add RTS tool plugin if not exists
        if not rts_exists:
            rts_plugin = create_rts_plugin(tool_name)
            plugins.append(rts_plugin)
            print(f"[INFO] Added {tool_name} plugin")
        else:
            print(f"[INFO] {tool_name} plugin already exists")

        tree.write(pom_path, encoding="utf-8", xml_declaration=True)
        format_xml_with_xmllint(pom_path)
        return True

    except Exception as e:
        print(f"[ERROR] Failed to modify {pom_path}: {e}")
        return False


def configure_surefire_settings(project_path: str) -> None:
    """Configure surefire settings for RTS compatibility."""
    # Currently no modifications needed - keep existing surefire settings
    pass


def cleanup_rts_artifacts(project_path: str) -> None:
    """Clean up existing RTS artifacts from previous runs."""
    artifacts_to_delete = [".ekstazi", ".jcg", "diffLog", "classes-javacg_merged.jar-output_javacg"]

    for root, dirs, _ in os.walk(project_path):
        for dir_name in dirs:
            if dir_name in artifacts_to_delete:
                dir_path = os.path.join(root, dir_name)
                try:
                    shutil.rmtree(dir_path)
                    print(f"[INFO] Deleted: {dir_path}")
                except OSError as e:
                    print(f"[WARNING] Failed to delete {dir_path}: {e}")




def git_commit_changes(project_path: str, tool_name: str) -> bool:
    """Commit RTS configuration changes to git."""
    print("[INFO] Committing RTS configuration changes...")

    # Add project path to safe.directory
    execute_cmd(f"git config --global --add safe.directory {project_path}", cwd=project_path)

    # Set git user config
    execute_cmd('git config --global user.email "you@example.com"', cwd=project_path)
    execute_cmd('git config --global user.name "Your Name"', cwd=project_path)

    # Initialize git if not already initialized
    git_dir = os.path.join(project_path, ".git")
    if not os.path.exists(git_dir):
        print("[INFO] Git not initialized, running git init...")
        if not execute_cmd("git init", cwd=project_path):
            return False

    # Stage all changes
    if not execute_cmd("git add -A", cwd=project_path):
        return False

    # Commit with descriptive message
    commit_msg = f"[RTS] Configure {tool_name} for regression test selection"
    if not execute_cmd(f'git commit -m "{commit_msg}" --allow-empty', cwd=project_path):
        return False

    print(f"[INFO] Changes committed: {commit_msg}")
    return True


# Fixed path to test.sh for parsing INCLUDE_TESTS and EXCLUDE_TESTS
TEST_SH_PATH = "/built-src/test.sh"


def init_rts(
    project_path: str,
    tool_name: str,
) -> bool:
    """
    Initialize RTS tool configuration for a Java project.

    Parses INCLUDE_TESTS and EXCLUDE_TESTS from /$SRC/test.sh.

    Args:
        project_path: Path to the Java project root
        tool_name: RTS tool to use (ekstazi, jcgeks, or openclover)

    Returns:
        True if initialization succeeded, False otherwise
    """
    project_path = os.path.abspath(project_path)
    project_name = os.path.basename(project_path.rstrip("/"))

    print(f"[INFO] Initializing RTS ({tool_name}) for project: {project_name}")
    print(f"[INFO] Project path: {project_path}")

    # Validate tool name
    if tool_name not in RTS_TOOLS:
        print(f"[ERROR] Unknown RTS tool: {tool_name}")
        print(f"[ERROR] Available tools: {list(RTS_TOOLS.keys())}")
        return False

    # Find all pom.xml files
    pom_files = find_pom_files(project_path)
    if not pom_files:
        print("[ERROR] No pom.xml files found in project")
        return False

    print(f"[INFO] Found {len(pom_files)} pom.xml file(s)")

    # Ensure xmllint is available for XML formatting
    ensure_xmllint_installed()

    # Step 1: Clean up existing RTS artifacts (but keep build artifacts)
    print("[INFO] Step 1: Cleaning up existing RTS artifacts...")
    cleanup_rts_artifacts(project_path)

    # Step 2: Install RTS tool dependencies
    # OpenClover is fetched from Maven Central, no manual installation needed
    if tool_name == "openclover":
        print(f"[INFO] Step 2: Skipping dependency installation for {tool_name} (fetched from Maven Central)")
    elif tool_name == "jcgeks":
        print(f"[INFO] Step 2: Installing {tool_name} dependencies...")
        if not install_jcgeks_jars():
            print("[ERROR] Failed to install JcgEks dependencies")
            return False
    elif tool_name == "ekstazi":
        print(f"[INFO] Step 2: Installing {tool_name} dependencies...")
        if not install_ekstazi_jars():
            print("[ERROR] Failed to install Ekstazi dependencies")
            return False

    # Step 3: Add RTS plugins to all pom.xml files
    print("[INFO] Step 3: Adding RTS plugins to pom.xml files...")
    for pom_path in pom_files:
        if add_rts_plugins_to_pom(pom_path, project_name, tool_name):
            print(f"[INFO] Modified: {pom_path}")
        else:
            print(f"[WARNING] Failed to modify: {pom_path}")

    # Step 4: Configure surefire settings
    print("[INFO] Step 4: Configuring surefire settings...")
    configure_surefire_settings(project_path)

    # Step 5: Parse and add test patterns from $SRC/test.sh
    print(f"[INFO] Step 5: Parsing test patterns from {TEST_SH_PATH}...")
    include_patterns, exclude_patterns = parse_test_patterns_from_test_sh(TEST_SH_PATH)

    # Add include patterns
    if include_patterns:
        print(f"[INFO] Step 5a: Adding {len(include_patterns)} include pattern(s) to surefire...")
        for pom_path in pom_files:
            if add_includes_to_surefire(pom_path, include_patterns):
                print(f"[INFO] Added includes to: {pom_path}")
            else:
                print(f"[WARNING] Failed to add includes to: {pom_path}")
    else:
        print("[INFO] No INCLUDE_TESTS patterns found in test.sh")

    # Add exclude patterns
    if exclude_patterns:
        print(f"[INFO] Step 5b: Adding {len(exclude_patterns)} exclude pattern(s) to surefire...")
        for pom_path in pom_files:
            if add_excludes_to_surefire(pom_path, exclude_patterns):
                print(f"[INFO] Added excludes to: {pom_path}")
            else:
                print(f"[WARNING] Failed to add excludes to: {pom_path}")
    else:
        print("[INFO] No EXCLUDE_TESTS patterns found in test.sh")

    # Step 5c: Add OpenClover internal class exclusion pattern
    # OpenClover generates inner classes (e.g., TestUtils$__CLR*) that must be excluded
    if tool_name == "openclover":
        print(f"[INFO] Step 5c: Adding OpenClover internal class exclusion pattern...")
        openclover_excludes = [OPENCLOVER_EXCLUDE_PATTERN]
        for pom_path in pom_files:
            if add_excludes_to_surefire(pom_path, openclover_excludes):
                print(f"[INFO] Added OpenClover excludes to: {pom_path}")
            else:
                print(f"[WARNING] Failed to add OpenClover excludes to: {pom_path}")

    # Step 6: Commit changes to git
    print("[INFO] Step 6: Committing changes to git...")
    git_commit_changes(project_path, tool_name)

    print("[INFO] RTS initialization completed successfully!")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Initialize RTS (Regression Test Selection) for Java projects"
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Path to the Java project root (default: current directory)",
    )
    parser.add_argument(
        "--tool",
        choices=list(RTS_TOOLS.keys()),
        default=os.environ.get("RTS_TOOL", "jcgeks"),
        help="RTS tool to use (default: jcgeks or RTS_TOOL env var)",
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Install RTS dependencies only (Ekstazi and JcgEks, no project configuration)",
    )

    args = parser.parse_args()

    # Handle --install-deps option
    if args.install_deps:
        success = install_all_rts_deps()
        sys.exit(0 if success else 1)

    if not os.path.isdir(args.project_path):
        print(f"[ERROR] Project path does not exist: {args.project_path}")
        sys.exit(1)

    success = init_rts(args.project_path, args.tool)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
