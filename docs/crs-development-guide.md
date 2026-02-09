# CRS Development Guide

This guide walks you through building a Cyber Reasoning System (CRS) that integrates with the OSS-CRS framework. By the end, your CRS will be able to:

- Target any project in [OSS-Fuzz](https://github.com/google/oss-fuzz) format
- Run in any supported environment (local, Azure) without modification
- Be composable with other CRSs in ensemble campaigns

> **Before you start:** Check the [CRS Registry](registry.md) to see if an existing CRS already fits your needs. You can use a registered CRS directly with `crs-compose` — no development required. If you want to extend or customize an existing CRS, forking a registered one is often faster than starting from scratch. Registered CRS repositories also serve as practical references for how to structure your own CRS — studying their `oss-crs/crs.yaml`, Dockerfiles, and build scripts is a great way to learn the patterns.

## Prerequisites

| Requirement | Version |
|---|---|
| Python | >= 3.10 |
| Docker | latest |
| Git | latest |
| [uv](https://github.com/astral-sh/uv) | latest |

Familiarity with Docker, Docker Compose, and the OSS-Fuzz project format is recommended.

---

## Overview

A CRS is a self-contained, containerized bug-finding or bug-fixing system. OSS-CRS manages the entire lifecycle of a CRS through three phases:

```
  prepare  ──▶  build-target  ──▶  run
```

| Phase | What happens | What you provide |
|---|---|---|
| **Prepare** | Pull your CRS repository and build Docker images | HCL file for `docker buildx bake` |
| **Build Target** | Compile the target project with your CRS's instrumentation | Builder Dockerfile(s) + expected outputs |
| **Run** | Launch your CRS containers against the built target | Module Dockerfile(s) + entry scripts |

Your CRS communicates with the infrastructure through **libCRS**, a CLI library pre-installed in every container.

---

## Repository Structure

Your CRS repository must contain an `oss-crs/` directory at the root with a `crs.yaml` configuration file:

```
my-crs/
├── oss-crs/
│   ├── crs.yaml                    # CRS configuration (required)
│   ├── build.hcl                   # Docker buildx bake file
│   ├── asan-builder.Dockerfile     # Target build Dockerfile(s)
│   └── docker-compose/
│       ├── fuzzer.Dockerfile       # Run-phase module Dockerfile(s)
│       └── analyzer.Dockerfile
├── src/                            # Your CRS source code
│   └── ...
└── ...
```

---

## Step 1: Write `crs.yaml`

The `crs.yaml` file is the central configuration for your CRS. It tells OSS-CRS how to prepare, build, and run your system.

```yaml
name: my-crs
type:
  - bug-finding           # bug-finding, bug-fixing, or both
version: "1.0.0"
docker_registry: "ghcr.io/my-org/my-crs"

prepare_phase:
  hcl: oss-crs/build.hcl

target_build_phase:
  - name: asan-build
    dockerfile: oss-crs/asan-builder.Dockerfile
    outputs:
      - asan/build              # directory containing compiled binaries
      - asan/src                # directory containing source snapshot
    additional_env:           # optional extra env vars for the build
      BUILD_TYPE: asan
  - name: coverage-build
    dockerfile: oss-crs/asan-builder.Dockerfile
    additional_env:
      BUILD_TYPE: coverage
    outputs:
      - coverage/build

crs_run_phase:
  fuzzer:
    dockerfile: oss-crs/docker-compose/fuzzer.Dockerfile
    additional_env:
      FUZZER_THREADS: "4"
  analyzer:
    dockerfile: oss-crs/docker-compose/analyzer.Dockerfile

supported_target:
  mode:
    - full
  language:
    - c
    - c++
  sanitizer:
    - address
  architecture:
    - x86_64

# Only needed if your CRS uses LLMs
required_llms:
  - gpt-4.1
  - claude-sonnet-4-20250514
```

### Configuration Fields

| Field | Description |
|---|---|
| `name` | Unique name for your CRS |
| `type` | Set of CRS types: `bug-finding`, `bug-fixing` |
| `version` | Version string (used as a Docker image tag) |
| `docker_registry` | Docker registry URL for your CRS images |
| `prepare_phase.hcl` | Path to the HCL file for `docker buildx bake` |
| `target_build_phase` | List of build steps, each with a Dockerfile and expected outputs |
| `crs_run_phase` | Dictionary of named modules (containers) that run at runtime |
| `supported_target` | Languages, sanitizers, architectures, and modes your CRS supports |
| `required_llms` | *(Optional)* LLM model names your CRS needs |

For the complete schema reference, see [config/crs.md](config/crs.md).

---

## Step 2: Write the Prepare Phase (HCL)

The prepare phase uses [Docker Buildx Bake](https://docs.docker.com/build/bake/) to build your CRS images. Create an HCL file that defines your build targets:

```hcl
// oss-crs/build.hcl

group "default" {
  targets = ["my-crs-base"]
}

target "my-crs-base" {
  dockerfile = "Dockerfile"
  context    = "."
  tags       = ["my-crs-base:latest"]
}
```

This runs during `crs-compose prepare` and builds all images your CRS needs.

---

## Step 3: Write Target Build Dockerfiles

During the build-target phase, OSS-CRS compiles the target project using your Dockerfile. Your builder Dockerfile receives:

- **`target_base_image`** — The base image for the OSS-Fuzz target (with source code and build environment)
- **`crs_version`** — Your CRS version string
- **`libcrs`** — An additional build context containing the libCRS library

### Example Builder Dockerfile

```dockerfile
# oss-crs/asan-builder.Dockerfile

ARG target_base_image
FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh

# Install the compile script
COPY bin/compile_target /usr/local/bin/compile_target

# Actual compilation runs at container startup (not during image build),
# because the target source code is injected at runtime.
CMD ["compile_target"]
```

### Example Build Script

The `CMD` in the Dockerfile invokes a build script that compiles the target and submits outputs:

```bash
#!/bin/bash
# bin/compile_target
set -e

if [ "$BUILD_TYPE" = "asan" ]; then
    # Compile with AddressSanitizer instrumentation
    compile

    # Submit build output directories
    # These must match the `outputs` list in crs.yaml
    libCRS submit-build-output $OUT asan/build
    libCRS submit-build-output $SRC asan/src

elif [ "$BUILD_TYPE" = "coverage" ]; then
    SANITIZER=coverage compile
    libCRS submit-build-output $OUT coverage/build
fi
```

> **Note:** If a build step doesn't apply for certain targets (e.g., coverage builds for JVM projects), use `libCRS skip-build-output` to explicitly skip the output:
> ```bash
> libCRS skip-build-output coverage/build
> ```

### Key Points

- Each build step in `target_build_phase` runs as a separate Docker container.
- Build outputs are typically **directories**, not tarballs — `libCRS submit-build-output` handles both files and directories via `rsync`.
- Use `libCRS submit-build-output <src> <dst>` to make build artifacts available to the run phase.
- Use `libCRS skip-build-output <dst>` to mark optional outputs as intentionally skipped.
- The `outputs` list in `crs.yaml` declares what paths the build step is expected to produce. Every output must either be submitted or explicitly skipped.

---

## Step 4: Write Run-Phase Module Dockerfiles

Each module in `crs_run_phase` becomes a container at runtime. Modules share a private Docker network (for intra-CRS communication) and have access to shared infrastructure.

### Example Run-Phase Dockerfile

```dockerfile
# oss-crs/docker-compose/fuzzer.Dockerfile

ARG target_base_image
FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh

# Copy your CRS code
COPY src/ /opt/my-crs/

# Entry script
COPY oss-crs/docker-compose/run-fuzzer.sh /opt/run-fuzzer.sh
RUN chmod +x /opt/run-fuzzer.sh

CMD ["/opt/run-fuzzer.sh"]
```

### Example Entry Script

```bash
#!/bin/bash
# oss-crs/docker-compose/run-fuzzer.sh

# 1. Download build output directories from the target build phase
libCRS download-build-output asan/build /opt/target/build
libCRS download-build-output asan/src /opt/target/src

# 2. Set up shared directories for inter-container communication
libCRS register-shared-dir /shared-corpus corpus

# 3. Register directories for automatic artifact submission
#    (These run as background daemons)
libCRS register-submit-dir seed /output/seeds &
libCRS register-submit-dir pov /output/povs &
libCRS register-submit-dir bug-candidate /output/bugs &

# 4. Resolve other module endpoints (if needed)
ANALYZER_HOST=$(libCRS get-service-domain analyzer)

# 5. Run your fuzzer
/opt/target/fuzzer \
  --target /opt/target/binary \
  --seeds /shared-corpus \
  --output /output
```

---

## Step 5: Using libCRS

libCRS is automatically installed in every CRS container. It provides the interface between your CRS code and the OSS-CRS infrastructure.

### Environment Variables Available at Runtime

Your containers receive these environment variables automatically:

| Variable | Description | Example |
|---|---|---|
| `OSS_CRS_RUN_ENV_TYPE` | Execution environment | `local` |
| `OSS_CRS_NAME` | CRS name (from `crs-compose.yaml`) | `my-crs` |
| `OSS_CRS_SERVICE_NAME` | Full service name | `my-crs_fuzzer` |
| `OSS_CRS_TARGET` | Target project name | `libxml2` |
| `OSS_CRS_TARGET_HARNESS` | Target harness binary name | `xml` |
| `OSS_CRS_CPUSET` | Allocated CPU cores | `4-7` |
| `OSS_CRS_MEMORY_LIMIT` | Memory limit | `16G` |
| `OSS_CRS_BUILD_OUT_DIR` | Build output directory (read-only at run time) | `/OSS_CRS_BUILD_OUT_DIR` |
| `OSS_CRS_SUBMIT_DIR` | Submission directory | `/OSS_CRS_SUBMIT_DIR` |
| `OSS_CRS_SHARED_DIR` | Shared directory (between containers in this CRS) | `/OSS_CRS_SHARED_DIR` |
| `FUZZING_ENGINE` | OSS-Fuzz fuzzing engine | `libfuzzer` |
| `SANITIZER` | OSS-Fuzz sanitizer | `address` |
| `ARCHITECTURE` | Target architecture | `x86_64` |
| `FUZZING_LANGUAGE` | Target language | `c` |

### LLM Environment Variables (if configured)

| Variable | Description |
|---|---|
| `OSS_CRS_LLM_API_URL` | LiteLLM proxy endpoint (e.g., `http://litellm.oss-crs:4000`) |
| `OSS_CRS_LLM_API_KEY` | Per-CRS API key for LLM access |

Use these with any OpenAI-compatible client library. All requests are proxied through LiteLLM, which enforces your budget.

### libCRS Command Quick Reference

```bash
# Build outputs
libCRS submit-build-output <src_path> <dst_path>
libCRS download-build-output <src_path> <dst_path>
libCRS skip-build-output <dst_path>

# Automatic directory submission (runs as daemon)
libCRS register-submit-dir <type> <path>      # type: pov, seed, bug-candidate
libCRS register-shared-dir <local_path> <shared_path>

# Manual submission
libCRS submit <type> <file_path>

# Network
libCRS get-service-domain <service_name>
```

For the complete libCRS reference, see [design/libCRS.md](design/libCRS.md).

---

## Step 6: Test Locally

### Create a Compose File

Create a `crs-compose.yaml` to run your CRS locally:

```yaml
run_env: local
docker_registry: ghcr.io/my-org

oss_crs_infra:
  cpuset: "0-1"
  memory: "4G"

my-crs:
  source:
    local_path: /path/to/my-crs    # Use local path during development
  cpuset: "2-7"
  memory: "16G"
  # llm_budget: 100               # Uncomment if using LLMs

# Uncomment if using LLMs
# llm_config:
#   litellm_config: /path/to/litellm-config.yaml
```

### Run the Three Phases

```bash
# 1. Prepare — build CRS Docker images
uv run crs-compose prepare \
  --compose-file ./my-crs-compose.yaml

# 2. Build target — compile the target with your instrumentation
uv run crs-compose build-target \
  --compose-file ./my-crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2

# 3. Run — launch the CRS
uv run crs-compose run \
  --compose-file ./my-crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

### Debugging Tips

- **Check container logs:** `docker compose logs <service_name>`
- **Shell into a container:** `docker compose exec <service_name> bash`
- **Inspect build outputs:** Check the build output directory created during `build-target`
- **Use `local_path`:** During development, use `local_path` in `crs-compose.yaml` to avoid pushing to git for every change

---

## Step 7: Publish Your CRS

Once your CRS is working, push it to a Git repository and switch from `local_path` to `url` + `ref`:

```yaml
my-crs:
  source:
    url: https://github.com/my-org/my-crs.git
    ref: v1.0.0
  cpuset: "2-7"
  memory: "16G"
```

### Register in the CRS Registry

To make your CRS discoverable, add an entry to the [CRS Registry](registry.md) by creating a `registry/<crs-name>/pkg.yaml`:

```yaml
name: my-crs
type:
  - bug-finding
source:
  url: https://github.com/my-org/my-crs.git
  ref: main
```

---

## Multi-Module CRS Architecture

A CRS can contain multiple modules (containers) that work together. Modules share a private Docker network and can communicate via DNS.

```
┌────────────────────────────────────────────────┐
│              my-crs (Isolated)                 │
│                                                │
│  ┌──────────────┐    ┌─────────────────┐       │
│  │   fuzzer     │◄──►│    analyzer     │       │
│  │  (module)    │    │    (module)     │       │
│  └──────────────┘    └─────────────────┘       │
│         │                    │                 │
│         ▼                    ▼                 │
│     /shared-corpus  (via register-shared-dir)  │
│                                                │
│  DNS: fuzzer.my-crs    analyzer.my-crs         │
└────────────────────────────────────────────────┘
```

### Inter-Module Communication

| Method | Use case | How |
|---|---|---|
| **Shared filesystem** | Passing corpus, seeds, or results between modules | `libCRS register-shared-dir /local corpus` |
| **DNS** | HTTP APIs or custom protocols between modules | `libCRS get-service-domain analyzer` → `analyzer.my-crs` |

### Example: Two-Module CRS

```yaml
# crs.yaml
crs_run_phase:
  fuzzer:
    dockerfile: oss-crs/docker-compose/fuzzer.Dockerfile
  analyzer:
    dockerfile: oss-crs/docker-compose/analyzer.Dockerfile
    additional_env:
      ANALYSIS_MODE: "deep"
```

In the fuzzer container:
```bash
libCRS register-shared-dir /shared-corpus corpus
# Fuzzer writes seeds to /shared-corpus
```

In the analyzer container:
```bash
libCRS register-shared-dir /shared-corpus corpus
# Analyzer reads seeds from /shared-corpus
FUZZER_ADDR=$(libCRS get-service-domain fuzzer)
# Can connect to fuzzer's API at $FUZZER_ADDR
```

---

## Using LLMs in Your CRS

If your CRS leverages LLMs (e.g., for harness generation, code analysis, or patch synthesis):

### 1. Declare Required Models

In `crs.yaml`:
```yaml
required_llms:
  - gpt-4.1
  - claude-sonnet-4-20250514
```

OSS-CRS validates that these models are available in the LiteLLM config before launching.

### 2. Use the LLM API

At runtime, use the provided environment variables with any OpenAI-compatible client:

```python
from openai import OpenAI
import os

client = OpenAI(
    api_key=os.environ["OSS_CRS_LLM_API_KEY"],
    base_url=os.environ["OSS_CRS_LLM_API_URL"],
)

response = client.chat.completions.create(
    model="gpt-4.1",  # Use the model name from required_llms
    messages=[{"role": "user", "content": "Analyze this code for bugs..."}],
)
```

### 3. Budget Management

- LLM budgets are set per-CRS in `crs-compose.yaml` (in dollars)
- All requests are proxied through LiteLLM, which tracks usage and enforces limits
- When the budget is exhausted, LLM requests will be rejected

For LLM configuration details, see [config/llm.md](config/llm.md).

---

## Submitting Artifacts

Your CRS should submit findings through libCRS:

| Artifact | Description | How to submit |
|---|---|---|
| **Seeds** | Interesting fuzzing inputs | `libCRS register-submit-dir seed /output/seeds` or `libCRS submit seed <file>` |
| **PoVs** | Crash-triggering inputs | `libCRS register-submit-dir pov /output/povs` or `libCRS submit pov <file>` |
| **Bug Candidates** | Bug reports for verification | `libCRS register-submit-dir bug-candidate /output/bugs` or `libCRS submit bug-candidate <file>` |
| **Patches** | Fixes for discovered bugs | `libCRS register-submit-dir patch /output/patches` or `libCRS submit patch <file>` |

### Directory Registration vs. Manual Submission

- **`register-submit-dir`** — Best for high-volume output. Forks a daemon that watches the directory, deduplicates files by hash, and submits in batches. Use this for seeds and PoVs.
- **`submit`** — Best for one-off submissions. Submits a single file immediately.

### Adding Metadata

For each submitted file `<name>`, you can optionally create a `.<name>.metadata` JSON file:

```json
{
  "finder": "my-fuzzer-module"
}
```

This attributes the artifact to the component that found it.

---

## Checklist

Before publishing your CRS, verify:

- [ ] `oss-crs/crs.yaml` is valid and complete
- [ ] `prepare_phase.hcl` builds all required images
- [ ] All `target_build_phase` outputs are submitted or skipped via libCRS
- [ ] Run-phase Dockerfiles install libCRS (`COPY --from=libcrs . /opt/libCRS && RUN /opt/libCRS/install.sh`)
- [ ] Containers download build outputs at startup via `libCRS download-build-output`
- [ ] Artifact directories are registered with `libCRS register-submit-dir`
- [ ] `supported_target` accurately reflects your CRS capabilities
- [ ] `required_llms` lists all models used (if any)
- [ ] The CRS runs successfully against at least one OSS-Fuzz project

---

## Further Reading

- [CRS Configuration Reference](config/crs.md) — Full `crs.yaml` schema
- [CRS Compose Configuration](config/crs-compose.md) — `crs-compose.yaml` schema
- [Target Project Configuration](config/target-project.md) — OSS-Fuzz `project.yaml` format
- [LLM Configuration](config/llm.md) — LiteLLM model setup
- [libCRS Reference](design/libCRS.md) — Complete CLI documentation
- [Architecture Overview](design/architecture.md) — System design and component diagram
