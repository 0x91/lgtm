"""Narrative report generator for code review analysis.

Instead of dumping tables, this tells a story:
"Is code review adding value, or is it just a rubber stamp?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from .analyze import get_connection


@dataclass
class ReportSection:
    """A section of the report with headline and details."""

    headline: str
    summary: str
    details: list[str] | None = None
    table: list[dict] | None = None


def format_pct(value: float | None) -> str:
    """Format percentage with one decimal."""
    if value is None:
        return "N/A"
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


def print_section(section: ReportSection) -> None:
    """Print a report section."""
    print(f"\n## {section.headline}")
    print()
    print(section.summary)

    if section.details:
        print()
        for detail in section.details:
            print(f"  - {detail}")

    if section.table:
        print()
        # Print as formatted table
        if section.table:
            headers = list(section.table[0].keys())
            # Calculate column widths
            widths = {h: max(len(str(h)), max(len(str(row.get(h, ""))) for row in section.table)) for h in headers}

            # Print header
            header_line = " | ".join(str(h).ljust(widths[h]) for h in headers)
            print(f"  {header_line}")
            print(f"  {'-' * len(header_line)}")

            # Print rows
            for row in section.table:
                row_line = " | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers)
                print(f"  {row_line}")


def analyze_review_substance(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """Are reviews substantive or just approvals?"""
    # Get the core numbers
    result = con.execute("""
        WITH review_stats AS (
            SELECT
                r.review_id,
                r.state,
                r.body,
                COUNT(rc.comment_id) as inline_comments
            FROM reviews r
            LEFT JOIN review_comments rc ON r.pr_number = rc.pr_number
                AND r.reviewer_login = rc.author_login
            WHERE NOT r.reviewer_is_bot
            GROUP BY 1, 2, 3
        )
        SELECT
            COUNT(*) as total_reviews,
            SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals,
            SUM(CASE WHEN state = 'APPROVED' AND (body IS NULL OR TRIM(body) = '')
                     AND inline_comments = 0 THEN 1 ELSE 0 END) as empty_approvals,
            SUM(CASE WHEN state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as changes_requested,
            SUM(CASE WHEN inline_comments > 0 THEN 1 ELSE 0 END) as with_inline
        FROM review_stats
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No Review Data",
            summary="No human reviews found in the dataset."
        )

    total, approvals, empty_approvals, changes_requested, with_inline = result

    empty_rate = 100.0 * empty_approvals / approvals if approvals else 0
    substantive_rate = 100.0 * (with_inline + changes_requested) / total if total else 0

    # Determine the headline based on the data
    if empty_rate > 60:
        headline = f"{format_pct(empty_rate)} of approvals are rubber stamps"
        tone = "concerning"
    elif empty_rate > 40:
        headline = f"Mixed signals: {format_pct(empty_rate)} empty approvals"
        tone = "moderate"
    else:
        headline = f"Reviews are substantive: only {format_pct(empty_rate)} empty approvals"
        tone = "positive"

    summary = (
        f"Of {total:,} human reviews, {approvals:,} were approvals. "
        f"{empty_approvals:,} of those had no comment and no inline feedback. "
        f"{changes_requested:,} reviews requested changes, and {with_inline:,} included inline comments."
    )

    details = []
    if tone == "concerning":
        details.append("Most approvals come without any written feedback")
        details.append("Consider whether reviewers have time to review properly")
    elif tone == "moderate":
        details.append("Some reviewers engage deeply, others approve quickly")
        details.append("Check collaboration context - empty approvals may be informed")

    return ReportSection(headline=headline, summary=summary, details=details if details else None)


