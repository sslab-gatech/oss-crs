import argparse
from pathlib import Path
from ..base import DataType, CRSUtils
from ..local import LocalCRSUtils
from ..common import OSS_CRS_RUN_ENV_TYPE, EnvType


def init_crs_utils() -> CRSUtils:
    if OSS_CRS_RUN_ENV_TYPE == EnvType.LOCAL:
        return LocalCRSUtils()
    else:
        raise NotImplementedError(
            f"CRSUtils not implemented for run environment: {OSS_CRS_RUN_ENV_TYPE}"
        )


def main():
    crs_utils = init_crs_utils()
    parser = argparse.ArgumentParser(
        prog="libCRS", description="libCRS - CRS utilities"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # =========================================================================
    # Build output commands
    # =========================================================================

    # submit-build-output command
    submit_build_parser = subparsers.add_parser(
        "submit-build-output", help="Submit build output from src_path to dst_path"
    )
    submit_build_parser.add_argument("src_path", help="Source path in docker container")
    submit_build_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    submit_build_parser.set_defaults(
        func=lambda args: crs_utils.submit_build_output(args.src_path, args.dst_path)
    )

    # skip-build-output command
    skip_parser = subparsers.add_parser(
        "skip-build-output",
        help="Skip build output for dst_path on build output file system",
    )
    skip_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    skip_parser.set_defaults(
        func=lambda args: crs_utils.skip_build_output(args.dst_path)
    )

    # download-build-output command
    download_build_parser = subparsers.add_parser(
        "download-build-output",
        help="Download build output from src_path (on build output filesystem) to dst_path (in docker container)",
    )
    download_build_parser.add_argument(
        "src_path", help="Source path on build output file system"
    )
    download_build_parser.add_argument(
        "dst_path", help="Destination path in docker container"
    )
    download_build_parser.set_defaults(
        func=lambda args: crs_utils.download_build_output(args.src_path, args.dst_path)
    )

    # =========================================================================
    # Data registration commands (auto-sync directories)
    # =========================================================================

    # register-submit-dir command (auto-submit data to oss-crs-infra)
    register_submit_dir_parser = subparsers.add_parser(
        "register-submit-dir",
        help="Register a directory for automatic submission to oss-crs-infra",
    )
    register_submit_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=list(DataType),
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate",
    )
    register_submit_dir_parser.add_argument(
        "path", type=Path, help="Directory path to register"
    )
    register_submit_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_submit_dir(args.type, args.path)
    )

    # register-fetch-dir command (auto-fetch shared data from other CRS)
    register_fetch_dir_parser = subparsers.add_parser(
        "register-fetch-dir",
        help="Register a directory to automatically fetch shared data from other CRS",
    )
    register_fetch_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=list(DataType),
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate",
    )
    register_fetch_dir_parser.add_argument(
        "path", type=Path, help="Directory path to receive shared data"
    )
    register_fetch_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_fetch_dir(args.type, args.path)
    )

    # =========================================================================
    # Manual data operations
    # =========================================================================

    # submit command (manually submit a single file)
    submit_parser = subparsers.add_parser(
        "submit",
        help="Submit a single file to oss-crs-infra",
    )
    submit_parser.add_argument(
        "type",
        type=DataType,
        choices=list(DataType),
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate",
    )
    submit_parser.add_argument("path", type=Path, help="File path to submit")
    submit_parser.set_defaults(func=lambda args: crs_utils.submit(args.type, args.path))

    # fetch command (manually fetch shared data)
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch shared data from other CRS to a directory",
    )
    fetch_parser.add_argument(
        "type",
        type=DataType,
        choices=list(DataType),
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate",
    )
    fetch_parser.add_argument("path", type=Path, help="Output directory path")
    fetch_parser.set_defaults(
        func=lambda args: print("\n".join(crs_utils.fetch(args.type, args.path)))
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
