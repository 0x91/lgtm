"""Narrative report generator for code review analysis.

Instead of dumping tables, this tells a story:
"Is code review adding value, or is it just a rubber stamp?"
"""

from __future__ import annotations

import duckdb
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analyze import get_connection
from .repo import get_repo

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
    """Get repo name from detected repo."""
    try:
        return get_repo().full_name
    except ValueError:
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
        modules[module]["reviewers"].append(
            {
                "login": r[1],
                "prs": r[2],
                "share": r[3],
            }
        )

    return list(modules.values())


def get_thread_outcomes(con: duckdb.DuckDBPyConnection) -> dict:
    """Get thread resolution outcome stats.

    Based on Bosu et al. 2015: usefulness is best measured by
    whether feedback led to action.
    """
    result = con.execute("""
        WITH thread_stats AS (
            SELECT
                pr_number,
                path,
                -- Group comments into threads by path (simplified)
                COUNT(*) as comment_count,
                BOOL_OR(COALESCE(is_resolved, false)) as resolved,
                BOOL_OR(COALESCE(is_outdated, false)) as outdated,
                COUNT(DISTINCT author_login) as unique_authors
            FROM review_comments
            WHERE NOT author_is_bot
            GROUP BY pr_number, path
        )
        SELECT
            COUNT(*) as total_threads,
            SUM(CASE WHEN resolved THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN outdated AND NOT resolved THEN 1 ELSE 0 END) as outdated,
            SUM(CASE WHEN unique_authors > 1 THEN 1 ELSE 0 END) as discussed,
            SUM(CASE WHEN NOT resolved AND NOT outdated AND unique_authors = 1 THEN 1 ELSE 0 END) as standalone,
            AVG(comment_count) as avg_thread_depth,
            -- Check if we have any resolution data at all
            SUM(CASE WHEN resolved OR outdated THEN 1 ELSE 0 END) as any_resolution_data
        FROM thread_stats
    """).fetchone()

    if not result:
        return {}

    total = result[0] or 1
    resolved = result[1] or 0
    outdated = result[2] or 0
    has_resolution_data = (result[6] or 0) > 0

    return {
        "total_threads": result[0] or 0,
        "resolved": resolved,
        "outdated": outdated,
        "discussed": result[3] or 0,
        "standalone": result[4] or 0,
        "avg_depth": result[5] or 0,
        "addressed_rate": 100.0 * (resolved + outdated) / total if has_resolution_data else None,
        "has_resolution_data": has_resolution_data,
    }


def get_iteration_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """Get stats on whether PRs iterated after review feedback.

    If review leads to commits, it's adding value.
    """
    result = con.execute("""
        WITH review_timing AS (
            SELECT
                p.pr_number,
                MIN(r.submitted_at) as first_review,
                MAX(r.submitted_at) as last_review
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE p.merged AND NOT r.reviewer_is_bot
            GROUP BY p.pr_number
        ),
        commit_after_review AS (
            SELECT
                rt.pr_number,
                COUNT(DISTINCT te.created_at) FILTER (
                    WHERE te.event_type = 'committed'
                    AND te.created_at > rt.first_review
                ) as commits_after_review
            FROM review_timing rt
            LEFT JOIN timeline_events te ON rt.pr_number = te.pr_number
            GROUP BY rt.pr_number
        )
        SELECT
            COUNT(*) as total_prs,
            SUM(CASE WHEN commits_after_review > 0 THEN 1 ELSE 0 END) as prs_with_iteration,
            AVG(commits_after_review) as avg_commits_after
        FROM commit_after_review
    """).fetchone()

    if not result:
        return {}

    total = result[0] or 1
    return {
        "total_prs": result[0] or 0,
        "iterated": result[1] or 0,
        "avg_commits": result[2] or 0,
        "iteration_rate": 100.0 * (result[1] or 0) / total,
    }


