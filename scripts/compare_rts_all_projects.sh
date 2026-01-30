#!/bin/bash

# Compare RTS performance for all C and JVM projects
# Uses --pull-snapshot to pull pre-built snapshots from remote registry
# Uses --compare-rts to compare inc-build only vs inc-build + RTS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSS_CRS_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
JOBS=1
PROJECT_LIST=""
BENCHMARKS_DIR=""
LANGUAGE="all"  # all, c, or jvm
SKIP_BASELINE=false

usage() {
    echo "Usage: $0 <OSS_FUZZ_PATH> -b <BENCHMARKS_DIR> [OPTIONS]"
    echo ""
    echo "Compare RTS performance across all projects using pre-built snapshots."
    echo "Runs each project twice: inc-build only (RTS OFF) and inc-build + RTS."
    echo ""
    echo "Arguments:"
    echo "  OSS_FUZZ_PATH      Path to OSS-Fuzz directory"
    echo ""
    echo "Required Options:"
    echo "  -b, --benchmarks-dir DIR  Benchmarks directory with bundled tarballs (pkgs/)"
    echo ""
    echo "Options:"
    echo "  -j, --jobs N         Number of parallel jobs (default: 1)"
    echo "  -l, --list FILE      File containing project names (one per line)"
    echo "  --lang LANG          Language filter: all, c, or jvm (default: all)"
    echo "  --skip-baseline      Skip baseline measurement (faster, less comparison data)"
    echo "  -h, --help           Show this help message"
    echo ""
    echo "Example:"
    echo "  $0 ../oss-fuzz -b ../../benchmarks"
    echo "  $0 ../oss-fuzz -b ../../benchmarks --lang c -j 4"
    echo "  $0 ../oss-fuzz -b ../../benchmarks --lang jvm --skip-baseline"
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
        -b|--benchmarks-dir)
            BENCHMARKS_DIR="$2"
            shift 2
            ;;
        -j|--jobs)
            JOBS="$2"
            shift 2
            ;;
        -l|--list)
            PROJECT_LIST="$2"
            shift 2
            ;;
        --lang)
            LANGUAGE="$2"
            shift 2
            ;;
        --skip-baseline)
            SKIP_BASELINE=true
            shift
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

if [ -z "$BENCHMARKS_DIR" ]; then
    echo "Error: --benchmarks-dir is required"
    usage
fi

if [ ! -d "$BENCHMARKS_DIR" ]; then
    echo "Error: Benchmarks directory not found: $BENCHMARKS_DIR"
    exit 1
fi

if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [ "$JOBS" -lt 1 ]; then
    echo "Error: jobs must be a positive integer"
    exit 1
fi

if [[ ! "$LANGUAGE" =~ ^(all|c|jvm)$ ]]; then
    echo "Error: --lang must be 'all', 'c', or 'jvm'"
    exit 1
fi

C_PROJECTS_DIR="$OSS_FUZZ_PATH/projects/aixcc/c"
JVM_PROJECTS_DIR="$OSS_FUZZ_PATH/projects/aixcc/jvm"

LOG_DIR="$OSS_CRS_DIR/logs/compare_rts_$(date +%Y%m%d_%H%M%S)"
RESULT_DIR="$LOG_DIR/.results"
mkdir -p "$LOG_DIR" "$RESULT_DIR"

echo "=========================================="
echo "RTS Comparison Test Runner"
echo "(--pull-snapshot --compare-rts)"
echo "=========================================="
echo "OSS-Fuzz path:    $OSS_FUZZ_PATH"
echo "Benchmarks dir:   $BENCHMARKS_DIR"
echo "Language filter:  $LANGUAGE"
echo "Log directory:    $LOG_DIR"
echo "Parallel jobs:    $JOBS"
echo "Skip baseline:    $SKIP_BASELINE"

# Build list of all projects
declare -a all_projects

if [ -n "$PROJECT_LIST" ]; then
    if [ ! -f "$PROJECT_LIST" ]; then
        echo "Error: Project list file not found: $PROJECT_LIST"
        exit 1
    fi
    # Read projects from file (skip empty lines and comments)
    mapfile -t all_projects < <(grep -v '^#' "$PROJECT_LIST" | grep -v '^[[:space:]]*$')
    echo "Project list:     $PROJECT_LIST"
