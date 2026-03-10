from pathlib import Path

from oss_crs.tests.integration.support.builder_client import BuilderClient


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"HTTP {self.status_code}")


def test_wait_for_health_retries_until_ready(monkeypatch) -> None:
    client = BuilderClient("http://builder.local:8080")
    sleeps: list[float] = []
    calls = {"count": 0}

    def _fake_get(url: str, timeout: int) -> _Response:
        assert url == "http://builder.local:8080/health"
        calls["count"] += 1
        if calls["count"] < 3:
            from requests import ConnectionError

            raise ConnectionError("not ready")
        return _Response(200, {"status": "ok"})

    monkeypatch.setattr("oss_crs.tests.integration.support.builder_client.http_requests.get", _fake_get)
    monkeypatch.setattr("oss_crs.tests.integration.support.builder_client.time.sleep", sleeps.append)

    assert client.wait_for_health(max_wait=5) is True
    assert sleeps == [1.0, 2.0]
    assert calls["count"] == 3


def test_apply_patch_build_writes_success_outputs(monkeypatch, tmp_path: Path) -> None:
    patch_path = tmp_path / "patch.diff"
    patch_path.write_text("diff --git a/a b/a")
    response_dir = tmp_path / "response"
    client = BuilderClient("http://builder.local:8080")

    monkeypatch.setattr(client, "wait_for_health", lambda max_wait=120, initial_interval=1.0: True)

    def _fake_post(url: str, files: dict, data: dict, timeout: int) -> _Response:
        assert url == "http://builder.local:8080/build"
        assert "patch" in files
        assert data == {}
        return _Response(200, {"id": "job-123"})

    polls = {"count": 0}

    def _fake_get(url: str, timeout: int) -> _Response:
        polls["count"] += 1
        assert url == "http://builder.local:8080/status/job-123"
        if polls["count"] == 1:
            return _Response(200, {"id": "job-123", "status": "running"})
        return _Response(
            200,
            {
                "id": "job-123",
                "status": "done",
                "build_exit_code": 0,
                "build_log": "ok",
                "build_stdout": "stdout",
                "build_stderr": "stderr",
            },
        )

    monkeypatch.setattr("oss_crs.tests.integration.support.builder_client.http_requests.post", _fake_post)
    monkeypatch.setattr("oss_crs.tests.integration.support.builder_client.http_requests.get", _fake_get)
    monkeypatch.setattr("oss_crs.tests.integration.support.builder_client.time.sleep", lambda _: None)

    assert client.apply_patch_build(patch_path, response_dir) == 0
    assert (response_dir / "build_exit_code").read_text() == "0"
    assert (response_dir / "build_id").read_text() == "job-123"
    assert (response_dir / "build.log").read_text() == "ok"
    assert (response_dir / "build_stdout.log").read_text() == "stdout"
    assert (response_dir / "build_stderr.log").read_text() == "stderr"


def test_run_test_writes_unavailable_result(monkeypatch, tmp_path: Path) -> None:
    response_dir = tmp_path / "response"
    client = BuilderClient("http://builder.local:8080")

    monkeypatch.setattr(client, "submit_and_poll", lambda *args, **kwargs: None)

    assert client.run_test("base", response_dir) == 1
    assert (response_dir / "test_exit_code").read_text() == "1"
    assert "Builder unavailable or timed out" in (response_dir / "test_stderr.log").read_text()
