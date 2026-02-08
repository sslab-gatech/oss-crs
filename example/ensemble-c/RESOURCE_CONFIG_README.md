# CRS Resource Configuration Guide

## Overview

This document explains the resource configuration format for deploying CRS (Cyber Reasoning System) instances across multiple workers with fine-grained resource control.

## Design Philosophy

The configuration structure is designed to support:

1. **Multi-worker deployment**: Deploy CRS instances across multiple physical/virtual machines
2. **Flexible resource allocation**: From simple even distribution to explicit per-CRS control
3. **LLM budget management**: Control API costs across all CRS instances
4. **Progressive specification**: Start simple, add detail as needed

## Configuration Structure

### Top Level Sections

```yaml
workers:      # Define available worker machines and their capacities
llm:          # Global LLM resource limits and allocation strategy
crs:          # Per-CRS configuration and placement
```

## Workers Section

Define each worker machine and its available resources:

```yaml
workers:
  local:
    cpu: 16        # Number of CPU cores
    memory: 32G    # RAM capacity
  server1:
    cpu: 32
    memory: 64G
```

**Purpose**: Establishes the total resource pool available for CRS deployment.

## LLM Section

Define total LLM API resources to be shared across all CRS instances:

```yaml
llm:
  max_budget: 100      # Total budget in dollars for ALL CRSs
  max_rpm: 1200        # Total requests per minute across ALL CRSs
  max_tpm: 1000000     # Total tokens per minute across ALL CRSs
```

### Automatic Allocation Logic

The system automatically determines how to divide LLM resources based on what's specified:

**No per-CRS LLM specs** → Even division
- Divide global limits equally among all CRS instances
- Simple and fair
```yaml
llm: {max_budget: 100, ...}
crs:
  crs1: {workers: [local]}
  crs2: {workers: [local]}
# Result: Each gets $50
```

**All CRS have explicit LLM specs** → Validation only
- Use the specified per-CRS values
- Validate that sum ≤ global limits
```yaml
llm: {max_budget: 100, ...}
crs:
  crs1: {llm: {max_budget: 60, ...}}
  crs2: {llm: {max_budget: 40, ...}}
# Result: crs1=$60, crs2=$40, sum=100 ✓
```

**Some CRS have explicit LLM specs** → Mixed allocation
- Use explicit values where specified
- Divide remainder evenly among unspecified CRS
```yaml
llm: {max_budget: 100, ...}
crs:
  crs1: {llm: {max_budget: 60, ...}}
  crs2: {workers: [local]}
  crs3: {workers: [local]}
# Result: crs1=$60, crs2=$20, crs3=$20
```

## CRS Section

Configure individual CRS instances:

### Minimal Configuration

```yaml
crs:
  atlantis-c-libafl:
    workers:
      - local
```

**Result**:
- CPU/memory divided from worker capacity
- LLM resources divided evenly (if no other CRS have explicit specs)

### Simple Configuration

```yaml
crs:
  atlantis-c-libafl:
    workers:
      - local
    resources:
      cpu: 4
      memory: 4G
```

**Result**:
- Same CPU/memory on each worker
- LLM resources divided evenly (if no other CRS have explicit specs)

### Per-Worker Configuration

```yaml
crs:
  atlantis-c-libafl:
    workers:
      - local
      - server1
    resources:
      local:
        cpu: 4
        memory: 4G
      server1:
        cpu: 8
        memory: 8G
```

**Result**:
- Different resources on each worker
- LLM resources divided evenly (if no other CRS have explicit specs)

### Full Explicit Configuration

```yaml
crs:
  atlantis-c-libafl:
    workers:
      - local
    resources:
      local:
        cpu: 8
        memory: 16G
    llm:
      max_budget: 50
      max_rpm: 600
      max_tpm: 500000
```

**Result**:
- Complete control over all resources
- LLM allocation is explicit for this CRS

## LLM Resource Semantics

### Global vs Per-CRS

The design uses **total global limits** that are divided among CRS instances:

```yaml
llm:
  max_budget: 100  # TOTAL for all CRSs
  max_rpm: 1200    # TOTAL for all CRSs
```

**Rationale**:
- Easier to reason about total costs
- Prevents accidental over-allocation
- Mirrors how cloud billing works (one total budget)

**Alternative considered**: Per-CRS specification that sums up
- Pro: Explicit per-CRS control
- Con: Easy to accidentally exceed budget
- Con: Must manually ensure sum equals desired total

### Per-CRS LLM Specification

When you specify LLM resources for a specific CRS:

```yaml
crs:
  atlantis-c-libafl:
    llm:
      max_budget: 30  # This CRS gets $30 of global budget
```

