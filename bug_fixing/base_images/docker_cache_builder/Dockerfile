# OSS-Patch runner image to run CRS against the provided project.
# ARG parent_image=docker:cli

FROM cruizba/ubuntu-dind:latest

RUN apt update

COPY infra/base-images/docker-cache-builder/image_checker.sh /image_checker.sh
