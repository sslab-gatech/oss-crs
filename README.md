# OSS-CRS: LLM-era Bug Finding and Remediation for Open Source Software

OSS-CRS (Cyber Reasoning System) is a unified orchestration framework for LLM-based bug-finding and remediation systems. It provides budget control and ensembling for Java and C projects, and uses [OSS-Fuzz](https://github.com/google/oss-fuzz) as the default target format, enabling support for 1,000+ projects out of the box.

## Quickstart

### 1. Set up the environment

```bash
# Clone the OSS-Fuzz repository (e.g., Team Atlanta's fork)
git clone https://github.com/Team-Atlanta/oss-fuzz-post-aixcc

# Configure API key (OPENAI_API_KEY is required)
export OPENAI_API_KEY="sk-fake-key"
```

### 2. Build and run a simple bug-finding or remediation system

Any [OSS-Fuzz](https://github.com/google/oss-fuzz) project can be used as a target (e.g., `json-c`, `libxml2`, `openssl`).

```bash
# prepare (one-time, pre-builds CRS docker images)
uv run oss-bugfind-crs prepare crs-libfuzzer

# build (using json-c as an example OSS-Fuzz project)
uv run oss-bugfind-crs build example_configs/crs-libfuzzer json-c

# run
uv run oss-bugfind-crs run example_configs/crs-libfuzzer json-c json_array_fuzzer

# clean (remove build artifacts)
uv run oss-bugfind-crs clean
```

Note: The prepare step is optional — it runs automatically during build if needed.

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
