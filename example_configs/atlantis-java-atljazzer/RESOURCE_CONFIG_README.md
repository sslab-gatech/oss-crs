# atlantis-java-atljazzer CRS Configuration

## Overview

This configuration directory contains settings for deploying the atlantis-java-atljazzer CRS (Cyber Reasoning System) for Java fuzzing on OSS-Fuzz projects.

## Configuration Files

### config-resource.yaml
Defines:
- **Workers**: Available machines and their resources (CPU cores, memory)
- **LLM Budget**: Total API budget, rate limits, and token limits shared across all CRS instances
- **CRS Placement**: Which workers each CRS should run on, with optional resource constraints

**Current Setup:**
- Single worker named `local` with 16 CPU cores (0-15) and 16GB RAM
- Total LLM budget: $10
- atlantis-java-atljazzer uses auto-divided resources on the local worker

### config-crs.yaml
CRS-specific configuration:
- **models**: List of LLM models to use (gpt-4o-mini)
- **ncpu**: CPU allocation strategy (1-all means use all available cores assigned)

### config-litellm.yaml
LiteLLM proxy configuration:
- Defines model endpoints and API key sources
- Uses OPENAI_API_KEY from environment

### config-worker.yaml
Optional worker-specific overrides (currently empty)

## Usage

### Building CRS

```bash
# Build the atlantis-java-atljazzer CRS for a specific project
python infra/helper.py build_crs \
  infra/crs/example_configs/atlantis-java-atljazzer \
  <project-name> \
  --engine libfuzzer \
  --sanitizer address
```

**Example for commons-imaging:**
```bash
python infra/helper.py build_crs \
  infra/crs/example_configs/atlantis-java-atljazzer \
  commons-imaging
```

### Running CRS

```bash
# Run the CRS on a specific fuzzer
python infra/helper.py run_crs \
  infra/crs/example_configs/atlantis-java-atljazzer \
  <project-name> \
  <fuzzer-name> \
  [fuzzer-args...] \
  --worker local
```

**Example:**
```bash
python infra/helper.py run_crs \
  infra/crs/example_configs/atlantis-java-atljazzer \
  commons-imaging \
  ImagingUtilsFuzzer \
  -max_total_time=3600 \
  --worker local
```

## Environment Setup

### Required Environment Variables

Create a `.env` file in this directory with:

```bash
# OpenAI API Key for LLM access
OPENAI_API_KEY=sk-your-api-key-here

# LiteLLM Master Key (for securing the proxy)
LITELLM_MASTER_KEY=your-secure-master-key
```

**Note:** The `.env` file is gitignored and should never be committed.

### Example .env File

```bash
cp infra/crs/example_configs/env.example .env
# Edit .env with your actual keys
```

## Resource Allocation

### Default Allocation (Auto-division)

When no explicit resource constraints are specified in `config-resource.yaml`:
- CPU cores: Divided evenly among all CRS instances on the worker
- Memory: Divided evenly among all CRS instances on the worker
- LLM budget: Divided evenly among all CRS instances

### Custom Allocation

To specify explicit resources for atlantis-java-atljazzer, edit `config-resource.yaml`:

```yaml
crs:
  atlantis-java-atljazzer:
    workers:
      - local
    resources:
      local:
        cpuset: "0-7"   # Use CPUs 0-7
        memory: "8G"    # Use 8GB RAM
    llm:
      max_budget: 5     # Use $5 of the total budget
      max_rpm: 600      # Up to 600 requests per minute
      max_tpm: 500000   # Up to 500k tokens per minute
```

## Multi-Worker Deployment

To deploy across multiple machines:

```yaml
workers:
  local:
    cpuset: "0-15"
    memory: "16G"
  server1:
    cpuset: "0-31"
    memory: "32G"

crs:
  atlantis-java-atljazzer:
    workers:
      - local
      - server1
    resources:
      local:
        cpuset: "0-7"
        memory: "8G"
      server1:
        cpuset: "0-15"
        memory: "16G"
```

Then specify worker when running:
```bash
# Run on local worker
python infra/helper.py run_crs ... --worker local

# Run on server1 worker
python infra/helper.py run_crs ... --worker server1
```

## Ensembling with Other CRS

To run atlantis-java-atljazzer alongside other CRS (e.g., atlantis-c-libafl):

```yaml
crs:
  atlantis-java-atljazzer:
    workers:
      - local
    resources:
      local:
        cpuset: "0-7"
        memory: "8G"

  atlantis-c-libafl:
    workers:
      - local
    resources:
      local:
        cpuset: "8-15"
        memory: "8G"
```

See [infra/crs/example_configs/ensemble-c](../ensemble-c/) for a complete ensembling example.

## Troubleshooting

### Build Failures

1. **CRS repository not found:**
   - Ensure `atlantis-java-atljazzer` is registered in `crs_registry/`
   - Check the CRS repository access (git URL or local path in `pkg.yaml`)

2. **Out of memory during build:**
   - Increase memory allocation in `config-resource.yaml`
   - Reduce number of parallel CRS builds

3. **LiteLLM connection failed:**
   - Verify `.env` file exists with valid API keys
   - Check network connectivity to LiteLLM service

### Runtime Failures

1. **Fuzzer not found:**
   - Ensure `build_crs` completed successfully
   - Check `build/out/atlantis-java-atljazzer/<project>/` for fuzzer binaries

2. **API rate limit exceeded:**
   - Reduce `max_rpm` in `config-resource.yaml`
   - Increase `max_budget` if hitting cost limits

3. **CPU/Memory constraints too tight:**
   - Monitor resource usage during fuzzing
   - Adjust `cpuset` and `memory` allocations

## Advanced Configuration

### Custom LLM Models

Edit `config-litellm.yaml` to add custom models:

```yaml
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: claude-3-5-sonnet-20241022
    litellm_params:
      model: claude-3-5-sonnet-20241022
      api_key: os.environ/ANTHROPIC_API_KEY
```

Then update `config-crs.yaml`:
```yaml
atlantis-java-atljazzer:
  models:
    - gpt-4o-mini
    - claude-3-5-sonnet-20241022
  ncpu: 1-all
```

### Resource Monitoring

Monitor CRS resource usage:

```bash
# Check running containers
docker ps | grep atlantis-java-atljazzer

# Monitor CPU/memory usage
docker stats | grep atlantis-java-atljazzer

# View logs
docker logs <container-id>
```

## References

- [General Resource Configuration Guide](../reference/RESOURCE_CONFIG_README.md)
- [Ensemble Configuration Example](../ensemble-c/RESOURCE_CONFIG_README.md)
- [OSS-Fuzz Documentation](https://google.github.io/oss-fuzz/)
