# OSS-CRS

OSS-CRS aims to standardize the running interface for bug-finding Cyber Reasoning Systems that target OSS-Fuzz projects. 
The primary deployment target is running one or more CRSs locally with simple dependencies (docker).

## Workflow

OSS-CRS supports two phases for the CRS: building and running.
We want to separate the building phase so that performance can be evaluated solely on the running phase without CRSs needing to optimize or compromise on the building overhead.
Furthermore for bug-finding CRSs, we believe the building phase is commonly customized for instrumentation or other artifact generation purposes.

```sh
# Operator uses configs to build a particular CRS
uv run oss-bugfind-crs build example_configs/ensemble-c json-c

# Operator uses configs to run a particular CRS on a harness
uv run oss-bugfind-crs run example_configs/ensemble-c json-c json_array_fuzzer
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
```
name: crs-libfuzzer
type: bug-finding
source:
  url: https://github.com/Team-Atlanta/crs-libfuzzer
  ref: main
```

For local debugging, `source.local_path` can specify an absolute path to the CRS directory
so that CRS developers do not have to update their remote repository for minute changes.
```
name: crs-libfuzzer
type: bug-finding
source:
  local_path: ~/crs-libfuzzer
```

### config-crs.yaml

Specify constraints and dependencies for the CRS in this file.
The specified LLM models will be included in the LiteLLM provisioned key during deployment.
`ncpu` and `ram` may specified for minimum resource requirements for the CRS.
```
atlantis-java-main:
  models:
    - gpt-5
    - o4-mini
    - o3
    - gpt-4.1
  ncpu: 1-all
```

If a multi-container deployment is required for the CRS, specify `dind` in `dependencies.
```
mock-dind:
  dependencies:
    - dind
```

The CRS can also restrict the modes it deploys in (i.e. full or delta).
For example, the bullseye directed fuzzer should only be deployed in delta mode when a diff is provided.
By default if `modes` is not specified, the CRS may be deployed in both full and delta modes.
```
atlantis-c-bullseye:
  models:
    - o3
  modes:
    - delta
```

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

### Delta mode

In delta mode, when OSS-CRS is provided the `--diff` option, 
the diff file will be mounted inside the container as `/ref.diff`.
The CRS may assume the diff has already been applied to the source code.

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


# OSS-PATCH

OSS-PATCH aims to standardize the running interface for patching Cyber Reasoning Systems (CRSs) that target OSS-Fuzz projects. 
This documentation describes how to integrate one's CRS into OSS-Patch based on the current status.

So far, we have integrated three patching CRSs from ATLANTIS (Team Atlanta), buttercup (Trail-of-Bits), and PatchAgent (Northwestern Univ).

## Workflow

Similar to OSS-CRS, OSS-PATCH supports two phases for the CRS: building (`build`) and running (`run`).
We separate the building phase so that CRS performance can be evaluated solely on the running phase.

* [NOTE]: Since we are on active development of the interfaces for OSS-PATCH. Cli argument details are subject to change.

