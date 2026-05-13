# OSS-CRS Documentation

Start with the [project landing page](../README.md) if you want the high-level story. This section is for setup, development, and reference material once you know where OSS-CRS fits.

## Start Here

| Topic | Why you would read it |
|---|---|
| [Quick start](../README.md#quick-start) | Run a baseline CRS against an OSS-Fuzz-style target |
| [Setup guide](setup.md) | Configure host-side cgroup support for better runtime isolation |
| [Registry guide](registry.md) | See what CRSs are already available |
| [CRS development guide](crs-development-guide.md) | Build and package your own CRS |

## Reference

| Topic | What it covers |
|---|---|
| [Compose config](config/crs-compose.md) | Top-level campaign orchestration in `crs-compose.yaml` |
| [CRS config](config/crs.md) | Per-CRS definition in `oss-crs/crs.yaml` |
| [Target project config](config/target-project.md) | Target expectations and OSS-Fuzz project metadata |
| [LLM config](config/llm.md) | LiteLLM config used by internal mode |

## Design Notes

| Topic | What it covers |
|---|---|
| [Architecture](design/architecture.md) | Main components and lifecycle |
| [Parallel builds and runs](design/parallel.md) | `run_id` and `build_id` isolation model |
| [libCRS design notes](design/libCRS.md) | Library-level communication details |
| [LiteLLM provider notes](llm-providers.md) | Routing to local and remote model backends |

## Key Concepts

### CRS Lifecycle

Every CRS campaign follows three phases managed by `oss-crs`:

1. **Prepare**: fetch CRS sources and build Docker images with `oss-crs prepare`
2. **Build Target**: compile the target project and run each CRS's target build pipeline with `oss-crs build-target`
3. **Run**: launch CRSs and shared infrastructure with `oss-crs run`

Pass `--incremental-build` to `build-target` to create Docker snapshots for faster rebuilds, and pass it to `run` to use snapshot images for ephemeral rebuild containers.

### CRS Isolation

Each CRS runs in its own containerized environment with resource boundaries:

- **CPU**: pinned to specific cores through `cpuset`
- **Memory**: capped through `mem_limit`
- **LLM Budget**: enforced per CRS through LiteLLM when configured
- **Network**: private Docker network per CRS, with shared infrastructure access where needed

Run `oss-crs setup` to enable [cgroup-parent mode](setup.md) for flexible resource sharing within each CRS.

### Ensemble Campaigns

Multiple CRSs can be composed in a single `crs-compose.yaml` and run simultaneously. Each CRS keeps its own resource allocation, while shared infrastructure and result aggregation are handled by the framework.

## Project Docs

- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)
- [Plan / open TODOs](../PLAN.md)
