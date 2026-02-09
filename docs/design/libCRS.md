# libCRS — CRS Communication Library

## Overview

libCRS is a Python CLI library installed in every CRS container. It provides a uniform interface for CRS containers to interact with the OSS-CRS infrastructure — submitting artifacts (seeds, PoVs, bug candidates), sharing files between containers within a CRS, managing build outputs, and resolving service endpoints.

By abstracting these operations behind a single CLI, libCRS allows CRS developers to write infrastructure-agnostic code: the same `libCRS` commands work regardless of whether the CRS runs locally via Docker Compose or (in the future) on a cloud deployment.

## Installation

libCRS is installed inside CRS container images during the Docker build phase. The provided `install.sh` script handles the full setup:

```bash
# Inside a Dockerfile
COPY libCRS /opt/libCRS
RUN /opt/libCRS/install.sh
```

This installs:
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- `rsync` (used for file operations)
- The `libCRS` CLI tool, available at `/usr/local/bin/libCRS`

### Dependencies

- Python >= 3.10
- `watchdog >= 6.0.0` (filesystem event monitoring for directory registration)
- `rsync` (installed automatically by `install.sh`)

## Environment Variables

libCRS relies on several environment variables injected by CRS Compose at container startup:

| Variable | Description |
|---|---|
| `OSS_CRS_RUN_ENV_TYPE` | Execution environment type (`local`) |
| `OSS_CRS_NAME` | Name of the CRS (used for network domain resolution and metadata) |
| `OSS_CRS_BUILD_OUT_DIR` | Shared filesystem path for build outputs |
| `OSS_CRS_SUBMIT_DIR` | Shared filesystem path for submitted artifacts (seeds, PoVs, etc.) |
| `OSS_CRS_SHARED_DIR` | Shared filesystem path for inter-container file sharing within a CRS |

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      CRS Container                       │
│                                                          │
│   CRS Code  ──▶  libCRS CLI  ──▶  libCRS Library        │
│                                        │                 │
│                                        ▼                 │
│                                  ┌──────────┐            │
│                                  │ CRSUtils │            │
│                                  └────┬─────┘            │
│                                       │                  │
└───────────────────────────────────────┼──────────────────┘
                                        │
               ┌────────────┬───────────┼──────────┐
               ▼            ▼           ▼          ▼
          ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐
          │  Build  │ │ Submit / │ │ Shared  │ │ Network │
          │ Output  │ │  Fetch   │ │   FS    │ │ (DNS)   │
          └─────────┘ └──────────┘ └─────────┘ └─────────┘
```

libCRS uses the **strategy pattern** via the abstract `CRSUtils` base class. Currently, `LocalCRSUtils` implements all operations for local Docker Compose deployments. An `AzureCRSUtils` implementation is planned to support Azure-based deployments (e.g., using Azure Blob Storage for shared filesystems and Azure Container Instances for CRS execution). New deployment backends can be added by implementing the `CRSUtils` interface without changing the CLI or any CRS code.

### Data Types

All submission and fetching operations work with one of the following data types:

| Type | Description |
|---|---|
| `pov` | Proof-of-vulnerability inputs that trigger bugs |
| `seed` | Fuzzing seed inputs |
| `bug-candidate` | Potential bug reports for verification |
| `patch` | Patches to fix discovered bugs |

## CLI Reference

### Build Output Commands

#### `submit-build-output` ✅

Submit a build artifact from the container to the shared build output filesystem.

```bash
$ libCRS submit-build-output <src_path> <dst_path>
```

| Argument | Description |
|---|---|
| `src_path` | Source file/directory path inside the container |
| `dst_path` | Destination path on the build output filesystem |

**Example** — Submit a compiled binary after target build:
```bash
$ libCRS submit-build-output /out/fuzzer /fuzzer
```

#### `download-build-output` ✅

Download a build artifact from the shared build output filesystem into the container.

```bash
$ libCRS download-build-output <src_path> <dst_path>
```

| Argument | Description |
|---|---|
| `src_path` | Source path on the build output filesystem |
| `dst_path` | Destination path inside the container |

**Example** — Retrieve a compiled binary during the run phase:
```bash
$ libCRS download-build-output /fuzzer /opt/fuzzer
```

#### `skip-build-output` ✅

Mark a build output path as intentionally skipped (creates a `.skip` sentinel file).

```bash
$ libCRS skip-build-output <dst_path>
```

| Argument | Description |
|---|---|
| `dst_path` | Path on the build output filesystem to skip |

**Example** — Skip an optional build output:
```bash
$ libCRS skip-build-output /optional-sanitizer-build
```

---

### Directory Registration Commands

These commands set up **automatic background syncing** between the container and the shared infrastructure. Registration commands fork a daemon process that watches the directory using filesystem events.

#### `register-submit-dir` ✅

Register a local directory for automatic submission to oss-crs-infra. A background daemon watches for new files and submits them in batches.

```bash
$ libCRS register-submit-dir [--log <log_path>] <type> <path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, or `bug-candidate` |
| `path` | Local directory to watch |
| `--log` | *(Optional)* Log file path for the daemon |

**How it works:**
1. A daemon process is forked into the background.
2. The daemon uses `watchdog` to monitor the directory for new files.
3. New files are deduplicated by MD5 hash and queued for submission.
4. Queued files are flushed in batches (every 10 seconds or when 100 files accumulate).
5. Files are also copied to the shared submit directory for cross-CRS access.

