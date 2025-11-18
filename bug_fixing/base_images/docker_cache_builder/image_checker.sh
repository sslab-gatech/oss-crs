#!/bin/sh
# NOTE: Using /bin/sh for maximum compatibility in minimal Docker images.

# Script to check if a specific image is loaded in the local Docker daemon.
# Arguments:
#   $1: Name of the image to check (e.g., aixcc-afc/aixcc/c/mock-c)

IMAGE_NAME="$1"
DAEMON_TIMEOUT=30
ELAPSED=0

# Check for required arguments
if [ -z "$IMAGE_NAME" ]; then
    echo "Usage: $0 <image_name>"
    exit 1
fi

# --- DinD Daemon Readiness Check ---
echo "Starting DinD daemon readiness check (Timeout: ${DAEMON_TIMEOUT}s)..."

# Loop until the local daemon is responsive (docker info returns 0) or times out
until docker info > /dev/null 2>&1 || [ "$ELAPSED" -ge "$DAEMON_TIMEOUT" ]; do
    echo "Waiting for local Docker daemon... (Elapsed: $ELAPSED)"
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

if [ "$ELAPSED" -ge "$DAEMON_TIMEOUT" ]; then
    echo "ERROR: Local Docker daemon did not become ready within the timeout."
    exit 255 # Custom exit code for daemon failure
fi

echo "Local Docker daemon is ready. Running inspection..."

# --- Image Existence Check ---
# docker inspect returns 0 if the image exists, non-zero otherwise.
# We explicitly set the exit status based on the inspect command.
docker inspect "$IMAGE_NAME"

EXIT_CODE=$?

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "SUCCESS: Image '$IMAGE_NAME' is loaded."
    exit 0
else
    # The image was not found by docker inspect
    echo "FAILURE: Image '$IMAGE_NAME' NOT loaded."
    exit 1
fi
