# External System Integration

## Overview

The `oss-crs` package integrates with several external systems to provide a complete CRS deployment solution. This document describes the integration points and how they work together.

## OSS-Fuzz Integration

### Purpose

OSS-Fuzz provides the base fuzzing infrastructure that CRS implementations build upon:
- Standard project structure and build system
- Language-specific Docker base images
- Fuzzing harnesses and corpus management
- Build scripts (`compile`, `precompile`)

### Integration Points

#### 1. OSS-Fuzz Repository Management

- **Location**: `build/oss-fuzz/` (default) or custom via `--oss-fuzz-dir`
- **Auto-cloning**: `_clone_oss_fuzz_if_needed()` in `crs_main.py`
  - Clones from `https://github.com/google/oss-fuzz.git` if not present
  - Checks for `.git` directory to verify valid repo

#### 2. Project Image Building

- **Function**: `_build_project_image()` in `crs_main.py`
- **Command**: `python infra/helper.py build_image <project-name>`
- **Output**: Docker image `gcr.io/oss-fuzz/<project-name>` (or custom prefix)
- **Purpose**: Creates base image with project source code and dependencies

#### 3. Language Detection

- **Function**: `get_project_language()` in `render_compose.py`
- **Source**: Reads `project.yaml` from OSS-Fuzz projects directory
- **Usage**: Determines CRS compatibility (e.g., Java CRS for Java projects)

#### 4. Directory Structure

```
build/oss-fuzz/
├── infra/
│   └── helper.py              # Build tooling
├── projects/
│   └── <project-name>/
│       ├── project.yaml       # Language and fuzzing config
│       └── Dockerfile         # Project-specific build
└── ...
```

### Data Flow

```
OSS-Fuzz Repository
    ↓ (clone if needed)
build/oss-fuzz/
    ↓ (read project.yaml)
Detect project language
    ↓ (run helper.py build_image)
Base Docker image (gcr.io/oss-fuzz/<project>)
    ↓ (used as base for CRS image)
CRS Docker image
```

## Docker Compose Orchestration

### Purpose

Docker Compose manages multi-container CRS deployments with:
- Service definitions (LiteLLM, CRS runners)
- Resource constraints (CPU, memory)
- Volume mounts and networking
- Build context and dependencies

### Integration Points

#### 1. Template Rendering

- **Templates**: Located in `oss_crs/templates/`
  - `build-compose.yaml.j2` - Build-time compose file
  - `run-compose.yaml.j2` - Runtime compose file
  - `litellm-compose.yaml.j2` - LiteLLM service
- **Engine**: Jinja2 template rendering
- **Function**: `render_compose_for_worker()` in `render_compose.py`

#### 2. Service Definitions

**Build Phase**:
- `crs-builder` service: Builds CRS Docker image from source
- `litellm` service (optional): Provides LLM proxy during build

**Run Phase**:
- `crs-runner` service: Executes CRS for fuzzing
- `litellm` service (optional): Provides LLM proxy during execution

#### 3. Resource Management

Applied to Docker Compose services:
- **CPU pinning**: `cpuset_cpus` parameter
- **Memory limits**: `mem_limit` parameter
- **Environment variables**: API keys, configuration paths

#### 4. Volume Mounts

```yaml
volumes:
  - <build-dir>/out:/out                    # OSS-Fuzz build artifacts
  - <build-dir>/corpus:/corpus              # Fuzzing corpus
  - <config-dir>:/config                    # CRS configuration
  - <litellm-shared>:/shared                # LiteLLM key exchange
```

### Command Execution

```python
# Build
subprocess.run(['docker', 'compose', '-f', 'build-compose.yaml', 'up', '--build'])

# Run
subprocess.run(['docker', 'compose', '-f', 'run-compose.yaml', 'up'])
```

## LiteLLM Proxy Integration

### Purpose

LiteLLM provides a unified proxy for multiple LLM providers with:
- Budget management and rate limiting
- Virtual API keys
- Multi-provider support (OpenAI, Anthropic, etc.)
- Request tracking and logging

### Integration Modes

#### 1. Internal Mode (Default)

oss-crs automatically deploys and manages LiteLLM:
- Starts LiteLLM service in Docker Compose
- Provisions virtual API keys
- Sets budgets per CRS instance
- Handles service lifecycle

**Workflow**:
```
Start LiteLLM service
    ↓
Wait for health check (http://localhost:4000/health)
    ↓
Generate virtual keys via REST API
    ↓
Store keys in shared volume (/shared/litellm_key)
    ↓
CRS containers read keys at startup
```

