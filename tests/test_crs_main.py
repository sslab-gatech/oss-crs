"""Tests for bug_finding.src.crs_main module - OSS-Fuzz copy optimization."""

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_oss_fuzz_source(tmp_path: Path) -> Path:
    """Create a temporary OSS-Fuzz source directory structure for testing.

    Structure:
        source_oss_fuzz/
        ├── build/              # Should be excluded
        │   └── out/
        │       └── artifact.o
        ├── projects/
        │   ├── json-c/         # Target project
        │   │   ├── Dockerfile
        │   │   ├── build.sh
        │   │   └── project.yaml
        │   ├── libxml2/        # Other project (should be excluded)
        │   │   └── Dockerfile
        │   └── aixcc/          # Nested project structure
        │       └── c/
        │           └── myproject/
        │               ├── Dockerfile
        │               └── project.yaml
        ├── infra/              # Should be copied
        │   └── base-images/
        │       └── base-builder/
        │           └── Dockerfile
        └── README.md           # Root file (should be copied)
    """
    source = tmp_path / "source_oss_fuzz"
    source.mkdir()

    # Create build/ directory with artifacts
    build_out = source / "build" / "out"
    build_out.mkdir(parents=True)

    # Target project build artifacts (should be copied)
    (build_out / "json-c").mkdir()
    (build_out / "json-c" / "fuzzer").write_text("binary")
    (build_out / "json-c-asan").mkdir()
    (build_out / "json-c-asan" / "fuzzer").write_text("binary")
    (build_out / "json-c-ubsan").mkdir()
    (build_out / "json-c-ubsan" / "fuzzer").write_text("binary")

    # Other project build artifacts (should be excluded)
    (build_out / "libxml2").mkdir()
    (build_out / "libxml2" / "fuzzer").write_text("binary")
    (build_out / "libxml2-asan").mkdir()
    (build_out / "libxml2-asan" / "fuzzer").write_text("binary")

    # Create build/work/ directory with intermediate artifacts
    build_work = source / "build" / "work"
    build_work.mkdir(parents=True)

    # Target project work artifacts (should be copied)
    (build_work / "json-c").mkdir()
    (build_work / "json-c" / "obj.o").write_text("object")

    # Other project work artifacts (should be excluded)
    (build_work / "libxml2").mkdir()
    (build_work / "libxml2" / "obj.o").write_text("object")

    # Create projects/ with multiple projects
    projects_dir = source / "projects"

    # Target project: json-c
    json_c = projects_dir / "json-c"
    json_c.mkdir(parents=True)
    (json_c / "Dockerfile").write_text("FROM base-builder")
    (json_c / "build.sh").write_text("#!/bin/bash\nmake")
    (json_c / "project.yaml").write_text("name: json-c\nmain_repo: https://github.com/json-c/json-c")

    # Other project: libxml2 (should be excluded when copying json-c)
    libxml2 = projects_dir / "libxml2"
    libxml2.mkdir(parents=True)
    (libxml2 / "Dockerfile").write_text("FROM base-builder")

    # Nested project: aixcc/c/myproject
    nested = projects_dir / "aixcc" / "c" / "myproject"
    nested.mkdir(parents=True)
    (nested / "Dockerfile").write_text("FROM base-builder")
    (nested / "project.yaml").write_text("name: myproject")

    # Create infra/ directory (should be copied)
    infra = source / "infra" / "base-images" / "base-builder"
    infra.mkdir(parents=True)
    (infra / "Dockerfile").write_text("FROM ubuntu:20.04")

    # Create root file (should be copied)
    (source / "README.md").write_text("# OSS-Fuzz")

    return source


@pytest.fixture
def temp_oss_fuzz_dest(tmp_path: Path) -> Path:
    """Create a temporary destination directory for OSS-Fuzz copy."""
    # Don't create it - let the copy function create it
    return tmp_path / "dest_oss_fuzz"


@pytest.fixture
def source_with_symlinks(temp_oss_fuzz_source: Path) -> Path:
    """Add symlinks to the OSS-Fuzz source directory.

    Adds:
        - valid_link -> infra/base-images (relative symlink, valid)
        - dangling_link -> nonexistent_target (relative symlink, dangling)

    Uses relative symlinks to match typical OSS-Fuzz patterns.
    rsync with --safe-links will preserve relative symlinks that stay
    within the source tree.
    """
    # Valid relative symlink (points to existing directory)
    valid_link = temp_oss_fuzz_source / "valid_link"
    valid_link.symlink_to("infra/base-images")

    # Dangling relative symlink (points to non-existent target)
    dangling_link = temp_oss_fuzz_source / "dangling_link"
    dangling_link.symlink_to("nonexistent_target_that_does_not_exist")

    return temp_oss_fuzz_source


# =============================================================================
# User Story 1 Tests - Faster OSS-Fuzz Directory Copy
# =============================================================================

