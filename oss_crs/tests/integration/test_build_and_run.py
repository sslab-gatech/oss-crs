"""Integration tests for build-target and run commands.

These tests use embedded fixtures for a minimal mock-c project and
require Docker to be available.
"""

import json
import sys
import subprocess
import pytest
import yaml
import shutil
from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.docker]

REPO_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def cli_runner(request):
    """Return a function to run the oss-crs CLI.

    Logs are saved to oss_crs/tests/integration/.logs/<test_name>/<call>_<command>/
    """
    test_name = request.node.name
    log_dir = Path(__file__).parent / ".logs" / test_name
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    call_count = [0]  # mutable counter

    def run(*args, check=False, timeout=120):
        cmd = [sys.executable, "-m", "oss_crs.src.cli.crs_compose"] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=timeout,
        )

        # Save logs to separate files
        call_count[0] += 1
        command = args[0] if args else "unknown"
        call_dir = log_dir / f"{call_count[0]:02d}_{command}"
        call_dir.mkdir(exist_ok=True)

        (call_dir / "command.log").write_text(" ".join(cmd))
        (call_dir / "returncode.log").write_text(str(result.returncode))
        (call_dir / "stdout.log").write_text(result.stdout)
        (call_dir / "stderr.log").write_text(result.stderr)

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result
    return run


@pytest.fixture
def mock_project_path():
    """Return path to the embedded mock-c OSS-Fuzz project."""
    return FIXTURES_DIR / "mock-c-project"


@pytest.fixture
def mock_java_project_path():
    """Return path to the embedded mock-java OSS-Fuzz project."""
    return FIXTURES_DIR / "mock-java-project"


def _init_git_repo(dst):
    """Initialize a directory as a git repo with initial commit."""
    subprocess.run(["git", "init"], cwd=dst, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=dst, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=dst, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=dst, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=dst, check=True, capture_output=True)


@pytest.fixture
def mock_repo_path(tmp_dir):
    """Copy embedded mock-c repo to tmp_dir and init as git repo.

    The build process expects the target repo to be a git repository.
    """
    src = FIXTURES_DIR / "mock-c-repo"
    dst = tmp_dir / "mock-c"
    shutil.copytree(src, dst)
    _init_git_repo(dst)
    return dst


@pytest.fixture
def mock_java_repo_path(tmp_dir):
    """Copy embedded mock-java repo to tmp_dir and init as git repo."""
    src = FIXTURES_DIR / "mock-java-repo"
    dst = tmp_dir / "mock-java"
    shutil.copytree(src, dst)
    _init_git_repo(dst)
    return dst