**Metadata support:** For each file `<name>`, you can create a `.<name>.metadata` JSON file with a `"finder"` field to attribute which component found the artifact.

**Example:**
```bash
$ libCRS register-submit-dir seed /output/seeds
$ libCRS register-submit-dir --log /var/log/pov-submit.log pov /output/povs
```

#### `register-shared-dir` ✅

Create a symlink from a local path to a shared filesystem path, enabling file sharing between containers within the same CRS.

```bash
$ libCRS register-shared-dir <local_path> <shared_fs_path>
```

| Argument | Description |
|---|---|
| `local_path` | Local directory path inside the container (must not already exist) |
| `shared_fs_path` | Path on the shared filesystem visible to all containers in the CRS |

**How it works:**
1. Creates the shared directory on the shared filesystem if it doesn't exist.
2. Creates a symlink from `local_path` → `$OSS_CRS_SHARED_DIR/<shared_fs_path>`.
3. Any container in the CRS that registers the same `shared_fs_path` will see the same files.

**Example** — Share a corpus between a fuzzer and an analyzer container:
```bash
# In the fuzzer container:
$ libCRS register-shared-dir /shared-corpus corpus

# In the analyzer container:
$ libCRS register-shared-dir /shared-corpus corpus
```

#### `register-fetch-dir` 📝 *(TODO)*

Register a directory to automatically fetch shared data from other CRSs via oss-crs-infra.

```bash
$ libCRS register-fetch-dir [--log <log_path>] <type> <path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, or `bug-candidate` |
| `path` | Local directory to receive shared data |
| `--log` | *(Optional)* Log file path for the daemon |

**Example:**
```bash
$ libCRS register-fetch-dir seed /shared-seeds
$ libCRS register-fetch-dir pov /shared-povs
$ libCRS register-fetch-dir bug-candidate /shared-bug-candidates
```

---

### Manual Data Operations

#### `submit` ✅

Manually submit a single file to oss-crs-infra.

```bash
$ libCRS submit <type> <file_path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, or `bug-candidate` |
| `file_path` | Path to the file to submit |

**Example:**
```bash
$ libCRS submit pov /tmp/crash-input
$ libCRS submit seed /tmp/interesting-input
$ libCRS submit bug-candidate /tmp/bug-report
```

#### `fetch` 📝 *(TODO)*

Fetch shared data from other CRSs to a local directory. Returns a list of downloaded file names (one per line).

```bash
$ libCRS fetch <type> <dst_dir_path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, or `bug-candidate` |
| `dst_dir_path` | Local directory to download files into |

**Example:**
```bash
$ libCRS fetch seed /tmp/shared-seeds
$ libCRS fetch pov /tmp/shared-povs
```

---

### Network Commands

#### `get-service-domain` ✅

Resolve the Docker network domain name for a service within the CRS. Returns the domain string and verifies it via DNS resolution.

```bash
$ libCRS get-service-domain <service_name>
```

| Argument | Description |
|---|---|
| `service_name` | Name of the service (as defined in `crs.yaml` modules) |

The returned domain follows the pattern `<service_name>.<crs_name>`.

**Example:**
```bash
$ libCRS get-service-domain my-analyzer
# Output: my-analyzer.my-crs
```

---

### Patching Commands 📝 *(TODO)*

#### `apply-patch-build`

Apply a diff patch to a target build and rebuild.

```bash
$ libCRS apply-patch-build <patch_diff_file> <dst_dir_path>
```

## Typical Usage in a CRS

### During Target Build Phase

```bash
#!/bin/bash
# build.sh — executed during crs-compose build-target

# Compile the target with custom instrumentation
cd /src && make CC=afl-clang-fast

# Submit the compiled binary
libCRS submit-build-output /src/target /target

# If an optional build is not needed, skip it
libCRS skip-build-output /optional-target
```

### During Run Phase

```bash
#!/bin/bash
# run.sh — executed during crs-compose run

# Retrieve build outputs
libCRS download-build-output /target /opt/target

# Set up shared directories for inter-container communication
libCRS register-shared-dir /shared-corpus corpus

# Register directories for automatic submission to infra
libCRS register-submit-dir seed /output/seeds &
libCRS register-submit-dir pov /output/povs &
libCRS register-submit-dir bug-candidate /output/bugs &

# Resolve service endpoints
ANALYZER_HOST=$(libCRS get-service-domain analyzer)

# Start the fuzzer
/opt/fuzzer --target /opt/target --output /output --seeds /shared-corpus
```

## Implementation Status

| Feature | Status | Notes |
|---|---|---|
| `submit-build-output` | ✅ Implemented | Uses `rsync` for file copying |
| `download-build-output` | ✅ Implemented | Uses `rsync` for file copying |
| `skip-build-output` | ✅ Implemented | Creates `.skip` sentinel file |
| `register-submit-dir` | ✅ Implemented | Daemon with `watchdog` + batch submission |
| `register-shared-dir` | ✅ Implemented | Symlink-based sharing |
| `submit` | ✅ Implemented | Single-file submission |
| `get-service-domain` | ✅ Implemented | DNS-verified domain resolution |
| `register-fetch-dir` | 📝 Planned | Registered in CLI, not yet implemented |
| `fetch` | 📝 Planned | Registered in CLI, not yet implemented |
| `apply-patch-build` | 📝 Planned | Not yet registered in CLI |
| `AzureCRSUtils` | 📝 Planned | Azure deployment backend for `CRSUtils` |
| InfraClient integration | 📝 Stub | `submit_batch` is a no-op stub |