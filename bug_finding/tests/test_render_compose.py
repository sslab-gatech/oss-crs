"""Tests for render_compose.py module."""

import logging
import tempfile
from pathlib import Path

from bug_finding.src.render_compose.config import (
    format_cpu_list,
    parse_cpu_range,
)
from bug_finding.src.render_compose.helpers import (
    expand_volume_vars,
    get_crs_env_vars,
    get_dot_env_vars,
    merge_env_vars,
)


class TestParseCpuRange:
    """Test parse_cpu_range function."""

    def test_simple_range(self):
        """Test simple range format like '0-7'."""
        result = parse_cpu_range('0-7')
        assert result == [0, 1, 2, 3, 4, 5, 6, 7]

    def test_single_core(self):
        """Test single core specification."""
        result = parse_cpu_range('5')
        assert result == [5]

    def test_comma_separated_list(self):
        """Test comma-separated list format like '0,2,4,6'."""
        result = parse_cpu_range('0,2,4,6')
        assert result == [0, 2, 4, 6]

    def test_mixed_format(self):
        """Test mixed format with ranges and individual cores."""
        result = parse_cpu_range('0-3,8,12-15')
        assert result == [0, 1, 2, 3, 8, 12, 13, 14, 15]

    def test_with_spaces(self):
        """Test that spaces are handled correctly."""
        result = parse_cpu_range('0-3, 8, 12-15')
        assert result == [0, 1, 2, 3, 8, 12, 13, 14, 15]

    def test_duplicates_removed(self):
        """Test that duplicate cores are removed."""
        result = parse_cpu_range('0-3,2-5')
        assert result == [0, 1, 2, 3, 4, 5]

    def test_unsorted_input(self):
        """Test that output is sorted even with unsorted input."""
        result = parse_cpu_range('8,0-3,5')
        assert result == [0, 1, 2, 3, 5, 8]

    def test_large_range(self):
        """Test larger CPU range."""
        result = parse_cpu_range('0-15')
        assert result == list(range(16))

    def test_non_zero_start_range(self):
        """Test range not starting at 0."""
        result = parse_cpu_range('4-11')
        assert result == [4, 5, 6, 7, 8, 9, 10, 11]


class TestFormatCpuList:
    """Test format_cpu_list function."""

    def test_simple_list(self):
        """Test formatting a simple list."""
        result = format_cpu_list([0, 1, 2, 3])
        assert result == '0,1,2,3'

    def test_non_contiguous_list(self):
        """Test formatting non-contiguous cores."""
        result = format_cpu_list([0, 2, 4, 6])
        assert result == '0,2,4,6'

    def test_single_core(self):
        """Test formatting single core."""
        result = format_cpu_list([5])
        assert result == '5'

    def test_large_list(self):
        """Test formatting larger list."""
        result = format_cpu_list([0, 1, 2, 3, 4, 5, 6, 7])
        assert result == '0,1,2,3,4,5,6,7'


class TestRoundTrip:
    """Test round-trip conversion (parse -> format)."""

    def test_range_format(self):
        """Test that range format survives round-trip as comma-separated."""
        parsed = parse_cpu_range('0-7')
        formatted = format_cpu_list(parsed)
        assert formatted == '0,1,2,3,4,5,6,7'
        # Re-parse to verify consistency
        reparsed = parse_cpu_range(formatted)
        assert reparsed == parsed

    def test_list_format(self):
        """Test that list format survives round-trip."""
        original = '0,2,4,6'
        parsed = parse_cpu_range(original)
        formatted = format_cpu_list(parsed)
        assert formatted == original
        reparsed = parse_cpu_range(formatted)
        assert reparsed == parsed

    def test_mixed_format(self):
        """Test mixed format round-trip."""
        parsed = parse_cpu_range('0-3,8,12-15')
        formatted = format_cpu_list(parsed)
        reparsed = parse_cpu_range(formatted)
        assert reparsed == parsed


