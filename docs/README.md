# CRS Infrastructure

This repository contains the infrastructure for building, running, and ensembling Continuous Reasoning Systems (CRSs) as a demonstration of bug-finding capabilities.

## Implemented Features

- Basic workflow for building, running, and ensembling CRSs
- Automated LiteLLM server deployment during CRS operations
- YAML-based configuration for CPU core allocation, memory limits, and LLM budget control

## Prerequisites

The following system dependencies are required:

- **Python 3.9+** - Required for running the CRS tools
- **uv** - Python package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Docker** - Required for building and running CRS containers
- **Git** - Required for cloning repositories
- **rsync** - Required for optimized OSS-Fuzz directory copying

Install rsync if not present:
```bash
# Debian/Ubuntu
apt-get install rsync

# CentOS/RHEL
yum install rsync

# macOS (usually pre-installed)
brew install rsync
```

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Team-Atlanta/oss-fuzz-post-aixcc
```

### 2. Configure API Key

Set your OpenAI API key as an environment variable. The key can be a placeholder value (e.g., `sk-fake-key`), but the variable must be set for the system to run:

```bash
export OPENAI_API_KEY=sk-fake-key
```

Note: Most CRSs in this demo have minimal or zero OpenAI API quota usage.

### 3. Prepare CRS (optional)

Pre-build CRS docker images. This step runs automatically during build if needed, but can be run explicitly:

```bash
uv run oss-bugfind-crs prepare mock-dind
# Rebuild without docker layer cache
uv run oss-bugfind-crs prepare mock-dind --no-cache
```

### 4. Build a CRS

Any [OSS-Fuzz](https://github.com/google/oss-fuzz) project can be used as a target — just pass the project name. The tool automatically clones the project definition from OSS-Fuzz and builds it. Examples below use `json-c` as a reference project.

```bash
# Example 1: Build ensemble-c for the json-c project
uv run oss-bugfind-crs build example_configs/ensemble-c json-c
# Example 2: Build ensemble-java for the java-example project
uv run oss-bugfind-crs build example_configs/ensemble-java java-example
# Rebuild without docker layer cache
uv run oss-bugfind-crs build --no-cache example_configs/ensemble-c json-c
```

Built artifacts will be available under `build/out` and `build/artifacts`.

### 5. Run a Built CRS

Execute a built CRS with a specific fuzzer target:

```bash
# c
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer
# java
uv run oss-bugfind-crs run example_configs/ensemble-java java-example ExampleFuzzer
```

**Expected Output**: The CRS will launch successfully with running logs showing CPU core allocation (base numbers 0-15). For ensemble CRSs, CPU cores are evenly distributed among the contained CRSs.

### 6. Clean Up

Remove build artifacts and cached data:

```bash
# Clean everything
uv run oss-bugfind-crs clean

# Clean specific CRS
uv run oss-bugfind-crs clean --crs atlantis-c-libafl

# Clean specific CRS + project combination
uv run oss-bugfind-crs clean --crs atlantis-c-libafl --project json-c
```

## Testing

### Quick Test Scripts

For quick testing, use the provided test scripts:

#### Build Script

```bash
# Test build with custom project (project name inferred from basename)
./scripts/test-build.sh /home/yufu/aixcc_shared/CRSBench/benchmarks/atlanta-binutils-delta-01

# Test build with both custom project and source path
./scripts/test-build.sh ~/benchmarks/my-project ~/src/my-source
```

The build test script automatically:
- Infers project name from the basename of the project path
- Uses `example_configs/crs-libfuzzer` as the CRS configuration
- Enables `--clone` and `--overwrite` flags for convenient testing

#### Run Script

```bash
# Run with project name and harness
./scripts/test-run.sh libxml2-delta-01 lint

# Run with project path (name inferred from basename)
./scripts/test-run.sh /home/yufu/aixcc_shared/CRSBench/benchmarks/atlanta-binutils-delta-01 my_harness

