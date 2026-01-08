# Instructions

This is the `oss-crs` package - a command-line tool for building and running Cyber Reasoning Systems (CRS).

## Overview

The `oss-crs` package provides standardized commands for CRS operations:
- `oss-crs build` - Build CRS Docker images
- `oss-crs run` - Execute built CRS for vulnerability discovery

This package is part of the larger CRSBench ecosystem and implements the standardized CRS interface.

## Glossary

- CRS: cyber reasoning system
- LLM: large language model
- OSS-Fuzz: Google's continuous fuzzing service for open source software

## Documentation Structure

This repository has two types of documentation:

### docs/ - User-Facing Documentation

- Command-line interface documentation
- Configuration file formats
- User guides for running CRS
- Target audience: CRS operators and users

### design-docs/ - Implementation Details

- **MUST create design document in `design-docs/` before implementing new features**
- Architecture and design decisions
- Module implementation documentation
- Internal APIs and data flows
- Target audience: oss-crs contributors

**Design doc should cover:**

- Architecture overview and component interaction
- Data flow and API design
- File structure and module organization
- Integration points with existing code
- Testing strategy

**Writing guidelines:**

- Avoid lengthy or unnecessary details
- Include only the essential features that must be implemented
- Do not add extra requirements unless explicitly requested by the user
- Keep documentation focused on oss-crs package implementation

## Coding standard

### Module Organization

- **Main entry point**: `oss_crs/__main__.py` provides the CLI via `oss-crs` command
- **Core modules**:
  - `oss_crs/crs_main.py` - Main build/run logic
  - `oss_crs/render_compose.py` - Docker Compose configuration generation
  - `oss_crs/key_provisioner/` - LiteLLM key provisioning
- Keep modules focused and cohesive
- Organize related functionality into subdirectories when appropriate

### Python
- use absolute import instead of relative import; so moving files around for
  restructuring is straightforward without further editing import statements.

### Testing

- Follow TDD (Test-Driven Development) design when applicable
- **MUST run corresponding tests when modifying a module**
- **ALL test code MUST be in `tests/` directory, NOT in module directories**
- Test files are located in `tests/` directory
- Test file naming: `test_<module_name>.py` (e.g., `test_crs_main.py`, `test_render_compose.py`)
- **IMPORTANT: Always use `uv run pytest` instead of bare `pytest`**
  - This ensures tests run in the correct virtual environment
  - Example: `uv run pytest tests/test_<module_name>.py -v`
  - Example: `uv run pytest tests/test_<module_name>.py --cov=oss_crs.<module_name>`
- Update tests when changing module behavior or adding features
- **DO NOT use `cat` command or heredocs to create test files**
  - Create proper Python test scripts using the Write tool
  - Use Python's `tempfile` module for temporary test data
  - Use `with open()` context managers for file operations in tests


## Documentation Standards

- Update README.md when adding new features or changing behavior
- Document command-line options and configuration formats
- Keep documentation in sync with code changes

## Usage or reference of third party codebase

- Document any third-party code usage for proper attribution
- Include licenses and acknowledgments as appropriate

## Testing oss-crs CLI

The main CLI entry point is `oss-crs` command provided by `oss_crs/__main__.py`.

### Installation

Install the package in editable mode:

```bash
uv pip install -e .
```

This creates the `oss-crs` executable.

### Running oss-crs

After installation, run it using:

```bash
# Build a CRS
uv run oss-crs build example_configs/ensemble-c json-c

# Run a CRS
uv run oss-crs run example_configs/ensemble-c json-c json_array_fuzzer

# Get help
uv run oss-crs --help
uv run oss-crs build --help
uv run oss-crs run --help
```

### Example configurations

Example CRS configurations are in `example_configs/`:
- `ensemble-c/` - C language ensemble CRS
- `ensemble-java/` - Java language ensemble CRS
- `atlantis-c-libafl/` - LibAFL-based C CRS
- `crs-libfuzzer/` - Vanilla libFuzzer reference

## Other instructions

- Use uv as python package manager
- Follow absolute imports for better code organization

## Active Technologies
- Python 3.9+ + rsync (system utility), subprocess (stdlib) (001-oss-fuzz-copy-optimize)
- Filesystem (OSS-Fuzz directory structure) (001-oss-fuzz-copy-optimize)

## Recent Changes
- 001-oss-fuzz-copy-optimize: Added Python 3.9+ + rsync (system utility), subprocess (stdlib)
