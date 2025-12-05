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

COPY bug_fixing/base_images/oss_patch_runner/setup.sh /usr/bin/setup.sh
COPY bug_fixing/base_images/oss_patch_runner/launch_crs.sh /usr/bin/launch_crs.sh
COPY bug_fixing/base_images/oss_patch_runner/build_fuzzers.sh /usr/bin/build_fuzzers.sh
COPY bug_fixing/base_images/oss_patch_runner/replay_build.sh /opt/replay_build.sh
COPY bug_fixing/base_images/oss_patch_runner/make_build_replayable.py /opt/make_build_replayable.py

RUN chmod +x /usr/bin/setup.sh && setup.sh
    
CMD ["sh", "-c", "/usr/bin/launch_crs.sh"]
