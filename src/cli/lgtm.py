"""Main CLI entry point for lgtm - unified code review analysis tool."""

import argparse
import sys
from pathlib import Path

from .init_config import init_config


def main():
    """Main CLI entry point for lgtm."""
    parser = argparse.ArgumentParser(
        prog="lgtm",
        description="Code review quality analysis tool",
        epilog="Run 'lgtm <command> --help' for more information on a command.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command - generate config
    init_parser = subparsers.add_parser(
        "init",
        help="Generate lgtm.yaml config from workspace definitions",
        description="Auto-detect package manager workspaces and generate module config.",
    )
    init_parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root directory (default: current directory)",
    )
    init_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (default: lgtm.yaml in root)",
    )

    # extract command - pull PR data from GitHub
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract PR and review data from GitHub",
        description="Pull PR data from GitHub API and save to parquet files.",
    )
    extract_parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD format, default: Jan 1 current year)",
    )
    extract_parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Checkpoint file path for resuming",
    )
    extract_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw"),
        help="Output directory for parquet files",
    )

    # analyze command - run analysis queries
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run analysis queries on extracted data",
        description="Analyze code review patterns from extracted data.",
    )
    analyze_parser.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Run specific query (default: run all)",
    )
    analyze_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory containing parquet files",
    )

    args = parser.parse_args()

    if args.command == "init":
        output = args.output or args.root / "lgtm.yaml"
        init_config(args.root, output)

    elif args.command == "extract":
        # Import here to avoid circular imports and slow startup
        from ..main import cli as extract_cli

        # Pass args to extract CLI
        extract_cli()

    elif args.command == "analyze":
        from ..analyze import main as analyze_main

        analyze_main()

    elif args.command is None:
        parser.print_help()
        sys.exit(0)

    else:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
