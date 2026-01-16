#!/usr/bin/env python3
"""CLI entrypoint for CRS package."""

import argparse
import logging
import sys
from pathlib import Path

from bug_finding.src.build import build_crs
from bug_finding.src.prepare import prepare_crs
from bug_finding.src.run import run_crs
from bug_finding.src.utils import set_gitcache


def main() -> int:
    """Main entry point for CRS CLI."""
    # FIXME: does not work
    # logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(
        description="CRS (Cyber Reasoning System) build and run tool"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Default clone directory (hidden at cwd)
    default_clone_dir = Path.cwd() / ".oss-bugfind"

    # prepare subcommand
    prepare_parser = subparsers.add_parser(
        "prepare", help="Prepare CRS by pre-building docker images for dind"
    )
    prepare_parser.add_argument(
        "crs_name", help="Name of the CRS to prepare (must exist in registry)"
    )
    prepare_parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path.cwd() / "build",
        help="Path to build directory (default: ./build)",
    )
    prepare_parser.add_argument(
        "--clone-dir",
        type=Path,
        default=default_clone_dir,
        help="Path to clone directory (default: ./.oss-bugfind)",
    )
    prepare_parser.add_argument(
        "--registry-dir", type=Path, help="Path to local oss-crs-registry directory"
    )

    # build_crs subcommand
    build_parser = subparsers.add_parser("build", help="Build CRS for a project")
    build_parser.add_argument(
        "config_dir", type=Path, help="Directory containing CRS configuration files"
    )
    build_parser.add_argument("project", help="OSS-Fuzz project name")
    build_parser.add_argument(
        "source_path", nargs="?", type=Path, help="Optional path to local source"
    )
    build_parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path.cwd() / "build",
        help="Path to build directory (default: ./build)",
    )
    build_parser.add_argument(
        "--project-path", type=Path, help="Path to local OSS-compatible project"
    )
    build_parser.add_argument(
        "--clone-dir",
        type=Path,
        default=default_clone_dir,
        help="Path to clone directory (default: ./.oss-bugfind)",
    )
    build_parser.add_argument(
        "--oss-fuzz-dir",
        type=Path,
        default=None,
        help="Path to source oss-fuzz directory to copy from",
    )
    build_parser.add_argument(
        "--registry-dir", type=Path, help="Path to local oss-crs-registry directory"
    )
    build_parser.add_argument(
        "--engine", default="libfuzzer", help="Fuzzing engine (default: libfuzzer)"
    )
    build_parser.add_argument(
        "--sanitizer", default="address", help="Sanitizer (default: address)"
    )
    build_parser.add_argument(
        "--architecture", default="x86_64", help="Architecture (default: x86_64)"
    )
    build_parser.add_argument(
        "--project-image-prefix",
        default="gcr.io/oss-fuzz",
        help="Project image prefix (default: gcr.io/oss-fuzz)",
    )
    build_parser.add_argument(
        "--external-litellm",
        action="store_true",
        help="Use external LiteLLM instance (requires LITELLM_URL and LITELLM_KEY env vars)",
    )
    build_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing project in oss-fuzz/projects/ when using project_path",
    )
    build_parser.add_argument(
        "--clone",
        action="store_true",
        help="Clone project source from main_repo in project.yaml (for custom projects)",
    )
    build_parser.add_argument(
        "--gitcache", action="store_true", help="Use gitcache for git clone operations"
    )
    build_parser.add_argument(
        "--skip-oss-fuzz-clone",
        action="store_true",
        help="Skip cloning oss-fuzz (user guarantees oss-fuzz is already available)",
    )
    build_parser.add_argument(
        "--no-prepare-images",
        action="store_false",
        dest="prepare_images",
        help="Disable auto-prepare when CRS bake images are missing",
    )

    # run_crs subcommand
    run_parser = subparsers.add_parser("run", help="Run CRS")
    run_parser.add_argument(
        "config_dir", type=Path, help="Directory containing CRS configuration files"
    )
    run_parser.add_argument("project", help="OSS-Fuzz project name")
    run_parser.add_argument("fuzzer_name", help="Name of the fuzzer")
    run_parser.add_argument(
        "fuzzer_args", nargs="*", help="Arguments to pass to the fuzzer"
    )
    run_parser.add_argument(
        "--worker", default="local", help="Worker name (default: local)"
    )
    run_parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path.cwd() / "build",
        help="Path to build directory (default: ./build)",
    )
    run_parser.add_argument(
        "--clone-dir",
        type=Path,
        default=default_clone_dir,
        help="Path to clone directory (default: ./.oss-bugfind)",
    )
    run_parser.add_argument(
        "--oss-fuzz-dir",
        type=Path,
        default=None,
        help="Path to source oss-fuzz directory to copy from",
    )
    run_parser.add_argument(
        "--registry-dir", type=Path, help="Path to local oss-crs-registry directory"
    )
    run_parser.add_argument(
        "--engine", default="libfuzzer", help="Fuzzing engine (default: libfuzzer)"
    )
    run_parser.add_argument(
        "--sanitizer", default="address", help="Sanitizer (default: address)"
    )
    run_parser.add_argument(
        "--architecture", default="x86_64", help="Architecture (default: x86_64)"
    )
    run_parser.add_argument(
        "--hints",
        type=Path,
        help="Directory containing hints (SARIF reports and corpus)",
    )
    run_parser.add_argument(
        "--harness-source", type=Path, help="Path to harness source file for analysis"
    )
    run_parser.add_argument("--diff", type=Path, help="Path to diff file for analysis")
    run_parser.add_argument(
        "--external-litellm",
        action="store_true",
        help="Use external LiteLLM instance (requires LITELLM_URL and LITELLM_KEY env vars)",
    )
    run_parser.add_argument(
        "--gitcache", action="store_true", help="Use gitcache for git clone operations"
    )
    run_parser.add_argument(
        "--ensemble-dir",
        type=Path,
        default=None,
        help="Base directory for ensemble sharing (default: build/ensemble/<config>/<project>/<harness>/)",
    )
    run_parser.add_argument(
        "--disable-ensemble",
        action="store_true",
        help="Disable automatic ensemble directory for multi-CRS mode",
    )
    run_parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="Directory containing initial corpus files to copy to ensemble corpus",
    )
    run_parser.add_argument(
        "--skip-oss-fuzz-clone",
        action="store_true",
        help="Skip cloning oss-fuzz (user guarantees oss-fuzz is already available)",
    )
    run_parser.add_argument(
        "--coverage-build-dir",
        type=Path,
        default=None,
        help="Directory containing coverage-instrumented binaries to mount at /coverage-out",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Handle prepare command (doesn't need config_dir, project, oss_fuzz_dir, gitcache)
    if args.command == "prepare":
        from importlib.resources import files

        build_dir = args.build_dir.resolve()
        clone_dir = args.clone_dir.resolve()

        if not build_dir.exists():
            logging.info(f"Creating build directory: {build_dir}")
            build_dir.mkdir(parents=True, exist_ok=True)

        if not clone_dir.exists():
            logging.info(f"Creating clone directory: {clone_dir}")
            clone_dir.mkdir(parents=True, exist_ok=True)

        # Default registry path
        _pkg_files = files("bug_finding.src")
        DEFAULT_REGISTRY_DIR = Path(str(_pkg_files)).parent.parent / "crs_registry"

        prepare_kwargs = {
            "crs_name": args.crs_name,
            "build_dir": build_dir,
            "clone_dir": clone_dir,
            "registry_dir": args.registry_dir.resolve() if args.registry_dir else DEFAULT_REGISTRY_DIR,
        }
        result = prepare_crs(**prepare_kwargs)
        return 0 if result else 1

    # For build and run commands, resolve paths
    config_dir = args.config_dir.resolve()
    build_dir = args.build_dir.resolve()
    clone_dir = args.clone_dir.resolve()

    # Ensure directories exist
    if not build_dir.exists():
        logging.info(f"Creating build directory: {build_dir}")
        build_dir.mkdir(parents=True, exist_ok=True)

    if not clone_dir.exists():
        logging.info(f"Creating clone directory: {clone_dir}")
        clone_dir.mkdir(parents=True, exist_ok=True)

    # For build and run commands, set up additional paths
    # OSS-Fuzz goes into build_dir (per-project, tied to build artifacts)
    oss_fuzz_dir = (build_dir / "oss-fuzz" / args.project).resolve()

    # Resolve source oss-fuzz directory if provided (for copying)
    source_oss_fuzz_dir = args.oss_fuzz_dir.resolve() if args.oss_fuzz_dir else None

    # Set gitcache mode
    set_gitcache(args.gitcache)

    if args.command == "build":
        # Build kwargs, only including non-None optional arguments
        build_kwargs = {
            "config_dir": config_dir,
            "project_name": args.project,
            "oss_fuzz_dir": oss_fuzz_dir,
            "build_dir": build_dir,
            "clone_dir": clone_dir,
            "engine": args.engine,
            "sanitizer": args.sanitizer,
            "architecture": args.architecture,
            "overwrite": args.overwrite,
            "clone": args.clone,
            "project_image_prefix": args.project_image_prefix,
            "external_litellm": args.external_litellm,
            "skip_oss_fuzz_clone": args.skip_oss_fuzz_clone,
            "prepare_images": args.prepare_images,
        }

        # Only add optional paths if provided
        if args.source_path:
            build_kwargs["source_path"] = args.source_path.resolve()
        if args.project_path:
            build_kwargs["project_path"] = args.project_path.resolve()
        if args.registry_dir:
            build_kwargs["registry_dir"] = args.registry_dir.resolve()
        if source_oss_fuzz_dir:
            build_kwargs["source_oss_fuzz_dir"] = source_oss_fuzz_dir

        result = build_crs(**build_kwargs)
    elif args.command == "run":
        # Resolve and validate run-specific paths
        hints_dir = args.hints.resolve() if args.hints else None
        harness_source = args.harness_source.resolve() if args.harness_source else None
        diff_path = args.diff.resolve() if args.diff else None

        if hints_dir and not hints_dir.exists():
            logging.error(f"Hints directory does not exist: {hints_dir}")
            return 1
        if harness_source and not harness_source.exists():
            logging.error(f"Harness source file does not exist: {harness_source}")
            return 1
        if diff_path and not diff_path.exists():
            logging.error(f"Diff file does not exist: {diff_path}")
            return 1

        # Build kwargs, only including non-None optional arguments
        run_kwargs = {
            "config_dir": config_dir,
            "project_name": args.project,
            "fuzzer_name": args.fuzzer_name,
            "fuzzer_args": args.fuzzer_args,
            "oss_fuzz_dir": oss_fuzz_dir,
            "build_dir": build_dir,
            "clone_dir": clone_dir,
            "worker": args.worker,
            "engine": args.engine,
            "sanitizer": args.sanitizer,
            "architecture": args.architecture,
            "external_litellm": args.external_litellm,
            "skip_oss_fuzz_clone": args.skip_oss_fuzz_clone,
        }

        # Only add optional paths if provided
        if args.registry_dir:
            run_kwargs["registry_dir"] = args.registry_dir.resolve()
        if hints_dir:
            run_kwargs["hints_dir"] = hints_dir
        if harness_source:
            run_kwargs["harness_source"] = harness_source
        if diff_path:
            run_kwargs["diff_path"] = diff_path
        if source_oss_fuzz_dir:
            run_kwargs["source_oss_fuzz_dir"] = source_oss_fuzz_dir

        # Ensemble directory options
        if args.ensemble_dir:
            run_kwargs["ensemble_dir"] = args.ensemble_dir.resolve()
        run_kwargs["disable_ensemble"] = getattr(args, "disable_ensemble", False)
        if args.corpus:
            run_kwargs["corpus_dir"] = args.corpus.resolve()
        if args.coverage_build_dir:
            run_kwargs["coverage_build_dir"] = args.coverage_build_dir.resolve()

        result = run_crs(**run_kwargs)
    else:
        parser.print_help()
        return 1

    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