class TestGetCrsEnvVars:
    """Test get_crs_env_vars function."""

    def test_extracts_crs_prefixed_vars(self):
        """Test that only CRS_* prefixed vars are extracted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CRS_CACHE_DIR=/path/to/cache\n"
                "CRS_INPUT_GENS=given_fuzzer\n"
                "CRS_SKIP_SAVE=True\n"
                "POSTGRES_PASSWORD=secret\n"
                "OPENAI_API_KEY=sk-123\n"
            )
            result = get_crs_env_vars(Path(tmpdir))
            assert result == ['CRS_CACHE_DIR', 'CRS_INPUT_GENS', 'CRS_SKIP_SAVE']

    def test_returns_sorted_list(self):
        """Test that vars are returned sorted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CRS_ZEBRA=z\n"
                "CRS_ALPHA=a\n"
                "CRS_MIDDLE=m\n"
            )
            result = get_crs_env_vars(Path(tmpdir))
            assert result == ['CRS_ALPHA', 'CRS_MIDDLE', 'CRS_ZEBRA']

    def test_returns_empty_list_when_no_env_file(self):
        """Test that empty list is returned when .env doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_crs_env_vars(Path(tmpdir))
            assert result == []

    def test_returns_empty_list_when_no_crs_vars(self):
        """Test that empty list is returned when no CRS_* vars exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "POSTGRES_PASSWORD=secret\n"
                "OPENAI_API_KEY=sk-123\n"
            )
            result = get_crs_env_vars(Path(tmpdir))
            assert result == []


class TestGetDotEnvVars:
    """Test get_dot_env_vars function."""

    def test_loads_all_vars(self):
        """Test that all vars are loaded as dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "CRS_INPUT_GENS=given_fuzzer\n"
                "POSTGRES_PASSWORD=secret\n"
                "LITELLM_KEY=sk-123\n"
            )
            result = get_dot_env_vars(Path(tmpdir))
            assert result == {
                'CRS_INPUT_GENS': 'given_fuzzer',
                'POSTGRES_PASSWORD': 'secret',
                'LITELLM_KEY': 'sk-123'
            }

    def test_returns_empty_dict_when_no_env_file(self):
        """Test that empty dict is returned when .env doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_dot_env_vars(Path(tmpdir))
            assert result == {}

    def test_handles_empty_env_file(self):
        """Test handling of empty .env file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("")
            result = get_dot_env_vars(Path(tmpdir))
            assert result == {}


class TestMergeEnvVars:
    """Test merge_env_vars function."""

    def test_registry_only(self):
        """Test merge with only registry env vars."""
        registry_env = {'CRS_INPUT_GENS': 'given_fuzzer'}
        dot_env = {}
        result = merge_env_vars(registry_env, dot_env, 'test-crs', 'run')
        assert result == {'CRS_INPUT_GENS': 'given_fuzzer'}

    def test_dot_env_only(self):
        """Test merge with only .env vars."""
        registry_env = {}
        dot_env = {'LITELLM_KEY': 'sk-123'}
        result = merge_env_vars(registry_env, dot_env, 'test-crs', 'run')
        assert result == {'LITELLM_KEY': 'sk-123'}

    def test_no_conflict_merge(self):
        """Test merge with no conflicts (different keys)."""
        registry_env = {'CRS_INPUT_GENS': 'given_fuzzer'}
        dot_env = {'LITELLM_KEY': 'sk-123', 'POSTGRES_PASSWORD': 'secret'}
        result = merge_env_vars(registry_env, dot_env, 'test-crs', 'run')
        assert result == {
            'CRS_INPUT_GENS': 'given_fuzzer',
            'LITELLM_KEY': 'sk-123',
            'POSTGRES_PASSWORD': 'secret'
        }

    def test_dot_env_wins_on_conflict(self):
        """Test that .env wins when there's a conflict."""
        registry_env = {'CRS_INPUT_GENS': 'given_fuzzer'}
        dot_env = {'CRS_INPUT_GENS': 'all_modules'}
        result = merge_env_vars(registry_env, dot_env, 'test-crs', 'run')
        assert result == {'CRS_INPUT_GENS': 'all_modules'}

    def test_conflict_logs_warning(self, caplog):
        """Test that conflict logs a warning."""
        registry_env = {'CRS_INPUT_GENS': 'given_fuzzer'}
        dot_env = {'CRS_INPUT_GENS': 'all_modules'}

        with caplog.at_level(logging.WARNING):
            merge_env_vars(registry_env, dot_env, 'test-crs', 'run')

        assert 'overridden by .env' in caplog.text
        assert 'given_fuzzer' in caplog.text
        assert 'all_modules' in caplog.text

    def test_no_warning_when_same_value(self, caplog):
        """Test that no warning is logged when values are the same."""
        registry_env = {'CRS_INPUT_GENS': 'given_fuzzer'}
        dot_env = {'CRS_INPUT_GENS': 'given_fuzzer'}

        with caplog.at_level(logging.WARNING):
            result = merge_env_vars(registry_env, dot_env, 'test-crs', 'run')

        assert 'overridden' not in caplog.text
        assert result == {'CRS_INPUT_GENS': 'given_fuzzer'}

    def test_multiple_conflicts(self, caplog):
        """Test merge with multiple conflicts."""
        registry_env = {
            'CRS_INPUT_GENS': 'given_fuzzer',
            'CRS_CACHE_DIR': '/default/cache'
        }
        dot_env = {
            'CRS_INPUT_GENS': 'all_modules',
            'CRS_CACHE_DIR': '/custom/cache',
            'LITELLM_KEY': 'sk-123'
        }

        with caplog.at_level(logging.WARNING):
            result = merge_env_vars(registry_env, dot_env, 'test-crs', 'build')

        assert result == {
            'CRS_INPUT_GENS': 'all_modules',
            'CRS_CACHE_DIR': '/custom/cache',
            'LITELLM_KEY': 'sk-123'
        }
        # Should have two warnings
        assert caplog.text.count('overridden by .env') == 2

    def test_empty_both(self):
        """Test merge with both empty."""
        result = merge_env_vars({}, {}, 'test-crs', 'run')
        assert result == {}


