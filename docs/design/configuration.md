# Configuration System

## Overview

The `oss-crs` configuration system uses YAML files to specify resource allocation, CRS settings, and deployment parameters. Configurations are organized hierarchically with clear precedence rules.

## Configuration Files

### Directory Structure

```
example_configs/<crs-name>/
├── .env                     # Environment variables (optional)
├── resource.yaml            # Worker-level resources (optional)
├── worker.yaml              # Worker configuration (optional)
├── <crs-name>.yaml         # CRS-specific configuration
└── <variant>.yaml          # Additional CRS variants (for ensembles)
```

### File Purposes

#### 1. `.env` - Environment Variables

Optional file for sensitive data and external service configuration.

**Example**:
```bash
# LiteLLM configuration
LITELLM_URL=https://my-litellm-proxy:4000
LITELLM_KEY=sk-litellm-virtual-key

# Database credentials
DATABASE_URL=postgresql://user:pass@localhost/db
```

**Usage**: Loaded by `python-dotenv` and merged with process environment.

#### 2. `resource.yaml` - Worker Resources

Defines total available resources for the worker.

**Schema**:
```yaml
cpus: "0-15"              # CPU cores available
memory: "16G"             # Total memory
litellm_budget: 100.0     # Total LLM budget (USD)
```

**Fields**:
- `cpus`: CPU core range (e.g., "0-7", "0,2,4,6") or count (8)
- `memory`: Memory limit with unit (G, M, K)
- `litellm_budget`: Total budget for LLM API calls

#### 3. `worker.yaml` - Worker Configuration

Specifies which CRS instances to run and their resource allocation.

**Schema**:
```yaml
crses:
  - name: atlantis-c-libafl
    cpus: "0-7"           # CPU cores for this CRS
    memory: "8G"          # Memory limit
    litellm_budget: 50.0  # LLM budget (optional)

  - name: crs-libfuzzer
    cpus: "8-15"
    memory: "8G"
    litellm_budget: 50.0
```

**Purpose**: Enables fine-grained resource control per CRS instance.

#### 4. CRS Configuration Files

CRS-specific settings and parameters.

**Example** (`atlantis-c-libafl.yaml`):
```yaml
fuzzer: libafl
timeout: 3600
max_iterations: 1000
llm_models:
  - gpt-4
  - claude-3-sonnet
```

**Purpose**: Passed to CRS container as configuration.

## Resource Allocation

### Allocation Modes

The system supports three resource allocation modes, determined by configuration presence:

#### 1. Fine-Grained Mode

**When**: `worker.yaml` exists with per-CRS resource specifications

**Behavior**: Resources explicitly allocated to each CRS

**Example**:
```yaml
# worker.yaml
crses:
  - name: crs-a
    cpus: "0-3"
    memory: "4G"
    litellm_budget: 30.0
  - name: crs-b
    cpus: "4-7"
    memory: "4G"
    litellm_budget: 70.0
```

**Validation**:
- CPU ranges must not overlap
- Total memory ≤ worker memory (if specified)
- Total budget ≤ worker budget (if specified)

#### 2. Global Mode

**When**: `resource.yaml` exists but `worker.yaml` doesn't

**Behavior**: All resources available to single CRS

**Example**:
```yaml
# resource.yaml
cpus: "0-7"
memory: "8G"
litellm_budget: 100.0
```

Result: Single CRS gets all resources.

#### 3. Auto-Division Mode

**When**: Neither `resource.yaml` nor `worker.yaml` exists

**Behavior**: System auto-detects available resources and divides equally

**Logic**:
- CPU: Divided equally among CRS instances
- Memory: Divided equally (or uses system default)
- Budget: Divided equally (or unlimited if not specified)

### CPU Allocation

#### Parsing

**Function**: `parse_cpu_range()` in `render_compose.py`

**Formats**:
- Range: `"0-7"` → `[0,1,2,3,4,5,6,7]`
- List: `"0,2,4,6"` → `[0,2,4,6]`
- Count: `8` → First 8 cores from system

#### Conflict Detection

**Function**: `get_crs_for_worker()`

**Validation**:
```python
# Check for CPU overlap between CRS instances
for crs_a, crs_b in combinations(crses, 2):
    if set(crs_a.cpus) & set(crs_b.cpus):
        raise ValueError(f"CPU conflict between {crs_a.name} and {crs_b.name}")
```

#### Docker Compose Format

```yaml
services:
  crs-runner:
    cpuset_cpus: "0-7"    # Pins to cores 0-7
```

### Memory Allocation

#### Parsing

**Function**: `parse_memory_mb()` in `render_compose.py`

**Formats**:
- Gigabytes: `"8G"` → 8192 MB
- Megabytes: `"4096M"` → 4096 MB
- Kilobytes: `"1024K"` → 1 MB

