# OSS-CRS

OSS-CRS aims to standardize the running interface for bug-finding Cyber Reasoning Systems that target OSS-Fuzz projects. 
The primary deployment target is running one or more CRSs locally with simple dependencies (docker).

## Workflow

OSS-CRS supports two phases for the CRS: building and running.
We want to separate the building phase so that performance can be evaluated solely on the running phase without CRSs needing to optimize or compromise on the building overhead.
Furthermore for bug-finding CRSs, we believe the building phase is commonly customized for instrumentation or other artifact generation purposes.

```sh
# Operator uses configs to build a particular CRS
uv run oss-crs build example_configs/ensemble-c json-c

# Operator uses configs to run a particular CRS on a harness
uv run oss-crs run example_configs/ensemble-c json-c json_array_fuzzer
```

Artifacts from the build phase are shared to the run phase thought the `/out` directory inside the containers.
On the host's filesystem, the directory structure looks like the following:

```
build
└── out
    └── ensemble-c
        └── json-c
            ├── json_array_fuzzer
            ├── json_object_fuzzer
            └── ...
```

## Required registry files

Registering a CRS to OSS-CRS is done through a PR to the `crs_registry` directory.
There are two files that are required, `pkg.yaml` which specifies the CRS repository 
and `config-crs.yaml` which specifies CRS dependencies,
such as LLM models, features, or resource requirements.

### pkg.yaml

For the integration PR, use `source.url` and `source.ref`.
For local debugging, `source.local_path` can specify an absolute path to the CRS directory
so that CRS developers do not have to update their remote repository for minute changes.

### config-crs.yaml

Specify constraints and dependencies for the CRS in this file.
The specified LLM models will be included in the LiteLLM provisioned key during deployment.
`ncpu` and `ram` may specified for minimum resource requirements for the CRS.
If a multi-container deployment is required for the CRS, specify `dind` in `dependencies.

## CRS entrypoint

We expect the CRS to expose two entrypoints: `builder.Dockerfile` and `runner.Dockerfile`.
These files should be at the root level of the CRS repository that's referenced in `pkg.yaml`.

### builder.Dockerfile

This container will be run without overriding the default command, 
so we expect CRS developers to specify their own `CMD`.

`parent_image` is provided as a docker image build argument,
and we expect CRS developers to base their image from the parent image.
The parent image is the tag for the oss-fuzz project image,
such as `gcr.io/oss-fuzz/json-c`.
In this workflow, the builder container has access to the `compile` command
and the project's original build environment.

The builder container will have the following defined in the docker compose:
```
build:
  context: <path-to-build>/crs/<hash>/crs-libfuzzer
  dockerfile: builder.Dockerfile
  additional_contexts:
    project: <path-to-oss-fuzz>/projects/json-c
  args:
    - CRS_TARGET=json-c
    - PROJECT_PATH=<path-to-oss-fuzz>/projects/json-c
    - parent_image=gcr.io/oss-fuzz/json-c
volumes:
  - <path-to-build>/out/crs-libfuzzer/json-c:/out
  - <path-to-build>/work/crs-libfuzzer/json-c:/work
  - <hash>_keys_data_crs-libfuzzer:/keys:ro
environment:
  - LITELLM_URL=http://crs-litellm-<hash>-litellm-1:4000
  - FUZZING_ENGINE=libfuzzer
  - SANITIZER=address
  - ARCHITECTURE=x86_64
  - PROJECT_NAME=json-c
  - FUZZING_LANGUAGE=c++
  - HELPER=True
networks:
  - <hash>_crs_network