def analyze_review_timing(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """How long do authors wait for reviews?"""
    result = con.execute("""
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
            COUNT(*) as prs,
            AVG(EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600) as avg_hours,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600) as median_hours,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (first_review_at - pr_created)) / 3600) as p90_hours
        FROM pr_first_review
    """).fetchone()

    if not result or not result[1]:
        return ReportSection(
            headline="No Timing Data",
            summary="Could not calculate review timing."
        )

    prs, avg_hours, median_hours, p90_hours = result

    # The headline should focus on typical experience (median)
    if median_hours < 2:
        headline = f"Fast feedback: median {format_hours(median_hours)} to first review"
    elif median_hours < 8:
        headline = f"Same-day reviews: median {format_hours(median_hours)} wait"
    elif median_hours < 24:
        headline = f"Next-day reviews typical: median {format_hours(median_hours)}"
    else:
        headline = f"Slow reviews: median {format_hours(median_hours)} to first feedback"

    summary = (
        f"Across {prs:,} PRs, the median wait for first review is {format_hours(median_hours)}. "
        f"Average is {format_hours(avg_hours)} (skewed by outliers). "
        f"10% of PRs wait longer than {format_hours(p90_hours)}."
    )

    return ReportSection(headline=headline, summary=summary)


def analyze_review_load(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """Is review work distributed fairly?"""
    result = con.execute("""
        WITH reviewer_stats AS (
            SELECT
                reviewer_login,
                COUNT(DISTINCT pr_number) as prs_reviewed
            FROM reviews
            WHERE NOT reviewer_is_bot
            GROUP BY 1
        ),
        totals AS (
            SELECT
                COUNT(DISTINCT reviewer_login) as num_reviewers,
                SUM(prs_reviewed) as total_reviews
            FROM reviewer_stats
        ),
        top_reviewers AS (
            SELECT
                reviewer_login,
                prs_reviewed,
                1.0 * prs_reviewed / t.total_reviews as share
            FROM reviewer_stats
            CROSS JOIN totals t
            ORDER BY prs_reviewed DESC
            LIMIT 5
        )
        SELECT
            t.num_reviewers,
            t.total_reviews,
            SUM(tr.share) as top5_share,
            MAX(tr.prs_reviewed) as top_reviewer_prs,
            (SELECT reviewer_login FROM top_reviewers ORDER BY prs_reviewed DESC LIMIT 1) as top_reviewer
        FROM totals t
        LEFT JOIN top_reviewers tr ON true
        GROUP BY t.num_reviewers, t.total_reviews
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No Reviewer Data",
            summary="Could not analyze reviewer distribution."
        )

    num_reviewers, total_reviews, top5_share, top_prs, top_reviewer = result
    top5_pct = (top5_share or 0) * 100

    if top5_pct > 60:
        headline = f"Review bottleneck: top 5 do {format_pct(top5_pct)} of reviews"
    elif top5_pct > 40:
        headline = f"Concentrated reviews: top 5 handle {format_pct(top5_pct)}"
    else:
        headline = f"Well-distributed: top 5 do {format_pct(top5_pct)} of reviews"

    summary = (
        f"{num_reviewers} people have reviewed PRs. "
        f"The busiest reviewer ({top_reviewer}) has reviewed {top_prs:,} PRs."
    )

    return ReportSection(headline=headline, summary=summary)


def analyze_informed_reviews(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """Are empty approvals informed or first-time?"""
    result = con.execute("""
        WITH approval_context AS (
            SELECT
                r.pr_number,
                p.author_login as author,
                r.reviewer_login as reviewer,
                r.body,
                r.submitted_at
            FROM reviews r
            JOIN prs p ON r.pr_number = p.pr_number
            WHERE r.state = 'APPROVED'
              AND NOT r.reviewer_is_bot
              AND NOT p.author_is_bot
        ),
        with_history AS (
            SELECT
                ac.pr_number,
                ac.body,
                (
                    SELECT COUNT(DISTINCT r2.pr_number)
                    FROM reviews r2
                    JOIN prs p2 ON r2.pr_number = p2.pr_number
                    WHERE r2.reviewer_login = ac.reviewer
                      AND p2.author_login = ac.author
                      AND r2.submitted_at < ac.submitted_at
                ) as prior_reviews
            FROM approval_context ac
        )
        SELECT
            SUM(CASE WHEN prior_reviews >= 10 AND (body IS NULL OR TRIM(body) = '') THEN 1 ELSE 0 END) as informed_empty,
            SUM(CASE WHEN prior_reviews < 3 AND (body IS NULL OR TRIM(body) = '') THEN 1 ELSE 0 END) as uninformed_empty,
            SUM(CASE WHEN body IS NULL OR TRIM(body) = '' THEN 1 ELSE 0 END) as total_empty,
            COUNT(*) as total_approvals
        FROM with_history
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No Approval Context",
            summary="Could not analyze approval context."
        )

    informed_empty, uninformed_empty, total_empty, total_approvals = result

    if total_empty == 0:
        return ReportSection(
            headline="No Empty Approvals",
            summary="All approvals included written feedback."
        )

    informed_pct = 100.0 * informed_empty / total_empty if total_empty else 0
    uninformed_pct = 100.0 * uninformed_empty / total_empty if total_empty else 0

    if uninformed_pct > 40:
        headline = f"Concerning: {format_pct(uninformed_pct)} of empty approvals are from first-time reviewers"
    elif informed_pct > 50:
        headline = f"Context helps: {format_pct(informed_pct)} of empty approvals are from familiar reviewers"
    else:
        headline = "Mixed approval context"

    summary = (
        f"Of {total_empty:,} empty approvals, {informed_empty:,} came from reviewers with 10+ prior reviews "
        f"of that author. {uninformed_empty:,} came from reviewers with fewer than 3 prior reviews."
    )

    details = []
    if uninformed_pct > 40:
        details.append("First-time reviewers approving without comment is a risk signal")
        details.append("Consider code ownership or required reviewers for critical areas")

    return ReportSection(headline=headline, summary=summary, details=details if details else None)


def analyze_large_pr_risk(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """Are large PRs getting proper review?"""
    result = con.execute("""
        WITH pr_stats AS (
            SELECT
                p.pr_number,
                SUM(CASE WHEN NOT f.is_gen THEN f.additions + f.deletions ELSE 0 END) as code_churn,
                COUNT(DISTINCT CASE WHEN NOT rc.author_is_bot THEN rc.comment_id END) as inline_comments,
                BOOL_OR(r.state = 'CHANGES_REQUESTED') as had_feedback
            FROM prs p
            JOIN files f ON p.pr_number = f.pr_number
            LEFT JOIN reviews r ON p.pr_number = r.pr_number AND NOT r.reviewer_is_bot
            LEFT JOIN review_comments rc ON p.pr_number = rc.pr_number
            WHERE p.merged AND NOT p.author_is_bot
            GROUP BY 1
        )
        SELECT
            COUNT(*) FILTER (WHERE code_churn >= 300) as large_prs,
            COUNT(*) FILTER (WHERE code_churn >= 300 AND inline_comments = 0 AND NOT had_feedback) as large_no_review,
            COUNT(*) FILTER (WHERE code_churn >= 500) as xl_prs,
            COUNT(*) FILTER (WHERE code_churn >= 500 AND inline_comments = 0 AND NOT had_feedback) as xl_no_review
        FROM pr_stats
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No PR Size Data",
            summary="Could not analyze PR sizes."
        )

    large_prs, large_no_review, xl_prs, xl_no_review = result

    if large_prs == 0:
        return ReportSection(
            headline="No Large PRs",
            summary="No PRs with 300+ lines of code changes found."
        )

    risk_rate = 100.0 * large_no_review / large_prs

    if risk_rate > 30:
        headline = f"Risk: {large_no_review} large PRs ({format_pct(risk_rate)}) merged without substantive review"
    elif risk_rate > 10:
        headline = f"Some risk: {large_no_review} large PRs merged without feedback"
    else:
        headline = f"Large PRs get attention: only {format_pct(risk_rate)} without feedback"

    summary = (
        f"Of {large_prs:,} PRs with 300+ lines of code changes, "
        f"{large_no_review:,} were merged with no inline comments and no change requests. "
        f"{xl_prs:,} PRs had 500+ lines, {xl_no_review:,} of those had no substantive review."
    )

    return ReportSection(headline=headline, summary=summary)


def analyze_feedback_quality(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """How often does review feedback lead to changes?"""
    result = con.execute("""
        WITH pr_reviews AS (
            SELECT
                pr_number,
                SUM(CASE WHEN state = 'CHANGES_REQUESTED' THEN 1 ELSE 0 END) as change_requests,
                SUM(CASE WHEN state = 'APPROVED' THEN 1 ELSE 0 END) as approvals
            FROM reviews
            WHERE NOT reviewer_is_bot
            GROUP BY 1
        )
        SELECT
            COUNT(*) as reviewed_prs,
            COUNT(*) FILTER (WHERE change_requests > 0) as prs_with_feedback,
            COUNT(*) FILTER (WHERE change_requests > 0 AND approvals > 0) as feedback_then_approved,
            COUNT(*) FILTER (WHERE change_requests >= 2) as multi_round
        FROM pr_reviews
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No Feedback Data",
            summary="Could not analyze review feedback."
        )

    reviewed, with_feedback, then_approved, multi_round = result

    feedback_rate = 100.0 * with_feedback / reviewed if reviewed else 0
    resolved_rate = 100.0 * then_approved / with_feedback if with_feedback else 0

    if feedback_rate > 30:
        headline = f"Reviewers push back: {format_pct(feedback_rate)} of PRs get change requests"
    elif feedback_rate > 15:
        headline = f"Moderate pushback: {format_pct(feedback_rate)} of PRs see change requests"
    else:
        headline = f"Rare pushback: only {format_pct(feedback_rate)} of PRs get change requests"

    summary = (
        f"Of {reviewed:,} reviewed PRs, {with_feedback:,} received at least one change request. "
        f"{then_approved:,} of those were eventually approved. "
        f"{multi_round:,} PRs went through multiple rounds of feedback."
    )

    return ReportSection(headline=headline, summary=summary)


def analyze_quick_approvals(con: duckdb.DuckDBPyConnection) -> ReportSection:
    """Are there suspiciously fast approvals on large PRs?"""
    result = con.execute("""
        WITH first_approval AS (
            SELECT
                r.pr_number,
                MIN(r.submitted_at) as first_approved_at
            FROM reviews r
            WHERE r.state = 'APPROVED' AND NOT r.reviewer_is_bot
            GROUP BY 1
        )
        SELECT
            COUNT(*) as large_prs,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM (fa.first_approved_at - p.created_at)) / 60 < 5
            ) as quick_approvals,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM (fa.first_approved_at - p.created_at)) / 60 < 2
            ) as very_quick
        FROM prs p
        JOIN first_approval fa ON p.pr_number = fa.pr_number
        WHERE p.additions + p.deletions >= 200
          AND p.merged
    """).fetchone()

    if not result:
        return ReportSection(
            headline="No Quick Approval Data",
            summary="Could not analyze approval timing."
        )

    large_prs, quick, very_quick = result

    if large_prs == 0:
        return ReportSection(
            headline="No Large PRs to Analyze",
            summary="No merged PRs with 200+ lines found."
        )

    quick_rate = 100.0 * quick / large_prs

    if quick_rate > 20:
        headline = f"Speed concern: {quick} large PRs approved in under 5 minutes"
    elif quick_rate > 5:
        headline = f"Some quick approvals: {quick} large PRs approved in <5 min"
    else:
        headline = f"Large PRs get time: only {quick} approved under 5 min"

    summary = (
        f"Of {large_prs:,} merged PRs with 200+ lines, "
        f"{quick:,} were approved within 5 minutes of creation. "
        f"{very_quick:,} were approved in under 2 minutes."
    )

    details = []
    if very_quick > 5:
        details.append("Sub-2-minute approvals on large PRs warrant investigation")
        details.append("May indicate coordinated merges or skipped review")

    return ReportSection(headline=headline, summary=summary, details=details if details else None)


def generate_report(con: duckdb.DuckDBPyConnection | None = None) -> None:
    """Generate the full narrative report."""
    if con is None:
        con = get_connection()

    # Header
    print("=" * 70)
    print("CODE REVIEW ANALYSIS: Is review adding value?")
    print("=" * 70)

    # Get date range and totals
    result = con.execute("""
        SELECT
            COUNT(*) as total_prs,
            MIN(created_at) as first_pr,
            MAX(created_at) as last_pr,
            COUNT(*) FILTER (WHERE merged) as merged_prs
        FROM prs
    """).fetchone()

    if result:
        total_prs, first_pr, last_pr, merged = result
        print(f"\nData: {total_prs:,} PRs from {first_pr:%Y-%m-%d} to {last_pr:%Y-%m-%d}")
        print(f"Merged: {merged:,} ({100.0 * merged / total_prs:.1f}%)")

    # The story
    print("\n" + "=" * 70)
    print("THE BIG PICTURE")
    print("=" * 70)

    sections = [
        analyze_review_substance(con),
        analyze_informed_reviews(con),
        analyze_feedback_quality(con),
    ]

    for section in sections:
        print_section(section)

    print("\n" + "=" * 70)
    print("TIMING & WORKLOAD")
    print("=" * 70)

    sections = [
        analyze_review_timing(con),
        analyze_review_load(con),
    ]

    for section in sections:
        print_section(section)

    print("\n" + "=" * 70)
    print("RISK INDICATORS")
    print("=" * 70)

    sections = [
        analyze_large_pr_risk(con),
        analyze_quick_approvals(con),
    ]

    for section in sections:
        print_section(section)

    print()


def main() -> None:
    """CLI entry point."""
    con = get_connection()
    generate_report(con)
    con.close()


if __name__ == "__main__":
    main()
