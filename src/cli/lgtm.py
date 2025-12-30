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

    # fetch command - pull PR data from GitHub
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch PR and review data from GitHub",
        description="Pull PR data from GitHub API and save to ~/.cache/lgtm/{owner}/{repo}/",
    )
    fetch_parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=None,
        help="Limit number of PRs to fetch",
    )
    fetch_parser.add_argument(
        "--refresh-days",
        "-r",
        type=int,
        default=None,
        help="Re-fetch PRs from the last N days (updates existing data)",
    )
    fetch_parser.add_argument(
        "--since",
        "-s",
        type=str,
        default=None,
        help="Fetch PRs created after this date (ISO format: YYYY-MM-DD). Overrides lgtm.yaml config.",
    )
    fetch_parser.add_argument(
        "--full",
        "-f",
        action="store_true",
        help="Full fetch from start_date (ignore incremental mode)",
    )

    # analyze command - run analysis queries (raw tables)
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run all analysis queries (raw table output)",
        description="Run all analysis queries and print raw DuckDB tables.",
    )
    analyze_parser.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Run specific query (default: run all)",
    )

    # report command - narrative report
    report_parser = subparsers.add_parser(
        "report",
        help="Generate narrative report (recommended)",
        description="Generate a narrative report answering: Is code review adding value?",
    )
    report_parser.add_argument(
        "--format",
        "-f",
        choices=["terminal", "pdf"],
        default="terminal",
        help="Output format (default: terminal)",
    )
    report_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (for PDF format, default: ~/.cache/lgtm/{repo}/report.pdf)",
    )

    # mcp command - start MCP server for AI assistants
    subparsers.add_parser(
        "mcp",
        help="Start MCP server for AI assistants (requires lgtm[ai])",
        description="Start an MCP (Model Context Protocol) server that allows AI assistants to query code review data.",
    )

    # chat command - interactive AI chat
    chat_parser = subparsers.add_parser(
        "chat",
        help="Interactive AI chat for exploring code review patterns (requires lgtm[ai])",
        description="Start an interactive chat session to explore code review patterns using AI.",
    )
    chat_parser.add_argument(
        "--model",
        "-m",
        type=str,
        default="claude-sonnet-4-20250514",
        help="LLM model to use (e.g., claude-sonnet-4-20250514, gpt-4o, gemini/gemini-1.5-pro)",
    )

    # ask command - one-shot query
    ask_parser = subparsers.add_parser(
        "ask",
        help="Ask a single question about code review patterns (requires lgtm[ai])",
        description="Ask a question and get an answer without entering interactive mode.",
    )
    ask_parser.add_argument(
        "question",
        type=str,
        help="Question to ask about code review patterns",
    )
    ask_parser.add_argument(
        "--model",
        "-m",
        type=str,
        default="claude-sonnet-4-20250514",
        help="LLM model to use",
    )

    args = parser.parse_args()

    if args.command == "init":
        output = args.output or args.root / "lgtm.yaml"
        init_config(args.root, output)

    elif args.command == "fetch":
        # Import here to avoid circular imports and slow startup
        import trio

        from ..main import main as fetch_main

        trio.run(fetch_main, args.limit, args.refresh_days, args.since, args.full)

    elif args.command == "analyze":
        from ..analyze import main as analyze_main

        analyze_main()

    elif args.command == "report":
        from ..report import main as report_main

        report_main(format=args.format, output=args.output)

    elif args.command == "mcp":
        from ..mcp_server import main as mcp_main

        mcp_main()

    elif args.command == "chat":
        from ..chat.tui import main as chat_main

        chat_main(model=args.model)

    elif args.command == "ask":
        from ..chat.agent import LGTMAgent

        try:
            agent = LGTMAgent(model=args.model)
            response = agent.chat(args.question)
            print(response)
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command is None:
        parser.print_help()
        sys.exit(0)

    else:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
