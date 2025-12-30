"""Narrative report generator for code review analysis.

Instead of dumping tables, this tells a story:
"Is code review adding value, or is it just a rubber stamp?"
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import duckdb
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyze import get_connection


console = Console()


def format_pct(value: float | None) -> str:
    """Format percentage with no decimal if whole number."""
    if value is None:
        return "N/A"
    if value == int(value):
        return f"{int(value)}%"
    return f"{value:.1f}%"


def format_hours(value: float | None) -> str:
    """Format hours in human-readable way."""
    if value is None:
        return "N/A"
    if value < 1:
        return f"{int(value * 60)} min"
    if value < 24:
        return f"{value:.1f} hrs"
    return f"{value / 24:.1f} days"


def format_minutes(value: float | None) -> str:
    """Format minutes."""
    if value is None:
        return "N/A"
    if value < 1:
        return "<1 min"
    return f"{int(value)} min"


def get_repo_name() -> str:
    """Get repo name from environment or default."""
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    if owner and name:
        return f"{owner}/{name}"
    return "your-repo"


# ============================================================================
# Data Fetching
# ============================================================================


def get_summary_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """Get high-level summary statistics."""
    result = con.execute("""
        SELECT
            COUNT(*) as total_prs,
            MIN(created_at) as first_pr,
            MAX(created_at) as last_pr,
            COUNT(*) FILTER (WHERE merged) as merged_prs
        FROM prs
    """).fetchone()

    if not result:
        return {}

    return {
        "total_prs": result[0],
        "first_pr": result[1],
        "last_pr": result[2],
        "merged_prs": result[3],
    }


def get_approval_context(con: duckdb.DuckDBPyConnection) -> dict:
    """Get approval stats broken down by reviewer context."""
    result = con.execute("""
        WITH approval_context AS (
            SELECT
                r.pr_number,
                p.author_login as author,
                r.reviewer_login as reviewer,
                r.body,
                r.submitted_at,
                f.computed_module as module
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            JOIN files f ON r.pr_number = f.pr_number
            WHERE r.state = 'APPROVED'
              AND NOT r.reviewer_is_bot
              AND NOT p.author_is_bot
              AND NOT f.is_gen
        ),
        with_history AS (
            SELECT DISTINCT ON (ac.pr_number, ac.reviewer)
                ac.pr_number,
                ac.reviewer,
                ac.module,
                ac.body,
                -- Prior reviews of this author's code
                (
                    SELECT COUNT(DISTINCT r2.pr_number)
                    FROM reviews r2
                    JOIN prs p2 ON r2.pr_number = p2.pr_number
                    WHERE r2.reviewer_login = ac.reviewer
                      AND p2.author_login = ac.author
                      AND r2.submitted_at < ac.submitted_at
                ) as prior_author_reviews,
                -- Prior reviews in this module
                (
                    SELECT COUNT(DISTINCT r2.pr_number)
                    FROM reviews r2
                    JOIN files f2 ON r2.pr_number = f2.pr_number
                    WHERE r2.reviewer_login = ac.reviewer
                      AND f2.computed_module = ac.module
                      AND r2.submitted_at < ac.submitted_at
                ) as prior_module_reviews
            FROM approval_context ac
        )
        SELECT
            -- Total approvals
            COUNT(*) as total_approvals,
            SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) as empty_approvals,

            -- Module experts (10+ reviews in that module)
            SUM(CASE WHEN prior_module_reviews >= 10 THEN 1 ELSE 0 END) as expert_approvals,
            SUM(CASE WHEN prior_module_reviews >= 10 AND (body IS NULL OR TRIM(body) = '') THEN 1 ELSE 0 END) as expert_empty,

            -- Familiar with author (10+ reviews of their code)
            SUM(CASE WHEN prior_author_reviews >= 10 AND prior_module_reviews < 10 THEN 1 ELSE 0 END) as familiar_approvals,
            SUM(CASE WHEN prior_author_reviews >= 10 AND prior_module_reviews < 10 AND (body IS NULL OR TRIM(body) = '') THEN 1 ELSE 0 END) as familiar_empty,

            -- First-time reviewers (<3 reviews of author)
            SUM(CASE WHEN prior_author_reviews < 3 THEN 1 ELSE 0 END) as firsttime_approvals,
            SUM(CASE WHEN prior_author_reviews < 3 AND (body IS NULL OR TRIM(body) = '') THEN 1 ELSE 0 END) as firsttime_empty
        FROM with_history
    """).fetchone()

    if not result:
        return {}

    return {
        "total_approvals": result[0] or 0,
        "empty_approvals": result[1] or 0,
        "expert_approvals": result[2] or 0,
        "expert_empty": result[3] or 0,
        "familiar_approvals": result[4] or 0,
        "familiar_empty": result[5] or 0,
        "firsttime_approvals": result[6] or 0,
        "firsttime_empty": result[7] or 0,
    }


def get_quick_large_approvals(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get large PRs approved suspiciously quickly with no comments."""
    results = con.execute("""
        WITH first_approval AS (
            SELECT
                r.pr_number,
                r.reviewer_login,
                MIN(r.submitted_at) as approved_at
            FROM reviews r
            WHERE r.state = 'APPROVED' AND NOT r.reviewer_is_bot
            GROUP BY 1, 2
        ),
        pr_stats AS (
            SELECT
                p.pr_number,
                p.author_login,
                SUM(CASE WHEN NOT f.is_gen THEN f.additions + f.deletions ELSE 0 END) as code_lines,
                COUNT(DISTINCT rc.comment_id) as inline_comments
            FROM prs p
            JOIN files f ON p.pr_number = f.pr_number
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number AND NOT rc.author_is_bot
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1, 2
        )
        SELECT
            ps.pr_number,
            ps.author_login,
            ps.code_lines,
            ROUND(EXTRACT(EPOCH FROM (fa.approved_at - p.created_at)) / 60, 0) as minutes_to_approve,
            fa.reviewer_login
        FROM pr_stats ps
        JOIN prs p ON ps.pr_number = p.pr_number
        JOIN first_approval fa ON ps.pr_number = fa.pr_number
        WHERE ps.code_lines >= 500
          AND EXTRACT(EPOCH FROM (fa.approved_at - p.created_at)) / 60 < 5
          AND ps.inline_comments = 0
        ORDER BY ps.code_lines DESC
        LIMIT 10
    """).fetchall()

    return [
        {
            "pr_number": r[0],
            "author": r[1],
            "lines": r[2],
            "minutes": r[3],
            "reviewer": r[4],
        }
        for r in results
    ]


