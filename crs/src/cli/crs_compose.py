import sys
import argparse


def add_common_arguments(parser):
    pass


def add_prepare_command(subparsers):
    pass


def add_build_target_command(subparsers):
    pass


def add_run_command(subparsers):
    pass


def add_check_command(subparsers):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CRS (Cyber Reasoning System) Compose CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    add_prepare_command(subparsers)
    add_build_target_command(subparsers)
    add_run_command(subparsers)
    add_check_command(subparsers)

    args = parser.parse_args()

    if args.command == "prepare":
        pass
    elif args.command == "build-target":
        pass
    elif args.command == "run":
        pass
    elif args.command == "check":
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
