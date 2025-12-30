"""Analysis queries for code review data.

Run with: uv run analyze
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .module_config import ModuleConfig

DATA_DIR = Path("data/raw")

# Module-level config instance
_module_config: ModuleConfig | None = None


def get_module_config() -> ModuleConfig:
    """Get or load the module config."""
    global _module_config
    if _module_config is None:
        _module_config = ModuleConfig.load()
    return _module_config


def get_connection() -> duckdb.DuckDBPyConnection:
    """Get DuckDB connection with parquet files and computed columns."""
    con = duckdb.connect()

    # Register base tables as views
    for table in ["prs", "reviews", "pr_comments", "review_comments", "checks", "timeline_events", "users"]:
        path = DATA_DIR / f"{table}.parquet"
        if path.exists():
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM '{path}'")

    # Register UDFs for module and is_generated
    config = get_module_config()
    con.create_function("module", config.extract_module, [str], str)
    con.create_function("is_generated", config.is_generated, [str], bool)

    # Create enriched files table with precomputed module and is_generated
    # This avoids calling Python UDFs repeatedly in queries
    files_path = DATA_DIR / "files.parquet"
    if files_path.exists():
        con.execute(f"""
            CREATE TABLE files AS
            SELECT
                *,
                module(filename) as computed_module,
                is_generated(filename) as is_gen
            FROM '{files_path}'
        """)
        # Create a view that uses computed columns by default
        con.execute("""
            CREATE VIEW files_enriched AS
            SELECT
                pr_number,
                filename,
                status,
                additions,
                deletions,
                changes,
                computed_module as module,
                is_gen as is_generated
            FROM files
        """)

    return con


def run_query(con: duckdb.DuckDBPyConnection, title: str, query: str) -> None:
    """Run a query and print results."""
    print(f"\n=== {title} ===")
    con.sql(query).show()


def rubber_stamp_rate(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze empty approval rate by reviewer."""
    run_query(con, "Rubber Stamp Rate (Empty Approvals)", """
        SELECT
            reviewer_login,
            COUNT(*) as approvals,
            SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) as empty,
            ROUND(100.0 * SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) / COUNT(*), 1) as empty_pct
        FROM reviews
        WHERE state = 'APPROVED' AND NOT reviewer_is_bot
        GROUP BY 1
        HAVING COUNT(*) >= 10
        ORDER BY approvals DESC
        LIMIT 20
    """)


