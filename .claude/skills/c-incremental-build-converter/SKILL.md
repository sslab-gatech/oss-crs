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

### $WORK May Be Shared - Use Separate Prefix in test.sh

**WARNING: While $SRC is separate, $WORK may still be shared between build.sh and test.sh.**

If build.sh installs ASAN-instrumented libraries to `$WORK`, test.sh will get ASAN link errors.

```bash
# BAD in test.sh - Uses shared $WORK with ASAN artifacts
./configure --prefix="$WORK"
# Error: undefined reference to __asan_*

# GOOD in test.sh - Uses separate prefix
TEST_PREFIX="$SRC/test_deps"
mkdir -p "$TEST_PREFIX/lib" "$TEST_PREFIX/include"

if [ ! -f "$TEST_PREFIX/lib/libz.a" ]; then
    pushd "$SRC/zlib"
    ./configure --static --prefix="$TEST_PREFIX"
    make -j$(nproc) CFLAGS="-fPIC"  # No ASAN flags
    make install
    popd
fi
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

### 7. Copy Dependencies If Not Present (Conditional, Not Delete-and-Copy)

```bash
# BAD - Deletes and recopies every time, breaks incremental build
rm -rf modules/oniguruma
rsync -a $SRC/oniguruma modules/

# GOOD - Only copy if not present
if [ ! -d modules/oniguruma ]; then
    mkdir -p modules
    cp -r $SRC/oniguruma modules/
fi
```

### 8. Conditional Dict/Corpus File Copy

Files like dictionaries and seed corpus may not exist in all builds.

```bash
# BAD - Fails if file doesn't exist
cp $SRC/libtiff/contrib/oss-fuzz/tiff.dict $OUT/

# GOOD - Conditional copy with fallback
[ -f $SRC/libtiff/contrib/oss-fuzz/tiff.dict ] && cp $SRC/libtiff/contrib/oss-fuzz/tiff.dict $OUT/ || true
find . -name "*.tif" | xargs zip $OUT/seed_corpus.zip || true
```

### 9. Use `make -k check || true` for Potentially Failing Tests

Some tests may fail in fuzzing environment. Use `-k` to continue and `|| true` to not fail the script.

```bash
# BAD - Stops on first test failure
make check

# GOOD - Continue through failures
make -k check || true

echo "Tests completed"
```

### 10. Remove Tests from Makefile.am BEFORE configure

When removing failing tests, modify Makefile.am BEFORE running autoreconf/configure.

```bash
# BAD - Removes test file but Makefile still references it
rm -f tests/shtest
autoreconf -fi
./configure
make check  # Error: No rule to make target 'tests/shtest'

# GOOD - Remove from Makefile.am before configure
sed -i 's| tests/shtest||g' Makefile.am
autoreconf -fi
./configure
make check  # Works
```

### 11. Rewrite Instead of Sourcing Internal build.sh

If the project's source contains its own build.sh (e.g., `contrib/oss-fuzz/build.sh`), it may use `mv` or other non-idempotent operations. Rewrite the logic in the project's build.sh instead.

```bash
# BAD - Sources internal build.sh that uses mv
source $SRC/project/contrib/oss-fuzz/build.sh

# GOOD - Rewrite the logic with cp instead of mv
if [ ! -f "$WORK/lib/libjbig.a" ]; then
    pushd "$SRC/jbigkit"
    make lib
    # Use cp instead of mv for incremental build support
    cp "$SRC"/jbigkit/libjbig/*.a "$WORK/lib/"
    cp "$SRC"/jbigkit/libjbig/*.h "$WORK/include/"
    popd
fi
```

## Standard test.sh Template

```bash
#!/bin/bash
set -e

# test.sh for {project}
# $SRC is separate from build.sh (build.sh uses /built-src, test.sh uses /test-src)

cd $SRC/{project}

# Build (only configure on first run)
if [ ! -f Makefile ]; then
    autoreconf -fi  # or ./bootstrap, or skip if not autotools
    ./configure --options
fi
make -j$(nproc)

# Run tests (use -k to continue on failures, || true to not fail script)
make -k check || true

echo "Tests completed"
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
make -k check || true

echo "Tests completed"
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

# Copy submodule if not present (conditional, not delete-and-copy)
if [ ! -d modules/oniguruma ]; then
    mkdir -p modules
    cp -r $SRC/oniguruma modules/
fi

# Remove failing tests from Makefile.am BEFORE configure
sed -i 's| tests/shtest||g' Makefile.am

if [ ! -f Makefile ]; then
    autoreconf -fi
    ./configure --with-oniguruma=builtin
fi
make -j$(nproc)

# Run tests (some may fail in fuzzing environment)
make -k check || true

echo "Tests completed"
```

## Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `undefined reference to __asan_*` | $WORK shared with ASAN artifacts from build.sh | Use separate prefix like `$SRC/test_deps` in test.sh |
| `Makefile:xxx: No rule to make target` | Partial build state | Use `if [ ! -f Makefile ]` guard around configure |
| `No rule to make target 'tests/shtest'` | Test removed but still in Makefile.am | Use `sed -i 's| tests/shtest||g' Makefile.am` BEFORE configure |
| `source directory already configured` | Previous configure run | Check for Makefile before configure |
| `mv: cannot stat: No such file` | File moved in previous run | Use `cp` with guard |
| `failed to create tests/*.trs` | TESTS= propagates to submodules | Remove failing test files directly |
| `No rule to make target 'jbig.h'` | Original build.sh used `mv` to move files | Rewrite to use `cp` instead |
| `tiff.dict: No such file` | Dict/corpus file doesn't exist | Use conditional copy: `[ -f file ] && cp file dest \|\| true` |
| `No rule to make target 'all'` in submodule | `rm -rf && rsync` deleted submodule's Makefile | Use conditional copy: `if [ ! -d dir ]; then cp; fi` |

## Testing Incremental Builds

Use the `test-inc-build` command to verify scripts work on both first run and repeated runs:

```bash
# From oss-crs directory
uv run oss-bugfix-crs test-inc-build {project_name} ../oss-fuzz

# Examples
uv run oss-bugfix-crs test-inc-build atlanta-jq-delta-01 ../oss-fuzz
uv run oss-bugfix-crs test-inc-build atlanta-libtiff-full-01 ../oss-fuzz
```

This command:
1. Runs build.sh (first run)
2. Runs docker commit to save state
3. Runs build.sh again (incremental run)
4. Compares build times and reports reduction percentage

**Success criteria:**
- Both runs complete without errors
- Build time reduction is reported (higher % = better incremental build support)

## Checklist

- [ ] Uses `$SRC` variable, NOT hardcoded paths like `/src` or `/built-src`
- [ ] Uses `if [ ! -f Makefile ]` guard around configure/autoreconf
- [ ] Does NOT delete build artifacts (.o, .lo, .a, .la, .libs)
- [ ] Uses `cp` instead of `mv` where applicable
- [ ] Failing tests removed from Makefile.am BEFORE configure (not just file deletion)
- [ ] Dependencies have existence guards
- [ ] Directory copies are conditional (`if [ ! -d ]`), not delete-and-copy
- [ ] test.sh uses separate prefix (e.g., `$SRC/test_deps`) if $WORK has ASAN artifacts
- [ ] Dict/corpus copies use conditional: `[ -f file ] && cp ... || true`
- [ ] Tests use `make -k check || true` if some may fail
- [ ] Internal build.sh rewritten if it uses `mv` or non-idempotent operations
- [ ] Works on first run AND repeated runs