# Run with additional fuzzer arguments
./scripts/test-run.sh my-project my_fuzzer -max_len=100
```

The run test script automatically:
- Infers project name from basename if a path is provided
- Uses `example_configs/crs-libfuzzer` as the CRS configuration
- Passes through fuzzer arguments

## Options

### Build Directory (`--build-dir`)

Specify a custom build directory for CRS artifacts. Defaults to `./build` in the current directory.

The build directory contains:
- `docker-data/<crs>/` - Docker data for dind CRSs (prepared, build, run phases)
- `out/<crs>/<project>/` - Compiled fuzzers (mounted at `/out`)
- `work/<crs>/<project>/` - Intermediate build files (mounted at `/work`)
- `artifacts/<crs>/<project>/` - Run artifacts (mounted at `/artifacts`)
- `oss-fuzz/<project>/` - OSS-Fuzz clone
- `crs/<hash>/` - Generated compose files
- `src/<project>/` - Cloned project sources (when using `--clone`)
- `ensemble/<config>/<project>/<harness>/` - Ensemble shared dirs (when ensemble enabled)

```bash
# Use custom build directory
uv run oss-bugfind-crs build --build-dir /tmp/my-builds \
                     example_configs/ensemble-c \
                     json-c

# Run must use the same build directory
uv run oss-bugfind-crs run --build-dir /tmp/my-builds \
                   example_configs/ensemble-c \
                   json-c \
                   json_array_fuzzer
```

**Note:** Both `build` and `run` commands must use the same `--build-dir` to share built artifacts.

### OSS-Fuzz Directory (`--oss-fuzz-dir`)

Specify a source OSS-Fuzz repository to copy from. Without this flag, OSS-Fuzz is cloned from GitHub. The working copy is always created at `${BUILD_DIR}/oss-fuzz/${PROJECT}/`.

Useful when:
- Using a forked or customized OSS-Fuzz repository
- Working with AIxCC or other OSS-Fuzz variants
- Avoiding repeated cloning from GitHub

```bash
# Use custom OSS-Fuzz directory
uv run oss-bugfind-crs build --oss-fuzz-dir ~/my-oss-fuzz \
                     example_configs/crs-libfuzzer \
                     json-c

# For AIxCC with custom project image prefix
uv run oss-bugfind-crs build --oss-fuzz-dir oss-fuzz-aixcc \
                     --project-image-prefix aixcc-afc \
                     example_configs/crs-libfuzzer \
                     aixcc/c/asc-nginx \
                     ~/aixcc/oss-fuzz/clone/cp-user-nginx-asc-source

uv run oss-bugfind-crs run --oss-fuzz-dir oss-fuzz-aixcc \
                   example_configs/crs-libfuzzer \
                   aixcc/c/asc-nginx \
                   pov_harness
```

**Note:**
- Without `--oss-fuzz-dir`, OSS-Fuzz is cloned from https://github.com/google/oss-fuzz (sparse checkout with only `infra/` and the target project)
- Use `--project-image-prefix` when working with non-standard OSS-Fuzz forks (e.g., AIxCC uses `aixcc-afc` instead of `gcr.io/oss-fuzz`)

### CRS Registry Directory (`--registry-dir`)

Specify a custom CRS registry directory. Defaults to the bundled `crs_registry/` in the oss-bugfind-crs repository.

The registry contains:
- CRS metadata (`pkg.yaml` for each CRS)
- CRS source references (git URL + ref, or local path)
- CRS-specific configuration (`config-crs.yaml`)
- CRS type and mode information

```bash
# Use custom registry
uv run oss-bugfind-crs build --registry-dir ~/my-crs-registry \
                     example_configs/atlantis-c-libafl \
                     json-c

# Registry with local CRS development
uv run oss-bugfind-crs build --registry-dir ./local-registry \
                     example_configs/my-custom-crs \
                     test-project