def get_review_depth_by_type(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get review depth broken down by PR type."""
    results = con.execute("""
        WITH pr_churn AS (
            SELECT
                pr_number,
                SUM(CASE WHEN NOT is_gen THEN additions ELSE 0 END) as code_adds,
                SUM(CASE WHEN NOT is_gen THEN deletions ELSE 0 END) as code_dels
            FROM files
            GROUP BY 1
        ),
        pr_types AS (
            SELECT
                pr_number,
                code_adds + code_dels as total_churn,
                CASE
                    WHEN code_adds + code_dels = 0 THEN 'generated-only'
                    WHEN code_adds + code_dels >= 500 THEN 'large-change'
                    WHEN 1.0 * code_adds / (code_adds + code_dels) > 0.85 THEN 'new-code'
                    WHEN 1.0 * code_adds / (code_adds + code_dels) < 0.15 THEN 'cleanup'
                    ELSE 'refactor'
                END as pr_type
            FROM pr_churn
        ),
        review_stats AS (
            SELECT
                p.pr_number,
                pt.pr_type,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments,
                BOOL_OR(r.state = 'CHANGES_REQUESTED') as had_feedback
            FROM prs p
            JOIN pr_types pt ON p.pr_number = pt.pr_number
            LEFT JOIN reviews r ON p.pr_number = r.pr_number AND NOT r.reviewer_is_bot
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1, 2
        )
        SELECT
            pr_type,
            COUNT(*) as prs,
            ROUND(AVG(inline_comments), 1) as avg_comments,
            ROUND(100.0 * SUM(CASE WHEN inline_comments > 0 OR had_feedback THEN 1 ELSE 0 END) / COUNT(*), 0) as pct_with_feedback
        FROM review_stats
        WHERE pr_type != 'generated-only'
        GROUP BY 1
        ORDER BY
            CASE pr_type
                WHEN 'large-change' THEN 1
                WHEN 'new-code' THEN 2
                WHEN 'refactor' THEN 3
                WHEN 'cleanup' THEN 4
                ELSE 5
            END
    """).fetchall()

    return [
        {
            "type": r[0],
            "prs": r[1],
            "avg_comments": r[2],
            "pct_feedback": r[3],
        }
        for r in results
    ]


def get_module_reviewers(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get top reviewers per module with ownership stats."""
    results = con.execute("""
        WITH module_reviews AS (
            SELECT
                f.computed_module as module,
                r.reviewer_login,
                COUNT(DISTINCT r.pr_number) as prs_reviewed
            FROM reviews r
            JOIN files f ON r.pr_number = f.pr_number
            WHERE NOT r.reviewer_is_bot AND NOT f.is_gen
            GROUP BY 1, 2
        ),
        module_totals AS (
            SELECT
                module,
                SUM(prs_reviewed) as total_prs,
                COUNT(DISTINCT reviewer_login) as num_reviewers
            FROM module_reviews
            GROUP BY 1
            HAVING SUM(prs_reviewed) >= 20
        ),
        top_reviewers AS (
            SELECT
                mr.module,
                mr.reviewer_login,
                mr.prs_reviewed,
                ROUND(100.0 * mr.prs_reviewed / mt.total_prs, 0) as share_pct,
                mt.num_reviewers,
                ROW_NUMBER() OVER (PARTITION BY mr.module ORDER BY mr.prs_reviewed DESC) as rank
            FROM module_reviews mr
            JOIN module_totals mt ON mr.module = mt.module
        )
        SELECT
            module,
            reviewer_login,
            prs_reviewed,
            share_pct,
            num_reviewers,
            rank
        FROM top_reviewers
        WHERE rank <= 2
        ORDER BY module, rank
    """).fetchall()

    # Group by module
    modules = {}
    for r in results:
        module = r[0]
        if module not in modules:
            modules[module] = {
                "module": module,
                "num_reviewers": r[4],
                "reviewers": [],
            }
        modules[module]["reviewers"].append({
            "login": r[1],
            "prs": r[2],
            "share": r[3],
        })

    return list(modules.values())


def get_red_flags(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get specific PRs that might have slipped through."""
    # PRs with:
    # - Large code changes (500+)
    # - Quick approval (<5 min)
    # - Reviewer who hasn't reviewed this author much
    # - No inline comments
    results = con.execute("""
        WITH approval_context AS (
            SELECT
                r.pr_number,
                r.reviewer_login,
                r.submitted_at as approved_at,
                p.author_login,
                p.created_at as pr_created,
                (
                    SELECT COUNT(DISTINCT r2.pr_number)
                    FROM reviews r2
                    JOIN prs p2 ON r2.pr_number = p2.pr_number
                    WHERE r2.reviewer_login = r.reviewer_login
                      AND p2.author_login = p.author_login
                      AND r2.submitted_at < r.submitted_at
                ) as prior_reviews_of_author
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE r.state = 'APPROVED'
              AND NOT r.reviewer_is_bot
              AND NOT p.author_is_bot
              AND p.merged
        ),
        pr_stats AS (
            SELECT
                p.pr_number,
                SUM(CASE WHEN NOT f.is_gen THEN f.additions + f.deletions ELSE 0 END) as code_lines,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments
            FROM prs p
            JOIN files f ON p.pr_number = f.pr_number
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged
            GROUP BY 1
        )
        SELECT
            ac.pr_number,
            ac.author_login,
            ps.code_lines,
            ROUND(EXTRACT(EPOCH FROM (ac.approved_at - ac.pr_created)) / 60, 0) as minutes,
            CASE
                WHEN ac.prior_reviews_of_author = 0 THEN 'Never reviewed this author'
                WHEN ac.prior_reviews_of_author < 3 THEN 'First-time reviewer'
                ELSE 'Some history'
            END as context
        FROM approval_context ac
        JOIN pr_stats ps ON ac.pr_number = ps.pr_number
        WHERE ps.code_lines >= 400
          AND EXTRACT(EPOCH FROM (ac.approved_at - ac.pr_created)) / 60 < 10
          AND ps.inline_comments = 0
          AND ac.prior_reviews_of_author < 3
        ORDER BY ps.code_lines DESC
        LIMIT 10
    """).fetchall()

    return [
        {
            "pr_number": r[0],
            "author": r[1],
            "lines": r[2],
            "minutes": r[3],
            "context": r[4],
        }
        for r in results
    ]


# ============================================================================
# Report Sections
# ============================================================================


def print_header(stats: dict) -> None:
    """Print the report header."""
    repo = get_repo_name()
    total = stats.get("total_prs", 0)
    first = stats.get("first_pr")
    last = stats.get("last_pr")

    year_str = ""
    if first and last:
        if first.year == last.year:
            year_str = str(first.year)
        else:
            year_str = f"{first.year}-{last.year}"

    subtitle = f"{repo} | {year_str} | {total:,} PRs"

    panel = Panel(
        Text(subtitle, justify="center"),
        title="[bold]Is Code Review Adding Value?[/bold]",
        border_style="blue",
        padding=(1, 2),
    )
    console.print(panel)


def print_short_answer(approval_ctx: dict, quick_large: list[dict]) -> None:
    """Print the short answer section."""
    console.print("\n[bold]## The Short Answer[/bold]\n")

    total = approval_ctx.get("total_approvals", 0)
    empty = approval_ctx.get("empty_approvals", 0)
    empty_pct = 100.0 * empty / total if total else 0

    console.print(f"[bold]{format_pct(empty_pct)}[/bold] of approvals have no comment or feedback. But context matters:")
    console.print()

    # Expert context
    expert_total = approval_ctx.get("expert_approvals", 0)
    expert_empty = approval_ctx.get("expert_empty", 0)
    if expert_total > 0:
        expert_pct = 100.0 * expert_empty / expert_total
        console.print(f"  [green]\u2022[/green] From module experts: {format_pct(expert_pct)} empty (probably fine - they know the code)")

    # Familiar context
    familiar_total = approval_ctx.get("familiar_approvals", 0)
    familiar_empty = approval_ctx.get("familiar_empty", 0)
    if familiar_total > 0:
        familiar_pct = 100.0 * familiar_empty / familiar_total
        console.print(f"  [green]\u2022[/green] From familiar reviewers: {format_pct(familiar_pct)} empty (know the author's work)")

    # First-time context
    firsttime_total = approval_ctx.get("firsttime_approvals", 0)
    firsttime_empty = approval_ctx.get("firsttime_empty", 0)
    if firsttime_total > 0:
        firsttime_pct = 100.0 * firsttime_empty / firsttime_total
        if firsttime_pct > 30:
            console.print(f"  [yellow]\u2022[/yellow] From first-time reviewers: {format_pct(firsttime_pct)} empty [yellow](worth checking)[/yellow]")
        else:
            console.print(f"  [green]\u2022[/green] From first-time reviewers: {format_pct(firsttime_pct)} empty")

    # Quick large approvals
    if quick_large:
        console.print()
        console.print(f"[bold red]{len(quick_large)} large PRs (500+ lines) were approved in under 5 minutes with no comments.[/bold red]")


def print_review_depth(depth_data: list[dict]) -> None:
    """Print review depth by PR type."""
    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold]## Review Depth by Risk[/bold]")
    console.print("[dim]Are we spending effort where it matters?[/dim]\n")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("PR Type")
    table.add_column("Count", justify="right")
    table.add_column("Avg Comments", justify="right")
    table.add_column("% With Feedback", justify="right")
    table.add_column("Concern?")

    type_labels = {
        "large-change": "Large (500+)",
        "new-code": "New code",
        "refactor": "Refactors",
        "cleanup": "Cleanup/deletions",
    }

    for row in depth_data:
        pr_type = type_labels.get(row["type"], row["type"])
        pct = row["pct_feedback"] or 0

        # Determine concern level
        if row["type"] == "large-change":
            concern = "[green]\u2713 Good[/green]" if pct >= 60 else "[red]\u26a0 Risk[/red]"
        elif row["type"] == "new-code":
            concern = "[green]\u2713 Good[/green]" if pct >= 50 else "[yellow]Check[/yellow]"
        elif row["type"] == "refactor":
            concern = "[green]OK[/green]" if pct >= 20 else "[yellow]Maybe OK[/yellow]"
        else:
            concern = "[green]\u2713 Expected[/green]"

        table.add_row(
            pr_type,
            f"{row['prs']:,}",
            f"{row['avg_comments']:.1f}",
            format_pct(pct),
            concern,
        )

    console.print(table)


def print_module_ownership(module_data: list[dict]) -> None:
    """Print module ownership analysis."""
    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold]## Who's Actually Reviewing?[/bold]")
    console.print("[dim]Are experts reviewing their areas?[/dim]\n")

    # Sort modules by total activity (sum of top reviewers' prs)
    sorted_modules = sorted(
        module_data,
        key=lambda m: sum(r["prs"] for r in m["reviewers"]),
        reverse=True,
    )[:10]  # Top 10 modules

    for mod in sorted_modules:
        module_name = mod["module"]
        num_reviewers = mod["num_reviewers"]
        reviewers = mod["reviewers"]

        # Build reviewer string
        reviewer_parts = []
        for r in reviewers[:2]:
            reviewer_parts.append(f"{r['login']} {int(r['share'])}%")
        reviewer_str = ", ".join(reviewer_parts)

        # Determine ownership quality
        top_share = reviewers[0]["share"] if reviewers else 0
        if num_reviewers <= 3 and top_share >= 30:
            status = "[green]\u2713[/green]"
            note = "concentrated expertise"
        elif num_reviewers > 10 and top_share < 15:
            status = "[yellow]\u26a0[/yellow]"
            note = "scattered, no ownership"
        else:
            status = "[dim]\u2022[/dim]"
            note = ""

        line = f"  [bold]{module_name}:[/bold] {reviewer_str}"
        if note:
            line += f" \u2192 {note} {status}"

        console.print(line)


def print_red_flags(flags: list[dict]) -> None:
    """Print specific PRs that might have slipped through."""
    if not flags:
        return

    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold]## Red Flags[/bold]")
    console.print("[dim]PRs that might have slipped through:[/dim]\n")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("PR#", justify="right")
    table.add_column("Author")
    table.add_column("Lines", justify="right")
    table.add_column("Time to Approve")
    table.add_column("Reviewer Context")

    for flag in flags:
        table.add_row(
            f"#{flag['pr_number']}",
            flag["author"],
            f"{flag['lines']:,}",
            format_minutes(flag["minutes"]),
            f"[yellow]{flag['context']}[/yellow]",
        )

    console.print(table)


# ============================================================================
# Main Report
# ============================================================================


def generate_report(con: duckdb.DuckDBPyConnection | None = None) -> None:
    """Generate the full narrative report."""
    if con is None:
        con = get_connection()

    # Gather all data
    stats = get_summary_stats(con)
    approval_ctx = get_approval_context(con)
    quick_large = get_quick_large_approvals(con)
    depth_data = get_review_depth_by_type(con)
    module_data = get_module_reviewers(con)
    red_flags = get_red_flags(con)

    # Print report
    console.print()
    print_header(stats)
    print_short_answer(approval_ctx, quick_large)
    print_review_depth(depth_data)
    print_module_ownership(module_data)
    print_red_flags(red_flags)
    console.print()


def main() -> None:
    """CLI entry point."""
    con = get_connection()
    generate_report(con)
    con.close()


if __name__ == "__main__":
    main()
