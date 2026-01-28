# Bug-finding CRS Run Phase with Compose

Currently the bug finding CRSs can only run with a single runner.Dockerfile. We want to extend the support for CRSs to provide a compose.yaml. The single docker container is defined in OSS-CRS's own compose.yaml. So for the support of CRS compose, we need to import the services from CRS's compose into OSS-CRS' rendered version.

## Interface Changes

### config-crs.yaml (in crs_registry)

Add `run.docker_compose` to specify the path to the CRS's docker-compose file:

```yaml
run:
  docker_compose: docker-compose.yaml  # Relative path within CRS repo

crs-name: []
```

### CRS docker-compose.yaml

The CRS provides a docker-compose.yaml with services. Each service should:

1. **Use ENTRYPOINT** (not CMD) for the main command
2. **Accept or ignore CMD arguments** - OSS-CRS passes `fuzzer_command` as CMD to all services

Example structure:
```yaml
services:
  # Helper service - ignores CMD args
  helper:
    build:
      context: .
      dockerfile: helper.Dockerfile

  # Fuzzer service - uses CMD args via ENTRYPOINT
  fuzzer:
    build:
      context: .
      dockerfile: runner.Dockerfile
```

**Image naming:** Services with `build:` sections get images named `{crs_name}_{service_name}` (e.g., `crs-libfuzzer_helper`, `crs-libfuzzer_fuzzer`).

### CRS Dockerfile conventions

- **ENTRYPOINT**: Define how the container runs
- **CMD args from OSS-CRS**: The `fuzzer_command` (harness name + args) is passed as CMD
- Services that don't need the fuzzer command should use an ENTRYPOINT script that ignores arguments

Example helper.Dockerfile (ignores CMD):
```dockerfile
FROM alpine:latest
COPY helper.sh /helper.sh
RUN chmod +x /helper.sh
ENTRYPOINT ["/helper.sh"]
```

Example runner.Dockerfile (uses CMD):
```dockerfile
FROM base-image
COPY run.py /run.py
ENTRYPOINT ["python3", "/run.py"]
# CMD args (harness name) passed by OSS-CRS
```

## Resource Limits

When a CRS has multiple containers from its compose file:
1. **CPU set**: Same CPU set assigned to all containers from the same CRS
2. **Memory**: Split evenly across containers from the same CRS

## Environment Variables

All runner services receive these environment variables:
- `LITELLM_URL`, `LITELLM_KEY` - LLM access
- `FUZZING_ENGINE`, `SANITIZER` - Fuzzing config
- `RUN_FUZZER_MODE=interactive`, `HELPER=True`
- `CPUSET_CPUS`, `MEMORY_LIMIT` - Resource constraints
- `CRS_TARGET`, `CRS_NAME`, `SOURCE_WORKDIR`
- `HARNESS_NAME` - The harness/fuzzer name (when available)
- Custom env vars from `run_env` in config-crs.yaml

## Implementation

### Prepare phase (`prepare.py`)

When `run.docker_compose` is specified:
- `build_compose_images()` builds all services with `build:` sections
- Images tagged as `{crs_name}_{service_name}`
- Falls back to `build_runner_image()` for traditional single-container mode

### Run phase (`render_compose.py`, `compose.yaml.j2`)

- `load_crs_compose_services()` parses CRS compose file and calculates resource splits
- Template renders imported services with:
  - Explicit image names matching prepare phase
  - Resource constraints (cpuset, memory)
  - Common environment variables via `runner_env_vars()` macro
  - Common volume mounts via `runner_volumes()` macro
  - `fuzzer_command` as CMD (unless service specifies its own command)

## Testing

```bash
# Prepare (builds compose images)
uv run oss-bugfind-crs prepare crs-libfuzzer

# Build project
uv run oss-bugfind-crs build example_configs/crs-libfuzzer project-name

# Run
uv run oss-bugfind-crs run --skip-litellm example_configs/crs-libfuzzer project-name harness_name
```

---

# Multi-Builder Support

## Overview

Support sequential execution of multiple builder Dockerfiles. This enables CRS to produce multiple build variants (e.g., ASan build, coverage build, instrumented build) in a single build phase.

## config-crs.yaml Interface

```yaml
build:
  dockerfiles:
    - builder.Dockerfile.default    # Builds default harness binaries
    - builder.Dockerfile.coverage   # Builds coverage-instrumented binaries

run:
  docker_compose: docker-compose.yaml

crs-name: []
```

When `build.dockerfiles` is specified:
- Builders run **sequentially** in the order listed
- Each builder shares the same `/out`, `/work`, `/artifacts` volumes
- Falls back to single `builder.Dockerfile` if `build.dockerfiles` not specified

## Artifact Organization

Builders should organize artifacts by build type:

```
/artifacts/
├── default/           # Default (ASan) harness binaries
│   ├── harness1
│   └── harness2
├── coverage/          # Coverage-instrumented binaries
│   ├── harness1
│   └── harness2
└── instrumented/      # Other instrumented builds
    └── ...
```

## Run Phase Integration

The run phase (fuzzer wrapper) should:
1. Clear `/out` directory
2. Sync default harness binaries from `/artifacts/default/` to `/out/`
3. Start the fuzzer

Example `run_fuzzer_wrapper.sh`:
```bash
#!/bin/bash
# Clear and populate /out with default binaries
rm -rf /out/*
cp -r /artifacts/default/* /out/

# Start fuzzer
exec python3 /run.py "$@"
```

## crs-libfuzzer Example

### builder.Dockerfile.default
```dockerfile
FROM gcr.io/oss-fuzz-base/base-builder
# Build with default sanitizer (ASan)
# Export to /artifacts/default/
```

### builder.Dockerfile.coverage
```dockerfile
FROM gcr.io/oss-fuzz-base/base-builder
ENV SANITIZER=coverage
# Build with coverage instrumentation
# Export to /artifacts/coverage/
```

## Implementation Notes

### Build phase changes (`compose.yaml.j2`)
- Loop through `build.dockerfiles` list
- Run each builder sequentially (not parallel)
- All builders share same volume mounts

### Template changes
- Add `crs.build_dockerfiles` field to CRS config
- Render multiple builder services or run them sequentially
