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

### Step 4: Test the Fixes Using Subagent

**CRITICAL: Use the `test-inc-build` subagent via Task tool instead of running uv run directly.**

This approach prevents context limit errors by having the subagent analyze the logs and return only essential information.

**Single project test:**
```
Task tool:
  subagent_type: "test-inc-build"
  description: "Test incremental build"
  prompt: "Run incremental build test for {project_name} with oss-fuzz path ../oss-fuzz"
```

**For multiple projects, launch parallel subagents in a single message:**
```
Task tool (run_in_background: true):
  subagent_type: "test-inc-build"
  prompt: "Run incremental build test for project-1 with oss-fuzz path ../oss-fuzz"

Task tool (run_in_background: true):
  subagent_type: "test-inc-build"
  prompt: "Run incremental build test for project-2 with oss-fuzz path ../oss-fuzz"

Task tool (run_in_background: true):
  subagent_type: "test-inc-build"
  prompt: "Run incremental build test for project-3 with oss-fuzz path ../oss-fuzz"

Then use TaskOutput to retrieve results from each agent.
```

### Step 5: Handle Subagent Results

The `test-inc-build` subagent returns a concise summary:

**On Success:**
- Project name
- Build time reduction percentage
- Confirmation that incremental build works

**On Failure:**
- Project name
- Error type (categorized)
- Exact error message (max 10 lines)
- Suggested fix

Based on the subagent's failure report:
1. Apply the suggested fix
2. Re-run the test via subagent
3. Repeat until success

**This approach prevents context limit errors by:**
- Having subagent analyze full logs internally
- Returning only essential error information
- Keeping each test result under 30 lines

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