#### Validation

```python
# Ensure total doesn't exceed worker memory
total_memory = sum(crs.memory for crs in crses)
if total_memory > worker.memory:
    raise ValueError("CRS memory exceeds worker memory")
```

#### Docker Compose Format

```yaml
services:
  crs-runner:
    mem_limit: "8G"
```

### LLM Budget Allocation

#### Calculation

**Function**: `calculate_budgets()` in `key_provisioner.py`

**Algorithm**:
```python
1. Allocate explicit budgets from worker.yaml
2. Calculate remaining budget:
   remaining = worker_budget - sum(explicit_budgets)
3. Count CRS without explicit budget
4. Auto-allocate:
   auto_budget = remaining / count_without_budget
5. Return final budget per CRS
```

#### Example

```yaml
# worker.yaml
litellm_budget: 100.0
crses:
  - name: crs-a
    litellm_budget: 30.0   # Explicit
  - name: crs-b
    # No budget specified
  - name: crs-c
    # No budget specified
```

Result:
- crs-a: 30.0
- crs-b: 35.0 (auto-allocated)
- crs-c: 35.0 (auto-allocated)

#### LiteLLM API Format

```python
POST /key/generate
{
    "models": ["gpt-4", "claude-3"],
    "max_budget": 35.0,
    "budget_duration": "24h"
}
```

## Configuration Precedence

### Loading Order

1. **Process environment** - System environment variables
2. **`.env` file** - Config-specific environment
3. **YAML files** - Explicit configuration files

### Merge Strategy

- **Environment variables**: Later sources override earlier
- **YAML fields**: Explicit values override defaults
- **Resource allocation**: Fine-grained > Global > Auto

### Example

```bash
# System environment
export LITELLM_KEY=system-key

# .env file
LITELLM_KEY=config-key
LITELLM_URL=http://localhost:4000

# Result
LITELLM_KEY=config-key    # .env overrides system
LITELLM_URL=http://localhost:4000  # From .env
```

## Template Rendering

### Process

```python
# 1. Load configurations
resource_config = load_config('resource.yaml')
worker_config = load_config('worker.yaml')
crs_config = load_config('crs.yaml')

# 2. Allocate resources
crs_list = get_crs_for_worker(worker_config, resource_config)

# 3. Prepare template context
context = {
    'crs_name': crs.name,
    'cpus': format_cpu_range(crs.cpus),
    'memory': f"{crs.memory}M",
    'litellm_budget': crs.budget,
    'config_hash': compute_hash(crs_config),
    ...
}

# 4. Render template
compose_content = template.render(**context)

# 5. Write output
write_file(f'build-compose-{config_hash}.yaml', compose_content)
```

### Config Hashing

**Purpose**: Isolate builds for different configurations

**Function**: Computes hash of configuration files

**Usage**:
```python
config_hash = hashlib.md5(config_content).hexdigest()[:8]
build_dir = f"build/crs-{config_hash}/"
```

**Benefits**:
- Multiple configs can coexist
- Prevents config conflicts
- Cache invalidation on config change

## Validation

### Schema Validation

Performed during configuration loading:
- Required fields present
- Correct data types (int, str, float)
- Valid format (CPU ranges, memory units)

### Resource Validation

Performed during allocation:
- CPU ranges don't overlap
- Total memory within limits
- Total budget within limits
- CRS exists in registry

### Runtime Validation

Performed before execution:
- OSS-Fuzz directory exists
- Project image available
- LiteLLM service reachable (if external)
- Required directories writable

## Common Configurations

### Single CRS

```yaml
# resource.yaml
cpus: "0-7"
memory: "8G"
litellm_budget: 100.0
```

### Ensemble (Multiple CRS)

```yaml
# worker.yaml
crses:
  - name: atlantis-c-libafl
    cpus: "0-7"
    memory: "8G"
    litellm_budget: 50.0

  - name: crs-libfuzzer
    cpus: "8-15"
    memory: "8G"
    litellm_budget: 50.0
```

### External LiteLLM

```bash
# .env
LITELLM_URL=https://my-proxy:4000
LITELLM_KEY=sk-litellm-virtual-key
```

```bash
# Command
oss-crs build --external-litellm config/ project
```

### Local CRS Development

```yaml
# Registry pkg.yaml
name: my-crs
type: bug-finding
source:
  local_path: ~/dev/my-crs  # Use local directory
```

## Best Practices

1. **Use fine-grained mode for ensembles**: Explicit resource control prevents conflicts
2. **Set budgets conservatively**: LLM costs can accumulate quickly
3. **Pin CPUs for performance**: Reduces context switching overhead
4. **Version control configs**: Track configuration changes with code
5. **Use config hashing**: Enables parallel experiments with different configs
6. **Validate before running**: Check resource limits and service availability