This means:
- This CRS is allocated $30 from the global pool
- Remaining $70 is divided among other CRSs
- Total across all CRS cannot exceed global max_budget

### Division Logic Examples

**Even division (no per-CRS specs):**
```
Global budget: $100
CRS 1: $33.33
CRS 2: $33.33
CRS 3: $33.33
```

**Mixed allocation (partial specs):**
```
Global budget: $100
CRS 1 (explicit): $50
CRS 2 (explicit): $30
CRS 3 (auto): $10  (remaining ÷ 2)
CRS 4 (auto): $10  (remaining ÷ 2)
```

## Example Use Cases

### Use Case 1: Development Environment

**Scenario**: Testing locally with limited resources

```yaml
workers:
  local:
    cpu: 8
    memory: 16G

llm:
  max_budget: 10
  max_rpm: 100
  max_tpm: 100000

crs:
  test-crs-1:
    workers: [local]
  test-crs-2:
    workers: [local]
```

### Use Case 2: Production with Priority Tiers

**Scenario**: High-priority CRS gets more resources

```yaml
workers:
  prod-server:
    cpu: 64
    memory: 128G

llm:
  max_budget: 500
  max_rpm: 5000
  max_tpm: 5000000

crs:
  critical-crs:
    workers: [prod-server]
    resources:
      prod-server:
        cpu: 32
        memory: 64G
    llm:
      max_budget: 300
      max_rpm: 3000
      max_tpm: 3000000

  standard-crs-1:
    workers: [prod-server]
    resources:
      prod-server:
        cpu: 16
        memory: 32G
    # Gets share of remaining: $100, 1000 RPM, 1M TPM

  standard-crs-2:
    workers: [prod-server]
    resources:
      prod-server:
        cpu: 16
        memory: 32G
    # Gets share of remaining: $100, 1000 RPM, 1M TPM
```

### Use Case 3: Multi-Region Deployment

**Scenario**: CRS instances across different servers

```yaml
workers:
  us-east:
    cpu: 32
    memory: 64G
  us-west:
    cpu: 32
    memory: 64G
  eu-central:
    cpu: 32
    memory: 64G

llm:
  max_budget: 300
  max_rpm: 3000
  max_tpm: 3000000

crs:
  atlantis-us-east:
    workers: [us-east]
    resources:
      cpu: 16
      memory: 32G

  atlantis-us-west:
    workers: [us-west]
    resources:
      cpu: 16
      memory: 32G

  atlantis-eu:
    workers: [eu-central]
    resources:
      cpu: 16
      memory: 32G

# Each gets: $100, 1000 RPM, 1M TPM
```

## Implementation Notes

### Validation Rules

1. Per-CRS resources must not exceed worker capacity
2. Total explicit LLM allocations must not exceed global limits
3. Worker names in CRS config must exist in workers section

### Calculation Algorithm

```python
def calculate_llm_allocation(config):
    total_budget = config['llm']['max_budget']
    crs_configs = config['crs']

    # Separate CRS with explicit vs automatic allocation
    explicit_crs = {name: cfg['llm']['max_budget']
                    for name, cfg in crs_configs.items()
                    if 'llm' in cfg}
    auto_crs = [name for name in crs_configs if name not in explicit_crs]

    # Calculate allocation
    if not explicit_crs:
        # All automatic - divide evenly
        per_crs = total_budget / len(crs_configs)
        return {crs: per_crs for crs in crs_configs}
    elif not auto_crs:
        # All explicit - validate and return
        if sum(explicit_crs.values()) > total_budget:
            raise ValueError("Explicit allocations exceed global budget")
        return explicit_crs
    else:
        # Mixed - explicit + divide remainder
        explicit_total = sum(explicit_crs.values())
        if explicit_total > total_budget:
            raise ValueError("Explicit allocations exceed global budget")
        remaining = total_budget - explicit_total
        per_auto = remaining / len(auto_crs)

        result = explicit_crs.copy()
        result.update({crs: per_auto for crs in auto_crs})
        return result
```

## Migration from Old Format

Old format:
```yaml
all:
  cpu: 14
  llm:
    quota: 100

atlantis-c-libafl:
  cpu: 4
  ram: 4G
```

New format:
```yaml
workers:
  local:
    cpu: 14
    memory: 32G

llm:
  max_budget: 100
  max_rpm: 1000
  max_tpm: 100000

crs:
  atlantis-c-libafl:
    workers: [local]
    resources:
      cpu: 4
      memory: 4G
```

## Future Extensions

Possible additions:
- GPU resource specification
- Network bandwidth limits
- Storage/disk quotas
- Auto-scaling parameters
- Cost optimization policies
