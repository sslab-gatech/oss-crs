# OSS-CRS: Cyber Reasoning Systems for Open Source Software

OSS-CRS is an orchestration framework for building and running Cyber Reasoning Systems: autonomous bug-finding and bug-fixing systems that work against OSS-Fuzz-style targets.

OSS-CRS provides:

- a standard CRS lifecycle: `prepare`, `build-target`, and `run`
- support for OSS-Fuzz-compatible target projects
- Docker-based isolation with per-CRS CPU, memory, and optional LLM budgets
- ensemble execution for running multiple CRSs in one campaign
- registry-backed CRS resolution
- `libCRS` standard interface for submitting seeds, PoVs, bug candidates, patches, and build outputs

## Quick Start

### Prerequisites

- Python 3.10+
- Docker
- Git
- [`uv`](https://github.com/astral-sh/uv)

### Prepare a Target Project

```bash
git clone https://github.com/google/oss-fuzz.git ~/oss-fuzz
```

OSS-CRS works with projects that follow the [OSS-Fuzz](https://github.com/google/oss-fuzz) project structure. You can use a project from `google/oss-fuzz` or prepare your own compatible target repository.

### Run a Simple CRS

The example below uses `crs-libfuzzer`, a lightweight CRS that runs libFuzzer on the target. See [`./example/crs-libfuzzer/compose.yaml`](example/crs-libfuzzer/compose.yaml) for the full configuration.

```bash
# Prepare the CRS
uv run oss-crs prepare \
  --compose-file ./example/crs-libfuzzer/compose.yaml

# Build the target project
uv run oss-crs build-target \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

# Run the CRS against the "xml" harness
uv run oss-crs run \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

### Run an LLM-Backed CRS

For a CRS that uses LLMs, use one of the Atlantis multilang examples. The example below uses [`./example/atlantis-multilang-wo-concolic/compose.yaml`](example/atlantis-multilang-wo-concolic/compose.yaml).

LLM-backed runs require provider credentials. You can export them in your shell or place them in a `.env` file in the directory where you run `oss-crs`; the CLI loads `.env` automatically before parsing the compose file.

<!-- TODO fix the LLM keys somehow -->

```bash
export OPENAI_API_KEY=<OPENAI_API_KEY>
export GEMINI_API_KEY=<GEMINI_API_KEY>
export ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY>

uv run oss-crs prepare \
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml

uv run oss-crs build-target \
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

uv run oss-crs run \
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

See [LLM configuration](docs/config/llm.md) for provider setup details.

### Run an Ensemble of Multiple CRSs

Combine multiple CRSs in a single campaign by defining them in an ensemble compose file. For example, [`./example/ensemble/compose.yaml`](example/ensemble/compose.yaml) launches multiple CRSs simultaneously with independent resource allocation.

```bash
uv run oss-crs prepare \
  --compose-file ./example/ensemble/compose.yaml

uv run oss-crs build-target \
  --compose-file ./example/ensemble/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

export OPENAI_API_KEY=<OPENAI_API_KEY>
export GEMINI_API_KEY=<GEMINI_API_KEY>
export ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY>

uv run oss-crs run \
  --compose-file ./example/ensemble/compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

Each CRS runs independently, and results are aggregated automatically.

<!-- TODO run a patching CRS on the results of bug-finding crashes -->

## Build Your Own CRS

Follow the [CRS development guide](docs/crs-development-guide.md) to package your bug-finding or bug-fixing tool as a CRS. Once integrated, your CRS can:

- work with OSS-Fuzz-compatible targets
- run through the standard `prepare`, `build-target`, and `run` lifecycle
- compose with other CRSs in ensemble campaigns

## Documentation

- [Docs index](docs/README.md): setup, development, reference, and design docs
- [CRS development guide](docs/crs-development-guide.md): how to build or integrate your own CRS
- [Compose file reference](docs/config/crs-compose.md): top-level orchestration config
- [CRS config reference](docs/config/crs.md): per-CRS prepare, build, and run config
- [Target project config](docs/config/target-project.md): target setup and OSS-Fuzz compatibility
- [Registry guide](docs/registry.md): using and publishing registry-backed CRSs
- [Setup guide](docs/setup.md): host setup for enhanced resource management
- [Builder sidecar](docs/crs-development-guide.md#builder-sidecar): incremental rebuild service for patch-testing CRSs
- [LLM configuration](docs/config/llm.md): provider setup and LiteLLM configuration
- [libCRS README](libCRS/README.md): CRS helper library and command interface
- [Changelog](CHANGELOG.md): breaking changes, deprecations, and migration notes
- [Plan / open TODOs](PLAN.md): planned improvements

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

See [LICENSE](LICENSE) for details.
