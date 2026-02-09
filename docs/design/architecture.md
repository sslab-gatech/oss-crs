# OSS-CRS Architecture

## Overview

OSS-CRS is an orchestration framework for building and running LLM-based autonomous bug-finding and bug-fixing systems (Cyber Reasoning Systems). The architecture is designed around three core principles: **isolation** (each CRS runs in its own containerized environment), **composability** (multiple CRSs can be ensembled in a single campaign), and **portability** (CRSs run across different environments without modification).

The system is composed of three major layers:

1. **CRS Compose (Orchestration Layer)** — Manages the lifecycle of one or more CRSs. Currently supports local execution via Docker Compose, with Azure deployment planned.
2. **Individual CRS Containers** — Isolated per-CRS execution environments, each communicating through libCRS.
3. **oss-crs-infra (Shared Infrastructure)** — Central services shared across all CRSs, including LLM budget management and (planned) deduplication and monitoring services.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CRS Compose (Orchestrator)                         │
│                                                                             │
│   Lifecycle:  prepare  ──▶  build-target  ──▶  run                         │
│                                                                             │
│   Config:  crs-compose.yaml                                                 │
│   (defines CRS list, resources, LLM config, run environment)                │
└────────────┬──────────────────────────────┬─────────────────────────────────┘
             │                              │
             ▼                              ▼
┌────────────────────────┐   ┌────────────────────────┐
│    CRS A (Isolated)    │   │    CRS B (Isolated)    │   ... (N CRSs)
│                        │   │                        │
│  ┌──────────────────┐  │   │  ┌──────────────────┐  │
│  │  Container 1     │  │   │  │  Container 1     │  │
│  │  (e.g., fuzzer)  │  │   │  │  (e.g., analyzer)│  │
│  └──────────────────┘  │   │  └──────────────────┘  │
│  ┌──────────────────┐  │   │  ┌──────────────────┐  │
│  │  Container 2     │  │   │  │  Container 2     │  │
│  │  (e.g., analyzer)│  │   │  │  (e.g., fuzzer)  │  │
│  └──────────────────┘  │   │  └──────────────────┘  │
│                        │   │                        │
│  Resources: cpuset,    │   │  Resources: cpuset,    │
│    memory, llm_budget  │   │    memory, llm_budget  │
│                        │   │                        │
│  Networks:             │   │  Networks:             │
│   - CRS-A private net  │   │   - CRS-B private net  │
│   - shared infra net   │   │   - shared infra net   │
└────────┬───────────────┘   └────────┬───────────────┘
         │ ▲                          │ ▲
         │ │  via libCRS              │ │  via libCRS
         │ │                          │ │
         │ │  Submit/Fetch            │ │  Submit/Fetch
         │ │  seeds, PoVs,            │ │  seeds, PoVs,
         │ │  bug candidates, patches │ │  bug candidates, patches
         ▼ │                          ▼ │
┌─────────────────────────────────────────────────────────────────────────────┐
│                          oss-crs-infra                                      │
│                     (Shared Infrastructure)                                 │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │   LiteLLM Proxy  │  │  Seed Dedup      │  │   WebUI                  │   │
│  │  [Implemented]   │  │  [Planned]       │  │   [Planned]              │   │
│  │                  │  │                  │  │                          │   │
│  │ - Budget mgmt    │  │ - Deduplication  │  │  - Coverage monitoring   │   │
│  │ - Per-CRS keys   │  │ - Cross-CRS      │  │  - Bug candidate view    │   │
│  │ - Model routing  │  │   seed sharing   │  │  - PoV status            │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │  PostgreSQL      │  │  PoV Dedup       │  │   Storage                │   │
│  │  [Implemented]   │  │  [Planned]       │  │   [Implemented]          │   │
│  │                  │  │                  │  │                          │   │
│  │ - LiteLLM state  │  │ - Verification   │  │  - Seeds                 │   │
│  │ - Budget tracking│  │ - Deduplication  │  │  - PoVs                  │   │
│  └──────────────────┘  └──────────────────┘  │  - Bug candidates        │   │
│                                              │  - Patches               │   │
│                                              └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1. CRS Compose (Orchestration Layer)

CRS Compose is the top-level orchestrator that manages the entire lifecycle of a campaign. It is configured via a single `crs-compose.yaml` file and exposes three commands:

| Phase | Command | Description |
|---|---|---|
| **Prepare** | `crs-compose prepare` | Pulls CRS source repositories, builds Docker images using `docker buildx bake` |
| **Build Target** | `crs-compose build-target` | Builds the target project (OSS-Fuzz format) and runs each CRS's target build pipeline |
| **Run** | `crs-compose run` | Launches all CRSs and infrastructure via Docker Compose |

**Configuration (`crs-compose.yaml`)** file declares:
- `run_env` — Execution environment (`local`, with `azure` planned)
- `docker_registry` — Registry for caching/publishing CRS images
- `oss_crs_infra` — Resource allocation (cpuset, memory) for shared infrastructure
- Per-CRS entries — Each CRS with its source (git URL + ref, or local path), resource limits (`cpuset`, `memory`), and optional `llm_budget` (in dollars)
- `llm_config` — Path to a LiteLLM configuration file defining available LLM models

