#!/bin/bash

# Test all C projects with incremental build (no RTS)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSS_CRS_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
JOBS=1

usage() {
    echo "Usage: $0 <OSS_FUZZ_PATH> [-j|--jobs N]"
    echo ""
    echo "Arguments:"
    echo "  OSS_FUZZ_PATH    Path to OSS-Fuzz directory"
    echo ""
    echo "Options:"
    echo "  -j, --jobs N     Number of parallel jobs (default: 1)"
    echo "  -h, --help       Show this help message"
    exit 1
}

# Parse arguments
if [ -z "$1" ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    usage
fi

OSS_FUZZ_PATH="$1"
shift

while [[ $# -gt 0 ]]; do
    case $1 in
        -j|--jobs)
            JOBS="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [ "$JOBS" -lt 1 ]; then
    echo "Error: jobs must be a positive integer"
    exit 1
fi

C_PROJECTS_DIR="$OSS_FUZZ_PATH/projects/aixcc/c"

LOG_DIR="$OSS_CRS_DIR/logs/c_inc_build_test_$(date +%Y%m%d_%H%M%S)"
RESULT_DIR="$LOG_DIR/.results"
mkdir -p "$LOG_DIR" "$RESULT_DIR"

echo "=========================================="
echo "C Incremental Build Test Runner"
echo "=========================================="
echo "OSS-Fuzz path: $OSS_FUZZ_PATH"
echo "C projects:   $C_PROJECTS_DIR"
echo "Log directory: $LOG_DIR"
echo "Parallel jobs: $JOBS"
echo "=========================================="

# Get list of projects
projects=($(ls -d "$C_PROJECTS_DIR"/*/ 2>/dev/null | xargs -n1 basename))

echo "Found ${#projects[@]} projects"
echo ""

total=${#projects[@]}

cd "$OSS_CRS_DIR"

# Function to run a single test (used for parallel execution)
run_single_test() {
    local project="$1"
    local log_dir="$2"
    local oss_fuzz_path="$3"
    local result_dir="$4"

    local log_file="$log_dir/${project}.log"
    local result_file="$result_dir/${project}.result"

    if uv run oss-bugfix-crs test-inc-build "aixcc/c/$project" "$oss_fuzz_path" > "$log_file" 2>&1; then
        echo "PASSED" > "$result_file"
    else
        echo "FAILED" > "$result_file"
    fi
}
export -f run_single_test

if [ "$JOBS" -eq 1 ]; then
    # Sequential execution with progress output
    current=0
    for project in "${projects[@]}"; do
        current=$((current + 1))
        echo "[$current/$total] Testing: aixcc/c/$project"

        log_file="$LOG_DIR/${project}.log"
        result_file="$RESULT_DIR/${project}.result"

        if uv run oss-bugfix-crs test-inc-build "aixcc/c/$project" "$OSS_FUZZ_PATH" > "$log_file" 2>&1; then
            echo "  ✓ PASSED"
            echo "PASSED" > "$result_file"
        else
            echo "  ✗ FAILED (see $log_file)"
            echo "FAILED" > "$result_file"
        fi
    done
else
    # Parallel execution using xargs
    echo "Running tests in parallel..."
    printf '%s\n' "${projects[@]}" | xargs -P "$JOBS" -I {} bash -c \
        'run_single_test "$@"' _ {} "$LOG_DIR" "$OSS_FUZZ_PATH" "$RESULT_DIR"

    # Print results after parallel execution
    for project in "${projects[@]}"; do
        result_file="$RESULT_DIR/${project}.result"
        if [ -f "$result_file" ] && [ "$(cat "$result_file")" = "PASSED" ]; then
            echo "  ✓ aixcc/c/$project: PASSED"
        else
            echo "  ✗ aixcc/c/$project: FAILED (see $LOG_DIR/${project}.log)"
        fi
    done
fi

# Collect results
passed=0
failed=0
failed_projects=()

for project in "${projects[@]}"; do
    result_file="$RESULT_DIR/${project}.result"
    if [ -f "$result_file" ] && [ "$(cat "$result_file")" = "PASSED" ]; then
        passed=$((passed + 1))
    else
        failed=$((failed + 1))
        failed_projects+=("$project")
    fi
done

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="
echo "Total:  $total"
echo "Passed: $passed"
echo "Failed: $failed"

if [ ${#failed_projects[@]} -gt 0 ]; then
    echo ""
    echo "Failed projects:"
    for p in "${failed_projects[@]}"; do
        echo "  - $p"
    done
fi

# Write summary to file
cat > "$LOG_DIR/summary.txt" << EOF
C Incremental Build Test Summary
================================
Date: $(date)
Parallel Jobs: $JOBS
Total: $total
Passed: $passed
Failed: $failed

Failed projects:
$(printf '%s\n' "${failed_projects[@]}")
EOF

echo ""
echo "Logs saved to: $LOG_DIR"

# Cleanup result files
rm -rf "$RESULT_DIR"

# Exit with error if any failed
[ $failed -eq 0 ]
