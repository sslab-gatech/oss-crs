import argparse
import logging
import os
import sys
from pathlib import Path
from .oss_patch import OSSPatch
from .oss_patch.functions import change_ownership_with_docker


logger = logging.getLogger(__name__)


def _get_path_or_none(arg_str: str) -> Path | None:
    return Path(arg_str) if arg_str else None


# This format removes the long module path, adds a timestamp, and keeps it concise.
CUSTOM_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"

# This is an even more concise format, only showing the log level, the filename,
# and the message (similar to what you might want if the module path is too long).
CONCISE_LOG_FORMAT = "OSS-Patch | %(levelname)s | %(message)s"

# Define the date format (optional, works with %(asctime)s)
CUSTOM_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _setup_logger_config():
    """
    Configures the root logger with the desired format and level.
    """
    # Use the concise format for this example
    logging.basicConfig(
        level=logging.INFO,  # Set the minimum level to log (e.g., DEBUG, INFO, WARNING)
        format=CONCISE_LOG_FORMAT,  # Apply the custom format string
        datefmt=CUSTOM_DATE_FORMAT,  # Apply the custom date format
    )


def main():  # pylint: disable=too-many-branches,too-many-return-statements
    """Gets subcommand from program arguments and does it. Returns 0 on success 1
    on error."""
    _setup_logger_config()
    parser = _get_parser()
    args = _parse_args(parser)

    if args.command == "build":
        oss_patch = OSSPatch(args.project, crs_name=args.crs)
        result = oss_patch.build(
            Path(args.oss_fuzz),
            _get_path_or_none(args.project_path),
            _get_path_or_none(args.source_path),
            _get_path_or_none(args.local_crs),
            _get_path_or_none(args.registry),
            args.overwrite,
            args.gitcache,
        )
    elif args.command == "run":
        # Resolve litellm config: CLI args > env vars
        litellm_base = args.litellm_base or os.environ.get("LITELLM_API_BASE")
        litellm_key = args.litellm_key or os.environ.get("LITELLM_API_KEY")

        if not litellm_base:
            logger.error(
                "LiteLLM API base not set. Use --litellm-base or set LITELLM_API_BASE env var."
            )
            return 1
        if not litellm_key:
            logger.error(
                "LiteLLM API key not set. Use --litellm-key or set LITELLM_API_KEY env var."
            )
            return 1

        oss_patch = OSSPatch(args.project, crs_name=args.crs)
        result = oss_patch.run_crs(
            args.harness,
            Path(args.povs),
            litellm_key,
            litellm_base,
            _get_path_or_none(args.hints),
            Path(args.out),
        )
        # FIXME: Bandaid solution for permission issues when runner executes as root
        change_ownership_with_docker(Path(args.out))
    elif args.command == "test-inc-build":
        oss_patch = OSSPatch(args.project)
        result = oss_patch.test_inc_build(Path(args.oss_fuzz))
    # elif args.command == "run_pov":
    #     oss_patch = OSSPatch(args.project)
    #     result = oss_patch.run_pov(args.harness, Path(args.pov), args.source_path)
    # elif args.command == "check_povs":
    #     oss_patch = OSSPatch(args.project)
    #     result = oss_patch.test_povs(Path(args.oss_fuzz))
    else:
        # Print help string if no arguments provided.
        parser.print_help()
        result = False

    return 0 if result else 1


def _parse_args(parser, args=None):
    """Parses |args| using |parser| and returns parsed args. Also changes
    |args.build_integration_path| to have correct default behavior."""
    # Use default argument None for args so that in production, argparse does its
    # normal behavior, but unittesting is easier.
    parsed_args = parser.parse_args(args)
    return parsed_args


