import argparse
from ..build import submit_build_output, skip_build_output, download_build_output


def main():
    parser = argparse.ArgumentParser(
        prog="libCRS", description="libCRS - CRS utilities"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # submit-build-output command
    submit_parser = subparsers.add_parser(
        "submit-build-output", help="Submit build output from src_path to dst_path"
    )
    submit_parser.add_argument("src_path", help="Source path in docker container")
    submit_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    submit_parser.set_defaults(
        func=lambda args: submit_build_output(args.src_path, args.dst_path)
    )

    # skip-build-output command
    skip_parser = subparsers.add_parser(
        "skip-build-output",
        help="Skip build output for dst_path on build output file system",
    )
    skip_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    skip_parser.set_defaults(func=lambda args: skip_build_output(args.dst_path))

    # download-build-output command
    copy_parser = subparsers.add_parser(
        "download-build-output",
        help="Download build output from src_path (on build output filesystem) to dst_path (in docker container)",
    )
    copy_parser.add_argument("src_path", help="Source path on build output file system")
    copy_parser.add_argument("dst_path", help="Destination path in docker container")
    copy_parser.set_defaults(
        func=lambda args: download_build_output(args.src_path, args.dst_path)
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
