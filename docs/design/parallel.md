# Parallel Builds and Runs

`crs-compose` supports running multiple builds and runs in parallel through build and run identifiers.

## Build ID

The `--build-id` flag isolates build artifacts, allowing parallel builds of different target versions or configurations.
By default, it will be "default" and override previous builds (but still delimited by project).

```bash
uv run crs-compose build-target \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --build-id asan-build
```

Build artifacts are stored at:
```
{work_dir}/{target}/BUILD_OUT_DIR/{build-id}/
```

Multiple runs can share the same build by specifying the same `--build-id`.

## Run ID

The `--run-id` flag isolates run artifacts (seeds, PoVs, shared state), allowing multiple experiments against the same build.

```bash
uv run crs-compose run \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --build-id asan-build \
  --run-id experiment-1 \
  --timeout 3600
```

| Command     | Default                                                        |
|-------------|----------------------------------------------------------------|
| `run`       | Auto-generated timestamp + random bytes (e.g., `1739819274ab`) |
| `artifacts` | Interactive selection in reverse chronological order           |

Run artifacts are stored at:
```
{work_dir}/{target}/SUBMIT_DIR/{harness}/{run-id}/pov/
{work_dir}/{target}/SUBMIT_DIR/{harness}/{run-id}/seed/
{work_dir}/{target}/FETCH_DIR/{harness}/{run-id}/
{work_dir}/{target}/SHARED_DIR/{harness}/{run-id}/
```

## Artifacts Command

Query artifact directories for a specific build and run.

```bash
uv run crs-compose artifacts \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --build-id default \
  --run-id 1739819274ab
```

### Interactive Run Selection

If `--run-id` is omitted, an interactive prompt lists available runs sorted by timestamp:

```
? Select run-id:
❯ 2025-02-17 16:01:57  (1739819274ab)
  2025-02-17 15:58:30  (test-explicit)
  2025-02-17 15:55:00  (experiment-1)
```

### Output Format

```json
{
  "build_id": "default",
  "run_id": "1739819274ab",
  "crs": {
    "crs-libfuzzer": {
      "build": "/path/to/BUILD_OUT_DIR/default",
      "pov": "/path/to/SUBMIT_DIR/xml/1739819274ab/pov",
      "seed": "/path/to/SUBMIT_DIR/xml/1739819274ab/seed",
      "fetch": "/path/to/FETCH_DIR/xml/1739819274ab",
      "shared": "/path/to/SHARED_DIR/xml/1739819274ab"
    }
  }
}
```

Fields are omitted if the directory does not exist. If `--target-harness` is not provided, only `build` is returned.
