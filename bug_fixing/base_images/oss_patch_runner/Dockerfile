# OSS-Patch runner image to run CRS against the provided project.
ARG parent_image=cruizba/ubuntu-dind:latest
# ARG parent_image=docker:cli

FROM $parent_image

ARG target_project=default
ARG target_sanitizer=address
ARG crs_docker_path=/crs-docker
# RUN apk add bash python3

ENV CRS_DOCKER_PATH=${crs_docker_path}
ENV TARGET_PROJ=${target_project}
ENV OSS_FUZZ=/oss-fuzz
ENV CP_SOURCES=/cp-sources
ENV SANITIZER=${target_sanitizer}

COPY .build_context/oss-fuzz /oss-fuzz
COPY .build_context/cp-sources /cp-sources
# COPY .build_context/builder-image.tar /builder-images/builder-image.tar

COPY infra/base-images/oss-patch-runner/setup.sh /usr/bin/setup.sh
COPY infra/base-images/oss-patch-runner/launch_crs.sh /usr/bin/launch_crs.sh
COPY infra/base-images/oss-patch-runner/build_fuzzers.sh /usr/bin/build_fuzzers.sh

RUN chmod +x /usr/bin/setup.sh && setup.sh
    
CMD ["sh", "-c", "/usr/bin/launch_crs.sh"]
