# Cgroup-Parent Resource Management Setup

This document explains how to set up and use the `--cgroup-parent` feature for Docker container resource management in oss-crs.

## Overview

The `--cgroup-parent` feature allows fine-grained CPU and memory resource control for CRS containers by leveraging Linux cgroup v2. This is useful for:

- Running multiple CRS instances with isolated resources
- Preventing resource contention between containers
- Enforcing strict CPU affinity and memory limits per CRS

## Architecture

### Cgroup Hierarchy

```
/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/
├── cgroup.subtree_control   <- cpuset+memory enabled (auto)
└── oss-crs/
    ├── cgroup.subtree_control   <- cpuset+memory enabled (auto)
    └── <run_id>-<phase>-<timestamp>-<random>-<worker>/  (worker cgroup)
        ├── cpuset.cpus          <- Total CPUs for all CRS
        ├── memory.max           <- Total memory for all CRS
        ├── cgroup.subtree_control   <- cpuset+memory enabled
        └── <crs-name>/          (per-CRS sub-cgroup)
            ├── cpuset.cpus      <- CRS-specific CPUs
            └── memory.max       <- CRS-specific memory
```

### Docker Integration

The cgroup path is passed to Docker's `cgroup_parent` option with `/sys/fs/cgroup` prefix stripped:

```
Docker receives: /user.slice/user-1000.slice/user@1000.service/oss-crs/worker-123/crs-name
Actual path:     /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/oss-crs/worker-123/crs-name
```

## Requirements

### 1. Docker Cgroup Driver

Docker **must** use the `cgroupfs` cgroup driver (not `systemd`).

#### Check Current Driver

```bash
docker info | grep "Cgroup Driver"
```

#### Configure cgroupfs Driver

If Docker is using `systemd` driver, configure it to use `cgroupfs`:

**1. Edit or create `/etc/docker/daemon.json`:**

```json
{
  "exec-opts": ["native.cgroupdriver=cgroupfs"]
}
```

**2. Restart Docker:**

```bash
sudo systemctl restart docker
```

**3. Verify:**

```bash
docker info | grep "Cgroup Driver"
# Should show: Cgroup Driver: cgroupfs
```

### 2. Cgroup v2 Delegation

The user must have write access to `cgroup.subtree_control` under their user service cgroup.

#### Automatic Setup

oss-crs automatically:
1. Creates the `oss-crs` directory under `user@<uid>.service`
2. Enables `cpuset` and `memory` controllers at all hierarchy levels

#### Manual Verification

```bash
# Check controller delegation (should show cpuset and memory)
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control

# If not enabled, set up manually:
uid=$(id -u)
gid=$(id -g)
base_path="/sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service"

# Enable controllers at user@<uid>.service level
echo "+cpuset +memory" | sudo tee ${base_path}/cgroup.subtree_control

# Create oss-crs directory with proper ownership
sudo mkdir -p ${base_path}/oss-crs
sudo chown ${uid}:${gid} ${base_path}/oss-crs

# Enable controllers at oss-crs level
echo "+cpuset +memory" | tee ${base_path}/oss-crs/cgroup.subtree_control
```

## Usage

### Build Phase

```bash
oss-crs build \
  --cgroup-parent \
  example_configs/ensemble-c \
  json-c
```

### Run Phase

```bash
oss-crs run \
  --cgroup-parent \
  example_configs/ensemble-c \
  json-c \
  json_array_fuzzer
```

### Resource Configuration

Resources are defined in `config-resource.yaml`:

```yaml
crs:
  - name: crs1
    cpuset: "0-3"          # CPU cores 0-3
    memory_limit: "4G"     # 4GB memory

  - name: crs2
    cpuset: "4-7"          # CPU cores 4-7
    memory_limit: "8G"     # 8GB memory

workers:
  local:
    cpuset: "0-15"         # Total CPUs (union of all CRS)
    memory: "16G"          # Total memory (sum of all CRS)
```

## Implementation Details

### Key Functions

#### `cgroup_path_for_docker(cgroup_path: Path) -> str`

Converts filesystem cgroup path to Docker-compatible format by stripping `/sys/fs/cgroup` prefix.

**Example:**
```python
# Input:  /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/oss-crs/worker
# Output: /user.slice/user-1000.slice/user@1000.service/oss-crs/worker
```

#### `check_docker_cgroup_driver() -> tuple[bool, str]`

Validates Docker is using `cgroupfs` driver. Returns `(is_cgroupfs, driver_name)`.

#### `setup_cgroup_hierarchy(base_path: Path) -> None`

Automatically sets up cgroup hierarchy:
1. Creates `oss-crs` directory
2. Enables `cpuset` and `memory` controllers at `user@<uid>.service` level
3. Enables controllers at `oss-crs` level

#### `create_crs_cgroups(...) -> Path`

Creates worker cgroup with per-CRS sub-cgroups:
1. Worker cgroup with total resources (union/sum of all CRS)
2. Per-CRS sub-cgroups with individual limits

### Docker Compose Integration

Cgroup parent is set in `compose.yaml.j2`:

```yaml
services:
  crs_builder:
    # ... other config ...
    cgroup_parent: {{ cgroup_parent }}/{{ crs.name }}
```

The `cgroup_parent` variable is set to the output of `cgroup_path_for_docker()`.

## Troubleshooting

### Error: "cgroup-parent for systemd cgroup should be a valid slice"

**Cause:** Docker is using `systemd` cgroup driver instead of `cgroupfs`.

**Solution:** Configure Docker to use `cgroupfs` driver (see Requirements section).

### Error: "Failed to set up cgroup hierarchy: Permission denied"

**Cause:** User lacks write permission to `cgroup.subtree_control`.

**Solution:** Follow manual verification steps in "Cgroup v2 Delegation" section.

### Warning: "Failed to remove cgroup: Device or resource busy"

**Status:** Known issue - currently disabled (commented out).

**Explanation:** Docker containers may still hold references to the cgroup after exit, causing cleanup to fail. The system will automatically clean up cgroups when they become empty (no processes).

**TODO:** Implement proper cleanup with retry logic or wait for container termination.

### Verify Cgroup Setup

```bash
# Check if cgroup was created
uid=$(id -u)
ls -la /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/

# Check worker cgroup resources
worker_cgroup=$(ls /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/ | head -1)
cat /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/${worker_cgroup}/cpuset.cpus
cat /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/${worker_cgroup}/memory.max

# Check per-CRS sub-cgroup
cat /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/${worker_cgroup}/crs1/cpuset.cpus
cat /sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/oss-crs/${worker_cgroup}/crs1/memory.max

# Verify containers are using the cgroup
docker ps --format "{{.Names}}" | xargs -I {} docker inspect {} --format '{{.Name}}: {{.HostConfig.CgroupParent}}'
```

## References

- [Docker Cgroup v2 Documentation](https://docs.docker.com/config/containers/runmetrics/)
- [Linux Cgroup v2 Documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [Docker Resource Management with Cgroups](https://baykara.medium.com/docker-resource-management-via-cgroups-and-systemd-633b093a835c)
