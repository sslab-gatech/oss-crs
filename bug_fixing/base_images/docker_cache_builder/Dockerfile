# OSS-Patch runner image to run CRS against the provided project.
# ARG parent_image=docker:cli

FROM cruizba/ubuntu-dind:latest

RUN apt update

COPY bug_fixing/base_images/docker_cache_builder/image_checker.sh /image_checker.sh