This is the example of running 42-patch-agent (https://github.com/sslab-gatech/42-PatchAgent) under OSS-PATCH.

```sh
# Type `-h` to see the usage
uv run oss-bugfix-crs build -h

# build a particular CRS (for example, target project is `sqlite3` project)
uv run oss-bugfix-crs build 42-patch-agent sqlite3 --oss-fuzz /path/to/oss-fuzz

# Type `-h` to see the usage
uv run oss-bugfix-crs run -h

# Run a particular CRS on a harness with the provided raw PoVs (i.e., `/path/to/povs`)
uv run oss-bugfix-crs run 42-patch-agent sqlite3 --povs /path/to/povs --harness fuzz_process_input_header --litellm-base https://dummy.org --litellm-key sk-fake-key --out ./out
```

`/path/to/povs` contains the raw blob files (e.g., `pov_1.bin`, `pov_2.bin`, etc).
We recommend using the OSS-Fuzz and benchmarks before the final competition (i.e., curl, sqlite, etc in round 3).


## How to register patching CRS to OSS-PATCH?

To run a CRS under OSS-PATCH, each developer should first register the target CRS to OSS-PATCH, which is done through a PR to the `crs_registry` directory located in the ROOT of the repository.
There are two files that are required, `pkg.yaml` which specifies the CRS repository and `config-crs.yaml` which specifies CRS dependencies, such as LLM models, features, or resource requirements.

### pkg.yaml

For the integration PR, use `source.url` and `source.ref`.
`source.url` represents the remote repository URL of the CRS and `source.ref` represents the specific branch used to integration.

```
name: atlantis-multi-retrieval
type: bug-fixing
source:
  url: https://github.com/Team-Atlanta/crete
  ref: oss-patch
```

For example, the above `pkg.yaml` indicates that the branch `oss-patch` in `https://github.com/Team-Atlanta/crete` repository will be used to register the CRS `atlantis-multi-retrieval`.


### config-crs.yaml

Specify constraints and dependencies for the CRS in this file.
The specified LLM models will be included in the LiteLLM provisioned key during deployment.

The main field of this YAML is `models` and `build` (optional). `models` specifies LLM models the CRS can use and `build` specifies the dockerfile that will be used to build the CRS docekr image.

```
42-b3yond-bug-patch-agent:
  models:
    - gpt-5
    - o4-mini
    - o3
    - gpt-4.1
  build:  # optional, by default `builder.Dockerfile`
    dockerfile: oss-patch/builder.Dockerfile
  modes:  # optional
    - delta    
```

The CRS can also restrict the modes it deploys in (i.e. full or delta). By default if `modes` is not specified, the CRS may be deployed in both full and delta modes.

* [NOTE]: Currently, our system supports only "full" mode.


## Making CRS Compatible with OSS-PATCH

As described before, we expect each CRS to expose the main entrypoints for building docker image in `builder.Dockerfile`.
These files should be at the relative location from the root of the CRS repository, which is referenced in `pkg.yaml`.

Similar to OSS-CRS, we are planning to support `runner.Dockerfile` in the future to help the CRS that wants to build a project-specific image.

### builder.Dockerfile

As mentioned earlier, `builder.Dockerfile` is used to build the docker image that runs the corresponding CRS.
For now, each CRS image will be named as `gcr.io/oss-patch/<crs-name>`.

Since each CRS utilizes the OSS-Fuzz for building each target project, 
we require the docker image of each CRS must be in DinD (Docker-in-Docker) environment.

When integrating each CRS using this Dockerfile, the developer must specify their own `CMD` in their Dockerfile like the below example.


```dockerfile
# [... install CRS ...]

### Register the CRS-running command
CMD ["sh", "-c", "python3 /app/run_crs.py"]
```

### CRS Runtime Environment

The command that is used to run each CRS is `uv run oss-patch run <...>`.
When the command is issued, OSS-Patch will invoke the CRS container and run the program registered in `CMD`. 

To provide CRS inputs in a standardized way, OSS-Patch uses container's `/work` directory to pass inputs to each CRS. 
To be specific, each CRS will see `/work` directory in container when launched as follows:

```
# In the CRS' container
work/
└── povs/
    └── pov_0.yaml
    └── pov_1.yaml
    [...]
└── hints/ (optional)
   └── pov_0_hint.txt
   └── pov_1_hint.txt
    [...]
└── ref.diff (optional, for delta mode)
```


Each YAML file (e.g., `pov_0.yaml`, `pov_1.yaml`, ...) contains PoV information like below:

```yaml
harness_name: fuzz_process_input_header
mode: full
blob: QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQQ==
pov_id: pov_0
project_name: aixcc/c/mock-c
```
The `blob` field contains the actual PoV content encoded in base64. 
Each CRS developer must implement their CRS with using the information provided in `/work` directory.

In addition to the use of `/work` directory, each CRS container will run with the following important environment variables to help the standardization:

* Environment Variables:
  * **$OSS_FUZZ**: The path of OSS-Fuzz (default: `/oss-fuzz`). We are planning to provide OSS-Fuzz that supports incremental build feature. Each CRS can access to the OSS-Fuzz by using this environment variable.
  * **$TARGET_PROJ**: Name of the target project (e.g., json-c, nginx, etc).
  * **$CP_SOURCES**: The path of source code repository of target project (default: `/cp-sources`). Each CRS can access to target project's source code by using this environment variable.
  * **$LITELLM_API_KEY**: Litellm API key
  * **$LITELLM_API_BASE**: Litellm API Base URL


### Output format

For supporting the benchmarks RFC, the each CRS must construct `/out` directory in its container as follows:
```
/out/                           # CRS output directory (in container)
├── patches/                    # Generated patches (required for patch generation CRS)
│   ├── pov_0/                  # Patches for pov_0
│   │   └── patch.diff          # Unified diff format
│   ├── pov_1/                  # Patches for pov_1
│   │   └── patch.diff
│   └── pov_2/                  # Patches for pov_2
│       └── patch.diff
├── crs-data/                   # CRS-specific outputs (optional)
│   ├── pov_0/                  
│   │   └── additional_outputs, repair.log, test-result.json, etc          
│   ├── pov_1/                  
│   │   └── [...]
    [...omitted...]
```


### Integration Example: PatchAgent (from Northwestern University)

Here we describe the integration of PatchAgent published in USENIX Security '25. 
The CRS is based on the repository in https://github.com/cla7aye15I4nd/PatchAgent.

To make PatchAgent run under OSS-PATCH, `oss-patch/` is created to contain OSS-PATCh-specific files (This is just one of the examples. Implementation may vary).
i.e., 
```
PatchAgent/                     # PatchAgent CRS repository
├── ...
└── oss-patch/
    ├── builder.Dockerfile      # Dockerfile that will be used to build the CRS image
    └── runner.py               # A wrapper script for running PatchAgent (specific implementation may vary across developers)
```

The following is the example `builder.Dockerfile` that builds the docker image of PatchAgent.

```dockerfile
# DO NOT EDIT OR REMOVE THIS `ARG` STATEMENT
ARG crs_base_image=cruizba/ubuntu-dind:latest

# DO NOT EDIT OR REMOVE THIS `FROM` STATEMENT
FROM $crs_base_image

RUN DEBIAN_FRONTEND=noninteractive && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    # [...omitted...]
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH"

# [...omitted...]

WORKDIR /source
COPY patchagent /source/patchagent
COPY pyproject.toml /source/pyproject.toml

RUN pip install --no-cache-dir -e ".[dev]"

# OSS-Patch will run /source/oss-patch/runner.py when running the CRS.
COPY oss-patch /source/oss-patch
CMD ["sh", "-c", "python3 /source/oss-patch/runner.py"]
```

As described in `builder.Dockerfile`, OSS-PATCH will execute the `runner.py` when running PatchAgent.

Based on the PatchAgent's structure, the `runner.py` can be implemented as follows:

```py
from patchagent.agent.generator import agent_generator
from patchagent.builder import OSSFuzzBuilder, OSSFuzzPoC
from patchagent.parser.sanitizer import Sanitizer
from patchagent.task import PatchTask
import yaml
from pathlib import Path
import base64
import os

OSS_PATCH_POVS_DIRECTORY = Path("/work/povs") # `/work` directory contains PoV information
OSS_PATCH_OUT_DIRECTORY = Path("/out")        # Output directory must be `/out`
OSS_PATCH_OSS_FUZZ_PATH = Path(os.environ["OSS_FUZZ"])
OSS_PATCH_SOURCE_DIRECTORY_PATH = Path(os.environ["CP_SOURCES"])
DEFAULT_POC_BIN_PATH = Path("/tmp/poc.bin")

if __name__ == "__main__":
    assert OSS_PATCH_OSS_FUZZ_PATH.exists()
    assert OSS_PATCH_SOURCE_DIRECTORY_PATH.exists()
    assert OSS_PATCH_POVS_DIRECTORY.exists()

    assert os.environ["LITELLM_API_KEY"]
    assert os.environ["LITELLM_API_BASE"]

    os.environ["OPENAI_API_KEY"] = os.environ["LITELLM_API_KEY"]
    os.environ["OPENAI_BASE_URL"] = f"{os.environ["LITELLM_API_BASE"]}/openai"

    for pov_yaml_path in OSS_PATCH_POVS_DIRECTORY.iterdir():
        with open(pov_yaml_path, "r") as f:
            pov_yaml = yaml.safe_load(f)

        pov_out_dir = OSS_PATCH_OUT_DIRECTORY / pov_yaml["pov_id"]
        pov_out_dir.mkdir(exist_ok=True)

        with open(DEFAULT_POC_BIN_PATH, 'wb') as f:
            f.write(base64.b64decode(pov_yaml["blob"].encode()))

        # Initialize the repair task
        patchtask = PatchTask(
            [OSSFuzzPoC(DEFAULT_POC_BIN_PATH, pov_yaml["harness_name"])],  # Proof of Concept file with target
            OSSFuzzBuilder(
                pov_yaml["project_name"], # Project name
                OSS_PATCH_SOURCE_DIRECTORY_PATH, # Source code path
                OSS_PATCH_OSS_FUZZ_PATH,         # OSS-Fuzz path
                [Sanitizer.AddressSanitizer],    # Sanitizer to use
            ),
        )

        # Initialize and run the repair process
        patchtask.initialize()
        patch = patchtask.repair(agent_generator())
        print(f"Generated patch: {patch}")

        pov_out_dir = OSS_PATCH_OUT_DIRECTORY / pov_yaml["pov_id"]
        if patch:
            (pov_out_dir / "patch.diff").write_text(patch)
        else:
            (pov_out_dir / "patch.diff").write_text("")
```

As a result, PatchAgent CRS will create patches in the specified output directory.
