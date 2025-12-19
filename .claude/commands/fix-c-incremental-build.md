# Fix C/C++ Incremental Build Command

Fix C/C++ project build.sh and test.sh scripts for incremental builds after docker commit.

> **IMPORTANT:** This command uses the `c-incremental-build-converter` skill. **YOU MUST invoke the skill** to get the correct patterns and guidelines before making any modifications.

## Quick Start

Given a log directory from C incremental build tests and an oss-fuzz directory, analyze errors and fix the build.sh/test.sh scripts.

## Instructions

### Step 1: Invoke the Skill (REQUIRED)

**Before analyzing logs or making any changes, invoke the `c-incremental-build-converter` skill:**

```
Use the Skill tool with skill: "c-incremental-build-converter"
```

The skill provides:
- Key architecture patterns (separate $SRC for build.sh and test.sh)
- Conditional execution patterns
- Common error fixes
- Standard templates
- Testing checklist

### Step 2: Analyze Logs

1. Read `summary.txt` in the log directory
2. Identify failed projects
3. Read individual log files for error details

### Step 3: Apply Fixes Using Skill Patterns

Apply fixes based on the skill guidelines. Key principles:

- **$SRC is separate** - build.sh uses `/built-src`, test.sh uses `/test-src`
- **DO NOT delete artifacts** - No `make clean`, `make distclean`, `rm *.o`, etc.
- **Use conditional execution** - `[ -f Makefile ] || ./configure`
- **Use `cp` not `mv`** - For idempotent operations

### Step 4: Test the Fixes

Test using the `test-inc-build` command:

```bash
uv run oss-bugfix-crs test-inc-build {project_name} ../oss-fuzz
```

**For multiple projects, use parallel testing:**

```bash
# Run tests in parallel as background tasks
uv run oss-bugfix-crs test-inc-build project-1 ../oss-fuzz &
uv run oss-bugfix-crs test-inc-build project-2 ../oss-fuzz &
uv run oss-bugfix-crs test-inc-build project-3 ../oss-fuzz &
wait
```

### Step 5: Handle Test Failures (IMPORTANT - Context Limit Prevention)

**CRITICAL: To prevent context limit errors, do NOT analyze the entire test output.**

When running tests:

1. **Run tests in background** and wait for exit code:
   ```bash
   uv run oss-bugfix-crs test-inc-build {project_name} ../oss-fuzz 2>&1 | tee /tmp/{project_name}_test.log
   echo "Exit code: $?"
   ```

2. **Check exit code FIRST** - If exit code is 0, the test passed. Move on.

3. **On failure, read ONLY the last 100 lines of the log:**
   ```bash
   tail -100 /tmp/{project_name}_test.log
   ```

4. **Focus on error patterns in the tail output:**
   - Look for `Error:`, `FAILED`, `undefined reference`, `No rule to make target`
   - Identify the specific failure point
   - Apply targeted fixes based on the error patterns in the skill

**DO NOT:**
- Read the entire log file
- Analyze all test output before checking exit code
- Include full logs in your context

**This approach prevents context limit errors by only loading relevant failure information.**

## Common Fix Patterns

### For test.sh - Use Out-of-Tree Build

If test.sh has linker errors (`undefined reference`), use out-of-tree build:

```bash
#!/bin/bash
set -ex

: "${SRC:=/src}"

PROJECT_DIR="$SRC/project"
cd "$PROJECT_DIR"

# Conditional autogen
[ -f configure ] || autoreconf -fi

# Out-of-tree build for tests
BUILD_DIR="${SRC}/project_build_test"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Configure in separate directory (use absolute path)
[ -f Makefile ] || "$PROJECT_DIR/configure"

make -j$(nproc)
make -k check || true
```

### For build.sh - Conditional Configure

```bash
#!/bin/bash
set -e

cd $SRC/project

if [ ! -f Makefile ]; then
    autoreconf -fi
    ./configure --enable-static
fi
make -j$(nproc)

# Build fuzzer...
```

## Input Required

Provide:
1. Log directory path
2. OSS-Fuzz directory path
3. Project filter (optional) - e.g., "afc-libexif-*"

$ARGUMENTS
