#!/usr/bin/env python3
"""CLI entrypoint for CRS package."""

import argparse
import logging
import sys
from pathlib import Path

from .crs_main import build_crs, run_crs, OSS_FUZZ_DIR, BUILD_DIR


def main():
    """Main entry point for CRS CLI."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(
        description='CRS (Cyber Reasoning System) build and run tool'
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # build_crs subcommand
    build_parser = subparsers.add_parser('build', help='Build CRS for a project')
    build_parser.add_argument('config_dir',
                              help='Directory containing CRS configuration files')
    build_parser.add_argument('project', help='OSS-Fuzz project name')
    build_parser.add_argument('source_path', nargs='?',
                              help='Optional path to local source')
    build_parser.add_argument('--build-dir', default=str(BUILD_DIR),
                              help='Path to build directory')
    build_parser.add_argument('--oss-fuzz-dir', default=str(OSS_FUZZ_DIR),
                              help='Path to oss-fuzz directory')
    build_parser.add_argument('--registry-dir',
                              help='Path to local oss-crs-registry directory')
    build_parser.add_argument('--engine', default='libfuzzer',
                              help='Fuzzing engine (default: libfuzzer)')
    build_parser.add_argument('--sanitizer', default='address',
                              help='Sanitizer (default: address)')
    build_parser.add_argument('--architecture', default='x86_64',
                              help='Architecture (default: x86_64)')
    build_parser.add_argument('--external-litellm', action='store_true',
                              help='Use external LiteLLM instance (requires LITELLM_URL and LITELLM_KEY env vars)')

    # run_crs subcommand
    run_parser = subparsers.add_parser('run', help='Run CRS')
    run_parser.add_argument('config_dir',
                            help='Directory containing CRS configuration files')
    run_parser.add_argument('project', help='OSS-Fuzz project name')
    run_parser.add_argument('fuzzer_name', help='Name of the fuzzer')
    run_parser.add_argument('fuzzer_args', nargs='*',
                            help='Arguments to pass to the fuzzer')
    run_parser.add_argument('--worker', default='local',
                            help='Worker name (default: local)')
    run_parser.add_argument('--build-dir', default=str(BUILD_DIR),
                            help='Path to build directory')
    run_parser.add_argument('--oss-fuzz-dir', default=str(OSS_FUZZ_DIR),
                            help='Path to oss-fuzz directory')
    run_parser.add_argument('--registry-dir',
                            help='Path to local oss-crs-registry directory')
    run_parser.add_argument('--engine', default='libfuzzer',
                            help='Fuzzing engine (default: libfuzzer)')
    run_parser.add_argument('--sanitizer', default='address',
                            help='Sanitizer (default: address)')
    run_parser.add_argument('--architecture', default='x86_64',
                            help='Architecture (default: x86_64)')
    run_parser.add_argument('--output',
                            help='Output directory for CRS results')
    run_parser.add_argument('--hints',
                            help='Directory containing hints (SARIF reports and corpus)')
    run_parser.add_argument('--harness-source',
                            help='Path to harness source file for analysis')
    run_parser.add_argument('--external-litellm', action='store_true',
                            help='Use external LiteLLM instance (requires LITELLM_URL and LITELLM_KEY env vars)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Convert paths to str for consistency

    # Validate paths for run command
    if args.command == 'run':
        if args.output and not Path(args.output).exists():
            logging.error(f"Output directory does not exist: {args.output}")
            return 1
        if args.hints and not Path(args.hints).exists():
            logging.error(f"Hints directory does not exist: {args.hints}")
            return 1
        if args.harness_source and not Path(args.harness_source).exists():
            logging.error(f"Harness source file does not exist: {args.harness_source}")
            return 1

    if args.command == 'build':
        result = build_crs(
            config_dir=args.config_dir,
            project_name=args.project,
            oss_fuzz_dir=args.oss_fuzz_dir,
            build_dir=args.build_dir,
            engine=args.engine,
            sanitizer=args.sanitizer,
            architecture=args.architecture,
            source_path=args.source_path,
            registry_dir=args.registry_dir,
            external_litellm=args.external_litellm
        )
    elif args.command == 'run':
        result = run_crs(
            config_dir=args.config_dir,
            project_name=args.project,
            fuzzer_name=args.fuzzer_name,
            fuzzer_args=args.fuzzer_args,
            oss_fuzz_dir=args.oss_fuzz_dir,
            build_dir=args.build_dir,
            worker=args.worker,
            engine=args.engine,
            sanitizer=args.sanitizer,
            architecture=args.architecture,
            registry_dir=args.registry_dir,
            output_dir=args.output,
            hints_dir=args.hints,
            harness_source=args.harness_source,
            external_litellm=args.external_litellm
        )
    else:
        parser.print_help()
        return 1

    return 0 if result else 1


if __name__ == '__main__':
    sys.exit(main())
