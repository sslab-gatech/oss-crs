#!/usr/bin/env bash
set -e

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <project-name-or-path> <harness-name> [fuzzer-args...]"
    echo "Example: $0 libxml2-delta-01 lint"
    echo "Example: $0 /home/yufu/aixcc_shared/CRSBench/benchmarks/atlanta-binutils-delta-01 my_harness"
    echo "Example: $0 my-project my_fuzzer -max_len=100"
    exit 1
fi

PROJECT_INPUT="$1"
HARNESS_NAME="$2"
shift 2
FUZZER_ARGS="$@"

# Infer project name from basename if it's a path
if [[ -d "$PROJECT_INPUT" ]]; then
    PROJECT_NAME=$(basename "$PROJECT_INPUT")
    echo "Detected path, using project name: $PROJECT_NAME"
else
    PROJECT_NAME="$PROJECT_INPUT"
fi

echo "Running: $PROJECT_NAME"
echo "Harness: $HARNESS_NAME"
[[ -n "$FUZZER_ARGS" ]] && echo "Fuzzer args: $FUZZER_ARGS"

# Run command
uv run oss-crs run example_configs/crs-libfuzzer "$PROJECT_NAME" "$HARNESS_NAME" $FUZZER_ARGS
