# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Added
- `--early-exit` flag to `oss-crs run` to stop on the first discovered artifact (POV or patch)
- GitHub Actions CI pipeline with lint (ruff check), format check (ruff format), type check (pyright), unit tests, and parallel C/Java smoke tests
- atlantis-java-main to registry/ and example/
- atlantis-c-deepgen to registry/ and example/
- Volume mounts `/OSS_CRS_FUZZ_PROJ` (read-only) and `/OSS_CRS_TARGET_SOURCE`
  in both build-target and run compose templates.
- Automatic WORKDIR extraction from built Docker images when
  `--target-source-path` is not provided — target source is always available at
  `/OSS_CRS_TARGET_SOURCE` in both build and run phases.
- `libCRS download-source fuzz-proj <dest>` — copies fuzz project from mount.
- `libCRS download-source target-source <dest>` — copies target source from
  mount.

### Changed
- Clarified that target env `repo_path` is the effective in-container source
  path (Dockerfile final `WORKDIR`) used for `OSS_CRS_REPO_PATH`, not a host
  path override.
- When `--target-source-path` is provided, source override now uses
  `rsync -a --delete` into the effective `WORKDIR` (strict replacement of that
  tree).
- `OSS_CRS_REPO_PATH` resolution is documented as: final `WORKDIR` -> `$SRC` ->
  `/src` fallback chain.
- Target build-option resolution now uses precedence:
  CLI `--sanitizer` flag -> `additional_env` override (SANITIZER at CRS-entry scope)
  -> `project.yaml` fallback (uses address if provided, else first)
  -> framework defaults.
- `artifacts --sanitizer` is now optional; when omitted, sanitizer is resolved
  using the same contract (compose/project/default) used by build/run flows.
- **Breaking:** `libCRS download-source` API replaced — `target`/`repo`
  subcommands removed, use `fuzz-proj`/`target-source` instead. Python API
  `download_source()` now returns `None` instead of `Path`.

### Deprecated
- Deprecated CLI aliases:
  - `--target-path` in favor of `--fuzz-proj-path`
  - `--target-proj-path` in favor of `--fuzz-proj-path`
- Deprecated aliases now emit runtime warnings and are planned for removal in a
  future minor release.

### Removed
- Removed legacy CLI alias `--target-repo-path`; use `--target-source-path`.
- Removed `libCRS download-source target` and `download-source repo` commands.
- Removed `SourceType.TARGET` and `SourceType.REPO` enum values from libCRS.
- Removed ~140 lines of fallback resolution logic from libCRS
  (`_resolve_repo_source_path`, `_normalize_repo_source_path`,
  `_translate_repo_hint_to_build_output`, `_resolve_downloaded_repo_path`,
  `_relative_repo_hint`).

### Fixed
- The local run path now passes a `Path` compose-file object consistently into
  `docker_compose_up()`, so helper-sidecar teardown classification applies on
  the main local run path.

### Security
- N/A
