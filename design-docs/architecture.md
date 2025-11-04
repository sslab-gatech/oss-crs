# oss-crs Architecture

## Overview

The `oss-crs` package is a command-line tool that orchestrates the building and execution of Cyber Reasoning Systems (CRS) using Docker Compose. It provides a standardized interface for CRS deployment with resource management, LiteLLM integration, and OSS-Fuzz compatibility.

## System Components

### 1. CLI Layer (`__main__.py`)

Entry point providing two main commands:
- `oss-crs build` - Build CRS Docker images
- `oss-crs run` - Execute built CRS containers

Responsibilities:
- Command-line argument parsing
- Path validation and directory creation
- Delegating to core logic layer

### 2. Core Logic Layer (`crs_main.py`)

Orchestrates the build and run workflows:
- `build_crs()` - Manages the complete build process
- `run_crs()` - Manages runtime execution and cleanup
- Helper functions for OSS-Fuzz management, image building, validation

Responsibilities:
- Workflow coordination between components
- OSS-Fuzz repository management
- Docker image building
- Service lifecycle management (start, stop, cleanup)
- Signal handling for graceful shutdown

### 3. Compose Generation (`render_compose.py`)

Generates Docker Compose files from templates and configuration:
- Configuration loading and merging
- Resource allocation (CPU, memory, LLM budgets)
- Template rendering with Jinja2
- CRS repository management

Responsibilities:
- YAML configuration parsing and validation
- CPU/memory allocation across CRS instances
- Docker Compose file generation for build and run phases
- Config hashing for isolation

### 4. Key Provisioner (`key_provisioner/key_provisioner.py`)

Manages LiteLLM API keys and budgets:
- `LiteLLMKeyProvisioner` class for key lifecycle
- Budget calculation and allocation
- Health checking and key storage

Responsibilities:
- LiteLLM service health verification
- API key generation with rate/budget limits
- Budget allocation across CRS instances
- Key persistence to shared volumes

## Data Flow

### Build Workflow

```
CLI (build command)
    ↓
crs_main.build_crs()
    ↓
1. Clone OSS-Fuzz if needed
2. Build project Docker image (OSS-Fuzz base)
    ↓
render_compose.render_build_compose()
    ├─ Load configurations (resource, worker, CRS)
    ├─ Clone/verify CRS repositories
    ├─ Allocate resources per CRS
    └─ Generate build compose files
    ↓
3. Start LiteLLM service (if internal mode)
4. Build CRS Docker images using compose
5. Inject source code (if local development)
    ↓
Built CRS images ready for execution
```

### Run Workflow

```
CLI (run command)
    ↓
crs_main.run_crs()
    ↓
render_compose.render_run_compose()
    ├─ Load configurations
    ├─ Allocate runtime resources
    └─ Generate runtime compose files
    ↓
1. Start LiteLLM service (if internal mode)
2. Provision API keys with budgets
3. Launch CRS runner containers
4. Monitor and handle signals (SIGINT/SIGTERM)
5. Cleanup on completion
```

## Design Principles

### Separation of Concerns

- **CLI layer**: Argument parsing and user interaction only
- **Business logic**: Workflow orchestration separate from infrastructure details
- **Infrastructure**: Docker Compose generation isolated in dedicated module
- **Services**: Key provisioning as independent, reusable component

### Infrastructure as Code

- YAML-based configuration for reproducibility
- Jinja2 templates for flexible compose generation
- Config hashing for version isolation
- Declarative resource specifications

### Flexibility

Multiple modes for different deployment scenarios:
- **LiteLLM**: Internal (auto-deployed) or external (operator-provided)
- **CRS sources**: Git repositories or local directories
- **OSS-Fuzz**: Default clone or custom directory
- **Resource allocation**: Fine-grained, global, or auto-division modes

### Resource Management

Three-tier resource allocation:
1. **Worker level**: Total available resources
2. **CRS level**: Resources per CRS instance
3. **Container level**: Resources per Docker container

Includes validation and conflict detection for CPU pinning.

## Directory Structure

```
oss-crs/
├── oss_crs/
│   ├── __main__.py              # CLI entry point
│   ├── crs_main.py              # Core build/run logic
│   ├── render_compose.py        # Compose generation
│   ├── key_provisioner/
│   │   └── key_provisioner.py   # LiteLLM key management
│   └── templates/               # Jinja2 templates
│       ├── build-compose.yaml.j2
│       ├── run-compose.yaml.j2
│       └── litellm-compose.yaml.j2
├── example_configs/             # Example CRS configurations
│   ├── ensemble-c/
│   ├── ensemble-java/
│   └── ...
└── build/                       # Build artifacts (created at runtime)
    ├── crs/                     # Built CRS images
    ├── out/                     # OSS-Fuzz build output
    └── oss-fuzz/                # OSS-Fuzz clone
```

## Module Interactions

```
__main__.py (CLI)
    ↓
crs_main.py (Orchestration)
    ├─→ render_compose.py (Config & Templates)
    │       ↓
    │   Jinja2 Templates
    │
    └─→ key_provisioner.py (LiteLLM Keys)
            ↓
        LiteLLM REST API
```

## Key Design Decisions

1. **Docker Compose over direct Docker**: Simplifies multi-container orchestration and resource management

2. **Template-based generation**: Allows flexible configuration without code changes

3. **Config hashing**: Enables isolated builds for different configurations

4. **Modular key provisioning**: Separates LiteLLM concerns from core CRS logic

5. **OSS-Fuzz integration**: Leverages existing fuzzing infrastructure rather than rebuilding

6. **Three-tier resource model**: Provides flexibility for single CRS, ensembles, and complex deployments