def _get_parser():  # pylint: disable=too-many-statements,too-many-locals
    """Returns an argparse parser."""
    parser = argparse.ArgumentParser(
        "oss-patch-crs", description="OSS-Patch helper script"
    )
    subparsers = parser.add_subparsers(dest="command")

    build_crs_parser = subparsers.add_parser("build", help="Build CRS for a project.")

    build_crs_parser.add_argument("crs", help="name of the crs")
    build_crs_parser.add_argument(
        "project",
        help="name of the project in the given OSS-Fuzz (e.g., json-c, aixcc/c/mock-c, etc).",
    )
    # build_crs_parser.add_argument('source_path',
    #                               help='path of local source',
    #                               nargs='?')
    build_crs_parser.add_argument("--local-crs", help="path to local CRS source code")
    build_crs_parser.add_argument(
        "--oss-fuzz", required=True, help="path to OSS-Fuzz repository"
    )
    build_crs_parser.add_argument(
        "--project-path",
        help="Path to OSS-Fuzz compatible project directory "
        "(alternative to oss-fuzz/projects/{name}). "
        "Must contain project.yaml, Dockerfile, and build.sh",
        default=None,
    )
    build_crs_parser.add_argument(
        "--source-path",
        help="Path to pre-cloned source code directory "
        "(alternative to cloning from project.yaml main_repo). "
        "Requires --project-path",
        default=None,
    )
    build_crs_parser.add_argument(
        "--registry",
        help="Path to CRS registry directory (default: ../crs_registry relative to package)",
        default=None,
    )
    build_crs_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing oss-fuzz and project directories if they exist",
    )
    build_crs_parser.add_argument(
        "--gitcache",
        action="store_true",
        help="Use gitcache for git clone and submodule operations",
    )

    build_crs_parser.set_defaults(clean=False)

    run_crs_parser = subparsers.add_parser("run", help="Run a patching CRS.")
    run_crs_parser.add_argument("crs", help="name of the crs")
    run_crs_parser.add_argument("project", help="name of the project")
    # run_crs_parser.add_argument(
    #     "--pov", help="path to a single PoV file to generate a patch"
    # )
    run_crs_parser.add_argument(
        "--povs",
        required=True,
        help="path to directory that contains a set of PoVs to generate patches",
    )
    run_crs_parser.add_argument("--harness", required=True, help="name of the harness")
    # run_crs_parser.add_argument(
    #     "--harness-source", help="path to harness source file for analysis"
    # )
    run_crs_parser.add_argument("--hints", help="path to hint text file for the crs")
    run_crs_parser.add_argument(
        "--out", required=True, help="path to crs output directory"
    )
    run_crs_parser.add_argument(
        "--litellm-base", help="address of litellm API base (env: LITELLM_API_BASE)"
    )
    run_crs_parser.add_argument(
        "--litellm-key", help="The API key for litellm (env: LITELLM_API_KEY)"
    )

    run_pov_parser = subparsers.add_parser(
        "run_pov",
        help="Run a PoV under the current project builder environemt. A sanitizer output (i.e., a crash) is expected for a valid PoV.",
    )
    run_pov_parser.add_argument("project", help="name of the project")
    run_pov_parser.add_argument("harness", help="name of the harness")
    run_pov_parser.add_argument("pov", help="PoV path to test against")
    run_pov_parser.add_argument(
        "source_path",
        help="Source code of project where the PoV will be tested based on",
    )

    test_inc_build_parser = subparsers.add_parser(
        "test-inc-build", help="Test incremental build for a given project."
    )

    # test_inc_build_sub_parser = test_inc_build_parser.add_subparsers(
    #     dest="manage_command", required=True, help="Subcommand for testing incremental build."
    # )
    test_inc_build_parser.add_argument("project", help="name of the project")
    test_inc_build_parser.add_argument("oss_fuzz", help="path to OSS-Fuzz")

    test_project_parser = subparsers.add_parser(
        "check_povs",
        help="Test whether the given PoVs for the project work properly (i.e., result in crashes).",
    )
    test_project_parser.add_argument("project", help="name of the project")
    test_project_parser.add_argument("oss_fuzz", help="path to OSS-Fuzz")

    # list_parser = manage_subparsers.add_parser(
    #     "list", help="list existing CRS-related images"
    # )
    # check_parser = manage_subparsers.add_parser(
    #     "check", help="Check a specific CRS or artifact status."
    # )
    # # Add the specific argument for the 'check' command
    # check_parser.add_argument(
    #     "image_name",
    #     help="Specify the Docker image name to check against the cache/volume.",
    # )

    # remove_parser = manage_subparsers.add_parser(
    #     "delete", help="Check a specific CRS or artifact status."
    # )
    # remove_parser.add_argument(
    #     "image_name",
    #     help="Specify the Docker image name to check against the cache/volume.",
    # )
    return parser


if __name__ == "__main__":
    sys.exit(main())