def time_to_review(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze time to first review."""
    run_query(con, "Time to First Review", """
        WITH pr_first_review AS (
            SELECT
                p.pr_number,
                p.created_at as pr_created,
                MIN(r.submitted_at) as first_review_at
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE NOT r.reviewer_is_bot
            GROUP BY 1, 2
        )
        SELECT
            ROUND(AVG(EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as avg_hours,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as median_hours,
            ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as p90_hours,
            COUNT(*) as prs
        FROM pr_first_review
    """)


def review_coverage(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze what percentage of PRs get human reviews."""
    run_query(con, "Review Coverage", """
        SELECT
            CASE
                WHEN human_reviews > 0 THEN 'Human reviewed'
                WHEN bot_reviews > 0 THEN 'Bot-only'
                ELSE 'No reviews'
            END as status,
            COUNT(*) as prs,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM (
            SELECT
                p.pr_number,
                SUM(CASE WHEN NOT r.reviewer_is_bot THEN 1 ELSE 0 END) as human_reviews,
                SUM(CASE WHEN r.reviewer_is_bot THEN 1 ELSE 0 END) as bot_reviews
            FROM prs p
            LEFT JOIN reviews r ON p.pr_number = r.pr_number
            GROUP BY 1
        )
        GROUP BY 1
        ORDER BY prs DESC
    """)


def who_reviews_whom(con: duckdb.DuckDBPyConnection) -> None:
    """Show top reviewer-author pairs."""
    run_query(con, "Top Reviewer-Author Pairs", """
        SELECT
            r.reviewer_login as reviewer,
            p.author_login as author,
            COUNT(*) as reviews,
            SUM(CASE WHEN r.state = 'APPROVED' THEN 1 ELSE 0 END) as approvals
        FROM reviews r
        JOIN prs p ON r.pr_number = p.pr_number
        WHERE NOT r.reviewer_is_bot
          AND NOT p.author_is_bot
          AND r.reviewer_login != p.author_login
        GROUP BY 1, 2
        ORDER BY reviews DESC
        LIMIT 15
    """)


def substantive_reviewers(con: duckdb.DuckDBPyConnection) -> None:
    """Find reviewers who leave inline code comments."""
    run_query(con, "Substantive Reviewers (Inline Comments)", """
        SELECT
            author_login as reviewer,
            COUNT(*) as inline_comments,
            COUNT(DISTINCT pr_number) as prs
        FROM review_comments
        WHERE NOT author_is_bot
        GROUP BY 1
        ORDER BY inline_comments DESC
        LIMIT 15
    """)


def bot_activity(con: duckdb.DuckDBPyConnection) -> None:
    """Show bot review activity."""
    run_query(con, "Bot Activity", """
        SELECT
            u.bot_name,
            COUNT(DISTINCT r.pr_number) as prs_touched,
            COUNT(*) as total_reviews,
            SUM(CASE WHEN r.state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
            SUM(CASE WHEN r.state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested
        FROM reviews r
        JOIN users u ON r.reviewer_login = u.login
        WHERE r.reviewer_is_bot AND u.bot_name IS NOT NULL
        GROUP BY 1
        ORDER BY prs_touched DESC
    """)


def module_coverage(con: duckdb.DuckDBPyConnection) -> None:
    """Show review activity by module.

    Uses precomputed module column from files table.
    """
    run_query(con, "Module Review Coverage", """
        WITH module_stats AS (
            SELECT
                f.computed_module as mod,
                COUNT(DISTINCT f.pr_number) as prs,
                COUNT(DISTINCT CASE WHEN r.review_id IS NOT NULL AND NOT r.reviewer_is_bot THEN f.pr_number END) as reviewed_prs,
                SUM(f.additions + f.deletions) as total_churn
            FROM files f
            LEFT JOIN reviews r ON f.pr_number = r.pr_number
            GROUP BY 1
        )
        SELECT
            mod as module,
            prs,
            reviewed_prs,
            ROUND(100.0 * reviewed_prs / NULLIF(prs, 0), 1) as review_pct,
            total_churn
        FROM module_stats
        WHERE prs >= 10
        ORDER BY prs DESC
        LIMIT 20
    """)


def pr_size_vs_review(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze if larger PRs get more review attention."""
    run_query(con, "PR Size vs Review Activity", """
        WITH pr_stats AS (
            SELECT
                p.pr_number,
                p.additions + p.deletions as lines_changed,
                COUNT(DISTINCT CASE WHEN NOT r.reviewer_is_bot THEN r.review_id END) as human_reviews,
                COUNT(DISTINCT rc.comment_id) as inline_comments
            FROM prs p
            LEFT JOIN reviews r ON p.pr_number = r.pr_number
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number AND NOT rc.author_is_bot
            GROUP BY 1, 2
        )
        SELECT
            CASE
                WHEN lines_changed <= 10 THEN 'XS (<=10)'
                WHEN lines_changed <= 50 THEN 'S (11-50)'
                WHEN lines_changed <= 200 THEN 'M (51-200)'
                WHEN lines_changed <= 500 THEN 'L (201-500)'
                ELSE 'XL (500+)'
            END as size_bucket,
            COUNT(*) as prs,
            ROUND(AVG(human_reviews), 2) as avg_reviews,
            ROUND(AVG(inline_comments), 2) as avg_inline_comments
        FROM pr_stats
        GROUP BY 1
        ORDER BY
            CASE size_bucket
                WHEN 'XS (<=10)' THEN 1
                WHEN 'S (11-50)' THEN 2
                WHEN 'M (51-200)' THEN 3
                WHEN 'L (201-500)' THEN 4
                ELSE 5
            END
    """)


# === Review Quality Metrics ===


def review_depth(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze reviews with inline comments vs just approvals."""
    run_query(con, "Review Depth (Inline Comments per Reviewer)", """
        SELECT
            r.reviewer_login,
            COUNT(DISTINCT r.review_id) as reviews,
            COUNT(DISTINCT rc.comment_id) as inline_comments,
            ROUND(1.0 * COUNT(DISTINCT rc.comment_id) / NULLIF(COUNT(DISTINCT r.review_id), 0), 2) as comments_per_review,
            SUM(CASE WHEN r.state = 'APPROVED' AND (r.body IS NULL OR TRIM(r.body) = '')
                     AND rc.comment_id IS NULL THEN 1 ELSE 0 END) as empty_approvals
        FROM reviews r
        LEFT JOIN review_comments rc ON r.pr_number = rc.pr_number
            AND r.reviewer_login = rc.author_login
        WHERE NOT r.reviewer_is_bot
        GROUP BY 1
        HAVING COUNT(DISTINCT r.review_id) >= 10
        ORDER BY comments_per_review DESC
        LIMIT 20
    """)


def review_iterations(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze how many rounds of changes_requested → re-review per PR."""
    run_query(con, "Review Iterations (Rounds of Feedback)", """
        WITH review_rounds AS (
            SELECT
                pr_number,
                COUNT(*) as total_reviews,
                SUM(CASE WHEN state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested,
                SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals
            FROM reviews
            WHERE NOT reviewer_is_bot
            GROUP BY 1
        )
        SELECT
            CASE
                WHEN changes_requested = 0 THEN '0 rounds (approved first try)'
                WHEN changes_requested = 1 THEN '1 round of changes'
                WHEN changes_requested = 2 THEN '2 rounds of changes'
                ELSE '3+ rounds of changes'
            END as iteration_bucket,
            COUNT(*) as prs,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM review_rounds
        GROUP BY 1
        ORDER BY
            CASE iteration_bucket
                WHEN '0 rounds (approved first try)' THEN 1
                WHEN '1 round of changes' THEN 2
                WHEN '2 rounds of changes' THEN 3
                ELSE 4
            END
    """)


def stale_approvals(con: duckdb.DuckDBPyConnection) -> None:
    """Find PRs where commits were pushed after approval."""
    run_query(con, "Stale Approvals (Commits After Approval)", """
        WITH last_approval AS (
            SELECT pr_number, MAX(submitted_at) as approved_at
            FROM reviews
            WHERE state = 'APPROVED' AND NOT reviewer_is_bot
            GROUP BY 1
        ),
        pr_activity AS (
            SELECT
                p.pr_number,
                p.title,
                p.author_login,
                la.approved_at,
                p.updated_at,
                p.merged_at
            FROM prs p
            JOIN last_approval la ON p.pr_number = la.pr_number
            WHERE p.merged
              AND p.updated_at > la.approved_at + INTERVAL '5 minutes'
        )
        SELECT
            pr_number,
            author_login,
            approved_at,
            updated_at,
            ROUND(EXTRACT(EPOCH FROM (updated_at - approved_at)) / 3600, 1) as hours_after_approval
        FROM pr_activity
        ORDER BY hours_after_approval DESC
        LIMIT 20
    """)


def brief_comments(con: duckdb.DuckDBPyConnection) -> None:
    """Find reviewers who leave short comments."""
    run_query(con, "Brief Comments Analysis", """
        WITH comment_stats AS (
            SELECT
                author_login,
                COUNT(*) as total_comments,
                SUM(CASE WHEN LENGTH(body) < 20 THEN 1 ELSE 0 END) as short_comments,
                SUM(CASE WHEN LOWER(body) IN ('lgtm', 'looks good', 'nit', 'nice', '+1', 'approved', 'ship it')
                         THEN 1 ELSE 0 END) as low_value_comments
            FROM review_comments
            WHERE NOT author_is_bot
            GROUP BY 1
            HAVING COUNT(*) >= 10
        )
        SELECT
            author_login as reviewer,
            total_comments,
            short_comments,
            ROUND(100.0 * short_comments / total_comments, 1) as short_pct,
            low_value_comments,
            ROUND(100.0 * low_value_comments / total_comments, 1) as low_value_pct
        FROM comment_stats
        ORDER BY low_value_pct DESC
        LIMIT 15
    """)


def self_review_activity(con: duckdb.DuckDBPyConnection) -> None:
    """Find authors commenting on their own PRs."""
    run_query(con, "Self-Review Activity (Authors on Own PRs)", """
        SELECT
            p.author_login,
            COUNT(DISTINCT p.pr_number) as total_prs,
            COUNT(DISTINCT CASE WHEN rc.author_login = p.author_login THEN p.pr_number END) as prs_with_self_comments,
            COUNT(CASE WHEN rc.author_login = p.author_login THEN 1 END) as self_comments,
            ROUND(100.0 * COUNT(DISTINCT CASE WHEN rc.author_login = p.author_login THEN p.pr_number END)
                / NULLIF(COUNT(DISTINCT p.pr_number), 0), 1) as self_review_pct
        FROM prs p
        LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
        WHERE NOT p.author_is_bot
        GROUP BY 1
        HAVING COUNT(DISTINCT p.pr_number) >= 10
        ORDER BY self_review_pct DESC
        LIMIT 15
    """)


# === Temporal Patterns ===


def review_by_time(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze review activity by hour and day of week."""
    run_query(con, "Review Activity by Day of Week", """
        SELECT
            CASE DAYOFWEEK(submitted_at)
                WHEN 0 THEN 'Sunday'
                WHEN 1 THEN 'Monday'
                WHEN 2 THEN 'Tuesday'
                WHEN 3 THEN 'Wednesday'
                WHEN 4 THEN 'Thursday'
                WHEN 5 THEN 'Friday'
                WHEN 6 THEN 'Saturday'
            END as day_of_week,
            COUNT(*) as reviews,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM reviews
        WHERE NOT reviewer_is_bot
        GROUP BY DAYOFWEEK(submitted_at)
        ORDER BY DAYOFWEEK(submitted_at)
    """)


def review_latency_by_author(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze which authors wait longest for reviews."""
    run_query(con, "Review Latency by Author (Who Waits Longest?)", """
        WITH pr_first_review AS (
            SELECT
                p.pr_number,
                p.author_login,
                p.created_at as pr_created,
                MIN(r.submitted_at) as first_review_at
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE NOT r.reviewer_is_bot AND NOT p.author_is_bot
            GROUP BY 1, 2, 3
        )
        SELECT
            author_login,
            COUNT(*) as prs,
            ROUND(AVG(EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as avg_hours,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as median_hours,
            ROUND(MAX(EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as max_hours
        FROM pr_first_review
        GROUP BY 1
        HAVING COUNT(*) >= 10
        ORDER BY avg_hours DESC
        LIMIT 15
    """)


def review_latency_by_module(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze which modules get slow reviews."""
    run_query(con, "Review Latency by Module", """
        WITH pr_first_review AS (
            SELECT
                p.pr_number,
                p.created_at as pr_created,
                MIN(r.submitted_at) as first_review_at
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE NOT r.reviewer_is_bot
            GROUP BY 1, 2
        ),
        pr_modules AS (
            SELECT DISTINCT pr_number, computed_module as mod
            FROM files
        )
        SELECT
            pm.mod as module,
            COUNT(DISTINCT pfr.pr_number) as prs,
            ROUND(AVG(EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as avg_hours,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600), 1) as median_hours
        FROM pr_first_review pfr
        JOIN pr_modules pm ON pfr.pr_number = pm.pr_number
        GROUP BY 1
        HAVING COUNT(DISTINCT pfr.pr_number) >= 20
        ORDER BY avg_hours DESC
        LIMIT 15
    """)


def time_in_review(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze total time from first review to merge."""
    run_query(con, "Time in Review (First Review to Merge)", """
        WITH pr_review_times AS (
            SELECT
                p.pr_number,
                MIN(r.submitted_at) as first_review,
                p.merged_at
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            WHERE p.merged AND NOT r.reviewer_is_bot
            GROUP BY 1, 3
        )
        SELECT
            CASE
                WHEN EXTRACT(EPOCH FROM (merged_at - first_review)) / 3600 < 1 THEN '< 1 hour'
                WHEN EXTRACT(EPOCH FROM (merged_at - first_review)) / 3600 < 4 THEN '1-4 hours'
                WHEN EXTRACT(EPOCH FROM (merged_at - first_review)) / 3600 < 24 THEN '4-24 hours'
                WHEN EXTRACT(EPOCH FROM (merged_at - first_review)) / 3600 < 72 THEN '1-3 days'
                ELSE '3+ days'
            END as time_bucket,
            COUNT(*) as prs,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM pr_review_times
        GROUP BY 1
        ORDER BY
            CASE time_bucket
                WHEN '< 1 hour' THEN 1
                WHEN '1-4 hours' THEN 2
                WHEN '4-24 hours' THEN 3
                WHEN '1-3 days' THEN 4
                ELSE 5
            END
    """)


# === Team Dynamics ===


def review_reciprocity(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze if people review each other equally."""
    run_query(con, "Review Reciprocity (Mutual Review Pairs)", """
        WITH review_pairs AS (
            SELECT
                r.reviewer_login,
                p.author_login,
                COUNT(*) as reviews_given
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE NOT r.reviewer_is_bot
              AND NOT p.author_is_bot
              AND r.reviewer_login != p.author_login
            GROUP BY 1, 2
            HAVING COUNT(*) >= 5
        )
        SELECT
            a.reviewer_login as person_a,
            a.author_login as person_b,
            a.reviews_given as a_reviews_b,
            COALESCE(b.reviews_given, 0) as b_reviews_a,
            ROUND(1.0 * a.reviews_given / NULLIF(COALESCE(b.reviews_given, 0), 0), 2) as imbalance_ratio
        FROM review_pairs a
        LEFT JOIN review_pairs b ON a.reviewer_login = b.author_login
            AND a.author_login = b.reviewer_login
        ORDER BY a.reviews_given + COALESCE(b.reviews_given, 0) DESC
        LIMIT 20
    """)


def reviewer_load_balance(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze distribution of review work."""
    run_query(con, "Reviewer Load Balance", """
        WITH reviewer_stats AS (
            SELECT
                reviewer_login,
                COUNT(DISTINCT pr_number) as prs_reviewed,
                COUNT(*) as total_reviews
            FROM reviews
            WHERE NOT reviewer_is_bot
            GROUP BY 1
        ),
        totals AS (
            SELECT SUM(prs_reviewed) as total_prs
            FROM reviewer_stats
        )
        SELECT
            reviewer_login,
            prs_reviewed,
            total_reviews,
            ROUND(100.0 * prs_reviewed / t.total_prs, 2) as pct_of_all_reviews
        FROM reviewer_stats
        CROSS JOIN totals t
        ORDER BY prs_reviewed DESC
        LIMIT 20
    """)


# === Risk Indicators ===


def large_pr_no_comments(con: duckdb.DuckDBPyConnection) -> None:
    """Find large PRs that received no inline feedback."""
    run_query(con, "Risk: Large PRs with No Inline Comments", """
        WITH pr_comments AS (
            SELECT
                p.pr_number,
                p.title,
                p.author_login,
                p.additions + p.deletions as lines_changed,
                COUNT(DISTINCT rc.comment_id) as inline_comments
            FROM prs p
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number AND NOT rc.author_is_bot
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1, 2, 3, 4
        )
        SELECT
            pr_number,
            author_login,
            lines_changed,
            inline_comments
        FROM pr_comments
        WHERE lines_changed >= 200 AND inline_comments = 0
        ORDER BY lines_changed DESC
        LIMIT 20
    """)


def quick_approve_large_pr(con: duckdb.DuckDBPyConnection) -> None:
    """Find large PRs approved suspiciously quickly."""
    run_query(con, "Risk: Quick Approvals on Large PRs (<5 min)", """
        WITH first_approval AS (
            SELECT
                r.pr_number,
                MIN(r.submitted_at) as first_approved_at
            FROM reviews r
            WHERE r.state = 'APPROVED' AND NOT r.reviewer_is_bot
            GROUP BY 1
        )
        SELECT
            p.pr_number,
            p.author_login,
            p.additions + p.deletions as lines_changed,
            ROUND(EXTRACT(EPOCH FROM (fa.first_approved_at - p.created_at)) / 60, 1) as minutes_to_approval
        FROM prs p
        JOIN first_approval fa ON p.pr_number = fa.pr_number
        WHERE p.additions + p.deletions >= 500
          AND EXTRACT(EPOCH FROM (fa.first_approved_at - p.created_at)) / 60 < 5
          AND p.merged
        ORDER BY lines_changed DESC
        LIMIT 20
    """)


def single_reviewer_merges(con: duckdb.DuckDBPyConnection) -> None:
    """Find PRs merged with only one human reviewer."""
    run_query(con, "Risk: Single Reviewer Merges", """
        WITH pr_reviewers AS (
            SELECT
                pr_number,
                COUNT(DISTINCT reviewer_login) as human_reviewers
            FROM reviews
            WHERE NOT reviewer_is_bot
            GROUP BY 1
        )
        SELECT
            CASE
                WHEN human_reviewers = 0 THEN 'No human reviewers'
                WHEN human_reviewers = 1 THEN 'Single reviewer'
                WHEN human_reviewers = 2 THEN 'Two reviewers'
                ELSE '3+ reviewers'
            END as reviewer_count,
            COUNT(*) as prs,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) as pct
        FROM prs p
        LEFT JOIN pr_reviewers pr ON p.pr_number = pr.pr_number
        WHERE p.merged AND NOT p.author_is_bot
        GROUP BY 1
        ORDER BY
            CASE reviewer_count
                WHEN 'No human reviewers' THEN 1
                WHEN 'Single reviewer' THEN 2
                WHEN 'Two reviewers' THEN 3
                ELSE 4
            END
    """)


def code_review_depth(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze review depth on actual code changes (excluding generated files).

    Key metric: Are reviewers engaging with real code, or just approving?
    """
    run_query(con, "Review Depth on Real Code (Excluding Generated)", """
        WITH pr_code_churn AS (
            SELECT
                pr_number,
                SUM(CASE WHEN NOT is_gen THEN additions + deletions ELSE 0 END) as code_churn
            FROM files
            GROUP BY 1
        ),
        pr_review_stats AS (
            SELECT
                p.pr_number,
                pc.code_churn,
                COUNT(DISTINCT CASE WHEN NOT r.reviewer_is_bot THEN r.review_id END) as human_reviews,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments,
                BOOL_OR(r.state = 'CHANGES_REQUESTED') as had_feedback
            FROM prs p
            JOIN pr_code_churn pc ON p.pr_number = pc.pr_number
            LEFT JOIN reviews r ON p.pr_number = r.pr_number
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged AND NOT p.author_is_bot AND pc.code_churn > 0
            GROUP BY 1, 2
        )
        SELECT
            CASE
                WHEN code_churn <= 50 THEN 'Small (<=50)'
                WHEN code_churn <= 200 THEN 'Medium (51-200)'
                WHEN code_churn <= 500 THEN 'Large (201-500)'
                ELSE 'XL (500+)'
            END as code_size,
            COUNT(*) as prs,
            ROUND(AVG(inline_comments), 2) as avg_inline_comments,
            ROUND(100.0 * SUM(CASE WHEN inline_comments > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_with_comments,
            ROUND(100.0 * SUM(CASE WHEN had_feedback THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_with_feedback
        FROM pr_review_stats
        GROUP BY 1
        ORDER BY
            CASE code_size
                WHEN 'Small (<=50)' THEN 1
                WHEN 'Medium (51-200)' THEN 2
                WHEN 'Large (201-500)' THEN 3
                ELSE 4
            END
    """)


def pr_type_review_depth(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze review depth by PR type: new-code vs refactor vs cleanup.

    Uses additions/deletions ratio to classify PRs:
    - new-code: >85% additions (building new things)
    - refactor: 15-85% additions (restructuring existing code)
    - cleanup: <15% additions (removing/simplifying)
    """
    run_query(con, "Review Depth by PR Type (New vs Refactor vs Cleanup)", """
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
                pt.total_churn,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments,
                BOOL_OR(r.state = 'CHANGES_REQUESTED') as had_feedback
            FROM prs p
            JOIN pr_types pt ON p.pr_number = pt.pr_number
            LEFT JOIN reviews r ON p.pr_number = r.pr_number AND NOT r.reviewer_is_bot
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1, 2, 3
        )
        SELECT
            pr_type,
            COUNT(*) as prs,
            ROUND(AVG(inline_comments), 2) as avg_comments,
            ROUND(100.0 * SUM(CASE WHEN inline_comments > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_with_comments,
            ROUND(100.0 * SUM(CASE WHEN had_feedback THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_with_feedback
        FROM review_stats
        GROUP BY 1
        ORDER BY prs DESC
    """)


def conventional_commits(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze conventional commit style adoption in PR titles.

    Checks for prefixes like feat:, fix:, chore:, docs:, refactor:, etc.
    """
    run_query(con, "Conventional Commit Adoption by Author", """
        WITH commit_types AS (
            SELECT
                author_login,
                CASE
                    WHEN REGEXP_MATCHES(LOWER(title), '^(feat|fix|chore|docs|refactor|test|style|perf|ci|build|revert)[:\\(]') THEN true
                    ELSE false
                END as is_conventional
            FROM prs
            WHERE NOT author_is_bot
        )
        SELECT
            author_login,
            COUNT(*) as prs,
            SUM(CASE WHEN is_conventional THEN 1 ELSE 0 END) as conventional,
            ROUND(100.0 * SUM(CASE WHEN is_conventional THEN 1 ELSE 0 END) / COUNT(*), 1) as conventional_pct
        FROM commit_types
        GROUP BY 1
        HAVING COUNT(*) >= 20
        ORDER BY conventional_pct DESC
        LIMIT 15
    """)


def underreviewed_code(con: duckdb.DuckDBPyConnection) -> None:
    """Find large real-code PRs that got rubber-stamped.

    These are PRs with significant actual code changes but no substantive review.
    """
    run_query(con, "Risk: Large Code PRs with No Substantive Review", """
        WITH pr_stats AS (
            SELECT
                p.pr_number,
                p.author_login,
                SUM(CASE WHEN NOT f.is_gen THEN f.additions + f.deletions ELSE 0 END) as code_churn,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments,
                BOOL_OR(r.state = 'CHANGES_REQUESTED') as had_changes_requested
            FROM prs p
            JOIN files f ON p.pr_number = f.pr_number
            LEFT JOIN reviews r ON p.pr_number = r.pr_number AND NOT r.reviewer_is_bot
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1, 2
        )
        SELECT
            pr_number,
            author_login,
            code_churn,
            inline_comments,
            CASE WHEN had_changes_requested THEN 'Yes' ELSE 'No' END as feedback
        FROM pr_stats
        WHERE code_churn >= 300
          AND inline_comments = 0
          AND NOT had_changes_requested
        ORDER BY code_churn DESC
        LIMIT 20
    """)


# === Collaboration Context ===


def module_experts(con: duckdb.DuckDBPyConnection) -> None:
    """Show who has authored the most PRs per module.

    Module experts are people who deeply understand an area of the codebase.
    Their reviews of that area carry more weight.
    """
    run_query(con, "Module Experts (Top Authors per Module)", """
        WITH author_module_stats AS (
            SELECT
                f.computed_module as module,
                p.author_login,
                COUNT(DISTINCT p.pr_number) as prs_authored,
                SUM(CASE WHEN NOT f.is_gen THEN f.additions + f.deletions ELSE 0 END) as code_churn
            FROM prs p
            JOIN files f ON p.pr_number = f.pr_number
            WHERE NOT p.author_is_bot AND p.merged
            GROUP BY 1, 2
        ),
        ranked AS (
            SELECT
                module,
                author_login,
                prs_authored,
                code_churn,
                ROW_NUMBER() OVER (PARTITION BY module ORDER BY prs_authored DESC) as rank
            FROM author_module_stats
            WHERE prs_authored >= 5
        )
        SELECT module, author_login as expert, prs_authored, code_churn
        FROM ranked
        WHERE rank <= 3
        ORDER BY module, rank
    """)


def module_reviewers(con: duckdb.DuckDBPyConnection) -> None:
    """Show who reviews the most PRs per module.

    Frequent reviewers of a module develop expertise even without authoring.
    """
    run_query(con, "Module Reviewers (Top Reviewers per Module)", """
        WITH reviewer_module_stats AS (
            SELECT
                f.computed_module as module,
                r.reviewer_login,
                COUNT(DISTINCT r.pr_number) as prs_reviewed,
                COUNT(DISTINCT rc.comment_id) as inline_comments
            FROM reviews r
            JOIN files f ON r.pr_number = f.pr_number
            LEFT JOIN review_comments rc ON r.pr_number = rc.pr_number
                AND r.reviewer_login = rc.author_login
            WHERE NOT r.reviewer_is_bot
            GROUP BY 1, 2
        ),
        ranked AS (
            SELECT
                module,
                reviewer_login,
                prs_reviewed,
                inline_comments,
                ROW_NUMBER() OVER (PARTITION BY module ORDER BY prs_reviewed DESC) as rank
            FROM reviewer_module_stats
            WHERE prs_reviewed >= 5
        )
        SELECT module, reviewer_login as reviewer, prs_reviewed, inline_comments
        FROM ranked
        WHERE rank <= 3
        ORDER BY module, rank
    """)


def collaboration_pairs(con: duckdb.DuckDBPyConnection) -> None:
    """Show author-reviewer pairs with their shared history.

    High collaboration count suggests the reviewer knows the author's style
    and the areas they work in.
    """
    run_query(con, "Collaboration History (Author-Reviewer Pairs)", """
        WITH pair_stats AS (
            SELECT
                p.author_login as author,
                r.reviewer_login as reviewer,
                COUNT(DISTINCT p.pr_number) as prs_together,
                COUNT(DISTINCT f.computed_module) as shared_modules,
                SUM(CASE WHEN r.state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
                SUM(CASE WHEN r.state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            JOIN files f ON p.pr_number = f.pr_number
            WHERE NOT p.author_is_bot
              AND NOT r.reviewer_is_bot
              AND p.author_login != r.reviewer_login
            GROUP BY 1, 2
        )
        SELECT
            author,
            reviewer,
            prs_together,
            shared_modules,
            approvals,
            changes_requested,
            ROUND(100.0 * changes_requested / NULLIF(approvals + changes_requested, 0), 1) as pushback_rate
        FROM pair_stats
        WHERE prs_together >= 10
        ORDER BY prs_together DESC
        LIMIT 20
    """)


def module_collaboration(con: duckdb.DuckDBPyConnection) -> None:
    """Show which author-reviewer pairs collaborate on which modules.

    A quick approval from someone who has reviewed 50 of your PRs in this module
    is very different from a quick approval from a stranger.
    """
    run_query(con, "Module Collaboration (Who Reviews Whom Where)", """
        WITH module_pairs AS (
            SELECT
                f.computed_module as module,
                p.author_login as author,
                r.reviewer_login as reviewer,
                COUNT(DISTINCT p.pr_number) as prs_in_module,
                SUM(CASE WHEN r.state = 'APPROVED' AND (r.body IS NULL OR TRIM(r.body) = '')
                         THEN 1 ELSE 0 END) as quick_approvals
            FROM prs p
            JOIN reviews r ON p.pr_number = r.pr_number
            JOIN files f ON p.pr_number = f.pr_number
            WHERE NOT p.author_is_bot
              AND NOT r.reviewer_is_bot
              AND p.author_login != r.reviewer_login
              AND NOT f.is_gen
            GROUP BY 1, 2, 3
        )
        SELECT
            module,
            author,
            reviewer,
            prs_in_module,
            quick_approvals,
            ROUND(100.0 * quick_approvals / prs_in_module, 1) as quick_approval_rate
        FROM module_pairs
        WHERE prs_in_module >= 5
        ORDER BY prs_in_module DESC
        LIMIT 30
    """)


def informed_approvals(con: duckdb.DuckDBPyConnection) -> None:
    """Analyze empty approvals with collaboration context.

    An empty approval might be informed (reviewer knows the code well)
    or uninformed (first time reviewing this author/module).
    """
    run_query(con, "Approval Context (Informed vs First-Time)", """
        WITH approval_context AS (
            SELECT
                r.pr_number,
                p.author_login as author,
                r.reviewer_login as reviewer,
                f.computed_module as module,
                r.body,
                r.submitted_at
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            JOIN files f ON r.pr_number = f.pr_number
            WHERE r.state = 'APPROVED'
              AND NOT r.reviewer_is_bot
              AND NOT p.author_is_bot
              AND NOT f.is_gen
        ),
        prior_history AS (
            SELECT
                ac.pr_number,
                ac.author,
                ac.reviewer,
                ac.module,
                ac.body,
                -- Count prior collaborations before this PR
                COUNT(DISTINCT CASE
                    WHEN r2.submitted_at < ac.submitted_at
                    THEN r2.pr_number
                END) as prior_reviews_of_author,
                COUNT(DISTINCT CASE
                    WHEN r2.submitted_at < ac.submitted_at
                    AND f2.computed_module = ac.module
                    THEN r2.pr_number
                END) as prior_reviews_in_module
            FROM approval_context ac
            LEFT JOIN reviews r2 ON r2.reviewer_login = ac.reviewer
            LEFT JOIN prs p2 ON r2.pr_number = p2.pr_number
                AND p2.author_login = ac.author
                AND p2.pr_number != ac.pr_number
            LEFT JOIN files f2 ON r2.pr_number = f2.pr_number
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT
            CASE
                WHEN prior_reviews_in_module >= 10 THEN 'Expert (10+ in module)'
                WHEN prior_reviews_of_author >= 10 THEN 'Familiar (10+ with author)'
                WHEN prior_reviews_of_author >= 3 THEN 'Some history (3-9)'
                ELSE 'First-time (<3)'
            END as reviewer_context,
            COUNT(*) as approvals,
            SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) as empty_approvals,
            ROUND(100.0 * SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) / COUNT(*), 1) as empty_rate
        FROM prior_history
        GROUP BY 1
        ORDER BY
            CASE reviewer_context
                WHEN 'Expert (10+ in module)' THEN 1
                WHEN 'Familiar (10+ with author)' THEN 2
                WHEN 'Some history (3-9)' THEN 3
                ELSE 4
            END
    """)


def main() -> None:
    """Run all analysis queries."""
    con = get_connection()

    print("=" * 60)
    print("LGTM: Code Review Analysis Report")
    print("=" * 60)

    result = con.execute("SELECT COUNT(*) FROM prs").fetchone()
    total_prs = result[0] if result else 0
    print(f"\nTotal PRs analyzed: {total_prs:,}")

    # Core metrics
    rubber_stamp_rate(con)
    time_to_review(con)
    review_coverage(con)
    who_reviews_whom(con)
    substantive_reviewers(con)
    bot_activity(con)
    module_coverage(con)
    pr_size_vs_review(con)

    # Review quality
    review_depth(con)
    review_iterations(con)
    stale_approvals(con)
    brief_comments(con)
    self_review_activity(con)

    # Temporal patterns
    review_by_time(con)
    review_latency_by_author(con)
    review_latency_by_module(con)
    time_in_review(con)

    # Team dynamics
    review_reciprocity(con)
    reviewer_load_balance(con)

    # Risk indicators
    large_pr_no_comments(con)
    quick_approve_large_pr(con)
    single_reviewer_merges(con)

    # Code review quality (excluding generated files)
    code_review_depth(con)
    pr_type_review_depth(con)
    conventional_commits(con)
    underreviewed_code(con)

    # Collaboration context
    module_experts(con)
    module_reviewers(con)
    collaboration_pairs(con)
    module_collaboration(con)
    informed_approvals(con)

    con.close()


if __name__ == "__main__":
    main()