```

**Registry Structure:**
```
crs_registry/
├── atlantis-c-libafl/
│   ├── pkg.yaml
│   └── config-crs.yaml
├── crs-libfuzzer/
│   ├── pkg.yaml
│   └── config-crs.yaml
└── my-custom-crs/
    ├── pkg.yaml
    └── config-crs.yaml
```

**Using `local_path` in `pkg.yaml`:**

In the registry, the `local_path` key is supported as an alternative to `url` and `ref` for local CRS development:

```yaml
# pkg.yaml
name: atlantis-c-libafl
type: bug-finding
source:
  local_path: ~/dev/atlantis-c-libafl  # Use local path instead of git URL
```

### External LiteLLM Proxy (`--external-litellm`)

Use an external LiteLLM proxy instead of deploying one alongside the CRSs.

The operator must set up the environment variables `LITELLM_URL` and `LITELLM_KEY`
either in the process environment or in `CONFIG_DIR/.env`.

```bash
export LITELLM_URL=https://my-litellm-proxy:4000
export LITELLM_KEY=sk-litellm-virtual-key
uv run oss-bugfind-crs build --external-litellm example_configs/atlantis-c-libafl json-c
uv run oss-bugfind-crs run --external-litellm example_configs/atlantis-c-libafl json-c json_array_fuzzer
```

### Ensemble Directory (`--ensemble-dir`, `--disable-ensemble`)

Enable cross-CRS seed sharing for ensemble mode. When multiple CRS instances run on the same worker, an ensemble watcher service monitors each CRS's corpus and povs directories, deduplicates by content hash, and shares them to all CRS instances.

**Auto-detection (default)**: When running an ensemble configuration with more than one CRS on the same worker, ensemble directories are automatically created at `build/ensemble/{configuration}/{project}/{harness_name}/`.

```bash
# Ensemble mode - auto-enabled when multiple CRS on same worker
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer
# Creates: build/ensemble/ensemble-c/json-c/json_array_fuzzer/{corpus,povs,crs-data}
```

**Explicit path**: Override the base ensemble directory location (harness name is appended automatically).

```bash
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer \
    --ensemble-dir /custom/ensemble/path
# Creates: /custom/ensemble/path/json_array_fuzzer/{corpus,povs,crs-data}
```

**Disable ensemble**: Opt out of automatic ensemble directory creation.

```bash
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer \
    --disable-ensemble
```

**Initial corpus**: Provide initial corpus files to pre-populate the ensemble corpus.

```bash
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer \
    --corpus /path/to/initial/corpus
```

**Mount structure inside containers**:
- Each CRS gets the shared corpus as a read-only mount at `/seed_share_dir/`
- The ensemble watcher service monitors each CRS's output and deduplicates by content hash

### Custom project path

Provide a custom OSS-Fuzz compatible project directory with `--project-path`.
This allows using out-of-tree projects (e.g., AIxCC challenges, custom benchmarks) without modifying the OSS-Fuzz repository.

The custom project is copied to `oss-fuzz/projects/{project-name}/` during build.
Use `--overwrite` to replace an existing project at that location.

```bash
# Use custom project directory
uv run oss-bugfind-crs build example_configs/crs-libfuzzer \
                     my-custom-project \
                     --project-path ~/my-projects/custom-project

# With nested project names (e.g., aixcc/c/asc-nginx)
uv run oss-bugfind-crs build example_configs/ensemble-c \
                     aixcc/c/asc-nginx \
                     --project-path ~/aixcc/projects/asc-nginx \
                     --overwrite

# Combined with source path override
uv run oss-bugfind-crs build example_configs/atlantis-c-libafl \
                     benchmark-project \
                     ~/src/benchmark-source \
                     --project-path ~/benchmarks/benchmark-proj
