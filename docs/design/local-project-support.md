# Local Project Support

## Overview

The `project_path` parameter enables users to specify custom OSS-Fuzz compatible project directories outside the standard OSS-Fuzz repository. This supports out-of-tree projects such as AIxCC challenge programs, custom benchmarks, and proprietary codebases.

## Motivation

By default, `oss-crs` expects projects to exist in the OSS-Fuzz repository at `{oss_fuzz_path}/projects/{project_name}/`. However, several use cases require custom projects:

1. **AIxCC Challenge Projects**: Competition-specific programs not in public OSS-Fuzz
2. **Custom Benchmarks**: Private or experimental fuzzing targets
3. **Proprietary Code**: Internal applications with OSS-Fuzz compatible structure
4. **Development Projects**: New projects being prepared for OSS-Fuzz submission

## Design

### `project_path` vs `source_path`

These parameters serve different purposes and can be used together:

| Aspect | `source_path` | `project_path` |
|--------|---------------|----------------|
| **Purpose** | Override source code only | Provide entire project structure |
| **What it provides** | Application source files | project.yaml, Dockerfile, build.sh, all project files |
| **Use case** | Testing local code changes | Using custom/out-of-tree projects |
| **OSS-Fuzz needed** | Yes (for project structure) | No (project is self-contained) |
| **Implementation** | Volume mount + copy during build | Copy to oss-fuzz/projects/{project_name} |
| **Example** | Developing json-c library locally | AIxCC challenge in ~/challenges/my-cp/ |

**Example scenario using both:**
```bash
# Use custom AIxCC project with local source modifications
oss-crs build example_configs/crs-libfuzzer \
    aixcc-challenge \
    /path/to/my/source \
    --project-path /path/to/aixcc/project
```

### Implementation Strategy: Copy to OSS-Fuzz Projects

**Simple approach:** Copy the custom project directory to `oss-fuzz/projects/{project_name}/`

This strategy minimizes code changes by reusing the existing OSS-Fuzz infrastructure.

**Algorithm:**
```python
if project_path is provided:
    # Validate project_path exists and has required files
    validate_project_path(project_path)

    # Destination path (handles nested names like aixcc/c/project)
    dest = Path(oss_fuzz_dir) / "projects" / project_name

    # Error if destination exists (safety first)
    if dest.exists() and not overwrite:
        raise FileExistsError(
            f"Project already exists: {dest}. Use --overwrite to replace."
        )

    # Create parent directories for nested project names
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing if overwrite is enabled
    if dest.exists():
        shutil.rmtree(dest)

    # Copy project to OSS-Fuzz projects directory
    shutil.copytree(project_path, dest)
    logger.info(f"Copied project from {project_path} to {dest}")

# Then continue with normal OSS-Fuzz workflow
# No other changes needed - everything uses standard paths
```

**Benefits:**
- **Minimal code changes**: Only modify `crs_main.py` and `__main__.py`
- **No template changes**: Uses standard OSS-Fuzz paths
- **No render_compose changes**: Reuses existing infrastructure
- **Safety**: Requires explicit `--overwrite` flag to replace existing projects
- **Nested project support**: Handles project names with "/" (e.g., `aixcc/c/asc-nginx`)

**Validation requirements for custom project path:**
1. Directory exists and is accessible
2. Contains `project.yaml` with valid schema
3. Contains `Dockerfile` for building the project
4. Contains `build.sh` or build instructions
5. Project language matches CRS compatibility (if applicable)

### Data Flow

```
CLI (--project-path, --overwrite)
    ↓
__main__.py: Pass to build_crs()
    ↓
crs_main.build_crs(project_path=..., overwrite=...)
    ↓
If project_path provided:
    ├─ Validate project_path exists and has project.yaml
    ├─ Check if destination exists: oss-fuzz/projects/{project_name}
    ├─ Error if exists and not overwrite
    ├─ Create parent directories (for nested names)
    └─ Copy project_path to oss-fuzz/projects/{project_name}
    ↓
Continue with normal workflow:
    ├─ Build project image (uses copied project)
    └─ render_compose.render_build_compose()
        └─ Uses standard OSS-Fuzz paths (no changes needed)
```

### Integration Points

#### 1. CLI Layer (`__main__.py`)

**Build command:**
```python
build_parser.add_argument('--project-path',
    help='Path to local OSS-compatible project')
build_parser.add_argument('--overwrite', action='store_true',
    help='Overwrite existing project in oss-fuzz/projects/')

# Pass to build_crs
build_crs(..., project_path=args.project_path, overwrite=args.overwrite)
```

