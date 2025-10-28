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