def get_feedback_with_code(con: duckdb.DuckDBPyConnection) -> dict:
    """Get stats on comments with code suggestions."""
    result = con.execute("""
        SELECT
            COUNT(*) as total_comments,
            SUM(CASE WHEN body LIKE '%```%' THEN 1 ELSE 0 END) as with_code,
            SUM(CASE WHEN body LIKE '%http%' THEN 1 ELSE 0 END) as with_links
        FROM review_comments
        WHERE NOT author_is_bot
    """).fetchone()

    if not result:
        return {}

    total = result[0] or 1
    return {
        "total": result[0] or 0,
        "with_code": result[1] or 0,
        "with_links": result[2] or 0,
        "code_rate": 100.0 * (result[1] or 0) / total,
        "link_rate": 100.0 * (result[2] or 0) / total,
    }


def get_reviewer_file_experience(con: duckdb.DuckDBPyConnection) -> dict:
    """Get stats on whether reviewers have prior experience with files they're reviewing.

    Based on Bosu et al. 2015: "reviewers who had reviewed a file before were
    almost twice more useful (65%-71%) than first-time reviewers (32%-37%)"
    """
    result = con.execute("""
        WITH reviewer_file_pairs AS (
            -- For each review, get all files the reviewer is looking at
            SELECT DISTINCT
                r.pr_number,
                r.reviewer_login,
                r.submitted_at,
                f.filename
            FROM reviews r
            JOIN files f ON r.pr_number = f.pr_number
            WHERE NOT r.reviewer_is_bot
              AND r.state IN ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED')
              AND NOT f.is_gen
        ),
        file_experience AS (
            -- For each file in a review, check if reviewer has seen it before
            SELECT
                rfp.pr_number,
                rfp.reviewer_login,
                rfp.filename,
                EXISTS (
                    SELECT 1 FROM reviewer_file_pairs prior
                    WHERE prior.reviewer_login = rfp.reviewer_login
                      AND prior.filename = rfp.filename
                      AND prior.submitted_at < rfp.submitted_at
                ) as has_prior_experience
            FROM reviewer_file_pairs rfp
        ),
        review_experience AS (
            -- Aggregate to review level: % of files reviewer has seen before
            SELECT
                pr_number,
                reviewer_login,
                COUNT(*) as files_in_review,
                SUM(CASE WHEN has_prior_experience THEN 1 ELSE 0 END) as files_seen_before,
                1.0 * SUM(CASE WHEN has_prior_experience THEN 1 ELSE 0 END) / COUNT(*) as familiarity_pct
            FROM file_experience
            GROUP BY pr_number, reviewer_login
        )
        SELECT
            COUNT(*) as total_reviews,
            -- Reviews where reviewer has seen 0% of files before
            SUM(CASE WHEN familiarity_pct = 0 THEN 1 ELSE 0 END) as fully_unfamiliar,
            -- Reviews where reviewer has seen <25% of files
            SUM(CASE WHEN familiarity_pct < 0.25 THEN 1 ELSE 0 END) as mostly_unfamiliar,
            -- Reviews where reviewer has seen 75%+ of files
            SUM(CASE WHEN familiarity_pct >= 0.75 THEN 1 ELSE 0 END) as mostly_familiar,
            -- Reviews where reviewer has seen 100% of files
            SUM(CASE WHEN familiarity_pct = 1 THEN 1 ELSE 0 END) as fully_familiar,
            AVG(familiarity_pct) as avg_familiarity
        FROM review_experience
    """).fetchone()

    if not result:
        return {}

    total = result[0] or 1
    return {
        "total_reviews": result[0] or 0,
        "fully_unfamiliar": result[1] or 0,
        "mostly_unfamiliar": result[2] or 0,
        "mostly_familiar": result[3] or 0,
        "fully_familiar": result[4] or 0,
        "avg_familiarity": (result[5] or 0) * 100,
        "unfamiliar_rate": 100.0 * (result[1] or 0) / total,
        "familiar_rate": 100.0 * (result[3] or 0) / total,
    }