else
    # Auto-discover projects based on language filter
    if [ "$LANGUAGE" = "all" ] || [ "$LANGUAGE" = "c" ]; then
        if [ -d "$C_PROJECTS_DIR" ]; then
            for p in $(ls -d "$C_PROJECTS_DIR"/*/ 2>/dev/null | xargs -n1 basename); do
                all_projects+=("aixcc/c/$p")
            done
        fi
    fi

    if [ "$LANGUAGE" = "all" ] || [ "$LANGUAGE" = "jvm" ]; then
        if [ -d "$JVM_PROJECTS_DIR" ]; then
            for p in $(ls -d "$JVM_PROJECTS_DIR"/*/ 2>/dev/null | xargs -n1 basename); do
                all_projects+=("aixcc/jvm/$p")
            done
        fi
    fi
fi

echo "=========================================="
echo "Found ${#all_projects[@]} projects"
echo ""

if [ ${#all_projects[@]} -eq 0 ]; then
    echo "No projects found!"
    exit 1
fi

# List projects
for p in "${all_projects[@]}"; do
    echo "  - $p"
done
echo ""

total=${#all_projects[@]}

cd "$OSS_CRS_DIR"

# Build extra options
EXTRA_OPTS=""
if [ "$SKIP_BASELINE" = true ]; then
    EXTRA_OPTS="$EXTRA_OPTS --skip-baseline"
fi

# Function to run a single test (used for parallel execution)
run_single_test() {
    local project="$1"
    local log_dir="$2"
    local oss_fuzz_path="$3"
    local result_dir="$4"
    local benchmarks_dir="$5"
    local extra_opts="$6"

    # Replace / with _ for filename
    local safe_name="${project//\//_}"
    local log_file="$log_dir/${safe_name}.log"
    local result_file="$result_dir/${safe_name}.result"

    # Run with --pull-snapshot --compare-rts
    if uv run oss-bugfix-crs test-inc-build "$project" "$oss_fuzz_path" \
        --benchmarks-dir "$benchmarks_dir" \
        --pull-snapshot \
        --compare-rts \
        $extra_opts > "$log_file" 2>&1; then
        echo "PASSED" > "$result_file"
    else
        echo "FAILED" > "$result_file"
    fi
}
export -f run_single_test

if [ "$JOBS" -eq 1 ]; then
    # Sequential execution with progress output
    current=0
    for project in "${all_projects[@]}"; do
        current=$((current + 1))
        echo "[$current/$total] Testing: $project"

        safe_name="${project//\//_}"
        log_file="$LOG_DIR/${safe_name}.log"
        result_file="$RESULT_DIR/${safe_name}.result"

        # Run with --pull-snapshot --compare-rts
        if uv run oss-bugfix-crs test-inc-build "$project" "$OSS_FUZZ_PATH" \
            --benchmarks-dir "$BENCHMARKS_DIR" \
            --pull-snapshot \
            --compare-rts \
            $EXTRA_OPTS > "$log_file" 2>&1; then
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
    printf '%s\n' "${all_projects[@]}" | xargs -P "$JOBS" -I {} bash -c \
        'run_single_test "$@"' _ {} "$LOG_DIR" "$OSS_FUZZ_PATH" "$RESULT_DIR" "$BENCHMARKS_DIR" "$EXTRA_OPTS"

    # Print results after parallel execution
    for project in "${all_projects[@]}"; do
        safe_name="${project//\//_}"
        result_file="$RESULT_DIR/${safe_name}.result"
        if [ -f "$result_file" ] && [ "$(cat "$result_file")" = "PASSED" ]; then
            echo "  ✓ $project: PASSED"
        else
            echo "  ✗ $project: FAILED (see $LOG_DIR/${safe_name}.log)"
        fi
    done
fi

# Collect results
passed=0
failed=0
failed_projects=()

for project in "${all_projects[@]}"; do
    safe_name="${project//\//_}"
    result_file="$RESULT_DIR/${safe_name}.result"
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
RTS Comparison Test Summary
===========================
Date: $(date)
Options: --pull-snapshot --compare-rts
Language: $LANGUAGE
Skip baseline: $SKIP_BASELINE
Parallel Jobs: $JOBS

Total: $total
Passed: $passed
Failed: $failed

Failed projects:
$(printf '%s\n' "${failed_projects[@]}")
EOF

echo ""
echo "Logs saved to: $LOG_DIR"
echo ""
echo "To parse results, run:"
echo "  python scripts/parse_inc_build_logs.py $LOG_DIR"

# Cleanup result files
rm -rf "$RESULT_DIR"

# Exit with error if any failed
[ $failed -eq 0 ]
