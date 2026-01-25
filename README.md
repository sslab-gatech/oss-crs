# OSS-CRS: LLM-era Bug Finding and Remediation for Open Source Software

OSS-CRS (Cyber Reasoning System) is a unified orchestration framework for LLM-based bug-finding and remediation systems. It provides budget control and ensembling for Java and C projects, and uses [OSS-Fuzz](https://github.com/google/oss-fuzz) as the default target format, enabling support for 1,000+ projects out of the box.

## Quickstart

### 1. Set up the environment

```bash
# Prereqs: install `uv` and ensure Docker works on your machine.
# Configure API key (OPENAI_API_KEY is required for direct OpenAI usage)
export OPENAI_API_KEY="sk-fake-key"
```

### 2. Build and run a simple bug-finding or bug-fixing system

```bash
# Build and run a bug-finding CRS
# See more options: uv run oss-bugfind-crs build/run --help
uv run oss-bugfind-crs build example_configs/crs-libfuzzer json-c
uv run oss-bugfind-crs run example_configs/crs-libfuzzer json-c json_array_fuzzer

# Build and run a bug-fixing CRS
# `atlantis-multi-retrieval` is a bundled CRS config in `crs_registry/atlantis-multi-retrieval/`.
# Provide OSS-Fuzz by copying it to `./.oss-fuzz/` or passing `--oss-fuzz /path/to/oss-fuzz`.
# See more options: uv run oss-bugfix-crs build/run --help
uv run oss-bugfix-crs build atlantis-multi-retrieval json-c
uv run oss-bugfix-crs run atlantis-multi-retrieval json-c --povs /path/to/povs --harness json_array_fuzzer \
    --litellm-base $URL --litellm-key $KEY --out /tmp/out-test
```

### 3. Build and run an ensemble system (combining multiple systems together)

```bash
# Choose configured CRS from `example_configs` and build it:
# Example: Build ensemble-java for the json-example project
uv run oss-bugfind-crs build example_configs/ensemble-java java-example
# Run the built systems
uv run oss-bugfind-crs run example_configs/ensemble-java java-example ExampleFuzzer
```

## Documentation

Read our [detailed documentation](./docs/README.md) to learn more about OSS-CRS.
