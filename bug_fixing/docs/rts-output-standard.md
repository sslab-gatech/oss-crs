# RTS Output Standard for test.sh Scripts

## Overview

When a test.sh script implements RTS (Regression Test Selection) at a level different from the test runner's native reporting (e.g., gtest cases within ctest executables), it should output standardized markers for accurate statistics tracking.

## Output Format

Test scripts should print these markers to stdout before running tests:

```
[RTS] Total: <number>
[RTS] Selected: <number>
[RTS] Excluded: <number>
```

### Example

```bash
# In test.sh, after RTS selection logic:
echo "[RTS] Total: 13037"
echo "[RTS] Selected: 6233"
echo "[RTS] Excluded: 6804"
```

## When to Use

Use these markers when:
- CTest runs GoogleTest binaries with `GTEST_EXCLUDES_FILE`
- Any test runner wraps another framework with finer granularity
- The test runner's summary doesn't reflect actual test case counts

## Implementation in test.sh

```bash
# Example for CTest + GoogleTest with GTEST_EXCLUDES_FILE
if [[ "$RTS_ON" == "1" ]]; then
    # Run RTS selection, get counts
    SELECTED=$(wc -l < "$SELECTION_DIR/selected.txt")
    EXCLUDED=$(wc -l < "$SELECTION_DIR/excluded.txt")
    TOTAL=$((SELECTED + EXCLUDED))

    echo "[RTS] Total: $TOTAL"
    echo "[RTS] Selected: $SELECTED"
    echo "[RTS] Excluded: $EXCLUDED"

    export GTEST_EXCLUDES_FILE="$SELECTION_DIR/excluded.txt"
fi

ctest --output-on-failure
```

## Parser Behavior

The oss_patch parser will:
1. Look for `[RTS] Total:`, `[RTS] Selected:`, `[RTS] Excluded:` lines
2. If found, use `[RTS] Selected` as the RTS test count
3. For baseline comparison, the summary generator extracts `[RTS] Total` from RTS logs
   and uses it as the baseline count (since baseline cannot trace and doesn't have this info)

This ensures accurate comparison even when baseline runs report ctest executables (52)
while RTS runs report gtest cases (6233 selected out of 13037 total).
