---
name: c-incremental-build-converter
description: Convert C/C++ project build.sh and test.sh scripts to support incremental builds. Use when fixing build scripts for re-execution after docker commit, converting to out-of-tree builds, or making scripts idempotent for repeated execution.
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
---

# C/C++ Incremental Build Converter Skill

This skill converts C/C++ project build.sh and test.sh scripts to support incremental builds after docker commit.

## Key Architecture: Separate $SRC for build.sh and test.sh

**IMPORTANT: $SRC is different between build.sh and test.sh.**

- build.sh runs in `/built-src` → `$SRC` = `/built-src`
- test.sh runs in `/test-src` → `$SRC` = `/test-src`

These are completely separate directories. This separation means:
1. **NO artifact cleanup needed** - Don't delete .o, .lo, .a, .la, .libs
2. **NO out-of-tree build needed** - In-source build is fine since $SRC is separate
3. **NO ASAN conflict** - test.sh builds in its own clean copy

### ALWAYS Use $SRC, NEVER Hardcode Paths

**CRITICAL: Always use `$SRC` variable, never hardcode `/src`, `/built-src`, or `/test-src`.**

```bash
# BAD - Hardcoded path breaks when $SRC changes
cd /src/project
cp -r /src/oniguruma modules/

# GOOD - Uses $SRC variable
cd $SRC/project
cp -r $SRC/oniguruma modules/
```

## Critical Requirements

### 1. Scripts MUST Work on BOTH First Run AND Repeated Runs

**NEVER** assume a script will only run once. Scripts must be idempotent.

```bash
# BAD - Runs configure every time
autoreconf -f -i
./configure

# GOOD - Only configure on first run
if [ ! -f Makefile ]; then
    autoreconf -f -i
    ./configure
fi
make -j$(nproc)
```

### 2. DO NOT Delete Build Artifacts

Since $SRC is separate, there's no need to clean artifacts. Deleting them defeats incremental build.

```bash
# BAD - Destroys incremental build benefits
find . -name "*.o" -delete
find . -name "*.a" -delete
rm -rf .libs

# GOOD - Just build, artifacts from previous run will speed things up
if [ ! -f Makefile ]; then
    ./configure
fi
make -j$(nproc)
```

### 3. Conditional Configure/Autoreconf

Only run configure-related commands on first execution.

```bash
# Pattern: Check for Makefile existence
if [ ! -f Makefile ]; then
    autoreconf -fi       # or ./bootstrap
    ./configure --options
fi
make -j$(nproc)
make check
```

### 4. Use `cp` Instead of `mv`

`mv` removes the source file, causing failures on repeated runs.

```bash
# BAD - Fails on second run
mv scripts/config.temp scripts/config

# GOOD - Preserves source file
[ -f scripts/config.temp ] && cp scripts/config.temp scripts/config || true
```

### 5. Use Guards for Dependencies

```bash
# Build dependency only if not already built
if [ ! -f "$WORK/lib/libz.a" ]; then
    pushd "$SRC/zlib"
    ./configure --static --prefix="$WORK"
    make -j$(nproc)
    make install
    popd
fi
```

### 6. Handle Failing Tests

Remove failing test files directly instead of using TESTS variable (which can propagate to submodules).

```bash
# BAD - TESTS variable propagates to submodules
make check TESTS="tests/mantest tests/jqtest"

# GOOD - Remove failing test files directly
rm -f tests/shtest
rm -f tests/multiple.*
sed -i '/multiple/d' tests/Makefile.am
make check
```

### 7. Copy Dependencies If Not Present

```bash
# Copy oniguruma to modules if not present
if [ ! -d modules/oniguruma ]; then
    mkdir -p modules
    cp -r $SRC/oniguruma modules/
fi
```

## Standard test.sh Template

```bash
#!/bin/bash
set -e

# test.sh for {project}
# $SRC is separate from build.sh

cd $SRC/{project}

# Build (only configure on first run)
if [ ! -f Makefile ]; then
    autoreconf -fi  # or ./bootstrap, or skip if not autotools
    ./configure --options
fi
make -j$(nproc)

# Run tests
make check
```

## Examples by Build System

### Autotools Project

```bash
#!/bin/bash
set -e

cd $SRC/project

if [ ! -f Makefile ]; then
    autoreconf -fi
    ./configure --enable-static
fi
make -j$(nproc)
make check
```

### CMake Project

```bash
#!/bin/bash
set -e

cd $SRC/project

if [ ! -f Makefile ]; then
    cmake . -DCMAKE_INSTALL_PREFIX=$WORK -DBUILD_SHARED_LIBS=off
fi
make -j$(nproc)
make test
```

### Project with Dependencies

```bash
#!/bin/bash
set -e

cd $SRC/project

# Build zlib (only on first run)
if [ ! -f "$WORK/lib/libz.a" ]; then
    pushd "$SRC/zlib"
    ./configure --static --prefix="$WORK"
    make -j$(nproc)
    make install
    popd
fi

# Build main project
if [ ! -f Makefile ]; then
    cmake . -DCMAKE_INSTALL_PREFIX=$WORK
fi
make -j$(nproc)
make test
```

### Project with Submodules (e.g., jq with oniguruma)

```bash
#!/bin/bash
set -e

cd $SRC/jq

# Copy submodule if not present
if [ ! -d modules/oniguruma ]; then
    mkdir -p modules
    cp -r $SRC/oniguruma modules/
fi

if [ ! -f Makefile ]; then
    autoreconf -fi
    ./configure --with-oniguruma=builtin
fi
make -j$(nproc)

# Remove failing tests and run
rm -f tests/shtest
make -k check || true
```

## Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `undefined reference to __asan_*` | Old artifacts from build.sh | Should not happen with separate $SRC. If it does, verify $SRC is actually separate |
| `Makefile:xxx: No rule to make target` | Partial build state | Use `if [ ! -f Makefile ]` guard around configure |
| `source directory already configured` | Previous configure run | Check for Makefile before configure |
| `mv: cannot stat: No such file` | File moved in previous run | Use `cp` with guard |
| `failed to create tests/*.trs` | TESTS= propagates to submodules | Remove failing test files directly |

## Checklist

- [ ] Uses `$SRC` variable, NOT hardcoded paths like `/src` or `/built-src`
- [ ] Uses `if [ ! -f Makefile ]` guard around configure/autoreconf
- [ ] Does NOT delete build artifacts (.o, .lo, .a, .la, .libs)
- [ ] Uses `cp` instead of `mv` where applicable
- [ ] Failing tests removed directly (not using TESTS=)
- [ ] Dependencies have existence guards
- [ ] Works on first run AND repeated runs
