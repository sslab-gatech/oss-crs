# CRS Infrastructure

This repository contains the infrastructure for building, running, and ensembling Continuous Reasoning Systems (CRSs) as a demonstration of bug-finding capabilities.

## Implemented Features

- Basic workflow for building, running, and ensembling CRSs
- Automated LiteLLM server deployment during CRS operations
- YAML-based configuration for CPU core allocation, memory limits, and LLM budget control

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

### 3. Build a CRS

Choose a configured CRS from `example_configs` and build it:

```bash
# Example 1: Build ensemble-c for the json-c project
uv run oss-crs build example_configs/ensemble-c json-c
# Example 2: Build ensemble-java for the java-example project
uv run oss-crs build example_configs/ensemble-java java-example
```

Built artifacts will be available under `build/crs` and `build/out`.

### 4. Run a Built CRS

Execute a built CRS with a specific fuzzer target:

```bash
# c
uv run oss-crs run example_configs/ensemble-c json-c json_array_fuzzer
# java
uv run oss-crs run example_configs/ensemble-java java-example ExampleFuzzer
```

**Expected Output**: The CRS will launch successfully with running logs showing CPU core allocation (base numbers 0-15). For ensemble CRSs, CPU cores are evenly distributed among the contained CRSs.

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

### External LiteLLM Proxy

As an alternative to the default behaviour of deploying a LiteLLM proxy along with the CRSs,
the `--external-litellm` instead forwards the operator provided LiteLLM URL and key
to the CRSs.

The operator must set up the environment variables `LITELLM_URL` and `LITELLM_KEY`
either in the process environment or in `CONFIG_DIR/.env`.

```bash
export LITELLM_URL=https://my-litellm-proxy:4000
export LITELLM_KEY=sk-litellm-virtual-key
uv run oss-crs build --external-litellm example_configs/atlantis-c-libafl json-c
uv run oss-crs run --external-litellm example_configs/atlantis-c-libafl json-c json_array_fuzzer
```

### Custom CRS registry

Provide the path to the custom registry with `--registry-dir`.

```bash
uv run oss-crs build --registry-dir oss-crs-registry \
                     example_configs/atlantis-c-libafl \
                     json-c
```

In the registry, the key `local_path` is supported as an alternative to `url` and `ref`.

```yaml pkg.yaml
name: atlantis-c-libafl
type: bug-finding
source:
  local_path: ~/atlantis-c-libafl
```

### Custom project path

Provide a custom OSS-Fuzz compatible project directory with `--project-path`.
This allows using out-of-tree projects (e.g., AIxCC challenges, custom benchmarks) without modifying the OSS-Fuzz repository.

The custom project is copied to `oss-fuzz/projects/{project-name}/` during build.
Use `--overwrite` to replace an existing project at that location.

```bash
# Use custom project directory
uv run oss-crs build example_configs/crs-libfuzzer \
                     my-custom-project \
                     --project-path ~/my-projects/custom-project

# With nested project names (e.g., aixcc/c/asc-nginx)
uv run oss-crs build example_configs/ensemble-c \
                     aixcc/c/asc-nginx \
                     --project-path ~/aixcc/projects/asc-nginx \
                     --overwrite

# Combined with source path override
uv run oss-crs build example_configs/atlantis-c-libafl \
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
uv run oss-crs build --clone \
                     --project-path ~/my-custom-project \
                     example_configs/crs-libfuzzer \
                     my-project

# Clone is idempotent - skips if build/src/{project_name}/ already exists
uv run oss-crs build --clone \
                     --project-path ~/benchmarks/atlanta-binutils-delta-01 \
                     example_configs/ensemble-c \
                     atlanta-binutils-delta-01
```

**Note:**
- `--clone` and `source_path` are mutually exclusive
- Standard OSS-Fuzz projects already clone source in their Dockerfile, so `--clone` is not needed for them
- The `project.yaml` must contain a `main_repo` field with a valid git URL

### Custom OSS-Fuzz directory

Provide the path to the custom OSS-Fuzz repository with `--oss-fuzz-dir`.
In case the project image is built with a different prefix than `gcr.io/oss-fuzz`
(e.g. for AIxCC, the prefix is `aixcc-afc`),
then use the `--project-image-prefix` for the build command.

```bash
uv run oss-crs build --oss-fuzz-dir oss-fuzz-aixcc \
                     --project-image-prefix aixcc-afc \
                     example_configs/crs-libfuzzer \
                     aixcc/c/asc-nginx \
                     ~/aixcc/oss-fuzz/clone/cp-user-nginx-asc-source

uv run oss-crs run --oss-fuzz-dir oss-fuzz-aixcc \
                   example_configs/crs-libfuzzer \
                   aixcc/c/asc-nginx \
                   pov_harness
```

## Supported CRSs

- **`atlantis-c-libafl`** - LibAFL-based Atlantis-C for C projects
- **`crs-libfuzzer`** - Vanilla libFuzzer as a reference CRS implementation
- **`atlantis-java-main`** - Atlantis-Java with LLM components enabled (with LLM)
- **`atlantis-java-atljazzer`** - Atlantis-Java with fuzzer-only mode (no LLM)
- **`ensemble-c`** - Ensemble combining `atlantis-c-libafl` and `crs-libfuzzer`
- **`ensemble-java`** - Ensemble combining `atlantis-java-main` and `atlantis-java-atljazzer`

## Repository Structure

- **Entry Repository**: [oss-fuzz-post-aixcc](https://github.com/Team-Atlanta/oss-fuzz-post-aixcc)
- **CRS Registry**: [oss-crs-registry](https://github.com/Team-Atlanta/oss-crs-registry)
- **C CRS Implementations**:
  - [atlantis-c-libafl-snapshot](https://github.com/Team-Atlanta/atlantis-c-libafl-snapshot)
  - [crs-libfuzzer](https://github.com/Team-Atlanta/crs-libfuzzer)
- **Java CRS Implementation**: [atlantis-java-snapshot](https://github.com/Team-Atlanta/atlantis-java-snapshot)

## Configuration

Each CRS configuration is located in `example_configs/<crs-name>/` and includes:
- `.env` - Environment variables for database and LiteLLM configuration
- YAML configuration files for resource allocation and CRS-specific settings