For the full configuration reference, see [docs/config/crs-compose.md](../config/crs-compose.md).

## 2. CRS Isolation Model

Each CRS is a self-contained unit consisting of one or more **modules** (Docker containers). CRSs are completely isolated from each other at the network, filesystem, and resource level.

### CRS Definition (`crs.yaml`)

Every CRS repository contains an `oss-crs/crs.yaml` file that declares:

- **Prepare phase** — An HCL file for `docker buildx bake` to build the CRS images
- **Target build phase** — A list of build steps, each with a Dockerfile and expected outputs
- **Run phase** — A set of named modules (containers) that constitute the CRS at runtime
- **Supported targets** — Languages, sanitizers, and architectures the CRS supports
- **Required LLMs** — Model names the CRS needs (validated against the LiteLLM config before launch)

### Resource Isolation

Each CRS enforces strict resource boundaries via Docker:

| Resource | Mechanism |
|---|---|
| **CPU** | `cpuset` — pins containers to specific CPU cores |
| **Memory** | `mem_limit` — hard memory cap |
| **LLM Budget** | Per-CRS API key with dollar budget tracked by LiteLLM |
| **Network** | Private Docker network per CRS; shared infra network for oss-crs-infra access |

## 3. libCRS (CRS Communication Library) — [libCRS.md](libCRS.md)

libCRS is a Python library installed in every CRS container. It provides a uniform API for CRSs to interact with the infrastructure, regardless of the deployment environment.

The quick reference below summarizes the common registration and submission/fetch flows.

### Quick Reference: Register/Submit/Fetch

#### ✅ Build Output Submission Functions
```
$ libCRS submit-build-output <src path in container> <path in output fs>
$ libCRS skip-build-output <path in output fs>
```
#### ✅ Submission Functions
```
$ libCRS register-submit-dir pov /povs
$ libCRS register-submit-dir seed /seeds
$ libCRS register-submit-dir bug-candidate /bug-candidates

$ libCRS submit pov <pov_file_path>
$ libCRS submit seed <seed_file_path>
$ libCRS submit bug-candidate <bug_candidate_file_path>
```
 
#### ✅ Sharing File between Containers in a CRS
```
$ libCRS register-shared-dir <local_dir_path> <shared_fs_path>
```

#### 📝 Fetching Functions (TODO)
```
$ libCRS register-fetch-dir pov /shared-povs
$ libCRS register-fetch-dir seed /shared-seeds
$ libCRS register-fetch-dir bug-candidate /shared-bug-candidates

$ libCRS fetch pov <dst_dir_path> # return a list of file names
$ libCRS fetch seed <dst_dir_path>
$ libCRS fetch bug-candidate <dst_dir_path>
```

#### 📝Patching Functions (TODO)
```
$ libCRS apply-patch-build <patch diff file> <dst dir path>
```

#### ✅ Network Functions
```
$ libCRS get-service-domain <service name>
```

## 4. oss-crs-infra (Shared Infrastructure)

oss-crs-infra provides centralized services that all CRSs share. It runs in its own resource-constrained containers with dedicated CPU and memory allocations.

### ✅ LLM Budget Management (Implemented) — [litellm.md](oss-crs-infra/litellm.md)

The LLM subsystem uses [LiteLLM](https://github.com/BerriAI/litellm) as a proxy:

- **Unified API**: CRSs send requests to a single endpoint (`$OSS_CRS_LLM_API_URL`), abstracting all model providers (OpenAI, Anthropic, Google, etc.)
- **Per-CRS API Keys**: Each CRS receives a unique `$OSS_CRS_LLM_API_KEY` at launch
- **Budget Enforcement**: Dollar-denominated limits per CRS, tracked in PostgreSQL
- **Model Routing**: Logical model names are mapped to provider-specific models via LiteLLM config

LLM setup flow during `crs-compose run`:

1. CRS Compose generates per-CRS API keys
2. The `litellm-key-gen` service registers keys and budgets in LiteLLM
3. CRS containers receive their API key via `OSS_CRS_LLM_API_KEY` and endpoint via `OSS_CRS_LLM_API_URL`
4. All LLM requests are proxied through LiteLLM, which enforces budgets and logs usage

### 📝 Seed Deduplication Service (Planned) — [seed-dedup.md](oss-crs-infra/seed-dedup.md)

Will provide cross-CRS seed deduplication to avoid redundant fuzzing effort across CRSs in an ensemble.

### 📝 PoV Verification/Deduplication Service (Planned) — [pov-dedup.md](oss-crs-infra/pov-dedup.md)

Will verify proof-of-vulnerability inputs and deduplicate crashes found by multiple CRSs, providing a unified view of unique bugs.

### 📝 WebUI (Planned) — [webui.md](oss-crs-infra/webui.md)

A monitoring dashboard for observing the status of running CRSs, including:
- Code coverage metrics
- Bug candidates discovered
- PoV status and verification results
- LLM usage and budget consumption
