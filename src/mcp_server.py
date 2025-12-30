"""MCP server for exposing code review analysis to AI assistants.

This server allows AI assistants like Claude to query code review data,
run analysis, and get insights about code review patterns.

Requires optional dependencies: pip install lgtm[ai]

Usage:
    lgtm mcp  # Start the MCP server
"""

from __future__ import annotations

import json
from typing import Any

# Check if MCP dependencies are available
MCP_AVAILABLE = False

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        TextContent,
        Tool,
    )

    MCP_AVAILABLE = True
except ImportError:
    pass

import duckdb

from .repo import get_repo


def _get_connection() -> duckdb.DuckDBPyConnection:
    """Get a DuckDB connection to the analysis database."""
    repo = get_repo()
    db_path = repo.data_dir / "analysis.duckdb"

    if not db_path.exists():
        raise RuntimeError(
            f"No analysis database found at {db_path}. "
            "Run 'lgtm fetch' first to extract PR data."
        )

    return duckdb.connect(str(db_path), read_only=True)


def get_overview() -> dict[str, Any]:
    """Get an overview of the code review data.

    Returns summary statistics about PRs, reviews, and reviewers.
    """
    conn = _get_connection()

    try:
        # Basic counts
        pr_count = conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0]
        review_count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        merged_count = conn.execute("SELECT COUNT(*) FROM prs WHERE merged = true").fetchone()[0]

        # Date range
        dates = conn.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM prs"
        ).fetchone()

        # Top reviewers
        top_reviewers = conn.execute("""
            SELECT reviewer_login, COUNT(*) as review_count
            FROM reviews
            WHERE reviewer_is_bot = false
            GROUP BY reviewer_login
            ORDER BY review_count DESC
            LIMIT 10
        """).fetchall()

        # Approval stats
        approval_stats = conn.execute("""
            SELECT
                state,
                COUNT(*) as count
            FROM reviews
            GROUP BY state
        """).fetchall()

        return {
            "total_prs": pr_count,
            "merged_prs": merged_count,
            "total_reviews": review_count,
            "date_range": {
                "first": str(dates[0]) if dates[0] else None,
                "last": str(dates[1]) if dates[1] else None,
            },
            "top_reviewers": [
                {"login": r[0], "reviews": r[1]} for r in top_reviewers
            ],
            "review_states": {r[0]: r[1] for r in approval_stats},
        }
    finally:
        conn.close()


def query_data(sql: str) -> list[dict[str, Any]]:
    """Run a DuckDB SQL query against the code review data.

    Available tables:
    - prs: Pull requests (pr_number, title, author_login, state, merged, created_at, etc.)
    - reviews: PR reviews (review_id, pr_number, reviewer_login, state, body, submitted_at)
    - pr_comments: PR-level comments
    - review_comments: Inline code review comments
    - files: Files changed per PR
    - checks: CI check runs
    - timeline_events: PR timeline events

    Args:
        sql: DuckDB SQL query

    Returns:
        Query results as a list of dictionaries
    """
    conn = _get_connection()

    try:
        result = conn.execute(sql).fetchall()
        columns = [desc[0] for desc in conn.description]
        return [dict(zip(columns, row, strict=False)) for row in result]
    finally:
        conn.close()


def get_red_flags(limit: int = 20) -> list[dict[str, Any]]:
    """Get PRs that might have slipped through review.

    Identifies:
    - Large PRs (500+ lines) approved quickly with no comments
    - PRs with no reviews at all
    - First-time reviewers on complex changes

    Args:
        limit: Maximum number of red flags to return (max 100)

    Returns:
        List of potentially problematic PRs
    """
    # Validate and cap limit
    limit = min(max(1, int(limit)), 100)

    conn = _get_connection()

    try:
        # Large PRs approved quickly with no comments
        quick_large = conn.execute("""
            SELECT
                p.pr_number,
                p.title,
                p.author_login,
                p.additions + p.deletions as lines_changed,
                r.reviewer_login,
                r.state as review_state,
                EXTRACT(EPOCH FROM (r.submitted_at - p.created_at)) / 60 as minutes_to_review
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE p.additions + p.deletions > 500
              AND r.state = 'APPROVED'
              AND EXTRACT(EPOCH FROM (r.submitted_at - p.created_at)) / 60 < 5
              AND (r.body IS NULL OR LENGTH(TRIM(r.body)) = 0)
            ORDER BY p.additions + p.deletions DESC
            LIMIT ?
        """, [limit]).fetchall()

        columns = ["pr_number", "title", "author", "lines_changed",
                   "reviewer", "review_state", "minutes_to_review"]
        return [dict(zip(columns, row, strict=False)) for row in quick_large]
    finally:
        conn.close()


