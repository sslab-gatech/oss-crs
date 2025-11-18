#! /bin/bash

set -e -o pipefail
set -u
# docker load -i /crs-images/crs-image.tar

# Copy the provided inputs for `run_crs` to mount them for the CRS container
cp -r /work /tmp/work
mkdir /tmp/out

# Run the CRS container
docker run --rm --privileged --net=host -v $CRS_DOCKER_PATH:/var/lib/docker -v /oss-fuzz:/oss-fuzz -v /cp-sources:/cp-sources -v /tmp/work:/work -v /tmp/out:/out -e LITELLM_API_BASE=$LITELLM_API_BASE -e LITELLM_API_KEY=$LITELLM_API_KEY -e TARGET_PROJ=$TARGET_PROJ -e OSS_FUZZ=/oss-fuzz gcr.io/oss-patch/$CRS_NAME

cp -r /tmp/out/* /out