#### 2. Core Logic (`crs_main.py`)

**build_crs() changes:**
- Accept `project_path` and `overwrite` parameters
- If `project_path` provided:
  - Validate path exists and contains `project.yaml`
  - Check destination: `oss-fuzz/projects/{project_name}`
  - Error if destination exists and `not overwrite`
  - Create parent directories (for nested project names)
  - Copy project to destination with `shutil.copytree()`
- Continue with normal workflow (no other changes)

**run_crs() changes:**
- No changes needed (project already copied during build)

#### 3. Compose Generation (`render_compose.py`)

**No changes needed** - uses standard OSS-Fuzz paths after project is copied

#### 4. Templates

**No changes needed** - uses standard OSS-Fuzz paths after project is copied

## OSS-Fuzz Compatible Project Structure

Custom projects must follow OSS-Fuzz conventions:

```
my-custom-project/
├── project.yaml          # Required: language, sanitizers, etc.
├── Dockerfile            # Required: base image and dependencies
├── build.sh              # Required: build instructions
├── fuzzer1.cc            # Fuzzing harnesses
├── fuzzer2.cc
└── src/                  # Application source
    └── ...
```

**Minimal project.yaml:**
```yaml
homepage: "https://example.com/my-project"
language: c++
primary_contact: "security@example.com"
sanitizers:
  - address
  - undefined
```

## Backward Compatibility

The implementation maintains full backward compatibility:

- **No `project_path` specified**: Uses standard OSS-Fuzz projects (existing behavior)
- **With `project_path`**: Uses custom project directory (new behavior)
- **All existing configs**: Continue to work without modification

## Use Cases

### 1. AIxCC Challenge Project

```bash
# Build CRS for AIxCC challenge
oss-crs build \
    example_configs/crs-libfuzzer \
    aixcc-challenge \
    --project-path ~/aixcc/challenge-projects/cp-nginx/
```

### 2. Development Project

```bash
# Test new project before submitting to OSS-Fuzz
oss-crs build \
    example_configs/ensemble-c \
    my-new-library \
    --project-path ~/dev/my-library-ossfuzz/
```

### 3. Combined with Source Override

```bash
# Custom project with local source modifications
oss-crs build \
    example_configs/atlantis-c-libafl \
    benchmark-project \
    ~/src/benchmark-source/ \              # source_path (positional)
    --project-path ~/benchmarks/benchmark-proj/   # project_path (flag)
```

### 4. Private Benchmark Suite

```bash
# Run proprietary fuzzing benchmark
for project in /benchmarks/suite/*; do
    oss-crs build example_configs/ensemble-c \
        $(basename $project) \
        --project-path $project
done
```

## Validation and Error Handling

### Required Validations

1. **Path exists**: `project_path` must be a valid directory
2. **project.yaml exists**: Required for language detection
3. **Dockerfile exists**: Required for building
4. **Absolute path**: Resolve relative paths to absolute
5. **Readable**: User has read permissions

### Error Messages

```python
# Path doesn't exist
FileNotFoundError: Project path does not exist: /path/to/project

# Missing project.yaml
FileNotFoundError: project.yaml not found in /path/to/project

# Invalid project.yaml
ValueError: Invalid project.yaml: missing 'language' field

# Language mismatch (optional warning)
Warning: Project language 'java' may not be compatible with C CRS
```

## Implementation Notes

### OSS-Fuzz Image Building

When `project_path` is provided:
- Skip `python infra/helper.py build_image` step
- Project will be built directly in CRS build process
- No dependency on OSS-Fuzz base project image

### Template Context

The resolved `project_path` is passed to templates as an absolute path:
```python
context = {
    'project_path': '/absolute/path/to/project',
    'project': 'project-name',  # Still needed for naming
    ...
}
```

### Interaction with Other Features

- **Registry**: Works independently (CRS registry vs project path)
- **Source path**: Complementary (can use together)
- **OSS-Fuzz dir**: Still needed for infrastructure (unless fully custom)
- **External LiteLLM**: No interaction
- **Build dir**: Still used for artifacts

## Future Enhancements

Potential improvements (not in initial implementation):

1. **Auto-detection**: Check if project exists in OSS-Fuzz, else require `project_path`
2. **Project validation**: Comprehensive OSS-Fuzz compatibility checker
3. **Template selection**: Custom templates for non-OSS-Fuzz projects
4. **Project type**: Support non-fuzzing projects (e.g., pure testing)
5. **Multiple projects**: Array of project paths for batch operations