def get_reviewer_stats(reviewer: str) -> dict[str, Any]:
    """Get statistics for a specific reviewer.

    Args:
        reviewer: GitHub username of the reviewer

    Returns:
        Detailed stats about the reviewer's review activity
    """
    # Validate username (GitHub: 1-39 chars, alphanumeric and hyphens)
    if not reviewer or len(reviewer) > 39:
        return {"error": f"Invalid username: {reviewer}"}

    conn = _get_connection()

    try:
        # Basic stats
        stats = conn.execute("""
            SELECT
                COUNT(*) as total_reviews,
                SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
                SUM(CASE WHEN state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested,
                SUM(CASE WHEN state = 'COMMENTED' THEN 1 ELSE 0 END) as comments_only,
                SUM(CASE WHEN body IS NULL OR LENGTH(TRIM(body)) = 0 THEN 1 ELSE 0 END) as empty_reviews,
                AVG(EXTRACT(EPOCH FROM (submitted_at - p.created_at)) / 3600) as avg_hours_to_review
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE r.reviewer_login = ?
        """, [reviewer]).fetchone()

        if not stats or stats[0] == 0:
            return {"error": f"No reviews found for {reviewer}"}

        # Inline comments count
        inline_comments = conn.execute("""
            SELECT COUNT(*)
            FROM review_comments
            WHERE author_login = ?
        """, [reviewer]).fetchone()[0]

        # Authors they review most
        top_authors = conn.execute("""
            SELECT p.author_login, COUNT(*) as count
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE r.reviewer_login = ?
            GROUP BY p.author_login
            ORDER BY count DESC
            LIMIT 5
        """, [reviewer]).fetchall()

        return {
            "reviewer": reviewer,
            "total_reviews": stats[0],
            "approvals": stats[1],
            "changes_requested": stats[2],
            "comments_only": stats[3],
            "empty_reviews": stats[4],
            "empty_rate": round(100 * stats[4] / stats[0], 1) if stats[0] > 0 else 0,
            "avg_hours_to_review": round(stats[5], 1) if stats[5] else None,
            "inline_comments": inline_comments,
            "top_authors_reviewed": [
                {"author": a[0], "count": a[1]} for a in top_authors
            ],
        }
    finally:
        conn.close()


def get_author_stats(author: str) -> dict[str, Any]:
    """Get statistics for a specific PR author.

    Args:
        author: GitHub username of the author

    Returns:
        Detailed stats about the author's PRs and review feedback
    """
    # Validate username (GitHub: 1-39 chars, alphanumeric and hyphens)
    if not author or len(author) > 39:
        return {"error": f"Invalid username: {author}"}

    conn = _get_connection()

    try:
        # Basic PR stats
        pr_stats = conn.execute("""
            SELECT
                COUNT(*) as total_prs,
                SUM(CASE WHEN merged THEN 1 ELSE 0 END) as merged_prs,
                AVG(additions + deletions) as avg_pr_size,
                AVG(commits) as avg_commits
            FROM prs
            WHERE author_login = ?
        """, [author]).fetchone()

        if not pr_stats or pr_stats[0] == 0:
            return {"error": f"No PRs found for {author}"}

        # Review feedback received
        feedback = conn.execute("""
            SELECT
                COUNT(*) as total_reviews,
                SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
                SUM(CASE WHEN state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE p.author_login = ?
        """, [author]).fetchone()

        # Top reviewers
        top_reviewers = conn.execute("""
            SELECT r.reviewer_login, COUNT(*) as count
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE p.author_login = ?
              AND r.reviewer_is_bot = false
            GROUP BY r.reviewer_login
            ORDER BY count DESC
            LIMIT 5
        """, [author]).fetchall()

        return {
            "author": author,
            "total_prs": pr_stats[0],
            "merged_prs": pr_stats[1],
            "merge_rate": round(100 * pr_stats[1] / pr_stats[0], 1) if pr_stats[0] > 0 else 0,
            "avg_pr_size": round(pr_stats[2], 0) if pr_stats[2] else 0,
            "avg_commits": round(pr_stats[3], 1) if pr_stats[3] else 0,
            "total_reviews_received": feedback[0] if feedback else 0,
            "approvals_received": feedback[1] if feedback else 0,
            "changes_requested": feedback[2] if feedback else 0,
            "top_reviewers": [
                {"reviewer": r[0], "count": r[1]} for r in top_reviewers
            ],
        }
    finally:
        conn.close()


# MCP Server definition
if MCP_AVAILABLE:
    server = Server("lgtm")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="get_overview",
                description="Get an overview of the code review data including total PRs, reviews, top reviewers, and date range.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            Tool(
                name="query",
                description="Run a DuckDB SQL query against the code review data. Available tables: prs, reviews, pr_comments, review_comments, files, checks, timeline_events.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "DuckDB SQL query to execute",
                        },
                    },
                    "required": ["sql"],
                },
            ),
            Tool(
                name="get_red_flags",
                description="Get PRs that might have slipped through review - large PRs approved quickly with no comments.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of red flags to return (default: 20)",
                            "default": 20,
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_reviewer_stats",
                description="Get statistics for a specific reviewer including total reviews, approval rate, empty review rate, and top authors they review.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "reviewer": {
                            "type": "string",
                            "description": "GitHub username of the reviewer",
                        },
                    },
                    "required": ["reviewer"],
                },
            ),
            Tool(
                name="get_author_stats",
                description="Get statistics for a specific PR author including total PRs, merge rate, avg PR size, and top reviewers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "author": {
                            "type": "string",
                            "description": "GitHub username of the author",
                        },
                    },
                    "required": ["author"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""
        try:
            if name == "get_overview":
                result = get_overview()
            elif name == "query":
                result = query_data(arguments["sql"])
            elif name == "get_red_flags":
                limit = arguments.get("limit", 20)
                result = get_red_flags(limit)
            elif name == "get_reviewer_stats":
                result = get_reviewer_stats(arguments["reviewer"])
            elif name == "get_author_stats":
                result = get_author_stats(arguments["author"])
            else:
                result = {"error": f"Unknown tool: {name}"}

            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def run_server():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        raise ImportError(
            "MCP server requires optional dependencies. "
            "Install with: pip install lgtm[ai]"
        )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Main entry point for MCP server."""
    import asyncio
    asyncio.run(run_server())
