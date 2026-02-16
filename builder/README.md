# OSS-CRS Builder

The **Builder CRS** provides snapshot-based incremental builds for patch-testing CRSs. Instead of rebuilding the entire target from scratch for each patch, the builder creates a Docker snapshot of the compiled project and applies patches incrementally on top of it.

## How It Works

1. When a builder CRS is present in the compose file, the framework creates a **snapshot image** during the build phase. This snapshot contains the fully compiled target project.
2. The builder service starts from the snapshot image and exposes an HTTP API for incremental builds.
3. Other CRSs (e.g., a patcher) send patches to the builder via `libCRS.apply_patch_build()`, which calls the builder's `/build` endpoint.
4. The builder applies the patch to the source tree, recompiles, and returns the result — much faster than a full rebuild.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/build` | POST | Apply a patch and compile. Accepts `patch` file upload. Returns build ID. |
| `/run-pov` | POST | Run a POV binary against a specific build. Accepts `pov` file, `harness_name`, and `build_id`. |
| `/run-test` | POST | Run the project's bundled `test.sh` against a specific build. Accepts `build_id`. |
| `/status/<id>` | GET | Poll job status. Returns `queued`, `running`, or `done`. |
| `/health` | GET | Healthcheck endpoint. |

All jobs are processed sequentially through a single worker thread to prevent races on the shared source tree.

Build jobs are deduplicated by patch content — submitting the same patch twice returns the existing result if the build is in-flight or succeeded. Failed builds are not cached, allowing retry with the same patch.

## Usage

Only one builder CRS is allowed per compose file. Add the builder as a CRS entry in your compose file:

```yaml
oss-crs-builder:
  source:
    local_path: /path/to/oss-crs/builder
  cpuset: "2-3"
  memory: "8G"
```

The framework automatically:
- Creates a snapshot image during the build phase
- Starts the builder service from the snapshot
- Sets `OSS_CRS_BUILDER_URL` on all non-builder CRS modules
- Exposes the builder on the shared Docker network

No additional configuration is needed. Any CRS that uses `libCRS.apply_patch_build()` will automatically communicate with the builder. The libCRS client waits for the builder's `/health` endpoint to respond before sending requests, with exponential backoff up to 120 seconds.

## CRS Configuration

The builder's `crs.yaml` declares `type: [builder]`, which tells the framework to treat it as an infrastructure service rather than a regular CRS:

```yaml
name: oss-crs-builder
type:
  - builder
version: 1.0.0

crs_run_phase:
  server:
    dockerfile: oss-crs/server.Dockerfile
```

The builder has no `prepare_phase` or `target_build_phase` — the framework handles target compilation during snapshot creation.

## Environment Variables

The framework sets the following environment variables automatically when a builder CRS is present:

| Variable | Set On | Description |
|---|---|---|
| `OSS_CRS_BUILDER_URL` | All non-builder CRS modules | HTTP URL of the builder service (e.g., `http://oss-crs-builder_server:8080`). Used by libCRS to send build/POV/test requests. |
| `OSS_CRS_SNAPSHOT_IMAGE` | All non-builder CRS modules | Docker image tag of the snapshot used by the builder. |

The builder service itself receives the standard `OSS_CRS_*` environment variables plus oss-fuzz variables (`SANITIZER`, `PROJECT_NAME`, `FUZZING_ENGINE`, etc.).

## Input Validation

The builder validates all user-provided parameters:
- `harness_name`: alphanumeric, hyphens, underscores, or dots only
- `build_id`: alphanumeric, hyphens, or underscores only

## Build Artifacts

Build artifacts from each patch are stored at `/builds/{build_id}/out/` inside the builder container. These are shared with other CRSs via `libCRS register-shared-dir`. A base (unpatched) build is created automatically at startup as `/builds/base/out/`.