```

**Requirements for custom projects:**
- Must contain `project.yaml` with valid OSS-Fuzz metadata
- Must contain `Dockerfile` for building
- Must contain `build.sh` or build instructions
- Should follow OSS-Fuzz project structure conventions

### Clone project source

For custom projects that don't clone source in their Dockerfile, use `--clone` to automatically clone the repository specified in `main_repo` field of `project.yaml`.

The source will be cloned to `build/src/{project_name}/` with depth 1 and recursive submodules.

```bash
# Clone source for custom project
uv run oss-bugfind-crs build --clone \
                     --project-path ~/my-custom-project \
                     example_configs/crs-libfuzzer \
                     my-project

# Clone is idempotent - skips if build/src/{project_name}/ already exists
uv run oss-bugfind-crs build --clone \
                     --project-path ~/benchmarks/atlanta-binutils-delta-01 \
                     example_configs/ensemble-c \
                     atlanta-binutils-delta-01
```

**Note:**
- `--clone` and `source_path` are mutually exclusive
- Standard OSS-Fuzz projects already clone source in their Dockerfile, so `--clone` is not needed for them
- The `project.yaml` must contain a `main_repo` field with a valid git URL

### Gitcache Support (`--gitcache`)

Both `bug_finding` and `bug_fixing` packages support [gitcache](https://github.com/seeraven/gitcache) to accelerate git clone operations through local caching.

Gitcache is a tool that caches git repositories locally and serves them via HTTP. This significantly speeds up repeated git clone operations, which is especially useful when:
- Repeatedly building CRSs (clones CRS repositories from registry)
- Cloning OSS-Fuzz repository
- Cloning project sources with `--clone`
- Working with large repositories or slow network connections

**Installation:**

```bash
# Install gitcache (requires Python)
pip install gitcache
```

**Usage:**

```bash
# Build with gitcache
uv run oss-bugfind-crs build --gitcache example_configs/ensemble-c json-c

# Run with gitcache
uv run oss-bugfind-crs run --gitcache example_configs/ensemble-c json-c json_array_fuzzer

# Bug fixing with gitcache
uv run oss-bugfix-crs build --gitcache my-crs my-project --oss-fuzz ~/oss-fuzz
```

**How it works:**

When `--gitcache` is enabled, all git operations (clone, checkout, submodule update) are prefixed with `gitcache`, which:
1. Checks if the repository is cached locally
2. If cached, serves it from the local cache (fast)
3. If not cached, clones from remote and caches it for future use

**Affected operations:**
- CRS repository cloning (from registry)
- OSS-Fuzz repository cloning
- Project source cloning (when using `--clone`)
- Git submodule updates

For more information about gitcache, see: https://github.com/seeraven/gitcache

## Supported CRSs

- **`atlantis-c-libafl`** - LibAFL-based Atlantis-C for C projects
- **`crs-libfuzzer`** - Vanilla libFuzzer as a reference CRS implementation
- **`atlantis-java-main`** - Atlantis-Java with LLM components enabled (with LLM)
- **`atlantis-java-atljazzer`** - Atlantis-Java with fuzzer-only mode (no LLM)
- **`ensemble-c`** - Ensemble combining `atlantis-c-libafl` and `crs-libfuzzer`
- **`ensemble-java`** - Ensemble combining `atlantis-java-main` and `atlantis-java-atljazzer`

## Repository Structure

- **Main Repository**: [oss-fuzz-post-aixcc](https://github.com/Team-Atlanta/oss-fuzz-post-aixcc)
  - Contains the `oss-bugfind-crs` package and bundled CRS registry (`crs_registry/`)
- **C CRS Implementations**:
  - [atlantis-c-libafl-snapshot](https://github.com/Team-Atlanta/atlantis-c-libafl-snapshot)
  - [crs-libfuzzer](https://github.com/Team-Atlanta/crs-libfuzzer)
- **Java CRS Implementation**: [atlantis-java-snapshot](https://github.com/Team-Atlanta/atlantis-java-snapshot)

## Configuration

Each CRS configuration is located in `example_configs/<crs-name>/` and includes:
- `.env` - Environment variables for database and LiteLLM configuration
- YAML configuration files for resource allocation and CRS-specific settings
