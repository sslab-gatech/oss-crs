import sys
import argparse
from pathlib import Path
from ..crs_compose import CRSCompose


def add_common_arguments(parser):
    parser.add_argument(
        "--compose-file",
        type=Path,
        required=True,
        help="Path to the CRS Compose file",
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
    pass


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

    crs_compose = CRSCompose.from_yaml_file(args.compose_file)

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish):
            return False
    elif args.command == "build-target":
        pass
    elif args.command == "run":
        pass
    elif args.command == "check":
        pass
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
