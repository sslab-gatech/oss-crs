from pathlib import Path

from libCRS.libCRS.local import LocalCRSUtils


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


def test_local_utils_resolve_builder_url(monkeypatch) -> None:
    utils = LocalCRSUtils()
    monkeypatch.setattr(utils, "get_service_domain", lambda service_name: f"{service_name}.example-crs")
    assert utils._get_builder_url("builder-asan") == "http://builder-asan.example-crs:8080"


def test_local_utils_apply_patch_build_writes_outputs(monkeypatch, tmp_path: Path) -> None:
    utils = LocalCRSUtils()
    patch_path = tmp_path / "patch.diff"
    patch_path.write_text("diff --git a/a b/a")
    response_dir = tmp_path / "response"

    monkeypatch.setattr(utils, "_wait_for_builder_health", lambda builder, max_wait=120, initial_interval=1.0: True)
    monkeypatch.setattr(utils, "_get_builder_url", lambda builder: "http://builder.local:8080")

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

    monkeypatch.setattr("libCRS.libCRS.local.http_requests.post", _fake_post)
    monkeypatch.setattr("libCRS.libCRS.local.http_requests.get", _fake_get)
    monkeypatch.setattr("libCRS.libCRS.local.time.sleep", lambda _: None)

    assert utils.apply_patch_build(patch_path, response_dir, "builder-asan") == 0
    assert (response_dir / "build_exit_code").read_text() == "0"
    assert (response_dir / "build_id").read_text() == "job-123"
    assert (response_dir / "build.log").read_text() == "ok"