```

If the builder container needs the files from `oss-fuzz/projects/<project/`,
they can be copied into the image in the Dockerfile by using the `PROJECT_PATH` build argument.

Source code can be found in the default parent image `WORKDIR` as per oss-fuzz convention.
If local source code is provided (overriding the code fetched in project image's Dockerfile),
OSS-CRS will mount the local source code to `/local-source-mount` and deal with snapshotting
the container with the local source code overwriting those from the original image.
Thus, CRS developers can assume that source code will always be at `WORKDIR`.

### runner.Dockerfile

This container will be run with the fuzzing harness and fuzzer arguments overriding the command.
We expect CRS developers to specify their own `ENTRYPOINT` 
which parses the fuzzing harness and arguments.
As a minimal example, running oss-fuzz's default fuzzer would be `ENTRYPOINT ["run_fuzzer"]`.

The runner container will have the following defined in the docker compose:
```
crs-libfuzzer_runner:
  image: crs-libfuzzer_runner
  privileged: true
  build:
    context: <path-to-build>/crs/<hash>/crs-libfuzzer
    dockerfile: runner.Dockerfile
    args:
      - PROJECT_PATH=<path-to-build>/crs/oss-fuzz/projects/json-c
  volumes:
    - <path-to-build>/out/crs-libfuzzer/json-c:/out
    - <hash>_keys_data_crs-libfuzzer:/keys:ro
  environment:
    - LITELLM_URL=http://crs-litellm-<hash>-litellm-1:4000
    - FUZZING_ENGINE=libfuzzer
    - SANITIZER=address
    - RUN_FUZZER_MODE=interactive
    - HELPER=True
    - CPUSET_CPUS=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
    - MEMORY_LIMIT=64G
    - CRS_TARGET=json-c
    - CRS_NAME=crs-libfuzzer
  networks:
    - 1b81b6ba52665603_crs_network
  cpuset: 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
  deploy:
    resources:
      limits:
        memory: 64G
  command: ["json_array_fuzzer"]
```

Resource constraints are defined in the docker compose file,
and passed as environment to the CRS runner container 
so that the CRS may apply own resource handling logic.

### LiteLLM key

Each CRS will be deployed with a LiteLLM key, 
with models specified from `config-crs.yaml`
and budget calculated from `config-resource.yaml` in the operator-provided configs dir.
The provisioned key will be stored in a shared volume at `/keys/api_key`.

The CRS developers should migrate their LLM requests to using the LiteLLM proxy.

### Multi-Container (DinD)

NOTE: this approach was chosen so that OSS-CRS can still control container resources easily,
but we have yet to port a real CRS that requires multiple containers.
As such, this section may be under further development or redesigns if substantial
obstacles are encountered with porting such a CRS.

The current approach to supporting multi-container CRSs is by letting the CRS use DinD
and provide supplementary resources for the CRS container.

The proposed workflow is for the CRS to build its additional docker images at the build phase, 
and export the images as tarballs into `/out` for them to be loaded at the run phase.

In order to access the project image for building the harnesses, 
it is provided as `/project-image.tar` inside the build container.

The `mock-dind` CRS is provided as an example of the docker image exporting and loading workflow.

## Operator configuration files

The operator needs to provide configuration files which specify which CRSs run, 
what machine the CRSs runs on, compute contraints, and LLM budgets.
In order to help the operator get started, we provide sample configurations in the
`example_configs/` directory.

CRS developers may optionally add sample configurations for their CRS.
For running OSS-CRS for debugging purposes, 
developers should at least modify one `config-resource.yaml` to include their CRS.

## Output format

For supporting the benchmarks RFC, the `/out` directory should be organized as follows:
```
/out/                           # CRS output directory (container)
├── povs/                       # POVs discovered (required for bug finding CRS)
│   ├── pov_001                 # Binary blob (test input that triggers vulnerability)
│   ├── pov_002                 # Binary blob
│   └── pov_003                 # Binary blob
├── corpus/                     # Fuzzing corpus (optional)
│   ├── input-001               # Test input
│   ├── input-002
│   └── input-003
└── crs-data/                   # CRS-specific outputs (optional)
    ├── analysis-report.txt     # Any additional data CRS wants to record
    ├── intermediate-results.json
    └── debug-trace.log
```
