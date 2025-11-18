#! /bin/bash

set -e -o pipefail
set -u
# docker load -i /crs-images/crs-image.tar

python3 $OSS_FUZZ/infra/helper.py build_fuzzers $TARGET_PROJ $CP_SOURCES

