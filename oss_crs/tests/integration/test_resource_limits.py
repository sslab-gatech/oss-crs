"""Integration tests for resource limit enforcement."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def _memory_to_bytes(memory: str) -> int:
    match = re.fullmatch(r"(\d+)([KMG])", memory.strip().upper())
    if not match:
        raise ValueError(f"Unsupported test memory format: {memory}")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "K":
        return value * 1024
    if unit == "M":
        return value * 1024 * 1024
    return value * 1024 * 1024 * 1024


def _extract_field(text: str, key: str) -> str:
    for line in text.splitlines():
        marker = f"{key}:"
        if marker in line:
            return line.split(marker, 1)[1].strip()
    raise AssertionError(f"Missing field '{key}' in text:\n{text}")


@pytest.fixture
def mock_project_path():
    return FIXTURES_DIR / "mock-c-project"


@pytest.fixture
def mock_repo_path(tmp_dir):
    src = FIXTURES_DIR / "mock-c-repo"
    dst = tmp_dir / "mock-c"
    shutil.copytree(src, dst)
    init_git_repo(dst)
    return dst


@pytest.fixture
def dummy_compose_file(tmp_dir):
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0", "memory": "256M"},
        "dummy-crs": {
            "source": {"local_path": str(FIXTURES_DIR / "dummy-crs")},
            "cpuset": "0-1",
            "memory": "192M",
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_build_target_applies_configured_limits(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    dummy_compose_file,
    work_dir,
):
    build_id = "resource-build"
    expected_memory = _memory_to_bytes("192M")

    result = cli_runner(
        "build-target",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert result.returncode == 0, f"build-target failed: {result.stderr}"

    build_info_files = list(work_dir.rglob("build_info.txt"))
    assert build_info_files, "Expected dummy builder output build_info.txt"

    build_info = build_info_files[0].read_text()
    memory_max = int(_extract_field(build_info, "memory_max"))
    cpuset = _extract_field(build_info, "cpuset")

    assert memory_max == expected_memory
    assert cpuset == "0-1"


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_run_enforces_memory_limits_in_active_mode(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    dummy_compose_file,
    work_dir,
):
    build_id = "resource-build"
    run_id = "resource-run"
    expected_memory = _memory_to_bytes("192M")

    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    run_result = cli_runner(
        "run",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--build-id",
        build_id,
        "--run-id",
        run_id,
        "--timeout",
        "25",
        timeout=90,
    )
    assert run_result.returncode in (0, 1), f"run failed unexpectedly: {run_result.stderr}"

    memory_logs = sorted(work_dir.rglob("*_memory_test.txt"))
    assert memory_logs, "Expected memory logs from dummy CRS modules"

    uses_cgroup_parent = "Created cgroups at:" in run_result.stdout
    observed_max_values: list[str] = []

    for log_path in memory_logs:
        text = log_path.read_text()
        assert "env_memory_limit: \"192M\"" in text
        assert "ALLOCATED chunk" in text
        observed_max_values.append(_extract_field(text, "memory.max"))

    if uses_cgroup_parent:
        assert any(value == "max" for value in observed_max_values)
    else:
        numeric = [int(value) for value in observed_max_values if value.isdigit()]
        assert numeric, f"Expected numeric memory.max values, got: {observed_max_values}"
        assert all(value == expected_memory for value in numeric)