class TestExpandVolumeVars:
    """Test expand_volume_vars function."""

    def test_expands_braced_var(self):
        """Test expansion of ${VAR} syntax."""
        volumes = ['${HOST_CACHE_DIR}:/cache/images:ro']
        env_vars = {'HOST_CACHE_DIR': '/tmp/cache'}
        result = expand_volume_vars(volumes, env_vars)
        assert result == ['/tmp/cache:/cache/images:ro']

    def test_expands_unbraced_var(self):
        """Test expansion of $VAR syntax."""
        volumes = ['$HOST_CACHE_DIR:/cache/images:ro']
        env_vars = {'HOST_CACHE_DIR': '/tmp/cache'}
        result = expand_volume_vars(volumes, env_vars)
        assert result == ['/tmp/cache:/cache/images:ro']

    def test_keeps_unknown_var(self):
        """Test that unknown variables are kept as-is."""
        volumes = ['${UNKNOWN_VAR}:/cache:ro']
        env_vars = {'HOST_CACHE_DIR': '/tmp/cache'}
        result = expand_volume_vars(volumes, env_vars)
        assert result == ['${UNKNOWN_VAR}:/cache:ro']

    def test_expands_multiple_vars(self):
        """Test expansion of multiple variables in one volume."""
        volumes = ['${HOST_DIR}/${SUB_DIR}:/mount:rw']
        env_vars = {'HOST_DIR': '/home', 'SUB_DIR': 'data'}
        result = expand_volume_vars(volumes, env_vars)
        assert result == ['/home/data:/mount:rw']

    def test_multiple_volumes(self):
        """Test expansion across multiple volumes."""
        volumes = [
            '${HOST_CACHE_DIR}:/cache:ro',
            '/static/path:/other:rw',
            '${DATA_DIR}:/data:rw'
        ]
        env_vars = {'HOST_CACHE_DIR': '/tmp/cache', 'DATA_DIR': '/mnt/data'}
        result = expand_volume_vars(volumes, env_vars)
        assert result == [
            '/tmp/cache:/cache:ro',
            '/static/path:/other:rw',
            '/mnt/data:/data:rw'
        ]

    def test_empty_volumes(self):
        """Test with empty volumes list."""
        result = expand_volume_vars([], {'VAR': 'value'})
        assert result == []

    def test_no_vars_in_volume(self):
        """Test volume without variables."""
        volumes = ['/host/path:/container/path:ro']
        result = expand_volume_vars(volumes, {'VAR': 'value'})
        assert result == ['/host/path:/container/path:ro']


