"""Tests for Configuration."""

import os
from pathlib import Path

import pytest

from seed_ensembler.config import Configuration


class TestConfiguration:
    def test_defaults(self):
        cfg = Configuration(temp_dir=Path("/tmp/test"))
        assert cfg.worker_pool_size == 4
        assert cfg.runner_image is None
        assert cfg.verbose is False
        assert cfg.mode == "coverage"
        assert cfg.poll_interval == 2.0
        assert cfg.submit_root == Path("/submit")
        assert cfg.exchange_root == Path("/OSS_CRS_EXCHANGE_DIR")
        assert cfg.build_out_dir == Path("/OSS_CRS_BUILD_OUT_DIR")

    def test_frozen(self):
        cfg = Configuration(temp_dir=Path("/tmp/test"))
        with pytest.raises(AttributeError):
            cfg.mode = "passthrough"


class TestFromEnv:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("ENSEMBLER_TEMP_DIR", raising=False)
        monkeypatch.delenv("ENSEMBLER_WORKERS", raising=False)
        monkeypatch.delenv("ENSEMBLER_MODE", raising=False)
        monkeypatch.delenv("RUNNER_IMAGE", raising=False)
        monkeypatch.delenv("ENSEMBLER_VERBOSE", raising=False)
        monkeypatch.delenv("ENSEMBLER_POLL_INTERVAL", raising=False)
        monkeypatch.delenv("SUBMIT_ROOT", raising=False)
        monkeypatch.delenv("EXCHANGE_ROOT", raising=False)
        monkeypatch.delenv("BUILD_OUT_DIR", raising=False)

        cfg = Configuration.from_env()
        assert cfg.temp_dir == Path("/tmp/ensembler")
        assert cfg.worker_pool_size == 2
        assert cfg.mode == "coverage"
        assert cfg.runner_image is None
        assert cfg.verbose is False
        assert cfg.poll_interval == 2.0

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLER_TEMP_DIR", "/custom/tmp")
        monkeypatch.setenv("ENSEMBLER_WORKERS", "8")
        monkeypatch.setenv("ENSEMBLER_MODE", "passthrough")
        monkeypatch.setenv("RUNNER_IMAGE", "my-image:latest")
        monkeypatch.setenv("ENSEMBLER_VERBOSE", "true")
        monkeypatch.setenv("ENSEMBLER_POLL_INTERVAL", "5.0")
        monkeypatch.setenv("SUBMIT_ROOT", "/my/submit")
        monkeypatch.setenv("EXCHANGE_ROOT", "/my/exchange")
        monkeypatch.setenv("BUILD_OUT_DIR", "/my/build")

        cfg = Configuration.from_env()
        assert cfg.temp_dir == Path("/custom/tmp")
        assert cfg.worker_pool_size == 8
        assert cfg.mode == "passthrough"
        assert cfg.runner_image == "my-image:latest"
        assert cfg.verbose is True
        assert cfg.poll_interval == 5.0
        assert cfg.submit_root == Path("/my/submit")
        assert cfg.exchange_root == Path("/my/exchange")
        assert cfg.build_out_dir == Path("/my/build")

    def test_verbose_1(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLER_VERBOSE", "1")
        cfg = Configuration.from_env()
        assert cfg.verbose is True

    def test_verbose_false(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLER_VERBOSE", "no")
        cfg = Configuration.from_env()
        assert cfg.verbose is False