@pytest.fixture
def compose_file(tmp_dir):
    """Create a minimal compose file for testing."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "8G"},
        "crs-libfuzzer": {"cpuset": "4-7", "memory": "8G"},
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.fixture
def work_dir(tmp_dir):
    """Create a work directory for CRS operations."""
    d = tmp_dir / "work"
    d.mkdir()
    return d


def docker_available():
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class TestBuildTarget:
    """Integration tests for oss-crs build-target command."""

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_build_target_basic(
        self, cli_runner, tmp_dir, mock_project_path, mock_repo_path, compose_file, work_dir
    ):
        """Build target with minimal configuration."""
        result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--build-id", "test-build-001",
            timeout=300,
        )

        # Check command succeeded
        assert result.returncode == 0, f"build-target failed: {result.stderr}"

        # Verify build output exists (structure: crs_compose/<hash>/address/builds/<build_id>/)
        build_dirs = list(work_dir.rglob("builds/test-build-001-*"))
        assert len(build_dirs) >= 1, "Expected build output directory"

class TestRunTimeout:
    """Integration tests for oss-crs run --timeout command."""

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_run_timeout_stops_containers(
        self, cli_runner, tmp_dir, mock_project_path, mock_repo_path, compose_file, work_dir
    ):
        """Run with --timeout should stop gracefully after timeout.

        This test first builds the target, then runs with a short timeout
        to verify the timeout mechanism works.
        """
        # First, build the target
        build_result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--build-id", "timeout-test",
            timeout=300,
        )
        assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

        # Run with short timeout (5 seconds)
        run_result = cli_runner(
            "run",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--target-harness", "fuzz_parse_buffer",
            "--timeout", "5",
            "--build-id", "timeout-test",
            timeout=60,  # Give it more time than the --timeout to allow cleanup
        )

        # Run should complete (either success or graceful timeout)
        # The timeout should cause containers to stop, but the command itself
        # should exit cleanly rather than hang
        assert run_result.returncode in (0, 1), \
            f"run --timeout did not exit cleanly: {run_result.stderr}"

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_run_timeout_produces_artifacts(
        self, cli_runner, tmp_dir, mock_project_path, mock_repo_path, compose_file, work_dir
    ):
        """Run with --timeout should still produce artifacts."""
        # Build target
        build_result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--build-id", "artifacts-test",
            timeout=300,
        )
        assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

        # Run with timeout
        cli_runner(
            "run",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--target-harness", "fuzz_parse_buffer",
            "--timeout", "10",
            "--build-id", "artifacts-test",
            "--run-id", "test-run",
            timeout=60,
        )

        # Use artifacts command to verify run was created
        artifacts_result = cli_runner(
            "artifacts",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_project_path),
            "--target-repo-path", str(mock_repo_path),
            "--target-harness", "fuzz_parse_buffer",
            "--build-id", "artifacts-test",
            "--run-id", "test-run",
        )

        assert artifacts_result.returncode == 0, f"artifacts failed: {artifacts_result.stderr}"
        artifacts = json.loads(artifacts_result.stdout)
        assert "build_id" in artifacts
        assert "run_id" in artifacts
        assert artifacts["run_id"].startswith("test-run")


class TestBuildTargetErrors:
    """Error handling tests for build-target (no Docker needed)."""

    def test_missing_compose_file(self, cli_runner, tmp_dir, mock_project_path):
        """Error when compose file doesn't exist."""
        result = cli_runner(
            "build-target",
            "--compose-file", "/nonexistent/compose.yaml",
            "--target-proj-path", str(mock_project_path),
        )

        assert result.returncode != 0

    def test_missing_target_proj_path(self, cli_runner, tmp_dir, compose_file, work_dir):
        """Error when target project path doesn't exist."""
        result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", "/nonexistent/project",
        )

        assert result.returncode != 0


class TestMockJava:
    """Integration tests for mock-java (JVM) target."""

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_build_mock_java(
        self, cli_runner, tmp_dir, mock_java_project_path, mock_java_repo_path, compose_file, work_dir
    ):
        """Build mock-java target."""
        result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_java_project_path),
            "--target-repo-path", str(mock_java_repo_path),
            "--build-id", "java-build-001",
            timeout=600,  # Java builds take longer
        )

        assert result.returncode == 0, f"build-target failed: {result.stderr}"

        # Verify build output exists
        build_dirs = list(work_dir.rglob("builds/java-build-001-*"))
        assert len(build_dirs) >= 1, "Expected build output directory"

    @pytest.mark.skipif(not docker_available(), reason="Docker not available")
    def test_run_mock_java(
        self, cli_runner, tmp_dir, mock_java_project_path, mock_java_repo_path, compose_file, work_dir
    ):
        """Build and run mock-java target with timeout."""
        # Build first
        build_result = cli_runner(
            "build-target",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_java_project_path),
            "--target-repo-path", str(mock_java_repo_path),
            "--build-id", "java-run-test",
            timeout=600,
        )
        assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

        # Run with timeout
        run_result = cli_runner(
            "run",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_java_project_path),
            "--target-repo-path", str(mock_java_repo_path),
            "--target-harness", "OssFuzz1",
            "--timeout", "10",
            "--build-id", "java-run-test",
            "--run-id", "java-run",
            timeout=120,
        )

        # Run should complete (either success or graceful timeout)
        assert run_result.returncode in (0, 1), \
            f"run --timeout did not exit cleanly: {run_result.stderr}"

        # Verify artifacts
        artifacts_result = cli_runner(
            "artifacts",
            "--compose-file", str(compose_file),
            "--work-dir", str(work_dir),
            "--target-proj-path", str(mock_java_project_path),
            "--target-repo-path", str(mock_java_repo_path),
            "--target-harness", "OssFuzz1",
            "--build-id", "java-run-test",
            "--run-id", "java-run",
        )

        assert artifacts_result.returncode == 0, f"artifacts failed: {artifacts_result.stderr}"
        artifacts = json.loads(artifacts_result.stdout)
        assert "build_id" in artifacts
        assert "run_id" in artifacts