class TestLoadCrsComposeServices:
    """Test load_crs_compose_services function."""

    def test_loads_services_from_compose_file(self):
        """Test loading services from a valid compose file."""
        from bug_finding.src.render_compose.render import load_crs_compose_services

        with tempfile.TemporaryDirectory() as tmpdir:
            compose_content = """
services:
  helper:
    image: alpine:latest
    command: ["sleep", "infinity"]
  fuzzer:
    build:
      context: .
      dockerfile: runner.Dockerfile
    privileged: true
"""
            compose_path = Path(tmpdir) / "docker-compose.yaml"
            compose_path.write_text(compose_content)

            result = load_crs_compose_services(
                crs_path=Path(tmpdir),
                compose_path="docker-compose.yaml",
                crs_memory_limit="4G",
                crs_cpuset="0,1,2,3",
            )

            assert len(result) == 2
            # Check that helper service is loaded correctly
            helper_svc = next(s for s in result if s["name"] == "helper")
            assert helper_svc["config"]["image"] == "alpine:latest"
            assert helper_svc["cpuset"] == "0,1,2,3"  # Same CPU set
            assert helper_svc["memory_limit"] == "2G"  # Half of 4G

            # Check that fuzzer service is loaded correctly
            fuzzer_svc = next(s for s in result if s["name"] == "fuzzer")
            assert fuzzer_svc["config"]["privileged"] is True
            assert fuzzer_svc["cpuset"] == "0,1,2,3"  # Same CPU set
            assert fuzzer_svc["memory_limit"] == "2G"  # Half of 4G

    def test_memory_split_evenly(self):
        """Test that memory is split evenly across services."""
        from bug_finding.src.render_compose.render import load_crs_compose_services

        with tempfile.TemporaryDirectory() as tmpdir:
            compose_content = """
services:
  service1:
    image: alpine:latest
  service2:
    image: alpine:latest
  service3:
    image: alpine:latest
"""
            compose_path = Path(tmpdir) / "docker-compose.yaml"
            compose_path.write_text(compose_content)

            result = load_crs_compose_services(
                crs_path=Path(tmpdir),
                compose_path="docker-compose.yaml",
                crs_memory_limit="3G",
                crs_cpuset="0-7",
            )

            assert len(result) == 3
            # Each service should get 1G (3G / 3 services)
            for svc in result:
                assert svc["memory_limit"] == "1G"
                assert svc["cpuset"] == "0-7"

    def test_returns_empty_list_for_missing_file(self, caplog):
        """Test that missing compose file returns empty list."""
        from bug_finding.src.render_compose.render import load_crs_compose_services

        with tempfile.TemporaryDirectory() as tmpdir:
            with caplog.at_level(logging.WARNING):
                result = load_crs_compose_services(
                    crs_path=Path(tmpdir),
                    compose_path="nonexistent.yaml",
                    crs_memory_limit="4G",
                    crs_cpuset="0-3",
                )

            assert result == []
            assert "not found" in caplog.text

    def test_returns_empty_list_for_empty_services(self, caplog):
        """Test that compose file with no services returns empty list."""
        from bug_finding.src.render_compose.render import load_crs_compose_services

        with tempfile.TemporaryDirectory() as tmpdir:
            compose_content = """
# Empty compose file
version: "3"
"""
            compose_path = Path(tmpdir) / "docker-compose.yaml"
            compose_path.write_text(compose_content)

            with caplog.at_level(logging.WARNING):
                result = load_crs_compose_services(
                    crs_path=Path(tmpdir),
                    compose_path="docker-compose.yaml",
                    crs_memory_limit="4G",
                    crs_cpuset="0-3",
                )

            assert result == []
            assert "No services found" in caplog.text