class TestCloneOssFuzzSelectiveBuildCopy:
    """Test that build/ artifacts are selectively copied (FR-001)."""

    def test_clone_oss_fuzz_copies_target_project_build_artifacts(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify target project's build artifacts are copied."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="json-c",
        )

        assert result is True
        # Target project build artifacts should be copied (both out/ and work/)
        assert (temp_oss_fuzz_dest / "build" / "out" / "json-c").exists()
        assert (temp_oss_fuzz_dest / "build" / "out" / "json-c-asan").exists()
        assert (temp_oss_fuzz_dest / "build" / "out" / "json-c-ubsan").exists()
        assert (temp_oss_fuzz_dest / "build" / "work" / "json-c").exists()

    def test_clone_oss_fuzz_excludes_other_project_build_artifacts(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify other projects' build artifacts are NOT copied."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="json-c",
        )

        assert result is True
        # Other project build artifacts should NOT be copied (neither out/ nor work/)
        assert not (temp_oss_fuzz_dest / "build" / "out" / "libxml2").exists()
        assert not (temp_oss_fuzz_dest / "build" / "out" / "libxml2-asan").exists()
        assert not (temp_oss_fuzz_dest / "build" / "work" / "libxml2").exists()


class TestCloneOssFuzzCopiesOnlyTargetProject:
    """Test that only target project is copied from projects/ (FR-002)."""

    def test_clone_oss_fuzz_copies_only_target_project(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify only the target project is copied, not other projects."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="json-c",
        )

        assert result is True
        # Target project should exist
        assert (temp_oss_fuzz_dest / "projects" / "json-c").exists()
        assert (temp_oss_fuzz_dest / "projects" / "json-c" / "Dockerfile").exists()
        # Other projects should NOT exist
        assert not (temp_oss_fuzz_dest / "projects" / "libxml2").exists()


class TestCloneOssFuzzCopiesInfraAndRootFiles:
    """Test that infra/ and root files are copied (FR-003)."""

    def test_clone_oss_fuzz_copies_infra_and_root_files(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify infra/ directory and root files are copied."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="json-c",
        )

        assert result is True
        # infra/ should be copied
        assert (temp_oss_fuzz_dest / "infra").exists()
        assert (temp_oss_fuzz_dest / "infra" / "base-images" / "base-builder" / "Dockerfile").exists()
        # Root files should be copied
        assert (temp_oss_fuzz_dest / "README.md").exists()


class TestRsyncNotAvailableError:
    """Test error handling when rsync is not available (FR-008)."""

    def test_rsync_not_available_error(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify clear error message when rsync is not installed."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        with patch("bug_finding.src.utils.shutil.which", return_value=None):
            result = _clone_oss_fuzz_if_needed(
                oss_fuzz_dir=temp_oss_fuzz_dest,
                source_oss_fuzz_dir=temp_oss_fuzz_source,
                project_name="json-c",
            )

            assert result is False


# =============================================================================
# User Story 2 Tests - Nested Project Structure
# =============================================================================

class TestCloneOssFuzzNestedProjectName:
    """Test nested project name support (FR-005)."""

    def test_clone_oss_fuzz_nested_project_name(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify nested project paths like aixcc/c/myproject are handled."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="aixcc/c/myproject",
        )

        assert result is True
        # Nested project should exist with full path
        assert (temp_oss_fuzz_dest / "projects" / "aixcc" / "c" / "myproject").exists()
        assert (temp_oss_fuzz_dest / "projects" / "aixcc" / "c" / "myproject" / "Dockerfile").exists()


# =============================================================================
# User Story 3 Tests - Symlink Handling
# =============================================================================

class TestCloneOssFuzzSymlinkHandling:
    """Test symlink handling (FR-004)."""

    def test_clone_oss_fuzz_preserves_valid_symlinks(
        self, source_with_symlinks: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify valid symlinks are preserved during copy."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=source_with_symlinks,
            project_name="json-c",
        )

        assert result is True
        # Valid symlink should be preserved
        valid_link = temp_oss_fuzz_dest / "valid_link"
        assert valid_link.is_symlink()

    def test_clone_oss_fuzz_ignores_dangling_symlinks(
        self, source_with_symlinks: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify dangling symlinks don't cause errors."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        # Verify source has dangling symlink
        dangling = source_with_symlinks / "dangling_link"
        assert dangling.is_symlink()
        assert not dangling.exists()  # Target doesn't exist

        # Copy should complete without error
        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=source_with_symlinks,
            project_name="json-c",
        )

        assert result is True
        # Operation completed successfully (dangling symlink ignored)


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestCloneOssFuzzEdgeCases:
    """Test edge cases and error conditions."""

    def test_clone_oss_fuzz_project_not_found(
        self, temp_oss_fuzz_source: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify clear error when target project doesn't exist (FR-006)."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=temp_oss_fuzz_source,
            project_name="nonexistent-project",
        )

        assert result is False

    def test_clone_oss_fuzz_no_projects_dir(
        self, tmp_path: Path, temp_oss_fuzz_dest: Path
    ):
        """Verify error when source has no projects/ directory."""
        from bug_finding.src.crs_main import _clone_oss_fuzz_if_needed

        # Create invalid OSS-Fuzz structure (no projects/ dir)
        invalid_source = tmp_path / "invalid_oss_fuzz"
        invalid_source.mkdir()
        (invalid_source / "README.md").write_text("Invalid")

        result = _clone_oss_fuzz_if_needed(
            oss_fuzz_dir=temp_oss_fuzz_dest,
            source_oss_fuzz_dir=invalid_source,
            project_name="json-c",
        )

        assert result is False
