# Bug-finding CRS Run Phase with Compose

Currently the bug finding CRSs can only run with a single runner.Dockerfile. We want to extend the support for CRSs to provide a compose.yaml. The single docker container is defined in OSS-CRS's own compose.yaml. So for the support of CRS compose, we need to import the services from CRS's compose into OSS-CRS' rendered version.

## Where to specify

Put `run.docker_compose:` in `config-crs.yaml` to specify where to find the docker compose in the CRS repo.

## Creating a compose in a CRS

Use `crs-libfuzzer` and `~/post/crs-libfuzzer` for local development and testing.

Create a docker-compose.yaml for crs-libfuzzer. Just do 2 services: hello-world service, but override command to sleep infinitely, and our original fuzzer service.

## Resource limits

Previously we set memory and CPU set to the single CRS container. Now, we must take those cpu and memory constraints for that one container, and:
1. assign the same CPU set to all containers from the same CRS
2. split the memory evenly across containers from the same CRS

## Running and testing end to end

```
# Build
uv run oss-bugfind-crs build --project-image-prefix aixcc-afc --oss-fuzz-dir ~/post/oss-fuzz-clean example_configs/crs-libfuzzer  aixcc/c/sanity-mock-c-delta-01 ~/post/clone/mock-c

# Run (what we are trying to implement, can re-run this after 1 initial build)
uv run oss-bugfind-crs run --skip-litellm example_configs/crs-libfuzzer aixcc/c/sanity-mock-c-delta-01 fuzz_process_input_header
```
