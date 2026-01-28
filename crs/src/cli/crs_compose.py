import sys
import argparse
from pathlib import Path
from ..crs_compose import CRSCompose
from ..target import Target


DEFAULT_WORK_DIR = (Path(__file__) / "../../../../.oss-crs-workdir").resolve()


def add_common_arguments(parser):
    parser.add_argument(
        "--compose-file",
        type=Path,
        required=True,
        help="Path to the CRS Compose file",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="Working directory for CRS Compose operations",
    )


def add_prepare_command(subparsers):
    prepare = subparsers.add_parser(
        "prepare", help="Prepare CRSs defined in CRS Compose file"
    )
    add_common_arguments(prepare)
    prepare.add_argument(
        "--publish",
        type=bool,
        default=False,
        help="Publish prepared CRS docker images to the specified docker resgistry",
    )


def add_build_target_command(subparsers):
    build_target = subparsers.add_parser(
        "build-target", help="Build target repository defined in CRS Compose file"
    )
    add_common_arguments(build_target)
    build_target.add_argument(
        "--target-proj-path",
        type=Path,
        required=True,
        help="""
        Target Project Path where includes oss-fuzz compatible files (e.g., Dockerfile, project.yaml, ...)
        # TODO: this accepts only local paths for now. But, we will support remote paths later.
        """,
    )
    build_target.add_argument(
        "--target-repo-path",
        type=Path,
        required=False,
        help="Local path to the target repository to build with the target project configuration.",
    )
    build_target.add_argument(
        "--no-checkout",
        type=bool,
        default=False,
        help="Whether to checkout the target repository before building.",
    )


def add_run_command(subparsers):
    pass


def add_check_command(subparsers):
    pass


def main() -> bool:
    parser = argparse.ArgumentParser(
        description="CRS (Cyber Reasoning System) Compose CLI"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Command to run"
    )
    add_prepare_command(subparsers)
    add_build_target_command(subparsers)
    add_run_command(subparsers)
    add_check_command(subparsers)

    args = parser.parse_args()

    crs_compose = CRSCompose.from_yaml_file(args.compose_file, args.work_dir)

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish):
            return False
    elif args.command == "build-target":
        target = Target(args.work_dir, args.target_proj_path, args.target_repo_path)
        if not crs_compose.build_target(target, args.no_checkout):
            return False
    elif args.command == "run":
        pass
    elif args.command == "check":
        pass
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
