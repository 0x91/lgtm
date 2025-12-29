"""Analysis queries for code review data.

Run with: uv run analyze
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DATA_DIR = Path("data/raw")


def get_connection() -> duckdb.DuckDBPyConnection:
    """Get DuckDB connection with parquet files registered as views."""
    con = duckdb.connect()

    for table in ["prs", "reviews", "pr_comments", "review_comments", "files", "checks", "timeline_events", "users"]:
        path = DATA_DIR / f"{table}.parquet"
        if path.exists():
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM '{path}'")

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
    """Show review activity by module."""
    run_query(con, "Module Review Coverage", """
        WITH module_stats AS (
            SELECT
                f.module,
                COUNT(DISTINCT f.pr_number) as prs,
                COUNT(DISTINCT CASE WHEN r.review_id IS NOT NULL AND NOT r.reviewer_is_bot THEN f.pr_number END) as reviewed_prs,
                SUM(f.additions + f.deletions) as total_churn
            FROM files f
            LEFT JOIN reviews r ON f.pr_number = r.pr_number
            GROUP BY 1
        )
        SELECT
            module,
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


def main() -> None:
    """Run all analysis queries."""
    con = get_connection()

    print("=" * 60)
    print("LGTM: Code Review Analysis Report")
    print("=" * 60)

    total_prs = con.execute("SELECT COUNT(*) FROM prs").fetchone()[0]
    print(f"\nTotal PRs analyzed: {total_prs:,}")

    rubber_stamp_rate(con)
    time_to_review(con)
    review_coverage(con)
    who_reviews_whom(con)
    substantive_reviewers(con)
    bot_activity(con)
    module_coverage(con)
    pr_size_vs_review(con)

    con.close()


if __name__ == "__main__":
    main()