def get_first_time_file_reviews(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Get large PRs where reviewer has never seen any of the files before.

    This is a warning signal per Bosu et al. - first-time reviewers of an
    artifact are less useful.
    """
    results = con.execute("""
        WITH reviewer_file_pairs AS (
            SELECT DISTINCT
                r.pr_number,
                r.reviewer_login,
                r.submitted_at,
                f.filename
            FROM reviews r
            JOIN files f ON r.pr_number = f.pr_number
            WHERE NOT r.reviewer_is_bot
              AND r.state IN ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED')
              AND NOT f.is_gen
        ),
        file_experience AS (
            SELECT
                rfp.pr_number,
                rfp.reviewer_login,
                rfp.filename,
                EXISTS (
                    SELECT 1 FROM reviewer_file_pairs prior
                    WHERE prior.reviewer_login = rfp.reviewer_login
                      AND prior.filename = rfp.filename
                      AND prior.submitted_at < rfp.submitted_at
                ) as has_prior_experience
            FROM reviewer_file_pairs rfp
        ),
        review_experience AS (
            SELECT
                pr_number,
                reviewer_login,
                COUNT(*) as files_in_review,
                SUM(CASE WHEN has_prior_experience THEN 1 ELSE 0 END) as files_seen_before
            FROM file_experience
            GROUP BY pr_number, reviewer_login
        ),
        pr_size AS (
            SELECT
                pr_number,
                SUM(CASE WHEN NOT is_gen THEN additions + deletions ELSE 0 END) as code_lines
            FROM files
            GROUP BY pr_number
        )
        SELECT
            re.pr_number,
            re.reviewer_login,
            re.files_in_review,
            ps.code_lines,
            p.author_login
        FROM review_experience re
        JOIN pr_size ps ON re.pr_number = ps.pr_number
        JOIN prs p ON re.pr_number = p.pr_number
        WHERE re.files_seen_before = 0  -- Never seen any of these files
          AND ps.code_lines >= 300      -- Large PR
          AND p.merged
          AND NOT p.author_is_bot
        ORDER BY ps.code_lines DESC
        LIMIT 15
    """).fetchall()

    return [
        {
            "pr_number": r[0],
            "reviewer": r[1],
            "files": r[2],
            "lines": r[3],
            "author": r[4],
        }
        for r in results
    ]


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
    console.print("\n[bold cyan]## The Short Answer[/bold cyan]\n")

    total = approval_ctx.get("total_approvals", 0)
    empty = approval_ctx.get("empty_approvals", 0)
    empty_pct = 100.0 * empty / total if total else 0

    # Color based on severity
    if empty_pct > 70:
        pct_color = "bold red"
    elif empty_pct > 50:
        pct_color = "bold yellow"
    else:
        pct_color = "bold green"

    console.print(
        f"[{pct_color}]{format_pct(empty_pct)}[/{pct_color}] of approvals have no comment or feedback. But context matters:"
    )
    console.print()

    # Expert context
    expert_total = approval_ctx.get("expert_approvals", 0)
    expert_empty = approval_ctx.get("expert_empty", 0)
    if expert_total > 0:
        expert_pct = 100.0 * expert_empty / expert_total
        console.print(
            f"  [green]\u2022[/green] From module experts: {format_pct(expert_pct)} empty (probably fine - they know the code)"
        )

    # Familiar context
    familiar_total = approval_ctx.get("familiar_approvals", 0)
    familiar_empty = approval_ctx.get("familiar_empty", 0)
    if familiar_total > 0:
        familiar_pct = 100.0 * familiar_empty / familiar_total
        console.print(
            f"  [green]\u2022[/green] From familiar reviewers: {format_pct(familiar_pct)} empty (know the author's work)"
        )

    # First-time context
    firsttime_total = approval_ctx.get("firsttime_approvals", 0)
    firsttime_empty = approval_ctx.get("firsttime_empty", 0)
    if firsttime_total > 0:
        firsttime_pct = 100.0 * firsttime_empty / firsttime_total
        if firsttime_pct > 30:
            console.print(
                f"  [yellow]\u2022[/yellow] From first-time reviewers: {format_pct(firsttime_pct)} empty [yellow](worth checking)[/yellow]"
            )
        else:
            console.print(
                f"  [green]\u2022[/green] From first-time reviewers: {format_pct(firsttime_pct)} empty"
            )

    # Quick large approvals
    if quick_large:
        console.print()
        console.print(
            f"[bold red]{len(quick_large)} large PRs (500+ lines) were approved in under 5 minutes with no comments.[/bold red]"
        )


def print_review_depth(depth_data: list[dict]) -> None:
    """Print review depth by PR type."""
    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold cyan]## Review Depth by Risk[/bold cyan]")
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
    console.print("\n[bold cyan]## Who's Actually Reviewing?[/bold cyan]")
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


def print_review_engagement(
    thread_outcomes: dict,
    iteration_stats: dict,
    feedback_stats: dict,
) -> None:
    """Print review engagement and outcomes section."""
    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold cyan]## Did Review Lead to Action?[/bold cyan]")
    console.print("[dim]Outcomes matter more than activity[/dim]\n")

    # Thread outcomes
    total_threads = thread_outcomes.get("total_threads", 0)
    has_resolution_data = thread_outcomes.get("has_resolution_data", False)

    if total_threads > 0:
        discussed = thread_outcomes.get("discussed", 0)
        standalone = thread_outcomes.get("standalone", 0)

        console.print(f"  [bold]Review threads:[/bold] {total_threads:,}")

        if has_resolution_data:
            resolved = thread_outcomes.get("resolved", 0)
            outdated = thread_outcomes.get("outdated", 0)
            addressed_rate = thread_outcomes.get("addressed_rate", 0)

            # Color based on addressed rate
            if addressed_rate >= 70:
                rate_color = "green"
            elif addressed_rate >= 50:
                rate_color = "yellow"
            else:
                rate_color = "red"

            console.print(
                f"    [{rate_color}]\u2022[/{rate_color}] Resolved: {resolved:,} ({format_pct(100.0 * resolved / total_threads)})"
            )
            console.print(
                f"    [{rate_color}]\u2022[/{rate_color}] Outdated (code changed): {outdated:,} ({format_pct(100.0 * outdated / total_threads)})"
            )
            console.print(f"    [dim]\u2022[/dim] Discussed (multi-author): {discussed:,}")
            console.print(f"    [dim]\u2022[/dim] Standalone: {standalone:,}")
            console.print()
            console.print(
                f"  [{rate_color}]{format_pct(addressed_rate)}[/{rate_color}] of threads led to resolution or code changes"
            )
        else:
            # No resolution data - show what we can
            console.print(
                f"    [dim]\u2022[/dim] With back-and-forth: {discussed:,} ({format_pct(100.0 * discussed / total_threads)})"
            )
            console.print(f"    [dim]\u2022[/dim] Standalone comments: {standalone:,}")
            console.print()
            console.print(
                "  [dim]Resolution data not available - re-extract to populate is_resolved/is_outdated[/dim]"
            )

        console.print()

    # Iteration stats
    total_prs = iteration_stats.get("total_prs", 0)
    if total_prs > 0:
        iteration_rate = iteration_stats.get("iteration_rate", 0)
        avg_commits = iteration_stats.get("avg_commits", 0)

        if iteration_rate >= 50:
            iter_color = "green"
            iter_note = "review is driving changes"
        elif iteration_rate >= 30:
            iter_color = "yellow"
            iter_note = "some iteration happening"
        else:
            iter_color = "dim"
            iter_note = "PRs mostly ship as-is"

        console.print("  [bold]Post-review iteration:[/bold]")
        console.print(
            f"    [{iter_color}]\u2022[/{iter_color}] {format_pct(iteration_rate)} of PRs had commits after first review"
        )
        console.print(
            f"    [dim]\u2022[/dim] Avg {avg_commits:.1f} commits after review when iterating"
        )
        console.print(f"    \u2192 {iter_note}")
        console.print()

    # Feedback quality signals
    total_comments = feedback_stats.get("total", 0)
    if total_comments > 0:
        code_rate = feedback_stats.get("code_rate", 0)
        link_rate = feedback_stats.get("link_rate", 0)

        console.print("  [bold]Comment quality signals:[/bold]")
        console.print(f"    [dim]\u2022[/dim] {format_pct(code_rate)} include code suggestions")
        console.print(f"    [dim]\u2022[/dim] {format_pct(link_rate)} include links/references")


def print_reviewer_file_experience(
    experience: dict,
    first_time_reviews: list[dict],
) -> None:
    """Print reviewer file experience section."""
    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold cyan]## Reviewer File Experience[/bold cyan]")
    console.print("[dim]Are reviewers familiar with what they're reviewing?[/dim]\n")

    total = experience.get("total_reviews", 0)
    if total == 0:
        console.print("  [dim]No review data available[/dim]")
        return

    avg_familiarity = experience.get("avg_familiarity", 0)
    unfamiliar_rate = experience.get("unfamiliar_rate", 0)

    # Color based on familiarity rate
    if avg_familiarity >= 60:
        fam_color = "green"
    elif avg_familiarity >= 40:
        fam_color = "yellow"
    else:
        fam_color = "red"

    console.print(
        f"  [bold]File familiarity:[/bold] [{fam_color}]{format_pct(avg_familiarity)}[/{fam_color}] avg across {total:,} reviews"
    )
    console.print()

    fully_unfamiliar = experience.get("fully_unfamiliar", 0)
    fully_familiar = experience.get("fully_familiar", 0)

    # Breakdown
    console.print("  [dim]Breakdown:[/dim]")
    console.print(
        f"    [green]\u2022[/green] Fully familiar (100% files seen): {fully_familiar:,} ({format_pct(100.0 * fully_familiar / total)})"
    )
    console.print(
        f"    [dim]\u2022[/dim] Mostly familiar (75%+): {experience.get('mostly_familiar', 0):,}"
    )
    console.print(
        f"    [yellow]\u2022[/yellow] Mostly unfamiliar (<25%): {experience.get('mostly_unfamiliar', 0):,}"
    )
    console.print(
        f"    [red]\u2022[/red] First-time (0% files seen): {fully_unfamiliar:,} ({format_pct(unfamiliar_rate)})"
    )

    # Show warning PRs if any
    if first_time_reviews:
        console.print()
        console.print(
            f"  [yellow]\u26a0 {len(first_time_reviews)} large PRs reviewed by someone who'd never seen the files:[/yellow]"
        )

        # Show top 5
        for pr in first_time_reviews[:5]:
            console.print(
                f"    [dim]#{pr['pr_number']}[/dim] "
                f"{pr['lines']:,} lines, {pr['files']} files - "
                f"reviewed by {pr['reviewer']}"
            )

        if len(first_time_reviews) > 5:
            console.print(f"    [dim]...and {len(first_time_reviews) - 5} more[/dim]")


def print_red_flags(flags: list[dict]) -> None:
    """Print specific PRs that might have slipped through."""
    if not flags:
        return

    console.print("\n[dim]" + "\u2500" * 70 + "[/dim]")
    console.print("\n[bold red]## Red Flags[/bold red]")
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
    thread_outcomes = get_thread_outcomes(con)
    iteration_stats = get_iteration_stats(con)
    feedback_stats = get_feedback_with_code(con)
    reviewer_experience = get_reviewer_file_experience(con)
    first_time_reviews = get_first_time_file_reviews(con)
    red_flags = get_red_flags(con)

    # Print report
    console.print()
    print_header(stats)
    print_short_answer(approval_ctx, quick_large)
    print_review_depth(depth_data)
    print_review_engagement(thread_outcomes, iteration_stats, feedback_stats)
    print_reviewer_file_experience(reviewer_experience, first_time_reviews)
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