#### 2. External Mode (`--external-litellm`)

Uses operator-provided LiteLLM instance:
- Reads `LITELLM_URL` and `LITELLM_KEY` from environment
- Validates connectivity before build/run
- Passes keys directly to CRS containers
- No key provisioning performed

**Configuration**: `<config-dir>/.env`
```bash
LITELLM_URL=https://my-litellm-proxy:4000
LITELLM_KEY=sk-litellm-virtual-key
```

### Key Provisioner API

#### Health Check
```python
GET {litellm_url}/health
→ 200 OK: Service ready
```

#### Key Generation
```python
POST {litellm_url}/key/generate
Headers: Authorization: Bearer {master_key}
Body: {
    "models": ["gpt-4", "claude-3"],
    "max_budget": 10.0,
    "budget_duration": "24h"
}
→ Response: {"key": "sk-litellm-..."}
```

#### Key Storage
```python
# Stored in shared volume
/shared/litellm_key_{crs_name}
```

### Budget Allocation

Handled by `calculate_budgets()` in `key_provisioner.py`:
- Explicit budgets: Specified in CRS config
- Auto-allocation: Divides remaining budget equally
- Validation: Ensures total doesn't exceed worker budget

## CRS Registry Integration

### Purpose

The CRS Registry stores CRS metadata and source references:
- CRS package definitions (`pkg.yaml`)
- Git repository URLs and versions (or local paths)
- CRS-specific configuration (`config-crs.yaml`)
- Build instructions and dependencies

### Registry Structure

The registry is bundled with the oss-crs package at `crs_registry/`:

```
crs_registry/
├── atlantis-c-libafl/
│   ├── pkg.yaml
│   └── config-crs.yaml
├── crs-libfuzzer/
│   ├── pkg.yaml
│   └── config-crs.yaml
├── atlantis-java-main/
│   ├── pkg.yaml
│   └── config-crs.yaml
└── ...
```

### Package Definition (`pkg.yaml`)

```yaml
name: atlantis-c-libafl
type: bug-finding
source:
  url: https://github.com/Team-Atlanta/atlantis-c-libafl.git
  ref: main
  # OR for local development:
  # local_path: ~/atlantis-c-libafl
```

### Integration Points

#### 1. Registry Location

- **Default**: `crs_registry/` (bundled with oss-crs package)
- **Custom**: Via `--registry-dir` flag
- **Implementation**: `DEFAULT_REGISTRY_DIR = files(oss_crs).parent / 'crs_registry'` in `crs_main.py:27`

#### 2. CRS Resolution

**Function**: `clone_crs_if_needed()` in `render_compose.py`

**Remote source**:
```python
git clone {url} build/crs/{crs_name}
git checkout {ref}
```

**Local source**:
```python
# Use local_path directly (no cloning)
build_context = {local_path}
```

#### 3. Usage in Compose

```yaml
services:
  crs-builder:
    build:
      context: {crs_path}  # From registry
      dockerfile: Dockerfile
```

## Jinja2 Template System

### Purpose

Provides flexible, configuration-driven compose file generation without code changes.

### Template Locations

```
oss_crs/templates/
├── build-compose.yaml.j2    # Build-time services
├── run-compose.yaml.j2      # Runtime services
└── litellm-compose.yaml.j2  # LiteLLM proxy
```

### Rendering Pipeline

```python
# 1. Load template
env = jinja2.Environment(loader=FileSystemLoader('templates'))
template = env.get_template('build-compose.yaml.j2')

# 2. Prepare context
context = {
    'crs_name': 'atlantis-c-libafl',
    'crs_path': '/path/to/crs',
    'cpus': '0-7',
    'memory': '8G',
    'litellm_url': 'http://litellm:4000',
    ...
}

# 3. Render
output = template.render(**context)

# 4. Write compose file
with open('build-compose.yaml', 'w') as f:
    f.write(output)
```

### Template Variables

Common variables passed to templates:
- `crs_name`, `crs_path`, `project_name`
- `cpus`, `memory`, `budget`
- `litellm_url`, `litellm_key`
- `oss_fuzz_dir`, `build_dir`, `output_dir`
- `config_hash` (for isolation)

### Benefits

- **Flexibility**: Change deployment without code changes
- **Testability**: Templates can be tested independently
- **Maintainability**: Clear separation of config from logic
- **Extensibility**: Easy to add new services or configurations
