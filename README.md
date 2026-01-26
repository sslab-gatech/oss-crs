# OSS-CRS: LLM-era Bug Finding and Remediation for Open Source Software

OSS-CRS (Cyber Reasoning System) is a unified orchestration framework for LLM-based bug-finding and remediation systems. It provides budget control and ensembling for Java and C projects, and uses [OSS-Fuzz](https://github.com/google/oss-fuzz) as the default target format, enabling support for 1,000+ projects out of the box.

## Quickstart

### 1. Set up the environment

- python >= 3.10
- docker
- git
- rsync
- uv (optional, but preferred)

### 2. Build and run a simple bug-finding or bug-fixing system

#### libfuzzer

We packaged libfuzzer (the default OSS-Fuzz fuzzer) for OSS-CRS.
The bug-finding workflow is composed of two primary stages:
1. `build` which lets CRSs perform custom instrumenation and compilation for the fuzzers
2. `run` which lets CRSs run the fuzzers

```bash
# Build and run a bug-finding CRS
# See more options: uv run oss-bugfind-crs build/run --help
uv run oss-bugfind-crs build example_configs/crs-libfuzzer json-c
uv run oss-bugfind-crs run example_configs/crs-libfuzzer json-c json_array_fuzzer
```

#### atlantis-multi-retrieval

```bash
# Build and run a bug-fixing CRS
# `atlantis-multi-retrieval` is a bundled CRS config in `crs_registry/atlantis-multi-retrieval/`.
# Provide OSS-Fuzz by copying it to `./.oss-fuzz/` or passing `--oss-fuzz /path/to/oss-fuzz`.
# See more options: uv run oss-bugfix-crs build/run --help
uv run oss-bugfix-crs build atlantis-multi-retrieval json-c
uv run oss-bugfix-crs run atlantis-multi-retrieval json-c --povs /path/to/povs --harness json_array_fuzzer \
    --litellm-base $URL --litellm-key $KEY --out /tmp/out-test
```

#### Note on LLM Keys

For bug-finding systems that need LLM capabilities, the user must provide appropriate keys.
For example, [atlantis-java](https://github.com/sslab-gatech/oss-crs/blob/main/crs_registry/atlantis-java-main/config-crs.yaml)
requires OpenAI models such as GPT-5.
The user must provide `OPENAI_API_KEY` as an environment variable before launching `atlantis-java`.
Environment variables mapping to models can be found at our default
[config-litellm.yaml](https://github.com/sslab-gatech/oss-crs/blob/main/bug_finding/templates/config-litellm.yaml).

```bash
# Build artifacts (no LLM requirement or support)
uv run oss-bugfind-crs build example_configs/atlantis-java-main java-example
# atlantis-java needs an OpenAI key
export OPENAI_API_KEY=<OpenAI Key>
uv run oss-bugfind-crs run example_configs/atlantis-java-main java-example ExampleFuzzer
```

Currently, only bug-finding systems support native LLM provider keys.
The bug-fixing systems *must* provide a LiteLLM proxy and virtual key.
As such, if you need to run both systems we recommend setting up a LiteLLM proxy and
use it for both bug-finding and bug-fixing.

```bash
# Build artifacts (no LLM requirement or support)
uv run oss-bugfind-crs build example_configs/atlantis-java-main java-example
# Use --external-litellm for LiteLLM proxy usage
export LITELLM_URL=<LiteLLM Proxy URL>
export LITELLM_KEY=<LiteLLM Virtual Key>
uv run oss-bugfind-crs run --external-litellm example_configs/atlantis-java-main java-example ExampleFuzzer
```

We aim to resolve this discrepancy with [#20](https://github.com/sslab-gatech/oss-crs/issues/20).

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
