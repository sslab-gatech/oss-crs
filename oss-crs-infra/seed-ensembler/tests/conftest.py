"""Shared pytest fixtures for seed_ensembler tests."""

import pytest
from pathlib import Path


@pytest.fixture
def test_data_dir() -> Path:
    """Path to the test_data directory bundled with the package."""
    return (
        Path(__file__).resolve().parent.parent
        / "seed_ensembler"
        / "test_data"
    )
