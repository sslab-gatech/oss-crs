#! /bin/bash

set -e -o pipefail
set -u

SOURCE_DIR="/cp-sources"

# docker load -i /builder-images/*

# Use sed to replace 'build_project_image=True' with 'build_project_image=False'
sed -i.bak 's/build_project_image=True/build_project_image=False/g' "$OSS_FUZZ/infra/helper.py"

# # Pre-build the artifact for the fuzzers
# python3 $OSS_FUZZ/infra/helper.py build_fuzzers $TARGET_PROJ $SOURCE_DIR --sanitizer $SANITIZER
